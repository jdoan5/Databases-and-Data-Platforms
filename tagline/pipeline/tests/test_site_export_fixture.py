"""The fixture's rows, without BigQuery. The BigQuery half is site_export_fixture.py itself (make fixture)."""

import json
import re

import pytest

import site_export_fixture as fx_mod

FX = fx_mod.build_fixture()
DAILY = [r for name, rows in FX.tables.items() if not name.startswith("events_intraday_") for r in rows]
ALL = [r for rows in FX.tables.values() for r in rows]


def params(row):
    return {p["key"]: next(v for v in p["value"].values() if v is not None) for p in row["event_params"]}


def test_tables_are_named_as_ga4_names_them_and_rows_match_their_day():
    for name, rows in FX.tables.items():
        m = re.fullmatch(r"events_(?:intraday_)?(\d{8})", name)
        assert m, name
        assert {r["event_date"] for r in rows} == {m.group(1)}


def test_streaming_copy_of_a_daily_day_is_identical():
    assert FX.tables["events_intraday_20260925"] == FX.tables["events_20260925"]


def test_exactly_one_exact_duplicate_row():
    keys = [json.dumps(r, sort_keys=True) for r in DAILY]
    assert len(keys) - len(set(keys)) == FX.duplicated_rows == 1


def test_purchase_sent_twice_with_one_transaction_id():
    purchases = [r for r in DAILY if r["event_name"] == "purchase" and r["user_pseudo_id"] == fx_mod.DEVICE_B]
    assert len(purchases) == 2
    assert {r["ecommerce"]["transaction_id"] for r in purchases} == {FX.transaction_id}
    assert purchases[0]["event_timestamp"] != purchases[1]["event_timestamp"]
    assert purchases[0]["ecommerce"]["purchase_revenue"] == FX.order_value == 61.0
    assert purchases[0]["ecommerce"]["tax_value"] == FX.order_tax == 4.88


def test_device_a_is_anonymous_until_sign_up_and_device_b_until_login():
    a = sorted((r for r in DAILY if r["user_pseudo_id"] == fx_mod.DEVICE_A), key=lambda r: r["event_timestamp"])
    first_signed = next(i for i, r in enumerate(a) if r["user_id"])
    assert a[first_signed]["event_name"] == "sign_up"
    assert all(r["user_id"] is None for r in a[:first_signed])
    assert {r["user_id"] for r in a[first_signed:]} == {fx_mod.U}
    b = [r for r in DAILY if r["user_pseudo_id"] == fx_mod.DEVICE_B]
    assert {r["user_id"] for r in b if r["user_id"]} == {fx_mod.U}
    assert {r["user_id"] for r in DAILY if r["user_pseudo_id"] == fx_mod.DEVICE_D and r["user_id"]} == {fx_mod.V, fx_mod.W}


def test_utm_landings_carry_collected_traffic_source():
    for row in ALL:
        p = params(row)
        url = p["page_location"]
        if "utm_campaign=" in url:
            campaign = re.search(r"utm_campaign=([^&]+)", url).group(1)
            assert row["collected_traffic_source"]["manual_campaign_name"] == campaign
            assert row["session_traffic_source_last_click"]["manual_campaign"]["campaign_name"] == campaign


def test_session_ids_are_consistent_per_device():
    sessions = {}
    for r in ALL:
        if r["user_pseudo_id"] is None:
            continue  # cookieless: no session ids at all
        p = params(r)
        sessions.setdefault((r["user_pseudo_id"], p["ga_session_id"]), set()).add(p["ga_session_number"])
    assert all(len(numbers) == 1 for numbers in sessions.values())
    assert len(sessions) == len(fx_mod.expected_sessions(FX))


def test_cookieless_rows_have_no_ids_and_denied_consent():
    rows = [r for r in DAILY if r["user_pseudo_id"] is None]
    assert len(rows) == FX.cookieless_events > 0
    assert all(r["privacy_info"]["analytics_storage"] == "No" for r in rows)
    assert all("ga_session_id" not in params(r) and "ga_session_number" not in params(r) for r in rows)
    purchases = [r for r in rows if r["event_name"] == "purchase"]
    assert [(r["ecommerce"]["transaction_id"], r["user_id"]) for r in purchases] == [
        (o.transaction_id, uid) for o, uid in FX.cookieless_orders
    ]
    assert {uid for _, uid in FX.cookieless_orders} == {None, fx_mod.V}


def test_device_a_first_session_is_named_only_by_cross_channel_campaign():
    a1 = [r for r in DAILY if r["user_pseudo_id"] == fx_mod.DEVICE_A and params(r)["ga_session_number"] == 1]
    assert a1 and all("utm_" not in params(r)["page_location"] for r in a1)
    for r in a1:
        last_click = r["session_traffic_source_last_click"]
        assert last_click["manual_campaign"] is None
        cross = last_click["cross_channel_campaign"]
        assert (cross["source"], cross["medium"], cross["campaign_name"]) == ("google", "cpc", "fall_launch")
        assert not (r["collected_traffic_source"] or {}).get("manual_source")


def test_no_personal_data_and_opaque_user_ids():
    blob = json.dumps(ALL)
    assert "@" not in blob and "%40" not in blob
    for uid in {r["user_id"] for r in ALL if r["user_id"]}:
        assert re.fullmatch(r"[0-9a-f]{32}", uid)


def test_campaign_expectations_join_the_synthetic_costs():
    rows = fx_mod.expected_campaign_rows(FX)
    newsletter = [r for r in rows if r[3] == "newsletter_oct" and r[4] > 0]
    assert len(newsletter) == 1
    date, *_, sessions, orders, revenue, cost, roas, cpo = newsletter[0]
    assert (sessions, orders, revenue) == (1, 1, 61.0)
    assert cost is not None and roas == round(61.0 / cost, 4) and cpo == cost


def test_export_schema_adds_the_traffic_source_records():
    bigquery = pytest.importorskip("google.cloud.bigquery")
    F = bigquery.SchemaField
    sample = [
        F("event_name", "STRING"),
        F("stream_id", "INTEGER"),
        F("privacy_info", "RECORD", fields=[F("analytics_storage", "INTEGER"), F("ads_storage", "INTEGER"), F("uses_transient_token", "STRING")]),
    ]
    schema = {f.name: f for f in fx_mod.export_schema(sample)}
    assert schema["stream_id"].field_type == "STRING"
    assert {f.name: f.field_type for f in schema["privacy_info"].fields}["analytics_storage"] == "STRING"
    ctc = {f.name for f in schema["collected_traffic_source"].fields}
    assert {"manual_source", "manual_medium", "manual_campaign_name", "manual_term"} <= ctc
    last_click = {f.name: f for f in schema["session_traffic_source_last_click"].fields}
    assert {f.name for f in last_click["manual_campaign"].fields} >= {"source", "medium", "campaign_name"}
    assert "campaign_name" in {f.name for f in last_click["google_ads_campaign"].fields}
    assert {"source", "medium", "campaign_name"} <= {f.name for f in last_click["cross_channel_campaign"].fields}
