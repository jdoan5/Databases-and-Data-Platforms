# Orchestration: Tagline Stage 3

> **Status, 2026-09-28.** The whole DAG ran end to end on the committed Dataproc runtime, **3.0**, at
> 14:17 UTC: Stage 2 models and checks, the attribution batch on Dataproc Serverless, the three checks
> on its output and the summary, all green, in 487 s and about $0.081 ([measured below](#measured)).
> After the first, runtime 3.0 batches could not be created in this project until the Cloud Resource
> Manager API was enabled, at 14:02 UTC that day ([Limitations](#limitations)). The earlier full runs
> used runtime 2.3 as a stand-in, with the same job code; they are kept below as history.

One DAG, `tagline_daily`, runs the whole pipeline once a day: wait for the site's GA4 export (only
when one is configured), rebuild the Stage 2 BigQuery models in lineage order, run the Stage 2 data
checks, run the Stage 3 multi-touch attribution job on Dataproc Serverless, and check the Spark
output against Stage 2. Airflow 3 runs locally in Docker; the DAG uses the Google provider's
operators, so it would run on Cloud Composer with the changes listed [below](#what-would-change-on-cloud-composer).

---

## The DAG

```
check_ga4_export ─┬─► wait_for_ga4_export ─┬─► prepare_sources
                  └─► no_ga4_export ───────┘
prepare_sources ─► stg_events ─┬─► stg_items ──────────────────────────────────┐
                               └─► int_identity ─► fct_sessions ─► fct_orders ─┴─► fct_order_items ──┐
                                                   fct_sessions ─► mart_campaign_daily ──────────────┤
                                                   fct_sessions ─► mart_funnel_daily ────────────────┴─► stage2_checks (9)
stage2_checks ─► attribution_enabled ─► spark_attribution ─► attribution_checks (3) ─► run_summary
```

`stg_events` also feeds `fct_sessions` and `fct_orders`, and `int_identity` feeds `fct_orders`. The
model edges are not a hand-kept list: the DAG reads each model's SQL and wires one task to another
when it reads that model's table (`{{ staging }}.stg_events`), and refuses a model that reads one
that builds after it. The DagBag test compares the result with the lineage in
[data-model.md](data-model.md#lineage), written down independently.

| Task | What it does | Guard |
|---|---|---|
| `check_ga4_export` | branch: the sensor when `TAGLINE_GA4_DATASET` is set, else `no_ga4_export` | |
| `wait_for_ga4_export` | waits for the day's `events_YYYYMMDD` **or** `events_intraday_YYYYMMDD` table (two free metadata calls per poke) | reschedule mode (no worker slot held), poke every 15 min, **gives up after 8 h as skipped** (soft fail), so the build still runs on the days that did arrive |
| `prepare_sources` | what `make build` does first: create the three datasets if missing, list the site export's tables | runs when the sensor succeeded **or** was skipped (`none_failed`) |
| 8 model tasks | one `CREATE OR REPLACE TABLE` each, rendered by the Stage 2 package, then the table and column descriptions and the row count, as `make build` does | `maximumBytesBilled` on every job |
| `stage2_checks` (9) | Stage 2's checks, in parallel; each returns no rows when it passes | a failing check fails its task **without retrying** (the same query on the same tables cannot pass) |
| `attribution_enabled` | short-circuit on the run parameter `attribution` (default true) | `{"attribution": false}` rebuilds and checks Stage 2 only |
| `spark_attribution` | `AttributionBatchOperator` (the provider's `DataprocCreateBatchOperator`, plus a cancel, below): the attribution job on Dataproc Serverless. The batch is `tagline/spark/tagline_spark/batch.py`, the one definition `make spark-submit` uses too; it runs the code version hashed from the mounted `spark/` sources, so `make spark-upload` must have put that version in the bucket | batch TTL 30 min; task timeout 45 min; a try that ends before its batch does (timeout, any error while waiting, Ctrl-C) cancels the batch; one retry, as a new batch |
| `attribution_checks` (3) | weights sum to 1 per order and model (and every order has all six models); attributed orders and revenue per model = Stage 2's real orders; every order's `last_click` credit is on its own session (`fct_orders.session_key`), and `last_click` orders and revenue by channel = Stage 2's, with no adjustment | `maximumBytesBilled`; fail without retry |
| `run_summary` | the Stage 2 cost table for every BigQuery job of the run, and the batch's final state, wall time, DCU and shuffle usage and list price (re-reading the batch, for at most 3 min, until Dataproc reports the usage) | runs whatever happened (`all_done`); **fails the run if any task failed** (see below) |

The Stage 2 checks are the files in `pipeline/sql/checks/`; the three attribution checks are
`airflow/dags/tagline_airflow/sql/attribution_checks/`, written in the same convention (`{{ project }}`,
an `@check` header, no rows = pass).

Run-level settings: daily at 10:00 UTC, created paused, `catchup=False`, `max_active_runs=1`,
run timeout 10 h. Tasks retry twice (the Spark batch once, `run_summary` never) with exponential
backoff from 1 minute, capped at 10; a check whose query returns rows fails at once, without
retrying. Every task has an execution timeout (20 min by default, 10 for checks, 5 per sensor
poke, 45 for the batch).

**Which export day.** With a cron schedule, Airflow 3 sets a run's logical date to the moment it is
due (10:00 UTC), so the run waits for the export of the day before. That is a UTC date; the GA4
property's own time zone may put the day boundary elsewhere, and GA4 writes the daily table
some hours into the next day, which is what the 8-hour sensor window is for. A run triggered by
hand or through the API may have no logical date at all; Airflow 3 then leaves `logical_date` out
of the task context, and the sensor falls back to the run's `run_after` (when it was triggered),
so it waits for the day before that. The DagBag test calls the real sensor on such a context.

**Why the last task decides the run's state.** Airflow marks a run successful when its leaf tasks
succeed. `run_summary` is the only leaf and runs whatever happened, so on its own it would turn
a failed check into a green run. It asks Airflow for every task's state in the run and fails if
any task failed or could not run because an upstream failed; an unreadable answer fails too.
Tested by marking one check failed in a finished run and re-running `run_summary`: it failed,
naming the check.

---

## Run it locally

Requires Docker Desktop and Google application-default credentials
(`gcloud auth application-default login`), plus `tagline/.env` with `TAGLINE_GCP_PROJECT`,
`TAGLINE_GCP_REGION`, `TAGLINE_SPARK_BUCKET` and `TAGLINE_SPARK_SERVICE_ACCOUNT` (see
`.env.example`), and the Cloud Resource Manager API enabled in the project: without it runtime 3.0
batches fail at creation (every one after the first did here; [Limitations](#limitations)).

```bash
cd tagline
make airflow-up        # first run: generates the admin login and prints it once; UI on http://localhost:8080
make airflow-check     # DagBag import test in the Airflow image (no Google Cloud calls)
make spark-upload      # the job's code for the current sources, if not in the bucket yet (spark-submit does it too)
make airflow-test      # LIVE: airflow dags test tagline_daily <today, UTC> end to end (BigQuery + Dataproc)
make airflow-test AIRFLOW_DATE=2026-09-27 AIRFLOW_CONF='{"attribution": false}'   # Stage 2 part only
make airflow-down      # stop everything; the metadata database volume is kept
make airflow-orphans   # after a crash: Dataproc batches the DAG left running (CANCEL=1 cancels them)
```

- **The stack** (`airflow/docker-compose.yaml`): the official `apache/airflow:3.3.2` image with
  LocalExecutor (tasks run as processes of the scheduler container), `postgres:16` for the metadata
  database, and the three Airflow 3 components that must run: api-server (UI, REST API and the task
  execution API), scheduler, dag-processor. No Celery, Redis or triggerer: nothing in the DAG defers.
  The UI is bound to `127.0.0.1` only. Up and healthy in about 20 s once the images are pulled
  (3.2 GB for Airflow).
- **Mounts**: the DAGs, `pipeline/` (the Stage 2 package and its SQL) and `spark/` (the batch
  definition in `tagline_spark`, and the job's sources it hashes into the code version), both
  read-only and on `PYTHONPATH`; the DagBag test; and the task logs (`airflow/logs/`, gitignored).
- **Configuration**: `tagline/.env` goes into the containers as environment variables (`env_file`),
  and the DAG reads it through the Stage 2 package's own `load_config`, so the DAG and `make build`
  cannot disagree about the project, the GA4 dataset or the cost guard.
- **Google Cloud auth, local only**: the ADC file is mounted read-only at
  `/opt/airflow/gcp/application_default_credentials.json` and named by `GOOGLE_APPLICATION_CREDENTIALS`,
  in two containers only: the scheduler (LocalExecutor runs every task there) and the one-off
  `airflow-cli`. The api-server (the only published port), the dag-processor and `airflow-init` make
  no Google Cloud calls and do not get it: it is a refresh token with the user's own access (project
  Owner here). Checked with the stack up: the file and the variable exist in the scheduler and in
  neither of the other two running services, and the DAG parsed without import errors. The
  `google_cloud_default` connection is defined from the environment with no key, so every operator
  and hook falls back to ADC. Nothing is copied into the repo or an image. (Tasks run with the
  user's identity; impersonating a least-privilege service account through the connection would
  narrow that further, and is not done here.)
- **Login**: Airflow 3's simple auth manager, user `admin`, with a random password that
  `airflow/scripts/init-secrets.sh` generates on the first `make airflow-up`, prints once and keeps
  only in `airflow/.secrets/simple_auth_manager_passwords.json` (gitignored). The same script writes
  `airflow/.env` (gitignored): the host UID, a random Postgres password, the JWT secret the
  components share, and a Fernet key. The simple auth manager is Airflow's development-only one
  (it logs a warning because the database is Postgres); that is fine on `localhost` and is one
  of the things Composer replaces.
- **What an unpause starts.** `tagline_daily` is created paused (`is_paused_upon_creation=True` in
  the DAG, so on Composer too, and Airflow's own default in the compose file). Unpausing it in any
  way (the UI toggle, `airflow dags unpause`, or the trigger form's "Unpause on trigger" box) makes
  the scheduler create the run for the most recent 10:00 UTC straight away (Airflow 3 with
  `catchup=False` runs the latest interval that has passed) with the default `attribution: true`:
  a full Stage 2 rebuild (8.62 GiB, about $0.05) and a Spark batch. Triggering
  `{"attribution": false}` does not avoid that. A run triggered while the DAG is paused is queued
  and does not start, and once the DAG is unpaused the scheduled run is created as well, since a
  manual run does not stand in for it; the two then run one after the other. To rebuild and check
  Stage 2 only, leave the DAG paused and run `make airflow-test AIRFLOW_CONF='{"attribution":
  false}'`: `airflow dags test` runs whether the DAG is paused or not.
- **Nothing starts by itself.** Every service has `restart: "no"`, so the stack runs only between
  `make airflow-up` and `make airflow-down` (or until Docker Desktop quits), and does not come back
  when Docker Desktop starts. The pause state is kept in the metadata database, which
  `make airflow-down` keeps too: if you unpaused the DAG, the next `make airflow-up` starts the most
  recent missed 10:00 UTC run at once. Pause it in the UI before `make airflow-down` if you don't
  want that. While `TAGLINE_GA4_DATASET` is empty the input is the static public sample, so a
  scheduled run rebuilds the same tables and reruns the same attribution every day: it costs
  money and changes nothing, so keep the DAG paused until the site's export exists.

`airflow dags test` runs every task in one process, one at a time, without the scheduler or the
executor; a scheduled or triggered run goes through the scheduler (LocalExecutor, parallelism 8),
which runs independent tasks (`stg_items` and `int_identity`, the nine checks) side by side.

---

## Cost guards and retries

- **BigQuery**: every job the DAG submits, models and checks, carries `maximumBytesBilled`
  (`TAGLINE_MAX_BYTES_BILLED`, 10 GB by default: a job that would bill more fails before it runs),
  no query cache, and Stage 2's labels (`app`, `stage`, `kind`, `step`) plus `orchestrator=airflow`;
  the provider adds `airflow-dag` and `airflow-task`. Stage 4 can find these jobs in
  `INFORMATION_SCHEMA.JOBS` exactly as it finds the CLI's. The DagBag test renders every model and
  check and asserts the guard on each; the first live run's `stg_events` job was read back from
  BigQuery with `maximum_bytes_billed = 10000000000` and those labels.
- **Dataproc**: the batch (runtime 3.0, Spark 4.0.2) has a 30-minute TTL (Dataproc stops it whatever
  Airflow does; it fired on the first manual run, see `spark/README.md`), the smallest shape
  Serverless accepts (a 4-core driver, two 4-core executors, no scale-out, 250 GiB disks), and labels
  `app=tagline, stage=3, job=attribution, orchestrator=airflow, code=<version>`; the operator adds
  `airflow-dag-id=tagline-daily` and `airflow-task-id=spark-attribution` (it lowercases ids and turns
  `_` into `-`). The DagBag test asserts the DAG's batch equals `make spark-submit`'s apart from the
  `orchestrator` label. The job itself runs no query jobs: it reads through the BigQuery Storage Read
  API and writes with two load jobs (free), and checks its own output before writing anything.
- **Retries are safe to repeat.** Models are `CREATE OR REPLACE`, so a retry rebuilds the same table.
  BigQuery tasks set `durable=False`: Airflow 3.3's resumable operators would otherwise reuse a
  previous try's finished job on retry (and, unless the state store is cleared on success, even
  after the task is cleared by hand), which would make a re-run check re-read old results.
- **Batch ids are unique per try, and never come round again**:
  `tagline-attr-<date>-<hash of the run id>-t<try>-<hash of the task instance id>`, e.g.
  `tagline-attr-20260928-ca4371b6-t1-98ee8f`. Dataproc refuses a second batch with an existing id,
  and the operator then attaches to the existing batch and reports its result, so an id that came
  round again would re-report an old batch instead of running Spark. Airflow 3 gives every try a new
  task instance id (a UUID7, drawn in `TaskInstance.prepare_db_for_next_try`, which a retry and a
  clear both call), so the id is new for a retry, for another run, and after the metadata database is
  wiped (when run ids and try numbers start again but Dataproc still keeps the old batches).
  **Measured** on the earlier real run (below): clearing `spark_attribution` drew a new task instance id
  (`01a0e67a-e8cc-…` to `01a0e685-0f50-…`, try number still 1) and the task then ran a new batch,
  `…-t1-4f152d` after `…-t1-98ee8f`; it overwrote both tables (73,980 and 5,532 rows before and
  after, under the journey rule of the time; the same 19,656,546 bytes, 73,980 distinct order × model
  × touch keys) and the three checks
  passed again. Without the clear, `airflow tasks test` on the same run re-uses that try's task
  instance, renders the same id, and attaches to the finished batch (it logged "Batch with given id
  already exists", reported success in 1.4 s and ran nothing): harmless, but it is not a re-run. To
  run Spark again for a run, clear the task (UI or API) first.
- **A try that ends early cancels its batch.** The provider's operator cancels its batch only in
  `on_kill`, which Airflow calls on an execution timeout or a SIGTERM. Any other exception while it
  waits (an ADC token that cannot be refreshed after the laptop slept, a transport error during a
  network drop: the hook's wait retries server errors only) failed the try and left the batch
  running, and so did a Ctrl-C in `make airflow-test` (the in-process runner catches the
  `KeyboardInterrupt` without calling `on_kill`); the retry, a new batch id, then started a second
  batch beside the first, both overwriting the same `tagline_marts` tables. `AttributionBatchOperator`
  (`tagline_airflow/operators.py`) cancels the batch on any exception that ends the try, at most once,
  and never lets a failed cancel hide the original error; the DagBag test drives it with an error,
  a `KeyboardInterrupt`, a batch that already ended, a failing cancel and a deferral.
- **A batch can still outlive a crashed Airflow.** A worker that dies outright (the scheduler
  container killed or out of memory, Docker Desktop quit mid-run) cancels nothing: the batch runs on
  until it finishes or reaches its 30-minute TTL (counted from RUNNING: the one TTL stop so far came
  1,807 s after RUNNING, 1,918 s after creation), and when Airflow comes back the task's retry starts
  a second batch beside it. The TTL caps the waste at one extra batch: about $0.03 for a normal run,
  and up to about $0.36 for one that runs its full 30 minutes at the 12-DCU shape (the TTL-stopped
  batch, below, ran in local mode and cost $0.15). After such a crash, or if a cancel was logged as
  failed, run `make airflow-orphans` before `make airflow-up`: it lists the batches `tagline_daily`
  started (labels `app=tagline` and the provider's `airflow-dag-id=tagline-daily`) that are still
  pending or running, and
  `make airflow-orphans CANCEL=1` cancels them. A batch submitted by hand carries no
  `airflow-dag-id` label and is left alone. (It uses the gcloud CLI and reads the project and
  region from `tagline/.env`.) The provider writes the DAG id as a label value, lowercased with `_`
  turned into `-`: the script first matched `tagline_daily` and listed nothing while the retry's
  batch below was pending; it now matches `tagline-daily` and listed it.
- **Network.** Serverless batches run on the default network's `us-central1` subnet with internal
  IPs only (Private Google Access is on), and the one firewall rule they need is
  `default-allow-internal` (all ports inside `10.128.0.0/9`). The project also still has the
  default network's `default-allow-ssh` (tcp:22), `default-allow-rdp` (tcp:3389) and
  `default-allow-icmp` open to `0.0.0.0/0`, with logging off. Nothing in Tagline uses them and the
  project has no VM, so they expose nothing today, but they would expose any VM or Dataproc cluster
  with an external IP created later (a Stage 4 experiment, say). **Recommended: delete all three**
  (or, if SSH is ever needed, narrow it to IAP's range `35.235.240.0/20` and use `gcloud compute ssh
  --tunnel-through-iap`). Stage 3 did not change them; that is the owner's call.

---

## Measured

All on 2026-09-28, GA4 sample only. Prices are Google's list prices, read that day:
BigQuery on demand $6.25 per TiB billed (US multi-region); Serverless for Apache Spark standard tier in
`us-central1`, $0.06 per DCU-hour and $0.04 per GB-month of shuffle storage ($0.000054795 per
GB-hour), billed per second with a 1-minute minimum
([pricing](https://cloud.google.com/dataproc-serverless/pricing)).

**Full DAG run on runtime 3.0** (`airflow dags test tagline_daily 2026-09-28`, run
`manual__2026-09-28T14:17:36.172874+00:00`, with the committed `spark/` mounted as it is, code
version `230e52e7cb36`): 26 tasks succeeded and 1 was skipped (the GA4 sensor: no export configured),
all nine Stage 2 checks and all three attribution checks passed, and `run_summary` succeeded.
**487 s** (8 min 7 s) from the first task's start to the last task's end, one task at a time
(`dags test`), and about **$0.081** in all. The last column is the same DAG's earlier run with the
runtime 2.3 stand-in (13:50 UTC, same job code; history, below):

| part | tasks | wall | what it used | list price | 2.3 stand-in run |
|---|---|---|---|---|---|
| branch, `prepare_sources` | 4 | 2.8 s | | | 2.1 s |
| Stage 2 models | 8 | 60.8 s | 8 query jobs, 7.12 GiB billed, 44.7 s of job time | $0.043 | 60.8 s, 7.12 GiB, $0.043 |
| Stage 2 checks | 9 | 22.2 s | 9 query jobs, 1.50 GiB billed, 12.5 s | $0.009 | 22.8 s, 1.50 GiB, $0.009 |
| `attribution_enabled`, `spark_attribution` | 2 | 394.8 s | batch `tagline-attr-20260928-f2954fae-t1-b5bdad`: 389 s from creation to end (60 s pending, 329 s running); 0.4409 DCU-hours, 27.56 GB-hours shuffle storage; 2 load jobs (free) | $0.028 | 236.7 s; batch 225 s (70 / 154), 0.5069 DCU-hours, $0.032 |
| attribution checks | 3 | 6.3 s | 3 query jobs, 80 MiB billed, 2.9 s | $0.0005 | 6.5 s, 80 MiB |
| `run_summary` | 1 | 0.1 s | the cost table; the batch's usage was already in the operator's result | | 31.5 s, waiting for the usage |
| **total** | **27** | **487 s** | **20 query jobs, 8.70 GiB billed (9,338,617,856 bytes); 0.441 DCU-hours** | **$0.081** | **361 s, 8.69 GiB, 0.507 DCU-hours, $0.085** |

BigQuery figures are `run_summary`'s own cost table, and agree with
`region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT`: 20 query jobs labelled `app=tagline` and
`orchestrator=airflow` in the run's window, 9,338,617,856 bytes billed (7,642,021,888 by the models,
1,612,709,888 by the Stage 2 checks, 83,886,080 by the attribution checks). The Spark job adds two
load jobs run as `tagline-spark` (`fct_attribution` 2.8 s, `mart_attribution_daily` 2.7 s; load jobs
are free) and its Storage Read API reads, which `JOBS` does not list. The Stage 2 half took 86 s, as
in the 2.3 run and the Stage 2 only run (below), for the same bytes. `run_summary` did not wait:
Dataproc had filled in the batch's usage (at 14:25:30.6 UTC) before the batch reached SUCCEEDED
(14:25:31.2), so the operator's result already carried it.

Inside the batch (the job's `ATTRIBUTION_SUMMARY` line, in Cloud Logging): Spark 4.0.2, as pinned;
4,918 orders, 12,315 touches, 73,890 `fct_attribution` rows and 5,532 `mart_attribution_daily` rows,
no order whose last touch is not its own session; **166.9 s** to read, build the journeys, attribute
and check, and **116.5 s** to write both tables and set their descriptions. Around the job: 60 s
pending, about 20 s from RUNNING to the Spark application's start, and about 20 s from the job's last
line to SUCCEEDED, so about a quarter of the 389 s. `make spark-submit` shortly before, with the same
code (batch `tagline-attr-20260928-596c23f6-t1-2c6856`): 368 s (60 s pending, 308 s running), 0.4093
DCU-hours, 25.58 GB-hours shuffle storage, $0.026, compute 159.1 s, write 105.2 s, loads 2.7 s and
2.7 s; `make spark-report` afterwards matched all 73,890 rows against the independent SQL rebuild, and
matched them again after the DAG run.

**Runtime 3.0 against the 2.3 stand-in**: the same code, the same rows, the same checks passing. The
3.0 batch took longer and cost less: 389 s against 225 s, but 0.44 DCU-hours against 0.51. Like the
first 3.0 batch (below), both 3.0 batches ran as a single driver in Spark local mode (application id
`local-...`, which also names the connector's staging paths; every log entry comes from the driver
node, `...-m`), averaging 4.8 DCUs while running, where the 2.3 batches ran with executors
(`app-...`; the 13:50 run's batch also logged from two workers, `-w-0` and `-w-1`) at about 12. The
job's own compute and write took about twice as long as on 2.3 (167 s and 117 s, against 76 s and
58 s). Why Dataproc runs this batch that way on 3.0 is not known; it is for Stage 4 to measure, and
the result does not depend on it.

**History: the runtime 2.3 stand-in runs.** Before the API was enabled, the DAG ran with runtime 2.3
from a scratch copy of `tagline_spark/batch.py` (runtime `2.3`, Spark `3.5.3`, one extra label
`purpose=standin-runtime-2-3`) mounted over `spark/`; nothing in the repo runs 2.3. The last of those
runs is the table's last column: `manual__2026-09-28T13:50:42.149532+00:00`, batch
`tagline-attr-20260928-8ef9aa53-t1-fc0bd9` (0.5069 DCU-hours, 32.08 GB-hours; compute 76.3 s, write
58.3 s; loads 2.4 s and 2.3 s), 20 query jobs and 9,335,472,128 bytes billed. `make spark-submit` on
2.3 with the same code (batch `tagline-attr-20260928-ed18fc3b-t1-2ec747`): 218 s (61 s pending, 158 s
running), 0.5135 DCU-hours, $0.033, compute 79.3 s, write 59.3 s, loads 2.4 s and 2.8 s.

**Earlier the same day, before the journey fix** (runtime 2.3; journeys then ended at the purchase:
12,330 touches, 73,980 rows): a full run, `manual__2026-09-28T05:26:49.721200+00:00`, took 403 s and
$0.091 (batch `…-ca4371b6-t1-98ee8f`: 277 s, 0.5958 DCU-hours, $0.038; loads 2.2 s and 2.4 s), and
its Spark task cleared and run again (batch `…-t1-4f152d`: 217 s, 0.5069 DCU-hours, $0.032; loads
11.4 s and 2.2 s) overwrote both tables and passed the checks again (see "Batch ids" above). That
run found two bugs, both fixed and tested since:

- `run_summary` printed **0.000 DCU-hours**: the operator's XCom is the batch as it ended, usually
  before Dataproc fills in its usage. `run_summary` now re-reads the batch until the usage is there
  (at most 3 minutes) and prices it with the rates `make spark-submit` uses (in the 13:50 run it
  waited 31.5 s, and printed `0.507 DCU-hours, 32.083 GB-hours shuffle storage (about $0.032 at list
  price)`).
- `make airflow-orphans` never matched a DAG batch (label value `tagline-daily`, not
  `tagline_daily`; above).

**The first runtime 3.0 batch** (`tagline-attr-20260928-e580908d-t1-8f7f6a`, `make spark-submit` at
04:27 UTC) was created and ran, and was cancelled by the TTL while writing: 2.4132 DCU-hours, $0.153
(`spark/README.md`). What is known about how it differs from the runs above: it ran code version
`61bf231e63fc`, from before the one-file write and before the journey fix, and it was created before
the Cloud Resource Manager API was enabled. The five 3.0 batches after it (04:59 to 13:40 UTC) failed
at creation in 4.5 to 9.8 s, at no cost ([Limitations](#limitations)). Why that first one was created
without the API is not known.

**Stage 2 part only** (earlier the same day, `make airflow-test AIRFLOW_DATE=2026-09-27
AIRFLOW_CONF='{"attribution": false}'`): 17 BigQuery jobs, **8.62 GiB billed, about $0.05** on
demand, the same as `make build` (8.62 GiB). **86 s** of wall time in `dags test`, of which 56.9 s
was BigQuery job time; the remaining ~29 s is Airflow's per-task overhead (starting each task,
XComs, applying the table docs). All nine checks passed; the Spark tasks were skipped by the
parameter, and `run_summary` reported 21 tasks succeeded and 5 skipped.

Also exercised: the sensor against the configured-but-not-yet-created site export
(`analytics_<property_id>`): it found neither table, logged it, and rescheduled itself; the branch
picked the sensor when the dataset is set and skipped it when not.

---

## Why not Cloud Composer

Composer is managed Airflow, and the DAG is written for it, but an environment bills for its
Airflow components (schedulers, DAG processors, the web server, workers) and its managed
infrastructure for every hour it exists, whether or not a DAG runs. Composer 3 is billed in DCU-hours
at **$0.06 per DCU-hour**; Google's own pricing example, a `us-central1` environment using 12 DCUs,
comes to $0.72 an hour, **about $525 a month** (730 hours), plus database storage (a 10 GiB minimum,
about $1.70 a month). Those figures are from Google's
[pricing page](https://cloud.google.com/products/managed-service-for-apache-airflow/pricing)
(which now calls the product Managed Service for Apache Airflow, with Composer 3 as its Gen 3; in the
same way, Serverless for Apache Spark, where the attribution batch runs, is now Managed Service for
Apache Spark, serverless deployment, and its pricing page is titled so),
read on 2026-09-28; 12 DCUs is Google's example, not a quoted minimum. This pipeline runs once a
day, and a whole run, measured above, is about 8 minutes and $0.081: 8.70 GiB of BigQuery
(about $0.05) and one Spark batch of about 6.5 minutes and 0.44 DCU-hours (about $0.03). Composer
would cost the price of that run every 7 minutes, whether or not it runs.
At $0.72 an hour an environment would pass the project's $10 monthly
budget alert in its first 14 hours. Local Airflow in Docker runs the same operators against the same
project for nothing, while the machine is on, which is the honest trade-off: nobody runs it at 10:00
UTC unless the laptop is awake.

## What would change on Cloud Composer

The DAG file and `tagline_airflow/` would run unchanged. What changes is everything around them:

1. **Auth.** Drop the ADC mount, `GOOGLE_APPLICATION_CREDENTIALS` and the
   `AIRFLOW_CONN_GOOGLE_CLOUD_DEFAULT` variable. Composer's `google_cloud_default` connection uses the
   environment's service account, which needs what the user's ADC has now: BigQuery Job User; Data
   Editor on `tagline_raw`, `tagline_staging` and `tagline_marts`; for what `prepare_sources` does
   before the first model, as `make build` does, a project-level role with
   `bigquery.datasets.create` (e.g. BigQuery User) to create a missing dataset, and Data Owner on a
   dataset to fix its description or labels (a dataset-level role cannot be granted on a dataset
   that does not exist yet); read on the public GA4 sample (public, no grant) and on the site's
   export dataset; Dataproc Editor (create, get and cancel batches); and Service Account User on
   `tagline-spark`, so it can start batches that run as it. The `tagline-spark` account itself does
   not change.
2. **Configuration.** The `TAGLINE_*` values become Composer environment variables instead of
   `env_file: ../.env`; the DAG reads them the same way.
3. **Code.** Upload `dags/tagline_daily.py`, `dags/.airflowignore` and `dags/tagline_airflow/` to the
   environment bucket's `dags/` folder, and the Stage 2 package with its SQL beside it
   (`pipeline/tagline_pipeline/` and `pipeline/sql/` side by side: the package finds `sql/` next to
   itself), adding both folders to `.airflowignore`. The package's only dependency,
   `google-cloud-bigquery`, is already in Composer's images. The DAG also imports the batch definition,
   `tagline_spark`, and hashes the job's sources into the code version at parse time, so upload
   `spark/main.py`, `spark/attribution/` and `spark/tagline_spark/` together, in the same layout, to a
   folder on the DAG's import path (and in `.airflowignore`); they must be the same files `make
   spark-upload` put in `gs://<bucket>/code/attribution/<version>/`, or the batch names a version that
   is not there and fails at once. `tagline_spark` is standard library only.
4. **Versions.** The DAG uses the Airflow 3 task SDK (`airflow.sdk`, `get_task_states`) and the
   `durable` argument of `BigQueryInsertJobOperator` (Airflow 3.3); it needs a Composer image with
   Airflow 3.3 or later and its Google provider, or, on an older Airflow 3, `durable=False` removed
   (retries there already resubmit).
5. **Deferral, optionally.** With at least one triggerer configured in the environment (Composer 3
   lets the count be 0, and a triggerer is billed in DCUs like the other components), the sensor
   and the Dataproc task could use `deferrable=True` and free their worker slots while they wait.
6. **Removed.** `docker-compose.yaml`, `scripts/init-secrets.sh`, the generated login (Composer's UI
   sits behind Google sign-in and IAM) and the `airflow-*` Make targets. Logs go to Cloud Logging.

Unchanged: the Dataproc batch (region, default subnetwork with Private Google Access, service
account, TTL, labels) and every BigQuery job, which never depended on where Airflow runs.

---

## Limitations

- **Runtime 3.0 needs the Cloud Resource Manager API** (resolved on 2026-09-28). Dataproc looks up a
  secure tag key through that API when it creates a runtime 3.0 batch (`TagKeys.GetNamespacedTagKey`);
  with the API disabled, creation fails in under 10 s, at no cost, with `Failed to get secure tag
  key ... Cloud Resource Manager API has not been used in project ... or it is disabled`
  (PERMISSION_DENIED). Five 3.0 batches failed that way on 2026-09-28, the last at 13:40 UTC, while
  runtime 2.3 batches with the same job and settings were created and ran. The API was enabled at
  14:02 UTC (`gcloud services enable cloudresourcemanager.googleapis.com --project <project>`, no
  charge); since then `make spark-submit` and the full DAG run above have both succeeded on 3.0. A
  new project needs the same step before its first batch, or `make spark-submit` fails at creation
  and a DAG run fails at `spark_attribution` (both tries) and is marked failed by `run_summary`.
- **The two attribution tables are not replaced together.** The job overwrites `fct_attribution` and
  then `mart_attribution_daily`, each with its own `WRITE_TRUNCATE` load job, about 20 s apart. A
  batch that stops between them leaves a new fact table beside an old mart; that happened once, to
  the TTL-stopped 3.0 batch. And when a DAG run rebuilds Stage 2 and the Spark task then fails, both
  attribution tables are older than the new `fct_orders`. Either way the run is red and the
  attribution checks do not run (`upstream_failed`), so the tables stay out of step, silently,
  until the next good run. On the sample the inputs never change, so nothing differs in practice;
  with the site's export, read a red run as "attribution tables stale". Swapping both tables in at
  once (staging tables, then one multi-statement transaction) is left for when that matters.
- **Leftovers in Cloud Storage** (fractions of a cent, not managed by the pipeline): the TTL-stopped
  3.0 batch left its staged Parquet files under `.spark-bigquery-local-.../` in the Spark bucket
  (1,577 objects, 2.8 MiB; the connector deletes them only after a completed load, and the bucket has
  no lifecycle rule). The two 3.0 batches that completed deleted their staged files after their
  loads: after the DAG run the bucket held only `code/` and that one old prefix. The first runtime
  2.3 batch (a diagnostic one at 05:03 UTC, before the stand-in runs; `spark/README.md`) made
  Dataproc create two buckets of its own in the project,
  `dataproc-staging-us-central1-<project number>-...` (driver output; no lifecycle rule) and
  `dataproc-temp-us-central1-<project number>-...`; runtime 3.0 uses neither, and nothing in the repo
  runs 2.3. Cleaning up is the owner's call: delete that prefix, add a lifecycle rule to the Spark
  bucket (delete objects under `.spark-bigquery-` after a day), and delete the two `dataproc-*`
  buckets.
- **Only `airflow dags test` was run**, not a scheduler-driven run: the local scheduler would have
  started the most recent 10:00 UTC run as soon as the DAG was unpaused (see above). The components
  a scheduled run adds (LocalExecutor, the execution API behind the JWT secret) came up healthy but
  were not exercised by a task.
- **No alerting.** A failed run is red in the UI and nowhere else; alerting is Stage 5.
- **The export day is a UTC date**, not the property's time zone.
