-- Every version of every company, straight out of the SCD2 dimension.
--
-- Each row is one valuation mark and the window during which it was the
-- latest known value. The trajectory chart plots valid_from as the point
-- where the mark was set; nothing is recomputed, so if the chart is right,
-- the Type 2 model is right.
--
-- The lateral join pulls the round that set each mark, so a hover can show
-- how much was raised and where the figure came from.
SELECT c.company_id,
       c.company_name,
       c.sector,
       c.status,
       d.latest_valuation_usd AS valuation_usd,
       d.latest_round_stage   AS round_stage,
       d.valid_from,
       LEAST(d.valid_to, CURRENT_DATE) AS valid_to_plot,
       d.valid_to,
       d.is_current,
       f.amount_raised_usd,
       f.source_url
FROM dim_company d
JOIN ref_company c ON c.company_id = d.company_id
LEFT JOIN LATERAL (
    SELECT amount_raised_usd, source_url
    FROM fct_funding_round f
    WHERE f.company_id = d.company_id
      AND f.announced_date = d.valuation_as_of
      AND f.post_money_usd = d.latest_valuation_usd
    LIMIT 1
) f ON TRUE
WHERE d.latest_valuation_usd IS NOT NULL
ORDER BY c.company_name, d.valid_from;
