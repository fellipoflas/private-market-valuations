-- Schema for the private company tracker.
--
-- Three layers, on purpose:
--   raw_*  -> exactly what the API gave us, never edited
--   stg_*  -> cleaned up, temporary staging for loads
--   dim_/fct_ -> the modeled tables the dashboard actually reads
--
-- The dim_/fct_ split is dimensional modeling (star schema). dim_ tables describe
-- *things*, fct_ tables record *events*. Naming it this way also means we could
-- drop dbt on top later without renaming anything.
--
-- Safe to re-run: everything is CREATE IF NOT EXISTS.


-- ============================================================
-- RAW LAYER
-- ============================================================

-- Grain: one row per API response we ever received.
-- Append-only. We never UPDATE or DELETE here.
--
-- Why bother? If the parsing code has a bug, we can re-parse from this table
-- instead of re-hitting the API. GDELT's Context API only keeps a few days of
-- history, so once we miss a window it's gone forever. This table is the
-- insurance policy.
CREATE TABLE IF NOT EXISTS raw_api_response (
    raw_id        BIGSERIAL PRIMARY KEY,
    source        TEXT        NOT NULL,   -- gdelt_context | gdelt_doc | bw_rss | pr_rss | hn
    query_key     TEXT        NOT NULL,   -- which company/query produced this
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload       JSONB       NOT NULL,
    payload_hash  TEXT        NOT NULL,

    -- This is what makes ingestion idempotent. Run the pipeline twice in one day
    -- and identical responses just get rejected instead of duplicating.
    CONSTRAINT raw_api_response_dedup UNIQUE (source, query_key, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_fetched_at ON raw_api_response (fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_source_query ON raw_api_response (source, query_key);


-- ============================================================
-- REFERENCE LAYER
-- ============================================================

-- Grain: one row per company we track. Mirrors config/companies.yml.
--
-- This is config-as-data: the YAML is the source of truth, this table is a
-- loaded copy so SQL can join against it. Type 1 on purpose - if a company's
-- sector is corrected, we just overwrite. We don't need the history of our own
-- classification decisions, only the history of their valuations.
CREATE TABLE IF NOT EXISTS ref_company (
    company_id   TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    sector       TEXT,
    subsector    TEXT,
    hq_country   TEXT,
    fp_risk      TEXT NOT NULL DEFAULT 'low',
    status       TEXT NOT NULL DEFAULT 'private',  -- private | public | acquired
    status_note  TEXT,
    status_source_url TEXT,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ref_company_status_valid
        CHECK (status IN ('private', 'public', 'acquired'))
);

-- Poor man's migration. CREATE TABLE IF NOT EXISTS won't add columns to a table
-- that already exists, so new columns need an explicit ALTER. Fine at this size;
-- if the schema keeps churning, graduate to a real migration tool.
ALTER TABLE ref_company ADD COLUMN IF NOT EXISTS status_source_url TEXT;

-- IPO watch: only set when a published source reports concrete IPO prep (banks
-- hired, confidential S-1, a stated timeline). The URL is mandatory - a check
-- fails if a note exists without one.
ALTER TABLE ref_company ADD COLUMN IF NOT EXISTS ipo_watch_note   TEXT;
ALTER TABLE ref_company ADD COLUMN IF NOT EXISTS ipo_watch_url    TEXT;
ALTER TABLE ref_company ADD COLUMN IF NOT EXISTS ipo_watch_as_of  DATE;


-- ============================================================
-- DIMENSION LAYER (SCD Type 2)
-- ============================================================

-- Grain: one row per company PER PERIOD DURING WHICH ITS ATTRIBUTES WERE UNCHANGED.
--
-- This is the Slowly Changing Dimension Type 2 pattern. When a company's valuation
-- changes, we do NOT overwrite the old value - we close out the old row and open a
-- new one. That way "what was Anduril worth in March 2024?" stays answerable.
--
-- Because there are multiple rows per company, company_id can't be the primary key.
-- That's why company_sk (surrogate key) exists.
CREATE TABLE IF NOT EXISTS dim_company (
    company_sk           BIGSERIAL PRIMARY KEY,  -- surrogate key, meaningless outside this table
    company_id           TEXT    NOT NULL,       -- natural key, e.g. 'anduril'
    company_name         TEXT    NOT NULL,
    sector               TEXT,
    hq_country           TEXT,
    founded_year         INT,

    -- the attributes we actually track changes on
    latest_valuation_usd NUMERIC(18,2),
    latest_round_stage   TEXT,
    valuation_as_of      DATE,                   -- when this valuation was announced

    -- SCD2 bookkeeping
    valid_from           DATE    NOT NULL,
    valid_to             DATE    NOT NULL DEFAULT DATE '9999-12-31',  -- sentinel = "still true"
    is_current           BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT dim_company_version UNIQUE (company_id, valid_from),
    CONSTRAINT dim_company_valid_range CHECK (valid_to >= valid_from)
);

-- Enforce "exactly one current row per company" at the DATABASE level, not in
-- application code. A partial unique index means a buggy load physically cannot
-- commit two current rows. This is cheap insurance and a good thing to point at
-- in an interview.
CREATE UNIQUE INDEX IF NOT EXISTS dim_company_one_current
    ON dim_company (company_id) WHERE is_current;

CREATE INDEX IF NOT EXISTS idx_dim_company_lookup
    ON dim_company (company_id, valid_from, valid_to);


-- ============================================================
-- FACT LAYER
-- ============================================================

-- Grain: one funding round per company.
-- This is the event table that feeds the SCD2 dimension above.
CREATE TABLE IF NOT EXISTS fct_funding_round (
    round_id          BIGSERIAL PRIMARY KEY,
    company_id        TEXT    NOT NULL,
    announced_date    DATE    NOT NULL,
    round_stage       TEXT,                  -- Seed / Series A / ... / Growth / Secondary
    amount_raised_usd NUMERIC(18,2),
    post_money_usd    NUMERIC(18,2),
    lead_investor     TEXT,

    -- Provenance. This is the most important part of the table.
    -- Every single number has to be traceable back to a real article.
    source_url        TEXT    NOT NULL,
    confidence        TEXT    NOT NULL,      -- curated | auto_high | auto_review
    extraction_method TEXT    NOT NULL,      -- seed_csv | regex | llm
    reviewed_by_human BOOLEAN NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fct_funding_round_natural_key UNIQUE (company_id, announced_date, round_stage),
    CONSTRAINT fct_funding_confidence_valid
        CHECK (confidence IN ('curated', 'auto_high', 'auto_review')),
    CONSTRAINT fct_funding_method_valid
        CHECK (extraction_method IN ('seed_csv', 'regex', 'llm')),
    -- you cannot raise more money than the company is worth afterwards
    CONSTRAINT fct_funding_amounts_sane
        CHECK (post_money_usd IS NULL OR post_money_usd > 0),
    CONSTRAINT fct_funding_raise_lte_postmoney
        CHECK (
            amount_raised_usd IS NULL
            OR post_money_usd IS NULL
            OR amount_raised_usd <= post_money_usd
        )
);

CREATE INDEX IF NOT EXISTS idx_funding_company_date
    ON fct_funding_round (company_id, announced_date DESC);

-- Lineage fields. notes = the curator's context from the seed CSV;
-- evidence_text = the exact sentence an auto-extracted figure came from, so
-- anyone can compare the number against its source text.
ALTER TABLE fct_funding_round ADD COLUMN IF NOT EXISTS notes         TEXT;
ALTER TABLE fct_funding_round ADD COLUMN IF NOT EXISTS evidence_text TEXT;


-- Grain: one DEDUPLICATED article per company mention.
--
-- Deduplication matters more than you'd think. Our first test run returned the
-- same syndicated press release six times from six different domains. Without
-- dedup, news-volume charts are just a measure of how syndicated a story was.
CREATE TABLE IF NOT EXISTS fct_news_mention (
    mention_id         BIGSERIAL PRIMARY KEY,
    company_id         TEXT NOT NULL,
    article_url        TEXT NOT NULL,
    url_canonical      TEXT NOT NULL,   -- lowercased, no UTM params, no trailing slash
    title              TEXT,
    title_norm         TEXT,            -- lowercased, punctuation stripped - fuzzy dedup key
    domain             TEXT,
    published_at       TIMESTAMPTZ,
    source             TEXT,            -- which ingester found it
    tone               NUMERIC(6,3),    -- GDELT document tone, roughly -10..+10
    sentence           TEXT,            -- GDELT Context sentence - where valuations come from
    is_funding_related BOOLEAN NOT NULL DEFAULT FALSE,
    raw_id             BIGINT REFERENCES raw_api_response(raw_id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- the canonical URL is the dedup key
    CONSTRAINT fct_news_mention_dedup UNIQUE (company_id, url_canonical)
);

CREATE INDEX IF NOT EXISTS idx_mention_company_published
    ON fct_news_mention (company_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_mention_title_norm
    ON fct_news_mention (title_norm);
CREATE INDEX IF NOT EXISTS idx_mention_funding
    ON fct_news_mention (company_id, published_at DESC) WHERE is_funding_related;


-- ============================================================
-- STAGING LAYER
-- ============================================================

-- Scratch table the SCD2 upsert reads from. Truncated and refilled each run.
-- Holds "what each company looks like right now" before we compare against
-- the current dim_company rows to decide what changed.
CREATE TABLE IF NOT EXISTS stg_company_current (
    company_id           TEXT PRIMARY KEY,
    company_name         TEXT NOT NULL,
    sector               TEXT,
    hq_country           TEXT,
    founded_year         INT,
    latest_valuation_usd NUMERIC(18,2),
    latest_round_stage   TEXT,
    valuation_as_of      DATE,
    effective_date       DATE NOT NULL   -- the date this version starts being true
);


-- ============================================================
-- OPERATIONS LAYER
-- ============================================================

-- Grain: one row per pipeline command run. This is what lets the dashboard say
-- "pipeline last ran 3 hours ago, 0 failures this week" instead of leaving you
-- to guess whether the cron is silently dead.
CREATE TABLE IF NOT EXISTS etl_run (
    run_id        BIGSERIAL PRIMARY KEY,
    command       TEXT        NOT NULL,          -- ingest | load-seed | check-links
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT        NOT NULL DEFAULT 'running',
    rows_fetched  INT,
    rows_inserted INT,
    error         TEXT,

    CONSTRAINT etl_run_status_valid CHECK (status IN ('running', 'success', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_etl_run_command_started ON etl_run (command, started_at DESC);

-- Grain: one row per distinct source URL, overwritten each time links are checked.
-- "Every figure has a source" means little if half the sources are 404s.
CREATE TABLE IF NOT EXISTS source_link_status (
    source_url  TEXT PRIMARY KEY,
    http_status INT,                 -- NULL when the request itself failed
    reachable   BOOLEAN     NOT NULL,
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    error       TEXT
);
