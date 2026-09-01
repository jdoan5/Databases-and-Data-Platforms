-- ============================================================================
-- Stage 4d — pay back what Stage 3 borrowed
--
-- The audit found indexes with zero scans across a full benchmark pass. Two of
-- them are not merely unused, they are SUPERSEDED -- Stage 3 built something
-- that covers their job and left them in place:
--
--   idx_movements_product (product_id)                      34 MB, 0 scans
--     -> idx_sm_prod_created_incl leads with product_id, so any lookup the
--        single-column index could serve, the composite serves too.
--
--   idx_po_status (status)                                 280 kB, 0 scans
--     -> idx_po_open_expected is a 96 kB partial index on exactly the status
--        values Q09 filters on.
--
-- IMPORTANT CAVEAT, and it belongs in the writeup: "never used" means never
-- used BY THE TEN BENCHMARK QUERIES. A real application has queries this
-- harness does not model. On a production system the same evidence would
-- justify an investigation, not an immediate DROP -- and you would check
-- pg_stat_user_indexes over weeks, not one benchmark pass.
-- ============================================================================

\set ON_ERROR_STOP on
\set batch 200000

\echo '=== before ==='
SELECT pg_size_pretty(pg_indexes_size('stock_movements')) AS sm_indexes,
       pg_size_pretty(pg_table_size('stock_movements'))   AS sm_heap,
       round(pg_indexes_size('stock_movements')::numeric
             / pg_table_size('stock_movements'), 2)       AS index_to_heap;

DROP INDEX IF EXISTS idx_movements_product;
DROP INDEX IF EXISTS idx_po_status;

\echo ''
\echo '=== after ==='
SELECT pg_size_pretty(pg_indexes_size('stock_movements')) AS sm_indexes,
       pg_size_pretty(pg_table_size('stock_movements'))   AS sm_heap,
       round(pg_indexes_size('stock_movements')::numeric
             / pg_table_size('stock_movements'), 2)       AS index_to_heap;

-- --------------------------------------------------------------------------
-- What the cleanup bought back on the write side.
-- Config D mirrors the post-cleanup index set: created_at, warehouse_id and
-- the covering index. Compare against B and C from 41_write_amplification.sql.
-- --------------------------------------------------------------------------
\echo ''
\echo '=== write cost after the cleanup (config D) ==='
DROP TABLE IF EXISTS bench_gen.wal_test;
CREATE TABLE bench_gen.wal_test (LIKE stock_movements INCLUDING DEFAULTS);
CREATE INDEX wt_warehouse ON bench_gen.wal_test (warehouse_id);
CREATE INDEX wt_created   ON bench_gen.wal_test (created_at DESC);
CREATE INDEX wt_covering  ON bench_gen.wal_test (product_id, created_at DESC)
    INCLUDE (warehouse_id, quantity, movement_type);

CHECKPOINT;
SELECT pg_current_wal_lsn() AS lsn0, clock_timestamp() AS t0 \gset

INSERT INTO bench_gen.wal_test (product_id, warehouse_id, movement_type,
                                quantity, reference_type, created_at)
SELECT 1 + (g % 60000), 1 + (g % 6), 'OUT', 1 + (g % 50), 'SYNTHETIC',
       TIMESTAMP '2026-09-01' + (g || ' seconds')::interval
FROM   generate_series(1, :batch) g;

SELECT 'D post-cleanup (3)' AS config,
       round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz) * 1000, 1) AS ms,
       round(:batch / extract(epoch FROM clock_timestamp() - :'t0'::timestamptz)) AS rows_per_sec,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), :'lsn0')) AS wal,
       round(pg_wal_lsn_diff(pg_current_wal_lsn(), :'lsn0')::numeric / :batch) AS wal_bytes_per_row;

DROP TABLE bench_gen.wal_test;
ANALYZE;
