-- @table Purchase funnel by day: one row per date x source. A session reaches a step when it has
--     that step's event and reached every earlier step, in the same session (a closed funnel, in
--     any order within the session). converted_sessions counts every session with an order, by
--     any path, so it reconciles with fct_sessions; purchase_sessions is the funnel's last step.
--     Built by tagline/pipeline.
-- @column date: Session date (property time zone).
-- @column source: ga4_sample or tagline_site.
-- @column sessions: All sessions.
-- @column view_item_sessions: Sessions with a view_item.
-- @column add_to_cart_sessions: ... and an add_to_cart.
-- @column begin_checkout_sessions: ... and a begin_checkout.
-- @column purchase_sessions: ... and an order (fct_sessions.converted: a zero-value purchase without a transaction_id
--     does not count).
-- @column converted_sessions: Sessions with an order, whatever steps they skipped.
-- @column view_item_rate: view_item_sessions / sessions.
-- @column add_to_cart_rate: add_to_cart_sessions / view_item_sessions.
-- @column begin_checkout_rate: begin_checkout_sessions / add_to_cart_sessions.
-- @column purchase_rate: purchase_sessions / begin_checkout_sessions.
-- @column session_conversion_rate: converted_sessions / sessions.

CREATE OR REPLACE TABLE `{{ project }}.{{ marts }}.mart_funnel_daily`
PARTITION BY date
AS
WITH steps AS (
  SELECT
    session_date AS date,
    source,
    COUNT(*) AS sessions,
    COUNTIF(has_view_item) AS view_item_sessions,
    COUNTIF(has_view_item AND has_add_to_cart) AS add_to_cart_sessions,
    COUNTIF(has_view_item AND has_add_to_cart AND has_begin_checkout) AS begin_checkout_sessions,
    COUNTIF(has_view_item AND has_add_to_cart AND has_begin_checkout AND converted) AS purchase_sessions,
    COUNTIF(converted) AS converted_sessions
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY date, source
)

SELECT
  *,
  ROUND(SAFE_DIVIDE(view_item_sessions, sessions), 4) AS view_item_rate,
  ROUND(SAFE_DIVIDE(add_to_cart_sessions, view_item_sessions), 4) AS add_to_cart_rate,
  ROUND(SAFE_DIVIDE(begin_checkout_sessions, add_to_cart_sessions), 4) AS begin_checkout_rate,
  ROUND(SAFE_DIVIDE(purchase_sessions, begin_checkout_sessions), 4) AS purchase_rate,
  ROUND(SAFE_DIVIDE(converted_sessions, sessions), 4) AS session_conversion_rate
FROM steps
