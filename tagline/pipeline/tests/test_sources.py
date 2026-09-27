from tagline_pipeline.config import Config
from tagline_pipeline.sources import SiteTables, classify_site_tables, site_union_sql


def test_daily_tables_win_over_intraday_for_the_same_day():
    tables = classify_site_tables(
        [
            "events_20260924",
            "events_20260925",
            "events_intraday_20260925",  # both exist until GA4 deletes the streaming table
            "events_intraday_20260926",  # only the streaming table so far
            "pseudonymous_users_20260925",
            "users_20260925",
            "events_fresh_20260925",
            "events_2026092",  # not eight digits
        ]
    )
    assert tables == SiteTables(daily=("20260924", "20260925"), intraday_only=("20260926",))
    assert not tables.empty


def test_nothing_to_read():
    assert classify_site_tables(["users_20260925"]).empty
    assert site_union_sql(Config(project="my-project", ga4_dataset="analytics_1"), None) == ""
    assert site_union_sql(Config(project="my-project"), SiteTables((), ())) == ""


def test_union_filters_every_wildcard_by_table_suffix():
    cfg = Config(project="my-project", ga4_dataset="analytics_1")
    sql = site_union_sql(cfg, SiteTables(daily=("20260924", "20260925"), intraday_only=("20260926", "20260927")))
    assert sql.count("UNION ALL") == 2
    assert "`my-project.analytics_1.events_*`" in sql
    assert "_TABLE_SUFFIX BETWEEN '20260924' AND '20260925'" in sql
    assert "`my-project.analytics_1.events_intraday_*`" in sql
    assert "_TABLE_SUFFIX IN ('20260926', '20260927')" in sql
    assert "'tagline_site' AS source" in sql
    assert "{{" not in sql


def test_site_project_can_differ():
    cfg = Config(project="my-project", ga4_dataset="analytics_1", ga4_project="other-project")
    sql = site_union_sql(cfg, SiteTables(daily=("20260924",), intraday_only=()))
    assert "`other-project.analytics_1.events_*`" in sql
    assert "'daily' AS export_table" in sql
    assert "events_intraday_*" not in sql
