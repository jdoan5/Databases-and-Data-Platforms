-- Q04 | the correlated-column row estimate (Stage 4), and the covering-index
--       subject (Stage 3a).
--
-- product_id 1970 is pop_rank 3 in the skew distribution and warehouse 3 is its
-- home. Both are stable across PROFILE=small and PROFILE=full: popularity rank
-- is assigned by md5(product_id) over the products table, which the movement
-- count does not touch. Do not swap in a random SKU -- most of the catalog has
-- single-digit movements and the query returns nothing.
--
-- The point: 85% of this product's movements are in warehouse 3, by
-- construction. The planner assumes product_id and warehouse_id are independent
-- and so estimates roughly one sixth of the true count. That gap is what
-- CREATE STATISTICS (product_id, warehouse_id) closes, and the before/after row
-- estimate is the screenshot.
SELECT movement_type, count(*) AS n, sum(quantity) AS units
FROM   stock_movements
WHERE  product_id   = 1970
  AND  warehouse_id = 3
  AND  created_at  >= TIMESTAMP '2026-01-01'
GROUP  BY movement_type
ORDER  BY n DESC;
