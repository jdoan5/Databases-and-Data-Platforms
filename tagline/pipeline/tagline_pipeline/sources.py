"""Which GA4 export tables feed stg_events.

The public sample is always there. The site's own export (analytics_<property_id>)
is optional: when TAGLINE_GA4_DATASET is unset, stg_events unions only the sample.
When it is set, the dataset's tables are listed and:

* every daily table (events_YYYYMMDD) is read;
* a streaming table (events_intraday_YYYYMMDD) is read only for days that have no
  daily table yet. Google deletes the intraday table once the day's daily table is
  complete, but both exist for a while, and reading both would double-count the day.

Everything that decides this is a pure function so it can be unit tested without
BigQuery.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from .config import SQL_DIR, Config
from .sqlfiles import render


@dataclass(frozen=True)
class SiteTables:
    daily: tuple[str, ...]  # YYYYMMDD suffixes with a daily table
    intraday_only: tuple[str, ...]  # YYYYMMDD suffixes with only a streaming table

    @property
    def empty(self) -> bool:
        return not self.daily and not self.intraday_only


def classify_site_tables(table_ids: Iterable[str], prefix: str = "events_") -> SiteTables:
    """Split a dataset's table names into daily days and intraday-only days."""
    daily_re = re.compile(re.escape(prefix) + r"(\d{8})")
    intraday_re = re.compile(re.escape(prefix) + r"intraday_(\d{8})")
    daily: set[str] = set()
    intraday: set[str] = set()
    for table_id in table_ids:
        if m := daily_re.fullmatch(table_id):
            daily.add(m.group(1))
        elif m := intraday_re.fullmatch(table_id):
            intraday.add(m.group(1))
        # anything else (users_*, pseudonymous_users_*, events_fresh_*) is not read
    return SiteTables(daily=tuple(sorted(daily)), intraday_only=tuple(sorted(intraday - daily)))


def site_union_sql(cfg: Config, tables: SiteTables | None) -> str:
    """The `UNION ALL SELECT ...` blocks that add the site export to stg_events ('' if none)."""
    if tables is None or tables.empty:
        return ""
    partial = (SQL_DIR / "partials" / "site_events.sql").read_text(encoding="utf-8")
    base = {"site_project": cfg.site_project, "site_dataset": cfg.ga4_dataset}
    blocks: list[str] = []
    if tables.daily:
        # A BETWEEN on eight-digit suffixes cannot match intraday_* or fresh_* tables,
        # and it is a constant filter, so BigQuery prunes the wildcard to those days.
        blocks.append(
            render(
                partial,
                base
                | {
                    "export_table": "daily",
                    "table_pattern": f"{cfg.ga4_table_prefix}*",
                    "suffix_filter": f"_TABLE_SUFFIX BETWEEN '{tables.daily[0]}' AND '{tables.daily[-1]}'",
                },
            )
        )
    if tables.intraday_only:
        days = ", ".join(f"'{d}'" for d in tables.intraday_only)
        blocks.append(
            render(
                partial,
                base
                | {
                    "export_table": "intraday",
                    "table_pattern": f"{cfg.ga4_table_prefix}intraday_*",
                    "suffix_filter": f"_TABLE_SUFFIX IN ({days})",
                },
            )
        )
    return "".join("\n  UNION ALL\n" + block.strip("\n") for block in blocks)
