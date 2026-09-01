-- 90_acceptance.sql — Stage 1 gate. All four checks must pass before any
-- tuning starts, because every later number is only as good as this dataset.

\echo '== row counts =='
SELECT relname, n_live_tup
FROM   pg_stat_user_tables
WHERE  schemaname = 'public'
ORDER  BY n_live_tup DESC;

\echo ''
\echo '== GATE 1: skew is real (top 5% of SKUs must hold > 50% of movements) =='
WITH per_product AS (
    SELECT product_id, count(*) AS c FROM stock_movements GROUP BY 1
), ranked AS (
    SELECT c, row_number() OVER (ORDER BY c DESC) AS rnk FROM per_product
), cutoff AS (
    SELECT ceil(count(*) * 0.05) AS top5 FROM per_product
)
SELECT round(sum(r.c) FILTER (WHERE r.rnk <= c.top5)::numeric / sum(r.c), 4)
         AS top5pct_share,
       '> 0.50' AS gate
FROM   ranked r CROSS JOIN cutoff c;

\echo ''
\echo '== GATE 2: physical correlation (BRIN viability) =='
-- created_at must be near 1.0: the generator emits ORDER BY created_at, and a
-- BRIN index over a poorly-correlated column is a tiny index the planner
-- correctly refuses to use.
-- product_id must stay near 0.0, and that is not a failure -- popularity rank is
-- decoupled from product_id by md5 ordering precisely so the primary key index
-- cannot accidentally look brilliant.
SELECT attname,
       round(correlation::numeric, 4) AS correlation,
       CASE attname
         WHEN 'created_at' THEN '> 0.99  (BRIN viable)'
         WHEN 'product_id' THEN '< 0.10  (decoupled on purpose)'
       END AS gate
FROM   pg_stats
WHERE  schemaname = 'public'
  AND  tablename = 'stock_movements'
  AND  attname IN ('created_at', 'product_id')
ORDER  BY attname;

\echo ''
\echo '== GATE 3: the product/warehouse correlation exists (Stage 4 needs it) =='
SELECT round(
         count(*) FILTER (
           WHERE sm.warehouse_id = (SELECT min(warehouse_id) FROM warehouses)
                                   + (sm.product_id % (SELECT count(*) FROM warehouses))::int
         )::numeric / count(*), 4) AS home_warehouse_share,
       '~ 0.85' AS gate
FROM (SELECT product_id, warehouse_id FROM stock_movements LIMIT 200000) sm;

\echo ''
\echo '== GATE 4: determinism — this hash must be identical across two full loads =='
SELECT md5(string_agg(product_id || ':' || warehouse_id || ':' || movement_type
                      || ':' || quantity, ',' ORDER BY movement_id)) AS sample_hash
FROM  (SELECT * FROM stock_movements ORDER BY movement_id LIMIT 10000) t;
