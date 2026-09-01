# Stage 4 — When the planner is right and you are wrong

PostgreSQL 17.10, 5,000,000 movements, `PROFILE=full`, n=15 round-robin
iterations, warm cache, JIT off. Reproduce with
`make reset PROFILE=full && make apply-stage3 && make stage4`.

Stage 3 reported three queries 12–17× faster. Stage 4 is the invoice.

---

## 4a — Extended statistics fixed a 6× error and changed almost nothing

`product_id` and `warehouse_id` are 85% correlated by construction. Postgres
estimates a multi-column `AND` by multiplying single-column selectivities, which
assumes independence. The arithmetic, straight from the data:

```
rows matching product_id = 1970          53,585
rows matching warehouse_id = 3          721,047
total rows                            5,000,000

independent estimate  53,585 × 721,047 / 5,000,000  =    7,727
actual rows matching both                            =   46,917     6.1× low
```

`CREATE STATISTICS (ndistinct, dependencies, mcv)` closes it. Measured on three
different query shapes:

| Query shape | Estimate before | Estimate after | Actual | Error before → after |
|---|--:|--:|--:|---|
| Q04, covering index present | 2,579 | 15,923 | 15,539 | 6.0× low → 2.5% |
| Same, covering index dropped | 7,647 | 48,600 | 46,917 | 6.1× low → 3.6% |
| Three-table join on the pair | 8,104 | 48,431 | 48,999 | 6.0× low → 1.2% |

**And in all three the plan was byte-identical, and the runtime did not move.**
*(See the amendment below — a fourth query shape, missed here, does benefit.)*

That is the finding. A misestimate only costs you when it drives a *different
decision*. Here the planner reached the right plan while holding a number that
was six times wrong — a `Bitmap Heap Scan` fed by a `BitmapAnd` is the correct
shape whether the intersection is 7,000 rows or 47,000, and an `Index Only Scan`
on the leading column stays correct regardless of how many rows the trailing
filter removes.

**Where it would matter:** when the estimate crosses a threshold that flips a
decision — nested loop vs. hash join, index scan vs. sequential scan, whether a
sort fits in `work_mem`. None of the ten benchmark queries sits near such a
boundary on this dataset. On a different workload the same fix could be
transformative; here it is measured insurance, not a win.

The statistics object is kept. Unlike an index it costs nothing on write — only
a little `ANALYZE` time — and it protects against a future plan flip. But the
honest headline is: **no measurable effect on any query that filters on the
pair.**

### Amended by Stage 5

That conclusion was drawn from three queries, and all three *filter* on
`product_id AND warehouse_id`. Stage 5 turned up a query it missed: **Q07, which
`GROUP BY`s the correlated pair**, and where the statistics do measurably help.

| | Group estimate | Actual | Q07 p50 |
|---|--:|--:|--:|
| Without extended statistics | 238,020 | 98,729 | 202.6 ms |
| With extended statistics | 59,594 | 98,729 | **187.8 ms** (−7.3%) |

Both measured with 15 round-robin iterations on the same database, adding the
statistics between runs. The estimate does not become *accurate* — it goes from
2.4× over to 1.7× under — but it gets closer, the aggregate is costed better,
and the query gets 7.3% faster.

The generalisation to keep: extended statistics on a correlated pair matter
where the pair determines a **cardinality the planner has to size something
against** — a `GROUP BY`, a hash table, a sort. Where the pair is only a filter
feeding a scan that was already the right choice, they change nothing. Stage 4
tested only the second case and drew the general conclusion from it.

### The 31% improvement that wasn't

The first A/B looked like a 31% win: 64.8 ms without statistics, 44.9 ms with.
It was entirely cold cache on the first execution. Alternating the two
configurations:

```
WITH-1  81.8 ms     <- cold
WITH-2  48.4 ms
WITH-3  47.2 ms
WITHOUT-1  44.8 ms
WITHOUT-2  43.8 ms
WITHOUT-3  43.9 ms
WITH-4  45.1 ms
WITH-5  45.3 ms
```

Steady state is ~45 ms either way, and the run *without* statistics is
marginally faster — which is noise, and the point. One measurement in each
direction would have published a fabricated result.

---

## 4b — What Stage 3's indexes cost on the write side

`stock_movements` is an append-only ledger. Every index is paid for on every
insert, forever, whether or not any query uses it. Measured on a scratch table
of the same shape, 200,000 rows per configuration, `CHECKPOINT` before each run
so full-page images do not land on whichever config runs first.

| Config | Indexes | Time | Rows/sec | WAL | WAL/row | Time × | WAL × |
|---|--:|--:|--:|--:|--:|--:|--:|
| A heap only | 0 | 104 ms | 1,921,230 | 22 MB | 116 B | 1.00 | 1.00 |
| B the original three | 3 | 422 ms | 473,709 | 64 MB | 335 B | 4.06 | 2.89 |
| C + Stage 3's covering index | 4 | 545 ms | 367,175 | 85 MB | 448 B | 5.23 | 3.87 |

Three ordinary B-tree indexes cost **4× the insert time and 2.9× the WAL** of a
bare heap. Adding one covering index on top costs a further **29% time and 33%
WAL**.

WAL matters more than time here: it is what streaming replication ships, what
every backup stores, and what a managed provider meters. 448 bytes of WAL to
insert a row whose payload is about 60 bytes is the real number to quote.

---

## 4c — Six indexes that earn nothing

`pg_stat_reset()`, then a full 15-iteration benchmark pass, then the audit — so
the scan counts reflect the actual query mix rather than whatever was typed by
hand.

| Index | Size | Scans | |
|---|--:|--:|---|
| `idx_movements_product` | **34 MB** | **0** | superseded by the covering index |
| `idx_poi_product` | 6,248 kB | 0 | |
| `idx_po_supplier` | 416 kB | 0 | |
| `idx_products_category` | 400 kB | 0 | |
| `idx_po_status` | **280 kB** | **0** | superseded by the partial index |
| `idx_po_warehouse` | 264 kB | 0 | |
| `idx_movements_warehouse` | 33 MB | 1 | |
| `idx_sm_prod_created_incl` | 236 MB | 45 | |
| `idx_movements_created` | 107 MB | 45 | |

Two of those are not merely unused — they are **superseded by things Stage 3
built and then left in place**:

- `idx_sm_prod_created_incl` leads with `product_id`, so every lookup
  `idx_movements_product` could serve, it serves too. The 34 MB single-column
  index has been dead since the moment the covering index was created, and
  Stage 3 never noticed.
- `idx_po_open_expected` is a 96 kB partial index on exactly the status values
  Q09 filters on, which retires the 280 kB `idx_po_status`.

And the footprint that produced:

| Table | Heap | Indexes | Index : heap |
|---|--:|--:|--:|
| `stock_movements` | 403 MB | **518 MB** | **1.29** |
| `purchase_order_items` | 29 MB | 27 MB | 0.95 |
| `stock_levels` | 39 MB | 21 MB | 0.55 |

More index than table.

**Caveat that belongs in any writeup of this:** "never used" means never used by
*these ten queries*. A real application has queries this harness does not model.
On a production system the same evidence justifies an investigation, not an
immediate `DROP`, and you would watch `pg_stat_user_indexes` over weeks rather
than one benchmark pass.

---

## 4d — Paying it back

Dropped `idx_movements_product` and `idx_po_status`.

| | Before | After |
|---|--:|--:|
| `stock_movements` index size | 518 MB | **484 MB** |
| Index : heap | 1.29 | **1.20** |
| Insert 200k rows | 545 ms | **455 ms** (−16%) |
| WAL generated | 85 MB | **71 MB** (−17%) |
| WAL per row | 448 B | **374 B** |
| Read performance | — | **unchanged, ±3%, no plan changes** |

Sixteen percent of insert throughput and seventeen percent of WAL, recovered for
nothing. The reads did not notice — all ten queries within ±3% and not a single
plan change.

---

## The complete trade, Stages 3 and 4 together

Against the original index set:

| | Change |
|---|---|
| Q04 point lookup | **17× faster** (40.6 → 2.4 ms) |
| Q09 overdue POs | **12.8× faster** (5.1 → 0.4 ms) |
| Q01 low stock | **3.6× faster** (35.0 → 9.7 ms) |
| Insert throughput | 8% slower (422 → 455 ms per 200k) |
| WAL volume | 11% more (64 → 71 MB per 200k) |
| Index footprint | +34 MB net (450 → 484 MB) |
| `reorder_point` updates | 3.2× slower (sync trigger) |
| `created_at` correlation | 1.0000 → 0.9799, permanently |

Before the 4d cleanup that write cost was 29% and 33%. Most of the bill turned
out to be an index nobody was using.

The honest summary: three queries got an order of magnitude faster, writes got
8% slower, and the single largest lever in the whole exercise was deleting
something rather than adding it.

---

## Methodology note

Stage 3 turned up two measurement bugs (cumulative buffer counts, and `TRUNCATE`
not being a reset — both in [STAGE3-RESULTS.md](STAGE3-RESULTS.md)). Stage 4
turned up a third:

**Cold cache manufactures wins.** A single before-and-after pair, run in that
order, credits the second measurement with all the warm-up the first one paid
for. Every A/B in this stage alternates configurations and discards the first
run. The 31% "improvement" in 4a was 100% cache and would have been the most
quotable number in the writeup.
