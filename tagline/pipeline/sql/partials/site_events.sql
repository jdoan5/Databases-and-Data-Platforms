  -- Source 2: the site's own GA4 export, {{ export_table }} tables. Same export schema as
  -- the sample plus the traffic-source records Google added later:
  --   collected_traffic_source            (June 2023) utm_* / gclid collected with the event
  --   session_traffic_source_last_click   (July 2024) GA4's session attribution, repeated on
  --                                       every event of the session; its cross_channel_campaign
  --                                       subrecord combines every integration (added later still)
  -- The column list must stay in step with the sample block in 10_stg_events.sql.
  SELECT
    'tagline_site' AS source,
    '{{ export_table }}' AS export_table,
    FARM_FINGERPRINT(TO_JSON_STRING(t)) AS row_fingerprint,
    t.event_date,
    t.event_timestamp,
    t.event_name,
    ARRAY(
      SELECT AS STRUCT p.key, p.value.string_value, p.value.int_value, p.value.double_value, p.value.float_value
      FROM UNNEST(t.event_params) AS p
    ) AS event_params,
    t.user_id,
    t.user_pseudo_id,
    CAST(t.privacy_info.analytics_storage AS STRING) AS analytics_storage,
    CAST(t.privacy_info.ads_storage AS STRING) AS ads_storage,
    CAST(t.privacy_info.uses_transient_token AS STRING) AS uses_transient_token,
    t.device.category AS device_category,
    t.device.operating_system,
    t.device.web_info.browser,
    t.geo.country AS geo_country,
    t.geo.region AS geo_region,
    t.geo.city AS geo_city,
    t.platform,
    CAST(t.stream_id AS STRING) AS stream_id,
    t.traffic_source.source AS first_user_source,
    t.traffic_source.medium AS first_user_medium,
    t.traffic_source.name AS first_user_campaign,
    t.collected_traffic_source.manual_source AS ctc_source,
    t.collected_traffic_source.manual_medium AS ctc_medium,
    t.collected_traffic_source.manual_campaign_name AS ctc_campaign,
    t.collected_traffic_source.manual_term AS ctc_term,
    -- Session attribution, one whole record at a time so a triple never mixes records:
    -- cross_channel_campaign (every integration: manual, Google Ads, SA360, DV360, CM360),
    -- else manual_campaign (utm_*), else a Google Ads click (auto-tagging, no utm_*), which GA4
    -- reports as google / cpc.
    CASE
      WHEN COALESCE(t.session_traffic_source_last_click.cross_channel_campaign.source,
                    t.session_traffic_source_last_click.cross_channel_campaign.medium,
                    t.session_traffic_source_last_click.cross_channel_campaign.campaign_name) IS NOT NULL
        THEN t.session_traffic_source_last_click.cross_channel_campaign.source
      WHEN COALESCE(t.session_traffic_source_last_click.manual_campaign.source,
                    t.session_traffic_source_last_click.manual_campaign.medium,
                    t.session_traffic_source_last_click.manual_campaign.campaign_name) IS NOT NULL
        THEN t.session_traffic_source_last_click.manual_campaign.source
      WHEN t.session_traffic_source_last_click.google_ads_campaign.campaign_name IS NOT NULL THEN 'google'
    END AS last_click_source,
    CASE
      WHEN COALESCE(t.session_traffic_source_last_click.cross_channel_campaign.source,
                    t.session_traffic_source_last_click.cross_channel_campaign.medium,
                    t.session_traffic_source_last_click.cross_channel_campaign.campaign_name) IS NOT NULL
        THEN t.session_traffic_source_last_click.cross_channel_campaign.medium
      WHEN COALESCE(t.session_traffic_source_last_click.manual_campaign.source,
                    t.session_traffic_source_last_click.manual_campaign.medium,
                    t.session_traffic_source_last_click.manual_campaign.campaign_name) IS NOT NULL
        THEN t.session_traffic_source_last_click.manual_campaign.medium
      WHEN t.session_traffic_source_last_click.google_ads_campaign.campaign_name IS NOT NULL THEN 'cpc'
    END AS last_click_medium,
    CASE
      WHEN COALESCE(t.session_traffic_source_last_click.cross_channel_campaign.source,
                    t.session_traffic_source_last_click.cross_channel_campaign.medium,
                    t.session_traffic_source_last_click.cross_channel_campaign.campaign_name) IS NOT NULL
        THEN t.session_traffic_source_last_click.cross_channel_campaign.campaign_name
      WHEN COALESCE(t.session_traffic_source_last_click.manual_campaign.source,
                    t.session_traffic_source_last_click.manual_campaign.medium,
                    t.session_traffic_source_last_click.manual_campaign.campaign_name) IS NOT NULL
        THEN t.session_traffic_source_last_click.manual_campaign.campaign_name
      ELSE t.session_traffic_source_last_click.google_ads_campaign.campaign_name
    END AS last_click_campaign,
    t.event_value_in_usd,
    t.ecommerce.transaction_id,
    t.ecommerce.purchase_revenue_in_usd,
    t.ecommerce.purchase_revenue,
    t.ecommerce.tax_value_in_usd,
    t.ecommerce.tax_value,
    t.ecommerce.shipping_value_in_usd,
    t.ecommerce.shipping_value,
    t.ecommerce.total_item_quantity,
    t.ecommerce.unique_items,
    ARRAY(
      SELECT AS STRUCT
        pos AS item_index, i.item_id, i.item_name, i.item_brand, i.item_variant,
        i.item_category, i.item_category2, i.item_category3, i.item_category4, i.item_category5,
        i.price_in_usd, i.price, i.quantity, i.item_revenue_in_usd, i.item_revenue,
        i.coupon, i.affiliation, i.item_list_id, i.item_list_name, CAST(i.item_list_index AS STRING) AS item_list_index,
        i.promotion_id, i.promotion_name, i.creative_name, i.creative_slot
      FROM UNNEST(t.items) AS i WITH OFFSET AS pos
      ORDER BY pos
    ) AS items
  FROM `{{ site_project }}.{{ site_dataset }}.{{ table_pattern }}` AS t
  WHERE {{ suffix_filter }}
