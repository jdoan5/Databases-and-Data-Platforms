"""The entrypoint (attribution/job.py) with BigQuery replaced by local DataFrames: argument checks, the read
and write options, the column docs, and a whole run() through to what it would write."""

from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import T0, order, session

from attribution import job, models, tables


def args(*extra: str):
    return job.parse_args(["--project=my-project-123", "--temp-bucket=gs://my-bucket/", *extra])


def test_parse_args_validates():
    a = args()
    assert (a.project, a.marts_dataset, a.temp_bucket, a.no_write) == ("my-project-123", "tagline_marts", "my-bucket", False)
    with pytest.raises(SystemExit):
        job.parse_args(["--project=Bad Project", "--temp-bucket=b-b-b"])
    with pytest.raises(SystemExit):
        job.parse_args(["--project=my-project-123", "--temp-bucket=b", "--marts-dataset=a.b"])


def test_spark_master_argument():
    assert args().spark_master is None, "no argument: the runtime's master"
    for ok in ("local", "local[4]", "local[16]", "local[*]", "dataproc"):
        assert args(f"--spark-master={ok}").spark_master == ok
    for bad in ("yarn", "local[0]", "local[4", "spark://h:7077", "local[1000]", ""):
        with pytest.raises(SystemExit):
            args(f"--spark-master={bad}")


def test_write_options_overwrite_a_partitioned_table_with_labelled_load_jobs():
    o = job.write_options("p.d.fct_attribution", "my-bucket", "fct_attribution")
    assert o["writeMethod"] == "indirect" and o["temporaryGcsBucket"] == "my-bucket"
    assert o["partitionField"] == "order_date" and o["partitionType"] == "DAY"
    assert o["bigQueryJobLabel.app"] == "tagline" and o["bigQueryJobLabel.stage"] == "3"
    assert o["bigQueryJobLabel.step"] == "fct_attribution"
    assert job.read_options("p.d.fct_orders") == {"table": "p.d.fct_orders", "readDataFormat": "ARROW"}


def test_write_table_stages_one_file_per_table():
    calls = []

    class Fake:
        def __getattr__(self, name):
            def record(*a, **k):
                calls.append((name, a, k))
                return self

            return record

        @property
        def write(self):
            calls.append(("write", (), {}))
            return self

    job.write_table(Fake(), "p.d.t", "my-bucket", "t")
    assert calls[0] == ("coalesce", (job.WRITE_PARTITIONS,), {}) and job.WRITE_PARTITIONS == 1
    assert [c[0] for c in calls[1:]] == ["write", "format", "mode", "options", "save"]
    assert calls[3][1] == ("overwrite",)


def test_documented_schema_refuses_undocumented_or_missing_columns():
    cols = (("a", "A."), ("b", "B."))
    assert job.documented_schema([{"name": "a", "type": "STRING"}, {"name": "b", "type": "INT64"}], cols)[1] == {
        "name": "b", "type": "INT64", "description": "B.",
    }
    with pytest.raises(job.AttributionProblem):
        job.documented_schema([{"name": "a", "type": "STRING"}, {"name": "c", "type": "STRING"}], cols)


def test_run_reads_attributes_checks_and_writes(frames, monkeypatch, capsys):
    t1 = T0 - timedelta(days=2)
    orders, sessions = frames(
        [
            order("o1", "P", t1 + timedelta(minutes=10), "s2", revenue=40.0),
            order("o2", "P", T0, "s3", revenue=60.0),
            order("z", "P", T0, "s3", revenue=None, zero_value_without_id=True),
        ],
        [
            session("s1", "P", T0 - timedelta(days=5), "email"),
            session("s2", "P", t1, "cpc"),
            session("s3", "P", T0 - timedelta(minutes=15), "direct"),
            session("s4", "P", T0 - timedelta(minutes=1), "referral"),  # after o2's order session: not a touch
        ],
    )
    reads, writes, docs = [], {}, []

    def fake_read(spark, table, columns, where=None):
        reads.append((table, columns, where is not None))
        df = orders if table.endswith("fct_orders") else sessions
        df = df.select(*columns)
        return df.where(where) if where is not None else df

    monkeypatch.setattr(job, "read_table", fake_read)
    monkeypatch.setattr(job, "write_table", lambda df, table, bucket, step: writes.__setitem__(table, df.collect()))
    monkeypatch.setattr(job, "apply_docs", lambda project, table, description, columns: docs.append(table))

    spark = orders.sparkSession
    summary = job.run(spark, args("--expected-spark-version=" + spark.version))
    assert reads == [
        ("my-project-123.tagline_marts.fct_orders", tuple(c for c in reads[0][1]), True),
        ("my-project-123.tagline_marts.fct_sessions", tuple(c for c in reads[1][1]), False),
    ]
    assert summary["orders"] == 2 and summary["touches"] == 2 + 3 and summary["problems"] == []
    assert summary["fct_attribution_rows"] == 5 * len(models.MODELS)
    assert summary["orders_last_touch_not_order_session"] == 0
    assert summary["spark_master"] == "local[2]" and summary["default_parallelism"] == 2  # the test session's
    assert summary["app_id"].startswith("local-")
    assert summary["models"]["linear"] == {"orders": 2.0, "revenue_usd": 100.0}
    fct = writes["my-project-123.tagline_marts.fct_attribution"]
    assert list(fct[0].asDict()) == tables.column_names(tables.FCT_ATTRIBUTION)
    mart = writes["my-project-123.tagline_marts.mart_attribution_daily"]
    assert list(mart[0].asDict()) == tables.column_names(tables.MART_ATTRIBUTION_DAILY)
    assert docs == list(writes)
    assert job.SUMMARY_MARKER in capsys.readouterr().out


def test_run_writes_nothing_when_the_checks_fail(frames, monkeypatch):
    orders, sessions = frames([order("o1", "P", T0, "s1")], [session("s1", "P", T0 - timedelta(minutes=5))])
    monkeypatch.setattr(job, "read_table", lambda spark, table, columns, where=None: (orders if table.endswith("fct_orders") else sessions).select(*columns))
    monkeypatch.setattr(job.models, "problems", lambda a, t: ["weights do not sum to 1"])
    monkeypatch.setattr(job, "write_table", lambda *a, **k: pytest.fail("wrote despite failed checks"))
    with pytest.raises(job.AttributionProblem):
        job.run(orders.sparkSession, args())


def test_no_write(frames, monkeypatch):
    orders, sessions = frames([order("o1", "P", T0, "s1")], [session("s1", "P", T0 - timedelta(minutes=5))])
    monkeypatch.setattr(job, "read_table", lambda spark, table, columns, where=None: (orders if table.endswith("fct_orders") else sessions).select(*columns))
    monkeypatch.setattr(job, "write_table", lambda *a, **k: pytest.fail("--no-write wrote"))
    assert job.run(orders.sparkSession, args("--no-write"))["written"] is False

