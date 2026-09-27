"""Build the models in order, run the checks, print the reports."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from google.api_core.exceptions import NotFound

from .bq import BigQuery
from .config import SAMPLE_TABLE, SQL_DIR, Config
from .costs import JobStat
from .sources import SiteTables, classify_site_tables, site_union_sql
from .sqlfiles import list_models, list_sql, parse_doc, render


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
    return result


@dataclass
class CheckResult:
    name: str
    description: str
    failures: list[dict]


def run_checks(
    cfg: Config, bq: BigQuery, site: SiteTables | None = None, stats: list[JobStat] | None = None
) -> tuple[list[CheckResult], list[JobStat]]:
    """Each check is a query that returns no rows when the check passes. Jobs are appended to `stats` if given."""
    ctx = context(cfg, site)
    results: list[CheckResult] = []
    stats = stats if stats is not None else []
    for path in list_sql(SQL_DIR / "checks"):
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
