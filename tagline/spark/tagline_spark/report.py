"""The attribution results as tables (make spark-report): how channel credit shifts between the six models,
for all orders and for the orders whose 30-day journey lies entirely inside the data, whether last_click
ever leaves the order session (Stage 2's channel), and an independent rebuild of fct_attribution in SQL
(spark/sql/independent_rebuild.sql) compared row by row. Run from tagline/spark: `python -m tagline_spark.report`.

Three small query jobs on tagline_marts (fct_attribution; the rebuild also reads fct_orders and fct_sessions), each
with maximum_bytes_billed (TAGLINE_MAX_BYTES_BILLED, 10 GB by default, as Stage 2), no query cache and labels
app=tagline, stage=3, kind=report.
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

from . import batch as spark_batch
from . import submit

MODELS = ("last_click", "last_non_direct", "first_click", "linear", "time_decay", "position_based")
SHORT = {"last_click": "last", "last_non_direct": "last n-d", "first_click": "first", "linear": "linear", "time_decay": "decay", "position_based": "position"}
TOP_CHANNELS = 10

# From fct_attribution rather than mart_attribution_daily (its sum by day and channel), so the report needs only the
# fact table.
BY_CHANNEL = """
SELECT
  IF(lookback_complete, 'complete', 'incomplete') AS lookback,
  model,
  CONCAT(session_source, ' / ', session_medium) AS channel,
  SUM(weight) AS orders,
  SUM(attributed_revenue_usd) AS revenue
FROM `{project}.{marts}.fct_attribution`
GROUP BY 1, 2, 3
"""

JOURNEYS = """
WITH orders AS (
  SELECT source, order_id, ANY_VALUE(touch_count) AS touches, ANY_VALUE(lookback_complete) AS complete,
    ANY_VALUE(order_revenue_usd) AS revenue,
    LOGICAL_OR(model = 'last_click' AND weight > 0 AND NOT is_order_session) AS elsewhere,
    LOGICAL_AND(is_direct) AS direct_only
  FROM `{project}.{marts}.fct_attribution`
  GROUP BY 1, 2
)
SELECT
  COUNT(*) AS orders,
  SUM(revenue) AS revenue,
  COUNTIF(NOT complete) AS incomplete_orders,
  SUM(IF(NOT complete, revenue, 0)) AS incomplete_revenue,
  SUM(touches) AS touches,
  COUNTIF(touches = 1) AS one_touch,
  COUNTIF(touches = 2) AS two_touches,
  COUNTIF(touches BETWEEN 3 AND 5) AS three_to_five,
  COUNTIF(touches > 5) AS six_plus,
  MAX(touches) AS max_touches,
  COUNTIF(touches > 1 AND complete) / COUNTIF(complete) AS multi_touch_share_complete,
  COUNTIF(touches > 1 AND NOT complete) / NULLIF(COUNTIF(NOT complete), 0) AS multi_touch_share_incomplete,
  COUNTIF(direct_only) AS direct_only_orders,
  COUNTIF(elsewhere) AS last_click_elsewhere_orders
FROM orders
"""

REBUILD_SQL = spark_batch.SPARK_DIR / "sql" / "independent_rebuild.sql"


def run_query(client, sql: str, step: str, max_bytes: int):
    from google.cloud import bigquery

    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=max_bytes,
        use_query_cache=False,
        labels={"app": "tagline", "stage": "3", "kind": "report", "step": step},
    )
    job = client.query(sql, job_config=config)
    rows = list(job.result())
    return rows, job.total_bytes_billed or 0


def channel_table(rows, scope: str) -> str:
    revenue: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if scope == "all" or r.lookback == scope:
            revenue[r.channel][r.model] += r.revenue
    totals = {m: sum(v[m] for v in revenue.values()) for m in MODELS}
    top = sorted(revenue, key=lambda c: -max(revenue[c][m] for m in MODELS))[:TOP_CHANNELS]
    other = {m: totals[m] - sum(revenue[c][m] for c in top) for m in MODELS}
    width = max(len(c) for c in top + ["channel (source / medium)"])
    lines = [f"{'channel (source / medium)':<{width}}  " + "  ".join(f"{SHORT[m]:>9}" for m in MODELS)]
    for name, values in [*((c, revenue[c]) for c in top), (f"other ({len(revenue) - len(top)} channels)", other)]:
        lines.append(f"{name:<{width}}  " + "  ".join(f"{100 * values[m] / totals[m]:>8.1f}%" for m in MODELS))
    lines.append(f"{'total revenue':<{width}}  " + "  ".join(f"{totals[m]:>9,.0f}" for m in MODELS))
    return "\n".join(lines)


def main() -> int:
    from google.cloud import bigquery

    try:
        target = submit.load_target()
    except submit.ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    env_text = submit.ENV_FILE.read_text(encoding="utf-8") if submit.ENV_FILE.exists() else ""
    max_bytes = int(os.environ.get("TAGLINE_MAX_BYTES_BILLED") or submit.parse_env_file(env_text).get("TAGLINE_MAX_BYTES_BILLED") or 10 * 1000**3)
    client = bigquery.Client(project=target.project, location="US")
    names = {"project": target.project, "marts": target.marts_dataset}
    billed = 0
    by_channel, b = run_query(client, BY_CHANNEL.format(**names), "attribution_by_channel", max_bytes)
    billed += b
    (j,), b = run_query(client, JOURNEYS.format(**names), "attribution_journeys", max_bytes)
    billed += b
    (r,), b = run_query(client, REBUILD_SQL.read_text(encoding="utf-8").format(**names), "attribution_rebuild", max_bytes)
    billed += b

    print(
        f"orders {j.orders:,} (${j.revenue:,.2f}); touches {j.touches:,}: 1 touch {j.one_touch:,}, 2 {j.two_touches:,}, "
        f"3-5 {j.three_to_five:,}, 6+ {j.six_plus:,} (max {j.max_touches})"
    )
    print(
        f"lookback incomplete (30-day window starts before the data): {j.incomplete_orders:,} orders "
        f"(${j.incomplete_revenue:,.2f}); multi-touch share {j.multi_touch_share_complete:.1%} of complete-lookback "
        f"orders vs {j.multi_touch_share_incomplete or 0:.1%} of incomplete"
    )
    print(f"orders whose every touch is Direct: {j.direct_only_orders:,}")
    for scope, title in (("all", "all orders"), ("complete", "complete-lookback orders only")):
        print(f"\nShare of attributed revenue by channel, {title}:\n{channel_table(by_channel, scope)}")
    print(
        f"\nlast_click vs Stage 2: {j.last_click_elsewhere_orders:,} orders credited to a session other than the one the "
        "order was placed in (0 expected: a journey ends at the order session)"
    )
    print(
        f"independent SQL rebuild ({REBUILD_SQL.relative_to(spark_batch.SPARK_DIR)}): {r.rows_compared:,} rows compared, "
        f"{r.only_in_spark:,} only in fct_attribution, {r.only_in_rebuild:,} only in the rebuild, {r.position_differs:,} "
        f"with another position, max weight difference {r.max_weight_diff or 0:.1e}, max revenue difference "
        f"{r.max_revenue_diff or 0:.1e}"
    )
    print(f"\n3 query jobs, {billed / 1024**2:,.0f} MiB billed (maximum_bytes_billed {max_bytes:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
