\set ON_ERROR_STOP on
\timing on

\echo '--- part 2: covering, deliberately WITHOUT a vacuum ---'
DROP INDEX IF EXISTS idx_sm_prod_created;
CREATE INDEX idx_sm_prod_created_incl
    ON stock_movements (product_id, created_at DESC)
    INCLUDE (warehouse_id, quantity, movement_type);
ANALYZE stock_movements;

-- Force the visibility map stale so the Heap Fetches problem reproduces even on
-- a table that was vacuumed by the loader.
UPDATE stock_movements
SET    notes = notes
WHERE  product_id = 1970;

\echo 'visibility: fraction of pages marked all-visible'
SELECT relname,
       relpages,
       relallvisible,
       round(relallvisible::numeric / NULLIF(relpages, 0), 4) AS all_visible_frac
FROM   pg_class WHERE relname = 'stock_movements';
