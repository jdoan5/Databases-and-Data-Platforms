-- ============================================================================
-- Stage 4b — what Stage 3's indexes cost on the write side
--
-- Stage 3 reported three queries 12-17x faster. It did not report the bill.
-- stock_movements is an append-only ledger: every index is paid for on every
-- insert, forever, whether or not any query uses it.
--
-- Measured on a scratch table with the same shape, at three index
-- configurations, with both wall time and WAL bytes generated. WAL matters
-- more than time here -- it is what replication ships, what backups store, and
-- what a cloud provider bills for.
--
-- CHECKPOINT before each run: the first write to a page after a checkpoint
-- emits a full-page image, so without this the first configuration measured
-- absorbs everyone else's full-page writes and looks artificially expensive.
-- ============================================================================

\set ON_ERROR_STOP on
\set batch 200000

DROP TABLE IF EXISTS bench_gen.wal_test;
CREATE TABLE bench_gen.wal_test (LIKE stock_movements INCLUDING DEFAULTS);

CREATE TEMP TABLE wal_result (
    config      TEXT,
    indexes     INT,
    ms          NUMERIC,
    wal_bytes   BIGINT,
    index_bytes BIGINT
);

-- ---------------------------------------------------------------- config A --
\echo '=== A: heap only, no indexes ==='
CHECKPOINT;
SELECT pg_current_wal_lsn() AS lsn0, clock_timestamp() AS t0 \gset

INSERT INTO bench_gen.wal_test (product_id, warehouse_id, movement_type,
                                quantity, reference_type, created_at)
SELECT 1 + (g % 60000), 1 + (g % 6), 'OUT', 1 + (g % 50), 'SYNTHETIC',
       TIMESTAMP '2026-09-01' + (g || ' seconds')::interval
FROM   generate_series(1, :batch) g;

INSERT INTO wal_result
SELECT 'A heap only', 0,
       round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz) * 1000, 1),
       pg_wal_lsn_diff(pg_current_wal_lsn(), :'lsn0'),
       0;

-- ---------------------------------------------------------------- config B --
\echo '=== B: the three original indexes ==='
TRUNCATE bench_gen.wal_test;
CREATE INDEX wt_product   ON bench_gen.wal_test (product_id);
CREATE INDEX wt_warehouse ON bench_gen.wal_test (warehouse_id);
CREATE INDEX wt_created   ON bench_gen.wal_test (created_at DESC);
CHECKPOINT;
SELECT pg_current_wal_lsn() AS lsn0, clock_timestamp() AS t0 \gset

INSERT INTO bench_gen.wal_test (product_id, warehouse_id, movement_type,
                                quantity, reference_type, created_at)
SELECT 1 + (g % 60000), 1 + (g % 6), 'OUT', 1 + (g % 50), 'SYNTHETIC',
       TIMESTAMP '2026-09-01' + (g || ' seconds')::interval
FROM   generate_series(1, :batch) g;

INSERT INTO wal_result
SELECT 'B baseline (3)', 3,
       round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz) * 1000, 1),
       pg_wal_lsn_diff(pg_current_wal_lsn(), :'lsn0'),
       pg_indexes_size('bench_gen.wal_test');

-- ---------------------------------------------------------------- config C --
\echo '=== C: baseline three plus the Stage 3 covering index ==='
TRUNCATE bench_gen.wal_test;
CREATE INDEX wt_covering ON bench_gen.wal_test (product_id, created_at DESC)
    INCLUDE (warehouse_id, quantity, movement_type);
CHECKPOINT;
SELECT pg_current_wal_lsn() AS lsn0, clock_timestamp() AS t0 \gset

INSERT INTO bench_gen.wal_test (product_id, warehouse_id, movement_type,
                                quantity, reference_type, created_at)
SELECT 1 + (g % 60000), 1 + (g % 6), 'OUT', 1 + (g % 50), 'SYNTHETIC',
       TIMESTAMP '2026-09-01' + (g || ' seconds')::interval
FROM   generate_series(1, :batch) g;

INSERT INTO wal_result
SELECT 'C + covering (4)', 4,
       round(extract(epoch FROM clock_timestamp() - :'t0'::timestamptz) * 1000, 1),
       pg_wal_lsn_diff(pg_current_wal_lsn(), :'lsn0'),
       pg_indexes_size('bench_gen.wal_test');

-- ------------------------------------------------------------------ report --
\echo ''
\echo '=== the bill for Stage 3, per 200,000 inserted rows ==='
SELECT config,
       indexes,
       ms,
       round(:batch / (ms / 1000)) AS rows_per_sec,
       pg_size_pretty(wal_bytes)   AS wal,
       round(wal_bytes::numeric / :batch) AS wal_bytes_per_row,
       pg_size_pretty(index_bytes) AS index_size,
       round(ms / first_value(ms) OVER (ORDER BY indexes), 2)        AS time_x,
       round(wal_bytes::numeric
             / first_value(wal_bytes) OVER (ORDER BY indexes), 2)    AS wal_x
FROM   wal_result
ORDER  BY indexes;

DROP TABLE bench_gen.wal_test;
