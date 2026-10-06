-- One row per tracked company: the Yahoo-style "highest valued" leaderboard.
--
-- The interesting column is change_1y. It's a point-in-time join on the SCD2
-- dimension: "which version of this company was true exactly 365 days ago?"
-- Because every version carries the window it was valid for, that's a single
-- BETWEEN - no reconstructing history from raw events. This is the payoff for
-- modelling valuations as a Type 2 dimension instead of overwriting them.
WITH latest_round AS (
    -- most recent funding event of any kind (priced or not)
    SELECT DISTINCT ON (company_id)
           company_id,
           announced_date    AS last_round_date,
           round_stage       AS last_round_stage,
           amount_raised_usd AS last_raise_usd
    FROM fct_funding_round
    ORDER BY company_id, announced_date DESC, post_money_usd DESC NULLS LAST
),
totals AS (
    SELECT company_id,
           SUM(amount_raised_usd) AS raised_tracked_usd,
           COUNT(*)               AS n_rounds
    FROM fct_funding_round
    GROUP BY company_id
),
year_ago AS (
    -- valid_to is inclusive (a closed version ends the day before the next begins)
    SELECT company_id, latest_valuation_usd AS valuation_1y_ago
    FROM dim_company
    WHERE CURRENT_DATE - 365 BETWEEN valid_from AND valid_to
),
path AS (
    -- every mark in order, for the sparkline column
    SELECT company_id,
           ARRAY_AGG(ROUND(latest_valuation_usd / 1e9, 2) ORDER BY valid_from) AS valuation_path_b
    FROM dim_company
    WHERE latest_valuation_usd IS NOT NULL
    GROUP BY company_id
)
SELECT c.company_id,
       c.company_name,
       c.sector,
       c.subsector,
       c.status,
       c.status_note,
       c.ipo_watch_note,
       c.ipo_watch_url,
       c.ipo_watch_as_of,
       d.latest_valuation_usd,
       d.valuation_as_of,
       CURRENT_DATE - d.valuation_as_of                        AS days_since_mark,
       y.valuation_1y_ago,
       d.latest_valuation_usd / NULLIF(y.valuation_1y_ago, 0) - 1 AS change_1y,
       lr.last_round_date,
       lr.last_round_stage,
       lr.last_raise_usd,
       t.raised_tracked_usd,
       t.n_rounds,
       p.valuation_path_b,
       mark.source_url                                         AS mark_source_url
FROM ref_company c
LEFT JOIN dim_company d   ON d.company_id = c.company_id AND d.is_current
LEFT JOIN year_ago y      ON y.company_id = c.company_id
LEFT JOIN latest_round lr ON lr.company_id = c.company_id
LEFT JOIN totals t        ON t.company_id = c.company_id
LEFT JOIN path p          ON p.company_id = c.company_id
-- the source of the round that set the current mark, so the headline number links somewhere
LEFT JOIN LATERAL (
    SELECT f.source_url
    FROM fct_funding_round f
    WHERE f.company_id = c.company_id
      AND f.announced_date = d.valuation_as_of
      AND f.post_money_usd = d.latest_valuation_usd
    LIMIT 1
) mark ON TRUE
ORDER BY d.latest_valuation_usd DESC NULLS LAST;
