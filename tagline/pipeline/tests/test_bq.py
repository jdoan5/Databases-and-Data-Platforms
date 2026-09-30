"""The cost guard and dataset handling, against a fake BigQuery client (no network)."""

from types import SimpleNamespace

import pytest

pytest.importorskip("google.cloud.bigquery")

from google.api_core.exceptions import NotFound  # noqa: E402

from tagline_pipeline.bq import DATASET_DESCRIPTIONS, BigQuery  # noqa: E402
from tagline_pipeline.config import Config  # noqa: E402


class FakeJob:
    total_bytes_processed = 1024
    total_bytes_billed = 10 * 1024**2
    slot_millis = 42
    started = ended = None
    job_id = "job_1"

    def result(self):
        return []


class FakeClient:
    def __init__(self, datasets=None):
        self.configs = []
        self.datasets = datasets or {}
        self.created, self.updated = [], []

    def query(self, sql, job_config=None, project=None, location=None):
        self.configs.append((job_config, project, location))
        return FakeJob()

    def get_dataset(self, ref):
        if ref not in self.datasets:
            raise NotFound(ref)
        return self.datasets[ref]

    def create_dataset(self, ds):
        self.created.append(ds.dataset_id)

    def update_dataset(self, ds, fields):
        self.updated.append((ds, fields))


CFG = Config(project="my-project", max_bytes_billed=123_456_789)


@pytest.mark.parametrize("dry_run", [False, True])
def test_every_query_job_carries_the_cost_guard(dry_run):
    client = FakeClient()
    BigQuery(CFG, client=client).query("stg_events", "model", "SELECT 1", dry_run=dry_run)
    (config, project, location), = client.configs
    assert config.maximum_bytes_billed == CFG.max_bytes_billed
    assert config.use_query_cache is False
    assert config.dry_run is dry_run
    assert (project, location) == ("my-project", "US")
    assert config.labels["step"] == "stg_events"


def test_missing_datasets_creates_nothing():
    client = FakeClient({"my-project.tagline_raw": SimpleNamespace(location="US")})
    assert BigQuery(CFG, client=client).missing_datasets() == ["tagline_staging", "tagline_marts"]
    assert client.created == [] and client.updated == []


def test_ensure_datasets_creates_missing_and_refreshes_a_stale_description():
    stale = SimpleNamespace(location="US", description="old words", labels={"app": "tagline", "stage": "2", "keep": "me"})
    current = SimpleNamespace(location="US", description=DATASET_DESCRIPTIONS["staging"], labels={"app": "tagline", "stage": "2"})
    client = FakeClient({"my-project.tagline_raw": stale, "my-project.tagline_staging": current})
    created = BigQuery(CFG, client=client).ensure_datasets()
    assert created == ["tagline_marts"]
    assert [(ds, fields) for ds, fields in client.updated] == [(stale, ["description"])]
    assert stale.description == DATASET_DESCRIPTIONS["raw"] and stale.labels["keep"] == "me"


def test_ensure_datasets_refuses_another_location():
    client = FakeClient({"my-project.tagline_raw": SimpleNamespace(location="EU", description="", labels={})})
    with pytest.raises(RuntimeError, match="not US"):
        BigQuery(CFG, client=client).ensure_datasets()


def test_a_script_carries_the_cost_guard_and_reports_each_statement():
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 9, 29, tzinfo=timezone.utc)

    def child(job_id, statement, table, billed, minute):
        return SimpleNamespace(
            job_id=job_id, statement_type=statement, destination=SimpleNamespace(table_id=table) if table else None,
            total_bytes_processed=billed // 2, total_bytes_billed=billed, slot_millis=7,
            created=t0 + timedelta(minutes=minute), started=t0 + timedelta(minutes=minute), ended=t0 + timedelta(minutes=minute, seconds=2),
        )

    class ScriptClient(FakeClient):
        def list_jobs(self, parent_job=None):
            assert parent_job == "job_1"
            # out of order, as the API may list them; an anonymous result table is not named
            return [child("c2", "MERGE", "stg_events", 20, 2), child("c1", "CREATE_TABLE_AS_SELECT", "_new_events", 10, 1),
                    child("c3", "SELECT", "anon123", 0, 3)]

    client = ScriptClient()
    stats = BigQuery(CFG, client=client, extra_labels={"orchestrator": "airflow"}).script("incremental", "incremental", "BEGIN SELECT 1; END")
    (config, project, location), = client.configs
    assert config.maximum_bytes_billed == CFG.max_bytes_billed and config.use_query_cache is False
    assert config.labels == {"orchestrator": "airflow", "app": "tagline", "stage": "2", "kind": "incremental", "step": "incremental"}
    assert [(s.step, s.kind, s.bytes_billed, s.parent_job_id) for s in stats] == [
        ("_new_events", "create_table_as_select", 10, "job_1"), ("stg_events", "merge", 20, "job_1"), ("-", "select", 0, "job_1"),
    ]
    assert stats[0].seconds == 2.0


def test_the_window_metadata_queries_carry_the_cost_guard_return_their_cost_and_validate_the_prefix():
    client = FakeClient()
    client.sqls = []
    query = client.query

    def recording_query(sql, job_config=None, project=None, location=None):
        client.sqls.append(sql)
        return query(sql, job_config=job_config, project=project, location=location)

    client.query = recording_query
    client.list_rows = lambda table_id: [{"source": "tagline_site"}] if table_id == "p.d.t" else []
    bq = BigQuery(CFG, client=client)
    parts, parts_stat = bq.partition_ids("tagline_staging", "stg_events")
    meta, meta_stat = bq.table_metadata("site-project", "analytics_1", "events_")
    assert parts == set() and meta == {}
    # their cost records go into the build's cost table (and so into Stage 4's records)
    assert (parts_stat.step, parts_stat.kind, parts_stat.bytes_billed) == ("partitions", "metadata", 10 * 1024**2)
    assert (meta_stat.step, meta_stat.job_id) == ("export_tables", "job_1")
    parts_sql, tables_sql = client.sqls
    assert "INFORMATION_SCHEMA.PARTITIONS" in parts_sql and "total_rows > 0" in parts_sql
    assert "`site-project.analytics_1.__TABLES__`" in tables_sql and "STARTS_WITH(table_id, 'events_')" in tables_sql
    assert "last_modified_time" in tables_sql and "row_count" in tables_sql
    assert all(c.maximum_bytes_billed == CFG.max_bytes_billed and c.use_query_cache is False for c, _, _ in client.configs)
    with pytest.raises(ValueError, match="prefix"):
        bq.table_metadata("site-project", "analytics_1", "events_'; DROP")
    # the staged record is read through the table-data API: no query job
    assert bq.read_rows("p.d.t") == [{"source": "tagline_site"}] and len(client.configs) == 2
