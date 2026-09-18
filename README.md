# Databases & Data Platforms

[![plan-gate](https://github.com/jdoan5/Databases-and-Data-Platforms/actions/workflows/plan-gate.yml/badge.svg)](https://github.com/jdoan5/Databases-and-Data-Platforms/actions/workflows/plan-gate.yml)

SQL and database engineering on one inventory domain: a schema with real
constraints and triggers, that same schema scaled to five million rows and tuned
with measurements rather than guesses, and a small API + dashboard reading from
it.

Everything here is synthetic data generated from a fixed seed. No customer or
production data.

---

## Projects

### [`query-plan-forensics/`](query-plan-forensics/) — PostgreSQL tuning, measured

The main project. The inventory schema scaled to 5,000,000 rows with a fixed
seed and deliberate skew, then tuned one change at a time — composite vs.
covering indexes, partial indexes, BRIN vs. B-tree, extended statistics, monthly
range partitioning, a materialized view — with every change benchmarked before
it was kept.

Three queries ended up 12–17× faster. **Three of the five optimizations were
reverted after measurement**, which is the part most tuning writeups leave out:

- BRIN — 1,523× smaller than the B-tree, and the planner never chose it
- Monthly partitioning — two queries ~40% slower against a 7.6% win on the one
  it was built for
- A 34 MB index the covering index had silently made redundant

A GitHub Action fails a pull request when a query plan changes shape. It
compares plan node types and estimated cost, never wall-clock, because a
runner's IO is nothing like a laptop's.

Stage writeups: [3](query-plan-forensics/STAGE3-RESULTS.md) ·
[4](query-plan-forensics/STAGE4-RESULTS.md) ·
[5](query-plan-forensics/STAGE5-RESULTS.md) ·
[6](query-plan-forensics/STAGE6-RESULTS.md)

```bash
cd query-plan-forensics
make up && make reset && make check && make bench
```

Needs Docker and nothing else — there is no `psql` on the host path and none is
required; every SQL call goes through the container.

### [`Centralized Inventory Management System/`](Centralized%20Inventory%20Management%20System/) — the schema everything else uses

Plain PostgreSQL, run in order. Enum types, a self-referencing category tree,
check constraints, five views, triggers that maintain `stock_levels` from an
append-only `stock_movements` ledger, and two stored functions
(`transfer_stock`, `receive_purchase_order`).

| File | |
|---|---|
| `01_schema.sql` | tables, enums, constraints, indexes |
| `02_seed_data.sql` | a small readable dataset |
| `03_queries.sql` | joins, aggregates, window functions |
| `04_views.sql` | current stock, low stock, PO summary, valuation, recent movements |
| `05_triggers.sql` | ledger → balances, PO status transitions |
| `06_procedures.sql` | `transfer_stock`, `receive_purchase_order` |
| `07_bulk_data.sql` | scale-up with `generate_series` |
| `EXERCISES.sql` | practice questions, with solutions alongside |

`sample_databases/` holds the Chinook sample for cross-database practice;
`sqlite/` holds a SQLite translation of the schema.

`query-plan-forensics` copies `01`, `04`, `05` and `06` verbatim into
`sql/base/` and documents the provenance, so the tuning work runs against this
exact schema.

### [`Sales Inventory Dashboard/`](Sales%20Inventory%20Dashboard/) — FastAPI + SQLite + vanilla JS

A small read-only dashboard: FastAPI serving KPI endpoints over a SQLite
inventory database, with a dependency-free HTML/CSS/JS front end.

```bash
cd "Sales Inventory Dashboard"
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

From the project directory, not `backend/` — `main.py` imports `.db` relatively.
Python 3.9–3.13; the pinned pydantic has no 3.14 wheel.

---

## Three defects in this schema, on purpose

`query-plan-forensics` includes a self-audit (`make audit`) that reproduces
three real bugs in the SQL above before fixing them. They are left in the
teaching schema deliberately:

1. **`05_triggers.sql`** — a `GREATEST(v_delta, 0)` clamp means the first `OUT`
   against a location with no stock row silently writes `0` instead of raising.
   The clamp swallows the error it looks like it prevents.
2. **`01_schema.sql`** — `CHECK (quantity > 0)` makes a negative cycle-count
   `ADJUSTMENT` impossible to record. Shrinkage is a fact of inventory; this
   schema cannot write it down.
3. **`07_bulk_data.sql`** — `stock_levels` is seeded independently of the
   ledger, so the balances never agreed with `SUM(movements)`. Not because the
   bulk load disables the trigger, which is the obvious and wrong explanation.

---

## License

[MIT](LICENSE)
