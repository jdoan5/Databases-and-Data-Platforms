-- @check mart_tag_health_daily covers every event: its key is unique and never NULL; every date x source x event_name
--     in stg_events has a pii:email row counting exactly its events, and every (session) row counts exactly
--     fct_sessions' sessions that day; every check of an event counts the same events, with 0 <= violations <= events;
--     each contract event holds every check the contract generates (required checks for every source, all checks for
--     the sources held to the contract); status is pass exactly when there is no violation, and expected exactly when
--     a documented expectation matched (one at most).
WITH mart AS (
  SELECT *, COUNT(*) OVER (PARTITION BY date, source, event_name, check_name) AS copies
  FROM `{{ project }}.{{ marts }}.mart_tag_health_daily`
),
stg AS (
  SELECT event_date AS date, source, event_name, COUNT(*) AS events
  FROM `{{ project }}.{{ staging }}.stg_events`
  GROUP BY date, source, event_name
  UNION ALL
  SELECT session_date, source, '(session)', COUNT(*)
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY session_date, source
),
per_event AS (
  SELECT date, source, event_name,
    MIN(events) AS min_events, MAX(events) AS max_events,
    ANY_VALUE(IF(check_name IN ('pii:email', 'attribution:source_not_set'), events, NULL)) AS covered_events,
    COUNTIF(check_kind = 'required') AS required_checks,
    COUNTIF(check_kind IN ('required', 'format', 'value_math')) AS contract_checks,
    COUNTIF(copies > 1 OR date IS NULL OR source IS NULL OR event_name IS NULL OR check_name IS NULL) AS bad_keys,
    COUNTIF(NOT (violations BETWEEN 0 AND events)) AS bad_counts,
    COUNTIF((violations = 0) != (status = 'pass') OR (status = 'expected') != (expectation IS NOT NULL)
      OR status NOT IN ('pass', 'expected', 'violation')) AS bad_status
  FROM mart
  GROUP BY date, source, event_name
),
contract AS (
  SELECT * FROM UNNEST({{ contract_check_counts }})
)
SELECT
  COALESCE(p.date, s.date) AS date,
  COALESCE(p.source, s.source) AS source,
  COALESCE(p.event_name, s.event_name) AS event_name,
  s.events AS stg_events, p.covered_events, p.min_events, p.max_events,
  p.required_checks, c.required_checks AS contract_required_checks,
  p.contract_checks, c.all_checks AS contract_all_checks,
  p.bad_keys, p.bad_counts, p.bad_status
FROM per_event AS p
FULL OUTER JOIN stg AS s ON s.date = p.date AND s.source = p.source AND s.event_name = p.event_name
LEFT JOIN contract AS c ON c.event_name = COALESCE(p.event_name, s.event_name)
WHERE p.date IS NULL OR s.date IS NULL
   OR p.covered_events IS DISTINCT FROM s.events
   OR p.min_events != p.max_events
   OR p.bad_keys > 0 OR p.bad_counts > 0 OR p.bad_status > 0
   OR (c.event_name IS NOT NULL AND p.required_checks != c.required_checks)
   OR (c.event_name IS NOT NULL AND COALESCE(p.source, s.source) IN ({{ contract_sources }}) AND p.contract_checks != c.all_checks)
