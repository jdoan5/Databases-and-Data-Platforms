-- Q06 | aggregate over stock_levels x products x categories.
-- 360k stock rows, eight groups. The join-order and hash-vs-sort decision.
SELECT c.name AS category,
       count(DISTINCT sl.product_id) AS skus,
       sum(sl.quantity)              AS units,
       round(sum(sl.quantity * p.unit_cost), 2) AS value_at_cost
FROM   stock_levels sl
JOIN   products   p ON p.product_id  = sl.product_id
JOIN   categories c ON c.category_id = p.category_id
GROUP  BY c.name
ORDER  BY value_at_cost DESC;
