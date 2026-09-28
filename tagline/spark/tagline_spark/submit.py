"""Upload the attribution job, run it on Dataproc Serverless, report what it used. Run from tagline/spark:

    python -m tagline_spark.submit upload            # code to gs://<bucket>/code/attribution/<version>/
    python -m tagline_spark.submit submit            # upload, create the batch, wait, report (make spark-submit)
    python -m tagline_spark.submit submit --no-wait  # upload, create the batch, print its id
    python -m tagline_spark.submit status [BATCH_ID] # one batch (default: the latest), and any still running
    python -m tagline_spark.submit cancel BATCH_ID

The batch is tagline_spark.batch.batch(), exactly what the Airflow DAG submits. Configuration comes from
tagline/.env (TAGLINE_GCP_PROJECT, TAGLINE_GCP_REGION, TAGLINE_SPARK_BUCKET, TAGLINE_SPARK_SERVICE_ACCOUNT);
TAGLINE_* variables in the environment win. Credentials: application-default credentials. Ctrl-C while
waiting cancels the batch.

Exit codes: 0 succeeded, 1 the batch failed or was cancelled, 2 configuration error.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import batch as spark_batch

ENV_FILE = spark_batch.SPARK_DIR.parent / ".env"

# Google's list prices for Serverless for Apache Spark in us-central1, standard tier, read 2026-09-28 from
# https://cloud.google.com/dataproc-serverless/pricing: billed per second with a 1-minute minimum.
DCU_USD_PER_HOUR = 0.06
SHUFFLE_USD_PER_GIB_HOUR = 0.000054795

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


class ConfigError(ValueError):
    pass


# -- configuration ------------------------------------------------------------------------------------


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=value lines; blank lines and # comments ignored; optional quotes and `export` (as Stage 2 reads it)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key.strip()] = value
    return values


def load_target(environ: Mapping[str, str] | None = None, env_file: Path | None = ENV_FILE) -> spark_batch.Target:
    values: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        values.update(parse_env_file(env_file.read_text(encoding="utf-8")))
    values.update({k: v for k, v in (os.environ if environ is None else environ).items() if k.startswith("TAGLINE_")})
    keys = ("TAGLINE_GCP_PROJECT", "TAGLINE_GCP_REGION", "TAGLINE_SPARK_BUCKET", "TAGLINE_SPARK_SERVICE_ACCOUNT")
    missing = [k for k in keys if not values.get(k, "").strip()]
    if missing:
        raise ConfigError(f"{', '.join(missing)} not set (tagline/.env; see tagline/.env.example)")
    try:
        return spark_batch.Target(
            project=values["TAGLINE_GCP_PROJECT"].strip(),
            region=values["TAGLINE_GCP_REGION"].strip(),
            bucket=values["TAGLINE_SPARK_BUCKET"].strip().removeprefix("gs://").rstrip("/"),
            service_account=values["TAGLINE_SPARK_SERVICE_ACCOUNT"].strip(),
        )
    except ValueError as e:
        raise ConfigError(f"tagline/.env: {e}") from None


# -- code ---------------------------------------------------------------------------------------------


def package_zip(spark_dir: Path = spark_batch.SPARK_DIR) -> bytes:
    """attribution.zip: the package's modules under attribution/, byte-for-byte reproducible (sorted entries,
    fixed timestamps)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in spark_batch.code_files(spark_dir):
            if path.name == spark_batch.MAIN_FILE and path.parent == spark_dir:
                continue
            info = zipfile.ZipInfo(path.relative_to(spark_dir).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return buffer.getvalue()


def upload(target: spark_batch.Target, spark_dir: Path = spark_batch.SPARK_DIR) -> str:
    """Upload main.py and attribution.zip for the current sources' version, unless already there. Returns the version."""
    from google.cloud import storage

    version = spark_batch.code_version(spark_dir)
    bucket = storage.Client(project=target.project).bucket(target.bucket)
    main_uri, zip_uri = spark_batch.code_uris(target.bucket, version)
    for uri, data, content_type in (
        (main_uri, (spark_dir / spark_batch.MAIN_FILE).read_bytes(), "text/x-python"),
        (zip_uri, package_zip(spark_dir), "application/zip"),
    ):
        blob = bucket.blob(uri.removeprefix(f"gs://{target.bucket}/"))
        if blob.exists():
            print(f"  {uri} (already uploaded)")
            continue
        blob.metadata = {"code_version": version}
        blob.upload_from_string(data, content_type=content_type)
        print(f"  {uri} ({len(data):,} bytes)")
    return version


# -- batches ------------------------------------------------------------------------------------------


def _client(target: spark_batch.Target):
    from google.cloud import dataproc_v1

    return dataproc_v1.BatchControllerClient(client_options={"api_endpoint": f"{target.region}-dataproc.googleapis.com:443"})


def _state(b) -> str:
    return b.state.name if hasattr(b.state, "name") else str(b.state)


def usage(b) -> dict[str, Any]:
    """Wall time, DCU and shuffle usage and a list-price estimate for a finished Batch (dataproc_v1.Batch)."""
    created = b.create_time
    ended = b.state_time if _state(b) in TERMINAL else None
    running = next((h.state_start_time for h in b.state_history if h.state.name == "RUNNING"), None)
    if running is None and _state(b) == "RUNNING":
        running = b.state_time
    approx = b.runtime_info.approximate_usage
    dcu_hours = approx.milli_dcu_seconds / 1000 / 3600
    shuffle_gib_hours = approx.shuffle_storage_gb_seconds / 3600
    return {
        "batch_id": b.name.rsplit("/", 1)[-1],
        "project": b.name.split("/")[1] if b.name.startswith("projects/") else "",
        "state": _state(b),
        "state_message": b.state_message,
        "runtime_version": b.runtime_config.version,
        "created": created,
        "wall_seconds": (ended - created).total_seconds() if ended else None,
        "pending_seconds": (running - created).total_seconds() if running else None,
        "running_seconds": (ended - running).total_seconds() if ended and running else None,
        "milli_dcu_seconds": approx.milli_dcu_seconds,
        "shuffle_storage_gb_seconds": approx.shuffle_storage_gb_seconds,
        "dcu_hours": dcu_hours,
        "shuffle_gib_hours": shuffle_gib_hours,
        "usd_estimate": dcu_hours * DCU_USD_PER_HOUR + shuffle_gib_hours * SHUFFLE_USD_PER_GIB_HOUR,
        "output_uri": b.runtime_info.output_uri,
        "labels": dict(b.labels),
    }


def format_usage(u: dict[str, Any]) -> str:
    def secs(value: float | None) -> str:
        return "-" if value is None else f"{value:.0f} s"

    lines = [
        f"batch    {u['batch_id']}  {u['state']}" + (f"  ({u['state_message']})" if u["state_message"] else ""),
        f"runtime  {u['runtime_version']}   labels {', '.join(f'{k}={v}' for k, v in sorted(u['labels'].items()))}",
        f"time     created {u['created']:%Y-%m-%d %H:%M:%S} UTC; wall {secs(u['wall_seconds'])} "
        f"(pending {secs(u['pending_seconds'])}, running {secs(u['running_seconds'])})",
    ]
    if u["milli_dcu_seconds"]:
        lines.append(
            f"usage    {u['dcu_hours']:.4f} DCU-hours (milliDcuSeconds {u['milli_dcu_seconds']:,}), "
            f"{u['shuffle_gib_hours']:.4f} GB-hours shuffle storage (shuffleStorageGbSeconds {u['shuffle_storage_gb_seconds']:,})"
        )
        lines.append(
            f"cost     about ${u['usd_estimate']:.4f} at list price (standard tier, us-central1: ${DCU_USD_PER_HOUR}/DCU-hour, "
            f"${SHUFFLE_USD_PER_GIB_HOUR:.9f}/GB-hour shuffle; per second, 1-minute minimum)"
        )
    elif u["state"] in TERMINAL:
        lines.append("usage    not reported yet (Dataproc fills runtimeInfo.approximateUsage shortly after the end)")
    if u["output_uri"]:
        lines.append(f"output   {u['output_uri']}")
    else:  # runtime 3.0 has no staging bucket: the driver's output is in Cloud Logging
        lines.append(
            f"output   gcloud logging read 'resource.type=\"cloud_dataproc_batch\" AND resource.labels.batch_id=\"{u['batch_id']}\" "
            f"AND logName:\"output\" AND textPayload:\"ATTRIBUTION_SUMMARY\"' --project={u['project']} --format='value(textPayload)'"
        )
    return "\n".join(lines)


def wait(client, name: str, poll_seconds: float = 10, usage_grace_seconds: float = 180):
    """Poll until the batch ends, printing each state change, then until its usage is reported (bounded)."""
    started, last = time.monotonic(), None
    while True:
        b = client.get_batch(name=name)
        state = _state(b)
        if state != last:
            print(f"  {time.monotonic() - started:6.0f} s  {state}", flush=True)
            last = state
        if state in TERMINAL:
            break
        time.sleep(poll_seconds)
    deadline = time.monotonic() + usage_grace_seconds
    while not b.runtime_info.approximate_usage.milli_dcu_seconds and time.monotonic() < deadline:
        time.sleep(poll_seconds)
        b = client.get_batch(name=name)
    return b


def cancel(client, b) -> None:
    if _state(b) in TERMINAL:
        print(f"{b.name.rsplit('/', 1)[-1]} is already {_state(b)}")
        return
    client.cancel_operation(request={"name": b.operation})
    print(f"cancel requested for {b.name.rsplit('/', 1)[-1]}")


def submit(target: spark_batch.Target, wait_for_end: bool = True) -> int:
    print(f"uploading the attribution job to gs://{target.bucket}/{spark_batch.CODE_PREFIX}/")
    version = upload(target)
    body = spark_batch.batch(target, orchestrator="make", version=version)
    batch_id = spark_batch.batch_id(f"make__{datetime.now(UTC):%Y-%m-%dT%H:%M:%S}", 1)
    client = _client(target)
    client.create_batch(request={"parent": target.parent, "batch": body, "batch_id": batch_id})
    name = f"{target.parent}/batches/{batch_id}"
    print(
        f"batch {batch_id}: runtime {spark_batch.RUNTIME_VERSION}, TTL {spark_batch.TTL_SECONDS // 60} min, "
        f"code {version}, as {target.service_account}"
    )
    if not wait_for_end:
        print(f"not waiting; make spark-status BATCH={batch_id}")
        return 0
    try:
        b = wait(client, name)
    except KeyboardInterrupt:
        cancel(client, client.get_batch(name=name))
        raise
    print(format_usage(usage(b)))
    return 0 if _state(b) == "SUCCEEDED" else 1


def status(target: spark_batch.Target, batch_id: str | None = None) -> int:
    client = _client(target)
    if batch_id:
        b = client.get_batch(name=f"{target.parent}/batches/{batch_id}")
    else:
        latest = list(
            client.list_batches(
                request={
                    "parent": target.parent,
                    "filter": "labels.app = tagline AND labels.job = attribution",
                    "order_by": "create_time desc",
                    "page_size": 1,
                }
            )
        )
        if not latest:
            print("no attribution batches yet")
            return 0
        b = latest[0]
    print(format_usage(usage(b)))
    running = running_batches(client, target)
    print(f"running  {', '.join(running) if running else 'no tagline batch is pending or running'}")
    return 0


def running_batches(client, target: spark_batch.Target) -> list[str]:
    """Tagline batches (label app=tagline) still PENDING or RUNNING. One query per state: Dataproc's filter
    takes no list of states, and an OR of states has been seen to hang."""
    found = []
    for state in ("PENDING", "RUNNING"):
        for x in client.list_batches(request={"parent": target.parent, "filter": f"state = {state}"}):
            if x.labels.get("app") == "tagline":
                found.append(f"{x.name.rsplit('/', 1)[-1]} ({state})")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tagline_spark.submit", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("upload", help="upload the code for the current sources")
    p_submit = sub.add_parser("submit", help="upload, run the batch, report")
    p_submit.add_argument("--no-wait", action="store_true")
    p_status = sub.add_parser("status", help="report a batch (default: the latest attribution batch)")
    p_status.add_argument("batch_id", nargs="?")
    p_cancel = sub.add_parser("cancel", help="cancel a batch")
    p_cancel.add_argument("batch_id")
    args = parser.parse_args(argv)
    try:
        target = load_target()
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    if args.command == "upload":
        print(f"code version {upload(target)}")
        return 0
    if args.command == "submit":
        return submit(target, wait_for_end=not args.no_wait)
    if args.command == "status":
        return status(target, args.batch_id or None)
    client = _client(target)
    cancel(client, client.get_batch(name=f"{target.parent}/batches/{args.batch_id}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
