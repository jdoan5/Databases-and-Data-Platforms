"""mart_tag_health_daily's SQL run for real on literal rows, in BigQuery (opt-in: `make test-tag-health-sql`).

The rest of the suite checks the generated SQL as text (test_contract.py) and the anomaly rules on rows already
labelled `violation` (test_anomaly.py). This runs the whole model: the checks generated from events.schema.json, the
pii, dedupe and attribution checks, and the status / expectation join, over a few stg_events- and fct_sessions-shaped
rows written as literals, each breaking one rule on a day of its own, and asserts exactly which checks flag which row
and with what status. No table is read, so BigQuery bills 0 bytes (asserted); the job keeps maximum_bytes_billed and
the query cache is off, like every job the pipeline runs.

Skipped unless TAGLINE_BQ_TESTS=1 (it needs Google Cloud credentials and tagline/.env).
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("TAGLINE_BQ_TESTS") != "1", reason="BigQuery test: TAGLINE_BQ_TESTS=1")

DAY0 = date(2026, 1, 1)
ITEM_FIELDS = (("item_id", "STRING"), ("item_name", "STRING"), ("item_brand", "STRING"), ("item_category", "STRING"),
               ("item_variant", "STRING"), ("price", "FLOAT64"), ("quantity", "INT64"), ("item_list_index", "STRING"))
ITEM_TYPE = "STRUCT<" + ", ".join(f"{n} {t}" for n, t in ITEM_FIELDS) + ">"
STG_FIELDS = (
    ("event_date", "DATE"), ("source", "STRING"), ("event_name", "STRING"), ("page_location", "STRING"),
    ("page_title", "STRING"), ("page_referrer", "STRING"), ("search_term", "STRING"), ("method", "STRING"),
    ("currency", "STRING"), ("event_value", "FLOAT64"), ("item_list_id", "STRING"), ("item_list_name", "STRING"),
    ("shipping_tier", "STRING"), ("payment_type", "STRING"), ("transaction_id", "STRING"), ("tax_usd", "FLOAT64"),
    ("shipping_usd", "FLOAT64"), ("items", f"ARRAY<{ITEM_TYPE}>"), ("item_count", "INT64"), ("user_id", "STRING"),
    ("is_duplicate_purchase", "BOOL"), ("is_transaction_id_collision", "BOOL"),
)
STG_TYPE = "STRUCT<" + ", ".join(f"{n} {t}" for n, t in STG_FIELDS) + ">"
SESSION_FIELDS = (("session_date", "DATE"), ("source", "STRING"), ("session_source", "STRING"), ("session_medium", "STRING"))
SESSION_TYPE = "STRUCT<" + ", ".join(f"{n} {t}" for n, t in SESSION_FIELDS) + ">"


def typed(v, t: str) -> str:
    """A literal of exactly type t, NULL included."""
    if t.startswith("ARRAY<"):
        return f"{t}[{', '.join(struct(x, ITEM_FIELDS) for x in v)}]"
    if v is None:
        return f"CAST(NULL AS {t})"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, date):
        return f"DATE '{v.isoformat()}'"
    if isinstance(v, (int, float)):
        return f"CAST({v!r} AS {t})"
    assert "'" not in v and "\\" not in v, v
    return f"'{v}'"


def struct(values: dict, fields) -> str:
    return "STRUCT(" + ", ".join(f"{typed(values[n], t)} AS {n}" for n, t in fields) + ")"


def item(**over):
    it = {"item_id": "TL-DRK-001", "item_name": "Ceramic Mug", "item_brand": "Tagline Supply", "item_category": "Drinkware",
          "item_variant": "White", "price": 13.99, "quantity": 1, "item_list_index": None}
    it.update(over)
    return it


def event(name, source="tagline_site", items=None, **over):
    items = [item()] if items is None else items
    row = {"source": source, "event_name": name, "page_location": "https://shop.example/product/TL-DRK-001",
           "page_title": "Ceramic Mug", "page_referrer": None, "search_term": None, "method": None, "currency": "USD",
           "event_value": round(sum(i["price"] * i["quantity"] for i in items), 2), "item_list_id": None,
           "item_list_name": None, "shipping_tier": None, "payment_type": None, "transaction_id": None, "tax_usd": None,
           "shipping_usd": None, "items": items, "user_id": None, "is_duplicate_purchase": False,
           "is_transaction_id_collision": False}
    if name == "purchase":
        row.update(transaction_id="TL-MG3K2ZQ1-0A7F3KD", tax_usd=1.12, shipping_usd=5.0)
    row.update(over)
    return row


def purchase(**over):
    return event("purchase", **over)


# Each case: (name, stg_events rows, fct_sessions rows, {(event_name, check_name): status} expected with violations > 0).
CASES = [
    ("a correct add_to_cart and purchase", [event("add_to_cart"), purchase()], [], {}),
    ("item_brand NULL", [event("add_to_cart", items=[item(item_brand=None)])], [],
     {("add_to_cart", "required:items[].item_brand"): "violation"}),
    ("item_brand (not set)", [event("add_to_cart", items=[item(item_brand="(not set)")])], [],
     {("add_to_cart", "required:items[].item_brand"): "violation"}),
    ("item_brand of another store", [event("add_to_cart", items=[item(item_brand="Acme")])], [],
     {("add_to_cart", "format:items[].item_brand"): "violation"}),
    ("two items on one add_to_cart", [event("add_to_cart", items=[item(), item(item_id="TL-DRK-002")])], [],
     {("add_to_cart", "format:ecommerce.items"): "violation"}),
    ("value is not the sum of the items", [event("add_to_cart", event_value=13.0)], [],
     {("add_to_cart", "value_math:ecommerce.value"): "violation"}),
    ("a SKU outside the pattern", [event("add_to_cart", items=[item(item_id="12345")])], [],
     {("add_to_cart", "format:items[].item_id"): "violation"}),
    ("a transaction_id outside the pattern", [purchase(transaction_id="order-1")], [],
     {("purchase", "format:ecommerce.transaction_id"): "violation"}),
    ("a purchase with no items", [purchase(items=[], event_value=12.0)], [],
     {("purchase", "required:ecommerce.items"): "violation"}),
    ("an email, URL-encoded, in page_location", [event("page_view", items=[], page_location="https://shop.example/?e=jane%40mail.com")], [],
     {("page_view", "pii:email"): "violation"}),
    ("an email as user_id", [event("login", items=[], user_id="jane@mail.com", method="email")], [],
     {("login", "pii:email"): "violation"}),
    ("a purchase sent twice", [purchase(), purchase(is_duplicate_purchase=True)], [],
     {("purchase", "dedupe:duplicate_transaction_id"): "violation"}),
    ("a site session with no source", [], [{"source": "tagline_site", "session_source": "(not set)", "session_medium": "(not set)"},
                                          {"source": "tagline_site", "session_source": "google", "session_medium": "cpc"}],
     {("(session)", "attribution:source_not_set"): "expected"}),
    ("the sample's missing brand is a documented expectation, and no format check applies to it",
     [event("add_to_cart", source="ga4_sample", items=[item(item_brand=None, item_id="9180753")])], [],
     {("add_to_cart", "required:items[].item_brand"): "expected"}),
]


def literal_ctes() -> str:
    stg, sessions = [], []
    for n, (_, events, sess, _) in enumerate(CASES):
        day = DAY0 + timedelta(days=n)
        stg += [struct({**e, "event_date": day, "item_count": len(e["items"])}, STG_FIELDS) for e in events]
        sessions += [struct({**x, "session_date": day}, SESSION_FIELDS) for x in sess]
    return (f"lit_stg_events AS (SELECT * FROM UNNEST(ARRAY<{STG_TYPE}>[{', '.join(stg)}])),\n"
            f"lit_fct_sessions AS (SELECT * FROM UNNEST(ARRAY<{SESSION_TYPE}>[{', '.join(sessions)}]))")


def model_over_literals(cfg) -> str:
    """The model's SELECT, rendered as the build renders it, with its two inputs swapped for the literal rows."""
    from tagline_pipeline.config import SQL_DIR
    from tagline_pipeline.pipeline import context
    from tagline_pipeline.sqlfiles import list_models, render

    model = {m.name: m for m in list_models(SQL_DIR)}["mart_tag_health_daily"]
    sql = render(model.sql(), context(cfg, None))
    body = sql[sql.index("\nAS\nWITH ") + len("\nAS\nWITH "):]
    stg, sessions = f"`{cfg.project}.{cfg.staging_dataset}.stg_events`", f"`{cfg.project}.{cfg.marts_dataset}.fct_sessions`"
    assert body.count(stg) == 1 and body.count(sessions) == 1, "the model's inputs moved: update this test"
    body = body.replace(stg, "lit_stg_events").replace(sessions, "lit_fct_sessions")
    return f"WITH {literal_ctes()},\n{body}\nORDER BY date, event_name, check_name"


def test_the_tag_health_model_flags_exactly_the_broken_rows():
    from tagline_pipeline.bq import BigQuery
    from tagline_pipeline.config import load_config

    cfg = load_config()
    bq = BigQuery(cfg, extra_labels={"purpose": "test"})
    rows, stat = bq.query("test_tag_health_sql", "test", model_over_literals(cfg))
    assert stat.bytes_billed == 0, f"a literal-only query billed {stat.bytes_billed} bytes"
    flagged: dict[int, dict[tuple[str, str], str]] = {n: {} for n in range(len(CASES))}
    for r in rows:
        assert r["contract_version"], "the contract version is stamped on every row"
        if r["violations"] > 0:
            flagged[(r["date"] - DAY0).days][(r["event_name"], r["check_name"])] = r["status"]
        else:
            assert r["status"] == "pass" and r["expectation"] is None, dict(r)
    for n, (name, _, _, expected) in enumerate(CASES):
        assert flagged[n] == expected, f"{name}: flagged {flagged[n]}, expected {expected}"
