-- @check No person has two user_ids (in int_identity or in fct_sessions); every device's person and rule follow
--     from its user_ids, and every session's from its own user_id and its device; user_ids are opaque 32-hex
--     account ids, as the tagging plan requires. One row per kind of problem, with a count and an example.
WITH problems AS (
  SELECT 'int_identity: person with several user_ids' AS problem, person_id AS id
  FROM `{{ project }}.{{ staging }}.int_identity`
  GROUP BY person_id
  HAVING COUNT(DISTINCT user_id) > 1
  UNION ALL
  SELECT 'fct_sessions: person with several user_ids', person_id
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY person_id
  HAVING COUNT(DISTINCT user_id) > 1
  UNION ALL
  -- IS NOT TRUE, so that a NULL (a NULL user_id_count, say) fails instead of slipping through
  SELECT "int_identity: person_id or identity_rule does not follow from the device's user_ids", user_pseudo_id
  FROM `{{ project }}.{{ staging }}.int_identity`
  WHERE (
    CASE
      WHEN user_id_count = 0 THEN identity_rule = 'anonymous_device' AND person_id = CONCAT('anon:', source, ':', user_pseudo_id)
      WHEN user_id_count = 1 THEN identity_rule = 'user_id' AND person_id = user_id
      WHEN user_id_count > 1 THEN identity_rule = 'shared_device' AND person_id = CONCAT('anon:', source, ':', user_pseudo_id)
    END
  ) IS NOT TRUE
  UNION ALL
  SELECT 'fct_sessions: identity_rule does not follow from the session and its device', s.session_key
  FROM `{{ project }}.{{ marts }}.fct_sessions` AS s
  JOIN `{{ project }}.{{ staging }}.int_identity` AS i USING (source, user_pseudo_id)
  WHERE (
    CASE
      WHEN s.session_user_id_count > 0 THEN s.identity_rule = 'signed_in_session' AND s.person_id = s.user_id
      WHEN i.identity_rule = 'user_id' THEN s.identity_rule = 'device_user_id' AND s.person_id = i.user_id
      WHEN i.identity_rule = 'shared_device' THEN s.identity_rule = 'shared_device_anonymous' AND s.person_id = i.person_id
      ELSE s.identity_rule = 'anonymous_device' AND s.person_id = i.person_id
    END
  ) IS NOT TRUE
  UNION ALL
  SELECT 'stg_events: user_id is not a 32-hex id', CONCAT(source, ':', CAST(event_key AS STRING))
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE user_id IS NOT NULL AND NOT REGEXP_CONTAINS(user_id, r'^[0-9a-f]{32}$')
)
SELECT problem, COUNT(*) AS failing, MIN(id) AS example
FROM problems
GROUP BY problem
