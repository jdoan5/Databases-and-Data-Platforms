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
