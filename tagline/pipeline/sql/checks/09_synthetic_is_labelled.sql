-- @check Synthetic data says so: every campaign_costs and products cost row is flagged synthetic, and both
--     tables' descriptions say SYNTHETIC.
SELECT 'campaign_costs row not flagged synthetic' AS problem, COUNT(*) AS n
FROM `{{ project }}.{{ raw }}.campaign_costs`
WHERE NOT is_synthetic
HAVING COUNT(*) > 0
UNION ALL
SELECT 'products row not flagged synthetic', COUNT(*)
FROM `{{ project }}.{{ raw }}.products`
WHERE NOT cost_is_synthetic
HAVING COUNT(*) > 0
UNION ALL
SELECT CONCAT(t.table_name, ': description does not say SYNTHETIC'), 1
FROM `{{ project }}.{{ raw }}.INFORMATION_SCHEMA.TABLES` AS t
LEFT JOIN `{{ project }}.{{ raw }}.INFORMATION_SCHEMA.TABLE_OPTIONS` AS o
  ON o.table_name = t.table_name AND o.option_name = 'description'
WHERE t.table_name IN ('campaign_costs', 'products')
  AND NOT COALESCE(STRPOS(o.option_value, 'SYNTHETIC') > 0, FALSE)
