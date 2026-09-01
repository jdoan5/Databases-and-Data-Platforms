-- ============================================================================
-- Stage 4c — which indexes actually earn their keep
--
-- pg_stat_user_indexes.idx_scan counts since the last stats reset. Run this
-- AFTER a full benchmark pass so the counts reflect the real query mix rather
-- than whatever happened to be run by hand.
--
-- An index with idx_scan = 0 is not neutral: it is paid for on every insert,
-- it enlarges every backup, and it is one more thing VACUUM has to walk.
-- ============================================================================

\set ON_ERROR_STOP on

SELECT
    i.relname                                       AS table,
    i.indexrelname                                  AS index,
    i.idx_scan                                      AS scans,
    pg_size_pretty(pg_relation_size(i.indexrelid))  AS size,
    CASE WHEN i.idx_scan = 0 THEN 'NEVER USED'
         WHEN i.idx_scan < 10 THEN 'rarely'
         ELSE '' END                                AS verdict
FROM   pg_stat_user_indexes i
JOIN   pg_index x ON x.indexrelid = i.indexrelid
WHERE  NOT x.indisprimary AND NOT x.indisunique
ORDER  BY i.idx_scan, pg_relation_size(i.indexrelid) DESC;

\echo ''
\echo '=== index footprint vs heap, per table ==='
SELECT relname AS table,
       pg_size_pretty(pg_table_size(relid))   AS heap,
       pg_size_pretty(pg_indexes_size(relid)) AS indexes,
       round(pg_indexes_size(relid)::numeric
             / NULLIF(pg_table_size(relid), 0), 2) AS index_to_heap
FROM   pg_stat_user_tables
WHERE  pg_table_size(relid) > 1024 * 1024
ORDER  BY pg_indexes_size(relid) DESC;
