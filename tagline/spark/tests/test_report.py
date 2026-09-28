"""make spark-report's table layout (tagline_spark/report.py). No BigQuery."""

from __future__ import annotations

from collections import namedtuple

from tagline_spark import report

Row = namedtuple("Row", "lookback model channel orders revenue")


def test_channel_table_shares_sum_per_model_and_scope_filters():
    rows = []
    for model in report.MODELS:
        rows += [
            Row("complete", model, "google / organic", 1.0, 60.0 if model == "first_click" else 30.0),
            Row("complete", model, "(direct) / (none)", 1.0, 40.0 if model == "first_click" else 70.0),
            Row("incomplete", model, "google / cpc", 1.0, 100.0),
        ]
    everything = report.channel_table(rows, "all")
    assert "google / cpc" in everything and "total revenue" in everything
    complete = report.channel_table(rows, "complete")
    assert "google / cpc" not in complete
    organic = next(line for line in complete.splitlines() if line.startswith("google / organic"))
    assert organic.split()[3:5] == ["30.0%", "30.0%"] and "60.0%" in organic  # last, last n-d ... first


def test_the_rebuild_query_is_committed_and_formats():
    sql = report.REBUILD_SQL.read_text(encoding="utf-8").format(project="my-project-123", marts="tagline_marts")
    assert "`my-project-123.tagline_marts.fct_attribution`" in sql and "{" not in sql
