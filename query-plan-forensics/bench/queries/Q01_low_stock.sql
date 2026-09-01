-- Q01 | v_low_stock_items | the cross-table predicate that CANNOT be partial-indexed.
-- quantity < reorder_point spans stock_levels and products, and a partial index
-- predicate may only reference columns of its own table. Stage 3b is about what
-- it costs to make this indexable at all.
SELECT sku, product_name, warehouse_code, quantity, reorder_point, deficit
FROM   v_low_stock_items
ORDER  BY deficit DESC
LIMIT  100;
