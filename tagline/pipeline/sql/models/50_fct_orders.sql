-- @table One row per order: purchase events deduplicated by transaction_id per device (the first
--     purchase event per source + device + transaction_id wins; tagging plan section 12). The same
--     transaction_id from two devices is an id collision and stays two orders (order_id gets the
--     device appended). A purchase with no usable transaction_id in ecommerce.transaction_id or the
--     transaction_id param (NULL, '' or (not set)) cannot be deduplicated, so each is its own order;
--     has_transaction_id marks them, and is_zero_value_without_id marks those that also have no
--     revenue (all 450 such purchases in the sample): they stay here, flagged, but fct_sessions and
--     the marts do not count them as orders or conversions. Person: the purchase event's own
--     user_id when it carried one, else the session's person. An order in no session (a cookieless
--     purchase with no user_pseudo_id or ga_session_id) keeps its row, with no session and a
--     person from its user_id, its device, or a per-order anonymous id. Revenue is GA4's purchase
--     revenue, which is the purchase value: on the Tagline site the item subtotal, with tax and
--     shipping in their own columns. Built by tagline/pipeline.
-- @column source: ga4_sample or tagline_site.
-- @column order_id: transaction_id; 'no-transaction-id:<event_key>' when the purchase had none;
--     '<transaction_id>@<user_pseudo_id>' when the transaction_id was sent from two or more devices. (source, order_id) is the key.
-- @column transaction_id: The transaction_id sent with the purchase; NULL when it had none.
-- @column has_transaction_id: FALSE for purchases without a usable transaction_id (not deduplicated).
-- @column is_zero_value_without_id: TRUE when the purchase has no transaction_id and no revenue. Not counted as an
--     order or a conversion in fct_sessions, mart_campaign_daily or mart_funnel_daily.
-- @column is_transaction_id_collision: TRUE when this transaction_id was also sent from another device (so it is
--     a separate order, and order_id carries the device).
-- @column purchase_event_key: stg_events.event_key of the purchase event kept for this order.
-- @column order_date: Event day of that purchase event (property time zone).
-- @column ordered_at: Timestamp of that purchase event.
-- @column session_key: The session the order was placed in; NULL for a purchase in no session (cookieless).
-- @column session_date: That session's date.
-- @column user_pseudo_id: Device the order was placed on (NULL for a cookieless purchase without one).
-- @column person_id: Who placed the order: the purchase event's user_id; else the session's person
--     (fct_sessions.person_id); else, with no session, the device's person (int_identity); else
--     'cookieless:<source>:<order_id>'.
-- @column identity_rule: signed_in_purchase (the purchase carried a user_id), the session's identity_rule
--     (fct_sessions), device_without_session (no session, device known) or cookieless (no session, no device,
--     no user_id).
-- @column session_source: The session's source (the order's attribution); NULL with no session.
-- @column session_medium: The session's medium.
-- @column session_campaign: The session's campaign.
-- @column revenue_usd: Purchase revenue in USD: ecommerce.purchase_revenue_in_usd, else purchase_revenue.
-- @column tax_usd: Tax in USD.
-- @column shipping_usd: Shipping in USD (NULL throughout the sample).
-- @column item_quantity: ecommerce.total_item_quantity.
-- @column line_count: Items in the purchase event (fct_order_items rows).
-- @column duplicate_purchase_events: Further purchase events from this device with this transaction_id that were dropped.

CREATE OR REPLACE TABLE `{{ project }}.{{ marts }}.fct_orders`
PARTITION BY order_date
AS
WITH purchases AS (
  -- stg_events' purchase events, narrow (int_purchases), rather than a scan of all 4.3 million events (Stage 4)
  SELECT *
  FROM `{{ project }}.{{ staging }}.int_purchases`
),

duplicates AS (
  SELECT source, order_id, COUNTIF(is_duplicate_purchase) AS duplicate_purchase_events
  FROM purchases
  GROUP BY source, order_id
)

SELECT
  p.source,
  p.order_id,
  p.transaction_id,
  p.transaction_id IS NOT NULL AS has_transaction_id,
  p.is_zero_value_without_id,
  p.is_transaction_id_collision,
  p.event_key AS purchase_event_key,
  p.event_date AS order_date,
  p.event_timestamp AS ordered_at,
  p.session_key,
  s.session_date,
  p.user_pseudo_id,
  -- The purchase's own user_id first: if someone switches accounts mid-session, the session goes
  -- to the last account, but an order belongs to the account that placed it.
  COALESCE(p.user_id, s.person_id, i.person_id, CONCAT('cookieless:', p.source, ':', p.order_id)) AS person_id,
  CASE
    WHEN p.user_id IS NOT NULL THEN 'signed_in_purchase'
    WHEN s.session_key IS NOT NULL THEN s.identity_rule
    WHEN i.person_id IS NOT NULL THEN 'device_without_session'
    ELSE 'cookieless'
  END AS identity_rule,
  s.session_source,
  s.session_medium,
  s.session_campaign,
  p.purchase_revenue_usd AS revenue_usd,
  p.tax_usd,
  p.shipping_usd,
  p.total_item_quantity AS item_quantity,
  p.item_count AS line_count,
  d.duplicate_purchase_events
FROM purchases AS p
JOIN duplicates AS d USING (source, order_id)
LEFT JOIN `{{ project }}.{{ marts }}.fct_sessions` AS s USING (source, session_key)
LEFT JOIN `{{ project }}.{{ staging }}.int_identity` AS i
  ON i.source = p.source AND i.user_pseudo_id = p.user_pseudo_id
WHERE NOT p.is_duplicate_purchase
