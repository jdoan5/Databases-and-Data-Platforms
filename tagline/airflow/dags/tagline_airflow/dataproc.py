"""The Dataproc Serverless batch for the Stage 3 attribution job, and its batch id: both taken from
tagline/spark/tagline_spark/batch.py, the one definition `make spark-submit` uses too (runtime version,
code URIs, job arguments, service account, subnet, TTL, labels, Spark properties, the batch-id rule).

`tagline_spark` imports from /opt/tagline/spark (docker-compose mounts tagline/spark there read-only and
puts it on PYTHONPATH); it is standard library only, so the Airflow image needs nothing added. The code
version in the batch (its bucket folder and `code` label) is hashed from those mounted sources, so a DAG
run always submits exactly the code in the repo. `make spark-upload` (or `make spark-submit`) puts that
version in the bucket; until it is there the batch fails at once, naming the missing file.
"""

from __future__ import annotations

from typing import Any

from tagline_pipeline.config import Config
from tagline_spark import batch as spark_batch

from .settings import SparkSettings

# Cost guard: Dataproc terminates a batch that runs longer than this, whatever Airflow does.
BATCH_TTL_SECONDS = spark_batch.TTL_SECONDS
RUNTIME_VERSION = spark_batch.RUNTIME_VERSION


def target(cfg: Config, spark: SparkSettings) -> spark_batch.Target:
    return spark_batch.Target(
        project=cfg.project,
        region=spark.region,
        bucket=spark.bucket,
        service_account=spark.service_account,
        marts_dataset=cfg.marts_dataset,
    )


def batch_id(run_id: str, try_number: int | str, ti_id: object | None = None) -> str:
    """tagline_spark.batch.batch_id for a task try: `tagline-attr-<date>-<run hash>-t<try>-<ti.id hash>`.

    The DAG renders it as `attribution_batch_id(run_id, ti.try_number, ti.id)`. Airflow 3 gives every try a
    new task instance id (a UUID7, drawn in TaskInstance.prepare_db_for_next_try), so the id is new for a
    retry, for another run, and after the metadata database is wiped (when run ids and try numbers repeat
    but Dataproc still holds the old batches), and the same if rendered twice within one try."""
    return spark_batch.batch_id(run_id, try_number, ti_id)


def attribution_batch(cfg: Config, spark: SparkSettings) -> dict[str, Any]:
    """The Batch resource (as a dict; the operator converts it) for the attribution job."""
    return spark_batch.batch(target(cfg, spark), orchestrator="airflow")
