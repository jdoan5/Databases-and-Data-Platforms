-- @table Daily KPIs (Stage 5): one row per date x source, the numbers the anomaly rules watch
--     (tagline_pipeline/anomaly.py, docs/monitoring.md). Session KPIs come from fct_sessions by session date (a
--     purchase with no transaction_id and no revenue is not a conversion there); orders and revenue from fct_orders by
--     order date, every order included, the ones placed in no session too (consent denied); consent from stg_events,
--     for the sources that record it (the GA4 sample has no privacy_info: its consent columns are NULL). Rates are
--     rounded to 6 decimals and NULL when their denominator is 0. Built by tagline/pipeline.
-- @column date: The day (property time zone): session date for session KPIs, order date for orders and revenue,
--     event date for events.
-- @column source: ga4_sample or tagline_site. (date, source) is the key.
-- @column sessions: Sessions that started that day.
-- @column engaged_sessions: Of those, engaged (GA4's session_engaged).
-- @column engaged_session_rate: engaged_sessions / sessions.
-- @column converted_sessions: Sessions with an order (fct_sessions.converted).
-- @column conversion_rate: converted_sessions / sessions.
-- @column add_to_cart_sessions: Sessions with an add_to_cart event.
-- @column add_to_cart_rate: add_to_cart_sessions / sessions (open: whatever else the session did).
-- @column checkout_sessions: Sessions with a begin_checkout event.
-- @column checkout_to_purchase_rate: Sessions with a begin_checkout and an order / checkout_sessions.
-- @column orders: Orders placed that day (fct_orders, zero-value purchases without a transaction_id not counted),
--     including orders placed in no session.
-- @column revenue_usd: Their revenue (purchase value: items subtotal, no tax or shipping).
-- @column aov_usd: revenue_usd / orders (average order value).
-- @column cookieless_orders: Orders placed in no session (consent denied: no device or session id), which session
--     KPIs and attribution cannot see.
-- @column cookieless_order_share: cookieless_orders / orders.
-- @column events: Events that day (stg_events), for the sources that record consent; NULL for the GA4 sample.
-- @column consented_events: Of those, sent with analytics_storage granted (Yes).
-- @column consent_accept_share: consented_events / events: the share of hits sent after the visitor accepted.

CREATE OR REPLACE TABLE `{{ project }}.{{ marts }}.mart_kpi_daily`
PARTITION BY date
AS
WITH sessions AS (
  SELECT
    session_date AS date,
    source,
    COUNT(*) AS sessions,
    COUNTIF(is_engaged) AS engaged_sessions,
    COUNTIF(converted) AS converted_sessions,
    COUNTIF(has_add_to_cart) AS add_to_cart_sessions,
    COUNTIF(has_begin_checkout) AS checkout_sessions,
    COUNTIF(has_begin_checkout AND converted) AS checkout_converted_sessions
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  WHERE TRUE
    -- make build-incremental narrows the three inputs to the days it recomputes (tagline_pipeline/incremental.py)
    {{ incremental_filter_sessions }}
  GROUP BY date, source
),

orders AS (
  SELECT
    order_date AS date,
    source,
    COUNTIF(NOT is_zero_value_without_id) AS orders,
    -- summed as NUMERIC, as in the other marts, so the total does not depend on the order rows are added in
    CAST(SUM(CAST(IF(is_zero_value_without_id, NULL, revenue_usd) AS NUMERIC)) AS FLOAT64) AS revenue_usd,
    COUNTIF(NOT is_zero_value_without_id AND session_key IS NULL) AS cookieless_orders
  FROM `{{ project }}.{{ marts }}.fct_orders`
  WHERE TRUE
    {{ incremental_filter_orders }}
  GROUP BY date, source
),

consent AS (
  -- The GA4 sample records no consent (privacy_info is NULL throughout; docs/data-model.md), so only the other sources
  -- are read: stg_events is clustered by source, and this reads the site's rows alone.
  SELECT
    event_date AS date,
    source,
    COUNT(*) AS events,
    COUNTIF(analytics_storage = 'Yes') AS consented_events
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE source != 'ga4_sample'
    {{ incremental_filter }}
  GROUP BY date, source
)

SELECT
  date,
  source,
  COALESCE(s.sessions, 0) AS sessions,
  COALESCE(s.engaged_sessions, 0) AS engaged_sessions,
  ROUND(SAFE_DIVIDE(s.engaged_sessions, s.sessions), 6) AS engaged_session_rate,
  COALESCE(s.converted_sessions, 0) AS converted_sessions,
  ROUND(SAFE_DIVIDE(s.converted_sessions, s.sessions), 6) AS conversion_rate,
  COALESCE(s.add_to_cart_sessions, 0) AS add_to_cart_sessions,
  ROUND(SAFE_DIVIDE(s.add_to_cart_sessions, s.sessions), 6) AS add_to_cart_rate,
  COALESCE(s.checkout_sessions, 0) AS checkout_sessions,
  ROUND(SAFE_DIVIDE(s.checkout_converted_sessions, s.checkout_sessions), 6) AS checkout_to_purchase_rate,
  COALESCE(o.orders, 0) AS orders,
  COALESCE(o.revenue_usd, 0) AS revenue_usd,
  ROUND(SAFE_DIVIDE(o.revenue_usd, o.orders), 2) AS aov_usd,
  COALESCE(o.cookieless_orders, 0) AS cookieless_orders,
  ROUND(SAFE_DIVIDE(o.cookieless_orders, o.orders), 6) AS cookieless_order_share,
  c.events,
  c.consented_events,
  ROUND(SAFE_DIVIDE(c.consented_events, c.events), 6) AS consent_accept_share
FROM sessions AS s
FULL OUTER JOIN orders AS o USING (date, source)
FULL OUTER JOIN consent AS c USING (date, source)
