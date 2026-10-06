-- Round-over-round valuation change.
--
-- The step-up multiple (this round's post-money / the previous round's) is how
-- investors talk about momentum. Below 1.0 is a down round: the company raised
-- at a lower valuation than it previously held.
--
-- Window functions do the work: LAG reaches back to the prior priced round for
-- the same company, so we never need a self-join.
WITH ordered AS (
    SELECT company_id,
           announced_date,
           round_stage,
           post_money_usd,
           amount_raised_usd,
           source_url,
           LAG(post_money_usd) OVER w AS prev_post_money,
           LAG(announced_date) OVER w AS prev_date,
           LAG(round_stage)    OVER w AS prev_round_stage
    FROM fct_funding_round
    WHERE post_money_usd IS NOT NULL
    WINDOW w AS (PARTITION BY company_id ORDER BY announced_date)
)
SELECT c.company_id,
       c.company_name,
       c.sector,
       c.status,
       o.announced_date,
       o.round_stage,
       o.post_money_usd,
       o.amount_raised_usd,
       o.prev_post_money,
       o.prev_date,
       o.prev_round_stage,
       o.post_money_usd / NULLIF(o.prev_post_money, 0)     AS step_up_multiple,
       o.post_money_usd / NULLIF(o.prev_post_money, 0) - 1 AS pct_change,
       o.announced_date - o.prev_date                      AS days_since_prev_round,
       o.source_url
FROM ordered o
JOIN ref_company c ON c.company_id = o.company_id
WHERE o.prev_post_money IS NOT NULL
ORDER BY o.announced_date DESC;
