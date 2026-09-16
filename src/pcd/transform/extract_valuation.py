"""Pull funding figures out of a sentence.

This is deliberately regex-first and conservative. We would much rather miss a
round than record a wrong number, because every row here is supposed to be
defensible - someone can click the source_url and check it.

The hard part isn't finding dollar amounts, it's telling them apart. In
"raised $600 million at a $3.7 billion valuation" there are two figures that
mean completely different things, and in "a $3.7 billion company" there's one
that might mean revenue. So we match on the WORDS AROUND the number, never on
the number alone.
"""

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------

# Longest units first so "billion" wins over a bare "b".
_UNIT = r"trillion|billion|million|thousand|bn|mn|tn|[bmkt]"

# Reusable money sub-pattern. Uses named groups; each pattern below embeds it
# exactly once, so the names never collide.
# Currency markers appear on either side of the symbol in the wild: "US$183bn"
# and "$US183 billion" are both common, the latter especially in Australian and
# Canadian outlets. The separator before the unit can also be a hyphen -
# "raised EUR3-billion" showed up in live Reuters copy.
MONEY = (
    r"(?:US)?(?P<cur>[$€£])(?:US)?\s?"
    r"(?P<amount>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"[\s-]*(?P<unit>" + _UNIT + r")?\b"
)

MULTIPLIERS = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}

# Hedges that can sit between the trigger word and the number.
_HEDGE = r"(?:about|roughly|nearly|around|approximately|over|more than|up to|at least|some)?\s*"

# ---------------------------------------------------------------------------
# What kind of number is this?
# ---------------------------------------------------------------------------

VALUATION_PATTERNS = [
    # "of" / "at" / "to" all appear in the wild: "valuation of $3B", "valued at
    # $3B", "doubles valuation to $3B". Missing "to" cost us a real headline.
    rf"valu(?:ation|ed|ing)\s+(?:of\s+|at\s+|to\s+|hits\s+|reach(?:ed|es)\s+)?{_HEDGE}{MONEY}",
    rf"values?\s+(?:the\s+)?(?:company|startup|firm|business|maker)\s+at\s+{_HEDGE}{MONEY}",
    rf"at\s+a\s+{_HEDGE}{MONEY}\s+(?:pre-money\s+|post-money\s+)?valuation",
    rf"{MONEY}\s+(?:pre-money\s+|post-money\s+)?valuation",
]

RAISE_PATTERNS = [
    rf"rais(?:ed|es|ing)\s+{_HEDGE}{MONEY}",
    rf"clos(?:ed|es|ing)\s+(?:a|an|its)?\s*{_HEDGE}{MONEY}",
    rf"secur(?:ed|es|ing)\s+{_HEDGE}{MONEY}",
    rf"land(?:ed|s)\s+{_HEDGE}{MONEY}",
    rf"{MONEY}\s+(?:in\s+)?(?:new\s+|fresh\s+|additional\s+)?"
    rf"(?:funding|financing|investment|capital|round)",
    rf"(?:a|an)\s+{MONEY}\s+(?:Series\s+[A-K]\b|seed\b|round\b|financing\b)",
    rf"Series\s+[A-K]\s+(?:round\s+)?(?:of|worth|totaling|totalling)\s+{_HEDGE}{MONEY}",
]

# "pre-money" changes the meaning entirely, so we detect and flag it.
PRE_MONEY = re.compile(r"pre-?money", re.I)
POST_MONEY = re.compile(r"post-?money", re.I)

ROUND_STAGE_PATTERNS = [
    (re.compile(r"\bpre-?seed\b", re.I), "Pre-Seed"),
    (re.compile(r"\bseed\s+(?:round|funding|financing)\b", re.I), "Seed"),
    (re.compile(r"\bSeries\s+([A-K])\b", re.I), None),  # None = use the captured letter
    (re.compile(r"\b(?:tender\s+offer|employee\s+tender)\b", re.I), "Tender Offer"),
    (re.compile(r"\bsecondary\s+(?:sale|offering|market|share)\b", re.I), "Secondary"),
    (re.compile(r"\bgrowth\s+(?:round|equity|financing)\b", re.I), "Growth"),
    (re.compile(r"\b(?:debt\s+financing|credit\s+facility|term\s+loan)\b", re.I), "Debt"),
]

# A sentence must look like it's about funding at all before we bother parsing.
FUNDING_KEYWORDS = re.compile(
    r"\b(?:rais(?:ed|es|ing)|fund(?:ing|ed)|financ(?:ing|ed)|"
    r"valu(?:ation|ed|es|ing)|"  # "values the company at" needs the -es form too
    r"Series\s+[A-K]|seed\s+round|investment|investors?|round)\b",
    re.I,
)

_COMPILED_VALUATION = [re.compile(p, re.I) for p in VALUATION_PATTERNS]
_COMPILED_RAISE = [re.compile(p, re.I) for p in RAISE_PATTERNS]
_COMPILED_MONEY = re.compile(MONEY, re.I)


@dataclass
class Extraction:
    """What we managed to pull out of one sentence."""

    amount_raised_usd: float | None = None
    post_money_usd: float | None = None
    round_stage: str | None = None
    currency: str = "USD"
    is_pre_money: bool = False
    matched_phrases: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.amount_raised_usd is None and self.post_money_usd is None


def parse_money(amount: str, unit: str | None) -> float | None:
    """'3.7' + 'billion' -> 3700000000.0

    A bare number with no unit is rejected. Funding figures are always reported
    with a scale word, so an unqualified '$500' is almost certainly not a round.
    """
    if not unit:
        return None
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    multiplier = MULTIPLIERS.get(unit.lower())
    return value * multiplier if multiplier else None


def _first_match(patterns: list[re.Pattern], text: str) -> tuple[float | None, str, str | None]:
    """Return (usd_value, matched_text, currency_symbol) for the EARLIEST hit in the text.

    Earliest position, not first pattern in the list. Funding sentences often
    state the new figure and then compare it to an old one:

        "raised $5B at a $190B valuation, up 41.8% from its last valuation of $134B"

    Both halves match valuation patterns. The claim being made is the first one;
    the trailing clause is history. Taking whichever pattern happened to be
    earlier in our list picked $134B here - the wrong number entirely.
    """
    best: tuple[int, float, str, str | None] | None = None

    for pattern in patterns:
        for match in pattern.finditer(text):
            value = parse_money(match.group("amount"), match.group("unit"))
            if value is None:
                continue
            start = match.start()
            if best is None or start < best[0]:
                best = (start, value, match.group(0), match.group("cur"))
            break  # leftmost hit for this pattern is enough

    if best is None:
        return None, "", None
    return best[1], best[2], best[3]


def detect_round_stage(text: str) -> str | None:
    for pattern, label in ROUND_STAGE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if label is not None:
            return label
        return f"Series {match.group(1).upper()}"
    return None


def looks_like_funding(text: str) -> bool:
    """Cheap gate so we don't regex every sentence GDELT hands us."""
    return bool(text) and bool(FUNDING_KEYWORDS.search(text)) and bool(_COMPILED_MONEY.search(text))


def extract(text: str) -> Extraction:
    """Best-effort extraction from a single sentence.

    Returns an empty Extraction if nothing confident was found - that's the
    expected outcome for most sentences and is not an error.
    """
    result = Extraction()
    if not looks_like_funding(text):
        return result

    valuation, val_phrase, val_cur = _first_match(_COMPILED_VALUATION, text)
    raised, raise_phrase, raise_cur = _first_match(_COMPILED_RAISE, text)

    # If both patterns latched onto the same span, the sentence only really
    # stated one figure. Trust the valuation reading and drop the raise.
    if val_phrase and raise_phrase and val_phrase == raise_phrase:
        raised, raise_phrase = None, ""

    # Sanity: you can't raise more than the company is worth afterwards.
    # If that happens we've mismatched the two figures, so keep neither.
    if valuation is not None and raised is not None and raised > valuation:
        return Extraction(round_stage=detect_round_stage(text))

    result.post_money_usd = valuation
    result.amount_raised_usd = raised
    result.round_stage = detect_round_stage(text)
    result.matched_phrases = [p for p in (val_phrase, raise_phrase) if p]

    symbol = val_cur or raise_cur
    result.currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(symbol or "$", "USD")

    # Pre-money is a different number than post-money. Flag rather than silently
    # storing it in a post_money column.
    if PRE_MONEY.search(text) and not POST_MONEY.search(text):
        result.is_pre_money = True

    return result
