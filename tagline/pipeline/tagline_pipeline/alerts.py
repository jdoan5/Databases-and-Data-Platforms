"""tagline_marts.kpi_alerts and alert delivery (Stage 5).

**Detect** (`detect_and_store`): read `mart_kpi_daily` and `mart_tag_health_daily` through the table-data API (no
query job, nothing billed), run the rules (tagline_pipeline/anomaly.py, thresholds in monitoring.toml) over every day,
and replace `kpi_alerts` with the result through a load job (free). The table is therefore a function of the marts, the
rules and `as_of` (the last day the site's export is due, so a day that never arrived is a `no_data` alert): a restated
day, or a changed threshold, changes it on the next run. Each alert keeps the time it was first detected and, once
sent, when and how, carried over from the table as it was.

**Notify** (`notify`): the alerts that are news on the run's day (their day at most `notify_max_age_days` before it)
and not sent yet go out as one short message, headed by the run's failed tasks when the caller passes any (the DAG
does: a failed data check, PII included, then reaches the webhook too): POSTed as JSON to `TAGLINE_ALERT_WEBHOOK_URL`
when it is set (Slack and Teams incoming webhooks take `{"text": ...}`, Discord `{"content": ...}`), otherwise written
to the log. The message is logged before the POST, so the log has it whatever happens. The alerts are then marked
sent, so a rerun does not send them twice. A send that fails raises and nothing is marked sent: in Airflow the task
retries, then fails, and the run ends red whatever `fail_on_alert` says (a channel that is down is an operational
failure); the next run sends the alerts that are still news. Delivery is at least once: a crash after the POST and
before the table is rewritten sends again.

The webhook URL is a secret (Slack's and Discord's carry the token in the path): it lives only in tagline/.env, and
nothing here prints more than its host.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from . import anomaly
from .config import Config
from .incremental import SITE

TABLE = "kpi_alerts"  # in the marts dataset
KPI_MART, HEALTH_MART = "mart_kpi_daily", "mart_tag_health_daily"
MAX_MESSAGE_LINES = 20
DISCORD_LIMIT = 2000

DESCRIPTION = (
    "Tagline Stage 5: one row per alert the anomaly rules raise on the monitoring marts (mart_kpi_daily, "
    "mart_tag_health_daily): date x source x metric x rule. Replaced on every run by tagline_pipeline/alerts.py from "
    "the marts and pipeline/monitoring.toml (docs/monitoring.md); first_detected_at, notified_at and delivery carry over."
)
# (name, type, description); the order is the table's
COLUMNS = (
    ("date", "DATE", "The day the alert is about (the mart's date)."),
    ("source", "STRING", "ga4_sample or tagline_site."),
    ("metric", "STRING", "A mart_kpi_daily column (orders, conversion_rate, ...), tag_health.<event_name>.<check_name> "
                         "for mart_tag_health_daily's violation rate, events.<event_name> for an event's daily count "
                         "(vanished), or day (no_data). (date, source, metric, rule) is the key."),
    ("rule", "STRING", "mad (outside the robust band of its trailing window), floor (below an absolute level), "
                       "contract_violation (a tag-health check with status violation), no_data (no row in "
                       "mart_kpi_daily on a day data was due) or vanished (an event with no row on a day with data, "
                       "against its usual daily count)."),
    ("value", "FLOAT64", "The metric's value that day."),
    ("expected", "FLOAT64", "What the rule expected: the window's (same-weekday) median; 0 for a contract violation; "
                            "the median daily count for vanished; NULL for no_data and for a floor alert without "
                            "enough history."),
    ("band_low", "FLOAT64", "Lower edge of the band (mad), or the floor (floor)."),
    ("band_high", "FLOAT64", "Upper edge of the band (mad); 0 for a contract violation."),
    ("severity", "STRING", "warning or critical."),
    ("direction", "STRING", "down or up: which way the value left the band."),
    ("score", "FLOAT64", "mad: scaled MADs from the centre (negative: below). NULL for the other rules."),
    ("history_days", "INT64", "Judged days in the trailing window."),
    ("volume", "FLOAT64", "The day's denominator for a rate (sessions, orders, events), NULL for a count."),
    ("detail", "STRING", "The rule's reason, in words."),
    ("rules_version", "STRING", "First 12 hex characters of the SHA-256 of monitoring.toml when the alert was computed."),
    ("detected_at", "TIMESTAMP", "When this run computed the alert."),
    ("first_detected_at", "TIMESTAMP", "When the alert was first computed (kept across runs)."),
    ("notified_at", "TIMESTAMP", "When it was sent (webhook) or logged; NULL until then, and for alerts too old to be news."),
    ("delivery", "STRING", "webhook or log, once sent."),
)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def table_id(cfg: Config) -> str:
    return f"{cfg.project}.{cfg.marts_dataset}.{TABLE}"


def rules_version(path=anomaly.CONFIG_FILE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _schema():
    from google.cloud import bigquery

    return [bigquery.SchemaField(name, typ, description=desc) for name, typ, desc in COLUMNS]


# -- rows ------------------------------------------------------------------------------------------------------


def _key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return str(row["date"])[:10], row["source"], row["metric"], row["rule"]


def _ts(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def alert_rows(
    alerts: Iterable[anomaly.Alert], previous: Iterable[Mapping[str, Any]], now: datetime, version: str
) -> list[dict[str, Any]]:
    """kpi_alerts rows for these alerts, keeping first_detected_at, notified_at and delivery from the previous rows of
    the same key. An alert that no longer fires is dropped (the table describes the marts as they are now)."""
    before = {_key(r): r for r in previous}
    rows = []
    seen: set[tuple[str, str, str, str]] = set()
    for a in alerts:
        row = {
            "date": a.date.isoformat(), "source": a.source, "metric": a.metric, "rule": a.rule, "value": a.value,
            "expected": a.expected, "band_low": a.band_low, "band_high": a.band_high, "severity": a.severity,
            "direction": a.direction, "score": a.score, "history_days": a.history_days, "volume": a.volume,
            "detail": a.detail, "rules_version": version, "detected_at": now.isoformat(),
        }
        key = _key(row)
        if key in seen:
            raise ValueError(f"two alerts with the key {key}")
        seen.add(key)
        old = before.get(key, {})
        row["first_detected_at"] = _ts(old.get("first_detected_at")) or now.isoformat()
        row["notified_at"] = _ts(old.get("notified_at"))
        row["delivery"] = old.get("delivery")
        rows.append(row)
    return sorted(rows, key=_key)


def row_alert(row: Mapping[str, Any]) -> anomaly.Alert:
    d = row["date"]
    return anomaly.Alert(
        date=d if isinstance(d, date) else date.fromisoformat(str(d)[:10]), source=row["source"], metric=row["metric"],
        value=row["value"], expected=row["expected"], band_low=row["band_low"], band_high=row["band_high"],
        severity=row["severity"], rule=row["rule"], direction=row["direction"], score=row["score"],
        history_days=int(row["history_days"] or 0), volume=row["volume"], detail=row["detail"] or "",
    )


# -- the message -----------------------------------------------------------------------------------------------


def _group(a: anomaly.Alert) -> tuple:
    if a.metric.startswith(anomaly.TAG_HEALTH_PREFIX + "."):
        return a.date, a.source, "tag_health." + a.metric.split(".")[1], a.rule
    return a.date, a.source, a.metric, a.rule


def message(alerts: list[anomaly.Alert], as_of: date, max_lines: int = MAX_MESSAGE_LINES,
            run_problems: Iterable[str] = ()) -> str:
    """A short text: a header, the run's failed tasks if any, then one line per KPI alert and one per event's
    tag-health alerts, critical first."""
    groups: dict[tuple, list[anomaly.Alert]] = defaultdict(list)
    for a in alerts:
        groups[_group(a)].append(a)
    ordered = sorted(groups.values(), key=lambda g: (min(0 if a.severity == "critical" else 1 for a in g), g[0].date, g[0].source, g[0].metric))
    critical = sum(1 for a in alerts if a.severity == "critical")
    lines = [f"Tagline alerts as of {as_of}: {len(alerts)} new ({critical} critical), in {len(groups)} group(s)"]
    problems = sorted(run_problems)
    if problems:
        lines.append(f"- [critical] {len(problems)} task(s) of this run failed or could not run: {', '.join(problems)} "
                     "(the alerts below come from the marts as they are)")
    for g in ordered[:max_lines]:
        first = max(g, key=lambda a: (a.severity == "critical", abs(a.score or 0), a.value))
        line = anomaly.describe(first)
        if len(g) > 1:
            others = sorted({a.metric.split(".", 2)[-1] for a in g if a is not first})
            line += f" (+{len(g) - 1} more on the same event: {', '.join(others[:4])}{', ...' if len(others) > 4 else ''})"
        lines.append("- " + line)
    if len(ordered) > max_lines:
        lines.append(f"... and {len(ordered) - max_lines} more group(s): see tagline_marts.{TABLE}")
    return "\n".join(lines)


# -- delivery --------------------------------------------------------------------------------------------------


class DeliveryError(RuntimeError):
    pass


def safe_host(url: str) -> str:
    """scheme://host of a URL, for logs: the path can hold the webhook's token."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname or '?'}" + (f":{parts.port}" if parts.port else "")


def payload(url: str, text: str) -> dict[str, str]:
    host = (urlsplit(url).hostname or "").lower()
    if host == "discord.com" or host.endswith((".discord.com", "discordapp.com")):
        return {"content": text if len(text) <= DISCORD_LIMIT else text[: DISCORD_LIMIT - 1] + "…"}
    return {"text": text}


def post_webhook(url: str, text: str, timeout: float = 10.0) -> int:
    """POST the message as JSON; returns the HTTP status, raises DeliveryError on anything but 2xx."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise DeliveryError("TAGLINE_ALERT_WEBHOOK_URL is not an http(s) URL")
    body = json.dumps(payload(url, text)).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - the URL is the operator's
            status = response.status
    except urllib.error.HTTPError as e:
        raise DeliveryError(f"webhook {safe_host(url)} answered HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DeliveryError(f"webhook {safe_host(url)} unreachable: {getattr(e, 'reason', e)}") from None
    if not 200 <= status < 300:
        raise DeliveryError(f"webhook {safe_host(url)} answered HTTP {status}")
    return status


# -- the two steps ---------------------------------------------------------------------------------------------


@dataclass
class DetectResult:
    alerts: int
    by_rule: dict[str, int]
    by_severity: dict[str, int]
    rows_read: dict[str, int]
    rules_version: str


@dataclass
class NotifyResult:
    as_of: str
    fresh: int  # alerts that are news on as_of (sent now or earlier)
    new: int  # of those, sent by this run
    critical: int  # fresh and critical
    delivery: str  # webhook, log or none
    lines: list[str] = field(default_factory=list)
    run_problems: list[str] = field(default_factory=list)  # the run's failed tasks, sent in the same message

    def summary(self) -> dict[str, Any]:
        return asdict(self)


def read_marts(cfg: Config, bq) -> tuple[list[dict], list[dict]]:
    return (bq.read_rows(f"{cfg.project}.{cfg.marts_dataset}.{KPI_MART}"),
            bq.read_rows(f"{cfg.project}.{cfg.marts_dataset}.{HEALTH_MART}"))


def previous_rows(cfg: Config, bq) -> list[dict]:
    return bq.read_rows(table_id(cfg)) if bq.table_exists(table_id(cfg)) else []


def write_rows(cfg: Config, bq, rows: list[dict]) -> None:
    bq.load_json(TABLE, table_id(cfg), rows, _schema(), DESCRIPTION, stage="5", partition_field="date")


def detect_and_store(cfg: Config, bq, config: anomaly.Config | None = None, now: datetime | None = None,
                     as_of: date | None = None) -> DetectResult:
    """`as_of`: the last day the site's export is due (the DAG: the export day it waited for). Days from the site's
    newest data up to it are no_data alerts; without it, only a gap between two days with data is."""
    config = config or anomaly.load_config()
    now = now or datetime.now(timezone.utc)
    kpi, health = read_marts(cfg, bq)
    through = {SITE: as_of} if as_of is not None and cfg.has_site else None
    found = anomaly.detect(kpi, health, config, through=through)
    version = rules_version()
    rows = alert_rows(found, previous_rows(cfg, bq), now, version)
    write_rows(cfg, bq, rows)
    count = lambda attr: dict(sorted(Counter(getattr(a, attr) for a in found).items()))  # noqa: E731
    result = DetectResult(len(found), count("rule"), count("severity"), {KPI_MART: len(kpi), HEALTH_MART: len(health)}, version)
    log(f"kpi_alerts: {len(found)} alert(s) over every day ({result.by_rule}, {result.by_severity}); "
        f"read {len(kpi)} + {len(health)} mart rows, rules {version}")
    return result


def notify(
    cfg: Config,
    bq,
    as_of: date,
    webhook_url: str | None,
    config: anomaly.Config | None = None,
    now: datetime | None = None,
    send: Callable[[str, str], int] = post_webhook,
    run_problems: Iterable[str] = (),
) -> NotifyResult:
    """`run_problems`: the run's failed tasks (the DAG passes them), sent even when no alert is new; not recorded in
    kpi_alerts, so a rerun of a failed run reports them again."""
    config = config or anomaly.load_config()
    now = now or datetime.now(timezone.utc)
    problems = sorted(run_problems)
    rows = previous_rows(cfg, bq)
    fresh_keys = {a.key for a in anomaly.fresh([row_alert(r) for r in rows], as_of, config.settings.notify_max_age_days)}
    fresh_rows = [r for r in rows if (row_alert(r).key in fresh_keys)]
    new_rows = [r for r in fresh_rows if r.get("notified_at") is None]
    critical = sum(1 for r in fresh_rows if r["severity"] == "critical")
    if not new_rows and not problems:
        log(f"notify: nothing new as of {as_of} ({len(fresh_rows)} alert(s) of the last {config.settings.notify_max_age_days + 1} "
            "day(s) already sent)")
        return NotifyResult(as_of.isoformat(), len(fresh_rows), 0, critical, "none")
    text = message([row_alert(r) for r in new_rows], as_of, run_problems=problems)
    # Logged first: if the POST fails, the task log still says what should have gone out (never the URL).
    log("notify: the message:\n" + text)
    if webhook_url:
        try:
            status = send(webhook_url, text)
        except DeliveryError as e:
            log(f"notify: delivery failed ({e}); nothing is marked sent, so the next try or run sends these again")
            raise
        delivery = "webhook"
        log(f"notify: sent {len(new_rows)} alert(s){f' and {len(problems)} failed task(s)' if problems else ''} to "
            f"{safe_host(webhook_url)} (HTTP {status})")
    else:
        delivery = "log"
        log("notify: TAGLINE_ALERT_WEBHOOK_URL is not set; the message above is the delivery")
    if not new_rows:
        return NotifyResult(as_of.isoformat(), len(fresh_rows), 0, critical, delivery, text.splitlines(), problems)
    sent = {_key(r) for r in new_rows}
    for r in rows:
        r["date"] = str(r["date"])[:10]
        for col in ("detected_at", "first_detected_at", "notified_at"):
            r[col] = _ts(r.get(col))
        if _key(r) in sent:
            r["notified_at"], r["delivery"] = now.isoformat(), delivery
    write_rows(cfg, bq, rows)
    return NotifyResult(as_of.isoformat(), len(fresh_rows), len(new_rows), critical, delivery, text.splitlines(), problems)

