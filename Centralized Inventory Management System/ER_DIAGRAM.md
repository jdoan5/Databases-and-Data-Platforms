# Centralized Inventory Management System — Entity-Relationship Diagram

## How to generate the *live* diagram in DataGrip

DataGrip can draw this from your actual database — always in sync with reality:

1. In the **Database Explorer**, right-click **`inventory_mgmt → public`** (the schema)
2. Choose **Diagrams → Show Visualization** (or press **⌥⇧⌘U**)
3. A diagram tab opens with all 8 tables as boxes and the foreign keys as connecting lines
4. Tips while viewing:
   - Drag boxes to rearrange; the layout is yours to organize
   - Hover a line to highlight the exact column → column link
   - Right-click the canvas → **Show → Columns / Keys / Indices** to control detail
   - Right-click → **Export to File** to save a PNG for notes

## The same model, as a Mermaid diagram

This renders in any Markdown viewer that supports Mermaid (GitHub, VS Code with a
Mermaid plugin, many note apps). It's a portable, version-controllable picture of
the schema.

```mermaid
erDiagram
    categories ||--o{ categories          : "parent of (self-ref)"
    categories ||--o{ products            : classifies
    products   ||--o{ stock_levels        : "stocked as"
    warehouses ||--o{ stock_levels        : holds
    suppliers  ||--o{ purchase_orders     : supplies
    warehouses ||--o{ purchase_orders     : "delivered to"
    purchase_orders ||--o{ purchase_order_items : contains
    products   ||--o{ purchase_order_items : "ordered as"
    products   ||--o{ stock_movements     : "moved as"
    warehouses ||--o{ stock_movements     : "moved at"

    categories {
        int      category_id PK
        varchar  name
        int      parent_id   FK "-> categories"
    }
    suppliers {
        int      supplier_id PK
        varchar  name
        varchar  contact_email
        boolean  is_active
    }
    warehouses {
        int      warehouse_id PK
        varchar  code UK
        varchar  name
    }
    products {
        int      product_id PK
        varchar  sku UK
        varchar  name
        int      category_id FK
        numeric  unit_price
        numeric  unit_cost
        int      reorder_point
        int      reorder_quantity
    }
    stock_levels {
        int      product_id   PK,FK
        int      warehouse_id PK,FK
        int      quantity
        timestamp last_updated
    }
    purchase_orders {
        int      po_id PK
        varchar  po_number UK
        int      supplier_id  FK
        int      warehouse_id FK
        enum     status
        date     order_date
        date     expected_date
        date     received_date
    }
    purchase_order_items {
        int      po_item_id PK
        int      po_id       FK
        int      product_id  FK
        int      quantity_ordered
        int      quantity_received
        numeric  unit_cost
    }
    stock_movements {
        int      movement_id PK
        int      product_id   FK
        int      warehouse_id FK
        enum     movement_type
        int      quantity
        varchar  reference_type
        int      reference_id
        timestamp created_at
    }
```

## How to read it

- **`||--o{`** means "one-to-many": one category classifies **many** products; one
  product can be a line on **many** purchase orders, etc. The `||` (exactly one)
  side is the *parent*; the `o{` (zero-or-many) side is the *child* holding the
  foreign key.
- **PK** = primary key (uniquely identifies a row). **FK** = foreign key (points at
  a PK elsewhere). **UK** = unique key (no duplicates, e.g. `sku`, warehouse `code`).
- **`stock_levels`** has a *composite* PK `(product_id, warehouse_id)` — that's the
  database guaranteeing **one stock row per product per warehouse**. It's the classic
  "junction table" resolving the many-to-many between products and warehouses.
- **`categories` points at itself** (`parent_id → category_id`) — that's how a tree
  (Electronics → Laptops) lives in a single flat table.
- **`stock_movements.reference_id` has no FK line** on purpose — it's *polymorphic*:
  it may point at a purchase order, a sales order, or nothing, depending on
  `reference_type`. That flexibility is the trade-off for losing a hard FK guarantee.

## The shape of the model, in one sentence

`products` and `warehouses` are the two "anchor" entities; `stock_levels` is the
**current balance** where they meet, and `stock_movements` is the **history** of how
that balance got there — with `suppliers` + `purchase_orders` feeding stock *in*.
