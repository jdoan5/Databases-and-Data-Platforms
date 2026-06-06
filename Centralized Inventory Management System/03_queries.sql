-- ============================================================================
-- 03_queries.sql  -  guided SELECT practice, beginner -> intermediate.
-- Run each block on its own (Ctrl+Enter in DataGrip) and inspect the output.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 1. Basic SELECT  -  every column, every row.
-- ----------------------------------------------------------------------------
SELECT *
FROM products;


-- ----------------------------------------------------------------------------
-- 2. Projection + filter  -  pick columns, filter rows with WHERE.
-- ----------------------------------------------------------------------------
SELECT sku, name, unit_price
FROM products
WHERE unit_price > 200;


-- ----------------------------------------------------------------------------
-- 3. Sorting + paging  -  ORDER BY, LIMIT, OFFSET.
-- ----------------------------------------------------------------------------
SELECT sku, name, unit_price
FROM products
ORDER BY unit_price DESC
LIMIT 5;


-- ----------------------------------------------------------------------------
-- 4. INNER JOIN  -  every product with its category name.
-- Rows without a category are dropped (that's what INNER means).
-- ----------------------------------------------------------------------------
SELECT p.sku, p.name AS product, c.name AS category, p.unit_price
FROM products   p
JOIN categories c ON c.category_id = p.category_id
ORDER BY c.name, p.name;


-- ----------------------------------------------------------------------------
-- 5. LEFT JOIN  -  every warehouse, even ones with no stock rows.
-- The right side is NULL when no match exists.
-- ----------------------------------------------------------------------------
SELECT w.code, w.name AS warehouse, COUNT(sl.product_id) AS distinct_skus
FROM warehouses   w
LEFT JOIN stock_levels sl ON sl.warehouse_id = w.warehouse_id
GROUP BY w.code, w.name
ORDER BY w.code;


-- ----------------------------------------------------------------------------
-- 6. Aggregates  -  SUM / AVG / MIN / MAX / COUNT.
-- ----------------------------------------------------------------------------
SELECT
    COUNT(*)         AS product_count,
    AVG(unit_price)  AS avg_price,
    MIN(unit_price)  AS cheapest,
    MAX(unit_price)  AS most_expensive
FROM products;


-- ----------------------------------------------------------------------------
-- 7. GROUP BY + HAVING  -  totals per group, filter the groups.
-- "Categories whose average price is above $100."
-- ----------------------------------------------------------------------------
SELECT c.name AS category,
       COUNT(*)        AS sku_count,
       AVG(p.unit_price) AS avg_price
FROM products   p
JOIN categories c ON c.category_id = p.category_id
GROUP BY c.name
HAVING AVG(p.unit_price) > 100
ORDER BY avg_price DESC;


-- ----------------------------------------------------------------------------
-- 8. Multi-table JOIN  -  total units on-hand per product across warehouses.
-- ----------------------------------------------------------------------------
SELECT p.sku, p.name,
       SUM(sl.quantity)          AS total_on_hand,
       SUM(sl.quantity * p.unit_cost) AS inventory_value
FROM products      p
JOIN stock_levels  sl ON sl.product_id = p.product_id
GROUP BY p.sku, p.name
ORDER BY inventory_value DESC;


-- ----------------------------------------------------------------------------
-- 9. Sub-query in WHERE  -  products that are below their reorder point
-- in AT LEAST ONE warehouse.
-- ----------------------------------------------------------------------------
SELECT sku, name, reorder_point
FROM products
WHERE product_id IN (
    SELECT sl.product_id
    FROM   stock_levels sl
    JOIN   products     p2 ON p2.product_id = sl.product_id
    WHERE  sl.quantity < p2.reorder_point
);


-- ----------------------------------------------------------------------------
-- 10. CTE (Common Table Expression)  -  same idea, more readable.
-- A CTE is a named, temporary result set you can reference like a table.
-- ----------------------------------------------------------------------------
WITH low_stock AS (
    SELECT sl.product_id, sl.warehouse_id, sl.quantity, p.reorder_point
    FROM   stock_levels sl
    JOIN   products     p ON p.product_id = sl.product_id
    WHERE  sl.quantity < p.reorder_point
)
SELECT p.sku, p.name, w.code AS warehouse, ls.quantity, ls.reorder_point
FROM   low_stock   ls
JOIN   products    p ON p.product_id    = ls.product_id
JOIN   warehouses  w ON w.warehouse_id  = ls.warehouse_id
ORDER BY p.sku, w.code;


-- ----------------------------------------------------------------------------
-- 11. Window function  -  rank products by total on-hand within each category,
-- without collapsing rows (unlike GROUP BY).
-- ----------------------------------------------------------------------------
SELECT
    c.name                                                       AS category,
    p.sku,
    p.name,
    SUM(sl.quantity)                                             AS on_hand,
    RANK() OVER (PARTITION BY c.category_id
                 ORDER BY SUM(sl.quantity) DESC)                 AS rank_in_category
FROM products      p
JOIN categories    c  ON c.category_id = p.category_id
LEFT JOIN stock_levels sl ON sl.product_id = p.product_id
GROUP BY c.category_id, c.name, p.sku, p.name
ORDER BY c.name, rank_in_category;


-- ----------------------------------------------------------------------------
-- 12. Date filter + JOIN chain  -  recent activity per supplier.
-- "Total units received from each supplier in the last 60 days."
-- ----------------------------------------------------------------------------
SELECT s.name                       AS supplier,
       COUNT(DISTINCT po.po_id)     AS po_count,
       SUM(poi.quantity_received)   AS units_received
FROM suppliers              s
JOIN purchase_orders        po  ON po.supplier_id = s.supplier_id
JOIN purchase_order_items   poi ON poi.po_id      = po.po_id
WHERE po.order_date >= CURRENT_DATE - INTERVAL '60 days'
GROUP BY s.name
ORDER BY units_received DESC NULLS LAST;


-- ----------------------------------------------------------------------------
-- 13. UNION ALL  -  combine receipts and shipments into one feed.
-- UNION removes duplicates; UNION ALL keeps them (and is faster).
-- ----------------------------------------------------------------------------
SELECT created_at, 'RECEIPT'  AS event, product_id, quantity
FROM   stock_movements
WHERE  movement_type IN ('IN', 'TRANSFER_IN', 'RETURN')

UNION ALL

SELECT created_at, 'SHIPMENT' AS event, product_id, -quantity
FROM   stock_movements
WHERE  movement_type IN ('OUT', 'TRANSFER_OUT')

ORDER BY created_at DESC;


-- ----------------------------------------------------------------------------
-- 14. CASE expression  -  bucket products by stock health.
-- ----------------------------------------------------------------------------
SELECT
    p.sku,
    p.name,
    SUM(sl.quantity) AS on_hand,
    p.reorder_point,
    CASE
        WHEN SUM(sl.quantity) = 0                          THEN 'OUT_OF_STOCK'
        WHEN SUM(sl.quantity) < p.reorder_point            THEN 'LOW'
        WHEN SUM(sl.quantity) < p.reorder_point * 2        THEN 'OK'
        ELSE                                                    'HEALTHY'
    END AS stock_status
FROM products      p
LEFT JOIN stock_levels sl ON sl.product_id = p.product_id
GROUP BY p.sku, p.name, p.reorder_point
ORDER BY stock_status, p.sku;


-- ----------------------------------------------------------------------------
-- 15. Self-join on categories  -  show each category with its parent name.
-- ----------------------------------------------------------------------------
SELECT child.name AS category, parent.name AS parent_category
FROM   categories child
LEFT JOIN categories parent ON parent.category_id = child.parent_id
ORDER BY parent.name NULLS FIRST, child.name;
