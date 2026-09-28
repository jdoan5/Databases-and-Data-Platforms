# tagline/spark: multi-touch attribution (Stage 3)

Every real order in `tagline_marts.fct_orders` is attributed across the buyer's sessions of the 30 days
before it, up to the session it was placed in, under six models, in PySpark on Serverless for Apache
Spark (Dataproc Serverless; Google now calls it Managed Service for Apache Spark, serverless deployment,
and the pricing page is titled that way). The job reads `fct_orders` and `fct_sessions` and overwrites
`fct_attribution` (order x model x touch) and `mart_attribution_daily` (order date x model x channel)
in `tagline_marts`.

Stage 2 already gives each order one channel: the session it was placed in. Attributing an order across
a journey is a small program per order (collect the person's sessions, order them, weight each under
six rules that must each sum to 1). In Spark the rules are functions over DataFrames, unit tested on
hand-built journeys with a local SparkSession. It is not smaller than SQL: the same rules as one
BigQuery query (`sql/independent_rebuild.sql`, one join, a few window functions, an `UNNEST` of the
models) are about the same size, and `make spark-report` runs that query to check the job row by row.
At this volume BigQuery could do it all; the job is in Spark for the tests and for running Spark on
Dataproc under Airflow, not for the data size.

```
spark/
  main.py                    Dataproc main file: calls attribution.job.main()
  attribution/               shipped to Dataproc as attribution.zip
    journeys.py              which orders, which sessions, in which order (the rules below)
    models.py                the six models, the daily mart, the checks run before writing
    tables.py                output columns, descriptions, labels
    job.py                   the only BigQuery I/O: read, attribute, check, write, document
  tagline_spark/             runs where you submit from, never shipped
    batch.py                 THE batch definition, used by make and by the Airflow DAG
    submit.py                make spark-upload / spark-submit / spark-status / spark-cancel
    report.py                make spark-report
  sql/independent_rebuild.sql  the same rules in BigQuery SQL, compared with fct_attribution by make spark-report
  tests/                     pytest on a local SparkSession (Java 21)
```

## The rules

Settled in `attribution/journeys.py` and `models.py`, and pinned by the tests.

- **Orders**: Stage 2's real orders placed in a session: not `is_zero_value_without_id`, `session_key` not
  NULL. On the GA4 sample: 4,918 orders, $340,145.00 (all real orders have a session). An order in no
  session is in no Stage 2 channel mart either.
- **Touches**: the order person's sessions (same data source) with
  `ordered_at - 30 days <= session_start_at <= the order session's session_start_at`, both ends
  inclusive, 30 days = 30 x 24 h. The window reaches back from the purchase and ends at the **order
  session**: a session that started after the order session is not a touch, even one opened (in another
  tab, say) before the purchase while the order session was still going. The order session is always a
  touch, and always the last one.
- **Order and ties**: by session start; on a tie the order session goes last, the others by
  `session_key`. So every model gives the same answer whatever order rows arrive in.
- **Direct**: GA4's definition, source `(direct)` with medium `(none)` or `(not set)`. Stage 2's
  `(not set) / (not set)` means nothing was collected: unknown, not direct, so `last_non_direct` can
  credit it. On the sample it does for 859 orders, 666 of them ($52,406) with an earlier touch from a
  known non-direct source that would take the credit if unknown were skipped like Direct; 161 orders
  have only Direct touches and keep Direct.
- **lookback_complete**: FALSE when `ordered_at - 30 days` is before the data source's first session
  (the sample starts 2020-11-01 00:00:04 UTC). Such orders are still attributed; reports show both.

| model | weight of touch p of n |
|---|---|
| `last_click` | 1 on the last touch, which is the order session: Stage 2's channel |
| `last_non_direct` | 1 on the last touch that is not Direct; the last touch if all are Direct |
| `first_click` | 1 on the first touch in the window |
| `linear` | 1/n |
| `time_decay` | 2^(-days_before_order / 7), normalised: a 7-day half-life before the purchase |
| `position_based` | 40% first, 40% last, 20% shared by the middle; one touch 100%, two 50/50 |

**last_click and Stage 2.** Stage 2 credits the session the purchase was recorded in, and a journey
ends at that session, so `last_click` credits it too: its orders and revenue by source / medium /
campaign equal Stage 2's exactly. The Airflow check `03_last_click_matches_stage2.sql` requires both:
every order's one `last_click` credit is on its own `fct_orders.session_key`, and the channel totals
equal `fct_sessions`' to the cent, with no adjustment. (An earlier version ended the window at the
purchase instead. On the sample 15 orders ($978) had another session of the buyer that started after
the order session and before the purchase, 9 of them already over by then, and `last_click` credited
that session, so 11 of those orders changed channel. The spec asks for the order session, and that
rule is what runs now.)

## Runtime

**Serverless for Apache Spark runtime 3.0**: Java 21, Python 3.12, Scala 2.13, spark-bigquery connector
0.44.0, google-cloud-bigquery preinstalled. Google's page lists Spark 4.0.1 for the newest subminor
(3.0.14), but the batch on 2026-09-28 reported `spark.version` **4.0.2**, so pyspark is pinned to 4.0.2
(`pyproject.toml`; `tests/test_batch.py` ties the pin to `batch.SPARK_VERSION`). Chosen over 2.3 LTS
(Spark 3.5.3, Java 17, Python 3.11) because Spark 4.0 supports Java 21, the JDK installed here and the
runtime's own, so tests and batches run on the same JVM; Spark 3.5 supports Java 8/11/17 only. The
price: 3.0 is not LTS, end of support 2027-01-31 (2.3 LTS: 2027-11-26), and 3.0 has no staging bucket,
Lightning Engine or Native Query Execution.

## Run it

```bash
cd tagline
make spark-venv      # spark/.venv: pyspark 4.0.2, pytest, Google Cloud clients (Python 3.12+)
make spark-test      # 41 tests, 25 to 45 s, on a local SparkSession with Java 21
make spark-submit    # LIVE: upload the code, run one batch, wait; prints wall time, DCU usage, cost
make spark-status    # the latest batch (or BATCH=<id>), and any tagline batch still running
make spark-report    # channel credit by model (all, complete-lookback); last_click vs Stage 2; the SQL rebuild
make spark-cancel BATCH=<id>
```

CI: `.github/workflows/tagline-spark.yml` runs the tests on Ubuntu with Python 3.12 and Java 21 when
`tagline/spark/**` changes (read-only token, no Google Cloud access).

**One batch definition.** `tagline_spark/batch.py` builds the Batch resource (runtime, code URIs, job
arguments, service account, `default` subnet, 30-minute TTL, labels, Spark properties) and the batch id;
`make spark-submit` and the Airflow DAG (`airflow/dags/tagline_airflow/dataproc.py`) both call it, and
the DagBag test asserts the DAG's batch equals make's apart from the `orchestrator` label. Code goes to
`gs://<bucket>/code/attribution/<version>/` where the version is a hash of `main.py` and `attribution/`,
so a batch always runs exactly the code it names; the DAG hashes the same sources (mounted read-only),
so after changing the job run `make spark-upload` (or `spark-submit`) before a DAG run, or its batch
fails at once on the missing file. Batch ids: `tagline-attr-<date>-<run hash>-t<try>-<attempt hash>`,
where Airflow's attempt hash comes from the task instance's id (a new UUID7 on every try), so ids never
repeat, even after Airflow's metadata database is wiped.

**Cost guards**: 30-minute TTL on every batch (it fired on the first run, below); the smallest shape
(4-core driver, two 4-core executors, no scale-out, 250 GiB disks); labels `app=tagline, stage=3,
job=attribution, orchestrator, code`; Ctrl-C in `make spark-submit` cancels the batch. The job runs no
BigQuery query jobs (reads are Storage Read API sessions, writes are load jobs); `make spark-report`'s
three queries carry `maximum_bytes_billed`. The job checks its own output (every order has every model,
weights in [0, 1] summing to 1, orders and revenue conserved per model, last_click on the last touch
and that touch the order session) and writes nothing if any check fails.

## Verified on the GA4 sample

From `make spark-submit`'s batch (`tagline-attr-20260928-596c23f6-t1-2c6856`) and the full Airflow
run's batch (`tagline-attr-20260928-f2954fae-t1-b5bdad`), both on runtime 3.0 with code version
`230e52e7cb36`. Each wrote 73,890 `fct_attribution` rows (12,315 touches x 6 models) and 5,532
`mart_attribution_daily` rows; the DAG's three checks passed on the Airflow batch's output, and
`make spark-report` matched each batch's output against the independent rebuild. The two runtime 2.3
stand-in batches with the same code (history, below) wrote the same rows and passed the same checks:

| | |
|---|---|
| orders, touches | 4,918 orders, 12,315 touches: 2,145 one-touch, 977 two, 1,349 three to five, 447 six or more (max 12) |
| weights | sum to 1 per order and model (check 01: no rows) |
| conservation | every model: 4,918.0 orders, $340,145.00, in `fct_attribution` and in `mart_attribution_daily` (check 02); complete-lookback orders: 3,208, $215,866.00 under every model (`make spark-report`) |
| last_click vs Stage 2 | every order's credit on its own session, and orders and revenue by source / medium / campaign equal to `fct_sessions`' to the cent, with no adjustment (check 03) |
| independent rebuild | `sql/independent_rebuild.sql` recomputes the journeys and all six models in one BigQuery query from `fct_orders` / `fct_sessions`: all 73,890 rows match, positions identical, max weight difference 2.2e-16 (`make spark-report`) |
| incomplete lookback | 1,710 orders ($124,279.00); 48.4% of them have more than one touch, against 60.6% of complete-lookback orders |

Before the journey fix (window ending at the purchase) the same checks passed on 12,330 touches and
73,980 rows, with check 03 allowing the 15 re-credited orders. Rewritten, check 03 fails on those old
tables: 15 orders credited away from their own session, and 7 channels off in orders and in revenue.

Share of attributed revenue, complete-lookback orders (`make spark-report` prints all orders too):

| source / medium | last | last non-direct | first | linear | time decay | position |
|---|---|---|---|---|---|---|
| google / organic | 26.3% | 27.1% | 40.0% | 30.7% | 30.4% | 32.2% |
| &lt;Other&gt; / referral | 20.3% | 21.0% | 24.1% | 21.5% | 21.9% | 21.9% |
| (not set) / (not set) | 19.1% | 20.1% | 6.0% | 13.9% | 15.2% | 13.1% |
| shop.googlemerchandisestore.com / referral | 17.0% | 18.2% | 4.0% | 14.0% | 13.7% | 11.9% |
| (direct) / (none) | 6.7% | 2.5% | 10.3% | 7.3% | 6.9% | 8.0% |
| google / cpc | 1.0% | 1.0% | 2.4% | 1.5% | 1.4% | 1.6% |

Organic search starts journeys (40% of first-click revenue) that end on the store's own checkout host
and on sessions with no collected source; the checkout-host referral (`shop.googlemerchandisestore.com`)
is mostly a last step. On the sample a person is one device, so all of this is within one browser.

## Measured

Every batch of 2026-09-28, runtime 3.0 (the committed runtime) first:

| batch | result | wall (pending / running) | DCU-hours | shuffle GB-hours | list price |
|---|---|---|---|---|---|
| `...e580908d-t1-8f7f6a` (04:27 UTC, code `61bf231e63fc`) | CANCELLED by the 30-min TTL after writing `fct_attribution`, before the mart | 1,918 s (111 / 1,807) | 2.4132 | 150.82 | $0.153 |
| `...3ef1592e-t1-9be5af`, `...c79bab32-t1-e1162f`, `...ca8d31f6-t1-93fc23`, `...ffcdfa20-t1-4e81f3`, `...498a7a1d-t1-b75e5c` (04:59 to 13:40 UTC) | FAILED at creation: Cloud Resource Manager API not enabled (below) | 4.5 to 9.8 s | 0 | 0 | $0 |
| `...596c23f6-t1-2c6856` (14:03 UTC) | SUCCEEDED: `make spark-submit` after the API was enabled; compute 159.1 s, write 105.2 s; loads 2.7 s, 2.7 s | 368 s (60 / 308) | 0.4093 | 25.58 | $0.026 |
| `...f2954fae-t1-b5bdad` (14:19 UTC) | SUCCEEDED: the full Airflow run (`docs/orchestration.md`); compute 166.9 s, write 116.5 s; loads 2.8 s, 2.7 s | 389 s (60 / 329) | 0.4409 | 27.56 | $0.028 |

History: runtime 2.3.39, first a diagnostic batch, then a stand-in while 3.0 batches could not be created:

| batch | result | wall (pending / running) | DCU-hours | shuffle GB-hours | list price |
|---|---|---|---|---|---|
| `...e55b0ae7-t1-bbd965` | diagnostic, cancelled on reaching RUNNING | 87 s | 0.1975 | 12.5 | $0.0125 |
| `...ca4371b6-t1-98ee8f` | SUCCEEDED: the first full Airflow run, before the journey fix; compute 86.6 s, write 61.3 s; loads 2.2 s, 2.4 s | 277 s (96 / 182) | 0.5958 | 37.71 | $0.038 |
| `...ca4371b6-t1-4f152d` | SUCCEEDED: that run's Spark task cleared and run again; tables overwritten, rows unchanged; compute 70.5 s, write 65.3 s; loads 11.4 s, 2.2 s | 217 s (62 / 154) | 0.5069 | 32.08 | $0.032 |
| `...ed18fc3b-t1-2ec747` | SUCCEEDED: `make spark-submit` after the journey fix; compute 79.3 s, write 59.3 s; loads 2.4 s, 2.8 s | 218 s (61 / 158) | 0.5135 | 32.50 | $0.033 |
| `...8ef9aa53-t1-fc0bd9` | SUCCEEDED: the full Airflow run after the fix; compute 76.3 s, write 58.3 s; loads 2.4 s, 2.3 s | 225 s (70 / 154) | 0.5069 | 32.08 | $0.032 |

The four stand-in runs used a scratch copy of `tagline_spark/batch.py` (runtime `2.3`, `SPARK_VERSION`
`3.5.3`, one extra label `purpose=standin-runtime-2-3`) with the same `main.py` and `attribution/`
(code versions `5dd7dabf4ddf` before the journey fix, `230e52e7cb36` after); nothing in the repo
selects 2.3. The diagnostic batch ran code `606aa90c3a86` with the label `purpose=diagnostic` and
wrote nothing: it was cancelled 12 s after reaching RUNNING. On 2.3 the job ran on executors
(application id `app-...`), not in local mode, and with one file per table each load job took 2 to
3 s, once 11.4 s.

Prices: standard tier, us-central1, $0.06 per DCU-hour and $0.000054795 per GB-hour of shuffle storage,
per second with a 1-minute minimum ([pricing](https://cloud.google.com/dataproc-serverless/pricing),
now titled "Managed Service for Apache Spark (formerly Dataproc) pricing").

**Runtime 3.0, completed.** Both batches that completed ran the committed code (`230e52e7cb36`) with
the one-file write: each load job took under 3 s, and the connector deleted its staged files after the
loads. Like the first 3.0 batch (below), both ran as a single driver in Spark local mode (application
id `local-...`, which also names the connector's staging paths; every log entry comes from the driver
node, while a 2.3 batch also logged from two workers), averaging 4.8 DCUs while running, against
about 12 on 2.3. So on 3.0 the job itself is slower (compute 159 to 167 s and write 105 to 117 s,
against 76 to 79 s and 58 to 59 s on 2.3) and the batch is longer (368 to 389 s against 218 to 225 s),
but it uses fewer DCU-hours (0.41 to 0.44 against 0.51) and costs a little less ($0.026 to $0.028
against $0.032 to $0.033). About a quarter of a 3.0 batch is Serverless around the job: 60 s pending,
about 20 s from RUNNING to the Spark application's start, and about 20 s after the job's last line.
Why Dataproc runs this batch in local mode on 3.0 is not known; that is for Stage 4 to measure.

**The first 3.0 batch** (`...e580908d-t1-8f7f6a`, the TTL stop) ran an earlier code version,
`61bf231e63fc`, from before the one-file write and before the journey fix. Its computation took about
4 minutes after the driver started (reads, journeys, models, checks). Writing did not finish: the
cached DataFrame had 987 partitions, the connector staged 987 Parquet files (10.6 MB) for
`fct_attribution` and committed them to Cloud Storage one at a time for 15 minutes, and the mart's
staging ran into the TTL. The job now coalesces each table to one file before the write
(`job.WRITE_PARTITIONS`). It too ran as a single driver in local mode (no executor containers),
averaging 4.8 DCUs. The TTL did its job. The staged files of that interrupted write are still in the
bucket (`docs/orchestration.md`, Limitations).

**Runtime 3.0 needs the Cloud Resource Manager API.** When it creates a runtime 3.0 batch, Dataproc
looks up a secure tag key through that API (TagKeys.GetNamespacedTagKey). With the API disabled,
every 3.0 batch after the first failed at creation with `Failed to get secure tag key ... Cloud
Resource Manager API has not been used in project ... or it is disabled` (PERMISSION_DENIED),
including one with the first batch's exact configuration, a retry 17 minutes later (05:20 UTC)
and `make spark-submit` at 13:40 UTC, while runtime 2.3 batches were created and ran (why the first
3.0 batch, at 04:27 UTC, was created without the API is not known). The API was
enabled on 2026-09-28 at 14:02 UTC (`gcloud services enable cloudresourcemanager.googleapis.com
--project <project>`, no charge), and both 3.0 batches since were created and succeeded. A new
project needs the same step before its first batch.

## Limitations

- **One device per person on the sample**: first click, linear and the rest only move credit between
  channels seen on the device that bought. Cross-device journeys need the site's stitched export.
- **Orders without a session are not attributed** (none on the sample). A signed-in purchase with no
  session could be attributed to the person's earlier sessions; Stage 2's channel marts leave it out,
  and so does this.
- **The mart keeps zero rows**: every touch appears under every model, so a channel that touched an
  order but got no credit under a model has a row with 0 attributed orders (5,532 rows, 4,982 non-zero).
- **The two tables are written one after the other** (two `WRITE_TRUNCATE` load jobs, about 20 s
  apart), not swapped in together: a batch that stops between them leaves a new `fct_attribution`
  beside an old mart (it happened once, to the TTL-stopped batch). The next good run repairs it.
- **Runtime drift**: Dataproc takes only `3.0`; a new subminor can change the Spark patch version. The
  job logs a warning and records both versions in its `ATTRIBUTION_SUMMARY` line (in Cloud Logging:
  runtime 3.0 has no staging bucket for driver output).
