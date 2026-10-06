-- Every funding event, flat. Feeds the "priced rounds in the last 12 months"
-- KPI and per-company counts. A round without a disclosed post-money is still
-- a real event; is_priced separates the two.
SELECT c.company_id,
       c.company_name,
       c.sector,
       c.status,
       f.announced_date,
       f.round_stage,
       f.amount_raised_usd,
       f.post_money_usd,
       f.post_money_usd IS NOT NULL AS is_priced
FROM fct_funding_round f
JOIN ref_company c ON c.company_id = f.company_id
ORDER BY f.announced_date;
