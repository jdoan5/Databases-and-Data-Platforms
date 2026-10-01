"""The exporter's pure transforms, and the figures it cites from the write-ups (no BigQuery).

Run: make dashboard-test-py (pipeline/.venv/bin/python -m pytest dashboard/tests).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[1]
TAGLINE = DASHBOARD.parent
sys.path.insert(0, str(DASHBOARD))

import export_snapshot as ex  # noqa: E402


def attr_row(source, complete, model, s, m, c, orders, revenue):
    return {"source": source, "lookback_complete": complete, "model": model, "session_source": s,
            "session_medium": m, "session_campaign": c, "orders": orders, "revenue_usd": revenue}


def test_attribution_sample_uses_complete_lookback_and_folds_the_tail():
    rows = []
    # 10 channels with complete lookback, revenue 10..100 under every model; one incomplete order elsewhere
    for i in range(10):
        for model in ex.MODELS:
            rows.append(attr_row("ga4_sample", True, model, f"src{i}", "referral", "(referral)", 1.0, 10.0 * (i + 1)))
    for model in ex.MODELS:
        rows.append(attr_row("ga4_sample", False, model, "late", "cpc", "x", 1.0, 999.0))
    out = ex.attribution(rows)
    sample = out["ga4_sample"]
    names = [c["channel"] for c in sample["channels"]]
    assert len(names) == ex.TOP_CHANNELS + 1
    assert names[0] == "src9 / referral"  # biggest first
    assert names[-1] == "other (2 channels)" and sample["channels"][-1]["other"] is True
    assert "late / cpc" not in names  # incomplete lookback is left out of the sample's shares
    for m in ex.MODELS:
        assert sum(c["share"][m] for c in sample["channels"]) == pytest.approx(1.0, abs=1e-5)
    assert sample["revenue_usd"] == 550.0 and sample["orders"] == 10
    assert out["lookback"]["ga4_sample"] == {"complete_orders": 10, "incomplete_orders": 1,
                                             "complete_revenue_usd": 550.0, "incomplete_revenue_usd": 999.0}


def test_attribution_site_keeps_every_order_and_the_campaign():
    rows = [attr_row("tagline_site", False, m, "newsletter", "email", "newsletter_oct", 1.0, 24.0) for m in ex.MODELS]
    rows += [attr_row("tagline_site", False, m, "facebook", "paid_social", "retarget_q4", 1.0, 76.0) for m in ex.MODELS]
    site = ex.attribution(rows)["tagline_site"]
    assert [c["channel"] for c in site["channels"]] == ["facebook / paid_social / retarget_q4",
                                                        "newsletter / email / newsletter_oct"]
    assert site["channels"][0]["share"]["first_click"] == 0.76
    assert "ga4_sample" not in ex.attribution(rows)


def kpi_row(d, source, **kw):
    base = {"date": d, "source": source, "sessions": 0, "engaged_sessions": 0, "converted_sessions": 0,
            "add_to_cart_sessions": 0, "checkout_sessions": 0, "orders": 0, "revenue_usd": 0.0,
            "cookieless_orders": 0, "events": None, "consented_events": None, "conversion_rate": None,
            "add_to_cart_rate": None, "checkout_to_purchase_rate": None, "aov_usd": None}
    return {**base, **kw}


def test_kpi_totals_recompute_rates_from_sums():
    rows = [
        kpi_row(date(2020, 11, 1), "ga4_sample", sessions=100, engaged_sessions=90, converted_sessions=2, orders=2,
                revenue_usd=100.0),
        kpi_row(date(2020, 11, 2), "ga4_sample", sessions=300, engaged_sessions=210, converted_sessions=2, orders=3,
                revenue_usd=50.5),
        kpi_row(date(2026, 9, 27), "tagline_site", sessions=48, orders=13, revenue_usd=840.93, cookieless_orders=5,
                events=1433, consented_events=1018),
    ]
    t = ex.kpi_totals(rows)
    s = t["ga4_sample"]
    assert (s["first_date"], s["last_date"], s["days"]) == ("2020-11-01", "2020-11-02", 2)
    assert s["sessions"] == 400 and s["engaged_session_rate"] == 0.75 and s["conversion_rate"] == 0.01
    assert s["revenue_usd"] == 150.5 and s["aov_usd"] == 30.1
    assert s["consent_accept_share"] is None  # the sample records no consent
    site = t["tagline_site"]
    assert site["cookieless_order_share"] == round(5 / 13, 6) and site["consent_accept_share"] == round(1018 / 1433, 6)


def test_funnel_step_rates_are_from_the_step_before():
    f = ex.funnel([{"source": "tagline_site", "first_date": date(2026, 9, 27), "last_date": date(2026, 9, 27),
                    "days": 1, "sessions": 48, "view_item": 37, "add_to_cart": 21, "begin_checkout": 13,
                    "purchase": 8, "converted": 8}])["tagline_site"]
    assert f["step_rates"] == {"view_item": round(37 / 48, 6), "add_to_cart": round(21 / 37, 6),
                               "begin_checkout": round(13 / 21, 6), "purchase": round(8 / 13, 6)}


def test_campaigns_roas_and_totals():
    d = date(2026, 9, 27)
    rows = [
        {"session_source": "(direct)", "session_medium": "(none)", "session_campaign": "(direct)", "first_date": d,
         "last_date": d, "sessions": 13, "engaged_sessions": 12, "orders": 2, "revenue_usd": 81.0, "cost_usd": None},
        {"session_source": "facebook", "session_medium": "paid_social", "session_campaign": "retarget_q4",
         "first_date": d, "last_date": d, "sessions": 5, "engaged_sessions": 5, "orders": 3, "revenue_usd": 278.96,
         "cost_usd": 39.43},
        {"session_source": "google", "session_medium": "cpc", "session_campaign": "fall_launch", "first_date": d,
         "last_date": d, "sessions": 7, "engaged_sessions": 5, "orders": 0, "revenue_usd": 0.0, "cost_usd": 35.41},
    ]
    c = ex.campaigns(rows)
    by = {r["channel"]: r for r in c["rows"]}
    assert by["facebook / paid_social / retarget_q4"]["roas"] == 7.07
    assert by["facebook / paid_social / retarget_q4"]["cost_per_order"] == 13.14
    assert by["google / cpc / fall_launch"]["roas"] == 0.0 and by["google / cpc / fall_launch"]["cost_per_order"] is None
    assert by["(direct) / (none) / (direct)"]["roas"] is None
    assert c["totals"] == {"sessions": 25, "orders": 5, "revenue_usd": 359.96, "cost_usd": 74.84,
                           "paid_revenue_usd": 278.96, "paid_roas": 3.73}
    assert (c["first_date"], c["last_date"]) == ("2026-09-27", "2026-09-27")


def test_alerts_grouped_by_day_with_the_worst_severity():
    rows = [
        {"date": date(2021, 1, 31), "source": "ga4_sample", "metric": "revenue_usd", "rule": "mad",
         "severity": "warning", "direction": "down", "value": 0.0, "expected": 1406.0},
        {"date": date(2021, 1, 31), "source": "ga4_sample", "metric": "orders", "rule": "mad",
         "severity": "critical", "direction": "down", "value": 0.0, "expected": 14.0},
        {"date": date(2020, 11, 1), "source": "ga4_sample", "metric": "add_to_cart_rate", "rule": "floor",
         "severity": "critical", "direction": "down", "value": 0.0, "expected": None},
    ]
    a = ex.alerts(rows)
    assert a["total"] == 3 and a["source_days"] == 2
    assert a["by_rule"] == {"floor": 1, "mad": 2} and a["by_source"] == {"ga4_sample": 3, "tagline_site": 0}
    last = a["days"][-1]
    assert last["date"] == "2021-01-31" and last["max_severity"] == "critical" and last["count"] == 2
    assert [i["metric"] for i in last["items"]] == ["orders", "revenue_usd"]  # critical first


def test_tag_health_counts_rows_by_status_and_keeps_the_expectations():
    d1, d2 = date(2020, 11, 1), date(2021, 1, 31)
    rows = [
        {"source": "ga4_sample", "check_kind": "required", "status": "pass", "expectation": None, "n_rows": 5,
         "days": 92, "first_date": d1, "last_date": d2, "violations": 0, "contract_version": "1.0.0"},
        {"source": "ga4_sample", "check_kind": "required", "status": "expected", "expectation": "Google's tagging",
         "n_rows": 7, "days": 92, "first_date": d1, "last_date": d2, "violations": 70, "contract_version": "1.0.0"},
        {"source": "ga4_sample", "check_kind": "attribution", "status": "expected", "expectation": "Google's tagging",
         "n_rows": 2, "days": 92, "first_date": d1, "last_date": d2, "violations": 9, "contract_version": "1.0.0"},
    ]
    t = ex.tag_health(rows)
    s = t["by_source"]["ga4_sample"]
    assert (s["rows"], s["pass"], s["expected"], s["violation"]) == (14, 5, 9, 0)
    assert [k["kind"] for k in s["by_kind"]] == ["required", "attribution"]
    assert t["expectations"] == [{"source": "ga4_sample", "expectation": "Google's tagging", "rows": 9,
                                  "kinds": ["required", "attribution"]}]
    assert t["contract_versions"] == ["1.0.0"]


def test_build_snapshot_is_strict_json():
    snap = ex.build_snapshot(
        {"kpi_daily": [kpi_row(date(2026, 9, 27), "tagline_site", sessions=1, conversion_rate=float("nan"))],
         "funnel": [], "campaigns": [], "attribution": [], "tag_health": [], "alerts": []},
        [], [], datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc), ex.DASHBOARD_MAX_BYTES_BILLED)
    text = json.dumps(snap, allow_nan=False)  # NaN would raise
    assert snap["generated_at"] == "2026-09-30T12:00:00Z"
    assert snap["sources"]["ga4_sample"]["days"] == 0 and snap["daily"]["tagline_site"][0]["conversion_rate"] is None
    assert "cited" in json.loads(text)


# --- the cited figures still say what the documents say ------------------------------------------------------------


def slug(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def section(doc: str) -> str:
    """The text of doc#anchor: from the heading to the next heading of the same or a higher level."""
    path, _, anchor = doc.partition("#")
    text = (TAGLINE / path).read_text(encoding="utf-8")
    if not anchor:
        return text
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(#+)\s+(.*)$", line)
        if m and slug(m.group(2)) == anchor:
            level = len(m.group(1))
            end = next((j for j in range(i + 1, len(lines))
                        if (h := re.match(r"^(#+)\s", lines[j])) and len(h.group(1)) <= level), len(lines))
            return "\n".join(lines[i:end])
    raise AssertionError(f"{doc}: no heading with that anchor")


def cited_sources(obj):
    if isinstance(obj, dict):
        if set(obj) >= {"doc", "url", "label"}:
            yield obj
        for v in obj.values():
            yield from cited_sources(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from cited_sources(v)


def test_every_cited_source_resolves_to_a_document_section():
    sources = list(cited_sources(ex.CITED))
    assert len(sources) >= 6
    for s in sources:
        assert s["url"] == ex.REPO + s["doc"]
        section(s["doc"])  # raises if the file or the anchor is gone


def test_cited_pipeline_runs_match_their_sections():
    for run in ex.CITED["pipeline_runs"]["runs"]:
        text = section(run["source"]["doc"])
        assert f"{run['wall_s']} s" in text
        assert f"{run['bigquery_gib']:.2f}" in text
        assert f"{run['dcu_hours']}" in text
        assert f"${run['usd']}" in text
    builds = section(ex.CITED["pipeline_runs"]["builds_source"]["doc"])
    for b in ex.CITED["pipeline_runs"]["builds"]:
        assert f"{b['stage4_gib']:.2f} GiB" in builds and f"{b['stage5_gib']:.2f} GiB" in builds
    monthly = ex.CITED["pipeline_runs"]["monthly"]
    text = section(monthly["source"]["doc"])
    for k in ("before_usd", "after_usd", "paid_before_usd", "paid_after_usd"):
        assert f"${monthly[k]:.2f}" in text


def test_cited_alert_history_matches_its_section():
    h = ex.CITED["alert_history"]
    text = " ".join(section(h["source"]["doc"]).split())
    assert f"A rule needs {h['min_judged_days']} judged days in its window" in text
    for phrase in ("only `contract_violation` and `no_data` can fire", "a day of 100 sessions (the site's day had 48)",
                   "`vanished` needs 3 days of history", f"the band {h['min_judged_days']} judged days"):
        assert phrase in text, phrase


def test_cited_backtest_and_tag_qa_match_their_sections():
    b = ex.CITED["backtest"]
    text = section(b["source"]["doc"])
    assert f"in {b['incidents']} incidents" in text and "11 incidents are six real problems, 1 is noise" in text
    assert b["problems"] == 6 and b["noise_incidents"] == 1
    assert sorted(j["problem"] for j in b["judged"] if j["kind"] == "signal") == list(range(1, b["problems"] + 1))
    assert sum(1 for j in b["judged"] if j["kind"] == "noise") == b["noise_incidents"]
    q = ex.CITED["tag_qa"]
    words = {9: "nine", 10: "Ten", 12: "Twelve"}
    readme = section("README.md#tag-qa")  # the README's summary of docs/tag-qa.md
    assert f"{words[q['journeys']]} journeys" in readme and f"{words[q['rules']]} rules" in readme
    assert f"{words[q['mutations']]} changes" in readme and f"final {q['tests']} tests" in readme
    assert q["mutations_caught"] == q["mutations"]
    assert f"{q['mutations']} of {q['mutations']} mutations" in (TAGLINE / "README.md").read_text(encoding="utf-8")
