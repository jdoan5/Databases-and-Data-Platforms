"""The batch definition shared by make spark-submit and the Airflow DAG (tagline_spark/batch.py), and the
submit tool's pure parts (tagline_spark/submit.py). No Google Cloud calls."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from tagline_spark import batch as b
from tagline_spark import submit

SPARK_DIR = Path(__file__).resolve().parents[1]
TARGET = b.Target(
    project="my-project-123",
    region="us-central1",
    bucket="my-bucket",
    service_account="spark-sa@my-project-123.iam.gserviceaccount.com",
)


def test_pyspark_is_pinned_to_the_runtimes_spark():
    import pyspark

    pins = tomllib.loads((SPARK_DIR / "pyproject.toml").read_text())["project"]["optional-dependencies"]["dev"]
    assert f"pyspark=={b.SPARK_VERSION}" in pins
    assert pyspark.__version__ == b.SPARK_VERSION, "the venv's pyspark is not the runtime's Spark: make spark-venv"
    assert b.RUNTIME_VERSION == "3.0" and b.SPARK_VERSION == "4.0.2"


def test_batch_has_the_cost_guards_and_runs_as_the_service_account():
    body = b.batch(TARGET, orchestrator="make", version="abc123def456")
    execution = body["environment_config"]["execution_config"]
    assert execution == {
        "service_account": TARGET.service_account,
        "subnetwork_uri": "default",
        "ttl": {"seconds": 1800},
    }
    assert "staging_bucket" not in execution  # runtime 3.0 uses none
    assert body["runtime_config"]["version"] == "3.0"
    props = body["runtime_config"]["properties"]
    assert props["spark.executor.instances"] == props["spark.dynamicAllocation.maxExecutors"] == "2"
    assert props["spark.driver.cores"] == props["spark.executor.cores"] == "4"
    assert body["labels"] == {
        "app": "tagline", "stage": "3", "job": "attribution", "orchestrator": "make", "code": "abc123def456",
    }
    assert body["pyspark_batch"] == {
        "main_python_file_uri": "gs://my-bucket/code/attribution/abc123def456/main.py",
        "python_file_uris": ["gs://my-bucket/code/attribution/abc123def456/attribution.zip"],
        "args": [
            "--project=my-project-123",
            "--marts-dataset=tagline_marts",
            "--temp-bucket=my-bucket",
            f"--expected-spark-version={b.SPARK_VERSION}",
        ],
    }


def test_batch_converts_to_the_dataproc_resource():
    dataproc_v1 = pytest.importorskip("google.cloud.dataproc_v1")
    proto = dataproc_v1.Batch(b.batch(TARGET, orchestrator="airflow"))
    assert proto.environment_config.execution_config.ttl.total_seconds() == 1800
    assert proto.runtime_config.version == "3.0"
    assert proto.pyspark_batch.main_python_file_uri.endswith("/main.py")


def test_target_is_validated():
    with pytest.raises(ValueError):
        b.Target(project="x", region="us-central1", bucket="my-bucket", service_account=TARGET.service_account)
    with pytest.raises(ValueError):
        b.Target(project="my-project-123", region="us-central1", bucket="my-bucket", service_account="someone@gmail.com")
    with pytest.raises(ValueError):
        b.labels("make", "Not A Label")


def test_code_version_follows_the_sources(tmp_path):
    copy = tmp_path / "spark"
    shutil.copytree(SPARK_DIR, copy, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "tests"))
    version = b.code_version(copy)
    assert re.fullmatch(r"[0-9a-f]{12}", version) and version == b.code_version(copy) == b.code_version(SPARK_DIR)
    (copy / "attribution" / "models.py").write_text((copy / "attribution" / "models.py").read_text() + "\n# changed\n")
    assert b.code_version(copy) != version
    names = [p.relative_to(copy).as_posix() for p in b.code_files(copy)]
    assert names[0] == "main.py" and "attribution/journeys.py" in names and all(n.endswith(".py") for n in names)


def test_package_zip_is_reproducible_and_importable(tmp_path):
    data = submit.package_zip(SPARK_DIR)
    assert data == submit.package_zip(SPARK_DIR)
    names = zipfile.ZipFile(BytesIO(data)).namelist()
    assert "attribution/__init__.py" in names and "attribution/job.py" in names and "main.py" not in names
    zpath = tmp_path / "attribution.zip"
    zpath.write_bytes(data)
    # Dataproc puts the zip on the path and runs main.py; do the same here, from an empty directory.
    code = f"import sys; sys.path.insert(0, {str(zpath)!r}); import attribution.job as j; print(j.__file__)"
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert "attribution.zip" in out.stdout


def test_batch_ids():
    run = "scheduled__2026-09-28T10:00:00+00:00"
    first = b.batch_id(run, 1, "0192a3b4-c5d6-7e8f-9012-3456789abcde")
    assert first == b.batch_id(run, 1, "0192a3b4-c5d6-7e8f-9012-3456789abcde"), "stable when rendered twice for one try"
    assert re.fullmatch(r"tagline-attr-20260928-[0-9a-f]{8}-t1-[0-9a-f]{6}", first)
    assert b.batch_id(run, 2, "0192a3b4-c5d6-7e8f-9012-3456789abcdf") != first, "a retry gets a new batch"
    # After the metadata database is wiped: same run id, same try number, a new task instance id.
    assert b.batch_id(run, 1, "0192a3b4-ffff-7e8f-9012-3456789abcde") != first
    assert b.batch_id("manual__2026-09-28T10:00:00+00:00", 1, "0192a3b4-c5d6-7e8f-9012-3456789abcde") != first
    assert b.batch_id(run, 1) != b.batch_id(run, 1), "no unique value: random"
    for value in (first, b.batch_id("a weird run id with no date!" * 5, 10)):
        assert 4 <= len(value) <= 63 and value == value.lower() and all(c.isalnum() or c == "-" for c in value)


def test_load_target_reads_env_file_and_environment(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nTAGLINE_GCP_PROJECT=my-project-123\nTAGLINE_GCP_REGION=us-central1\n"
        "TAGLINE_SPARK_BUCKET=gs://my-bucket/\nexport TAGLINE_SPARK_SERVICE_ACCOUNT='spark-sa@my-project-123.iam.gserviceaccount.com'\n"
    )
    assert submit.load_target(environ={}, env_file=env) == TARGET
    other = submit.load_target(environ={"TAGLINE_GCP_REGION": "europe-west1"}, env_file=env)
    assert other.region == "europe-west1"
    with pytest.raises(submit.ConfigError, match="TAGLINE_SPARK_BUCKET"):
        submit.load_target(environ={"TAGLINE_SPARK_BUCKET": ""}, env_file=env)
    with pytest.raises(submit.ConfigError, match="not a valid region"):
        submit.load_target(environ={"TAGLINE_GCP_REGION": "Iowa"}, env_file=env)


def test_usage_report_from_a_finished_batch():
    dataproc_v1 = pytest.importorskip("google.cloud.dataproc_v1")
    from datetime import UTC, datetime

    finished = dataproc_v1.Batch(
        name="projects/p/locations/us-central1/batches/tagline-attr-20260928-1f3a9c2e-t1-7c01d2",
        state=dataproc_v1.Batch.State.SUCCEEDED,
        create_time=datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC),
        state_time=datetime(2026, 9, 28, 10, 4, 0, tzinfo=UTC),
        state_history=[
            {"state": dataproc_v1.Batch.State.PENDING, "state_start_time": datetime(2026, 9, 28, 10, 0, 0, tzinfo=UTC)},
            {"state": dataproc_v1.Batch.State.RUNNING, "state_start_time": datetime(2026, 9, 28, 10, 1, 0, tzinfo=UTC)},
        ],
        runtime_config={"version": "3.0"},
        runtime_info={"approximate_usage": {"milli_dcu_seconds": 2_880_000, "shuffle_storage_gb_seconds": 180_000}},
        labels={"app": "tagline"},
    )
    u = submit.usage(finished)
    assert u["state"] == "SUCCEEDED" and u["wall_seconds"] == 240 and u["pending_seconds"] == 60 and u["running_seconds"] == 180
    assert u["dcu_hours"] == pytest.approx(0.8) and u["shuffle_gib_hours"] == pytest.approx(50)
    assert u["usd_estimate"] == pytest.approx(0.8 * 0.06 + 50 * 0.000054795)
    text = submit.format_usage(u)
    assert "0.8000 DCU-hours" in text and "wall 240 s" in text and "SUCCEEDED" in text
