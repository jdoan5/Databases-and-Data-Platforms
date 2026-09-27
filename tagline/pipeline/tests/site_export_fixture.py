#!/usr/bin/env python3
"""Prove the site-export path works before the real GA4 export exists.

    make fixture                                   (from tagline/)
    .venv/bin/python tests/site_export_fixture.py  (from tagline/pipeline/)

What it does, in order:

1. Loads a handful of hand-built rows shaped like the site's GA4 export into TEMPORARY tables in
   tagline_raw, named the way GA4 names them behind a fake_ga4_ prefix (no extra dataset):
       fake_ga4_events_20260924            daily
       fake_ga4_events_20260925            daily
       fake_ga4_events_intraday_20260925   streaming table for a day that already has its daily
                                           table: an exact copy, which the build must ignore
       fake_ga4_events_intraday_20260926   a day with only a streaming table: the build must read it
   Each table has a 24-hour expiry and a purpose:fixture label, so a crashed run cleans itself up
   and a later run knows the tables are its own. The schema is the public sample's, copied from
   BigQuery, plus the two records current exports have and the sample predates
   (collected_traffic_source, session_traffic_source_last_click with its manual_campaign,
   google_ads_campaign and cross_channel_campaign subrecords), with stream_id and privacy_info as
   STRING as current exports type them (the obfuscated sample has them as INTEGER).
2. Builds every model with the site export pointed at those tables (TAGLINE_GA4_DATASET=tagline_raw,
   table prefix fake_ga4_events_), runs the data checks, and verifies the scenarios below. This
   build replaces the real tagline_staging / tagline_marts tables for a few minutes.
3. Rebuilds with the normal configuration, so no fixture row is left in any table, then deletes the
   fixture tables whatever happened before. If that rebuild fails, it says so: run `make build`.

The rows are synthetic and hand-built, not captured: page URLs, titles, item fields, event
names and parameters follow what the site sends (tagging plan; simulator dry-run hits).
Everything is in UTC, as if the property's time zone were UTC. Scenarios:

  device A  09-24  anonymous session from a google / cpc / fall_launch click that carries a gclid and
                   no utm_*: only session_traffic_source_last_click.cross_channel_campaign names it
                   (as for a Search Ads 360 click); one export row duplicated
            09-25  lands from facebook / paid_social / retarget_q4, browses, signs up as U
  device B  09-25  lands from newsletter / email / newsletter_oct, logs in as U, buys; the
                   purchase is sent twice with the same transaction_id
  device D  09-25  a shared laptop: V signs in, later W signs in, later an anonymous visit
  no device 09-25  consent denied (cookieless pings: no user_pseudo_id, no ga_session_id): an
                   anonymous visitor buys; later V, signed in on a phone that denied consent, buys
  device C  09-26  organic visit from Google, never signs in (streaming table only)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PIPELINE_DIR = Path(__file__).resolve().parents[1]
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from tagline_pipeline.config import PRODUCTS_JSON, Config  # noqa: E402
from tagline_pipeline.reference import campaign_cost_rows, product_rows  # noqa: E402

TABLE_PREFIX = "fake_ga4_"  # fixture tables in tagline_raw: fake_ga4_events_YYYYMMDD, fake_ga4_events_intraday_YYYYMMDD
LABELS = {"app": "tagline", "stage": "2", "purpose": "fixture"}
SAMPLE_SCHEMA_TABLE = "bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_20210131"
SAMPLE_EVENTS = 4_295_584  # the public sample, 2020-11-01 to 2021-01-31
HOST = "http://localhost:5190"
STREAM_ID = "1000000001"  # made up
TITLE = "{} · Tagline Supply"
TAX_RATE = 0.08  # site/src/checkout/orders.ts

DAY1, DAY2, DAY3 = date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26)


def account_id(name: str) -> str:
    """An opaque 32-hex account id, as the site's account service issues (never derived from an email there)."""
    return hashlib.sha256(f"tagline-fixture/account/{name}".encode()).hexdigest()[:32]


U, V, W = account_id("u"), account_id("v"), account_id("w")
DEVICE_A = "1111111111.1790380800"
DEVICE_B = "2222222222.1790467200"
DEVICE_C = "3333333333.1790553600"
DEVICE_D = "4444444444.1790467300"
COOKIELESS = "cookieless"  # a DEVICES key only: cookieless rows have no user_pseudo_id

PRODUCTS = {p["item_id"]: p for p in json.loads(PRODUCTS_JSON.read_text(encoding="utf-8"))}

DIRECT = {"source": "(direct)", "medium": "(none)", "campaign_name": "(direct)"}
FALL_LAUNCH = {"source": "google", "medium": "cpc", "campaign_name": "fall_launch"}
RETARGET = {"source": "facebook", "medium": "paid_social", "campaign_name": "retarget_q4"}
NEWSLETTER = {"source": "newsletter", "medium": "email", "campaign_name": "newsletter_oct"}
ORGANIC = {"source": "google", "medium": "organic", "campaign_name": "(organic)"}

DEVICES = {
    DEVICE_A: {"category": "desktop", "operating_system": "Macintosh", "browser": "Chrome", "city": "Brooklyn", "region": "New York"},
    DEVICE_B: {"category": "mobile", "operating_system": "iOS", "browser": "Safari", "city": "Brooklyn", "region": "New York"},
    DEVICE_C: {"category": "desktop", "operating_system": "Windows", "browser": "Chrome", "city": "Austin", "region": "Texas"},
    DEVICE_D: {"category": "desktop", "operating_system": "Macintosh", "browser": "Safari", "city": "Portland", "region": "Oregon"},
    COOKIELESS: {"category": "mobile", "operating_system": "Android", "browser": "Chrome", "city": "Denver", "region": "Colorado"},
}

CHANNEL_GROUP = {"cpc": "Paid Search", "email": "Email", "paid_social": "Paid Social", "organic": "Organic Search", "(none)": "Direct"}


def cross_channel(c: dict, platform: str = "Manual") -> dict:
    """session_traffic_source_last_click.cross_channel_campaign for a source / medium / campaign."""
    return {**c, "source_platform": platform, "default_channel_group": CHANNEL_GROUP[c["medium"]]}


# ---------------------------------------------------------------------------------
# Rows


@dataclass(frozen=True)
class Session:
    device: str
    number: int  # ga_session_number
    start: datetime
    last_click: dict | None  # session_traffic_source_last_click.manual_campaign (None: that record is empty)
    first_touch: dict | None  # traffic_source: the device's first-touch source (NULL for new users in streaming tables)
    cross: dict | None = None  # session_traffic_source_last_click.cross_channel_campaign; default: last_click's
    cookieless: bool = False  # consent denied: no user_pseudo_id, no session ids, no attribution records

    @property
    def id(self) -> int:
        return int(self.start.timestamp())

    def last_click_record(self) -> dict | None:
        if self.cookieless:
            return None
        cross = self.cross if self.cross is not None else (cross_channel(self.last_click) if self.last_click else None)
        return {"manual_campaign": dict(self.last_click) if self.last_click else None, "cross_channel_campaign": cross}


def _param(key: str, value: Any) -> dict:
    if isinstance(value, bool):
        raise TypeError("GA4 has no boolean params")
    if isinstance(value, int):
        return {"key": key, "value": {"int_value": value}}
    if isinstance(value, float):
        return {"key": key, "value": {"double_value": value}}
    return {"key": key, "value": {"string_value": str(value)}}


def item(item_id: str, quantity: int | None = None, *, purchase: bool = False, list_name: str | None = None, index: int | None = None) -> dict:
    p = PRODUCTS[item_id]
    out = {
        "item_id": item_id,
        "item_name": p["item_name"],
        "item_brand": "Tagline Supply",
        "item_variant": p.get("item_variant"),
        "item_category": p["item_category"],
        "price": p["price"],
        "price_in_usd": p["price"],
        "quantity": quantity,
        "item_list_name": list_name,
        "item_list_index": None if index is None else str(index),
    }
    if purchase:
        out["item_revenue"] = out["item_revenue_in_usd"] = round(p["price"] * quantity, 2)
    return out


# collected_traffic_source is what GA4 read from the event itself: gtag.js sends the page URL
# (utm_* included) with every event on the landing page, so every event there carries it.
def event(
    s: Session,
    seconds: float,
    name: str,
    path: str,
    title: str,
    *,
    referrer: str | None = None,
    user_id: str | None = None,
    collected: dict | None = None,
    params: dict | None = None,
    items: list[dict] | None = None,
    ecommerce: dict | None = None,
    engaged: bool = True,
) -> dict:
    ts = s.start + timedelta(seconds=seconds)
    d = DEVICES[s.device]
    session_params = [] if s.cookieless else [_param("ga_session_id", s.id), _param("ga_session_number", s.number)]
    event_params = [
        *session_params,
        _param("page_location", HOST + path),
        _param("page_title", TITLE.format(title)),
        _param("session_engaged", "1" if engaged else "0"),
        _param("engagement_time_msec", 1200),
    ]
    if referrer:
        event_params.append(_param("page_referrer", referrer))
    for k, v in (params or {}).items():
        event_params.append(_param(k, v))
    return {
        "event_date": ts.strftime("%Y%m%d"),
        "event_timestamp": int(ts.timestamp() * 1_000_000),
        "event_name": name,
        "event_params": event_params,
        "user_id": user_id,
        "user_pseudo_id": None if s.cookieless else s.device,
        "privacy_info": {"analytics_storage": "No" if s.cookieless else "Yes", "ads_storage": "No" if s.cookieless else "Yes", "uses_transient_token": "No"},
        "user_properties": [],
        "device": {
            "category": d["category"],
            "operating_system": d["operating_system"],
            "language": "en-us",
            "web_info": {"browser": d["browser"], "browser_version": "1.0"},
        },
        "geo": {"continent": "Americas", "sub_continent": "Northern America", "country": "United States", "region": d["region"], "city": d["city"]},
        "traffic_source": None if s.first_touch is None or s.cookieless else {"source": s.first_touch["source"], "medium": s.first_touch["medium"], "name": s.first_touch["campaign_name"]},
        "stream_id": STREAM_ID,
        "platform": "WEB",
        "event_dimensions": {"hostname": "localhost"},
        "ecommerce": ecommerce,
        "items": items or [],
        "collected_traffic_source": collected,
        "session_traffic_source_last_click": s.last_click_record(),
    }


def _ecom(items: list[dict]) -> dict:
    """ecommerce on a non-purchase ecommerce event: GA4 writes (not set) for the transaction id."""
    return {"total_item_quantity": sum(i["quantity"] or 0 for i in items), "unique_items": len(items), "transaction_id": "(not set)"}


def _value(items: list[dict]) -> float:
    return round(sum(i["price"] * (i["quantity"] or 1) for i in items), 2)


def _utm(path: str, c: dict, extra: dict) -> str:
    q = {"utm_source": c["source"], "utm_medium": c["medium"], "utm_campaign": c["campaign_name"], **extra}
    return path + "?" + "&".join(f"{k}={v.replace(' ', '%20')}" for k, v in q.items())


def _collected(c: dict, term: str | None = None, content: str | None = None) -> dict:
    return {"manual_source": c["source"], "manual_medium": c["medium"], "manual_campaign_name": c.get("campaign_name"), "manual_term": term, "manual_content": content}


def _base36(n: int) -> str:
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    out = ""
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out or "0"


@dataclass(frozen=True)
class Order:
    transaction_id: str
    items: tuple[tuple[str, int], ...]  # (item_id, quantity)
    value: float
    tax: float
    shipping: float
    at: datetime

    def ecommerce(self) -> dict:
        return {
            "total_item_quantity": sum(q for _, q in self.items),
            "purchase_revenue_in_usd": self.value,
            "purchase_revenue": self.value,
            "tax_value_in_usd": self.tax,
            "tax_value": self.tax,
            "shipping_value_in_usd": self.shipping,
            "shipping_value": self.shipping,
            "unique_items": len(self.items),
            "transaction_id": self.transaction_id,
        }

    def params(self) -> dict:
        return {"currency": "USD", "value": self.value, "tax": self.tax, "shipping": self.shipping, "transaction_id": self.transaction_id}

    def bought(self) -> list[dict]:
        return [item(i, q, purchase=True) for i, q in self.items]

    def lines(self) -> list[dict]:
        return [item(i, q) for i, q in self.items]


def make_order(items: tuple[tuple[str, int], ...], at: datetime, suffix: str, shipping: float = 5.0) -> Order:
    """An order as the site builds it: transaction_id TL-<base36 ms>-<suffix>, tax at the site's rate."""
    value = round(sum(PRODUCTS[i]["price"] * q for i, q in items), 2)
    return Order(f"TL-{_base36(int(at.timestamp() * 1000))}-{suffix}", items, value, round(value * TAX_RATE, 2), shipping, at)


@dataclass(frozen=True)
class Fixture:
    tables: dict[str, list[dict]]
    transaction_id: str  # device B's order, the one sent twice
    order_items: tuple[tuple[str, int], ...]  # (item_id, quantity)
    order_value: float
    order_tax: float
    order_shipping: float
    duplicated_rows: int  # exact duplicate export rows planted in daily tables
    anonymous_events_on_a: int  # staged (deduplicated) events on device A without a user_id
    cookieless_orders: tuple[tuple[Order, str | None], ...] = ()  # (order, user_id) placed with consent denied
    cookieless_events: int = 0  # staged events with no user_pseudo_id / session


def build_fixture() -> Fixture:
    t0 = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc)  # noqa: E731

    # --- device A, day 1: anonymous, from a paid search ad -----------------------------
    # An auto-tagged ad click managed in Search Ads 360: a gclid and no utm_*, so nothing manual is
    # collected and the manual_campaign record is empty; cross_channel_campaign names the campaign.
    a1 = Session(DEVICE_A, 1, t0(DAY1, 14), None, FALL_LAUNCH, cross=cross_channel(FALL_LAUNCH, "Search Ads 360"))
    landing = "/category/apparel?gclid=Cj0KCQjwfixtureA1"
    col = {"gclid": "Cj0KCQjwfixtureA1"}
    apparel = [item(i, list_name="Apparel", index=n) for n, i in enumerate(["TL-APP-001", "TL-APP-002", "TL-APP-003", "TL-APP-004", "TL-APP-005"])]
    hoodie = [item("TL-APP-003", 1)]
    day1 = [
        event(a1, 0.000, "first_visit", landing, "Apparel", collected=col),
        event(a1, 0.001, "session_start", landing, "Apparel", collected=col),
        event(a1, 0.002, "page_view", landing, "Apparel", collected=col),
        event(a1, 1.0, "view_item_list", landing, "Apparel", collected=col, items=apparel, ecommerce=_ecom(apparel), params={"item_list_name": "Apparel"}),
        event(a1, 12.0, "select_item", landing, "Apparel", collected=col, items=[apparel[2]], ecommerce=_ecom([apparel[2]])),
        event(a1, 12.5, "page_view", "/product/TL-APP-003", "Pipeline Hoodie", referrer=HOST + landing),
        event(a1, 12.6, "view_item", "/product/TL-APP-003", "Pipeline Hoodie", items=[item("TL-APP-003")], ecommerce=_ecom([item("TL-APP-003")]), params={"currency": "USD", "value": 58.0}),
        event(a1, 40.0, "add_to_cart", "/product/TL-APP-003", "Pipeline Hoodie", items=hoodie, ecommerce=_ecom(hoodie), params={"currency": "USD", "value": 58.0}),
    ]
    anonymous_a = len(day1)
    day1.append(json.loads(json.dumps(day1[2])))  # the export repeated the landing page_view exactly

    # --- device A, day 2: back from a retargeting ad, signs up as U ---------------------
    a2 = Session(DEVICE_A, 2, t0(DAY2, 9), RETARGET, FALL_LAUNCH)
    landing = _utm("/product/TL-APP-003", RETARGET, {"utm_content": "carousel"})
    col = _collected(RETARGET, content="carousel")
    home = [item(i, list_name="All products", index=n) for n, i in enumerate(PRODUCTS)]
    a2_rows = [
        event(a2, 0.000, "session_start", landing, "Pipeline Hoodie", collected=col),
        event(a2, 0.001, "page_view", landing, "Pipeline Hoodie", collected=col),
        event(a2, 0.5, "view_item", landing, "Pipeline Hoodie", collected=col, items=[item("TL-APP-003")], ecommerce=_ecom([item("TL-APP-003")]), params={"currency": "USD", "value": 58.0}),
        event(a2, 20.0, "page_view", "/signin", "Sign in", referrer=HOST + landing),
        # The site pushes user_id before sign_up, so sign_up already carries it.
        event(a2, 45.0, "sign_up", "/signin", "Sign in", user_id=U, params={"method": "email"}),
        event(a2, 46.0, "page_view", "/", "All products", user_id=U, referrer=HOST + "/signin"),
        event(a2, 46.1, "view_item_list", "/", "All products", user_id=U, items=home, ecommerce=_ecom(home), params={"item_list_name": "All products"}),
    ]
    anonymous_a += 4

    # --- device B, day 2: from the newsletter, logs in as U, buys; purchase sent twice ------
    b1 = Session(DEVICE_B, 1, t0(DAY2, 18, 30), NEWSLETTER, NEWSLETTER)
    landing = _utm("/", NEWSLETTER, {"utm_content": "hero"})
    col = _collected(NEWSLETTER, content="hero")
    order = (("TL-DRK-003", 2), ("TL-STK-002", 1))
    lines = [item(i, q) for i, q in order]
    b_order = make_order(order, b1.start + timedelta(seconds=125.1), "0FIXTUR")  # Ground shipping
    value, tax, shipping, transaction_id = b_order.value, b_order.tax, b_order.shipping, b_order.transaction_id
    assert value == _value(lines)
    bought, purchase_ecom, purchase_params = b_order.bought(), b_order.ecommerce(), b_order.params()
    confirmation = f"/order/{transaction_id}"
    tumbler, stickers = [item("TL-DRK-003", 2)], [item("TL-STK-002", 1)]
    b1_rows = [
        event(b1, 0.000, "first_visit", landing, "All products", collected=col),
        event(b1, 0.001, "session_start", landing, "All products", collected=col),
        event(b1, 0.002, "page_view", landing, "All products", collected=col),
        event(b1, 0.5, "view_item_list", landing, "All products", collected=col, items=home, ecommerce=_ecom(home), params={"item_list_name": "All products"}),
        event(b1, 10.0, "page_view", "/signin", "Sign in", referrer=HOST + landing),
        event(b1, 30.0, "login", "/signin", "Sign in", user_id=U, params={"method": "email"}),
        event(b1, 31.0, "page_view", "/", "All products", user_id=U, referrer=HOST + "/signin"),
        event(b1, 31.1, "view_item_list", "/", "All products", user_id=U, items=home, ecommerce=_ecom(home), params={"item_list_name": "All products"}),
        event(b1, 40.0, "select_item", "/", "All products", user_id=U, items=[home[7]], ecommerce=_ecom([home[7]])),
        event(b1, 40.5, "page_view", "/product/TL-DRK-003", "Cold Brew Tumbler", user_id=U, referrer=HOST + "/"),
        event(b1, 40.6, "view_item", "/product/TL-DRK-003", "Cold Brew Tumbler", user_id=U, items=[item("TL-DRK-003")], ecommerce=_ecom([item("TL-DRK-003")]), params={"currency": "USD", "value": 24.5}),
        event(b1, 55.0, "add_to_cart", "/product/TL-DRK-003", "Cold Brew Tumbler", user_id=U, items=tumbler, ecommerce=_ecom(tumbler), params={"currency": "USD", "value": 49.0}),
        event(b1, 70.0, "page_view", "/product/TL-STK-002", "Sticker Pack", user_id=U, referrer=HOST + "/product/TL-DRK-003"),
        event(b1, 70.1, "view_item", "/product/TL-STK-002", "Sticker Pack", user_id=U, items=[item("TL-STK-002")], ecommerce=_ecom([item("TL-STK-002")]), params={"currency": "USD", "value": 12.0}),
        event(b1, 78.0, "add_to_cart", "/product/TL-STK-002", "Sticker Pack", user_id=U, items=stickers, ecommerce=_ecom(stickers), params={"currency": "USD", "value": 12.0}),
        event(b1, 90.0, "page_view", "/cart", "Cart", user_id=U, referrer=HOST + "/product/TL-STK-002"),
        event(b1, 90.1, "view_cart", "/cart", "Cart", user_id=U, items=lines, ecommerce=_ecom(lines), params={"currency": "USD", "value": value}),
        event(b1, 100.0, "page_view", "/checkout", "Checkout", user_id=U, referrer=HOST + "/cart"),
        event(b1, 100.1, "begin_checkout", "/checkout", "Checkout", user_id=U, items=lines, ecommerce=_ecom(lines), params={"currency": "USD", "value": value}),
        event(b1, 110.0, "add_shipping_info", "/checkout", "Checkout", user_id=U, items=lines, ecommerce=_ecom(lines), params={"currency": "USD", "value": value, "shipping_tier": "Ground"}),
        event(b1, 115.0, "add_payment_info", "/checkout", "Checkout", user_id=U, items=lines, ecommerce=_ecom(lines), params={"currency": "USD", "value": value, "payment_type": "Credit Card"}),
        event(b1, 125.0, "page_view", confirmation, "Order confirmed", user_id=U, referrer=HOST + "/checkout"),
        event(b1, 125.1, "purchase", confirmation, "Order confirmed", user_id=U, items=bought, ecommerce=purchase_ecom, params=purchase_params),
        # The same order sent again four seconds later (a second tab, a cleared claim key):
        # a different export row, the same transaction_id. Stage 2 must count it once.
        event(b1, 129.1, "purchase", confirmation, "Order confirmed", user_id=U, items=bought, ecommerce=purchase_ecom, params=purchase_params),
    ]

    # --- device D, day 2: a shared laptop -------------------------------------------------
    d1 = Session(DEVICE_D, 1, t0(DAY2, 10), DIRECT, DIRECT)
    d2 = Session(DEVICE_D, 2, t0(DAY2, 15), DIRECT, DIRECT)
    d3 = Session(DEVICE_D, 3, t0(DAY2, 21), DIRECT, DIRECT)
    notebook = [item("TL-OFF-001")]
    d_rows = [
        event(d1, 0.000, "first_visit", "/", "All products"),
        event(d1, 0.001, "session_start", "/", "All products"),
        event(d1, 0.002, "page_view", "/", "All products"),
        event(d1, 15.0, "page_view", "/signin", "Sign in", referrer=HOST + "/"),
        event(d1, 30.0, "login", "/signin", "Sign in", user_id=V, params={"method": "email"}),
        event(d1, 31.0, "page_view", "/", "All products", user_id=V, referrer=HOST + "/signin"),
        event(d1, 50.0, "page_view", "/product/TL-OFF-001", "Dot Grid Notebook", user_id=V, referrer=HOST + "/"),
        event(d1, 50.1, "view_item", "/product/TL-OFF-001", "Dot Grid Notebook", user_id=V, items=notebook, ecommerce=_ecom(notebook), params={"currency": "USD", "value": 12.0}),
        # V signs out (the site pushes user_id: null, no event); later W signs in on the same laptop.
        event(d2, 0.000, "session_start", "/", "All products"),
        event(d2, 0.001, "page_view", "/", "All products"),
        event(d2, 10.0, "page_view", "/signin", "Sign in", referrer=HOST + "/"),
        event(d2, 25.0, "login", "/signin", "Sign in", user_id=W, params={"method": "email"}),
        event(d2, 26.0, "page_view", "/", "All products", user_id=W, referrer=HOST + "/signin"),
        # Nobody signs in: this session cannot be given to V or W.
        event(d3, 0.000, "session_start", "/", "All products", engaged=False),
        event(d3, 0.001, "page_view", "/", "All products", engaged=False),
    ]

    # --- device C, day 3: organic, only in the streaming table ------------------------------
    c1 = Session(DEVICE_C, 1, t0(DAY3, 8, 15), ORGANIC, None)
    col = {"manual_source": "google", "manual_medium": "organic"}
    bags = [item(i, list_name="All products", index=n) for n, i in enumerate(PRODUCTS)]
    c_rows = [
        event(c1, 0.000, "first_visit", "/", "All products", referrer="https://www.google.com/", collected=col),
        event(c1, 0.001, "session_start", "/", "All products", referrer="https://www.google.com/", collected=col),
        event(c1, 0.002, "page_view", "/", "All products", referrer="https://www.google.com/", collected=col),
        event(c1, 0.5, "view_item_list", "/", "All products", items=bags, ecommerce=_ecom(bags), params={"item_list_name": "All products"}),
        event(c1, 20.0, "select_item", "/", "All products", items=[bags[10]], ecommerce=_ecom([bags[10]])),
        event(c1, 20.5, "page_view", "/product/TL-BAG-002", "Everyday Backpack", referrer=HOST + "/"),
        event(c1, 20.6, "view_item", "/product/TL-BAG-002", "Everyday Backpack", items=[item("TL-BAG-002")], ecommerce=_ecom([item("TL-BAG-002")]), params={"currency": "USD", "value": 72.0}),
    ]

    # --- consent denied, day 2: cookieless pings (no user_pseudo_id, no ga_session_id) ------------
    # Several write-ups report that the export has neither id on rows with analytics_storage = No;
    # the model must keep these purchases as orders, with a person, and without a session.
    def cookieless_visit(start: datetime, items: tuple[tuple[str, int], ...], suffix: str, user_id: str | None) -> tuple[list[dict], Order]:
        s = Session(COOKIELESS, 0, start, None, None, cookieless=True)
        o = make_order(items, start + timedelta(seconds=90.1), suffix)
        product = items[0][0]
        name = PRODUCTS[product]["item_name"]
        rows = [
            event(s, 0.0, "page_view", f"/product/{product}", name, user_id=user_id),
            event(s, 0.1, "view_item", f"/product/{product}", name, user_id=user_id, items=[item(product)], ecommerce=_ecom([item(product)]), params={"currency": "USD", "value": PRODUCTS[product]["price"]}),
            event(s, 20.0, "add_to_cart", f"/product/{product}", name, user_id=user_id, items=o.lines(), ecommerce=_ecom(o.lines()), params={"currency": "USD", "value": o.value}),
            event(s, 60.0, "begin_checkout", "/checkout", "Checkout", user_id=user_id, items=o.lines(), ecommerce=_ecom(o.lines()), params={"currency": "USD", "value": o.value}),
            event(s, 90.0, "page_view", f"/order/{o.transaction_id}", "Order confirmed", user_id=user_id, referrer=HOST + "/checkout"),
            event(s, 90.1, "purchase", f"/order/{o.transaction_id}", "Order confirmed", user_id=user_id, items=o.bought(), ecommerce=o.ecommerce(), params=o.params()),
        ]
        return rows, o

    e_rows, e_order = cookieless_visit(t0(DAY2, 20), (("TL-DRK-001", 2),), "0COOKIE", None)
    f_rows, f_order = cookieless_visit(t0(DAY2, 22), (("TL-BAG-001", 1), ("TL-STK-001", 3)), "0COOKIV", V)

    day2 = sorted(a2_rows + b1_rows + d_rows + e_rows + f_rows, key=lambda r: r["event_timestamp"])
    tables = {
        f"events_{DAY1:%Y%m%d}": day1,
        f"events_{DAY2:%Y%m%d}": day2,
        f"events_intraday_{DAY2:%Y%m%d}": json.loads(json.dumps(day2)),
        f"events_intraday_{DAY3:%Y%m%d}": c_rows,
    }
    return Fixture(
        tables, transaction_id, order, value, tax, shipping, duplicated_rows=1, anonymous_events_on_a=anonymous_a,
        cookieless_orders=((e_order, None), (f_order, V)), cookieless_events=len(e_rows) + len(f_rows),
    )


# ---------------------------------------------------------------------------------
# Expectations: written from the scenario, not derived from the models


@dataclass(frozen=True)
class ExpectedSession:
    device: str
    number: int
    date: date
    person_id: str
    identity_rule: str
    campaign: dict
    events: set[str]
    purchase_events: int = 0
    orders: int = 0


def anon(device: str) -> str:
    return f"anon:tagline_site:{device}"


def expected_sessions(fx: Fixture) -> list[ExpectedSession]:
    """In session_start_at order."""
    ecommerce = {"view_item", "add_to_cart", "begin_checkout"}
    return [
        ExpectedSession(DEVICE_A, 1, DAY1, U, "device_user_id", FALL_LAUNCH, {"view_item", "add_to_cart"}),
        ExpectedSession(DEVICE_A, 2, DAY2, U, "signed_in_session", RETARGET, {"view_item"}),
        ExpectedSession(DEVICE_D, 1, DAY2, V, "signed_in_session", DIRECT, {"view_item"}),
        ExpectedSession(DEVICE_D, 2, DAY2, W, "signed_in_session", DIRECT, set()),
        ExpectedSession(DEVICE_B, 1, DAY2, U, "signed_in_session", NEWSLETTER, ecommerce, purchase_events=2, orders=1),
        ExpectedSession(DEVICE_D, 3, DAY2, anon(DEVICE_D), "shared_device_anonymous", DIRECT, set()),
        ExpectedSession(DEVICE_C, 1, DAY3, anon(DEVICE_C), "anonymous_device", ORGANIC, {"view_item"}),
    ]


def expected_staged_rows(fx: Fixture) -> dict[str, int]:
    daily = sum(len(rows) for name, rows in fx.tables.items() if not name.startswith("events_intraday_"))
    intraday_only = len(fx.tables[f"events_intraday_{DAY3:%Y%m%d}"])
    return {"daily": daily - fx.duplicated_rows, "daily_export_rows": daily, "intraday": intraday_only}


def expected_campaign_rows(fx: Fixture) -> list[tuple]:
    """mart_campaign_daily for tagline_site: sessions from the scenario, cost from the same generator that loaded campaign_costs."""
    sessions = expected_sessions(fx)
    first, last = min(s.date for s in sessions), max(s.date for s in sessions)
    grid: dict[tuple, dict] = {}
    for s in sessions:
        key = (s.date.isoformat(), s.campaign["source"], s.campaign["medium"], s.campaign["campaign_name"])
        g = grid.setdefault(key, {"sessions": 0, "orders": 0, "revenue": 0.0, "cost": None})
        g["sessions"] += 1
        g["orders"] += s.orders
        g["revenue"] += fx.order_value if s.orders else 0.0
    for c in campaign_cost_rows():
        if c["source"] != "tagline_site" or not first.isoformat() <= c["cost_date"] <= last.isoformat():
            continue
        key = (c["cost_date"], c["session_source"], c["session_medium"], c["session_campaign"])
        g = grid.setdefault(key, {"sessions": 0, "orders": 0, "revenue": 0.0, "cost": None})
        g["cost"] = round((g["cost"] or 0) + c["cost_usd"], 2)
    out = []
    for key in sorted(grid):
        g = grid[key]
        roas = None if g["cost"] is None else round(g["revenue"] / g["cost"], 4)
        cpo = None if g["cost"] is None or not g["orders"] else round(g["cost"] / g["orders"], 2)
        out.append((*key, g["sessions"], g["orders"], round(g["revenue"], 2), g["cost"], roas, cpo))
    return out


def expected_order_lines(fx: Fixture) -> list[tuple]:
    """Every order's lines, in order time: device B's, then the two cookieless orders."""
    costs = {p["item_id"]: p["unit_cost_usd"] for p in product_rows()}
    out = []
    for order_id, items in [(fx.transaction_id, fx.order_items), *((o.transaction_id, o.items) for o, _ in fx.cookieless_orders)]:
        for n, (item_id, qty) in enumerate(items, start=1):
            revenue = round(PRODUCTS[item_id]["price"] * qty, 2)
            cost = round(costs[item_id] * qty, 2)
            out.append((order_id, n, item_id, qty, revenue, True, costs[item_id], cost, round(revenue - costs[item_id] * qty, 2)))
    return out


def expected_orders(fx: Fixture) -> list[tuple]:
    """fct_orders for tagline_site, in order time."""
    b = (fx.transaction_id, DEVICE_B, U, "signed_in_purchase", "newsletter", "email", "newsletter_oct", fx.order_value,
         fx.order_tax, fx.order_shipping, sum(q for _, q in fx.order_items), len(fx.order_items), 1)
    rest = [
        (o.transaction_id, None, uid or f"cookieless:tagline_site:{o.transaction_id}", "signed_in_purchase" if uid else "cookieless",
         None, None, None, o.value, o.tax, o.shipping, sum(q for _, q in o.items), len(o.items), 0)
        for o, uid in fx.cookieless_orders
    ]
    return [b, *rest]


def expected_funnel(fx: Fixture) -> list[tuple]:
    by_day: dict[date, list[int]] = {}
    for s in expected_sessions(fx):
        v = by_day.setdefault(s.date, [0, 0, 0, 0, 0, 0])
        vi, ac, bc = "view_item" in s.events, "add_to_cart" in s.events, "begin_checkout" in s.events
        v[0] += 1
        v[1] += vi
        v[2] += vi and ac
        v[3] += vi and ac and bc
        v[4] += vi and ac and bc and s.orders > 0
        v[5] += s.orders > 0
    return [(d.isoformat(), *v) for d, v in sorted(by_day.items())]


# ---------------------------------------------------------------------------------
# Schema


def export_schema(sample_fields: list) -> list:
    """The public sample's schema plus the traffic-source records current exports have, with current types."""
    from google.cloud.bigquery import SchemaField

    fields = [f.to_api_repr() for f in sample_fields]
    by_name = {f["name"]: f for f in fields}
    by_name["stream_id"]["type"] = "STRING"
    for sub in by_name["privacy_info"]["fields"]:
        if sub["name"] in ("analytics_storage", "ads_storage"):
            sub["type"] = "STRING"

    def s(name: str) -> dict:
        return {"name": name, "type": "STRING", "mode": "NULLABLE"}

    def record(name: str, subs: list[dict]) -> dict:
        return {"name": name, "type": "RECORD", "mode": "NULLABLE", "fields": subs}

    fields.append(
        record(
            "collected_traffic_source",
            [s(n) for n in ("manual_campaign_id", "manual_campaign_name", "manual_source", "manual_medium", "manual_term",
                            "manual_content", "manual_source_platform", "manual_creative_format", "manual_marketing_tactic",
                            "gclid", "dclid", "srsltid")],
        )
    )
    fields.append(
        record(
            "session_traffic_source_last_click",
            [
                record("manual_campaign", [s(n) for n in ("campaign_id", "campaign_name", "source", "medium", "term", "content",
                                                          "source_platform", "creative_format", "marketing_tactic")]),
                record("google_ads_campaign", [s(n) for n in ("customer_id", "account_name", "campaign_id", "campaign_name",
                                                              "ad_group_id", "ad_group_name")]),
                record("cross_channel_campaign", [s(n) for n in ("campaign_id", "campaign_name", "source", "medium",
                                                                 "source_platform", "default_channel_group", "primary_channel_group")]),
            ],
        )
    )
    return [SchemaField.from_api_repr(f) for f in fields]


# ---------------------------------------------------------------------------------
# Running


def _norm(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _rows(rows) -> list[tuple]:
    return [tuple(_norm(v) for v in r.values()) for r in rows]


def verifications(cfg: Config, fx: Fixture) -> list[tuple[str, str, str, list[tuple]]]:
    """(step, name, SQL, expected rows). Every query reads only the built tables."""
    stg = f"`{cfg.project}.{cfg.staging_dataset}"
    mart = f"`{cfg.project}.{cfg.marts_dataset}"
    staged = expected_staged_rows(fx)
    sessions = expected_sessions(fx)
    return [
        (
            "v1_sources",
            "both sources in stg_events (the duplicate row collapsed; the streaming copy of a day with a daily table ignored)",
            f"SELECT source, export_table, COUNT(*) AS events, SUM(export_row_count) AS export_rows "
            f"FROM {stg}.stg_events` GROUP BY source, export_table ORDER BY source, export_table",
            [
                ("ga4_sample", "sample", SAMPLE_EVENTS, SAMPLE_EVENTS),
                ("tagline_site", "daily", staged["daily"], staged["daily_export_rows"]),
                ("tagline_site", "intraday", staged["intraday"], staged["intraday"]),
            ],
        ),
        (
            "v2_identity_map",
            "identity map: A and B are person U (cross-device), C anonymous, D shared and not merged",
            f"SELECT user_pseudo_id, person_id, identity_rule, user_id_count, person_device_count "
            f"FROM {stg}.int_identity` WHERE source = 'tagline_site' ORDER BY user_pseudo_id",
            [
                (DEVICE_A, U, "user_id", 1, 2),
                (DEVICE_B, U, "user_id", 1, 2),
                (DEVICE_C, anon(DEVICE_C), "anonymous_device", 0, 1),
                (DEVICE_D, anon(DEVICE_D), "shared_device", 2, 1),
            ],
        ),
        (
            "v3_device_a_stitched",
            "device A's anonymous events (day 1, and day 2 before sign-up) all belong to person U",
            f"SELECT COUNT(*) AS anonymous_events, COUNTIF(s.person_id = '{U}') AS as_person_u, COUNT(DISTINCT e.session_key) AS sessions "
            f"FROM {stg}.stg_events` AS e JOIN {mart}.fct_sessions` AS s USING (source, session_key) "
            f"WHERE e.source = 'tagline_site' AND e.user_pseudo_id = '{DEVICE_A}' AND e.user_id IS NULL",
            [(fx.anonymous_events_on_a, fx.anonymous_events_on_a, 2)],
        ),
        (
            "v4_sessions",
            "sessions: person, identity rule and session source / medium / campaign",
            f"SELECT user_pseudo_id, ga_session_number, session_date, person_id, identity_rule, session_source, session_medium, "
            f"session_campaign, traffic_source_basis, purchase_events, orders "
            f"FROM {mart}.fct_sessions` WHERE source = 'tagline_site' ORDER BY session_start_at",
            [
                (s.device, s.number, s.date.isoformat(), s.person_id, s.identity_rule, s.campaign["source"], s.campaign["medium"],
                 s.campaign["campaign_name"], "session_traffic_source_last_click", s.purchase_events, s.orders)
                for s in sessions
            ],
        ),
        (
            "v5_duplicate_purchase",
            "the purchase sent twice is two events and one order (plus the two cookieless purchases, one order each)",
            f"SELECT COUNT(*) AS purchase_events, COUNTIF(is_duplicate_purchase) AS duplicates, COUNT(DISTINCT order_id) AS orders "
            f"FROM {stg}.stg_events` WHERE source = 'tagline_site' AND event_name = 'purchase'",
            [(2 + len(fx.cookieless_orders), 1, 1 + len(fx.cookieless_orders))],
        ),
        (
            "v6_orders",
            "fct_orders: device B's order is person U's (its purchase carried U), attributed to the newsletter; the "
            "cookieless orders have no session, one a per-order anonymous person, one person V",
            f"SELECT order_id, user_pseudo_id, person_id, identity_rule, session_source, session_medium, session_campaign, "
            f"revenue_usd, tax_usd, shipping_usd, item_quantity, line_count, duplicate_purchase_events "
            f"FROM {mart}.fct_orders` WHERE source = 'tagline_site' ORDER BY ordered_at",
            expected_orders(fx),
        ),
        (
            "v7_order_items",
            "fct_order_items: lines enriched with the catalog's synthetic unit cost",
            f"SELECT order_id, line_number, item_id, quantity, line_revenue_usd, is_catalog_product, unit_cost_usd, line_cost_usd, gross_margin_usd "
            f"FROM {mart}.fct_order_items` WHERE source = 'tagline_site' ORDER BY ordered_at, line_number",
            expected_order_lines(fx),
        ),
        (
            "v8_campaign_daily",
            "mart_campaign_daily: sessions, orders and revenue by campaign, with synthetic cost, ROAS and cost per order",
            f"SELECT CAST(date AS STRING) AS date, session_source, session_medium, session_campaign, sessions, orders, revenue_usd, cost_usd, "
            f"roas, cost_per_order FROM {mart}.mart_campaign_daily` WHERE source = 'tagline_site' "
            f"ORDER BY date, session_source, session_medium, session_campaign",
            expected_campaign_rows(fx),
        ),
        (
            "v9_funnel_daily",
            "mart_funnel_daily: closed funnel per day",
            f"SELECT CAST(date AS STRING) AS date, sessions, view_item_sessions, add_to_cart_sessions, begin_checkout_sessions, "
            f"purchase_sessions, converted_sessions FROM {mart}.mart_funnel_daily` WHERE source = 'tagline_site' ORDER BY date",
            expected_funnel(fx),
        ),
        (
            "v10_cookieless",
            "consent-denied cookieless rows: staged with no session, in no fct_sessions row, not in int_identity",
            f"SELECT COUNTIF(session_key IS NULL) AS events_without_session, COUNTIF(session_key IS NULL AND analytics_storage = 'No') AS denied, "
            f"COUNTIF(user_pseudo_id IS NULL) AS without_device, "
            f"(SELECT COUNT(*) FROM {stg}.int_identity` WHERE source = 'tagline_site' AND user_pseudo_id IS NULL) AS identity_rows_without_device "
            f"FROM {stg}.stg_events` WHERE source = 'tagline_site'",
            [(fx.cookieless_events, fx.cookieless_events, fx.cookieless_events, 0)],
        ),
        (
            "v11_cross_channel",
            "device A's first session: a gclid click with no utm_* and an empty manual_campaign, attributed from cross_channel_campaign",
            f"SELECT DISTINCT session_last_click_source, session_last_click_medium, session_last_click_campaign, collected_source, collected_medium "
            f"FROM {stg}.stg_events` WHERE source = 'tagline_site' AND user_pseudo_id = '{DEVICE_A}' AND ga_session_number = 1",
            [("google", "cpc", "fall_launch", None, None)],
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", action="store_true", help="leave the fixture tables and the fixture build in place (the tables still expire in 24 h)")
    parser.add_argument("--costs-json", metavar="PATH", help="also write the cost table as JSON")
    args = parser.parse_args(argv)

    from tagline_pipeline import pipeline
    from tagline_pipeline.bq import BigQuery
    from tagline_pipeline.config import load_config
    from tagline_pipeline.costs import JobStat, format_cost_table, write_json

    cfg = load_config()
    if cfg.ga4_dataset == cfg.raw_dataset and cfg.ga4_table_prefix.startswith(TABLE_PREFIX):
        print(f"TAGLINE_GA4_DATASET points at the fixture's own tables ({cfg.raw_dataset}.{TABLE_PREFIX}*); unset it first", file=sys.stderr)
        return 2
    fixture_cfg = replace(cfg, ga4_dataset=cfg.raw_dataset, ga4_project=None, ga4_table_prefix=TABLE_PREFIX + "events_")
    bq = BigQuery(cfg)
    client = bq.client
    raw = f"{cfg.project}.{cfg.raw_dataset}"
    stats: list[JobStat] = []
    failures: list[str] = []
    fx = build_fixture()

    def say(msg: str = "") -> None:
        print(msg, flush=True)

    def fixture_tables() -> list:
        return [t for t in client.list_tables(raw) if t.table_id.startswith(TABLE_PREFIX)]

    # 0. tagline_raw exists (it holds the reference data too); leftovers from a crashed run are ours only if labelled
    bq.ensure_datasets()
    for t in fixture_tables():
        if (t.labels or {}).get("purpose") != "fixture":
            say(f"{raw}.{t.table_id} starts with {TABLE_PREFIX} but is not labelled purpose:fixture; refusing to touch it")
            return 2
        say(f"{raw}.{t.table_id} left over from an earlier run: deleting it")
        client.delete_table(t.reference, not_found_ok=True)

    built_with_fixture = False
    try:
        # 1. export-shaped tables in tagline_raw, each expiring in 24 hours
        schema = export_schema(client.get_table(SAMPLE_SCHEMA_TABLE).schema)
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        for name, rows in fx.tables.items():
            table_id = f"{raw}.{TABLE_PREFIX}{name}"
            stats.append(bq.load_json(name, table_id, rows, schema, "TEMPORARY SYNTHETIC fixture rows shaped like a GA4 export table, "
                                      "loaded and deleted by tagline/pipeline/tests/site_export_fixture.py."))
            table = client.get_table(table_id)
            table.expires = expires
            table.labels = {**(table.labels or {}), **LABELS}
            client.update_table(table, ["expires", "labels"])
            say(f"  loaded {TABLE_PREFIX}{name}: {len(rows)} rows (expires in 24 h)")

        # 2. build with the fixture, check, verify
        say(f"\nbuilding with the site export at {raw}.{fixture_cfg.ga4_table_prefix}*")
        built_with_fixture = True
        result = pipeline.build(fixture_cfg, bq, stats=stats)
        if result.site is None or result.site.daily != (f"{DAY1:%Y%m%d}", f"{DAY2:%Y%m%d}") or result.site.intraday_only != (f"{DAY3:%Y%m%d}",):
            failures.append(f"site tables classified as {result.site}")
        checks, _ = pipeline.run_checks(fixture_cfg, bq, result.site, stats=stats)
        say(pipeline.format_checks(checks))
        failures += [f"check {c.name}" for c in checks if c.failures]

        say("\nverifying the fixture's scenarios")
        for step, name, sql, expected in verifications(cfg, fx):
            rows, stat = bq.query(step, "fixture", sql)
            stats.append(stat)
            actual = _rows(rows)
            want = [tuple(_norm(v) for v in r) for r in expected]
            ok = actual == want
            say(f"{'PASS' if ok else 'FAIL'}  {name}")
            header = list(rows[0].keys()) if rows else []
            say("\n".join("        " + line for line in pipeline.format_rows([dict(r.items()) for r in rows]).splitlines()) if rows else "        (no rows)")
            if not ok:
                failures.append(name)
                say("        expected:")
                for r in want:
                    say("        " + ", ".join(f"{h}={v}" for h, v in zip(header, r)) if header else f"        {r}")
    finally:
        # 3. rebuild without the fixture, then drop its tables (never `return` in here: it would
        # swallow an exception on its way out)
        if args.keep:
            say(f"\n--keep: the {TABLE_PREFIX}* tables in {raw} and the fixture build are still in place; run `make build`, "
                f"then delete the tables (bq rm -f -t {cfg.project}:{cfg.raw_dataset}.<table>) or let them expire")
        else:
            restored = not built_with_fixture
            try:
                if built_with_fixture:
                    say("\nrebuilding with the normal configuration (TAGLINE_GA4_DATASET "
                        + (f"= {cfg.ga4_dataset})" if cfg.ga4_dataset else "unset)"))
                    result = pipeline.build(cfg, bq, stats=stats)
                    checks, _ = pipeline.run_checks(cfg, bq, result.site, stats=stats)
                    say(pipeline.format_checks(checks))
                    failures += [f"check {c.name} after the rebuild" for c in checks if c.failures]
                    if not cfg.has_site:
                        rows, stat = bq.query(
                            "no_site_rows_left", "fixture",
                            f"SELECT COUNT(*) AS n FROM `{cfg.project}.{cfg.staging_dataset}.stg_events` WHERE source = 'tagline_site'",
                        )
                        stats.append(stat)
                        left = rows[0]["n"]
                        say(f"{'PASS' if left == 0 else 'FAIL'}  tagline_site rows left in stg_events after the rebuild: {left}")
                        if left:
                            failures.append("site rows left after the rebuild")
                    restored = True
            finally:
                for t in fixture_tables():
                    client.delete_table(t.reference, not_found_ok=True)
                left_tables = [t.table_id for t in fixture_tables()]
                if left_tables:
                    failures.append(f"fixture tables still exist: {', '.join(left_tables)}")
                    say(f"FAIL  fixture tables still exist: {', '.join(left_tables)}")
                else:
                    say(f"deleted the {len(fx.tables)} {TABLE_PREFIX}* tables from {raw}")
                if not restored:
                    failures.append("the rebuild without the fixture did not finish")
                    say(f"FAIL  the rebuild without the fixture did not finish: {cfg.staging_dataset} and {cfg.marts_dataset} "
                        "may still hold the fixture's tagline_site rows. Run `make build`.")

    say("\nBigQuery jobs (bytes processed / billed, slot-ms, wall seconds):")
    say(format_cost_table(stats))
    if args.costs_json:
        write_json(stats, Path(args.costs_json))
    say(f"\n{'FIXTURE PASSED' if not failures else 'FIXTURE FAILED: ' + '; '.join(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
