# Private Company News & Valuation Tracker

## Project Goal
A portfolio project demonstrating end-to-end data analytics skills for data analyst job
applications. Tracks funding news and valuation changes for ~37 well-known private companies.

## Tech Stack
- **Language**: Python 3.12
- **Ingestion**: GDELT Context 2.0 API (free, no key) + curated seed CSV
- **Storage**: PostgreSQL (local for dev, Neon for deployment)
- **Dashboard**: Streamlit
- **Scheduling/CI**: GitHub Actions
- **Deployment target**: Streamlit Community Cloud

## Context
- I'm a data science undergrad (SJSU) targeting **data analyst** roles, with growing interest in ML.
- I intern at a fintech startup in private markets/secondary trades in private equity, which
  inspired this project. No confidential data is used — only public company names and publicly
  reported news.
- I prefer learning by doing: explain what you're building and why as you go.
- Keep comments plain and informal, not overly formal.

## Architecture
```
GDELT firehose ─┐
curated seed ───┼─> raw_api_response ─> dedup/extract ─> fct_* / dim_company ─> Streamlit
                                                              (SCD Type 2)
```

**The core modeling idea**: valuation-over-time is a Slowly Changing Dimension Type 2 problem.
`dim_company` keeps one row per company per period, with `valid_from` / `valid_to` / `is_current`,
so "what was Anduril worth in March 2024?" stays answerable. Implemented in plain SQL
(`sql/02_scd2_upsert.sql`), no dbt — but the layer naming (`raw_` / `stg_` / `dim_` / `fct_`)
means dbt could wrap it later without renaming anything.

## Data sources — decisions already made, don't re-litigate
- **NewsData.io was abandoned.** Free tier paywalls article body text, delays data ~12h, and
  `/latest` reaches back only 48 hours. You cannot build a historical tracker on it.
- **No free structured valuation source exists.** SEC Form D reports amount raised but never
  valuation, and Anduril/ElevenLabs have no company Form D at all (EDGAR hits are third-party
  SPVs). Crunchbase killed its free API in 2025.
- **GDELT Context 2.0** is the news source. Two gotchas, both hard-won:
  - `mode=artlist` is **required** — without it you get HTTP 200 and an empty list, every time.
  - The window is only **~15 minutes wide**, so we run one broad "funding firehose" query and
    filter locally rather than querying per company. It accumulates forward and **cannot backfill**.
- Therefore: **hybrid strategy.** `data/seed/valuations_seed.csv` supplies hand-verified history
  (every row has a `source_url`); GDELT detects new rounds going forward.

## Working Style / Conventions
- Build incrementally: ingestion before storage, storage before dashboard.
- Favor clear, well-commented code over clever one-liners — this needs to be explainable in an
  interview.
- **Provenance rule**: every figure must trace to a real, clickable source. If you can't link it,
  don't assert it. This applies to company status claims too, not just funding numbers.
- Re-check company `status` before each seed refresh — 5 of the original 37 had already IPO'd or
  been acquired.
- Use the virtual environment (`venv/`). Secrets live in `.env`, never in git.
- Note assumptions out loud rather than silently picking one.

## Commands
```bash
python -m pcd.cli setup      # create/migrate tables
python -m pcd.cli load-seed  # config + curated rounds + SCD2 history replay
python -m pcd.cli ingest     # one pass over the GDELT funding firehose
python -m pcd.cli check      # data quality gate (exits 1 on failure)
python -m pcd.cli status     # what's in the database right now
pytest                       # unit tests
ruff check src/ tests/       # lint
```

## Out of scope for now
- Scraping (API + RSS only)
- Authentication/user accounts
- Real-time/streaming updates — scheduled refresh is fine
- PySpark (the dataset is ~100 rows and ~20k articles; Pandas is correct here)
