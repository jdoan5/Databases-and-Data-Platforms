-- ============================================================================
-- 02_seed_data.sql  -  realistic sample data for practicing queries
-- Insert order respects FK dependencies.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- categories  (a small two-level hierarchy)
-- ----------------------------------------------------------------------------
INSERT INTO categories (name, parent_id) VALUES
    ('Electronics',       NULL),
    ('Furniture',         NULL),
    ('Office Supplies',   NULL),
    ('Tools',             NULL),
    ('Clothing',          NULL);

INSERT INTO categories (name, parent_id) VALUES
    ('Laptops',           (SELECT category_id FROM categories WHERE name='Electronics')),
    ('Audio',             (SELECT category_id FROM categories WHERE name='Electronics')),
    ('Desks',             (SELECT category_id FROM categories WHERE name='Furniture')),
    ('Chairs',            (SELECT category_id FROM categories WHERE name='Furniture'));


-- ----------------------------------------------------------------------------
-- suppliers
-- ----------------------------------------------------------------------------
INSERT INTO suppliers (name, contact_email, phone, address, city, country) VALUES
    ('Acme Electronics Co.',   'sales@acme-elec.com',    '+1-415-555-0101', '100 Market St',  'San Francisco', 'USA'),
    ('Globex Furniture Ltd.',  'orders@globex-fur.com',  '+1-312-555-0142', '22 Lake Shore',  'Chicago',       'USA'),
    ('Initech Office Goods',   'hello@initech.com',      '+1-512-555-0188', '500 Congress',   'Austin',        'USA'),
    ('Hooli Tools Mfg.',       'support@hooli-tools.com','+1-206-555-0177', '7 Tech Way',     'Seattle',       'USA'),
    ('Vandelay Apparel',       'sales@vandelay.com',     '+1-212-555-0199', '88 Broadway',    'New York',      'USA');


-- ----------------------------------------------------------------------------
-- warehouses
-- ----------------------------------------------------------------------------
INSERT INTO warehouses (code, name, address, city, country) VALUES
    ('WH-WEST', 'West Coast DC',  '1 Logistics Blvd',  'Los Angeles', 'USA'),
    ('WH-CENT', 'Central DC',     '500 Hub Road',      'Dallas',      'USA'),
    ('WH-EAST', 'East Coast DC',  '900 Atlantic Ave',  'Newark',      'USA');


-- ----------------------------------------------------------------------------
-- products
-- ----------------------------------------------------------------------------
INSERT INTO products (sku, name, description, category_id, unit_price, unit_cost, reorder_point, reorder_quantity) VALUES
    ('ELEC-LAP-001', 'UltraBook 14"',          '14-inch business laptop',     (SELECT category_id FROM categories WHERE name='Laptops'),         1299.00,  850.00, 15, 40),
    ('ELEC-LAP-002', 'GamerPro 17"',           '17-inch gaming laptop',       (SELECT category_id FROM categories WHERE name='Laptops'),         1899.00, 1300.00,  8, 20),
    ('ELEC-AUD-001', 'NoiseCancel Headphones', 'Over-ear ANC headphones',     (SELECT category_id FROM categories WHERE name='Audio'),            249.00,  140.00, 25, 75),
    ('ELEC-AUD-002', 'Bluetooth Speaker',      'Portable BT speaker',         (SELECT category_id FROM categories WHERE name='Audio'),             79.00,   38.00, 30,100),
    ('FURN-DSK-001', 'Standing Desk 60"',      'Electric height-adjustable',  (SELECT category_id FROM categories WHERE name='Desks'),            599.00,  340.00, 10, 25),
    ('FURN-DSK-002', 'Compact Writing Desk',   'Small home-office desk',      (SELECT category_id FROM categories WHERE name='Desks'),            199.00,  110.00, 12, 30),
    ('FURN-CHR-001', 'Ergonomic Mesh Chair',   'Lumbar-support office chair', (SELECT category_id FROM categories WHERE name='Chairs'),           329.00,  180.00, 15, 40),
    ('FURN-CHR-002', 'Executive Leather Chair','Premium executive chair',     (SELECT category_id FROM categories WHERE name='Chairs'),           549.00,  310.00,  8, 20),
    ('OFFC-PEN-001', 'Gel Pen Pack (12)',      'Black gel pens, dozen',       (SELECT category_id FROM categories WHERE name='Office Supplies'),    9.99,    3.50, 50,200),
    ('OFFC-PAP-001', 'A4 Copy Paper 500ct',    'Multipurpose white paper',    (SELECT category_id FROM categories WHERE name='Office Supplies'),   12.49,    6.00,100,300),
    ('OFFC-STP-001', 'Heavy-Duty Stapler',     '50-sheet capacity',           (SELECT category_id FROM categories WHERE name='Office Supplies'),   24.99,   11.00, 20, 60),
    ('TOOL-DRL-001', 'Cordless Drill 18V',     'Brushless drill + battery',   (SELECT category_id FROM categories WHERE name='Tools'),            159.00,   85.00, 15, 40),
    ('TOOL-SAW-001', 'Circular Saw 7-1/4"',    'Corded circular saw',         (SELECT category_id FROM categories WHERE name='Tools'),            129.00,   70.00, 10, 30),
    ('CLTH-TSH-001', 'Cotton T-Shirt (M)',     'Crew-neck, medium',           (SELECT category_id FROM categories WHERE name='Clothing'),          14.99,    5.50, 40,150),
    ('CLTH-JKT-001', 'Rain Jacket (L)',        'Waterproof shell, large',     (SELECT category_id FROM categories WHERE name='Clothing'),          89.00,   42.00, 20, 60);


-- ----------------------------------------------------------------------------
-- stock_levels  -  starting on-hand quantities
-- Mix of healthy / low / zero stock so reorder queries have something to find.
-- ----------------------------------------------------------------------------
INSERT INTO stock_levels (product_id, warehouse_id, quantity)
SELECT p.product_id, w.warehouse_id,
       CASE
           WHEN p.sku = 'ELEC-LAP-001' AND w.code = 'WH-WEST' THEN  8   -- below reorder
           WHEN p.sku = 'ELEC-LAP-001' AND w.code = 'WH-CENT' THEN 42
           WHEN p.sku = 'ELEC-LAP-001' AND w.code = 'WH-EAST' THEN 27
           WHEN p.sku = 'ELEC-LAP-002' AND w.code = 'WH-WEST' THEN 12
           WHEN p.sku = 'ELEC-LAP-002' AND w.code = 'WH-CENT' THEN  3   -- below reorder
           WHEN p.sku = 'ELEC-LAP-002' AND w.code = 'WH-EAST' THEN 18
           WHEN p.sku = 'ELEC-AUD-001'                        THEN 60
           WHEN p.sku = 'ELEC-AUD-002' AND w.code = 'WH-EAST' THEN 22   -- below reorder
           WHEN p.sku = 'ELEC-AUD-002'                        THEN 85
           WHEN p.sku = 'FURN-DSK-001'                        THEN 18
           WHEN p.sku = 'FURN-DSK-002' AND w.code = 'WH-CENT' THEN  0   -- stock-out
           WHEN p.sku = 'FURN-DSK-002'                        THEN 35
           WHEN p.sku = 'FURN-CHR-001'                        THEN 30
           WHEN p.sku = 'FURN-CHR-002' AND w.code = 'WH-WEST' THEN  5   -- below reorder
           WHEN p.sku = 'FURN-CHR-002'                        THEN 16
           WHEN p.sku = 'OFFC-PEN-001'                        THEN 180
           WHEN p.sku = 'OFFC-PAP-001'                        THEN 260
           WHEN p.sku = 'OFFC-STP-001' AND w.code = 'WH-EAST' THEN 12   -- below reorder
           WHEN p.sku = 'OFFC-STP-001'                        THEN 55
           WHEN p.sku = 'TOOL-DRL-001'                        THEN 28
           WHEN p.sku = 'TOOL-SAW-001'                        THEN 19
           WHEN p.sku = 'CLTH-TSH-001'                        THEN 120
           WHEN p.sku = 'CLTH-JKT-001' AND w.code = 'WH-WEST' THEN 15   -- below reorder
           WHEN p.sku = 'CLTH-JKT-001'                        THEN 45
           ELSE 25
       END AS quantity
FROM products  p
CROSS JOIN warehouses w;


-- ----------------------------------------------------------------------------
-- purchase_orders  +  line items  (one of each status, for query practice)
-- ----------------------------------------------------------------------------
INSERT INTO purchase_orders (po_number, supplier_id, warehouse_id, status, order_date, expected_date, received_date) VALUES
    ('PO-2026-0001', (SELECT supplier_id FROM suppliers WHERE name='Acme Electronics Co.'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-WEST'),
                     'RECEIVED',          '2026-05-01', '2026-05-10', '2026-05-09'),
    ('PO-2026-0002', (SELECT supplier_id FROM suppliers WHERE name='Globex Furniture Ltd.'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-CENT'),
                     'PARTIALLY_RECEIVED','2026-05-15', '2026-05-25', NULL),
    ('PO-2026-0003', (SELECT supplier_id FROM suppliers WHERE name='Initech Office Goods'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-EAST'),
                     'PLACED',            '2026-05-28', '2026-06-08', NULL),
    ('PO-2026-0004', (SELECT supplier_id FROM suppliers WHERE name='Hooli Tools Mfg.'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-CENT'),
                     'DRAFT',             '2026-06-04', NULL,         NULL);

-- line items
INSERT INTO purchase_order_items (po_id, product_id, quantity_ordered, quantity_received, unit_cost) VALUES
    -- PO-0001 (received)
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0001'),
     (SELECT product_id FROM products  WHERE sku='ELEC-LAP-001'), 30, 30,  850.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0001'),
     (SELECT product_id FROM products  WHERE sku='ELEC-AUD-001'), 50, 50,  140.00),
    -- PO-0002 (partial)
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0002'),
     (SELECT product_id FROM products  WHERE sku='FURN-DSK-001'), 20, 12,  340.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0002'),
     (SELECT product_id FROM products  WHERE sku='FURN-CHR-001'), 25,  0,  180.00),
    -- PO-0003 (placed, nothing received)
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0003'),
     (SELECT product_id FROM products  WHERE sku='OFFC-PAP-001'),200,  0,    6.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0003'),
     (SELECT product_id FROM products  WHERE sku='OFFC-STP-001'), 40,  0,   11.00),
    -- PO-0004 (draft)
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0004'),
     (SELECT product_id FROM products  WHERE sku='TOOL-DRL-001'), 30,  0,   85.00);


-- ----------------------------------------------------------------------------
-- stock_movements  -  historical ledger entries (older events)
-- NOTE: these are historical -- stock_levels above already reflects them.
-- After 05_triggers.sql is installed, future inserts here will auto-update
-- stock_levels for you.
-- ----------------------------------------------------------------------------
INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reference_type, reference_id, notes, created_at) VALUES
    ((SELECT product_id FROM products  WHERE sku='ELEC-LAP-001'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-WEST'),
     'IN',  30, 'PURCHASE_ORDER',
     (SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0001'),
     'Receipt from Acme', '2026-05-09 10:15:00'),

    ((SELECT product_id FROM products  WHERE sku='ELEC-AUD-001'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-WEST'),
     'IN',  50, 'PURCHASE_ORDER',
     (SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0001'),
     'Receipt from Acme', '2026-05-09 10:20:00'),

    ((SELECT product_id FROM products  WHERE sku='ELEC-LAP-001'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-WEST'),
     'OUT', 12, 'SALES_ORDER', 9001, 'Bulk customer order',     '2026-05-20 14:00:00'),

    ((SELECT product_id FROM products  WHERE sku='ELEC-AUD-002'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-EAST'),
     'OUT',  8, 'SALES_ORDER', 9002, 'Retail fulfillment',      '2026-05-22 09:30:00'),

    ((SELECT product_id FROM products  WHERE sku='OFFC-PEN-001'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-CENT'),
     'ADJUSTMENT', 5, 'MANUAL', NULL, 'Cycle count correction', '2026-05-25 16:45:00'),

    ((SELECT product_id FROM products  WHERE sku='CLTH-TSH-001'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-WEST'),
     'OUT', 25, 'SALES_ORDER', 9003, 'Wholesale order',         '2026-05-30 11:00:00'),

    ((SELECT product_id FROM products  WHERE sku='ELEC-AUD-001'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-CENT'),
     'TRANSFER_OUT', 10, 'TRANSFER', 7001, 'To East DC',        '2026-06-01 08:00:00'),
    ((SELECT product_id FROM products  WHERE sku='ELEC-AUD-001'),
     (SELECT warehouse_id FROM warehouses WHERE code='WH-EAST'),
     'TRANSFER_IN',  10, 'TRANSFER', 7001, 'From Central DC',   '2026-06-02 14:30:00');
