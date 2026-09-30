"""The attribution job's entrypoint, and the only module that talks to BigQuery.

Read fct_orders and fct_sessions, build the journeys, attribute them under the six models, check the
result, overwrite fct_attribution and mart_attribution_daily, then set their descriptions and labels.

Runs on Serverless for Apache Spark (main.py calls main()). The runtime provides pyspark, the
spark-bigquery connector and google-cloud-bigquery; nothing else is needed. Reads go through the
BigQuery Storage Read API with only the needed columns and the order filter pushed down; writes are
load jobs from Parquet files the connector stages in the bucket and deletes afterwards (the indirect
method, the one that can create a partitioned table). The job runs no query jobs, so there is no
maximum_bytes_billed to set: Storage Read API bytes and load jobs are what it uses.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from typing import Any

from . import journeys, models, tables

SUMMARY_MARKER = "ATTRIBUTION_SUMMARY"
_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
# Where Spark runs its tasks: `local[N]` / `local[*]` (N threads in the driver JVM) or `dataproc` (runtime 3.0's
# own cluster manager, which asks Dataproc for executor nodes). Without the argument the job keeps the master the
# runtime gives it; on runtime 3.0 that is `local`, one thread (Stage 4, experiment 6).
_MASTER_RE = re.compile(r"^(local(\[([1-9][0-9]{0,2}|\*)\])?|dataproc)$")


class AttributionProblem(RuntimeError):
    """The attribution failed its own checks; nothing was written."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="attribution", description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True, help="project that holds the datasets")
    parser.add_argument("--marts-dataset", default="tagline_marts", help="reads fct_orders and fct_sessions, writes the outputs")
    parser.add_argument("--temp-bucket", required=True, help="bucket for the connector's staged Parquet files (no gs://)")
    parser.add_argument("--expected-spark-version", default=None, help="logged against the runtime's Spark version")
    parser.add_argument("--no-write", action="store_true", help="compute and check, write nothing")
    parser.add_argument("--spark-master", default=None, help="local[N], local[*], local or dataproc; default: the runtime's")
    args = parser.parse_args(argv)
    if not _PROJECT_RE.fullmatch(args.project):
        parser.error(f"--project {args.project!r} is not a valid project id")
    if not _NAME_RE.fullmatch(args.marts_dataset):
        parser.error(f"--marts-dataset {args.marts_dataset!r} is not a valid dataset name")
    args.temp_bucket = args.temp_bucket.removeprefix("gs://").rstrip("/")
    if not _BUCKET_RE.fullmatch(args.temp_bucket):
        parser.error(f"--temp-bucket {args.temp_bucket!r} is not a valid bucket name")
    if args.spark_master is not None and not _MASTER_RE.fullmatch(args.spark_master):
        parser.error(f"--spark-master {args.spark_master!r} is not local, local[N], local[*] or dataproc")
    return args


def table_id(args: argparse.Namespace, name: str) -> str:
    return f"{args.project}.{args.marts_dataset}.{name}"


def read_options(table: str) -> dict[str, str]:
    return {"table": table, "readDataFormat": "ARROW"}


def write_options(table: str, temp_bucket: str, step: str) -> dict[str, str]:
    """Overwrite (a load job with WRITE_TRUNCATE: the whole table, schema included), partitioned by order date,
    load jobs labelled like Stage 2's jobs so Stage 4 finds them in INFORMATION_SCHEMA.JOBS."""
    options = {
        "table": table,
        "writeMethod": "indirect",
        "temporaryGcsBucket": temp_bucket,
        "intermediateFormat": "parquet",
        "partitionField": tables.PARTITION_FIELD,
        "partitionType": "DAY",
        "createDisposition": "CREATE_IF_NEEDED",
    }
    for key, value in {**tables.LABELS, "kind": "spark", "step": step}.items():
        options[f"bigQueryJobLabel.{key}"] = value
    return options


def read_table(spark, table: str, columns: tuple[str, ...], where=None):
    df = spark.read.format("bigquery").options(**read_options(table)).load().select(*columns)
    return df.where(where) if where is not None else df


# Output files per table. Both tables are small (73,980 and 5,532 rows on the GA4 sample), and the connector stages
# one Parquet file per partition before its load job. Left alone, the cached DataFrame's partitions became 987
# files (10.6 MB) for fct_attribution in the first real run (tagline-attr-20260928-e580908d-t1-8f7f6a), committed
# to Cloud Storage one by one for 15 minutes, and the mart's staging ran the batch into its 30-minute TTL.
WRITE_PARTITIONS = 1


def write_table(df, table: str, temp_bucket: str, step: str) -> None:
    (
        df.coalesce(WRITE_PARTITIONS)
        .write.format("bigquery")
        .mode("overwrite")
        .options(**write_options(table, temp_bucket, step))
        .save()
    )


def documented_schema(schema: list[dict[str, Any]], columns: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    """The table's schema (BigQuery API form) with every column's description; refuses a column without one,
    or a documented column the table lacks, as Stage 2's runner does."""
    docs = dict(columns)
    names = [f["name"] for f in schema]
    missing = [n for n in names if n not in docs]
    extra = [n for n in docs if n not in names]
    if missing or extra:
        raise AttributionProblem(f"undocumented columns {missing}, documented but absent {extra}")
    return [{**f, "description": docs[f["name"]]} for f in schema]


def apply_docs(project: str, table: str, description: str, columns: tuple[tuple[str, str], ...]) -> None:
    from google.cloud import bigquery  # provided by the runtime; imported here so tests need no client

    client = bigquery.Client(project=project)
    t = client.get_table(table)
    t.schema = [bigquery.SchemaField.from_api_repr(f) for f in documented_schema([f.to_api_repr() for f in t.schema], columns)]
    t.description = description
    t.labels = {**(t.labels or {}), **tables.LABELS}
    client.update_table(t, ["schema", "description", "labels"])


def run(spark, args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    orders = read_table(spark, table_id(args, "fct_orders"), journeys.ORDER_COLUMNS, journeys.attributable_filter())
    sessions = read_table(spark, table_id(args, "fct_sessions"), journeys.SESSION_COLUMNS)

    touches = journeys.build_touches(orders, sessions).cache()
    attribution = models.attribute(touches).select(*tables.column_names(tables.FCT_ATTRIBUTION)).cache()
    mart = models.daily_mart(attribution).select(*tables.column_names(tables.MART_ATTRIBUTION_DAILY)).cache()

    found = models.problems(attribution, touches)
    computed = time.monotonic()
    sc = spark.sparkContext
    summary: dict[str, Any] = {
        "spark_version": spark.version,
        "spark_master": sc.master,
        "app_id": sc.applicationId,
        "default_parallelism": sc.defaultParallelism,
        "expected_spark_version": args.expected_spark_version,
        "orders": touches.select("source", "order_id").distinct().count(),
        "touches": touches.count(),
        "fct_attribution_rows": attribution.count(),
        "mart_attribution_daily_rows": mart.count(),
        "orders_with_incomplete_lookback": touches.where("NOT lookback_complete").select("source", "order_id").distinct().count(),
        "orders_last_touch_not_order_session": touches.where("touch_position = touch_count AND NOT is_order_session").count(),
        "problems": found,
        "compute_seconds": round(computed - started, 1),
    }
    summary["models"] = {
        r["model"]: {"orders": round(r["orders"], 6), "revenue_usd": round(r["revenue"], 2)}
        for r in attribution.groupBy("model").agg({"weight": "sum", "attributed_revenue_usd": "sum"})
        .withColumnRenamed("sum(weight)", "orders").withColumnRenamed("sum(attributed_revenue_usd)", "revenue")
        .collect()
    }
    summarized = time.monotonic()
    # The counts above are Spark jobs too: timed apart, so the write path can be measured on its own (Stage 4).
    summary["summary_seconds"] = round(summarized - computed, 1)
    if args.expected_spark_version and spark.version != args.expected_spark_version:
        print(f"WARNING: runtime Spark {spark.version}, but pyspark is pinned to {args.expected_spark_version}", file=sys.stderr)
    if found:
        print(f"{SUMMARY_MARKER} {json.dumps(summary, sort_keys=True)}")
        raise AttributionProblem("; ".join(found))

    if not args.no_write:
        for name, df in ((tables.FCT_ATTRIBUTION, attribution), (tables.MART_ATTRIBUTION_DAILY, mart)):
            write_table(df, table_id(args, name), args.temp_bucket, step=name)
            description, columns = tables.TABLES[name]
            apply_docs(args.project, table_id(args, name), description, columns)
    summary["written"] = not args.no_write
    ended = time.monotonic()
    # write_seconds keeps its Stage 3 meaning (everything after the checks: the summary's counts, then the writes)
    # so it compares with earlier runs; write_only_seconds is the two writes and their table docs alone.
    summary["write_seconds"] = round(ended - computed, 1)
    summary["write_only_seconds"] = round(ended - summarized, 1)
    print(f"{SUMMARY_MARKER} {json.dumps(summary, sort_keys=True)}")
    return summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from pyspark.sql import SparkSession

    builder = SparkSession.builder.appName("tagline-attribution").config("spark.sql.session.timeZone", "UTC")
    if args.spark_master:
        # The Batch API refuses spark.master as a property on runtime 3.0 (only `local` is accepted), so the batch
        # passes it as an argument and the job sets it before the session exists.
        builder = builder.master(args.spark_master)
    spark = builder.getOrCreate()
    try:
        run(spark, args)
    except AttributionProblem as e:
        print(f"attribution failed its checks, nothing written: {e}", file=sys.stderr)
        return 1
    finally:
        spark.stop()
    return 0
