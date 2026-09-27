-- @table Sessions by how their person was resolved (fct_sessions.identity_rule).
SELECT source, identity_rule, COUNT(*) AS sessions, SUM(orders) AS orders, ROUND(SUM(COALESCE(revenue_usd, 0)), 2) AS revenue_usd
FROM `{{ project }}.{{ marts }}.fct_sessions`
GROUP BY source, identity_rule
ORDER BY source, identity_rule
