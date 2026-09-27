-- @table The ten largest session source / medium pairs per source (with or without cost).
SELECT source, session_source, session_medium, sessions, orders, revenue_usd
FROM (
  SELECT source, session_source, session_medium, COUNT(*) AS sessions, SUM(orders) AS orders,
    ROUND(SUM(COALESCE(revenue_usd, 0)), 2) AS revenue_usd,
    ROW_NUMBER() OVER (PARTITION BY source ORDER BY COUNT(*) DESC, session_source, session_medium) AS rank
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY source, session_source, session_medium
)
WHERE rank <= 10
ORDER BY source, rank
