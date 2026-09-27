-- @table Order lines: revenue, and for Tagline catalog products the SYNTHETIC cost and gross margin.
SELECT
  source,
  COUNT(*) AS lines,
  COUNTIF(is_catalog_product) AS catalog_lines,
  SUM(quantity) AS units,
  ROUND(SUM(line_revenue_usd), 2) AS line_revenue_usd,
  ROUND(SUM(line_cost_usd), 2) AS line_cost_usd,
  ROUND(SUM(gross_margin_usd), 2) AS gross_margin_usd,
  ROUND(SAFE_DIVIDE(SUM(gross_margin_usd), SUM(IF(is_catalog_product, line_revenue_usd, NULL))), 4) AS gross_margin_rate
FROM `{{ project }}.{{ marts }}.fct_order_items`
GROUP BY source
ORDER BY source
