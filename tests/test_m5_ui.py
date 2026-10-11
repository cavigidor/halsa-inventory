"""M5-2: read-only AI Aksiyon Merkezi tab (design: docs/m5_action_center.md §4, §5, §7, §10, §11).

Two layers, synthetic data only, no key, no network, no paid call:
1. Structural checks on the generated dashboard (Python only).
2. DOM behaviour (tests/js/ai_panel.test.mjs, jsdom) against a fake server whose responses are real
   API output from this FastAPI app on the synthetic fixture (mock provider). Skipped when node or the
   jsdom dev dependency is missing, unless REQUIRE_JS_TESTS=1 (set in CI), where it must run.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as DB
from services import actions as A
from services import action_sources  # noqa: F401
from services.agent import BusinessActionAgent
from services.ai.base import NullProvider
from services.ai.mock_provider import MockProvider
from services.entity import ContextRepository

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_DIR = os.path.join(ROOT, "tests", "js")
TODAY = dt.date(2026, 10, 5)
H = {"X-StockAgent": "1"}
AI_START, AI_END = "\n<!--AI-TAB-START-->", "<!--AI-TAB-END-->"
AI_BUTTON = '<button class="tab" data-t="ai">✨ AI Aksiyon Merkezi</button>'


@pytest.fixture(scope="module")
def built(synthetic_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("dash")
    with_ai, without = str(out / "with_ai.html"), str(out / "without_ai.html")
    _, counts_ai = DB.build(synthetic_dir, with_ai, today=TODAY)
    _, counts_plain = DB.build(synthetic_dir, without, today=TODAY, ai_panel=False)
    read = lambda p: open(p, encoding="utf-8").read()
    return {"ai": read(with_ai), "plain": read(without), "path": with_ai,
            "counts_ai": counts_ai, "counts_plain": counts_plain}


def _ai_block(html):
    s, e = html.index(AI_START), html.index(AI_END) + len(AI_END)
    return html[s:e]


def _ai_script(html):
    block = _ai_block(html)
    return block[block.index("<script>") + 8: block.rindex("</script>")]


# ---------------- 1-4: tab, panel, existing tabs, existing output unchanged ----------------
def test_dashboard_has_ai_tab_button_and_panel(built):
    h = built["ai"]
    assert h.count(AI_BUTTON) == 1
    assert h.count('<div id="ai" class="panel"') == 1
    assert "✨ AI yorumlarını oluştur" in h and "Sonraki 5 için AI yorumu oluştur" in h


def test_existing_tabs_still_present(built):
    tabs = re.findall(r'<button class="tab" data-t="([a-z]+)"', built["ai"])
    plain = re.findall(r'<button class="tab" data-t="([a-z]+)"', built["plain"])
    assert tabs == plain + ["ai"], "the AI tab is appended; existing tabs keep their order"
    for t in ("overview", "reorder", "overstock", "osmove", "winback", "momentum", "ordersize", "margin"):
        assert t in tabs


def test_existing_dashboard_output_is_byte_identical_without_the_ai_block(built):
    """Removing the AI button and block gives exactly the build without the panel (= pre-M5-2 output)."""
    stripped = built["ai"].replace(AI_BUTTON, "", 1).replace(_ai_block(built["ai"]), "", 1)
    assert stripped == built["plain"]
    assert built["counts_ai"] == built["counts_plain"]


def test_existing_table_data_blob_unchanged(built):
    blob = lambda h: re.search(r"const DATA=(.*?);\n", h).group(1)
    assert blob(built["ai"]) == blob(built["plain"])


def test_panel_carries_the_dashboard_data_date(built, synthetic_ctx):
    m = re.search(r'<div id="ai" class="panel" data-asof="([0-9-]*)"', built["ai"])
    assert m and m.group(1) == synthetic_ctx["as_of_data_date"], "same rule as the server context"


# ---------------- 5-8, 11: static guarantees of the AI script ----------------
def test_ai_script_hard_codes_no_category(built):
    js = _ai_script(built["ai"])
    for c in A.categories():
        assert c.key not in js and c.label_tr not in js, c.key
    assert "/api/agent/categories" in js


def test_ai_script_has_no_html_injection_sinks(built):
    js = _ai_script(built["ai"])
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js, sink
    assert "textContent" in js


def test_ai_script_post_contract(built):
    js = _ai_script(built["ai"])
    assert "'X-StockAgent':'1'" in js
    assert "MAX_IDS=5" in js and ".slice(0,MAX_IDS)" in js
    assert js.count("method:'POST'") == 1 and "/api/agent/actions/enrich" in js
    # only provider-free GETs otherwise; no draft / customer / product AI endpoints in M5-2
    for path in ("/api/agent/customer", "/api/agent/product", "/api/agent/draft-message", "/api/actions/"):
        assert path not in js


def test_approved_turkish_state_copy_is_present(built):
    js = _ai_script(built["ai"])
    for text in (
        "Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp "
        "http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.",
        "Veriler hazırlanıyor…", "AI kapalı (.env: AI_PROVIDER=openai)", "Oluşturuluyor…",
        "OpenAI kredisi yetersiz", "Çok fazla istek, biraz sonra deneyin", "OpenAI'ye ulaşılamadı",
        "API anahtarı geçersiz", "AI metni güvenlik kontrolünden geçmedi, gösterilmiyor",
        "AI yorumu üretilemedi", "Bugün için açık aksiyon yok.",
        "Pano ve sunucu farklı veri tarihleri kullanıyor; veriyi yenileyin.",
    ):
        assert text in js, text
    assert "</script" not in js.lower()


def test_m5_3_to_m5_5_controls_are_not_built(built):
    block = _ai_block(built["ai"])
    for label in ("Yapıldı", "Ertele", "Gizle", "Taslak", "Yenile", "Geri al"):
        assert label not in block, label


# ---------------- backend support: health exposes the data date only when ready ----------------
def test_health_reports_as_of_data_date_when_ready(synthetic_ctx):
    APP.app.state.repo = ContextRepository(context=synthetic_ctx)
    APP.app.state.provider = NullProvider("AI_PROVIDER=disabled")
    APP.app.state.agent = BusinessActionAgent(APP.app.state.provider)
    h = TestClient(APP.app).get("/api/health").json()
    assert h["context_ready"] is True and h["as_of_data_date"] == synthetic_ctx["as_of_data_date"]


def test_health_does_not_build_context_to_report_the_date(tmp_path):
    APP.app.state.repo = ContextRepository(data_folder=str(tmp_path))   # empty folder: a build would fail
    APP.app.state.provider = NullProvider("AI_PROVIDER=disabled")
    APP.app.state.agent = BusinessActionAgent(APP.app.state.provider)
    h = TestClient(APP.app).get("/api/health").json()
    assert h["context_ready"] is False and h["as_of_data_date"] is None and h["context_error"] is None


# ---------------- DOM behaviour (jsdom) ----------------
class _SpyMock(MockProvider):
    def __init__(self):
        self.calls, self.model = 0, "mock-1"

    def generate_structured(self, system_prompt, payload, response_model):
        self.calls += 1
        return super().generate_structured(system_prompt, payload, response_model)


def _fixtures(synthetic_ctx):
    """Real responses of this app on the synthetic context: health, categories, actions (no provider
    call), and one enrich response produced by the mock provider."""
    spy = _SpyMock()
    APP.app.state.repo = ContextRepository(context=synthetic_ctx)
    APP.app.state.provider = spy
    APP.app.state.agent = BusinessActionAgent(spy)
    c = TestClient(APP.app, headers=H)
    health, cats, acts = (c.get(p).json() for p in ("/api/health", "/api/agent/categories", "/api/agent/actions"))
    assert spy.calls == 0, "GET endpoints never call the provider"
    ids = [a["action_id"] for a in acts["actions"]][:5]
    enrich = c.post("/api/agent/actions/enrich", json={"action_ids": ids}).json()
    assert spy.calls == 1 and enrich["enriched"], "mock enrichment must succeed for the sample"
    return {"health": health, "categories": cats, "actions": acts, "enrich_sample": enrich}


def _js_runnable():
    node = shutil.which("node")
    return node if node and os.path.isdir(os.path.join(JS_DIR, "node_modules", "jsdom")) else None


def test_ai_tab_dom_behaviour(built, synthetic_ctx, tmp_path):
    node = _js_runnable()
    if not node:
        if os.environ.get("REQUIRE_JS_TESTS") == "1":
            pytest.fail("REQUIRE_JS_TESTS=1 but node or tests/js/node_modules/jsdom is missing (npm ci --prefix tests/js)")
        pytest.skip("node + jsdom not installed (npm ci --prefix tests/js); structural tests still ran")
    fx = tmp_path / "fixtures.json"
    fx.write_text(json.dumps(_fixtures(synthetic_ctx), ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "AI_DASH_HTML": built["path"], "AI_FIXTURES": str(fx)}
    r = subprocess.run([node, "--test", "--test-reporter=spec", os.path.join(JS_DIR, "ai_panel.test.mjs")],
                       cwd=JS_DIR, env=env, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout[-6000:] + r.stderr[-3000:]
    assert re.search(r"ℹ pass \d+", r.stdout) and "ℹ fail 0" in r.stdout, r.stdout[-3000:]
    print(r.stdout[-4000:])   # visible with `pytest -s`
