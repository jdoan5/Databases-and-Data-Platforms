-- ============================================================================
-- 01_schema.sql  -  Centralized Inventory Management System
-- Target: PostgreSQL 14+
-- Run order: 01_schema -> 02_seed_data -> 03_queries -> 04_views -> 05_triggers
-- ============================================================================

-- Drop in reverse-dependency order so the script is re-runnable.
DROP TABLE IF EXISTS stock_movements      CASCADE;
DROP TABLE IF EXISTS purchase_order_items CASCADE;
DROP TABLE IF EXISTS purchase_orders      CASCADE;
DROP TABLE IF EXISTS stock_levels         CASCADE;
DROP TABLE IF EXISTS products             CASCADE;
DROP TABLE IF EXISTS suppliers            CASCADE;
DROP TABLE IF EXISTS warehouses           CASCADE;
DROP TABLE IF EXISTS categories           CASCADE;

DROP TYPE IF EXISTS po_status_enum;
DROP TYPE IF EXISTS movement_type_enum;


-- ----------------------------------------------------------------------------
-- ENUM types  (Postgres custom types -- restrict a column to known values)
-- ----------------------------------------------------------------------------
CREATE TYPE po_status_enum AS ENUM (
    'DRAFT', 'PLACED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'CANCELLED'
);

CREATE TYPE movement_type_enum AS ENUM (
    'IN',          -- receive from supplier
    'OUT',         -- ship to customer
    'TRANSFER_IN', -- arrived at destination warehouse
    'TRANSFER_OUT',-- left source warehouse
    'ADJUSTMENT',  -- manual correction (cycle count)
    'RETURN'       -- customer return
);


-- ----------------------------------------------------------------------------
-- categories  -  self-referencing tree (a category can have a parent)
-- ----------------------------------------------------------------------------
CREATE TABLE categories (
    category_id   SERIAL       PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    parent_id     INT          REFERENCES categories(category_id) ON DELETE SET NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW()
);


-- ----------------------------------------------------------------------------
-- suppliers
-- ----------------------------------------------------------------------------
CREATE TABLE suppliers (
    supplier_id   SERIAL       PRIMARY KEY,
    name          VARCHAR(150) NOT NULL,
    contact_email VARCHAR(150),
    phone         VARCHAR(30),
    address       VARCHAR(255),
    city          VARCHAR(80),
    country       VARCHAR(80),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    CONSTRAINT suppliers_email_format CHECK (contact_email LIKE '%@%.%')
);


-- ----------------------------------------------------------------------------
-- warehouses
-- ----------------------------------------------------------------------------
CREATE TABLE warehouses (
    warehouse_id  SERIAL       PRIMARY KEY,
    code          VARCHAR(10)  NOT NULL UNIQUE,
    name          VARCHAR(150) NOT NULL,
    address       VARCHAR(255),
    city          VARCHAR(80),
    country       VARCHAR(80),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW()
);


-- ----------------------------------------------------------------------------
-- products
-- ----------------------------------------------------------------------------
CREATE TABLE products (
    product_id       SERIAL        PRIMARY KEY,
    sku              VARCHAR(40)   NOT NULL UNIQUE,
    name             VARCHAR(200)  NOT NULL,
    description      TEXT,
    category_id      INT           REFERENCES categories(category_id) ON DELETE SET NULL,
    unit_price       NUMERIC(12,2) NOT NULL CHECK (unit_price >= 0),
    unit_cost        NUMERIC(12,2) NOT NULL CHECK (unit_cost  >= 0),
    reorder_point    INT           NOT NULL DEFAULT 10 CHECK (reorder_point    >= 0),
    reorder_quantity INT           NOT NULL DEFAULT 50 CHECK (reorder_quantity >  0),
    is_active        BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP     NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_products_category ON products(category_id);


-- ----------------------------------------------------------------------------
-- stock_levels  -  current on-hand quantity per (product, warehouse)
-- Composite PK enforces one row per product per warehouse.
-- ----------------------------------------------------------------------------
CREATE TABLE stock_levels (
    product_id    INT       NOT NULL REFERENCES products(product_id)     ON DELETE CASCADE,
    warehouse_id  INT       NOT NULL REFERENCES warehouses(warehouse_id) ON DELETE CASCADE,
    quantity      INT       NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    last_updated  TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (product_id, warehouse_id)
);

CREATE INDEX idx_stock_levels_warehouse ON stock_levels(warehouse_id);


-- ----------------------------------------------------------------------------
-- purchase_orders  +  purchase_order_items  (header / line-item pattern)
-- ----------------------------------------------------------------------------
CREATE TABLE purchase_orders (
    po_id          SERIAL          PRIMARY KEY,
    po_number      VARCHAR(20)     NOT NULL UNIQUE,
    supplier_id    INT             NOT NULL REFERENCES suppliers(supplier_id),
    warehouse_id   INT             NOT NULL REFERENCES warehouses(warehouse_id),
    status         po_status_enum  NOT NULL DEFAULT 'DRAFT',
    order_date     DATE            NOT NULL DEFAULT CURRENT_DATE,
    expected_date  DATE,
    received_date  DATE,
    notes          TEXT,
    created_at     TIMESTAMP       NOT NULL DEFAULT NOW(),
    CONSTRAINT po_dates_sane CHECK (expected_date IS NULL OR expected_date >= order_date),
    CONSTRAINT po_received_after_order CHECK (received_date IS NULL OR received_date >= order_date)
);

CREATE INDEX idx_po_supplier  ON purchase_orders(supplier_id);
CREATE INDEX idx_po_warehouse ON purchase_orders(warehouse_id);
CREATE INDEX idx_po_status    ON purchase_orders(status);


CREATE TABLE purchase_order_items (
    po_item_id        SERIAL        PRIMARY KEY,
    po_id             INT           NOT NULL REFERENCES purchase_orders(po_id) ON DELETE CASCADE,
    product_id        INT           NOT NULL REFERENCES products(product_id),
    quantity_ordered  INT           NOT NULL CHECK (quantity_ordered > 0),
    quantity_received INT           NOT NULL DEFAULT 0 CHECK (quantity_received >= 0),
    unit_cost         NUMERIC(12,2) NOT NULL CHECK (unit_cost >= 0),
    CONSTRAINT received_le_ordered CHECK (quantity_received <= quantity_ordered),
    CONSTRAINT poi_unique_product_per_po UNIQUE (po_id, product_id)
);

CREATE INDEX idx_poi_product ON purchase_order_items(product_id);


-- ----------------------------------------------------------------------------
-- stock_movements  -  immutable ledger of every inventory change
-- Every row represents one event (receipt, sale, transfer, adjustment).
-- ----------------------------------------------------------------------------
CREATE TABLE stock_movements (
    movement_id    SERIAL              PRIMARY KEY,
    product_id     INT                 NOT NULL REFERENCES products(product_id),
    warehouse_id   INT                 NOT NULL REFERENCES warehouses(warehouse_id),
    movement_type  movement_type_enum  NOT NULL,
    quantity       INT                 NOT NULL CHECK (quantity > 0),
    reference_type VARCHAR(30),  -- e.g. 'PURCHASE_ORDER', 'SALES_ORDER', 'MANUAL'
    reference_id   INT,          -- id in the referenced table (no FK -- polymorphic)
    notes          TEXT,
    created_at     TIMESTAMP           NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_movements_product   ON stock_movements(product_id);
CREATE INDEX idx_movements_warehouse ON stock_movements(warehouse_id);
CREATE INDEX idx_movements_created   ON stock_movements(created_at DESC);
