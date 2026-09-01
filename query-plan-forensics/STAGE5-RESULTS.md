# Stage 5 — Partitioning, including the queries it made slower

PostgreSQL 17.10, 5,000,000 movements, 26 monthly range partitions on
`created_at`, n=15 round-robin iterations, warm cache, JIT off. Both sides
measured from the same clean rebuild of the Stage 4 end state. Reproduce with
`make reset PROFILE=full && make apply-stage3 && make stage5`.

**Verdict: partitioning made this workload worse, and the code is kept as the
experiment rather than the configuration.** Two queries lost ~40%. The query it
was built to help gained 7.6%.

---

## The query verdict

| Query | Before | After | Δ | Buffers Δ | Scan nodes | Partitions |
|---|--:|--:|--:|--:|--:|--:|
| Q10 full history | 198.2 ms | **279.2 ms** | **+40.9%** | −21,320 | 1 → 26 | 26 of 26 |
| Q04 point lookup | 2.1 ms | **2.9 ms** | **+39.9%** | +30 | 1 → 10 | 10 of 26 |
| Q03 recent feed | 1.2 ms | 1.3 ms | +4.3% | +5 | 3 → 5 | 2 of 26 |
| Q08 supplier spend | 39.7 ms | 41.2 ms | +3.7% | 0 | 3 → 3 | none |
| Q07 top movers | 187.8 ms | 186.2 ms | −0.8% | −4,547 | 2 → 11 | 11 of 26 |
| Q01, Q06 | — | — | −0.5%, −1.9% | — | — | none |
| Q02 valuation | 197.4 ms | 188.2 ms | −4.6% | 0 | — | none |
| Q05 daily rollup | 50.6 ms | **46.7 ms** | **−7.6%** | −568 | 1 → 1 | **1 of 26** |

Q09 moved +21.5%, which is 0.06 ms on a 0.3 ms query against `purchase_orders`
— a table partitioning never touched. It is noise, and listing it as a
regression would be dishonest.

Q05 is the case partitioning exists for: a single-month predicate pruned to
exactly one partition out of twenty-six, and it reads that partition with a
plain `Seq Scan` instead of an index. **7.6% faster.** That is the entire
upside.

### Q10 — one sequential scan became twenty-six index scans

```
BEFORE                        AFTER
  Sort                          Sort
    Aggregate                     Aggregate
      Gather Merge                  Gather Merge
        Sort                          Sort
          Aggregate                     Aggregate
            Seq Scan                      Append
                                            Index Only Scan on p2026_03
                                            Index Only Scan on p2025_07
                                            ... 24 more
```

An unbounded aggregate cannot prune anything. The planner switched each child to
an index-only scan, which cut buffers by **41%** — and the query still got
**40.9% slower**, because twenty-six B-tree descents plus `Append` overhead cost
more than the buffers saved.

### Q04 — one index descent became ten

A point lookup on `product_id` filtered to `created_at >= 2026-01-01` was a
single `Index Only Scan`. Pruning correctly eliminated 16 of 26 partitions, and
the ten survivors each need their own descent under an `Append`. Buffers went
*up*, 114 → 144, for an identical answer.

Pruning only pays when it prunes to *few* partitions. Twenty-six down to ten is
a loss.

### Buffers are not latency

Q10 got 41% cheaper in buffers and 41% slower in time. Worth stating plainly,
because buffers are the metric everyone reaches for first and here it pointed
exactly backwards.

---

## The claim this stage nearly published

The first version of this comparison reported **"Q07 +48.5%, partitioning cost
it its parallel plan."** It had a `Gather` before and none after, so the
mechanism looked obvious and the number was the most quotable in the writeup.

It was wrong. Q07's plan is **unstable across rebuilds**, independent of
partitioning:

| | Runs | Q07 p50 |
|---|--:|--:|
| Parallel plan (`Gather`) | 2 of 18 | ~130 ms |
| Serial plan | 16 of 18 | 188–217 ms |

The baseline chosen for the comparison happened to be one of the two anomalous
parallel runs. Measured against a clean rebuild of the same logical state,
partitioning moves Q07 by **−0.8%** — nothing.

The row estimate is not the discriminator either: the parallel run estimated
59,056 groups and a serial run estimated 59,594, essentially identical. Adding
the extended statistics from Stage 4 moves the estimate from 238,020 to 59,594
against 98,729 actual — closer, but it does not reproduce the parallel plan. The
flip remains unattributed, and saying so is the honest position.

**The lesson is about method, not about Postgres.** An unstable baseline does
not produce noisy results; it produces confident, mechanistic, entirely wrong
ones. The plan diff *supported* the false conclusion. What caught it was
noticing that the reverted table — which should have matched the baseline —
didn't, and then checking every run in the results directory instead of the two
being compared.

---

## What it costs to operate

26 partitions with four indexes each produce **104 index objects** where there
were four.

**`CREATE INDEX CONCURRENTLY` is unavailable on a partitioned parent.**

```
ERROR:  cannot create index on partitioned table "stock_movements" concurrently
```

On an unpartitioned table you add an index online. Here the options are to take
the lock on the parent, or to CIC each of the 26 partitions individually, then
`CREATE INDEX ON ONLY` the parent and `ATTACH` each child index by hand.

*(The first version of this test wrapped the statement in a `DO` block and got
"cannot run inside a transaction block" — a different error entirely, proving
nothing. It has to be run bare.)*

**Global uniqueness is gone.**

```
ERROR:  unique constraint on partitioned table must include all partitioning columns
```

The primary key had to become `(movement_id, created_at)`. Nothing now prevents
the same `movement_id` existing in two partitions. For a ledger whose purpose is
unambiguous row identity, that is a real loss, not a technicality.

**`ATTACH` has sharp edges.** `LIKE ... INCLUDING DEFAULTS` does not carry CHECK
constraints, and `ATTACH` rejects a child missing them
(`child table is missing constraint stock_movements_quantity_check`). A matching
CHECK lets `ATTACH` skip validating the incoming table — but it does not exempt
the DEFAULT partition, which is scanned under `ACCESS EXCLUSIVE` to prove no row
already sitting there belongs in the new range.

**The conversion drops things quietly.** `LIKE ... INCLUDING CONSTRAINTS` copies
CHECK constraints but not foreign keys and not the primary key; both had to be
re-added by hand. `DROP TABLE ... CASCADE` took `v_recent_movements` with it —
and re-running `04_views.sql` to restore it would have looked harmless while
silently reverting `v_low_stock_items` to its base cross-table definition,
undoing Stage 3b and costing Q01 its partial index with no error anywhere. The
extended statistics died with the table too; recreating them is required or the
before/after differs by more than partitioning.

---

## The one thing partitioning actually buys

Nobody partitions a ledger for query speed. The case is data lifecycle. Expiring
one month, 212,338 rows:

| | Partitioned | Unpartitioned |
|---|---|---|
| Operation | `DETACH` + `DROP TABLE` | `DELETE` + `VACUUM` |
| Time | **6.5 ms** | 682 ms (40 + 642) |
| Size before | — | 489 MB |
| After the delete | — | 489 MB |
| After `VACUUM` | — | **489 MB** |
| Disk returned to the OS | **yes, immediately** | **no** |

**105× faster, and the second half matters more than the first.** `DELETE` plus
`VACUUM` marks space reusable by that table; it does not give it back. The
489 MB never moves. `DROP TABLE` on a partition returns the file to the
filesystem the moment it commits.

---

## Verdict, and the condition that would flip it

**Reverted**, same discipline as the BRIN index in Stage 3c: measured,
concluded, removed.

- This workload is query-heavy with **no retention policy**. Expiry is the one
  thing partitioning delivers well, and nothing in the benchmark asks for it.
- 403 MB of heap is far below the size where `VACUUM` duration or maintenance
  windows start to dominate.
- Two of ten queries lost ~40%; the query it was designed for gained 7.6%.

**It would be the right call if any of these were true**, and the conditions are
more useful than the verdict:

- A retention requirement exists — "keep 24 months" turns a 682 ms `VACUUM` that
  reclaims nothing into a 6.5 ms `DROP` that reclaims everything, monthly and
  forever.
- The table is large enough that `VACUUM` cannot finish in a maintenance window.
- The query mix is predominantly single-period, like Q05. Here it is
  predominantly cross-period, which is the worst possible shape.
- Bulk loads arrive per-period and can be `ATTACH`ed pre-built rather than
  inserted.

Partitioning is a **data-lifecycle** feature that sometimes helps queries, not a
query optimization that sometimes helps lifecycle. Choosing it for query speed
on a cross-period workload is choosing it for the thing it is worst at.

---

## A note on `make s5-ops`

It is destructive by design: it expires a month, because that is the operation
being measured. The table ends at 4,794,940 rows. Any benchmark comparison must
start from `make reset`, not from a post-`s5-ops` database — comparing 4.79M
rows against a 5M baseline is how the first pass at this stage went wrong.
