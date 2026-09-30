-- @table Tag health on the collected data (Stage 5): one row per date x source x event_name x check. Tags can pass
--     in CI and still break in production, so the tagging contract is checked again on what GA4 exported. The
--     contract checks are generated from tagline/tagging/events.schema.json by tagline_pipeline/contract.py, not
--     copied by hand: every parameter the contract requires for the event must be present (required:), and, for the
--     sources held to the contract (the site), its value must satisfy the contract's const / enum / pattern / length /
--     minimum rules (format:) and value must equal the sum of price x quantity (value_math:). On every event of every
--     source: no email-like string, with the contract's own pattern, in the page fields, the search term or user_id
--     (pii:). On purchases: a transaction_id repeated from the same device (dedupe:duplicate_transaction_id) or sent
--     from two devices (dedupe:transaction_id_collision). On sessions (event_name '(session)'): no source collected, or
--     an obfuscated one (attribution:). Violations that are documented expectations for a source (the GA4 sample is
--     Google's store, tagged by Google and obfuscated; the session attribution checks on any source, which are not
--     contract rules) are status 'expected', not 'violation'; the anomaly rules still watch their rates
--     (docs/monitoring.md). Built by tagline/pipeline.
-- @column date: Event date (property time zone); for (session) rows, the session date.
-- @column source: ga4_sample or tagline_site.
-- @column event_name: The GA4 event checked, or (session) for the session-level attribution checks.
-- @column check_name: <check_kind>:<field>, e.g. required:ecommerce.currency, format:items[].item_brand, pii:email.
--     (date, source, event_name, check_name) is the key.
-- @column check_kind: required (present: not NULL, '' or (not set)), format (the contract's rules on a present value),
--     value_math (value = sum of price x quantity), pii (no email-like string), dedupe (purchase transaction ids) or
--     attribution (session source).
-- @column field: The contract field checked (ecommerce.currency, items[].item_id; items[] checks count an event once
--     when any of its items fails), or the columns checked for pii and attribution.
-- @column events: Events of this name that day (sessions, for (session) rows): the denominator.
-- @column violations: Events (sessions) that fail the check.
-- @column violation_rate: violations / events.
-- @column status: pass (no violation), expected (violations that are a documented expectation for the source; see
--     expectation) or violation (anything else: a contract breach on collected data).
-- @column expectation: Why the violations are expected (documented quirk of the source), when status is expected.
-- @column contract_version: The tagging contract version the checks were generated from (events.schema.json).

CREATE OR REPLACE TABLE `{{ project }}.{{ marts }}.mart_tag_health_daily`
PARTITION BY date
CLUSTER BY source, event_name
AS
WITH events AS (
  SELECT
    event_date AS date,
    source,
    event_name,
    -- the contract's checks for this event, generated from tagging/events.schema.json (tagline_pipeline/contract.py;
    -- `make contract-sql` prints them): ARRAY<STRUCT<check_name, check_kind, field, violated>>
    {{ contract_checks }} AS contract_checks,
    {{ contract_pii }} AS has_email,
    COALESCE(is_duplicate_purchase, FALSE) AS is_duplicate_purchase,
    COALESCE(is_transaction_id_collision, FALSE) AS is_transaction_id_collision
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE TRUE
    -- make build-incremental narrows this to the days it recomputes (tagline_pipeline/incremental.py); empty otherwise
    {{ incremental_filter }}
),

sessions AS (
  SELECT session_date AS date, source, session_source, session_medium
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  WHERE TRUE
    -- as incremental_filter, on session_date
    {{ incremental_filter_sessions }}
),

checks AS (
  -- required checks apply to every source; format and value_math only to the sources held to the contract
  SELECT e.date, e.source, e.event_name, c.check_name, c.check_kind, c.field, COUNT(*) AS events, COUNTIF(c.violated) AS violations
  FROM events AS e, UNNEST(e.contract_checks) AS c
  WHERE c.check_kind = 'required' OR e.source IN ({{ contract_sources }})
  GROUP BY date, source, event_name, check_name, check_kind, field

  UNION ALL
  SELECT date, source, event_name, 'pii:email', 'pii', {{ contract_pii_fields }}, COUNT(*), COUNTIF(has_email)
  FROM events
  GROUP BY date, source, event_name

  UNION ALL
  SELECT date, source, event_name, 'dedupe:duplicate_transaction_id', 'dedupe', 'ecommerce.transaction_id', COUNT(*),
    COUNTIF(is_duplicate_purchase)
  FROM events
  WHERE event_name = 'purchase'
  GROUP BY date, source, event_name

  UNION ALL
  SELECT date, source, event_name, 'dedupe:transaction_id_collision', 'dedupe', 'ecommerce.transaction_id', COUNT(*),
    COUNTIF(is_transaction_id_collision)
  FROM events
  WHERE event_name = 'purchase'
  GROUP BY date, source, event_name

  UNION ALL
  SELECT date, source, '(session)', 'attribution:source_not_set', 'attribution', 'session_source', COUNT(*),
    COUNTIF(session_source = '(not set)')
  FROM sessions
  GROUP BY date, source

  UNION ALL
  SELECT date, source, '(session)', 'attribution:source_obfuscated', 'attribution', 'session_source, session_medium', COUNT(*),
    COUNTIF(session_source IN ('<Other>', '(data deleted)') OR session_medium IN ('<Other>', '(data deleted)'))
  FROM sessions
  GROUP BY date, source
),

expectations AS (
  -- Documented expectations (docs/monitoring.md, "Expected violations"): violations known and accepted for a
  -- source, recorded as status 'expected' instead of 'violation'. The patterns must not overlap (check 11).
  SELECT * FROM UNNEST(ARRAY<STRUCT<source STRING, event_pattern STRING, check_pattern STRING, reason STRING>>[
    ('ga4_sample', '%', 'required:%',
     'GA4 sample: the Google Merchandise Store, tagged by Google, not to the Tagline contract, and obfuscated: currency '
     || 'and value only from add_shipping_info on, no items on add_shipping_info / add_payment_info, often none on '
     || 'view_item, item brand / quantity / list index often (not set), 450 purchases with no transaction_id'),
    ('ga4_sample', 'purchase', 'dedupe:duplicate_transaction_id',
     'GA4 sample: 324 purchase events repeat an earlier one of the same order on the same device; fct_orders keeps the first'),
    ('ga4_sample', 'purchase', 'dedupe:transaction_id_collision',
     'GA4 sample: 15 transaction ids were sent from two devices weeks apart; kept as separate orders'),
    ('ga4_sample', '(session)', 'attribution:%',
     'GA4 sample: obfuscated sources (<Other>, (data deleted)), and sessions with no source collected at all'),
    -- Not part of the tagging contract: GA4 itself leaves some sessions without a source ((not set)) in any property.
    -- Held to zero, one such session out of the site's ~50 a day would be a critical contract_violation; as an
    -- expectation, its rate is watched by the robust band instead (a jump, once there is history).
    ('tagline_site', '(session)', 'attribution:%',
     'The site: GA4 leaves some sessions without a source ((not set)) or with a deleted one; not a tagging-contract rule, '
     || 'so the rate is watched for jumps rather than held to zero')
  ])
)

SELECT
  c.date,
  c.source,
  c.event_name,
  c.check_name,
  c.check_kind,
  c.field,
  c.events,
  c.violations,
  ROUND(SAFE_DIVIDE(c.violations, c.events), 6) AS violation_rate,
  CASE WHEN c.violations = 0 THEN 'pass' WHEN x.reason IS NOT NULL THEN 'expected' ELSE 'violation' END AS status,
  IF(c.violations > 0, x.reason, NULL) AS expectation,
  {{ contract_version }} AS contract_version
FROM checks AS c
LEFT JOIN expectations AS x
  ON x.source = c.source AND c.event_name LIKE x.event_pattern AND c.check_name LIKE x.check_pattern
