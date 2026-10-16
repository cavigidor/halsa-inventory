"""M7-1: Playwright smoke tests for the Müşteri 360 tab in real Chromium against the real FastAPI app.

Each test starts its own server (free port, temporary folder with SYNTHETIC Customer 360 workbooks, temporary
dashboard and SQLite DB, AI_PROVIDER=disabled, no .env) and waits until both the context and the Customer 360
snapshot are ready. No real data, no key, no network, no paid call.

Skipped when Playwright/Chromium is missing, unless REQUIRE_BROWSER_TESTS=1 (CI).
"""
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from services import refresh as RF
from tests.fixtures import c360_synth as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = os.environ.get("REQUIRE_BROWSER_TESTS") == "1"


def _unavailable(reason):
    if REQUIRED:
        pytest.fail(f"REQUIRE_BROWSER_TESTS=1 but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright is not installed (pip install -r requirements-dev.txt)")
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch()
    except Exception as e:
        pw.stop()
        _unavailable(f"Chromium could not start ({type(e).__name__}); run: python -m playwright install chromium")
    yield b
    b.close()
    pw.stop()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode())


@contextlib.contextmanager
def c360_server(tmp_path, hedef=False):
    data = F.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": "disabled", "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": data, "DASHBOARD_PATH": dash,
           "AGENT_DB": str(tmp_path / "state.db")}
    env.pop("HEDEF_PATH", None)
    if hedef:
        env["HEDEF_PATH"] = F.write_hedef(str(tmp_path / F.HEDEF_FILE))
        env["HEDEF_SNAPSHOT_DATE"] = "2026-02-15"
    port = _free_port()
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 90
        while True:
            if proc.poll() is not None:
                log.flush()
                pytest.fail("server exited early:\n" + open(tmp_path / "server.log").read()[-3000:])
            try:
                if _get(base + "/api/health").get("context_ready") and \
                        _get(base + "/api/customer360/status").get("state") == "ready":
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                pytest.fail("server not ready within 90 s:\n" + open(tmp_path / "server.log").read()[-3000:])
            time.sleep(0.2)
        yield {"base": base, "dash": dash, "data": data, "log": tmp_path / "server.log"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def ai_calls(srv):
    return len(_get(srv["base"] + "/api/ai/calls")["calls"])


def open_page(browser, viewport=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    page = ctx.new_page()
    page.errors, page.requests = [], []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("request", lambda r: page.requests.append(r.url))
    return page


def open_tab(page, base):
    page.goto(base + "/")
    page.click('.tab[data-t="c360"]')
    page.wait_for_selector("#c360 .c3-q:not([disabled])")


def search_open(page, query, code):
    page.fill("#c360 .c3-q", query)
    page.wait_for_selector(f'#c360 .c3-hit[data-code="{code}"]')
    page.click(f'#c360 .c3-hit[data-code="{code}"]')
    page.wait_for_selector('#c360 .c3-card[data-sec="activity"]')


def no_horizontal_overflow(page):
    w, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    return w <= iw


def test_c360_tab_search_detail_sections_no_ai(browser, tmp_path):
    with c360_server(tmp_path) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert "c360" in tabs and tabs[-1] == "ai" and page.inner_text('.tab[data-t="c360"]') == "Müşteri 360"
        assert not any("/api/customer360" in u for u in page.requests), "nothing is fetched before the tab opens"
        open_tab(page, srv["base"])
        # search: name prefix; KAPALI badge in results
        page.fill("#c360 .c3-q", "sentetik")
        page.wait_for_selector("#c360 .c3-hit")
        hits = page.eval_on_selector_all("#c360 .c3-hit", "els => els.map(e => e.dataset.code)")
        api = _get(srv["base"] + "/api/customer360/search?q=sentetik&limit=20")
        assert hits == [h["customer_code"] for h in api["results"]], "Python order, never re-sorted"
        kap = page.query_selector('#c360 .c3-hit[data-code="910.00.0006"] .c3-kapali')
        assert kap is not None and kap.inner_text() == "KAPALI"
        # detail
        search_open(page, "910.00.0001", "910.00.0001")
        secs = page.eval_on_selector_all("#c360 .c3-card", "els => els.map(e => e.dataset.sec)")
        assert secs == ["identity", "sales", "products", "opportunities", "receivables", "hedef", "events",
                        "activity"]                                         # M7-3 adds Fırsatlar
        titles = page.eval_on_selector_all("#c360 .c3-card h4", "els => els.map(e => e.textContent)")
        assert titles == ["Kimlik", "Satış geçmişi", "Ürünler / kategoriler", "Fırsatlar", "Cari / alacak",
                          "Hedef bilgileri", "Fuar / etkinlik geçmişi", "Aksiyonlar ve geçmiş"]
        assert "Net Sipariş Tutarı (Nettutar)" in page.inner_text('#c360 [data-sec="sales"]')
        confs = set(page.eval_on_selector_all("#c360 .c3-badge", "els => els.map(e => e.dataset.conf)"))
        assert {"trusted", "unverified", "unavailable"} <= confs
        assert "Doğrulanmadı" in page.inner_text('#c360 [data-sec="identity"]')
        # Hedef not configured
        assert page.get_attribute('#c360 [data-sec="hedef"]', "data-status") == "not_configured"
        assert "yapılandırılmadı" in page.inner_text('#c360 [data-sec="hedef"]')
        # events: separate types, edition year, raw local event name
        ev = page.inner_text('#c360 [data-sec="events"]')
        for t in ("Genel: 3 sipariş", "Fuar: 2 sipariş", "Etkinlik: 1 sipariş", "Saha: 1 sipariş"):
            assert t in ev
        assert "2026ZQXKENTFUAR" in ev
        # receivables: account level, limitation, detailed collections not supplied
        rc = page.inner_text('#c360 [data-sec="receivables"]')
        assert "Detaylı tahsilat raporu sağlanmadı." in rc and "Aralık-2025" in rc
        # shared invoice account warning
        search_open(page, "iki ofis", "910.00.0002")
        shared = page.query_selector('#c360 .c3-acc[data-account="910.99.0002"] [data-shared="1"]')
        assert shared is not None and "alacak bu müşteriye tek başına atanamaz" in shared.inner_text()
        # changing customers again: still zero AI calls; no page errors
        search_open(page, "üç okul", "910.00.0003")
        assert ai_calls(srv) == 0 and page.errors == []
        assert not any("/api/agent/" in u or "analyze" in u or "enrich" in u for u in page.requests)
        log = open(srv["log"], encoding="utf-8").read()
        assert "ZQXKENT" not in log and "Sentetik Bir" not in log         # event / customer names never logged


def test_c360_hostile_names_execute_nothing(browser, tmp_path):
    with c360_server(tmp_path) as srv:
        page = open_page(browser)
        open_tab(page, srv["base"])
        page.fill("#c360 .c3-q", "kalın")
        page.wait_for_selector('#c360 .c3-hit[data-code="910.00.0008"]')
        assert "<img" in page.inner_text('#c360 .c3-hit[data-code="910.00.0008"]')       # shown as text
        page.click('#c360 .c3-hit[data-code="910.00.0008"]')
        page.wait_for_selector('#c360 .c3-card[data-sec="events"]')
        assert "<img" in page.inner_text("#c360 .c3-name")
        assert "<script>" in page.inner_text('#c360 [data-sec="events"]')
        assert page.evaluate("window.__C360_XSS") is None and page.errors == []
        assert page.query_selector("#c360 img") is None and page.query_selector("#c360 .c3-detail script") is None
        assert ai_calls(srv) == 0


def test_c360_hedef_confidence_badges(browser, tmp_path):
    with c360_server(tmp_path, hedef=True) as srv:
        page = open_page(browser)
        open_tab(page, srv["base"])
        search_open(page, "910.00.0001", "910.00.0001")
        hed = '#c360 [data-sec="hedef"]'
        assert page.get_attribute(hed, "data-status") == "matched"
        for key in ("historical_weekly_stamp_capacity", "historical_risk_limit", "historical_unsecured_credit_limit"):
            assert page.get_attribute(f'{hed} [data-key="{key}"] .c3-badge', "data-conf") == "stale"
            assert page.inner_text(f'{hed} [data-key="{key}"] .c3-badge') == "Eski veri"
        assert page.inner_text(f'{hed} [data-key="visit_count"] .c3-badge') == "Eksik kayıt olabilir"
        assert page.inner_text(f'{hed} [data-key="hedef_rep"] .c3-badge') == "Doğrulanmadı"
        text = page.inner_text(hed)
        assert "15 Şubat 2026" in text and "potansiyel" in text.lower()
        assert ai_calls(srv) == 0


def test_c360_phone_width(browser, tmp_path):
    with c360_server(tmp_path) as srv:
        page = open_page(browser, viewport={"width": 390, "height": 844})
        open_tab(page, srv["base"])
        page.fill("#c360 .c3-q", "çokuzun")
        page.wait_for_selector('#c360 .c3-hit[data-code="910.00.0010"]')
        assert no_horizontal_overflow(page)
        page.click('#c360 .c3-hit[data-code="910.00.0010"]')
        page.wait_for_selector('#c360 .c3-card[data-sec="activity"]')
        assert no_horizontal_overflow(page)
        search_open(page, "910.00.0001", "910.00.0001")                   # tables, badges, long event names
        assert no_horizontal_overflow(page)
        page.mouse.wheel(0, 4000)
        assert no_horizontal_overflow(page) and page.errors == [] and ai_calls(srv) == 0


def test_c360_file_mode_requires_server(browser, tmp_path):
    data = F.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    page = open_page(browser)
    page.goto("file://" + dash)
    page.click('.tab[data-t="reorder"]')
    assert page.query_selector("#reorder table") is not None                 # static modules still work
    page.click('.tab[data-t="c360"]')
    page.wait_for_selector("#c360 .c3-state:not([hidden])")
    assert page.inner_text("#c360 .c3-state") == "Müşteri 360 için panoyu http://127.0.0.1:8000/ üzerinden açın."
    assert page.is_disabled("#c360 .c3-q")
    assert [u for u in page.requests if not u.startswith("file://")] == [] and page.errors == []
