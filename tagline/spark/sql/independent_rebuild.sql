-- An independent rebuild of fct_attribution in BigQuery SQL, compared row by row with what the Spark job wrote.
-- Run by `make spark-report` (tagline_spark/report.py fills in {project} and {marts}; maximum_bytes_billed applies).
-- It re-derives the rules from the docs, not from the job's code: an order's touches are its person's sessions
-- that started at most 30 days (30 x 24 h) before the purchase and no later than the order session, plus the
-- order session; ordered by start, a tie putting the order session last, then session_key. One row comes back:
-- all counts 0 and the max differences near 0 mean the two agree on every order x model x touch.
WITH o AS (
  SELECT o.source, o.order_id, o.ordered_at, o.person_id, o.session_key AS osk,
    COALESCE(o.revenue_usd, 0) AS rev, os.session_start_at AS os_start
  FROM `{project}.{marts}.fct_orders` AS o
  JOIN `{project}.{marts}.fct_sessions` AS os ON os.source = o.source AND os.session_key = o.session_key
  WHERE NOT o.is_zero_value_without_id AND o.session_key IS NOT NULL
),
s AS (
  SELECT source, session_key, person_id, session_start_at, session_source, session_medium
  FROM `{project}.{marts}.fct_sessions`
),
t AS (
  SELECT o.source, o.order_id, o.osk, o.rev, s.session_key,
    (UNIX_MICROS(o.ordered_at) - UNIX_MICROS(s.session_start_at)) / 86400e6 AS d,
    COALESCE(s.session_source = '(direct)' AND s.session_medium IN ('(none)', '(not set)'), FALSE) AS dir,
    ROW_NUMBER() OVER (PARTITION BY o.source, o.order_id ORDER BY s.session_start_at, s.session_key = o.osk, s.session_key) AS p,
    COUNT(*) OVER (PARTITION BY o.source, o.order_id) AS n
  FROM o
  JOIN s ON s.source = o.source AND (
    (s.person_id = o.person_id AND s.session_start_at <= o.os_start
      AND s.session_start_at >= TIMESTAMP_SUB(o.ordered_at, INTERVAL 30 DAY))
    OR s.session_key = o.osk)
),
w AS (
  SELECT *,
    COALESCE(MAX(IF(NOT dir, p, NULL)) OVER (PARTITION BY source, order_id), n) AS lnd,
    POW(0.5, d / 7) / SUM(POW(0.5, d / 7)) OVER (PARTITION BY source, order_id) AS td
  FROM t
),
rebuilt AS (
  SELECT source, order_id, session_key, p, n, rev, m.model, m.weight
  FROM w, UNNEST([
    STRUCT('last_click' AS model, IF(p = n, 1.0, 0.0) AS weight),
    ('last_non_direct', IF(p = lnd, 1.0, 0.0)),
    ('first_click', IF(p = 1, 1.0, 0.0)),
    ('linear', 1.0 / n),
    ('time_decay', td),
    ('position_based', CASE WHEN n = 1 THEN 1.0 WHEN n = 2 THEN 0.5 WHEN p IN (1, n) THEN 0.4 ELSE 0.2 / (n - 2) END)
  ]) AS m
),
spark AS (
  SELECT source, order_id, session_key, model, touch_position, touch_count, weight, attributed_revenue_usd
  FROM `{project}.{marts}.fct_attribution`
)
SELECT
  COUNT(*) AS rows_compared,
  COUNTIF(r.order_id IS NULL) AS only_in_spark,
  COUNTIF(f.order_id IS NULL) AS only_in_rebuild,
  COUNTIF(r.p != f.touch_position OR r.n != f.touch_count) AS position_differs,
  MAX(ABS(r.weight - f.weight)) AS max_weight_diff,
  MAX(ABS(r.weight * r.rev - f.attributed_revenue_usd)) AS max_revenue_diff
FROM rebuilt AS r
FULL JOIN spark AS f USING (source, order_id, session_key, model)
