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
WITH devices AS (
  SELECT
    source,
    user_pseudo_id,
    -- ARRAY_AGG over nothing but NULLs is NULL, not [], so a never-signed-in device needs the IFNULL.
    IFNULL(ARRAY_AGG(DISTINCT user_id IGNORE NULLS ORDER BY user_id), []) AS user_ids,
    MIN(event_timestamp) AS first_seen_at,
    MIN(IF(user_id IS NOT NULL, event_timestamp, NULL)) AS first_signed_in_at,
    MAX(event_timestamp) AS last_seen_at,
    COUNT(*) AS events,
    COUNT(DISTINCT session_key) AS sessions
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE user_pseudo_id IS NOT NULL
  GROUP BY source, user_pseudo_id
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
