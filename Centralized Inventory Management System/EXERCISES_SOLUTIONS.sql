-- ============================================================================
-- EXERCISES_SOLUTIONS.sql  -  one correct answer per question.
-- There's often more than one valid way; if yours returns the same rows, it's
-- right. Compare approaches - that's where the learning is.
-- ============================================================================


-- Q1 -------------------------------------------------------------------------
SELECT p.sku, p.name, p.unit_price
FROM   products   p
JOIN   categories c ON c.category_id = p.category_id
WHERE  c.name = 'Audio'
ORDER BY p.name;


-- Q2 -------------------------------------------------------------------------
-- LEFT JOIN keeps categories with no products; COUNT(p.*) counts only matches.
SELECT c.name AS category, COUNT(p.product_id) AS product_count
FROM   categories c
LEFT JOIN products p ON p.category_id = c.category_id
GROUP BY c.name
ORDER BY product_count DESC, c.name;


-- Q3 -------------------------------------------------------------------------
SELECT w.code AS warehouse,
       SUM(sl.quantity)                AS total_units,
       SUM(sl.quantity * p.unit_cost)  AS value_at_cost
FROM   warehouses   w
JOIN   stock_levels sl ON sl.warehouse_id = w.warehouse_id
JOIN   products     p  ON p.product_id    = sl.product_id
GROUP BY w.code
ORDER BY value_at_cost DESC;


-- Q4 -------------------------------------------------------------------------
SELECT p.sku, p.name, w.code AS warehouse, sl.quantity, p.reorder_point
FROM   stock_levels sl
JOIN   products     p ON p.product_id   = sl.product_id
JOIN   warehouses   w ON w.warehouse_id = sl.warehouse_id
WHERE  sl.quantity < p.reorder_point
ORDER BY (p.reorder_point - sl.quantity) DESC;


-- Q5 -------------------------------------------------------------------------
-- LEFT JOINs so suppliers with no POs survive; COALESCE turns their NULL sum to 0.
SELECT s.name AS supplier,
       COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0) AS total_po_value
FROM   suppliers s
LEFT JOIN purchase_orders      po  ON po.supplier_id = s.supplier_id
LEFT JOIN purchase_order_items poi ON poi.po_id      = po.po_id
GROUP BY s.name
ORDER BY total_po_value DESC;


-- Q6 -------------------------------------------------------------------------
SELECT p.sku, p.name,
       SUM(sl.quantity * p.unit_cost) AS total_value
FROM   products     p
JOIN   stock_levels sl ON sl.product_id = p.product_id
GROUP BY p.sku, p.name
ORDER BY total_value DESC
LIMIT 3;


-- Q7 -------------------------------------------------------------------------
-- SUM(...) OVER () with no PARTITION = grand total on every row.
WITH wh AS (
    SELECT w.code AS warehouse,
           SUM(sl.quantity * p.unit_cost) AS value_at_cost
    FROM   warehouses   w
    JOIN   stock_levels sl ON sl.warehouse_id = w.warehouse_id
    JOIN   products     p  ON p.product_id    = sl.product_id
    GROUP BY w.code
)
SELECT warehouse,
       value_at_cost,
       RANK() OVER (ORDER BY value_at_cost DESC)                       AS rank,
       ROUND(100.0 * value_at_cost / SUM(value_at_cost) OVER (), 1)    AS pct_of_total
FROM   wh
ORDER BY rank;


-- Q8 -------------------------------------------------------------------------
SELECT p.sku, p.name
FROM   products p
WHERE  NOT EXISTS (
    SELECT 1 FROM stock_movements sm
    WHERE  sm.product_id = p.product_id
      AND  sm.movement_type = 'OUT'
)
ORDER BY p.sku;

-- equivalent LEFT JOIN form:
-- SELECT p.sku, p.name
-- FROM products p
-- LEFT JOIN stock_movements sm
--        ON sm.product_id = p.product_id AND sm.movement_type = 'OUT'
-- WHERE sm.movement_id IS NULL
-- ORDER BY p.sku;


-- Q9 -------------------------------------------------------------------------
-- NULLIF(x,0) makes the denominator NULL when 0, so the division yields NULL
-- instead of erroring; COALESCE then turns that into 0.
SELECT po.po_number,
       po.status,
       COALESCE(ROUND(
           100.0 * SUM(poi.quantity_received) / NULLIF(SUM(poi.quantity_ordered), 0)
       , 1), 0) AS percent_received
FROM   purchase_orders        po
LEFT JOIN purchase_order_items poi ON poi.po_id = po.po_id
GROUP BY po.po_number, po.status
ORDER BY percent_received DESC, po.po_number;


-- Q10 ------------------------------------------------------------------------
SELECT p.sku, p.name,
       SUM(CASE WHEN sm.movement_type IN ('IN','RETURN','TRANSFER_IN')
                THEN sm.quantity ELSE -sm.quantity END) AS net_change
FROM   stock_movements sm
JOIN   products        p ON p.product_id = sm.product_id
WHERE  sm.created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY p.sku, p.name
HAVING SUM(CASE WHEN sm.movement_type IN ('IN','RETURN','TRANSFER_IN')
                THEN sm.quantity ELSE -sm.quantity END) <> 0
ORDER BY net_change;
