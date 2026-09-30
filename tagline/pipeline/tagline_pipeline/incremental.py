"""The daily incremental build: process only the export days that are new or may have changed, not the whole
history (Stage 4, experiment 1).

`make build` (a full refresh) rebuilds every table from every export day. `make build-incremental` works out a
**window** per source and runs one BigQuery script that brings every table up to date for it, inside one
transaction, so either every table moves forward or none does:

* **What was staged.** Every build that writes stg_events also writes `staged_export_days` (in the staging
  dataset): one row per site export day it read, naming the table it read (the daily table, else the streaming
  one) and that table's last modification time and row count, taken from the export dataset's `__TABLES__`
  (metadata, 0 bytes) *before* the build read the table. The daily script writes its window's rows in the same
  transaction as the tables, so the record always describes what stg_events holds. Taking the metadata before the
  read errs one way only: a change that lands during the read makes the table look changed next time, never the
  reverse.
* **Which days.** For each source, the window starts at the earliest day that needs processing and runs to the
  source's newest export day, so everything before the window is untouched history and everything in it is
  replaced. A day needs processing when
  - it has an export table but no rows in stg_events yet (a new day, or a gap after missed runs);
  - it is loaded, but the table a build would read for it now is not the one recorded when it was staged: its
    daily table landed after the day was read from its streaming table, or the table was modified since (GA4
    added late events or reprocessed the day, someone backfilled it) or has another row count, or the day has no
    record (staged by a build older than the record). This is what keeps a change outside the lookback, or a
    run that did not happen, from leaving stale rows behind;
  - for the site, it is one of the `lookback` days (default 4) counted back from the newest day that has a
    daily table: a floor under the rule above. GA4 updates a daily table through the third day after its date
    (Google: table 20220101 is updated through 20220104), so the run on the fourth day after is the first to
    read the final table; counting from the newest *daily* table keeps a streaming table for today from
    shortening that. A streaming table is always in the window (it is newer than every daily table), which
    matters because its streaming buffer is not in the metadata.
  The public sample is static, so it is only processed when days of it are missing or `--since` asks for it.
  `--since YYYYMMDD` sets the window start for every source by hand. A lookback below 1 is refused.
  A day whose export table has gone (deleted, or expired) is reported, not acted on: its rows stay unless the
  window starts at or before it (docs/data-model.md, "When to run a full build").
* **What each table does** (the models' own SELECTs, restricted; see the model files for the hooks):

  | table | how | why |
  |---|---|---|
  | stg_events | replace the window's date partitions; recompute the collision flag and order_id of earlier purchases whose transaction_id the window now also shows on another device (rare) | rows are per export row; the purchase dedupe looks back through int_purchases |
  | stg_items, int_purchases, int_device_days | replace the window's date partitions (int_purchases also takes the collision updates) | rows are per event, or per device and day |
  | int_identity | whole table, added up from int_device_days (about 20 MiB) | a sign-in today changes the device's earlier events' person, and person_device_count spans devices |
  | fct_sessions | recompute every session with an event in the window (old or new), from the date partitions those sessions span; re-resolve the person of every other session on a device whose identity changed | sessions cross midnight; identity reaches back |
  | fct_orders | whole table, from int_purchases, fct_sessions and int_identity | small once it reads int_purchases instead of stg_events |
  | fct_order_items | the lines of every order that is new, gone or changed, from the stg_items partitions of those orders' dates | lines carry the order's id, person and session |
  | mart_campaign_daily, mart_funnel_daily | whole tables | a few MiB of fct_sessions |
  | staged_export_days | the site window's days replaced by what this run read | the next run compares the export with it |

The script, and the window, are pure functions of their inputs, so both are unit tested without BigQuery.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .config import Config
from .sources import SiteTables, site_union_sql
from .sqlfiles import Model, TemplateError, render

SAMPLE, SITE = "ga4_sample", "tagline_site"
# A floor under the staged-metadata rule: GA4 updates a daily table through the third day after its date, so the run
# on the fourth day reads the final table (support.google.com/analytics/answer/9358801). Counted back from the newest
# day with a daily table.
DEFAULT_LOOKBACK_DAYS = 4

_DAY = re.compile(r"^\d{8}$")


def _d(day: str) -> date:
    return datetime.strptime(day, "%Y%m%d").date()


def _s(day: date) -> str:
    return day.strftime("%Y%m%d")


def calendar_days(start: str, end: str) -> list[str]:
    """Every YYYYMMDD from start to end, inclusive."""
    first, last = _d(start), _d(end)
    return [_s(first + timedelta(days=i)) for i in range((last - first).days + 1)]


def export_days(cfg: Config, site: SiteTables | None) -> dict[str, list[str]]:
    """The days each source has an export table for. The sample has one table per day of its range."""
    out = {SAMPLE: calendar_days(cfg.sample_start, cfg.sample_end)}
    if site is not None and not site.empty:
        out[SITE] = sorted(set(site.daily) | set(site.intraday_only))
    return out


def lookback_anchors(site: SiteTables | None) -> dict[str, str]:
    """The day each source's lookback counts back from: the site's newest day with a daily table. A streaming table
    for a later day does not move it (with streaming on, today's table would otherwise shorten the days re-read)."""
    return {SITE: site.daily[-1]} if site is not None and site.daily else {}


# -- what was staged -------------------------------------------------------------------------------------------

STAGED_TABLE = "staged_export_days"  # in the staging dataset
STAGED_DESCRIPTION = (
    "Tagline Stage 4: one row per site export day in stg_events: the export table the build read for it and that "
    "table's metadata, taken before the read. make build-incremental re-reads a day whose export table no longer "
    "matches its row. Written by every build that writes stg_events (docs/data-model.md, Incremental builds)."
)
# (column, type, description); the order is the table's
STAGED_COLUMNS = (
    ("source", "STRING", "Always tagline_site: the public sample is static and needs no record."),
    ("export_day", "DATE", "The export day (the table's YYYYMMDD suffix): stg_events' rows of this event_date for the source."),
    ("export_table", "STRING", "daily (events_YYYYMMDD) or intraday (events_intraday_YYYYMMDD): the table the build read for the day."),
    ("table_id", "STRING", "That table's name in the export dataset."),
    ("last_modified_time", "TIMESTAMP", "The table's last modification time in the export dataset's __TABLES__, read before the build read the table."),
    ("row_count", "INT64", "The table's row count from the same metadata (a streaming table's buffer is not counted there)."),
    ("staged_at", "TIMESTAMP", "When the build wrote this row."),
)
_TABLE_ID = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class ExportDay:
    """The table a build reads for one site export day, and that table's metadata."""

    day: str  # YYYYMMDD
    export_table: str  # daily | intraday
    table_id: str
    last_modified: datetime
    row_count: int

    def key(self) -> tuple[str, datetime, int]:
        return self.table_id, self.last_modified, self.row_count


def export_state(site: SiteTables | None, prefix: str, meta: Mapping[str, tuple[datetime, int]]) -> dict[str, ExportDay]:
    """Per site export day, the table a build reads for it (the daily table, else the streaming one) with its metadata
    now. meta: table name -> (last modified, row count), from the export dataset's __TABLES__. A table listed by the
    build but gone from the metadata is left out, so the day is not recorded and the next run reads it again."""
    if site is None:
        return {}
    out: dict[str, ExportDay] = {}
    for kind, days, name in (("daily", site.daily, "{p}{d}"), ("intraday", site.intraday_only, "{p}intraday_{d}")):
        for d in days:
            table = name.format(p=prefix, d=d)
            if table in meta:
                modified, rows = meta[table]
                out[d] = ExportDay(d, kind, table, modified, int(rows or 0))
    return out


def recorded_state(rows: Iterable[Mapping[str, object]]) -> dict[str, ExportDay]:
    """staged_export_days' rows for the site, per export day."""
    out: dict[str, ExportDay] = {}
    for r in rows:
        if r["source"] != SITE:
            continue
        d = r["export_day"]
        day = _s(d) if isinstance(d, date) else str(d).replace("-", "")
        out[day] = ExportDay(day, str(r["export_table"]), str(r["table_id"]), r["last_modified_time"], int(r["row_count"]))  # type: ignore[arg-type]
    return out


def changed_days(current: Mapping[str, ExportDay], recorded: Mapping[str, ExportDay], loaded: Iterable[str]) -> set[str]:
    """The loaded site days to read again: the table a build would read for the day now is not the one recorded when
    the day was staged (its daily table landed after it was read from the streaming one), or it was modified since
    or has another row count, or the day has no record (staged by a build older than the record). A loaded day with
    no export table now is not here (vanished_days): there is nothing to read."""
    out: set[str] = set()
    for d in loaded:
        now, was = current.get(d), recorded.get(d)
        if now is not None and (was is None or was.key() != now.key()):
            out.add(d)
    return out


def vanished_days(current: Mapping[str, ExportDay], recorded: Mapping[str, ExportDay]) -> list[str]:
    """Days staged earlier whose export table is gone (deleted or expired)."""
    return sorted(set(recorded) - set(current))


def _ts(t: datetime) -> str:
    return t.isoformat(sep=" ", timespec="microseconds")


def staged_rows_sql(state: Iterable[ExportDay]) -> str:
    """A SELECT of staged_export_days rows for these days (literals; staged_at is the time it runs)."""
    rows = []
    for x in sorted(state, key=lambda x: x.day):
        if x.export_table not in ("daily", "intraday") or not _TABLE_ID.fullmatch(x.table_id) or not _DAY.fullmatch(x.day):
            raise ValueError(f"not an export table: {x}")
        rows.append(
            f"('{SITE}', DATE '{_d(x.day).isoformat()}', '{x.export_table}', '{x.table_id}', TIMESTAMP '{_ts(x.last_modified)}', {int(x.row_count)})"
        )
    struct = ", ".join(f"{name} {typ}" for name, typ, _ in STAGED_COLUMNS if name != "staged_at")
    body = "".join(f"\n  {r}," for r in rows).rstrip(",")
    return f"SELECT *, CURRENT_TIMESTAMP() AS staged_at FROM UNNEST(ARRAY<STRUCT<{struct}>>[{body}\n])"


def _string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def staged_table_sql(table_ref: str, state: Iterable[ExportDay]) -> str:
    """A full build's staged_export_days: the whole table, replaced (DDL, reads no table: 0 bytes)."""
    cols = ",\n  ".join(f"{name} {typ} OPTIONS(description={_string(desc)})" for name, typ, desc in STAGED_COLUMNS)
    return (
        f"CREATE OR REPLACE TABLE {table_ref} (\n  {cols}\n)\n"
        f"OPTIONS(description={_string(STAGED_DESCRIPTION)}, labels=[('app', 'tagline'), ('stage', '2')])\n"
        f"AS\n{staged_rows_sql(state)}"
    )


@dataclass(frozen=True)
class SourceWindow:
    source: str
    days: tuple[str, ...]  # YYYYMMDD, ascending, contiguous in the source's export: every export day from the first on
    reason: str

    @property
    def since(self) -> str:
        return self.days[0]


@dataclass(frozen=True)
class Window:
    sources: tuple[SourceWindow, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def empty(self) -> bool:
        return not self.sources

    def get(self, source: str) -> SourceWindow | None:
        return next((w for w in self.sources if w.source == source), None)

    @property
    def min_since(self) -> str:
        return min(w.since for w in self.sources)

    def predicate(self, alias: str = "") -> str:
        """The rows the window replaces: `(source = 'x' AND event_date >= DATE 'y') OR ...`. Only date literals,
        so BigQuery prunes the date partitions."""
        a = f"{alias}." if alias else ""
        if not self.sources:
            return "FALSE"
        parts = [f"({a}source = '{w.source}' AND {a}event_date >= DATE '{_d(w.since).isoformat()}')" for w in self.sources]
        return "(" + " OR ".join(parts) + ")"

    def describe(self) -> str:
        if self.empty:
            return "nothing to process"
        return "; ".join(f"{w.source}: {w.days[0]}..{w.days[-1]} ({len(w.days)} day(s), {w.reason})" for w in self.sources)


class WindowError(ValueError):
    """The window cannot be processed incrementally; the message says why (and suggests a full build)."""


def plan_window(
    days_by_source: Mapping[str, list[str]],
    loaded_by_source: Mapping[str, set[str]],
    *,
    since: str | None = None,
    lookback: int = DEFAULT_LOOKBACK_DAYS,
    static_sources: Iterable[str] = (SAMPLE,),
    changed_by_source: Mapping[str, set[str]] | None = None,
    anchor_by_source: Mapping[str, str] | None = None,
) -> Window:
    """Which days of each source to (re)process.

    days_by_source: every export day per source. loaded_by_source: the days stg_events already has rows for.
    changed_by_source: loaded days whose export table no longer matches what was staged (changed_days).
    anchor_by_source: the day a source's lookback counts back from (lookback_anchors); by default its newest export day.
    A window always runs from its first day to the source's newest export day, so the rows it replaces are the
    newest ones and every earlier row is history the window cannot change (the purchase dedupe relies on it)."""
    if lookback < 1:
        raise WindowError("lookback must be at least 1 day")
    if since is not None and not _DAY.fullmatch(since):
        raise WindowError(f"--since {since!r} is not YYYYMMDD")
    static = set(static_sources)
    changed_by_source = changed_by_source or {}
    anchor_by_source = anchor_by_source or {}
    out: list[SourceWindow] = []
    notes: list[str] = []
    for source, days in days_by_source.items():
        days = sorted(days)
        if not days:
            continue
        loaded = loaded_by_source.get(source, set())
        missing = [d for d in days if d not in loaded]
        changed = sorted(d for d in changed_by_source.get(source, set()) if d in loaded and d in days)
        if since is not None:
            for kind, before in (("are not in stg_events", [d for d in missing if d < since]),
                                 ("changed in the export since they were staged", [d for d in changed if d < since])):
                if before:
                    raise WindowError(
                        f"{source}: {len(before)} export day(s) before --since {since} {kind} ({before[0]}..{before[-1]}); "
                        "start the window earlier or run a full build (make build)"
                    )
            start, reason = since, "--since"
        else:
            candidates = []
            if missing:
                candidates.append((missing[0], f"{len(missing)} day(s) not loaded yet"))
            if changed:
                candidates.append((changed[0], f"{len(changed)} day(s) changed in the export since staged ({', '.join(changed)})"))
            if source not in static:
                anchor = anchor_by_source.get(source) or days[-1]
                candidates.append((_s(_d(anchor) - timedelta(days=lookback - 1)), f"the {lookback} day(s) up to {anchor} are re-read"))
            if not candidates:
                notes.append(f"{source}: every export day is loaded and the source is static; not processed")
                continue
            candidates.sort()
            start = candidates[0][0]
            reason = "; ".join(r for _, r in candidates)
        window_days = tuple(d for d in days if d >= start)
        if window_days:
            out.append(SourceWindow(source, window_days, reason))
        else:
            notes.append(f"{source}: no export day on or after {start}")
    return Window(tuple(out), tuple(notes))


def restrict_site(site: SiteTables | None, window: Window) -> SiteTables | None:
    """The site's export tables that fall in the window."""
    w = window.get(SITE)
    if site is None or w is None:
        return None
    keep = set(w.days)
    return SiteTables(daily=tuple(d for d in site.daily if d in keep), intraday_only=tuple(d for d in site.intraday_only if d in keep))


# -- the script -----------------------------------------------------------------------------------------------

_BODY = re.compile(r"^CREATE OR REPLACE TABLE[^\n]*\n(?:[^\n]*\n)*?AS\n", re.M)
_IDENTITY = re.compile(r"-- identity: begin[^\n]*\n(?:[ \t]*--[^\n]*\n)*(.*?)\n[ \t]*-- identity: end", re.S)


def select_body(sql: str) -> str:
    """A model file's SELECT: everything after its `CREATE OR REPLACE TABLE ... AS` line."""
    m = _BODY.search(sql)
    if not m:
        raise TemplateError("model has no `CREATE OR REPLACE TABLE ...` line followed by a line `AS`")
    return sql[m.end() :].strip().rstrip(";")


def identity_expressions(fct_sessions_sql: str) -> str:
    """fct_sessions' person_id / user_id / identity_rule expressions (between `-- identity: begin` and `-- identity:
    end`), reused by the script to re-resolve sessions whose device's identity changed, so the rule is written once."""
    m = _IDENTITY.search(fct_sessions_sql)
    if not m:
        raise TemplateError("fct_sessions has no `-- identity: begin` ... `-- identity: end` block")
    return m.group(1).strip().rstrip(",")


def _table(cfg: Config, model: Model) -> str:
    dataset = cfg.staging_dataset if model.layer == "staging" else cfg.marts_dataset
    return f"`{cfg.project}.{dataset}.{model.name}`"


def script(
    cfg: Config,
    models: Mapping[str, Model],
    base_context: Mapping[str, object],
    site: SiteTables | None,
    window: Window,
    staged: Mapping[str, ExportDay] | None = None,
) -> str:
    """The BigQuery script that applies the window to every table, in one transaction.

    staged: what the run is about to read for the site window's days (export_state, taken before the script runs):
    it replaces those days' rows in staged_export_days, in the same transaction. None leaves that table alone."""
    if window.empty:
        raise WindowError("the window is empty: nothing to process")
    for name in ("stg_events", "stg_items", "int_purchases", "int_device_days", "int_identity", "fct_sessions", "fct_orders",
                 "fct_order_items", "mart_campaign_daily", "mart_funnel_daily"):
        if name not in models:
            raise TemplateError(f"no model {name}")
    t = {name: _table(cfg, m) for name, m in models.items()}
    pred = window.predicate()
    pred_t = window.predicate("T")
    in_window_sources = ", ".join(f"'{w.source}'" for w in window.sources)
    min_since = _d(window.min_since).isoformat()

    def body(name: str, **extra: object) -> str:
        ctx = {**base_context, "incremental_filter": "", "purchase_history": "", **extra}
        return render(select_body(models[name].sql()), ctx)

    sample = window.get(SAMPLE)
    stg_events = body(
        "stg_events",
        sample_start=sample.since if sample else cfg.sample_start,
        sample_end=cfg.sample_end,
        # the sample block stays (the column list is shared); with no sample day in the window it reads nothing
        incremental_filter="" if sample else " AND FALSE",
        site_union=site_union_sql(cfg, restrict_site(site, window)),
        purchase_history=(
            "\n    UNION ALL\n"
            "    -- the same sources' purchases before the window, so that a purchase in the window repeating an earlier\n"
            "    -- one is a duplicate, and a transaction_id seen earlier on another device is a collision\n"
            "    SELECT source, event_key, COALESCE(transaction_id, CONCAT('no-transaction-id:', CAST(event_key AS STRING))) AS order_id,\n"
            "      user_pseudo_id, event_timestamp\n"
            f"    FROM {t['int_purchases']}\n"
            f"    WHERE source IN ({in_window_sources}) AND NOT {pred}"
        ),
    )
    identity_sql = identity_expressions(models["fct_sessions"].sql())
    site_window = window.get(SITE)
    staged_step = ""
    if staged is not None and site_window is not None:
        keep = set(site_window.days)
        staged_step = f"""
-- 10. What was staged: the site window's days, each with its export table's metadata as read before this run.
MERGE `{cfg.project}.{cfg.staging_dataset}.{STAGED_TABLE}` AS T USING (
{staged_rows_sql(x for d, x in staged.items() if d in keep)}
) AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE AND T.source = '{SITE}' AND T.export_day >= DATE '{_d(site_window.since).isoformat()}' THEN DELETE;
"""

    return f"""-- Tagline incremental build, generated by tagline_pipeline.incremental.
-- Window: {window.describe()}
DECLARE min_session_date DATE;
DECLARE order_dates ARRAY<DATE>;

BEGIN TRANSACTION;

-- 1. stg_events: the window's export rows replace its date partitions.
CREATE TEMP TABLE _old_events AS
SELECT source, session_key FROM {t['stg_events']} WHERE {pred};

CREATE TEMP TABLE _new_events AS
{stg_events};

MERGE {t['stg_events']} AS T USING _new_events AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE AND {pred_t} THEN DELETE;

-- Earlier purchases whose transaction_id the window now shows on a second device (or no longer does) become (or stop
-- being) collisions, which changes their order_id. Rare: the updates below run only when there is one.
CREATE TEMP TABLE _flips AS
WITH window_purchases AS (
  SELECT source, transaction_id, COALESCE(user_pseudo_id, '') AS device
  FROM _new_events WHERE event_name = 'purchase' AND transaction_id IS NOT NULL
),
known AS (
  SELECT source, transaction_id, COALESCE(user_pseudo_id, '') AS device, {pred} AS in_window
  FROM {t['int_purchases']}
  WHERE transaction_id IS NOT NULL AND source IN ({in_window_sources})
),
ids AS (
  SELECT DISTINCT source, transaction_id FROM window_purchases
  UNION DISTINCT
  SELECT source, transaction_id FROM known WHERE in_window
),
before AS (
  SELECT source, transaction_id, COUNT(DISTINCT device) > 1 AS collision FROM known JOIN ids USING (source, transaction_id) GROUP BY 1, 2
),
after AS (
  SELECT source, transaction_id, COUNT(DISTINCT device) > 1 AS collision
  FROM (
    SELECT source, transaction_id, device FROM known JOIN ids USING (source, transaction_id) WHERE NOT in_window
    UNION ALL
    SELECT source, transaction_id, device FROM window_purchases
  )
  GROUP BY 1, 2
)
SELECT source, transaction_id, COALESCE(a.collision, FALSE) AS collision
FROM after AS a FULL OUTER JOIN before AS b USING (source, transaction_id)
WHERE COALESCE(a.collision, FALSE) != COALESCE(b.collision, FALSE);

IF (SELECT COUNT(*) FROM _flips) > 0 THEN
  UPDATE {t['stg_events']} AS T
  SET is_transaction_id_collision = f.collision,
      order_id = IF(f.collision, CONCAT(T.transaction_id, '@', COALESCE(T.user_pseudo_id, '(no device)')), T.transaction_id)
  FROM _flips AS f
  WHERE T.event_name = 'purchase' AND T.source = f.source AND T.transaction_id = f.transaction_id AND NOT {pred_t};
END IF;

-- 2. stg_items: the window's items replace its date partitions.
MERGE {t['stg_items']} AS T USING (
{body("stg_items", incremental_filter=f"WHERE {window.predicate('e')}")}
) AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE AND {pred_t} THEN DELETE;

-- 3. int_purchases: the window's purchases replace its date partitions; earlier ones take the collision updates.
MERGE {t['int_purchases']} AS T USING (
{body("int_purchases", incremental_filter=f" AND {pred}")}
) AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE AND {pred_t} THEN DELETE;

IF (SELECT COUNT(*) FROM _flips) > 0 THEN
  UPDATE {t['int_purchases']} AS T
  SET is_transaction_id_collision = f.collision,
      order_id = IF(f.collision, CONCAT(T.transaction_id, '@', COALESCE(T.user_pseudo_id, '(no device)')), T.transaction_id)
  FROM _flips AS f
  WHERE T.source = f.source AND T.transaction_id = f.transaction_id AND NOT {pred_t};
END IF;

-- 4. int_device_days: the window's device-days replace its date partitions.
MERGE {t['int_device_days']} AS T USING (
{body("int_device_days", incremental_filter=f"AND {pred}")}
) AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE AND {pred_t} THEN DELETE;

-- 5. int_identity: the whole table, added up from int_device_days (about 20 MiB). A sign-in in the window gives the
-- device's earlier events to the person, and person_device_count spans devices.
CREATE TEMP TABLE _identity AS
{body("int_identity")};

-- Devices that existed before this run and now resolve to another person, user_id or rule. (A new device's sessions
-- are all in the window, so step 5 recomputes them anyway.)
CREATE TEMP TABLE _identity_changed AS
SELECT n.source, n.user_pseudo_id
FROM _identity AS n
JOIN {t['int_identity']} AS o USING (source, user_pseudo_id)
WHERE n.person_id IS DISTINCT FROM o.person_id OR n.user_id IS DISTINCT FROM o.user_id OR n.identity_rule IS DISTINCT FROM o.identity_rule;

MERGE {t['int_identity']} AS T USING _identity AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE THEN DELETE;

-- 6. fct_sessions: every session with an event in the window, before or after this run, is recomputed from all of its
-- events (a session that started before the window reaches back to its first day); every other session on a device
-- whose identity changed gets its person again.
CREATE TEMP TABLE _sessions_touched AS
SELECT DISTINCT source, session_key
FROM (SELECT source, session_key FROM _old_events UNION ALL SELECT source, session_key FROM _new_events)
WHERE session_key IS NOT NULL;

SET min_session_date = (
  SELECT LEAST(DATE '{min_since}', IFNULL(MIN(session_date), DATE '{min_since}'))
  FROM {t['fct_sessions']}
  WHERE session_key IN (SELECT session_key FROM _sessions_touched)
);

DELETE FROM {t['fct_sessions']}
WHERE session_date >= min_session_date AND session_key IN (SELECT session_key FROM _sessions_touched);

INSERT INTO {t['fct_sessions']}
{body("fct_sessions", incremental_filter=" AND event_date >= min_session_date AND session_key IN (SELECT session_key FROM _sessions_touched)")};

IF (SELECT COUNT(*) FROM _identity_changed) > 0 THEN
  UPDATE {t['fct_sessions']} AS T
  SET person_id = S.person_id, user_id = S.user_id, identity_rule = S.identity_rule
  FROM (
    SELECT
      s.session_key,
      {identity_sql}
    FROM (
      -- a session that is not signed_in_session carried no user_id of its own
      SELECT session_key, source, user_pseudo_id, CAST(NULL AS STRING) AS session_user_id
      FROM {t['fct_sessions']}
      WHERE identity_rule != 'signed_in_session'
        AND STRUCT(source, user_pseudo_id) IN (SELECT AS STRUCT source, user_pseudo_id FROM _identity_changed)
    ) AS s
    LEFT JOIN {t['int_identity']} AS i USING (source, user_pseudo_id)
  ) AS S
  WHERE T.session_key = S.session_key;
END IF;

-- 7. fct_orders: the whole table, from int_purchases, fct_sessions and int_identity.
CREATE TEMP TABLE _orders AS
{body("fct_orders")};

CREATE TEMP TABLE _orders_changed AS
SELECT DISTINCT source, order_id FROM (
  (SELECT * FROM _orders EXCEPT DISTINCT SELECT * FROM {t['fct_orders']})
  UNION ALL
  (SELECT * FROM {t['fct_orders']} EXCEPT DISTINCT SELECT * FROM _orders)
);

MERGE {t['fct_orders']} AS T USING _orders AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE THEN DELETE;

-- 8. fct_order_items: the lines of every order that is new, gone or changed, read from the stg_items partitions of
-- those orders' dates only.
SET order_dates = (
  SELECT ARRAY_AGG(DISTINCT o.order_date) FROM _orders AS o JOIN _orders_changed AS c USING (source, order_id)
);

DELETE FROM {t['fct_order_items']} AS T
WHERE STRUCT(T.source, T.order_id) IN (SELECT AS STRUCT source, order_id FROM _orders_changed);

INSERT INTO {t['fct_order_items']}
{body("fct_order_items", incremental_filter="WHERE STRUCT(o.source, o.order_id) IN (SELECT AS STRUCT source, order_id FROM _orders_changed) AND i.event_date IN UNNEST(order_dates)")};

-- 9. The marts: whole tables (a few MiB of fct_sessions).
MERGE {t['mart_campaign_daily']} AS T USING (
{body("mart_campaign_daily")}
) AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE THEN DELETE;

MERGE {t['mart_funnel_daily']} AS T USING (
{body("mart_funnel_daily")}
) AS S ON FALSE
WHEN NOT MATCHED THEN INSERT ROW
WHEN NOT MATCHED BY SOURCE THEN DELETE;
{staged_step}
COMMIT TRANSACTION;
"""
