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
    "staging": "Tagline Stage 2: stg_events, stg_items, int_identity, built from the GA4 exports by tagline/pipeline.",
    "marts": "Tagline Stage 2: sessions, orders, order items and daily campaign / funnel marts, built by tagline/pipeline.",
}


class DocError(RuntimeError):
    pass


def _label(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", value.lower())[:63]


class BigQuery:
    def __init__(self, cfg: Config, client: bigquery.Client | None = None) -> None:
        self.cfg = cfg
        self.client = client or bigquery.Client(project=cfg.project, location=cfg.location)

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
            labels={"app": "tagline", "stage": "2", "kind": _label(kind), "step": _label(step)},
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
    ) -> JobStat:
        """Replace a table with these rows (a load job: free, unlike streaming inserts)."""
        job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            destination_table_description=description,
            labels={"app": "tagline", "stage": "2", "kind": "load", "step": _label(step)},
        )
        job = self.client.load_table_from_json(rows, table_id, job_config=job_config, project=self.cfg.project, location=self.cfg.location)
        job.result()
        table = self.client.get_table(table_id)
        table.labels = {**(table.labels or {}), "app": "tagline", "stage": "2"}
        self.client.update_table(table, ["labels"])
        seconds = (job.ended - job.started).total_seconds() if job.ended and job.started else None
        return JobStat(step=step, kind="load", seconds=seconds, rows=job.output_rows, job_id=job.job_id)
