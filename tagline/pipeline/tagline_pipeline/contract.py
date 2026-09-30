"""The tag health checks on the collected data, generated from the tagging contract (Stage 5).

`tagging/events.schema.json` is the contract the site validates every dataLayer push against. The same file
says what the warehouse should find for each event, so `mart_tag_health_daily` does not hand-copy it: this
module reads the schema and turns it into SQL over `stg_events`:

* **required**: for each event, every parameter the contract requires (top level, inside `ecommerce`, and on
  every item) must be present in the collected row. Missing means NULL, '' or GA4's `(not set)`.
* **format**: the contract's constraints on a present value (`const`, `enum`, `pattern`, lengths, minimums,
  item counts): `currency` = USD, `item_brand` = Tagline Supply, the SKU pattern, the shipping tiers, ...
* **value_math**: for events whose contract requires both `value` and `items`, `value` = sum of price x quantity
  (the tagging plan's section 6; the schema describes it but cannot check it).
* **pii**: no email-like string, with the contract's own `looks_like_email` pattern, in any parameter the
  contract forbids one in, and in `user_id`.

The one hand-written piece is where each contract field lands in the GA4 export, as a `stg_events` column
(`EXPORT_COLUMNS`, `ITEM_COLUMNS`). A contract change that requires a parameter with no column here fails the
generator (and so the build and the unit tests) until the column is named: the checks cannot drift silently
from the contract.

Everything here is pure (no BigQuery) and unit tested; `make contract-sql` prints the generated SQL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import TAGLINE_DIR

CONTRACT_FILE = TAGLINE_DIR / "tagging" / "events.schema.json"

# Sources whose tags are held to the contract's formats. The GA4 sample is the Google Merchandise Store, tagged by
# Google: it gets the presence checks (recorded as documented expectations) but not the Tagline formats (its brand is
# Google, its item ids are numbers).
CONTRACT_SOURCES = ("tagline_site",)

# Where each contract field lands in the GA4 export, as a stg_events column. gtag.js flattens the dataLayer's
# `ecommerce` object into event parameters; GA4 exports transaction_id, tax and shipping in the `ecommerce` record and
# the rest in `event_params` (stg_events flattens both, docs/data-model.md).
EXPORT_COLUMNS = {
    "page_location": "page_location",
    "page_title": "page_title",
    "page_referrer": "page_referrer",
    "search_term": "search_term",
    "method": "method",
    "ecommerce.currency": "currency",
    "ecommerce.value": "event_value",
    "ecommerce.item_list_id": "item_list_id",
    "ecommerce.item_list_name": "item_list_name",
    "ecommerce.shipping_tier": "shipping_tier",
    "ecommerce.payment_type": "payment_type",
    "ecommerce.transaction_id": "transaction_id",
    "ecommerce.tax": "tax_usd",
    "ecommerce.shipping": "shipping_usd",
    "ecommerce.items": "items",
}
# An item's fields, in stg_events.items (alias i). The contract's `index` is the export's item_list_index (a string).
ITEM_COLUMNS = {
    "item_id": "i.item_id",
    "item_name": "i.item_name",
    "item_brand": "i.item_brand",
    "item_category": "i.item_category",
    "item_variant": "i.item_variant",
    "price": "i.price",
    "quantity": "i.quantity",
    "index": "SAFE_CAST(i.item_list_index AS INT64)",
}
# Parameters checked for email-like strings besides the ones the contract marks: user_id (its 32-hex pattern already
# rules an email out, which is what this check verifies on the collected data).
EXTRA_PII_COLUMNS = ("user_id",)

_NOT_SET = "('', '(not set)')"


class ContractError(ValueError):
    """The contract cannot be turned into checks; the message names the event and field."""


@dataclass(frozen=True)
class FieldRule:
    """One required field of one event, and the constraints the contract puts on its value."""

    event: str
    path: str  # contract path: page_title, ecommerce.currency, ecommerce.items, items[].item_brand
    column: str  # SQL over stg_events (an items[] field: over the item alias i)
    kind: str  # string | number | integer | array
    item: bool = False
    const: Any = None
    enum: tuple[Any, ...] = ()
    pattern: str | None = None
    min_length: int | None = None
    max_length: int | None = None
    minimum: float | None = None
    exclusive_minimum: float | None = None
    maximum: float | None = None
    min_items: int | None = None
    max_items: int | None = None
    no_email: bool = False


@dataclass(frozen=True)
class Contract:
    version: str
    events: tuple[str, ...]
    rules: tuple[FieldRule, ...]
    email_pattern: str
    pii_columns: tuple[str, ...]
    value_events: tuple[str, ...] = field(default=())  # events whose contract requires both value and items


# -- reading the schema ---------------------------------------------------------------------------------------


def _pointer(schema: dict, ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ContractError(f"only local $refs are supported, not {ref!r}")
    node: Any = schema
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def resolve(schema: dict, node: Any) -> Any:
    """A schema node with its $ref (recursively) merged in: the referenced node's keywords, then the node's own on top;
    `required` lists are joined and `properties` merged (a property given as `true` keeps the referenced schema)."""
    if not isinstance(node, dict):
        return node
    if "$ref" not in node:
        return node
    base = dict(resolve(schema, _pointer(schema, node["$ref"])))
    for key, value in node.items():
        if key == "$ref":
            continue
        if key == "required":
            base["required"] = list(dict.fromkeys([*base.get("required", []), *value]))
        elif key == "properties":
            props = dict(base.get("properties", {}))
            for name, sub in value.items():
                if sub is True and name in props:
                    continue
                props[name] = sub
            base["properties"] = props
        else:
            base[key] = value
    return base


def _is_email_rule(schema: dict, node: Any) -> bool:
    return isinstance(node, dict) and node.get("$ref") == "#/$defs/looks_like_email"


def _rule(schema: dict, event: str, path: str, column: str, node: Any, item: bool) -> FieldRule:
    s = resolve(schema, node)
    if not isinstance(s, dict):
        raise ContractError(f"{event}: {path} has no schema")
    kind = s.get("type")
    if kind is None and "const" in s:
        kind = "string" if isinstance(s["const"], str) else "number"
    if kind is None and "enum" in s:
        kind = "string" if all(isinstance(v, str) for v in s["enum"]) else "number"
    if kind not in ("string", "number", "integer", "array"):
        raise ContractError(f"{event}: {path} has type {kind!r}, which the checks do not handle")
    no_email = _is_email_rule(schema, s.get("not"))
    return FieldRule(
        event=event,
        path=path,
        column=column,
        kind=kind,
        item=item,
        const=s.get("const"),
        enum=tuple(s.get("enum", ())),
        pattern=s.get("pattern"),
        min_length=s.get("minLength"),
        max_length=s.get("maxLength"),
        minimum=s.get("minimum"),
        exclusive_minimum=s.get("exclusiveMinimum"),
        maximum=s.get("maximum"),
        min_items=s.get("minItems"),
        max_items=s.get("maxItems"),
        no_email=no_email,
    )


def _export_column(event: str, path: str) -> str:
    try:
        return EXPORT_COLUMNS[path]
    except KeyError:
        raise ContractError(
            f"{event}: the contract requires {path!r}, which has no stg_events column in contract.EXPORT_COLUMNS; "
            "flatten it in 10_stg_events.sql and name it there"
        ) from None


def parse_contract(schema: dict) -> Contract:
    """Every event in the contract, with the rules for its required fields."""
    defs = schema.get("$defs", {})
    events = tuple(defs["event_name"]["enum"])
    rules: list[FieldRule] = []
    value_events: list[str] = []
    for event in events:
        if event not in defs:
            raise ContractError(f"{event}: in event_name but has no $defs entry")
        ev = resolve(schema, defs[event])
        props = ev.get("properties", {})
        for name in ev.get("required", []):
            if name == "event":
                continue
            if name == "ecommerce":
                eco = resolve(schema, props["ecommerce"])
                eprops = eco.get("properties", {})
                required = eco.get("required", [])
                for ename in required:
                    path = f"ecommerce.{ename}"
                    rule = _rule(schema, event, path, _export_column(event, path), eprops[ename], item=False)
                    rules.append(rule)
                    if ename == "items":
                        items = resolve(schema, eprops["items"])
                        item_schema = resolve(schema, items.get("items", {}))
                        iprops = item_schema.get("properties", {})
                        for iname in item_schema.get("required", []):
                            if iname not in ITEM_COLUMNS:
                                raise ContractError(
                                    f"{event}: items require {iname!r}, which has no column in contract.ITEM_COLUMNS"
                                )
                            rules.append(_rule(schema, event, f"items[].{iname}", ITEM_COLUMNS[iname], iprops[iname], item=True))
                if "value" in required and "items" in required:
                    value_events.append(event)
                continue
            rules.append(_rule(schema, event, name, _export_column(event, name), props[name], item=False))
    email = resolve(schema, defs["looks_like_email"])["pattern"]
    # every parameter the contract forbids an email in, required or not (page_referrer is optional), plus user_id
    pii: list[str] = []
    for event in events:
        ev = resolve(schema, defs[event])
        props = dict(ev.get("properties", {}))
        eco = resolve(schema, props.pop("ecommerce", {}))
        candidates = [*props.items(), *((f"ecommerce.{k}", v) for k, v in (eco.get("properties", {}) if isinstance(eco, dict) else {}).items())]
        for path, node in candidates:
            if path in EXPORT_COLUMNS and _is_email_rule(schema, resolve(schema, node).get("not") if isinstance(node, dict) else None):
                pii.append(EXPORT_COLUMNS[path])
    pii_columns = tuple(dict.fromkeys([*pii, *EXTRA_PII_COLUMNS]))
    version = re.search(r"Contract version (\d+\.\d+\.\d+)", schema.get("$comment", ""))
    return Contract(
        version=version.group(1) if version else "unknown",
        events=events,
        rules=tuple(rules),
        email_pattern=email,
        pii_columns=pii_columns,
        value_events=tuple(value_events),
    )


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> Contract:
    return parse_contract(json.loads(Path(path).read_text(encoding="utf-8")))


def load_contract(path: Path = CONTRACT_FILE) -> Contract:
    if not path.exists():
        raise ContractError(f"the tagging contract {path} is missing (the Airflow containers mount tagline/tagging read-only)")
    return _load(str(path), path.stat().st_mtime)


# -- SQL -------------------------------------------------------------------------------------------------------


def _literal(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        if "\\" in value or "'" in value or "\n" in value:
            raise ContractError(f"value {value!r} needs escaping the generator does not do")
        return f"'{value}'"
    raise ContractError(f"cannot write {value!r} as SQL")


def _regex(pattern: str) -> str:
    if "'" in pattern or "\n" in pattern:
        raise ContractError(f"pattern {pattern!r} cannot be written as a raw SQL string")
    return f"r'{pattern}'"


def _missing(rule: FieldRule, col: str) -> str:
    if rule.kind == "array":
        return "COALESCE(item_count, 0) = 0"
    if rule.kind == "string":
        return f"({col} IS NULL OR TRIM({col}) IN {_NOT_SET})"
    return f"{col} IS NULL"


def _nonconforming(rule: FieldRule, col: str) -> list[str]:
    out: list[str] = []
    if rule.kind == "array":
        if rule.min_items is not None and rule.min_items > 1:
            out.append(f"item_count < {int(rule.min_items)}")
        if rule.max_items is not None:
            out.append(f"item_count > {int(rule.max_items)}")
        return out
    if rule.const is not None:
        out.append(f"{col} != {_literal(rule.const)}")
    if rule.enum:
        out.append(f"{col} NOT IN ({', '.join(_literal(v) for v in rule.enum)})")
    if rule.pattern is not None:
        out.append(f"NOT REGEXP_CONTAINS({col}, {_regex(rule.pattern)})")
    if rule.min_length is not None:
        out.append(f"CHAR_LENGTH({col}) < {int(rule.min_length)}")
    if rule.max_length is not None:
        out.append(f"CHAR_LENGTH({col}) > {int(rule.max_length)}")
    if rule.exclusive_minimum is not None:
        out.append(f"{col} <= {_literal(rule.exclusive_minimum)}")
    if rule.minimum is not None:
        out.append(f"{col} < {_literal(rule.minimum)}")
    if rule.maximum is not None:
        out.append(f"{col} > {_literal(rule.maximum)}")
    if rule.kind == "integer" and not rule.item and not rule.column.startswith("SAFE_CAST"):
        out.append(f"{col} != TRUNC({col})")
    return out


def _over_items(condition: str) -> str:
    return f"EXISTS(SELECT 1 FROM UNNEST(items) AS i WHERE {condition})"


def _struct(kind: str, path: str, violated: str) -> str:
    return f"STRUCT('{kind}:{path}' AS check_name, '{kind}' AS check_kind, '{path}' AS field, {violated} AS violated)"


def event_checks(contract: Contract, event: str) -> list[str]:
    """The STRUCTs (check_name, check_kind, field, violated) for one event's row."""
    out: list[str] = []
    for rule in (r for r in contract.rules if r.event == event):
        col = rule.column
        missing = _missing(rule, col)
        if rule.item:
            out.append(_struct("required", rule.path, _over_items(missing)))
        else:
            out.append(_struct("required", rule.path, missing))
        bad = _nonconforming(rule, col)
        if bad:
            cond = f"NOT {missing} AND ({' OR '.join(bad)})" if rule.kind != "array" else " OR ".join(bad)
            out.append(_struct("format", rule.path, _over_items(cond) if rule.item else f"COALESCE({cond}, FALSE)"))
    if event in contract.value_events:
        # tagging plan section 6: value = sum of price x quantity, in cents, before tax and shipping
        out.append(_struct(
            "value_math", "ecommerce.value",
            "COALESCE(ABS(event_value - (SELECT SUM(i.price * i.quantity) FROM UNNEST(items) AS i)) > 0.005, FALSE)",
        ))
    return out


def checks_sql(contract: Contract, indent: str = "    ") -> str:
    """A CASE on event_name giving each contract event's checks as ARRAY<STRUCT<check_name, check_kind, field,
    violated>>; an event outside the contract gets an empty array."""
    lines = ["CASE event_name"]
    for event in contract.events:
        checks = event_checks(contract, event)
        if not checks:
            continue
        body = f",\n{indent}    ".join(checks)
        lines.append(f"{indent}  WHEN '{event}' THEN [\n{indent}    {body}\n{indent}  ]")
    lines.append(
        f"{indent}  ELSE ARRAY<STRUCT<check_name STRING, check_kind STRING, field STRING, violated BOOL>>[]\n{indent}END"
    )
    return "\n".join(lines)


def pii_sql(contract: Contract) -> str:
    """TRUE when any checked parameter of the row holds an email-like string (the contract's own pattern)."""
    pattern = _regex(contract.email_pattern)
    return "(" + " OR ".join(f"COALESCE(REGEXP_CONTAINS({c}, {pattern}), FALSE)" for c in contract.pii_columns) + ")"


def check_counts_sql(contract: Contract) -> str:
    """Per contract event, how many required checks and how many checks in all (for a source held to the contract) the
    mart must hold for a day it has the event: check 11 compares, so a check that silently stops running is caught."""
    rows = []
    for event in contract.events:
        checks = event_checks(contract, event)
        required = sum(1 for r in contract.rules if r.event == event)  # one required check per required field
        rows.append(f"STRUCT('{event}' AS event_name, {required} AS required_checks, {len(checks)} AS all_checks)")
    return "[" + ", ".join(rows) + "]"


def template_context(contract: Contract | None = None) -> dict[str, str]:
    """What mart_tag_health_daily's template needs: the generated checks and the contract's facts."""
    contract = contract or load_contract()
    return {
        "contract_checks": checks_sql(contract),
        "contract_pii": pii_sql(contract),
        "contract_version": _literal(contract.version),
        "contract_events": ", ".join(_literal(e) for e in contract.events),
        "contract_sources": ", ".join(_literal(s) for s in CONTRACT_SOURCES),
        "contract_pii_fields": _literal(", ".join(contract.pii_columns)),
        "contract_check_counts": check_counts_sql(contract),
    }
