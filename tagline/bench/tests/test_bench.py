"""The harness's logic, without Google Cloud: SQL builders, record handling, statistics, prices, batch overrides,
log parsing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tagline_bench import prices, results
from tagline_bench.bigquery import (
    Column,
    diff_sql,
    fingerprint_sql,
    job_record,
    job_totals,
    jobs_by_ids_sql,
    jobs_by_window_sql,
    quote_ident,
    row_struct_sql,
    table_ref,
    table_storage_sql,
)
from tagline_bench.config import ConfigError, load_config, resolve_tables
from tagline_bench.report import render
from tagline_bench.spark import app_id_from, apply_overrides, describe_batch, parse_kv, parse_summary

PROJECT = "my-proj-123"


# -- config -------------------------------------------------------------------------------------------------


def test_load_config_reads_env_file_and_environment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TAGLINE_GCP_PROJECT=my-proj-123\nTAGLINE_SPARK_BUCKET=gs://my-bucket/\nTAGLINE_GA4_DATASET=analytics_42\n")
    cfg = load_config(environ={"TAGLINE_MAX_BYTES_BILLED": "123"}, env_file=env)
    assert (cfg.project, cfg.bucket, cfg.ga4_dataset, cfg.max_bytes_billed) == ("my-proj-123", "my-bucket", "analytics_42", 123)
    assert cfg.secrets == {"my-proj-123": "<project>", "my-bucket": "<bucket>", "analytics_42": "analytics_<property_id>"}


def test_load_config_needs_a_project(tmp_path):
    with pytest.raises(ConfigError):
        load_config(environ={}, env_file=tmp_path / "missing")


def test_resolve_tables():
    # "stage2" is every table a Stage 2 build writes (the daily-run comparison fingerprints all ten)
    assert [t for _, t in resolve_tables("stage2")] == ["stg_events", "stg_items", "int_purchases", "int_device_days", "int_identity",
                                                        "fct_sessions", "fct_orders", "fct_order_items", "mart_campaign_daily",
                                                        "mart_funnel_daily"]
    assert ("tagline_marts", "fct_attribution") in resolve_tables("gate")
    assert ("tagline_staging", "stg_events") not in resolve_tables("gate")
    assert resolve_tables("fct_orders,tagline_staging.stg_items") == (("tagline_marts", "fct_orders"), ("tagline_staging", "stg_items"))
    with pytest.raises(ConfigError):
        resolve_tables("nope")


# -- SQL ----------------------------------------------------------------------------------------------------


def test_jobs_sql_filters_on_creation_time_and_ids():
    sql = jobs_by_ids_sql(PROJECT)
    assert f"`{PROJECT}`.`region-us`.INFORMATION_SCHEMA.JOBS_BY_PROJECT" in sql
    assert "creation_time BETWEEN @since AND @until" in sql and "job_id IN UNNEST(@ids)" in sql


def test_jobs_by_window_excludes_the_harness_and_validates_labels():
    sql = jobs_by_window_sql(PROJECT, {"orchestrator": "airflow"}, ("QUERY", "LOAD"))
    assert "l.key = 'app' AND l.value = 'tagline'" in sql
    assert "l.key = 'orchestrator' AND l.value = 'airflow'" in sql
    assert "l.key = 'kind' AND l.value = 'bench'" in sql and "NOT EXISTS" in sql
    assert "job_type IN ('QUERY', 'LOAD')" in sql
    with pytest.raises(ValueError):
        jobs_by_window_sql(PROJECT, {"x": "a' OR '1'='1"})
    with pytest.raises(ValueError):
        jobs_by_window_sql(PROJECT, {}, ("DROP",))


def test_storage_sql():
    sql = table_storage_sql(PROJECT)
    for col in ("active_logical_bytes", "long_term_physical_bytes", "time_travel_physical_bytes", "fail_safe_physical_bytes", "deleted"):
        assert col in sql
    assert "table_schema IN UNNEST(@datasets)" in sql


def test_row_struct_sorts_columns_and_rounds_only_top_level_floats():
    cols = [Column("b", "FLOAT"), Column("a", "STRING"), Column("c", "FLOAT", "REPEATED"), Column("d", "INTEGER")]
    assert row_struct_sql(cols) == "STRUCT(`a`, `b`, `c`, `d`)"
    assert row_struct_sql(cols, 9) == "STRUCT(`a`, ROUND(`b`, 9) AS `b`, `c`, `d`)"


def test_identifiers_are_validated():
    with pytest.raises(ValueError):
        quote_ident("a`b")
    with pytest.raises(ValueError):
        table_ref(PROJECT, "ds", "t; DROP")
    assert table_ref(PROJECT, "tagline_marts", "fct_orders") == f"`{PROJECT}.tagline_marts.fct_orders`"


def test_fingerprint_and_diff_sql_shape():
    cols = [Column("x", "INTEGER"), Column("y", "FLOAT")]
    fp = fingerprint_sql("`p.d.t`", cols)
    assert "BIT_XOR(fp)" in fp and "SUM(CAST(fp AS BIGNUMERIC))" in fp and "TO_JSON_STRING(STRUCT(`x`, `y`))" in fp
    d = diff_sql("`p.d.t`", "`p.b.t`", cols, 6)
    assert d.count("EXCEPT DISTINCT") == 2
    assert "ROUND(`y`, 6) AS `y`" in d
    assert "current_rows" in d and "baseline_rows" in d and "current_sum_fp" in d


# -- job records --------------------------------------------------------------------------------------------


def _job(job_id, step, billed, slot, secs, kind="model"):
    t0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    return {
        "job_id": job_id,
        "creation_time": t0,
        "start_time": t0 + timedelta(seconds=1),
        "end_time": t0 + timedelta(seconds=1 + secs),
        "job_type": "QUERY",
        "statement_type": "CREATE_TABLE_AS_SELECT",
        "state": "DONE",
        "total_bytes_processed": billed - 100,
        "total_bytes_billed": billed,
        "total_slot_ms": slot,
        "cache_hit": False,
        "error_reason": None,
        "destination_dataset": "tagline_staging",
        "destination_table": step,
        "labels": [{"key": "step", "value": step}, {"key": "kind", "value": kind}, {"key": "app", "value": "tagline"}],
    }


def test_job_record_and_totals():
    jobs = [job_record(_job("a", "stg_events", 2**40, 1000, 10.0)), job_record(_job("b", "01_keys", 2**30, 500, 2.0, "check"))]
    assert jobs[0]["step"] == "stg_events" and jobs[0]["kind"] == "model"
    assert jobs[0]["elapsed_seconds"] == 10.0 and jobs[0]["queued_seconds"] == 1.0
    assert jobs[0]["destination"] == "tagline_staging.stg_events"
    t = job_totals(jobs)
    assert t["jobs"] == 2 and t["bytes_billed"] == 2**40 + 2**30 and t["slot_ms"] == 1500 and t["job_seconds"] == 12.0
    assert t["usd"] == pytest.approx(6.25 * (1 + 1 / 1024))


# -- results log --------------------------------------------------------------------------------------------


def test_append_scrubs_secrets_everywhere(tmp_path):
    secrets = {"my-proj-123": "<project>", "my-bucket": "<bucket>"}
    rec = {"type": "x", "variant": "v1", "msg": "gs://my-bucket/a in my-proj-123", "nested": [{"my-proj-123.ds": 1}], "when": datetime(2026, 1, 1, tzinfo=UTC)}
    path = results.append(rec, secrets, results_dir=tmp_path)
    text = path.read_text()
    assert "my-proj-123" not in text and "my-bucket" not in text
    loaded = results.load(results_dir=tmp_path)[0]
    assert loaded["msg"] == "gs://<bucket>/a in <project>" and loaded["nested"] == [{"<project>.ds": 1}]
    assert loaded["when"] == "2026-01-01T00:00:00Z" and loaded["schema"] == results.SCHEMA_VERSION
    results.append({"type": "x", "variant": "v1"}, secrets, results_dir=tmp_path)
    assert len(path.read_text().splitlines()) == 2  # appended, not rewritten


def test_stats_median_and_range():
    assert results.stats([3, 1, 2]) == {"n": 3, "median": 2, "min": 1, "max": 3}
    assert results.stats([1, 2, 3, 10])["median"] == 2.5
    assert results.stats([None]) == {"n": 0, "median": None, "min": None, "max": None}
    assert results.spread_pct({"n": 3, "median": 100, "min": 90, "max": 110}) == 20


# -- prices -------------------------------------------------------------------------------------------------


def test_prices():
    assert prices.bq_usd(2**40) == 6.25
    assert prices.spark_usd(1, 3600) == pytest.approx(0.06 + 3600 * 0.000054795)
    gib = 2**30
    m = prices.storage_monthly_usd(active_logical=10 * gib, long_term_logical=10 * gib, active_physical=2 * gib, long_term_physical=gib, fail_safe_physical=gib)
    assert m["logical"] == pytest.approx(10 * 0.02 + 10 * 0.01)
    assert m["physical"] == pytest.approx(3 * 0.04 + 1 * 0.02)


# -- Spark --------------------------------------------------------------------------------------------------


BODY = {
    "runtime_config": {"version": "3.0", "properties": {"spark.executor.instances": "2", "spark.driver.cores": "4"}},
    "environment_config": {"execution_config": {"ttl": {"seconds": 1800}}},
    "labels": {"app": "tagline", "stage": "3"},
}


def test_apply_overrides_sets_unsets_and_labels_without_touching_the_input():
    out = apply_overrides(BODY, {"spark.sql.shuffle.partitions": "8"}, ["spark.executor.instances"], {"variant": "s4_x"}, "2.3")
    assert out["runtime_config"]["properties"] == {"spark.driver.cores": "4", "spark.sql.shuffle.partitions": "8"}
    assert out["runtime_config"]["version"] == "2.3" and out["labels"]["variant"] == "s4_x"
    assert BODY["runtime_config"]["properties"]["spark.executor.instances"] == "2"


def test_apply_overrides_sets_and_removes_job_arguments():
    body = {**BODY, "pyspark_batch": {"args": ["--project=p", "--spark-master=local"]}}
    out = apply_overrides(body, job_args={"spark-master": "local[4]", "--no-write": "1"})
    assert out["pyspark_batch"]["args"] == ["--project=p", "--spark-master=local[4]", "--no-write=1"]
    assert apply_overrides(body, job_args={"spark-master": ""})["pyspark_batch"]["args"] == ["--project=p"]
    assert body["pyspark_batch"]["args"] == ["--project=p", "--spark-master=local"]


def test_apply_overrides_keeps_the_guards():
    no_ttl = {**BODY, "environment_config": {"execution_config": {}}}
    with pytest.raises(ValueError, match="TTL"):
        apply_overrides(no_ttl)
    with pytest.raises(ValueError):
        apply_overrides(BODY, labels={"Bad Key": "x"})


def test_parse_kv():
    assert parse_kv(["a=1", "b.c = x=y"]) == {"a": "1", "b.c": "x=y"}
    with pytest.raises(ValueError):
        parse_kv(["novalue"])


def test_summary_and_app_id_parsing():
    line = 'ATTRIBUTION_SUMMARY {"compute_seconds": 166.9, "write_seconds": 114.7, "orders": 4926}'
    assert parse_summary(line) == {"compute_seconds": 166.9, "write_seconds": 114.7, "orders": 4926}
    assert parse_summary("nothing here") is None
    assert parse_summary("ATTRIBUTION_SUMMARY {broken") is None
    assert app_id_from("Deleting path gs://b/.spark-bigquery-local-1790607860967-45ec36a4-0d02-419a-b832-9bdf07fe5528") == "local-1790607860967"
    assert app_id_from("gs://b/.spark-bigquery-app-20260928050501-0000-1a2b3c4d-0000") == "app-20260928050501-0000"
    assert app_id_from("gs://b/.spark-bigquery-batch-a8c142b1-5257-41ce-8e3e-5694e6c5db86-9f1e2d3c-0000-4000-8000-000000000000") == (
        "batch-a8c142b1-5257-41ce-8e3e-5694e6c5db86"
    )
    assert app_id_from("no path") is None


def test_describe_batch_reads_a_finished_batch_it_did_not_start():
    from types import SimpleNamespace as NS

    created = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
    running_at = created + timedelta(seconds=50)
    ended = running_at + timedelta(seconds=100)
    b = NS(
        name="projects/p/locations/us-central1/batches/tagline-attr-20260928-abc-t1-def",
        state=NS(name="SUCCEEDED"), state_message="", create_time=created, state_time=ended,
        state_history=[NS(state=NS(name="PENDING"), state_start_time=created), NS(state=NS(name="RUNNING"), state_start_time=running_at)],
        runtime_info=NS(approximate_usage=NS(milli_dcu_seconds=300_000_000, shuffle_storage_gb_seconds=30_000), output_uri=""),
        runtime_config=NS(version="3.0", properties={"spark.sql.shuffle.partitions": "4"}),
        environment_config=NS(execution_config=NS(ttl=timedelta(minutes=30))),
        labels={"app": "tagline", "code": "37f5b3bf52ba", "orchestrator": "airflow"},
        pyspark_batch=NS(args=["--spark-master=local[4]"]),
    )
    client = NS(get_batch=lambda name: b)
    bq = NS(jobs_by_window=lambda *a, **k: [])
    logs = NS(job_facts=lambda batch_id, since: {"summary": {"compute_seconds": 31.0, "write_seconds": 30.0}, "app_id": "local-1",
                                                 "spark_master": "local[4]", "mode": "local", "worker_logged": False})
    rec = describe_batch(client, b.name, bq, logs)
    assert rec["batch_id"] == "tagline-attr-20260928-abc-t1-def" and rec["code_version"] == "37f5b3bf52ba"
    assert rec["ttl_seconds"] == 1800 and rec["job_args"] == ["--spark-master=local[4]"]
    assert (rec["wall_seconds"], rec["pending_seconds"], rec["running_seconds"]) == (150, 50, 100)
    assert rec["dcu_hours"] == pytest.approx(300_000 / 3600) and rec["avg_dcu_while_running"] == pytest.approx(3000)
    assert rec["compute_seconds"] == 31.0 and rec["mode"] == "local" and rec["usage_samples"] == []


# -- report -------------------------------------------------------------------------------------------------


def test_report_renders_medians_and_fingerprint_stability():
    jobs = [job_record(_job("a", "stg_events", 2**30, 1000, 10.0))]
    recs = []
    for i, (wall, slot) in enumerate([(60.0, 900), (70.0, 1100), (65.0, 1000)], start=1):
        j = [dict(jobs[0], slot_ms=slot)]
        recs.append(json.loads(json.dumps(results._jsonable({
            "type": "bq_run", "variant": "baseline", "run": i, "exit_code": 0, "wall_seconds": wall, "checks_line": "9/9 checks passed",
            "jobs": j, "totals": job_totals(j), "fingerprints": {"tagline_marts.fct_orders": {"fingerprint": "5:1:2", "float_digits": None}},
        }))))
    text = render(recs)
    assert "| baseline | 3 | 65.0 (60.0–70.0) |" in text
    assert "1,000 (900–1,100)" in text
    assert "| baseline | fct_orders | 3 | 1 | 5 |" in text


def test_storage_row_from_table_metadata():
    from tagline_bench.bigquery import storage_row_from_table

    props = {"numRows": "10", "numPartitions": "3", "numTotalLogicalBytes": "1000", "numActiveLogicalBytes": "1000",
             "numLongTermLogicalBytes": "0", "numTotalPhysicalBytes": "300", "numActivePhysicalBytes": "300",
             "numLongTermPhysicalBytes": "0", "numTimeTravelPhysicalBytes": "250", "numCurrentPhysicalBytes": "50"}
    row = storage_row_from_table("tagline_staging", "stg_events", "TABLE", props)
    assert row["total_rows"] == 10 and row["current_physical_bytes"] == 50 and row["time_travel_physical_bytes"] == 250
    assert row["fail_safe_physical_bytes"] is None and row["deleted"] is False


def test_job_ids_from_costs_json(tmp_path):
    from tagline_bench.cli import _job_ids_from_costs_json

    path = tmp_path / "costs.json"
    path.write_text(json.dumps([{"job_id": "a"}, {"job_id": None}, {"job_id": "b", "dry_run": True}, {"job_id": "c"}]))
    assert _job_ids_from_costs_json(path) == ["a", "c"]
    assert _job_ids_from_costs_json(tmp_path / "missing.json") == []


def test_script_statements_are_nested_under_their_script_and_not_counted_twice():
    from tagline_bench.bigquery import job_totals, nest_statements

    rows = [
        {"job_id": "script", "parent_job_id": None, "bytes_billed": 30, "slot_ms": 7, "elapsed_seconds": 2.0},
        {"job_id": "s1", "parent_job_id": "script", "bytes_billed": 10, "slot_ms": 3, "elapsed_seconds": 1.0},
        {"job_id": "s2", "parent_job_id": "script", "bytes_billed": 20, "slot_ms": 4, "elapsed_seconds": 1.0},
        {"job_id": "check", "parent_job_id": None, "bytes_billed": 5, "slot_ms": 1, "elapsed_seconds": 0.5},
        {"job_id": "other", "parent_job_id": "someone-else", "bytes_billed": 99, "slot_ms": 9, "elapsed_seconds": 9.0},
    ]
    jobs = nest_statements(rows, ["script", "check"])
    assert [j["job_id"] for j in jobs] == ["script", "check"]
    assert [c["job_id"] for c in jobs[0]["statements"]] == ["s1", "s2"] and "statements" not in jobs[1]
    t = job_totals(jobs)
    assert t["jobs"] == 2 and t["bytes_billed"] == 35 and t["slot_ms"] == 8


def test_a_time_window_nests_script_statements_too():
    from tagline_bench.bigquery import job_totals, nest_statements, top_level_ids

    rows = [
        {"job_id": "meta", "parent_job_id": None, "bytes_billed": 10, "slot_ms": 1, "elapsed_seconds": 0.2},
        {"job_id": "script", "parent_job_id": None, "bytes_billed": 30, "slot_ms": 7, "elapsed_seconds": 2.0},
        {"job_id": "s1", "parent_job_id": "script", "bytes_billed": 10, "slot_ms": 3, "elapsed_seconds": 1.0},
        {"job_id": "s2", "parent_job_id": "script", "bytes_billed": 20, "slot_ms": 4, "elapsed_seconds": 1.0},
        {"job_id": "orphan", "parent_job_id": "before-the-window", "bytes_billed": 5, "slot_ms": 1, "elapsed_seconds": 0.5},
    ]
    assert top_level_ids(rows) == ["meta", "script", "orphan"]
    t = job_totals(nest_statements(rows, top_level_ids(rows)))
    assert t["jobs"] == 3 and t["bytes_billed"] == 45


def test_costs_json_ids_record_a_script_once(tmp_path):
    import json

    from tagline_bench.cli import _job_ids_from_costs_json

    path = tmp_path / "costs.json"
    path.write_text(json.dumps([
        {"job_id": "c1", "parent_job_id": "script"}, {"job_id": "c2", "parent_job_id": "script"},
        {"job_id": "check1"}, {"job_id": None}, {"job_id": "dry", "dry_run": True},
    ]))
    assert _job_ids_from_costs_json(path) == ["script", "check1"]


def test_the_equivalence_fixture_steps_hold_the_rows_each_path_needs():
    """The fixture scenario's steps (no BigQuery): each step's rows are the ones its label and expectations name."""
    import sys

    from tagline_bench import equivalence as eq

    if str(eq.PIPELINE_TESTS) not in sys.path:
        sys.path.insert(0, str(eq.PIPELINE_TESTS))
    import site_export_fixture as fixture

    steps = eq.fixture_days(fixture)
    # a lookback of 1 everywhere: only the rule under test can put an earlier day in the window
    assert [s.opts for s in steps] == [{}] + [{"lookback": 1}] * 7
    first, day2, day3, days34, days45, day6, day1_again, no_change = steps
    # where each window must start: day 3 (staged from its streaming table) and day 4 (re-delivered) are before the
    # lookback's single day, so a window that starts there shows the changed-since-staged rule at work
    days = [f"{fixture.DAY1 + __import__('datetime').timedelta(days=n):%Y%m%d}" for n in range(6)]
    assert [s.since for s in steps] == [None, days[1], days[1], days[2], days[3], days[5], days[0], days[5]]
    assert no_change.tables == {} and not no_change.updates
    (d1,), (d4, d5), (d6,) = first.tables, sorted(days45.tables), day6.tables

    def purchases(rows):
        return [(r["user_pseudo_id"], next(p["value"]["string_value"] for p in r["event_params"] if p["key"] == "transaction_id"), r["user_id"])
                for r in rows if r["event_name"] == "purchase"]

    tx_b = fixture.build_fixture().transaction_id
    # day 1: A buys anonymously; restated later with one row more
    [(device, tx_a, user)] = purchases(first.tables[d1])
    assert device == fixture.DEVICE_A and user is None and tx_a != tx_b
    assert len(day1_again.tables[d1]) == len(first.tables[d1]) + 1 and day1_again.since == d1.removeprefix("events_")
    # days 3-4: C reuses B's transaction_id; E's only session
    day4 = days34.tables[d4]
    assert purchases(day4) == [(fixture.DEVICE_C, tx_b, None)]
    e_rows = [r for r in day4 if r["user_pseudo_id"] not in (fixture.DEVICE_C,)]
    assert e_rows and {r["event_name"] for r in e_rows} == {"session_start", "page_view"}
    # days 4-5: day 4 without the purchase and without E; B repeats its purchase, signed in as U
    assert purchases(days45.tables[d4]) == [] and {r["user_pseudo_id"] for r in days45.tables[d4]} == {fixture.DEVICE_C}
    assert purchases(days45.tables[d5]) == [(fixture.DEVICE_B, tx_b, fixture.U)]
    # day 6: W signs in on A
    assert {(r["user_pseudo_id"], r["user_id"]) for r in day6.tables[d6] if r["user_id"]} == {(fixture.DEVICE_A, fixture.W)}
    # every row's event_date is its table's day
    for s in steps:
        for name, rows in s.tables.items():
            assert {r["event_date"] for r in rows} == {name.rsplit("_", 1)[-1]}
    # the expectations: the conditional updates each path must run, and facts that format into plain SQL
    assert [sorted(s.updates) for s in steps] == [[], ["update fct_sessions"], [], ["update int_purchases", "update stg_events"],
                                                  ["update int_purchases", "update stg_events"], ["update fct_sessions"], [], []]
    for s in steps:
        for _, sql in s.facts:
            text = sql.format(staging="p.staging", marts="p.marts")
            assert "{" not in text and text.startswith("SELECT") and " AS ok" in text
