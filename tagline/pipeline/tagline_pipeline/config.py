"""Configuration: tagline/.env, overridden by the process environment.

The project id lives in tagline/.env (gitignored) so it never lands in a commit.
Everything that ends up inside SQL text is validated here, because the models are
plain SQL files with names substituted in: a dataset name is checked against
BigQuery's rules before it can reach a query.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parents[1]
TAGLINE_DIR = PIPELINE_DIR.parent
ENV_FILE = TAGLINE_DIR / ".env"
SQL_DIR = PIPELINE_DIR / "sql"
PRODUCTS_JSON = TAGLINE_DIR / "site" / "src" / "catalog" / "products.json"

LOCATION = "US"
RAW_DATASET = "tagline_raw"
STAGING_DATASET = "tagline_staging"
MARTS_DATASET = "tagline_marts"

# The public sample: Google Merchandise Store, obfuscated, 2020-11-01 to 2021-01-31.
SAMPLE_TABLE = "bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*"
SAMPLE_START = "20201101"
SAMPLE_END = "20210131"

DEFAULT_MAX_BYTES_BILLED = 10 * 1000**3  # 10 GB

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_]{1,200}$")


class ConfigError(ValueError):
    """The configuration cannot be used; the message says which value and why."""


def parse_env_file(text: str) -> dict[str, str]:
    """KEY=value lines; blank lines and # comments ignored; optional quotes and `export`."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError(f".env line {number}: expected KEY=value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


@dataclass(frozen=True)
class Config:
    project: str
    ga4_dataset: str | None = None
    ga4_project: str | None = None
    ga4_table_prefix: str = "events_"
    max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED
    sample_start: str = SAMPLE_START
    sample_end: str = SAMPLE_END
    location: str = LOCATION
    raw_dataset: str = RAW_DATASET
    staging_dataset: str = STAGING_DATASET
    marts_dataset: str = MARTS_DATASET
    # Stage 5: where alerts are POSTed (Slack / Discord / Teams incoming webhook). A secret: never printed.
    alert_webhook_url: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not _PROJECT_RE.fullmatch(self.project):
            raise ConfigError(f"TAGLINE_GCP_PROJECT {self.project!r} is not a valid project id")
        if self.ga4_project is not None and not _PROJECT_RE.fullmatch(self.ga4_project):
            raise ConfigError(f"TAGLINE_GA4_PROJECT {self.ga4_project!r} is not a valid project id")
        if self.ga4_dataset is not None and not _DATASET_RE.fullmatch(self.ga4_dataset):
            raise ConfigError(f"TAGLINE_GA4_DATASET {self.ga4_dataset!r} is not a valid dataset name")
        if not _PREFIX_RE.fullmatch(self.ga4_table_prefix):
            raise ConfigError(f"GA4 table prefix {self.ga4_table_prefix!r} is not a valid table name prefix")
        for name in (self.raw_dataset, self.staging_dataset, self.marts_dataset):
            if not _DATASET_RE.fullmatch(name):
                raise ConfigError(f"dataset name {name!r} is not valid")
        for label, value in (("sample start", self.sample_start), ("sample end", self.sample_end)):
            if not re.fullmatch(r"\d{8}", value):
                raise ConfigError(f"{label} {value!r} must be YYYYMMDD")
        if self.sample_start > self.sample_end:
            raise ConfigError("sample start is after sample end")
        if self.max_bytes_billed <= 0:
            raise ConfigError("TAGLINE_MAX_BYTES_BILLED must be a positive number of bytes")
        if self.alert_webhook_url is not None and not re.fullmatch(r"https?://[^\s/]+(/\S*)?", self.alert_webhook_url):
            raise ConfigError("TAGLINE_ALERT_WEBHOOK_URL is not an http(s) URL")

    @property
    def has_site(self) -> bool:
        """True when the site's GA4 export dataset is configured."""
        return self.ga4_dataset is not None

    @property
    def site_project(self) -> str:
        return self.ga4_project or self.project


def load_config(
    environ: Mapping[str, str] | None = None,
    env_file: Path | None = ENV_FILE,
    **overrides: object,
) -> Config:
    """Read tagline/.env, then the environment on top, then explicit overrides (CLI flags)."""
    values: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        values.update(parse_env_file(env_file.read_text(encoding="utf-8")))
    values.update({k: v for k, v in (os.environ if environ is None else environ).items() if k.startswith("TAGLINE_")})

    def get(key: str) -> str | None:
        value = values.get(key, "").strip()
        return value or None

    project = get("TAGLINE_GCP_PROJECT")
    if project is None:
        raise ConfigError(
            "TAGLINE_GCP_PROJECT is not set. Copy tagline/.env.example to tagline/.env and set it."
        )
    max_bytes = get("TAGLINE_MAX_BYTES_BILLED")
    try:
        max_bytes_billed = int(max_bytes) if max_bytes else DEFAULT_MAX_BYTES_BILLED
    except ValueError:
        raise ConfigError(f"TAGLINE_MAX_BYTES_BILLED {max_bytes!r} is not a whole number of bytes") from None

    kwargs: dict[str, object] = {
        "project": project,
        "ga4_dataset": get("TAGLINE_GA4_DATASET"),
        "ga4_project": get("TAGLINE_GA4_PROJECT"),
        "max_bytes_billed": max_bytes_billed,
        "alert_webhook_url": get("TAGLINE_ALERT_WEBHOOK_URL"),
    }
    kwargs.update({k: v for k, v in overrides.items() if v is not None})
    return Config(**kwargs)  # type: ignore[arg-type]
