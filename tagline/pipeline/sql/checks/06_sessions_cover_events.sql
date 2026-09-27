-- @check Per source: every event with a session id is in exactly one fct_sessions row (distinct session keys
--     and event totals agree), every device in stg_events is in int_identity, and every session has a person.
WITH events AS (
  SELECT source, COUNT(DISTINCT session_key) AS sessions, COUNTIF(session_key IS NOT NULL) AS session_events,
    COUNT(DISTINCT user_pseudo_id) AS devices
  FROM `{{ project }}.{{ staging }}.stg_events`
  GROUP BY source
),
sessions AS (
  SELECT source, COUNT(*) AS sessions, SUM(events) AS session_events, COUNTIF(person_id IS NULL) AS sessions_without_person
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY source
),
identity AS (
  SELECT source, COUNT(*) AS devices FROM `{{ project }}.{{ staging }}.int_identity` GROUP BY source
)
SELECT source, e.sessions AS event_sessions, s.sessions AS fct_sessions, e.session_events AS events_with_session,
  s.session_events AS fct_session_events, e.devices AS event_devices, i.devices AS identity_devices,
  s.sessions_without_person
FROM events AS e
FULL OUTER JOIN sessions AS s USING (source)
FULL OUTER JOIN identity AS i USING (source)
WHERE COALESCE(e.sessions, -1) != COALESCE(s.sessions, -1)
   OR COALESCE(e.session_events, -1) != COALESCE(s.session_events, -1)
   OR COALESCE(e.devices, -1) != COALESCE(i.devices, -1)
   OR COALESCE(s.sessions_without_person, 0) > 0
