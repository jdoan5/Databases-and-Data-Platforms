# Query Plan Forensics

Scale a real PostgreSQL inventory schema to five million rows with a fixed seed
and deliberate skew, change one thing at a time, and measure it — composite vs.
covering indexes, BRIN vs. B-tree, extended statistics, monthly range
partitions, a materialized view — then wire a GitHub Action that fails a pull
request when a query plan changes shape.

Including the optimizations that made things worse.

---

## Run it

Requires Docker. Nothing else — there is no `psql` on the host PATH and none is
needed; every SQL invocation goes through the container, which also guarantees
client version matches server version 17.

```bash
cd query-plan-forensics
make up          # pinned Postgres 17, all planner GUCs on the command line
make reset       # schema + 5M movements, fixed seed  (PROFILE=small for 500k)
make check       # acceptance gates: skew, correlation, determinism
make bench       # 15 round-robin iterations, results to bench/results/
make report      # markdown table
```

Then, once the schema is settled and you are ready to freeze the contract:

```bash
make reset       # ALWAYS start an experiment here — see below
make venv        # once — pytest, for running the gate locally
make baseline    # same run, but also writes bench/baselines/*.json
make gate        # the regression test, locally
```

`make help` lists everything.

**`make reset`, not `make load`.** `TRUNCATE` replaces rows but leaves every
index, added column and trigger from a later stage in place, so a baseline
captured after any tuning work silently measures the *tuned* database. This cost
a full round of numbers before it was noticed — Q04's baseline read 2.1 ms
instead of 40.6 ms, understating its own improvement by 19x. `make reset` runs
`init` first, and `01_schema.sql` opens with `DROP TABLE ... CASCADE`.

Everything above has been run end to end on Postgres 17.10 / arm64. Stages 0–4
and 7 are complete: all four acceptance gates pass, ten queries benchmark at a
coefficient of variation under 4%, the regression gate is armed with committed
baselines, and `make audit` reproduces all three schema defects. Stage 3 results
are in **[STAGE3-RESULTS.md](STAGE3-RESULTS.md)** — three queries 12–17x faster,
two measurably worse, and two methodology bugs found along the way. Stage 4 is
the invoice: **[STAGE4-RESULTS.md](STAGE4-RESULTS.md)** — write amplification in
time and WAL bytes, extended statistics that fixed a 6x estimate error and
changed nothing, and a 34 MB index that Stage 3 silently made redundant.

---

## Why the dataset is built the way it is

`sql/10_generate.sql` replaces the original `07_bulk_data.sql`. Three
differences, each of which decides which optimizations are even available later:

**A fixed seed.** `setseed(0.42)` plus `max_parallel_workers_per_gather = 0`
during generation. Re-running produces identical data, so a plan captured six
weeks ago still reproduces and the CI gate has something stable to compare
against. Gate 4 in `sql/90_acceptance.sql` hashes a 10k-row sample to prove it.

**Deliberate skew.** Uniform-random data gives partial indexes and extended
statistics nothing to bite on. Two skews:

- A power law over SKUs — `power(random(), 5)` puts roughly 55% of all movements
  on the top 5% of products. The exponent matters: at `power(random(), 3)` the
  top 5% take only 36.8% and the acceptance gate demanding >50% fails.
  `0.05 ^ (1/5) = 0.549`; check the arithmetic before changing it.
- A product-to-warehouse correlation. Each product ships from a home warehouse
  85% of the time, so `product_id` and `warehouse_id` are correlated by
  construction. The planner assumes independence and underestimates by 6.1×
  (7,727 estimated against 46,917 actual). Stage 4 fixes that estimate with
  `CREATE STATISTICS` and finds it changes no plan and no runtime — which is a
  more useful result than a win, and not one you can get without the skew.

**Physical ordering by `created_at`.** The `ORDER BY` at the end of the insert
looks like a pointless flourish until Stage 3. `pg_stats.correlation` lands near
1.0; on a randomly-ordered table it is near 0.01, and a BRIN index over 0.01
correlation is a 40KB index the planner correctly refuses to use. The data
layout decision at load time determines which index types exist two stages later.

**60,000 products, not 500.** The original script left `stock_levels` at 3,000
rows. A partial index on a table that fits in fifteen pages is a table the
planner will seq-scan anyway, which makes the Stage 3 denormalization experiment
unmeasurable. At 60k products the table is 360k rows and the experiment has
something to say.

The trigger is disabled during the load — `stock_movements` fires a per-row
upsert into `stock_levels`, and a roughly balanced random walk breaches
`CHECK (quantity >= 0)` within a few thousand rows. This is the standard ETL
shape and Stage 1 measures it against the two slower alternatives.

---

## The self-audit

`make audit` reproduces three real defects in the committed schema, before
fixing any of them. Auditing your own published work is the point.

| # | Where | What |
|---|---|---|
| 1 | `05_triggers.sql:39` | `GREATEST(v_delta, 0)` on the INSERT arm means a first-ever `OUT` against a location with no `stock_levels` row silently writes `0` instead of tripping the `quantity >= 0` CHECK at `01_schema.sql:108`. The clamp swallows the error it looks like it prevents. |
| 2 | `01_schema.sql:162` | `CHECK (quantity > 0)` makes a negative cycle-count `ADJUSTMENT` impossible to record. The trigger's own comment at `05_triggers.sql:34` concedes it. Shrinkage is a fact of inventory; this schema cannot write it down. |
| 3 | `07_bulk_data.sql:47` | `stock_levels` is seeded independently of the ledger, so `SUM(signed movements)` has never equalled `stock_levels.quantity`. |

Finding 3 has a trap in it. The obvious explanation — "the bulk load disabled
the trigger" — is wrong, and anyone who checks will catch it. The balances are
seeded *before any movements exist at all*; they were never derived from the
ledger, disabled trigger or not. The correct explanation is the more interesting
one.

---

## What the regression gate asserts

| Assert | Why it reproduces on a different machine |
|---|---|
| Plan fingerprint: ordered `(depth, node, relation, index)` | pure function of schema + statistics + GUCs |
| Root `Total Cost` within ±15% | an estimate, not a measurement |
| `Plan Rows` within 2x | catches a statistics regression before it becomes a plan regression |
| `required_indexes` still present | catches a dropped or renamed index |
| **Never:** wall-clock, execution time, planning time, workers launched | a GitHub runner's IO is nothing like a laptop's; asserting on these guarantees a flapping gate |

`total_buffers` is recorded and reported but **not** asserted by default. It is
tempting — buffers *touched* looks deterministic for a given plan — but it moves
with actual worker count and with bitmap-heap lossiness under `work_mem`
pressure. Set `QPF_ASSERT_BUFFERS=1` to enforce it and watch what happens.

It is read from the **root node only**, never summed across the tree. EXPLAIN's
buffer counters are cumulative — every node already includes what its children
touched — so summing double-counts once per level of nesting. That bug reported
339,126 buffers for a query that touched 56,526, and because the error scales
with the value rather than being a constant offset, it invented a phantom
"+208,830 buffers with no plan change" between two runs.

CI runs `PROFILE=small`; local runs `PROFILE=full`. Plan shapes can legitimately
differ between them — that is a cost-based planner working correctly. The gate
proves the plan did not change *relative to the same-sized dataset it was
baselined against*; it does not prove the 5M-row plan is unchanged. Run
`make bench PROFILE=full` locally before merging anything that touches an index.

### The PR that fails

Don't fake it by dropping an index; that is obviously artificial. Open a PR that
rewrites Q05's date range as `date_trunc('month', created_at) = DATE '2026-06-01'`.
Same result set, reads cleaner, and it is non-sargable — a careful reviewer
would have approved it.

Measured on `PROFILE=small`, 500k movements:

```
BASELINE (sargable half-open range)      AFTER the "cleaner" rewrite
  Aggregate                                Aggregate
    Sort                                     Gather Merge
      Index Scan [idx_movements_created]       Aggregate
                                                 Sort
                                                   Seq Scan on stock_movements

indexes  ['idx_movements_created']  ->  []
cost     2,758.50                   ->  9,653.55     (+250%)
buffers  283                        ->  5,243        (18.5x)
```

The gate's output names the divergence directly:

```
Q05 FAILED: plan fingerprint changed
  first divergence at node 1:
    baseline: [1, 'Sort', None, None]
    current:  [1, 'Gather Merge', None, None]
  root_total_cost 2758.5 -> 9653.55
```

Nine queries stay green; reverting the edit turns the tenth green again. That
red-then-green pair on one PR is the headline screenshot.

---

## Where DataGrip earns its place

Free under the non-commercial licence (self-education qualifies; requires online
activation, and anonymized telemetry is mandatory on that tier). The identical
engine ships as the **Database Tools and SQL** plugin bundled in IntelliJ IDEA
Ultimate, so this is not a second purchase.

| Feature | Used for |
|---|---|
| Explain Plan visualizer | the core tool — reading a 24-partition `Append` as raw text is miserable |
| Multiple data sources in one project | `inventory_baseline`, `inventory_tuned`, and `chinook` side by side |
| Schema compare | the diff *is* the list of indexes, statistics objects and partitions this project added; it cannot drift the way a hand-written changelog does |
| Per-source consoles | one `.sql` file executed against baseline and tuned by switching the console target — not two queries typed twice and hoped identical |
| Session control | `CREATE INDEX` on 5M rows takes minutes; detach it and keep the console usable for `pg_stat_progress_create_index` |

Connect to `localhost:55432`, database `inventory`, user `postgres`, password
`qpf`. Port 55432 rather than 5432 so any local Postgres is left alone.

PyCharm free core hosts `bench/`. It does *not* get the database tools — those
are Pro-only in unified PyCharm — and does not need them.

DataSpell is not an option: JetBrains deprecated it on 2026-05-28 and converted
subscriptions to PyCharm Pro on 2026-09-01.

---

## Stages

- [x] **0** — pinned Postgres 17, every planner GUC in git, checksums on at initdb
- [x] **1** — 5M rows, fixed seed, deliberate skew, physical ordering
- [x] **2** — baseline harness, round-robin iterations, EXPLAIN-sourced timings
- [x] **3** — indexing one change at a time: composite vs. covering, partial, BRIN vs. B-tree — **[results](STAGE3-RESULTS.md)**
- [x] **4** — when the planner is right and you are wrong: write amplification, `CREATE STATISTICS`, the index audit — **[results](STAGE4-RESULTS.md)**
- [ ] **5** — monthly range partitions, including the query it makes slower
- [ ] **6** — `v_stock_valuation` as a view vs. a matview: latency against staleness
- [x] **7** — the regression gate (harness in place; capture baselines to arm it)

Stages 5 and 6 are evening add-ons, each independently publishable. Ship 0–4 and
7 first.

## Honest limitations

- `buffer_cold` mode empties `shared_buffers` but **not** the Docker Desktop
  Linux VM's page cache, so it is not a true cold read. Say so rather than
  letting an interviewer find it.
- Benchmarks on a laptop are subject to thermal throttling. The harness reports
  a coefficient of variation per query and warns above 10%; numbers above that
  are not defensible under questioning.
- The dataset is synthetic. The skew is deliberate and documented, but it is
  still a model of a business rather than one.
