-- Is round momentum slowing? Priced rounds, median step-up and down rounds per year.
--
-- Deliberately includes companies that have since IPO'd or been acquired. Their
-- private rounds happened, and dropping them would be survivorship bias: the
-- companies that exited are disproportionately the ones that compounded fastest.
WITH steps AS (
    SELECT announced_date,
           post_money_usd
             / NULLIF(LAG(post_money_usd) OVER (PARTITION BY company_id ORDER BY announced_date), 0)
             AS step_up
    FROM fct_funding_round
    WHERE post_money_usd IS NOT NULL
)
SELECT EXTRACT(YEAR FROM announced_date)::INT                          AS year,
       COUNT(*)                                                        AS priced_rounds,
       COUNT(step_up)                                                  AS rounds_with_prior_mark,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY step_up)            AS median_step_up,
       COUNT(*) FILTER (WHERE step_up < 1)                             AS down_rounds
FROM steps
GROUP BY 1
ORDER BY 1;
