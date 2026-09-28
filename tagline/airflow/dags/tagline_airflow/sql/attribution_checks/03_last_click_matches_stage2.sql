-- @check last_click reconciles with Stage 2's session attribution, per data source and session source / medium /
--     campaign, to the cent. Stage 2 credits each order to the session it was placed in; a Stage 3 journey ends at
--     that session (tagline/spark/attribution/journeys.py), so last_click must credit it too. So: every
--     attributed order has exactly one last_click credit, of weight 1, on its own order session (fct_orders.session_key,
--     read here, not the is_order_session flag the job wrote); and Stage 2's orders and revenue by channel
--     (fct_sessions) equal mart_attribution_daily's last_click by channel, with no adjustment.
WITH orders AS (
  SELECT source, order_id, session_key
  FROM `{{ project }}.{{ marts }}.fct_orders`
  WHERE NOT is_zero_value_without_id AND session_key IS NOT NULL
),
credited AS (
  SELECT source, order_id, session_key, weight
  FROM `{{ project }}.{{ marts }}.fct_attribution`
  WHERE model = 'last_click' AND weight > 0
),
per_order AS (
  SELECT
    o.source,
    o.order_id,
    COUNT(c.order_id) AS credits,
    SUM(c.weight) AS weight,
    COUNTIF(c.session_key != o.session_key) AS elsewhere
  FROM orders AS o
  LEFT JOIN credited AS c USING (source, order_id)
  GROUP BY o.source, o.order_id
),
stage2 AS (
  SELECT
    source,
    COALESCE(session_source, '(null)') AS session_source,
    COALESCE(session_medium, '(null)') AS session_medium,
    COALESCE(session_campaign, '(null)') AS session_campaign,
    ROUND(SUM(orders), 6) AS orders,
    ROUND(SUM(COALESCE(revenue_usd, 0)), 2) AS revenue
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  WHERE orders > 0
  GROUP BY 1, 2, 3, 4
),
stage3 AS (
  SELECT
    source,
    COALESCE(session_source, '(null)') AS session_source,
    COALESCE(session_medium, '(null)') AS session_medium,
    COALESCE(session_campaign, '(null)') AS session_campaign,
    ROUND(SUM(attributed_orders), 6) AS orders,
    ROUND(SUM(attributed_revenue_usd), 2) AS revenue
  FROM `{{ project }}.{{ marts }}.mart_attribution_daily`
  WHERE model = 'last_click'
  GROUP BY 1, 2, 3, 4
)
SELECT 'order without exactly one last_click credit of weight 1' AS problem, source, order_id AS item,
  CAST(credits AS FLOAT64) AS expected, weight AS actual
FROM per_order
WHERE credits != 1 OR ABS(weight - 1) > 1e-9
UNION ALL
SELECT 'last_click on a session other than the order session', source, order_id, 0, elsewhere
FROM per_order
WHERE elsewhere > 0
UNION ALL
SELECT 'orders by channel: Stage 2 != last_click',
  source, CONCAT(session_source, ' / ', session_medium, ' / ', session_campaign), s2.orders, s3.orders
FROM stage2 AS s2
FULL OUTER JOIN stage3 AS s3 USING (source, session_source, session_medium, session_campaign)
WHERE ABS(COALESCE(s2.orders, 0) - COALESCE(s3.orders, 0)) > 1e-6
UNION ALL
SELECT 'revenue by channel: Stage 2 != last_click',
  source, CONCAT(session_source, ' / ', session_medium, ' / ', session_campaign), s2.revenue, s3.revenue
FROM stage2 AS s2
FULL OUTER JOIN stage3 AS s3 USING (source, session_source, session_medium, session_campaign)
WHERE ABS(COALESCE(s2.revenue, 0) - COALESCE(s3.revenue, 0)) > 0.01
