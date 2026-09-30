"""Build the models in order, run the checks, print the reports."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from google.api_core.exceptions import NotFound

from . import incremental
from .bq import BigQuery
from .config import SAMPLE_TABLE, SQL_DIR, Config
from .costs import JobStat
from .sources import SiteTables, classify_site_tables, site_union_sql
from .sqlfiles import Model, list_models, list_sql, parse_doc, render


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def discover_site(cfg: Config, bq: BigQuery) -> SiteTables | None:
    """The site export's tables, or None when TAGLINE_GA4_DATASET is unset or has no event tables yet."""
    if not cfg.has_site:
        log("site export: TAGLINE_GA4_DATASET is not set, building from the GA4 sample only")
        return None
    tables = classify_site_tables(bq.list_table_ids(cfg.site_project, cfg.ga4_dataset), cfg.ga4_table_prefix)
    if tables.empty:
        log(f"site export: {cfg.site_project}.{cfg.ga4_dataset} has no {cfg.ga4_table_prefix}* tables yet, sample only")
        return None
    log(
        f"site export: {cfg.site_project}.{cfg.ga4_dataset}: {len(tables.daily)} daily table(s)"
        + (f" {tables.daily[0]}..{tables.daily[-1]}" if tables.daily else "")
        + f", {len(tables.intraday_only)} intraday-only day(s) {', '.join(tables.intraday_only)}".rstrip()
    )
    return tables


def context(cfg: Config, site: SiteTables | None) -> dict[str, object]:
    return {
        "project": cfg.project,
        "raw": cfg.raw_dataset,
        "staging": cfg.staging_dataset,
        "marts": cfg.marts_dataset,
        "sample_table": SAMPLE_TABLE,
        "sample_start": cfg.sample_start,
        "sample_end": cfg.sample_end,
        "site_union": site_union_sql(cfg, site),
        # Hooks the daily incremental build fills in (tagline_pipeline/incremental.py); a full build leaves them empty.
        "incremental_filter": "",
        "purchase_history": "",
    }


def _sources_note(cfg: Config, site: SiteTables | None) -> str:
    note = f"Sources in this build: ga4_sample ({cfg.sample_start}-{cfg.sample_end})"
    if site is not None:
        note += f", tagline_site ({cfg.site_project}.{cfg.ga4_dataset}, {cfg.ga4_table_prefix}*)"
    return note + "."


@dataclass
class BuildResult:
    stats: list[JobStat] = field(default_factory=list)
    site: SiteTables | None = None
    window: incremental.Window | None = None  # set by build_incremental


def staged_table_id(cfg: Config) -> str:
    return f"{cfg.project}.{cfg.staging_dataset}.{incremental.STAGED_TABLE}"


def export_state(cfg: Config, bq: BigQuery, site: SiteTables | None, stats: list[JobStat] | None = None) -> dict[str, incremental.ExportDay]:
    """What a build is about to read from the site's export: per day, the table and its metadata now (__TABLES__,
    0 bytes). Taken before the read, so a change during the read shows as a change next time."""
    if site is None:
        return {}
    meta, stat = bq.table_metadata(cfg.site_project, cfg.ga4_dataset, cfg.ga4_table_prefix)
    if stats is not None:
        stats.append(stat)
    return incremental.export_state(site, cfg.ga4_table_prefix, meta)


def record_staged(cfg: Config, bq: BigQuery, state: dict[str, incremental.ExportDay], stats: list[JobStat] | None = None) -> None:
    """A full build's record of what stg_events was built from: staged_export_days replaced (a DDL job that reads no
    table). The daily build keeps it up to date inside its own transaction."""
    _, stat = bq.query(incremental.STAGED_TABLE, "record", incremental.staged_table_sql(f"`{staged_table_id(cfg)}`", state.values()))
    if stats is not None:
        stats.append(stat)


def build(
    cfg: Config,
    bq: BigQuery,
    *,
    dry_run: bool = False,
    start_at: str | None = None,
    only: str | None = None,
    stats: list[JobStat] | None = None,
) -> BuildResult:
    """Build the models in order. Each finished job is appended to `stats` (if given) as it completes, so a
    caller still has the cost of the jobs that ran when a later one fails.

    A dry run creates nothing. BigQuery validates a model against the tables it reads, so a dry run stops,
    with a message, at the first model whose inputs do not exist yet; after a build, later models are
    estimated against the tables as last built, not as this build would make them."""
    if dry_run:
        missing = bq.missing_datasets()
        if missing:
            log(f"dry run: dataset(s) {', '.join(missing)} do not exist yet (make build creates them); nothing was created")
    else:
        created = bq.ensure_datasets()
        if created:
            log(f"created dataset(s) {', '.join(created)} in {cfg.location}")
    result = BuildResult(stats=stats if stats is not None else [], site=discover_site(cfg, bq))
    ctx = context(cfg, result.site)
    models = list_models(SQL_DIR)
    names = [m.name for m in models]
    for wanted in (start_at, only):
        if wanted is not None and wanted not in names:
            raise SystemExit(f"no model named {wanted!r}; models: {', '.join(names)}")
    if only is not None:
        models = [m for m in models if m.name == only]
    elif start_at is not None:
        models = models[names.index(start_at) :]
    # what stg_events is about to read from the site's export, recorded once it has (the daily build compares with it)
    staged = export_state(cfg, bq, result.site, result.stats) if not dry_run and any(m.name == "stg_events" for m in models) else None
    for model in models:
        dataset = cfg.staging_dataset if model.layer == "staging" else cfg.marts_dataset
        table_id = f"{cfg.project}.{dataset}.{model.name}"
        log(f"{'dry run' if dry_run else 'build'} {table_id}")
        sql_text = model.sql()
        try:
            _, stat = bq.query(model.name, "model", render(sql_text, ctx), dry_run=dry_run)
        except NotFound as e:
            if not dry_run:
                raise
            log(
                f"dry run stopped at {model.name}: a table or dataset it needs does not exist yet "
                f"({e.message.splitlines()[0] if e.message else e}). A dry run validates each model against the "
                "tables it reads, so run `make build` once first."
            )
            break
        result.stats.append(stat)
        if not dry_run:
            bq.apply_docs(table_id, parse_doc(sql_text), suffix=_sources_note(cfg, result.site))
            stat.rows = bq.num_rows(table_id)
            if model.name == "stg_events" and staged is not None:
                record_staged(cfg, bq, staged, result.stats)
    return result


def loaded_days(
    cfg: Config, bq: BigQuery, days_by_source: dict[str, list[str]], parts: Iterable[str], stats: list[JobStat] | None = None
) -> dict[str, set[str]]:
    """The export days stg_events already holds, per source: its non-empty date partitions (`parts`, from a metadata
    query). A day that two sources both have an export table for cannot be told apart that way, so for those days
    (none so far: the sample is 2020-21, the site 2026) the sources are read from the partitions themselves."""
    parts = set(parts)
    count: dict[str, int] = {}
    for days in days_by_source.values():
        for d in set(days):
            count[d] = count.get(d, 0) + 1
    shared = sorted(d for d, n in count.items() if n > 1 and d in parts)
    exact: dict[str, set[str]] = {}
    if shared:
        dates = ", ".join(f"DATE '{d[:4]}-{d[4:6]}-{d[6:]}'" for d in shared)
        rows, stat = bq.query(
            "loaded_days",
            "metadata",
            f"SELECT DISTINCT source, FORMAT_DATE('%Y%m%d', event_date) AS day "
            f"FROM `{cfg.project}.{cfg.staging_dataset}.stg_events` WHERE event_date IN ({dates})",
        )
        if stats is not None:
            stats.append(stat)
        for r in rows:
            exact.setdefault(r["source"], set()).add(r["day"])
    return {
        source: {d for d in days if d in parts and count[d] == 1} | (exact.get(source, set()) & set(days))
        for source, days in days_by_source.items()
    }


def build_incremental(
    cfg: Config,
    bq: BigQuery,
    *,
    since: str | None = None,
    lookback: int = incremental.DEFAULT_LOOKBACK_DAYS,
    stats: list[JobStat] | None = None,
    print_script: bool = False,
) -> BuildResult:
    """The daily path (tagline_pipeline/incremental.py): work out the window of export days to (re)process and apply
    it to every table in one BigQuery script, in one transaction. Needs every table from an earlier full build.
    With print_script, print the script instead of running it."""
    if lookback < 1:
        raise incremental.WindowError("lookback must be at least 1 day")
    created = bq.ensure_datasets()
    if created:
        raise SystemExit(f"dataset(s) {', '.join(created)} did not exist: run a full build first (make build)")
    result = BuildResult(stats=stats if stats is not None else [], site=discover_site(cfg, bq))
    models = {m.name: m for m in list_models(SQL_DIR)}

    def table_id(m: Model) -> str:
        return f"{cfg.project}.{cfg.staging_dataset if m.layer == 'staging' else cfg.marts_dataset}.{m.name}"

    missing = [name for name, m in models.items() if not bq.table_exists(table_id(m))]
    if missing:
        raise SystemExit(f"table(s) {', '.join(missing)} do not exist yet: run a full build first (make build)")
    days = incremental.export_days(cfg, result.site)
    parts, stat = bq.partition_ids(cfg.staging_dataset, "stg_events")
    result.stats.append(stat)
    loaded = loaded_days(cfg, bq, days, parts, result.stats)
    # what this run is about to read, and what was read when each loaded day was staged
    current = export_state(cfg, bq, result.site, result.stats)
    has_record = bq.table_exists(staged_table_id(cfg))
    recorded = incremental.recorded_state(bq.read_rows(staged_table_id(cfg))) if has_record else {}
    if not has_record and loaded.get(incremental.SITE):
        log(f"no {incremental.STAGED_TABLE} yet (tables built before it existed): every loaded site day is read again, once")
    changed = incremental.changed_days(current, recorded, loaded.get(incremental.SITE, set()))
    window = incremental.plan_window(
        days,
        loaded,
        since=since,
        lookback=lookback,
        changed_by_source={incremental.SITE: changed},
        anchor_by_source=incremental.lookback_anchors(result.site),
    )
    result.window = window
    log(f"incremental window: {window.describe()}")
    for note in window.notes:
        log(f"  {note}")
    gone = incremental.vanished_days(current, recorded)
    if gone:
        log(f"  tagline_site: {len(gone)} staged day(s) no longer have an export table ({', '.join(gone)}); their rows stay "
            "unless the window starts on or before them (a full build drops them)")
    if window.empty:
        return result
    site_window = window.get(incremental.SITE)
    staged = {d: current[d] for d in site_window.days if d in current} if site_window is not None else None
    sql = incremental.script(cfg, models, context(cfg, result.site), result.site, window, staged)
    if print_script:
        print(sql)
        return result
    if not has_record:
        record_staged(cfg, bq, {}, result.stats)  # empty: the script fills in the window's days
    try:
        bq.script("incremental", "incremental", sql)
    finally:
        result.stats.extend(bq.last_script_statements)
    note = _sources_note(cfg, result.site)
    for m in models.values():
        bq.apply_docs(table_id(m), parse_doc(m.sql()), suffix=note)
    return result


@dataclass
class CheckResult:
    name: str
    description: str
    failures: list[dict]


def run_checks(
    cfg: Config,
    bq: BigQuery,
    site: SiteTables | None = None,
    stats: list[JobStat] | None = None,
    directory: Path | None = None,
) -> tuple[list[CheckResult], list[JobStat]]:
    """Each check is a query that returns no rows when the check passes. Jobs are appended to `stats` if given.
    `directory` runs another directory of checks written the same way (the DAG's attribution checks)."""
    ctx = context(cfg, site)
    results: list[CheckResult] = []
    stats = stats if stats is not None else []
    files = list_sql(directory if directory is not None else SQL_DIR / "checks")
    if not files:
        raise SystemExit(f"no .sql checks in {directory}")
    for path in files:
        text = path.read_text(encoding="utf-8")
        rows, stat = bq.query(path.stem, "check", render(text, ctx))
        results.append(CheckResult(path.stem, parse_doc(text).check, [dict(r.items()) for r in rows]))
        stats.append(stat)
    return results, stats


def format_checks(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"{'PASS' if not r.failures else 'FAIL'}  {r.name}: {r.description}")
        for row in r.failures[:10]:
            lines.append("        " + ", ".join(f"{k}={v}" for k, v in row.items()))
        if len(r.failures) > 10:
            lines.append(f"        ... {len(r.failures) - 10} more")
    passed = sum(1 for r in results if not r.failures)
    lines.append(f"{passed}/{len(results)} checks passed")
    return "\n".join(lines)


def run_reports(cfg: Config, bq: BigQuery) -> tuple[list[tuple[str, str, list[dict]]], list[JobStat]]:
    ctx = context(cfg, None)
    out, stats = [], []
    for path in list_sql(SQL_DIR / "reports"):
        text = path.read_text(encoding="utf-8")
        rows, stat = bq.query(path.stem, "report", render(text, ctx))
        out.append((path.stem, parse_doc(text).table, [dict(r.items()) for r in rows]))
        stats.append(stat)
    return out, stats


def format_rows(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    headers = list(rows[0])

    def cell(v: object) -> str:
        if isinstance(v, float):
            return f"{v:,.2f}"
        if isinstance(v, int) and not isinstance(v, bool):
            return f"{v:,}"
        return "NULL" if v is None else str(v)

    table = [[cell(r[h]) for h in headers] for r in rows]
    widths = [max(len(h), *(len(row[i]) for row in table)) for i, h in enumerate(headers)]
    numeric = [all(isinstance(r[h], (int, float)) and not isinstance(r[h], bool) or r[h] is None for r in rows) for h in headers]

    def fmt(row: list[str]) -> str:
        return "  ".join(c.rjust(w) if n else c.ljust(w) for c, w, n in zip(row, widths, numeric)).rstrip()

    return "\n".join([fmt(headers), "  ".join("-" * w for w in widths), *(fmt(r) for r in table)])
