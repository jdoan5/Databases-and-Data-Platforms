"""Run the attribution batch as a named variant and record what it used.

The batch is tagline_spark.batch.batch(), the one definition `make spark-submit` and the DAG use, with the
variant's Spark property overrides applied on top (so an experiment never edits batch.py to be measured) and
two extra labels (bench=stage4, variant=<name>). The TTL and the labels are checked before anything is created.

Recorded per batch: wall, pending and running time (state history), runtimeInfo.approximateUsage (DCU-hours,
shuffle GB-hours, list price), the running usage sampled while polling (runtimeInfo.currentUsage: how many
DCUs were actually allocated), the job's own ATTRIBUTION_SUMMARY line from Cloud Logging (compute_seconds,
write_seconds, row counts), the Spark application id (local-... = local mode, app-... = executors), whether any
worker node logged anything, and the connector's load jobs from INFORMATION_SCHEMA.JOBS_BY_PROJECT."""

from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

SUMMARY_MARKER = "ATTRIBUTION_SUMMARY"
_APP_ID_RE = re.compile(r"\.spark-bigquery-((?:local-\d+)|(?:app-\d{14}-\d{4})|(?:batch-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}))")
_LABEL_RE = re.compile(r"^[a-z0-9_-]{0,63}$")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def parse_kv(items: Sequence[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or ():
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"expected key=value, got {item!r}")
        out[key.strip()] = value.strip()
    return out


def apply_overrides(
    body: Mapping[str, Any],
    set_props: Mapping[str, str] | None = None,
    unset_props: Sequence[str] | None = None,
    labels: Mapping[str, str] | None = None,
    runtime: str | None = None,
    job_args: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """A copy of the batch body with Spark properties set / removed, labels added, job arguments set (`name=value`
    replaces or adds `--name=value`; an empty value removes `--name...`) and, optionally, another runtime.
    Refuses a body without a TTL or without the app label: the cost guards are not optional."""
    out = copy.deepcopy(dict(body))
    props = out.setdefault("runtime_config", {}).setdefault("properties", {})
    for key in unset_props or ():
        props.pop(key, None)
    props.update(set_props or {})
    if job_args:
        batch_args = out.setdefault("pyspark_batch", {}).setdefault("args", [])
        for name, value in job_args.items():
            flag = f"--{name.lstrip('-')}"
            batch_args[:] = [a for a in batch_args if a != flag and not a.startswith(f"{flag}=")]
            if value:
                batch_args.append(f"{flag}={value}")
    if runtime:
        out["runtime_config"]["version"] = runtime
    new_labels = {**out.get("labels", {}), **(labels or {})}
    bad = [f"{k}={v}" for k, v in new_labels.items() if not (_LABEL_RE.fullmatch(k) and _LABEL_RE.fullmatch(v))]
    if bad:
        raise ValueError(f"invalid label(s): {', '.join(bad)}")
    out["labels"] = new_labels
    ttl = out.get("environment_config", {}).get("execution_config", {}).get("ttl", {}).get("seconds")
    if not ttl:
        raise ValueError("the batch has no TTL")
    if new_labels.get("app") != "tagline":
        raise ValueError("the batch has no app=tagline label")
    return out


def parse_summary(message: str) -> dict[str, Any] | None:
    """The JSON after ATTRIBUTION_SUMMARY in a driver log line, or None."""
    idx = message.find(SUMMARY_MARKER)
    if idx < 0:
        return None
    try:
        return json.loads(message[idx + len(SUMMARY_MARKER) :].strip())
    except json.JSONDecodeError:
        return None


def app_id_from(message: str) -> str | None:
    """The Spark application id from the connector's staging path: .spark-bigquery-local-<ms>-<uuid> in local mode,
    .spark-bigquery-app-<yyyymmddhhmmss>-<nnnn>-<uuid> with executors under runtime 2.x's standalone master, and
    .spark-bigquery-batch-<uuid>-<uuid> with executors under runtime 3.0's `dataproc` master."""
    m = _APP_ID_RE.search(message)
    return m.group(1) if m else None


class Logs:
    """Cloud Logging entries.list over REST with application-default credentials (no extra client library)."""

    URL = "https://logging.googleapis.com/v2/entries:list"

    def __init__(self, project: str, session: Any = None) -> None:
        self.project = project
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            session = AuthorizedSession(creds)
        self.session = session

    def entries(self, flt: str, page_size: int = 20) -> list[dict[str, Any]]:
        resp = self.session.post(
            self.URL,
            json={"resourceNames": [f"projects/{self.project}"], "filter": flt, "orderBy": "timestamp desc", "pageSize": page_size},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("entries", [])

    @staticmethod
    def message(entry: Mapping[str, Any]) -> str:
        return entry.get("textPayload") or (entry.get("jsonPayload") or {}).get("message") or ""

    def batch_filter(self, batch_id: str, since: datetime, extra: str = "") -> str:
        since_s = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        base = f'resource.type="cloud_dataproc_batch" AND resource.labels.batch_id="{batch_id}" AND timestamp>="{since_s}"'
        return f"{base} AND {extra}" if extra else base

    def job_facts(self, batch_id: str, since: datetime, wait_seconds: float = 180) -> dict[str, Any]:
        """The summary line (waiting for it to be ingested), the application id and whether a worker logged."""
        deadline = time.monotonic() + wait_seconds
        summary = None
        while summary is None:
            for e in self.entries(self.batch_filter(batch_id, since, f'"{SUMMARY_MARKER}"'), 5):
                summary = parse_summary(self.message(e))
                if summary is not None:
                    break
            if summary is None:
                if time.monotonic() > deadline:
                    break
                time.sleep(15)
        app_id = None
        for e in self.entries(self.batch_filter(batch_id, since, '"spark-bigquery-"'), 5):
            app_id = app_id_from(self.message(e))
            if app_id:
                break
        workers = self.entries(self.batch_filter(batch_id, since, 'labels."dataproc.googleapis.com/resource_name":"-w-"'), 1)
        worker_node = None
        if workers:
            name = (workers[0].get("labels") or {}).get("dataproc.googleapis.com/resource_name", "")
            worker_node = "-".join(name.rsplit("-", 2)[-2:]) if name else None  # e.g. w-0
        # The job's own summary names its master and application id (code from Stage 4 on); older code only shows
        # the application id in the connector's staging paths.
        app_id = (summary or {}).get("app_id") or app_id
        return {
            "summary": summary,
            "app_id": app_id,
            "spark_master": (summary or {}).get("spark_master"),
            "default_parallelism": (summary or {}).get("default_parallelism"),
            "mode": ("local" if app_id.startswith("local-") else "executors") if app_id else None,
            "worker_logged": bool(workers),
            "worker_node_suffix": worker_node,
        }


def _state(b: Any) -> str:
    return b.state.name if hasattr(b.state, "name") else str(b.state)


def usage_sample(b: Any, started: float) -> dict[str, Any]:
    cu = b.runtime_info.current_usage
    return {
        "t": round(time.monotonic() - started, 1),
        "state": _state(b),
        "milli_dcu": cu.milli_dcu,
        "shuffle_storage_gb": cu.shuffle_storage_gb,
    }


def run_batch(target: Any, variant: str, run_index: int, overrides: Mapping[str, Any], bq: Any, logs: Logs, poll_seconds: float = 30) -> dict[str, Any]:
    """Upload the current code, create the batch, poll it (printing every poll), then gather what it used."""
    from tagline_spark import batch as spark_batch
    from tagline_spark import submit

    from .bigquery import label_value

    version = submit.upload(target)
    body = spark_batch.batch(target, orchestrator="bench", version=version)
    body = apply_overrides(
        body,
        overrides.get("set_props"),
        overrides.get("unset_props"),
        {"bench": "stage4", "variant": label_value(variant), **(overrides.get("labels") or {})},
        overrides.get("runtime"),
        overrides.get("job_args"),
    )
    batch_id = spark_batch.batch_id(f"bench__{datetime.now(UTC):%Y-%m-%dT%H:%M:%S}__{variant}", run_index)
    client = submit._client(target)
    client.create_batch(request={"parent": target.parent, "batch": body, "batch_id": batch_id})
    name = f"{target.parent}/batches/{batch_id}"
    ttl = body["environment_config"]["execution_config"]["ttl"]["seconds"]
    print(f"  batch {batch_id}: runtime {body['runtime_config']['version']}, code {version}, TTL {ttl // 60} min", flush=True)
    started = time.monotonic()
    samples: list[dict[str, Any]] = []
    try:
        while True:
            b = client.get_batch(name=name)
            s = usage_sample(b, started)
            samples.append(s)
            print(f"  {s['t']:6.0f} s  {s['state']:<9}  current usage {s['milli_dcu'] / 1000:.1f} DCU, {s['shuffle_storage_gb']} GB shuffle", flush=True)
            if s["state"] in TERMINAL:
                break
            time.sleep(poll_seconds)
    except BaseException:
        b = client.get_batch(name=name)
        if _state(b) not in TERMINAL:
            client.cancel_operation(request={"name": b.operation})
            print(f"  cancel requested for {batch_id}", flush=True)
        raise
    rec = describe_batch(client, name, bq, logs, samples)
    # what was submitted, as submitted (the same values the batch resource carries)
    rec.update(
        code_version=version,
        runtime=body["runtime_config"]["version"],
        properties=body["runtime_config"]["properties"],
        labels=body["labels"],
        ttl_seconds=ttl,
        job_args=body["pyspark_batch"].get("args", []),
    )
    return rec


def describe_batch(client: Any, name: str, bq: Any, logs: Logs, samples: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What a finished batch used, read back from Dataproc, Cloud Logging and JOBS_BY_PROJECT: the fields of a
    spark_run record. Also records a batch the harness did not start (the Airflow DAG's: `tagline_bench batch`)."""
    from tagline_spark import submit

    batch_id = name.rsplit("/", 1)[-1]
    b = client.get_batch(name=name)
    deadline = time.monotonic() + 180
    while _state(b) in TERMINAL and not b.runtime_info.approximate_usage.milli_dcu_seconds and time.monotonic() < deadline:
        time.sleep(10)
        b = client.get_batch(name=name)
    u = submit.usage(b)
    created = u["created"]
    ended = created + timedelta(seconds=u["wall_seconds"] or 0)
    facts = logs.job_facts(batch_id, created - timedelta(minutes=1))
    loads = bq.jobs_by_window(created, ended + timedelta(minutes=2), {"stage": "3", "kind": "spark"}, job_types=("LOAD",))
    running = u["running_seconds"]
    summary = facts.get("summary") or {}
    ttl = b.environment_config.execution_config.ttl
    labels = dict(b.labels)
    return {
        "batch_id": batch_id,
        "code_version": labels.get("code"),
        "runtime": b.runtime_config.version,
        "properties": dict(b.runtime_config.properties),
        "labels": labels,
        "ttl_seconds": int(ttl.total_seconds()) if hasattr(ttl, "total_seconds") else getattr(ttl, "seconds", None),
        "state": u["state"],
        "state_message": u["state_message"],
        "created": created,
        "wall_seconds": u["wall_seconds"],
        "pending_seconds": u["pending_seconds"],
        "running_seconds": running,
        "milli_dcu_seconds": u["milli_dcu_seconds"],
        "dcu_hours": u["dcu_hours"],
        "avg_dcu_while_running": (u["milli_dcu_seconds"] / 1000 / running) if running else None,
        "shuffle_storage_gb_seconds": u["shuffle_storage_gb_seconds"],
        "shuffle_gib_hours": u["shuffle_gib_hours"],
        "usd": u["usd_estimate"],
        "compute_seconds": summary.get("compute_seconds"),
        "write_seconds": summary.get("write_seconds"),
        "summary": summary,
        "job_args": list(b.pyspark_batch.args),
        "app_id": facts.get("app_id"),
        "spark_master": facts.get("spark_master"),
        "default_parallelism": facts.get("default_parallelism"),
        "mode": facts.get("mode"),
        "worker_logged": facts.get("worker_logged"),
        # A load job runs in BigQuery's shared (free) pool, so it can wait before it starts: queued_seconds.
        "load_jobs": [
            {k: j[k] for k in ("step", "job_id", "created", "started", "ended", "queued_seconds", "elapsed_seconds", "slot_ms")} for j in loads
        ],
        "usage_samples": samples or [],
    }
