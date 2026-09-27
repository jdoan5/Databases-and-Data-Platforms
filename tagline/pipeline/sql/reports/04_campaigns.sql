-- @table Campaigns with SYNTHETIC cost, whole period: sessions, orders, revenue, cost, ROAS, cost per order.
WITH totals AS (
  SELECT
    source, session_source, session_medium, session_campaign,
    SUM(sessions) AS sessions, SUM(orders) AS orders, SUM(revenue_usd) AS revenue, SUM(cost_usd) AS cost
  FROM `{{ project }}.{{ marts }}.mart_campaign_daily`
  GROUP BY source, session_source, session_medium, session_campaign
)
SELECT
  source, session_source, session_medium, session_campaign, sessions, orders,
  ROUND(revenue, 2) AS revenue_usd,
  ROUND(cost, 2) AS cost_usd,
  ROUND(SAFE_DIVIDE(revenue, cost), 2) AS roas,
  ROUND(SAFE_DIVIDE(cost, NULLIF(orders, 0)), 2) AS cost_per_order
FROM totals
WHERE cost IS NOT NULL
ORDER BY source, cost DESC
