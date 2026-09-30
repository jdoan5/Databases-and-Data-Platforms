"""kpi_alerts and delivery (tagline_pipeline/alerts.py), without BigQuery: an in-memory table stands in for it, and the
webhook is a local HTTP sink started by the test on 127.0.0.1. Nothing is sent anywhere else."""

import json
import socket
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tagline_pipeline import alerts, anomaly
from tagline_pipeline.config import Config, load_config

T1 = datetime(2026, 9, 28, 10, 30, tzinfo=timezone.utc)
T2 = datetime(2026, 9, 29, 10, 30, tzinfo=timezone.utc)
CFG = Config(project="my-project")


def alert(day, metric="orders", rule="mad", severity="warning", source="tagline_site", value=5.0):
    return anomaly.Alert(day, source, metric, value, 40.0, 20.0, 80.0, severity, rule, "down", -5.2, 21, None, "5.2 spreads below")


class FakeBQ:
    """The three table calls alerts.py makes: read_rows, table_exists, load_json (replace)."""

    def __init__(self, marts=None):
        self.tables = {}
        self.loads = []
        for name, rows in (marts or {}).items():
            self.tables[f"my-project.tagline_marts.{name}"] = rows

    def read_rows(self, table_id):
        return [dict(r) for r in self.tables.get(table_id, [])]

    def table_exists(self, table_id):
        return table_id in self.tables

    def load_json(self, step, table_id, rows, schema, description, *, stage="2", partition_field=None):
        json.dumps(rows)  # what the load job sends must be plain JSON
        self.loads.append({"table": table_id, "stage": stage, "partition": partition_field, "columns": [f.name for f in schema]})
        self.tables[table_id] = [dict(r) for r in rows]


class Sink:
    """A local webhook: records every POST, answers with `status`."""

    def __init__(self, status=200):
        sink = self
        self.requests = []
        self.status = status

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                sink.requests.append({"path": self.path, "type": self.headers["Content-Type"], "body": json.loads(body)})
                self.send_response(sink.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/services/T000/B000/secret-token"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# -- rows ------------------------------------------------------------------------------------------------------


def test_rows_carry_what_was_sent_and_drop_what_no_longer_fires():
    first = alerts.alert_rows([alert(date(2026, 9, 27)), alert(date(2026, 9, 26), metric="revenue_usd")], [], T1, "abc")
    assert [r["metric"] for r in first] == ["revenue_usd", "orders"]  # sorted by key: date first
    assert all(r["first_detected_at"] == T1.isoformat() and r["notified_at"] is None for r in first)
    first[1]["notified_at"], first[1]["delivery"] = T1.isoformat(), "webhook"
    again = alerts.alert_rows([alert(date(2026, 9, 27))], first, T2, "def")
    assert len(again) == 1, "the revenue alert no longer fires (a restated day): it is gone"
    row = again[0]
    assert row["first_detected_at"] == T1.isoformat() and row["notified_at"] == T1.isoformat() and row["delivery"] == "webhook"
    assert row["detected_at"] == T2.isoformat() and row["rules_version"] == "def"
    assert set(row) == {c[0] for c in alerts.COLUMNS}
    with pytest.raises(ValueError, match="two alerts"):
        alerts.alert_rows([alert(date(2026, 9, 27)), alert(date(2026, 9, 27))], [], T1, "abc")


def test_the_message_groups_an_events_checks_and_puts_critical_first():
    items = [alert(date(2026, 9, 27), metric=f"tag_health.purchase.required:items[].{f}", rule="contract_violation",
                   severity="critical", value=0.2) for f in ("item_id", "item_name", "price")]
    text = alerts.message([alert(date(2026, 9, 27)), *items], date(2026, 9, 27))
    lines = text.splitlines()
    assert lines[0] == "Tagline alerts as of 2026-09-27: 4 new (3 critical), in 2 group(s)"
    assert lines[1].startswith("- [critical] 2026-09-27 tagline_site tag_health.purchase.required:items[].")
    assert "+2 more on the same event" in lines[1]
    assert lines[2].startswith("- [warning] 2026-09-27 tagline_site orders: 5.00 (expected 40.00, band 20.00 to 80.00")
    many = alerts.message([alert(date(2026, 9, 27), metric=f"m{i}") for i in range(30)], date(2026, 9, 27), max_lines=5)
    assert many.splitlines()[-1] == "... and 25 more group(s): see tagline_marts.kpi_alerts"


def test_payload_fits_the_service_and_logs_never_show_the_token():
    assert alerts.payload("https://hooks.slack.com/services/T/B/x", "hi") == {"text": "hi"}
    assert alerts.payload("https://example.webhook.office.com/webhookb2/x", "hi") == {"text": "hi"}
    assert alerts.payload("https://discord.com/api/webhooks/1/x", "hi") == {"content": "hi"}
    long = alerts.payload("https://discord.com/api/webhooks/1/x", "x" * 3000)["content"]
    assert len(long) == alerts.DISCORD_LIMIT
    assert alerts.safe_host("https://hooks.slack.com/services/T/B/secret") == "https://hooks.slack.com"
    assert alerts.safe_host("http://127.0.0.1:8123/a/b") == "http://127.0.0.1:8123"


def test_the_webhook_url_comes_from_the_environment_and_is_never_printed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TAGLINE_GCP_PROJECT=my-project\nTAGLINE_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T/B/secret\n")
    cfg = load_config(environ={}, env_file=env)
    assert cfg.alert_webhook_url.endswith("/secret") and "secret" not in repr(cfg)
    from tagline_pipeline.config import ConfigError

    with pytest.raises(ConfigError, match="not an http"):
        load_config(environ={"TAGLINE_ALERT_WEBHOOK_URL": "ftp://x"}, env_file=env)


# -- delivery against a local sink ------------------------------------------------------------------------------


def test_post_to_a_local_sink():
    with Sink() as sink:
        assert alerts.post_webhook(sink.url, "Tagline alerts: test") == 200
    assert sink.requests == [{"path": "/services/T000/B000/secret-token", "type": "application/json",
                              "body": {"text": "Tagline alerts: test"}}]


def test_a_failing_webhook_raises_without_the_token():
    with Sink(status=500) as sink:
        with pytest.raises(alerts.DeliveryError) as e:
            alerts.post_webhook(sink.url, "x")
    assert "HTTP 500" in str(e.value) and "secret-token" not in str(e.value)
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(alerts.DeliveryError) as e:
        alerts.post_webhook(f"http://127.0.0.1:{port}/hook/secret-token", "x", timeout=2)
    assert "unreachable" in str(e.value) and "secret-token" not in str(e.value)
    with pytest.raises(alerts.DeliveryError):
        alerts.post_webhook("file:///etc/passwd", "x")


# -- detect and notify, end to end on the fake table -----------------------------------------------------------


def kpi_rows(days, broken_day=None):
    rows = []
    for i in range(days):
        d = date(2026, 9, 1).toordinal() + i
        day = date.fromordinal(d)
        orders = 5 if day == broken_day else 40 + (i % 3)
        rows.append({"date": day.isoformat(), "source": "tagline_site", "sessions": 3000 + 10 * (i % 5), "orders": orders,
                     "revenue_usd": orders * 60.0, "conversion_rate": None, "engaged_session_rate": None, "add_to_cart_rate": None,
                     "checkout_to_purchase_rate": None, "aov_usd": None, "cookieless_order_share": None,
                     "consent_accept_share": None, "checkout_sessions": 0, "events": None})
    return rows


def health_row(day, status="pass", violations=0):
    return {"date": day.isoformat(), "source": "tagline_site", "event_name": "purchase", "check_name": "pii:email",
            "check_kind": "pii", "events": 40, "violations": violations, "violation_rate": violations / 40, "status": status}


def test_detect_then_notify_sends_once_and_only_whats_recent():
    day = date(2026, 9, 27)
    bq = FakeBQ({"mart_kpi_daily": kpi_rows(27, broken_day=day),
                 "mart_tag_health_daily": [health_row(date(2026, 9, 3), "violation", 1), health_row(day, "violation", 2)]})
    result = alerts.detect_and_store(CFG, bq, now=T1)
    assert result.by_rule == {"contract_violation": 2, "mad": 2} and result.by_severity == {"critical": 4}
    table = bq.tables["my-project.tagline_marts.kpi_alerts"]
    assert bq.loads[-1] == {"table": "my-project.tagline_marts.kpi_alerts", "stage": "5", "partition": "date",
                            "columns": [c[0] for c in alerts.COLUMNS]}
    assert {(r["date"], r["metric"], r["rule"]) for r in table} == {
        ("2026-09-03", "tag_health.purchase.pii:email", "contract_violation"),
        ("2026-09-27", "tag_health.purchase.pii:email", "contract_violation"),
        ("2026-09-27", "orders", "mad"), ("2026-09-27", "revenue_usd", "mad")}

    with Sink() as sink:
        first = alerts.notify(CFG, bq, day, sink.url, now=T2)
        second = alerts.notify(CFG, bq, day, sink.url, now=T2)
    assert (first.fresh, first.new, first.critical, first.delivery) == (3, 3, 3, "webhook")
    assert (second.fresh, second.new, second.delivery) == (3, 0, "none"), "a rerun sends nothing twice"
    assert len(sink.requests) == 1
    text = sink.requests[0]["body"]["text"]
    assert text.startswith("Tagline alerts as of 2026-09-27: 3 new (3 critical)") and "2026-09-03" not in text
    table = {(r["date"], r["metric"]): r for r in bq.tables["my-project.tagline_marts.kpi_alerts"]}
    assert table[("2026-09-27", "orders")]["notified_at"] == T2.isoformat() and table[("2026-09-27", "orders")]["delivery"] == "webhook"
    assert table[("2026-09-03", "tag_health.purchase.pii:email")]["notified_at"] is None, "too old to be news: kept, not sent"

    # the next day's detection keeps what was sent
    alerts.detect_and_store(CFG, bq, now=datetime(2026, 9, 30, tzinfo=timezone.utc))
    kept = {(r["date"], r["metric"]): r for r in bq.tables["my-project.tagline_marts.kpi_alerts"]}
    assert kept[("2026-09-27", "orders")]["notified_at"] == T2.isoformat()
    assert kept[("2026-09-27", "orders")]["first_detected_at"] == T1.isoformat()


def test_without_a_webhook_the_alerts_are_logged(capsys):
    day = date(2026, 9, 27)
    bq = FakeBQ({"mart_kpi_daily": [], "mart_tag_health_daily": [health_row(day, "violation", 1)]})
    alerts.detect_and_store(CFG, bq, now=T1)
    result = alerts.notify(CFG, bq, day, None, now=T2)
    assert (result.new, result.delivery) == (1, "log")
    assert "TAGLINE_ALERT_WEBHOOK_URL is not set" in capsys.readouterr().err
    assert bq.tables["my-project.tagline_marts.kpi_alerts"][0]["delivery"] == "log"


def test_a_failed_send_marks_nothing_sent():
    day = date(2026, 9, 27)
    bq = FakeBQ({"mart_kpi_daily": [], "mart_tag_health_daily": [health_row(day, "violation", 1)]})
    alerts.detect_and_store(CFG, bq, now=T1)
    with Sink(status=503) as sink, pytest.raises(alerts.DeliveryError):
        alerts.notify(CFG, bq, day, sink.url, now=T2)
    assert bq.tables["my-project.tagline_marts.kpi_alerts"][0]["notified_at"] is None
    with Sink() as sink:
        assert alerts.notify(CFG, bq, day, sink.url, now=T2).new == 1


def test_the_message_is_logged_before_the_post_so_a_failed_send_keeps_it(capsys):
    day = date(2026, 9, 27)
    bq = FakeBQ({"mart_kpi_daily": [], "mart_tag_health_daily": [health_row(day, "violation", 1)]})
    alerts.detect_and_store(CFG, bq, now=T1)
    with Sink(status=503) as sink, pytest.raises(alerts.DeliveryError):
        alerts.notify(CFG, bq, day, sink.url, now=T2)
    err = capsys.readouterr().err
    assert "Tagline alerts as of 2026-09-27: 1 new (1 critical)" in err and "tag_health.purchase.pii:email" in err
    assert "delivery failed" in err and "secret-token" not in err


def test_the_runs_failed_tasks_head_the_message_and_go_out_without_any_alert():
    day = date(2026, 9, 27)
    bq = FakeBQ({"mart_kpi_daily": kpi_rows(27), "mart_tag_health_daily": [health_row(day)]})
    alerts.detect_and_store(CFG, bq, now=T1)
    with Sink() as sink:
        result = alerts.notify(CFG, bq, day, sink.url, now=T2, run_problems=["stage2_checks.no_email_like_strings"])
        again = alerts.notify(CFG, bq, day, sink.url, now=T2)
    assert (result.new, result.delivery, result.run_problems) == (0, "webhook", ["stage2_checks.no_email_like_strings"])
    assert again.delivery == "none" and len(sink.requests) == 1
    lines = sink.requests[0]["body"]["text"].splitlines()
    assert lines[0] == "Tagline alerts as of 2026-09-27: 0 new (0 critical), in 0 group(s)"
    assert lines[1].startswith("- [critical] 1 task(s) of this run failed or could not run: stage2_checks.no_email_like_strings")


def test_the_site_export_day_that_never_came_is_an_alert():
    """detect with as_of (the DAG: the export day it waited for): the site's days after its newest data are no_data
    alerts. Only for the site, and only when a site export is configured."""
    site_cfg = Config(project="my-project", ga4_dataset="analytics_1")
    rows = kpi_rows(27)  # 2026-09-01 to 2026-09-27
    bq = FakeBQ({"mart_kpi_daily": rows, "mart_tag_health_daily": []})
    assert alerts.detect_and_store(site_cfg, bq, now=T1, as_of=date(2026, 9, 29)).by_rule == {"no_data": 2}
    assert alerts.detect_and_store(site_cfg, bq, now=T1, as_of=date(2026, 9, 27)).alerts == 0
    assert alerts.detect_and_store(CFG, bq, now=T1, as_of=date(2026, 9, 29)).alerts == 0, "no site configured: nothing is due"


def test_no_alerts_writes_an_empty_table_and_sends_nothing():
    bq = FakeBQ({"mart_kpi_daily": kpi_rows(27), "mart_tag_health_daily": [health_row(date(2026, 9, 27))]})
    assert alerts.detect_and_store(CFG, bq, now=T1).alerts == 0
    assert bq.tables["my-project.tagline_marts.kpi_alerts"] == []
    assert alerts.notify(CFG, bq, date(2026, 9, 27), "http://127.0.0.1:9/never", now=T2).delivery == "none"
