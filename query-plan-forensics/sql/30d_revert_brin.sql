-- ============================================================================
-- Stage 3d — the verdict on BRIN: revert it.
--
-- Two measurements decided this, neither of them a matter of taste:
--   * BRIN alongside the B-tree changed no plan and moved no buffer. The
--     planner correctly preferred exact tuple pointers over a block-range
--     summary that has to recheck.
--   * BRIN INSTEAD of the B-tree saved 1,523x in index size and destroyed Q03
--     by 33x, because BRIN cannot produce ordered output and an
--     ORDER BY created_at DESC LIMIT 200 then has to sort the whole window.
--
-- An index the planner never chooses is not free -- it is write overhead on
-- every insert for zero read benefit. So it goes.
--
-- BRIN would be the right call on this column if the workload were purely
-- range-aggregate with no ordered access. It is not.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

CREATE INDEX IF NOT EXISTS idx_movements_created
    ON stock_movements (created_at DESC);

DROP INDEX IF EXISTS idx_sm_created_brin;

ANALYZE stock_movements;

SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM   pg_stat_user_indexes WHERE relname = 'stock_movements'
ORDER  BY pg_relation_size(indexrelid) DESC;
