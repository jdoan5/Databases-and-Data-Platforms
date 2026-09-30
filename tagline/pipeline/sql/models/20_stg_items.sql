-- @table One row per item per ecommerce event (every event with a non-empty items array),
--     from stg_events.items. Item text fields are trimmed, and '' or (not set) become NULL
--     (the sample writes (not set) for missing list, promotion, coupon and affiliation values,
--     and pads variants with a leading space). Money in USD. Built by tagline/pipeline.
-- @column source: ga4_sample or tagline_site.
-- @column event_key: The event this item belongs to (stg_events.event_key).
-- @column item_index: 0-based position in the event's items array. (source, event_key, item_index) is the key.
-- @column event_date: Event day in the property's time zone.
-- @column event_timestamp: When GA4 received the event.
-- @column event_name: view_item, add_to_cart, purchase, ...
-- @column session_key: The event's session (stg_events.session_key).
-- @column user_pseudo_id: The event's device.
-- @column item_id: Item id (a catalog SKU such as TL-DRK-001 on the site; a numeric id in the sample).
-- @column item_name: Item name.
-- @column item_brand: Item brand.
-- @column item_variant: Item variant.
-- @column item_category: Top-level item category.
-- @column item_category2: Second-level category.
-- @column item_category3: Third-level category.
-- @column item_category4: Fourth-level category.
-- @column item_category5: Fifth-level category.
-- @column price_usd: Unit price: price_in_usd, else price (the sample leaves price_in_usd NULL on some events;
--     both sources are USD-only).
-- @column quantity: Units (NULL when the event did not say; the sample omits it on most view_item items).
-- @column item_revenue_usd: item_revenue_in_usd, else item_revenue: price x quantity, purchase events only.
-- @column coupon: Item coupon.
-- @column affiliation: Item affiliation.
-- @column item_list_id: List the item was shown in.
-- @column item_list_name: Name of that list.
-- @column item_list_index: Position in that list, as an integer.
-- @column promotion_id: Promotion id.
-- @column promotion_name: Promotion name.
-- @column creative_name: Promotion creative name.
-- @column creative_slot: Promotion creative slot.

-- Not clustered (Stage 4, experiment 4): nothing reads stg_items by event_name or item_id, and without the
-- clustering its build took about half the slot-ms (50-53k against 94-168k over three builds each), same bytes.
CREATE OR REPLACE TABLE `{{ project }}.{{ staging }}.stg_items`
PARTITION BY event_date
AS
SELECT
  e.source,
  e.event_key,
  i.item_index,
  e.event_date,
  e.event_timestamp,
  e.event_name,
  -- stg_events.session_key, rebuilt from the 8-byte ga_session_id rather than read: the stored string is
  -- 174 MiB of stg_events (Stage 4, experiment 2). Same expression as in 10_stg_events.sql.
  IF(e.user_pseudo_id IS NULL OR e.ga_session_id IS NULL, NULL, CONCAT(e.source, ':', e.user_pseudo_id, ':', CAST(e.ga_session_id AS STRING))) AS session_key,
  e.user_pseudo_id,
  NULLIF(NULLIF(TRIM(i.item_id), ''), '(not set)') AS item_id,
  NULLIF(NULLIF(TRIM(i.item_name), ''), '(not set)') AS item_name,
  NULLIF(NULLIF(TRIM(i.item_brand), ''), '(not set)') AS item_brand,
  NULLIF(NULLIF(TRIM(i.item_variant), ''), '(not set)') AS item_variant,
  NULLIF(NULLIF(TRIM(i.item_category), ''), '(not set)') AS item_category,
  NULLIF(NULLIF(TRIM(i.item_category2), ''), '(not set)') AS item_category2,
  NULLIF(NULLIF(TRIM(i.item_category3), ''), '(not set)') AS item_category3,
  NULLIF(NULLIF(TRIM(i.item_category4), ''), '(not set)') AS item_category4,
  NULLIF(NULLIF(TRIM(i.item_category5), ''), '(not set)') AS item_category5,
  COALESCE(i.price_in_usd, i.price) AS price_usd,
  i.quantity,
  COALESCE(i.item_revenue_in_usd, i.item_revenue) AS item_revenue_usd,
  NULLIF(NULLIF(TRIM(i.coupon), ''), '(not set)') AS coupon,
  NULLIF(NULLIF(TRIM(i.affiliation), ''), '(not set)') AS affiliation,
  NULLIF(NULLIF(TRIM(i.item_list_id), ''), '(not set)') AS item_list_id,
  NULLIF(NULLIF(TRIM(i.item_list_name), ''), '(not set)') AS item_list_name,
  SAFE_CAST(i.item_list_index AS INT64) AS item_list_index,
  NULLIF(NULLIF(TRIM(i.promotion_id), ''), '(not set)') AS promotion_id,
  NULLIF(NULLIF(TRIM(i.promotion_name), ''), '(not set)') AS promotion_name,
  NULLIF(NULLIF(TRIM(i.creative_name), ''), '(not set)') AS creative_name,
  NULLIF(NULLIF(TRIM(i.creative_slot), ''), '(not set)') AS creative_slot
FROM `{{ project }}.{{ staging }}.stg_events` AS e
CROSS JOIN UNNEST(e.items) AS i
-- make build-incremental narrows this to the days it processes (tagline_pipeline/incremental.py); empty otherwise
{{ incremental_filter }}
