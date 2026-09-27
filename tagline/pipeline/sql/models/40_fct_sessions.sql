-- @table One row per session: source + user_pseudo_id + ga_session_id (GA4's own session key;
--     a session that crosses midnight is still one row, dated by its first event). Events
--     without a session id (cookieless pings) are not in any session. Person: the last user_id
--     seen in the session if it carried one, else the device's person from int_identity. Session
--     source / medium / campaign: GA4's session_traffic_source_last_click when the export has it
--     (site data), else the traffic source collected at the session's landing (sample), else, for
--     a device's first session, traffic_source (first-user source), else (not set): unknown, kept
--     apart from an explicit (direct); traffic_source_basis says which. Orders and revenue count
--     purchases after the transaction_id dedupe; a purchase with no transaction_id and no revenue
--     is counted in zero_value_orders, not in orders. Built by tagline/pipeline.
-- @column source: ga4_sample or tagline_site.
-- @column session_key: source:user_pseudo_id:ga_session_id. The key.
-- @column user_pseudo_id: The session's device.
-- @column ga_session_id: GA4 session id.
-- @column ga_session_number: The device's session count at this session.
-- @column session_date: event_date of the session's first event (property time zone).
-- @column session_start_at: First event timestamp.
-- @column session_end_at: Last event timestamp.
-- @column duration_seconds: session_end_at - session_start_at, in seconds.
-- @column person_id: Who the session belongs to (see the table description and int_identity).
-- @column user_id: The person's user_id when the person is known by one, else NULL.
-- @column identity_rule: signed_in_session (the session carried a user_id), device_user_id (anonymous session
--     on a device that signed in at some point: stitched), anonymous_device (the device never signed in)
--     or shared_device_anonymous (anonymous session on a device used by two or more user_ids: not guessed).
-- @column session_user_id_count: Distinct user_ids seen in the session (more than 1 means someone switched
--     accounts mid-session; the last one gets the session).
-- @column session_source: Session source. See traffic_source_basis.
-- @column session_medium: Session medium.
-- @column session_campaign: Session campaign.
-- @column traffic_source_basis: Which export fields gave the session source: session_traffic_source_last_click
--     (GA4's session attribution; current exports), collected_at_landing (collected_traffic_source or the source /
--     medium / campaign event params on the session's first page_view, else on an event before it, else, with no
--     page_view, on its first event that has them; the sample), first_user_traffic_source (nothing collected at
--     landing, first session of the device: traffic_source.*), or no_source_collected (nothing: (not set)).
-- @column landing_page: page_location of the session's first page_view (else of its first event with a page).
-- @column landing_page_path: Path of landing_page.
-- @column events: Events in the session.
-- @column page_views: page_view events in the session.
-- @column engagement_time_msec: Sum of engagement_time_msec.
-- @column is_engaged: GA4 engaged session: any event with session_engaged = 1.
-- @column device_category: device.category at the session's first event.
-- @column operating_system: device.operating_system at the first event.
-- @column browser: Browser at the first event.
-- @column geo_country: Country at the first event.
-- @column analytics_storage: privacy_info.analytics_storage on the session's last event (consent state).
-- @column has_view_item: The session has a view_item event.
-- @column has_add_to_cart: The session has an add_to_cart event.
-- @column has_begin_checkout: The session has a begin_checkout event.
-- @column purchase_events: purchase events in the session, duplicates included.
-- @column orders: Orders in the session after the transaction_id dedupe (fct_orders rows), not counting
--     zero_value_orders.
-- @column zero_value_orders: fct_orders rows in the session with no transaction_id and no revenue
--     (fct_orders.is_zero_value_without_id): not counted as orders or conversions.
-- @column converted: The session has at least one order (zero_value_orders do not count).
-- @column revenue_usd: Sum of the session's orders' revenue (purchase value: items subtotal, no tax or shipping).

CREATE OR REPLACE TABLE `{{ project }}.{{ marts }}.fct_sessions`
PARTITION BY session_date
CLUSTER BY source
AS
WITH events AS (
  SELECT
    *,
    MIN(IF(event_name = 'page_view', event_timestamp, NULL)) OVER (PARTITION BY session_key) AS landing_at
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE session_key IS NOT NULL
),

sessions AS (
  SELECT
    source,
    session_key,
    user_pseudo_id,
    ga_session_id,
    MAX(ga_session_number) AS ga_session_number,
    ARRAY_AGG(event_date ORDER BY event_timestamp, event_key LIMIT 1)[OFFSET(0)] AS session_date,
    MIN(event_timestamp) AS session_start_at,
    MAX(event_timestamp) AS session_end_at,
    ARRAY_AGG(user_id IGNORE NULLS ORDER BY event_timestamp DESC, event_key DESC LIMIT 1)[SAFE_OFFSET(0)] AS session_user_id,
    COUNT(DISTINCT user_id) AS session_user_id_count,
    ARRAY_AGG(
      IF(
        COALESCE(session_last_click_source, session_last_click_medium, session_last_click_campaign) IS NOT NULL,
        STRUCT(session_last_click_source AS src, session_last_click_medium AS med, session_last_click_campaign AS camp),
        NULL
      ) IGNORE NULLS ORDER BY event_timestamp, event_key LIMIT 1
    )[SAFE_OFFSET(0)] AS last_click,
    -- The source collected at landing: on the first page_view, else on an event before it. A
    -- source first seen later in the session is not the session's (GA4 does not re-attribute a
    -- session mid-way; in the sample most such later sources are the store's own checkout host).
    ARRAY_AGG(
      IF(
        COALESCE(collected_source, collected_medium, collected_campaign) IS NOT NULL
          AND (landing_at IS NULL OR event_timestamp <= landing_at),
        STRUCT(collected_source AS src, collected_medium AS med, collected_campaign AS camp),
        NULL
      ) IGNORE NULLS
      ORDER BY event_name = 'page_view' AND event_timestamp = landing_at DESC, event_timestamp, event_key LIMIT 1
    )[SAFE_OFFSET(0)] AS collected,
    ARRAY_AGG(
      IF(
        COALESCE(first_user_source, first_user_medium, first_user_campaign) IS NOT NULL,
        STRUCT(first_user_source AS src, first_user_medium AS med, first_user_campaign AS camp),
        NULL
      ) IGNORE NULLS ORDER BY event_timestamp, event_key LIMIT 1
    )[SAFE_OFFSET(0)] AS first_user,
    ARRAY_AGG(IF(event_name = 'page_view', page_location, NULL) IGNORE NULLS ORDER BY event_timestamp, event_key LIMIT 1)[SAFE_OFFSET(0)] AS first_page_view,
    ARRAY_AGG(page_location IGNORE NULLS ORDER BY event_timestamp, event_key LIMIT 1)[SAFE_OFFSET(0)] AS first_page,
    ARRAY_AGG(
      STRUCT(device_category, operating_system, browser, geo_country)
      ORDER BY event_timestamp, event_key LIMIT 1
    )[OFFSET(0)] AS first_event,
    ARRAY_AGG(analytics_storage IGNORE NULLS ORDER BY event_timestamp DESC, event_key DESC LIMIT 1)[SAFE_OFFSET(0)] AS analytics_storage,
    COUNT(*) AS events,
    COUNTIF(event_name = 'page_view') AS page_views,
    SUM(engagement_time_msec) AS engagement_time_msec,
    LOGICAL_OR(COALESCE(session_engaged, FALSE)) AS is_engaged,
    LOGICAL_OR(event_name = 'view_item') AS has_view_item,
    LOGICAL_OR(event_name = 'add_to_cart') AS has_add_to_cart,
    LOGICAL_OR(event_name = 'begin_checkout') AS has_begin_checkout,
    COUNTIF(event_name = 'purchase') AS purchase_events,
    COUNTIF(event_name = 'purchase' AND NOT is_duplicate_purchase AND NOT is_zero_value_without_id) AS orders,
    COUNTIF(event_name = 'purchase' AND NOT is_duplicate_purchase AND is_zero_value_without_id) AS zero_value_orders,
    SUM(IF(event_name = 'purchase' AND NOT is_duplicate_purchase, purchase_revenue_usd, NULL)) AS revenue_usd
  FROM events
  GROUP BY source, session_key, user_pseudo_id, ga_session_id
),

attributed AS (
  SELECT
    *,
    CASE
      WHEN last_click IS NOT NULL THEN 'session_traffic_source_last_click'
      WHEN collected IS NOT NULL THEN 'collected_at_landing'
      WHEN ga_session_number = 1 AND first_user IS NOT NULL THEN 'first_user_traffic_source'
      ELSE 'no_source_collected'
    END AS traffic_source_basis
  FROM sessions
)

SELECT
  s.source,
  s.session_key,
  s.user_pseudo_id,
  s.ga_session_id,
  s.ga_session_number,
  s.session_date,
  s.session_start_at,
  s.session_end_at,
  TIMESTAMP_DIFF(s.session_end_at, s.session_start_at, MICROSECOND) / 1e6 AS duration_seconds,
  COALESCE(s.session_user_id, i.person_id) AS person_id,
  COALESCE(s.session_user_id, i.user_id) AS user_id,
  CASE
    WHEN s.session_user_id IS NOT NULL THEN 'signed_in_session'
    WHEN i.identity_rule = 'user_id' THEN 'device_user_id'
    WHEN i.identity_rule = 'shared_device' THEN 'shared_device_anonymous'
    ELSE 'anonymous_device'
  END AS identity_rule,
  s.session_user_id_count,
  -- The chosen record's fields; a field it leaves empty is (not set), and so is a session with
  -- nothing to go on (unknown, not direct: the sample collects no source on session_start).
  COALESCE(CASE s.traffic_source_basis
    WHEN 'session_traffic_source_last_click' THEN s.last_click.src
    WHEN 'collected_at_landing' THEN s.collected.src
    WHEN 'first_user_traffic_source' THEN s.first_user.src
  END, '(not set)') AS session_source,
  COALESCE(CASE s.traffic_source_basis
    WHEN 'session_traffic_source_last_click' THEN s.last_click.med
    WHEN 'collected_at_landing' THEN s.collected.med
    WHEN 'first_user_traffic_source' THEN s.first_user.med
  END, '(not set)') AS session_medium,
  COALESCE(CASE s.traffic_source_basis
    WHEN 'session_traffic_source_last_click' THEN s.last_click.camp
    WHEN 'collected_at_landing' THEN s.collected.camp
    WHEN 'first_user_traffic_source' THEN s.first_user.camp
  END, '(not set)') AS session_campaign,
  s.traffic_source_basis,
  COALESCE(s.first_page_view, s.first_page) AS landing_page,
  REGEXP_EXTRACT(COALESCE(s.first_page_view, s.first_page), r'^[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]*(/[^?#]*)') AS landing_page_path,
  s.events,
  s.page_views,
  s.engagement_time_msec,
  s.is_engaged,
  s.first_event.device_category,
  s.first_event.operating_system,
  s.first_event.browser,
  s.first_event.geo_country,
  s.analytics_storage,
  s.has_view_item,
  s.has_add_to_cart,
  s.has_begin_checkout,
  s.purchase_events,
  s.orders,
  s.zero_value_orders,
  s.orders > 0 AS converted,
  s.revenue_usd
FROM attributed AS s
LEFT JOIN `{{ project }}.{{ staging }}.int_identity` AS i
  USING (source, user_pseudo_id)
