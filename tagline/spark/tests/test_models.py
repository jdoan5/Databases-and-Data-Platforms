"""The six models on hand-built journeys, the daily mart, and the invariants (attribution/models.py)."""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from conftest import CHANNELS, T0, order, session
from pyspark.sql import functions as F

from attribution import journeys, models

ALL = set(models.MODELS)


def attribute(frames, orders, sessions):
    o, s = frames(orders, sessions)
    touches = journeys.build_touches(o, s)
    return touches, models.attribute(touches)


def weights(attribution, order_id: str = "o1") -> dict[str, list[float]]:
    """{model: weights by touch position} for one order."""
    out: dict[str, list[float]] = {}
    for r in attribution.where(F.col("order_id") == order_id).orderBy("model", "touch_position").collect():
        out.setdefault(r.model, []).append(r.weight)
    return out


def journey(*specs: tuple[str, float], order_session: int = -1) -> tuple[list, list]:
    """A journey of (channel, days before the order) touches for person P; the order session is `order_session`."""
    sessions = [session(f"s{i}", "P", T0 - timedelta(days=days), channel) for i, (channel, days) in enumerate(specs)]
    key = sessions[order_session][1]
    return [order("o1", "P", T0, key, revenue=90.0)], sessions


def test_one_touch_gets_everything_under_every_model(frames):
    _, a = attribute(frames, *journey(("cpc", 0.01)))
    w = weights(a)
    assert set(w) == ALL
    assert all(values == [1.0] for values in w.values())


def test_two_touches(frames):
    _, a = attribute(frames, *journey(("email", 7.0), ("organic", 0.0)))
    w = weights(a)
    assert w["last_click"] == [0.0, 1.0]
    assert w["last_non_direct"] == [0.0, 1.0]
    assert w["first_click"] == [1.0, 0.0]
    assert w["linear"] == [0.5, 0.5]
    assert w["position_based"] == [0.5, 0.5]
    assert w["time_decay"] == pytest.approx([1 / 3, 2 / 3])  # 2^-1 and 2^0, normalised: 7 days is one half-life


def test_three_touches(frames):
    _, a = attribute(frames, *journey(("email", 14.0), ("cpc", 7.0), ("organic", 0.0)))
    w = weights(a)
    assert w["position_based"] == pytest.approx([0.4, 0.2, 0.4])
    assert w["linear"] == pytest.approx([1 / 3] * 3)
    assert w["time_decay"] == pytest.approx([0.25 / 1.75, 0.5 / 1.75, 1 / 1.75])
    assert w["first_click"] == [1.0, 0.0, 0.0] and w["last_click"] == [0.0, 0.0, 1.0]


def test_five_touches(frames):
    _, a = attribute(frames, *journey(("email", 20), ("cpc", 12), ("referral", 6), ("unknown", 2), ("organic", 0.5)))
    w = weights(a)
    assert w["position_based"] == pytest.approx([0.4, 0.2 / 3, 0.2 / 3, 0.2 / 3, 0.4])
    assert w["linear"] == pytest.approx([0.2] * 5)
    raw = [2 ** (-d / 7) for d in (20, 12, 6, 2, 0.5)]
    assert w["time_decay"] == pytest.approx([x / sum(raw) for x in raw])


def test_direct_only_journey_credits_the_last_touch_under_last_non_direct(frames):
    _, a = attribute(frames, *journey(("direct", 9), ("direct", 3), ("direct", 0)))
    w = weights(a)
    assert w["last_non_direct"] == w["last_click"] == [0.0, 0.0, 1.0]


def test_last_non_direct_skips_direct_but_not_unknown(frames):
    _, a = attribute(frames, *journey(("cpc", 9), ("direct", 3), ("direct", 0)))
    assert weights(a)["last_non_direct"] == [1.0, 0.0, 0.0]
    # (not set) / (not set) is Stage 2's "nothing collected": unknown, not direct, so it can take the credit.
    _, a = attribute(frames, *journey(("cpc", 9), ("unknown", 3), ("direct", 0)))
    assert weights(a)["last_non_direct"] == [0.0, 1.0, 0.0]


def test_touches_outside_the_window_get_nothing(frames):
    orders, sessions = journey(("email", 31), ("cpc", 29), ("organic", 0.01))
    sessions.append(session("s-after", "P", T0 + timedelta(hours=1), "referral"))
    _, a = attribute(frames, orders, sessions)
    rows = a.where("model = 'first_click' AND weight > 0").collect()
    assert [r.session_key for r in rows] == ["s1"]  # the 29-day touch, not the 31-day one
    assert a.where("session_key IN ('s0', 's-after')").count() == 0


def test_a_session_opened_after_the_order_session_takes_no_credit(frames):
    # Opened after the order session but before the purchase: not a touch, so last_click stays on the order
    # session, as in Stage 2, and no model credits the later session.
    orders, sessions = journey(("organic", 0.03), ("referral", 0.002), order_session=0)
    _, a = attribute(frames, orders, sessions)
    last = a.where("model = 'last_click' AND weight = 1").collect()
    assert [(r.session_key, r.is_order_session) for r in last] == [("s0", True)]
    assert a.where("session_key = 's1'").count() == 0


def test_a_tie_with_the_order_session_leaves_last_click_on_the_order_session(frames):
    tied = T0 - timedelta(minutes=30)
    orders = [order("o1", "P", T0, "s-order")]
    sessions = [session("s-a", "P", tied, "referral"), session("s-order", "P", tied, "cpc")]
    _, a = attribute(frames, orders, sessions)
    assert [r.session_key for r in a.where("model = 'last_click' AND weight = 1").collect()] == ["s-order"]


def test_two_orders_sharing_touches_are_attributed_independently(frames):
    t1 = T0 - timedelta(days=2)
    orders = [order("o1", "P", t1 + timedelta(minutes=10), "s2", revenue=40.0), order("o2", "P", T0, "s3", revenue=60.0)]
    sessions = [session("s1", "P", T0 - timedelta(days=5), "email"), session("s2", "P", t1, "cpc"), session("s3", "P", T0 - timedelta(minutes=15), "direct")]
    touches, a = attribute(frames, orders, sessions)
    assert weights(a, "o1")["first_click"] == [1.0, 0.0] and weights(a, "o2")["first_click"] == [1.0, 0.0, 0.0]
    assert weights(a, "o2")["last_non_direct"] == [0.0, 1.0, 0.0]  # s3 is direct: s2 (cpc) gets it
    shared = a.where("session_key = 's1' AND model = 'linear'").groupBy().sum("attributed_revenue_usd").first()[0]
    assert shared == pytest.approx(40.0 / 2 + 60.0 / 3)
    assert models.problems(a, touches) == []


def random_journeys(seed: int, n_orders: int = 60):
    rng = random.Random(seed)
    orders, sessions = [], []
    for i in range(n_orders):
        person = f"P{rng.randrange(n_orders // 2)}"  # people with several orders share touches
        at = T0 - timedelta(days=rng.uniform(0, 40), seconds=rng.randrange(86400))
        key = f"s-order-{i}"
        sessions.append(session(key, person, at - timedelta(minutes=rng.uniform(0, 50)), rng.choice(list(CHANNELS))))
        for j in range(rng.randrange(0, 7)):
            start = at - timedelta(days=rng.choice([rng.uniform(0, 35), 0.0]))
            sessions.append(session(f"s-{i}-{j}", person, start, rng.choice(list(CHANNELS))))
        orders.append(order(f"o{i}", person, at, key, revenue=round(rng.uniform(1, 500), 2)))
    return orders, sessions


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_weights_sum_to_one_and_revenue_is_conserved_on_random_journeys(frames, seed):
    orders, sessions = random_journeys(seed)
    touches, a = attribute(frames, orders, sessions)
    sums = a.groupBy("order_id", "model").agg(F.sum("weight").alias("w")).collect()
    assert len(sums) == len(orders) * len(models.MODELS)
    assert max(abs(r.w - 1) for r in sums) < 1e-9
    revenue = sum(o[6] for o in orders)
    per_model = {r.model: r.rev for r in a.groupBy("model").agg(F.sum("attributed_revenue_usd").alias("rev")).collect()}
    assert set(per_model) == ALL
    assert all(v == pytest.approx(revenue, abs=0.005) for v in per_model.values())
    mart = models.daily_mart(a)
    by_model = {r.model: (r.o, r.rev) for r in mart.groupBy("model").agg(F.sum("attributed_orders").alias("o"), F.sum("attributed_revenue_usd").alias("rev")).collect()}
    assert all(o == pytest.approx(len(orders)) and rev == pytest.approx(revenue, abs=0.005) for o, rev in by_model.values())
    assert models.problems(a, touches) == []


def test_problems_catches_broken_output(frames):
    touches, a = attribute(frames, *journey(("email", 7.0), ("organic", 0.0)))
    doubled = a.withColumn("weight", F.when(F.col("model") == "linear", F.col("weight") * 2).otherwise(F.col("weight")))
    found = models.problems(doubled, touches)
    assert any("differ from 1" in p for p in found) and any(p.startswith("linear: attributed orders") for p in found)
    negative = a.withColumn(
        "weight",
        F.when(F.col("model") == "first_click", F.when(F.col("touch_position") == 1, 1.5).otherwise(-0.5)).otherwise(F.col("weight")),
    )
    found = models.problems(negative, touches)
    assert any("2 weights outside [0, 1]" in p for p in found) and not any("differ from 1" in p for p in found)
    missing = a.where("model != 'time_decay'")
    assert any(p.startswith("time_decay:") for p in models.problems(missing, touches))
    moved = a.withColumn(
        "weight",
        F.when(F.col("model") == "last_click", F.when(F.col("touch_position") == 1, 1.0).otherwise(0.0)).otherwise(F.col("weight")),
    )
    assert any("not on the last touch" in p for p in models.problems(moved, touches))
    # On the last touch, but that touch is not the order session (which build_touches never produces).
    relabelled = a.withColumn("is_order_session", F.lit(False))
    assert any("not on the order session" in p for p in models.problems(relabelled, touches))


def test_daily_mart_splits_complete_and_incomplete_lookback(frames):
    from conftest import DATA_START

    orders = [order("early", "P", DATA_START + timedelta(days=3), "s1", revenue=10.0), order("late", "Q", T0, "s2", revenue=30.0)]
    sessions = [session("s1", "P", DATA_START + timedelta(days=3, minutes=-5), "cpc"), session("s2", "Q", T0 - timedelta(minutes=5), "cpc")]
    _, a = attribute(frames, orders, sessions)
    mart = models.daily_mart(a).where("model = 'linear'").collect()
    got = {(r.lookback_complete, r.session_medium): (r.attributed_orders, r.attributed_revenue_usd) for r in mart}
    assert got == {(False, "cpc"): (1.0, 10.0), (True, "cpc"): (1.0, 30.0)}
    assert set(models.MART_KEYS) | {"attributed_orders", "attributed_revenue_usd"} == set(models.daily_mart(a).columns)
