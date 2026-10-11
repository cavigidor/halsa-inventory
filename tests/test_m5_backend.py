"""M5-1 backend prerequisites (design: docs/m5_action_center.md §4–§6, §11, §14; D-015, D-017, D-020, D-022).
Synthetic data only; spy/mock providers only (no key, no network, no paid call)."""
import time

import pytest
from fastapi.testclient import TestClient

import app as APP
from services import actions as A
from services import action_sources  # noqa: F401
from services.agent import BusinessActionAgent
from services.ai.base import AIResult, NullProvider
from services.ai.mock_provider import MockProvider
from services.entity import ContextRepository

H = {"X-StockAgent": "1"}


class CountingMock(MockProvider):
    """Mock provider that counts calls and remembers which actions it was asked about."""
    def __init__(self, model="mock-1"):
        self.calls, self.titles, self.model = 0, [], model

    def generate_structured(self, system_prompt, payload, response_model):
        self.calls += 1
        self.titles.append([it["title"] for it in payload.get("items", [])])
        return super().generate_structured(system_prompt, payload, response_model)


@pytest.fixture
def mk(synthetic_ctx):
    def _mk(provider=None, ctx=None, headers=H):
        provider = provider or CountingMock()
        APP.app.state.repo = ContextRepository(context=ctx or synthetic_ctx)
        APP.app.state.provider = provider
        APP.app.state.agent = BusinessActionAgent(provider)
        return TestClient(APP.app, headers=headers), provider
    return _mk


def _ids(client):
    return [a["action_id"] for a in client.get("/api/agent/actions").json()["actions"]]


# ---------------- contract, registry, categories, reason (D-015, D-022) ----------------
def test_actions_carry_contract_fields_and_python_ranking(mk):
    c, _ = mk()
    acts = c.get("/api/agent/actions").json()["actions"]
    assert acts, "synthetic data must produce actions"
    for a in acts:
        assert a["source"] and a["reason"].strip() and a["action_id"].startswith(a["category"] + ":")
        assert a["facts"] and 0 <= a["score"] <= 100 and a["priority"] in ("critical", "high", "medium", "low")
    scores = [a["score"] for a in acts]
    assert scores == sorted(scores, reverse=True)


def test_categories_are_server_described(mk):
    c, _ = mk()
    cats = c.get("/api/agent/categories").json()
    keys = {x["key"] for x in cats}
    assert {"collections", "overstock", "winback", "real_decline"} <= keys
    assert all(x["label_tr"] and x["color"].startswith("#") and x["entity_type"] and x["source"] for x in cats)
    used = {a["category"] for a in c.get("/api/agent/actions").json()["actions"]}
    assert used <= keys


def test_reason_is_deterministic_turkish(synthetic_ctx):
    by_cat = {}
    for a in synthetic_ctx["actions"]:
        by_cat.setdefault(a["category"], a["reason"])
    assert "2026" in by_cat["winback"] and "Kademe" in by_cat["winback"]
    assert "enflasyon" in by_cat["real_decline"]
    assert "Stok" in by_cat["overstock"]


def test_registry_refactor_preserves_m1_ordering_and_scores(synthetic_dir):
    """Every action matches the M1 formula output (scores/facts) and the queue is score-ranked."""
    from services import context as CTX
    c = CTX.build_context(synthetic_dir, inflation_rate=0.40)
    assert [a["category"] for a in c["actions"]][:2] == ["real_decline", "overstock"]
    assert all(A.validate(dict(a)) for a in c["actions"])


def test_new_source_plugs_in_without_touching_api_or_agent(mk, synthetic_ctx):
    """D-022: an extra registered source shows up in categories and the ranked queue automatically."""
    cat = A.Category("test_followup", "Takip (test)", "#123456", "customer", "test_source")

    def produce(inp):
        return [A.make_action(source="test_source", category="test_followup", entity_type="customer",
                              entity_id="900.00.0003", title="TAKİP — Sentetik T3 Okul",
                              reason="Test kaynağı: takip zamanı geldi.", facts=["Son temas: Yok"],
                              drivers={}, score=99.0, confidence="medium")]
    A.register(A.ActionSource("test_source", (cat,), produce))
    try:
        inputs = {"ctx": {}, "coll_scored": [], "over_cand": [], "lap": [], "shrink_real_neg": [], "infl": None}
        produced = A.produce_all(inputs)
        assert produced[0]["category"] == "test_followup"
        c, _ = mk()
        assert "test_followup" in {x["key"] for x in c.get("/api/agent/categories").json()}
    finally:
        A.unregister("test_source")
    assert "test_followup" not in {x.key for x in A.categories()}


def test_contract_violations_are_rejected():
    good = A.make_action(source="collections_aging", category="collections", entity_type="customer",
                         entity_id="1", title="T", reason="R", facts=["a: b"], drivers={}, score=50,
                         confidence="high")
    A.validate(dict(good))
    for bad in ({**good, "score": 120}, {**good, "reason": " "}, {**good, "category": "nope"},
                {k: v for k, v in good.items() if k != "source"}, {**good, "facts": [1, 2]}):
        with pytest.raises(A.ContractError):
            A.validate(bad)


# ---------------- GET never calls the provider (D-017) ----------------
def test_get_actions_makes_no_provider_call(mk):
    c, prov = mk()
    for _ in range(3):
        r = c.get("/api/agent/actions")
        assert r.status_code == 200 and all(a["interpretation"] is None for a in r.json()["actions"])
    assert prov.calls == 0


# ---------------- enrich: ≤5 ids, one call, cache (D-017) ----------------
def test_enrich_limits_and_validation(mk):
    c, prov = mk()
    ids = _ids(c)
    assert c.post("/api/agent/actions/enrich", json={"action_ids": []}).status_code == 422
    assert c.post("/api/agent/actions/enrich", json={"action_ids": ["x"] * 6}).status_code == 422
    assert c.post("/api/agent/actions/enrich", json={"action_ids": [ids[0], ids[0]]}).status_code == 422
    r = c.post("/api/agent/actions/enrich", json={"action_ids": [ids[0], "collections:nope:deadbeef"]})
    assert r.status_code == 422 and prov.calls == 0


def test_enrich_top5_is_one_call_then_cached(mk, synthetic_ctx):
    c, prov = mk()
    ids = _ids(c)[:5]
    r = c.post("/api/agent/actions/enrich", json={"action_ids": ids}).json()
    assert prov.calls == 1 and r["provider_called"] and sorted(r["enriched"]) == sorted(ids)
    assert all(a["interpretation"] and a["evidence"] for a in r["actions"])
    # same request again: answered from cache, no provider call
    r2 = c.post("/api/agent/actions/enrich", json={"action_ids": ids}).json()
    assert prov.calls == 1 and not r2["provider_called"] and sorted(r2["cached"]) == sorted(ids)
    # GET attaches the cached text, still no call
    acts = c.get("/api/agent/actions").json()["actions"]
    assert prov.calls == 1
    got = {a["action_id"]: a for a in acts}
    assert all(got[i]["ai_cached"] and got[i]["interpretation"] for i in ids)


def test_enrich_keeps_python_order_regardless_of_request_order(mk):
    c, prov = mk()
    ids = _ids(c)[:4]
    r = c.post("/api/agent/actions/enrich", json={"action_ids": list(reversed(ids))}).json()
    assert [a["action_id"] for a in r["actions"]] == ids
    assert _ids(c)[:4] == ids                                     # queue order unchanged by AI


def test_partial_cache_only_uncached_go_to_provider(mk):
    c, prov = mk()
    ids = _ids(c)
    c.post("/api/agent/actions/enrich", json={"action_ids": ids[:2]})
    r = c.post("/api/agent/actions/enrich", json={"action_ids": ids[:3]}).json()
    assert prov.calls == 2 and r["enriched"] == [ids[2]] and sorted(r["cached"]) == sorted(ids[:2])
    assert len(prov.titles[1]) == 1                               # only the uncached action was sent


def test_facts_change_means_cache_miss(mk, synthetic_ctx):
    import copy
    c, prov = mk()
    first = _ids(c)[0]
    c.post("/api/agent/actions/enrich", json={"action_ids": [first]})
    ctx2 = copy.deepcopy(synthetic_ctx)
    ctx2["actions"][0]["facts"] = list(ctx2["actions"][0]["facts"]) + ["Yeni bilgi: var"]
    ctx2["actions"][0].pop("action_id", None)                      # id is re-derived from the facts
    c2, prov2 = mk(provider=prov, ctx=ctx2)
    acts = c2.get("/api/agent/actions").json()["actions"]
    changed = acts[0]
    assert changed["action_id"] != first and changed["interpretation"] is None
    c2.post("/api/agent/actions/enrich", json={"action_ids": [changed["action_id"]]})
    assert prov.calls == 2


def test_model_or_prompt_change_means_cache_miss(mk):
    c, prov = mk(provider=CountingMock(model="mock-1"))
    first = _ids(c)[0]
    c.post("/api/agent/actions/enrich", json={"action_ids": [first]})
    c2, prov2 = mk(provider=CountingMock(model="mock-2"))         # different model -> different key
    assert all(a["interpretation"] is None for a in c2.get("/api/agent/actions").json()["actions"])
    agent = APP.app.state.agent
    pv = agent.prompt_version
    agent.system_prompt += "\n# değişti"
    assert agent.prompt_version != pv


def test_enrich_with_ai_disabled_makes_no_call(mk):
    c, _ = mk(provider=NullProvider("AI provider is not configured."))
    ids = _ids(c)[:2]
    r = c.post("/api/agent/actions/enrich", json={"action_ids": ids})
    assert r.status_code == 200 and r.json()["ai_available"] is False and r.json()["failed"] == ids
    assert c.get("/api/agent/actions").json()["ai_available"] is False


def test_cached_text_not_served_when_ai_disabled(mk):
    c, prov = mk()
    first = _ids(c)[0]
    c.post("/api/agent/actions/enrich", json={"action_ids": [first]})
    c2, _ = mk(provider=NullProvider("off"))
    assert all(a["interpretation"] is None for a in c2.get("/api/agent/actions").json()["actions"])


def test_failed_enrich_is_not_cached(mk):
    class Failing(CountingMock):
        def generate_structured(self, *a, **k):
            self.calls += 1
            return AIResult(available=True, error="simulated", error_category="timeout")
    c, prov = mk(provider=Failing())
    ids = _ids(c)[:2]
    r = c.post("/api/agent/actions/enrich", json={"action_ids": ids}).json()
    assert r["ai_error_category"] == "timeout" and r["failed"] == ids and r["enriched"] == []
    c.post("/api/agent/actions/enrich", json={"action_ids": ids})
    assert prov.calls == 2                                        # retried on the next explicit request


# ---------------- POST guard (D-020) ----------------
@pytest.mark.parametrize("path,body", [
    ("/api/agent/actions/enrich", {"action_ids": ["collections:x:1"]}),
    ("/api/actions/collections:x:1/complete", {}),
    ("/api/actions/collections:x:1/defer", {"until": "2099-01-01"}),
    ("/api/actions/collections:x:1/dismiss", {}),
    ("/api/agent/draft-message", {"customer_code": "900.00.0001", "message_type": "email"}),
    ("/api/scenario", {"financing_rate": 40}),
])
def test_post_without_guard_header_is_forbidden(mk, path, body):
    c, prov = mk(headers={})
    r = c.post(path, json=body)
    assert r.status_code == 403 and "X-StockAgent" in r.json()["detail"]
    assert prov.calls == 0
    assert c.post(path, json=body, headers={"X-StockAgent": "0"}).status_code == 403


def test_get_needs_no_header(mk):
    c, _ = mk(headers={})
    assert c.get("/api/health").status_code == 200 and c.get("/api/agent/actions").status_code == 200


# ---------------- GET / serves the dashboard (D-020) ----------------
def test_root_serves_dashboard_file(mk, tmp_path, monkeypatch):
    f = tmp_path / "dashboard.html"
    f.write_text("<!doctype html><title>Şirket Panosu — test</title>", encoding="utf-8")
    monkeypatch.setattr(APP, "DASHBOARD_PATH", str(f))
    c, _ = mk(headers={})
    r = c.get("/")
    assert r.status_code == 200 and "Şirket Panosu" in r.text and r.headers["content-type"].startswith("text/html")


def test_root_without_dashboard_gives_build_hint(mk, tmp_path, monkeypatch):
    monkeypatch.setattr(APP, "DASHBOARD_PATH", str(tmp_path / "missing.html"))
    c, _ = mk(headers={})
    r = c.get("/")
    assert r.status_code == 404 and "dashboard_builder.py" in r.text


# ---------------- warm-up / context_ready ----------------
def test_context_ready_flag_and_lazy_build(synthetic_dir):
    repo = ContextRepository(data_folder=synthetic_dir, inflation_rate=0.4)
    assert repo.is_ready() is False
    repo.context()
    assert repo.is_ready() is True and repo.last_error is None


def test_failed_build_reports_context_error(tmp_path):
    repo = ContextRepository(data_folder=str(tmp_path), inflation_rate=None)
    with pytest.raises(Exception):
        repo.context()
    assert repo.is_ready() is False and repo.last_error


def test_startup_warmup_makes_context_ready(monkeypatch, synthetic_dir, tmp_path):
    import importlib
    monkeypatch.setenv("DATA_FOLDER", synthetic_dir)
    monkeypatch.setenv("WARMUP_CONTEXT", "true")
    importlib.reload(APP)
    try:
        with TestClient(APP.app) as client:                       # runs the startup event
            deadline = time.time() + 30
            ready = False
            while time.time() < deadline:
                h = client.get("/api/health").json()
                if h["context_ready"]:
                    ready = True
                    break
                time.sleep(0.1)
            assert ready and h["context_error"] is None
    finally:
        monkeypatch.delenv("DATA_FOLDER")
        importlib.reload(APP)


def test_health_reports_context_error(mk, tmp_path):
    c, _ = mk()
    APP.app.state.repo = ContextRepository(data_folder=str(tmp_path))
    try:
        APP.app.state.repo.context()
    except Exception:
        pass
    h = c.get("/api/health").json()
    assert h["context_ready"] is False and h["context_error"]
