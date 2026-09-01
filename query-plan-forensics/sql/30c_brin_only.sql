-- ============================================================================
-- Stage 3c, part 2 — BRIN INSTEAD OF the B-tree, not alongside it.
--
-- Part 1 added BRIN next to the existing B-tree and nothing happened: no plan
-- changed, no buffer moved. That is the planner being right. Given both, a
-- 107MB B-tree with exact tuple pointers beats a 72kB summary index that has to
-- recheck every tuple in a candidate block range.
--
-- So the actual decision is a replacement, and it is a trade, not a win:
-- 1,523x less index to maintain, against whatever the recheck costs on each
-- query. Measure it before believing either direction.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

DROP INDEX IF EXISTS idx_movements_created;
ANALYZE stock_movements;

SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) AS size
FROM   pg_stat_user_indexes WHERE relname = 'stock_movements'
ORDER  BY pg_relation_size(indexrelid) DESC;
