-- Result of the last `pcd.cli check-links` run: does each cited source still load?
-- Some publishers block automated requests (403) even when the page is fine,
-- so unreachable means "a human should look", not "definitely broken".
SELECT source_url,
       SPLIT_PART(REGEXP_REPLACE(source_url, '^https?://(www\.)?', ''), '/', 1) AS domain,
       http_status,
       reachable,
       error,
       checked_at
FROM source_link_status
ORDER BY reachable, domain;
