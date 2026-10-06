-- Full lineage for one company: every funding event, where it came from, how it
-- got into the database, and whether its source link still resolves.
--
-- This is the answer to "how do you know these numbers are right?" - each
-- figure is one click away from the article it was taken from.
SELECT f.announced_date,
       f.round_stage,
       f.amount_raised_usd,
       f.post_money_usd,
       f.lead_investor,
       f.source_url,
       f.confidence,
       f.extraction_method,
       f.reviewed_by_human,
       f.notes,
       f.evidence_text,
       l.reachable   AS source_reachable,
       l.http_status AS source_http_status,
       l.checked_at  AS source_checked_at
FROM fct_funding_round f
LEFT JOIN source_link_status l ON l.source_url = f.source_url
WHERE f.company_id = %(company_id)s
ORDER BY f.announced_date DESC;
