"""The Dataproc Serverless batch for the attribution job: the one definition that `make spark-submit`
(tagline_spark/submit.py) and the Airflow DAG (airflow/dags/tagline_airflow/dataproc.py) both submit.
Standard library only, because the Airflow image imports it and has no pyspark.

**Runtime 3.0** (Serverless for Apache Spark): Java 21, Scala 2.13, Python 3.12, spark-bigquery connector
0.44.0 and google-cloud-bigquery 3.38 preinstalled. Google's 3.0 page lists Spark 4.0.1 for its newest
subminor (3.0.14, 2026-08-25), but the batches run on 2026-09-28 reported `spark.version` 4.0.2, so
pyspark is pinned to what the runtime actually runs, 4.0.2.
Chosen over 2.3 LTS (Spark 3.5.3, Java 17, Python 3.11) and 2.2 LTS (the default):
Spark 4.0 supports Java 17 and 21, so the Java 21 installed locally runs the unit tests on the same
JVM as the runtime, while Spark 3.5 supports Java 8/11/17 only (neither local JDK, 21 or 26). The cost:
3.0 is not LTS and Google's end of support is 2027-01-31 (the runtime keeps working until 2029-01-31);
moving to its successor means re-pinning pyspark here and in pyproject.toml, which a test ties
together. Runtime 3.0 also does not use a staging bucket and does not offer Lightning Engine or Native
Query Execution (2.x premium-tier features Stage 4 would have to switch runtimes to measure).
Dataproc takes only major.minor, so a new 3.0.x may bring a new Spark patch release (3.0.12 moved
4.0.0 -> 4.0.1, and a later build 4.0.2); the job logs the runtime's Spark version against SPARK_VERSION and
puts both in its summary line.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SPARK_DIR = Path(__file__).resolve().parents[1]

RUNTIME_VERSION = "3.0"
SPARK_VERSION = "4.0.2"  # what runtime 3.0 reported; pyproject.toml pins pyspark to it, tests/test_batch.py checks both
RUNTIME_PYTHON = "3.12"
RUNTIME_JAVA = "21"

# Cost guard: Dataproc terminates the batch after this, whatever submitted it.
TTL_SECONDS = 30 * 60
SUBNETWORK = "default"  # the default network's us-central1 subnet, with Private Google Access

# The smallest shape Serverless accepts: a 4-core driver, two 4-core executors (the minimum), no scaling
# beyond them (12 DCUs), and the minimum 250 GiB of disk (shuffle storage) per node instead of the default
# 100 GiB per core. The executor settings only matter if the job ever runs with executors: with SPARK_MASTER
# below it runs in the driver alone (Stage 4, experiment 6).
SPARK_PROPERTIES = {
    "spark.driver.cores": "4",
    # The smallest driver memory Serverless accepts, 1 GiB per core in all (the API refuses less): the node drops
    # from 24 GiB to about 5.75 GiB and the rate from 4.8 to about 2.98 DCUs (Stage 4, experiment 9). The job's
    # data is a few MB; the default was 16000m plus PySpark's 40% overhead.
    "spark.driver.memory": "2867m",
    "spark.driver.memoryOverhead": "1229m",
    "spark.executor.cores": "4",
    "spark.executor.instances": "2",
    "spark.dynamicAllocation.maxExecutors": "2",
    "spark.dataproc.driver.disk.size": "250g",
    "spark.dataproc.executor.disk.size": "250g",
    "spark.sql.session.timeZone": "UTC",
    # Dataproc's spark-defaults set 1000 shuffle partitions, and AQE does not coalesce the output of a cached plan,
    # so the job's three cached DataFrames kept 1000 partitions and every count and check over them ran 1000 tasks
    # for about 12 k touches. One partition per task thread, so as many as the driver's cores (Stage 4, experiment 7).
    "spark.sql.shuffle.partitions": "4",
}

# Where the job runs its tasks, passed as --spark-master (the job sets it on its session builder). Runtime 3.0
# starts this batch in Spark local mode with ONE task thread: Dataproc appends `spark.master=local` to the batch's
# Spark config (after the image's `spark.master=dataproc`), whatever the executor properties say, and the Batch API
# refuses spark.master as a property for anything but `local`. `local[N]` with N = the driver's cores keeps the
# single node (no executor start-up, no executor nodes to pay for) and uses all of its cores. Stage 4 measured
# `local` (the runtime's), `local[4]` and `dataproc` (executors); spark/README.md has the numbers.
SPARK_MASTER = f"local[{SPARK_PROPERTIES['spark.driver.cores']}]"

# Code in the bucket, one folder per version of the sources, so a batch always runs exactly the code its
# version names and an upload never changes what an earlier batch ran.
CODE_PREFIX = "code/attribution"
MAIN_FILE = "main.py"
PACKAGE = "attribution"
PACKAGE_ZIP = "attribution.zip"

LABELS = {"app": "tagline", "stage": "3", "job": "attribution"}
BATCH_ID_PREFIX = "tagline-attr"

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_REGION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
_SERVICE_ACCOUNT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_BATCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,61}[a-z0-9]$")
_LABEL_RE = re.compile(r"^[a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class Target:
    """Where the batch runs and what it reads and writes. Values come from tagline/.env."""

    project: str
    region: str
    bucket: str  # without gs://
    service_account: str
    marts_dataset: str = "tagline_marts"

    def __post_init__(self) -> None:
        for what, value, pattern in (
            ("project id", self.project, _PROJECT_RE),
            ("region", self.region, _REGION_RE),
            ("bucket name", self.bucket, _BUCKET_RE),
            ("service account email", self.service_account, _SERVICE_ACCOUNT_RE),
            ("dataset name", self.marts_dataset, _DATASET_RE),
        ):
            if not pattern.fullmatch(value or ""):
                raise ValueError(f"{value!r} is not a valid {what}")

    @property
    def parent(self) -> str:
        return f"projects/{self.project}/locations/{self.region}"


# -- the code ---------------------------------------------------------------------------------------


def code_files(spark_dir: Path = SPARK_DIR) -> list[Path]:
    """What goes to Dataproc: main.py and the attribution package's modules."""
    package = sorted(p for p in (spark_dir / PACKAGE).rglob("*.py") if "__pycache__" not in p.parts)
    return [spark_dir / MAIN_FILE, *package]


def code_version(spark_dir: Path = SPARK_DIR) -> str:
    """12 hex characters of a SHA-256 over those files' paths and bytes: the bucket folder and the `code` label."""
    digest = hashlib.sha256()
    for path in code_files(spark_dir):
        digest.update(path.relative_to(spark_dir).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def code_uris(bucket: str, version: str) -> tuple[str, str]:
    """(main file URI, attribution.zip URI) for a code version."""
    base = f"gs://{bucket}/{CODE_PREFIX}/{version}"
    return f"{base}/{MAIN_FILE}", f"{base}/{PACKAGE_ZIP}"


# -- the batch --------------------------------------------------------------------------------------


def job_args(target: Target) -> list[str]:
    return [
        f"--project={target.project}",
        f"--marts-dataset={target.marts_dataset}",
        f"--temp-bucket={target.bucket}",
        f"--expected-spark-version={SPARK_VERSION}",
        f"--spark-master={SPARK_MASTER}",
    ]


def labels(orchestrator: str, version: str) -> dict[str, str]:
    values = {**LABELS, "orchestrator": orchestrator, "code": version}
    bad = [f"{k}={v}" for k, v in values.items() if not (_LABEL_RE.fullmatch(k) and _LABEL_RE.fullmatch(v))]
    if bad:
        raise ValueError(f"invalid label(s): {', '.join(bad)}")
    return values


def batch(target: Target, orchestrator: str, version: str | None = None) -> dict[str, Any]:
    """The Batch resource as a dict: what google.cloud.dataproc_v1.Batch(...) and DataprocCreateBatchOperator take."""
    version = version or code_version()
    main_uri, zip_uri = code_uris(target.bucket, version)
    return {
        "pyspark_batch": {
            "main_python_file_uri": main_uri,
            "python_file_uris": [zip_uri],
            "args": job_args(target),
        },
        "runtime_config": {"version": RUNTIME_VERSION, "properties": dict(SPARK_PROPERTIES)},
        "environment_config": {
            "execution_config": {
                "service_account": target.service_account,
                "subnetwork_uri": SUBNETWORK,
                "ttl": {"seconds": TTL_SECONDS},
            },
        },
        "labels": labels(orchestrator, version),
    }


def batch_id(run_id: str, attempt: int | str, unique: object | None = None) -> str:
    """A batch id unique to one attempt: `tagline-attr-<date>-<run hash>-t<attempt>-<attempt hash>`.

    Dataproc refuses a second batch with an existing id, and Airflow's operator then attaches to the
    existing batch and reports its result, so an id must never come round again: not for a retry (it
    would re-attach to the batch that just failed), not for another run, and not after Airflow's
    metadata database is wiped, when run ids and try numbers start again and Dataproc still keeps the old
    batches. `unique` is what makes the last case safe: Airflow passes the task instance's id, a UUID7
    that Airflow 3 draws afresh for every try (TaskInstance.prepare_db_for_next_try), so the id is stable
    when rendered twice for one try and new for anything else; without it a random value is used.

    run ids (`scheduled__2026-09-28T10:00:00+00:00`) contain characters batch ids may not, so the run is a
    hash after its date: tagline-attr-20260928-1f3a9c2e-t1-7c01d2."""
    day = re.search(r"(\d{4})-(\d{2})-(\d{2})", run_id)
    run_hash = hashlib.sha256(run_id.encode()).hexdigest()[:8]
    attempt_hash = hashlib.sha256(str(unique if unique is not None else uuid.uuid4()).encode()).hexdigest()[:6]
    parts = [BATCH_ID_PREFIX, "".join(day.groups()) if day else None, run_hash, f"t{int(attempt)}", attempt_hash]
    value = "-".join(p for p in parts if p)
    if not _BATCH_ID_RE.fullmatch(value):
        raise ValueError(f"batch id {value!r} is not valid (4-63 characters: a-z, 0-9, -)")
    return value
