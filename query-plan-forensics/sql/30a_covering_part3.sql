\set ON_ERROR_STOP on
\timing on

\echo '--- part 3: the vacuum that makes the index-only scan real ---'
VACUUM (ANALYZE) stock_movements;

SELECT relname, relpages, relallvisible,
       round(relallvisible::numeric / NULLIF(relpages, 0), 4) AS all_visible_frac
FROM   pg_class WHERE relname = 'stock_movements';

\echo ''
\echo 'index sizes — the payload is not free'
SELECT indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size,
       idx_scan
FROM   pg_stat_user_indexes
WHERE  relname = 'stock_movements'
ORDER  BY pg_relation_size(indexrelid) DESC;
