# Stage 3 — Indexing, one change at a time

PostgreSQL 17.10, 5,000,000 movements, `PROFILE=full`, n=15 round-robin
iterations, warm cache, JIT off. Every number below is `Execution Time` from
`EXPLAIN (ANALYZE, BUFFERS, SERIALIZE TEXT, SETTINGS, FORMAT JSON)`, never
client wall-clock. Reproduce with `make reset PROFILE=full && make stage3`.

## The headline table

| Query | Baseline | After Stage 3 | Δ | Buffers | What did it |
|---|--:|--:|--:|--:|---|
| Q01 low stock | 35.0 ms | **9.7 ms** | −72% | 3,979 → 3,176 | denormalized `reorder_point` + partial index |
| Q04 point lookup | 40.6 ms | **2.4 ms** | −94% | 31,567 → 221 | covering index + `VACUUM` |
| Q09 overdue POs | 5.1 ms | **0.4 ms** | −92% | 3,697 → 202 | partial index on open POs |
| Q03 recent feed | 1.2 ms | 1.2 ms | — | 531 → 533 | untouched |
| Q05 daily rollup | 49.5 ms | 52.1 ms | +5% | 2,695 → 7,013 | **collateral damage, see 3a** |
| Q07 top movers | 204.7 ms | 209.3 ms | +2% | 21,721 → 56,526 | **collateral damage, see 3a** |
| Q02, Q06, Q08, Q10 | — | — | +1–4% | ↑ | index maintenance overhead |

Three queries got 12–17× faster. Two got measurably worse for a reason worth
more than the wins.

---

## 3a — Composite vs. covering, and the index-only scan that wasn't

Both indexes carry the same keys. The covering one adds three payload columns so
Q04 can be answered without touching the heap at all.

| Step | Q04 p50 | Buffers | Heap Fetches |
|---|--:|--:|--:|
| baseline (`idx_movements_warehouse` bitmap scan) | 40.6 ms | 31,567 | — |
| composite `(product_id, created_at DESC)` | 21.5 ms | 10,992 | — |
| covering `INCLUDE (warehouse_id, quantity, movement_type)`, no vacuum | 3.1 ms | 405 | **17,755** |
| same index, after `VACUUM` | **2.4 ms** | **221** | **0** |

**The finding.** An `Index Only Scan` is not heap-free until the visibility map
says the pages are all-visible. Immediately after the 18,000-row `UPDATE` used
to dirty the map, the plan still proudly reports `Index Only Scan` — and then
does 17,755 heap fetches, which is most of the cost the index was supposed to
remove. `VACUUM` is what makes the optimization real, not `CREATE INDEX`.

**The finding underneath the finding.** `pg_class.relallvisible` reported
`51547 / 51547` — 100% all-visible — at the exact moment the plan was doing
17,755 heap fetches. That counter is only refreshed by `VACUUM`/`ANALYZE`; the
`UPDATE` punched holes in the real visibility map without touching it. The
catalog was confidently wrong. Trust `Heap Fetches` in the plan, not the
catalog.

**What it cost.** `idx_sm_prod_created_incl` is **239 MB** — larger than the
primary key, and the biggest object on the table after the heap. Total index
footprint on `stock_movements` is now 521 MB against a 407 MB heap: *more index
than table*.

---

## 3b — The partial index that works, and the one that cannot be built

**Works.** Q09's predicate is syntactically identical to the index predicate, so
the planner's implication prover matches it:

```sql
CREATE INDEX idx_po_open_expected ON purchase_orders (expected_date)
  WHERE status IN ('PLACED', 'PARTIALLY_RECEIVED');
```

5.1 ms → **0.4 ms**, 3,697 → 202 buffers. The index is **96 kB** against 280 kB
for the full-table `idx_po_status`, because it only indexes the ~30% of rows
that are actually open.

**Cannot be built.** Q01's real predicate is
`stock_levels.quantity < products.reorder_point` — two tables. Postgres refuses:

```
ERROR: cannot use subquery in index predicate
```

A partial index predicate may only reference columns of its own table. There is
no clever way around this; the only fix is to stop the predicate being
cross-table.

**What the fix costs.** Copy `reorder_point` onto `stock_levels`, add a sync
trigger on `products`, and rewrite `v_low_stock_items` to reference the local
column — the planner cannot match the index predicate otherwise, so the
optimization leaks into the view definition.

| | Before | After |
|---|--:|--:|
| Q01 p50 | 35.0 ms | **9.7 ms** (−72%) |
| `UPDATE` 500 products, column with no trigger | 5.2 / 6.7 ms | — |
| `UPDATE` 500 products, `reorder_point` | — | 20.3 / 17.7 ms |

**3.2× write amplification** on every `reorder_point` change, plus a 908 ms
backfill across 360,000 rows, plus a denormalized column that can now drift.
That is the price of Q01's 3.6× read win, and it is a trade rather than a
victory. On a write-heavy catalog it would be the wrong call.

---

## 3c — BRIN: 1,523× smaller, and the planner was right to ignore it

`created_at` correlation is 1.0 by construction — the generator emits rows
`ORDER BY created_at` — which is the only condition under which BRIN is viable
at all.

| Index | Size |
|---|--:|
| `idx_movements_created` (B-tree) | 107 MB |
| `idx_sm_created_brin` (`pages_per_range=32`) | **72 kB** |

**Adding BRIN alongside the B-tree changed nothing.** No plan changed. No buffer
moved. Given both, the planner correctly prefers exact tuple pointers over a
block-range summary that has to recheck every tuple in a candidate range.

So the real question is replacement, not addition. Dropping the 107 MB B-tree
and keeping only the 72 kB BRIN:

| Query | With B-tree | BRIN only | Δ |
|---|--:|--:|--:|
| Q03 `ORDER BY created_at DESC LIMIT 200` | 1.2 ms | **38.9 ms** | **+3,140%** |
| Q05 daily rollup | 52.1 ms | 49.0 ms | −6% (buffers 7,013 → 2,264) |
| Q07 top movers | 209.3 ms | 200.2 ms | −4% (buffers 56,526 → 17,398) |

**Why Q03 collapses**, from the plans:

```
With B-tree                          BRIN only
  Limit                                Limit
    Nested Loop                          Gather Merge
      Nested Loop                          Sort              <-- the killer
        Index Scan [created_btree]           Hash Join
        Memoize → products_pkey                Hash Join
      Memoize → warehouses_pkey                  Bitmap Heap Scan
                                                   Bitmap Index Scan [brin]
```

The B-tree is *already stored in `created_at DESC` order*, so Postgres walks it
backwards, takes 200 rows and stops. BRIN cannot produce ordered output, so the
whole 30-day window must be materialized, hash-joined against both dimensions,
and **sorted** before `LIMIT` can take anything.

**Verdict: revert the BRIN.** An index the planner never chooses is not free —
it is write overhead on every insert for zero read benefit. BRIN would be the
right call on this column if the workload were purely range-aggregate, and the
buffer savings on Q05 and Q07 show that world is real. It is not this workload,
because Q03 exists.

---

## Collateral damage, and why it is in the writeup

Q05 and Q07 got slower and never recovered: buffers went 2,695 → 7,013 and
21,721 → 56,526 with **no plan change at all**.

Cause: the 18,000-row `UPDATE` in 3a. It dropped `pg_stats.correlation` on
`created_at` from **1.0000 to 0.9799** — a 2% loss — and cost Q07 2.6× in
buffers touched. Updated rows are written wherever there is free space, so an
index scan that used to read sequential pages now jumps around.

`VACUUM` reclaims the dead space but does **not** restore physical ordering.
Only `CLUSTER` or a table rewrite does, and both take an `ACCESS EXCLUSIVE`
lock. A 2% correlation loss from touching 0.36% of the table is permanent until
you take an outage.

---

## Two methodology bugs found while measuring

Both were caught by the harness disagreeing with itself, and both invalidated a
full round of numbers before being fixed.

**1. Buffer counts are cumulative.** `total_buffers()` summed every node in the
plan tree. EXPLAIN's counters already include everything a node's children
touched, so this double-counts once per level of nesting — on Q07, a six-deep
plan, it reported 339,126 against a true 56,526. Worse, the error scales with
the value rather than being a constant offset, so it manufactured a phantom
"+208,830 buffers with no plan change" between two runs. Fixed: read the root
node only.

**2. `TRUNCATE` is not a reset.** `make load` replaces rows but leaves every
index, added column and trigger from a later stage in place. A baseline captured
after any Stage 3 work silently measures the *tuned* database — Q04's baseline
read 2.1 ms instead of 40.6 ms, a 19× understatement of its own improvement.
Fixed: `make reset` runs `init` first, and `01_schema.sql` opens with
`DROP TABLE ... CASCADE`.

The second one is the more embarrassing and the more instructive: the benchmark
was quietly comparing the tuned database against itself and reporting that
tuning had achieved almost nothing.
