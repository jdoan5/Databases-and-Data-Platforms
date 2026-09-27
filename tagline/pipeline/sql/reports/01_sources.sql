-- @table Per source: events, sessions, people, orders and revenue after every dedupe.
WITH e AS (
  SELECT source, MIN(event_date) AS first_date, MAX(event_date) AS last_date,
    SUM(export_row_count) AS export_rows, COUNT(*) AS events, COUNT(DISTINCT user_pseudo_id) AS devices,
    COUNTIF(user_id IS NOT NULL) AS events_with_user_id, COUNTIF(session_key IS NULL) AS events_without_session,
    COUNTIF(event_name = 'purchase') AS purchase_events
  FROM `{{ project }}.{{ staging }}.stg_events`
  GROUP BY source
),
s AS (
  SELECT source, COUNT(*) AS sessions, COUNTIF(is_engaged) AS engaged_sessions, COUNT(DISTINCT person_id) AS people,
    COUNTIF(converted) AS converted_sessions
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY source
),
o AS (
  SELECT source, COUNT(*) AS orders, COUNTIF(NOT has_transaction_id) AS orders_without_transaction_id,
    COUNTIF(is_zero_value_without_id) AS zero_value_orders_without_id, COUNTIF(session_key IS NULL) AS orders_without_session,
    COUNT(DISTINCT IF(is_transaction_id_collision, transaction_id, NULL)) AS transaction_ids_on_2plus_devices,
    SUM(duplicate_purchase_events) AS duplicate_purchases_dropped,
    ROUND(SUM(revenue_usd), 2) AS revenue_usd, ROUND(SUM(tax_usd), 2) AS tax_usd, ROUND(SUM(shipping_usd), 2) AS shipping_usd
  FROM `{{ project }}.{{ marts }}.fct_orders`
  GROUP BY source
)
SELECT
  source, CAST(first_date AS STRING) AS first_date, CAST(last_date AS STRING) AS last_date,
  export_rows, e.events, devices, events_with_user_id, events_without_session,
  s.sessions, engaged_sessions, people, purchase_events, duplicate_purchases_dropped, o.orders,
  orders_without_transaction_id, zero_value_orders_without_id, orders_without_session, transaction_ids_on_2plus_devices,
  converted_sessions, revenue_usd, tax_usd, shipping_usd
FROM e
LEFT JOIN s USING (source)
LEFT JOIN o USING (source)
ORDER BY source
