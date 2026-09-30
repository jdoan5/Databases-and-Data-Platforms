"""
### tagline_daily

Every day: wait for the site's GA4 export (only when one is configured), bring the Stage 2 BigQuery tables
up to date (the Stage 5 monitoring marts included), run the Stage 2 data checks, run the KPI and tag-health
anomaly rules and send what is new (Stage 5), run the Stage 3 multi-touch attribution job on Dataproc Serverless,
and check its output against Stage 2.

```
check_ga4_export ─┬─► wait_for_ga4_export ─┬─► prepare_sources ─► build_mode
                  └─► no_ga4_export ───────┘
build_mode ─┬─► stage2_incremental ─────────────────────────────────────────────────────────────────┐   (the default)
            └─► stg_events ─┬─► stg_items ─────────────────────────────────────┐                     │   (full_refresh)
                            ├─► int_purchases ──────────────────► fct_orders ──┴─► fct_order_items ──┤
                            └─► int_device_days ─► int_identity ─► fct_sessions ─► fct_orders         │
                                                                   fct_sessions ─► mart_campaign_daily┤
                                                                   fct_sessions ─► mart_funnel_daily ─┤
                                                     fct_sessions, fct_orders ─► mart_kpi_daily ──────┤
                                                                   fct_sessions ─► mart_tag_health_daily ┴─► stage2_checks (11)
stage2_checks ─┬─► detect_anomalies ─► notify_alerts ──────────────────────────────┬─► run_summary
               └─► attribution_enabled ─► spark_attribution ─► attribution_checks (3) ┘
```
(stg_events also feeds the two monitoring marts.)
By default a run is the daily incremental build (`stage2_incremental`: the export days that are new or may
have changed, applied to every table in one BigQuery script and one transaction; Stage 4). With the run
parameter `{"full_refresh": true}` it rebuilds every model from every export day instead, one task per model
(stg_events also feeds fct_sessions, and int_identity feeds fct_orders: the edges are read from each model's
SQL). The GA4 sensor runs only when TAGLINE_GA4_DATASET is set, and soft-fails to skipped after 8 hours, so
the build still runs on the export days that did arrive.

Every BigQuery job carries maximumBytesBilled (TAGLINE_MAX_BYTES_BILLED, 10 GB by default) and the
Stage 2 labels; the Dataproc batch has a 30-minute TTL and app=tagline / stage=3 labels.

The DAG is created paused. Unpausing it (in any way, including the trigger form's "unpause" option)
starts the latest 10:00 UTC run straight away with the default `attribution: true`, so a Stage 2
rebuild and a Spark batch. A run triggered while it is paused waits, queued, until it is unpaused.
To rebuild and check Stage 2 only, leave it paused and run
`make airflow-test AIRFLOW_CONF='{"attribution": false}'` (airflow dags test ignores the pause).

Alerts (Stage 5): `detect_anomalies` replaces tagline_marts.kpi_alerts from the monitoring marts and
pipeline/monitoring.toml (the export day the run waited for is the last day data is due: an export that never came
is a no_data alert); `notify_alerts` sends the alerts that are news on that day, headed by any task of the run that
failed (TAGLINE_ALERT_WEBHOOK_URL, else the task log), and run_summary reports them. Both run once the checks are
done, passed or not, so a failed check (an email in the data, say) reaches the webhook too. By default an alert does
not fail the run; `{"fail_on_alert": true}` makes notify_alerts fail when it sends any alert. A webhook that fails
fails notify_alerts after its retries, and so the run. See tagline/docs/orchestration.md and
tagline/docs/monitoring.md.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Param, PokeReturnValue, TaskGroup, task

from tagline_airflow import dataproc, settings, stage2

try:  # Airflow 3 task SDK
    from airflow.sdk.exceptions import AirflowFailException
except ImportError:  # pragma: no cover - Airflow 2
    from airflow.exceptions import AirflowFailException
from tagline_airflow.operators import AttributionBatchOperator, NoRowsCheckOperator, Stage2ModelOperator

ATTRIBUTION_CHECKS_DIR = Path(__file__).parent / "tagline_airflow" / "sql" / "attribution_checks"

cfg = settings.pipeline_config()
spark = settings.spark_settings()

default_args = {
    "owner": "tagline",
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
    "execution_timeout": timedelta(minutes=20),
}


def bigquery_client():
    """A google.cloud.bigquery.Client from the Airflow connection (ADC locally, the environment's
    service account on Composer)."""
    return BigQueryHook(gcp_conn_id=settings.GCP_CONN_ID, location=cfg.location).get_client(
        project_id=cfg.project, location=cfg.location
    )


with DAG(
    dag_id="tagline_daily",
    description="GA4 export -> Stage 2 BigQuery models and checks -> Stage 3 attribution on Dataproc -> checks",
    doc_md=__doc__,
    schedule="0 10 * * *",  # 10:00 UTC; the run waits for the export of the day before
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,
    # Created paused wherever it is deployed, not only where the environment says so: the first
    # unpause starts the latest 10:00 UTC run at once, Spark batch included.
    is_paused_upon_creation=True,
    max_active_runs=1,
    dagrun_timeout=timedelta(hours=10),
    default_args=default_args,
    params={
        "attribution": Param(
            True,
            type="boolean",
            description="Run the Spark attribution job and its checks after the Stage 2 build.",
        ),
        "full_refresh": Param(
            False,
            type="boolean",
            description="Rebuild every Stage 2 table from every export day (one task per model) instead of the daily "
            "incremental build, which processes only the export days that are new or may have changed.",
        ),
        "fail_on_alert": Param(
            False,
            type="boolean",
            description="Fail the run when it sends any KPI or tag-health alert (after sending it). Alerts sent by an "
            "earlier run do not count. Off by default: alert and continue.",
        ),
    },
    user_defined_macros={"attribution_batch_id": dataproc.batch_id},
    tags=["tagline", "stage-2", "stage-3", "stage-5", "bigquery", "dataproc"],
) as dag:
    # 1. The site's GA4 export: wait for the day's table only when a dataset is configured.

    @task.branch(task_id="check_ga4_export")
    def check_ga4_export() -> str:
        return "wait_for_ga4_export" if settings.pipeline_config().has_site else "no_ga4_export"

    @task.sensor(
        task_id="wait_for_ga4_export",
        mode="reschedule",  # frees the worker slot between pokes
        poke_interval=15 * 60,
        timeout=8 * 60 * 60,  # give up at 18:00 UTC ...
        soft_fail=True,  # ... as skipped, not failed: the build then runs on the days that did arrive
        execution_timeout=timedelta(minutes=5),  # per poke
    )
    def wait_for_ga4_export(**context) -> PokeReturnValue:
        run_cfg = settings.pipeline_config()
        day = stage2.export_day(stage2.run_moment(context))  # a manual run may have no logical date
        hook = BigQueryHook(gcp_conn_id=settings.GCP_CONN_ID)
        found = [
            table
            for table in stage2.export_tables(day, run_cfg.ga4_table_prefix)
            if hook.table_exists(project_id=run_cfg.site_project, dataset_id=run_cfg.ga4_dataset, table_id=table)
        ]
        print(f"{run_cfg.site_project}.{run_cfg.ga4_dataset} for {day}: {', '.join(found) or 'no export table yet'}")
        return PokeReturnValue(is_done=bool(found), xcom_value=found[0] if found else None)

    no_ga4_export = EmptyOperator(task_id="no_ga4_export")

    # 2. What `make build` does before the first model; the site tables go to the models as an XCom.

    @task(trigger_rule="none_failed")
    def prepare_sources() -> dict | None:
        return stage2.prepare_sources(settings.pipeline_config(), bigquery_client())

    sources = prepare_sources()
    check_ga4_export() >> [wait_for_ga4_export(), no_ga4_export] >> sources

    # 3. The Stage 2 tables: the daily incremental build (one task, one BigQuery script), or with full_refresh one
    #    task per model, wired from the tables each model's SQL reads.

    deps = stage2.model_dependencies(stage2.MODELS)
    root_models = [name for name, upstream in deps.items() if not upstream]

    @task.branch(task_id="build_mode")
    def build_mode(params: dict | None = None) -> str | list[str]:
        return root_models if (params or {}).get("full_refresh", False) else "stage2_incremental"

    @task(task_id="stage2_incremental")
    def stage2_incremental() -> list[dict]:
        """The export days that are new or may have changed (the 4 up to the site's newest daily table, any whose export
        table no longer matches what was recorded when it was staged, and any not loaded yet), applied to every table,
        and to that record, in one BigQuery script and one transaction (tagline_pipeline/incremental.py). A failed script
        changes nothing, so a retry starts from the same tables; the script replaces what it writes, so running it again
        leaves the same tables."""
        from tagline_pipeline.incremental import WindowError

        try:
            return stage2.run_incremental(settings.pipeline_config(), bigquery_client())
        except (WindowError, SystemExit) as e:  # no tables yet (run with full_refresh), or an impossible window
            raise AirflowFailException(f"incremental build: {e}") from None

    mode = build_mode()
    incremental = stage2_incremental()
    sources >> mode >> incremental
    models = {
        m.name: Stage2ModelOperator(task_id=m.name, model=m, project_id=cfg.project, location=cfg.location)
        for m in stage2.MODELS
    }
    for name, upstream in deps.items():
        if upstream:
            for up in upstream:
                models[up] >> models[name]
        else:
            mode >> models[name]

    # 4. Stage 2's data checks: every one must pass before Spark reads the tables.

    with TaskGroup("stage2_checks", tooltip="Stage 2 data checks (pipeline/sql/checks)") as stage2_checks:
        stage2_check_tasks = [
            NoRowsCheckOperator(
                task_id=stage2.check_task_id(path),
                check_path=path,
                project_id=cfg.project,
                location=cfg.location,
                execution_timeout=timedelta(minutes=10),
                # after whichever build ran: the other branch is skipped, and a failed build still stops the checks
                trigger_rule="none_failed_min_one_success",
            )
            for path in stage2.CHECKS
        ]
    [models[name] for name in stage2.leaf_models(deps)] >> stage2_checks
    incremental >> stage2_checks

    # 5. Stage 5: the anomaly rules on the monitoring marts, and delivery. Once the checks are done, passed or not: the
    #    day a check fails (an email in the data, a broken build) is the day the webhook matters most, so the message
    #    names the failed tasks first. Neither task runs a query (table-data reads, load-job writes).

    @task(task_id="detect_anomalies", trigger_rule="all_done")
    def detect_anomalies(**context) -> dict:
        """kpi_alerts from the marts as they are; the export day this run waited for is the last day data is due."""
        day = stage2.export_day(stage2.run_moment(context))
        return stage2.run_detect(settings.pipeline_config(), bigquery_client(), as_of=day)

    @task(task_id="notify_alerts", trigger_rule="all_done")
    def notify_alerts(params: dict | None = None, **context) -> dict:
        """Send the alerts that are news on the export day this run waited for, headed by the run's failed tasks; with
        fail_on_alert, then fail if any alert was sent."""
        day = stage2.export_day(stage2.run_moment(context))
        problems = stage2.failed_so_far(context["ti"]) if context.get("ti") is not None else []
        summary = stage2.run_notify(settings.pipeline_config(), bigquery_client(), day, problems)
        print(f"alerts as of {day}: {summary['fresh']} news, {summary['new']} sent now ({summary['delivery']}), "
              f"{summary['critical']} critical; failed tasks reported: {', '.join(problems) or 'none'}")
        failure = stage2.alert_failure(summary, bool((params or {}).get("fail_on_alert", False)))
        if failure:
            if context.get("ti") is not None:  # so run_summary still reports what was sent
                context["ti"].xcom_push(key="return_value", value=summary)
            raise AirflowFailException(failure)
        return summary

    alerts_sent = notify_alerts()
    stage2_checks >> detect_anomalies() >> alerts_sent

    # 6. Stage 3: multi-touch attribution on Dataproc Serverless.

    @task.short_circuit(ignore_downstream_trigger_rules=False)
    def attribution_enabled(params: dict | None = None) -> bool:
        return bool((params or {}).get("attribution", True))

    spark_attribution = AttributionBatchOperator(
        task_id="spark_attribution",
        gcp_conn_id=settings.GCP_CONN_ID,
        project_id=cfg.project,
        region=spark.region,
        batch=dataproc.attribution_batch(cfg, spark),
        batch_id="{{ attribution_batch_id(run_id, ti.try_number, ti.id) }}",
        retries=1,
        # Longer than the batch TTL (30 min), so Dataproc stops a stuck batch first. A try that ends while
        # its batch may still run (this timeout, any error while waiting, a Ctrl-C in `make airflow-test`)
        # cancels the batch before the retry starts a new one (AttributionBatchOperator). A worker that dies
        # outright (container killed, Docker quit) cancels nothing: its batch runs on until it ends or
        # reaches the TTL, and the retry starts a second one. `make airflow-orphans` lists such batches
        # (CANCEL=1 cancels them); run it before `make airflow-up` after a crash.
        execution_timeout=timedelta(minutes=45),
    )

    with TaskGroup("attribution_checks", tooltip="Checks on the Spark output") as attribution_checks:
        attribution_check_tasks = [
            NoRowsCheckOperator(
                task_id=stage2.check_task_id(path),
                check_path=path,
                stage="3",
                sources_task_id=None,
                project_id=cfg.project,
                location=cfg.location,
                execution_timeout=timedelta(minutes=10),
            )
            for path in sorted(ATTRIBUTION_CHECKS_DIR.glob("*.sql"))
        ]

    stage2_checks >> attribution_enabled() >> spark_attribution >> attribution_checks

    # 7. What the run cost, whatever happened. run_summary is the only leaf task, so it also decides the
    #    run's final state: it fails when any task failed, or a check failure would end in a green run.

    bigquery_task_ids = [
        *models,
        *(t.task_id for t in stage2_check_tasks),
        *(t.task_id for t in attribution_check_tasks),
    ]

    @task(trigger_rule="all_done", retries=0)
    def run_summary(**context) -> dict:
        from collections import Counter

        from google.cloud.dataproc_v1 import Batch

        from airflow.providers.google.cloud.hooks.dataproc import DataprocHook
        from tagline_airflow.summary import failed_tasks, run_states, settle_usage, summarize

        def fetch_batch(name: str) -> dict:
            hook = DataprocHook(gcp_conn_id=settings.GCP_CONN_ID)
            return Batch.to_dict(hook.get_batch(batch_id=name.rsplit("/", 1)[-1], region=spark.region, project_id=cfg.project))

        ti = context["ti"]
        stats = [ti.xcom_pull(task_ids=task_id) for task_id in bigquery_task_ids]
        stats += ti.xcom_pull(task_ids="stage2_incremental") or []  # the incremental script's statements
        # The operator's XCom is the batch as it ended, before Dataproc reports its usage: re-read it (at most 3 min).
        batch = settle_usage(ti.xcom_pull(task_ids="spark_attribution"), fetch_batch)
        result = summarize([s for s in stats if s], batch)
        alerts = ti.xcom_pull(task_ids="notify_alerts")
        if alerts:
            print(f"Alerts (Stage 5) as of {alerts['as_of']}: {alerts['fresh']} news, {alerts['new']} sent by this run "
                  f"(delivery: {alerts['delivery']}), {alerts['critical']} critical")
            for line in alerts.get("lines", [])[1:]:
                print("  " + line)
        else:
            print("Alerts (Stage 5): notify_alerts did not report (skipped or failed)")
        result["alerts"] = alerts
        states = run_states(ti.get_task_states(dag_id=ti.dag_id, run_ids=[ti.run_id]), ti.run_id)
        states.pop(ti.task_id, None)
        print("task states:", ", ".join(f"{state} {n}" for state, n in sorted(Counter(states.values()).items())))
        failed = failed_tasks(states)
        if failed:
            raise RuntimeError(f"{len(failed)} task(s) failed or did not run because an upstream failed: {', '.join(failed)}")
        return result

    summary_task = run_summary()
    attribution_checks >> summary_task
    alerts_sent >> summary_task
