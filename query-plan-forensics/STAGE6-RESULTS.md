# Stage 6 — Materialization: read latency against staleness against refresh cost

PostgreSQL 17.10, 5,000,000 movements, 360,000 stock rows, warm cache, JIT off.
Reproduce with `make reset PROFILE=full && make apply-stage3 && make stage6`.

Q02 (`v_stock_valuation`) is the one query in the set with no index to add. It
is a full aggregate over every stock row joined to products, collapsing to six
warehouse totals. Nothing to prune, nothing to seek. The only lever left is
whether the answer is computed on demand or stored.

**Verdict: keep it, refresh concurrently every five minutes — and the reason has
nothing to do with performance.**

---

## The read

| | Execution | Buffers | Plan |
|---|--:|--:|---|
| `v_stock_valuation` (computed) | 163.8 ms | 16,991 | `GroupAggregate` over an `Incremental Sort` of 360,000 rows |
| `mv_stock_valuation` (stored) | **0.032 ms** | **1** | `Seq Scan`, six rows |

**5,118× faster on 16,991× fewer buffers**, and the whole thing is 40 kB on disk.

That number is not interesting on its own. Storing a precomputed six-row answer
is obviously faster than recomputing it from 360,000 rows. The interesting
questions are what the refresh costs and what the stored answer is worth.

---

## The refresh, and the lock that matters

| Mode | Time | Locks taken on the matview |
|---|--:|---|
| `REFRESH MATERIALIZED VIEW` | 130.3 ms | `ShareLock`, `ExclusiveLock`, **`AccessExclusiveLock`** |
| `REFRESH ... CONCURRENTLY` | 137.1 ms | `AccessShareLock`, `RowExclusiveLock`, `ExclusiveLock` |

`CONCURRENTLY` costs **5% more time** and never takes `AccessExclusiveLock`, so
readers are not blocked while it runs. On a dashboard query that is the entire
difference between "refreshes are invisible" and "the dashboard hangs for 130 ms
every cycle".

It has a precondition that is easy to miss until it bites: **`CONCURRENTLY`
requires a unique index on the materialized view.** Without one the command
fails outright. Creating that index is a build-time decision, and discovering
you skipped it happens the first time you try to refresh without blocking
production.

---

## The break-even

| | |
|---|--:|
| Saved per read | 163.768 ms |
| Cost per refresh (concurrent) | 137.1 ms |
| **Reads before the refresh pays for itself** | **0.84** |

Under one. If the view is read even *once* between refreshes, materializing it
was cheaper than not.

Scaled to a policy:

| Policy | Server time spent refreshing | Duty cycle | Read speedup |
|---|---|---|--:|
| Every 5 minutes, concurrently | 1.65 s/hour | **0.046%** | 5,119× |

The compute cost is a rounding error. Which means the compute cost is not the
decision, and treating it as the decision is the mistake this stage is really
about.

---

## The decision is staleness, and it is not close

A materialized view is a cache with no invalidation. The moment anything moves,
it is wrong — and it stays wrong until the next refresh, silently, with no
error and no indication to the reader.

One ordinary receipt, 5,000 units:

```
before          WH01   5,997,847 units   $2,744,494,651.29

INSERT INTO stock_movements (..., 'IN', 5000, ...);

live view       WH01   6,002,847 units   $2,747,725,051.29
materialized    WH01   5,997,847 units   $2,744,494,651.29    <- unchanged
```

The trigger updated `stock_levels`. The materialized view does not know and
cannot know.

How often does that happen? The ledger says **285 movements per hour**, 6,847
per day. So the gross value moving through the warehouses inside each staleness
window is:

| Refresh interval | Movements missed | Gross value moved in the window |
|---|--:|--:|
| 1 minute | 5 | $52,518 |
| 5 minutes | 24 | $262,590 |
| 1 hour | 285 | $3,151,082 |
| 1 day | 6,847 | $75,625,980 |

*Gross, not net — `IN` and `OUT` partially cancel, so the actual error is
smaller than these figures. They measure how much value is in motion and
therefore unaccounted for, which is the number to hand someone who has to decide
whether the staleness is tolerable.*

**That is the whole decision.** Not "is the refresh affordable" — it costs 0.046%
of a core. The question is whether an inventory valuation that ignores a quarter
of a million dollars of movement is fit for its purpose. For an operations
dashboard where the number is a trend indicator: yes, easily. For a month-end
financial close or anything an auditor signs: no, and no refresh interval fixes
it, because the answer must be correct *as of a stated instant*, which a
materialized view cannot promise.

---

## Verdict

**Kept**, unlike the BRIN index and the partitioning. This is the first Stage 3–6
optimization that survives.

- Refresh `CONCURRENTLY` on a five-minute schedule. 5% more expensive than the
  blocking form and it never blocks a reader.
- The unique index on `warehouse_id` is not optional — it is what makes
  `CONCURRENTLY` legal.
- Expose `refreshed_at` in the view itself, so every consumer can see how stale
  the number is rather than assuming it is live. The column is in the definition
  for that reason and for no other.
- Anything that must be correct as of an instant reads `v_stock_valuation`, the
  plain view, and pays the 163 ms.

The general shape: **materialization is a correctness decision wearing a
performance costume.** The performance question answers itself in under one
read. The correctness question is the only one that takes judgment, and it is
the one the refresh-interval tuning guides never ask.
