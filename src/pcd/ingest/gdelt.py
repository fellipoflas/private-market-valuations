"""GDELT ingestion.

We use the Context 2.0 API rather than DOC 2.0, because Context returns the
actual SENTENCE containing your search terms. That's the unit our valuation
extractor needs - DOC only gives titles and URLs, and you can't read a
valuation off a headline reliably.

Two things cost us time here, both worth knowing:

1. `mode=artlist` is REQUIRED. Without it the Context API cheerfully returns
   HTTP 200 with an empty article list for every query, which looks like "no
   results" rather than "you called it wrong".

2. The rate limit is about one request every five seconds, and tripping it
   earns a sticky block that outlasts several minutes of backoff. It returns
   HTTP 429 with a plain-text body, so pace requests rather than react to errors.
"""

import logging
from datetime import UTC, datetime

from pcd.ingest.base import RateLimiter, fetch_json
from pcd.settings import Company
from pcd.transform.dedup import Article

log = logging.getLogger(__name__)

CONTEXT_URL = "https://api.gdeltproject.org/api/v2/context/context"

# GDELT asks for one request per five seconds. We add a margin because the
# penalty for being wrong is a multi-minute block, not a single failed call.
GDELT_LIMITER = RateLimiter(min_interval_seconds=6.0)

# Terms that make a sentence likely to be about financing rather than, say, a
# government contract. Kept short: GDELT queries have a length limit.
FUNDING_TERMS = ["raised", "funding", "valuation"]


def build_context_query(company: Company, funding_only: bool = True) -> str:
    """Compose a GDELT query from a company's aliases and exclusions.

    Produces something like:
        ("Anduril Industries" OR "Anduril") ("raised" OR "valuation") -"Tolkien"
    """
    # GDELT rejects parentheses around a single term: "Parentheses may only be
    # used around OR'd statements." So only group when there's something to OR.
    query = _or_group(dict.fromkeys(company.search_names))

    if funding_only:
        query = f"{query} {_or_group(FUNDING_TERMS)}"

    for term in company.exclude_terms:
        query += f' -"{term}"'

    return query


def _or_group(terms) -> str:
    """Quote terms and OR them, parenthesising only when there are 2 or more."""
    quoted = [f'"{t}"' for t in terms]
    if len(quoted) == 1:
        return quoted[0]
    return "(" + " OR ".join(quoted) + ")"


def _parse_seendate(value: str | None) -> datetime | None:
    """GDELT stamps look like 20260915T184704Z."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        log.warning("unparseable seendate %r", value)
        return None


def fetch_context(company: Company, max_records: int = 75) -> dict:
    """Raw Context API response for one company. Persist this before parsing.

    Mostly useful for spot-checking one company. For the scheduled pipeline
    prefer fetch_funding_firehose() - see the note there.
    """
    params = {
        "query": build_context_query(company),
        "format": "json",
        "mode": "artlist",  # without this you get an empty list, silently
        "maxrecords": max_records,
    }
    payload = fetch_json(CONTEXT_URL, params, limiter=GDELT_LIMITER)
    log.info("gdelt context %s: %d articles", company.id, len(payload.get("articles", [])))
    return payload


# One broad query per run, instead of one per company.
#
# GDELT's Context window turned out to be about fifteen minutes wide, not the
# few days we expected. Querying per company meant 37 requests (at 6s apiece)
# that almost all returned zero, because any given company is rarely in the
# news during any given quarter hour. Sampling the funding firehose and
# filtering locally gets far more usable sentences for one request.
#
# Consequence worth understanding: this accumulates forward and CANNOT
# backfill. History comes from the curated seed CSV; GDELT only catches what
# happens from now on. Run it often enough and the gaps stay small.
FIREHOSE_QUERY = '"raised" "valuation"'

# Hard cap enforced by the API: asking for more returns a plain-text error.
MAX_RECORDS = 200


def fetch_funding_firehose(max_records: int = MAX_RECORDS, query: str = FIREHOSE_QUERY) -> dict:
    """Recent sentences anywhere in the news that look like funding announcements."""
    params = {
        "query": query,
        "format": "json",
        "mode": "artlist",
        "maxrecords": min(max_records, MAX_RECORDS),
    }
    payload = fetch_json(CONTEXT_URL, params, limiter=GDELT_LIMITER)
    log.info("gdelt firehose: %d articles", len(payload.get("articles", [])))
    return payload


def to_articles(payload: dict) -> list[Article]:
    """Map a Context response onto the shape the dedup logic expects."""
    return [
        Article(
            url=item["url"],
            title=item.get("title"),
            published_at=_parse_seendate(item.get("seendate")),
            domain=item.get("domain"),
            source="gdelt_context",
        )
        for item in payload.get("articles", [])
        if item.get("url")
    ]


def sentences(payload: dict) -> list[tuple[str, str]]:
    """(url, sentence) pairs - the input to valuation extraction.

    The `context` field is a wider window around the match. We prefer the
    tighter `sentence` because extra text mostly adds false positives.
    """
    return [
        (item["url"], item["sentence"])
        for item in payload.get("articles", [])
        if item.get("url") and item.get("sentence")
    ]
