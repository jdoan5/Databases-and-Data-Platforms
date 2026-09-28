-- @check Every attributed order has all six models, each touch weight is between 0 and 1, and each order's
--     weights sum to 1 under every model (to 1e-9).
--     Columns: tagline/spark/attribution/tables.py.
WITH expected_models AS (
  SELECT model
  FROM UNNEST(['last_click', 'last_non_direct', 'first_click', 'linear', 'time_decay', 'position_based']) AS model
),
per_order AS (
  SELECT source, order_id, model, SUM(weight) AS weight_sum, COUNTIF(weight < 0 OR weight > 1) AS bad_weights
  FROM `{{ project }}.{{ marts }}.fct_attribution`
  GROUP BY source, order_id, model
),
orders AS (
  SELECT DISTINCT source, order_id FROM per_order
)
SELECT 'weights do not sum to 1' AS problem, source, order_id, model, weight_sum AS value
FROM per_order
WHERE ABS(weight_sum - 1) > 1e-9
UNION ALL
SELECT 'weight outside [0, 1]', source, order_id, model, bad_weights
FROM per_order
WHERE bad_weights > 0
UNION ALL
SELECT 'model missing for order', o.source, o.order_id, m.model, NULL
FROM orders AS o
CROSS JOIN expected_models AS m
LEFT JOIN per_order AS p USING (source, order_id, model)
WHERE p.model IS NULL
UNION ALL
SELECT 'unexpected model', source, order_id, model, NULL
FROM per_order
WHERE model NOT IN (SELECT model FROM expected_models)
UNION ALL
SELECT 'fct_attribution is empty', NULL, NULL, NULL, NULL
FROM (SELECT COUNT(*) AS n FROM per_order)
WHERE n = 0
