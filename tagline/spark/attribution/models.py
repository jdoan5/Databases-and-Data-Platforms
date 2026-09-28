"""The six attribution models, the daily mart, and the invariants checked before anything is written.

Each model gives every touch of an order a weight; an order's weights sum to 1 under every model, so
attributed orders (the sum of weights) and attributed revenue (weight x order revenue) move credit
between channels without creating or losing any. Touch positions come from journeys.build_touches
(1 = first touch, touch_count = last; ties broken there).

| model            | weight of touch p of n                                                             |
|------------------|------------------------------------------------------------------------------------|
| last_click       | 1 for the last touch, which is always the order session                            |
| last_non_direct  | 1 for the last touch that is not Direct; 1 for the last touch if all are Direct     |
| first_click      | 1 for the first touch in the 30-day window                                         |
| linear           | 1 / n                                                                               |
| time_decay       | 2^(-days_before_order / 7), normalised to sum to 1: a 7-day half-life before the order |
| position_based   | 40% first, 40% last, 20% shared by the middle; 1 touch = 100%, 2 touches = 50/50   |

A journey ends at the order session (journeys.py), so last_click credits the session the order was
placed in, exactly as Stage 2 does: its revenue by channel equals Stage 2's (the last_click check in
airflow/dags/tagline_airflow/sql/attribution_checks/).
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

HALF_LIFE_DAYS = 7.0
MODELS = ("last_click", "last_non_direct", "first_click", "linear", "time_decay", "position_based")
POSITION_BASED_ENDS = 0.4  # each of the first and the last touch; the middle touches share the rest (0.2)

MART_KEYS = (
    "order_date",
    "source",
    "model",
    "session_source",
    "session_medium",
    "session_campaign",
    "lookback_complete",
)


def model_weights(touches: DataFrame, half_life_days: float = HALF_LIFE_DAYS) -> dict[str, Column]:
    """{model: weight Column} over build_touches output. Window functions are evaluated per order."""
    journey = Window.partitionBy("source", "order_id")
    p, n = F.col("touch_position"), F.col("touch_count")
    decay = F.pow(F.lit(2.0), -F.col("days_before_order") / F.lit(float(half_life_days)))
    last_non_direct = F.coalesce(F.max(F.when(~F.col("is_direct"), p)).over(journey), n)
    ends = POSITION_BASED_ENDS
    return {
        "last_click": F.when(p == n, 1.0).otherwise(0.0),
        "last_non_direct": F.when(p == last_non_direct, 1.0).otherwise(0.0),
        "first_click": F.when(p == 1, 1.0).otherwise(0.0),
        "linear": F.lit(1.0) / n,
        "time_decay": decay / F.sum(decay).over(journey),
        "position_based": (
            F.when(n == 1, 1.0)
            .when(n == 2, 0.5)
            .when((p == 1) | (p == n), ends)
            .otherwise(F.lit(1.0 - 2 * ends) / (n - 2))
        ),
    }


def attribute(touches: DataFrame, half_life_days: float = HALF_LIFE_DAYS) -> DataFrame:
    """fct_attribution: one row per order x model x touch (every touch under every model, weight 0 included),
    with `weight` and `attributed_revenue_usd`."""
    weights = model_weights(touches, half_life_days)
    # Window results as plain columns first; the explode below then only references columns.
    wide = touches.select("*", *[w.cast("double").alias(f"_w_{m}") for m, w in weights.items()])
    pairs = F.array(*[F.struct(F.lit(m).alias("model"), F.col(f"_w_{m}").alias("weight")) for m in MODELS])
    return (
        wide.select(*touches.columns, F.explode(pairs).alias("_m"))
        .select(*touches.columns, F.col("_m.model").alias("model"), F.col("_m.weight").alias("weight"))
        .withColumn("attributed_revenue_usd", F.col("weight") * F.col("order_revenue_usd"))
    )


def daily_mart(attribution: DataFrame) -> DataFrame:
    """mart_attribution_daily: order date x data source x model x session source / medium / campaign x
    lookback_complete, with fractional attributed orders and attributed revenue."""
    return attribution.groupBy(*MART_KEYS).agg(
        F.sum("weight").alias("attributed_orders"),
        F.sum("attributed_revenue_usd").alias("attributed_revenue_usd"),
    )


def problems(attribution: DataFrame, touches: DataFrame, tolerance: float = 1e-9) -> list[str]:
    """What is wrong with fct_attribution, as sentences; empty when it is fit to write. The job refuses to
    write on any problem, so a bad run fails its batch and leaves yesterday's tables in place.

    Checks: every attributed order has every model; weights are in [0, 1] and sum to 1 per order and model;
    attributed orders and revenue per model equal the orders' count and revenue; last_click puts its whole
    weight on the last touch, and that touch is the order session."""
    per_order = attribution.groupBy("source", "order_id", "model").agg(
        F.sum("weight").alias("weight_sum"),
        F.sum(F.when((F.col("weight") < 0) | (F.col("weight") > 1 + tolerance), 1).otherwise(0)).alias("bad"),
    )
    orders = touches.select("source", "order_id", "order_revenue_usd").distinct()
    expected_orders = orders.count()
    expected_revenue = orders.agg(F.sum("order_revenue_usd")).first()[0] or 0.0
    found: list[str] = []

    off = per_order.where(F.abs(F.col("weight_sum") - 1) > tolerance).count()
    if off:
        found.append(f"{off} order x model weight sums differ from 1 by more than {tolerance}")
    bad = per_order.agg(F.sum("bad")).first()[0] or 0
    if bad:
        found.append(f"{bad} weights outside [0, 1]")
    counts = {r["model"]: r for r in per_order.groupBy("model").agg(F.count(F.lit(1)).alias("orders")).collect()}
    totals = {
        r["model"]: r
        for r in attribution.groupBy("model")
        .agg(F.sum("weight").alias("orders"), F.sum("attributed_revenue_usd").alias("revenue"))
        .collect()
    }
    for model in MODELS:
        if model not in counts or counts[model]["orders"] != expected_orders:
            got = counts[model]["orders"] if model in counts else 0
            found.append(f"{model}: {got} orders attributed, expected {expected_orders}")
            continue
        if abs(totals[model]["orders"] - expected_orders) > 1e-6:
            found.append(f"{model}: attributed orders {totals[model]['orders']:.6f} != {expected_orders}")
        if abs((totals[model]["revenue"] or 0.0) - expected_revenue) > 0.005:
            found.append(f"{model}: attributed revenue {totals[model]['revenue']:.2f} != {expected_revenue:.2f}")
    unexpected = set(totals) - set(MODELS)
    if unexpected:
        found.append(f"unexpected models: {sorted(unexpected)}")
    misplaced = attribution.where(
        (F.col("model") == "last_click")
        & (F.col("weight") > 0)
        & ((F.col("touch_position") != F.col("touch_count")) | ~F.col("is_order_session"))
    ).count()
    if misplaced:
        found.append(f"{misplaced} last_click weights not on the last touch or not on the order session")
    return found
