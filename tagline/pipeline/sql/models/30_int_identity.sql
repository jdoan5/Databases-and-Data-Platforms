-- @table Identity map: one row per device (source + user_pseudo_id) and the person it belongs to.
--     A device that only ever carried one user_id belongs to that person, including its
--     anonymous events before sign-in and after sign-out; several devices with the same user_id
--     are one person (cross-device). A device that carried two or more user_ids is a shared
--     device: it is not merged into anyone, it keeps a device-scoped anonymous person_id, and
--     fct_sessions gives each of its signed-in sessions to the user who signed in. The sample
--     has no user_id, so every sample person is a single device. Not partitioned: no date
--     grain, and it is always read whole. Built by tagline/pipeline.
-- @column source: ga4_sample or tagline_site.
-- @column user_pseudo_id: The device (GA4 client id). (source, user_pseudo_id) is the key.
-- @column person_id: The person this device belongs to: the user_id when the device carried exactly one,
--     else 'anon:<source>:<user_pseudo_id>'.
-- @column identity_rule: user_id (one user_id on this device), anonymous_device (never signed in) or
--     shared_device (two or more user_ids; not merged).
-- @column user_id: The device's only user_id, when it had exactly one.
-- @column user_id_count: Distinct user_ids seen on this device.
-- @column user_ids: Those user_ids, sorted (more than one only on shared devices).
-- @column first_seen_at: First event from this device.
-- @column first_signed_in_at: First event from this device that carried a user_id.
-- @column last_seen_at: Last event from this device.
-- @column events: Events from this device.
-- @column sessions: Sessions from this device.
-- @column person_device_count: Devices mapped to this person_id (more than 1 = stitched across devices).

CREATE OR REPLACE TABLE `{{ project }}.{{ staging }}.int_identity`
AS
WITH days AS (
  -- int_device_days is stg_events grouped by device and day; adding its rows up per device gives the same
  -- aggregates as grouping the events (Stage 4: the daily build redoes a day there and recomputes only the devices
  -- it touched, instead of scanning every event again)
  SELECT
    source,
    user_pseudo_id,
    ARRAY_CONCAT_AGG(user_ids) AS all_user_ids,
    MIN(first_seen_at) AS first_seen_at,
    MIN(first_signed_in_at) AS first_signed_in_at,
    MAX(last_seen_at) AS last_seen_at,
    SUM(events) AS events,
    ARRAY_CONCAT_AGG(ga_session_ids) AS all_ga_session_ids
  FROM `{{ project }}.{{ staging }}.int_device_days`
  -- make build-incremental narrows this to the devices it recomputes (tagline_pipeline/incremental.py); empty otherwise
  {{ incremental_filter }}
  GROUP BY source, user_pseudo_id
),

devices AS (
  SELECT
    source,
    user_pseudo_id,
    ARRAY(SELECT DISTINCT u FROM UNNEST(all_user_ids) AS u ORDER BY u) AS user_ids,
    first_seen_at,
    first_signed_in_at,
    last_seen_at,
    events,
    -- the device's distinct sessions: within one device, session_key differs exactly when ga_session_id does
    (SELECT COUNT(DISTINCT s) FROM UNNEST(all_ga_session_ids) AS s) AS sessions
  FROM days
),

resolved AS (
  SELECT
    *,
    ARRAY_LENGTH(user_ids) AS user_id_count,
    IF(ARRAY_LENGTH(user_ids) = 1, user_ids[OFFSET(0)], NULL) AS user_id,
    CASE ARRAY_LENGTH(user_ids)
      WHEN 0 THEN 'anonymous_device'
      WHEN 1 THEN 'user_id'
      ELSE 'shared_device'
    END AS identity_rule
  FROM devices
)

SELECT
  source,
  user_pseudo_id,
  COALESCE(user_id, CONCAT('anon:', source, ':', user_pseudo_id)) AS person_id,
  identity_rule,
  user_id,
  user_id_count,
  user_ids,
  first_seen_at,
  first_signed_in_at,
  last_seen_at,
  events,
  sessions,
  COUNT(*) OVER (PARTITION BY COALESCE(user_id, CONCAT('anon:', source, ':', user_pseudo_id))) AS person_device_count
FROM resolved
