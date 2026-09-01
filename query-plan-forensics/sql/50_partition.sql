-- ============================================================================
-- Stage 5 — declarative range partitioning by month
--
-- stock_movements is an append-only ledger spanning 2024-09 to 2026-08. That is
-- the textbook case for range partitioning on created_at. This file does the
-- conversion and the next one prices the operational overhead.
--
-- Two consequences that are not optional and are worth stating on the page:
--
--   1. A partitioned table's PRIMARY KEY must contain every partition column.
--      movement_id alone is no longer a legal PK; it becomes
--      (movement_id, created_at). Global uniqueness on movement_id by itself is
--      simply not available in declarative partitioning -- the same row id
--      could exist in two partitions and nothing would catch it.
--
--   2. LIKE ... INCLUDING CONSTRAINTS copies CHECK constraints but NOT foreign
--      keys, and not the primary key. Both have to be re-added by hand, which
--      is exactly the kind of thing a conversion script silently drops.
--
-- This is an offline swap: build, copy, rename. An online conversion would use
-- ATTACH PARTITION against the existing table, which is priced in 51.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

DROP TABLE IF EXISTS stock_movements_part CASCADE;

CREATE TABLE stock_movements_part (
    LIKE stock_movements INCLUDING DEFAULTS INCLUDING CONSTRAINTS
) PARTITION BY RANGE (created_at);

-- 25 monthly partitions covering 2024-09 through 2026-09, plus a DEFAULT so a
-- row outside the range is captured rather than rejected. The DEFAULT is not
-- free -- see 51, where ATTACH has to scan it.
DO $$
DECLARE
    m date := DATE '2024-09-01';
BEGIN
    WHILE m < DATE '2026-10-01' LOOP
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF stock_movements_part
             FOR VALUES FROM (%L) TO (%L)',
            'stock_movements_p' || to_char(m, 'YYYY_MM'),
            m, m + INTERVAL '1 month');
        m := m + INTERVAL '1 month';
    END LOOP;
END $$;

CREATE TABLE stock_movements_pdefault
    PARTITION OF stock_movements_part DEFAULT;

\echo ''
\echo '=== copy 5M rows into the partitioned table ==='
INSERT INTO stock_movements_part
SELECT * FROM stock_movements ORDER BY created_at;

-- The PK must include the partition key. This is the constraint, not a choice.
ALTER TABLE stock_movements_part
    ADD PRIMARY KEY (movement_id, created_at);

-- LIKE did not bring these across.
ALTER TABLE stock_movements_part
    ADD FOREIGN KEY (product_id)   REFERENCES products(product_id),
    ADD FOREIGN KEY (warehouse_id) REFERENCES warehouses(warehouse_id);

-- Indexes on the parent cascade to every partition, existing and future.
CREATE INDEX idx_pmovements_created ON stock_movements_part (created_at DESC);
CREATE INDEX idx_pmovements_warehouse ON stock_movements_part (warehouse_id);
CREATE INDEX idx_psm_prod_created_incl
    ON stock_movements_part (product_id, created_at DESC)
    INCLUDE (warehouse_id, quantity, movement_type);

-- --------------------------------------------------------------------------
-- The swap. Detach the sequence first or DROP TABLE takes it with the column
-- that owns it, and the new table's copied DEFAULT starts referencing nothing.
-- --------------------------------------------------------------------------
ALTER SEQUENCE stock_movements_movement_id_seq OWNED BY NONE;

DROP TABLE stock_movements CASCADE;
ALTER TABLE stock_movements_part RENAME TO stock_movements;

ALTER SEQUENCE stock_movements_movement_id_seq
    OWNED BY stock_movements.movement_id;

-- CASCADE took exactly one view: v_recent_movements. Recreate that one only.
-- Re-running 04_views.sql here would look harmless and would silently revert
-- v_low_stock_items to its base cross-table definition, undoing Stage 3b and
-- quietly costing Q01 its partial index. Recreate what was dropped, not
-- everything that shares a file with it.
CREATE VIEW v_recent_movements AS
SELECT sm.movement_id, sm.created_at, p.sku, p.name AS product_name,
       w.code AS warehouse_code, sm.movement_type, sm.quantity,
       sm.reference_type, sm.reference_id, sm.notes
FROM   stock_movements sm
JOIN   products   p ON p.product_id   = sm.product_id
JOIN   warehouses w ON w.warehouse_id = sm.warehouse_id
WHERE  sm.created_at >= CURRENT_DATE - INTERVAL '30 days';

-- Row triggers on a partitioned parent cascade to partitions (PG13+).
CREATE TRIGGER trg_stock_movement_apply
    AFTER INSERT ON stock_movements
    FOR EACH ROW EXECUTE FUNCTION apply_stock_movement();

-- Extended statistics live on the table and died with it. Recreate them, or
-- the before/after comparison silently differs by more than partitioning.
CREATE STATISTICS stx_sm_product_warehouse (ndistinct, dependencies, mcv)
    ON product_id, warehouse_id FROM stock_movements;

VACUUM (ANALYZE) stock_movements;

\echo ''
\echo '=== 26 partitions, sized ==='
-- pg_total_relation_size on a partitioned PARENT returns the parent's own
-- storage, which is always zero. Sum the children or the number is a lie.
SELECT count(*)                                          AS partitions,
       pg_size_pretty(sum(pg_total_relation_size(c.oid))) AS total,
       pg_size_pretty(sum(pg_relation_size(c.oid)))       AS heap,
       pg_size_pretty(sum(pg_indexes_size(c.oid)))        AS indexes
FROM   pg_inherits i
JOIN   pg_class c ON c.oid = i.inhrelid
WHERE  i.inhparent = 'stock_movements'::regclass;

\echo ''
\echo '=== pruning: one month should touch one partition ==='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, SUMMARY OFF)
SELECT date_trunc('day', created_at), movement_type, count(*)
FROM   stock_movements
WHERE  created_at >= DATE '2026-06-01' AND created_at < DATE '2026-07-01'
GROUP  BY 1, 2;
