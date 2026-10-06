-- Recent pipeline runs, newest first. Drives the pipeline-status cards: when did
-- ingestion last succeed, how many runs failed, how long do runs take.
SELECT run_id,
       command,
       status,
       started_at,
       finished_at,
       EXTRACT(EPOCH FROM finished_at - started_at)::NUMERIC(10, 1) AS duration_s,
       rows_fetched,
       rows_inserted,
       error
FROM etl_run
ORDER BY started_at DESC
LIMIT 200;
