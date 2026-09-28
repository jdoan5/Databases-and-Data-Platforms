-- @table One row per GA4 event. Sources unioned: the public GA4 sample (ga4_sample; Google
--     Merchandise Store, obfuscated, 2020-11-01 to 2021-01-31, no user_id) and, when
--     TAGLINE_GA4_DATASET is set, the Tagline site's own export (tagline_site; daily tables,
--     plus streaming tables for days with no daily table yet). Exact duplicate export rows are
--     collapsed (export_row_count says how many there were). Event parameters used downstream
--     are flattened; <Other>, (data deleted) and (not set) are kept as GA4 wrote them, except
--     transaction_id, where (not set) and '' become NULL. Purchases are deduplicated per device
--     by transaction_id (is_duplicate_purchase). Built by tagline/pipeline.
-- @column source: Which export the row came from: ga4_sample or tagline_site.
-- @column event_key: FARM_FINGERPRINT of the whole export row as JSON. Identical export rows get
--     the same key, which is how exact duplicates are found. Unique per source.
-- @column export_row_count: How many identical export rows this event stood for. 1 normally;
--     more than 1 means exact duplicates were collapsed into this row.
-- @column export_table: sample, daily (events_YYYYMMDD) or intraday (events_intraday_YYYYMMDD, used
--     only for days with no daily table).
-- @column event_date: event_date from the export: the day in the GA4 property's time zone.
-- @column event_timestamp: When GA4 received the event (event_timestamp, microseconds UTC).
-- @column event_name: GA4 event name (page_view, view_item, purchase, ...).
-- @column user_pseudo_id: GA4's pseudonymous device/browser id (the client id): one per browser. NULL on
--     consent-denied cookieless pings (in the site's export of 2026-09-27, all 415 analytics_storage = No rows have
--     neither user_pseudo_id nor ga_session_id); such events have no session_key.
-- @column user_id: The signed-in account id sent by the site (opaque 32-hex id), NULL when anonymous.
--     Always NULL in ga4_sample.
-- @column ga_session_id: GA4 session id (event param ga_session_id). Unique only together with
--     user_pseudo_id.
-- @column ga_session_number: Which session of this device this is (event param ga_session_number).
-- @column session_key: source:user_pseudo_id:ga_session_id, the session this event belongs to.
--     NULL when either id is missing (a cookieless ping, say): the event is in no session.
-- @column page_location: Event param page_location (full URL).
-- @column page_path: Path of page_location, without query string.
-- @column page_title: Event param page_title.
-- @column page_referrer: Event param page_referrer.
-- @column search_term: Event param search_term (search / view_search_results). <obfuscated> in the sample.
-- @column engagement_time_msec: Event param engagement_time_msec.
-- @column session_engaged: TRUE when the event carries session_engaged = 1 (GA4's engaged-session flag;
--     the sample stores it as a string on most events and an integer on some).
-- @column collected_source: Traffic source collected with this event: collected_traffic_source.manual_source
--     when the export has it, else event param source. Event scope, no attribution applied.
-- @column collected_medium: As collected_source, for the medium (manual_medium / event param medium).
-- @column collected_campaign: As collected_source, for the campaign (manual_campaign_name / event param campaign).
-- @column collected_term: As collected_source, for the term (manual_term / event param term).
-- @column session_last_click_source: GA4's session attribution from session_traffic_source_last_click: the
--     cross_channel_campaign record when it has values, else manual_campaign, else google for a Google Ads
--     click. NULL for ga4_sample, whose export predates the record.
-- @column session_last_click_medium: As session_last_click_source, for the medium (cpc for a Google Ads click).
-- @column session_last_click_campaign: As session_last_click_source, for the campaign name.
-- @column first_user_source: traffic_source.source: the source that first acquired this device (user scope; Google
--     reads it from the first_visit event). NULL for new users in streaming (intraday) tables.
-- @column first_user_medium: traffic_source.medium (user scope, first touch).
-- @column first_user_campaign: traffic_source.name (user scope, first touch).
-- @column analytics_storage: privacy_info.analytics_storage as text (Yes / No / Unset in current exports;
--     NULL throughout the sample).
-- @column ads_storage: privacy_info.ads_storage as text.
-- @column uses_transient_token: privacy_info.uses_transient_token.
-- @column device_category: device.category (desktop, mobile, tablet).
-- @column operating_system: device.operating_system.
-- @column browser: device.web_info.browser.
-- @column geo_country: geo.country.
-- @column geo_region: geo.region.
-- @column geo_city: geo.city.
-- @column platform: WEB, IOS or ANDROID.
-- @column stream_id: GA4 data stream id, as text (an integer in the sample's schema, a string in current exports).
-- @column currency: Event param currency.
-- @column event_value: Event param value as a number (the sample stores it as a double or an integer).
-- @column event_value_in_usd: event_value_in_usd from the export.
-- @column transaction_id: ecommerce.transaction_id, else event param transaction_id; each is cleaned first
--     ('' and (not set) become NULL), so a (not set) in ecommerce falls back to the param. NULL when neither has one.
-- @column order_id: purchase events only: transaction_id; 'no-transaction-id:' || event_key when the purchase has
--     none (its own order: it cannot be deduplicated); transaction_id || '@' || user_pseudo_id when the same
--     transaction_id was sent from two or more devices (an id collision: separate orders).
-- @column is_duplicate_purchase: purchase events only: TRUE when an earlier purchase event (by event_timestamp,
--     then event_key) from the same device, in the same source, had this transaction_id. fct_orders keeps only
--     FALSE rows.
-- @column is_transaction_id_collision: purchase events only: TRUE when this transaction_id was also sent from
--     another device. Treated as different orders, not duplicates (GA4 dedupes per user).
-- @column is_zero_value_without_id: purchase events only: TRUE when the purchase has no transaction_id and no
--     revenue (NULL or 0). Kept in fct_orders, flagged, and not counted as an order or conversion in
--     fct_sessions and the marts.
-- @column purchase_revenue_usd: ecommerce.purchase_revenue_in_usd (else purchase_revenue). GA4 fills it from the
--     purchase's value, which on the Tagline site is the item subtotal (no tax, no shipping).
-- @column tax_usd: ecommerce.tax_value_in_usd (else tax_value).
-- @column shipping_usd: ecommerce.shipping_value_in_usd (else shipping_value). NULL throughout the sample.
-- @column total_item_quantity: ecommerce.total_item_quantity.
-- @column unique_items: ecommerce.unique_items.
-- @column item_count: Number of entries in items.
-- @column items: The event's items array as exported (a subset of fields). stg_items is the cleaned,
--     one-row-per-item version.
-- @column items.item_index: 0-based position in the event's items array.
-- @column items.item_id: items.item_id.
-- @column items.item_name: items.item_name.
-- @column items.item_brand: items.item_brand.
-- @column items.item_variant: items.item_variant.
-- @column items.item_category: items.item_category.
-- @column items.item_category2: items.item_category2.
-- @column items.item_category3: items.item_category3.
-- @column items.item_category4: items.item_category4.
-- @column items.item_category5: items.item_category5.
-- @column items.price_in_usd: items.price_in_usd.
-- @column items.price: items.price (local currency).
-- @column items.quantity: items.quantity.
-- @column items.item_revenue_in_usd: items.item_revenue_in_usd (purchase events only).
-- @column items.item_revenue: items.item_revenue (purchase events only, local currency).
-- @column items.coupon: items.coupon.
-- @column items.affiliation: items.affiliation.
-- @column items.item_list_id: items.item_list_id.
-- @column items.item_list_name: items.item_list_name.
-- @column items.item_list_index: items.item_list_index (a string in the export).
-- @column items.promotion_id: items.promotion_id.
-- @column items.promotion_name: items.promotion_name.
-- @column items.creative_name: items.creative_name.
-- @column items.creative_slot: items.creative_slot.

CREATE OR REPLACE TABLE `{{ project }}.{{ staging }}.stg_events`
PARTITION BY event_date
CLUSTER BY source, event_name
AS
WITH raw_events AS (
  -- Source 1: the public sample. It predates collected_traffic_source and
  -- session_traffic_source_last_click, so those columns are NULL here; campaign data is in
  -- the event params source / medium / campaign / term instead.
  SELECT
    'ga4_sample' AS source,
    'sample' AS export_table,
    FARM_FINGERPRINT(TO_JSON_STRING(t)) AS row_fingerprint,
    t.event_date,
    t.event_timestamp,
    t.event_name,
    ARRAY(
      SELECT AS STRUCT p.key, p.value.string_value, p.value.int_value, p.value.double_value, p.value.float_value
      FROM UNNEST(t.event_params) AS p
    ) AS event_params,
    t.user_id,
    t.user_pseudo_id,
    CAST(t.privacy_info.analytics_storage AS STRING) AS analytics_storage,
    CAST(t.privacy_info.ads_storage AS STRING) AS ads_storage,
    CAST(t.privacy_info.uses_transient_token AS STRING) AS uses_transient_token,
    t.device.category AS device_category,
    t.device.operating_system,
    t.device.web_info.browser,
    t.geo.country AS geo_country,
    t.geo.region AS geo_region,
    t.geo.city AS geo_city,
    t.platform,
    CAST(t.stream_id AS STRING) AS stream_id,
    t.traffic_source.source AS first_user_source,
    t.traffic_source.medium AS first_user_medium,
    t.traffic_source.name AS first_user_campaign,
    CAST(NULL AS STRING) AS ctc_source,
    CAST(NULL AS STRING) AS ctc_medium,
    CAST(NULL AS STRING) AS ctc_campaign,
    CAST(NULL AS STRING) AS ctc_term,
    CAST(NULL AS STRING) AS last_click_source,
    CAST(NULL AS STRING) AS last_click_medium,
    CAST(NULL AS STRING) AS last_click_campaign,
    t.event_value_in_usd,
    t.ecommerce.transaction_id,
    t.ecommerce.purchase_revenue_in_usd,
    t.ecommerce.purchase_revenue,
    t.ecommerce.tax_value_in_usd,
    t.ecommerce.tax_value,
    t.ecommerce.shipping_value_in_usd,
    t.ecommerce.shipping_value,
    t.ecommerce.total_item_quantity,
    t.ecommerce.unique_items,
    ARRAY(
      SELECT AS STRUCT
        pos AS item_index, i.item_id, i.item_name, i.item_brand, i.item_variant,
        i.item_category, i.item_category2, i.item_category3, i.item_category4, i.item_category5,
        i.price_in_usd, i.price, i.quantity, i.item_revenue_in_usd, i.item_revenue,
        i.coupon, i.affiliation, i.item_list_id, i.item_list_name, CAST(i.item_list_index AS STRING) AS item_list_index,
        i.promotion_id, i.promotion_name, i.creative_name, i.creative_slot
      FROM UNNEST(t.items) AS i WITH OFFSET AS pos
      ORDER BY pos
    ) AS items
  FROM `{{ sample_table }}` AS t
  WHERE _TABLE_SUFFIX BETWEEN '{{ sample_start }}' AND '{{ sample_end }}'
  {{ site_union }}
),

with_params AS (
  -- One pass over each event's params. A key can hold a string on one event and an integer
  -- on another (session_engaged does in the sample), so the mixed ones are coalesced.
  SELECT
    * EXCEPT (event_params),
    (
      SELECT AS STRUCT
        MAX(IF(key = 'ga_session_id', COALESCE(int_value, SAFE_CAST(string_value AS INT64)), NULL)) AS ga_session_id,
        MAX(IF(key = 'ga_session_number', COALESCE(int_value, SAFE_CAST(string_value AS INT64)), NULL)) AS ga_session_number,
        MAX(IF(key = 'page_location', string_value, NULL)) AS page_location,
        MAX(IF(key = 'page_title', string_value, NULL)) AS page_title,
        MAX(IF(key = 'page_referrer', string_value, NULL)) AS page_referrer,
        MAX(IF(key = 'search_term', string_value, NULL)) AS search_term,
        MAX(IF(key = 'engagement_time_msec', COALESCE(int_value, SAFE_CAST(string_value AS INT64)), NULL)) AS engagement_time_msec,
        MAX(IF(key = 'session_engaged', COALESCE(string_value, CAST(int_value AS STRING)), NULL)) AS session_engaged,
        MAX(IF(key = 'source', string_value, NULL)) AS source,
        MAX(IF(key = 'medium', string_value, NULL)) AS medium,
        MAX(IF(key = 'campaign', string_value, NULL)) AS campaign,
        MAX(IF(key = 'term', string_value, NULL)) AS term,
        MAX(IF(key = 'currency', string_value, NULL)) AS currency,
        MAX(IF(key = 'value', COALESCE(double_value, float_value, CAST(int_value AS FLOAT64), SAFE_CAST(string_value AS FLOAT64)), NULL)) AS value,
        MAX(IF(key = 'transaction_id', COALESCE(string_value, CAST(int_value AS STRING)), NULL)) AS transaction_id
      FROM UNNEST(event_params)
    ) AS p
  FROM raw_events
),

flattened AS (
  SELECT
    source,
    row_fingerprint AS event_key,
    COUNT(*) OVER (PARTITION BY source, row_fingerprint) AS export_row_count,
    export_table,
    PARSE_DATE('%Y%m%d', event_date) AS event_date,
    TIMESTAMP_MICROS(event_timestamp) AS event_timestamp,
    event_name,
    user_pseudo_id,
    NULLIF(TRIM(user_id), '') AS user_id,
    p.ga_session_id,
    p.ga_session_number,
    IF(
      user_pseudo_id IS NULL OR p.ga_session_id IS NULL,
      NULL,
      CONCAT(source, ':', user_pseudo_id, ':', CAST(p.ga_session_id AS STRING))
    ) AS session_key,
    p.page_location,
    REGEXP_EXTRACT(p.page_location, r'^[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]*(/[^?#]*)') AS page_path,
    p.page_title,
    p.page_referrer,
    p.search_term,
    p.engagement_time_msec,
    p.session_engaged IN ('1', 'true') AS session_engaged,
    COALESCE(ctc_source, p.source) AS collected_source,
    COALESCE(ctc_medium, p.medium) AS collected_medium,
    COALESCE(ctc_campaign, p.campaign) AS collected_campaign,
    COALESCE(ctc_term, p.term) AS collected_term,
    last_click_source AS session_last_click_source,
    last_click_medium AS session_last_click_medium,
    last_click_campaign AS session_last_click_campaign,
    first_user_source,
    first_user_medium,
    first_user_campaign,
    analytics_storage,
    ads_storage,
    uses_transient_token,
    device_category,
    operating_system,
    browser,
    geo_country,
    geo_region,
    geo_city,
    platform,
    stream_id,
    p.currency,
    p.value AS event_value,
    event_value_in_usd,
    -- Clean each field before falling back: the sample writes (not set) in ecommerce.transaction_id
    -- on 883 purchases, and 433 of them carry the real id in the transaction_id param.
    COALESCE(
      NULLIF(NULLIF(TRIM(transaction_id), ''), '(not set)'),
      NULLIF(NULLIF(TRIM(p.transaction_id), ''), '(not set)')
    ) AS transaction_id,
    COALESCE(purchase_revenue_in_usd, purchase_revenue) AS purchase_revenue_usd,
    COALESCE(tax_value_in_usd, tax_value) AS tax_usd,
    COALESCE(shipping_value_in_usd, shipping_value) AS shipping_usd,
    total_item_quantity,
    unique_items,
    ARRAY_LENGTH(items) AS item_count,
    items
  FROM with_params
),

deduped AS (
  -- Exact duplicate export rows share an event_key: keep one.
  SELECT
    *,
    IF(
      event_name = 'purchase',
      COALESCE(transaction_id, CONCAT('no-transaction-id:', CAST(event_key AS STRING))),
      NULL
    ) AS order_id
  FROM flattened
  WHERE TRUE  -- BigQuery wants a WHERE, GROUP BY or HAVING next to QUALIFY
  QUALIFY ROW_NUMBER() OVER (PARTITION BY source, event_key) = 1
),

purchase_order AS (
  -- The same transaction_id fired twice from one device (reload, double tag) is one order: the
  -- first purchase event wins. Tagging plan section 12: Stage 2 dedupes again in SQL. The same
  -- id from another device is a collision, not a duplicate (the sample reuses 15 ids across
  -- devices, weeks apart, with other items and revenue): it stays a separate order.
  -- COALESCE: a device-less (cookieless) purchase is its own device group.
  SELECT
    source,
    event_key,
    ROW_NUMBER() OVER (
      PARTITION BY source, order_id, COALESCE(user_pseudo_id, '')
      ORDER BY event_timestamp, event_key
    ) > 1 AS is_duplicate_purchase,
    MIN(COALESCE(user_pseudo_id, '')) OVER (PARTITION BY source, order_id)
      != MAX(COALESCE(user_pseudo_id, '')) OVER (PARTITION BY source, order_id) AS is_transaction_id_collision
  FROM deduped
  WHERE event_name = 'purchase'
)

SELECT
  d.source, d.event_key, d.export_row_count, d.export_table, d.event_date, d.event_timestamp, d.event_name,
  d.user_pseudo_id, d.user_id, d.ga_session_id, d.ga_session_number, d.session_key,
  d.page_location, d.page_path, d.page_title, d.page_referrer, d.search_term,
  d.engagement_time_msec, d.session_engaged,
  d.collected_source, d.collected_medium, d.collected_campaign, d.collected_term,
  d.session_last_click_source, d.session_last_click_medium, d.session_last_click_campaign,
  d.first_user_source, d.first_user_medium, d.first_user_campaign,
  d.analytics_storage, d.ads_storage, d.uses_transient_token,
  d.device_category, d.operating_system, d.browser, d.geo_country, d.geo_region, d.geo_city,
  d.platform, d.stream_id,
  d.currency, d.event_value, d.event_value_in_usd,
  d.transaction_id,
  IF(po.is_transaction_id_collision, CONCAT(d.order_id, '@', COALESCE(d.user_pseudo_id, '(no device)')), d.order_id) AS order_id,
  po.is_duplicate_purchase,
  po.is_transaction_id_collision,
  IF(d.event_name = 'purchase', d.transaction_id IS NULL AND COALESCE(d.purchase_revenue_usd, 0) = 0, NULL) AS is_zero_value_without_id,
  d.purchase_revenue_usd, d.tax_usd, d.shipping_usd, d.total_item_quantity, d.unique_items,
  d.item_count, d.items
FROM deduped AS d
LEFT JOIN purchase_order AS po USING (source, event_key)
