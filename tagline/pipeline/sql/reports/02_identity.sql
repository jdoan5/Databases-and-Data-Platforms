-- @table Identity: devices and people by rule, and how many people were stitched across devices.
SELECT
  source,
  identity_rule,
  COUNT(*) AS devices,
  COUNT(DISTINCT person_id) AS people,
  COUNT(DISTINCT IF(person_device_count > 1, person_id, NULL)) AS people_on_2plus_devices,
  SUM(events) AS events,
  SUM(sessions) AS sessions
FROM `{{ project }}.{{ staging }}.int_identity`
GROUP BY source, identity_rule
ORDER BY source, identity_rule
