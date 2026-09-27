-- @table Funnel over the whole period, per source (closed funnel; step rates relative to the step before).
SELECT
  source,
  SUM(sessions) AS sessions,
  SUM(view_item_sessions) AS view_item,
  SUM(add_to_cart_sessions) AS add_to_cart,
  SUM(begin_checkout_sessions) AS begin_checkout,
  SUM(purchase_sessions) AS purchase,
  SUM(converted_sessions) AS converted_any_path,
  ROUND(SAFE_DIVIDE(SUM(view_item_sessions), SUM(sessions)), 4) AS view_item_rate,
  ROUND(SAFE_DIVIDE(SUM(add_to_cart_sessions), SUM(view_item_sessions)), 4) AS add_to_cart_rate,
  ROUND(SAFE_DIVIDE(SUM(begin_checkout_sessions), SUM(add_to_cart_sessions)), 4) AS begin_checkout_rate,
  ROUND(SAFE_DIVIDE(SUM(purchase_sessions), SUM(begin_checkout_sessions)), 4) AS purchase_rate,
  ROUND(SAFE_DIVIDE(SUM(converted_sessions), SUM(sessions)), 4) AS session_conversion_rate
FROM `{{ project }}.{{ marts }}.mart_funnel_daily`
GROUP BY source
ORDER BY source
