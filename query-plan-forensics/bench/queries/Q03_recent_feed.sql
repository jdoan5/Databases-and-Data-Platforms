-- Q03 | v_recent_movements | time-range + two joins. The partitioning win (Stage 5).
-- A 30-day window over 24 months of history: 1/24th of the table, which is
-- exactly the shape partition pruning is for.
SELECT movement_id, created_at, sku, warehouse_code, movement_type, quantity
FROM   v_recent_movements
ORDER  BY created_at DESC
LIMIT  200;
