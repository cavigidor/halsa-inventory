"""M7-4: Playwright scenarios for the Fuar Merkezi tab (and its links into Günlük Satış and Müşteri 360) in real
Chromium against the real FastAPI app.

Each test starts its own server (free port, temporary folder with SYNTHETIC fair workbooks, temporary dashboard and
SQLite DB, AI_PROVIDER=disabled, no .env). No real data, no key, no network. Scenario numbers follow §94.
Skipped without Playwright unless REQUIRE_BROWSER_TESTS=1.
"""
import contextlib
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from services import refresh as RF
from tests.fixtures import fair_synth as FX

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = os.environ.get("REQUIRE_BROWSER_TESTS") == "1"
P = FX.P
TODAY = dt.date.today().isoformat()
XSS_CAMPAIGN = '<img src=x onerror="window.__FAIR_XSS=3">Sentetik Kampanya'
XSS_NOTE = '<script>window.__FAIR_XSS=4</script>not'


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


def _post(url, body, method="POST"):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json", "X-StockAgent": "1"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


@contextlib.contextmanager
def fair_server(tmp_path):
    data = FX.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": "disabled", "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": data, "DASHBOARD_PATH": dash,
           "AGENT_DB": str(tmp_path / "state.db")}
    env.pop("HEDEF_PATH", None)
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
    page.on("request", lambda r: page.requests.append((r.method, r.url, r.post_data)))
    return page


def open_fair(page, base):
    page.goto(base + "/")
    page.click('.tab[data-t="fair"]')
    page.wait_for_selector('#fair [data-sec="campaign"]:not([hidden])')
    page.wait_for_selector('#fair .fm-h-list tbody tr')


def create_campaign(page, name="Sentetik Ocak Fuarı", year="2027", start="2027-01-15"):
    page.click("#fair .fm-new")
    page.fill("#fair .f-name", name)
    page.fill("#fair .f-year", year)
    page.fill("#fair .f-start", start)
    page.click("#fair .f-save")
    page.wait_for_selector("#fair .fm-head h4")


def fcard(code):
    return f'#fair .fm-card[data-code="{code}"]'


def add_by_search(page, query, code):
    page.fill("#fair .fm-add-q", query)
    page.wait_for_selector(f'#fair .fm-hit[data-code="{code}"]')
    page.click(f'#fair .fm-hit[data-code="{code}"] .fm-addone')


def stage_count(page, key):
    return page.inner_text(f'#fair .fm-stage[data-k="{key}"] b')


def no_horizontal_overflow(page):
    w, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    return w <= iw


# ---------------------------------------------------------------------------------------------
# 1–9, 18: tab, empty state, create, search/add, KAPALI, stage, date, funnel, candidates, zero AI
# ---------------------------------------------------------------------------------------------
def test_campaign_workflow(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[-6:] == ["sq", "c360", "opp", "fair", "crm", "ai"] and page.inner_text('.tab[data-t="fair"]') == "Fuar Merkezi"
        assert not any("/api/fairs" in u for _, u, _ in page.requests)                          # (1) lazy
        open_fair(page, srv["base"])
        assert page.is_visible("#fair .fm-empty") and "Henüz fuar kampanyası yok" in page.inner_text("#fair .fm-empty")
        create_campaign(page)                                                                    # (3)
        assert page.inner_text("#fair .fm-head h4") == "Sentetik Ocak Fuarı"
        assert "ERP fuar kimliği tanımlı değil" in page.inner_text("#fair .fm-head")
        assert page.is_hidden("#fair .fm-empty")
        add_by_search(page, "Sentetik Fuar 17", P[17])                                          # (4)
        page.wait_for_selector(fcard(P[17]))
        assert page.get_attribute(fcard(P[17]), "data-stage") == "invite_candidate"
        add_by_search(page, "Sentetik Fuar 10", P[10])                                          # (5) KAPALI
        page.wait_for_function("() => document.querySelector('#fair .fm-toast').textContent.includes('KAPALI engellendi: 1')")
        assert page.query_selector(fcard(P[10])) is None
        page.select_option(f"{fcard(P[17])} .fm-stage-sel", "called")                            # (6)
        page.fill(f"{fcard(P[17])} .fm-next", TODAY)                                              # (7)
        page.click(f"{fcard(P[17])} .fm-save")
        page.wait_for_selector(f'{fcard(P[17])}[data-stage="called"] .fm-due')
        assert stage_count(page, "called") == "1" and stage_count(page, "invite_candidate") == "0"   # (8)
        assert "zamanı gelen / gecikmiş görev: 1" in page.inner_text("#fair .fm-fnote")
        # (9) candidate discovery: read-only until an explicit selection
        page.wait_for_selector("#fair .fm-c-list tbody tr")
        assert "Bu segmentte 7 müşteri var" in page.inner_text("#fair .fm-c-count")
        assert "KAPALI olduğu için gösterilmeyen: 1" in page.inner_text("#fair .fm-c-count")
        cid = _get(srv["base"] + "/api/fairs/campaigns")["items"][0]["campaign_id"]
        assert _get(srv["base"] + f"/api/fairs/campaigns/{cid}")["total_customers"] == 1          # nothing auto-added
        page.select_option("#fair .fm-c-seg", "collections")
        page.wait_for_selector(f'#fair .fm-c-list input[data-code="{P[15]}"]')
        assert "Tahsilat önce" in page.inner_text("#fair .fm-c-list")
        page.check(f'#fair .fm-c-list input[data-code="{P[15]}"]')
        page.click("#fair .fm-c-add")
        page.wait_for_selector(fcard(P[15]))
        assert "Tahsilat önce" in page.inner_text(fcard(P[15]))
        assert stage_count(page, "invite_candidate") == "1"
        # funnel chip filters the list
        page.click('#fair .fm-stage[data-k="called"]')
        page.wait_for_function(f"() => document.querySelectorAll('#fair .fm-card').length === 1 && "
                               f"!!document.querySelector('{fcard(P[17])}')")
        assert ai_calls(srv) == 0 and page.errors == []                                          # (18)
        assert not any("/api/agent/" in u for _, u, _ in page.requests)


# ---------------------------------------------------------------------------------------------
# 10–12: historical list, edition detail, Müşteri 360 link (with campaign context)
# ---------------------------------------------------------------------------------------------
def test_history_detail_and_customer360(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        c = _post(srv["base"] + "/api/fairs/campaigns", {"name": "Bağlı Fuar", "edition_year": 2025,
                                                          "start_date": "2025-01-19", "event_stem": "SENTFUARA"})
        page = open_page(browser)
        open_fair(page, srv["base"])
        labels = page.eval_on_selector_all("#fair .fm-h-list tbody tr", "rs => rs.map(r => r.children[1].textContent)")
        api = _get(srv["base"] + "/api/fairs/history?limit=200")
        assert labels == [x["label"] for x in api["items"]] and len(labels) == 4               # (10)
        assert FX.EX in labels                                                                 # hostile as text
        notes = page.inner_text("#fair .fm-h-notes")
        assert "ETKİNLİK ve SAHA" in notes and "ROI" in notes and "dönüşüm penceresi" in notes
        e25 = next(x for x in api["items"] if x["label"] == FX.E25)["event_id"]
        page.click(f'#fair .fm-h-list tbody tr[data-event="{e25}"]')                           # (11)
        page.wait_for_selector(f'#fair .fm-h-detail[data-event="{e25}"] .fm-hpick')
        det = page.inner_text("#fair .fm-h-detail")
        assert "Fuar etiketli sipariş tutarına göre" in det and "katılım veya davet kaydı değildir" in det
        assert "Net Sipariş Tutarı (Nettutar)" in det and "Veri kalitesi" in det
        rows = page.eval_on_selector_all("#fair .fm-h-detail .fm-hpick", "els => els.map(e => e.dataset.code)")
        assert rows == [P[1], P[2], P[12], P[10]]
        assert page.is_disabled(f'#fair .fm-h-detail .fm-hpick[data-code="{P[10]}"]')          # KAPALI
        page.check(f'#fair .fm-h-detail .fm-hpick[data-code="{P[1]}"]')
        page.check(f'#fair .fm-h-detail .fm-hpick[data-code="{P[2]}"]')
        page.click("#fair .fm-hadd")                                                           # explicit copy
        page.wait_for_selector(fcard(P[1]))
        assert _get(srv["base"] + f"/api/fairs/campaigns/{c['campaign_id']}")["total_customers"] == 2
        assert "ERP'de bu fuara atfedilmiş sipariş bulundu" in page.inner_text(fcard(P[1]))
        assert page.get_attribute(fcard(P[1]), "data-stage") == "invite_candidate"            # never auto-advanced
        page.click(f"{fcard(P[1])} .fm-mark")                                                 # human-confirmed
        page.wait_for_selector(f'{fcard(P[1])}[data-stage="order"]')
        # (12) Müşteri 360 from the history table, with the read-only campaign context
        page.wait_for_selector(f'#fair .fm-h-detail[data-event="{e25}"] .fm-c360')
        page.click(f'#fair .fm-h-detail tr:has(input[data-code="{P[2]}"]) .fm-c360')
        page.wait_for_function(f"() => (document.querySelector('#c360 .c3-code') || {{}}).textContent === '{P[2]}'")
        page.wait_for_selector('#c360 [data-sec="events"] [data-fair="1"]')
        ev = page.inner_text('#c360 [data-sec="events"]')
        assert "Bağlı Fuar" in ev and "Davet Edilecek" in ev
        assert ai_calls(srv) == 0 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 13, 14, 18: Günlük Satış fair filter, explicit fair-invite draft only
# ---------------------------------------------------------------------------------------------
def test_daily_sales_fair_filter_and_draft(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        c = _post(srv["base"] + "/api/fairs/campaigns", {"name": "Sentetik Fuar Takip", "edition_year": 2027,
                                                          "start_date": "2027-01-15", "status": "active"})
        cid = c["campaign_id"]
        _post(srv["base"] + f"/api/fairs/campaigns/{cid}/customers", {"customer_codes": [P[17], P[18]]})
        _post(srv["base"] + f"/api/fairs/campaigns/{cid}/customers",
              {"customer_code": P[17], "stage": "called", "next_action_date": TODAY}, method="PUT")
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        page.click('.tab[data-t="sq"]')
        page.wait_for_selector("#sq .sq-card")
        page.click('#sq .sq-f[data-k="fair_followup"]')
        page.wait_for_selector(f'#sq .sq-card[data-code="{P[17]}"]')
        codes = page.eval_on_selector_all("#sq .sq-card", "els => els.map(e => e.dataset.code)")
        assert codes == [P[17]]                                                                 # (13) P18: no date
        assert "Fuar Takibi" in page.inner_text(f'#sq .sq-card[data-code="{P[17]}"] .sq-chips')
        assert "fuar takibi zamanı geldi" in page.inner_text(f'#sq .sq-card[data-code="{P[17]}"] .sq-primary')
        assert not any("/api/agent/" in u for _, u, _ in page.requests)                        # (14)
        page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-drafto')
        assert page.eval_on_selector(f'#sq .sq-card[data-code="{P[17]}"] .sq-draft select', "s => s.value") == "fair_invite"
        assert not any("/api/agent/" in u for _, u, _ in page.requests)
        with page.expect_request("**/api/agent/draft-message") as req:
            page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-dgo')
        body = json.loads(req.value.post_data)
        assert body["purpose"] == "fair_invite" and body["customer_code"] == P[17]
        assert set(body) == {"customer_code", "channel", "purpose"}                             # no fair data sent
        page.wait_for_selector(f'#sq .sq-card[data-code="{P[17]}"] .sq-draft .sq-err')
        # completing the queue task does not change the funnel stage
        page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-toggle')
        page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-r .sq-done')
        page.wait_for_function(f"() => !document.querySelector('#sq .sq-card[data-code=\"{P[17]}\"]')")
        rows = _get(srv["base"] + f"/api/fairs/campaigns/{cid}/customers")["items"]
        assert next(r for r in rows if r["customer_code"] == P[17])["stage"] == "called"
        assert ai_calls(srv) == 0 and page.errors == []                                         # (18)


# ---------------------------------------------------------------------------------------------
# 15, 16: hostile text, phone width
# ---------------------------------------------------------------------------------------------
def test_hostile_text_and_phone_width(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = open_page(browser, viewport={"width": 390, "height": 844})
        # wider glyphs than the local font (CI runners render text wider): long tokens must still wrap
        page.add_init_script("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');"
                             "s.textContent='*{letter-spacing:1.2px!important}';document.head.append(s);});")
        open_fair(page, srv["base"])
        create_campaign(page, name=XSS_CAMPAIGN)
        add_by_search(page, "930.10.0012", P[12])                                               # hostile name
        page.wait_for_selector(fcard(P[12]))
        page.fill(f"{fcard(P[12])} .fm-notein", XSS_NOTE)
        page.click(f"{fcard(P[12])} .fm-save")
        page.wait_for_function(f"() => (document.querySelector('{fcard(P[12])} .fm-notein')||{{}}).value === {json.dumps(XSS_NOTE)}")
        assert "<img" in page.inner_text("#fair .fm-head h4") and "<img" in page.inner_text(f"{fcard(P[12])} .fm-name")
        ex = next(x for x in _get(srv["base"] + "/api/fairs/history")["items"] if x["label"] == FX.EX)["event_id"]
        page.click(f'#fair .fm-h-list tbody tr[data-event="{ex}"]')
        page.wait_for_selector(f'#fair .fm-h-detail[data-event="{ex}"] h4')
        assert "<img" in page.inner_text("#fair .fm-h-detail h4")
        assert page.evaluate("window.__FAIR_XSS") is None and page.query_selector("#fair img") is None
        assert page.query_selector("#fair .fm-clist script") is None
        assert page.query_selector("#fair .fm-name b") is None and page.query_selector("#fair .fm-h-detail h4 *") is None
        # (16) phone width
        assert no_horizontal_overflow(page)
        for sel in ("#fair .fm-funnel", f"{fcard(P[12])} .fm-stage-sel", f"{fcard(P[12])} .fm-save", "#fair .fm-new"):
            box = page.eval_on_selector(sel, "e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }")
            assert box[0] >= 0 and box[1] <= 390
        h = page.eval_on_selector(f"{fcard(P[12])} .fm-save", "e => e.getBoundingClientRect().height")
        assert h >= 36
        ox = page.eval_on_selector("#fair .fm-h-list .fm-scroll", "e => getComputedStyle(e).overflowX")
        assert ox == "auto"
        page.mouse.wheel(0, 4000)
        assert no_horizontal_overflow(page) and page.errors == [] and ai_calls(srv) == 0
        log = open(srv["log"], encoding="utf-8").read()
        assert "SENTFUARA" not in log and "Sentetik Kampanya" not in log and "not</script>" not in log


# ---------------------------------------------------------------------------------------------
# 17: file:// mode
# ---------------------------------------------------------------------------------------------
def test_file_mode_requires_server(browser, tmp_path):
    data = FX.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    page = open_page(browser)
    page.goto("file://" + dash)
    page.click('.tab[data-t="fair"]')
    page.wait_for_selector("#fair .fm-state:not([hidden])")
    assert page.inner_text("#fair .fm-state") == "Fuar Merkezi için panoyu http://127.0.0.1:8000/ üzerinden açın."
    assert page.is_hidden('#fair [data-sec="campaign"]') and page.is_hidden('#fair [data-sec="history"]')
    page.click('.tab[data-t="reorder"]')
    assert page.query_selector("#reorder table") is not None
    assert [u for _, u, _ in page.requests if not u.startswith("file://")] == [] and page.errors == []
