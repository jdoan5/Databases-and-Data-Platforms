-- ============================================================================
-- 07_bulk_data.sql  -  scale the dataset up to realistic volume.
-- Run ONCE, after 01-06, against inventory_mgmt.public.
-- Adds ~485 products, 3 warehouses, 20 suppliers, ~3000 stock rows,
-- 20,000 stock movements over 90 days, and ~40 purchase orders.
--
-- To reset to the small set: re-run 01_schema -> 02_seed_data (-> 04, 05, 06).
--
-- Teaches: generate_series, random(), array indexing, bulk-load with the
-- trigger temporarily DISABLED (a standard ETL technique).
-- ============================================================================


-- 1. More suppliers (Vendor 6..25). Guarded so a re-run won't duplicate. ------
INSERT INTO suppliers (name, contact_email, country)
SELECT 'Vendor ' || g, 'sales' || g || '@vendor' || g || '.com', 'USA'
FROM   generate_series(6, 25) AS g
WHERE  NOT EXISTS (SELECT 1 FROM suppliers s WHERE s.name = 'Vendor ' || g);


-- 2. Three more warehouses (now 6 total). -------------------------------------
INSERT INTO warehouses (code, name, city, country) VALUES
    ('WH-NW', 'Northwest DC', 'Portland', 'USA'),
    ('WH-SE', 'Southeast DC', 'Atlanta',  'USA'),
    ('WH-SW', 'Southwest DC', 'Phoenix',  'USA')
ON CONFLICT (code) DO NOTHING;


-- 3. 485 synthetic products -> ~500 total. -----------------------------------
--    category picked at random from the array; cost = 40-70% of price.
INSERT INTO products (sku, name, category_id, unit_price, unit_cost, reorder_point, reorder_quantity)
SELECT
    'GEN-' || lpad(g::text, 5, '0'),
    'Synthetic Product ' || g,
    c.a[1 + floor(random() * array_length(c.a, 1))::int],
    pr.price,
    round(pr.price * (0.40 + random() * 0.30), 2),
    10 + floor(random() * 40)::int,
    20 + floor(random() * 80)::int
FROM generate_series(1, 485) AS g
CROSS JOIN (SELECT array_agg(category_id) AS a FROM categories) AS c
CROSS JOIN LATERAL (SELECT round((10 + random() * 1990)::numeric, 2) AS price) AS pr
ON CONFLICT (sku) DO NOTHING;


-- 4. Stock rows for every product x warehouse that doesn't have one yet. ------
INSERT INTO stock_levels (product_id, warehouse_id, quantity)
SELECT p.product_id, w.warehouse_id, floor(random() * 200)::int
FROM   products p
CROSS JOIN warehouses w
ON CONFLICT (product_id, warehouse_id) DO NOTHING;


-- 5. 20,000 historical stock movements over the last 90 days. -----------------
--    Trigger is DISABLED for the load: these are HISTORY, and stock_levels
--    already holds current balances. Re-enabled immediately after.
--    (This is how real bulk loads avoid firing per-row triggers 20k times.)
ALTER TABLE stock_movements DISABLE TRIGGER trg_stock_movement_apply;

INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reference_type, created_at)
SELECT
    p.a[1 + floor(random() * array_length(p.a, 1))::int],
    w.a[1 + floor(random() * array_length(w.a, 1))::int],
    t.a[1 + floor(random() * array_length(t.a, 1))::int],
    1 + floor(random() * 50)::int,
    'SYNTHETIC',
    NOW() - (random() * INTERVAL '90 days')
FROM generate_series(1, 20000) AS g
CROSS JOIN (SELECT array_agg(product_id)   AS a FROM products)   AS p
CROSS JOIN (SELECT array_agg(warehouse_id) AS a FROM warehouses) AS w
CROSS JOIN (SELECT ARRAY['IN','OUT','OUT','IN','ADJUSTMENT','RETURN','TRANSFER_IN','TRANSFER_OUT']::movement_type_enum[] AS a) AS t;

ALTER TABLE stock_movements ENABLE TRIGGER trg_stock_movement_apply;


-- 6. 40 more purchase orders (PO-2026-0101 .. 0140) + up to 3 lines each. -----
INSERT INTO purchase_orders (po_number, supplier_id, warehouse_id, status, order_date, expected_date)
SELECT
    'PO-2026-' || lpad((100 + g)::text, 4, '0'),
    s.a[1 + floor(random() * array_length(s.a, 1))::int],
    w.a[1 + floor(random() * array_length(w.a, 1))::int],
    st.a[1 + floor(random() * array_length(st.a, 1))::int],
    CURRENT_DATE - floor(random() * 90)::int,
    CURRENT_DATE + floor(random() * 14)::int
FROM generate_series(1, 40) AS g
CROSS JOIN (SELECT array_agg(supplier_id)  AS a FROM suppliers)  AS s
CROSS JOIN (SELECT array_agg(warehouse_id) AS a FROM warehouses) AS w
CROSS JOIN (SELECT ARRAY['DRAFT','PLACED','RECEIVED','PARTIALLY_RECEIVED']::po_status_enum[] AS a) AS st
ON CONFLICT (po_number) DO NOTHING;

INSERT INTO purchase_order_items (po_id, product_id, quantity_ordered, quantity_received, unit_cost)
SELECT
    po.po_id,
    pr.a[1 + floor(random() * array_length(pr.a, 1))::int],
    x.qo,
    floor(random() * (x.qo + 1))::int,
    round((5 + random() * 500)::numeric, 2)
FROM (SELECT po_id FROM purchase_orders WHERE po_number LIKE 'PO-2026-01%') AS po
CROSS JOIN generate_series(1, 3) AS item
CROSS JOIN (SELECT array_agg(product_id) AS a FROM products) AS pr
CROSS JOIN LATERAL (SELECT 10 + floor(random() * 90)::int AS qo) AS x
ON CONFLICT (po_id, product_id) DO NOTHING;


-- 7. Verify the new volume. ---------------------------------------------------
SELECT 'products'             AS table_name, COUNT(*) AS rows FROM products
UNION ALL SELECT 'warehouses',           COUNT(*) FROM warehouses
UNION ALL SELECT 'suppliers',            COUNT(*) FROM suppliers
UNION ALL SELECT 'stock_levels',         COUNT(*) FROM stock_levels
UNION ALL SELECT 'stock_movements',      COUNT(*) FROM stock_movements
UNION ALL SELECT 'purchase_orders',      COUNT(*) FROM purchase_orders
UNION ALL SELECT 'purchase_order_items', COUNT(*) FROM purchase_order_items
ORDER BY table_name;

-- Now your analytics have teeth. Try these against the bigger data:
--   SELECT * FROM v_recent_movements;                 -- thousands of rows now
--   SELECT * FROM v_stock_valuation;                  -- 6 warehouses
--   SELECT movement_type, COUNT(*), SUM(quantity)
--   FROM stock_movements GROUP BY movement_type ORDER BY 2 DESC;
