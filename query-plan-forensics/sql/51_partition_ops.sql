-- ============================================================================
-- Stage 5b — what partitioning costs to operate, and the one thing it buys
--
-- The query benchmark says partitioning made this workload worse. That is only
-- half an answer: nobody partitions a ledger for query speed alone. The real
-- case is data lifecycle -- dropping a month should be instant instead of a
-- DELETE that rewrites the table and leaves bloat behind.
--
-- Measured here, alongside the three operational costs the tutorials skip.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

\echo '=== how many objects does 26 partitions actually create? ==='
SELECT count(DISTINCT c.oid)          AS partitions,
       count(i.indexrelid)            AS partition_indexes
FROM   pg_inherits inh
JOIN   pg_class c ON c.oid = inh.inhrelid
LEFT   JOIN pg_index i ON i.indrelid = c.oid
WHERE  inh.inhparent = 'stock_movements'::regclass;

\echo ''
\echo '=== COST 1: CREATE INDEX CONCURRENTLY is not available on the parent ==='
-- Run bare, NOT inside a DO block. CIC cannot run in a transaction at all, so a
-- DO block reports "cannot run inside a transaction block" and hides the error
-- actually being demonstrated. First version of this file made that mistake and
-- proved nothing.
--
-- On an unpartitioned table you add an index online. On a partitioned parent
-- the options are: take the lock, or CIC each partition individually, then
-- CREATE INDEX ON ONLY the parent and ATTACH each child index by hand.
\set ON_ERROR_STOP off
CREATE INDEX CONCURRENTLY idx_sm_qty ON stock_movements (quantity);
\set ON_ERROR_STOP on

\echo ''
\echo '=== COST 2: ATTACH must scan the DEFAULT partition ==='
-- A DEFAULT partition means every ATTACH has to prove no row already sitting in
-- DEFAULT belongs in the incoming range. That scan happens under
-- ACCESS EXCLUSIVE on the default partition -- readers block.
-- INCLUDING CONSTRAINTS matters: without it the child lacks the parent's CHECK
-- constraints and ATTACH is rejected outright with
-- "child table is missing constraint stock_movements_quantity_check".
CREATE TABLE sm_2026_10 (LIKE stock_movements INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
-- A matching CHECK lets ATTACH skip its validation scan of the incoming table.
-- It does NOT exempt the DEFAULT partition from being scanned.
ALTER TABLE sm_2026_10 ADD CONSTRAINT ck
    CHECK (created_at >= DATE '2026-10-01' AND created_at < DATE '2026-11-01');

\echo 'attaching a new month (DEFAULT partition present and non-empty check):'
ALTER TABLE stock_movements
    ATTACH PARTITION sm_2026_10
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

\echo ''
\echo '=== COST 3: the partition key is forced into every unique constraint ==='
DO $$
BEGIN
    EXECUTE 'ALTER TABLE stock_movements ADD CONSTRAINT uq_movement_id
             UNIQUE (movement_id)';
    RAISE NOTICE 'UNEXPECTED: got a global unique on movement_id alone.';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'As expected: %', SQLERRM;
    RAISE NOTICE 'Global uniqueness on movement_id is not available. The PK is';
    RAISE NOTICE '(movement_id, created_at); the same id could exist in two';
    RAISE NOTICE 'partitions and nothing would catch it.';
END $$;

\echo ''
\echo '=== THE PAYOFF: expiring one month of data ==='
\echo '--- partitioned: DETACH + DROP ---'
ALTER TABLE stock_movements DETACH PARTITION stock_movements_p2024_09;
DROP TABLE stock_movements_p2024_09;

\echo ''
\echo '--- unpartitioned equivalent: DELETE the same range, then reclaim ---'
CREATE TABLE sm_flat AS SELECT * FROM stock_movements;
CREATE INDEX ON sm_flat (created_at DESC);
ANALYZE sm_flat;
SELECT pg_size_pretty(pg_total_relation_size('sm_flat')) AS before_delete;

DELETE FROM sm_flat WHERE created_at < DATE '2024-11-01';
SELECT pg_size_pretty(pg_total_relation_size('sm_flat')) AS after_delete_before_vacuum;

VACUUM (ANALYZE) sm_flat;
SELECT pg_size_pretty(pg_total_relation_size('sm_flat')) AS after_vacuum;

DROP TABLE sm_flat;

\echo ''
\echo '=== remaining partitions ==='
SELECT count(*) AS partitions,
       pg_size_pretty(sum(pg_total_relation_size(c.oid))) AS total
FROM   pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE  i.inhparent = 'stock_movements'::regclass;
