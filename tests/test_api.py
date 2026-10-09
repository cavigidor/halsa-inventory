import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest
from fastapi.testclient import TestClient

import app as APP
import schemas as S
from services.entity import ContextRepository
from services.agent import BusinessActionAgent
from services.ai.base import NullProvider
from services.ai.mock_provider import MockProvider
from services import store as ST


def make_context():
    """Küçük, elle yapılmış SENTETİK agent_context — gerçek müşteri/temsilci/ürün adı içermez."""
    return {
        "generated_at": "2026-08-31T10:00:00", "as_of_data_date": "2026-08-31",
        "source_files": {"stock": "s.xlsx", "sales": "sa.xlsx", "aging": "c.xlsx"},
        "config": {"max_context_items_per_category": 20, "inflation_rate": 0.40, "inflation_source": "manual"},
        "company": {"products_scanned": 100, "working_capital": {
            "overstock_value": 5_000_000, "overdue_total": 7_000_000, "low_stock_count": 10,
            "lapsed_count": 5, "shrinking_real_count": 3, "fx_exposure": {"available": False}}},
        "macro": {"available": False},
        "collections": [{"code": "120.1", "name": "Ornek Musteri A", "rep": "TEMSILCI-A", "overdue": 800000,
                         "oldest": "Ocak-2026", "priority_score": 90.0, "revenue_25_26": 2000000,
                         "still_active": True, "drivers": {"age_months": 7, "ratio": 0.4}}],
        "overstock": [{"code": "P1", "name": "SENTETIK-URUN-1", "stock": 3000, "inv": 300000, "buyers": 4,
                       "top_buyers": [{"code": "120.1", "name": "Ornek Musteri A", "spend": 50000, "last": 2026,
                                       "active_2026": True, "has_overdue": True, "related_brand": True, "score": 70}]}],
        "winback": [{"code": "120.2", "name": "Ornek Musteri B", "rep": "TEMSILCI-B", "sector": "C", "spend": 400000,
                     "last": 2025, "tier": 1, "overdue": 0,
                     "detail": {"years": [{"y": 2024, "spend": 200000, "orders": 5, "avg": 40000}],
                                "products": [{"name": "Sentetik Kalem", "spend": 100000, "qty": 500}]}}],
        "shrinking": [{"code": "120.3", "name": "Ornek Musteri C", "rep": "TEMSILCI-C", "sector": "AB",
                       "y2025": 1000000, "y2026": 400000, "delta": -600000, "pct": -60.0,
                       "nominal_pct": -60.0, "real_pct": -71.0, "real_available": True}],
        "low_stock": [{"code": "P2", "name": "SENTETIK-URUN-2", "stock": 0, "cov": -1.0, "min": 2.0}],
        "risk": [{"code": "120.1", "name": "Ornek Musteri A", "rep": "TEMSILCI-A", "overdue": 800000, "rev": 2000000,
                  "active": "Evet", "ratio": 40, "closed": False}],
        "actions": [
            {"category": "collections", "entity_type": "customer", "entity_id": "120.1",
             "title": "TAHSİLAT — Ornek Musteri A", "facts": ["Vadesi geçen: ₺800.000"], "drivers": {"age_months": 7, "ratio": 0.4},
             "score": 88.0, "priority": "critical", "confidence": "high", "requires_approval": True,
             "interpretation": None, "recommendation": None},
            {"category": "winback", "entity_type": "customer", "entity_id": "120.2",
             "title": "GERİ KAZANIM — Ornek Musteri B", "facts": ["Toplam geçmiş harcama: ₺400.000"], "drivers": {},
             "score": 60.0, "priority": "medium", "confidence": "high", "requires_approval": True,
             "interpretation": None, "recommendation": None},
        ],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DB", str(tmp_path / "state.db"))
    import importlib; importlib.reload(ST)
    ST.init_db()
    APP.app.state.repo = ContextRepository(context=make_context())
    APP.app.state.provider = MockProvider()
    APP.app.state.agent = BusinessActionAgent(APP.app.state.provider)
    # reset store module used by app to the reloaded one
    APP.ST = ST
    return TestClient(APP.app)


@pytest.fixture
def client_no_ai(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_DB", str(tmp_path / "state2.db"))
    import importlib; importlib.reload(ST)
    ST.init_db()
    APP.app.state.repo = ContextRepository(context=make_context())
    APP.app.state.provider = NullProvider("AI provider is not configured.")
    APP.app.state.agent = BusinessActionAgent(APP.app.state.provider)
    APP.ST = ST
    return TestClient(APP.app)


# ---- launches / health ----
def test_health_no_ai(client_no_ai):
    r = client_no_ai.get("/api/health"); assert r.status_code == 200
    assert r.json()["ai_available"] is False

def test_context(client):
    r = client.get("/api/context"); assert r.status_code == 200
    j = r.json()
    assert j["company"]["working_capital"]["overdue_total"] == 7_000_000
    assert j["counts"]["actions"] == 2

# ---- scenario deterministic (works without AI) ----
def test_scenario(client_no_ai):
    r = client_no_ai.post("/api/scenario", json={"usdtry_change_pct": 20, "financing_rate": 40, "inflation_rate": 35})
    assert r.status_code == 200
    res = r.json()["results"]
    assert res["fx_inventory_replacement_delta"]["available"] is False
    assert res["receivables_funding_cost"]["available"] is True
    assert r.json()["is_forecast"] is False

# ---- actions with and without AI ----
def test_actions_ai_unavailable(client_no_ai):
    r = client_no_ai.get("/api/agent/actions"); assert r.status_code == 200
    j = r.json()
    assert j["ai_available"] is False
    assert all(a["interpretation"] is None for a in j["actions"])
    assert j["count"] == 2

def test_actions_with_mock_ai(client):
    r = client.get("/api/agent/actions"); assert r.status_code == 200
    j = r.json()
    assert j["ai_available"] is True
    assert all(a["interpretation"] and a["recommendation"] for a in j["actions"])

# ---- customer / product ----
def test_customer_found(client):
    r = client.get("/api/agent/customer/120.2"); assert r.status_code == 200
    assert r.json()["ai_available"] is True and r.json()["summary"]

def test_customer_not_found(client):
    assert client.get("/api/agent/customer/NOPE").status_code == 404

def test_product_found(client):
    r = client.get("/api/agent/product/P1"); assert r.status_code == 200
    assert r.json()["best_buyers"]

def test_product_not_found(client):
    assert client.get("/api/agent/product/NOPE").status_code == 404

# ---- draft message (returns draft only, never sends) ----
def test_draft_message(client):
    r = client.post("/api/agent/draft-message", json={"customer_code": "120.2", "message_type": "winback"})
    assert r.status_code == 200
    assert r.json()["available"] is True and r.json()["message"]

def test_draft_message_no_ai(client_no_ai):
    r = client_no_ai.post("/api/agent/draft-message", json={"customer_code": "120.2", "message_type": "email"})
    assert r.status_code == 200 and r.json()["available"] is False

# ---- macro fallback ----
def test_macro_unavailable(client):
    r = client.get("/api/macro"); assert r.status_code == 200 and r.json()["available"] is False

# ---- action state + no-regenerate ----
def test_action_lifecycle(client):
    aid = client.get("/api/agent/actions").json()["actions"][0]["action_id"]
    assert client.post(f"/api/actions/{aid}/complete", json={"notes": "arandı"}).json()["status"] == "completed"
    # completed action should not reappear (same content)
    ids2 = [a["action_id"] for a in client.get("/api/agent/actions").json()["actions"]]
    assert aid not in ids2

def test_action_defer_future_hidden(client):
    acts = client.get("/api/agent/actions").json()["actions"]
    aid = acts[0]["action_id"]
    client.post(f"/api/actions/{aid}/defer", json={"until": "2099-01-01"})
    ids2 = [a["action_id"] for a in client.get("/api/agent/actions").json()["actions"]]
    assert aid not in ids2

def test_action_dismiss(client):
    aid = client.get("/api/agent/actions").json()["actions"][-1]["action_id"]
    client.post(f"/api/actions/{aid}/dismiss")
    ids2 = [a["action_id"] for a in client.get("/api/agent/actions").json()["actions"]]
    assert aid not in ids2

# ---- malformed AI handled (provider returns error -> treated as unavailable enrichment) ----
def test_malformed_ai(client, monkeypatch):
    from services.ai.base import AIResult
    def bad(*a, **k): return AIResult(available=True, error="validation:boom", data=None)
    APP.app.state.agent.provider.generate_structured = bad
    r = client.get("/api/agent/actions"); assert r.status_code == 200
    # enrichment failed but actions still returned deterministically
    assert all(a["interpretation"] is None for a in r.json()["actions"])
