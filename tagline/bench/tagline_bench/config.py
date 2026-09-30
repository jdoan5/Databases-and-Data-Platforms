"""Where the harness runs and what it may spend: tagline/.env, with TAGLINE_* environment variables on top,
exactly as the pipeline and the Spark submitter read it."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parents[1]
TAGLINE_DIR = BENCH_DIR.parent
ENV_FILE = TAGLINE_DIR / ".env"
RESULTS_DIR = BENCH_DIR / "results"
LOG_DIR = BENCH_DIR / "logs"  # raw command output; gitignored (it names the project)

LOCATION = "US"
REGION_QUALIFIER = "region-us"
STAGING = "tagline_staging"
MARTS = "tagline_marts"
RAW = "tagline_raw"
BASELINE_DATASET = "tagline_s4_baseline"
DEFAULT_MAX_BYTES_BILLED = 10 * 1000**3  # the pipeline's default guard

# The tables a build or a batch writes, by where they live.
STAGE2_STAGING = ("stg_events", "stg_items", "int_purchases", "int_device_days", "int_identity")
STAGE2_MARTS = ("fct_sessions", "fct_orders", "fct_order_items", "mart_campaign_daily", "mart_funnel_daily")
STAGE3_MARTS = ("fct_attribution", "mart_attribution_daily")

# Named table sets for fingerprint / snapshot / diff. "gate" is the correctness gate of the spec: the marts and
# the attribution tables (all of tagline_marts). "stage2" is the ten tables a Stage 2 build writes; "all" adds the
# attribution tables to it (stg_events alone is ~2.6 GiB to scan, so the staging tables are not in the default).
TABLE_SETS: dict[str, tuple[tuple[str, str], ...]] = {
    "stage2": tuple((STAGING, t) for t in STAGE2_STAGING) + tuple((MARTS, t) for t in STAGE2_MARTS),
    "stage2-marts": tuple((MARTS, t) for t in STAGE2_MARTS),
    "stage3": tuple((MARTS, t) for t in STAGE3_MARTS),
    "gate": tuple((MARTS, t) for t in STAGE2_MARTS + STAGE3_MARTS),
    "all": tuple((STAGING, t) for t in STAGE2_STAGING) + tuple((MARTS, t) for t in STAGE2_MARTS + STAGE3_MARTS),
}

_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


class ConfigError(ValueError):
    pass


def parse_env_file(text: str) -> dict[str, str]:
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


@dataclass(frozen=True)
class BenchConfig:
    project: str
    max_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED
    ga4_dataset: str | None = None
    region: str | None = None
    bucket: str | None = None
    service_account: str | None = None

    def __post_init__(self) -> None:
        if not _PROJECT_RE.fullmatch(self.project):
            raise ConfigError(f"TAGLINE_GCP_PROJECT {self.project!r} is not a valid project id")
        if self.max_bytes_billed <= 0:
            raise ConfigError("TAGLINE_MAX_BYTES_BILLED must be positive")

    @property
    def secrets(self) -> dict[str, str]:
        """Values that must never reach a committed file, and what replaces them."""
        pairs = {
            self.project: "<project>",
            self.bucket or "": "<bucket>",
            self.service_account or "": "<service-account>",
            self.ga4_dataset or "": "analytics_<property_id>",
        }
        return {k: v for k, v in pairs.items() if k}


def load_config(environ: Mapping[str, str] | None = None, env_file: Path | None = ENV_FILE) -> BenchConfig:
    values: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        values.update(parse_env_file(env_file.read_text(encoding="utf-8")))
    values.update({k: v for k, v in (os.environ if environ is None else environ).items() if k.startswith("TAGLINE_")})

    def get(key: str) -> str | None:
        return (values.get(key) or "").strip() or None

    project = get("TAGLINE_GCP_PROJECT")
    if not project:
        raise ConfigError("TAGLINE_GCP_PROJECT is not set (tagline/.env)")
    max_bytes = get("TAGLINE_MAX_BYTES_BILLED")
    try:
        max_bytes_billed = int(max_bytes) if max_bytes else DEFAULT_MAX_BYTES_BILLED
    except ValueError:
        raise ConfigError(f"TAGLINE_MAX_BYTES_BILLED {max_bytes!r} is not a whole number") from None
    bucket = get("TAGLINE_SPARK_BUCKET")
    return BenchConfig(
        project=project,
        max_bytes_billed=max_bytes_billed,
        ga4_dataset=get("TAGLINE_GA4_DATASET"),
        region=get("TAGLINE_GCP_REGION"),
        bucket=bucket.removeprefix("gs://").rstrip("/") if bucket else None,
        service_account=get("TAGLINE_SPARK_SERVICE_ACCOUNT"),
    )


def resolve_tables(spec: str) -> tuple[tuple[str, str], ...]:
    """A named set ("gate", "stage2", ...) or a comma list of dataset.table / table (table: looked up in the sets)."""
    if spec in TABLE_SETS:
        return TABLE_SETS[spec]
    known = {t: ds for ds, t in TABLE_SETS["all"]}
    out = []
    for item in (s.strip() for s in spec.split(",") if s.strip()):
        if "." in item:
            ds, t = item.split(".", 1)
            out.append((ds, t))
        elif item in known:
            out.append((known[item], item))
        else:
            raise ConfigError(f"unknown table {item!r}: use dataset.table or one of {', '.join(sorted(known))}")
    if not out:
        raise ConfigError("no tables given")
    return tuple(out)
