"""The two output tables: column order, descriptions, layout. The job writes the columns in this order and
then sets the table and column descriptions and labels, as Stage 2 does for every table it builds."""

from __future__ import annotations

LABELS = {"app": "tagline", "stage": "3"}
PARTITION_FIELD = "order_date"  # like Stage 2's facts; at this volume it prunes nothing (Stage 4 decides)

FCT_ATTRIBUTION = "fct_attribution"
MART_ATTRIBUTION_DAILY = "mart_attribution_daily"

FCT_ATTRIBUTION_DESCRIPTION = (
    "One row per order x attribution model x touch. Built by tagline/spark (PySpark on Serverless for Apache "
    "Spark) from tagline_marts.fct_orders and fct_sessions. Orders: Stage 2's real orders placed in a session "
    "(not is_zero_value_without_id). Touches: the order person's sessions that started at most 30 days "
    "(30 x 24 h) before the purchase (ordered_at) and no later than the order session, plus the order session; "
    "ordered by session start, a tie putting the order session last and then session_key, so the order session "
    "is always the last touch. Models: last_click (= Stage 2's session channel), "
    "last_non_direct (Direct = (direct) with (none) or (not set); (not set) is unknown, not direct), "
    "first_click, linear, time_decay (7-day half-life before the order), position_based (40/20/40; one touch "
    "100%, two 50/50). Weights sum to 1 per order and model, so attributed orders and revenue per model equal "
    "the orders'. lookback_complete is FALSE when the 30-day window starts before the data source's first "
    "session (the GA4 sample starts 2020-11-01). On the GA4 sample a person is one device."
)
MART_ATTRIBUTION_DAILY_DESCRIPTION = (
    "Attributed orders (fractional) and revenue per order date x data source x attribution model x session "
    "source / medium / campaign x lookback_complete, summed from fct_attribution. Built by tagline/spark. "
    "Sum over lookback_complete for all orders; filter lookback_complete for orders whose 30-day journey is "
    "entirely inside the data. last_click equals Stage 2's orders and revenue by session source / medium / "
    "campaign (fct_sessions)."
)

FCT_ATTRIBUTION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source", "Data source: ga4_sample or tagline_site."),
    ("order_id", "fct_orders.order_id; (source, order_id, model, session_key) is the key."),
    ("order_date", "fct_orders.order_date: event day of the purchase (property time zone). Partition column."),
    ("ordered_at", "fct_orders.ordered_at: timestamp of the purchase event. Touches start at most 30 days before it."),
    ("person_id", "fct_orders.person_id: whose sessions make the journey. On the GA4 sample, one device."),
    ("model", "last_click, last_non_direct, first_click, linear, time_decay or position_based."),
    ("touch_position", "1 = first touch of the journey; touch_count = last (the order session). By session start; ties: order session last, then session_key."),
    ("touch_count", "Touches in the order's journey (the same under every model)."),
    ("session_key", "The touch: fct_sessions.session_key."),
    ("session_start_at", "The touch session's start (first event)."),
    ("session_source", "The touch session's source (fct_sessions.session_source, Stage 2's session attribution)."),
    ("session_medium", "The touch session's medium."),
    ("session_campaign", "The touch session's campaign."),
    ("is_direct", "The touch is Direct: source (direct), medium (none) or (not set). last_non_direct skips these."),
    ("is_order_session", "The touch is the session the order was placed in (fct_orders.session_key)."),
    ("days_before_order", "(ordered_at - session_start_at) in days, fractional; 0 to 30 except, in principle, the order session."),
    ("weight", "The touch's share of the order under this model, 0 to 1; sums to 1 per order and model."),
    ("order_revenue_usd", "fct_orders.revenue_usd (0 when NULL)."),
    ("attributed_revenue_usd", "weight x order_revenue_usd."),
    ("lookback_complete", "FALSE when ordered_at - 30 days is before the data source's first session: the journey may be missing touches."),
    ("order_session_key", "fct_orders.session_key: the session the order was placed in."),
)

MART_ATTRIBUTION_DAILY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("order_date", "Order date (fct_orders.order_date). Partition column."),
    ("source", "Data source: ga4_sample or tagline_site."),
    ("model", "Attribution model (see fct_attribution)."),
    ("session_source", "Touch session source."),
    ("session_medium", "Touch session medium."),
    ("session_campaign", "Touch session campaign."),
    ("lookback_complete", "The orders' 30-day journeys lie entirely inside the data (see fct_attribution)."),
    ("attributed_orders", "Sum of weights: fractional orders credited to this channel under this model."),
    ("attributed_revenue_usd", "Sum of attributed_revenue_usd."),
)

TABLES = {
    FCT_ATTRIBUTION: (FCT_ATTRIBUTION_DESCRIPTION, FCT_ATTRIBUTION_COLUMNS),
    MART_ATTRIBUTION_DAILY: (MART_ATTRIBUTION_DAILY_DESCRIPTION, MART_ATTRIBUTION_DAILY_COLUMNS),
}


def column_names(table: str) -> list[str]:
    return [name for name, _ in TABLES[table][1]]
