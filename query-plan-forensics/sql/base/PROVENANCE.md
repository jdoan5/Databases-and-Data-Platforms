# Provenance

These four files are copied verbatim from `../../Centralized Inventory Management System/`
as of 2026-08-31. They are the *baseline* schema this project measures against.

| File | Role here |
|---|---|
| `01_schema.sql` | tables, enums, constraints, the nine original indexes |
| `04_views.sql`  | the five views that supply Q01, Q02, Q03 |
| `05_triggers.sql` | `apply_stock_movement()` — deliberately **disabled** during bulk load |
| `06_procedures.sql` | `transfer_stock()`, `receive_purchase_order()` |

They are copied rather than symlinked for two reasons: the source directory name
contains a space (which breaks Makefile targets, Docker bind mounts and CI shell
globbing), and this project needs the schema pinned so a benchmark from six weeks
ago still reproduces.

`02_seed_data.sql`, `03_queries.sql` and `07_bulk_data.sql` are deliberately NOT
copied. Seed and bulk data are replaced by `../10_generate.sql`, which adds the
three things the original lacks: a fixed seed, deliberate skew, and physical
ordering by `created_at`.
