-- 00_extensions.sql — run once, immediately after the container is healthy.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_prewarm;

-- Acceptance gate for Stage 0: every planner-relevant GUC must report
-- source='command line', not 'default'. If any row says 'default', the
-- setting is living in the container's memory instead of in git.
SELECT name, setting, source
FROM   pg_settings
WHERE  name IN ('jit','shared_buffers','effective_cache_size','work_mem',
                'random_page_cost','max_parallel_workers_per_gather',
                'default_statistics_target','track_io_timing')
ORDER  BY name;

-- Checksums are the one thing that cannot be turned on later without a
-- re-initdb. Must report 'on'.
SHOW data_checksums;
