"""The results log: one JSON object per line in bench/results/<variant>.jsonl, appended, never rewritten.

Every record passes through scrub() first, so the project id, bucket, service account and the GA4 export's
dataset name never reach a committed file, whatever an error message or a table path carries."""

from __future__ import annotations

import json
import statistics
import subprocess
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import RESULTS_DIR, TAGLINE_DIR

SCHEMA_VERSION = 1


def now_utc() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def scrub(value: Any, secrets: Mapping[str, str]) -> Any:
    """Replace every secret substring in every string (keys included), longest first."""
    order = sorted(secrets, key=len, reverse=True)

    def s(text: str) -> str:
        for k in order:
            text = text.replace(k, secrets[k])
        return text

    if isinstance(value, str):
        return s(value)
    if isinstance(value, Mapping):
        return {s(str(k)): scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    return value


def variant_file(variant: str, results_dir: Path = RESULTS_DIR) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in variant)
    return results_dir / f"{safe}.jsonl"


def append(record: Mapping[str, Any], secrets: Mapping[str, str], results_dir: Path = RESULTS_DIR) -> Path:
    """Append one record to its variant's log; returns the file."""
    rec = {"schema": SCHEMA_VERSION, "recorded_at": now_utc(), **record}
    rec = scrub(_jsonable(rec), secrets)
    path = variant_file(str(rec["variant"]), results_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return path


def load(paths: Iterable[Path] | None = None, results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    files = sorted(results_dir.glob("*.jsonl")) if paths is None else list(paths)
    out = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def stats(values: Iterable[float | int | None]) -> dict[str, float | int | None]:
    """n, median, min, max of the non-null values (the median of an even count is the mean of the middle two)."""
    xs = [v for v in values if v is not None]
    if not xs:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {"n": len(xs), "median": statistics.median(xs), "min": min(xs), "max": max(xs)}


def spread_pct(s: Mapping[str, Any]) -> float | None:
    """(max - min) / median, in percent: how noisy a measurement is."""
    if not s.get("n") or not s.get("median"):
        return None
    return (s["max"] - s["min"]) / s["median"] * 100


def git_state() -> dict[str, Any]:
    """The commit and whether tagline/ has uncommitted changes (read-only git commands)."""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=TAGLINE_DIR, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=TAGLINE_DIR, capture_output=True, text=True, check=True).stdout
        changed = [l[3:] for l in dirty.splitlines()]
        # What runs: the pipeline, the Spark job, the DAG. Changes to the harness, docs or results do not count.
        code = [f for f in changed if f.startswith(("tagline/pipeline/", "tagline/spark/", "tagline/airflow/"))]
        return {"commit": commit, "tagline_dirty": bool(changed), "code_dirty": bool(code), "changed_files": changed[:50]}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "tagline_dirty": None, "code_dirty": None, "changed_files": []}
