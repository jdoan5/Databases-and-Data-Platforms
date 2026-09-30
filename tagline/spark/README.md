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
  NULL. On the GA4 sample: 4,918 orders, $340,145.00 (all real orders have a session). On the site's
  export: 8 of its 13 orders, $461.95; the other 5 ($378.98) were sent with consent denied and have no
  session. An order in no session is in no Stage 2 channel mart either.
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

**Local mode.** Runtime 3.0 starts this batch in Spark local mode with one task thread: Dataproc adds
`spark.master=local` to the batch's Spark config, and the Batch API refused every other `spark.master`
property tried. The job therefore takes its master as an argument, and `batch.py` passes
`--spark-master=local[4]`, the driver's four cores ([Stage 4](#stage-4-local-mode-partitions-writes-size)
has how this was found and what executors would cost).

## Run it

```bash
cd tagline
make spark-venv      # spark/.venv: pyspark 4.0.2, pytest, Google Cloud clients (Python 3.12+)
make spark-test      # 46 tests, 25 to 45 s, on a local SparkSession with Java 21
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
(4-core driver with the minimum 4 GiB of memory, 250 GiB disks; since Stage 4 the job runs in the
driver alone with 4 task threads and 4 shuffle partitions, [below](#stage-4-local-mode-partitions-writes-size),
so the two 4-core executors `batch.py` still describes are never started); labels `app=tagline, stage=3,
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

## Verified on the site's export

From `make spark-submit`'s batch (`tagline-attr-20260928-bef2f7d9-t1-c95cc0`, 14:53 UTC) after the Stage 2
build with the site's export, and `make spark-report` after it; the DAG run that followed wrote the same
rows. The job wrote 73,962 `fct_attribution` rows and 5,556 `mart_attribution_daily` rows (the sample's
73,890 and 5,532 plus the site's 72 and 24), with 0 problems in its own checks, and the independent
rebuild matched all 73,962 rows (max weight difference 2.2e-16).

| | site (`source = 'tagline_site'`) |
|---|---|
| orders, touches | 8 orders, $461.95, 12 touches: 4 one-touch, 4 two-touch; one order's only touch is Direct |
| conservation | every model: 8.0 orders, $461.95 |
| cross-device | all 4 two-touch journeys cross devices (the buyer signed in on both) |
| lookback | all 8 incomplete: the site's data starts at its first session, 2026-09-28 01:54 UTC |
| not attributed | the 5 consent-denied orders, in no session ($378.98) |

The clearest journey: p031 arrived from organic search on one device and came back direct on another
about 10 s later to buy ($78.00). `last_click` gives the order to Direct (the order session), `last_non_direct`
and `first_click` to organic search on the first device; `linear`, `time_decay` (0.499997 / 0.500003:
the touches are seconds apart) and `position_based` split it in half. p003 opened `newsletter_oct` on one
device and bought from `retarget_q4` on another ($37.97): `first_click` credits the newsletter,
`last_click` the retargeting ad. Revenue by campaign over the 8 orders, last click → first click:
`retarget_q4` $278.96 → $219.00, `newsletter_oct` $24.00 → $83.96, organic $77.99 → $155.99, Direct
$81.00 → $3.00; `fall_launch` $0 under every model (the README has every model).

## Measured

Every batch of 2026-09-28, runtime 3.0 (the committed runtime) first:

| batch | result | wall (pending / running) | DCU-hours | shuffle GB-hours | list price |
|---|---|---|---|---|---|
| `...e580908d-t1-8f7f6a` (04:27 UTC, code `61bf231e63fc`) | CANCELLED by the 30-min TTL after writing `fct_attribution`, before the mart | 1,918 s (111 / 1,807) | 2.4132 | 150.82 | $0.153 |
| `...3ef1592e-t1-9be5af`, `...c79bab32-t1-e1162f`, `...ca8d31f6-t1-93fc23`, `...ffcdfa20-t1-4e81f3`, `...498a7a1d-t1-b75e5c` (04:59 to 13:40 UTC) | FAILED at creation: Cloud Resource Manager API not enabled (below) | 4.5 to 9.8 s | 0 | 0 | $0 |
| `...596c23f6-t1-2c6856` (14:03 UTC) | SUCCEEDED: `make spark-submit` after the API was enabled; compute 159.1 s, write 105.2 s; loads 2.7 s, 2.7 s | 368 s (60 / 308) | 0.4093 | 25.58 | $0.026 |
| `...f2954fae-t1-b5bdad` (14:19 UTC) | SUCCEEDED: the full Airflow run (`docs/orchestration.md`); compute 166.9 s, write 116.5 s; loads 2.8 s, 2.7 s | 389 s (60 / 329) | 0.4409 | 27.56 | $0.028 |
| `...bef2f7d9-t1-c95cc0` (14:53 UTC) | SUCCEEDED: `make spark-submit` with the site's export in Stage 2; 4,926 orders; compute 201.5 s, write 135.7 s | 444 s (56 / 388) | 0.5190 | 32.44 | $0.033 |
| `...e7c2318c-t1-0973b2` (15:03 UTC) | SUCCEEDED: the full Airflow run with the site's export (`docs/orchestration.md`); 73,962 rows written, the three checks passed; compute 166.9 s, write 114.7 s | 375 s (48 / 328) | 0.4364 | 27.27 | $0.028 |

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

**Runtime 3.0, completed.** Four batches have completed, all with the committed code (`230e52e7cb36`)
and the one-file write, and each time the connector deleted its staged files after the loads. Like the
first 3.0 batch (below), all four ran as a single driver in Spark local mode (application id
`local-...`, which also names the connector's staging paths), averaging 4.8 DCUs while running, against
about 12 on 2.3. The two on the sample alone (`596c23f6`, `f2954fae`: every log entry from the driver
node, while a 2.3 batch also logged from two workers; each load job under 3 s) show the trade: on 3.0
the job itself is slower (compute 159 to 167 s and write 105 to 117 s, against 76 to 79 s and 58 to 59 s
on 2.3) and the batch is longer (368 to 389 s against 218 to 225 s), but it uses fewer DCU-hours (0.41
to 0.44 against 0.51) and costs a little less ($0.026 to $0.028 against $0.032 to $0.033). The two with
the site's export in Stage 2 (`bef2f7d9`, `e7c2318c`: 8 more orders of 4,926) spread wider: 444 s and
375 s, compute 201.5 s and 166.9 s, write 135.7 s and 114.7 s, 0.52 and 0.44 DCU-hours, $0.033 and
$0.028, so the slower one used as many DCU-hours as a 2.3 batch. About a quarter of a 3.0 batch is
Serverless around the job: 48 to 60 s pending, and on the sample-only batches about 20 s from RUNNING
to the Spark application's start and about 20 s after the job's last line. Why Dataproc ran this
batch in local mode on 3.0, with one task thread, and what the batch runs as since: [Stage 4](#stage-4-local-mode-partitions-writes-size).

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
--project <project>`, no charge), and all four 3.0 batches since were created and succeeded. A new
project needs the same step before its first batch.

## Stage 4: local mode, partitions, writes, size

Stage 4's Spark experiments (6 to 9) changed one thing at a time in this job, each batch measured
by the Stage 4 harness (`make bench-spark`, `bench/README.md`; every record in
`bench/results/s4-e6-*.jsonl` to `s4-e9-*.jsonl`) and gated by the three attribution checks (`make
attribution-checks`) and an exact diff of `fct_attribution` and `mart_attribution_daily` against the
baseline copies (`make bench-diff`). Figures are median (min–max) of the runs; money is list price.

### Experiment 6: why runtime 3.0 runs in local mode

Google's runtime 3.0 page, the autoscaling page and the FAQ say nothing about batches running in
local mode. What six small diagnostic batches showed (a PySpark file that prints the SparkContext and
the Spark config, submitted with `batch.py`'s body; $0.039 in all):

- **Dataproc sets it.** The image's `/etc/spark/conf/spark-defaults.conf` has `spark.master=dataproc`
  (runtime 3.0's own cluster manager, which asks Dataproc's Resource Manager for executor nodes), and,
  further down in the block written from the batch's properties, `spark.master=local`. The later line
  wins; the driver's environment also has `MASTER=local`. The batch's `runtimeConfig.properties` does
  not show it.
- **`local` is one thread.** `defaultParallelism` was 1 on a 4-vCPU driver: the job ran every task one
  after another on one of the four cores Dataproc bills for (4.8 DCUs: 4 vCPUs x 0.6 + 24 GiB x 0.1).
- **Not this job's settings.** With Google's defaults (no executor properties) the batch was local too.
  Runtime 3.0 creates the workload with a driver node only (Resource Manager `CreateWorkload`, one
  `e2-custom-4-24576` node); runtime 2.3 started a standalone master and two workers before the job.
- **The Batch API will not change it.** `spark.master` as a property was refused for every value tried
  ("Invalid value for property spark.master": `dataproc`, `local[4]`, `local[*]`, `local[2]`,
  `local-cluster[...]`, `spark://...`, `yarn`), and `spark.dataproc.scaling.version=2` is "unsupported".
  The only value Google documents is `local`, for Spark Connect sessions ("3.0+ runtimes support Spark
  single-node execution"), and a 2026-07-13 release note says the 3.0 runtime "now uses fewer executors".
- **The job can choose.** A master set on the session builder wins over spark-defaults. With
  `local[4]` in code the driver had 4 task threads; with `dataproc` it got executors (application id
  `batch-<uuid>`): the driver asked the Resource Manager for a node pool of 2 executors about 70 s after
  the batch started, and they were ready about 50 s later.

**Measured** (two batches each, the same code and inputs, 2026-09-29; the control is the runtime's own
`local`):

| variant | wall s | running s | DCU-hours | list price | compute s | after compute s | mode |
|---|---|---|---|---|---|---|---|
| baseline (Stage 3 code, 3 runs) | 390 (380–517) | 338 (333–464) | 0.4527 (0.4472–0.6195) | $0.0287 | 170.6 | 123.2 | local, 1 thread |
| control (`local`, runtime default) | 360 (358–361) | 313 | 0.4157 (0.4140–0.4174) | $0.0264 | 161.6 | 107.8 | local, 1 thread |
| `--spark-master=local[4]` | 241 (229–252) | 190 (182–197) | 0.2528 (0.2412–0.2643) | $0.0160 | 87.3 | 58.3 | local, 4 threads |
| `--spark-master=dataproc` | 287 (263–310) | 240 (222–258) | 0.7944 (0.7400–0.8489) | $0.0504 | 83.8 | 63.2 | driver + 2 executors, 8 task slots |

"After compute" is the job's `write_seconds`, which since Stage 3 has covered everything after the
checks: the summary's counts and then the two writes (from experiment 7 on the job also reports the
two apart). Load jobs took 2.1 to 2.6 s each and never queued more than 0.3 s in these runs.

**Kept: `local[4]`.** Against the control, a third less wall time (241 s against 360 s) and 39% less
money ($0.0160 against $0.0264 a batch): the same node and the same 4.8 DCUs, for 190 s instead of
313 s, because compute and the summary's counts ran four tasks at a time. The ranges do not overlap.
Executors were **not** kept: the batch starts on the driver alone and the driver asks for the
executor node pool only when its SparkContext starts, so the two executors arrive about a minute
into the run; from then on the batch pays for three nodes (12 DCUs on average while running), and
eight task slots finished the job no faster than four threads on one node (compute 83.8 s against
87.3 s), so it cost 3.2 times as much as `local[4]` ($0.0504) and was slower (287 s). It also
changed the results: `fct_attribution` was identical, but `mart_attribution_daily` differed from
the baseline in 823 rows in the first run and 783 in the second, every one in the last bits of a
DOUBLE sum (0.17705424071389861 against ...64; revenue 13.200000000000001 against 13.2), identical
when rounded to 9 digits. The mart's sums depend on the order rows are added in, and with executors
that order follows the order shuffle blocks arrive over the network. In local mode, with one thread
or four, every run reproduced the baseline bit for bit (fingerprints and the exact diff), and the
three attribution checks passed.

`batch.py` now passes `--spark-master=local[N]`, N being the driver's cores, and says why; the DAG
submits the same batch (DagBag test 18/18).

### Experiment 7: 1000 shuffle partitions for 12 k touches

**Why 987 partitions.** Dataproc's spark-defaults set `spark.sql.shuffle.partitions=1000`. Adaptive
query execution is on and coalesces small shuffle partitions, but not the output of a cached plan
(`spark.sql.optimizer.canChangeCachedPlanOutputPartitioning` is false by default in Spark 4.0.2): the
three DataFrames the job caches (touches, fct_attribution, the mart) keep 1000 partitions (987 of them
held rows in the first 3.0 batch's write), and every count, check and aggregation over them runs 1000 tasks. A local run on synthetic data of the sample's
shape (guidance only, not a measurement: a laptop, 5,000 orders, 360,000 sessions) showed the pattern:
compute 26.3 s with `local` and 1000 partitions, 9.9 s with `local[4]`, 2.1 s with `local[4]` and 4
partitions, 2.2 s with `local[4]`, 1000 partitions and AQE allowed to coalesce cached plans (1 cached
partition).

**Step 1, a correctness change first: exact mart sums.** Changing the partitioning changes the order in
which the mart's DOUBLE sums add their rows, so it can change the last bits of a mart row without any
optimisation being wrong (experiment 6 showed it on executors). Before tuning partitions the mart now
sums in DECIMAL(38,18) and casts back to DOUBLE (`models.EXACT_SUM`): each weight and revenue rounded to
18 decimal places, far below a double's precision at these magnitudes, and an exact sum, the same
whatever the order. A unit test adds 0.1, 0.2 and 0.3 in three orders (0.6000000000000001 or 0.6 as
DOUBLE; 0.6 every time now), and another runs the job's models on random journeys split and shuffled
three ways and requires identical rows. Against the baseline, `fct_attribution` is identical and
1,030 of 5,556 mart rows differ, all in the last bits (1.6666666666666665 against 1.6666666666666663),
identical when rounded to 9 digits; the three attribution checks pass (revenue conserved to the cent).
This is the one documented difference from the baseline in the Spark tables, the same kind of change
as Stage 4's exact money sums in BigQuery. Two batches (`s4-e7-exactmart`): 286 s (258–315), 0.3011
DCU-hours (0.2757–0.3265), $0.0191; the compute phase, which does not touch the mart, took 100.6 and
118.5 s against 81.5 and 93.1 s for the same code in experiment 6, so run-to-run noise at 1000
partitions is about 30 s, and the cost of the decimal sums cannot be told from it here.

**Step 2, the partitions.** On top of step 1, two batches each:

| variant | wall s | running s | DCU-hours | list price | compute s | summary s | writes s |
|---|---|---|---|---|---|---|---|
| step 1 (1000 partitions) | 286 (258–315) | 225 (207–242) | 0.3011 (0.2757–0.3265) | $0.0191 | 109.5 (100.6–118.5) | 42.6 (39.5–45.7) | 24.8 (24.0–25.5) |
| `spark.sql.shuffle.partitions=4` | 152 (147–158) | 104 (101–106) | 0.1369 (0.1333–0.1406) | $0.0087 | 31.1 (30.5–31.6) | 3.3 | 27.2 (25.6–28.8) |
| `spark.sql.optimizer.canChangeCachedPlanOutputPartitioning=true` | 152 (147–157) | 104 (101–106) | 0.1362 (0.1330–0.1394) | $0.0086 | 31.0 (30.8–31.2) | 2.5 (2.4–2.6) | 27.1 (25.8–28.4) |

Both cut the batch by almost half against step 1 (152 s against 286 s) and its cost by about 55%
($0.0087 against $0.0191): compute fell from 110 s to 31 s and the summary's counts from 43 s to 3 s,
because 12 k touches no longer run as 1000 tasks per step. The two settings were within noise of each
other. **Kept: `spark.sql.shuffle.partitions=4`** (in `batch.py`'s properties, one partition per task
thread): it is a documented setting, where the other is an optimizer switch whose own description warns
that reading the cached data "may need an extra shuffle". Every batch of both variants wrote tables with
the step 1 fingerprints: `fct_attribution` identical to the baseline, the mart identical to step 1's,
the 1,030 last-bit rows included, the checks passing. So the partition count no longer changes a result.
Repartitioning the inputs was not tried: each read arrives as one or two Storage Read API streams
("Received 1 partitions" / "2 partitions" in the driver log), already fewer than the threads, and the
shuffles after them are what the setting sizes.

### Experiment 8: the write path

With experiment 7 in place the two writes take about as long as the job's compute (24 to 29 s against
about 31 s, `write_only_seconds` in six batches). Each write stages one Parquet file in the bucket, runs
one load job (2.0 to 3.6 s in those batches, never queued more than 0.4 s), deletes the staged folder,
and then the job sets the table and column descriptions (a `tables.get` and a `tables.update`, since a
WRITE_TRUNCATE load replaces the schema and its descriptions).

- **Direct write: not run.** The connector's direct method (Storage Write API) cannot create the
  partitioned table (`partitionField` is "not supported at this moment by the direct write method")
  and overwrites an existing table "using MERGE statement": a BigQuery query job the connector runs,
  and the connector has no option to cap its bytes billed. That breaks two rules this project keeps
  (every query job carries `maximum_bytes_billed`; the job runs no query jobs), so it was not run; the
  staging and loading it would replace are part of the ~27 s the writes take.
- **One file per table: kept.** With 4 shuffle partitions the DataFrames have at most 4 partitions;
  `coalesce(1)` keeps one staged file and one load input per table, which is what the 987-file write
  of the first 3.0 batch showed matters.
- **Partitioned output tables: kept.** The load job's side, measured apart (`s4-e8-loads`): the current
  table extracted to Parquet (free), then loaded with WRITE_TRUNCATE into a day-partitioned and an
  unpartitioned scratch table, alternately, three times each. `fct_attribution`: 2.75 s (2.74–15.02)
  partitioned, 2.91 s (1.98–6.12) not, within noise. `mart_attribution_daily`: 2.47 s (2.46–2.77)
  partitioned, 1.61 s (1.26–1.67) not, about 0.9 s a batch. Partitioning prunes nothing at this size
  (BigQuery bills at least 10 MB per table read), so it is kept for the layout's sake, like Stage 2's
  facts. Changing it would also need both tables dropped first: a WRITE_TRUNCATE load job without a
  partitioning spec into a partitioned table succeeds and leaves the table partitioned (checked on a
  scratch copy of the mart).
- **Both tables at once: not kept.** The job wrote the two tables from two threads (a
  `--write-concurrency=2` argument, now removed), three batches because the first two disagreed
  (`s4-e8-concurrent`): the writes took 33.1, 22.4 and 17.7 s against 24.0 to 28.8 s one after the
  other (median 22.4 against 25.7 over six sequential batches), and the first run's
  `fct_attribution` load ran 22.1 s inside BigQuery without queueing (0.2 s), a tail the load probe
  above also hit once (15.0 s). The batches cost $0.0099, $0.0098 and $0.0079 against $0.0085 to
  $0.0089 sequentially: the compute phase moved more between runs (31.0 to 38.6 s) than the writes
  saved. The saving is inside the spread of either, worth at most about 5 s x 4.8 DCUs ($0.0004) a
  batch, and costs a second thread writing next to the first; the job still writes one table after
  the other.

### Experiment 9: size

A DCU is 0.6 per vCPU plus 0.1 per GiB of the node's memory (up to 8 GiB per vCPU). The default driver,
4 cores with `spark.driver.memory` 16000m and PySpark's 40% overhead (6400m), gets an
`e2-custom-4-24576` node: 2.4 + 2.4 = 4.8 DCUs, the average of every local-mode batch here. What can
move, within Serverless's minimums:

- **Cores**: 4 is the smallest driver (4, 8 or 16). Not tried larger: after experiment 7 the job's own
  compute is about 30 s of a ~150 s batch, and Serverless's fixed part (about 50 s pending, about 40 s
  of the running time before the application starts and after it ends) does not shrink with cores.
- **Executors and dynamic allocation**: nothing to size in local mode; no executor is requested. The
  executor settings stay in `batch.py` for anyone who switches the master to `dataproc`.
- **Disk**: 250 GiB is the minimum per node, already set; shuffle storage is billed on the node's disks
  (250 GiB plus the 50 GiB boot disk: the 300 GB the batches report), about 5% of the price.
- **Memory**: the API wants 1024m to 7424m per core in total (it refused 2048m + 1024m with that
  message). `spark.driver.memory=2867m` with `spark.driver.memoryOverhead=1229m` is the minimum, 4 GiB
  in all: the node becomes `e2-custom-4-5888` (about 1.75 GiB above the driver's 4 GiB) and the rate
  2.4 + 0.575 = 2.975 DCUs, 38% less per second.

**Measured** (`s4-e9-driver4g`, on experiments 6 and 7, the job's final code): 152 s (150–155),
running 104 s (101–106), 0.0868 DCU-hours (0.0849–0.0888) at 3.0 DCUs on average, **$0.0057** a batch
($0.0056–$0.0058), against 152 s, 0.1369 DCU-hours and $0.0087 with the default memory: the same time,
37% fewer DCU-hours and 34% less money (the shuffle storage, billed on the disks, did not shrink). Compute (30.5, 32.2 s), the summary (3.5, 4.2 s) and the writes (26.8, 27.0 s) took as
long as with 24 GiB; the fingerprints were step 1's and the checks passed. **Kept**: the two memory
properties are in `batch.py`, and a test holds them at the minimum for the driver's cores.

### Where the batch ended up

| | baseline (Stage 3 code) | Stage 4 |
|---|---|---|
| master, threads | `local`, 1 (Dataproc's choice) | `local[4]` (`--spark-master`) |
| shuffle partitions | 1000 (Dataproc's default) | 4 |
| driver memory | 16000m + 6400m (4.8 DCUs) | 2867m + 1229m (3.0 DCUs) |
| mart sums | DOUBLE, order-dependent | DECIMAL(38,18), exact |
| batch wall time | 390 s (380–517) | 152 s (150–155) |
| running (billed) time | 338 s (333–464) | 104 s (101–106) |
| DCU-hours | 0.4527 (0.4472–0.6195) | 0.0868 (0.0849–0.0888) |
| list price a batch | $0.0287 | $0.0057 (-80%) |
| a daily batch, x30 | $0.86 a month | $0.17 a month |
| job: compute / summary / writes | 170.6 s / about 95 s / about 28 s | 31 s / 4 s / 27 s |

The final measurements, with every Stage 4 change in (`s4-end-spark`, two batches, 2026-09-29): **144 s**
(134–154), 98 s running, **0.0818 DCU-hours** (0.0764–0.0872), **$0.0054** a batch; the two Airflow runs'
batches took 145 s ($0.0054) and 183 s ($0.0068, one slow compute phase), every one with the same output
fingerprints ([STAGE4-RESULTS.md](../STAGE4-RESULTS.md#the-final-state)). Against the baseline that is −63% wall
time and −82% DCU-hours; against the same-day control (the baseline code rerun hours later: 360 s, 0.4157
DCU-hours), −60% and −80%.

What is left is mostly Serverless's own: about 50 s pending (not billed), and of the ~104 s billed about
40 s before the Spark application starts and after it ends, which no Spark setting reaches. The
outputs: `fct_attribution` identical to the baseline row for row; `mart_attribution_daily` identical
but for the last bits of 1,030 rows (exact sums), equal when rounded to 9 digits; the three
attribution checks pass, and `make spark-report`'s independent SQL rebuild matches all 73,962 rows
(max weight difference 2.2e-16). Every Stage 4 batch kept the 30-minute TTL and the labels; none was
left running. Spark spend for experiments 6 to 9: 17 measured batches and 6 diagnostic ones, about $0.34 at
list price, plus about 2 GiB of harness and check queries (about $0.01).

## Limitations

- **One device per person on the sample**: first click, linear and the rest only move credit between
  channels seen on the device that bought. Cross-device journeys need the site's stitched export: 4
  of its 8 attributed orders, one day of simulated visits seconds apart.
- **Orders without a session are not attributed** (none on the sample; 5 of the site's 13, $378.98, all
  sent with consent denied). A signed-in purchase with no session could be attributed to the person's
  earlier sessions; Stage 2's channel marts leave it out, and so does this (on the site, neither
  signed-in one has a person with any session).
- **The mart keeps zero rows**: every touch appears under every model, so a channel that touched an
  order but got no credit under a model has a row with 0 attributed orders (5,532 rows, 4,982 non-zero).
- **The two tables are written one after the other** (two `WRITE_TRUNCATE` load jobs, about 20 s
  apart), not swapped in together: a batch that stops between them leaves a new `fct_attribution`
  beside an old mart (it happened once, to the TTL-stopped batch). The next good run repairs it.
- **Runtime drift**: Dataproc takes only `3.0`; a new subminor can change the Spark patch version. The
  job logs a warning and records both versions in its `ATTRIBUTION_SUMMARY` line (in Cloud Logging:
  runtime 3.0 has no staging bucket for driver output).
