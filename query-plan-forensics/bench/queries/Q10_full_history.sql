-- Q10 | the query partitioning makes SLOWER (Stage 5).
-- An unbounded scan across all 24 monthly partitions. Pruning cannot help; the
-- Append node's per-partition overhead is pure cost. Every partitioning writeup
-- should contain one of these, and almost none do.
SELECT movement_type,
       count(*)                     AS n,
       sum(quantity)                AS units,
       min(created_at)              AS first_seen,
       max(created_at)              AS last_seen
FROM   stock_movements
GROUP  BY movement_type
ORDER  BY n DESC;
