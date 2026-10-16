"""M7-3: Playwright scenarios for the Fırsatlar tab, the Günlük Satış cross-sell deep link and the Customer 360
Fırsatlar section, in real Chromium against the real FastAPI app.

Each test starts its own server (free port, temporary folder with SYNTHETIC opportunity workbooks, optional synthetic
Hedef file, temporary dashboard and SQLite DB, AI_PROVIDER=disabled, no .env). No real data, no key, no network.
Scenario numbers follow the M7-3 instruction (§63). Skipped without Playwright unless REQUIRE_BROWSER_TESTS=1.
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
from tests.fixtures import opp_synth as OS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = os.environ.get("REQUIRE_BROWSER_TESTS") == "1"
O = OS.O


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
def opp_server(tmp_path, hedef=True):
    data = OS.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": "disabled", "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": data, "DASHBOARD_PATH": dash,
           "AGENT_DB": str(tmp_path / "state.db")}
    env.pop("HEDEF_PATH", None)
    env.pop("HEDEF_SNAPSHOT_DATE", None)
    if hedef:
        env["HEDEF_PATH"] = OS.write_hedef(str(tmp_path / OS.HEDEF_FILE))
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
    page.on("request", lambda r: page.requests.append((r.method, r.url)))
    return page


def open_opp(page, base):
    page.goto(base + "/")
    page.click('.tab[data-t="opp"]')
    page.wait_for_selector('#opp [data-sec="model"]:not([hidden]) .op-tile')
    page.wait_for_selector('#opp [data-sec="pairs"] tbody tr')


def no_horizontal_overflow(page):
    w, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    return w <= iw


def tile(page, k):
    return page.inner_text(f'#opp .op-tile[data-k="{k}"] .n')


# ---------------------------------------------------------------------------------------------
# 1–4, 9, 10, 13: tab, summary, pair table, configured territory, hostile labels, zero AI
# ---------------------------------------------------------------------------------------------
def test_tab_summary_pairs_territory_hostile_labels(browser, tmp_path):
    with opp_server(tmp_path, hedef=True) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        # (1) tab exists after Müşteri 360; nothing fetched before it opens
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[-4:] == ["sq", "c360", "opp", "ai"] and page.inner_text('.tab[data-t="opp"]') == "Fırsatlar"
        assert not any("/api/opportunities" in u for _, u in page.requests)
        open_opp(page, srv["base"])
        # (2) model summary
        api = _get(srv["base"] + "/api/opportunities/summary?limit=20")
        assert tile(page, "active") == "61" and tile(page, "groups") == "5" and tile(page, "pairs") == "10"
        assert tile(page, "customers") == str(api["customers_with_opportunities"])
        assert tile(page, "targets") == str(api["opportunity_targets"])
        assert tile(page, "reservoir") == str(api["queue_reservoir"])
        th = page.inner_text("#opp .op-th")
        assert "kaynak alıcı ≥ 20" in th and "birlikte alan ≥ 10" in th and "%30" in th and "69" in th
        assert "stok ayrıca kontrol" in page.inner_text('#opp [data-sec="model"]')
        # (3) pair table: Python order, no customer identities
        rows = page.eval_on_selector_all('#opp .op-pairs tbody tr',
                                         "rs => rs.map(r => [...r.children].map(td => td.textContent))")
        assert [(r[0], r[1]) for r in rows] == [(p["source_group"], p["target_group"]) for p in api["pairs"]]
        assert len(rows) == 10 and "920.10." not in page.inner_text('#opp [data-sec="pairs"]')
        # (9) hostile group label rendered as text
        assert any(OS.X in r[0] or OS.X in r[1] for r in rows)
        # (4) territory configured: unverified badge, disclaimer, dimension switch
        page.wait_for_selector('#opp .op-terr tbody tr')
        sec = '#opp [data-sec="territory"]'
        assert page.get_attribute(sec, "data-status") == "ok"
        assert page.text_content(f"{sec} .op-badge") == "Doğrulanmadı"
        assert page.inner_text(f"{sec} .op-tnote") == ("Hedef bölge/sektör alanları doğrulanmamış kaynak bilgisidir; "
                                                         "bu tablo satış performansı sıralaması değildir.")
        city = page.eval_on_selector_all('#opp .op-terr tbody tr', "rs => rs.map(r => r.children[0].textContent)")
        assert city == ["Sentetik İl Bir", OS.XSS_CITY]                     # (10) hostile city as text
        page.click('#opp .op-dim[data-k="sector"]')
        page.wait_for_function("() => [...document.querySelectorAll('#opp .op-terr tbody tr')]"
                               ".map(r => r.children[0].textContent).join('|') === 'SK-1|SK-2'")
        page.click('#opp .op-dim[data-k="district"]')
        page.wait_for_function("() => (document.querySelector('#opp .op-terr tbody tr td')||{}).textContent"
                               " === 'Sentetik İlçe 1'")
        heads = page.eval_on_selector_all('#opp .op-terr th', "els => els.map(e => e.textContent)")
        assert not any("emsilci" in h or "kapasite" in h.lower() for h in heads)   # no salesperson / capacity
        # (9, 10) nothing executed
        assert page.evaluate("window.__OPP_XSS") is None and page.query_selector("#opp img") is None
        assert page.query_selector("#opp tbody b") is None
        # (13) zero AI calls; no AI endpoint touched
        assert ai_calls(srv) == 0 and page.errors == []
        assert not any("/api/agent/" in u for _, u in page.requests)
        log = open(srv["log"], encoding="utf-8").read()
        assert "SENT-KAŞE" not in log and "Sentetik İl Bir" not in log and "Sentetik Fırsat" not in log


# ---------------------------------------------------------------------------------------------
# 5: territory not configured (cross-sell still works)
# ---------------------------------------------------------------------------------------------
def test_territory_not_configured(browser, tmp_path):
    with opp_server(tmp_path, hedef=False) as srv:
        page = open_page(browser)
        open_opp(page, srv["base"])
        sec = '#opp [data-sec="territory"]'
        page.wait_for_selector(f'{sec}[data-status="not_configured"] .op-tstate:not([hidden])')
        assert page.inner_text(f"{sec} .op-tstate") == "Bölge / sektör görünümü için Hedef dosyası yapılandırılmadı."
        assert page.query_selector(f"{sec} tbody tr") is None
        assert tile(page, "pairs") == "10" and page.errors == [] and ai_calls(srv) == 0


# ---------------------------------------------------------------------------------------------
# 6, 8: Günlük Satış deep link with the cross-sell filter; KAPALI excluded
# ---------------------------------------------------------------------------------------------
def test_daily_sales_cross_sell_deep_link_and_kapali(browser, tmp_path):
    with opp_server(tmp_path, hedef=True) as srv:
        page = open_page(browser)
        open_opp(page, srv["base"])
        page.click("#opp .op-go")
        page.wait_for_selector('#sq .sq-f.on[data-k="cross_sell"]')
        page.wait_for_selector("#sq .sq-card")
        assert page.is_visible("#sq") and not page.is_visible("#opp")
        assert "on" in page.get_attribute('.tab[data-t="sq"]', "class")
        assert any("/api/sales-queue?filter=cross_sell" in u for _, u in page.requests)
        api = _get(srv["base"] + "/api/sales-queue?filter=cross_sell&limit=20")
        codes = page.eval_on_selector_all("#sq .sq-card", "els => els.map(e => e.dataset.code)")
        assert codes == [k["customer_code"] for k in api["items"]] and len(codes) == 20
        chips = page.eval_on_selector_all("#sq .sq-card", "els => els.map(e => e.querySelector('.sq-chips').textContent)")
        assert all("Çapraz Satış" in c for c in chips)
        assert page.inner_text('#sq .sq-f[data-k="cross_sell"]') == f"Çapraz Satış ({api['filter_counts']['cross_sell']})"
        # (8) the KAPALI customer never appears as a cross-sell card
        full = _get(srv["base"] + "/api/sales-queue?filter=cross_sell&limit=50")
        assert O[30] not in [k["customer_code"] for k in full["items"]]
        assert page.query_selector(f'#sq .sq-card[data-code="{O[30]}"]') is None
        # winback before cross-sell (sequence), collections first
        page.click('#sq .sq-f[data-k="winback"]')
        page.wait_for_selector(f'#sq .sq-card[data-code="{O[27]}"]')
        page.click(f'#sq .sq-card[data-code="{O[27]}"] .sq-toggle')
        cats = page.eval_on_selector_all(f'#sq .sq-card[data-code="{O[27]}"] .sq-r', "els => els.map(e => e.dataset.cat)")
        assert cats == ["winback", "cross_sell", "cross_sell"]
        # clicking the deep link again while the queue is already open reloads with the filter
        page.click('.tab[data-t="opp"]')
        page.click("#opp .op-go")
        page.wait_for_selector('#sq .sq-f.on[data-k="cross_sell"]')
        assert ai_calls(srv) == 0 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 7, 8, 9: Customer 360 Fırsatlar section (incl. KAPALI note, hostile label)
# ---------------------------------------------------------------------------------------------
def _open_c360(page, code):
    page.click('.tab[data-t="c360"]')
    page.wait_for_selector("#c360 .c3-q:not([disabled])")
    page.evaluate(f"window.StockAgentC360.open({json.dumps(code)})")
    page.wait_for_function(f"() => (document.querySelector('#c360 .c3-code') || {{}}).textContent === "
                           f"{json.dumps(code)}")
    page.wait_for_selector('#c360 .c3-card[data-sec="opportunities"]')


def test_customer360_opportunity_section(browser, tmp_path):
    with opp_server(tmp_path, hedef=True) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        _open_c360(page, O[41])
        sec = '#c360 .c3-card[data-sec="opportunities"]'
        assert page.text_content(f"{sec} h4") == "Fırsatlar"
        assert page.get_attribute(sec, "data-status") == "available"
        boxes = page.eval_on_selector_all(f"{sec} .c3-opp", "els => els.map(e => e.dataset.target)")
        det = _get(srv["base"] + f"/api/customer360/{O[41]}")["opportunities"]
        assert boxes == [i["target_group"] for i in det["items"]] and len(boxes) == 3
        txt = page.inner_text(sec)
        assert "Çapraz satış adayı" in txt and "hiç alım yapmadı" in txt and "stok ayrıca kontrol" in txt
        assert "Ürün kapsam fırsatı" in txt
        low = txt.lower()
        assert "penetrasyon" not in low and "potansiyel" not in low and "sku-" not in low
        assert len(page.query_selector_all(f'{sec} [data-never="1"]')) == 3
        secs = page.eval_on_selector_all("#c360 .c3-card", "els => els.map(e => e.dataset.sec)")
        assert secs.index("opportunities") == secs.index("products") + 1
        # (9) hostile group label inert
        assert any(OS.X in b for b in boxes)
        assert page.evaluate("window.__OPP_XSS") is None and page.query_selector("#c360 .c3-opp img") is None
        # (8) KAPALI customer: opportunities shown with the queue note
        _open_c360(page, O[30])
        assert "KAPALI" in page.inner_text("#c360 .c3-name")
        assert "Günlük Satış'a satış gerekçesi olarak alınmaz" in page.inner_text(sec)
        # a customer without targets
        _open_c360(page, O[70])
        assert page.get_attribute(sec, "data-status") == "none"
        assert ai_calls(srv) == 0 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 11: phone width
# ---------------------------------------------------------------------------------------------
def test_phone_width(browser, tmp_path):
    with opp_server(tmp_path, hedef=True) as srv:
        page = open_page(browser, viewport={"width": 390, "height": 844})
        open_opp(page, srv["base"])
        page.wait_for_selector('#opp .op-terr tbody tr')
        assert no_horizontal_overflow(page)
        for box in ("#opp .op-pairs", "#opp .op-terr"):
            sw, cw, ox = page.eval_on_selector(box, "e => [e.scrollWidth, e.clientWidth, getComputedStyle(e).overflowX]")
            assert ox == "auto" and cw <= 390                              # the table scrolls inside its card
        n = page.eval_on_selector('#opp .op-tile .n', "e => e.getClientRects().length")
        assert n == 1                                                     # numbers never split
        _open_c360(page, O[41])
        assert no_horizontal_overflow(page)
        page.mouse.wheel(0, 3000)
        assert no_horizontal_overflow(page) and page.errors == [] and ai_calls(srv) == 0


# ---------------------------------------------------------------------------------------------
# 12: file:// mode
# ---------------------------------------------------------------------------------------------
def test_file_mode_requires_server(browser, tmp_path):
    data = OS.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    page = open_page(browser)
    page.goto("file://" + dash)
    page.click('.tab[data-t="overview"]')
    page.click('.tab[data-t="opp"]')
    page.wait_for_selector("#opp .op-state:not([hidden])")
    assert page.inner_text("#opp .op-state") == "Fırsatlar için panoyu http://127.0.0.1:8000/ üzerinden açın."
    assert page.is_hidden('#opp [data-sec="model"]') and page.is_hidden('#opp [data-sec="territory"]')
    page.click('.tab[data-t="reorder"]')
    assert page.query_selector("#reorder table") is not None                 # static tabs still work
    assert [u for _, u in page.requests if not u.startswith("file://")] == [] and page.errors == []
