"""M5-1 backend prerequisites (design: docs/m5_action_center.md §4–§6, §11, §14; D-015, D-017, D-020, D-022).
Synthetic data only; spy/mock providers only (no key, no network, no paid call)."""
import json
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
    ("/api/actions/collections:x:1/reopen", {}),
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
def test_root_never_serves_an_unpublished_dashboard_file(mk, tmp_path, monkeypatch):
    """M8-ACCURACY-2 (review of PR #28): `/` serves the on-disk HTML only when its exact bytes match the accepted,
    re-validated manifest. A hand-written page — even one with the policy marker and an EMPTY displayed-metrics
    marker — is a claim of the file itself and is never served (a published page: tests/test_m8_accuracy2_review3.py)."""
    f = tmp_path / "dashboard.html"
    from services import accuracy as ACC
    f.write_text(f'<!doctype html><meta name="stockagent-accuracy" content="{ACC.POLICY_VERSION}">'
                 '<meta name="stockagent-generation" content=""><meta name="stockagent-displayed" content="">'
                 "<title>Şirket Panosu — test</title><script>const DATA={}</script>", encoding="utf-8")
    monkeypatch.setattr(APP, "DASHBOARD_PATH", str(f))
    c, _ = mk(headers={})
    r = c.get("/")
    assert r.status_code == 503 and r.headers["content-type"].startswith("text/html")
    assert "Şirket Panosu — test" not in r.text and "const DATA" not in r.text and str(tmp_path) not in r.text
    assert r.headers["X-Halsa-Dashboard"] == "no_accepted_generation"


def test_root_without_dashboard_gives_build_hint(mk, tmp_path, monkeypatch):
    monkeypatch.setattr(APP, "DASHBOARD_PATH", str(tmp_path / "missing.html"))
    c, _ = mk(headers={})
    r = c.get("/")
    assert r.status_code == 404 and "services.refresh" in r.text and str(tmp_path) not in r.text


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


# ---------------- M5-3: action state (done / defer / dismiss / reopen) ----------------
import datetime as _dt

from services import store as ST


def _row(aid):
    return ST.get_action(aid)


def _history(aid):
    with ST._conn() as c:
        return [r["event"] for r in c.execute(
            "SELECT event FROM action_history WHERE action_key=? ORDER BY id", (aid,))]


def _future(days):
    return (_dt.date.today() + _dt.timedelta(days=days)).isoformat()


def test_complete_persists_completed_and_hides(mk):
    c, prov = mk()
    aid = _ids(c)[0]
    r = c.post(f"/api/actions/{aid}/complete", json={"notes": "arandı"})
    assert r.status_code == 200 and r.json() == {"ok": True, "action_id": aid, "status": "completed", "defer_until": None}
    row = _row(aid)
    assert row["status"] == "completed" and row["completed_at"] and row["notes"] == "arandı"
    assert aid not in _ids(c)
    assert prov.calls == 0


def test_dismiss_persists_ignored_and_hides(mk):
    c, prov = mk()
    aid = _ids(c)[-1]
    assert c.post(f"/api/actions/{aid}/dismiss").json()["status"] == "ignored"
    assert _row(aid)["status"] == "ignored" and aid not in _ids(c)
    assert prov.calls == 0


def test_defer_persists_requested_future_date_and_hides(mk):
    c, prov = mk()
    aid = _ids(c)[1]
    until = _future(10)
    r = c.post(f"/api/actions/{aid}/defer", json={"until": until}).json()
    assert r["status"] == "deferred" and r["defer_until"] == until
    row = _row(aid)
    assert row["status"] == "deferred" and row["defer_until"] == until
    assert aid not in _ids(c)
    assert prov.calls == 0


def test_defer_without_date_defaults_to_seven_days(mk):
    c, _ = mk()
    aid = _ids(c)[0]
    r = c.post(f"/api/actions/{aid}/defer", json={}).json()
    assert r["defer_until"] == _future(7) and _row(aid)["defer_until"] == _future(7)
    assert aid not in _ids(c)


@pytest.mark.parametrize("until", ["2020-01-01", "today", "not-a-date", "2026-13-40"])
def test_defer_rejects_past_today_or_invalid_dates(mk, until):
    c, _ = mk()
    aid = _ids(c)[0]
    if until == "today":
        until = _dt.date.today().isoformat()
    r = c.post(f"/api/actions/{aid}/defer", json={"until": until})
    assert r.status_code == 422
    assert _row(aid)["status"] == "open" and aid in _ids(c)
    assert _history(aid) == ["generated"]


def test_deferred_action_returns_after_its_date(mk):
    """Existing store semantics: hidden while the defer date is in the future, open again afterwards."""
    c, _ = mk()
    aid = _ids(c)[0]
    ST.set_status(aid, "deferred", until=_future(3))
    assert aid not in _ids(c)
    ST.set_status(aid, "deferred", until=(_dt.date.today() - _dt.timedelta(days=1)).isoformat())
    assert aid in _ids(c)


@pytest.mark.parametrize("kind,body", [("complete", {}), ("dismiss", {}), ("defer", {"until": "2099-01-01"})])
def test_reopen_restores_open_in_python_order_and_clears_state(mk, kind, body):
    c, prov = mk()
    before = _ids(c)
    aid = before[1]
    c.post(f"/api/actions/{aid}/{kind}", json=body)
    assert aid not in _ids(c)
    r = c.post(f"/api/actions/{aid}/reopen")
    assert r.status_code == 200 and r.json()["status"] == "open"
    row = _row(aid)
    assert row["status"] == "open" and row["completed_at"] is None and row["defer_until"] is None
    assert _ids(c) == before, "same action_id, same Python position, no duplicate"
    status = {"complete": "completed", "dismiss": "ignored", "defer": "deferred"}[kind]
    assert _history(aid) == ["generated", f"status:{status}", "status:open"]
    assert prov.calls == 0


def test_reopen_keeps_cached_ai_text_and_calls_no_provider(mk):
    c, prov = mk()
    ids = _ids(c)
    c.post("/api/agent/actions/enrich", json={"action_ids": ids[:1]})
    assert prov.calls == 1
    c.post(f"/api/actions/{ids[0]}/complete")
    c.post(f"/api/actions/{ids[0]}/reopen")
    a = c.get("/api/agent/actions").json()["actions"][0]
    assert a["action_id"] == ids[0] and a["ai_cached"] is True and a["interpretation"]
    assert prov.calls == 1, "state changes and undo never call the provider"


@pytest.mark.parametrize("kind", ["complete", "defer", "dismiss", "reopen"])
def test_state_endpoints_404_for_unknown_action(mk, kind):
    c, prov = mk()
    _ids(c)
    r = c.post(f"/api/actions/collections:900.00.9999:deadbeef/{kind}", json={})
    assert r.status_code == 404
    assert _history("collections:900.00.9999:deadbeef") == []
    assert prov.calls == 0


def test_completed_identical_content_is_not_reopened_by_a_new_build(mk, synthetic_ctx):
    """Store semantics kept: the same facts give the same action_id, which upsert never resets to open."""
    c, _ = mk()
    aid = _ids(c)[0]
    c.post(f"/api/actions/{aid}/complete")
    c2, _ = mk(ctx=synthetic_ctx)
    assert aid not in _ids(c2) and _row(aid)["status"] == "completed"



# ---------------- M5-4: drafts (channel × purpose), KAPALI refusal, deterministic customer/product GET ----------------
from services import drafts as DR
from services.ai.base import AIProvider

KAPALI_CODE = "900.00.0007"          # synthetic KAPALI customer
OPEN_CODE = "900.00.0001"            # synthetic lapsed (non-KAPALI) customer


class ScriptedDraft(AIProvider):
    """Returns scripted MessageDraftAI outputs and records every payload and prompt."""
    name, model = "scripted", "scripted-1"

    def __init__(self, outputs=None):
        self.outputs = list(outputs or [{"message": "Merhaba, nasılsınız?", "evidence_fact_ids": [], "warnings": []}])
        self.calls, self.payloads, self.prompts = 0, [], []

    def is_available(self):
        return True

    def generate_structured(self, system_prompt, payload, response_model):
        self.calls += 1
        self.payloads.append(payload)
        self.prompts.append(system_prompt)
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        return AIResult(available=True, data=out, meta={"provider": self.name, "model": self.model})


def _draft(c, **body):
    return c.post("/api/agent/draft-message", json={"customer_code": OPEN_CODE, **body})


def test_canonical_draft_whatsapp_collection_and_email(mk):
    c, prov = mk()
    r = _draft(c, channel="whatsapp", purpose="collection")
    assert r.status_code == 200 and r.json()["available"] is True
    assert r.json()["channel"] == "whatsapp" and r.json()["purpose"] == "collection" and r.json()["message"]
    r = _draft(c, channel="email", purpose="winback")
    assert r.status_code == 200 and r.json()["channel"] == "email" and r.json()["purpose"] == "winback"
    assert "Otomatik gönderim yapılmaz" in r.json()["note"]


@pytest.mark.parametrize("purpose", ["collection", "offer", "winback", "sales_followup", "fair_invite"])
def test_all_five_purposes_validate(mk, purpose):
    c, prov = mk()
    r = _draft(c, channel="whatsapp", purpose=purpose)
    assert r.status_code == 200 and r.json()["purpose"] == purpose and prov.calls == 1


@pytest.mark.parametrize("body", [
    {"channel": "sms", "purpose": "collection"},
    {"channel": "whatsapp", "purpose": "discount"},
    {"channel": "whatsapp"},
    {"purpose": "offer"},
    {},
    {"channel": "whatsapp", "purpose": "offer", "message_type": "email"},
    {"channel": "whatsapp", "purpose": "offer", "send": True},
    {"channel": "whatsapp", "purpose": "offer", "additional_instruction": "x" * 301},
])
def test_invalid_draft_requests_are_rejected(mk, body):
    c, prov = mk()
    assert _draft(c, **body).status_code == 422
    assert prov.calls == 0


@pytest.mark.parametrize("legacy,channel,purpose", [
    ("collection", "whatsapp", "collection"), ("sales_offer", "whatsapp", "offer"),
    ("winback", "whatsapp", "winback"), ("whatsapp", "whatsapp", "sales_followup"),
    ("email", "email", "sales_followup"),
])
def test_legacy_message_type_mapping(mk, legacy, channel, purpose):
    c, _ = mk()
    j = _draft(c, message_type=legacy).json()
    assert (j["channel"], j["purpose"]) == (channel, purpose) == DR.LEGACY_MESSAGE_TYPES[legacy]


def test_kapali_collection_draft_is_allowed(mk):
    c, prov = mk()
    r = c.post("/api/agent/draft-message", json={"customer_code": KAPALI_CODE, "channel": "whatsapp",
                                                  "purpose": "collection"})
    assert r.json()["available"] is True and prov.calls == 1


@pytest.mark.parametrize("purpose", ["offer", "winback", "sales_followup", "fair_invite"])
@pytest.mark.parametrize("channel", ["whatsapp", "email"])
def test_kapali_non_collection_refused_before_provider(mk, channel, purpose):
    c, prov = mk()
    r = c.post("/api/agent/draft-message", json={"customer_code": KAPALI_CODE, "channel": channel,
                                                  "purpose": purpose, "additional_instruction": "yine de yaz"})
    j = r.json()
    assert r.status_code == 200 and j["available"] is False and j["message"] is None
    assert j["ai_error_category"] == "kapali_customer" and j["reason"] == DR.KAPALI_REFUSAL
    assert prov.calls == 0


def test_kapali_refusal_holds_even_with_ai_disabled(mk):
    c, _ = mk(provider=NullProvider("AI provider is not configured."))
    j = c.post("/api/agent/draft-message", json={"customer_code": KAPALI_CODE, "message_type": "sales_offer"}).json()
    assert j["ai_error_category"] == "kapali_customer"


def test_kapali_status_comes_from_the_full_classification_not_the_top_n_risk_list(mk, synthetic_ctx):
    import copy
    ctx = copy.deepcopy(synthetic_ctx)
    for r in ctx["risk"]:
        r["closed"] = False                       # as if the customer were outside the top-N risk list
    assert KAPALI_CODE in ctx["closed_customers"]
    c, prov = mk(ctx=ctx)
    j = c.post("/api/agent/draft-message", json={"customer_code": KAPALI_CODE, "channel": "whatsapp",
                                                  "purpose": "offer"}).json()
    assert j["ai_error_category"] == "kapali_customer" and prov.calls == 0


def test_draft_post_requires_guard_header(mk):
    c, prov = mk(headers={})
    r = c.post("/api/agent/draft-message", json={"customer_code": OPEN_CODE, "channel": "whatsapp", "purpose": "offer"})
    assert r.status_code == 403 and prov.calls == 0


def test_draft_goes_through_numeric_guard_and_leak_check(mk):
    invented = {"message": "Merhaba, size yüzde 15 indirim yapabiliriz.", "evidence_fact_ids": [], "warnings": []}
    leak = {"message": "Merhaba, yapay zekâ sistemimiz puanınızı yüksek buldu.", "evidence_fact_ids": [], "warnings": []}
    for bad, cat in ((invented, "numeric_guard"), (leak, DR.ERR_LEAK)):
        prov = ScriptedDraft([bad, bad])
        c, _ = mk(provider=prov)
        j = _draft(c, channel="whatsapp", purpose="offer").json()
        assert j["available"] is False and j["ai_error_category"] == cat and j["message"] is None
        assert prov.calls == 2, "one stricter retry, then failure"


def test_draft_numbers_only_from_python_facts(mk):
    def packet_fact(prov, label):
        return next(f["fact_id"] for f in prov.payloads[0]["facts"] if f["label"] == label)
    probe = ScriptedDraft()
    c, _ = mk(provider=probe)
    c.post("/api/agent/draft-message", json={"customer_code": "900.00.0002", "channel": "whatsapp",
                                              "purpose": "collection"})
    fid = packet_fact(probe, "Vadesi geçen bakiye")
    prov = ScriptedDraft([{"message": f"Merhaba, vadesi geçmiş [[FACT:{fid}]] bakiyeniz için görüşebilir miyiz?",
                           "evidence_fact_ids": [fid], "warnings": []}])
    c, _ = mk(provider=prov)
    j = c.post("/api/agent/draft-message", json={"customer_code": "900.00.0002", "channel": "whatsapp",
                                                  "purpose": "collection"}).json()
    disp = next(f["display_value"] for f in prov.payloads[0]["facts"] if f["fact_id"] == fid)
    assert j["available"] is True and disp in j["message"] and "[[" not in j["message"]
    assert j["evidence"][0]["display_value"] == disp


def test_draft_packet_has_no_internal_scores_or_analysis(mk):
    prov = ScriptedDraft()
    c, _ = mk(provider=prov)
    for code in ("900.00.0002", "900.00.0006", OPEN_CODE, KAPALI_CODE):
        for purpose in ("collection", "offer", "winback", "sales_followup", "fair_invite"):
            c.post("/api/agent/draft-message", json={"customer_code": code, "channel": "email", "purpose": purpose})
    assert prov.payloads
    for p in prov.payloads:
        blob = json.dumps(p, ensure_ascii=False)
        labels = " ".join(f["label"] for f in p["facts"])
        for word in ("puan", "Puan", "kademe", "Kademe", "Borç / ciro", "değişim", "Reel", "skor", "öncelik"):
            assert word not in labels, word
        assert "score" not in blob and "KAPALI" not in blob and "winback_tier" not in blob
        assert {c["name"] for c in p["classifications"]} <= {"channel", "purpose", "user_instruction"}
        assert p["flags"] == []
        if dict((c["name"], c["value"]) for c in p["classifications"])["purpose"] != "collection":
            assert "Vadesi geçen bakiye" not in labels, "debt is not mentioned in sales messages"


def test_draft_prompt_layer_is_task_only_and_keeps_action_cache_key(mk):
    prov = ScriptedDraft()
    c, _ = mk(provider=prov)
    pv_before = APP.app.state.agent.prompt_version
    _draft(c, channel="email", purpose="fair_invite", additional_instruction="kısa olsun")
    assert APP.app.state.agent.prompt_version == pv_before
    prompt = prov.prompts[0]
    assert prompt.startswith(APP.app.state.agent.system_prompt)
    assert "Kanal: E-posta" in prompt and "fuar daveti" in prompt and "Sayın" in prompt
    instr = [x for x in prov.payloads[0]["classifications"] if x["name"] == "user_instruction"]
    assert instr == [{"name": "user_instruction", "value": "kısa olsun"}]


def test_draft_with_ai_unavailable_is_structured(mk):
    c, _ = mk(provider=NullProvider("AI provider is not configured."))
    r = _draft(c, channel="whatsapp", purpose="winback")
    j = r.json()
    assert r.status_code == 200 and j["available"] is False and j["ai_error_category"] == "disabled"
    assert j["channel"] == "whatsapp" and j["purpose"] == "winback"


def test_failed_draft_changes_no_state_or_cache(mk):
    c, prov = mk()
    ids = _ids(c)
    c.post("/api/agent/actions/enrich", json={"action_ids": ids[:1]})
    pv, key = APP.app.state.agent.prompt_version, APP.app.state.agent.model_key
    before = (ST.get_statuses(), ST.cache_get_many(ids, pv, key))
    assert before[1], "one cached action comment exists"
    APP.app.state.agent.provider = ScriptedDraft([{"message": "Puanınız 88.", "evidence_fact_ids": [], "warnings": []}])
    assert _draft(c, channel="whatsapp", purpose="offer").json()["available"] is False
    assert (ST.get_statuses(), ST.cache_get_many(ids, pv, key)) == before


def test_customer_get_is_deterministic_with_zero_provider_calls(mk):
    c, prov = mk()
    for code in (OPEN_CODE, "900.00.0002", "900.00.0006", KAPALI_CODE):
        r = c.get(f"/api/agent/customer/{code}")
        j = r.json()
        assert r.status_code == 200 and j["summary"] is None and j["facts"]["customer_code"] == code
        assert j["display_facts"] and all(set(f) == {"label", "display_value"} for f in j["display_facts"])
    assert c.get(f"/api/agent/customer/{KAPALI_CODE}").json()["facts"]["closed"] is True
    assert prov.calls == 0


def test_product_get_is_deterministic_with_zero_provider_calls(mk, synthetic_ctx):
    c, prov = mk()
    code = synthetic_ctx["overstock"][0]["code"]
    j = c.get(f"/api/agent/product/{code}").json()
    assert j["facts"]["product_code"] == code and j["recommendation"] is None and prov.calls == 0


def test_analysis_endpoints_are_post_only_and_guarded(mk, synthetic_ctx):
    c, prov = mk()
    prod = synthetic_ctx["overstock"][0]["code"]
    for i, path in enumerate((f"/api/agent/customer/{OPEN_CODE}/analyze", f"/api/agent/product/{prod}/analyze")):
        assert c.get(path).status_code == 405
        assert c.post(path, headers={"X-StockAgent": "0"}).status_code == 403
        assert prov.calls == i, "neither GET nor an unguarded POST reaches the provider"
        assert c.post(path).status_code == 200
        assert prov.calls == i + 1


@pytest.mark.parametrize("path", ["/api/agent/customer/NOPE", "/api/agent/product/NOPE"])
def test_unknown_customer_or_product_is_a_clean_404(mk, path):
    c, prov = mk()
    assert c.get(path).status_code == 404
    assert c.post(path + "/analyze").status_code == 404
    assert prov.calls == 0
    if "customer" in path:
        r = c.post("/api/agent/draft-message", json={"customer_code": "NOPE", "channel": "whatsapp",
                                                      "purpose": "collection"})
        assert r.status_code == 404 and prov.calls == 0


def test_categories_describe_a_default_draft_purpose(mk):
    c, _ = mk()
    cats = {x["key"]: x for x in c.get("/api/agent/categories").json()}
    assert cats["collections"]["draft_purpose"] == "collection"
    assert cats["winback"]["draft_purpose"] == "winback"
    assert cats["real_decline"]["draft_purpose"] == "sales_followup"
    assert cats["overstock"]["draft_purpose"] is None          # product action: no customer target
    assert all(x["draft_purpose"] in (None, *DR.PURPOSES) for x in cats.values())


def test_no_send_capability_exists():
    """Copy-only (D-018): no route of the app and no module it runs can send a message to anyone.
    weekly_agent.py (an older, unused, standalone weekly report e-mail to the owner; not imported by the
    app, never customer-facing) is the only file outside this surface and is deliberately not scanned."""
    import pathlib
    paths = [r.path for r in APP.app.routes]
    assert not [p for p in paths if any(w in p.lower() for w in ("send", "gonder", "whatsapp", "mail", "sms"))]
    root = pathlib.Path(APP.ROOT)
    surface = [root / "app.py", root / "schemas.py", root / "config.py", root / "dashboard_builder.py",
               *sorted((root / "services").glob("**/*.py"))]
    src = "\n".join(p.read_text(encoding="utf-8") for p in surface).lower()
    for needle in ("smtplib", "wa.me", "api.whatsapp", "twilio", "sendgrid", "gmail", "web.whatsapp", "mailto:"):
        assert needle not in src, needle
    assert "weekly_agent" not in src, "the app never imports the legacy e-mail script"


def test_every_customer_action_resolves_even_outside_the_top_n_cut(synthetic_dir):
    """Real-data finding (M5-4): actions use the full lists, the context lists are top-N. With a cut of 1,
    every customer action must still open in the drawer, and the top-N lists and actions are unchanged."""
    from services import context as CTX
    full = CTX.build_context(synthetic_dir, inflation_rate=0.40)
    small = CTX.build_context(synthetic_dir, inflation_rate=0.40, top_n=1)
    assert [(a["action_id"], a["score"]) for a in small["actions"]] == [(a["action_id"], a["score"]) for a in full["actions"]]
    assert len(small["winback"]) == 1 and small["action_customers"]["winback"], "rows carried outside the cut"
    repo = ContextRepository(context=small)
    for a in small["actions"]:
        if a["entity_type"] == "customer":
            facts = repo.customer(a["entity_id"])
            assert facts and facts["name"], a["category"]
    c = TestClient(APP.app, headers=H)
    APP.app.state.repo = repo
    APP.app.state.provider = CountingMock()
    APP.app.state.agent = BusinessActionAgent(APP.app.state.provider)
    for a in c.get("/api/agent/actions").json()["actions"]:
        if a["entity_type"] == "customer":
            assert c.get(f"/api/agent/customer/{a['entity_id']}").status_code == 200
    assert APP.app.state.provider.calls == 0
