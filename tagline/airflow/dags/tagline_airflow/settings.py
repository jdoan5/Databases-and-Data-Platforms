"""Configuration for the DAG.

Everything comes from tagline/.env, which docker-compose passes into the containers as
environment variables (`env_file: ../.env`), so nothing project-specific is written in the DAG:

* the Stage 2 settings (project, GA4 export dataset, cost guard) through the Stage 2 package's own
  `load_config`, so the DAG and `make build` can never disagree about them;
* the Stage 3 Spark settings: TAGLINE_GCP_REGION, TAGLINE_SPARK_BUCKET,
  TAGLINE_SPARK_SERVICE_ACCOUNT.

A missing or malformed value fails the DAG import with a message naming the variable, which
shows as an import error in the UI and in `make airflow-check`.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from tagline_pipeline.config import Config, ConfigError, load_config

# The Airflow connection every Google operator uses. docker-compose defines it from the
# environment (AIRFLOW_CONN_GOOGLE_CLOUD_DEFAULT) with no key: the hooks fall back to
# application-default credentials. On Cloud Composer the same id exists and uses the
# environment's service account.
GCP_CONN_ID = "google_cloud_default"

_REGION_RE = re.compile(r"^[a-z]+-[a-z]+[0-9]+$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]$")
_SERVICE_ACCOUNT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$")


@dataclass(frozen=True)
class SparkSettings:
    region: str  # Dataproc region, e.g. us-central1
    bucket: str  # bucket name, without gs://
    service_account: str  # the batch runs as this service account

    @property
    def bucket_uri(self) -> str:
        return f"gs://{self.bucket}"


def pipeline_config(environ: Mapping[str, str] | None = None) -> Config:
    """The Stage 2 configuration (TAGLINE_GCP_PROJECT, TAGLINE_GA4_DATASET, TAGLINE_MAX_BYTES_BILLED, ...)."""
    return load_config(environ=environ)


def spark_settings(environ: Mapping[str, str] | None = None) -> SparkSettings:
    env = os.environ if environ is None else environ

    def required(key: str, pattern: re.Pattern[str], what: str) -> str:
        value = (env.get(key) or "").strip()
        if key == "TAGLINE_SPARK_BUCKET":
            value = value.removeprefix("gs://").rstrip("/")
        if not value:
            raise ConfigError(f"{key} is not set (tagline/.env; see .env.example)")
        if not pattern.fullmatch(value):
            raise ConfigError(f"{key} {value!r} is not a valid {what}")
        return value

    return SparkSettings(
        region=required("TAGLINE_GCP_REGION", _REGION_RE, "region"),
        bucket=required("TAGLINE_SPARK_BUCKET", _BUCKET_RE, "bucket name"),
        service_account=required("TAGLINE_SPARK_SERVICE_ACCOUNT", _SERVICE_ACCOUNT_RE, "service account email"),
    )
