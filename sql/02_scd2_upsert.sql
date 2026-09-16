-- SCD Type 2 upsert for dim_company.
--
-- Assumes stg_company_current has already been filled with "what each company
-- looks like right now".
--
-- Two steps, one transaction:
--   1. Find companies whose tracked attributes changed. Close their current row.
--   2. Insert a fresh current row for any company that doesn't have one.
--
-- Step 2 picks up both "changed" companies (step 1 just closed their row) and
-- brand new companies (never had a row). That's why the order matters.
--
-- This is idempotent: run it twice with the same staging data and the second run
-- changes nothing, because nothing will compare as different.

BEGIN;

-- ------------------------------------------------------------
-- Step 1: close out rows whose tracked attributes changed
-- ------------------------------------------------------------
UPDATE dim_company d
SET valid_to   = s.effective_date - 1,
    is_current = FALSE
FROM stg_company_current s
WHERE d.company_id = s.company_id
  AND d.is_current
  -- Only close the row if something we care about actually changed.
  --
  -- IS DISTINCT FROM (not <>) because we have to handle NULLs. In SQL,
  -- NULL <> NULL is NULL, not TRUE - so a plain <> would silently miss changes
  -- involving NULL. IS DISTINCT FROM treats NULL as a comparable value.
  -- Comparing whole row constructors like this checks all columns at once.
  AND (d.latest_valuation_usd, d.latest_round_stage, d.sector, d.company_name)
      IS DISTINCT FROM
      (s.latest_valuation_usd, s.latest_round_stage, s.sector, s.company_name)
  -- Guard: never create a backwards date range. If the incoming effective_date
  -- is not strictly after the existing row's start, we'd write valid_to < valid_from
  -- and trip the CHECK constraint. Out-of-order data should be skipped, not crash.
  AND s.effective_date > d.valid_from;

-- ------------------------------------------------------------
-- Step 2: open a new current row for anyone missing one
-- ------------------------------------------------------------
INSERT INTO dim_company (
    company_id, company_name, sector, hq_country, founded_year,
    latest_valuation_usd, latest_round_stage, valuation_as_of,
    valid_from, valid_to, is_current
)
SELECT s.company_id,
       s.company_name,
       s.sector,
       s.hq_country,
       s.founded_year,
       s.latest_valuation_usd,
       s.latest_round_stage,
       s.valuation_as_of,
       s.effective_date,
       DATE '9999-12-31',
       TRUE
FROM stg_company_current s
LEFT JOIN dim_company d
       ON d.company_id = s.company_id
      AND d.is_current
WHERE d.company_sk IS NULL
-- Belt and braces: don't collide with an existing version that starts on the
-- same date (the UNIQUE (company_id, valid_from) constraint would reject it).
ON CONFLICT (company_id, valid_from) DO NOTHING;

COMMIT;
