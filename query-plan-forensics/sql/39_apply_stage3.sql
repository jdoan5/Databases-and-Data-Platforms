-- ============================================================================
-- 39_apply_stage3.sql — jump straight to Stage 3's end state.
--
-- The individual 30* files exist to be run one at a time with a benchmark
-- between each, which is how the numbers in STAGE3-RESULTS.md were produced.
-- This file is the destination only: the index set Stage 3 concluded with,
-- applied in one shot so Stage 4 has somewhere to start.
--
-- Deliberately absent: the BRIN. Stage 3c measured it as never chosen by the
-- planner when the B-tree exists, and an index the planner never chooses is
-- write overhead for no read benefit.
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

-- 3a — covering index (payload columns so Q04 never touches the heap)
DROP INDEX IF EXISTS idx_sm_prod_created;
CREATE INDEX IF NOT EXISTS idx_sm_prod_created_incl
    ON stock_movements (product_id, created_at DESC)
    INCLUDE (warehouse_id, quantity, movement_type);

-- 3b — the partial index that works
CREATE INDEX IF NOT EXISTS idx_po_open_expected
    ON purchase_orders (expected_date)
    WHERE status IN ('PLACED', 'PARTIALLY_RECEIVED');

-- 3b — the denormalization that makes Q01 indexable at all
ALTER TABLE stock_levels
    ADD COLUMN IF NOT EXISTS reorder_point INT NOT NULL DEFAULT 0;

UPDATE stock_levels sl
SET    reorder_point = p.reorder_point
FROM   products p
WHERE  p.product_id = sl.product_id
  AND  sl.reorder_point IS DISTINCT FROM p.reorder_point;

CREATE INDEX IF NOT EXISTS idx_sl_below_reorder
    ON stock_levels (warehouse_id, product_id)
    WHERE quantity < reorder_point;

CREATE OR REPLACE FUNCTION sync_reorder_point() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE stock_levels SET reorder_point = NEW.reorder_point
    WHERE  product_id = NEW.product_id;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_sync_reorder_point ON products;
CREATE TRIGGER trg_sync_reorder_point
    AFTER UPDATE OF reorder_point ON products
    FOR EACH ROW WHEN (OLD.reorder_point IS DISTINCT FROM NEW.reorder_point)
    EXECUTE FUNCTION sync_reorder_point();

CREATE OR REPLACE VIEW v_low_stock_items AS
SELECT p.sku, p.name AS product_name, w.code AS warehouse_code,
       sl.quantity, sl.reorder_point, p.reorder_quantity,
       (sl.reorder_point - sl.quantity) AS deficit,
       GREATEST(p.reorder_quantity, sl.reorder_point - sl.quantity) AS suggested_order_qty
FROM   stock_levels sl
JOIN   products   p ON p.product_id   = sl.product_id
JOIN   warehouses w ON w.warehouse_id = sl.warehouse_id
WHERE  sl.quantity < sl.reorder_point;

VACUUM (ANALYZE);
