-- ============================================================================
-- Stage 3c — BRIN vs. B-tree on the append-only created_at
--
-- This is where the ORDER BY in the generator pays off. BRIN stores one summary
-- per block range; it is only useful when physical order tracks logical order.
-- pg_stats.correlation is 1.0 here because the loader emitted rows sorted.
--
-- The headline is the size ratio, not the speed: BRIN is orders of magnitude
-- smaller. Whether it is also FASTER depends on the query, and Q05 vs Q03 will
-- disagree -- which is the point.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

\echo 'correlation — BRIN is only viable because this is near 1.0'
SELECT attname, round(correlation::numeric, 4) AS correlation
FROM   pg_stats
WHERE  tablename = 'stock_movements' AND attname = 'created_at';

CREATE INDEX IF NOT EXISTS idx_sm_created_brin
    ON stock_movements USING BRIN (created_at)
    WITH (pages_per_range = 32, autosummarize = on);

ANALYZE stock_movements;

\echo ''
\echo 'size: B-tree vs BRIN over the same column'
SELECT indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size,
       pg_relation_size(indexrelid)                 AS bytes
FROM   pg_stat_user_indexes
WHERE  relname = 'stock_movements'
  AND  indexrelname IN ('idx_movements_created', 'idx_sm_created_brin')
ORDER  BY bytes DESC;
