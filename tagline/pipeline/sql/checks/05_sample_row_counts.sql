-- @check ga4_sample: every raw export row is accounted for. Rows in the export = rows staged + exact
--     duplicates removed, and the documented number of exact duplicates in the sample is 0.
WITH raw AS (
  -- COUNT(*) over the wildcard is answered from table metadata: 0 bytes billed.
  SELECT COUNT(*) AS raw_rows
  FROM `{{ sample_table }}`
  WHERE _TABLE_SUFFIX BETWEEN '{{ sample_start }}' AND '{{ sample_end }}'
),
staged AS (
  SELECT COUNT(*) AS staged_rows, SUM(export_row_count) AS represented_rows
  FROM `{{ project }}.{{ staging }}.stg_events`
  WHERE source = 'ga4_sample'
)
SELECT raw_rows, staged_rows, represented_rows, raw_rows - staged_rows AS duplicates_removed, 0 AS documented_duplicates
FROM raw CROSS JOIN staged
WHERE raw_rows != represented_rows OR raw_rows - staged_rows != 0
