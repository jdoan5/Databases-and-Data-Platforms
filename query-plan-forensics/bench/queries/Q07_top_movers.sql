-- Q07 | RANK() OVER (PARTITION BY ...) | sort behaviour and work_mem.
-- The window function every SQL screen asks for, at a size where the sort
-- actually spills. Watch for "Sort Method: external merge  Disk: NkB".
WITH movement_totals AS (
    SELECT sm.warehouse_id, sm.product_id, sum(sm.quantity) AS units
    FROM   stock_movements sm
    WHERE  sm.movement_type = 'OUT'
      AND  sm.created_at >= DATE '2026-01-01'
    GROUP  BY 1, 2
)
SELECT warehouse_id, product_id, units, rnk
FROM (
    SELECT warehouse_id, product_id, units,
           rank() OVER (PARTITION BY warehouse_id ORDER BY units DESC) AS rnk
    FROM   movement_totals
) t
WHERE rnk <= 10
ORDER BY warehouse_id, rnk;
