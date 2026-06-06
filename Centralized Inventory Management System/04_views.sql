-- ============================================================================
-- 04_views.sql  -  reusable views that wrap common reporting queries.
-- A view is just a saved SELECT -- query it like a table.
-- ============================================================================

DROP VIEW IF EXISTS v_low_stock_items     CASCADE;
DROP VIEW IF EXISTS v_current_stock       CASCADE;
DROP VIEW IF EXISTS v_purchase_order_summary CASCADE;
DROP VIEW IF EXISTS v_stock_valuation     CASCADE;
DROP VIEW IF EXISTS v_recent_movements    CASCADE;


-- ----------------------------------------------------------------------------
-- v_current_stock  -  flat join of product x warehouse x quantity.
-- The "go-to" view for any stock-on-hand question.
-- ----------------------------------------------------------------------------
CREATE VIEW v_current_stock AS
SELECT
    p.product_id,
    p.sku,
    p.name           AS product_name,
    c.name           AS category,
    w.warehouse_id,
    w.code           AS warehouse_code,
    w.name           AS warehouse_name,
    sl.quantity,
    p.reorder_point,
    p.reorder_quantity,
    sl.last_updated
FROM stock_levels sl
JOIN products     p ON p.product_id   = sl.product_id
JOIN warehouses   w ON w.warehouse_id = sl.warehouse_id
LEFT JOIN categories c ON c.category_id = p.category_id;


-- ----------------------------------------------------------------------------
-- v_low_stock_items  -  every (product, warehouse) needing reorder.
-- Drives an automated purchasing report.
-- ----------------------------------------------------------------------------
CREATE VIEW v_low_stock_items AS
SELECT
    sku,
    product_name,
    warehouse_code,
    quantity,
    reorder_point,
    reorder_quantity,
    (reorder_point - quantity)             AS deficit,
    GREATEST(reorder_quantity,
             reorder_point - quantity)     AS suggested_order_qty
FROM v_current_stock
WHERE quantity < reorder_point;


-- ----------------------------------------------------------------------------
-- v_purchase_order_summary  -  one row per PO with totals.
-- Aggregates line items so the dashboard doesn't have to.
-- ----------------------------------------------------------------------------
CREATE VIEW v_purchase_order_summary AS
SELECT
    po.po_id,
    po.po_number,
    s.name                                   AS supplier_name,
    w.code                                   AS warehouse_code,
    po.status,
    po.order_date,
    po.expected_date,
    po.received_date,
    COUNT(poi.po_item_id)                    AS line_count,
    SUM(poi.quantity_ordered)                AS total_ordered,
    SUM(poi.quantity_received)               AS total_received,
    SUM(poi.quantity_ordered * poi.unit_cost) AS total_value
FROM purchase_orders        po
JOIN suppliers              s   ON s.supplier_id  = po.supplier_id
JOIN warehouses             w   ON w.warehouse_id = po.warehouse_id
LEFT JOIN purchase_order_items poi ON poi.po_id    = po.po_id
GROUP BY po.po_id, po.po_number, s.name, w.code,
         po.status, po.order_date, po.expected_date, po.received_date;


-- ----------------------------------------------------------------------------
-- v_stock_valuation  -  $ value of inventory per warehouse.
-- Uses unit_cost (what you paid), not unit_price (what you charge).
-- ----------------------------------------------------------------------------
CREATE VIEW v_stock_valuation AS
SELECT
    w.warehouse_id,
    w.code                            AS warehouse_code,
    w.name                            AS warehouse_name,
    COUNT(DISTINCT sl.product_id)     AS distinct_skus,
    SUM(sl.quantity)                  AS total_units,
    SUM(sl.quantity * p.unit_cost)    AS valuation_at_cost,
    SUM(sl.quantity * p.unit_price)   AS valuation_at_retail
FROM warehouses    w
LEFT JOIN stock_levels sl ON sl.warehouse_id = w.warehouse_id
LEFT JOIN products     p  ON p.product_id    = sl.product_id
GROUP BY w.warehouse_id, w.code, w.name;


-- ----------------------------------------------------------------------------
-- v_recent_movements  -  last 30 days of activity, denormalized.
-- ----------------------------------------------------------------------------
CREATE VIEW v_recent_movements AS
SELECT
    sm.movement_id,
    sm.created_at,
    p.sku,
    p.name              AS product_name,
    w.code              AS warehouse_code,
    sm.movement_type,
    sm.quantity,
    sm.reference_type,
    sm.reference_id,
    sm.notes
FROM stock_movements sm
JOIN products    p ON p.product_id   = sm.product_id
JOIN warehouses  w ON w.warehouse_id = sm.warehouse_id
WHERE sm.created_at >= CURRENT_DATE - INTERVAL '30 days';


-- ----------------------------------------------------------------------------
-- Try them out:
-- ----------------------------------------------------------------------------
-- SELECT * FROM v_current_stock         ORDER BY warehouse_code, sku;
-- SELECT * FROM v_low_stock_items       ORDER BY deficit DESC;
-- SELECT * FROM v_purchase_order_summary ORDER BY order_date DESC;
-- SELECT * FROM v_stock_valuation;
-- SELECT * FROM v_recent_movements      ORDER BY created_at DESC;
