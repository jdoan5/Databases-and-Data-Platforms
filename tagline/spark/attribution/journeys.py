"""Which orders are attributed, and the touches (sessions) of each order's journey.

The rules, settled here and in the tests (tests/test_journeys.py):

* **Attributed orders** are Stage 2's real orders placed in a session: `fct_orders` rows that are not
  `is_zero_value_without_id` (Stage 2's definition of a real order: the ones fct_sessions and the marts
  count) and have a `session_key`. An order in no session (a cookieless purchase) is in no Stage 2
  channel mart either, and is not attributed. On the GA4 sample that is 4,918 orders, all in a session.
* **Touches** are the order person's sessions (`fct_sessions.person_id = fct_orders.person_id`, same
  data source) from the 30 days before the purchase up to and including the order's own session:
  `ordered_at - 30 days <= session_start_at <= the order session's session_start_at`, both ends
  inclusive, 30 days meaning 30 x 24 hours. The journey ends at the order session, not at the
  purchase: a session that started after the order session is not a touch, even one opened (in
  another tab, say) before the purchase while the order session was still going. So the order
  session is always the last touch, and last_click is Stage 2's channel for every order. The order
  session itself is always a touch, even if its person differs from the order's (an account switch
  mid-session: the order belongs to the account that bought, the session to the last account seen).
* **Order of touches**: by `session_start_at`; on a tie, the order session goes last, then the others
  by `session_key`. Position 1 is the first touch, position `touch_count` the last (the order
  session). The tie rule makes every model deterministic whatever order the rows arrive in.
* **Direct** is GA4's definition: source `(direct)` with medium `(none)` or `(not set)`. Stage 2's
  `(not set) / (not set)` (nothing collected: unknown) is not direct, as Stage 2 keeps it apart.
* **lookback_complete** is FALSE when the 30-day window starts before the first session of the order's
  data source (the GA4 sample starts on 2020-11-01), so the journey may be missing touches. Those
  orders are still attributed (revenue stays conserved); reports show them apart.
"""

from __future__ import annotations

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

LOOKBACK_DAYS = 30
_MICROS_PER_DAY = 86_400 * 1_000_000

# The only columns read from BigQuery (the connector prunes to these).
ORDER_COLUMNS = (
    "source",
    "order_id",
    "order_date",
    "ordered_at",
    "person_id",
    "session_key",
    "revenue_usd",
    "is_zero_value_without_id",
)
SESSION_COLUMNS = (
    "source",
    "session_key",
    "person_id",
    "session_start_at",
    "session_source",
    "session_medium",
    "session_campaign",
)

TOUCH_COLUMNS = (
    "source",
    "order_id",
    "order_date",
    "ordered_at",
    "person_id",
    "order_session_key",
    "order_revenue_usd",
    "lookback_complete",
    "touch_position",
    "touch_count",
    "session_key",
    "session_start_at",
    "session_source",
    "session_medium",
    "session_campaign",
    "is_direct",
    "is_order_session",
    "days_before_order",
)


def attributable_filter() -> Column:
    """Stage 2's real orders that were placed in a session. A Column so the job can push it down to the read."""
    return ~F.col("is_zero_value_without_id") & F.col("session_key").isNotNull()


def is_direct(source: Column, medium: Column) -> Column:
    """GA4's Direct: source (direct) and medium (none) or (not set). Never NULL."""
    return F.coalesce((source == "(direct)") & medium.isin("(none)", "(not set)"), F.lit(False))


def data_starts(sessions: DataFrame) -> DataFrame:
    """The first session start of each data source: where every journey's history begins."""
    return sessions.groupBy("source").agg(F.min("session_start_at").alias("data_start_at"))


def build_touches(orders: DataFrame, sessions: DataFrame, lookback_days: int = LOOKBACK_DAYS) -> DataFrame:
    """One row per attributed order and touch, with its position in the journey (TOUCH_COLUMNS).

    `orders` needs ORDER_COLUMNS (fct_orders), `sessions` SESSION_COLUMNS (fct_sessions); the order filter
    is applied here too, so pushing it down in the read is an optimisation, not a requirement."""
    lookback = lookback_days * _MICROS_PER_DAY
    o = (
        orders.where(attributable_filter())
        .select(
            "source",
            "order_id",
            "order_date",
            "ordered_at",
            "person_id",
            F.col("session_key").alias("order_session_key"),
            F.coalesce(F.col("revenue_usd").cast("double"), F.lit(0.0)).alias("order_revenue_usd"),
        )
    )
    s = sessions.select(
        "source",
        "session_key",
        F.col("person_id").alias("session_person_id"),
        "session_start_at",
        "session_source",
        "session_medium",
        "session_campaign",
    )
    # Where each journey ends: the order session's start. `sessions` is joined more than once, so the joins
    # below name their sides by alias ("o", "s") rather than by DataFrame.
    order_start = s.select(
        F.col("source").alias("_os_source"),
        F.col("session_key").alias("_os_key"),
        F.col("session_start_at").alias("order_session_start_at"),
    )
    o = (
        o.join(
            order_start,
            (F.col("source") == F.col("_os_source")) & (F.col("order_session_key") == F.col("_os_key")),
            "inner",
        )
        .drop("_os_source", "_os_key")
        .alias("o")
    )
    s = s.alias("s")
    session_cols = [F.col(f"s.{c}") for c in s.columns if c != "source"]
    before = F.unix_micros(F.col("ordered_at")) - F.unix_micros(F.col("session_start_at"))

    # The person's sessions in [ordered_at - lookback, the order session's start] ...
    by_person = (
        o.join(s, (F.col("o.source") == F.col("s.source")) & (F.col("o.person_id") == F.col("s.session_person_id")), "inner")
        .where((F.col("s.session_start_at") <= F.col("o.order_session_start_at")) & (before <= lookback))
        .select("o.*", *session_cols)
    )
    # ... plus the order session, always (two equi-joins instead of one join on an OR, which Spark could
    # only run as a nested loop).
    own = o.join(
        s, (F.col("o.source") == F.col("s.source")) & (F.col("o.order_session_key") == F.col("s.session_key")), "inner"
    ).select("o.*", *session_cols)
    touches = by_person.unionByName(own).dropDuplicates(["source", "order_id", "session_key"])

    journey = Window.partitionBy("source", "order_id")
    ordered = journey.orderBy(
        F.col("session_start_at").asc(), F.col("is_order_session").asc(), F.col("session_key").asc()
    )
    starts = data_starts(sessions)
    return (
        touches.withColumn("is_order_session", F.col("session_key") == F.col("order_session_key"))
        .withColumn("touch_position", F.row_number().over(ordered))
        .withColumn("touch_count", F.count(F.lit(1)).over(journey))
        .withColumn("is_direct", is_direct(F.col("session_source"), F.col("session_medium")))
        .withColumn("days_before_order", before.cast("double") / _MICROS_PER_DAY)
        .join(starts, "source", "left")
        .withColumn(
            "lookback_complete",
            F.coalesce(
                F.unix_micros(F.col("ordered_at")) - F.lit(lookback) >= F.unix_micros(F.col("data_start_at")),
                F.lit(False),
            ),
        )
        .select(*TOUCH_COLUMNS)
    )
