"""KPI and tag-health anomaly detection (Stage 5): pure functions over daily numbers, no BigQuery.

Each metric of each source is a daily series (`mart_kpi_daily`'s KPIs; `mart_tag_health_daily`'s violation rate per
event and check, and each event's daily count). A day is judged against the series' own trailing window (the
`window_days` before it) by five rules, every threshold in `pipeline/monitoring.toml`:

* **mad** (robust band): how far the day is from the window's median, in units of the scaled median absolute
  deviation (1.4826 x MAD: the standard deviation for normal data, but not moved by a few outliers). With weekday
  seasonality the centre is the median of the window's same weekday, and the spread is how far each window day lies
  from the median of the *other* days of its weekday, so a quiet Sunday is compared with Sundays. Counts, money and
  small rates are judged on a log scale (a halving is the same size at any level), shares on their own. A rule is
  direction-aware (revenue falling is an incident, revenue rising is not; a violation rate rising is) and fires past
  `k` spreads (`k_critical`: critical) only when the change is also material: at least `min_delta` in the metric's
  units and, where set, a factor of `min_ratio`. It needs `min_history` judged days in the window and ignores tiny
  volumes: a rate is judged (and used as history) only on days whose denominator reaches `min_volume`, a count only
  when its expected value (the same-weekday median, or the window's median without seasonality) reaches
  `min_expected`.
* **floor**: a rate below a level no working tag produces (add-to-cart rate under 0.5% of sessions), whatever the
  history. It catches an outage that began before the window did, when the history is itself broken. Like the band,
  it judges only a day whose denominator reaches `min_volume`.
* **contract_violation**: a tag-health check with status `violation` (a contract breach on a source held to the
  contract, or PII anywhere), from the first day: the contract expects none, so no history is needed.
* **no_data**: a day in a source's calendar with no row in `mart_kpi_daily` (nothing exported: no session, order or
  event). The calendar runs from the source's first day to its last, or to `through[source]` when the caller says
  data is due until then (the DAG: the site's export day it waited for, so an export that never came is an alert).
* **vanished**: an event with no row at all on a day the source has data, while its daily count over the window's
  days with data has a median of at least `vanished_min_median`: a tag that stopped firing leaves no tag-health row
  to judge and turns its KPI rates NULL, so the other rules cannot see it.

Everything here is deterministic and unit tested (tests/test_anomaly.py), injected anomalies included.
"""

from __future__ import annotations

import math
import statistics
import tomllib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import PIPELINE_DIR

CONFIG_FILE = PIPELINE_DIR / "monitoring.toml"
MAD_SCALE = 1.4826  # scaled MAD = standard deviation for normally distributed data
SEVERITIES = ("warning", "critical")
DIRECTIONS = ("down", "up", "both")
SCALES = ("log", "linear")
TAG_HEALTH_PREFIX = "tag_health"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MetricRule:
    """How one metric is judged. `volume`: the column that must reach `min_volume` on a day for a rate to be judged
    (its denominator), by the band and the floor; None for counts, which need their expected value (the same-weekday
    median) to reach `min_expected` instead."""

    metric: str
    direction: str = "down"
    scale: str = "linear"
    volume: str | None = None
    min_volume: float = 0.0
    min_expected: float = 0.0
    min_delta: float = 0.0
    min_spread: float = 0.0
    min_ratio: float = 0.0  # up: value >= min_ratio x expected; down: value <= expected / min_ratio (0: no ratio test)
    floor: float | None = None
    seasonality: str | None = None  # weekday | none; None: the settings' default
    log_offset: float | None = None  # scale = log: judged as log(x + offset); None: the settings' default (for counts)
    upper_bound: float | None = None  # a share's band stops at 1 (every metric's stops at 0 below)
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise ConfigError(f"{self.metric}: direction {self.direction!r} is not one of {DIRECTIONS}")
        if self.scale not in SCALES:
            raise ConfigError(f"{self.metric}: scale {self.scale!r} is not one of {SCALES}")
        for name in ("min_volume", "min_expected", "min_delta", "min_spread", "min_ratio"):
            if getattr(self, name) < 0:
                raise ConfigError(f"{self.metric}: {name} must not be negative")
        if self.seasonality not in (None, "weekday", "none"):
            raise ConfigError(f"{self.metric}: seasonality {self.seasonality!r} is not weekday or none")


@dataclass(frozen=True)
class Settings:
    window_days: int = 28
    min_history: int = 14
    k: float = 3.5
    k_critical: float = 6.0
    seasonality: str = "weekday"
    min_weekday_points: int = 2  # below this, a weekday centre falls back to the whole window's median
    log_offset: float = 1.0  # log(x + offset): counts and money can be 0
    # tag health: the violation rate of each event x check
    health_direction: str = "up"
    health_min_events: float = 50
    health_min_delta: float = 0.05
    health_min_spread: float = 0.01
    health_min_ratio: float = 1.5  # and to at least 1.5 times the expected rate: a quirk on half the events is not news at 60%
    health_seasonality: str = "none"
    contract_critical_rate: float = 0.01  # a contract violation on at least this share of events is critical
    critical_check_kinds: tuple[str, ...] = ("pii",)  # always critical
    # vanished: an event with no row on a day with data, against at least this median a day ...
    vanished_min_median: float = 10
    # ... over at least this many of the window's days with data (from the event's first day)
    vanished_min_history: int = 3
    # delivery: an alert is news while its day is at most this many days before the run's day
    notify_max_age_days: int = 4

    def __post_init__(self) -> None:
        if self.window_days < 2 or not 1 <= self.min_history <= self.window_days:
            raise ConfigError("need window_days >= 2 and 1 <= min_history <= window_days")
        if not 0 < self.k < self.k_critical:
            raise ConfigError("need 0 < k < k_critical")
        if self.seasonality not in ("weekday", "none"):
            raise ConfigError(f"seasonality {self.seasonality!r} is not weekday or none")
        if self.health_direction not in DIRECTIONS:
            raise ConfigError(f"health_direction {self.health_direction!r} is not one of {DIRECTIONS}")
        if self.health_seasonality not in ("weekday", "none"):
            raise ConfigError(f"health_seasonality {self.health_seasonality!r} is not weekday or none")
        if self.min_weekday_points < 2:
            raise ConfigError("min_weekday_points must be at least 2")
        if self.vanished_min_median <= 0 or self.vanished_min_history < 1:
            raise ConfigError("need vanished_min_median > 0 and vanished_min_history >= 1")


@dataclass(frozen=True)
class Config:
    settings: Settings
    kpis: tuple[MetricRule, ...]

    def rule(self, metric: str) -> MetricRule | None:
        return next((r for r in self.kpis if r.metric == metric), None)

    def health_rule(self, metric: str) -> MetricRule:
        s = self.settings
        return MetricRule(metric=metric, direction=s.health_direction, scale="linear", volume="events",
                          min_volume=s.health_min_events, min_delta=s.health_min_delta, min_spread=s.health_min_spread,
                          min_ratio=s.health_min_ratio, seasonality=s.health_seasonality, upper_bound=1.0)


def load_config(path: Path = CONFIG_FILE, overrides: Mapping[str, Any] | None = None) -> Config:
    """monitoring.toml: [settings] and one [kpi.<metric>] table per KPI. `overrides` replace settings (tuning)."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return config_from_dict(raw, overrides)


def config_from_dict(raw: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> Config:
    known = {f.name for f in fields(Settings)}
    settings_raw = dict(raw.get("settings", {}))
    unknown = set(settings_raw) - known
    if unknown:
        raise ConfigError(f"unknown settings: {', '.join(sorted(unknown))}")
    if "critical_check_kinds" in settings_raw:
        settings_raw["critical_check_kinds"] = tuple(settings_raw["critical_check_kinds"])
    settings = replace(Settings(**settings_raw), **(overrides or {}))
    rule_fields = {f.name for f in fields(MetricRule)} - {"metric"}
    kpis = []
    for metric, values in raw.get("kpi", {}).items():
        unknown = set(values) - rule_fields
        if unknown:
            raise ConfigError(f"kpi.{metric}: unknown keys {', '.join(sorted(unknown))}")
        kpis.append(MetricRule(metric=metric, **values))
    if not kpis:
        raise ConfigError("no [kpi.*] rules")
    return Config(settings=settings, kpis=tuple(kpis))


# -- data ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Point:
    day: date
    value: float | None
    volume: float | None = None
    status: str | None = None  # tag health: pass | expected | violation
    check_kind: str | None = None


@dataclass(frozen=True)
class Alert:
    date: date
    source: str
    metric: str
    value: float
    expected: float | None
    band_low: float | None
    band_high: float | None
    severity: str
    rule: str
    direction: str
    score: float | None  # spreads from the centre (mad), None for the other rules
    history_days: int
    volume: float | None
    detail: str

    @property
    def key(self) -> tuple[date, str, str, str]:
        return self.date, self.source, self.metric, self.rule


SeriesKey = tuple[str, str]  # (source, metric)
SESSION_EVENT = "(session)"  # mart_tag_health_daily's pseudo-event for the session checks
NO_DATA_METRIC = "day"  # the no_data rule's metric: the source-day as a whole
EVENT_COUNT_PREFIX = "events"  # the vanished rule's metrics: events.<event_name>


def kpi_series(rows: Iterable[Mapping[str, Any]], config: Config) -> dict[SeriesKey, list[Point]]:
    """mart_kpi_daily rows -> one series per source and configured KPI."""
    out: dict[SeriesKey, list[Point]] = defaultdict(list)
    for r in rows:
        for rule in config.kpis:
            if not rule.enabled or rule.metric not in r:
                continue
            value = r[rule.metric]
            volume = r.get(rule.volume) if rule.volume else None
            out[(r["source"], rule.metric)].append(
                Point(_as_date(r["date"]), None if value is None else float(value), None if volume is None else float(volume))
            )
    return {k: sorted(v, key=lambda p: p.day) for k, v in out.items()}


def health_metric(event_name: str, check_name: str) -> str:
    return f"{TAG_HEALTH_PREFIX}.{event_name}.{check_name}"


def health_series(rows: Iterable[Mapping[str, Any]]) -> dict[SeriesKey, list[Point]]:
    """mart_tag_health_daily rows -> one violation-rate series per source, event and check."""
    out: dict[SeriesKey, list[Point]] = defaultdict(list)
    for r in rows:
        rate = r["violation_rate"]
        out[(r["source"], health_metric(r["event_name"], r["check_name"]))].append(
            Point(_as_date(r["date"]), None if rate is None else float(rate), float(r["events"]), r["status"], r.get("check_kind"))
        )
    return {k: sorted(v, key=lambda p: p.day) for k, v in out.items()}


def calendar(kpi_rows: Iterable[Mapping[str, Any]], through: Mapping[str, date] | None = None) -> dict[str, list[date]]:
    """Per source, every day it should have data: from its first row in mart_kpi_daily to its last, or to
    `through[source]` when that is later. A source with no row at all has no calendar (nothing to compare with)."""
    first: dict[str, date] = {}
    last: dict[str, date] = {}
    for r in kpi_rows:
        d, s = _as_date(r["date"]), r["source"]
        first[s] = min(first.get(s, d), d)
        last[s] = max(last.get(s, d), d)
    for s, d in (through or {}).items():
        if s in last and d > last[s]:
            last[s] = d
    return {s: [first[s] + timedelta(days=i) for i in range((last[s] - first[s]).days + 1)] for s in first}


def event_counts(health_rows: Iterable[Mapping[str, Any]]) -> dict[SeriesKey, dict[date, float]]:
    """(source, event_name) -> {day: events that day}, from mart_tag_health_daily (every check of an event counts the
    same events; the (session) pseudo-event is left out)."""
    out: dict[SeriesKey, dict[date, float]] = defaultdict(dict)
    for r in health_rows:
        if r["event_name"] == SESSION_EVENT or r.get("events") is None:
            continue
        d = _as_date(r["date"])
        key = (r["source"], r["event_name"])
        out[key][d] = max(out[key].get(d, 0.0), float(r["events"]))
    return out


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# -- the rules -------------------------------------------------------------------------------------------------


def _mad(values: Sequence[float]) -> float:
    m = statistics.median(values)
    return MAD_SCALE * statistics.median([abs(v - m) for v in values])


def _transform(rule: MetricRule, settings: Settings):
    if rule.scale == "log":
        offset = settings.log_offset if rule.log_offset is None else rule.log_offset
        return (lambda x: math.log(max(x, 0.0) + offset)), (lambda y: math.exp(y) - offset)
    return (lambda x: x), (lambda y: y)


def _judgeable(rule: MetricRule, p: Point) -> bool:
    if p.value is None or (isinstance(p.value, float) and math.isnan(p.value)):
        return False
    if rule.volume is not None:
        return p.volume is not None and p.volume >= rule.min_volume
    return True


@dataclass(frozen=True)
class Band:
    center: float  # in metric units
    low: float
    high: float
    spread: float  # on the transformed scale
    history_days: int


def robust_band(rule: MetricRule, settings: Settings, day: date, history: Sequence[Point]) -> Band | None:
    """The expected value and band for `day` from the judged points of its trailing window, or None when there are
    fewer than min_history of them (or a count's median is below min_expected).

    Weekday seasonality: the centre is the median of the window's same-weekday values (at least min_weekday_points of
    them). The spread is the scaled MAD of every window day's distance to the median of the *other* days of its weekday:
    the error the same rule would have made predicting that day, so the band is calibrated to how well a weekday median
    predicts a day (leaving the day in would shrink the spread, most for weekdays with few values)."""
    usable = [p for p in history if _judgeable(rule, p)]
    if len(usable) < settings.min_history:
        return None
    fwd, inv = _transform(rule, settings)
    ys = [fwd(p.value) for p in usable]  # type: ignore[arg-type]
    center = statistics.median(ys)
    spread = _mad(ys)
    if (rule.seasonality or settings.seasonality) == "weekday":
        groups: dict[int, list[float]] = defaultdict(list)
        for p, y in zip(usable, ys):
            groups[p.day.weekday()].append(y)
        same = groups.get(day.weekday(), [])
        if len(same) >= settings.min_weekday_points:
            center = statistics.median(same)
            residuals = []
            for values in groups.values():
                for i, y in enumerate(values):
                    others = values[:i] + values[i + 1:]
                    if others:
                        residuals.append(y - statistics.median(others))
            if len(residuals) >= settings.min_history:
                spread = _mad(residuals)
    spread = max(spread, rule.min_spread)
    expected = inv(center)
    if rule.volume is None and expected < rule.min_expected:
        return None
    k = settings.k
    high = inv(center + k * spread)
    if rule.upper_bound is not None:
        high = min(high, rule.upper_bound)
    return Band(center=expected, low=max(inv(center - k * spread), 0.0), high=high, spread=spread, history_days=len(usable))


def judge(rule: MetricRule, settings: Settings, source: str, point: Point, history: Sequence[Point]) -> list[Alert]:
    """Every alert the KPI rules raise for one day of one series."""
    if not _judgeable(rule, point):
        return []
    alerts: list[Alert] = []
    band = robust_band(rule, settings, point.day, history)
    value = float(point.value)  # type: ignore[arg-type]
    if rule.floor is not None and value < rule.floor:
        alerts.append(Alert(
            date=point.day, source=source, metric=rule.metric, value=value,
            expected=band.center if band else None, band_low=rule.floor, band_high=None, severity="critical",
            rule="floor", direction="down", score=None, history_days=band.history_days if band else 0, volume=point.volume,
            detail=f"below the floor of {rule.floor:g}, a level no working tag produces",
        ))
    if band is None:
        return alerts
    fwd, _ = _transform(rule, settings)
    score = (fwd(value) - fwd(band.center)) / band.spread if band.spread > 0 else 0.0
    moved = abs(value - band.center)
    down = score < -settings.k and rule.direction in ("down", "both")
    up = score > settings.k and rule.direction in ("up", "both")
    if rule.min_ratio > 0:
        if up and value < rule.min_ratio * band.center:
            up = False
        if down and value * rule.min_ratio > band.center:
            down = False
    if (down or up) and moved >= rule.min_delta and not any(a.rule == "floor" for a in alerts):
        severity = "critical" if abs(score) >= settings.k_critical else "warning"
        alerts.append(Alert(
            date=point.day, source=source, metric=rule.metric, value=value, expected=band.center,
            band_low=band.low, band_high=band.high, severity=severity, rule="mad", direction="down" if down else "up",
            score=round(score, 2), history_days=band.history_days, volume=point.volume,
            detail=f"{abs(score):.1f} spreads {'below' if down else 'above'} the {settings.window_days}-day "
                   f"{'same-weekday ' if (rule.seasonality or settings.seasonality) == 'weekday' else ''}median",
        ))
    return alerts


def judge_health(config: Config, source: str, point: Point, history: Sequence[Point], metric: str) -> list[Alert]:
    """Tag health: a contract violation from the first day; an expected quirk's rate by the robust band."""
    s = config.settings
    if point.value is None:
        return []
    if point.status == "violation":
        critical = (point.check_kind in s.critical_check_kinds) or point.value >= s.contract_critical_rate
        violations = round(point.value * (point.volume or 0))
        return [Alert(
            date=point.day, source=source, metric=metric, value=point.value, expected=0.0, band_low=None, band_high=0.0,
            severity="critical" if critical else "warning", rule="contract_violation", direction="up", score=None,
            history_days=0, volume=point.volume,
            detail=f"{violations} of {int(point.volume or 0)} events break the contract",
        )]
    if point.status != "expected":
        return []  # a passing day is judged only as part of the history
    return judge(config.health_rule(metric), s, source, point, history)


def judge_missing_days(source: str, days: Sequence[date], data_days: set[date]) -> list[Alert]:
    """no_data: every day of the source's calendar with no row in mart_kpi_daily."""
    return [
        Alert(date=d, source=source, metric=NO_DATA_METRIC, value=0.0, expected=None, band_low=None, band_high=None,
              severity="critical", rule="no_data", direction="down", score=None, history_days=0, volume=None,
              detail="nothing exported for this day: no session, order or event")
        for d in days if d not in data_days
    ]


def judge_vanished(settings: Settings, source: str, event: str, counts: Mapping[date, float], data_days: Sequence[date],
                   wanted: set[date] | None = None) -> list[Alert]:
    """vanished: the event has no row on a day the source has data, while the median of its daily count over the
    window's days with data (from its first day on, 0 where it had none) is at least vanished_min_median."""
    window = timedelta(days=settings.window_days)
    first = min(counts)
    alerts = []
    for d in data_days:
        if d <= first or counts.get(d, 0.0) > 0 or (wanted is not None and d not in wanted):
            continue
        history = [counts.get(h, 0.0) for h in data_days if first <= h < d and h >= d - window]
        if len(history) < settings.vanished_min_history:
            continue
        expected = statistics.median(history)
        if expected < settings.vanished_min_median:
            continue
        alerts.append(Alert(
            date=d, source=source, metric=f"{EVENT_COUNT_PREFIX}.{event}", value=0.0, expected=expected, band_low=None,
            band_high=None, severity="critical", rule="vanished", direction="down", score=None,
            history_days=len(history), volume=None,
            detail=f"no {event} event exported, against a median of {expected:g} a day over {len(history)} day(s)",
        ))
    return alerts


def detect(
    kpi_rows: Iterable[Mapping[str, Any]],
    health_rows: Iterable[Mapping[str, Any]],
    config: Config,
    days: Iterable[date] | None = None,
    through: Mapping[str, date] | None = None,
) -> list[Alert]:
    """Every alert for every judged day (or only `days`), sorted by date, source, metric, rule. `through`: per source,
    the last day data is due (the no_data rule's calendar runs to it); without it, a source's calendar ends at its
    newest row, so only a gap between two days with data is a missing day."""
    kpi_rows = list(kpi_rows)
    health_rows = list(health_rows)
    wanted = set(days) if days is not None else None
    window = timedelta(days=config.settings.window_days)
    alerts: list[Alert] = []
    data_days: dict[str, set[date]] = defaultdict(set)
    for r in kpi_rows:
        data_days[r["source"]].add(_as_date(r["date"]))
    for source, cal in calendar(kpi_rows, through).items():
        alerts += judge_missing_days(source, [d for d in cal if wanted is None or d in wanted], data_days[source])
    for (source, event), counts in event_counts(health_rows).items():
        if source in data_days:
            alerts += judge_vanished(config.settings, source, event, counts, sorted(data_days[source]), wanted)
    for (source, metric), points in kpi_series(kpi_rows, config).items():
        rule = config.rule(metric)
        assert rule is not None
        for i, p in enumerate(points):
            if wanted is not None and p.day not in wanted:
                continue
            history = [h for h in points[:i] if p.day - window <= h.day < p.day]
            alerts += judge(rule, config.settings, source, p, history)
    for (source, metric), points in health_series(health_rows).items():
        for i, p in enumerate(points):
            if wanted is not None and p.day not in wanted:
                continue
            history = [h for h in points[:i] if p.day - window <= h.day < p.day]
            alerts += judge_health(config, source, p, history, metric)
    return sorted(alerts, key=lambda a: (a.date, a.source, a.metric, a.rule))


def fresh(alerts: Iterable[Alert], as_of: date, max_age_days: int) -> list[Alert]:
    """The alerts that are news on `as_of`: their day is at most max_age_days before it (and not after it)."""
    first = as_of - timedelta(days=max_age_days)
    return [a for a in alerts if first <= a.date <= as_of]


def describe(a: Alert) -> str:
    """One line for a person: what moved, how far, against what."""
    share = a.metric.startswith(TAG_HEALTH_PREFIX) or a.metric.endswith(("_rate", "_share"))

    def num(x: float | None) -> str:
        if x is None:
            return "-"
        if share:
            return f"{x:.2%}"
        return f"{x:,.2f}" if abs(x) < 1000 else f"{x:,.0f}"

    if a.rule == "no_data":
        return f"[{a.severity}] {a.date} {a.source}: no data ({a.detail})"
    if a.rule == "vanished":
        return f"[{a.severity}] {a.date} {a.source} {a.metric}: 0 (vanished: {a.detail})"
    band = ""
    if a.rule == "mad":
        band = f", band {num(a.band_low)} to {num(a.band_high)}"
    elif a.rule == "floor":
        band = f", floor {num(a.band_low)}"
    exp = f"expected {num(a.expected)}" if a.expected is not None else "no history"
    return f"[{a.severity}] {a.date} {a.source} {a.metric}: {num(a.value)} ({exp}{band}; {a.rule}: {a.detail})"
