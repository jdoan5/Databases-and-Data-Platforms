"""Everything the harness asks BigQuery: jobs from INFORMATION_SCHEMA.JOBS_BY_PROJECT, table storage from
INFORMATION_SCHEMA.TABLE_STORAGE, table fingerprints, EXCEPT DISTINCT diffs against a baseline, and the baseline
copies. The SQL builders are pure functions (tested); BQ runs them.

Every query the harness runs has maximum_bytes_billed, no query cache, and the labels app=tagline, stage=4,
kind=bench, step=<what>, so it is never mistaken for a pipeline job (window collection skips kind=bench)."""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import LOCATION, REGION_QUALIFIER, BenchConfig
from .prices import bq_usd

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,299}$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")


def label_value(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", value.lower())[:63]


def quote_ident(name: str) -> str:
    if not _IDENT_RE.fullmatch(name):
        raise ValueError(f"not a plain column name: {name!r}")
    return f"`{name}`"


def table_ref(project: str, dataset: str, table: str) -> str:
    for part in (dataset, table):
        if not _DATASET_RE.fullmatch(part):
            raise ValueError(f"not a plain dataset/table name: {part!r}")
    return f"`{project}.{dataset}.{table}`"


# -- jobs ---------------------------------------------------------------------------------------------------

JOB_COLUMNS = """
  job_id, parent_job_id, creation_time, start_time, end_time, job_type, statement_type, state,
  total_bytes_processed, total_bytes_billed, total_slot_ms, cache_hit,
  error_result.reason AS error_reason,
  destination_table.dataset_id AS destination_dataset, destination_table.table_id AS destination_table,
  labels"""


def jobs_by_ids_sql(project: str) -> str:
    """Jobs by id (@ids), and the statements of any of them that is a script (their parent_job_id is one of @ids),
    inside a creation-time window (@since, @until) so the view reads only those days."""
    return (
        f"SELECT{JOB_COLUMNS}\nFROM `{project}`.`{REGION_QUALIFIER}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT\n"
        "WHERE creation_time BETWEEN @since AND @until AND (job_id IN UNNEST(@ids) OR parent_job_id IN UNNEST(@ids))\n"
        "ORDER BY creation_time, job_id"
    )


def nest_statements(records: Sequence[dict[str, Any]], ids: Sequence[str]) -> list[dict[str, Any]]:
    """The jobs asked for, each script with its statements (child jobs, in order) under `statements`. A script's own
    bytes and slot-ms already include its statements', so totals over the returned list count nothing twice."""
    wanted = set(ids)
    top = [dict(r) for r in records if r["job_id"] in wanted]
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r["job_id"] not in wanted and r.get("parent_job_id") in wanted:
            by_parent.setdefault(r["parent_job_id"], []).append(dict(r))
    for j in top:
        children = by_parent.get(j["job_id"])
        if children:
            j["statements"] = children
    return top


def top_level_ids(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """The ids of the jobs that are not a statement of another job in `records` (for a time window, where a script and
    its child statements all match the labels): pass them to nest_statements so a script is counted once."""
    ids = {r["job_id"] for r in records}
    return [r["job_id"] for r in records if not r.get("parent_job_id") or r["parent_job_id"] not in ids]


def jobs_by_window_sql(project: str, labels: Mapping[str, str], job_types: Sequence[str] = ("QUERY",)) -> str:
    """Every job labelled app=tagline (and each label given) created in [@since, @until], except the harness's own
    (kind=bench). Label values are validated, then inlined."""
    wanted = {"app": "tagline", **labels}
    for k, v in wanted.items():
        if not (re.fullmatch(r"[a-z0-9_-]{1,63}", k) and re.fullmatch(r"[a-z0-9_-]{0,63}", v)):
            raise ValueError(f"invalid label filter {k}={v}")
    for jt in job_types:
        if jt not in {"QUERY", "LOAD", "COPY", "EXTRACT"}:
            raise ValueError(f"invalid job type {jt}")
    conds = [f"EXISTS (SELECT 1 FROM UNNEST(labels) l WHERE l.key = '{k}' AND l.value = '{v}')" for k, v in wanted.items()]
    conds.append("NOT EXISTS (SELECT 1 FROM UNNEST(labels) l WHERE l.key = 'kind' AND l.value = 'bench')")
    types = ", ".join(f"'{t}'" for t in job_types)
    return (
        f"SELECT{JOB_COLUMNS}\nFROM `{project}`.`{REGION_QUALIFIER}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT\n"
        f"WHERE creation_time BETWEEN @since AND @until AND job_type IN ({types})\n  AND "
        + "\n  AND ".join(conds)
        + "\nORDER BY creation_time"
    )


def job_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """One JOBS row as the results log keeps it."""
    labels = {l["key"]: l["value"] for l in (row.get("labels") or [])}
    created, started, ended = row.get("creation_time"), row.get("start_time"), row.get("end_time")
    return {
        "job_id": row["job_id"],
        "parent_job_id": row.get("parent_job_id"),
        "step": labels.get("step") or labels.get("airflow-task") or row.get("destination_table"),
        "kind": labels.get("kind"),
        "job_type": row.get("job_type"),
        "statement_type": row.get("statement_type"),
        "state": row.get("state"),
        "error_reason": row.get("error_reason"),
        "created": created,
        "started": started,
        "ended": ended,
        "elapsed_seconds": (ended - started).total_seconds() if ended and started else None,
        "queued_seconds": (started - created).total_seconds() if started and created else None,
        "bytes_processed": row.get("total_bytes_processed"),
        "bytes_billed": row.get("total_bytes_billed"),
        "slot_ms": row.get("total_slot_ms"),
        "cache_hit": row.get("cache_hit"),
        "destination": f"{row.get('destination_dataset')}.{row.get('destination_table')}" if row.get("destination_table") else None,
        "labels": labels,
    }


def job_totals(jobs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    jobs = list(jobs)

    def total(key: str) -> int:
        return sum(int(j.get(key) or 0) for j in jobs)

    billed = total("bytes_billed")
    starts = [j["started"] for j in jobs if j.get("started")]
    ends = [j["ended"] for j in jobs if j.get("ended")]
    return {
        "jobs": len(jobs),
        "bytes_processed": total("bytes_processed"),
        "bytes_billed": billed,
        "slot_ms": total("slot_ms"),
        "job_seconds": round(sum(j.get("elapsed_seconds") or 0 for j in jobs), 3),
        "first_start_to_last_end_seconds": (max(ends) - min(starts)).total_seconds() if starts and ends else None,
        "usd": bq_usd(billed),
        "cache_hits": sum(1 for j in jobs if j.get("cache_hit")),
    }


# -- storage ------------------------------------------------------------------------------------------------

STORAGE_COLUMNS = (
    "total_rows",
    "total_partitions",
    "total_logical_bytes",
    "active_logical_bytes",
    "long_term_logical_bytes",
    "total_physical_bytes",
    "active_physical_bytes",
    "long_term_physical_bytes",
    "time_travel_physical_bytes",
    "fail_safe_physical_bytes",
)


def table_storage_sql(project: str) -> str:
    cols = ", ".join(STORAGE_COLUMNS)
    return (
        f"SELECT table_schema, table_name, table_type, deleted, storage_last_modified_time, {cols}\n"
        f"FROM `{project}`.`{REGION_QUALIFIER}`.INFORMATION_SCHEMA.TABLE_STORAGE\n"
        "WHERE table_schema IN UNNEST(@datasets)\nORDER BY table_schema, table_name"
    )


_METADATA_COUNTERS = {
    "total_rows": "numRows",
    "total_partitions": "numPartitions",
    "total_logical_bytes": "numTotalLogicalBytes",
    "active_logical_bytes": "numActiveLogicalBytes",
    "long_term_logical_bytes": "numLongTermLogicalBytes",
    "total_physical_bytes": "numTotalPhysicalBytes",
    "active_physical_bytes": "numActivePhysicalBytes",
    "long_term_physical_bytes": "numLongTermPhysicalBytes",
    "time_travel_physical_bytes": "numTimeTravelPhysicalBytes",
    "current_physical_bytes": "numCurrentPhysicalBytes",
}


def storage_row_from_table(dataset: str, table: str, table_type: str | None, props: Mapping[str, Any], modified: Any = None) -> dict[str, Any]:
    """A TABLE_STORAGE-shaped row from a table resource's counters (fail-safe bytes are not in the resource)."""
    row: dict[str, Any] = {"table_schema": dataset, "table_name": table, "table_type": table_type, "deleted": False, "storage_last_modified_time": modified}
    for col, key in _METADATA_COUNTERS.items():
        v = props.get(key)
        row[col] = int(v) if v is not None else None
    row["fail_safe_physical_bytes"] = None
    return row


# -- fingerprints and diffs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # BigQuery legacy type names from the table schema: FLOAT, INTEGER, RECORD, ...
    mode: str = "NULLABLE"


def row_struct_sql(columns: Sequence[Column], float_digits: int | None = None) -> str:
    """STRUCT(...) of the columns sorted by name, so the fingerprint does not depend on column order. With
    float_digits, top-level FLOAT64 columns are rounded (nested floats are left exact)."""
    parts = []
    for c in sorted(columns, key=lambda c: c.name):
        q = quote_ident(c.name)
        if float_digits is not None and c.type in {"FLOAT", "FLOAT64"} and c.mode != "REPEATED":
            parts.append(f"ROUND({q}, {int(float_digits)}) AS {q}")
        else:
            parts.append(q)
    return "STRUCT(" + ", ".join(parts) + ")"


def fingerprint_sql(ref: str, columns: Sequence[Column], float_digits: int | None = None) -> str:
    """Row count plus two order-independent hashes of the rows (XOR and sum of FARM_FINGERPRINT over each row's
    JSON): equal for the same multiset of rows, whatever order they are stored in."""
    return (
        "SELECT COUNT(*) AS row_count, BIT_XOR(fp) AS xor_fp, CAST(SUM(CAST(fp AS BIGNUMERIC)) AS STRING) AS sum_fp\n"
        f"FROM (SELECT FARM_FINGERPRINT(TO_JSON_STRING({row_struct_sql(columns, float_digits)})) AS fp FROM {ref} AS t)"
    )


def diff_sql(current: str, baseline: str, columns: Sequence[Column], float_digits: int | None = None) -> str:
    """Rows of each side missing from the other (EXCEPT DISTINCT both ways, on each row's JSON over the common
    columns), plus both row counts and multiset hashes, so a changed count of duplicate rows shows too."""
    row = row_struct_sql(columns, float_digits)
    return f"""WITH cur AS (SELECT TO_JSON_STRING({row}) AS r FROM {current} AS t),
base AS (SELECT TO_JSON_STRING({row}) AS r FROM {baseline} AS t)
SELECT
  (SELECT COUNT(*) FROM cur) AS current_rows,
  (SELECT COUNT(*) FROM base) AS baseline_rows,
  (SELECT COUNT(*) FROM (SELECT r FROM cur EXCEPT DISTINCT SELECT r FROM base)) AS only_in_current,
  (SELECT COUNT(*) FROM (SELECT r FROM base EXCEPT DISTINCT SELECT r FROM cur)) AS only_in_baseline,
  (SELECT CAST(SUM(CAST(FARM_FINGERPRINT(r) AS BIGNUMERIC)) AS STRING) FROM cur) AS current_sum_fp,
  (SELECT CAST(SUM(CAST(FARM_FINGERPRINT(r) AS BIGNUMERIC)) AS STRING) FROM base) AS baseline_sum_fp"""


def diff_examples_sql(current: str, baseline: str, columns: Sequence[Column], float_digits: int | None, limit: int = 5) -> str:
    row = row_struct_sql(columns, float_digits)
    return f"""WITH cur AS (SELECT TO_JSON_STRING({row}) AS r FROM {current} AS t),
base AS (SELECT TO_JSON_STRING({row}) AS r FROM {baseline} AS t)
(SELECT 'only_in_current' AS side, r FROM (SELECT r FROM cur EXCEPT DISTINCT SELECT r FROM base) ORDER BY r LIMIT {int(limit)})
UNION ALL
(SELECT 'only_in_baseline' AS side, r FROM (SELECT r FROM base EXCEPT DISTINCT SELECT r FROM cur) ORDER BY r LIMIT {int(limit)})"""


# -- the client ---------------------------------------------------------------------------------------------


@dataclass
class BQ:
    cfg: BenchConfig
    client: Any = None
    billed: int = 0  # what the harness's own queries billed
    jobs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.client is None:
            from google.cloud import bigquery

            self.client = bigquery.Client(project=self.cfg.project, location=LOCATION)

    def query(self, step: str, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        from google.cloud import bigquery

        job_config = bigquery.QueryJobConfig(
            maximum_bytes_billed=self.cfg.max_bytes_billed,
            use_query_cache=False,
            use_legacy_sql=False,
            query_parameters=list(params),
            labels={"app": "tagline", "stage": "4", "kind": "bench", "step": label_value(step)},
        )
        job = self.client.query(sql, job_config=job_config, project=self.cfg.project, location=LOCATION)
        rows = [dict(r.items()) for r in job.result()]
        self.billed += job.total_bytes_billed or 0
        self.jobs.append(job.job_id)
        return rows

    # jobs

    def jobs_by_ids(self, ids: Sequence[str], since: datetime, until: datetime, *, wait_seconds: float = 90) -> list[dict[str, Any]]:
        """JOBS rows for these ids. The view can lag a finished job by a few seconds, so this re-reads until every
        id is there (at most wait_seconds)."""
        from google.cloud import bigquery

        ids = list(dict.fromkeys(ids))
        if not ids:
            return []
        params = [
            bigquery.ScalarQueryParameter("since", "TIMESTAMP", since - timedelta(minutes=5)),
            bigquery.ScalarQueryParameter("until", "TIMESTAMP", until + timedelta(minutes=5)),
            bigquery.ArrayQueryParameter("ids", "STRING", ids),
        ]
        deadline = time.monotonic() + wait_seconds
        while True:
            rows = self.query("jobs_by_ids", jobs_by_ids_sql(self.cfg.project), params)
            found = {r["job_id"] for r in rows}
            if found >= set(ids) or time.monotonic() > deadline:
                missing = [i for i in ids if i not in found]
                if missing:
                    print(f"  warning: {len(missing)} job(s) not in JOBS_BY_PROJECT yet: {', '.join(missing[:5])}")
                return nest_statements([job_record(r) for r in rows], ids)
            time.sleep(10)

    def jobs_by_window(
        self, since: datetime, until: datetime, labels: Mapping[str, str] | None = None, job_types: Sequence[str] = ("QUERY",)
    ) -> list[dict[str, Any]]:
        from google.cloud import bigquery

        params = [
            bigquery.ScalarQueryParameter("since", "TIMESTAMP", since),
            bigquery.ScalarQueryParameter("until", "TIMESTAMP", until),
        ]
        rows = self.query("jobs_by_window", jobs_by_window_sql(self.cfg.project, labels or {}, job_types), params)
        records = [job_record(r) for r in rows]
        # a script's statements carry its labels too: nest them under it, or their bytes would be counted twice
        return nest_statements(records, top_level_ids(records))

    # storage

    def table_storage(self, datasets: Sequence[str]) -> tuple[list[dict[str, Any]], str, str | None]:
        """Storage per table: INFORMATION_SCHEMA.TABLE_STORAGE when the caller may read it, else the same counters
        from each table's metadata (tables.get, free). Returns (rows, source, why the view was not used).

        The view needs bigquery.tables.get and bigquery.tables.list at the project level. roles/owner does not
        include them (BigQuery gives basic roles data access through each dataset's ACL instead), so for a
        project owner without e.g. roles/bigquery.metadataViewer the view is denied. The table metadata carries
        the same logical / physical / long-term / time-travel counters (and numCurrentPhysicalBytes), but not
        fail-safe bytes and not the storage still held by dropped tables."""
        from google.api_core.exceptions import Forbidden
        from google.cloud import bigquery

        params = [bigquery.ArrayQueryParameter("datasets", "STRING", list(datasets))]
        try:
            rows = self.query("table_storage", table_storage_sql(self.cfg.project), params)
            for r in rows:
                r.setdefault("current_physical_bytes", None)
            return rows, "INFORMATION_SCHEMA.TABLE_STORAGE", None
        except Forbidden as e:
            why = (e.message or str(e)).splitlines()[0]
        return self.table_storage_from_metadata(datasets), "tables.get metadata", why

    def table_storage_from_metadata(self, datasets: Sequence[str]) -> list[dict[str, Any]]:
        from google.api_core.exceptions import NotFound

        out = []
        for ds in datasets:
            try:
                items = list(self.client.list_tables(f"{self.cfg.project}.{ds}"))
            except NotFound:
                continue
            for item in items:
                t = self.client.get_table(item.reference)
                out.append(storage_row_from_table(ds, t.table_id, t.table_type, t._properties, t.modified))
        return out

    def dataset_options(self, dataset: str) -> dict[str, Any] | None:
        """Storage billing model, time-travel window and default expirations (a metadata read: free)."""
        from google.api_core.exceptions import NotFound

        try:
            ds = self.client.get_dataset(f"{self.cfg.project}.{dataset}")
        except NotFound:
            return None
        return {
            "location": ds.location,
            "storage_billing_model": ds.storage_billing_model or "LOGICAL (default)",
            "max_time_travel_hours": ds.max_time_travel_hours or "168 (default)",
            "default_table_expiration_ms": ds.default_table_expiration_ms,
            "default_partition_expiration_ms": ds.default_partition_expiration_ms,
            "labels": dict(ds.labels or {}),
        }

    # tables

    def columns(self, dataset: str, table: str) -> list[Column]:
        t = self.client.get_table(f"{self.cfg.project}.{dataset}.{table}")
        return [Column(f.name, f.field_type, f.mode or "NULLABLE") for f in t.schema]

    def table_meta(self, dataset: str, table: str) -> dict[str, Any]:
        t = self.client.get_table(f"{self.cfg.project}.{dataset}.{table}")
        return {
            "rows": t.num_rows,
            "logical_bytes": t.num_bytes,
            "partition_field": t.time_partitioning.field if t.time_partitioning else None,
            "clustering": list(t.clustering_fields or []),
            "modified": t.modified,
        }

    def fingerprint(self, dataset: str, table: str, float_digits: int | None = None) -> dict[str, Any]:
        cols = self.columns(dataset, table)
        ref = table_ref(self.cfg.project, dataset, table)
        (row,) = self.query(f"fingerprint_{table}", fingerprint_sql(ref, cols, float_digits))
        schema_sig = ",".join(f"{c.name}:{c.type}:{c.mode}" for c in sorted(cols, key=lambda c: c.name))
        return {
            "rows": row["row_count"],
            "xor_fp": str(row["xor_fp"]),
            "sum_fp": row["sum_fp"],
            "fingerprint": f"{row['row_count']}:{row['xor_fp']}:{row['sum_fp']}",
            "float_digits": float_digits,
            "columns": len(cols),
            "schema": schema_sig,
        }

    def diff(self, dataset: str, table: str, baseline_dataset: str, float_digits: int | None = None, examples: int = 0) -> dict[str, Any]:
        cur_cols = {c.name: c for c in self.columns(dataset, table)}
        base_cols = {c.name: c for c in self.columns(baseline_dataset, table)}
        common = [cur_cols[n] for n in cur_cols if n in base_cols]
        type_changes = sorted(n for n in cur_cols if n in base_cols and (cur_cols[n].type, cur_cols[n].mode) != (base_cols[n].type, base_cols[n].mode))
        cur_ref = table_ref(self.cfg.project, dataset, table)
        base_ref = table_ref(self.cfg.project, baseline_dataset, table)
        (row,) = self.query(f"diff_{table}", diff_sql(cur_ref, base_ref, common, float_digits))
        out = {
            **row,
            "float_digits": float_digits,
            "columns_only_in_current": sorted(set(cur_cols) - set(base_cols)),
            "columns_only_in_baseline": sorted(set(base_cols) - set(cur_cols)),
            "columns_type_changed": type_changes,
        }
        out["identical"] = (
            row["only_in_current"] == 0
            and row["only_in_baseline"] == 0
            and row["current_rows"] == row["baseline_rows"]
            and row["current_sum_fp"] == row["baseline_sum_fp"]
            and not out["columns_only_in_current"]
            and not out["columns_only_in_baseline"]
            and not type_changes
        )
        if examples and not out["identical"]:
            out["examples"] = self.query(f"diff_examples_{table}", diff_examples_sql(cur_ref, base_ref, common, float_digits, examples))
        return out

    def ensure_dataset(self, dataset: str, description: str, labels: Mapping[str, str]) -> bool:
        """Create the dataset in the US if missing. Returns True when it was created."""
        from google.api_core.exceptions import NotFound
        from google.cloud import bigquery

        ref = f"{self.cfg.project}.{dataset}"
        try:
            ds = self.client.get_dataset(ref)
            if ds.location != LOCATION:
                raise RuntimeError(f"{dataset} is in {ds.location}, not {LOCATION}")
            return False
        except NotFound:
            ds = bigquery.Dataset(ref)
            ds.location = LOCATION
            ds.description = description
            ds.labels = dict(labels)
            self.client.create_dataset(ds)
            return True

    def copy_table(self, dataset: str, table: str, to_dataset: str, expires: datetime | None, labels: Mapping[str, str]) -> dict[str, Any]:
        """A copy job (free), replacing the destination; then the copy's expiration and labels."""
        from google.cloud import bigquery

        src = f"{self.cfg.project}.{dataset}.{table}"
        dst = f"{self.cfg.project}.{to_dataset}.{table}"
        job_config = bigquery.CopyJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            labels={"app": "tagline", "stage": "4", "kind": "bench", "step": label_value(f"snapshot_{table}")},
        )
        job = self.client.copy_table(src, dst, job_config=job_config, project=self.cfg.project, location=LOCATION)
        job.result()
        t = self.client.get_table(dst)
        t.expires = expires
        t.labels = {**(t.labels or {}), **labels}
        self.client.update_table(t, ["expires", "labels"])
        return {"job_id": job.job_id, "rows": t.num_rows, "logical_bytes": t.num_bytes, "expires": expires}
