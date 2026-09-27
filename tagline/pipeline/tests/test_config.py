from pathlib import Path

import pytest

from tagline_pipeline.config import DEFAULT_MAX_BYTES_BILLED, ConfigError, load_config, parse_env_file


def test_parse_env_file_handles_comments_quotes_and_export():
    text = """
# a comment
TAGLINE_GCP_PROJECT=my-project-123
export TAGLINE_GA4_DATASET="analytics_123"
TAGLINE_MAX_BYTES_BILLED=5000   # trailing comment
EMPTY=
"""
    assert parse_env_file(text) == {
        "TAGLINE_GCP_PROJECT": "my-project-123",
        "TAGLINE_GA4_DATASET": "analytics_123",
        "TAGLINE_MAX_BYTES_BILLED": "5000",
        "EMPTY": "",
    }


def test_parse_env_file_rejects_a_line_without_equals():
    with pytest.raises(ConfigError, match="line 2"):
        parse_env_file("A=1\nnot a setting\n")


def test_environment_beats_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("TAGLINE_GCP_PROJECT=file-project\nTAGLINE_GA4_DATASET=analytics_1\n")
    cfg = load_config(environ={"TAGLINE_GCP_PROJECT": "env-project", "HOME": "/x"}, env_file=env)
    assert cfg.project == "env-project"
    assert cfg.ga4_dataset == "analytics_1"
    assert cfg.has_site
    assert cfg.site_project == "env-project"
    assert cfg.max_bytes_billed == DEFAULT_MAX_BYTES_BILLED


def test_empty_ga4_dataset_means_sample_only(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("TAGLINE_GCP_PROJECT=my-project\nTAGLINE_GA4_DATASET=\n")
    cfg = load_config(environ={}, env_file=env)
    assert cfg.ga4_dataset is None
    assert not cfg.has_site


def test_cli_override_wins(tmp_path: Path):
    cfg = load_config(environ={"TAGLINE_GCP_PROJECT": "my-project"}, env_file=None, ga4_dataset="analytics_9", ga4_table_prefix=None)
    assert cfg.ga4_dataset == "analytics_9"
    assert cfg.ga4_table_prefix == "events_"


def test_missing_project_says_how_to_fix_it():
    with pytest.raises(ConfigError, match=r"\.env\.example"):
        load_config(environ={}, env_file=None)


@pytest.mark.parametrize(
    "environ, message",
    [
        ({"TAGLINE_GCP_PROJECT": "Bad_Project"}, "not a valid project id"),
        ({"TAGLINE_GCP_PROJECT": "my-project", "TAGLINE_GA4_DATASET": "x`; DROP TABLE y; --"}, "not a valid dataset name"),
        ({"TAGLINE_GCP_PROJECT": "my-project", "TAGLINE_MAX_BYTES_BILLED": "10GB"}, "whole number"),
        ({"TAGLINE_GCP_PROJECT": "my-project", "TAGLINE_MAX_BYTES_BILLED": "0"}, "positive"),
    ],
)
def test_values_that_reach_sql_are_validated(environ, message):
    with pytest.raises(ConfigError, match=message):
        load_config(environ=environ, env_file=None)
