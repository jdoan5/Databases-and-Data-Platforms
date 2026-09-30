-- @table One row per purchase event (repeats of the same order included), with the columns fct_orders needs:
--     stg_events' purchase rows, narrow. fct_orders reads this table instead of scanning all of stg_events for
--     5,705 of its 4.3 million rows, and the daily incremental build (make build-incremental) reads it to dedupe a
--     day's purchases against the earlier ones (Stage 4). Built by tagline/pipeline.
-- @column source: ga4_sample or tagline_site.
-- @column event_key: stg_events.event_key of the purchase event. (source, event_key) is the key.
-- @column event_date: Event day in the property's time zone.
-- @column event_timestamp: When GA4 received the purchase event.
-- @column user_pseudo_id: The device (NULL for a cookieless purchase).
-- @column session_key: stg_events.session_key: source:user_pseudo_id:ga_session_id, NULL when either id is missing.
-- @column user_id: The user_id the purchase event carried, if any.
-- @column transaction_id: stg_events.transaction_id (cleaned; NULL when the purchase had none).
-- @column order_id: stg_events.order_id: the order this purchase event belongs to.
-- @column is_duplicate_purchase: stg_events.is_duplicate_purchase: a repeat of an earlier purchase event of the same
--     order from the same device (fct_orders keeps only FALSE rows).
-- @column is_transaction_id_collision: stg_events.is_transaction_id_collision.
-- @column is_zero_value_without_id: stg_events.is_zero_value_without_id.
-- @column purchase_revenue_usd: stg_events.purchase_revenue_usd.
-- @column tax_usd: stg_events.tax_usd.
-- @column shipping_usd: stg_events.shipping_usd.
-- @column total_item_quantity: stg_events.total_item_quantity.
-- @column item_count: stg_events.item_count.

CREATE OR REPLACE TABLE `{{ project }}.{{ staging }}.int_purchases`
PARTITION BY event_date
AS
SELECT
  source,
  event_key,
  event_date,
  event_timestamp,
  user_pseudo_id,
  -- session_key rebuilt from ga_session_id rather than read (the stored string is 174 MiB of stg_events;
  -- Stage 4, experiment 2). Same expression as in 10_stg_events.sql.
  IF(user_pseudo_id IS NULL OR ga_session_id IS NULL, NULL, CONCAT(source, ':', user_pseudo_id, ':', CAST(ga_session_id AS STRING))) AS session_key,
  user_id,
  transaction_id,
  order_id,
  is_duplicate_purchase,
  is_transaction_id_collision,
  is_zero_value_without_id,
  purchase_revenue_usd,
  tax_usd,
  shipping_usd,
  total_item_quantity,
  item_count
FROM `{{ project }}.{{ staging }}.stg_events`
-- make build-incremental narrows this to the days it processes (tagline_pipeline/incremental.py); empty otherwise
WHERE event_name = 'purchase'{{ incremental_filter }}
