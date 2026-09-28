"""DagBag import test for tagline_daily, plus the DAG's pure helpers. No Google Cloud calls.

Runs in the Airflow container (make airflow-check), where the DAG's imports and environment exist:

    python /opt/airflow/tests/test_dag.py      # the image has no pytest; this file runs itself
    pytest /opt/airflow/tests                  # where pytest is installed

Checks: the DAG imports without errors; the task ids and every dependency are the expected ones
(the model edges are the lineage in docs/data-model.md, read here independently of the DAG's own
SQL parsing); the run-level settings (created paused, catchup, max_active_runs, retries, timeouts,
the sensor's bounds); every BigQuery job the DAG can submit carries maximumBytesBilled; the Dataproc
batch has a TTL, labels and the service account, and its task cancels the batch whenever a try ends
early (any exception, a Ctrl-C), not only on Airflow's timeout; batch ids are valid, unique per run and
try, and stable; the sensor works on a run with no logical date (a manual trigger); run_summary names the
batch state that the operator's XCom stores as an integer, waits (boundedly) for the batch's usage,
which Dataproc reports only after the XCom is taken, and prices it as `make spark-submit` does.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DAGS_DIR = Path(os.environ.get("TAGLINE_DAGS_DIR", Path(__file__).resolve().parents[1] / "dags"))
if str(DAGS_DIR) not in sys.path:
    sys.path.insert(0, str(DAGS_DIR))

DAG_ID = "tagline_daily"

MODEL_EDGES = {  # docs/data-model.md, "Lineage"
    ("stg_events", "stg_items"),
    ("stg_events", "int_identity"),
    ("stg_events", "fct_sessions"),
    ("int_identity", "fct_sessions"),
    ("stg_events", "fct_orders"),
    ("int_identity", "fct_orders"),
    ("fct_sessions", "fct_orders"),
    ("fct_orders", "fct_order_items"),
    ("stg_items", "fct_order_items"),
    ("fct_sessions", "mart_campaign_daily"),
    ("fct_sessions", "mart_funnel_daily"),
}
MODELS = [
    "stg_events", "stg_items", "int_identity", "fct_sessions",
    "fct_orders", "fct_order_items", "mart_campaign_daily", "mart_funnel_daily",
]
STAGE2_CHECKS = [
    "keys_unique", "orders_have_session_and_person", "order_revenue_reconciles", "marts_reconcile",
    "sample_row_counts", "sessions_cover_events", "identity", "no_email_like_strings", "synthetic_is_labelled",
]
ATTRIBUTION_CHECKS = ["weights_sum_to_one", "revenue_conserved", "last_click_matches_stage2"]
LEAF_MODELS = {"fct_order_items", "mart_campaign_daily", "mart_funnel_daily"}

_dag = None


def load_dag():
    global _dag
    if _dag is None:
        try:
            from airflow.dag_processing.dagbag import DagBag  # examples are off: AIRFLOW__CORE__LOAD_EXAMPLES
        except ImportError:  # Airflow < 3.1
            from airflow.models.dagbag import DagBag
        bag = DagBag(dag_folder=str(DAGS_DIR))
        assert not bag.import_errors, f"import errors: {bag.import_errors}"
        assert DAG_ID in bag.dags, f"{DAG_ID} not found; DAGs: {sorted(bag.dags)}"
        _dag = bag.dags[DAG_ID]
    return _dag


def edges(dag) -> set[tuple[str, str]]:
    return {(t.task_id, d) for t in dag.tasks for d in t.downstream_task_ids}


# -- the DagBag ----------------------------------------------------------------------------------


def test_dag_imports_without_errors():
    load_dag()


def test_task_ids():
    expected = {
        "check_ga4_export", "wait_for_ga4_export", "no_ga4_export", "prepare_sources",
        *MODELS,
        *(f"stage2_checks.{c}" for c in STAGE2_CHECKS),
        "attribution_enabled", "spark_attribution",
        *(f"attribution_checks.{c}" for c in ATTRIBUTION_CHECKS),
        "run_summary",
    }
    assert set(load_dag().task_ids) == expected, set(load_dag().task_ids) ^ expected


def test_dependencies():
    dag = load_dag()
    expected = {
        ("check_ga4_export", "wait_for_ga4_export"),
        ("check_ga4_export", "no_ga4_export"),
        ("wait_for_ga4_export", "prepare_sources"),
        ("no_ga4_export", "prepare_sources"),
        ("prepare_sources", "stg_events"),
        *MODEL_EDGES,
        *((m, f"stage2_checks.{c}") for m in LEAF_MODELS for c in STAGE2_CHECKS),
        *((f"stage2_checks.{c}", "attribution_enabled") for c in STAGE2_CHECKS),
        ("attribution_enabled", "spark_attribution"),
        *(("spark_attribution", f"attribution_checks.{c}") for c in ATTRIBUTION_CHECKS),
        *((f"attribution_checks.{c}", "run_summary") for c in ATTRIBUTION_CHECKS),
    }
    actual = edges(dag)
    assert actual == expected, f"missing: {sorted(expected - actual)}; unexpected: {sorted(actual - expected)}"


def test_run_settings():
    dag = load_dag()
    assert dag.catchup is False
    assert dag.is_paused_upon_creation is True, "the first unpause starts a run with a Spark batch: create it paused"
    assert dag.max_active_runs == 1
    assert dag.dagrun_timeout is not None and dag.dagrun_timeout <= timedelta(hours=12)
    for t in dag.tasks:
        assert t.execution_timeout is not None, f"{t.task_id} has no execution_timeout"
        if t.task_id != "run_summary":
            assert t.retries >= 1, f"{t.task_id} does not retry"
    for t in dag.tasks:
        if t.task_id not in ("run_summary",):
            assert t.retry_exponential_backoff, f"{t.task_id} retries without backoff"
    sensor = dag.get_task("wait_for_ga4_export")
    assert sensor.soft_fail is True
    assert sensor.mode == "reschedule"
    assert 0 < sensor.timeout <= 12 * 3600
    assert dag.get_task("prepare_sources").trigger_rule == "none_failed"
    assert dag.get_task("run_summary").trigger_rule == "all_done"
    assert [t.task_id for t in dag.tasks if not t.downstream_task_ids] == ["run_summary"], "run_summary must be the only leaf"


def test_every_bigquery_job_has_the_cost_guard():
    from tagline_airflow import settings
    from tagline_airflow.operators import TaglineQueryOperator
    from tagline_pipeline import pipeline
    from tagline_pipeline.sources import SiteTables
    from tagline_pipeline.sqlfiles import render

    cfg = settings.pipeline_config()
    site = SiteTables(daily=("20260926", "20260927"), intraday_only=("20260928",))
    ops = [t for t in load_dag().tasks if isinstance(t, TaglineQueryOperator)]
    assert len(ops) == len(MODELS) + len(STAGE2_CHECKS) + len(ATTRIBUTION_CHECKS)
    for op in ops:
        sql = render(Path(op.sql_path).read_text(encoding="utf-8"), pipeline.context(cfg, site))
        assert "{{" not in sql and "}}" not in sql, op.task_id
        conf = op.build_configuration(sql, cfg.max_bytes_billed)
        assert int(conf["query"]["maximumBytesBilled"]) == cfg.max_bytes_billed > 0, op.task_id
        assert conf["query"]["useQueryCache"] is False and conf["query"]["useLegacySql"] is False
        assert conf["labels"]["app"] == "tagline" and conf["labels"]["kind"] in ("model", "check")
        assert conf["labels"]["stage"] == ("3" if op.task_id.startswith("attribution_checks.") else "2")
        assert op.durable is False, op.task_id
        assert op.gcp_conn_id == settings.GCP_CONN_ID


def test_dataproc_batch():
    from tagline_airflow import dataproc, settings

    op = load_dag().get_task("spark_attribution")
    spark = settings.spark_settings()
    batch = op.batch
    execution = batch["environment_config"]["execution_config"]
    assert execution["ttl"] == {"seconds": dataproc.BATCH_TTL_SECONDS} and dataproc.BATCH_TTL_SECONDS <= 3600
    assert execution["service_account"] == spark.service_account
    assert execution["subnetwork_uri"] == "default"
    assert batch["labels"]["app"] == "tagline" and batch["labels"]["stage"] == "3"
    assert batch["runtime_config"]["version"]
    assert batch["pyspark_batch"]["main_python_file_uri"].startswith(f"gs://{spark.bucket}/code/")
    assert op.region == spark.region
    assert op.execution_timeout > timedelta(seconds=dataproc.BATCH_TTL_SECONDS)
    assert "attribution_batch_id(run_id, ti.try_number, ti.id)" in op.batch_id
    # One definition: the DAG submits exactly what `make spark-submit` does, bar the orchestrator label.
    from tagline_spark import batch as spark_batch

    cfg = settings.pipeline_config()
    make_batch = spark_batch.batch(dataproc.target(cfg, spark), orchestrator="make")
    assert batch == {**make_batch, "labels": {**make_batch["labels"], "orchestrator": "airflow"}}
    assert batch["labels"]["code"] == spark_batch.code_version(), "the code version is hashed from the mounted sources"


def test_the_batch_task_cancels_its_batch_when_a_try_ends_early():
    """The stock operator cancels only in on_kill (timeout, SIGTERM). Any other exception while it waits, or a
    Ctrl-C, must cancel the batch too, or the retry starts a second batch beside the first."""
    from unittest import mock

    from airflow.providers.google.cloud.operators.dataproc import DataprocCreateBatchOperator
    from airflow.sdk.exceptions import TaskDeferred
    from tagline_airflow.operators import AttributionBatchOperator

    assert isinstance(load_dag().get_task("spark_attribution"), AttributionBatchOperator)

    class Operation:
        def __init__(self, done=False, broken=False):
            self.done, self.broken, self.cancels = done, broken, 0

        def cancel(self):
            if self.broken:
                raise ConnectionError("network down")
            if self.done:
                return False
            self.cancels += 1
            return True

    def run(error=None, operation=None):
        op = AttributionBatchOperator(task_id="t", batch={}, region="us-central1", project_id="p", batch_id="b")

        def execute(self, context):
            self.operation = operation
            if error is not None:
                raise error
            return {"state": "SUCCEEDED"}

        quiet = mock.patch.object(AttributionBatchOperator, "log", mock.MagicMock())  # the expected cancel errors
        with mock.patch.object(DataprocCreateBatchOperator, "execute", execute), quiet:
            try:
                return op, op.execute({}), None
            except BaseException as e:  # noqa: BLE001
                return op, None, e

    for error in (RuntimeError("token refresh failed"), KeyboardInterrupt()):
        operation = Operation()
        op, _, raised = run(error, operation)
        assert raised is error and operation.cancels == 1, f"{type(error).__name__}: the batch must be cancelled"
        op.on_kill()  # Airflow's timeout path after the same exception: no second cancel
        assert operation.cancels == 1
    operation = Operation()
    _, result, raised = run(None, operation)
    assert result == {"state": "SUCCEEDED"} and raised is None and operation.cancels == 0
    operation = Operation(done=True)  # the batch had ended (FAILED at creation, say): nothing to cancel
    _, _, raised = run(RuntimeError("Batch job failed"), operation)
    assert isinstance(raised, RuntimeError) and operation.cancels == 0
    error = RuntimeError("wait failed")
    _, _, raised = run(error, Operation(broken=True))
    assert raised is error, "a failed cancel must not replace the error that ended the try"
    deferred = TaskDeferred(trigger=None, method_name="execute_complete")
    operation = Operation()
    _, _, raised = run(deferred, operation)
    assert raised is deferred and operation.cancels == 0, "deferring is not an exit"
    _, _, raised = run(KeyboardInterrupt(), None)  # AlreadyExists path: attached, created nothing
    assert isinstance(raised, KeyboardInterrupt)


# -- pure helpers ----------------------------------------------------------------------------------


def test_batch_id():
    from tagline_airflow.dataproc import batch_id

    import uuid

    run = "scheduled__2026-09-28T10:00:00+00:00"
    ti_1, ti_2, ti_after_reset = (uuid.UUID(int=n) for n in (1, 2, 3))  # Airflow 3: a new UUID7 per try
    first = batch_id(run, 1, ti_1)
    assert first == batch_id(run, 1, ti_1), "stable within a try"
    assert first.startswith("tagline-attr-20260928-") and "-t1-" in first
    assert batch_id(run, 2, ti_2) != first, "a retry gets its own batch"
    assert batch_id(run, 1, ti_after_reset) != first, "a wiped metadata DB repeats run id and try, not the task instance id"
    assert batch_id("manual__2026-09-28T10:00:00+00:00", 1, ti_1) != first, "another run gets its own batch"
    assert batch_id("manual__2026-09-28T10:05:12.123456+00:00", 1, ti_1) != batch_id("manual__2026-09-28T10:05:13+00:00", 1, ti_1)
    for value in (first, batch_id("a weird run id with no date!", 10, ti_2)):
        assert 4 <= len(value) <= 63 and value == value.lower() and all(c.isalnum() or c == "-" for c in value)


def test_model_dependencies_read_from_sql():
    from tagline_airflow import stage2

    deps = stage2.model_dependencies(stage2.MODELS)
    assert list(deps) == MODELS
    assert {(up, name) for name, ups in deps.items() for up in ups} == MODEL_EDGES
    assert set(stage2.leaf_models(deps)) == LEAF_MODELS


def test_export_day_and_tables():
    from tagline_airflow import stage2

    run = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
    assert stage2.export_day(run) == date(2026, 9, 27)
    assert stage2.export_tables(date(2026, 9, 27)) == ("events_20260927", "events_intraday_20260927")
    assert stage2.export_tables(date(2026, 1, 2), "fake_ga4_events_") == ("fake_ga4_events_20260102", "fake_ga4_events_intraday_20260102")


def test_run_moment_without_a_logical_date():
    from types import SimpleNamespace

    from tagline_airflow import stage2

    scheduled = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
    triggered = datetime(2026, 9, 30, 15, 30, tzinfo=timezone.utc)
    run = SimpleNamespace(run_after=triggered)
    assert stage2.run_moment({"logical_date": scheduled, "dag_run": run}) == scheduled
    # Airflow 3 leaves the key out for a run triggered with no logical date (and it may be None).
    assert stage2.run_moment({"dag_run": run}) == triggered
    assert stage2.run_moment({"logical_date": None, "dag_run": run}) == triggered


def test_sensor_pokes_without_a_logical_date():
    """The real sensor callable, on a manual run's context (no logical_date), with a fake BigQuery hook."""
    from types import SimpleNamespace

    from tagline_airflow import settings, stage2

    fn = load_dag().get_task("wait_for_ga4_export").python_callable
    prefix = settings.pipeline_config().ga4_table_prefix
    asked: list[str] = []

    class FakeHook:
        def __init__(self, **kwargs):
            pass

        def table_exists(self, project_id, dataset_id, table_id):
            asked.append(table_id)
            return table_id == f"{prefix}intraday_20260929"  # only the streaming table exists

    run = SimpleNamespace(run_after=datetime(2026, 9, 30, 15, 30, tzinfo=timezone.utc))
    saved = fn.__globals__["BigQueryHook"]
    fn.__globals__["BigQueryHook"] = FakeHook
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            manual = fn(dag_run=run)
            scheduled = fn(dag_run=run, logical_date=datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc))
    finally:
        fn.__globals__["BigQueryHook"] = saved
    assert asked[:2] == list(stage2.export_tables(date(2026, 9, 29), prefix)), asked
    assert manual.is_done and manual.xcom_value == f"{prefix}intraday_20260929"
    assert asked[2:] == list(stage2.export_tables(date(2026, 9, 27), prefix)), asked
    assert not scheduled.is_done and scheduled.xcom_value is None


def test_site_xcom_round_trip():
    from tagline_airflow import stage2
    from tagline_pipeline.sources import SiteTables

    site = SiteTables(daily=("20260926",), intraday_only=("20260927",))
    assert stage2.site_from_xcom(stage2.site_to_xcom(site)) == site
    assert stage2.site_to_xcom(None) is None and stage2.site_from_xcom(None) is None


def test_summary_helpers():
    from tagline_airflow.summary import batch_usage, failed_tasks, run_states, summarize

    answer = {"r1": {"a": "success", "b": "failed", "c": "TaskInstanceState.UPSTREAM_FAILED", "d": "skipped", "e": None}}
    states = run_states(answer, "r1")
    assert failed_tasks(states) == ["b", "c"]
    assert failed_tasks(run_states({"r2": {"a": "success", "d": "skipped"}}, "r2")) == []
    try:
        run_states(answer, "other")
    except RuntimeError:
        pass
    else:
        raise AssertionError("a run with no states must not pass as 'nothing failed'")
    usage = batch_usage({
        "name": "projects/p/locations/us-central1/batches/tagline-attr-20260928-abc12345-t1",
        "state": "SUCCEEDED",
        "create_time": "2026-09-28T10:10:00Z",
        "state_time": "2026-09-28T10:14:30.5Z",
        "runtime_info": {"approximate_usage": {"milli_dcu_seconds": "3600000", "shuffle_storage_gb_seconds": "7200"}},
    })
    assert usage["batch"] == "tagline-attr-20260928-abc12345-t1"
    assert usage["wall_seconds"] == 270.5 and usage["dcu_hours"] == 1.0 and usage["shuffle_storage_gb_hours"] == 2.0
    assert usage["state"] == "SUCCEEDED"
    from tagline_spark.submit import DCU_USD_PER_HOUR, SHUFFLE_USD_PER_GIB_HOUR

    assert usage["usd_estimate"] == round(DCU_USD_PER_HOUR + 2 * SHUFFLE_USD_PER_GIB_HOUR, 4), "priced as make spark-submit prices it"
    with contextlib.redirect_stdout(io.StringIO()):
        result = summarize([{"step": "stg_events", "kind": "model", "bytes_billed": 1024**4, "seconds": 2.0}], None)
    assert result["bigquery_usd_estimate"] == 6.25 and result["dataproc"] is None


def test_batch_state_from_the_operators_xcom():
    """The operator's XCom is Batch.to_dict(batch), which writes the state as an integer."""
    from google.cloud.dataproc_v1 import Batch

    from tagline_airflow.summary import batch_state, batch_usage, summarize

    for state in (Batch.State.SUCCEEDED, Batch.State.FAILED, Batch.State.CANCELLED):
        xcom = Batch.to_dict(Batch(name=f"projects/p/locations/r/batches/b-{state.value}", state=state))
        assert isinstance(xcom["state"], int), xcom["state"]
        assert batch_usage(xcom)["state"] == state.name
    assert batch_state("6") == "FAILED" and batch_state("State.RUNNING") == "RUNNING"
    assert batch_state(None) is None and batch_state(99) == "99"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        summarize([], Batch.to_dict(Batch(name="projects/p/locations/r/batches/b1", state=Batch.State.FAILED)))
    assert "batch b1 FAILED" in out.getvalue(), out.getvalue()


def test_run_summary_waits_for_the_batch_usage():
    """The operator's XCom is taken as the batch ends, before Dataproc reports its usage (the first full run
    printed 0 DCU-hours): run_summary re-reads the batch until the usage is there, within a bound."""
    from google.cloud.dataproc_v1 import Batch

    from tagline_airflow.summary import batch_usage, settle_usage

    name = "projects/p/locations/r/batches/tagline-attr-20260928-ca4371b6-t1-98ee8f"
    history = [{"state": Batch.State.PENDING}, {"state": Batch.State.RUNNING}]
    ended = Batch.to_dict(Batch(name=name, state=Batch.State.SUCCEEDED, state_history=history))
    reported = Batch.to_dict(Batch(
        name=name, state=Batch.State.SUCCEEDED, state_history=history,
        runtime_info={"approximate_usage": {"milli_dcu_seconds": 2144850, "shuffle_storage_gb_seconds": 135750}},
    ))
    reads, clock = [], [0.0]

    def fetch(n):
        reads.append(n)
        return ended if len(reads) < 3 else reported

    def sleep(seconds):
        clock[0] += seconds

    settled = settle_usage(ended, fetch, sleep=sleep, clock=lambda: clock[0])
    assert reads == [name] * 3 and abs(batch_usage(settled)["dcu_hours"] - 0.5958) < 1e-4
    assert settle_usage(reported, fetch) is reported, "no re-read when the XCom already has the usage"
    assert settle_usage(None, fetch) is None
    # Bounded: usage that never comes gives up at the timeout and reports what it has.
    reads.clear()
    clock[0] = 0.0
    never = settle_usage(ended, lambda n: reads.append(n) or ended, timeout_seconds=30, poll_seconds=10, sleep=sleep, clock=lambda: clock[0])
    assert never == ended and len(reads) == 4
    # A batch that failed at creation never ran: one re-read, no waiting.
    reads.clear()
    failed = Batch.to_dict(Batch(name=name, state=Batch.State.FAILED, state_history=[{"state": Batch.State.PENDING}]))
    assert settle_usage(failed, lambda n: reads.append(n) or failed, sleep=sleep) == failed and len(reads) == 1
    # A re-read that raises keeps the XCom: a report never fails the run.

    def broken(n):
        raise RuntimeError("permission denied")

    with contextlib.redirect_stdout(io.StringIO()):
        assert settle_usage(ended, broken) is ended


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items(), key=lambda kv: kv[1].__code__.co_firstlineno if callable(kv[1]) and hasattr(kv[1], "__code__") else 0) if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
