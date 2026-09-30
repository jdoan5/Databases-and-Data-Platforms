"""The tag health checks generated from tagging/events.schema.json (tagline_pipeline/contract.py). No BigQuery."""

import copy
import json

import pytest

from tagline_pipeline import contract as c
from tagline_pipeline.config import Config
from tagline_pipeline.pipeline import context
from tagline_pipeline.sqlfiles import render


@pytest.fixture(scope="module")
def schema():
    return json.loads(c.CONTRACT_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parsed(schema):
    return c.parse_contract(schema)


def rules_for(parsed, event):
    return {r.path: r for r in parsed.rules if r.event == event}


def test_every_contract_event_is_checked_with_its_required_fields(parsed, schema):
    assert parsed.events == tuple(schema["$defs"]["event_name"]["enum"])
    assert len(parsed.events) == 14
    assert set(rules_for(parsed, "page_view")) == {"page_location", "page_title"}  # page_referrer is optional
    assert set(rules_for(parsed, "search")) == {"search_term"}
    assert set(rules_for(parsed, "login")) == {"method"} and set(rules_for(parsed, "sign_up")) == {"method"}
    purchase = rules_for(parsed, "purchase")
    assert {"ecommerce.transaction_id", "ecommerce.currency", "ecommerce.value", "ecommerce.tax", "ecommerce.shipping",
            "ecommerce.items"} <= set(purchase)
    items = {p for p in purchase if p.startswith("items[].")}
    assert items == {f"items[].{f}" for f in ("item_id", "item_name", "item_brand", "item_category", "price", "quantity")}
    # list events: list id and name required, and each item's index (list_item = item + required index)
    for event in ("view_item_list", "select_item"):
        r = rules_for(parsed, event)
        assert {"ecommerce.item_list_id", "ecommerce.item_list_name", "items[].index"} <= set(r), event
        assert "ecommerce.value" not in r, "a list has no value"
    assert "items[].index" not in rules_for(parsed, "view_item")
    assert set(rules_for(parsed, "add_shipping_info")) >= {"ecommerce.shipping_tier"}
    assert set(rules_for(parsed, "add_payment_info")) >= {"ecommerce.payment_type"}


def test_constraints_come_from_the_schema(parsed):
    purchase = rules_for(parsed, "purchase")
    assert purchase["ecommerce.currency"].const == "USD"
    assert purchase["ecommerce.value"].exclusive_minimum == 0
    assert purchase["ecommerce.tax"].minimum == 0 and purchase["ecommerce.shipping"].minimum == 0
    assert purchase["ecommerce.transaction_id"].pattern == "^TL-[A-Z0-9]+-[A-Z0-9]+$"
    assert purchase["items[].item_brand"].const == "Tagline Supply"
    assert purchase["items[].item_category"].enum == ("Apparel", "Drinkware", "Bags", "Office", "Stickers")
    assert purchase["items[].item_id"].pattern == "^TL-[A-Z]{3}-[0-9]{3}$"
    assert purchase["items[].quantity"].kind == "integer" and purchase["items[].quantity"].minimum == 1
    assert purchase["ecommerce.items"].max_items == 200
    assert rules_for(parsed, "add_to_cart")["ecommerce.items"].max_items == 1, "exactly one item"
    assert rules_for(parsed, "add_shipping_info")["ecommerce.shipping_tier"].enum == ("Ground", "Express", "Next Day")
    assert rules_for(parsed, "page_view")["page_location"].max_length == 1000  # the event's own keyword over the $ref
    assert parsed.version == "1.0.0"
    assert parsed.value_events == ("view_item", "add_to_cart", "remove_from_cart", "view_cart", "begin_checkout",
                                   "add_shipping_info", "add_payment_info", "purchase")


def test_pii_uses_the_contracts_own_pattern_and_fields(parsed, schema):
    assert parsed.email_pattern == schema["$defs"]["looks_like_email"]["pattern"]
    assert parsed.pii_columns == ("page_location", "page_title", "page_referrer", "search_term", "user_id")
    sql = c.pii_sql(parsed)
    assert sql.count("REGEXP_CONTAINS") == 5 and f"r'{parsed.email_pattern}'" in sql


def test_generated_sql_per_check_kind(parsed):
    purchase = "\n".join(c.event_checks(parsed, "purchase"))
    assert "STRUCT('required:ecommerce.transaction_id' AS check_name, 'required' AS check_kind" in purchase
    assert "(transaction_id IS NULL OR TRIM(transaction_id) IN ('', '(not set)'))" in purchase
    assert "currency != 'USD'" in purchase
    assert "i.item_brand != 'Tagline Supply'" in purchase
    assert "NOT REGEXP_CONTAINS(i.item_id, r'^TL-[A-Z]{3}-[0-9]{3}$')" in purchase
    assert "tax_usd < 0" in purchase and "event_value <= 0" in purchase
    assert "EXISTS(SELECT 1 FROM UNNEST(items) AS i WHERE i.quantity IS NULL)" in purchase
    assert "'value_math:ecommerce.value'" in purchase and "SUM(i.price * i.quantity)" in purchase
    select_item = "\n".join(c.event_checks(parsed, "select_item"))
    assert "COALESCE(item_count > 1, FALSE)" in select_item  # items_one_listed: at most one
    assert "SAFE_CAST(i.item_list_index AS INT64) IS NULL" in select_item
    assert "value_math" not in select_item
    assert c.event_checks(parsed, "login") == [
        "STRUCT('required:method' AS check_name, 'required' AS check_kind, 'method' AS field, "
        "(method IS NULL OR TRIM(method) IN ('', '(not set)')) AS violated)",
        "STRUCT('format:method' AS check_name, 'format' AS check_kind, 'method' AS field, "
        "COALESCE(NOT (method IS NULL OR TRIM(method) IN ('', '(not set)')) AND (method NOT IN ('email')), FALSE) AS violated)",
    ]
    whole = c.checks_sql(parsed)
    assert whole.startswith("CASE event_name") and whole.rstrip().endswith("END")
    assert whole.count("WHEN '") == 14
    assert "ELSE ARRAY<STRUCT<check_name STRING, check_kind STRING, field STRING, violated BOOL>>[]" in whole


def test_check_names_are_unique_per_event(parsed):
    for event in parsed.events:
        names = [s.split("'")[1] for s in c.event_checks(parsed, event)]
        assert len(names) == len(set(names)), event


def test_check_counts_match_the_generated_checks(parsed):
    counts = c.check_counts_sql(parsed)
    assert "STRUCT('purchase' AS event_name, 12 AS required_checks, 25 AS all_checks)" in counts
    assert "STRUCT('login' AS event_name, 1 AS required_checks, 2 AS all_checks)" in counts


def test_a_new_required_parameter_without_a_column_fails_loudly(schema):
    changed = copy.deepcopy(schema)
    eco = changed["$defs"]["purchase"]["properties"]["ecommerce"]
    eco["required"].append("coupon")
    eco["properties"]["coupon"] = {"type": "string"}
    with pytest.raises(c.ContractError, match="coupon.*EXPORT_COLUMNS"):
        c.parse_contract(changed)
    changed = copy.deepcopy(schema)
    changed["$defs"]["item"]["required"].append("affiliation")
    changed["$defs"]["item"]["properties"]["affiliation"] = {"type": "string"}
    with pytest.raises(c.ContractError, match="affiliation.*ITEM_COLUMNS"):
        c.parse_contract(changed)


def test_a_changed_constraint_changes_the_sql(schema):
    changed = copy.deepcopy(schema)
    changed["$defs"]["item"]["properties"]["item_brand"]["const"] = "Tagline Goods"
    sql = "\n".join(c.event_checks(c.parse_contract(changed), "add_to_cart"))
    assert "i.item_brand != 'Tagline Goods'" in sql and "Tagline Supply" not in sql


def test_quotes_in_values_are_refused_not_escaped_badly(schema):
    changed = copy.deepcopy(schema)
    changed["$defs"]["currency"]["const"] = "US'D"
    with pytest.raises(c.ContractError):
        c.checks_sql(c.parse_contract(changed))


def test_the_tag_health_model_renders_with_the_generated_checks():
    ctx = context(Config(project="my-project"), None)
    from tagline_pipeline.config import SQL_DIR
    from tagline_pipeline.sqlfiles import list_models

    model = {m.name: m for m in list_models(SQL_DIR)}["mart_tag_health_daily"]
    sql = render(model.sql(), ctx)
    assert "{{" not in sql and "CASE event_name" in sql and "WHEN 'purchase' THEN [" in sql
    assert "e.source IN ('tagline_site')" in sql and "'1.0.0' AS contract_version" in sql
    for path in ("11_tag_health_reconciles.sql",):
        text = (SQL_DIR / "checks" / path).read_text()
        assert "UNNEST([STRUCT('page_view' AS event_name" in render(text, ctx)
