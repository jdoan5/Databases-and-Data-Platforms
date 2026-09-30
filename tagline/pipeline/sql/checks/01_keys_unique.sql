-- @check Every table's key is unique and never NULL.
WITH keys AS (
  SELECT 'stg_events' AS table_name, COUNT(*) AS row_count,
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, event_key))) AS distinct_keys,
    COUNTIF(source IS NULL OR event_key IS NULL) AS null_keys
  FROM `{{ project }}.{{ staging }}.stg_events`
  UNION ALL
  SELECT 'stg_items', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, event_key, item_index))),
    COUNTIF(source IS NULL OR event_key IS NULL OR item_index IS NULL)
  FROM `{{ project }}.{{ staging }}.stg_items`
  UNION ALL
  SELECT 'int_purchases', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, event_key))),
    COUNTIF(source IS NULL OR event_key IS NULL)
  FROM `{{ project }}.{{ staging }}.int_purchases`
  UNION ALL
  SELECT 'int_device_days', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, user_pseudo_id, event_date))),
    COUNTIF(source IS NULL OR user_pseudo_id IS NULL OR event_date IS NULL)
  FROM `{{ project }}.{{ staging }}.int_device_days`
  UNION ALL
  SELECT 'int_identity', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, user_pseudo_id))),
    COUNTIF(source IS NULL OR user_pseudo_id IS NULL)
  FROM `{{ project }}.{{ staging }}.int_identity`
  UNION ALL
  SELECT 'fct_sessions', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, user_pseudo_id, ga_session_id))),
    COUNTIF(source IS NULL OR user_pseudo_id IS NULL OR ga_session_id IS NULL OR session_key IS NULL)
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  UNION ALL
  SELECT 'fct_sessions.session_key', COUNT(*), COUNT(DISTINCT session_key), COUNTIF(session_key IS NULL)
  FROM `{{ project }}.{{ marts }}.fct_sessions`
  UNION ALL
  SELECT 'fct_orders', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, order_id))),
    COUNTIF(source IS NULL OR order_id IS NULL)
  FROM `{{ project }}.{{ marts }}.fct_orders`
  UNION ALL
  SELECT 'fct_order_items', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(source, order_id, line_number))),
    COUNTIF(source IS NULL OR order_id IS NULL OR line_number IS NULL)
  FROM `{{ project }}.{{ marts }}.fct_order_items`
  UNION ALL
  SELECT 'mart_campaign_daily', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(date, source, session_source, session_medium, session_campaign))),
    COUNTIF(date IS NULL OR source IS NULL OR session_source IS NULL OR session_medium IS NULL OR session_campaign IS NULL)
  FROM `{{ project }}.{{ marts }}.mart_campaign_daily`
  UNION ALL
  SELECT 'mart_funnel_daily', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(date, source))),
    COUNTIF(date IS NULL OR source IS NULL)
  FROM `{{ project }}.{{ marts }}.mart_funnel_daily`
  UNION ALL
  SELECT 'products', COUNT(*), COUNT(DISTINCT item_id), COUNTIF(item_id IS NULL)
  FROM `{{ project }}.{{ raw }}.products`
  UNION ALL
  SELECT 'campaign_costs', COUNT(*),
    COUNT(DISTINCT TO_JSON_STRING(STRUCT(cost_date, source, session_source, session_medium, session_campaign))),
    COUNTIF(cost_date IS NULL OR source IS NULL OR session_source IS NULL OR session_medium IS NULL OR session_campaign IS NULL)
  FROM `{{ project }}.{{ raw }}.campaign_costs`
)
SELECT table_name, row_count, distinct_keys, null_keys
FROM keys
WHERE row_count != distinct_keys OR null_keys > 0
