-- Q05 | BRIN's best case, and the non-sargable trap (Stage 3c, Stage 7).
-- Written as a half-open range on purpose. The regression gate's demonstration
-- PR rewrites this as date_trunc('month', created_at) = DATE '2026-06-01' --
-- same rows, reads cleaner, and kills the index, the BRIN and partition pruning
-- in one edit.
SELECT date_trunc('day', created_at) AS day,
       movement_type,
       count(*)      AS n,
       sum(quantity) AS units
FROM   stock_movements
WHERE  created_at >= DATE '2026-06-01'
  AND  created_at <  DATE '2026-07-01'
GROUP  BY 1, 2
ORDER  BY 1, 2;
