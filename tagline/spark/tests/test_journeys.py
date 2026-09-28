"""The journey rules (attribution/journeys.py): which orders, which sessions, in which order."""

from __future__ import annotations

import random
from datetime import timedelta

import pytest
from conftest import DATA_START, T0, order, session
from pyspark.sql import functions as F

from attribution import journeys


def touches_of(touches, order_id: str) -> list:
    return [r.asDict() for r in touches.where(F.col("order_id") == order_id).orderBy("touch_position").collect()]


def keys(rows) -> list[str]:
    return [r["session_key"] for r in rows]


def test_window_reaches_back_30_days_from_the_purchase_and_ends_at_the_order_session(frames):
    orders, sessions = frames(
        [order("o1", "P", T0, "s-order")],
        [
            session("s-31d", "P", T0 - timedelta(days=31)),  # out: before the window
            session("s-30d", "P", T0 - timedelta(days=30)),  # in: the window's start is inclusive
            session("s-10d", "P", T0 - timedelta(days=10), "email"),
            session("s-order", "P", T0 - timedelta(hours=1), "cpc"),
            session("s-overlap", "P", T0 - timedelta(minutes=5), "referral"),  # out: after the order session, though before the purchase
            session("s-at", "P", T0, "direct"),  # out: starting at the purchase, after the order session
            session("s-after", "P", T0 + timedelta(minutes=1)),  # out: same day, after the purchase
            session("s-other-person", "Q", T0 - timedelta(days=2)),  # out: someone else
            session("s-other-source", "P", T0 - timedelta(days=2), source="tagline_site"),  # out: other data source
        ],
    )
    rows = touches_of(journeys.build_touches(orders, sessions), "o1")
    assert keys(rows) == ["s-30d", "s-10d", "s-order"]
    assert [r["touch_position"] for r in rows] == [1, 2, 3]
    assert {r["touch_count"] for r in rows} == {3}
    assert [r["is_order_session"] for r in rows] == [False, False, True]
    assert [r["days_before_order"] for r in rows] == pytest.approx([30.0, 10.0, 1 / 24])
    assert {r["order_session_key"] for r in rows} == {"s-order"}


def test_the_order_session_is_always_the_last_touch(frames):
    # Sessions opened after the order session (another tab, before the purchase) are not touches, so the
    # last touch is the session the purchase was recorded in: Stage 2's channel.
    orders, sessions = frames(
        [order("o1", "P", T0, "s-order")],
        [
            session("s-early", "P", T0 - timedelta(days=2), "email"),
            session("s-order", "P", T0 - timedelta(minutes=40), "organic"),
            session("s-tab", "P", T0 - timedelta(minutes=39), "unknown"),  # opened and closed before the purchase
            session("s-tab2", "P", T0 - timedelta(seconds=1), "direct"),
        ],
    )
    rows = touches_of(journeys.build_touches(orders, sessions), "o1")
    assert keys(rows) == ["s-early", "s-order"]
    assert rows[-1]["is_order_session"] and rows[-1]["touch_position"] == rows[-1]["touch_count"]


def test_order_session_is_a_touch_even_when_its_person_differs(frames):
    # An account switch mid-session: the session went to the last account (D), the order to the buyer (U).
    orders, sessions = frames(
        [order("o1", "U", T0, "s-order")],
        [session("s-u", "U", T0 - timedelta(days=3), "email"), session("s-order", "D", T0 - timedelta(minutes=20))],
    )
    assert keys(touches_of(journeys.build_touches(orders, sessions), "o1")) == ["s-u", "s-order"]


def test_ties_are_broken_the_same_way_whatever_the_row_order(spark, frames):
    tied = T0 - timedelta(hours=2)
    rows = [
        session("s-c", "P", tied, "email"),
        session("s-order", "P", tied, "cpc"),  # tied with s-a and s-c: goes last of the three
        session("s-a", "P", tied, "organic"),
        session("s-early", "P", T0 - timedelta(days=1), "direct"),
    ]
    expected = ["s-early", "s-a", "s-c", "s-order"]
    for seed in range(4):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        orders, sessions = frames([order("o1", "P", T0, "s-order")], shuffled)
        sessions = sessions.repartition(3)
        assert keys(touches_of(journeys.build_touches(orders, sessions), "o1")) == expected


def test_only_real_orders_in_a_session_are_attributed(frames):
    orders, sessions = frames(
        [
            order("real", "P", T0, "s1"),
            order("zero-value-no-id", "P", T0, "s1", revenue=None, zero_value_without_id=True),
            order("cookieless", "P", T0, None),
            order("no-revenue-kept", "P", T0, "s1", revenue=None),  # a real order with NULL revenue: attributed, $0
        ],
        [session("s1", "P", T0 - timedelta(minutes=30))],
    )
    touches = journeys.build_touches(orders, sessions)
    assert sorted({r.order_id for r in touches.collect()}) == ["no-revenue-kept", "real"]
    assert touches_of(touches, "no-revenue-kept")[0]["order_revenue_usd"] == 0.0


def test_two_orders_by_one_person_share_touches(frames):
    t1 = T0 - timedelta(days=2)
    orders, sessions = frames(
        [order("o1", "P", t1 + timedelta(minutes=10), "s2"), order("o2", "P", T0, "s3")],
        [
            session("s1", "P", T0 - timedelta(days=5), "email"),
            session("s2", "P", t1, "cpc"),
            session("s3", "P", T0 - timedelta(minutes=15), "direct"),
        ],
    )
    touches = journeys.build_touches(orders, sessions)
    assert keys(touches_of(touches, "o1")) == ["s1", "s2"]
    assert keys(touches_of(touches, "o2")) == ["s1", "s2", "s3"]
    assert [r["is_order_session"] for r in touches_of(touches, "o2")] == [False, False, True]


def test_lookback_complete_compares_the_window_start_with_the_first_session_of_the_source(frames):
    first = DATA_START
    orders, sessions = frames(
        [
            order("o-29d", "P", first + timedelta(days=29), "s1"),
            order("o-30d", "P", first + timedelta(days=30), "s2"),  # window starts exactly at the first session
            order("o-45d", "P", first + timedelta(days=45), "s3"),
        ],
        [
            session("s1", "P", first + timedelta(days=29) - timedelta(minutes=5)),
            session("s2", "P", first + timedelta(days=30) - timedelta(minutes=5)),
            session("s3", "P", first + timedelta(days=45) - timedelta(minutes=5)),
        ],
    )
    flags = {r.order_id: r.lookback_complete for r in journeys.build_touches(orders, sessions).collect()}
    assert flags == {"o-29d": False, "o-30d": True, "o-45d": True}


def test_direct_is_ga4s_definition(spark):
    df = spark.createDataFrame(
        [("(direct)", "(none)"), ("(direct)", "(not set)"), ("(not set)", "(not set)"), ("google", "organic"), ("(direct)", "referral"), (None, None)],
        "source string, medium string",
    )
    got = [r.d for r in df.select(journeys.is_direct(F.col("source"), F.col("medium")).alias("d")).collect()]
    assert got == [True, True, False, False, False, False]
