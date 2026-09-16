-- Current state of every tracked company, with mark staleness.
--
-- "Staleness" is how long it's been since a company's valuation was last set by
-- a priced round. It matters because a private company's reported valuation is
-- a point-in-time mark, not a live price - a two-year-old mark tells you what
-- investors thought in a different market. In secondaries this is the first
-- thing you look at, and it's the question this whole project exists to answer.
SELECT c.company_id,
       c.company_name,
       c.sector,
       c.subsector,
       c.hq_country,
       c.status,
       c.status_note,
       d.latest_valuation_usd,
       d.latest_round_stage,
       d.valuation_as_of,
       CURRENT_DATE - d.valuation_as_of AS days_since_mark,
       CASE
           WHEN d.valuation_as_of IS NULL             THEN 'No mark'
           WHEN CURRENT_DATE - d.valuation_as_of < 183 THEN 'Fresh'
           WHEN CURRENT_DATE - d.valuation_as_of < 548 THEN 'Aging'
           ELSE 'Stale'
       END AS staleness_band
FROM ref_company c
LEFT JOIN dim_company d
       ON d.company_id = c.company_id
      AND d.is_current
ORDER BY d.latest_valuation_usd DESC NULLS LAST;
