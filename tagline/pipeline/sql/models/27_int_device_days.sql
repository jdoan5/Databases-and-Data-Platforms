-- @table One row per device per day it was seen: the day's user_ids, first / last event, events and session ids.
--     int_identity is these rows added up per device, so the daily incremental build (make build-incremental) can
--     redo a day here and recompute only the devices it touched, instead of scanning every event of every device
--     (Stage 4). Events with no device (cookieless pings) are not here. Built by tagline/pipeline.
-- @column source: ga4_sample or tagline_site.
-- @column user_pseudo_id: The device (GA4 client id). (source, user_pseudo_id, event_date) is the key.
-- @column event_date: The day (event_date, property time zone).
-- @column user_ids: Distinct user_ids the device carried that day, sorted (empty when none).
-- @column first_seen_at: The device's first event that day.
-- @column first_signed_in_at: Its first event that day that carried a user_id.
-- @column last_seen_at: Its last event that day.
-- @column events: Its events that day.
-- @column ga_session_ids: Distinct ga_session_ids of its events that day, sorted (a session that crosses midnight
--     is in both days' rows).

CREATE OR REPLACE TABLE `{{ project }}.{{ staging }}.int_device_days`
PARTITION BY event_date
AS
SELECT
  source,
  user_pseudo_id,
  event_date,
  -- ARRAY_AGG over nothing but NULLs is NULL, not [], hence the IFNULLs.
  IFNULL(ARRAY_AGG(DISTINCT user_id IGNORE NULLS ORDER BY user_id), []) AS user_ids,
  MIN(event_timestamp) AS first_seen_at,
  MIN(IF(user_id IS NOT NULL, event_timestamp, NULL)) AS first_signed_in_at,
  MAX(event_timestamp) AS last_seen_at,
  COUNT(*) AS events,
  IFNULL(ARRAY_AGG(DISTINCT ga_session_id IGNORE NULLS ORDER BY ga_session_id), []) AS ga_session_ids
FROM `{{ project }}.{{ staging }}.stg_events`
WHERE user_pseudo_id IS NOT NULL
  -- make build-incremental narrows this to the days it processes (tagline_pipeline/incremental.py); empty otherwise
  {{ incremental_filter }}
GROUP BY source, user_pseudo_id, event_date
