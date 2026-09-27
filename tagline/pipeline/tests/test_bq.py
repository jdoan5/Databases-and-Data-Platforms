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
