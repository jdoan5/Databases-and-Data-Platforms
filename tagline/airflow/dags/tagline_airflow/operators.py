"""BigQuery tasks that run a Stage 2-style SQL file through the Google provider's BigQueryInsertJobOperator.

Why subclass instead of passing a finished `configuration`: the SQL can only be rendered when the
task runs. stg_events unions the site export's tables, and which tables exist is looked up by the
`prepare_sources` task at run time (DAG files are parsed every few seconds and must not call
BigQuery). So each task reads its file, renders it with the Stage 2 package's `render` and
`context`, and then hands the job to the stock operator, which submits it with a deterministic
job id, waits, cancels it if the task is killed, and links it in the UI.

Every job gets what `make build` gives its jobs: `maximumBytesBilled` from TAGLINE_MAX_BYTES_BILLED
(a job that would bill more fails before it runs), no query cache (so the recorded cost is real),
and the same app / stage / kind / step labels, plus orchestrator=airflow; the provider adds
airflow-dag and airflow-task labels. Stage 4 can therefore find these jobs in
INFORMATION_SCHEMA.JOBS exactly as it finds the CLI's.

`durable=False`: a retry always runs its query again. Airflow 3.3's resumable operators would
otherwise reuse a previous try's finished job (and, unless the state store is cleared on success,
even after the task is cleared by hand), which is wrong for a check and surprising for a model.
The models are CREATE OR REPLACE, so running one twice is harmless.

Each task returns the Stage 2 cost record (JobStat) as its XCom; `run_summary` prints them as the
Stage 2 cost table.

`AttributionBatchOperator`, at the end, is the Dataproc batch task: the stock operator, plus cancelling its
batch whenever a try ends before the batch does.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from airflow.providers.google.cloud.hooks.bigquery import BigQueryHook
from airflow.providers.google.cloud.operators.bigquery import BigQueryInsertJobOperator
from airflow.providers.google.cloud.operators.dataproc import DataprocCreateBatchOperator

from tagline_pipeline import pipeline
from tagline_pipeline.bq import BigQuery
from tagline_pipeline.costs import JobStat, human_bytes
from tagline_pipeline.sqlfiles import Model, parse_doc, render

from . import settings, stage2

try:  # Airflow 3 task SDK
    from airflow.sdk.exceptions import AirflowFailException, TaskDeferred
except ImportError:  # pragma: no cover - Airflow 2
    from airflow.exceptions import AirflowFailException, TaskDeferred

MAX_FAILURE_ROWS_LOGGED = 20


def label(value: str) -> str:
    """A BigQuery label value: lowercase letters, digits, _ and -, at most 63 characters (as Stage 2's bq.py)."""
    return re.sub(r"[^a-z0-9_-]", "_", value.lower())[:63]


class TaglineQueryOperator(BigQueryInsertJobOperator):
    """Run one SQL file from the Stage 2 package (or written in its `{{ name }}` style) as a query job."""

    ui_color = "#e4f0e8"

    def __init__(
        self,
        *,
        sql_path: str,
        step: str,
        kind: str,
        stage: str,
        sources_task_id: str | None = "prepare_sources",
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("gcp_conn_id", settings.GCP_CONN_ID)
        kwargs.setdefault("durable", False)
        super().__init__(configuration={}, **kwargs)
        self.sql_path = sql_path
        self.step = step
        self.kind = kind
        self.stage = stage
        self.sources_task_id = sources_task_id

    # -- rendering ------------------------------------------------------------------------

    def build_configuration(self, sql: str, max_bytes_billed: int) -> dict[str, Any]:
        return {
            "query": {
                "query": sql,
                "useLegacySql": False,
                "useQueryCache": False,
                "maximumBytesBilled": str(max_bytes_billed),
            },
            "labels": {
                "app": "tagline",
                "stage": label(self.stage),
                "kind": label(self.kind),
                "step": label(self.step),
                "orchestrator": "airflow",
            },
        }

    def execute(self, context: Any) -> dict[str, Any]:
        cfg = settings.pipeline_config()
        site = None
        if self.sources_task_id:
            site = stage2.site_from_xcom(context["ti"].xcom_pull(task_ids=self.sources_task_id))
        sql_text = Path(self.sql_path).read_text(encoding="utf-8")
        self.configuration = self.build_configuration(render(sql_text, pipeline.context(cfg, site)), cfg.max_bytes_billed)
        self.project_id = cfg.project
        self.location = cfg.location
        self.log.info(
            "%s %s: %s, maximumBytesBilled=%s", self.kind, self.step, Path(self.sql_path).name, f"{cfg.max_bytes_billed:,}"
        )
        self.before_job(context, cfg, site)

        super().execute(context)  # submit, wait, raise on a job error

        job = self._job
        stat = JobStat(
            step=self.step,
            kind=self.kind,
            bytes_processed=job.total_bytes_processed,
            bytes_billed=job.total_bytes_billed,
            slot_ms=job.slot_millis,
            seconds=(job.ended - job.started).total_seconds() if job.ended and job.started else None,
            job_id=job.job_id,
        )
        self.after_job(context, cfg, site, sql_text, job, stat)
        self.log.info(
            "%s %s: processed %s, billed %s, %s slot-ms, %.1f s%s",
            self.kind,
            self.step,
            human_bytes(stat.bytes_processed),
            human_bytes(stat.bytes_billed),
            f"{stat.slot_ms:,}" if stat.slot_ms is not None else "-",
            stat.seconds or 0.0,
            f", {stat.rows:,} rows" if stat.rows is not None else "",
        )
        return asdict(stat)

    def before_job(self, context, cfg, site) -> None:  # noqa: ANN001
        """Hook for subclasses: runs before the job is submitted."""

    def after_job(self, context, cfg, site, sql_text: str, job, stat: JobStat) -> None:  # noqa: ANN001
        """Hook for subclasses: runs after the job succeeded."""


class Stage2ModelOperator(TaglineQueryOperator):
    """Build one Stage 2 model (CREATE OR REPLACE TABLE), then do what `make build` does after each model:
    set the table and column descriptions from the model's header, and count the rows. For stg_events, also what
    `make build` does around it: take the site export's metadata before the job reads the export, and record it in
    staged_export_days once the table is built (the daily incremental build compares the export with that record)."""

    def __init__(self, *, model: Model, **kwargs: Any) -> None:
        super().__init__(sql_path=str(model.path), step=model.name, kind="model", stage="2", **kwargs)
        self.model_name = model.name
        self.layer = model.layer
        self._staged: dict | None = None

    def _bigquery(self, cfg) -> BigQuery:  # noqa: ANN001
        hook = BigQueryHook(gcp_conn_id=self.gcp_conn_id, location=cfg.location)
        return BigQuery(cfg, client=hook.get_client(project_id=cfg.project, location=cfg.location), extra_labels={"orchestrator": "airflow"})

    def before_job(self, context, cfg, site) -> None:  # noqa: ANN001
        self._staged = pipeline.export_state(cfg, self._bigquery(cfg), site) if self.model_name == "stg_events" else None

    def after_job(self, context, cfg, site, sql_text, job, stat) -> None:  # noqa: ANN001
        dataset = cfg.staging_dataset if self.layer == "staging" else cfg.marts_dataset
        table_id = f"{cfg.project}.{dataset}.{self.model_name}"
        bq = BigQuery(cfg, client=self.hook.get_client(project_id=cfg.project, location=cfg.location), extra_labels={"orchestrator": "airflow"})
        # _sources_note is the "Sources in this build: ..." suffix `make build` appends to the description.
        bq.apply_docs(table_id, parse_doc(sql_text), suffix=pipeline._sources_note(cfg, site))
        stat.rows = bq.num_rows(table_id)
        self.log.info("built %s: %s rows", table_id, f"{stat.rows:,}")
        if self._staged is not None:
            pipeline.record_staged(cfg, bq, self._staged)
            self.log.info("recorded %d site export day(s) in %s", len(self._staged), pipeline.staged_table_id(cfg))


class NoRowsCheckOperator(TaglineQueryOperator):
    """A data check in Stage 2's convention: the query returns no rows when the check passes, and one
    row per failure otherwise. A failure fails the task without retrying (re-running the same query
    on the same tables cannot pass); an API error still retries."""

    ui_color = "#fdf2d0"

    def __init__(self, *, check_path: Path, stage: str = "2", **kwargs: Any) -> None:
        super().__init__(sql_path=str(check_path), step=check_path.stem, kind="check", stage=stage, **kwargs)

    def after_job(self, context, cfg, site, sql_text, job, stat) -> None:  # noqa: ANN001
        rows = [dict(r.items()) for r in job.result()]
        stat.rows = len(rows)
        description = parse_doc(sql_text).check or self.step
        if not rows:
            self.log.info("PASS  %s: %s", self.step, description)
            return
        self.log.error("FAIL  %s: %s", self.step, description)
        for row in rows[:MAX_FAILURE_ROWS_LOGGED]:
            self.log.error("        %s", ", ".join(f"{k}={v}" for k, v in row.items()))
        if len(rows) > MAX_FAILURE_ROWS_LOGGED:
            self.log.error("        ... %d more", len(rows) - MAX_FAILURE_ROWS_LOGGED)
        raise AirflowFailException(f"check {self.step} failed: {len(rows)} failing row(s) (job {job.job_id})")


class AttributionBatchOperator(DataprocCreateBatchOperator):
    """DataprocCreateBatchOperator that cancels its batch whenever the try ends while the batch may still run.

    The stock operator cancels only in `on_kill`, which Airflow calls on an execution timeout or a SIGTERM.
    Any other exception while it waits (an expired or unrefreshable ADC token after the laptop slept, a
    transport error during a network drop: `wait_for_batch` retries server errors only), or a Ctrl-C in
    `make airflow-test` (KeyboardInterrupt), fails the try and leaves the batch running; the retry, a new
    batch id, would then start a second batch beside it, both overwriting the same tables. Here any such
    exit cancels the batch first. Cancelling a batch that already ended is a no-op (the operation is done),
    and a failed cancel is logged, never raised over the original error. A worker that dies outright still
    cancels nothing: `make airflow-orphans` is for that."""

    def execute(self, context: Any) -> Any:
        try:
            return super().execute(context)
        except TaskDeferred:  # not an exit: the task resumes in execute_complete (unused here: not deferrable)
            raise
        except BaseException:
            self.cancel_batch()
            raise

    def on_kill(self) -> None:
        """Airflow's own path (execution timeout, SIGTERM): the same cancel, requested at most once per try."""
        self.cancel_batch()

    def cancel_batch(self) -> None:
        if self.operation is None or getattr(self, "_cancel_requested", False):
            return  # no batch created by this try (or it attached to an existing one), or already cancelled
        try:
            if self.operation.cancel():  # False, without a request, when the batch has already ended
                self._cancel_requested = True
                self.log.warning("cancelled batch %s: the task stopped while it was still running", self.batch_id)
        except Exception:  # noqa: BLE001 - never hide the error that ended the try
            self.log.exception("could not cancel batch %s; `make airflow-orphans` lists it if it still runs", self.batch_id)
