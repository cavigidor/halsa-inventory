"""M7-6: Playwright scenarios for the Yönetim tab, full M7 navigation, phone layout and async-race regressions in real
Chromium against the real FastAPI app.

Each test starts its own server (free port, temporary folder with SYNTHETIC workbooks — including synthetic owner
workbooks passed through the explicit KREDI_PATH / GIDER_GELIR_PATH / BUTCE_PATH variables — temporary dashboard and
SQLite DB, AI_PROVIDER=disabled, no .env). No real data, no key, no network. Scenario numbers follow §86 (1–16).
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
from tests import browser_auth as BA  # M8-CP1: real owner login for owner actions
from tests.fixtures import fair_synth as FX
from tests.fixtures import owner_synth as OS
from tests.test_m7_fair_browser import _get, _post, ai_calls, browser, no_horizontal_overflow, open_page  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = FX.P
TODAY = dt.date.today()
M7_TABS = ("sq", "c360", "opp", "fair", "crm", "own", "ai")
XSS_CUR = '<img src=x onerror="window.__OWN_XSS=1">'
XSS_GG = "2026_gider_gelir_<img src=x onerror=window.__OWN_XSS=2>.xlsx"
XSS_BU = "BUTCE_2026 <i onmouseover=window.__OWN_XSS=3>Rev.xlsx"
LETTER_SPACING = ("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');"
                  "s.textContent='*{letter-spacing:1.2px!important}';document.head.append(s);});")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def server(tmp_path, owner=None):
    data = FX.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": "disabled", "HALSA_TEST_SYNTHETIC_COLLECTIONS": "FAIR", "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": data, "DASHBOARD_PATH": dash,
           "AGENT_DB": str(tmp_path / "state.db"), **BA.server_env()}
    for k in ("HEDEF_PATH", "KREDI_PATH", "GIDER_GELIR_PATH", "BUTCE_PATH", "KREDI_SNAPSHOT_DATE",
              "GIDER_GELIR_REPORT_YEAR", "BUTCE_PLANNING_YEAR", "BUTCE_SNAPSHOT_DATE"):
        env.pop(k, None)
    env.update({k: str(v) for k, v in (owner or {}).items()})
    port = _free_port()
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "tests.accuracy_harness_app:app", "--host", "127.0.0.1", "--port", str(port)],
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
        BA.register(base)
        yield {"base": base, "dash": dash, "data": data, "log": tmp_path / "server.log"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def all_owner(tmp_path, **kw):
    d = tmp_path / "owner"
    return {"KREDI_PATH": OS.write_kredi(d, TODAY, **kw), "GIDER_GELIR_PATH": OS.write_gider_gelir(d),
            "BUTCE_PATH": OS.write_butce(d)}


def open_own(page, base, *, ready=("kredi", "gg", "butce")):
    page.goto(base + "/")
    page.click('.tab[data-t="own"]')
    page.wait_for_selector('#own [data-sec="ops"][data-state="ready"]')
    for sec in ready:
        page.wait_for_selector(f'#own [data-sec="{sec}"][data-state="ready"]')


def delay_fetch(page, pattern, ms, method=None):
    """Delay matching fetch() responses in the page (simulates a slow / late server response)."""
    page.add_init_script(f"""(()=>{{const f=window.fetch.bind(window); window.fetch=async(u,o)=>{{
      const r=await f(u,o); const m=(o&&o.method)||'GET';
      if(String(u).includes({json.dumps(pattern)}) && ({json.dumps(method)}===null || m==={json.dumps(method)}))
        await new Promise(res=>setTimeout(res,{ms}));
      return r;}};}})();""")


def reqs(page, part, method=None):
    return [u for m, u, _ in page.requests if part in u and (method is None or m == method)]


# ---------------------------------------------------------------------------------------------
# 1, 3, 4, 6, 7, 12, 16: tab, configured sources, reload, operational summary, warnings, full navigation, zero AI
# ---------------------------------------------------------------------------------------------
def test_owner_tab_configured_and_navigation(browser, tmp_path):
    with server(tmp_path, all_owner(tmp_path)) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[-7:] == list(M7_TABS) and page.inner_text('.tab[data-t="own"]') == "Yönetim"      # (1)
        assert reqs(page, "/api/owner") == []                                                        # lazy
        log = open(srv["log"], encoding="utf-8").read()
        assert "kredi" not in log.lower()                                       # nothing read before the tab opens
        open_own(page, srv["base"])
        assert "erişim kontrollüdür" in page.inner_text("#own .ow-aud")                  # M8-CP1 (D-061)
        k = page.inner_text('#own [data-sec="kredi"]')                                                # (3)
        assert OS.KREDI_FILE in k and "Müşteri kredisi" in k
        assert "Kaynağın kesin anlık tarihi bilinmiyor" in k and "Nakit akış tahmini değildir" in k
        assert "90 GÜN SONRASI" in k.upper() and "VADESI GEÇMIŞ (AYRI)" in k.upper().replace("İ", "I")
        assert page.query_selector('#own [data-sec="kredi"] .ow-cur[data-currency="TL"] table') is not None
        g = page.inner_text('#own [data-sec="gg"]')
        assert "Muhasebe esası (nakit / tahakkuk) doğrulanmadı" in g and "KIRTASIYE" in g and "IMALAT" in g
        assert "Eylül" in g
        b = page.inner_text('#own [data-sec="butce"]')
        assert "kısmen kullanılabilir" in b and "#REF!" in b
        for sec in ("kredi", "gg", "butce"):
            assert page.inner_text(f'#own [data-sec="{sec}"] .ow-chip') == "Hazır"
        ops = page.inner_text('#own [data-sec="ops"]')                                                # (6)
        assert "Müşteri 360 müşterisi" in ops and "Eksik kayıt olabilir" in ops
        assert page.inner_text('#own .ow-tile[data-k="customer360_customers"] .n') == "20"
        assert "Kişi veya temsilci bazında sayım ya da sıralama yapılmaz" in ops
        sysbox = page.inner_text('#own [data-sec="system"]')                                          # (7)
        assert "Sağlanmadı / M6-4 bloke" in sysbox and "DQ-1" in sysbox and "DQ-2" in sysbox
        assert "DQ-4" in sysbox and "DQ-5" in sysbox and "Çapraz satış eşikleri" in sysbox
        assert page.get_attribute('#own .ow-issue[data-code="detailed_collections_missing"]', "data-status") == "blocked"
        assert srv["data"] not in page.content() and str(tmp_path) not in page.content()
        # (4) reload: one POST, disabled while in flight, sources re-read, sections keep their content
        with page.expect_response(lambda r: "/api/owner/reload" in r.url) as resp:
            page.click("#own .ow-reload")
        assert resp.value.status == 200 and resp.value.json()["started"] is True
        page.wait_for_function("() => !document.querySelector('#own .ow-reload').disabled")
        page.wait_for_selector('#own [data-sec="kredi"][data-state="ready"][data-reloading="0"]')
        assert len(reqs(page, "/api/owner/reload", "POST")) == 1
        assert _get(srv["base"] + "/api/owner/status")["reload"]["generation"] == 2
        # (12) every tab opens without a page error (server tabs fetch lazily)
        for t in tabs:
            page.click(f'.tab[data-t="{t}"]')
            assert page.get_attribute(f'.tab[data-t="{t}"]', "aria-current") == "page"
            assert page.is_visible(f"#{t}")
            if t == "sq":
                page.wait_for_selector("#sq .sq-card")
        # §54 accessibility smoke: named buttons, labelled form fields, header cells, status regions
        page.click('.tab[data-t="crm"]')
        page.evaluate(f"window.StockAgentCRM.open({json.dumps(P[17])})")
        page.wait_for_selector(f'#crm .cr-panel[data-code="{P[17]}"] .ci-save')
        bad = page.evaluate("""() => {
          const out = [];
          for (const id of ['sq','c360','opp','fair','crm','own']) {
            const root = document.getElementById(id);
            root.querySelectorAll('button').forEach(b => { if (!(b.textContent.trim() || b.getAttribute('aria-label'))) out.push(id + ':button'); });
            root.querySelectorAll('input,select,textarea').forEach(f => {
              if (f.type === 'hidden') return;
              if (!(f.closest('label') || f.getAttribute('aria-label') || (f.id && root.querySelector('label[for="' + f.id + '"]'))))
                out.push(id + ':' + f.tagName + '.' + f.className);
            });
          }
          return out; }""")
        assert bad == [], bad
        assert page.eval_on_selector_all("#own table thead th", "ths => ths.every(t => t.scope === 'col')")
        assert page.get_attribute("#own .ow-state", "role") == "status"
        assert ai_calls(srv) == 0 and page.errors == []                                              # (16)
        assert reqs(page, "/api/agent/analyze") == [] and not reqs(page, "/api/context/refresh")
        assert not any("/api/agent/" in u and m == "POST" for m, u, _ in page.requests)


# ---------------------------------------------------------------------------------------------
# 2: no-source state; 9: file:// mode
# ---------------------------------------------------------------------------------------------
def test_no_sources_and_file_mode(browser, tmp_path):
    with server(tmp_path) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        page.click('.tab[data-t="own"]')
        page.wait_for_selector('#own [data-sec="ops"][data-state="ready"]')
        for sec in ("kredi", "gg", "butce"):
            page.wait_for_selector(f'#own [data-sec="{sec}"][data-state="not_configured"]')
            assert "Yapılandırılmadı" in page.inner_text(f'#own [data-sec="{sec}"]')
        assert "dosya otomatik seçilmez" in page.inner_text('#own [data-sec="kredi"]')
        assert page.errors == [] and ai_calls(srv) == 0
        # (9) file:// — every server-driven tab shows its message and makes no request
        fp = open_page(browser)
        fp.goto("file://" + srv["dash"])
        fp.click('.tab[data-t="own"]')
        fp.wait_for_selector("#own .ow-state:not([hidden])")
        assert fp.inner_text("#own .ow-state") == "Yönetim için panoyu http://127.0.0.1:8000/ üzerinden açın."
        for sec in ("ops", "kredi", "gg", "butce", "system"):
            assert fp.is_hidden(f'#own [data-sec="{sec}"]')
        assert fp.is_hidden("#own .ow-bar")
        for t in M7_TABS:
            fp.click(f'.tab[data-t="{t}"]')
        fp.click('.tab[data-t="reorder"]')
        assert fp.query_selector("#reorder table") is not None
        assert [u for _, u, _ in fp.requests if not u.startswith("file://")] == [] and fp.errors == []


# ---------------------------------------------------------------------------------------------
# 5: one owner source fails, the others render
# ---------------------------------------------------------------------------------------------
def test_failure_isolation(browser, tmp_path):
    d = tmp_path / "owner"
    owner = {"KREDI_PATH": OS.write_kredi(d, TODAY), "GIDER_GELIR_PATH": OS.write_gider_gelir(d),
             "BUTCE_PATH": OS.write_broken(d, "BUTCE_2026 Bozuk.xlsx")}
    with server(tmp_path, owner) as srv:
        page = open_page(browser)
        open_own(page, srv["base"], ready=("kredi", "gg"))
        page.wait_for_selector('#own [data-sec="butce"][data-state="failed"]')
        assert page.inner_text('#own [data-sec="butce"] .ow-chip') == "Okunamadı"
        assert page.query_selector('#own [data-sec="butce"] .ow-err') is not None
        assert page.query_selector('#own [data-sec="kredi"] table') is not None
        assert page.query_selector('#own [data-sec="gg"] .ow-months') is not None
        st = {s["key"]: s["state"] for s in _get(srv["base"] + "/api/owner/status")["system"]["owner_sources"]}
        assert st == {"kredi": "ready", "gider_gelir": "ready", "butce": "failed"}
        assert str(d) not in page.content() and page.errors == [] and ai_calls(srv) == 0


# ---------------------------------------------------------------------------------------------
# 8, 10: phone layout across ALL M7 tabs; hostile owner-source labels
# ---------------------------------------------------------------------------------------------
def test_hostile_labels_and_phone_all_tabs(browser, tmp_path):
    d = tmp_path / "owner"
    owner = {"KREDI_PATH": OS.write_kredi(d, TODAY, currency2=XSS_CUR),
             "GIDER_GELIR_PATH": OS.write_gider_gelir(d, name=XSS_GG), "BUTCE_PATH": OS.write_butce(d, name=XSS_BU)}
    with server(tmp_path, owner) as srv:
        page = open_page(browser, viewport={"width": 390, "height": 844})
        page.add_init_script(LETTER_SPACING)
        open_own(page, srv["base"])
        assert "<img" in page.inner_text('#own [data-sec="kredi"]')                                   # (10)
        assert "<img" in page.inner_text('#own [data-sec="gg"]') and "<i " in page.inner_text('#own [data-sec="butce"]')
        assert page.evaluate("window.__OWN_XSS") is None
        assert page.query_selector("#own img") is None and page.query_selector("#own i") is None
        assert no_horizontal_overflow(page)                                                           # (8)
        for sel in ("#own .ow-reload", "#own .ow-tiles", '#own [data-sec="kredi"] .ow-scroll',
                    '#own [data-sec="gg"] .ow-scroll', "#own .ow-issues"):
            box = page.eval_on_selector(sel, "e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }")
            assert box[0] >= 0 and box[1] <= 390, sel
        assert page.eval_on_selector("#own .ow-reload", "e => e.getBoundingClientRect().height") >= 36
        assert page.eval_on_selector('#own [data-sec="gg"] .ow-scroll', "e => getComputedStyle(e).overflowX") == "auto"
        # tab strip: wraps, no clipped label, every tab inside the viewport
        boxes = page.eval_on_selector_all(".tab", "els => els.map(e => { const r = e.getBoundingClientRect(); "
                                                  "return [r.left, r.right, e.scrollWidth <= e.clientWidth + 1]; })")
        assert all(l >= 0 and r <= 390 and fits for l, r, fits in boxes)
        for t in M7_TABS:                                                                             # §53
            page.click(f'.tab[data-t="{t}"]')
            page.wait_for_timeout(250)
            if t == "sq":
                page.wait_for_selector("#sq .sq-card")
            if t == "c360":
                page.evaluate(f"window.StockAgentC360.open({json.dumps(P[12])})")              # hostile customer name
                page.wait_for_selector("#c360 .c3-code")
            if t == "fair":
                page.wait_for_selector("#fair .fm-h-list tbody tr")
            if t == "crm":
                page.wait_for_selector('#crm [data-sec="summary"]:not([hidden]) .cr-tile')
            page.mouse.wheel(0, 3000)
            assert no_horizontal_overflow(page), t
        assert page.errors == [] and ai_calls(srv) == 0
        log = open(srv["log"], encoding="utf-8").read()
        assert "onerror" not in log and "__OWN_XSS" not in log and str(d) not in log


# ---------------------------------------------------------------------------------------------
# 11: rapid / late owner reload; 13: CRM form not wiped; 14: fair add message; 15: rapid queue filters
# ---------------------------------------------------------------------------------------------
def test_async_races(browser, tmp_path):
    with server(tmp_path, all_owner(tmp_path)) as srv:
        # (11) rapid reload clicks: one POST; previous cards stay visible while the slow reply is pending
        page = open_page(browser)
        delay_fetch(page, "/api/owner/reload", 1200, "POST")
        open_own(page, srv["base"])
        gen0 = _get(srv["base"] + "/api/owner/status")["reload"]["generation"]
        page.evaluate("() => { const b = document.querySelector('#own .ow-reload'); for (let i = 0; i < 6; i++) b.click(); }")
        assert page.is_disabled("#own .ow-reload")
        assert page.query_selector('#own [data-sec="kredi"] .ow-cur table') is not None          # nothing blanked
        assert "yeniden okunuyor" in page.inner_text("#own .ow-reloadmsg")
        page.wait_for_function("() => !document.querySelector('#own .ow-reload').disabled", timeout=15000)
        assert len(reqs(page, "/api/owner/reload", "POST")) == 1
        assert _get(srv["base"] + "/api/owner/status")["reload"]["generation"] == gen0 + 1
        page.wait_for_selector('#own [data-sec="butce"][data-state="ready"][data-reloading="0"]')
        # server side: concurrent reload calls never start two loads at once
        outs = [_post(srv["base"] + "/api/owner/reload", {}) for _ in range(3)]
        assert sum(o["started"] for o in outs) >= 1
        assert page.errors == []

        # (13) CRM: the first open from Customer 360 renders the form once; typing survives the late summary load
        p2 = open_page(browser)
        delay_fetch(p2, "/api/crm/summary", 1500)
        p2.goto(srv["base"] + "/")
        p2.click('.tab[data-t="c360"]')
        p2.evaluate(f"window.StockAgentC360.open({json.dumps(P[17])})")
        p2.wait_for_selector('#c360 [data-sec="crm"] .c3-crmlog')
        p2.click('#c360 [data-sec="crm"] .c3-crmlog')
        p2.wait_for_selector(f'#crm .cr-panel[data-code="{P[17]}"] .ci-save')
        p2.fill("#crm .ci-outcome", "Yazılan sonuç")
        p2.fill("#crm .ci-next", "Yazılan iş")
        p2.wait_for_selector('#crm [data-sec="summary"]:not([hidden]) .cr-tile', timeout=15000)
        p2.wait_for_timeout(600)
        assert p2.input_value("#crm .ci-outcome") == "Yazılan sonuç" and p2.input_value("#crm .ci-next") == "Yazılan iş"
        assert len(reqs(p2, f"/api/crm/customers/{P[17]}", "GET")) == 1
        # switching customers quickly: only the last one renders
        p2.evaluate(f"window.StockAgentCRM.open({json.dumps(P[18])}); window.StockAgentCRM.open({json.dumps(P[19])})")
        p2.wait_for_selector(f'#crm .cr-panel[data-code="{P[19]}"] .ci-save')
        p2.wait_for_timeout(500)
        assert p2.get_attribute("#crm .cr-panel", "data-code") == P[19] and p2.errors == []

        # (14) Fuar Merkezi: the add message appears only after the member list is redrawn
        p3 = open_page(browser)
        delay_fetch(p3, "/api/fairs/campaigns/", 900, "GET")
        _post(srv["base"] + "/api/fairs/campaigns", {"name": "Sentetik Yarış", "edition_year": 2027,
                                                     "start_date": "2027-01-15"})
        p3.goto(srv["base"] + "/")
        p3.click('.tab[data-t="fair"]')
        p3.wait_for_selector("#fair .fm-head h4")
        p3.fill("#fair .fm-add-q", "Sentetik Fuar 17")
        p3.wait_for_selector(f'#fair .fm-hit[data-code="{P[17]}"]')
        p3.click(f'#fair .fm-hit[data-code="{P[17]}"] .fm-addone')
        p3.wait_for_function("() => (document.querySelector('#fair .fm-toast')||{}).textContent && "
                             "document.querySelector('#fair .fm-toast').textContent.includes('Eklendi: 1')", timeout=15000)
        assert p3.query_selector(f'#fair .fm-card[data-code="{P[17]}"]') is not None
        assert p3.errors == []

        # (15) Günlük Satış: a slow older filter response never overwrites a newer filter
        p4 = open_page(browser)
        delay_fetch(p4, "filter=winback", 1500)
        p4.goto(srv["base"] + "/")
        p4.click('.tab[data-t="sq"]')
        p4.wait_for_selector("#sq .sq-card")
        p4.click('#sq .sq-f[data-k="winback"]')
        p4.click('#sq .sq-f[data-k="collection_first"]')
        p4.wait_for_selector('#sq .sq-f.on[data-k="collection_first"]')
        want = [k["customer_code"] for k in _get(srv["base"] + "/api/sales-queue?filter=collection_first&limit=20")["items"]]
        p4.wait_for_timeout(2000)                                                   # let the slow winback reply land
        assert p4.eval_on_selector_all("#sq .sq-card", "els => els.map(e => e.dataset.code)") == want
        assert p4.get_attribute('#sq .sq-f.on', "data-k") == "collection_first"
        assert p4.errors == [] and ai_calls(srv) == 0
