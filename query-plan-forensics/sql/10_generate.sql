-- ============================================================================
-- 10_generate.sql — the dataset the whole project rests on.
--
-- Replaces 07_bulk_data.sql. Three things the original lacks, each of which
-- decides which optimizations are even available later:
--
--   1. A FIXED SEED. Re-running produces byte-identical data, so a plan from
--      six weeks ago still reproduces and the CI gate has something stable to
--      compare against.
--   2. DELIBERATE SKEW. Uniform-random data gives partial indexes and extended
--      statistics nothing to bite on. Two skews: a power-law over SKUs, and a
--      product->warehouse correlation that CREATE STATISTICS will later fix.
--   3. PHYSICAL ORDERING by created_at. This looks like a flourish until
--      Stage 3: BRIN over a randomly-ordered column is a 40KB index the
--      planner correctly refuses to use.
--
-- Invoke through the Makefile, which supplies :rows —
--   make load PROFILE=full    ->  5,000,000 movements
--   make load PROFILE=small   ->    500,000 movements  (the CI profile)
-- ============================================================================

\set ON_ERROR_STOP on
\timing on

SELECT setseed(0.42);

-- Parallel workers produce nondeterministic tuple order, which defeats the
-- fixed seed. Off for the whole generation.
SET max_parallel_workers_per_gather = 0;
SET synchronous_commit = off;
SET maintenance_work_mem = '512MB';

-- ---------------------------------------------------------------------------
-- 1. Dimensions
-- ---------------------------------------------------------------------------
TRUNCATE stock_movements, purchase_order_items, purchase_orders,
         stock_levels, products, suppliers, warehouses, categories
         RESTART IDENTITY CASCADE;

INSERT INTO categories (name, parent_id)
SELECT 'Category ' || g, NULL FROM generate_series(1, 8) g;
UPDATE categories SET parent_id = 1 WHERE category_id BETWEEN 5 AND 8;

INSERT INTO suppliers (name, contact_email, country)
SELECT 'Vendor ' || g, 'sales' || g || '@vendor' || g || '.com', 'USA'
FROM   generate_series(1, 25) g;

INSERT INTO warehouses (code, name, city, country)
SELECT 'WH' || lpad(g::text, 2, '0'), 'Warehouse ' || g, 'City ' || g, 'USA'
FROM   generate_series(1, 6) g;

-- 60,000 products, so stock_levels lands at 360,000 rows rather than the 3,000
-- the original script produced. A partial index on a table that fits in fifteen
-- pages is a table the planner will always seq-scan instead, which makes the
-- Stage 3 denormalization experiment unmeasurable.
INSERT INTO products (sku, name, category_id, unit_price, unit_cost,
                      reorder_point, reorder_quantity)
SELECT 'SKU-' || lpad(g::text, 6, '0'),
       'Product ' || g,
       1 + (g % 8),
       round((10 + random() * 1990)::numeric, 2),
       round((5  + random() *  900)::numeric, 2),
       10 + floor(random() * 40)::int,
       25 + floor(random() * 75)::int
FROM   generate_series(1, 60000) g;

-- ---------------------------------------------------------------------------
-- 2. Current balances
--
-- NOTE, and this matters later: these balances are seeded independently of the
-- ledger, exactly as 07_bulk_data.sql does it. They were never derived from
-- stock_movements, so SUM(signed movements) will not equal stock_levels.quantity.
-- That divergence is real and it is the subject of sql/91_reconcile.sql. It is
-- NOT caused by the trigger being disabled below — the balances predate the
-- movements entirely. Get that distinction right in the writeup; it is the
-- interesting half of the finding.
-- ---------------------------------------------------------------------------
INSERT INTO stock_levels (product_id, warehouse_id, quantity)
SELECT p.product_id, w.warehouse_id, floor(random() * 200)::int
FROM   products p CROSS JOIN warehouses w;

-- ---------------------------------------------------------------------------
-- 3. Skew tables
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS bench_gen.pick;

CREATE TABLE bench_gen.pick AS
SELECT row_number() OVER (ORDER BY md5(p.product_id::text))::int AS pop_rank,
       p.product_id,
       -- Each product has a home warehouse it ships from 85% of the time.
       -- product_id -> warehouse is therefore a correlated pair, which is what
       -- makes the Stage 4 CREATE STATISTICS fix worth measuring.
       (SELECT min(warehouse_id) FROM warehouses)
         + (p.product_id % (SELECT count(*) FROM warehouses))::int AS home_wh
FROM   products p;

CREATE UNIQUE INDEX ON bench_gen.pick (pop_rank);
ANALYZE bench_gen.pick;

-- ---------------------------------------------------------------------------
-- 4. Purchase orders and line items
-- ---------------------------------------------------------------------------
INSERT INTO purchase_orders (po_number, supplier_id, warehouse_id, status,
                             order_date, expected_date)
SELECT 'PO-' || lpad(g::text, 6, '0'),
       1 + (g % 25),
       (SELECT min(warehouse_id) FROM warehouses) + (g % 6),
       (ARRAY['DRAFT','PLACED','PLACED','PARTIALLY_RECEIVED',
              'RECEIVED','RECEIVED','CANCELLED'])[1 + (g % 7)]::po_status_enum,
       DATE '2024-09-01' + (g % 730),
       DATE '2024-09-01' + (g % 730) + 14
FROM   generate_series(1, 20000) g;

-- 25 lines per PO, 500,000 line items.
--
-- The product for each line is picked by an affine hash of (po_id, line_no)
-- rather than by random(). Two reasons, and the first one cost an hour:
--
--   A scalar subquery containing random() inside a LATERAL gets evaluated once
--   per LATERAL invocation, not once per generate_series row. All 25 lines drew
--   the same product, UNIQUE (po_id, product_id) collapsed them, and the table
--   came out at 20,000 rows instead of 500,000.
--
--   104729 is prime and coprime with 60000, so line_no -> rank is injective for
--   line_no in 1..25. Twenty-five distinct products per PO, guaranteed by
--   arithmetic rather than by a retry loop -- and deterministic, which random()
--   would not have been even when it worked.
INSERT INTO purchase_order_items (po_id, product_id, quantity_ordered,
                                  quantity_received, unit_cost)
SELECT po.po_id,
       pk.product_id,
       q.qty,
       CASE WHEN po.status = 'RECEIVED' THEN q.qty ELSE 0 END,
       round((5 + random() * 900)::numeric, 2)
FROM   purchase_orders po
CROSS  JOIN generate_series(1, 25) AS line_no
JOIN   bench_gen.pick pk
       ON pk.pop_rank = 1 + ((po.po_id * 7919 + line_no * 104729) % 60000)
CROSS  JOIN LATERAL (SELECT 1 + floor(random() * 200)::int AS qty) q;

-- ---------------------------------------------------------------------------
-- 5. The ledger
--
-- Indexes dropped and the trigger disabled for the load. This is the standard
-- ETL shape and it is also what Stage 1 measures against the two slower
-- alternatives. The trigger MUST be off: it upserts stock_levels per row, and a
-- roughly balanced random walk breaches CHECK (quantity >= 0) within a few
-- thousand rows.
-- ---------------------------------------------------------------------------
ALTER TABLE stock_movements DISABLE TRIGGER trg_stock_movement_apply;

DROP INDEX IF EXISTS idx_movements_product;
DROP INDEX IF EXISTS idx_movements_warehouse;
DROP INDEX IF EXISTS idx_movements_created;

INSERT INTO stock_movements (product_id, warehouse_id, movement_type,
                             quantity, reference_type, created_at)
SELECT
    k.pid[s.r],
    CASE WHEN s.wh_roll < 0.85
         THEN k.hwh[s.r]
         ELSE k.wid[1 + floor(s.wh_pick * k.nw)::int]
    END,
    -- Eight slots, six distinct enum values. OUT is weighted 3x so the ledger
    -- looks like a business rather than a uniform draw.
    (ARRAY['IN','OUT','OUT','OUT','ADJUSTMENT',
           'RETURN','TRANSFER_IN','TRANSFER_OUT'])[s.type_pick]::movement_type_enum,
    s.qty,
    'SYNTHETIC',
    s.ts
FROM (
    SELECT (SELECT array_agg(product_id ORDER BY pop_rank) FROM bench_gen.pick) AS pid,
           (SELECT array_agg(home_wh    ORDER BY pop_rank) FROM bench_gen.pick) AS hwh,
           (SELECT array_agg(warehouse_id ORDER BY warehouse_id) FROM warehouses) AS wid,
           (SELECT count(*)::int FROM bench_gen.pick)  AS np,
           (SELECT count(*)::int FROM warehouses)      AS nw
) k
CROSS JOIN LATERAL (
    SELECT
        -- power(random(), 5): the top 5% of ranks take ~55% of all movements.
        -- (0.05 ^ (1/5) = 0.549.) Exponent 3 gives only 36.8% and fails the
        -- >50% acceptance gate below — check the arithmetic before changing it.
        1 + floor(power(random(), 5) * k.np)::int          AS r,
        random()                                           AS wh_roll,
        random()                                           AS wh_pick,
        1 + floor(random() * 8)::int                       AS type_pick,
        1 + floor(random() * 50)::int                      AS qty,
        TIMESTAMP '2024-09-01'
          + (g::numeric / :rows) * INTERVAL '730 days'
          + (random() * INTERVAL '3 hours')                AS ts
    FROM generate_series(1, :rows) g
) s
ORDER BY s.ts;      -- the precondition for BRIN in Stage 3

CREATE INDEX idx_movements_product   ON stock_movements (product_id);
CREATE INDEX idx_movements_warehouse ON stock_movements (warehouse_id);
CREATE INDEX idx_movements_created   ON stock_movements (created_at DESC);

ALTER TABLE stock_movements ENABLE TRIGGER trg_stock_movement_apply;

VACUUM ANALYZE;
