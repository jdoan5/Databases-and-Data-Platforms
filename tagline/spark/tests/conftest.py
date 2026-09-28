"""A local SparkSession for the tests, and builders for hand-made fct_orders / fct_sessions rows.

Spark 4.0 runs on Java 17 or 21. `make spark-test` sets JAVA_HOME to a Java 21; run directly, the tests
look for one with macOS's java_home when JAVA_HOME is not set. The Python workers use this interpreter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta

import pytest

os.environ["PYSPARK_PYTHON"] = sys.executable
os.environ["PYSPARK_DRIVER_PYTHON"] = sys.executable
if "JAVA_HOME" not in os.environ and shutil.which("/usr/libexec/java_home"):
    found = subprocess.run(["/usr/libexec/java_home", "-v", "21"], capture_output=True, text=True)
    if found.returncode == 0:
        os.environ["JAVA_HOME"] = found.stdout.strip()

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import types as T  # noqa: E402

ORDER_SCHEMA = T.StructType(
    [
        T.StructField("source", T.StringType()),
        T.StructField("order_id", T.StringType()),
        T.StructField("order_date", T.DateType()),
        T.StructField("ordered_at", T.TimestampType()),
        T.StructField("person_id", T.StringType()),
        T.StructField("session_key", T.StringType()),
        T.StructField("revenue_usd", T.DoubleType()),
        T.StructField("is_zero_value_without_id", T.BooleanType()),
    ]
)
SESSION_SCHEMA = T.StructType(
    [
        T.StructField("source", T.StringType()),
        T.StructField("session_key", T.StringType()),
        T.StructField("person_id", T.StringType()),
        T.StructField("session_start_at", T.TimestampType()),
        T.StructField("session_source", T.StringType()),
        T.StructField("session_medium", T.StringType()),
        T.StructField("session_campaign", T.StringType()),
    ]
)

# The purchase most tests attribute, and a session long before it so every journey's lookback is complete
# unless a test says otherwise (lookback_complete compares with the first session of the data source).
T0 = datetime(2021, 1, 15, 12, 0, 0, tzinfo=UTC)
DATA_START = T0 - timedelta(days=60)

CHANNELS = {
    "organic": ("google", "organic", "(organic)"),
    "cpc": ("google", "cpc", "brand"),
    "email": ("newsletter", "email", "nov"),
    "referral": ("shop.example.com", "referral", "(referral)"),
    "direct": ("(direct)", "(none)", "(direct)"),
    "unknown": ("(not set)", "(not set)", "(not set)"),
}


def session(key: str, person: str, start: datetime, channel: str = "organic", source: str = "ga4_sample") -> tuple:
    return (source, key, person, start, *CHANNELS[channel])


def order(
    order_id: str,
    person: str,
    ordered_at: datetime,
    session_key: str | None,
    revenue: float | None = 100.0,
    zero_value_without_id: bool = False,
    source: str = "ga4_sample",
) -> tuple:
    return (source, order_id, ordered_at.date() if ordered_at else date(2021, 1, 1), ordered_at, person, session_key, revenue, zero_value_without_id)


def data_start_session(source: str = "ga4_sample") -> tuple:
    return session("start-of-data", "someone-else", DATA_START, "direct", source)


@pytest.fixture(scope="session")
def spark():
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("tagline-attribution-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    yield spark
    spark.stop()


@pytest.fixture
def frames(spark):
    """frames(orders, sessions) -> (orders DataFrame, sessions DataFrame), with the data-start session added."""

    def make(orders: list[tuple], sessions: list[tuple], add_data_start: bool = True):
        rows = list(sessions) + ([data_start_session()] if add_data_start else [])
        return spark.createDataFrame(orders, ORDER_SCHEMA), spark.createDataFrame(rows, SESSION_SCHEMA)

    return make
