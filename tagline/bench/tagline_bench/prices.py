"""List prices used for every Stage 4 number, and the arithmetic on them.

Read from Google's pricing pages on 2026-09-28 (23:56 UTC):

- BigQuery, https://cloud.google.com/bigquery/pricing, location "US (us)":
  on-demand queries $6.25 per TiB (the first 1 TiB per month free; at least 10 MB per table referenced and
  per query); storage per GiB-month: active logical $0.02, long-term logical $0.01, active physical $0.04,
  long-term physical $0.02 (the first 10 GiB of each free every month). "There are no charges for time travel
  and failsafe storage for logical storage. Time travel and failsafe storage charges apply for physical
  storage." Load and copy jobs are free.
- Serverless for Apache Spark, https://cloud.google.com/dataproc-serverless/pricing (now titled "Managed
  Service for Apache Spark (formerly Dataproc) pricing"), us-central1: standard DCU $0.06 per DCU-hour
  (premium $0.089); standard shuffle storage $0.04 per GiB-month = $0.000054795 per GiB-hour (premium $0.10);
  prorated per second with a 1-minute minimum.

Money is always list price, before free tiers, unless a number says otherwise.
"""

from __future__ import annotations

PRICES_READ_ON = "2026-09-28"
SOURCES = {
    "bigquery": "https://cloud.google.com/bigquery/pricing",
    "dataproc_serverless": "https://cloud.google.com/dataproc-serverless/pricing",
}

TIB = 2**40
GIB = 2**30

BQ_ON_DEMAND_USD_PER_TIB = 6.25
BQ_FREE_QUERY_TIB_PER_MONTH = 1.0

# US multi-region, USD per GiB-month.
STORAGE_USD_PER_GIB_MONTH = {
    "active_logical": 0.02,
    "long_term_logical": 0.01,
    "active_physical": 0.04,
    "long_term_physical": 0.02,
}
STORAGE_FREE_GIB_PER_MONTH = 10  # per storage class, per billing account

# us-central1, standard tier.
DCU_USD_PER_HOUR = 0.06
SHUFFLE_USD_PER_GIB_HOUR = 0.000054795

DAYS_PER_MONTH = 30  # the spec's projection: a daily run x 30


def bq_usd(bytes_billed: int | float | None) -> float:
    """On-demand list price of this many billed bytes."""
    return (bytes_billed or 0) / TIB * BQ_ON_DEMAND_USD_PER_TIB


def spark_usd(dcu_hours: float | None, shuffle_gib_hours: float | None) -> float:
    return (dcu_hours or 0) * DCU_USD_PER_HOUR + (shuffle_gib_hours or 0) * SHUFFLE_USD_PER_GIB_HOUR


def storage_monthly_usd(
    *,
    active_logical: int = 0,
    long_term_logical: int = 0,
    active_physical: int = 0,
    long_term_physical: int = 0,
    fail_safe_physical: int = 0,
) -> dict[str, float]:
    """What these bytes would cost for a month under each dataset storage billing model (list price, no free tier).

    Logical: active + long-term logical bytes; time travel and fail-safe are free.
    Physical: TABLE_STORAGE's active_physical_bytes already include time-travel bytes; fail-safe bytes are
    billed too, at the active physical rate."""
    p = STORAGE_USD_PER_GIB_MONTH
    logical = active_logical / GIB * p["active_logical"] + long_term_logical / GIB * p["long_term_logical"]
    physical = (active_physical + fail_safe_physical) / GIB * p["active_physical"] + long_term_physical / GIB * p["long_term_physical"]
    return {"logical": logical, "physical": physical}


def as_dict() -> dict[str, object]:
    """The prices as a record for the results log."""
    return {
        "read_on": PRICES_READ_ON,
        "sources": SOURCES,
        "bq_on_demand_usd_per_tib": BQ_ON_DEMAND_USD_PER_TIB,
        "bq_free_query_tib_per_month": BQ_FREE_QUERY_TIB_PER_MONTH,
        "storage_usd_per_gib_month": STORAGE_USD_PER_GIB_MONTH,
        "storage_free_gib_per_month_per_class": STORAGE_FREE_GIB_PER_MONTH,
        "dcu_usd_per_hour": DCU_USD_PER_HOUR,
        "shuffle_usd_per_gib_hour": SHUFFLE_USD_PER_GIB_HOUR,
        "days_per_month": DAYS_PER_MONTH,
    }
