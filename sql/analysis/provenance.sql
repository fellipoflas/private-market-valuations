-- Where every funding figure came from.
--
-- Shown on the dashboard on purpose. The honest answer to "where did you get
-- private valuations for free?" is that no such structured source exists, so
-- history is hand-curated and new rounds are detected automatically. Publishing
-- that split is more credible than hiding it.
SELECT confidence,
       extraction_method,
       reviewed_by_human,
       COUNT(*)                                      AS rounds,
       COUNT(DISTINCT company_id)                    AS companies,
       COUNT(*) FILTER (WHERE post_money_usd IS NOT NULL) AS with_valuation,
       MIN(announced_date)                           AS earliest,
       MAX(announced_date)                           AS latest
FROM fct_funding_round
GROUP BY confidence, extraction_method, reviewed_by_human
ORDER BY rounds DESC;
