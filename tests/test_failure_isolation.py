"""Total AI-provider failure must not affect deterministic analytics; the provider only ever
receives a small evidence packet (no DataFrames, files, databases or whole tables)."""
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import schemas as S
from services import evidence as EV
from services import context as CTX
from services.agent import BusinessActionAgent
from services.ai.base import AIProvider, AIResult
from services.entity import ContextRepository


class Exploding(AIProvider):
    """Every call fails at the transport level, in a different way each time."""
    name, model = "exploding", "x"
    CATS = ["timeout", "rate_limit", "provider_5xx", "auth", "connection", "malformed"]

    def __init__(self):
        self.i = 0

    def is_available(self):
        return True

    def generate_structured(self, system_prompt, payload, response_model):
        cat = self.CATS[self.i % len(self.CATS)]
        self.i += 1
        return AIResult(available=True, error=f"simulated {cat}", error_category=cat)


class Raising(AIProvider):
    """A provider bug that raises instead of returning an AIResult."""
    name, model = "raising", "x"

    def is_available(self):
        return True

    def generate_structured(self, *a, **k):
        raise RuntimeError("provider bug")


class Spy(AIProvider):
    name, model = "spy", "spy-1"

    def __init__(self):
        self.payloads = []

    def is_available(self):
        return True

    def generate_structured(self, system_prompt, payload, response_model):
        self.payloads.append(payload)
        return AIResult(available=True, error="spy only", error_category="malformed")


@pytest.fixture
def make_client(synthetic_ctx):
    def _mk(provider):
        APP.app.state.repo = ContextRepository(context=synthetic_ctx)
        APP.app.state.provider = provider
        APP.app.state.agent = BusinessActionAgent(provider)
        return TestClient(APP.app, raise_server_exceptions=False, headers={"X-StockAgent": "1"})
    return _mk


def _deterministic_snapshot(client):
    ctx = client.get("/api/context").json()
    scen = client.post("/api/scenario", json={"financing_rate": 40, "inflation_rate": 35}).json()
    acts = client.get("/api/agent/actions").json()
    strip = [{k: a[k] for k in ("action_id", "category", "entity_id", "facts", "score", "priority")}
             for a in acts["actions"]]
    return ctx["company"], ctx["counts"], scen["results"], strip


def test_total_provider_failure_leaves_analytics_identical(make_client, synthetic_ctx):
    from services.ai.base import NullProvider
    baseline = _deterministic_snapshot(make_client(NullProvider()))
    failing = make_client(Exploding())
    assert _deterministic_snapshot(failing) == baseline
    ids = [a["action_id"] for a in failing.get("/api/agent/actions").json()["actions"]][:5]
    e = failing.post("/api/agent/actions/enrich", json={"action_ids": ids}).json()
    assert e["ai_available"] is True and e["ai_error_category"] in Exploding.CATS
    assert e["failed"] == ids and all(a["interpretation"] is None for a in e["actions"])
    assert _deterministic_snapshot(failing) == baseline              # still identical after the failure
    code = synthetic_ctx["winback"][0]["code"]
    for path in (f"/api/agent/customer/{code}", f"/api/agent/product/{synthetic_ctx['overstock'][0]['code']}"):
        r = failing.get(path)
        assert r.status_code == 200 and r.json()["reason"] and r.json()["ai_error_category"]
    r = failing.post("/api/agent/draft-message", json={"customer_code": code, "message_type": "email"})
    assert r.status_code == 200 and r.json()["available"] is False


def test_even_a_raising_provider_does_not_break_deterministic_endpoints(make_client):
    c = make_client(Raising())
    assert c.get("/api/context").status_code == 200
    assert c.post("/api/scenario", json={"financing_rate": 40}).status_code == 200
    assert c.get("/api/health").status_code == 200
    a = c.get("/api/agent/actions")
    assert a.status_code == 200 and a.json()["count"] > 0
    ids = [x["action_id"] for x in a.json()["actions"]][:5]
    e = c.post("/api/agent/actions/enrich", json={"action_ids": ids})
    assert e.status_code == 200 and e.json()["ai_error_category"] == "unknown"


def test_analytics_build_unaffected_by_ai_settings(monkeypatch, synthetic_dir):
    a = CTX.build_context(synthetic_dir, inflation_rate=0.4)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    b = CTX.build_context(synthetic_dir, inflation_rate=0.4)
    for k in ("company", "collections", "winback", "overstock", "actions"):
        assert a[k] == b[k]


# ---------------- data minimization ----------------
def _walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_provider_input_is_a_small_evidence_packet(make_client, synthetic_ctx):
    spy = Spy()
    c = make_client(spy)
    ids = [a["action_id"] for a in c.get("/api/agent/actions").json()["actions"]][:5]
    assert spy.payloads == []                                        # GET never reaches the provider
    c.post("/api/agent/actions/enrich", json={"action_ids": ids})
    code = synthetic_ctx["winback"][0]["code"]
    c.get(f"/api/agent/customer/{code}")
    c.get(f"/api/agent/product/{synthetic_ctx['overstock'][0]['code']}")
    c.post("/api/agent/draft-message", json={"customer_code": code, "message_type": "email"})
    assert len(spy.payloads) >= 4
    for p in spy.payloads:
        assert {"schema_version", "task", "facts", "warnings"} <= set(p)
        for node in _walk(p):
            assert isinstance(node, (dict, list, str, int, float, bool, type(None)))
            assert not isinstance(node, (pd.DataFrame, pd.Series, bytes))
        blob = json.dumps(p, ensure_ascii=False)
        assert len(blob.encode()) < 24_000
        for forbidden in (".xlsx", ".db", "agent_context", "Nettutar", "Musterikod", "sto_kod",
                          "Net_Tutar_Maliyet_Dusulmus"):
            assert forbidden not in blob
        # not whole tables: the context's other categories never ride along
        assert "collections" not in p and "winback" not in p and "overstock" not in p


@pytest.mark.parametrize("bad", [
    {"facts": [], "df": pd.DataFrame({"a": [1]})},
    {"facts": [], "blob": b"bytes"},
    {"facts": [], "file": "data/2020 satislar.xlsx"},
    {"facts": [], "rows": [{"Nettutar": 1}]},
    {"facts": [{"x": "y" * 30_000}]},
])
def test_validate_payload_rejects_non_minimized_input(bad):
    with pytest.raises(EV.PacketRejected):
        EV.validate_payload(bad)


def test_rejected_packet_never_reaches_provider(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(EV, "FORBIDDEN_SUBSTRINGS", EV.FORBIDDEN_SUBSTRINGS + ("Sentetik",))
    data, r = BusinessActionAgent(spy).analyze_customer({"customer_code": "1", "name": "Sentetik"})
    assert data is None and r["category"] == "packet_rejected" and spy.payloads == []
