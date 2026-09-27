-- @check No email-like string (name@domain.tld, raw or URL-encoded as %40) in user_id, person_id, page
--     fields or search terms. Reports counts per column, never the values.
WITH hits AS (
  SELECT source,
    COUNTIF(REGEXP_CONTAINS(user_id, r'(?i)[a-z0-9._%+-]+(@|%40)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}')) AS user_id,
    COUNTIF(REGEXP_CONTAINS(page_location, r'(?i)[a-z0-9._%+-]+(@|%40)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}')) AS page_location,
    COUNTIF(REGEXP_CONTAINS(page_title, r'(?i)[a-z0-9._%+-]+(@|%40)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}')) AS page_title,
    COUNTIF(REGEXP_CONTAINS(page_referrer, r'(?i)[a-z0-9._%+-]+(@|%40)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}')) AS page_referrer,
    COUNTIF(REGEXP_CONTAINS(search_term, r'(?i)[a-z0-9._%+-]+(@|%40)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}')) AS search_term,
    0 AS person_id,
    0 AS landing_page
  FROM `{{ project }}.{{ staging }}.stg_events`
  GROUP BY source
  UNION ALL
  SELECT source, 0, 0, 0, 0, 0,
    COUNTIF(REGEXP_CONTAINS(person_id, r'(?i)[a-z0-9._%+-]+(@|%40)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}')),
    COUNTIF(REGEXP_CONTAINS(landing_page, r'(?i)[a-z0-9._%+-]+(@|%40)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}'))
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  GROUP BY source
)
SELECT *
FROM (
  SELECT source, SUM(user_id) AS user_id, SUM(page_location) AS page_location, SUM(page_title) AS page_title,
    SUM(page_referrer) AS page_referrer, SUM(search_term) AS search_term, SUM(person_id) AS person_id,
    SUM(landing_page) AS landing_page
  FROM hits
  GROUP BY source
)
WHERE user_id + page_location + page_title + page_referrer + search_term + person_id + landing_page > 0
