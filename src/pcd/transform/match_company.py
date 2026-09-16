"""Work out which tracked company a piece of text is about.

Needed because we sample a broad funding-news firehose rather than searching
per company (GDELT's Context window is only minutes wide, so 37 narrow queries
would mostly return nothing). We pull in everything that looks like funding
news, then decide locally which of our companies it mentions.

The tricky part is that a lot of company names are ordinary English words.
"Ramp", "Notion", "Glean", "Together" and "Discord" all appear constantly in
text that has nothing to do with the company, so each one carries
exclude_terms and a false-positive risk rating in config/companies.yml.
"""

import re
from functools import lru_cache

from pcd.settings import Company, load_companies


@lru_cache(maxsize=512)
def _name_pattern(name: str) -> re.Pattern:
    r"""Word-boundary, case-insensitive match for a company name.

    \b on both sides stops "Ramp" matching inside "rampant" and "Deel" matching
    inside "Deeley". Names containing regex metacharacters are escaped.
    """
    return re.compile(rf"\b{re.escape(name)}\b", re.I)


def mentions(text: str, company: Company) -> bool:
    """True if text names this company and isn't obviously a false positive."""
    if not text:
        return False

    # Exclusions win. If the text says "Lord of the Rings", it isn't about the
    # defense contractor no matter how many times it says "Anduril".
    for term in company.exclude_terms:
        if _name_pattern(term).search(text):
            return False

    return any(_name_pattern(name).search(text) for name in company.search_names)


def _first_position(text: str, company: Company) -> int | None:
    """Character offset of the company's earliest name match, if any."""
    positions = [
        m.start()
        for name in company.search_names
        if (m := _name_pattern(name).search(text))
    ]
    return min(positions) if positions else None


def find_mentions(text: str, companies: list[Company] | None = None) -> list[tuple[str, int]]:
    """(company_id, position) for every tracked company named, earliest first."""
    companies = companies if companies is not None else load_companies()
    found = []
    for company in companies:
        if not mentions(text, company):
            continue
        pos = _first_position(text, company)
        if pos is not None:
            found.append((company.id, pos))
    return sorted(found, key=lambda pair: pair[1])


def match_companies(text: str, companies: list[Company] | None = None) -> list[str]:
    """Every tracked company mentioned in the text, earliest mention first.

    Returns a list because one article genuinely can cover several - funding
    round-ups name a dozen startups in a paragraph.
    """
    return [company_id for company_id, _ in find_mentions(text, companies)]


def primary_company(text: str, companies: list[Company] | None = None) -> str | None:
    """Best guess at which company the sentence is actually ABOUT.

    Heuristic: the earliest-named company is the subject. This matters because
    funding sentences routinely name other companies as context:

        "Anthropic, the company founded by former OpenAI researchers,
         raised $65 billion at a $965 billion valuation."

    Both companies are genuinely mentioned, but the $65B is Anthropic's. Without
    this, OpenAI would be credited with a funding round it never raised.

    Imperfect - a sentence starting "Unlike OpenAI, Anthropic raised..." fools
    it - which is exactly why extracted rounds go to a human review queue
    instead of straight into the dimension.
    """
    found = find_mentions(text, companies)
    return found[0][0] if found else None
