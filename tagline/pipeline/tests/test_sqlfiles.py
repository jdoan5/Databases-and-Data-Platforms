import re

import pytest

from tagline_pipeline.config import SAMPLE_TABLE, SQL_DIR, Config
from tagline_pipeline.pipeline import context
from tagline_pipeline.sources import SiteTables
from tagline_pipeline.sqlfiles import Model, TemplateError, list_models, list_sql, parse_doc, render


def test_render_substitutes_and_refuses_unknown_names():
    assert render("SELECT * FROM `{{ project }}.x`", {"project": "p"}) == "SELECT * FROM `p.x`"
    with pytest.raises(TemplateError, match="no value for dataset"):
        render("{{ dataset }}", {})
    with pytest.raises(TemplateError, match="unrecognised"):
        render("{% if x %}", {})


def test_parse_doc_reads_the_header_only():
    doc = parse_doc(
        "-- @table One row per thing,\n"
        "--     continued here.\n"
        "-- @column a: First.\n"
        "-- @column b.c: Nested,\n"
        "--     with more.\n"
        "\n"
        "SELECT 1\n"
        "-- @column later: not part of the header\n"
    )
    assert doc.table == "One row per thing, continued here."
    assert doc.columns == {"a": "First.", "b.c": "Nested, with more."}


def test_bad_column_line_is_an_error():
    with pytest.raises(TemplateError):
        parse_doc("-- @column no description\nSELECT 1")


def test_models_are_in_build_order_and_layers():
    names = [m.name for m in list_models(SQL_DIR)]
    assert names == [
        "stg_events", "stg_items", "int_purchases", "int_device_days", "int_identity", "fct_sessions",
        "fct_orders", "fct_order_items", "mart_campaign_daily", "mart_funnel_daily", "mart_kpi_daily", "mart_tag_health_daily",
    ]
    assert [m.layer for m in list_models(SQL_DIR)] == ["staging"] * 5 + ["marts"] * 7
    with pytest.raises(TemplateError):
        Model(SQL_DIR / "models" / "99_other.sql").layer


@pytest.mark.parametrize("site", [None, SiteTables(daily=("20260924",), intraday_only=("20260925",))])
def test_every_sql_file_renders_and_is_documented(site):
    ctx = context(Config(project="my-project", ga4_dataset="analytics_1"), site)
    for model in list_models(SQL_DIR):
        text = model.sql()
        doc = parse_doc(text)
        assert doc.table, f"{model.name}: no @table"
        assert doc.columns, f"{model.name}: no @column"
        sql = render(text, ctx)
        assert f"`my-project.tagline_{model.layer}.{model.name}`" in sql
        assert re.search(r"CREATE OR REPLACE TABLE", sql)
    for directory in ("checks", "reports"):
        files = list_sql(SQL_DIR / directory)
        assert files, f"no {directory}"
        for path in files:
            text = path.read_text()
            doc = parse_doc(text)
            assert (doc.check if directory == "checks" else doc.table), f"{path.name}: no header"
            render(text, ctx)


_WILDCARD = re.compile(r"`[^`]*\*`")
_SUFFIX_FILTER = re.compile(r"\bWHERE\b.*?\b_TABLE_SUFFIX\s*(BETWEEN\b|IN\s*\(|>=|<=|=|>|<)", re.S | re.I)


def _own_clause(sql: str) -> str:
    """The rest of the SELECT a table reference is in: up to its closing parenthesis, a UNION or a semicolon."""
    depth = 0
    for i, ch in enumerate(sql):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return sql[:i]
        elif ch == ";" and depth == 0:
            return sql[:i]
        elif depth == 0 and re.match(r"UNION\s+ALL\b", sql[i : i + 20], re.I):
            return sql[:i]
    return sql


def unfiltered_wildcards(sql: str) -> list[str]:
    """Wildcard tables (`...*`) whose own SELECT has no WHERE ... _TABLE_SUFFIX filter. Comments do not count."""
    code = re.sub(r"--[^\n]*", "", sql)
    return [m.group(0) for m in _WILDCARD.finditer(code) if not _SUFFIX_FILTER.search(_own_clause(code[m.end() :]))]


def test_the_suffix_detector_is_not_fooled():
    assert unfiltered_wildcards("SELECT 1 FROM `p.d.events_*` WHERE _TABLE_SUFFIX BETWEEN '1' AND '2'") == []
    assert unfiltered_wildcards("SELECT 1 FROM `p.d.fake_ga4_events_*` AS t\n  JOIN x USING (a)\nWHERE b AND _TABLE_SUFFIX IN ('1')") == []
    assert unfiltered_wildcards("SELECT 1 FROM `p.d.events_*`\n-- WHERE _TABLE_SUFFIX = '1'\n") == ["`p.d.events_*`"]
    assert unfiltered_wildcards("SELECT _TABLE_SUFFIX FROM `p.d.events_*`\nWHERE x = 1") == ["`p.d.events_*`"]
    assert unfiltered_wildcards("SELECT * FROM (SELECT 1 FROM `p.d.other_*`) WHERE _TABLE_SUFFIX = '1'") == ["`p.d.other_*`"]
    assert unfiltered_wildcards("SELECT 1 FROM `p.d.a_*` WHERE _TABLE_SUFFIX = '1' UNION ALL SELECT 1 FROM `p.d.b_*`") == ["`p.d.b_*`"]


@pytest.mark.parametrize("prefix", ["events_", "fake_ga4_events_"])
def test_every_scan_of_a_wildcard_table_is_filtered_by_suffix(prefix):
    ctx = context(Config(project="my-project", ga4_dataset="analytics_1", ga4_table_prefix=prefix), SiteTables(("20260924",), ("20260925",)))
    seen = 0
    for path in [*list_sql(SQL_DIR / "models"), *list_sql(SQL_DIR / "checks"), *list_sql(SQL_DIR / "reports")]:
        sql = render(path.read_text(), ctx)
        seen += len(_WILDCARD.findall(re.sub(r"--[^\n]*", "", sql)))
        assert unfiltered_wildcards(sql) == [], f"{path.name}: wildcard table without a _TABLE_SUFFIX filter"
    assert seen >= 4  # the sample in stg_events and check 05, and the site's daily and intraday blocks
    assert SAMPLE_TABLE.endswith("events_*")


_SESSION_KEY_CONCAT = re.compile(
    r"CONCAT\((?:\w+\.)?source, ':', (?:\w+\.)?user_pseudo_id, ':', CAST\((?:\w+\.)?ga_session_id AS STRING\)\)"
)


def test_session_key_is_built_the_same_way_everywhere():
    """stg_events defines session_key; stg_items, int_purchases and fct_sessions rebuild it from its parts instead of
    reading the stored string (Stage 4, experiment 2). All four must spell it the same way, or the keys the facts
    join on would stop matching stg_events."""
    models = {m.name: re.sub(r"--[^\n]*", "", m.sql()) for m in list_models(SQL_DIR)}
    for name in ("stg_events", "stg_items", "int_purchases", "fct_sessions"):
        flat = re.sub(r"\s+", " ", models[name])
        assert _SESSION_KEY_CONCAT.search(flat), f"{name}: session_key is not built as source:user_pseudo_id:ga_session_id"
    # the tables that rebuild it never read the stored column from stg_events (fct_orders reads int_purchases)
    assert "e.session_key" not in models["stg_items"]
    assert "stg_events" not in models["fct_orders"]
    assert not re.search(r"\bsession_key\b", models["int_identity"])
    assert "PARTITION BY session_key" not in models["fct_sessions"] and "WHERE session_key IS NOT NULL" not in models["fct_sessions"]
