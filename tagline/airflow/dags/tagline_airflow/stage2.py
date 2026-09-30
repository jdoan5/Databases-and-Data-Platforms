"""The Stage 2 pipeline, seen from Airflow.

The DAG does not copy any Stage 2 SQL or logic: it lists the model and check files through the
Stage 2 package (tagline/pipeline, mounted read-only and on PYTHONPATH), renders them with the
package's own `render` and `context`, and applies the package's table documentation. What this
module adds is what Airflow needs on top:

* `model_dependencies`: which model reads which, read from the SQL itself, so the graph's edges
  are the models' real lineage rather than a hand-kept list;
* the site-export tables in a form that fits in an XCom;
* the name of the GA4 export table a run waits for, and which day that is for a run with or
  without a logical date.

Everything here but `prepare_sources` is pure and unit tested (tests/test_dag.py).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from tagline_pipeline.config import SQL_DIR, Config
from tagline_pipeline.sources import SiteTables
from tagline_pipeline.sqlfiles import Model, list_models, list_sql

MODELS: list[Model] = list_models(SQL_DIR)
CHECKS: list[Path] = list_sql(SQL_DIR / "checks")

# `{{ staging }}.stg_events`, `{{ marts }}.fct_sessions`: a reference to a table another model builds.
_TABLE_REF = re.compile(r"\{\{\s*(?:staging|marts)\s*\}\}\.([a-z][a-z0-9_]*)")


def model_dependencies(models: list[Model]) -> dict[str, list[str]]:
    """For each model, the models whose tables its SQL reads (its own CREATE target excluded).

    A model may only read models that build before it (the numeric prefix is the build order
    `make build` uses); anything else would be a cycle or a build that reads stale data, so it
    is an error here rather than a silently different order in Airflow."""
    names = [m.name for m in models]
    order = {name: i for i, name in enumerate(names)}
    deps: dict[str, list[str]] = {}
    for model in models:
        refs = {r for r in _TABLE_REF.findall(model.sql()) if r in order and r != model.name}
        later = sorted(r for r in refs if order[r] > order[model.name])
        if later:
            raise ValueError(f"{model.path.name} reads {', '.join(later)}, which build(s) after it")
        deps[model.name] = sorted(refs, key=order.__getitem__)
    return deps


def check_task_id(path: Path) -> str:
    """01_keys_unique.sql -> keys_unique (the numeric prefix is only the run order)."""
    return re.sub(r"^\d+_", "", path.stem)


def leaf_models(deps: dict[str, list[str]]) -> list[str]:
    """Models no other model reads: the checks wait on these (and so on every model)."""
    read = {up for ups in deps.values() for up in ups}
    return [name for name in deps if name not in read]


# -- the site export -----------------------------------------------------------------------


def site_to_xcom(site: SiteTables | None) -> dict[str, list[str]] | None:
    if site is None:
        return None
    return {"daily": list(site.daily), "intraday_only": list(site.intraday_only)}


def site_from_xcom(value: dict[str, list[str]] | None) -> SiteTables | None:
    if not value:
        return None
    return SiteTables(daily=tuple(value.get("daily", ())), intraday_only=tuple(value.get("intraday_only", ())))


def run_moment(context: Mapping[str, Any]) -> datetime:
    """The moment a run is for: its logical date, else the time it was set to run (`dag_run.run_after`).

    Airflow 3 lets a run be triggered by hand or through the API with no logical date, and then
    leaves `logical_date` out of the task context altogether, so `context["logical_date"]` would
    raise KeyError. For such a run `run_after` is when it was triggered."""
    return context.get("logical_date") or context["dag_run"].run_after


def export_day(logical_date: datetime) -> date:
    """The GA4 export day a run waits for: the day before the run's logical date.

    With a cron schedule Airflow 3 sets the logical date to the moment the run is due (10:00 UTC
    here), so this is the day that has just ended. GA4 writes that day's daily table some hours
    into the next day (and the streaming table during the day, when streaming is on)."""
    return (logical_date - timedelta(days=1)).date()


def export_tables(day: date, prefix: str = "events_") -> tuple[str, str]:
    """(daily table, streaming table) for a day: events_20260927, events_intraday_20260927."""
    suffix = day.strftime("%Y%m%d")
    return f"{prefix}{suffix}", f"{prefix}intraday_{suffix}"


def run_incremental(cfg: Config, client) -> list[dict[str, Any]]:
    """The daily incremental build (`make build-incremental`), labelled orchestrator=airflow. Returns one cost
    record per statement of its script, for run_summary. Raises WindowError, or SystemExit when a table is missing
    (a full refresh has to build them first)."""
    from dataclasses import asdict

    from tagline_pipeline import pipeline
    from tagline_pipeline.bq import BigQuery
    from tagline_pipeline.costs import format_cost_table

    bq = BigQuery(cfg, client=client, extra_labels={"orchestrator": "airflow"})
    result = pipeline.build_incremental(cfg, bq)
    if result.stats:
        print(format_cost_table(result.stats))
    return [asdict(s) for s in result.stats]


def prepare_sources(cfg: Config, client) -> dict[str, list[str]] | None:
    """What `make build` does before the first model: create the three datasets if missing (and align
    their descriptions and labels), then list the site export's tables. Returns the XCom form."""
    from tagline_pipeline import pipeline
    from tagline_pipeline.bq import BigQuery

    bq = BigQuery(cfg, client=client)
    created = bq.ensure_datasets()
    if created:
        pipeline.log(f"created dataset(s) {', '.join(created)} in {cfg.location}")
    return site_to_xcom(pipeline.discover_site(cfg, bq))
