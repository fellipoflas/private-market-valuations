-- Every version of every company, straight out of the SCD2 dimension.
--
-- This is what makes the step chart possible: each row already carries the
-- window during which that valuation was true, so the chart is just the
-- dimension drawn to scale. Nothing is recomputed here - if the steps look
-- right, the Type 2 model is right.
--
-- valid_to is clamped to today for plotting, because the open-ended sentinel
-- (9999-12-31) would stretch the x-axis by eight thousand years.
SELECT c.company_id,
       c.company_name,
       c.sector,
       c.status,
       d.latest_valuation_usd AS valuation_usd,
       d.latest_round_stage   AS round_stage,
       d.valid_from,
       LEAST(d.valid_to, CURRENT_DATE) AS valid_to_plot,
       d.valid_to,
       d.is_current
FROM dim_company d
JOIN ref_company c ON c.company_id = d.company_id
WHERE d.latest_valuation_usd IS NOT NULL
ORDER BY c.company_name, d.valid_from;
