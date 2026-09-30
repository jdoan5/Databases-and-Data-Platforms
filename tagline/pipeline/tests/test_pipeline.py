"""build() without BigQuery: a dry run creates nothing and stops cleanly at a model whose inputs are missing."""

import pytest

pytest.importorskip("google.cloud.bigquery")

from google.api_core.exceptions import NotFound  # noqa: E402

from tagline_pipeline import pipeline  # noqa: E402
from tagline_pipeline.config import Config  # noqa: E402
from tagline_pipeline.costs import JobStat  # noqa: E402


class FakeBQ:
    def __init__(self, missing_from=None):
        self.calls = []
        self.missing_from = missing_from

    def ensure_datasets(self):
        self.calls.append("ensure_datasets")
        return []

    def missing_datasets(self):
        self.calls.append("missing_datasets")
        return ["tagline_staging"]

    def query(self, step, kind, sql, *, dry_run=False):
        self.calls.append(f"query {step} dry_run={dry_run}")
        if step == self.missing_from:
            raise NotFound(f"Not found: Table my-project:tagline_staging.stg_events was not found in location US")
        return [], JobStat(step, kind, bytes_processed=100, dry_run=dry_run)


def test_dry_run_creates_nothing_and_stops_at_the_first_model_with_missing_inputs(capsys):
    fake = FakeBQ(missing_from="stg_items")
    stats = []
    result = pipeline.build(Config(project="my-project"), fake, dry_run=True, stats=stats)
    assert "ensure_datasets" not in fake.calls
    assert fake.calls[:3] == ["missing_datasets", "query stg_events dry_run=True", "query stg_items dry_run=True"]
    assert len(fake.calls) == 3  # nothing after the model that could not be validated
    assert [s.step for s in stats] == ["stg_events"] and result.stats is stats
    err = capsys.readouterr().err
    assert "dry run stopped at stg_items" in err and "make build" in err


def test_a_real_build_does_not_swallow_not_found():
    fake = FakeBQ(missing_from="stg_events")
    with pytest.raises(NotFound):
        pipeline.build(Config(project="my-project"), fake)
    assert fake.calls[0] == "ensure_datasets"


# -- what was staged: the full build records it, the daily build compares with it and keeps it -------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from tagline_pipeline import incremental  # noqa: E402

T0 = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
SITE_CFG = Config(project="my-project", ga4_dataset="analytics_1", sample_start="20210130", sample_end="20210131")


class SiteBQ(FakeBQ):
    """Enough of BigQuery for a build with a site export: tables listed, their metadata, the staged record."""

    def __init__(self, tables, staged_rows=None):
        super().__init__()
        self.tables = tables  # export table name -> (last modified, row count)
        self.staged_rows = staged_rows  # None: no staged_export_days table
        self.scripts = []

    def list_table_ids(self, project, dataset):
        return list(self.tables)

    def table_metadata(self, project, dataset, prefix):
        self.calls.append("metadata export_tables")
        return dict(self.tables), JobStat("export_tables", "metadata", bytes_billed=0)

    def apply_docs(self, *args, **kwargs):
        pass

    def num_rows(self, table_id):
        return 1

    def table_exists(self, table_id):
        return not table_id.endswith("staged_export_days") or self.staged_rows is not None

    def partition_ids(self, dataset, table):
        return {"20210130", "20210131", "20260926", "20260927"}, JobStat("partitions", "metadata", bytes_billed=10 * 2**20)

    def read_rows(self, table_id):
        return list(self.staged_rows)

    def query(self, step, kind, sql, *, dry_run=False):
        if step == "staged_export_days":
            self.calls.append(f"record {sql.count(chr(39) + 'tagline_site' + chr(39) + ',')} day(s)")
        return super().query(step, kind, sql, dry_run=dry_run)

    def script(self, step, kind, sql):
        self.calls.append("script")
        self.scripts.append(sql)
        self.last_script_statements = [JobStat("stg_events", "merge", bytes_billed=1, parent_job_id="s1")]
        return self.last_script_statements


TABLES = {"events_20260926": (T0 - timedelta(days=1), 10), "events_20260927": (T0, 20), "events_intraday_20260927": (T0, 5)}


def _rows(**over):
    """staged_export_days as a build would have left it for TABLES."""
    state = incremental.export_state(pipeline.classify_site_tables(TABLES, "events_"), "events_", {**TABLES, **over})
    return [{"source": "tagline_site", "export_day": x.day, "export_table": x.export_table, "table_id": x.table_id,
             "last_modified_time": x.last_modified, "row_count": x.row_count} for x in state.values()]


def test_a_full_build_records_what_stg_events_read_taken_before_it_read_it():
    fake = SiteBQ(TABLES)
    result = pipeline.build(SITE_CFG, fake)
    calls = [c for c in fake.calls if not c.startswith("query") or "stg_events" in c]
    assert calls[:4] == ["ensure_datasets", "metadata export_tables", "query stg_events dry_run=False", "record 2 day(s)"]
    assert [s.step for s in result.stats][:3] == ["export_tables", "stg_events", "staged_export_days"]
    # a build that does not write stg_events leaves the record alone; so does a dry run
    fake = SiteBQ(TABLES)
    pipeline.build(SITE_CFG, fake, start_at="stg_items")
    assert not any(c.startswith(("metadata", "record")) for c in fake.calls)
    fake = SiteBQ(TABLES)
    pipeline.build(SITE_CFG, fake, dry_run=True)
    assert not any(c.startswith(("metadata", "record")) for c in fake.calls)


def test_the_daily_build_rereads_a_day_changed_since_it_was_staged_and_records_it_in_its_transaction(capsys):
    # day 26 was restated after it was staged (lookback 1 would read day 27 alone)
    fake = SiteBQ({**TABLES, "events_20260926": (T0, 11)}, staged_rows=_rows())
    result = pipeline.build_incremental(SITE_CFG, fake, lookback=1)
    site = result.window.get("tagline_site")
    assert site.days == ("20260926", "20260927") and "changed in the export since staged (20260926)" in site.reason
    (sql,) = fake.scripts
    step = sql[sql.index("staged_export_days"):]
    assert "TIMESTAMP '2026-09-28 12:00:00.000000+00:00', 11)" in step and "events_20260927" in step
    assert sql.index("staged_export_days") < sql.index("COMMIT TRANSACTION;")
    # the window's metadata queries are in the cost table
    assert [s.step for s in result.stats] == ["partitions", "export_tables", "stg_events"]
    # nothing changed: the lookback alone
    fake = SiteBQ(TABLES, staged_rows=_rows())
    assert pipeline.build_incremental(SITE_CFG, fake, lookback=1).window.get("tagline_site").days == ("20260927",)


def test_without_a_record_every_loaded_site_day_is_read_again_once(capsys):
    fake = SiteBQ(TABLES, staged_rows=None)
    result = pipeline.build_incremental(SITE_CFG, fake, lookback=1)
    assert result.window.get("tagline_site").days == ("20260926", "20260927")
    assert fake.calls.index("record 0 day(s)") < fake.calls.index("script")  # created empty; the script fills it
    assert "read again, once" in capsys.readouterr().err


def test_a_lookback_below_one_is_refused_before_bigquery_is_called():
    fake = SiteBQ(TABLES, staged_rows=_rows())
    with pytest.raises(incremental.WindowError, match="at least 1"):
        pipeline.build_incremental(SITE_CFG, fake, lookback=0)
    assert fake.calls == []
