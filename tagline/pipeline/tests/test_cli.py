"""Exit codes and the partial cost table, with the pipeline and BigQuery replaced by fakes."""

import pytest

pytest.importorskip("google.cloud.bigquery")

from google.auth.exceptions import DefaultCredentialsError  # noqa: E402

from tagline_pipeline import bq, cli, pipeline  # noqa: E402
from tagline_pipeline.costs import JobStat  # noqa: E402


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("TAGLINE_GCP_PROJECT", "my-project")
    monkeypatch.delenv("TAGLINE_GA4_DATASET", raising=False)
    monkeypatch.setattr(bq, "BigQuery", lambda cfg: object())
    state = {"check_failures": [], "build_error": None}

    def build(cfg, client, *, dry_run=False, start_at=None, only=None, stats=None):
        stats.append(JobStat("stg_events", "model", bytes_processed=1, bytes_billed=10))
        if state["build_error"]:
            raise state["build_error"]
        return pipeline.BuildResult(stats=stats)

    def run_checks(cfg, client, site=None, stats=None):
        stats.append(JobStat("01_keys_unique", "check", bytes_processed=1, bytes_billed=10))
        return [pipeline.CheckResult("01_keys_unique", "keys", state["check_failures"])], stats

    monkeypatch.setattr(pipeline, "build", build)
    monkeypatch.setattr(pipeline, "run_checks", run_checks)
    return state


def test_build_exits_0_when_every_check_passes(fake, capsys):
    assert cli.main(["build"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "1/1 checks passed" in out and "stg_events" in out and "01_keys_unique" in out


def test_build_and_check_exit_1_when_a_check_returns_rows(fake, capsys):
    fake["check_failures"] = [{"table_name": "fct_orders", "row_count": 2, "distinct_keys": 1}]
    assert cli.main(["build"]) == cli.EXIT_CHECK_FAILED
    assert cli.main(["check"]) == cli.EXIT_CHECK_FAILED
    assert "FAIL  01_keys_unique" in capsys.readouterr().out


def test_missing_credentials_is_one_line_exit_3_and_the_billed_jobs_are_still_recorded(fake, capsys, tmp_path):
    fake["build_error"] = DefaultCredentialsError("no credentials")
    costs = tmp_path / "costs.json"
    assert cli.main(["build", "--costs-json", str(costs)]) == cli.EXIT_GOOGLE
    captured = capsys.readouterr()
    assert "gcloud auth application-default login" in captured.err
    assert "Traceback" not in captured.err
    assert "stg_events" in captured.out and '"step": "stg_events"' in costs.read_text()


def test_a_configuration_error_is_exit_2(monkeypatch, capsys):
    def load_config(**overrides):
        raise cli.ConfigError("TAGLINE_GCP_PROJECT is not set")

    monkeypatch.setattr(cli, "load_config", load_config)
    assert cli.main(["check"]) == cli.EXIT_CONFIG
    assert "configuration error" in capsys.readouterr().err
