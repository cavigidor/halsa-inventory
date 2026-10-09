import os, sys, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest
import config as C
import dashboard_builder as db
from services import growth as GR, scoring as SC, scenarios as SCN, store as ST, context as CTX

DATA = os.path.join(ROOT, "data")

# ---------------- growth ----------------
def test_real_growth_basic():
    r = GR.calculate_real_growth(1_000_000, 1_150_000, 0.45)
    assert r["nominal_pct"] == 15.0
    # (1.15/1.45)-1 = -0.2069 -> -20.7%
    assert r["real_pct"] == pytest.approx(-20.7, abs=0.2)
    assert r["available"]

def test_real_growth_missing_inflation():
    r = GR.calculate_real_growth(100, 120, None)
    assert r["nominal_pct"] == 20.0
    assert r["real_pct"] is None and not r["available"] and r["reason"] == "no_inflation_rate"

def test_real_growth_zero_prior():
    r = GR.calculate_real_growth(0, 500, 0.4)
    assert not r["available"] and r["reason"] == "no_prior_period"

def test_financing_cost():
    assert GR.financing_cost(120000, 120, 0.5) == pytest.approx(19726.03, abs=1)
    assert GR.financing_cost(1000, 0, 0.5) == 0.0
    assert GR.financing_cost(1000, 120, None) is None

# ---------------- scoring ----------------
def test_bucket_age():
    asof = dt.date(2026, 8, 31)
    assert SC.bucket_age_months("Ağustos-2026", asof) == 0
    assert SC.bucket_age_months("Ocak-2026", asof) == 7
    assert SC.bucket_age_months("Temmuz-2025 ve öncesi", asof) == 13
    assert SC.bucket_age_months("—", asof) is None

def test_collections_bounds_and_monotonic():
    asof = dt.date(2026, 8, 31)
    s_small, _ = SC.collections_priority(10_000, 1_000_000, False, "Ağustos-2026", asof)
    s_big, _ = SC.collections_priority(600_000, 1_000_000, True, "Ocak-2025", asof)
    assert 0 <= s_small <= 100 and 0 <= s_big <= 100
    assert s_big > s_small  # bigger, older, active-and-owing scores higher

def test_buyer_overdue_penalty():
    good, _ = SC.buyer_score(80_000, 2026, True, True, has_overdue=False)
    bad, _ = SC.buyer_score(80_000, 2026, True, True, has_overdue=True)
    assert good - bad == pytest.approx(25.0, abs=0.1)

def test_action_priority_bounds():
    assert SC.action_priority(1,1,1,1) == 100.0
    assert SC.action_priority(0,0,0,0) == 0.0

# ---------------- scenarios ----------------
def test_scenario_fx_unavailable_without_supplier_data():
    r = SCN.run_scenario(receivables_total=1_000_000, financing_rate=0.5, inflation_rate=0.45,
                         fx_sensitive_inventory_value=None, assumptions=SCN.PRESETS["high"])
    assert r["is_forecast"] is False
    assert r["results"]["fx_inventory_replacement_delta"]["available"] is False
    assert r["results"]["receivables_funding_cost"]["available"] is True

def test_scenario_missing_inflation():
    r = SCN.run_scenario(receivables_total=1_000_000, financing_rate=0.5, inflation_rate=None,
                         fx_sensitive_inventory_value=None, assumptions={"avg_days_outstanding":120})
    assert r["results"]["receivables_real_erosion"]["available"] is False

# ---------------- store ----------------
def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DB", str(tmp_path / "t.db"))
    import importlib; importlib.reload(ST)
    ST.init_db()
    acts = [{"category":"collections","entity_type":"customer","entity_id":"120.1","priority":"high",
             "score":90,"title":"T","facts":["a","b"]}]
    r1 = ST.upsert_actions(acts); assert r1["added"] == 1
    r2 = ST.upsert_actions(acts); assert r2["skipped_existing"] == 1  # same content not re-added
    key = list(ST.get_statuses())[0]
    ST.set_status(key, "completed", "arandı")
    assert ST.get_statuses()[key]["status"] == "completed"
    r3 = ST.upsert_actions(acts); assert r3["added"] == 0  # completed not regenerated
    ST.add_memory("customer","120.1","contacted","aradık",next_followup="2026-09-20")
    assert ST.get_memory("customer","120.1")[0]["kind"] == "contacted"

# ---------------- context reconciliation (real data) ----------------
@pytest.fixture(scope="module")
def ctx():
    return CTX.build_context(DATA, inflation_rate=0.40)

def test_context_reconciles_overdue(ctx):
    coll, owed = db.m_collections(db._latest(DATA, db.is_aging))
    assert ctx["company"]["working_capital"]["overdue_total"] == round(sum(r["overdue"] for r in coll))

def test_context_reconciles_overstock(ctx):
    _, over, _ = db.m_stock(db._latest(DATA, db.is_stock))
    assert ctx["company"]["working_capital"]["overstock_value"] == round(sum(o["inv"] for o in over))

def test_context_topn_respected(ctx):
    assert len(ctx["collections"]) <= C.MAX_CONTEXT_ITEMS_PER_CATEGORY
    assert len(ctx["winback"]) <= C.MAX_CONTEXT_ITEMS_PER_CATEGORY

def test_actions_have_required_shape(ctx):
    assert len(ctx["actions"]) <= C.MAX_DAILY_ACTIONS
    for a in ctx["actions"]:
        assert 0 <= a["score"] <= 100
        assert a["requires_approval"] is True
        assert a["interpretation"] is None  # AI not run yet
        assert a["facts"] and isinstance(a["facts"], list)

def test_real_decline_absent_without_inflation():
    c = CTX.build_context(DATA, inflation_rate=None)
    assert c["config"]["inflation_source"] in ("unavailable", "manual_config")
    if c["config"]["inflation_rate"] is None:
        assert all(a["category"] != "real_decline" for a in c["actions"])
        assert c["company"]["working_capital"]["shrinking_real_count"] is None
