import json
import re
from pathlib import Path

from tagline_pipeline.config import PRODUCTS_JSON, TAGLINE_DIR
from tagline_pipeline.reference import CAMPAIGNS, CATEGORY_MARGIN, MARGIN_JITTER, campaign_cost_rows, product_rows


def test_campaign_costs_are_deterministic_unique_and_synthetic():
    rows = campaign_cost_rows()
    assert rows == campaign_cost_rows()
    keys = [(r["cost_date"], r["source"], r["session_source"], r["session_medium"], r["session_campaign"]) for r in rows]
    assert len(keys) == len(set(keys))
    assert all(r["is_synthetic"] is True and r["cost_usd"] > 0 for r in rows)
    assert rows != campaign_cost_rows(seed="another-seed")


def test_email_campaigns_cost_only_on_send_days():
    rows = [r for r in campaign_cost_rows() if r["session_campaign"] == "BlackFriday_V1"]
    assert [r["cost_date"] for r in rows] == ["2020-11-27"]


def test_every_simulator_campaign_has_cost():
    """The simulator's UTM triples (simulator/src/plan.js) must be in campaign_costs, or mart_campaign_daily cannot join spend."""
    plan = (TAGLINE_DIR / "simulator" / "src" / "plan.js").read_text()
    block = plan[plan.index("export const CAMPAIGNS = {") : plan.index("}\n", plan.index("export const CAMPAIGNS = {"))]
    triples = set(re.findall(r"source: '([^']+)', medium: '([^']+)', campaign: '([^']+)'", block))
    assert len(triples) == 3
    costed = {(c.session_source, c.session_medium, c.session_campaign) for c in CAMPAIGNS if c.source == "tagline_site"}
    assert triples == costed


def test_products_cover_the_catalog_with_bounded_synthetic_costs():
    catalog = json.loads(Path(PRODUCTS_JSON).read_text())
    rows = product_rows()
    assert [r["item_id"] for r in rows] == [p["item_id"] for p in catalog]
    for r in rows:
        base = CATEGORY_MARGIN[r["item_category"]]
        assert base - MARGIN_JITTER - 1e-9 <= r["gross_margin_rate"] <= base + MARGIN_JITTER + 1e-9
        assert abs(r["unit_cost_usd"] - round(r["price_usd"] * (1 - r["gross_margin_rate"]), 2)) < 1e-9
        assert 0 < r["unit_cost_usd"] < r["price_usd"]
        assert r["cost_is_synthetic"] is True
