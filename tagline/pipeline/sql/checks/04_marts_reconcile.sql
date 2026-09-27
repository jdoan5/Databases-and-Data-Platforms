-- @check Per source: mart_campaign_daily sessions, engaged sessions, orders and revenue equal fct_sessions,
--     and its cost equals campaign_costs on the dates the source has sessions; mart_funnel_daily sessions and
--     converted sessions equal fct_sessions, and every funnel step is no larger than the one before it.
WITH facts AS (
  SELECT source, COUNT(*) AS sessions, COUNTIF(is_engaged) AS engaged, SUM(orders) AS orders,
    ROUND(SUM(COALESCE(revenue_usd, 0)), 2) AS revenue, COUNTIF(converted) AS converted,
    MIN(session_date) AS first_date, MAX(session_date) AS last_date
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY source
),
campaign AS (
  SELECT source, SUM(sessions) AS sessions, SUM(engaged_sessions) AS engaged, SUM(orders) AS orders,
    ROUND(SUM(revenue_usd), 2) AS revenue, ROUND(SUM(COALESCE(cost_usd, 0)), 2) AS cost
  FROM `{{ project }}.{{ marts }}.mart_campaign_daily`
  GROUP BY source
),
costs AS (
  SELECT c.source, ROUND(SUM(c.cost_usd), 2) AS cost
  FROM `{{ project }}.{{ raw }}.campaign_costs` AS c
  JOIN facts AS f ON f.source = c.source AND c.cost_date BETWEEN f.first_date AND f.last_date
  GROUP BY c.source
),
funnel AS (
  SELECT source, SUM(sessions) AS sessions, SUM(converted_sessions) AS converted,
    COUNTIF(NOT (view_item_sessions <= sessions AND add_to_cart_sessions <= view_item_sessions
      AND begin_checkout_sessions <= add_to_cart_sessions AND purchase_sessions <= begin_checkout_sessions
      AND purchase_sessions <= converted_sessions)) AS non_monotonic_days
  FROM `{{ project }}.{{ marts }}.mart_funnel_daily`
  GROUP BY source
)
SELECT
  source,
  f.sessions AS fact_sessions, c.sessions AS campaign_sessions, u.sessions AS funnel_sessions,
  f.engaged AS fact_engaged, c.engaged AS campaign_engaged,
  f.orders AS fact_orders, c.orders AS campaign_orders,
  f.revenue AS fact_revenue, c.revenue AS campaign_revenue,
  k.cost AS cost_table, c.cost AS campaign_cost,
  f.converted AS fact_converted, u.converted AS funnel_converted, u.non_monotonic_days
FROM facts AS f
FULL OUTER JOIN campaign AS c USING (source)
FULL OUTER JOIN funnel AS u USING (source)
LEFT JOIN costs AS k USING (source)
WHERE COALESCE(f.sessions, -1) != COALESCE(c.sessions, -1)
   OR COALESCE(f.sessions, -1) != COALESCE(u.sessions, -1)
   OR COALESCE(f.engaged, -1) != COALESCE(c.engaged, -1)
   OR COALESCE(f.orders, -1) != COALESCE(c.orders, -1)
   OR ABS(COALESCE(f.revenue, 0) - COALESCE(c.revenue, 0)) > 0.005
   OR ABS(COALESCE(k.cost, 0) - COALESCE(c.cost, 0)) > 0.005
   OR COALESCE(f.converted, -1) != COALESCE(u.converted, -1)
   OR COALESCE(u.non_monotonic_days, 0) > 0
