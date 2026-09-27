-- @check Per source: fct_orders (count, zero-value orders, revenue, tax, shipping, distinct transaction ids) equals
--     the purchase events in stg_events after the transaction_id dedupe; kept + dropped purchases equal all
--     purchase events; fct_sessions orders + zero_value_orders and revenue equal the fct_orders that have a session.
WITH events AS (
  SELECT
    source,
    COUNTIF(NOT is_duplicate_purchase) AS orders,
    COUNTIF(is_duplicate_purchase) AS dropped,
    COUNT(*) AS purchase_events,
    COUNTIF(NOT is_duplicate_purchase AND is_zero_value_without_id) AS zero_value,
    COUNT(DISTINCT IF(transaction_id IS NOT NULL, order_id, NULL)) AS transaction_ids,
    ROUND(SUM(IF(NOT is_duplicate_purchase, purchase_revenue_usd, 0)), 2) AS revenue,
    ROUND(SUM(IF(NOT is_duplicate_purchase, tax_usd, 0)), 2) AS tax,
    ROUND(SUM(IF(NOT is_duplicate_purchase, shipping_usd, 0)), 2) AS shipping
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE event_name = 'purchase'
  GROUP BY source
),
orders AS (
  SELECT
    source,
    COUNT(*) AS orders,
    SUM(duplicate_purchase_events) AS dropped,
    COUNTIF(is_zero_value_without_id) AS zero_value,
    COUNTIF(has_transaction_id) AS transaction_ids,
    ROUND(SUM(COALESCE(revenue_usd, 0)), 2) AS revenue,
    ROUND(SUM(COALESCE(tax_usd, 0)), 2) AS tax,
    ROUND(SUM(COALESCE(shipping_usd, 0)), 2) AS shipping,
    COUNTIF(session_key IS NOT NULL) AS orders_in_sessions,
    ROUND(SUM(IF(session_key IS NOT NULL, COALESCE(revenue_usd, 0), 0)), 2) AS revenue_in_sessions
  FROM `{{ project }}.{{ marts }}.fct_orders`
  GROUP BY source
),
sessions AS (
  SELECT source, SUM(orders + zero_value_orders) AS orders, ROUND(SUM(COALESCE(revenue_usd, 0)), 2) AS revenue
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY source
)
SELECT
  source,
  e.orders AS event_orders, o.orders AS fct_orders, o.orders_in_sessions, s.orders AS session_orders,
  e.dropped AS event_dropped, o.dropped AS order_dropped, e.purchase_events,
  e.zero_value AS event_zero_value, o.zero_value AS order_zero_value,
  e.transaction_ids AS event_transaction_ids, o.transaction_ids AS order_transaction_ids,
  e.revenue AS event_revenue, o.revenue AS order_revenue, o.revenue_in_sessions, s.revenue AS session_revenue,
  e.tax AS event_tax, o.tax AS order_tax, e.shipping AS event_shipping, o.shipping AS order_shipping
FROM events AS e
FULL OUTER JOIN orders AS o USING (source)
FULL OUTER JOIN sessions AS s USING (source)
WHERE COALESCE(e.orders, 0) != COALESCE(o.orders, 0)
   OR COALESCE(s.orders, 0) != COALESCE(o.orders_in_sessions, 0)
   OR COALESCE(e.dropped, 0) != COALESCE(o.dropped, 0)
   OR COALESCE(e.orders, 0) + COALESCE(e.dropped, 0) != COALESCE(e.purchase_events, 0)
   OR COALESCE(e.zero_value, 0) != COALESCE(o.zero_value, 0)
   OR COALESCE(e.transaction_ids, 0) != COALESCE(o.transaction_ids, 0)
   OR ABS(COALESCE(e.revenue, 0) - COALESCE(o.revenue, 0)) > 0.005
   OR ABS(COALESCE(s.revenue, 0) - COALESCE(o.revenue_in_sessions, 0)) > 0.005
   OR ABS(COALESCE(e.tax, 0) - COALESCE(o.tax, 0)) > 0.005
   OR ABS(COALESCE(e.shipping, 0) - COALESCE(o.shipping, 0)) > 0.005
