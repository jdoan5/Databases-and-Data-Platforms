"""The anomaly rules (tagline_pipeline/anomaly.py) on synthetic series and on the GA4 sample's own marts, with injected
anomalies. No BigQuery: tests/fixtures/ holds the sample's mart_kpi_daily (92 days) and three of its tag-health series,
read from the tables built on 2026-09-30 (aggregates only)."""

import json
import math
import random
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from tagline_pipeline import anomaly as an
from tagline_pipeline.config import SQL_DIR
from tagline_pipeline.sqlfiles import list_models, parse_doc

FIXTURES = Path(__file__).parent / "fixtures"
START = date(2021, 3, 1)  # a Monday


@pytest.fixture(scope="module")
def config():
    return an.load_config()


@pytest.fixture(scope="module")
def sample_kpi():
    return json.loads((FIXTURES / "sample_kpi_daily.json").read_text())


@pytest.fixture(scope="module")
def sample_health():
    return json.loads((FIXTURES / "sample_tag_health_daily.json").read_text())


def weekly_rows(days=42, seed=7, **overrides):
    """A store with a weekly rhythm (weekends quieter) and day-to-day noise: sessions ~4,000 on weekdays."""
    rng = random.Random(seed)
    rows = []
    for i in range(days):
        d = START + timedelta(days=i)
        weekend = d.weekday() >= 5
        sessions = round((2600 if weekend else 4000) * rng.uniform(0.93, 1.07))
        conv = 0.012 * rng.uniform(0.9, 1.1)
        converted = round(sessions * conv)
        checkout = round(sessions * 0.03)
        orders = converted
        revenue = orders * 70 * rng.uniform(0.9, 1.1)
        rows.append({
            "date": d.isoformat(), "source": "store", "sessions": sessions, "engaged_session_rate": 0.9 * rng.uniform(0.98, 1.02),
            "converted_sessions": converted, "conversion_rate": converted / sessions, "add_to_cart_sessions": round(sessions * 0.08),
            "add_to_cart_rate": 0.08 * rng.uniform(0.9, 1.1), "checkout_sessions": checkout,
            "checkout_to_purchase_rate": 0.45 * rng.uniform(0.9, 1.1), "orders": orders, "revenue_usd": revenue,
            "aov_usd": revenue / orders, "cookieless_orders": 0, "cookieless_order_share": 0.0, "events": None,
            "consented_events": None, "consent_accept_share": None,
        })
    for (i, col), value in overrides.items():
        rows[i][col] = value
    return rows


def alerts_on(alerts, day, metric=None):
    return [a for a in alerts if a.date == day and (metric is None or a.metric == metric)]


# -- the configuration -----------------------------------------------------------------------------------------


def test_every_configured_kpi_is_a_column_of_the_mart(config):
    model = {m.name: m for m in list_models(SQL_DIR)}["mart_kpi_daily"]
    columns = set(parse_doc(model.sql()).columns)
    for rule in config.kpis:
        assert rule.metric in columns, rule.metric
        assert rule.volume is None or rule.volume in columns, rule.volume
        if rule.floor is not None:
            assert rule.direction == "down"
    assert {r.metric for r in config.kpis} >= {"sessions", "orders", "revenue_usd", "conversion_rate", "aov_usd",
                                               "add_to_cart_rate", "checkout_to_purchase_rate", "engaged_session_rate",
                                               "consent_accept_share", "cookieless_order_share"}
    assert config.settings.k < config.settings.k_critical


def test_bad_configuration_is_refused():
    with pytest.raises(an.ConfigError):
        an.config_from_dict({"settings": {"k": 5, "k_critical": 4}, "kpi": {"orders": {}}})
    with pytest.raises(an.ConfigError):
        an.config_from_dict({"settings": {"nonsense": 1}, "kpi": {"orders": {}}})
    with pytest.raises(an.ConfigError):
        an.config_from_dict({"kpi": {"orders": {"direction": "sideways"}}})
    with pytest.raises(an.ConfigError):
        an.config_from_dict({"kpi": {"orders": {"min_delta": -1}}})
    with pytest.raises(an.ConfigError):
        an.config_from_dict({"settings": {}})


# -- the robust band on synthetic series -----------------------------------------------------------------------


def test_a_normal_store_raises_nothing_weekends_included(config):
    assert an.detect(weekly_rows(), [], config) == []


def test_weekday_seasonality_is_what_keeps_weekends_quiet(config):
    """Without the weekday centre the quieter Saturdays and Sundays sit far below the window's median."""
    flat = replace(config, settings=replace(config.settings, seasonality="none", k=2.0, k_critical=3.0))
    weekday = replace(config, settings=replace(config.settings, k=2.0, k_critical=3.0))
    rows = weekly_rows()
    flat_alerts = [a for a in an.detect(rows, [], flat) if a.metric == "sessions"]
    assert flat_alerts and all(a.date.weekday() >= 5 for a in flat_alerts)
    assert [a for a in an.detect(rows, [], weekday) if a.metric == "sessions"] == []


def test_injected_half_of_the_purchases_is_caught(config):
    """Drop half of one day's purchases: orders, conversions and revenue halve; the day is flagged, the others not."""
    rows = weekly_rows()
    i = 30  # a Wednesday, with 30 days of history
    day = date.fromisoformat(rows[i]["date"])
    r = rows[i]
    rows[i] = {**r, "orders": r["orders"] // 2, "converted_sessions": r["converted_sessions"] // 2,
               "conversion_rate": (r["converted_sessions"] // 2) / r["sessions"], "revenue_usd": r["revenue_usd"] / 2,
               "checkout_to_purchase_rate": r["checkout_to_purchase_rate"] / 2}
    alerts = an.detect(rows, [], config)
    flagged = {a.metric for a in alerts_on(alerts, day)}
    assert {"orders", "revenue_usd", "conversion_rate", "checkout_to_purchase_rate"} <= flagged
    assert all(a.date == day for a in alerts), "only the injected day"
    a = alerts_on(alerts, day, "orders")[0]
    assert a.rule == "mad" and a.direction == "down" and a.score < -config.settings.k
    assert a.band_low < a.expected < a.band_high and a.value < a.band_low


def test_direction_matters(config):
    rows = weekly_rows()
    i = 30
    rows[i] = {**rows[i], "revenue_usd": rows[i]["revenue_usd"] * 3, "orders": rows[i]["orders"] * 3}
    assert an.detect(rows, [], config) == [], "a revenue spike is not an incident"
    rows[i] = {**rows[i], "cookieless_order_share": 0.6}
    up = an.detect(rows, [], config)
    assert [(a.metric, a.direction) for a in up] == [("cookieless_order_share", "up")]


def test_needs_history_and_ignores_tiny_volumes(config):
    rows = weekly_rows(days=12)
    rows[11] = {**rows[11], "orders": 1, "revenue_usd": 50.0}
    assert an.detect(rows, [], config) == [], "12 days of history is below min_history"
    tiny = weekly_rows()
    for r in tiny:
        r["checkout_sessions"] = 12  # below min_volume (30): never judged
    tiny[30] = {**tiny[30], "checkout_to_purchase_rate": 0.0}
    assert alerts_on(an.detect(tiny, [], config), START + timedelta(days=30), "checkout_to_purchase_rate") == []
    small = weekly_rows()
    for r in small:
        r["orders"] = 4  # the window's median is below min_expected (10)
    small[30] = {**small[30], "orders": 0}
    assert alerts_on(an.detect(small, [], config), START + timedelta(days=30), "orders") == []


def test_the_floor_needs_no_history(config):
    rows = weekly_rows(days=3)
    rows[2] = {**rows[2], "add_to_cart_rate": 0.0}
    alerts = an.detect(rows, [], config)
    assert [(a.metric, a.rule, a.severity, a.expected) for a in alerts] == [("add_to_cart_rate", "floor", "critical", None)]


def test_the_floor_needs_the_days_volume_too(config):
    """The floor judges only a day whose sessions reach min_volume (100): the site's 48 sessions a day are below it."""
    rows = weekly_rows(days=3)
    rows[2] = {**rows[2], "sessions": 48, "add_to_cart_rate": 0.0}
    assert alerts_on(an.detect(rows, [], config), START + timedelta(days=2), "add_to_cart_rate") == []


def test_a_quiet_constant_series_does_not_alert_on_a_tiny_wobble(config):
    """MAD is 0 on a constant series: min_spread and min_delta keep one extra conversion from being an incident."""
    rows = weekly_rows()
    for r in rows:
        r["engaged_session_rate"] = 0.9
    rows[30] = {**rows[30], "engaged_session_rate": 0.895}
    assert alerts_on(an.detect(rows, [], config), START + timedelta(days=30), "engaged_session_rate") == []
    rows[30] = {**rows[30], "engaged_session_rate": 0.6}
    assert alerts_on(an.detect(rows, [], config), START + timedelta(days=30), "engaged_session_rate")


# -- tag health ------------------------------------------------------------------------------------------------


def health_rows(rates, status="expected", events=400, source="store", event="purchase", check="required:ecommerce.transaction_id",
                kind="required"):
    out = []
    for i, rate in enumerate(rates):
        s = status if rate > 0 else "pass"
        out.append({"date": (START + timedelta(days=i)).isoformat(), "source": source, "event_name": event, "check_name": check,
                    "check_kind": kind, "events": events, "violations": round(rate * events), "violation_rate": rate, "status": s})
    return out


def test_a_contract_violation_alerts_on_the_first_day(config):
    rows = health_rows([0.0, 0.0, 0.005], status="violation")
    alerts = an.detect([], rows, config)
    assert [(a.date, a.rule, a.severity, a.expected) for a in alerts] == [(START + timedelta(days=2), "contract_violation", "warning", 0.0)]
    assert "2 of 400 events" in alerts[0].detail
    big = an.detect([], health_rows([0.2], status="violation"), config)
    assert big[0].severity == "critical"
    pii = an.detect([], health_rows([0.0025], status="violation", event="page_view", check="pii:email", kind="pii"), config)
    assert pii[0].severity == "critical" and pii[0].metric == "tag_health.page_view.pii:email"


def test_an_expected_quirk_alerts_only_when_its_rate_jumps(config):
    rng = random.Random(3)
    base = [0.03 + rng.uniform(-0.01, 0.01) for _ in range(30)]
    assert an.detect([], health_rows(base), config) == []
    spiked = base[:25] + [0.40] + base[26:]
    alerts = an.detect([], health_rows(spiked), config)
    assert [(a.date, a.rule, a.direction) for a in alerts] == [(START + timedelta(days=25), "mad", "up")]
    dropped = base[:25] + [0.0] + base[26:]
    assert an.detect([], health_rows(dropped), config) == [], "fewer violations is not an incident"


def test_a_quirk_already_on_half_the_events_is_not_news_at_seventy_percent(config):
    """The ratio test: 55% -> 70% is many spreads on a steady, high-volume rate, but not 1.5 times worse."""
    rng = random.Random(5)
    base = [0.55 + rng.uniform(-0.01, 0.01) for _ in range(30)]
    drift = base[:25] + [0.70] + base[26:]
    assert an.detect([], health_rows(drift, events=5000), config) == []
    gone = base[:25] + [0.99] + base[26:]
    assert alerts_on(an.detect([], health_rows(gone, events=5000), config), START + timedelta(days=25))


# -- the GA4 sample: the backtest, and anomalies injected into it ----------------------------------------------


def test_backtest_on_the_sample_kpis_is_the_documented_one(config, sample_kpi):
    """docs/monitoring.md's backtest, KPI part: pinned so a threshold change that moves it shows up here."""
    alerts = an.detect(sample_kpi, [], config)
    floor_days = sorted(a.date for a in alerts if a.rule == "floor")
    assert floor_days == [date(2020, 11, d) for d in (*range(1, 16), *range(20, 25))]
    assert {a.metric for a in alerts if a.rule == "floor"} == {"add_to_cart_rate"}
    mad = sorted((a.date, a.metric) for a in alerts if a.rule == "mad")
    assert mad == [
        (date(2021, 1, 27), "checkout_to_purchase_rate"), (date(2021, 1, 28), "checkout_to_purchase_rate"),
        (date(2021, 1, 29), "checkout_to_purchase_rate"), (date(2021, 1, 31), "checkout_to_purchase_rate"),
        (date(2021, 1, 31), "revenue_usd"),
    ]
    assert not [a for a in alerts if date(2020, 12, 20) <= a.date <= date(2021, 1, 3)], "Christmas raises nothing"


def test_backtest_on_the_sample_tag_health_series(config, sample_health):
    alerts = an.detect([], sample_health, config)
    by_metric = {}
    for a in alerts:
        by_metric.setdefault(a.metric.split(".", 1)[1], []).append(a.date)
    assert by_metric["purchase.dedupe:duplicate_transaction_id"] == [date(2020, 11, d) for d in range(18, 23)]
    assert by_metric["purchase.required:ecommerce.transaction_id"] == [
        date(2020, 11, 20), date(2020, 12, 30), date(2021, 1, 27), date(2021, 1, 28), date(2021, 1, 29), date(2021, 1, 30)]
    assert by_metric["begin_checkout.required:items[].item_id"] == [
        date(2020, 12, 30), date(2021, 1, 26), date(2021, 1, 27), date(2021, 1, 28), date(2021, 1, 29)]


def halve_purchases(row):
    r = dict(row)
    r["orders"] = round(r["orders"] / 2)
    r["converted_sessions"] = round(r["converted_sessions"] / 2)
    r["conversion_rate"] = r["converted_sessions"] / r["sessions"]
    r["revenue_usd"] = r["revenue_usd"] / 2
    r["checkout_to_purchase_rate"] = r["checkout_to_purchase_rate"] / 2
    return r


def test_injected_half_of_the_purchases_on_a_sample_day(config, sample_kpi):
    i = next(i for i, r in enumerate(sample_kpi) if r["date"] == "2021-01-13")
    rows = sample_kpi[:i] + [halve_purchases(sample_kpi[i])] + sample_kpi[i + 1:]
    new = alerts_on(an.detect(rows, [], config, days=[date(2021, 1, 13)]), date(2021, 1, 13))
    assert [(a.metric, a.rule, a.direction) for a in new] == [("checkout_to_purchase_rate", "mad", "down")]
    assert alerts_on(an.detect(sample_kpi, [], config, days=[date(2021, 1, 13)]), date(2021, 1, 13)) == []


def test_how_often_a_halving_is_caught_on_the_sample(config, sample_kpi):
    """The honest number (docs/monitoring.md): on the sample's noisy, holiday-shaped days a halving of the purchases is
    caught on 22 of the 72 days from 2020-11-15 to 2021-01-25, most of them in January."""
    base = {(a.date, a.metric) for a in an.detect(sample_kpi, [], config)}
    caught = 0
    days = [i for i, r in enumerate(sample_kpi) if "2020-11-15" <= r["date"] <= "2021-01-25"]
    for i in days:
        d = date.fromisoformat(sample_kpi[i]["date"])
        rows = sample_kpi[:i] + [halve_purchases(sample_kpi[i])] + sample_kpi[i + 1:]
        caught += any((a.date, a.metric) not in base for a in an.detect(rows, [], config, days=[d]))
    assert (caught, len(days)) == (22, 72)


def test_injected_tag_health_spike_on_the_sample(config, sample_health):
    """A day on which 40% of purchases lose their transaction_id (the sample's own rate is a few percent). A December
    day: in January the sample has too few days with 30 or more purchases to fill the window (min_history)."""
    day = "2020-12-16"
    rows = [dict(r) for r in sample_health]
    for r in rows:
        if r["date"] == day and r["check_name"] == "required:ecommerce.transaction_id":
            r["violations"] = round(0.4 * r["events"])
            r["violation_rate"] = r["violations"] / r["events"]
            r["status"] = "expected"
    new = [a for a in alerts_on(an.detect([], rows, config), date(2020, 12, 16))]
    assert [(a.metric, a.rule, a.severity) for a in new] == [
        ("tag_health.purchase.required:ecommerce.transaction_id", "mad", "critical")]


def test_injected_contract_breach_on_the_site():
    """The site is held to the contract: one day of an item without its brand is an alert with no history at all."""
    config = an.load_config()
    rows = health_rows([0.0], status="violation", source="tagline_site", event="add_to_cart", check="required:items[].item_brand")
    rows[0].update(violations=3, events=60, violation_rate=0.05, status="violation")
    alerts = an.detect([], rows, config)
    assert [(a.source, a.rule, a.severity, a.value) for a in alerts] == [("tagline_site", "contract_violation", "critical", 0.05)]


# -- days with no data, and events that stop firing ----------------------------------------------------------


def test_a_missing_day_is_a_no_data_alert_from_the_first_days(config):
    """A day with no row in mart_kpi_daily between two days with data; no history needed."""
    rows = weekly_rows(days=5)
    del rows[2]
    alerts = an.detect(rows, [], config)
    assert [(a.date, a.metric, a.rule, a.severity) for a in alerts] == [(START + timedelta(days=2), "day", "no_data", "critical")]
    assert an.describe(alerts[0]) == f"[critical] {START + timedelta(days=2)} store: no data (nothing exported for this day: no session, order or event)"


def test_an_export_that_never_came_is_a_no_data_alert_when_the_day_is_due(config):
    """The DAG passes the export day it waited for (the sensor gives up as skipped, the run goes on): the days after the
    newest data up to it are missing. Without `through`, the newest row ends the calendar."""
    rows = weekly_rows(days=5)
    last = START + timedelta(days=4)
    assert an.detect(rows, [], config) == []
    due = an.detect(rows, [], config, through={"store": last + timedelta(days=2), "no_such_source": last})
    assert [(a.date, a.rule) for a in due] == [(last + timedelta(days=1), "no_data"), (last + timedelta(days=2), "no_data")]
    assert an.detect(rows, [], config, days=[last + timedelta(days=2)], through={"store": last + timedelta(days=2)})[0].rule == "no_data"


def event_rows(counts, event="begin_checkout", source="store"):
    """mart_tag_health_daily rows for one event (its pii check), one per day with a count above 0."""
    return [{"date": (START + timedelta(days=i)).isoformat(), "source": source, "event_name": event, "check_name": "pii:email",
             "check_kind": "pii", "events": n, "violations": 0, "violation_rate": 0.0, "status": "pass"}
            for i, n in enumerate(counts) if n]


def test_an_event_that_stops_firing_is_caught(config):
    """begin_checkout gone on a day the store has data: no tag-health row to judge, its KPI rate NULL; the vanished rule
    sees the count fall to 0 against its median."""
    kpi = weekly_rows(days=10)
    counts = [120] * 10
    counts[7] = 0
    alerts = an.detect(kpi, event_rows(counts), config)
    assert [(a.date, a.metric, a.rule, a.severity, a.expected) for a in alerts] == [
        (START + timedelta(days=7), "events.begin_checkout", "vanished", "critical", 120)]
    assert "no begin_checkout event exported, against a median of 120 a day over 7 day(s)" in an.describe(alerts[0])


def test_vanished_needs_history_a_real_volume_and_a_day_with_data(config):
    kpi = weekly_rows(days=10)
    early = [120, 120, 0] + [120] * 7  # two days of history: below vanished_min_history (3)
    assert an.detect(kpi, event_rows(early), config) == []
    rare = [4, 0, 6, 3, 0, 5, 2, 0, 4, 3]  # a median under vanished_min_median (10): zeros are normal
    assert an.detect(kpi, event_rows(rare), config) == []
    gone = [120] * 7 + [0, 0, 0]
    assert [a.date for a in an.detect(kpi, event_rows(gone), config)] == [START + timedelta(days=d) for d in (7, 8, 9)]
    no_kpi_row = [r for r in kpi if r["date"] != (START + timedelta(days=7)).isoformat()]
    assert [(a.rule, a.metric) for a in an.detect(no_kpi_row, event_rows([120] * 7 + [0, 120, 120]), config)] == [("no_data", "day")], \
        "a day with no data at all is one no_data alert, not one vanished alert per event"


def test_the_sample_with_begin_checkout_or_a_whole_day_removed(config, sample_kpi, sample_health):
    """The review's two blind spots, on the sample's own 2021-01-13: begin_checkout's tag gone (its tag-health rows
    dropped, the KPI rate NULL), and the whole day gone from both marts."""
    day = date(2021, 1, 13)
    kpi = [dict(r, checkout_sessions=0, checkout_to_purchase_rate=None) if r["date"] == "2021-01-13" else r for r in sample_kpi]
    health = [r for r in sample_health if not (r["date"] == "2021-01-13" and r["event_name"] == "begin_checkout")]
    gone = an.detect(kpi, health, config, days=[day])
    assert [(a.metric, a.rule) for a in gone] == [("events.begin_checkout", "vanished")]
    assert gone[0].expected > 100
    no_day = an.detect([r for r in sample_kpi if r["date"] != "2021-01-13"], [r for r in sample_health if r["date"] != "2021-01-13"],
                       config, days=[day])
    assert [(a.metric, a.rule) for a in no_day] == [("day", "no_data")]
    assert not [a for a in an.detect(sample_kpi, sample_health, config) if a.rule in ("no_data", "vanished")], \
        "the sample itself has no missing day and no vanished event"


def test_fresh_is_the_recent_days_only():
    a = [an.Alert(date(2026, 9, d), "s", "m", 1.0, 2.0, 1.5, 2.5, "warning", "mad", "down", -5.0, 20, None, "") for d in (20, 23, 27, 28)]
    assert [x.date.day for x in an.fresh(a, date(2026, 9, 27), 4)] == [23, 27]


def test_describe_reads_like_a_sentence():
    a = an.Alert(date(2021, 1, 28), "ga4_sample", "checkout_to_purchase_rate", 0.0562, 0.48, 0.34, 0.62, "critical", "mad",
                 "down", -10.7, 21, 89.0, "10.7 spreads below the 21-day same-weekday median")
    text = an.describe(a)
    assert text.startswith("[critical] 2021-01-28 ga4_sample checkout_to_purchase_rate: 5.62% (expected 48.00%")
    assert math.isfinite(a.score)
