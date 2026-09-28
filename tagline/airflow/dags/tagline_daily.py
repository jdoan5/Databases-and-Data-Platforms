"""
### tagline_daily

Every day: wait for the site's GA4 export (only when one is configured), rebuild the Stage 2 BigQuery
models in lineage order, run the Stage 2 data checks, run the Stage 3 multi-touch attribution job on
Dataproc Serverless, and check its output against Stage 2.

```
check_ga4_export ─┬─► wait_for_ga4_export ─┬─► prepare_sources
                  └─► no_ga4_export ───────┘
prepare_sources ─► stg_events ─┬─► stg_items ──────────────────────────────────┐
                               └─► int_identity ─► fct_sessions ─► fct_orders ─┴─► fct_order_items ──┐
                                                   fct_sessions ─► mart_campaign_daily ──────────────┤
                                                   fct_sessions ─► mart_funnel_daily ────────────────┴─► stage2_checks (9)
stage2_checks ─► attribution_enabled ─► spark_attribution ─► attribution_checks (3) ─► run_summary
```
(stg_events also feeds fct_sessions and fct_orders, and int_identity feeds fct_orders: the edges are read
from each model's SQL.) The GA4 sensor runs only when TAGLINE_GA4_DATASET is set, and soft-fails to
skipped after 8 hours, so the build still runs on the export days that did arrive.

Every BigQuery job carries maximumBytesBilled (TAGLINE_MAX_BYTES_BILLED, 10 GB by default) and the
Stage 2 labels; the Dataproc batch has a 30-minute TTL and app=tagline / stage=3 labels.

The DAG is created paused. Unpausing it (in any way, including the trigger form's "unpause" option)
starts the latest 10:00 UTC run straight away with the default `attribution: true`, so a Stage 2
rebuild and a Spark batch. A run triggered while it is paused waits, queued, until it is unpaused.
To rebuild and check Stage 2 only, leave it paused and run
`make airflow-test AIRFLOW_CONF='{"attribution": false}'` (airflow dags test ignores the pause).
See tagline/docs/orchestration.md.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Param, PokeReturnValue, TaskGroup, task

from tagline_airflow import dataproc, settings, stage2
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
    },
    user_defined_macros={"attribution_batch_id": dataproc.batch_id},
    tags=["tagline", "stage-2", "stage-3", "bigquery", "dataproc"],
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

    # 3. One task per Stage 2 model, wired from the tables each model's SQL reads.

    deps = stage2.model_dependencies(stage2.MODELS)
    models = {
        m.name: Stage2ModelOperator(task_id=m.name, model=m, project_id=cfg.project, location=cfg.location)
        for m in stage2.MODELS
    }
    for name, upstream in deps.items():
        if upstream:
            for up in upstream:
                models[up] >> models[name]
        else:
            sources >> models[name]

    # 4. Stage 2's data checks: every one must pass before Spark reads the tables.

    with TaskGroup("stage2_checks", tooltip="Stage 2 data checks (pipeline/sql/checks)") as stage2_checks:
        stage2_check_tasks = [
            NoRowsCheckOperator(
                task_id=stage2.check_task_id(path),
                check_path=path,
                project_id=cfg.project,
                location=cfg.location,
                execution_timeout=timedelta(minutes=10),
            )
            for path in stage2.CHECKS
        ]
    [models[name] for name in stage2.leaf_models(deps)] >> stage2_checks

    # 5. Stage 3: multi-touch attribution on Dataproc Serverless.

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

    # 6. What the run cost, whatever happened. run_summary is the only leaf task, so it also decides the
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
        # The operator's XCom is the batch as it ended, before Dataproc reports its usage: re-read it (at most 3 min).
        batch = settle_usage(ti.xcom_pull(task_ids="spark_attribution"), fetch_batch)
        result = summarize([s for s in stats if s], batch)
        states = run_states(ti.get_task_states(dag_id=ti.dag_id, run_ids=[ti.run_id]), ti.run_id)
        states.pop(ti.task_id, None)
        print("task states:", ", ".join(f"{state} {n}" for state, n in sorted(Counter(states.values()).items())))
        failed = failed_tasks(states)
        if failed:
            raise RuntimeError(f"{len(failed)} task(s) failed or did not run because an upstream failed: {', '.join(failed)}")
        return result

    attribution_checks >> run_summary()
