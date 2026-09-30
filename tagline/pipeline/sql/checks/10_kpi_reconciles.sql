-- @check mart_kpi_daily has one row per date and source, for every day with a session, an order or (for sources
--     that record consent) an event; on every such day its sessions, engaged, converted, add-to-cart and checkout
--     sessions equal fct_sessions', its orders, revenue and cookieless orders equal fct_orders' (zero-value purchases
--     without a transaction_id not counted), its events and consented events equal stg_events'; and every rate is
--     between 0 and 1.
WITH sessions AS (
  SELECT session_date AS date, source, COUNT(*) AS sessions, COUNTIF(is_engaged) AS engaged, COUNTIF(converted) AS converted,
    COUNTIF(has_add_to_cart) AS add_to_cart, COUNTIF(has_begin_checkout) AS checkout
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY date, source
),
orders AS (
  SELECT order_date AS date, source, COUNTIF(NOT is_zero_value_without_id) AS orders,
    ROUND(SUM(IF(is_zero_value_without_id, 0, COALESCE(revenue_usd, 0))), 2) AS revenue,
    COUNTIF(NOT is_zero_value_without_id AND session_key IS NULL) AS cookieless
  FROM `{{ project }}.{{ marts }}.fct_orders`
  GROUP BY date, source
),
events AS (
  SELECT event_date AS date, source, COUNT(*) AS events, COUNTIF(analytics_storage = 'Yes') AS consented
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE source != 'ga4_sample'
  GROUP BY date, source
),
expected AS (
  SELECT date, source, COALESCE(s.sessions, 0) AS sessions, COALESCE(s.engaged, 0) AS engaged,
    COALESCE(s.converted, 0) AS converted, COALESCE(s.add_to_cart, 0) AS add_to_cart, COALESCE(s.checkout, 0) AS checkout,
    COALESCE(o.orders, 0) AS orders, COALESCE(o.revenue, 0) AS revenue, COALESCE(o.cookieless, 0) AS cookieless,
    e.events, e.consented
  FROM sessions AS s
  FULL OUTER JOIN orders AS o USING (date, source)
  FULL OUTER JOIN events AS e USING (date, source)
),
mart AS (
  SELECT *, COUNT(*) OVER (PARTITION BY date, source) AS copies
  FROM `{{ project }}.{{ marts }}.mart_kpi_daily`
)
SELECT
  COALESCE(x.date, m.date) AS date,
  COALESCE(x.source, m.source) AS source,
  m.copies,
  x.sessions AS fact_sessions, m.sessions AS mart_sessions,
  x.orders AS fact_orders, m.orders AS mart_orders,
  x.revenue AS fact_revenue, m.revenue_usd AS mart_revenue,
  x.events AS fact_events, m.events AS mart_events
FROM expected AS x
FULL OUTER JOIN mart AS m ON m.date = x.date AND m.source = x.source
WHERE x.date IS NULL OR m.date IS NULL
   OR m.copies > 1 OR m.source IS NULL
   OR m.sessions != x.sessions OR m.engaged_sessions != x.engaged OR m.converted_sessions != x.converted
   OR m.add_to_cart_sessions != x.add_to_cart OR m.checkout_sessions != x.checkout
   OR m.orders != x.orders OR ABS(m.revenue_usd - x.revenue) > 0.005 OR m.cookieless_orders != x.cookieless
   OR m.events IS DISTINCT FROM x.events OR m.consented_events IS DISTINCT FROM x.consented
   OR NOT COALESCE(m.engaged_session_rate BETWEEN 0 AND 1, TRUE)
   OR NOT COALESCE(m.conversion_rate BETWEEN 0 AND 1, TRUE)
   OR NOT COALESCE(m.add_to_cart_rate BETWEEN 0 AND 1, TRUE)
   OR NOT COALESCE(m.checkout_to_purchase_rate BETWEEN 0 AND 1, TRUE)
   OR NOT COALESCE(m.cookieless_order_share BETWEEN 0 AND 1, TRUE)
   OR NOT COALESCE(m.consent_accept_share BETWEEN 0 AND 1, TRUE)
