"""Persist ingested data.

Order matters: the raw API response is written FIRST, before anything is
parsed. If the parsing code has a bug we can re-parse from the database instead
of re-hitting the API - which matters a lot here, because GDELT's Context
window is only minutes wide. Miss it and it's gone.
"""

import logging
from dataclasses import dataclass

from psycopg.types.json import Jsonb

from pcd.db import connect
from pcd.ingest.base import payload_hash
from pcd.settings import load_companies
from pcd.transform.dedup import Article, dedupe_articles
from pcd.transform.extract_valuation import extract
from pcd.transform.match_company import match_companies, primary_company

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    raw_stored: bool = False
    articles_seen: int = 0
    articles_after_dedup: int = 0
    mentions_inserted: int = 0
    funding_sentences: int = 0

    def __str__(self) -> str:
        return (
            f"raw_stored={self.raw_stored} "
            f"articles={self.articles_seen}->{self.articles_after_dedup} "
            f"mentions_inserted={self.mentions_inserted} "
            f"funding_sentences={self.funding_sentences}"
        )


def store_raw(source: str, query_key: str, payload: dict) -> int | None:
    """Write one API response to raw_api_response.

    Returns the new raw_id, or None if we've already stored this exact payload.
    The UNIQUE (source, query_key, payload_hash) constraint is what makes
    re-running the pipeline safe: identical data is rejected, not duplicated.
    """
    digest = payload_hash(payload)
    with connect() as conn:
        row = conn.execute(
            """
            INSERT INTO raw_api_response (source, query_key, payload, payload_hash)
            VALUES (%(source)s, %(query_key)s, %(payload)s, %(hash)s)
            ON CONFLICT (source, query_key, payload_hash) DO NOTHING
            RETURNING raw_id
            """,
            {
                "source": source,
                "query_key": query_key,
                "payload": Jsonb(payload),
                "hash": digest,
            },
        ).fetchone()

    if row is None:
        log.info("raw payload already stored (%s/%s) - nothing new", source, query_key)
        return None
    return row["raw_id"]


def store_mentions(
    articles: list[Article],
    sentences_by_url: dict[str, str],
    raw_id: int | None,
    source: str,
) -> int:
    """Insert deduplicated article mentions, one row per (company, article).

    An article mentioning three tracked companies produces three rows - the
    grain of fct_news_mention is the company-article pair, not the article.
    """
    companies = load_companies()
    inserted = 0

    with connect() as conn:
        for article in articles:
            sentence = sentences_by_url.get(article.url, "")
            # Match on the sentence when we have one (tighter, fewer false
            # positives) and fall back to the headline.
            haystack = sentence or article.title or ""
            company_ids = match_companies(haystack, companies)
            if not company_ids:
                continue

            # Only the sentence's SUBJECT gets flagged as funding-related.
            # Other companies named in passing still get a mention row (they're
            # real coverage, useful for volume charts) but must not become
            # candidate funding rounds - see primary_company() for why.
            has_figures = not extract(sentence).is_empty if sentence else False
            subject = primary_company(haystack, companies) if has_figures else None

            for company_id in company_ids:
                is_funding = has_figures and company_id == subject
                result = conn.execute(
                    """
                    INSERT INTO fct_news_mention (
                        company_id, article_url, url_canonical, title, title_norm,
                        domain, published_at, source, sentence, is_funding_related, raw_id
                    )
                    VALUES (
                        %(company_id)s, %(url)s, %(canonical)s, %(title)s, %(title_norm)s,
                        %(domain)s, %(published_at)s, %(source)s, %(sentence)s,
                        %(is_funding)s, %(raw_id)s
                    )
                    ON CONFLICT (company_id, url_canonical) DO NOTHING
                    RETURNING mention_id
                    """,
                    {
                        "company_id": company_id,
                        "url": article.url,
                        "canonical": article.url_canonical,
                        "title": article.title,
                        "title_norm": article.title_norm,
                        "domain": article.domain,
                        "published_at": article.published_at,
                        "source": source,
                        "sentence": sentence or None,
                        "is_funding": is_funding,
                        "raw_id": raw_id,
                    },
                ).fetchone()
                if result:
                    inserted += 1

    return inserted


def ingest_payload(
    payload: dict,
    articles: list[Article],
    sentence_pairs: list[tuple[str, str]],
    *,
    source: str,
    query_key: str,
) -> IngestStats:
    """Full store path for one API response: raw -> dedup -> mentions."""
    stats = IngestStats(articles_seen=len(articles))

    raw_id = store_raw(source, query_key, payload)
    stats.raw_stored = raw_id is not None

    deduped = dedupe_articles(articles)
    stats.articles_after_dedup = len(deduped)

    sentences_by_url = dict(sentence_pairs)
    stats.funding_sentences = sum(
        1 for _, s in sentence_pairs if not extract(s).is_empty
    )
    stats.mentions_inserted = store_mentions(deduped, sentences_by_url, raw_id, source)

    return stats
