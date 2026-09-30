"""The only module that talks to BigQuery.

Every query job gets the cost guard (maximum_bytes_billed), no query cache (so the
recorded cost is the real cost), labels that identify the step (so Stage 4 can find the
jobs in INFORMATION_SCHEMA.JOBS), and an explicit project and location.
"""

from __future__ import annotations

import re
import time
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from .config import Config
from .costs import JobStat
from .sqlfiles import Doc

DATASET_DESCRIPTIONS = {
    "raw": "Tagline Stage 2: reference data loaded by tagline/pipeline: the site catalog with SYNTHETIC unit costs, "
    "and SYNTHETIC daily campaign costs. While `make fixture` runs, also its temporary fake_ga4_events_* tables "
    "(SYNTHETIC rows shaped like a GA4 export; deleted at the end, 24-hour expiry).",
    "staging": "Tagline Stage 2: stg_events, stg_items, int_purchases, int_device_days, int_identity, built from the GA4 "
    "exports by tagline/pipeline, and staged_export_days, what the builds read from the site's export (Stage 4).",
    "marts": "Tagline Stage 2: sessions, orders, order items and daily campaign / funnel marts, built by tagline/pipeline; "
    "Stage 5: the daily KPI and tag health marts and kpi_alerts.",
}


class DocError(RuntimeError):
    pass


def _label(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", value.lower())[:63]


class BigQuery:
    def __init__(self, cfg: Config, client: bigquery.Client | None = None, extra_labels: dict[str, str] | None = None) -> None:
        self.cfg = cfg
        self.client = client or bigquery.Client(project=cfg.project, location=cfg.location)
        self.extra_labels = {_label(k): _label(v) for k, v in (extra_labels or {}).items()}  # e.g. orchestrator=airflow
        self.last_script_statements: list[JobStat] = []

    def _labels(self, kind: str, step: str) -> dict[str, str]:
        return {**self.extra_labels, "app": "tagline", "stage": "2", "kind": _label(kind), "step": _label(step)}

    # -- datasets ---------------------------------------------------------------------

    def _datasets(self) -> tuple[tuple[str, str], ...]:
        return (("raw", self.cfg.raw_dataset), ("staging", self.cfg.staging_dataset), ("marts", self.cfg.marts_dataset))

    def ensure_datasets(self) -> list[str]:
        """Create tagline_raw / tagline_staging / tagline_marts in the US if missing, and bring an existing one's
        description and app/stage labels in line with the code. Returns the datasets created."""
        created = []
        for key, name in self._datasets():
            ref = f"{self.cfg.project}.{name}"
            labels = {"app": "tagline", "stage": "2"}
            try:
                ds = self.client.get_dataset(ref)
            except NotFound:
                ds = bigquery.Dataset(ref)
                ds.location = self.cfg.location
                ds.description = DATASET_DESCRIPTIONS[key]
                ds.labels = labels
                self.client.create_dataset(ds)
                created.append(name)
                continue
            if ds.location != self.cfg.location:
                raise RuntimeError(f"{ref} is in {ds.location}, not {self.cfg.location}; the sample is in US and cross-location queries fail")
            changed = []
            if ds.description != DATASET_DESCRIPTIONS[key]:
                ds.description = DATASET_DESCRIPTIONS[key]
                changed.append("description")
            if any((ds.labels or {}).get(k) != v for k, v in labels.items()):
                ds.labels = {**(ds.labels or {}), **labels}
                changed.append("labels")
            if changed:
                self.client.update_dataset(ds, changed)
        return created

    def missing_datasets(self) -> list[str]:
        """The tagline datasets that do not exist yet (read-only: creates nothing)."""
        missing = []
        for _, name in self._datasets():
            try:
                self.client.get_dataset(f"{self.cfg.project}.{name}")
            except NotFound:
                missing.append(name)
        return missing

    def list_table_ids(self, project: str, dataset: str) -> list[str]:
        try:
            return [t.table_id for t in self.client.list_tables(f"{project}.{dataset}")]
        except NotFound:
            raise RuntimeError(f"dataset {project}.{dataset} does not exist") from None

    # -- queries ----------------------------------------------------------------------

    def query(self, step: str, kind: str, sql: str, *, dry_run: bool = False) -> tuple[list[bigquery.Row], JobStat]:
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.cfg.max_bytes_billed,
            use_query_cache=False,
            use_legacy_sql=False,
            dry_run=dry_run,
            labels=self._labels(kind, step),
        )
        started = time.monotonic()
        job = self.client.query(sql, job_config=job_config, project=self.cfg.project, location=self.cfg.location)
        if dry_run:
            return [], JobStat(step, kind, bytes_processed=job.total_bytes_processed, dry_run=True)
        rows = list(job.result())
        seconds = (job.ended - job.started).total_seconds() if job.ended and job.started else time.monotonic() - started
        stat = JobStat(
            step=step,
            kind=kind,
            bytes_processed=job.total_bytes_processed,
            bytes_billed=job.total_bytes_billed,
            slot_ms=job.slot_millis,
            seconds=seconds,
            rows=len(rows) if kind != "model" else None,
            job_id=job.job_id,
        )
        return rows, stat

    def script(self, step: str, kind: str, sql: str) -> list[JobStat]:
        """Run a multi-statement script (one job) with the same guard, cache setting and labels as `query`, and return
        one JobStat per statement BigQuery ran for it (its child jobs, in order), each with the script's job id as
        parent_job_id. The statements' bytes billed add up to the script's. maximum_bytes_billed applies to each
        statement."""
        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.cfg.max_bytes_billed,
            use_query_cache=False,
            use_legacy_sql=False,
            labels=self._labels(kind, step),
        )
        job = self.client.query(sql, job_config=job_config, project=self.cfg.project, location=self.cfg.location)
        try:
            job.result()
        finally:
            children = sorted(self.client.list_jobs(parent_job=job.job_id), key=lambda j: (j.created, j.job_id))
            self.last_script_statements = [self._statement_stat(c, job.job_id) for c in children]
        return self.last_script_statements

    @staticmethod
    def _statement_stat(child: Any, parent_id: str) -> JobStat:
        dest = getattr(child, "destination", None)
        table = dest.table_id if dest is not None else None
        statement = (getattr(child, "statement_type", None) or "statement").lower()
        # temporary tables and query results live in anonymous datasets: name them by the temp table, or not at all
        step = table if table and not table.startswith("anon") else "-"
        seconds = (child.ended - child.started).total_seconds() if child.ended and child.started else None
        return JobStat(
            step=step,
            kind=statement,
            bytes_processed=getattr(child, "total_bytes_processed", None),
            bytes_billed=getattr(child, "total_bytes_billed", None),
            slot_ms=getattr(child, "slot_millis", None),
            seconds=seconds,
            job_id=child.job_id,
            parent_job_id=parent_id,
        )

    def partition_ids(self, dataset: str, table: str) -> tuple[set[str], JobStat]:
        """The non-empty partitions of a table (INFORMATION_SCHEMA.PARTITIONS: metadata, the 10 MB minimum), and the
        query's cost record."""
        rows, stat = self.query(
            "partitions",
            "metadata",
            f"SELECT partition_id FROM `{self.cfg.project}.{dataset}.INFORMATION_SCHEMA.PARTITIONS` "
            f"WHERE table_name = '{table}' AND total_rows > 0",
        )
        return {r["partition_id"] for r in rows}, stat

    def table_metadata(self, project: str, dataset: str, prefix: str) -> tuple[dict[str, tuple[Any, int]], JobStat]:
        """Each table whose name starts with `prefix`: (last modified time, row count), from the dataset's __TABLES__
        (metadata, 0 bytes billed); and the query's cost record."""
        if not re.fullmatch(r"[A-Za-z0-9_]*", prefix):
            raise ValueError(f"table prefix {prefix!r}: letters, digits and _ only")
        rows, stat = self.query(
            "export_tables",
            "metadata",
            f"SELECT table_id, TIMESTAMP_MILLIS(last_modified_time) AS last_modified_time, row_count "
            f"FROM `{project}.{dataset}.__TABLES__` WHERE STARTS_WITH(table_id, '{prefix}')",
        )
        return {r["table_id"]: (r["last_modified_time"], int(r["row_count"] or 0)) for r in rows}, stat

    def read_rows(self, table_id: str) -> list[dict[str, Any]]:
        """Every row of a (small) table, through the table-data API: no query, nothing billed."""
        return [dict(r.items()) for r in self.client.list_rows(table_id)]

    def table_exists(self, table_id: str) -> bool:
        try:
            self.client.get_table(table_id)
            return True
        except NotFound:
            return False

    # -- tables -----------------------------------------------------------------------

    def num_rows(self, table_id: str) -> int:
        return int(self.client.get_table(table_id).num_rows or 0)

    def apply_docs(self, table_id: str, doc: Doc, suffix: str = "") -> None:
        """Set the table and column descriptions from the model's header. Every column must be documented."""
        table = self.client.get_table(table_id)
        fields = [f.to_api_repr() for f in table.schema]
        documented = set(doc.columns)
        seen: set[str] = set()
        missing: list[str] = []

        def walk(items: list[dict[str, Any]], prefix: str) -> None:
            for f in items:
                path = prefix + f["name"]
                seen.add(path)
                if path in doc.columns:
                    f["description"] = doc.columns[path]
                else:
                    missing.append(path)
                if f.get("fields"):
                    walk(f["fields"], path + ".")

        walk(fields, "")
        unknown = sorted(documented - seen)
        if missing or unknown:
            raise DocError(
                f"{table_id}: "
                + (f"undocumented columns: {', '.join(missing)}. " if missing else "")
                + (f"documented columns that do not exist: {', '.join(unknown)}." if unknown else "")
            )
        table.schema = [bigquery.SchemaField.from_api_repr(f) for f in fields]
        table.description = (doc.table + (" " + suffix if suffix else "")).strip()
        table.labels = {**(table.labels or {}), "app": "tagline", "stage": "2"}
        self.client.update_table(table, ["schema", "description", "labels"])

    def load_json(
        self,
        step: str,
        table_id: str,
        rows: list[dict[str, Any]],
        schema: list[bigquery.SchemaField],
        description: str,
        *,
        stage: str = "2",
        partition_field: str | None = None,
    ) -> JobStat:
        """Replace a table with these rows (a load job: free, unlike streaming inserts). `partition_field`: a DATE column
        to partition a new table by (an existing table keeps its partitioning)."""
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            destination_table_description=description,
            labels={**self.extra_labels, "app": "tagline", "stage": _label(stage), "kind": "load", "step": _label(step)},
        )
        if partition_field is not None:
            job_config.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.DAY, field=partition_field)
        job = self.client.load_table_from_json(rows, table_id, job_config=job_config, project=self.cfg.project, location=self.cfg.location)
        job.result()
        table = self.client.get_table(table_id)
        table.labels = {**(table.labels or {}), "app": "tagline", "stage": _label(stage)}
        self.client.update_table(table, ["labels"])
        seconds = (job.ended - job.started).total_seconds() if job.ended and job.started else None
        return JobStat(step=step, kind="load", seconds=seconds, rows=job.output_rows, job_id=job.job_id)
