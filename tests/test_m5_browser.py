"""M5-6: Playwright smoke tests in real Chromium against the real FastAPI app (D-033).

browser → generated dashboard → FastAPI → API → SQLite action state → mock/disabled AI.

Every test starts its own server process (free port, temporary data folder with SYNTHETIC workbooks,
temporary dashboard and SQLite DB, AI_PROVIDER=disabled|mock, no .env), waits for readiness by polling
/api/health, and stops it afterwards. No real data, no key, no network, no paid call.

Skipped when Python Playwright or Chromium is missing, unless REQUIRE_BROWSER_TESTS=1 (set in CI).
Install locally: pip install -r requirements-dev.txt && python -m playwright install chromium
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
from tests.fixtures import synthetic_erp as SE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = os.environ.get("REQUIRE_BROWSER_TESTS") == "1"
H = {"X-StockAgent": "1"}
XSS = '<script>window.__M5_XSS = true</script><img src=x onerror="window.__M5_XSS = true">'


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
    except Exception as e:                       # browser binary missing
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
def app_server(tmp_path, provider):
    data = SE.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]          # builds the dashboard (CLI pipeline)
    port = _free_port()
    log = open(tmp_path / "server.log", "w")
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": provider, "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": data, "DASHBOARD_PATH": dash,
           "AGENT_DB": str(tmp_path / "state.db")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:                                                     # readiness: health answers + context built
            if proc.poll() is not None:
                log.flush()
                pytest.fail("server exited early:\n" + open(tmp_path / "server.log").read()[-3000:])
            try:
                if _get(base + "/api/health").get("context_ready"):
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                pytest.fail("server not ready within 60 s")
            time.sleep(0.2)
        yield {"base": base, "dir": tmp_path, "dash": dash, "data": data}
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


def open_page(browser, viewport=None, permissions=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    if permissions:
        ctx.grant_permissions(permissions)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    return page


def open_ai_tab(page):
    page.click('.tab[data-t="ai"]')
    page.wait_for_selector("#ai .aic")


def card_ids(page):
    return page.eval_on_selector_all("#ai .aic", "els => els.map(e => e.dataset.id)")


def no_horizontal_overflow(page):
    w, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    return w <= iw


# ---------------- A: AI disabled — the product stays fully useful ----------------
def test_ai_disabled_dashboard_and_action_center(browser, tmp_path):
    with app_server(tmp_path, "disabled") as srv:
        page = open_page(browser)
        r = page.goto(srv["base"] + "/")
        assert r.status == 200
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[0] == "overview" and tabs[-1] == "ai" and "reorder" in tabs and "winback" in tabs
        for t in tabs[1:-1]:                                            # every existing tab still renders
            if t in ("c360", "sq", "opp", "fair", "crm", "own"):               # M7: server-driven, tested in test_m7_*browser
                continue
            page.click(f'.tab[data-t="{t}"]')
            assert page.is_visible(f"#{t}") and page.query_selector(f"#{t} table") is not None
        open_ai_tab(page)
        api = _get(srv["base"] + "/api/agent/actions")
        assert len(card_ids(page)) == api["count"] and card_ids(page) == [a["action_id"] for a in api["actions"]]
        first = page.query_selector("#ai .aic")
        assert first.query_selector(".aic-reason").inner_text().startswith("Neden:")
        assert first.query_selector(".aic-score").inner_text().startswith("skor ")
        assert len(first.query_selector_all(".aic-facts li")) == len(api["actions"][0]["facts"])
        # filters (server-described)
        chip = next(c for c in page.query_selector_all("#ai .ai-f")
                    if c.get_attribute("data-k") and not c.inner_text().endswith("(0)"))
        label = chip.inner_text().rsplit(" (", 1)[0]
        chip.click()
        badges = page.eval_on_selector_all("#ai .aic .aic-cat", "els => els.map(e => e.textContent)")
        assert badges and set(badges) == {label}
        page.click('#ai .ai-f[data-k=""]')
        assert len(card_ids(page)) == api["count"]
        # AI disabled: note shown, generation controls absent / disabled
        assert page.inner_text("#ai .ai-aist") == "AI kapalı (.env: AI_PROVIDER=openai)"
        assert not page.is_visible("#ai .ai-gen") and page.query_selector("#ai .ai-one") is None
        page.click("#ai .aic-draft")
        assert page.is_disabled("#ai .ai-dgo")
        # customer drawer works without AI
        page.click("#ai .aic-cust")
        page.wait_for_selector("#ai .ai-dr-row")
        page.click("#ai .ai-dr-x")
        assert not page.is_visible("#ai .ai-drawer")
        # Ertele / Gizle / undo remain usable
        page.click("#ai .aic-defer")
        assert page.is_visible("#ai .aic-deferf input[type=date]")
        page.click("#ai .aic-defer-no")
        target = card_ids(page)[-1]
        page.click(f'#ai .aic[data-id="{target}"] .aic-hide')
        page.wait_for_selector(f'#ai .aic[data-id="{target}"]', state="detached")
        page.click("#ai .ai-undo-b")
        page.wait_for_selector(f'#ai .aic[data-id="{target}"]')
        assert page.errors == []
        assert ai_calls(srv) == 0


# ---------------- B: mock AI — calls only on explicit clicks; copy-only drafts ----------------
def test_mock_ai_runs_only_on_explicit_clicks_and_drafts_are_copy_only(browser, tmp_path):
    with app_server(tmp_path, "mock") as srv:
        page = open_page(browser, permissions=["clipboard-read", "clipboard-write"])
        page.goto(srv["base"] + "/")
        assert ai_calls(srv) == 0, "page load"
        open_ai_tab(page)
        assert ai_calls(srv) == 0, "opening the AI tab"
        page.click("#ai .ai-f >> nth=1")
        page.click('#ai .ai-f[data-k=""]')
        page.click("#ai .aic-cust")
        page.wait_for_selector("#ai .ai-dr-row")
        page.click("#ai .ai-dr-x")
        assert ai_calls(srv) == 0, "filters and drawer"
        facts_before = page.eval_on_selector_all("#ai .aic .aic-facts", "els => els.map(e => e.innerText)")
        page.click("#ai .ai-gen")                                     # explicit
        page.wait_for_selector("#ai .aic-ai")
        assert ai_calls(srv) >= 1
        blk = page.query_selector("#ai .aic .aic-ai")
        assert blk.query_selector(".aic-ai-h").inner_text().startswith("✨ AI YORUMU (ÖNERİDİR)") or \
            "AI yorumu (öneridir)" in blk.query_selector(".aic-ai-h").text_content()
        assert blk.query_selector_all(".aic-chip"), "evidence chips"
        assert page.eval_on_selector_all("#ai .aic .aic-facts", "els => els.map(e => e.innerText)") == facts_before
        n = ai_calls(srv)
        page.click("#ai .aic-draft")
        assert ai_calls(srv) == n, "opening the draft panel"
        page.click("#ai .ai-dgo")                                     # explicit
        page.wait_for_selector("#ai .ai-dmsg")
        assert ai_calls(srv) == n + 1
        text = page.input_value("#ai .ai-dmsg")
        assert text
        page.click("#ai .ai-dcopy")
        page.wait_for_selector("#ai .ai-dcopied")
        assert page.evaluate("navigator.clipboard.readText()") == text
        # copy-only: no send control or messaging link anywhere
        assert page.query_selector_all("#ai a[href]") == []
        labels = page.eval_on_selector_all("#ai button", "els => els.map(e => e.textContent)")
        assert not [x for x in labels if any(w in x.lower() for w in ("gönder", "send", "whatsapp'ı aç"))]
        html = page.content().lower()
        assert "wa.me" not in html and "mailto:" not in html and "web.whatsapp" not in html
        assert page.errors == []


# ---------------- security: hostile-looking AI text is displayed, never executed ----------------
def test_hostile_ai_text_is_displayed_as_text_not_executed(browser, tmp_path):
    """The grounding pipeline would reject such text, so the browser boundary is tested with intercepted
    responses (the real safety pipeline is unchanged)."""
    with app_server(tmp_path, "mock") as srv:
        page = open_page(browser)

        def hostile_enrich(route):
            body = route.fetch().json()
            for a in body["actions"]:
                a.update(interpretation=XSS, recommendation=XSS,
                         evidence=[{"fact_id": "F001", "label": XSS, "display_value": XSS}])
            route.fulfill(json=body)

        def hostile_draft(route):
            body = route.fetch().json()
            body.update(message=XSS, evidence=[{"fact_id": "F001", "label": "Müşteri", "display_value": XSS}])
            route.fulfill(json=body)

        page.route("**/api/agent/actions/enrich", hostile_enrich)
        page.route("**/api/agent/draft-message", hostile_draft)
        page.goto(srv["base"] + "/")
        open_ai_tab(page)
        scripts_before = page.evaluate("document.scripts.length")
        page.click("#ai .ai-gen")
        page.wait_for_selector("#ai .aic-ai")
        page.click("#ai .aic-draft")
        page.click("#ai .ai-dgo")
        page.wait_for_selector("#ai .ai-dmsg")
        assert page.evaluate("window.__M5_XSS === undefined")
        assert page.evaluate("document.scripts.length") == scripts_before
        assert page.query_selector_all("#ai .ai-list script, #ai .ai-list img") == []
        assert page.text_content("#ai .aic .aic-int") == XSS, "literal text is visible"
        assert page.input_value("#ai .ai-dmsg") == XSS
        assert page.errors == []


# ---------------- action state: real browser round trip with reloads ----------------
def test_action_state_round_trip_survives_reload(browser, tmp_path):
    with app_server(tmp_path, "disabled") as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        open_ai_tab(page)
        ids = card_ids(page)
        a, b = ids[0], ids[1]
        with page.expect_response(lambda r: r.url.endswith("/complete") and r.request.method == "POST") as resp:
            page.click(f'#ai .aic[data-id="{a}"] .aic-done')
        assert resp.value.ok and resp.value.request.headers.get("x-stockagent") == "1"
        page.wait_for_selector(f'#ai .aic[data-id="{a}"]', state="detached")
        page.reload()
        open_ai_tab(page)
        assert a not in card_ids(page), "still hidden after a full reload"
        r = page.request.post(f"{srv['base']}/api/actions/{a}/reopen", headers=H)   # supported reopen API
        assert r.ok and r.json()["status"] == "open"
        page.reload()
        open_ai_tab(page)
        assert card_ids(page) == ids, "open again, in Python order"
        page.click(f'#ai .aic[data-id="{b}"] .aic-hide')                         # the in-page undo workflow
        page.wait_for_selector(f'#ai .aic[data-id="{b}"]', state="detached")
        page.click("#ai .ai-undo-b")
        page.wait_for_selector(f'#ai .aic[data-id="{b}"]')
        page.reload()
        open_ai_tab(page)
        assert card_ids(page) == ids, "undo persisted across reload"
        assert page.errors == [] and ai_calls(srv) == 0


# ---------------- refresh smoke ----------------
def test_refresh_button_runs_the_guarded_refresh_and_reloads(browser, tmp_path):
    with app_server(tmp_path, "disabled") as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        open_ai_tab(page)
        assert page.is_visible("#ai .ai-refresh")
        page.evaluate("""() => { window.__rs = []; const el = document.querySelector('#ai .ai-rstat');
            new MutationObserver(() => window.__rs.push(el.className + '|' + el.textContent))
              .observe(el, {childList: true, characterData: true, subtree: true, attributes: true}); }""")
        with page.expect_request(lambda r: r.url.endswith("/api/context/refresh") and r.method == "POST") as req:
            page.click("#ai .ai-refresh")
        assert req.value.headers.get("x-stockagent") == "1"
        page.wait_for_selector("#ai .ai-rstat.ok")
        seen = page.evaluate("window.__rs")
        assert any("Veriler yenileniyor…" in s for s in seen), "pending state shown"
        assert page.is_disabled("#ai .ai-refresh")
        assert "Veriler yenilendi. Yeni veri tarihi: 30 Haziran 2026." in page.inner_text("#ai .ai-rstat")
        page.wait_for_function("window.__rs === undefined", timeout=15000)   # the page reloaded
        open_ai_tab(page)
        assert page.inner_text("#ai .ai-date") == "Veri: 30 Haziran 2026" and not page.is_visible("#ai .ai-warn")
        assert os.path.exists(str(tmp_path / "dashboard.prev.html"))
        assert page.errors == [] and ai_calls(srv) == 0


# ---------------- offline / file:// ----------------
def test_file_mode_static_dashboard_works_and_ai_tab_explains_offline(browser, tmp_path):
    data = SE.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    page = open_page(browser)
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto("file://" + dash)
    page.click('.tab[data-t="reorder"]')
    assert page.query_selector_all("#reorder tbody tr"), "static tables work without a server"
    page.click('.tab[data-t="ai"]')
    page.wait_for_selector("#ai .ai-state.off")
    assert page.inner_text("#ai .ai-state").startswith("Sunucu çalışmıyor.")
    assert not page.is_visible("#ai .ai-refresh") and not page.is_visible("#ai .ai-gen")
    page.click('.tab[data-t="overview"]')
    page.click('.tab[data-t="ai"]')
    assert [u for u in requests if not u.startswith("file://")] == [], "no requests at all from file://"
    assert page.errors == []


# ---------------- responsive: phone and desktop ----------------
LONG = "Uzunkelimeuzunkelime" * 25


def _boxes_overlap(a, b):
    return not (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


@pytest.mark.parametrize("viewport", [{"width": 390, "height": 844}, {"width": 375, "height": 812},
                                      {"width": 1280, "height": 900}])
def test_action_center_layout(browser, tmp_path, viewport):
    with app_server(tmp_path, "mock") as srv:
        page = open_page(browser, viewport=viewport)
        mobile = viewport["width"] < 600

        def long_enrich(route):
            body = route.fetch().json()
            for a in body["actions"]:
                a.update(interpretation=LONG, recommendation=LONG)
            route.fulfill(json=body)
        page.route("**/api/agent/actions/enrich", long_enrich)
        page.goto(srv["base"] + "/")
        open_ai_tab(page)
        assert no_horizontal_overflow(page)
        assert page.is_visible("#ai .ai-refresh")
        page.click("#ai .ai-gen")
        page.wait_for_selector("#ai .aic-ai")
        assert no_horizontal_overflow(page), "long AI text wraps"
        for c in page.query_selector_all("#ai .aic")[:3]:
            btns = [b.bounding_box() for b in c.query_selector_all(".aic-acts button")]
            assert all(b["height"] >= (34 if mobile else 20) for b in btns), "tappable"
            assert all(b["x"] >= 0 and b["x"] + b["width"] <= viewport["width"] for b in btns)
            assert not any(_boxes_overlap(x, y) for i, x in enumerate(btns) for y in btns[i + 1:])
        page.click("#ai .aic-draft")
        page.click("#ai .ai-dgo")
        page.wait_for_selector("#ai .ai-dmsg")
        assert no_horizontal_overflow(page)
        ta = page.query_selector("#ai .ai-dmsg").bounding_box()
        assert ta["width"] >= (250 if mobile else 500)
        page.click("#ai .aic-cust")
        page.wait_for_selector("#ai .ai-dr-row")
        dr = page.query_selector("#ai .ai-drawer").bounding_box()
        assert dr["x"] >= 0 and dr["x"] + dr["width"] <= viewport["width"] + 0.5 and dr["height"] <= viewport["height"] + 0.5
        assert (dr["width"] == viewport["width"]) if mobile else (370 <= dr["width"] <= 390)
        assert page.get_attribute("#ai .ai-dr-x", "aria-label") == "Kapat"
        assert page.evaluate("document.activeElement.classList.contains('ai-dr-x')"), "focus moves into the drawer"
        page.keyboard.press("Escape")
        assert not page.is_visible("#ai .ai-drawer")
        assert page.evaluate("document.activeElement.classList.contains('aic-cust')"), "focus returns to the opener"
        page.click("#ai .aic-done")
        page.wait_for_selector("#ai .ai-undo:not([hidden])")
        u = page.query_selector("#ai .ai-undo").bounding_box()
        assert u["x"] >= 0 and u["x"] + u["width"] <= viewport["width"] and u["y"] + u["height"] <= viewport["height"]
        ub = page.query_selector("#ai .ai-undo-b").bounding_box()
        assert ub["height"] < 50, "'Geri al' stays on one line"
        assert no_horizontal_overflow(page)
        page.screenshot(path=str(tmp_path / "layout.png"), full_page=False)   # synthetic data, temp folder only
        assert page.errors == []


def test_controls_have_accessible_names(browser, tmp_path):
    with app_server(tmp_path, "mock") as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        open_ai_tab(page)
        page.click("#ai .aic-draft")
        page.click("#ai .aic-defer")
        unnamed = page.evaluate("""() => [...document.querySelectorAll('#ai button, #ai input, #ai select, #ai textarea')]
            .filter(e => e.offsetParent !== null)
            .filter(e => !(e.getAttribute('aria-label') || e.textContent.trim() || (e.labels && e.labels.length)))
            .map(e => e.className)""")
        assert unnamed == []
        roles = page.eval_on_selector_all("#ai [role=status], #ai [aria-live]", "els => els.map(e => e.className)")
        for cls in ("ai-state", "ai-status", "ai-rstat", "ai-undo"):
            assert any(r.startswith(cls) for r in roles), cls
        assert page.get_attribute("#ai .ai-drawer", "role") == "dialog"
