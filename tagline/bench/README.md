# tagline/bench: the Stage 4 measurement harness

One way to measure every Stage 4 change, so a before and an after are always read the same way. It runs a
named **variant** (a build command, or the Spark batch with property overrides), reads what Google Cloud
recorded for it, and appends one JSON record per run to `bench/results/<variant>.jsonl`. `make bench-report`
turns the log into medians and ranges. Nothing here changes the pipeline; it only runs it and reads.

```bash
cd tagline
make bench-test                                        # the harness's own tests (no Google Cloud)
make bench-build VARIANT=baseline RUNS=3 FINGERPRINT=stage2-marts   # LIVE: make build x3
make bench-spark VARIANT=baseline RUNS=2 FINGERPRINT=stage3         # LIVE: the attribution batch x2
make bench-storage VARIANT=baseline                    # storage per table / dataset, monthly cost both models
make bench-snapshot VARIANT=baseline                   # copy the built tables into tagline_s4_baseline
make bench-diff VARIANT=s4-e2-narrow                   # the correctness gate against that copy
make bench-report                                      # Markdown: every variant, medians (min–max)
make stage4-equivalence                                # LIVE: incremental vs full build, throwaway datasets (~$0.30)
make attribution-checks                                # the DAG's 3 attribution checks, without Airflow
```

Table sets for `FINGERPRINT=` / `TABLES=`: `stage2-marts` (the five Stage 2 marts), `stage2` (all ten tables a
Stage 2 build writes, staging included: about 3.5 GiB to scan, mostly `stg_events`), `stage3` (the two
attribution tables), `gate` (every table in `tagline_marts`), `all` (`stage2` and `stage3`), or a comma list.

A variant that is not `make build` passes its own command: `make bench-build VARIANT=s4-e1-daily-v2
BUILD_CMD="make build-incremental"` (or `"make build-incremental SINCE=20210131"` for a sample-sized day).
The incremental build is one BigQuery script: the harness records the script job and, nested under it as
`statements`, each statement BigQuery ran for it (its child jobs: `statement_type`, destination table, bytes,
slot-ms, seconds), so a script's cost can be read statement by statement without being counted twice. Spark overrides: `make bench-spark VARIANT=s4-e7-shuffle8
SPARK_PROPS="spark.sql.shuffle.partitions=8"` (several, space-separated; `BENCH_ARGS="--unset-prop
spark.executor.instances"` removes one of `batch.py`'s). Job arguments: `JOB_ARGS="spark-master=local[4]"` sets
`--spark-master=local[4]` in the batch's arguments (an empty value removes one). Runtime 3.0's Batch API refuses
`spark.master` as a property unless it is `local`, so the job takes its master as an argument (Stage 4,
experiment 6, in `spark/README.md`). Name variants `s4-e<experiment>-<what>`, and put
what changed in `NOTE="..."` (no apostrophes: Make passes it through the shell in single quotes). An Airflow
run is recorded afterwards, its BigQuery jobs from its time window and its batch by id (the same record `bench-spark`
writes for a batch it started):

```bash
cd bench
PYTHONPATH=../spark:../pipeline:. ../spark/.venv/bin/python -m tagline_bench jobs --variant s4-end-dag-daily \
  --since <start> --until <end> --label orchestrator=airflow --wall <seconds>
PYTHONPATH=../spark:../pipeline:. ../spark/.venv/bin/python -m tagline_bench batch --variant s4-end-dag-daily \
  --batch-id tagline-attr-... --fingerprint stage3
```

## What each command records

| record (`type`) | from | fields that matter |
|---|---|---|
| `bq_run` (`bench-build`) | the command's own `COSTS_JSON` (every job id it ran, which the harness sets in its environment), then `region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT` for those ids | per job: `bytes_processed`, `bytes_billed`, `slot_ms`, `elapsed_seconds` (start to end), `queued_seconds`, `cache_hit`, `statement_type`, labels; per run: the command's `wall_seconds`, totals, the checks' PASS/FAIL lines, exit code, git commit and whether `tagline/` had uncommitted changes. A command that writes no `COSTS_JSON` is collected by time window and labels `app=tagline, stage=2` instead (`collection` says which) |
| `bq_window` (`jobs`) | JOBS_BY_PROJECT for a time window and labels | the same job fields, for an Airflow run; a script's statements carry its labels too, so they are nested under it as `statements` and counted once |
| `spark_run` (`bench-spark`, or `batch` for a batch the harness did not start) | the batch (`tagline_spark.batch.batch()` plus the overrides), Cloud Logging, JOBS_BY_PROJECT | `wall/pending/running_seconds` (state history), `dcu_hours`, `avg_dcu_while_running`, `shuffle_gib_hours`, `usd`; `compute_seconds`, `write_seconds` (everything after the checks: the summary's counts and the writes), `summary_seconds` and `write_only_seconds` from the job's own `ATTRIBUTION_SUMMARY` line; `spark_master`, `default_parallelism` and `app_id` from that line too (older code: `app_id` from the connector's staging path), and `mode` (`local` for `local-…`, `executors` for `app-…` under runtime 2.x's standalone master or `batch-<uuid>` under runtime 3.0's `dataproc` master); `job_args`; `worker_logged` (did any worker node log); `usage_samples` (runtimeInfo.currentUsage every poll: the DCUs actually allocated); the two load jobs' `queued_seconds` and `elapsed_seconds` |
| `storage` (`bench-storage`) | `INFORMATION_SCHEMA.TABLE_STORAGE`, or each table's metadata when the view is denied (below); dataset options (billing model, time-travel window) | per table and dataset: logical (active, long-term), physical (active, long-term, time travel, current), rows, partitions; monthly list price under the logical and the physical model |
| `fingerprint` (`bench-fingerprint`, or `FINGERPRINT=<set>` after a build or batch) | one query per table | row count and two order-independent hashes (BIT_XOR and SUM of `FARM_FINGERPRINT(TO_JSON_STRING(row))`, columns sorted by name) |
| `snapshot` (`bench-snapshot`) | copy jobs (free) | the copies in `tagline_s4_baseline`, their fingerprints |
| `diff` (`bench-diff`) | one query per table | rows only in the current table and only in the baseline (EXCEPT DISTINCT both ways on the row's JSON over the common columns), both row counts, both multiset hashes, added / removed / retyped columns, up to 3 example rows per side; `identical` only when all of that matches |
| `prices` | `tagline_bench/prices.py` | the list prices and the day they were read |
| `equivalence` (`make stage4-equivalence`) | throwaway datasets `tagline_s4_eq_*` | per scenario step: the window, the incremental script's statements, and an exact diff of all ten Stage 2 tables against a full build of the same export tables; for a fixture step also what it expected (the conditional updates that must run, where the site's window starts, facts on the incremental tables), `expectations_met` and any `problems` |

Every harness query has `maximum_bytes_billed` (`TAGLINE_MAX_BYTES_BILLED`, 10 GB by default), no query
cache, and the labels `app=tagline, stage=4, kind=bench`, so window collection never counts the harness's own
queries. Each command prints what its own queries billed (a JOBS lookup is 10 MiB, BigQuery's minimum; a
fingerprint of the gate tables about 210 MiB). Every Spark batch keeps `batch.py`'s 30-minute TTL and labels,
plus `bench=stage4` and `variant=<name>`; the harness refuses a body without a TTL, prints the batch's state
and running DCUs every 30 s, and cancels the batch if it is interrupted. Before anything is written, every
record goes through a scrubber that replaces the project id, bucket, service account and GA4 dataset name,
so the results log can be committed; raw command output goes to `bench/logs/` (gitignored: it names the
project).

## Method (the spec's rules, as the harness supports them)

- **Repeat and report the median.** Bytes billed are deterministic; slot-ms and seconds are not. BigQuery
  variants at least 3 runs, Spark variants at least 2. The report prints median (min–max) and, per job,
  the slot-ms spread ((max − min) / median); call a difference smaller than the spread noise.
- **Query cache off** everywhere (the pipeline already sets it; so does the harness).
- **Correctness gate.** After a change: `make build` (its 9 checks), the batch (its own checks) and the 3
  attribution checks (`make attribution-checks`, the DAG's SQL without Airflow), then `make bench-diff VARIANT=<v>`: all 7
  tables in `tagline_marts` compared with the baseline copies (`TABLES=all` adds the three staging
  tables; `stg_events` is about 2.6 GiB to scan per side). Exit 1 on any difference. Floating-point sums can
  differ in the last bits between two runs of the same code; `BENCH_ARGS="--float-digits 9"` rounds top-level
  FLOAT64 columns before comparing, and a difference that only disappears that way must be written down
  as such. The baseline log records whether repeated baseline builds and batches produced identical
  fingerprints, so it is known which tables are exactly reproducible.
- **Money** is list price before free tiers (`tagline_bench/prices.py`, read 2026-09-28): BigQuery on demand
  $6.25/TiB; storage $0.02 / $0.01 per GiB-month logical (active / long-term), $0.04 / $0.02 physical;
  Serverless for Apache Spark standard tier $0.06/DCU-hour, shuffle storage $0.000054795/GiB-hour. A daily
  run's monthly cost is the per-run median x 30.

## What the baseline taught about the measurements

From `results/baseline.jsonl` (summary: [`results/baseline.md`](results/baseline.md)):

- **The outputs are exactly reproducible.** Every table in `tagline_marts` had one fingerprint across the
  state Stage 3 left, three baseline builds, three baseline batches and the copies: the same rows to the
  last bit, floats included. So the gate compares exactly; `--float-digits` is not needed for the baseline
  code, and a variant that needs it has changed a result.
- **The gate catches what it should.** A live check against the copy of `fct_orders`: 1% of rows with
  revenue + 0.01 (57 rows each way), one duplicated row (0 rows each way under EXCEPT DISTINCT, but the row
  count and hash differ) and revenue + 1e-12 on every row were all DIFFERENT; only the last became
  IDENTICAL with `--float-digits 9`.
- **Bytes billed repeat; time does not.** 8.623 to 8.625 GiB over three builds: every job billed the same
  bytes except check 03 (98 to 100 MiB), which filters `stg_events` on `event_name`, a clustering column, so
  the blocks it can skip depend on how each rebuild laid the table out. Slot-ms per job spread 5% to 104%
  over three runs; the build's total slot-ms 7%, its wall time 12%.
- **A Spark run can be slow for reasons outside the job.** Baseline run 1 took 517 s against 380 s and
  390 s; its extra ~110 s of write time was the connector's two load jobs waiting 71 s and 38 s in
  BigQuery's shared (free) load pool before starting (JOBS_BY_PROJECT `creation_time` to `start_time`;
  0.2 to 1 s in the other runs). The harness now records each load job's `queued_seconds`; take a third run
  when two disagree, and quote the median.

## What the Spark experiments taught about the measurements

From `results/s4-e6-*.jsonl` to `s4-e9-*.jsonl` (summary: [`results/s4-spark.md`](results/s4-spark.md); the
write-up is in `spark/README.md`, Stage 4):

- **Measure a control on the same day.** The baseline code, rerun as `s4-e6-control` a few hours after the
  baseline, took 358 to 361 s against the baseline's 380 to 517 s, with identical output. A variant is compared
  with the control of its own session, and the baseline is quoted beside it.
- **`write_seconds` is not the write.** Since Stage 3 the job's `write_seconds` has covered everything after
  its checks, including the summary's counts, which on 1000 cached partitions took longer than the two writes.
  The job now reports `summary_seconds` and `write_only_seconds` as well.
- **`worker_logged` means nothing on runtime 3.0.** Executor nodes there are named
  `gdpic-rm-<uuid>-<suffix>`, not `-w-0`; `mode` comes from the application id and, from Stage 4's code on,
  from the master the job reports itself.
- **Local mode reproduces results; executors did not.** With one thread or four, every batch's two tables had
  the baseline's fingerprints. With executors, `mart_attribution_daily` changed in the last bits of 823 and 783
  rows in two runs (DOUBLE sums added in network order); the gate caught it, `--float-digits 9` did not.
- **The job's own timings are noisy too.** The same code at 1000 partitions computed in 81.5 to 118.5 s over four
  batches (4 threads); at 4 partitions, 30.5 to 38.6 s over nine. And a load job can run long inside BigQuery without
  queueing: 22.1 s once (and 15.0 s once in the load probe) against 2 to 3.6 s otherwise. Quote medians over at
  least two batches, add a third when two disagree, and look at `load_jobs` before crediting or blaming a variant.
- **Cancelling a runtime 3.0 batch while it is PENDING did not stop it.** A property-validation batch was
  cancelled 1 s after creation; it ran anyway and billed its 1-minute minimum ($0.003). A batch the API accepts
  should be assumed to run.

## The baseline dataset

`tagline_s4_baseline` (US) held copies of the ten tables the baseline code built (the three staging tables,
the five Stage 2 marts, the two attribution tables), taken by `make bench-snapshot VARIANT=baseline` after
the baseline builds and batches, each set to expire after 14 days as a backstop. After the final diff of
2026-09-29 (`s4-end-gate`: 8 of 10 tables identical, the two documented last-bit sums in the other two) **it was
deleted** (`bq rm -r -f -d <project>:tagline_s4_baseline`); its fingerprints stay in `results/baseline.jsonl`.
A later comparison needs a new baseline first: `make bench-snapshot VARIANT=<name>` copies the current tables
(copies are free; the storage is a few cents a month while it exists).

## The final runs

`results/s4-final.md` (from `make bench-report VARIANT="s4-end-full s4-end-daily s4-end-spark s4-end-dag-daily
s4-end-dag-full s4-end-gate s4-end-storage"`): the full rebuild x3, the daily incremental run x3, the batch x2,
both Airflow runs, the final diffs and the last storage reading, all with every Stage 4 change in. The write-up
is [STAGE4-RESULTS.md](../STAGE4-RESULTS.md). Recording the first Airflow run showed a harness bug: the
time-window collection listed the incremental script's 27 statements beside the script, so the daily run read
2.823 GiB instead of 2.211; `jobs` now nests them as `bench-build` always did (tested), and the record was
taken again.

## Why storage falls back to table metadata

`region-us.INFORMATION_SCHEMA.TABLE_STORAGE` needs `bigquery.tables.get` and `bigquery.tables.list` at the
project level. `roles/owner` does not include them (checked with `gcloud iam roles describe roles/owner`):
BigQuery gives basic roles access to data through each dataset's ACL instead, and region-level views ask for
the project-level permissions. So for this project's owner the view (and the region-level `TABLES`,
`COLUMNS`, `PARTITIONS` views) is Access Denied, while dataset-level views work. Stage 4 does not change IAM.
The harness therefore reads the same counters from each table's metadata (`tables.get`, free):
`numActiveLogicalBytes`, `numLongTermLogicalBytes`, `numActivePhysicalBytes`, `numLongTermPhysicalBytes`,
`numTimeTravelPhysicalBytes`, `numCurrentPhysicalBytes`, rows, partitions. Two things only the view has:
fail-safe bytes, and the storage still held by dropped tables; the physical-model cost from metadata is a
lower bound, and the record says so (`fail_safe_included: false`). Granting the owner
`roles/bigquery.metadataViewer` on the project would let the harness use the view; that is the owner's call.

## Files

```
bench/
  tagline_bench/cli.py        the commands above
  tagline_bench/bigquery.py   JOBS / storage / fingerprint / diff SQL (pure builders) and the BigQuery calls
  tagline_bench/spark.py      the batch with overrides, polling, Cloud Logging facts
  tagline_bench/prices.py     list prices, the date and the pages they come from
  tagline_bench/results.py    the JSONL log, the scrubber, median / range
  tagline_bench/report.py     Markdown from the log
  tagline_bench/equivalence.py  make stage4-equivalence: the daily incremental build against a full build
  tests/                      pytest (make bench-test)
  results/                    the committed results log, one file per variant
```
