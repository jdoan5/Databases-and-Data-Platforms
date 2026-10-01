# Stage 4 — Cheaper and faster, measured

The pipeline as Stage 3 left it (the GA4 sample, 4.3 M events, plus the site's own export) against the same
pipeline after nine experiments, each changing one thing and each kept or reverted on its numbers. Every
number comes from one harness, [`bench/`](bench/README.md): BigQuery jobs read back from
`region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT`, Dataproc batches from `runtimeInfo.approximateUsage` and the
job's own timings, storage from each table's metadata. Every run is a record in
[`bench/results/`](bench/results/). Figures are **median (min–max)**; BigQuery variants ran 3 times, Spark
variants at least 2, with the query cache off. Money is list price before free tiers, from Google's pricing pages
read on 2026-09-28: BigQuery on demand $6.25 per TiB billed (US multi-region), Serverless for Apache Spark
standard tier $0.06 per DCU-hour plus $0.000054795 per GiB-hour of shuffle storage (us-central1), storage
$0.02 / $0.04 per GiB-month active logical / physical ([`prices.py`](bench/tagline_bench/prices.py) has the
pages). Measured on 2026-09-29.

```bash
cd tagline
make bench-build VARIANT=<name> RUNS=3                          # make build (or BUILD_CMD=...) x3, every job recorded
make bench-build VARIANT=<name> BUILD_CMD="make build-incremental"
make bench-spark VARIANT=<name> RUNS=2                          # the attribution batch x2
make bench-diff VARIANT=<name>                                  # the correctness gate against the baseline's output
make stage4-equivalence                                         # the daily build against a full build (~$0.26)
make bench-report                                               # medians and ranges from bench/results/
```

## The headline table

A **daily run** is what the Airflow DAG does once a day: bring the Stage 2 tables up to date, run the 9 checks,
run the attribution batch. Before Stage 4 that meant rebuilding every table from every export day.

| | Baseline (Stage 3 code) | After Stage 4 | Δ | What did it |
|---|--:|--:|--:|---|
| BigQuery billed per daily run | 8.624 GiB | **2.123 GiB**³ | **−75%** | incremental daily build (E1) on narrow reads (E2) |
| BigQuery slot-ms per daily run | 1,706,455 (1.62–1.74 M) | **650,428** (0.58–0.65 M) | **−62%** | same |
| BigQuery wall per daily run (`make`) | 85.3 s (81.3–91.4) | 89.7 s (86.6–98.8) | not faster; +12% over all six final-code runs¹ | its 27 statements run one after another |
| Spark batch wall | 390 s (380–517) | **144 s** (134–154) | **−63%** (−60% against the same-day control²) | `local[4]` (E6), 4 shuffle partitions (E7) |
| Spark DCU-hours | 0.4527 (0.4472–0.6195) | **0.0818** (0.0764–0.0872) | **−82%** (−80%²) | E6, E7, and a 4 GiB driver (E9) |
| List price per daily run | $0.0814 | **$0.0183** | **−77%** | $0.0130 BigQuery + $0.0054 Spark (each rounded) |
| A daily run for a month (×30) | $2.44 | **$0.55** | −77% | |
| Full rebuild billed | 8.624 GiB | 8.052 GiB (8.045–8.057) | −6.6% | narrow reads (E2) |
| Full rebuild wall (`make build`) | 85.3 s (81.3–91.4) | 98.7 s (85.7–109.8) | not faster; +14% over all six final-code runs¹ | cause not isolated (below) |
| Whole DAG, daily run (`airflow dags test`) | 480 s, $0.081 (Stage 3) | **249 s, $0.0189** | **−48%, −77%** | all of the above; one run each |

¹ Both wall-time rows are the same kind of result. Each final set's range overlaps the baseline's, but pooled over
the six runs of the final BigQuery code (`s4-final-*` and `s4-end-*`, three each) the daily run's median is 95.7 s
(86.6–99.7), +12%, and the full rebuild's 97.2 s (85.7–109.8), +14%, with four and five of the six runs above the
baseline's slowest: probably somewhat slower, certainly not faster. The full rebuild's two new tables do not
explain it: in per-job medians the ten models add up to 50.4 s against the baseline's eight at 49.4 s
(`int_purchases` +2.6 s and `int_device_days` +5.8 s, but `int_identity` −3.7 s, `stg_events` −1.4 s,
`fct_sessions` −1.2 s), and E4's builds of the same 19 jobs (three tables unclustered) ran 82.6 s (80.6–87.6).
The rest is between jobs and in the jobs' spread, not in any one model; not isolated further.

² The same-day control (`s4-e6-control`, the baseline code rerun at 03:47 UTC): 360 s (358–361), 0.4157
DCU-hours, $0.0264 a batch. Against it the final batch is −60% wall and −80% DCU-hours, and the baseline month
below would be $2.37 instead of $2.44.

³ Measured before the review's fix to the daily window. With it (one run, `s4-fix-daily`, [below](#the-daily-window-after-review)),
a daily run bills 2.145 GiB, 1% more: the record of what was staged adds a 10 MiB MERGE, and the window's 10 MiB
partition query, left out of these records before, is now counted. −75% and $0.013 a run stand.

Two outputs differ from the baseline, both on purpose and both in the last bits of a floating-point sum (below,
[What differs from the baseline](#what-differs-from-the-baseline)). Everything else is identical row for row.

**Reverted or not kept:** removing `stg_events`' clustering (E4: purchase reads billed 16–18% more), removing
`fct_sessions`' clustering (E4: no difference), a slim copy of `stg_events`' columns for full builds (E3: it would
bill each reader the same bytes; the purchase-rows intermediate that E1 needs saves 53 MiB, 0.6%, of a full
build), Spark executors (E6: 3.2× the cost and not faster, and results that depended on network order), the
connector's direct write (E8: not run, it would run an uncapped MERGE), unpartitioned output tables (E8: the
mart loads about 0.9 s faster unpartitioned; kept partitioned for the layout), writing both attribution tables at
once (E8: within noise). **Left alone, with numbers:** the storage billing model and the time-travel window (E5:
the owner's call; recommendation below; the owner switched to physical billing on 2026-10-01).

---

## The baseline

The committed Stage 3 code (git `2896e02`), both sources, three builds and three batches
([`baseline.md`](bench/results/baseline.md)):

| | median (min–max) |
|---|---|
| `make build`: 8 models + 9 checks, 17 jobs | 85.3 s (81.3–91.4) wall; **8.624 GiB** billed (8.623–8.625), $0.0526; 1,706,455 slot-ms (1,623,238–1,735,924); 60.6 s of job time |
| of which `stg_events` | 3.340 GiB, 875,877 slot-ms, 15.0 s |
| of which the 9 checks | 1.50 GiB |
| attribution batch, runtime 3.0 | **390 s** (380–517): 52 s pending, 338 s running; **0.4527 DCU-hours** (0.4472–0.6195), $0.0287; the job's own compute 170.6 s, everything after it 123.2 s; local mode, one task thread |
| a daily run (build + batch) | $0.0814, **$2.44 a month** at list price (BigQuery 0.253 TiB a month) |

Bytes billed repeated to the MiB in all three builds except check 03 (98–100 MiB: it filters on a clustering
column, so what it can skip depends on each rebuild's block layout). Per-job slot-ms spread 5% to 104% over the
three runs, the build's total 7%, its wall time 12%. Every output table had one fingerprint across five readings
(what Stage 3 left, three builds or three batches, the copies), so the gate compares exactly: the copies of all
ten tables in `tagline_s4_baseline` are the reference for every later diff.

---

## BigQuery

### E2 — Narrow reads: kept

**Hypothesis.** Downstream models scan more of `stg_events` than they use.

**What was found.** `SELECT *` inside a CTE costs nothing: `fct_orders`' purchase read dry-runs at 448,874,491
bytes with `SELECT *` or with the column list, because BigQuery reads only the columns a query ends up using, and
every model used what it read. The waste was one column: four models read the stored `session_key` (a ~50-byte
string, 174 MiB of `stg_events`) to group or join on, where `ga_session_id` (8 bytes, 33 MiB) with
`user_pseudo_id` and `source` rebuilds it exactly.

**Change.** `stg_items`, `int_identity` (`COUNT(DISTINCT ga_session_id)`), `fct_sessions` and `fct_orders` build
`session_key` from its parts instead of reading it; a unit test keeps the four copies of the expression identical.

| model | baseline billed | E2 billed |
|---|--:|--:|
| `stg_items` | 1,396 MiB | 1,255 MiB |
| `int_identity` | 342 MiB | 200 MiB |
| `fct_sessions` | 1,163 MiB | 989 MiB |
| `fct_orders` | 563 MiB | 422 MiB |
| **the build** | **8.624 GiB** | **8.040 GiB** (8.038–8.040), −6.8% |

Slot-ms 1,847,992 (1,747,487–1,868,715) and wall 86.7 s (83.7–91.7): **within noise** of the baseline. The model
that did not change, `stg_events`, moved from 875,877 to 1,015,606 slot-ms between the two sets of runs, more
than the whole difference. Kept for the bytes; no claim on time.

### E2b — Exact money sums: kept (a correctness change)

E2's gate found one `mart_campaign_daily` row different from the baseline: revenue 278.96 against
278.96000000000004. E2's own three builds gave **two different fingerprints** for that table. A FLOAT64 `SUM`
depends on the order rows arrive in; E2's new `GROUP BY` changed that order, and an incremental build would
change it again. So `fct_sessions.revenue_usd` and `mart_campaign_daily.revenue_usd` / `cost_usd` are now summed
as NUMERIC and cast back to FLOAT64: an exact decimal sum, the same in any order. Against the baseline this changes
exactly 1 of 2,639 mart rows (to 278.96, the exact sum) and 0 of the 4,856 sessions with orders; bytes billed are
unchanged. Since then every build and every daily run has produced one fingerprint per table.

### E3 — A narrow shared intermediate: a slim copy not built; the purchase rows adopted for E1

**Hypothesis.** A slim table of the columns several models read from `stg_events` would be cheaper to scan than
`stg_events` itself. **Not built, by reasoning rather than measurement:** in a columnar store a slim copy of the
same columns and rows bills each reader the same bytes and adds its own build, so it cannot win. The only
intermediate with fewer *rows* is the 5,705 purchase events, `int_purchases`, which E1 needed anyway. **Measured
(real builds):** E2's `fct_orders` reading `stg_events` directly billed 422 MiB in all three builds; building
`int_purchases` and reading it from `fct_orders` billed 369 MiB (364–382) together in the six final full builds
(`int_purchases` 299, `fct_orders` 70). A saving of about 53 MiB, 0.6% of a full build, for one more job of about
2.6 s. Small but real; the table was kept for E1, where the daily path recomputes `fct_orders` from
`int_purchases` alone (70 MiB). (An earlier dry-run comparison put the two sides level, at 374 MiB each; it was not
logged, dry runs do not see cluster pruning, and the real runs above replace it. Without `stg_events`' clustering,
in E4, the pair billed 423 MiB and saved nothing.)

### E1 — Incremental daily processing: kept

**Hypothesis.** A daily run should process the new export day, not rebuild 4.3 M events.

**Change.** `make build-incremental` (and the DAG's default run) applies only the export days that are new or may
have changed, to every table, in **one BigQuery script in one transaction** (`pipeline/tagline_pipeline/incremental.py`,
using the model files' own SQL through hooks a full build leaves empty). The window, per source, starts at the
earliest of: any day not loaded yet; any loaded day whose export no longer matches what was recorded when the day
was staged (`staged_export_days`: the table read, its modification time and row count, taken before the read; GA4
reprocessed the day, a backfill, a daily table that replaced the streaming one the day was read from); and, for the
site, the 4 days up to its newest daily table (GA4 updates a daily table through the third day after its date). The
sample is static, so only missing days. (The runs measured here used the first version of the rule,
the site's newest 3 export days; a review found that too short for GA4's update period and blind to changes
outside it, and the rule above replaced it. With one site day in the export the window is that day under either
rule, so the measured runs stand; [below](#the-daily-window-after-review).) What each table does, and why
([data-model.md](docs/data-model.md#incremental-builds-stage-4) has the full rules):

| table | on the daily path |
|---|---|
| `stg_events`, `stg_items`, `int_purchases` (new), `int_device_days` (new) | the window's date partitions replaced |
| `int_identity` | recomputed whole from `int_device_days` (21 MiB): identity reaches back, so a sign-in today gives a device's earlier anonymous events to the person |
| `fct_sessions` | every session touched by old or new window rows recomputed from all its events; earlier sessions of a device whose person changed re-resolved (the rule is read out of the model file, so it is written once) |
| `fct_orders` | recomputed whole from `int_purchases`, `fct_sessions`, `int_identity` (70 MiB) |
| `fct_order_items` | the lines of changed orders, from those orders' date partitions |
| both marts | recomputed whole (a few MiB) |
| `stg_events`, `int_purchases` | earlier purchases whose `transaction_id` becomes (or stops being) a collision updated |
| `staged_export_days` (new) | the site window's days replaced by what the run read, in the same transaction |

Two versions were measured (`make build-incremental`, the site's day in the window, 3 runs each):

| version | billed | slot-ms | wall s |
|---|--:|--:|--:|
| v1: `int_identity` rebuilt from `stg_events` | 2.270 GiB (all 3 identical) | 507,345 (418,055–524,369) | 93.6 (86.3–101.8) |
| v2: adds `int_device_days` (device × day, 319,114 rows, 22 MiB) | **2.126 GiB** | 466,359 (436,360–489,550) | 94.7 (90.7–104.8) |
| v2 with a sample-sized day (`SINCE=20210131`, ~47 k events) | 2.139 GiB | 709,363 (702,813–762,329) | 102.8 (99.8–106.8) |

v2 saves 147 MiB a day and costs about +54 MiB on a full build (`int_device_days` 233 MiB, `int_identity` 21 MiB
instead of E2's 200); kept. **A day's volume barely moves the bill**: every
statement bills at least BigQuery's 10 MiB minimum per table it reads, so a 47,000-event day costs 13 MiB more
than a 1,433-row one. What a daily run bills (the final runs, below): a 627 MiB script of 27 statements (the
largest: the `int_identity` MERGE 70 MiB, the new-orders temp table 70, the `fct_order_items` insert 50, the
identity diff 46) and **1,547 MiB of checks, 71% of the run**.

**Correctness: identical to a full build on every step tested.** `make stage4-equivalence` builds in throwaway
datasets and diffs all ten Stage 2 tables exactly (EXCEPT DISTINCT both ways, row counts, multiset hashes) against a full build
of the same export tables, after the 9 checks pass on the incremental tables: (a) the sample built through
2021-01-30, then 2021-01-31 added (the spec's test); (b) the site's day then added with the default window;
(c) the Stage 2 fixture's site rows arriving day by day with a narrow lookback — a sign-up reaching back to a day-1
session outside the window, a streaming-only day, that day's daily table arriving with one event fewer and one
late event, another device reusing a `transaction_id` from outside the window. **Identical in all five steps**, and
each step's statements show the reach-back and collision updates ran where they had to and nowhere else. Run
again on the final code, after E4's layout change and the Spark experiments (`s4-end-equivalence`, 16 minutes,
27.1 GiB of builds and 15.1 GiB of diffs, $0.26): **identical in all five steps again**, the 9 checks passing on the
incremental tables at every step; the sample day's script billed 605 MiB and the site day's 617 MiB, the reach-back
update of `fct_sessions` ran on fixture day 2 and the collision updates of `stg_events` and `int_purchases` on days
3–4, and no conditional update ran anywhere else. A review then found four branches of the script those steps
could not fail on; the extended test, and a second fix to the window, are [below](#the-daily-window-after-review).

**Against the full build.** Bytes −73.6% against the final full build and −75.4% against the baseline; slot-ms
650,428 against the baseline's 1,706,455, −62% (the three sets of daily runs had medians from 466 k to 710 k,
−58% to −73%). **Wall time is not better**: 89.7 s (86.6–98.8) against 85.3 s (81.3–91.4), and 95.7 s
(86.6–99.7) over all six daily runs of the final BigQuery code, +12%, like the full rebuild's +14% (footnote 1 of
the headline table). The script's 27 statements run one after another at 1–4 s each, where a full build is 19 jobs
of similar length.

### The daily window, after review

A review of the finished stage found two silent holes in E1's first window rule (the site's newest 3 export days,
plus days not loaded):

- **Too short for GA4.** Google updates a daily table through the third day after its date (table 20220101 through
  20220104). The DAG reads the previous day's table once it lands (13:36 UTC for `events_20260927`; the property's
  time zone is about UTC−7), so the run on day R re-read R−3 to R−1 while R−4 could still change until the end of
  R−1 in the property's time: about 17 hours of late events never read. With streaming export on, today's streaming
  table counted as the newest export day and the gap grew by a day.
- **Blind outside the window.** A day GA4 reprocessed later, a backfill, a daily table arriving after its day had
  been read from the streaming table and the window had moved on, or updates while the DAG was not running: the
  incremental tables would drift from a full build with every check passing (check 05 counts rows for the sample
  only).

**Change** (`incremental.py`, `pipeline.py`, `bq.py`, and the DAG's `stg_events` task): every build that writes
`stg_events` now also writes `tagline_staging.staged_export_days`, one row per site export day: the export table it
read (the daily one, else the streaming one) and that table's last modification time and row count, from the export
dataset's `__TABLES__` (0 bytes billed), **taken before the read**. The daily script replaces its window's rows in
the same transaction as the tables. A run re-reads, with every day after it, any loaded day whose export no longer
matches its row: another table (the daily table has replaced the streaming one the day was read from), a later
modification, another row count, or no row at all. The site's lookback stays as a floor: 4 days, counted back from
its newest *daily* table. `--lookback 0` is now refused, by the CLI (which read 0 as unset and used the default) and
by the pipeline before it calls BigQuery. The window's two metadata queries are now in the run's cost table (the
10 MiB partition query was left out before). A first version of this fix compared each export table's modification
time with the time its `stg_events` partition was last written; that cannot see a change landing between a run's
read and its commit (the partition is written after the read, so it looks newer), and a record taken before the
read can. What the rule does not handle: a deleted or expired export day, which the run reports and does not act
on ([data-model.md](docs/data-model.md#when-to-run-a-full-build)). Unit tests: the default lookback against Google's
example, a streaming table for today, the export compared with the record (a daily table replacing the streaming
one, a later modification, another row count, no record), the record taken before `stg_events` and written after it
by a full build (and left alone by a partial build or a dry run), its MERGE inside the script's transaction, a
missing record, `--since` refusing to leave a changed day behind, `--lookback 0`, and the DAG's `stg_events` task.

**Measured on the real tables** (the final code): one full rebuild (`s4-fix-full`) and one daily run
(`s4-fix-daily`), each followed by fingerprints of **all ten Stage 2 tables**. The full rebuild: 21 jobs (the 19,
the export metadata query, 0 bytes, and the record, 0 bytes), 8.054 GiB, 9/9 checks. The daily run: the site's one
day (the lookback's; nothing had changed since the full build recorded it), 12 jobs, **2.145 GiB** against the
headline's 2.123 (+22 MiB: the record's MERGE, 10 MiB, BigQuery's minimum; the partition query, 10 MiB, now counted;
check 03, 98 MiB against 96), 28 statements in the script (637 MiB), 9/9 checks. **All ten tables' fingerprints equal
the full rebuild's**, `stg_events`' 4,297,017 rows included, and the five marts' equal `s4-end-full`'s. The
headline figures stand, within the +1% shown in footnote 3. (In between, the first version of this fix had one daily
run, `s4-review-daily`: 2.125 GiB, the same window, the five marts equal.) Then one DAG run of the daily path on this
code (`s4-fix-dag-daily`, [orchestration.md](docs/orchestration.md#after-the-review)): 274 s, 2.223 GiB and one batch,
$0.019, every check passing. The DAG's full-refresh path records what was staged through its `stg_events` task; that
is covered by the DagBag test (19/19), not by a live run after the fix.

**The equivalence test, extended.** The review also found that the fixture could not fail on four of the script's
branches: nothing vanished on re-delivery (so the old rows' half of the touched sessions was never needed), a
collision never went away, no device repeated a purchase across the window's edge, and the reach-back never had to
re-person an order; and that it could not fail on the window rule either, since every change fell inside the
lookback. The fixture now has seven incremental steps, **all with a lookback of 1**, so that only the rule under
test can bring an earlier day into the window. Each names the conditional updates that must run (and no others),
the day the site's window must start on, and facts on the incremental tables that show its path was taken, all
checked before the diff. On the final code (`s4-fix-equivalence`, the fixture scenario, 19 minutes,
9.0 GiB billed, $0.055): **every table identical to a full build in all seven steps, every expectation met**, the
9 checks passing on the incremental tables at each step. The sample and site scenarios, which the change to the
window does not reach (the sample is static; the site's one day is new to them), last ran on the first version of
the fix (`s4-review-equivalence`: both identical) and were not rerun, to stay inside the extra budget; the interrupted
fixture steps of that run are in the same file.

| fixture step (lookback 1) | site window | conditional updates | what it shows |
|---|---|---|---|
| day 2: A signs up; B sends its purchase twice; a streaming copy | day 2 | `fct_sessions` | A's day-1 session **and order** and its line, outside the window, become U's; the full build's record matched, so day 1 was not re-read |
| day 3: a streaming table only | days 2–3 | none | a streaming-only day; the lookback counts from day 2's daily table |
| days 3–4: day 3's daily table; C reuses B's `transaction_id`; new device E | days 3–4 | `stg_events`, `int_purchases` | day 3 re-read because it was staged from its streaming table; B's day-2 rows become a collision |
| days 4–5: day 4 re-delivered without C's purchase and without E; B repeats its purchase | days 4–5 | `stg_events`, `int_purchases` | day 4 re-read because its table changed; the collision goes away; E's session and E vanish; B's day-5 purchase is a repeat of one before the window |
| day 6: W signs in on A | day 6 | `fct_sessions` | A turns shared; its day-1 session and order go back to A's own person |
| day 1 restated | days 1–6 | none | the window reaches back five days to a changed day (the first rule would have read day 6 alone) |
| nothing new | day 6 | none | the step before recorded what it read: nothing is re-read but the lookback |

Still not covered: a `user_id` removed on re-delivery (a shared device going back to one user), a `transaction_id`
on three devices, a deleted export day (reported, not handled: run a full build), two sources on one date (none so
far), and a change landing during a run's read (the record errs toward re-reading the day; not tested).

### E4 — Partitioning and clustering: partitions kept, one clustering removed, two removals reverted

A Stage 2 reviewer had measured that clustering prunes nothing at this size (partitions of about 28 MiB, under
where clustering starts to help). Measured with and without, on the E1/E2 code:

- **Daily partitions: kept.** On an unpartitioned copy of `stg_events` clustered by `(source, event_name)`, the
  purchase read bills 12 MiB against 303 MiB partitioned (clustering works once blocks are big, 25× less). But
  the daily MERGE that replaces one day dry-runs at **2.58 GiB** there against **16 MiB** partitioned. The daily
  path needs partitions, and partitions cost the clustering its blocks.
The three clustering removals were measured together, in the same three builds (`s4-e4-nocluster`: no clustering
on `stg_events`, `stg_items` or `fct_sessions`), which broke the one-change-at-a-time rule: `stg_items` and
`fct_sessions` read `stg_events`, so their E4 figures mix two changes. Where later builds isolate one, it says so.

- **`stg_events` clustering removal: reverted.** Without it `int_purchases` billed 353 MiB against 299 (294–312)
  clustered, and check 03 113 MiB against 97 (96–101), the six final full builds' medians: reads filtered on
  purchases bill 16–18% more unclustered (clustering prunes 14–15% of them). These two read only `stg_events`, so
  the other two removals do not touch them. An earlier dry-run comparison hid this: dry runs do not account for
  cluster pruning. `stg_events`' own build took 932,257 slot-ms (916,720–990,175) without clustering, inside the
  clustered builds' 832,771–1,020,642 (baseline and E2): nothing saved.
- **`stg_items` clustering: removed, kept.** Nothing reads `stg_items` by its clustering columns. In E4 its build
  took 52,917 slot-ms (49,701–53,456) against 117,194 (95,476–167,677) in E2 and 97,886 (93,720–103,435) at
  baseline, for the same bytes, but with `stg_events` unclustered too. The six final full builds isolate it
  (`stg_events` clustered, `stg_items` not): 62,234 (49,215–83,391), still about half.
- **`fct_sessions` clustering removal: reverted.** 203,422 (186,664–233,978) without against 212,707
  (201,987–222,552) with in E2: noise either way, and never isolated (E4 changed its input's clustering too; the
  final builds, clustered, gave 226,517, 214,111–265,949). Left as it was.
- **Partition pruning on the daily path: confirmed per statement.** The export read is 2.39 MiB for the site's
  day; the `stg_events` MERGE processes 2.59 MiB (the site's day) or bills 33 MiB (a sample day, one partition);
  the `stg_items` MERGE 1.46 MiB.

`CREATE OR REPLACE` cannot change a table's clustering spec, so the first three runs without clustering failed at
once and billed nothing (they are in the log). The tables were dropped (derived, rebuilt within the minute) and the
runs repeated. Anyone changing the layout later has to drop the table first.

### E5 — Storage: measured, recommended, nothing switched

Storage is read from each table's metadata: `INFORMATION_SCHEMA.TABLE_STORAGE` is Access Denied for the project
owner (`roles/owner` lacks the project-level `bigquery.tables.list`; [bench/README.md](bench/README.md#why-storage-falls-back-to-table-metadata)).
Metadata has no fail-safe bytes, so physical-model costs are lower bounds; they are modelled for the steady state.

| | logical | current physical | time travel | compression |
|---|--:|--:|--:|--:|
| `tagline_staging` | 3.43 GiB | 0.18 GiB | 3.94 GiB | `stg_events` 22×, `stg_items` 16× |
| `tagline_marts` | 0.16 GiB | 0.03 GiB | 0.60 GiB | `fct_sessions` 6× |

The time travel there is about 21 full rebuilds in the last 7 days (the stage's own experiments), not one a day.
At the end of the stage (`s4-end-storage`) `tagline_staging` read 3.43 GiB logical, 0.16 GiB current physical and
1.45 GiB of time travel: lower, because the tables dropped and recreated for E4 took their time travel out of
what table metadata can see.

**What one run leaves behind**: a full rebuild supersedes about 211 MiB of physical data, a daily incremental run
about 11 MiB (8.6 MiB of it `int_identity`'s whole-table rewrite). With 7 days of time travel and 7 of fail-safe:

| storage billing model | daily full rebuilds | daily incremental runs |
|---|--:|--:|
| logical (today's; time travel and fail-safe free) | $0.072 a month | $0.072, falling to about $0.036 once history partitions go 90 days untouched (long-term price) |
| physical (billed on compressed bytes, time travel and fail-safe included) | about $0.12 a month | **about $0.014 a month** |

All of it is inside BigQuery's free 10 GiB of storage, so the project pays $0 today either way.
**Recommendation for the owner:**

- **Keep the logical model and the 7-day time-travel window for now.** Both are free under logical billing, time
  travel is how a bad incremental run is undone, and a model switch cannot be undone for 14 days.
- **Revisit physical billing if storage passes the free tier.** On the incremental path it is about 5× cheaper at
  list price (the data compresses 16–22×, and a daily run supersedes 11 MiB, not 211). Shortening the time-travel
  window only helps under physical billing, and it shortens how far back a bad run can be undone.
- **Staging tables cannot expire**: they are the incremental path's history. Scratch datasets already expire, and
  the Stage 4 ones are deleted ([below](#what-was-left-behind)).

No dataset's billing model, time-travel window or expiration was changed in Stage 4.

**Update, 2026-10-01:** the owner switched all four datasets (`tagline_raw`, `tagline_staging`, `tagline_marts` and
the GA4 export dataset) to physical billing with `ALTER SCHEMA … SET OPTIONS (storage_billing_model = 'PHYSICAL')`.
The time-travel window is still 7 days, and the model can't be switched back before 2026-10-15. A dataset created
fresh (say, `make build` in a new project) starts on logical billing.

---

## Spark

The batch before Stage 4: runtime 3.0, 390 s, 0.45 DCU-hours, one driver in Spark local mode with one task thread,
1000 shuffle partitions for 12 k touches. The full write-up of E6–E9 is in
[spark/README.md](spark/README.md#stage-4-local-mode-partitions-writes-size); every batch is in
[`s4-spark.md`](bench/results/s4-spark.md). Each Spark variant was compared with a control of its own session:
the baseline code rerun hours later took 360 s (358–361) against the baseline's 390 s (380–517), same output.

### E6 — Why runtime 3.0 runs in local mode: `local[4]` kept, executors reverted

Google's runtime 3.0, autoscaling and FAQ pages say nothing about it. Six diagnostic batches ($0.039) showed that
the image's spark-defaults set `spark.master=dataproc` (3.0's own cluster manager) and Dataproc then appends
`spark.master=local` from the batch properties; the later line wins, and `local` is **one** task thread on a
4-vCPU driver billed at 4.8 DCUs. It is not this job's settings (Google's defaults give the same), and the Batch
API refuses `spark.master` for every other value tried. A master set in the job's code does win, so the job now
takes `--spark-master`, and `batch.py` passes `local[N]` with N the driver's cores.

| variant (2 batches each) | wall s | DCU-hours | per batch | compute s |
|---|--:|--:|--:|--:|
| control: `local` (the runtime's) | 360 (358–361) | 0.4157 (0.4140–0.4174) | $0.0264 | 161.6 |
| **`local[4]`** | **241** (229–252) | **0.2528** (0.2412–0.2643) | **$0.0160** | 87.3 |
| `dataproc` (driver + 2 executors) | 287 (263–310) | 0.7944 (0.7400–0.8489) | $0.0504 | 83.8 |

**Kept `local[4]`**: 39% cheaper than the control, ranges not overlapping. **Reverted executors**: 3.2× the cost
of `local[4]` and slower, because the executors arrive a minute into the run and eight task slots finished no
faster than four threads. They also **changed results**: `mart_attribution_daily` differed from the baseline in
823 and 783 rows, in the last bits of DOUBLE sums added in network order. Local mode reproduced the baseline bit
for bit.

### E7 — Partitions: exact mart sums first, then 4 shuffle partitions, kept

Dataproc's spark-defaults set `spark.sql.shuffle.partitions=1000`, and AQE does not coalesce the output of a
cached plan, so every count and aggregation over the job's three cached DataFrames ran 1000 tasks.

- **Step 1, a correctness change: the mart sums in DECIMAL(38,18)** and casts back to DOUBLE, so a sum no longer
  depends on partitioning (E6 showed it could). Unit-tested (0.1 + 0.2 + 0.3 in three orders; random journeys
  split and shuffled three ways). Against the baseline `fct_attribution` is identical and 1,030 of 5,556 mart rows
  differ in the last bits (identical rounded to 9 digits). 2 batches: 286 s (258–315), $0.0191; the cost of the
  decimal sums is inside the ~30 s run-to-run noise at 1000 partitions.
- **Step 2:** `spark.sql.shuffle.partitions=4`: **152 s** (147–158), 0.1369 DCU-hours, **$0.0087**; compute 31.1 s
  against 109.5 s, the summary's counts 3.3 s against 42.6. Letting AQE coalesce cached plans instead gave the
  same within noise (152 s, $0.0086). **Kept the documented setting** (`shuffle.partitions=4`, one per thread) over
  an optimizer switch whose own description warns of an extra shuffle. All six step 1 and step 2 batches wrote
  identical tables: the partition count no longer changes a result.

### E8 — The write path: nothing changed

- **Direct write: not run.** Overwriting with the connector's direct method runs a MERGE query job the connector
  gives no bytes-billed cap, and it cannot create the partitioned table. That breaks two rules this project keeps.
- **One file per table: kept** (with 4 partitions it is at most 4 files anyway; the 987-file write of the first
  3.0 batch showed why it matters).
- **Partitioned output tables: kept.** A separate load test (3 loads each): `fct_attribution` 2.75 s partitioned
  against 2.91 s not (noise), the mart 2.47 against 1.61 s, about 0.9 s a batch. Not worth dropping the tables for.
- **Writing both tables at once: reverted.** 3 batches because the first two disagreed: writes 33.1, 22.4 and
  17.7 s against 24.0–28.8 s one after the other; batches $0.0079–$0.0099 against $0.0085–$0.0089. Within noise,
  at most ~$0.0004 a batch, for a second writer thread.

### E9 — Right-sizing: the minimum driver memory, kept

Cores: 4 is the smallest driver. Disk: 250 GiB is the minimum, already set. Executors and dynamic allocation
do nothing in local mode. **Memory**: the default driver (16000m + 6400m overhead) gets a 24 GiB node, 4.8 DCUs;
`spark.driver.memory=2867m` with 1229m of overhead is the smallest the API accepts (1 GiB per core in all), a
~5.75 GiB node at 3.0 DCUs. **152 s (150–155), 0.0868 DCU-hours, $0.0057**, against 152 s and $0.0087 with the
default memory: the same time, **37% fewer DCU-hours and 34% cheaper** (shuffle storage did not shrink). The job's phases took as long as with 24 GiB.

---

## The final state

Measured after every kept change was in, on 2026-09-29 (`s4-end-*` in `bench/results/`).

**Full rebuild** (`make build`, 3 runs, `s4-end-full`): 19 jobs, **8.052 GiB** billed (8.045–8.057), $0.0491;
1,793,407 slot-ms (1,767,071–1,985,295); **98.7 s** wall (85.7–109.8); 9/9 checks every time. Against the
baseline: −6.6% bytes; slot-ms +5%, with all six final full builds above the baseline's range, but +81 k of the +87 k
is the `stg_events` job, whose full-build query did not change (the same drift E2 saw), so no slot-ms claim either
way; and **not faster**: the range overlaps the baseline's, and the
six full builds of the final BigQuery code (these three and `s4-final-full`'s, before the Spark experiments: 8.050
GiB, 95.7 s, 91.7–101.8) have a median of 97.2 s, +14%, the same as the daily run's +12%. One run here lost 20 s to
a single `fct_orders` job that ran 24.5 s inside BigQuery without queueing (4.1–4.6 s in the others). The two new
tables' jobs (`int_purchases` 2.6 s, `int_device_days` 5.8 s) are about offset by faster `int_identity`,
`stg_events` and `fct_sessions` jobs (footnote 1 of the headline table); the difference is not in any one job.

**Daily incremental run** (`make build-incremental`, the default window, 3 runs, `s4-end-daily`): 10 jobs (the
script and 9 checks), **2.123 GiB** in every run, $0.0130; **650,428 slot-ms** (578,980–653,110); **89.7 s** wall
(86.6–98.8); 9/9 checks. The script: 627 MiB, 411,964 slot-ms (313,134–462,163), 59.1 s (56.4–69.7). The checks:
1,547 MiB. Every mart's fingerprint (the five Stage 2 tables in `tagline_marts`) equal to the full rebuild's in all
three runs; the staging tables were not fingerprinted in these runs. The run after the review's fix
(`s4-fix-daily`) fingerprinted all ten Stage 2 tables, staging included: every one equal to the full rebuild's
before it ([above](#the-daily-window-after-review)).

**Spark batch** (2 batches, `s4-end-spark`): **144 s** (134–154), 45 s pending, 98 s running (91–106);
**0.0818 DCU-hours** (0.0764–0.0872) at 3.0 DCUs, **$0.0054** ($0.0050–$0.0057); compute 29.3 s (27.0–31.6),
summary and writes 29.2 s (26.7–31.7); `local[4]`, parallelism 4; both load jobs 2.4–2.6 s, never queued more than
0.2 s. With E9's two batches, four batches of the final configuration span 134–155 s and $0.0050–$0.0058.

**The DAG, a normal daily run** (`make airflow-test AIRFLOW_DATE=2026-09-28`, run
`manual__2026-09-29T05:48:47`, `s4-end-dag-daily`): the sensor found `events_20260927` on its first poke,
`build_mode` took the incremental branch and skipped the 10 model tasks, and every other task succeeded, all 9
Stage 2 checks and all 3 attribution checks included. **249 s** from the first task's start to the last task's
end (`airflow dags test` runs one task at a time), against 480 s for the same DAG in Stage 3:

| part | tasks | wall | what it used | list price |
|---|---|---|---|---|
| branch, sensor, `prepare_sources`, `build_mode` | 5 (1 skipped) | 2.9 s | the sensor's poke 0.7 s; the window's metadata query (`INFORMATION_SCHEMA.PARTITIONS`, 10 MiB) | $0.0001 |
| `stage2_incremental` | 1 (10 model tasks skipped) | 66.2 s | the script: 27 statements, 627 MiB, 282,230 slot-ms, 56.5 s of job time | $0.0037 |
| Stage 2 checks | 9 | 19.5 s | 1,547 MiB | $0.0092 |
| `attribution_enabled`, `spark_attribution` | 2 | 154.4 s | batch 145 s (44 s pending, 101 s running), 0.0826 DCU-hours at 2.9 DCUs, `local[4]`; compute 29.7 s, summary and writes 30.1 s | $0.0054 |
| attribution checks | 3 | 5.9 s | 80 MiB | $0.0005 |
| `run_summary` | 1 | 0.1 s | | |
| **total** | **31 (11 skipped)** | **249 s** | **14 query jobs, 2.211 GiB billed; 0.0826 DCU-hours** | **$0.0189** |

Against Stage 3's DAG run: 249 s against 480 s (−48%), 2.21 GiB against 8.70 (−75%), 0.083 DCU-hours against
0.436 (−81%), $0.0189 against $0.081 (−77%). The Spark task is still 62% of the run's time. The CLI's cost table
(and so the `bench-build` records above) left out the 10 MiB window query; the DAG's record includes it, and since
the review's fix the CLI's does too.

**The DAG, a full-refresh run** (`AIRFLOW_CONF='{"full_refresh": true}'`, run `manual__2026-09-29T05:56:05`,
`s4-end-dag-full`): `build_mode` took the ten model tasks and skipped `stage2_incremental`; 29 tasks succeeded,
all checks passing. **278 s** and **$0.0564**: the models 63.9 s and 6.54 GiB, the checks 19.7 s and 1,549 MiB, the
Spark task 185.0 s (batch 183 s, 0.1036 DCU-hours, $0.0068: its compute phase took 53.6 s against 27–32 s in the
other five final batches, one slow batch), the attribution checks 6.1 s. 22 query jobs, 8.131 GiB. A full refresh
costs about three daily runs. Per-task tables for both runs: [docs/orchestration.md](docs/orchestration.md#measured).

## What it costs a month

A daily run, at list price, ×30:

| | per run | BigQuery | Spark | a month (×30) | BigQuery TiB a month |
|---|--:|--:|--:|--:|--:|
| baseline: full rebuild + Stage 3 batch | $0.0814 | $0.0526 | $0.0287 | **$2.44** | 0.253 |
| Stage 4: daily incremental run + final batch | **$0.0183** | $0.0130 | $0.0054 | **$0.55** | 0.062 |
| Stage 4 with a full refresh every day (for comparison) | $0.0545 | $0.0491 | $0.0054 | $1.64 | 0.236 |

Both sides also run the 3 attribution checks (80 MiB, $0.0005 a run). What the project **actually pays** is less:
the BigQuery part is inside BigQuery's free 1 TiB of queries a month either way, and storage inside the free
10 GiB, so the bill is the Spark part: **$0.86 a month before, $0.16 after**.

## What differs from the baseline

`make bench-diff TABLES=all` on the final tables (after the full-refresh DAG run) against the baseline copies:
**8 of 10 tables identical** row for row, `stg_events` (4,297,017 rows) and `fct_attribution` (73,962) included, and
exactly two documented differences, both identical when rounded to 9 digits (`--float-digits 9`):

1. **`mart_campaign_daily`: 1 of 2,639 rows**, the site's `retarget_q4` row: revenue 278.96 against the baseline's
   278.96000000000004. E2b's exact NUMERIC sum; the baseline's value is float noise from the order rows were added in.
2. **`mart_attribution_daily`: 1,030 of 5,556 rows**, each in the last bits (1.6666666666666663 against
   1.6666666666666665). E7's exact DECIMAL sums, the same change for the same reason. Identical rounded to 9 digits.

Both were made to make a result stop depending on the order of additions, which the baseline's numbers did: E2's
three builds gave two fingerprints for `mart_campaign_daily`, and Spark with executors gave two for the attribution
mart. The three attribution checks pass (revenue conserved to the cent), and `make spark-report`'s independent SQL
rebuild matches all 73,962 `fct_attribution` rows (largest weight difference 2.2e-16).

## What the measurements taught

- **A Spark run can be slow for reasons outside the job.** Baseline batch 1 took 517 s against 380 and 390: its
  two load jobs waited 71 s and 38 s in BigQuery's shared load pool before starting. The harness records each load
  job's queue time since, and a third run is taken when two disagree. A BigQuery job can also run long without
  queueing (a `fct_orders` job 24.5 s against ~4; a load job 22 s against 2–3).
- **Measure a control the same day.** The baseline code rerun hours later was 30 s faster than the baseline, with
  identical output. Spark variants were compared with their own session's control.
- **A result that depends on the order of a sum is a bug waiting for an optimisation.** Both documented
  differences are sums made exact after an otherwise correct change (a new `GROUP BY`; Spark executors) showed
  that the old sums depended on the order rows arrived in. The fingerprints caught both.
- **Dry runs do not see cluster pruning.** E4's first comparison, by dry run, showed clustering pruning nothing;
  real runs showed about 15% pruned on purchase reads.
- **At this size BigQuery's 10 MiB minimum decides the daily bill.** A daily run's 27 statements and 9 checks each
  bill at least 10 MiB per table they read, so a 47,000-event day costs almost what a 1,433-row day does, and the
  checks, which read whole tables, are 71% of it.
- **A passing equivalence test proves only the paths its data takes.** The first fixture passed while four of the
  script's branches were never needed, and while the window rule could miss GA4's late updates, because every
  step's changes fell inside the window. The extended fixture names, per step, the conditional statements that
  must run, the day its window must start on and a fact that shows the path was taken, and runs every step with a
  lookback of 1, so a window that reaches back can only come from the rule under test.
- **Same-sized differences get the same word.** The first write-up called a +5% daily wall time noise and a +16%
  full-rebuild wall time worse, from ranges that both overlapped the baseline's; pooled over six runs each, both
  are about +12–14%.
- **`CREATE OR REPLACE` refuses a new clustering spec**, and the storage metadata lags after a table is dropped and
  recreated (current physical bytes read 0.017, then 0.044, then 0.163 GiB), so per-run time-travel growth was
  modelled from table and partition sizes, not observed.
- **Cancelling a runtime 3.0 batch while it is PENDING did not stop it**: it ran and billed its 1-minute minimum.
- **The harness had a bug too.** Recording the first Airflow run by time window listed the incremental script's
  27 statements beside the script (they carry its labels), so the run read 2.823 GiB instead of 2.211. The window
  collection now nests them under the script, as the per-command collection always did; tested, record retaken.
  And the pipeline's own cost table left out the one 10 MiB metadata query that plans the daily window (the
  DAG's record had it); it is in since the review's fix.

## What it cost to find out

At list price: the baseline $0.31; the BigQuery experiments $1.08 (176 GiB, 939 top-level jobs); the Spark
experiments $0.35 (17 measured batches and 6 diagnostic ones, $0.34, plus $0.01 of queries); the final
measurements $0.58 (the three full rebuilds $0.15, the three daily runs $0.04, the two batches $0.01, both DAG runs
$0.08, the equivalence run $0.26, the final diffs and checks $0.05). **About $2.32 in all**, above the ~$1–2 the
stage was planned at, because the equivalence test ran twice (after E1 and again on the final code) and the final
full rebuilds were measured twice (before and after the Spark experiments). About $1.86 of it is BigQuery (roughly
0.30 TiB billed), which fits inside BigQuery's free 1 TiB of queries a month; the Spark batches, about $0.46 in all,
are what the project is billed for. The billing report is the authority on both.

**After the review**, with the owner's approval of about $0.30 more to finish: a first fix attempt was interrupted
part way through its equivalence run and billed 53.0 GiB ($0.32; its datasets were left behind and deleted in the
next round); the round that finished the fixes billed 28.7 GiB ($0.18: a full rebuild and a daily run with all ten
tables fingerprinted after each, the fixture equivalence run, one DAG run, metadata and spend queries) plus one
Spark batch ($0.006). That is **about $0.50 on top of the $2.32, about $2.82 for the stage** at list price, above
the extra $0.30 approved, because the interrupted attempt's $0.32 had not been counted when the extra was asked for.
All of it but the batch is BigQuery: the US region billed 0.58 TiB of queries in September (Pacific time), inside
the free 1 TiB, so what the project is billed for after the review is the one batch, about $0.006.

## What was left behind

- **Datasets:** `tagline_s4_baseline` was deleted after the final diff (its fingerprints stay in
  `bench/results/baseline.jsonl`); the equivalence command deletes its `tagline_s4_eq_*` datasets itself (and now
  also first, since the interrupted fix attempt left all five behind; its last run deleted them), and the probe
  datasets of the experiments were deleted by then. The project holds `tagline_raw`, `tagline_staging`,
  `tagline_marts` and the site's export, as before Stage 4; `tagline_staging` has one table more,
  `staged_export_days` (one row per site export day).
- **Dataproc:** no batch pending or running. Every Stage 4 batch had the 30-minute TTL (15 minutes for the diagnostic
  ones) and the `app`, `stage`, `job` labels plus `bench=stage4` and its variant.
- **Airflow:** stopped (`make airflow-down`), no container left; the DAG is still paused.
- **Cloud Storage:** the Spark bucket holds `code/` (the final version, `37f5b3bf52ba`, is what the DAG runs) and the
  staging prefix the TTL-stopped batch of Stage 3 left, whose cleanup is the owner's call
  ([docs/orchestration.md](docs/orchestration.md#limitations)); the experiments' scratch objects were deleted.
- **Time travel:** the stage's rebuilds left about 1.7 GiB of time travel in `tagline_staging` and `tagline_marts`;
  it is free under logical billing and ages out within 7 days.
- **Nothing else changed:** no IAM, network, firewall, bucket, billing-model, time-travel or export-dataset change.

## Not done, and why

- **Narrower checks on daily runs.** The 9 checks are 71% of a daily run's bytes. Row-local checks (08's email
  regex, 526 MiB; 07's `user_id` pattern; parts of 01) could look only at the rows the run wrote, about 0.6 GiB a day
  less, but that narrows the correctness gate on daily runs. The owner's call.
- **A faster daily script.** Folding its guards and temp tables into fewer statements might save 10–20 s of its
  ~60 s; not tried.
- **Serverless overhead** is now most of the batch: about 45 s pending (not billed) and about 40 s of the ~100 s
  billed before the Spark application starts and after it ends. No Spark setting reaches it.
- **`INFORMATION_SCHEMA.TABLE_STORAGE`** needs `roles/bigquery.metadataViewer` on the project for the owner (an IAM
  change, not made); until then physical storage costs are lower bounds and fail-safe bytes are invisible.
