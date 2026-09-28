-- @check Per data source and model: attributed orders and revenue in fct_attribution and in mart_attribution_daily
--     equal the count and revenue of Stage 2's real orders in fct_orders (not is_zero_value_without_id, placed in a
--     session), to the cent. Credit is moved between channels, never created or lost.
--     The order filter is the job's attributable orders (tagline/spark/attribution/journeys.py); columns: tables.py.
WITH orders AS (
  SELECT source, COUNT(*) AS orders, ROUND(SUM(COALESCE(revenue_usd, 0)), 2) AS revenue
  FROM `{{ project }}.{{ marts }}.fct_orders`
  WHERE NOT is_zero_value_without_id AND session_key IS NOT NULL
  GROUP BY source
),
models AS (
  SELECT model
  FROM UNNEST(['last_click', 'last_non_direct', 'first_click', 'linear', 'time_decay', 'position_based']) AS model
),
expected AS (
  SELECT o.source, m.model, o.orders, o.revenue
  FROM orders AS o CROSS JOIN models AS m
),
facts AS (
  SELECT source, model, ROUND(SUM(weight), 6) AS orders, ROUND(SUM(attributed_revenue_usd), 2) AS revenue
  FROM `{{ project }}.{{ marts }}.fct_attribution`
  GROUP BY source, model
),
mart AS (
  SELECT source, model, ROUND(SUM(attributed_orders), 6) AS orders, ROUND(SUM(attributed_revenue_usd), 2) AS revenue
  FROM `{{ project }}.{{ marts }}.mart_attribution_daily`
  GROUP BY source, model
)
SELECT
  source,
  model,
  e.orders AS expected_orders,
  f.orders AS fct_attribution_orders,
  m.orders AS mart_orders,
  e.revenue AS expected_revenue,
  f.revenue AS fct_attribution_revenue,
  m.revenue AS mart_revenue
FROM expected AS e
FULL OUTER JOIN facts AS f USING (source, model)
FULL OUTER JOIN mart AS m USING (source, model)
WHERE e.orders IS NULL OR f.orders IS NULL OR m.orders IS NULL
  OR ABS(e.orders - f.orders) > 1e-6 OR ABS(e.orders - m.orders) > 1e-6
  OR ABS(e.revenue - f.revenue) > 0.01 OR ABS(e.revenue - m.revenue) > 0.01
