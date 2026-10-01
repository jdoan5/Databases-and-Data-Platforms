"""Stage 6: write the dashboard's snapshot (make dashboard-snapshot).

Reads the small marts in tagline_marts (mart_kpi_daily, mart_funnel_daily, mart_campaign_daily,
mart_attribution_daily, mart_tag_health_daily, kpi_alerts) with the pipeline's own configuration and BigQuery
wrapper (tagline_pipeline.bq: maximum_bytes_billed on every job, no query cache, labels), folds the rows into
aggregates, and writes tagline/dashboard/data/snapshot.json for the static page (index.html).

What the snapshot holds: aggregates only. No person, device, session or order id, no raw event, no project id,
dataset name or GA4 property id. Every number the page shows is in it; the figures that come from the project's
write-ups rather than from BigQuery (the measured pipeline runs, the backtest's judgement, the tag QA suite) are in
its `cited` section, each with the document and section it comes from.

Run: make dashboard-snapshot (from tagline/), or pipeline/.venv/bin/python dashboard/export_snapshot.py.
Exit codes as the pipeline's CLI: 0 written, 2 configuration error, 3 credentials or BigQuery error. The cost table
of the jobs that ran is printed either way.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

DASHBOARD_DIR = Path(__file__).resolve().parent
TAGLINE_DIR = DASHBOARD_DIR.parent
PIPELINE_DIR = TAGLINE_DIR / "pipeline"
SNAPSHOT = DASHBOARD_DIR / "data" / "snapshot.json"
if str(PIPELINE_DIR) not in sys.path:  # the pipeline runs from source (see pipeline/pyproject.toml)
    sys.path.insert(0, str(PIPELINE_DIR))

from tagline_pipeline.config import ConfigError, load_config  # noqa: E402
from tagline_pipeline.costs import JobStat, format_cost_table  # noqa: E402

SCHEMA_VERSION = 1
# The marts are a few MB; each query bills BigQuery's 10 MiB minimum per table. The cap is far above that and far
# below the pipeline's 10 GB default, so a query that somehow read a big table fails before it runs.
DASHBOARD_MAX_BYTES_BILLED = 200 * 1000**2

MODELS = ("last_click", "last_non_direct", "first_click", "linear", "time_decay", "position_based")
SOURCES = ("ga4_sample", "tagline_site")
TOP_CHANNELS = 8  # the sample's channels shown by name; the rest fold into one "other" row
REPO = "https://github.com/jdoan5/Databases-and-Data-Platforms/blob/main/tagline/"

# --- the queries: every one reads tagline_marts only -------------------------------------------------------------

KPI_DAILY = """
SELECT date, source, sessions, engaged_sessions, converted_sessions, add_to_cart_sessions, checkout_sessions,
  orders, revenue_usd, cookieless_orders, events, consented_events,
  conversion_rate, add_to_cart_rate, checkout_to_purchase_rate, aov_usd
FROM `{project}.{marts}.mart_kpi_daily`
ORDER BY source, date
"""

FUNNEL = """
SELECT source, MIN(date) AS first_date, MAX(date) AS last_date, COUNT(*) AS days,
  SUM(sessions) AS sessions, SUM(view_item_sessions) AS view_item, SUM(add_to_cart_sessions) AS add_to_cart,
  SUM(begin_checkout_sessions) AS begin_checkout, SUM(purchase_sessions) AS purchase,
  SUM(converted_sessions) AS converted
FROM `{project}.{marts}.mart_funnel_daily`
GROUP BY source
"""

CAMPAIGNS = """
SELECT source, session_source, session_medium, session_campaign, MIN(date) AS first_date, MAX(date) AS last_date,
  SUM(sessions) AS sessions, SUM(engaged_sessions) AS engaged_sessions, SUM(orders) AS orders,
  SUM(revenue_usd) AS revenue_usd, SUM(cost_usd) AS cost_usd
FROM `{project}.{marts}.mart_campaign_daily`
WHERE source = 'tagline_site'
GROUP BY 1, 2, 3, 4
"""

ATTRIBUTION = """
SELECT source, lookback_complete, model, session_source, session_medium, session_campaign,
  SUM(attributed_orders) AS orders, SUM(attributed_revenue_usd) AS revenue_usd
FROM `{project}.{marts}.mart_attribution_daily`
GROUP BY 1, 2, 3, 4, 5, 6
"""

TAG_HEALTH = """
SELECT source, check_kind, status, expectation, COUNT(*) AS n_rows, COUNT(DISTINCT date) AS days,
  MIN(date) AS first_date, MAX(date) AS last_date, SUM(violations) AS violations,
  MAX(contract_version) AS contract_version
FROM `{project}.{marts}.mart_tag_health_daily`
GROUP BY 1, 2, 3, 4
"""

ALERTS = """
SELECT date, source, metric, rule, severity, direction, value, expected
FROM `{project}.{marts}.kpi_alerts`
ORDER BY date, source, metric, rule
"""

TABLES = ("mart_kpi_daily", "mart_funnel_daily", "mart_campaign_daily", "mart_attribution_daily",
          "mart_tag_health_daily", "kpi_alerts")

# --- figures from the write-ups, not from BigQuery: each carries its source ---------------------------------------


def _src(path: str, anchor: str, label: str) -> dict[str, str]:
    return {"doc": path + ("#" + anchor if anchor else ""), "url": REPO + path + ("#" + anchor if anchor else ""),
            "label": label}


CITED: dict[str, Any] = {
    "pipeline_runs": {
        "note": "One Airflow run of the daily DAG (airflow dags test) each, measured by the stage that made it. List "
                "price before free tiers: BigQuery on demand $6.25 per TiB billed, Dataproc Serverless $0.06 per "
                "DCU-hour.",
        "runs": [
            {"stage": "Stage 3", "label": "Stage 3: full rebuild + Spark batch", "wall_s": 480,
             "bigquery_gib": 8.70, "dcu_hours": 0.436, "usd": 0.081,
             "source": _src("STAGE4-RESULTS.md", "the-final-state", "STAGE4-RESULTS.md, The final state")},
            {"stage": "Stage 4", "label": "Stage 4: daily incremental build + tuned batch", "wall_s": 249,
             "bigquery_gib": 2.21, "dcu_hours": 0.083, "usd": 0.0189,
             "source": _src("STAGE4-RESULTS.md", "the-final-state", "STAGE4-RESULTS.md, The final state")},
            {"stage": "Stage 5", "label": "Stage 5: + monitoring marts, 11 checks, alert tasks", "wall_s": 383,
             "bigquery_gib": 2.51, "dcu_hours": 0.114, "usd": 0.022,
             "source": _src("README.md", "what-stage-5-costs", "README.md, What Stage 5 costs")},
        ],
        "monthly": {"before_usd": 2.44, "after_usd": 0.55, "paid_before_usd": 0.86, "paid_after_usd": 0.16,
                    "text": "A daily run for a month at list price, before and after Stage 4. BigQuery stays inside "
                            "its free monthly tier either way, so what the project pays is the Spark part.",
                    "source": _src("STAGE4-RESULTS.md", "what-it-costs-a-month", "STAGE4-RESULTS.md, What it costs a month")},
        "builds": [
            {"label": "Full build + checks", "stage4_gib": 8.05, "stage5_gib": 9.19},
            {"label": "Daily incremental build + checks", "stage4_gib": 2.15, "stage5_gib": 2.43},
        ],
        "builds_source": _src("README.md", "what-stage-5-costs", "README.md, What Stage 5 costs"),
    },
    "backtest": {
        "text": "Every alert was checked against the marts and judged: 12 incidents (the same series or event on "
                "consecutive days), 11 of them six real problems in the sample's own data, 1 noise. Christmas stayed "
                "inside the band.",
        "incidents": 12, "problems": 6, "noise_incidents": 1,
        "judged": [
            {"start": "2020-11-01", "end": "2020-11-15", "kind": "signal", "problem": 1,
             "label": "add_to_cart not collected (the floor rule)"},
            {"start": "2020-11-18", "end": "2020-11-22", "kind": "signal", "problem": 2,
             "label": "Purchases sent twice per order from the same device"},
            {"start": "2020-11-20", "end": "2020-11-24", "kind": "signal", "problem": 3,
             "label": "add_to_cart missing again"},
            {"start": "2020-11-24", "end": "2020-11-25", "kind": "signal", "problem": 4,
             "label": "Checkout items lose their category"},
            {"start": "2020-12-13", "end": "2020-12-13", "kind": "noise", "problem": None,
             "label": "The category quirk at a new high on a quiet Sunday"},
            {"start": "2020-12-30", "end": "2020-12-30", "kind": "signal", "problem": 5,
             "label": "One-day glitch: purchases without a transaction id"},
            {"start": "2021-01-26", "end": "2021-01-31", "kind": "signal", "problem": 6,
             "label": "The purchase tag breaks: no transaction id, checkout-to-purchase falls to 0"},
        ],
        "source": _src("docs/monitoring.md", "the-backtest-the-samples-92-days", "docs/monitoring.md, The backtest"),
    },
    "tag_qa": {
        "tests": 46, "journeys": 9, "rules": 10, "mutations": 12, "mutations_caught": 12,
        "text": "Scripted journeys in Chrome, checked against the contract and the rules, golden dataLayer snapshots "
                "and a GA4 hit layer, on every push in CI. The breaks were made to the site's tag code one at a time.",
        "source": _src("docs/tag-qa.md", "", "docs/tag-qa.md"),
    },
    "alert_history": {
        "min_judged_days": 14,
        "text": "On the site today only contract_violation and no_data can fire: the floor needs a day of 100 sessions "
                "(the site's day had 48), vanished needs 3 days of history, and the band 14 judged days, a day being "
                "judged only when it has enough volume.",
        "source": _src("docs/monitoring.md", "anomaly-alerts", "docs/monitoring.md, Anomaly alerts"),
    },
    "consent_denied_orders": {
        "text": "A purchase sent with consent denied has no device or session id, so it is an order in no session: it "
                "counts in orders and revenue, but has no campaign and no journey to attribute.",
        "source": _src("README.md", "results-on-the-sites-export", "README.md, Results on the site's export"),
    },
    "synthetic": {
        "text": "Campaign spend (campaign_costs) and product unit costs are seeded and flagged in their tables; every "
                "row of the site's export comes from the simulator.",
        "source": _src("README.md", "what-is-synthetic", "README.md, What is synthetic"),
    },
    "self_referral": {
        "channel": "shop.googlemerchandisestore.com / referral",
        "text": "A self-referral: the store's own domain referring to itself. It is mostly the last step of a journey "
                "that started elsewhere, so every multi-touch model moves part of its credit back to where the buyer "
                "came from.",
        "source": _src("README.md", "results-on-the-sample", "README.md, Results on the sample"),
    },
}

# --- pure transforms (unit-tested in dashboard/tests) -------------------------------------------------------------


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _num(value: Any, digits: int | None = None) -> Any:
    """BigQuery numbers as JSON numbers; NaN and None as null; floats rounded."""
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, digits) if digits is not None else value
    return value


def _rate(num: float | int | None, den: float | int | None) -> float | None:
    return round(num / den, 6) if num is not None and den else None


def channel_name(source: str, medium: str, campaign: str | None = None) -> str:
    parts = [source or "(not set)", medium or "(not set)"]
    if campaign is not None:
        parts.append(campaign or "(not set)")
    return " / ".join(parts)


def kpi_totals(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """mart_kpi_daily summed over its days, per source; rates recomputed from the sums."""
    acc: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = acc.setdefault(r["source"], {"first_date": r["date"], "last_date": r["date"], "days": 0,
                                         "sessions": 0, "engaged_sessions": 0, "converted_sessions": 0,
                                         "add_to_cart_sessions": 0, "checkout_sessions": 0, "orders": 0,
                                         "revenue_usd": 0.0, "cookieless_orders": 0, "events": None,
                                         "consented_events": None})
        a["first_date"], a["last_date"] = min(a["first_date"], r["date"]), max(a["last_date"], r["date"])
        a["days"] += 1
        for k in ("sessions", "engaged_sessions", "converted_sessions", "add_to_cart_sessions", "checkout_sessions",
                  "orders", "cookieless_orders"):
            a[k] += r[k] or 0
        a["revenue_usd"] += r["revenue_usd"] or 0.0
        for k in ("events", "consented_events"):  # NULL for the sample: it records no consent
            if r[k] is not None:
                a[k] = (a[k] or 0) + r[k]
    out = {}
    for source, a in acc.items():
        out[source] = {
            **{k: _iso(v) for k, v in a.items()},
            "revenue_usd": round(a["revenue_usd"], 2),
            "engaged_session_rate": _rate(a["engaged_sessions"], a["sessions"]),
            "conversion_rate": _rate(a["converted_sessions"], a["sessions"]),
            "add_to_cart_rate": _rate(a["add_to_cart_sessions"], a["sessions"]),
            "aov_usd": round(a["revenue_usd"] / a["orders"], 2) if a["orders"] else None,
            "cookieless_order_share": _rate(a["cookieless_orders"], a["orders"]),
            "consent_accept_share": _rate(a["consented_events"], a["events"]),
        }
    return out


def daily_series(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["source"]].append({
            "date": _iso(r["date"]),
            "sessions": r["sessions"],
            "orders": r["orders"],
            "revenue_usd": _num(r["revenue_usd"], 2),
            "conversion_rate": _num(r["conversion_rate"], 6),
            "add_to_cart_rate": _num(r["add_to_cart_rate"], 6),
            "checkout_to_purchase_rate": _num(r["checkout_to_purchase_rate"], 6),
        })
    return dict(out)


def funnel(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Closed funnel per session, summed over the days, with each step's rate from the step before."""
    out = {}
    steps = ("sessions", "view_item", "add_to_cart", "begin_checkout", "purchase")
    for r in rows:
        counts = {k: r[k] for k in steps}
        out[r["source"]] = {
            "first_date": _iso(r["first_date"]), "last_date": _iso(r["last_date"]), "days": r["days"],
            **counts, "converted": r["converted"],
            "step_rates": {steps[i]: _rate(counts[steps[i]], counts[steps[i - 1]]) for i in range(1, len(steps))},
        }
    return out


def campaigns(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    items = []
    for r in rows:
        cost = _num(r["cost_usd"], 2)
        revenue = round(r["revenue_usd"] or 0.0, 2)
        items.append({
            "channel": channel_name(r["session_source"], r["session_medium"], r["session_campaign"]),
            "sessions": r["sessions"], "engaged_sessions": r["engaged_sessions"], "orders": r["orders"],
            "revenue_usd": revenue, "cost_usd": cost,
            "roas": round(revenue / cost, 2) if cost else None,
            "cost_per_order": round(cost / r["orders"], 2) if cost and r["orders"] else None,
        })
    items.sort(key=lambda c: (-c["sessions"], c["channel"]))
    cost_total = round(sum(c["cost_usd"] or 0 for c in items), 2)
    paid_revenue = round(sum(c["revenue_usd"] for c in items if c["cost_usd"]), 2)
    dates = [d for r in rows for d in (r["first_date"], r["last_date"])]
    return {
        "first_date": _iso(min(dates)) if dates else None, "last_date": _iso(max(dates)) if dates else None,
        "rows": items,
        "totals": {
            "sessions": sum(c["sessions"] for c in items), "orders": sum(c["orders"] for c in items),
            "revenue_usd": round(sum(c["revenue_usd"] for c in items), 2),
            "cost_usd": cost_total, "paid_revenue_usd": paid_revenue,
            "paid_roas": round(paid_revenue / cost_total, 2) if cost_total else None,
        },
    }


def _fold_channels(revenue: dict[str, dict[str, float]], orders: dict[str, dict[str, float]], top: int | None
                   ) -> dict[str, Any]:
    totals = {m: sum(v.get(m, 0.0) for v in revenue.values()) for m in MODELS}
    order_totals = {m: sum(v.get(m, 0.0) for v in orders.values()) for m in MODELS}
    ranked = sorted(revenue, key=lambda c: (-max(revenue[c].get(m, 0.0) / totals[m] if totals[m] else 0 for m in MODELS), c))
    named = ranked if top is None else ranked[:top]
    rest = [c for c in ranked if c not in named]

    def row(name: str, rev: Mapping[str, float], is_other: bool = False, count: int = 1) -> dict[str, Any]:
        return {
            "channel": name, "other": is_other, "channels": count,
            "revenue_usd": {m: round(rev.get(m, 0.0), 2) for m in MODELS},
            "share": {m: round(rev.get(m, 0.0) / totals[m], 6) if totals[m] else None for m in MODELS},
        }

    rows = [row(c, revenue[c]) for c in named]
    if rest:
        other = {m: sum(revenue[c].get(m, 0.0) for c in rest) for m in MODELS}
        rows.append(row(f"other ({len(rest)} channels)", other, True, len(rest)))
    return {
        "orders": round(order_totals["last_click"], 6), "revenue_usd": round(totals["last_click"], 2),
        "totals_by_model": {m: {"orders": round(order_totals[m], 6), "revenue_usd": round(totals[m], 2)} for m in MODELS},
        "channels": rows,
    }


def attribution(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Revenue share by channel under the six models. The sample: complete-lookback orders only (their 30-day window
    lies inside the data), channel = source / medium, top channels by name. The site: every attributed order (none has
    a complete lookback: its data starts on its first day), channel = source / medium / campaign."""
    rows = list(rows)
    out: dict[str, Any] = {"models": list(MODELS)}
    sample_rev: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    sample_ord: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    site_rev: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    site_ord: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    lookback = defaultdict(lambda: {"complete_orders": 0.0, "incomplete_orders": 0.0,
                                    "complete_revenue_usd": 0.0, "incomplete_revenue_usd": 0.0})
    for r in rows:
        if r["model"] not in MODELS:
            continue
        if r["model"] == "last_click":
            k = "complete" if r["lookback_complete"] else "incomplete"
            lookback[r["source"]][f"{k}_orders"] += r["orders"] or 0.0
            lookback[r["source"]][f"{k}_revenue_usd"] += r["revenue_usd"] or 0.0
        if r["source"] == "ga4_sample" and r["lookback_complete"]:
            ch = channel_name(r["session_source"], r["session_medium"])
            sample_rev[ch][r["model"]] += r["revenue_usd"] or 0.0
            sample_ord[ch][r["model"]] += r["orders"] or 0.0
        elif r["source"] == "tagline_site":
            ch = channel_name(r["session_source"], r["session_medium"], r["session_campaign"])
            site_rev[ch][r["model"]] += r["revenue_usd"] or 0.0
            site_ord[ch][r["model"]] += r["orders"] or 0.0
    if sample_rev:
        out["ga4_sample"] = {"scope": "complete-lookback orders", "channel_grain": "source / medium",
                             **_fold_channels(sample_rev, sample_ord, TOP_CHANNELS)}
    if site_rev:
        out["tagline_site"] = {"scope": "all attributed orders (none has a complete 30-day lookback)",
                               "channel_grain": "source / medium / campaign", **_fold_channels(site_rev, site_ord, None)}
    out["lookback"] = {s: {k: round(v, 2) if "revenue" in k else round(v, 6) for k, v in d.items()}
                       for s, d in lookback.items()}
    return out


def tag_health(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """mart_tag_health_daily per source: rows (date x event x check) by status and by check kind, and the documented
    expectations behind the `expected` rows."""
    by_source: dict[str, dict[str, Any]] = {}
    expectations: dict[tuple[str, str], dict[str, Any]] = {}
    versions = set()
    for r in rows:
        s = by_source.setdefault(r["source"], {"rows": 0, "pass": 0, "expected": 0, "violation": 0, "violations": 0,
                                               "first_date": r["first_date"], "last_date": r["last_date"],
                                               "days": 0, "by_kind": {}})
        s["rows"] += r["n_rows"]
        s[r["status"]] += r["n_rows"]
        if r["status"] == "violation":
            s["violations"] += r["violations"] or 0
        s["first_date"], s["last_date"] = min(s["first_date"], r["first_date"]), max(s["last_date"], r["last_date"])
        s["days"] = max(s["days"], r["days"])
        k = s["by_kind"].setdefault(r["check_kind"], {"kind": r["check_kind"], "pass": 0, "expected": 0, "violation": 0})
        k[r["status"]] += r["n_rows"]
        if r["status"] == "expected" and r["expectation"]:
            e = expectations.setdefault((r["source"], r["expectation"]), {"source": r["source"],
                                                                          "expectation": r["expectation"],
                                                                          "rows": 0, "kinds": set()})
            e["rows"] += r["n_rows"]
            e["kinds"].add(r["check_kind"])
        if r["contract_version"]:
            versions.add(r["contract_version"])
    order = ("required", "format", "value_math", "pii", "dedupe", "attribution")
    rank = lambda kind: order.index(kind) if kind in order else len(order)  # noqa: E731
    for s in by_source.values():
        s["first_date"], s["last_date"] = _iso(s["first_date"]), _iso(s["last_date"])
        s["by_kind"] = sorted(s["by_kind"].values(), key=lambda k: rank(k["kind"]))
    return {
        "contract_versions": sorted(versions),
        "by_source": by_source,
        "expectations": sorted(({**e, "kinds": sorted(e["kinds"], key=rank)} for e in expectations.values()),
                               key=lambda e: (e["source"], -e["rows"])),
    }


SEVERITY_RANK = {"critical": 2, "warning": 1}


def alerts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """kpi_alerts grouped by day and source: how many, how severe, which rules and metrics."""
    days: dict[tuple[str, str], dict[str, Any]] = {}
    by_rule: dict[str, int] = defaultdict(int)
    by_severity: dict[str, int] = defaultdict(int)
    by_source: dict[str, int] = defaultdict(int)
    total = 0
    for r in rows:
        total += 1
        by_rule[r["rule"]] += 1
        by_severity[r["severity"]] += 1
        by_source[r["source"]] += 1
        key = (_iso(r["date"]), r["source"])
        d = days.setdefault(key, {"date": key[0], "source": key[1], "count": 0, "critical": 0, "warning": 0,
                                  "max_severity": "warning", "items": []})
        d["count"] += 1
        d[r["severity"]] = d.get(r["severity"], 0) + 1
        if SEVERITY_RANK.get(r["severity"], 0) > SEVERITY_RANK.get(d["max_severity"], 0):
            d["max_severity"] = r["severity"]
        d["items"].append({"metric": r["metric"], "rule": r["rule"], "severity": r["severity"],
                           "direction": r["direction"], "value": _num(r["value"], 6),
                           "expected": _num(r["expected"], 6)})
    for d in days.values():
        d["items"].sort(key=lambda i: (-SEVERITY_RANK.get(i["severity"], 0), i["metric"], i["rule"]))
    return {
        "total": total, "source_days": len(days), "by_rule": dict(sorted(by_rule.items())),
        "by_severity": dict(sorted(by_severity.items())), "by_source": {s: by_source.get(s, 0) for s in SOURCES},
        "days": sorted(days.values(), key=lambda d: (d["date"], d["source"])),
    }


SOURCE_INFO = {
    "ga4_sample": {
        "label": "GA4 sample: Google Merchandise Store",
        "short": "GA4 sample",
        "synthetic": False,
        "description": "Google's public GA4 sample export (bigquery-public-data.ga4_obfuscated_sample_ecommerce): real "
                       "store traffic, obfuscated by Google. No user_id, so every person is one device; revenue and "
                       "some values are obfuscated, so the numbers show the pipeline working, not how Google's store "
                       "performed.",
    },
    "tagline_site": {
        "label": "Tagline Supply: the site's own GA4 export",
        "short": "Site (synthetic)",
        "synthetic": True,
        "description": "The tagged React storefront's own GA4 property, exported daily to BigQuery. Every visit, order "
                       "and account in it is synthetic: seeded simulator shoppers driving the real site in Chrome on "
                       "one machine. Campaign spend is synthetic too.",
    },
}


def build_snapshot(results: Mapping[str, list[Mapping[str, Any]]], tables: list[dict[str, Any]],
                   stats: list[JobStat], generated_at: datetime, max_bytes_billed: int) -> dict[str, Any]:
    totals = kpi_totals(results["kpi_daily"])
    sources = {}
    for s in SOURCES:
        t = totals.get(s)
        sources[s] = {**SOURCE_INFO[s], "first_date": t["first_date"] if t else None,
                      "last_date": t["last_date"] if t else None, "days": t["days"] if t else 0}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "generator": "tagline/dashboard/export_snapshot.py (make dashboard-snapshot)",
        "export_cost": {
            "query_jobs": len(stats),
            "bytes_processed": sum(s.bytes_processed or 0 for s in stats),
            "bytes_billed": sum(s.bytes_billed or 0 for s in stats),
            "max_bytes_billed": max_bytes_billed,
        },
        "tables": tables,
        "sources": sources,
        "kpis": totals,
        "daily": daily_series(results["kpi_daily"]),
        "alerts": alerts(results["alerts"]),
        "funnel": funnel(results["funnel"]),
        "attribution": attribution(results["attribution"]),
        "tag_health": tag_health(results["tag_health"]),
        "campaigns": {"tagline_site": campaigns(results["campaigns"])},
        "cited": CITED,
    }


# --- BigQuery ------------------------------------------------------------------------------------------------------

QUERIES = {
    "kpi_daily": KPI_DAILY, "funnel": FUNNEL, "campaigns": CAMPAIGNS, "attribution": ATTRIBUTION,
    "tag_health": TAG_HEALTH, "alerts": ALERTS,
}


def run(out: Path, stats: list[JobStat], dry_run: bool = False) -> int:
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    cap = min(cfg.max_bytes_billed, DASHBOARD_MAX_BYTES_BILLED)
    cfg = dataclasses.replace(cfg, max_bytes_billed=cap)

    from google.api_core.exceptions import GoogleAPICallError
    from google.auth.exceptions import DefaultCredentialsError, RefreshError
    from tagline_pipeline.bq import BigQuery

    try:
        bq = BigQuery(cfg, extra_labels={"component": "dashboard"})
        names = {"project": cfg.project, "marts": cfg.marts_dataset}
        tables = []
        for i, name in enumerate(TABLES, start=1):
            t = bq.client.get_table(f"{cfg.project}.{cfg.marts_dataset}.{name}")  # metadata: nothing billed
            tables.append({"name": name, "rows": int(t.num_rows or 0),
                           "last_modified": t.modified.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")})
        print(f"[metadata] {len(tables)} tables read (no query)", flush=True)
        results: dict[str, list[dict[str, Any]]] = {}
        for i, (step, sql) in enumerate(QUERIES.items(), start=1):
            rows, stat = bq.query(f"dashboard_{step}", "report", sql.format(**names), dry_run=dry_run)
            if dry_run:
                stats.append(stat)
                print(f"[{i}/{len(QUERIES)}] {step}: dry run, would process {stat.bytes_processed or 0:,} bytes", flush=True)
                continue
            stats.append(stat)
            results[step] = [dict(r.items()) for r in rows]
            print(f"[{i}/{len(QUERIES)}] {step}: {len(rows):,} rows, {stat.bytes_billed or 0:,} bytes billed", flush=True)
    except (DefaultCredentialsError, RefreshError) as e:
        print(f"Google Cloud credentials are missing or expired ({type(e).__name__}). "
              "Run `gcloud auth application-default login` and try again.", file=sys.stderr)
        return 3
    except GoogleAPICallError as e:
        print(f"BigQuery error: {e.message or e}", file=sys.stderr)
        return 3
    if dry_run:
        print("dry run: nothing billed, nothing written", flush=True)
        return 0

    snapshot = build_snapshot(results, tables, stats, datetime.now(timezone.utc), cap)
    text = json.dumps(snapshot, indent=1, ensure_ascii=False, allow_nan=False) + "\n"
    leaks = [v for v in (cfg.project, cfg.ga4_dataset, cfg.ga4_project) if v and v in text]
    if leaks:  # the queries select no identifier, so this should never fire; refuse to write rather than leak
        print("refusing to write the snapshot: it contains a configured project or dataset name", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out.relative_to(TAGLINE_DIR) if out.is_relative_to(TAGLINE_DIR) else out} "
          f"({len(text.encode('utf-8')):,} bytes)", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=SNAPSHOT, help="where to write the snapshot (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="dry-run every query (free): bytes each would process")
    args = parser.parse_args(argv)
    stats: list[JobStat] = []
    try:
        return run(args.out.resolve(), stats, args.dry_run)
    finally:
        if stats:
            print("\nBigQuery jobs (bytes processed / billed, slot-ms, wall seconds):")
            print(format_cost_table(stats))


if __name__ == "__main__":
    sys.exit(main())
