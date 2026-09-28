"""The run's cost: the Stage 2 cost table for every BigQuery job the run ran, and the Dataproc batch's
usage and wall time (Stage 4's baseline).

Prices are estimates at list price: BigQuery on demand at $6.25 per TiB billed (US multi-region), and the
batch's DCU and shuffle-storage usage (runtimeInfo.approximateUsage, as Dataproc reports it) at the
standard-tier us-central1 rates that `make spark-submit` uses (tagline_spark.submit, one place for them).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from tagline_pipeline.costs import JobStat, format_cost_table
from tagline_spark.submit import DCU_USD_PER_HOUR, SHUFFLE_USD_PER_GIB_HOUR

ON_DEMAND_USD_PER_TIB = 6.25
FAILED_STATES = {"failed", "upstream_failed"}


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def batch_state(value: Any) -> str | None:
    """The batch state's name. The operator's XCom is `Batch.to_dict(batch)`, which writes enums as
    integers (SUCCEEDED is 5, FAILED 6), so the number is turned back into its name."""
    if value is None or value == "":
        return None
    if isinstance(value, str) and not value.isdigit():
        return value.rsplit(".", 1)[-1]  # already a name ("SUCCEEDED", or "State.SUCCEEDED")
    try:
        from google.cloud.dataproc_v1 import Batch

        return Batch.State(int(value)).name
    except (ImportError, ValueError):  # no client library, or a number it does not know
        return str(value)


def _approximate_usage(batch: dict[str, Any] | None) -> dict[str, Any]:
    return ((batch or {}).get("runtime_info") or {}).get("approximate_usage") or {}


def has_usage(batch: dict[str, Any] | None) -> bool:
    return float(_approximate_usage(batch).get("milli_dcu_seconds") or 0) > 0


def settle_usage(
    batch: dict[str, Any] | None,
    fetch: Callable[[str], dict[str, Any]],
    timeout_seconds: float = 180,
    poll_seconds: float = 10,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any] | None:
    """The batch with its usage. Dataproc fills runtimeInfo.approximateUsage shortly after a batch ends, and
    the operator's XCom is taken the moment it ends, so it usually has none (the first full DAG run reported
    0 DCU-hours for a batch that used 0.60). Re-read the batch with `fetch(name)` until the usage is there or
    `timeout_seconds` have passed; a batch that never ran (failed at creation) is re-read once. A failed
    re-read keeps what the XCom said: the summary must not fail a run over a report."""
    if not batch or has_usage(batch) or not batch.get("name"):
        return batch
    deadline = clock() + timeout_seconds
    latest = batch
    while True:
        try:
            latest = fetch(batch["name"])
        except Exception as e:  # noqa: BLE001
            print(f"could not re-read {batch['name']} for its usage: {e}")
            return latest
        ended = batch_state(latest.get("state")) in {"SUCCEEDED", "FAILED", "CANCELLED"}
        ran = any(batch_state(h.get("state")) == "RUNNING" for h in latest.get("state_history") or [])
        if has_usage(latest) or not ended or not ran or clock() >= deadline:
            return latest
        sleep(poll_seconds)


def batch_usage(batch: dict[str, Any] | None) -> dict[str, Any] | None:
    """DCU and shuffle usage, wall time and a list-price estimate from a Batch dict (DataprocCreateBatchOperator's
    XCom, or a re-read of the batch in the same form)."""
    if not batch:
        return None
    usage = _approximate_usage(batch)
    created, ended = _parse_time(batch.get("create_time")), _parse_time(batch.get("state_time"))
    milli_dcu_seconds = float(usage.get("milli_dcu_seconds") or 0)
    shuffle_gb_seconds = float(usage.get("shuffle_storage_gb_seconds") or 0)
    dcu_hours = milli_dcu_seconds / 1000 / 3600
    shuffle_gb_hours = shuffle_gb_seconds / 3600
    return {
        "batch": (batch.get("name") or "").rsplit("/", 1)[-1],
        "state": batch_state(batch.get("state")),
        "wall_seconds": (ended - created).total_seconds() if created and ended else None,
        "dcu_hours": dcu_hours,
        "shuffle_storage_gb_hours": shuffle_gb_hours,
        "usd_estimate": round(dcu_hours * DCU_USD_PER_HOUR + shuffle_gb_hours * SHUFFLE_USD_PER_GIB_HOUR, 4),
    }


def summarize(stats: list[dict[str, Any]], batch: dict[str, Any] | None) -> dict[str, Any]:
    jobs = [JobStat(**s) for s in stats]
    billed = sum(j.bytes_billed or 0 for j in jobs)
    if jobs:
        print("BigQuery jobs (bytes processed / billed, slot-ms, wall seconds):")
        print(format_cost_table(jobs))
    result: dict[str, Any] = {
        "bigquery_jobs": len(jobs),
        "bigquery_bytes_billed": billed,
        "bigquery_usd_estimate": round(billed / 1024**4 * ON_DEMAND_USD_PER_TIB, 4),
        "bigquery_job_seconds": round(sum(j.seconds or 0 for j in jobs), 1),
        "dataproc": batch_usage(batch),
    }
    print(
        f"\nBigQuery: {result['bigquery_jobs']} jobs, {billed / 1024**3:.2f} GiB billed "
        f"(about ${result['bigquery_usd_estimate']:.3f} on demand), {result['bigquery_job_seconds']} s of job time"
    )
    if result["dataproc"]:
        d = result["dataproc"]
        wall = f"{d['wall_seconds']:.0f} s" if d["wall_seconds"] is not None else "-"
        print(
            f"Dataproc: batch {d['batch']} {d['state']}, wall {wall}, {d['dcu_hours']:.3f} DCU-hours, "
            f"{d['shuffle_storage_gb_hours']:.3f} GB-hours shuffle storage (about ${d['usd_estimate']:.3f} at list price)"
        )
    else:
        print("Dataproc: no batch ran in this run")
    return result


def run_states(task_states: dict[str, Any], run_id: str) -> dict[str, str]:
    """{task_id: state} for one run, from RuntimeTaskInstance.get_task_states ({run_id: {task_id: state}}).
    Raises if the run is missing, so an unreadable answer can never pass for "nothing failed"."""
    states = task_states.get(run_id)
    if not states:
        raise RuntimeError(f"no task states returned for run {run_id}: cannot tell whether any task failed")
    return {task_id: str(state).lower().split(".")[-1] for task_id, state in states.items()}


def failed_tasks(states: dict[str, str]) -> list[str]:
    """Task ids that failed, or could not run because an upstream failed."""
    return sorted(task_id for task_id, state in states.items() if state in FAILED_STATES)
