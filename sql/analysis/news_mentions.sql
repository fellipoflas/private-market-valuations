-- News coverage per company, from the GDELT firehose.
--
-- is_funding_related marks the sentences where a figure was actually
-- extractable AND the company was the subject of the sentence - not merely
-- named in passing. That distinction stops a company being credited with
-- someone else's funding round.
SELECT c.company_name,
       c.sector,
       c.status,
       m.domain,
       m.published_at,
       m.title,
       m.sentence,
       m.is_funding_related,
       m.source
FROM fct_news_mention m
JOIN ref_company c ON c.company_id = m.company_id
ORDER BY m.published_at DESC NULLS LAST;
