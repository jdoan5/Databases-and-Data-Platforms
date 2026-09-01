-- ============================================================================
-- Stage 5c — the verdict on partitioning: revert it.
--
-- Same discipline as the BRIN index in 30d. Measured, concluded, removed.
--
--   Q07 +48.5%  (lost its parallel plan to a serial Append)
--   Q04 +42.3%  (one index descent became ten)
--   Q10 +35.5%  (Append over all 26 partitions)
--   Q05  -3.8%  (pruned to exactly one partition -- the entire upside)
--
-- Plus: no CREATE INDEX CONCURRENTLY on the parent, no global uniqueness on
-- movement_id, 104 index objects instead of four.
--
-- Partitioning is a data-lifecycle feature. It buys a 6.5ms DETACH+DROP against
-- a 682ms DELETE+VACUUM that returns no disk at all -- but this workload has no
-- retention policy to spend that on, and its query mix is cross-period, which
-- is the shape partitioning handles worst.
--
-- Keep 50 and 51 as the experiment. Do not keep the partitions.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

DROP TABLE IF EXISTS stock_movements_flat;

CREATE TABLE stock_movements_flat (
    LIKE stock_movements INCLUDING DEFAULTS INCLUDING CONSTRAINTS
);

INSERT INTO stock_movements_flat
SELECT * FROM stock_movements ORDER BY created_at;

-- Back to a single-column primary key. This is the point: on an unpartitioned
-- table movement_id alone is a legal identity again.
ALTER TABLE stock_movements_flat ADD PRIMARY KEY (movement_id);

ALTER TABLE stock_movements_flat
    ADD FOREIGN KEY (product_id)   REFERENCES products(product_id),
    ADD FOREIGN KEY (warehouse_id) REFERENCES warehouses(warehouse_id);

ALTER SEQUENCE stock_movements_movement_id_seq OWNED BY NONE;

DROP TABLE stock_movements CASCADE;
ALTER TABLE stock_movements_flat RENAME TO stock_movements;

ALTER SEQUENCE stock_movements_movement_id_seq
    OWNED BY stock_movements.movement_id;

-- The Stage 3/4 index set, minus everything those stages retired.
CREATE INDEX idx_movements_created   ON stock_movements (created_at DESC);
CREATE INDEX idx_movements_warehouse ON stock_movements (warehouse_id);
CREATE INDEX idx_sm_prod_created_incl
    ON stock_movements (product_id, created_at DESC)
    INCLUDE (warehouse_id, quantity, movement_type);

-- CASCADE took v_recent_movements again. Recreate only that one -- re-running
-- 04_views.sql would silently revert Stage 3b's v_low_stock_items.
CREATE VIEW v_recent_movements AS
SELECT sm.movement_id, sm.created_at, p.sku, p.name AS product_name,
       w.code AS warehouse_code, sm.movement_type, sm.quantity,
       sm.reference_type, sm.reference_id, sm.notes
FROM   stock_movements sm
JOIN   products   p ON p.product_id   = sm.product_id
JOIN   warehouses w ON w.warehouse_id = sm.warehouse_id
WHERE  sm.created_at >= CURRENT_DATE - INTERVAL '30 days';

CREATE TRIGGER trg_stock_movement_apply
    AFTER INSERT ON stock_movements
    FOR EACH ROW EXECUTE FUNCTION apply_stock_movement();

CREATE STATISTICS IF NOT EXISTS stx_sm_product_warehouse
    (ndistinct, dependencies, mcv)
    ON product_id, warehouse_id FROM stock_movements;

VACUUM (ANALYZE);

SELECT count(*) AS rows,
       pg_size_pretty(pg_table_size('stock_movements'))   AS heap,
       pg_size_pretty(pg_indexes_size('stock_movements')) AS indexes
FROM   stock_movements;
