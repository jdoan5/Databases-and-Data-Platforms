"""The daily incremental build's pure parts: which days a run processes, and the script it runs. No BigQuery."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from tagline_pipeline import incremental as inc
from tagline_pipeline.config import SQL_DIR, Config
from tagline_pipeline.pipeline import context
from tagline_pipeline.sources import SiteTables
from tagline_pipeline.sqlfiles import TemplateError, list_models

SAMPLE_DAYS = inc.calendar_days("20201101", "20210131")


def test_calendar_days_are_inclusive_and_cross_months():
    assert inc.calendar_days("20210130", "20210202") == ["20210130", "20210131", "20210201", "20210202"]
    assert len(SAMPLE_DAYS) == 92


def test_export_days_per_source():
    cfg = Config(project="my-project", ga4_dataset="analytics_1", sample_start="20210130", sample_end="20210131")
    site = SiteTables(daily=("20260925", "20260927"), intraday_only=("20260928",))
    assert inc.export_days(cfg, site) == {"ga4_sample": ["20210130", "20210131"], "tagline_site": ["20260925", "20260927", "20260928"]}
    assert inc.export_days(cfg, None) == {"ga4_sample": ["20210130", "20210131"]}


def test_a_daily_run_rereads_the_sites_newest_days_and_leaves_the_static_sample_alone():
    days = {"ga4_sample": SAMPLE_DAYS, "tagline_site": ["20260920", "20260925", "20260926", "20260927"]}
    loaded = {"ga4_sample": set(SAMPLE_DAYS), "tagline_site": {"20260920", "20260925", "20260926"}}
    w = inc.plan_window(days, loaded, lookback=3)
    assert [s.source for s in w.sources] == ["tagline_site"]
    # the 3 calendar days up to the newest are 25..27; 27 is also new
    assert w.get("tagline_site").days == ("20260925", "20260926", "20260927")
    assert "not loaded" in w.get("tagline_site").reason and "3 day(s) up to 20260927" in w.get("tagline_site").reason
    assert any("static" in n for n in w.notes)


def test_the_default_lookback_covers_gas_update_period():
    # Google: daily table 20220101 is updated through 20220104. The run on day R waits for R-1's table, so the table
    # of R-4 is final only once R-1 has ended: run R must still re-read it.
    assert inc.DEFAULT_LOOKBACK_DAYS == 4
    days = {"tagline_site": inc.calendar_days("20260901", "20260927")}
    w = inc.plan_window(days, {"tagline_site": set(days["tagline_site"])})
    assert w.get("tagline_site").days == ("20260924", "20260925", "20260926", "20260927")


def test_a_streaming_table_for_today_does_not_shorten_the_lookback():
    # with streaming on, the newest export day is today's streaming table; the lookback counts from the newest daily
    site = SiteTables(daily=tuple(inc.calendar_days("20260901", "20260927")), intraday_only=("20260928",))
    days = {"tagline_site": list(site.daily) + ["20260928"]}
    w = inc.plan_window(days, {"tagline_site": set(days["tagline_site"])}, anchor_by_source=inc.lookback_anchors(site))
    assert w.get("tagline_site").days == ("20260924", "20260925", "20260926", "20260927", "20260928")
    assert inc.lookback_anchors(SiteTables(daily=(), intraday_only=("20260928",))) == {}
    assert inc.lookback_anchors(None) == {}


def test_a_day_changed_in_the_export_after_it_was_loaded_is_reread_with_everything_after_it():
    days = {"ga4_sample": SAMPLE_DAYS, "tagline_site": inc.calendar_days("20260901", "20260927")}
    loaded = {"ga4_sample": set(SAMPLE_DAYS), "tagline_site": set(days["tagline_site"])}
    w = inc.plan_window(days, loaded, changed_by_source={"tagline_site": {"20260910", "20260915"}})
    site = w.get("tagline_site")
    assert site.days == tuple(inc.calendar_days("20260910", "20260927"))
    assert "2 day(s) changed" in site.reason and "4 day(s) up to 20260927" in site.reason
    # a changed day that is not loaded is simply missing; one inside the lookback changes nothing
    w = inc.plan_window(days, loaded, changed_by_source={"tagline_site": {"20260926", "20261001"}})
    assert w.get("tagline_site").days == ("20260924", "20260925", "20260926", "20260927")
    # --since refuses to leave a changed day behind
    with pytest.raises(inc.WindowError, match="changed in the export"):
        inc.plan_window(days, loaded, since="20260920", changed_by_source={"tagline_site": {"20260910"}})


T0 = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
SITE4 = SiteTables(daily=("20260925", "20260926", "20260927"), intraday_only=("20260928",))


def _meta(**over):
    """__TABLES__ as the export would show it: name -> (last modified, row count)."""
    meta = {
        "events_20260925": (T0 - timedelta(days=2), 900),
        "events_20260926": (T0 - timedelta(days=1), 1000),
        "events_20260927": (T0 - timedelta(hours=3), 1100),
        "events_intraday_20260927": (T0 - timedelta(hours=20), 800),  # not read: the day has a daily table
        "events_intraday_20260928": (T0, 50),
        "pseudonymous_users_20260927": (T0, 5),
    }
    meta.update(over)
    return meta


def test_export_state_is_the_table_a_build_reads_per_day():
    state = inc.export_state(SITE4, "events_", _meta())
    assert {d: (x.export_table, x.table_id, x.row_count) for d, x in state.items()} == {
        "20260925": ("daily", "events_20260925", 900), "20260926": ("daily", "events_20260926", 1000),
        "20260927": ("daily", "events_20260927", 1100), "20260928": ("intraday", "events_intraday_20260928", 50),
    }
    # a table listed by the build but gone from the metadata is not recorded (so the next run reads the day again)
    meta = _meta()
    del meta["events_20260926"]
    assert "20260926" not in inc.export_state(SITE4, "events_", meta)
    assert inc.export_state(None, "events_", meta) == {}


def test_changed_days_compares_the_export_now_with_what_was_staged():
    recorded = inc.export_state(SITE4, "events_", _meta(**{
        "events_20260927": (T0 - timedelta(hours=3), 1100),
    }))
    # day 27 was first staged from its streaming table; day 28 is new
    recorded["20260927"] = inc.ExportDay("20260927", "intraday", "events_intraday_20260927", T0 - timedelta(hours=20), 800)
    del recorded["20260928"]
    loaded = {"20260925", "20260926", "20260927"}
    now = inc.export_state(SITE4, "events_", _meta())
    # 27: its daily table landed after the day was read from the streaming one
    assert inc.changed_days(now, recorded, loaded) == {"20260927"}
    # GA4 added late events to 25 (modified later) or restated 26 (same time, another row count)
    now = inc.export_state(SITE4, "events_", _meta(events_20260925=(T0, 905), events_20260926=(T0 - timedelta(days=1), 999)))
    assert inc.changed_days(now, recorded, loaded) == {"20260925", "20260926", "20260927"}
    # a loaded day with no record (staged by a build older than the record) is read again
    assert inc.changed_days(now, {}, loaded) == loaded
    # a day that is not loaded is missing, not changed; a loaded day with no export table now is not changed either
    assert inc.changed_days(now, recorded, {"20260925", "20260101"}) == {"20260925"}
    assert inc.changed_days(inc.export_state(SITE4, "events_", _meta()), inc.export_state(SITE4, "events_", _meta()), loaded) == set()


def test_vanished_days_and_the_record_read_back():
    recorded = inc.export_state(SITE4, "events_", _meta())
    now = dict(recorded)
    del now["20260925"]
    assert inc.vanished_days(now, recorded) == ["20260925"]
    rows = [
        {"source": "tagline_site", "export_day": date(2026, 9, 26), "export_table": "daily", "table_id": "events_20260926",
         "last_modified_time": T0, "row_count": 7, "staged_at": T0},
        {"source": "ga4_sample", "export_day": date(2021, 1, 31), "export_table": "sample", "table_id": "x",
         "last_modified_time": T0, "row_count": 1, "staged_at": T0},
    ]
    assert inc.recorded_state(rows) == {"20260926": inc.ExportDay("20260926", "daily", "events_20260926", T0, 7)}


def test_the_staged_rows_are_literals_and_refuse_anything_but_an_export_table():
    state = inc.export_state(SITE4, "events_", _meta())
    sql = inc.staged_rows_sql(state.values())
    assert "('tagline_site', DATE '2026-09-25', 'daily', 'events_20260925', TIMESTAMP '2026-09-26 12:00:00.000000+00:00', 900)" in sql
    assert "'intraday', 'events_intraday_20260928'" in sql and sql.count("('tagline_site'") == 4
    assert sql.startswith("SELECT *, CURRENT_TIMESTAMP() AS staged_at FROM UNNEST(ARRAY<STRUCT<source STRING, export_day DATE")
    assert inc.staged_rows_sql([]).endswith("row_count INT64>>[\n])")  # an empty record is still typed
    with pytest.raises(ValueError):
        inc.staged_rows_sql([inc.ExportDay("20260925", "daily", "events_x'; DROP TABLE t; --", T0, 1)])
    ddl = inc.staged_table_sql("`p.tagline_staging.staged_export_days`", state.values())
    assert ddl.startswith("CREATE OR REPLACE TABLE `p.tagline_staging.staged_export_days` (\n  source STRING OPTIONS(description=")
    assert "labels=[('app', 'tagline'), ('stage', '2')]" in ddl and "table\\'s" in ddl
    assert [c for c, _, _ in inc.STAGED_COLUMNS] == ["source", "export_day", "export_table", "table_id", "last_modified_time",
                                                     "row_count", "staged_at"]


def test_a_gap_after_missed_runs_is_filled_from_its_first_day():
    days = {"tagline_site": ["20260901", "20260902", "20260903", "20260910"]}
    w = inc.plan_window(days, {"tagline_site": {"20260901"}}, lookback=3)
    assert w.get("tagline_site").days == ("20260902", "20260903", "20260910")


def test_missing_sample_days_are_processed_and_a_never_loaded_site_is_loaded_whole():
    days = {"ga4_sample": SAMPLE_DAYS, "tagline_site": ["20260927"]}
    loaded = {"ga4_sample": set(SAMPLE_DAYS[:-1])}
    w = inc.plan_window(days, loaded)
    assert w.get("ga4_sample").days == ("20210131",)
    assert w.get("tagline_site").days == ("20260927",)


def test_since_sets_the_start_for_every_source_and_refuses_a_gap_before_it():
    days = {"ga4_sample": SAMPLE_DAYS, "tagline_site": ["20260927"]}
    loaded = {"ga4_sample": set(SAMPLE_DAYS), "tagline_site": {"20260927"}}
    w = inc.plan_window(days, loaded, since="20210131")
    assert w.get("ga4_sample").days == ("20210131",) and w.get("tagline_site").days == ("20260927",)
    with pytest.raises(inc.WindowError, match="not in stg_events"):
        inc.plan_window(days, {"ga4_sample": set(SAMPLE_DAYS[:10])}, since="20210131")
    with pytest.raises(inc.WindowError, match="YYYYMMDD"):
        inc.plan_window(days, loaded, since="2021-01-31")
    with pytest.raises(inc.WindowError, match="lookback"):
        inc.plan_window(days, loaded, lookback=0)


def test_a_window_always_runs_to_the_newest_day():
    days = {"tagline_site": ["20260901", "20260905", "20260909"]}
    w = inc.plan_window(days, {"tagline_site": set(days["tagline_site"])}, since="20260903")
    assert w.get("tagline_site").days == ("20260905", "20260909")


def test_the_predicate_uses_date_literals_so_partitions_are_pruned():
    w = inc.Window((inc.SourceWindow("ga4_sample", ("20210131",), "x"), inc.SourceWindow("tagline_site", ("20260925", "20260926"), "y")))
    assert w.predicate("T") == (
        "((T.source = 'ga4_sample' AND T.event_date >= DATE '2021-01-31') OR (T.source = 'tagline_site' AND T.event_date >= DATE '2026-09-25'))"
    )
    assert w.min_since == "20210131"
    assert inc.Window().predicate() == "FALSE" and inc.Window().empty


def test_restrict_site_keeps_the_windows_tables():
    site = SiteTables(daily=("20260925", "20260926"), intraday_only=("20260927",))
    w = inc.Window((inc.SourceWindow("tagline_site", ("20260926", "20260927"), "x"),))
    assert inc.restrict_site(site, w) == SiteTables(daily=("20260926",), intraday_only=("20260927",))
    assert inc.restrict_site(site, inc.Window((inc.SourceWindow("ga4_sample", ("20210131",), "x"),))) is None


def test_select_body_and_identity_block_come_from_the_model_files():
    models = {m.name: m for m in list_models(SQL_DIR)}
    for m in models.values():
        body = inc.select_body(m.sql())
        assert "CREATE OR REPLACE" not in body and body.lstrip().upper().startswith(("WITH", "SELECT"))
    ident = inc.identity_expressions(models["fct_sessions"].sql())
    assert ident.startswith("COALESCE(s.session_user_id, i.person_id) AS person_id") and ident.endswith("END AS identity_rule")
    with pytest.raises(TemplateError):
        inc.select_body("SELECT 1")
    with pytest.raises(TemplateError):
        inc.identity_expressions("SELECT 1")


def _script(window, site=None, staged=None, **cfg_overrides):
    cfg = Config(project="my-project", ga4_dataset="analytics_1" if site else None, **cfg_overrides)
    models = {m.name: m for m in list_models(SQL_DIR)}
    return inc.script(cfg, models, context(cfg, site), site, window, staged)


def test_the_script_is_one_transaction_with_every_hook_filled():
    site = SiteTables(daily=("20260926",), intraday_only=("20260927",))
    w = inc.Window((inc.SourceWindow("tagline_site", ("20260926", "20260927"), "x"),))
    sql = _script(w, site)
    assert "{{" not in sql and "}}" not in sql
    assert sql.index("DECLARE") < sql.index("BEGIN TRANSACTION;") < sql.index("COMMIT TRANSACTION;")
    assert sql.count("BEGIN TRANSACTION;") == 1 and sql.strip().endswith("COMMIT TRANSACTION;")
    # no permanent table is replaced: DDL is not allowed in a transaction, so full tables are rewritten by MERGE
    assert "CREATE OR REPLACE" not in sql and not re.search(r"CREATE TABLE `", sql)
    for table in ("stg_events", "stg_items", "int_purchases", "int_device_days", "int_identity", "fct_sessions", "fct_orders",
                  "fct_order_items", "mart_campaign_daily", "mart_funnel_daily"):
        dataset = "tagline_staging" if table.startswith(("stg_", "int_")) else "tagline_marts"
        assert f"`my-project.{dataset}.{table}`" in sql, table
    # the site's window tables are read (daily and intraday), the sample reads nothing
    assert "_TABLE_SUFFIX BETWEEN '20260926' AND '20260926'" in sql and "_TABLE_SUFFIX IN ('20260927')" in sql
    assert re.search(r"_TABLE_SUFFIX BETWEEN '\d{8}' AND '20210131' AND FALSE", sql)
    # the earlier purchases join the dedupe; only the window's partitions are replaced
    assert "FROM `my-project.tagline_staging.int_purchases`\n    WHERE source IN ('tagline_site') AND NOT" in sql
    assert sql.count("WHEN NOT MATCHED BY SOURCE AND ((T.source = 'tagline_site' AND T.event_date >= DATE '2026-09-26')) THEN DELETE") == 4
    # sessions: only the touched ones, from the partitions they span
    assert "AND event_date >= min_session_date AND session_key IN (SELECT session_key FROM _sessions_touched)" in sql
    # order lines: only changed orders, from their dates' partitions
    assert "i.event_date IN UNNEST(order_dates)" in sql


def test_the_script_records_what_it_read_in_the_same_transaction():
    site = SiteTables(daily=("20260925", "20260926"), intraday_only=("20260927",))
    w = inc.Window((inc.SourceWindow("tagline_site", ("20260926", "20260927"), "x"),))
    state = inc.export_state(site, "events_", {"events_20260925": (T0, 1), "events_20260926": (T0, 2), "events_intraday_20260927": (T0, 3)})
    sql = _script(w, site, staged=state)
    merge = "MERGE `my-project.tagline_staging.staged_export_days` AS T USING ("
    assert sql.index("BEGIN TRANSACTION;") < sql.index(merge) < sql.index("COMMIT TRANSACTION;")
    step = sql[sql.index(merge):]
    # the window's days only, replacing the site's records from the window's first day on
    assert "DATE '2026-09-26', 'daily'" in step and "DATE '2026-09-27', 'intraday'" in step and "2026-09-25" not in step
    assert "WHEN NOT MATCHED BY SOURCE AND T.source = 'tagline_site' AND T.export_day >= DATE '2026-09-26' THEN DELETE;" in step
    # no record given, or no site day in the window: the table is left alone
    assert "staged_export_days" not in _script(w, site)
    assert "staged_export_days" not in _script(inc.Window((inc.SourceWindow("ga4_sample", ("20210131",), "--since"),)), staged=state)


def test_a_sample_day_reads_only_that_days_table():
    w = inc.Window((inc.SourceWindow("ga4_sample", ("20210131",), "--since"),))
    sql = _script(w)
    assert "_TABLE_SUFFIX BETWEEN '20210131' AND '20210131'\n" in sql
    assert "tagline_site" not in sql.split("-- 1. stg_events")[1].split("MERGE")[0].replace("'tagline_site'", "")


def test_an_empty_window_has_no_script():
    with pytest.raises(inc.WindowError):
        _script(inc.Window())


def test_the_monitoring_marts_recompute_only_the_days_the_window_can_change():
    """Stage 5: mart_kpi_daily and mart_tag_health_daily are one row per day, so step 10 deletes and re-inserts only the
    window sources' days from min_session_date on, plus the days of changed orders and of flipped collisions; the tag
    health insert reads only those stg_events partitions."""
    site = SiteTables(daily=("20260926",), intraday_only=())
    w = inc.Window((inc.SourceWindow("tagline_site", ("20260926",), "x"),))
    sql = _script(w, site)
    step = sql[sql.index("-- 10. The monitoring marts"):sql.index("COMMIT TRANSACTION;")]
    affected = "((source IN ('tagline_site') AND {col} >= min_session_date) OR {col} IN UNNEST(extra_dates))"
    assert sql.index("SET min_session_date") < sql.index("SET order_dates") < sql.index("SET extra_dates")
    assert "DECLARE extra_dates ARRAY<DATE>;" in sql.split("BEGIN TRANSACTION;")[0]
    for table in ("mart_kpi_daily", "mart_tag_health_daily"):
        assert f"DELETE FROM `my-project.tagline_marts.{table}` WHERE {affected.format(col='date')};" in step
        assert f"INSERT INTO `my-project.tagline_marts.{table}`" in step
    assert step.count(f"AND {affected.format(col='event_date')}") == 2  # both marts' stg_events reads
    assert step.count(f"AND {affected.format(col='session_date')}") == 2
    assert step.count(f"AND {affected.format(col='order_date')}") == 1
    assert "JOIN _flips AS f USING (source, transaction_id)" in step and "IFNULL(order_dates, ARRAY<DATE>[])" in step
    assert "CREATE OR REPLACE" not in step and "{{" not in step
