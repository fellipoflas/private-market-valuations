"""Deduplicating news articles.

Two kinds of duplicate show up in practice:

1. The SAME article reachable at several URLs - http vs https, with and without
   www, with tracking junk like ?utm_source=twitter glued on the end.
   Fixed by canonicalizing the URL.

2. The same WIRE STORY republished by dozens of outlets, each with its own URL
   and a slightly reworded headline. Our very first GDELT test pulled the same
   press release six times. Fixed by fuzzy-matching titles within a date window.

Without both, "news volume" charts just measure how syndicated a story was.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit

from rapidfuzz import fuzz

# Query params that identify a marketing campaign, not a document. Safe to drop.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "dclid", "msclkid", "twclid", "igshid", "mc_cid", "mc_eid",
    "ref", "referrer", "source", "cmpid", "ito", "ncid", "spm", "sh",
    "at_medium", "at_campaign", "at_custom1", "at_custom2",
    "_hsenc", "_hsmi", "__twitter_impression", "guccounter", "amp",
}

# Trailing outlet attribution: "Headline - Reuters", "Headline | TechCrunch"
OUTLET_SUFFIX = re.compile(r"\s*[|\-–—]\s*[A-Z][\w.& ]{2,30}\s*$")

PUNCT = re.compile(r"[^\w\s]")
WHITESPACE = re.compile(r"\s+")

# How similar two headlines must be to count as the same story (0-100).
TITLE_SIMILARITY_THRESHOLD = 90

# Syndication lag - the same wire story can appear over a few days.
SYNDICATION_WINDOW_DAYS = 3


def canonicalize_url(url: str) -> str:
    """Reduce a URL to a stable identity key.

    Deliberately drops the scheme: http://x.com/a and https://x.com/a are the
    same article, and treating them as different would create phantom duplicates.

    >>> canonicalize_url("https://WWW.Example.com/Story/?utm_source=x&id=7#top")
    'example.com/Story?id=7'
    """
    parts = urlsplit(url.strip())

    host = parts.netloc.lower()
    # strip credentials and port, they never distinguish news articles
    host = host.rsplit("@", 1)[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]

    # Path case is preserved - some CMSs genuinely serve different content for
    # different casing. Only the trailing slash is noise.
    path = parts.path.rstrip("/")

    # Keep meaningful query params (some sites put the article id there),
    # drop tracking, and sort so param order doesn't create false differences.
    kept = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS]
    query = urlencode(sorted(kept))

    canonical = f"{host}{path}"
    if query:
        canonical = f"{canonical}?{query}"
    return canonical


def normalize_title(title: str | None) -> str:
    """Strip a headline down to comparable words.

    >>> normalize_title("Anduril Raises $1.5B! - Reuters")
    'anduril raises 15b'
    """
    if not title:
        return ""
    cleaned = OUTLET_SUFFIX.sub("", title.strip())
    cleaned = PUNCT.sub("", cleaned.lower())
    return WHITESPACE.sub(" ", cleaned).strip()


@dataclass
class Article:
    """Minimal shape the dedup logic needs. Ingesters map their payloads to this."""

    url: str
    title: str | None
    published_at: datetime | None
    domain: str | None = None
    source: str | None = None

    @property
    def url_canonical(self) -> str:
        return canonicalize_url(self.url)

    @property
    def title_norm(self) -> str:
        return normalize_title(self.title)


def _sort_key(article: Article) -> tuple:
    """Earliest publication wins - the original beats the syndicated copy.

    Articles with no date sort last, since we can't prove they came first.
    """
    return (article.published_at is None, article.published_at or datetime.max)


def dedupe_articles(articles: list[Article]) -> list[Article]:
    """Collapse duplicates, keeping the earliest-published version of each story.

    Pass 1 collapses exact URL matches. Pass 2 collapses near-identical titles
    published within a few days of each other.
    """
    # Pass 1: exact canonical URL
    by_url: dict[str, Article] = {}
    for article in sorted(articles, key=_sort_key):
        by_url.setdefault(article.url_canonical, article)

    # Pass 2: fuzzy title within the syndication window.
    # O(n^2) over the surviving set, which is fine at our scale (hundreds, not millions).
    kept: list[Article] = []
    for candidate in sorted(by_url.values(), key=_sort_key):
        if _is_duplicate_of_any(candidate, kept):
            continue
        kept.append(candidate)
    return kept


def _is_duplicate_of_any(candidate: Article, kept: list[Article]) -> bool:
    cand_title = candidate.title_norm
    if not cand_title:
        return False

    for existing in kept:
        if not existing.title_norm:
            continue
        if not _within_window(candidate.published_at, existing.published_at):
            continue
        # token_sort_ratio ignores word order, so "Anduril raises $1.5B" and
        # "$1.5B raised by Anduril" still match.
        if fuzz.token_sort_ratio(cand_title, existing.title_norm) >= TITLE_SIMILARITY_THRESHOLD:
            return True
    return False


def _within_window(a: datetime | None, b: datetime | None) -> bool:
    """Unknown dates are treated as possibly-in-window rather than excluded."""
    if a is None or b is None:
        return True
    return abs(a - b) <= timedelta(days=SYNDICATION_WINDOW_DAYS)
