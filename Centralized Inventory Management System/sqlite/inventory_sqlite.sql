-- ============================================================================
-- inventory_sqlite.sql  -  the SAME model ported to SQLite.
-- Run all at once. Compare side-by-side with 01_schema.sql / 02_seed_data.sql
-- to feel how the dialect differs. Key changes are flagged with  -- DIALECT:
-- ============================================================================

PRAGMA foreign_keys = ON;   -- DIALECT: SQLite ignores FKs unless you switch this ON

DROP TABLE IF EXISTS stock_movements;
DROP TABLE IF EXISTS purchase_order_items;
DROP TABLE IF EXISTS purchase_orders;
DROP TABLE IF EXISTS stock_levels;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS suppliers;
DROP TABLE IF EXISTS warehouses;
DROP TABLE IF EXISTS categories;


-- ----------------------------------------------------------------------------
-- Schema
-- DIALECT notes vs PostgreSQL:
--   SERIAL              -> INTEGER PRIMARY KEY AUTOINCREMENT
--   ENUM type           -> TEXT + CHECK (col IN (...))   (no CREATE TYPE)
--   BOOLEAN             -> INTEGER + CHECK (col IN (0,1)) (no true/false literal)
--   TIMESTAMP DEFAULT NOW() -> TEXT DEFAULT CURRENT_TIMESTAMP
--   NUMERIC(12,2)       -> NUMERIC (precision/scale are not enforced)
-- ----------------------------------------------------------------------------
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    parent_id   INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE suppliers (
    supplier_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    contact_email TEXT,
    phone         TEXT,
    address       TEXT,
    city          TEXT,
    country       TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (contact_email IS NULL OR contact_email LIKE '%@%.%')
);

CREATE TABLE warehouses (
    warehouse_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    address      TEXT,
    city         TEXT,
    country      TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
    product_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    sku              TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    description      TEXT,
    category_id      INTEGER REFERENCES categories(category_id) ON DELETE SET NULL,
    unit_price       NUMERIC NOT NULL CHECK (unit_price >= 0),
    unit_cost        NUMERIC NOT NULL CHECK (unit_cost  >= 0),
    reorder_point    INTEGER NOT NULL DEFAULT 10 CHECK (reorder_point    >= 0),
    reorder_quantity INTEGER NOT NULL DEFAULT 50 CHECK (reorder_quantity >  0),
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE stock_levels (
    product_id   INTEGER NOT NULL REFERENCES products(product_id)     ON DELETE CASCADE,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id) ON DELETE CASCADE,
    quantity     INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    last_updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (product_id, warehouse_id)
);

CREATE TABLE purchase_orders (
    po_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number     TEXT NOT NULL UNIQUE,
    supplier_id   INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    warehouse_id  INTEGER NOT NULL REFERENCES warehouses(warehouse_id),
    status        TEXT NOT NULL DEFAULT 'DRAFT'
                  CHECK (status IN ('DRAFT','PLACED','PARTIALLY_RECEIVED','RECEIVED','CANCELLED')),
    order_date    TEXT NOT NULL DEFAULT (date('now')),
    expected_date TEXT,
    received_date TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE purchase_order_items (
    po_item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id             INTEGER NOT NULL REFERENCES purchase_orders(po_id) ON DELETE CASCADE,
    product_id        INTEGER NOT NULL REFERENCES products(product_id),
    quantity_ordered  INTEGER NOT NULL CHECK (quantity_ordered > 0),
    quantity_received INTEGER NOT NULL DEFAULT 0 CHECK (quantity_received >= 0),
    unit_cost         NUMERIC NOT NULL CHECK (unit_cost >= 0),
    CHECK (quantity_received <= quantity_ordered),
    UNIQUE (po_id, product_id)
);

CREATE TABLE stock_movements (
    movement_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL REFERENCES products(product_id),
    warehouse_id   INTEGER NOT NULL REFERENCES warehouses(warehouse_id),
    movement_type  TEXT NOT NULL
                   CHECK (movement_type IN ('IN','OUT','TRANSFER_IN','TRANSFER_OUT','ADJUSTMENT','RETURN')),
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    reference_type TEXT,
    reference_id   INTEGER,
    notes          TEXT,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_products_category    ON products(category_id);
CREATE INDEX idx_stock_levels_wh      ON stock_levels(warehouse_id);
CREATE INDEX idx_movements_product    ON stock_movements(product_id);


-- ----------------------------------------------------------------------------
-- Trigger  -  same behavior as Postgres 05, but SQLite trigger syntax:
-- the body is plain SQL inline (no separate CREATE FUNCTION / PL/pgSQL).
-- DIALECT: max(x,0) replaces Postgres GREATEST(x,0).
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_apply_stock_movement
AFTER INSERT ON stock_movements
BEGIN
    INSERT INTO stock_levels (product_id, warehouse_id, quantity, last_updated)
    VALUES (
        NEW.product_id,
        NEW.warehouse_id,
        max(CASE WHEN NEW.movement_type IN ('OUT','TRANSFER_OUT')
                 THEN -NEW.quantity ELSE NEW.quantity END, 0),
        CURRENT_TIMESTAMP
    )
    ON CONFLICT(product_id, warehouse_id) DO UPDATE SET
        quantity = quantity + CASE WHEN NEW.movement_type IN ('OUT','TRANSFER_OUT')
                                   THEN -NEW.quantity ELSE NEW.quantity END,
        last_updated = CURRENT_TIMESTAMP;
END;


-- ----------------------------------------------------------------------------
-- Seed data  (identical to 02_seed_data.sql - INSERT syntax is portable)
-- ----------------------------------------------------------------------------
INSERT INTO categories (name, parent_id) VALUES
    ('Electronics', NULL), ('Furniture', NULL), ('Office Supplies', NULL),
    ('Tools', NULL), ('Clothing', NULL);
INSERT INTO categories (name, parent_id) VALUES
    ('Laptops', (SELECT category_id FROM categories WHERE name='Electronics')),
    ('Audio',   (SELECT category_id FROM categories WHERE name='Electronics')),
    ('Desks',   (SELECT category_id FROM categories WHERE name='Furniture')),
    ('Chairs',  (SELECT category_id FROM categories WHERE name='Furniture'));

INSERT INTO suppliers (name, contact_email, phone, address, city, country) VALUES
    ('Acme Electronics Co.',  'sales@acme-elec.com',     '+1-415-555-0101', '100 Market St', 'San Francisco', 'USA'),
    ('Globex Furniture Ltd.', 'orders@globex-fur.com',   '+1-312-555-0142', '22 Lake Shore', 'Chicago',       'USA'),
    ('Initech Office Goods',  'hello@initech.com',       '+1-512-555-0188', '500 Congress',  'Austin',        'USA'),
    ('Hooli Tools Mfg.',      'support@hooli-tools.com', '+1-206-555-0177', '7 Tech Way',    'Seattle',       'USA'),
    ('Vandelay Apparel',      'sales@vandelay.com',      '+1-212-555-0199', '88 Broadway',   'New York',      'USA');

INSERT INTO warehouses (code, name, address, city, country) VALUES
    ('WH-WEST', 'West Coast DC', '1 Logistics Blvd', 'Los Angeles', 'USA'),
    ('WH-CENT', 'Central DC',    '500 Hub Road',     'Dallas',      'USA'),
    ('WH-EAST', 'East Coast DC', '900 Atlantic Ave', 'Newark',      'USA');

INSERT INTO products (sku, name, description, category_id, unit_price, unit_cost, reorder_point, reorder_quantity) VALUES
    ('ELEC-LAP-001','UltraBook 14"',           '14-inch business laptop',    (SELECT category_id FROM categories WHERE name='Laptops'),        1299.00,  850.00, 15, 40),
    ('ELEC-LAP-002','GamerPro 17"',            '17-inch gaming laptop',      (SELECT category_id FROM categories WHERE name='Laptops'),        1899.00, 1300.00,  8, 20),
    ('ELEC-AUD-001','NoiseCancel Headphones',  'Over-ear ANC headphones',    (SELECT category_id FROM categories WHERE name='Audio'),           249.00,  140.00, 25, 75),
    ('ELEC-AUD-002','Bluetooth Speaker',       'Portable BT speaker',        (SELECT category_id FROM categories WHERE name='Audio'),            79.00,   38.00, 30,100),
    ('FURN-DSK-001','Standing Desk 60"',       'Electric height-adjustable', (SELECT category_id FROM categories WHERE name='Desks'),           599.00,  340.00, 10, 25),
    ('FURN-DSK-002','Compact Writing Desk',    'Small home-office desk',     (SELECT category_id FROM categories WHERE name='Desks'),           199.00,  110.00, 12, 30),
    ('FURN-CHR-001','Ergonomic Mesh Chair',    'Lumbar-support office chair',(SELECT category_id FROM categories WHERE name='Chairs'),          329.00,  180.00, 15, 40),
    ('FURN-CHR-002','Executive Leather Chair', 'Premium executive chair',    (SELECT category_id FROM categories WHERE name='Chairs'),          549.00,  310.00,  8, 20),
    ('OFFC-PEN-001','Gel Pen Pack (12)',       'Black gel pens, dozen',      (SELECT category_id FROM categories WHERE name='Office Supplies'),   9.99,    3.50, 50,200),
    ('OFFC-PAP-001','A4 Copy Paper 500ct',     'Multipurpose white paper',   (SELECT category_id FROM categories WHERE name='Office Supplies'),  12.49,    6.00,100,300),
    ('OFFC-STP-001','Heavy-Duty Stapler',      '50-sheet capacity',          (SELECT category_id FROM categories WHERE name='Office Supplies'),  24.99,   11.00, 20, 60),
    ('TOOL-DRL-001','Cordless Drill 18V',      'Brushless drill + battery',  (SELECT category_id FROM categories WHERE name='Tools'),           159.00,   85.00, 15, 40),
    ('TOOL-SAW-001','Circular Saw 7-1/4"',     'Corded circular saw',        (SELECT category_id FROM categories WHERE name='Tools'),           129.00,   70.00, 10, 30),
    ('CLTH-TSH-001','Cotton T-Shirt (M)',      'Crew-neck, medium',          (SELECT category_id FROM categories WHERE name='Clothing'),         14.99,    5.50, 40,150),
    ('CLTH-JKT-001','Rain Jacket (L)',         'Waterproof shell, large',    (SELECT category_id FROM categories WHERE name='Clothing'),         89.00,   42.00, 20, 60);

-- starting stock (same CASE matrix as Postgres; CROSS JOIN is portable)
INSERT INTO stock_levels (product_id, warehouse_id, quantity)
SELECT p.product_id, w.warehouse_id,
       CASE
           WHEN p.sku='ELEC-LAP-001' AND w.code='WH-WEST' THEN  8
           WHEN p.sku='ELEC-LAP-001' AND w.code='WH-CENT' THEN 42
           WHEN p.sku='ELEC-LAP-001' AND w.code='WH-EAST' THEN 27
           WHEN p.sku='ELEC-LAP-002' AND w.code='WH-WEST' THEN 12
           WHEN p.sku='ELEC-LAP-002' AND w.code='WH-CENT' THEN  3
           WHEN p.sku='ELEC-LAP-002' AND w.code='WH-EAST' THEN 18
           WHEN p.sku='ELEC-AUD-001'                      THEN 60
           WHEN p.sku='ELEC-AUD-002' AND w.code='WH-EAST' THEN 22
           WHEN p.sku='ELEC-AUD-002'                      THEN 85
           WHEN p.sku='FURN-DSK-001'                      THEN 18
           WHEN p.sku='FURN-DSK-002' AND w.code='WH-CENT' THEN  0
           WHEN p.sku='FURN-DSK-002'                      THEN 35
           WHEN p.sku='FURN-CHR-001'                      THEN 30
           WHEN p.sku='FURN-CHR-002' AND w.code='WH-WEST' THEN  5
           WHEN p.sku='FURN-CHR-002'                      THEN 16
           WHEN p.sku='OFFC-PEN-001'                      THEN 180
           WHEN p.sku='OFFC-PAP-001'                      THEN 260
           WHEN p.sku='OFFC-STP-001' AND w.code='WH-EAST' THEN 12
           WHEN p.sku='OFFC-STP-001'                      THEN 55
           WHEN p.sku='TOOL-DRL-001'                      THEN 28
           WHEN p.sku='TOOL-SAW-001'                      THEN 19
           WHEN p.sku='CLTH-TSH-001'                      THEN 120
           WHEN p.sku='CLTH-JKT-001' AND w.code='WH-WEST' THEN 15
           WHEN p.sku='CLTH-JKT-001'                      THEN 45
           ELSE 25
       END
FROM products p CROSS JOIN warehouses w;

INSERT INTO purchase_orders (po_number, supplier_id, warehouse_id, status, order_date, expected_date, received_date) VALUES
    ('PO-2026-0001', (SELECT supplier_id FROM suppliers WHERE name='Acme Electronics Co.'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-WEST'),'RECEIVED','2026-05-01','2026-05-10','2026-05-09'),
    ('PO-2026-0002', (SELECT supplier_id FROM suppliers WHERE name='Globex Furniture Ltd.'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-CENT'),'PARTIALLY_RECEIVED','2026-05-15','2026-05-25',NULL),
    ('PO-2026-0003', (SELECT supplier_id FROM suppliers WHERE name='Initech Office Goods'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-EAST'),'PLACED','2026-05-28','2026-06-08',NULL),
    ('PO-2026-0004', (SELECT supplier_id FROM suppliers WHERE name='Hooli Tools Mfg.'),
                     (SELECT warehouse_id FROM warehouses WHERE code='WH-CENT'),'DRAFT','2026-06-04',NULL,NULL);

INSERT INTO purchase_order_items (po_id, product_id, quantity_ordered, quantity_received, unit_cost) VALUES
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0001'),(SELECT product_id FROM products WHERE sku='ELEC-LAP-001'),30,30,850.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0001'),(SELECT product_id FROM products WHERE sku='ELEC-AUD-001'),50,50,140.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0002'),(SELECT product_id FROM products WHERE sku='FURN-DSK-001'),20,12,340.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0002'),(SELECT product_id FROM products WHERE sku='FURN-CHR-001'),25, 0,180.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0003'),(SELECT product_id FROM products WHERE sku='OFFC-PAP-001'),200,0,  6.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0003'),(SELECT product_id FROM products WHERE sku='OFFC-STP-001'),40, 0, 11.00),
    ((SELECT po_id FROM purchase_orders WHERE po_number='PO-2026-0004'),(SELECT product_id FROM products WHERE sku='TOOL-DRL-001'),30, 0, 85.00);

-- NOTE: stock_movements left empty here. Because the trigger above is now live,
-- any INSERT you add will auto-update stock_levels - try it:
--   INSERT INTO stock_movements (product_id, warehouse_id, movement_type, quantity, reference_type, notes)
--   SELECT product_id, (SELECT warehouse_id FROM warehouses WHERE code='WH-EAST'),
--          'IN', 20, 'MANUAL', 'test'
--   FROM products WHERE sku='ELEC-AUD-001';
--   SELECT quantity FROM stock_levels
--   WHERE product_id=(SELECT product_id FROM products WHERE sku='ELEC-AUD-001')
--     AND warehouse_id=(SELECT warehouse_id FROM warehouses WHERE code='WH-EAST'); -- 80
