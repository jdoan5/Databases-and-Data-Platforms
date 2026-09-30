-- @table One row per order line: the items of each order's kept purchase event. Tagline products
--     (source tagline_site, item_id in tagline_raw.products) are enriched with the catalog's
--     unit_cost, giving line cost and gross margin; unit_cost is SYNTHETIC (a seeded, documented
--     margin per category; see tagline_raw.products). Sample lines keep their GA4 item fields and
--     have no cost. Built by tagline/pipeline.
-- @column source: ga4_sample or tagline_site.
-- @column order_id: fct_orders.order_id.
-- @column line_number: 1-based position of the item in the purchase event. (source, order_id, line_number) is the key.
-- @column order_date: The order's date.
-- @column ordered_at: The order's timestamp.
-- @column person_id: The order's person.
-- @column session_key: The order's session.
-- @column item_id: Item id.
-- @column item_name: Item name.
-- @column item_brand: Item brand.
-- @column item_variant: Item variant.
-- @column item_category: Item category.
-- @column price_usd: Unit price in USD.
-- @column quantity: Units.
-- @column line_revenue_usd: item_revenue_usd, else price_usd x quantity.
-- @column is_catalog_product: TRUE when the item is a Tagline catalog product with a unit_cost.
-- @column unit_cost_usd: SYNTHETIC unit cost from tagline_raw.products (Tagline products only).
-- @column line_cost_usd: unit_cost_usd x quantity.
-- @column gross_margin_usd: line_revenue_usd - line_cost_usd.
-- @column gross_margin_rate: gross_margin_usd / line_revenue_usd.

CREATE OR REPLACE TABLE `{{ project }}.{{ marts }}.fct_order_items`
PARTITION BY order_date
AS
WITH lines AS (
  SELECT
    o.source,
    o.order_id,
    i.item_index + 1 AS line_number,
    o.order_date,
    o.ordered_at,
    o.person_id,
    o.session_key,
    i.item_id,
    i.item_name,
    i.item_brand,
    i.item_variant,
    i.item_category,
    i.price_usd,
    i.quantity,
    COALESCE(i.item_revenue_usd, i.price_usd * i.quantity) AS line_revenue_usd,
    p.item_id IS NOT NULL AS is_catalog_product,
    p.unit_cost_usd
  FROM `{{ project }}.{{ marts }}.fct_orders` AS o
  JOIN `{{ project }}.{{ staging }}.stg_items` AS i
    ON i.source = o.source AND i.event_key = o.purchase_event_key
  LEFT JOIN `{{ project }}.{{ raw }}.products` AS p
    ON o.source = 'tagline_site' AND p.item_id = i.item_id
  -- make build-incremental narrows this to the orders that changed (tagline_pipeline/incremental.py); empty otherwise
  {{ incremental_filter }}
)

SELECT
  *,
  ROUND(unit_cost_usd * quantity, 2) AS line_cost_usd,
  ROUND(line_revenue_usd - unit_cost_usd * quantity, 2) AS gross_margin_usd,
  ROUND(SAFE_DIVIDE(line_revenue_usd - unit_cost_usd * quantity, line_revenue_usd), 4) AS gross_margin_rate
FROM lines
