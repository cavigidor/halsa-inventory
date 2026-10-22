"""M7-2: Playwright scenarios for the Günlük Satış tab in real Chromium against the real FastAPI app.

Each test starts its own server (free port, temporary folder with SYNTHETIC queue workbooks, temporary dashboard and
SQLite DB, AI_PROVIDER=disabled, no .env) and waits until the context and the Customer 360 snapshot are ready.
No real data, no key, no network, no paid call. Scenario numbers follow the M7-2 instruction (§64).

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
from tests import browser_auth as BA  # M8-CP1: real owner login for owner actions
from tests.fixtures import queue_synth as Q

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = os.environ.get("REQUIRE_BROWSER_TESTS") == "1"
Q1, Q2, Q3, Q7, Q8, Q9 = Q.Q[1], Q.Q[2], Q.Q[3], Q.Q[7], Q.Q[8], Q.Q[9]


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
    req = urllib.request.Request(url, headers=BA.probe_headers(url))       # M8-CP1: owner probes once registered
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


@contextlib.contextmanager
def queue_server(tmp_path):
    data = Q.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": "disabled", "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": data, "DASHBOARD_PATH": dash,
           "AGENT_DB": str(tmp_path / "state.db"), **BA.server_env()}
    env.pop("HEDEF_PATH", None)
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


def ai_calls(srv):
    return len(_get(srv["base"] + "/api/ai/calls")["calls"])


def open_page(browser, viewport=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    BA.authorize(ctx)
    page = ctx.new_page()
    page.errors, page.requests = [], []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("request", lambda r: page.requests.append((r.method, r.url)))
    return page


def open_tab(page, base):
    page.goto(base + "/")
    page.click('.tab[data-t="sq"]')
    page.wait_for_selector("#sq .sq-card")


def codes(page):
    return page.eval_on_selector_all("#sq .sq-card", "els => els.map(e => e.dataset.code)")


def card(code):
    return f'#sq .sq-card[data-code="{code}"]'


def open_reasons(page, code):
    page.click(f"{card(code)} .sq-toggle")
    page.wait_for_selector(f"{card(code)} .sq-reasons")


def wait_codes(page, fn, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn(codes(page)):
            return codes(page)
        page.wait_for_timeout(50)
    raise AssertionError(f"cards did not settle: {codes(page)}")


def no_horizontal_overflow(page):
    w, iw = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
    return w <= iw


# ---------------------------------------------------------------------------------------------
# 1–5: tab, cards, grouping, collection-first, filters
# ---------------------------------------------------------------------------------------------
def test_tab_cards_grouping_collection_first_filters(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        # (1) tab exists, before Müşteri 360; nothing fetched before it is opened
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[-7:] == ["sq", "c360", "opp", "fair", "crm", "own", "ai"] and page.inner_text('.tab[data-t="sq"]') == "Günlük Satış"
        assert not any("/api/sales-queue" in u for _, u in page.requests)
        page.click('.tab[data-t="sq"]')
        page.wait_for_selector("#sq .sq-card")
        assert page.text_content("#sq h2.sec") == "Bugün kimi aramalıyım?"
        # (2) cards load in Python order (never re-sorted)
        api = _get(srv["base"] + "/api/sales-queue")
        assert codes(page) == [k["customer_code"] for k in api["items"]] == [Q3, Q1, Q9, Q2, Q7, Q8]
        # (3) one customer, once, several reasons
        assert codes(page).count(Q1) == 1
        chips = page.eval_on_selector_all(f"{card(Q1)} .sq-chip", "els => els.map(e => e.textContent)")
        assert chips == ["Tahsilat ×2", "Reel Satış Düşüşü"]
        assert page.inner_text(f"{card(Q1)} .sq-toggle") == "Gerekçeler (3)"
        open_reasons(page, Q1)
        cats = page.eval_on_selector_all(f"{card(Q1)} .sq-r", "els => els.map(e => e.dataset.cat)")
        assert cats[0] == "collections" and sorted(cats) == ["collections", "collections", "real_decline"]
        assert "Temsilci:" not in page.inner_text(card(Q1))                     # legacy cached rep never shown
        # (4) collection-first warning; KAPALI badge; duplicate master badge
        assert page.query_selector(f"{card(Q1)} .sq-cf") is not None
        assert "Önce tahsilat" in page.inner_text(f"{card(Q1)} .sq-cf")
        assert page.query_selector(f"{card(Q3)} .sq-cf") is not None
        assert page.query_selector(f"{card(Q2)} .sq-cf") is None
        assert page.inner_text(f"{card(Q3)} .sq-kapali") == "KAPALI"
        assert "çift ana kayıt" in page.inner_text(f"{card(Q7)} .sq-top")
        assert page.inner_text(f"{card(Q1)} .sq-pri") in ("ORTA", "YÜKSEK", "KRİTİK", "DÜŞÜK")
        # (5) filters (server-side)
        labels = page.eval_on_selector_all("#sq .sq-f", "els => els.map(e => e.textContent)")
        assert labels == ["Tümü (6)", "Tahsilat önce (2)", "Tahsilat (2)", "Geri Kazanım (3)",
                          "Reel Satış Düşüşü (2)", "Çapraz Satış (0)",
                          "Fuar Takibi (0)", "Takip Zamanı (0)"]           # M7-3 filter
        page.click('#sq .sq-f[data-k="collection_first"]')
        assert wait_codes(page, lambda c: c == [Q3, Q1]) == [Q3, Q1]
        page.click('#sq .sq-f[data-k="winback"]')
        assert wait_codes(page, lambda c: set(c) == {Q2, Q7, Q8})
        page.click('#sq .sq-f[data-k="real_decline"]')
        assert wait_codes(page, lambda c: c == [Q1, Q9])
        page.click('#sq .sq-f[data-k="all"]')
        wait_codes(page, lambda c: len(c) == 6)
        assert any("filter=winback" in u for _, u in page.requests)
        assert ai_calls(srv) == 0 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 6–10: complete, card remains, defer, dismiss, undo
# ---------------------------------------------------------------------------------------------
def test_reason_state_complete_defer_dismiss_undo(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser)
        page.add_init_script("window.__SQ_UNDO_MS = 20000;")
        open_tab(page, srv["base"])
        # (6) complete Q1's primary collection reason → (7) the card remains with its other reasons
        open_reasons(page, Q1)
        first = page.get_attribute(f"{card(Q1)} .sq-r", "data-id")
        page.click(f'{card(Q1)} .sq-r[data-id="{first}"] .sq-done')
        page.wait_for_selector("#sq .sq-undo:not([hidden])")
        page.wait_for_function(f"""() => {{const c=document.querySelector('{card(Q1)} .sq-toggle');
                                   return c && c.textContent === 'Gerekçeler (2)';}}""")
        assert Q1 in codes(page)
        api = _get(srv["base"] + "/api/sales-queue")
        q1 = next(k for k in api["items"] if k["customer_code"] == Q1)
        assert first not in [r["action_id"] for r in q1["reasons"]] and q1["collection_first"] is True
        assert "tamamlandı" in page.inner_text("#sq .sq-undo")
        # (10) undo → reopen brings it back
        page.click("#sq .sq-undo-b")
        page.wait_for_function(f"""() => {{const c=document.querySelector('{card(Q1)} .sq-toggle');
                                   return c && c.textContent === 'Gerekçeler (3)';}}""")
        # (8) defer Q2's only reason → the card leaves the queue
        open_reasons(page, Q2)
        page.click(f"{card(Q2)} .sq-defer")
        page.wait_for_selector(f"{card(Q2)} .sq-deferf input[type=date]")
        page.click(f"{card(Q2)} .sq-defer-ok")
        wait_codes(page, lambda c: Q2 not in c)
        assert "ertelendi" in page.inner_text("#sq .sq-undo")
        # (9) dismiss Q8 → gone; undo → back
        open_reasons(page, Q8)
        page.click(f"{card(Q8)} .sq-hide")
        wait_codes(page, lambda c: Q8 not in c)
        assert "gizlendi" in page.inner_text("#sq .sq-undo")
        page.click("#sq .sq-undo-b")
        wait_codes(page, lambda c: Q8 in c)
        # state is shared with M5 (same SQLite rows, same ids)
        posts = [u for m, u in page.requests if m == "POST"]
        assert all("/api/actions/" in u for u in posts)
        assert any(u.endswith("/complete") for u in posts) and any(u.endswith("/defer") for u in posts)
        assert any(u.endswith("/dismiss") for u in posts) and any(u.endswith("/reopen") for u in posts)
        assert ai_calls(srv) == 0 and page.errors == []


def test_state_failure_shows_error_and_keeps_card(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser)
        open_tab(page, srv["base"])
        page.route("**/api/actions/**", lambda r: r.fulfill(status=500, body="{}"))
        open_reasons(page, Q2)
        page.click(f"{card(Q2)} .sq-done")
        page.wait_for_selector(f"{card(Q2)} .sq-err")
        assert page.inner_text(f"{card(Q2)} .sq-err") == "Durum kaydedilemedi."
        assert Q2 in codes(page) and page.is_hidden("#sq .sq-undo")


def test_building_state_polls_without_second_build(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser)
        page.add_init_script("window.__SQ_POLL_MS = 100;")
        seen = {"n": 0}

        def handle(route):
            seen["n"] += 1
            if seen["n"] <= 2:
                route.fulfill(status=503, content_type="application/json",
                              body=json.dumps({"detail": {"state": "building", "message": "x"}}))
            else:
                route.continue_()
        page.route("**/api/sales-queue*", handle)
        page.goto(srv["base"] + "/")
        page.click('.tab[data-t="sq"]')
        page.wait_for_selector("#sq .sq-state.wait")
        assert page.inner_text("#sq .sq-state") == "Müşteri verisi hazırlanıyor…"
        page.wait_for_selector("#sq .sq-card")
        assert seen["n"] >= 3 and page.is_hidden("#sq .sq-state")
        page.click('.tab[data-t="overview"]')
        page.click('.tab[data-t="sq"]')                                       # reopening: no new load cycle
        assert _get(srv["base"] + "/api/customer360/status")["state"] == "ready" and page.errors == []


# ---------------------------------------------------------------------------------------------
# 11: Müşteri 360 deep link
# ---------------------------------------------------------------------------------------------
def test_customer360_button_opens_exact_customer(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser)
        open_tab(page, srv["base"])
        page.click(f"{card(Q9)} .sq-c360")
        page.wait_for_selector("#c360 .c3-code")
        page.wait_for_function(f"() => (document.querySelector('#c360 .c3-code') || {{}}).textContent === '{Q9}'")
        assert page.is_visible("#c360") and not page.is_visible("#sq")
        assert "on" in page.get_attribute('.tab[data-t="c360"]', "class")
        assert any(u.endswith("/api/customer360/" + Q9) for _, u in page.requests)
        # another card → that exact customer
        page.click('.tab[data-t="sq"]')
        page.click(f"{card(Q3)} .sq-c360")
        page.wait_for_function(f"() => (document.querySelector('#c360 .c3-code') || {{}}).textContent === '{Q3}'")
        assert "KAPALI" in page.inner_text("#c360 .c3-name")
        assert ai_calls(srv) == 0 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 12–13: message draft is explicit only; no AI call before it
# ---------------------------------------------------------------------------------------------
def test_draft_explicit_only_no_ai_before(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser)
        open_tab(page, srv["base"])
        open_reasons(page, Q1)
        page.click(f"{card(Q1)} .sq-c360")
        page.click('.tab[data-t="sq"]')
        assert not any("/api/agent/" in u for _, u in page.requests)          # (13) nothing before the click
        assert ai_calls(srv) == 0
        # collection-first → default purpose collection; target is the collection account
        page.click(f"{card(Q1)} .sq-drafto")
        page.wait_for_selector(f"{card(Q1)} .sq-draft")
        assert page.eval_on_selector(f"{card(Q1)} .sq-draft select", "s => s.value") == "collection"
        assert not any("/api/agent/" in u for _, u in page.requests)          # opening the panel ≠ a request
        with page.expect_request("**/api/agent/draft-message") as req:
            page.click(f"{card(Q1)} .sq-dgo")                                  # (12) explicit click only
        body = json.loads(req.value.post_data)
        assert body["purpose"] == "collection" and body["customer_code"] in (Q1, Q.Q1_SECOND)
        assert req.value.headers.get("x-stockagent") == "1"
        page.wait_for_selector(f"{card(Q1)} .sq-draft .sq-err")
        assert "AI kapalı" in page.inner_text(f"{card(Q1)} .sq-draft .sq-err")
        # KAPALI → collection only
        page.click(f"{card(Q3)} .sq-drafto")
        opts = page.eval_on_selector_all(f"{card(Q3)} .sq-draft select:first-of-type option",
                                         "els => els.map(e => e.value)")
        assert opts == ["collection"]
        assert "yalnızca tahsilat" in page.inner_text(f"{card(Q3)} .sq-draft")
        # winback → winback; decline → sales follow-up
        page.click(f"{card(Q2)} .sq-drafto")
        assert page.eval_on_selector(f"{card(Q2)} .sq-draft select", "s => s.value") == "winback"
        page.click(f"{card(Q9)} .sq-drafto")
        assert page.eval_on_selector(f"{card(Q9)} .sq-draft select", "s => s.value") == "sales_followup"
        drafts = [u for _, u in page.requests if "/api/agent/" in u]
        assert len(drafts) == 1 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 14: file:// mode
# ---------------------------------------------------------------------------------------------
def test_file_mode_requires_server(browser, tmp_path):
    data = Q.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    page = open_page(browser)
    page.goto("file://" + dash)
    page.click('.tab[data-t="reorder"]')
    assert page.query_selector("#reorder table") is not None                 # static modules still work
    page.click('.tab[data-t="sq"]')
    page.wait_for_selector("#sq .sq-state:not([hidden])")
    assert page.inner_text("#sq .sq-state") == "Günlük Satış için panoyu http://127.0.0.1:8000/ üzerinden açın."
    assert page.query_selector("#sq .sq-card") is None
    assert [u for _, u in page.requests if not u.startswith("file://")] == [] and page.errors == []


# ---------------------------------------------------------------------------------------------
# 15–16: phone width, hostile text
# ---------------------------------------------------------------------------------------------
def test_phone_width(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser, viewport={"width": 390, "height": 844})
        open_tab(page, srv["base"])
        assert no_horizontal_overflow(page)
        for code in (Q1, Q8):
            open_reasons(page, code)
            page.click(f"{card(code)} .sq-drafto")
        page.click(f"{card(Q1)} .sq-r .sq-defer")
        assert no_horizontal_overflow(page)
        box = page.eval_on_selector(f"{card(Q1)} .sq-score", "e => [e.getClientRects().length]")
        assert box == [1]                                                     # the score never splits
        for sel in (".sq-toggle", ".sq-c360", ".sq-drafto"):
            h = page.eval_on_selector(f"{card(Q1)} {sel}", "e => e.getBoundingClientRect().height")
            assert h >= 36                                                    # usable touch targets
        page.mouse.wheel(0, 4000)
        assert no_horizontal_overflow(page) and page.errors == [] and ai_calls(srv) == 0


def test_hostile_text_executes_nothing(browser, tmp_path):
    with queue_server(tmp_path) as srv:
        page = open_page(browser)
        open_tab(page, srv["base"])
        assert "<img" in page.inner_text(f"{card(Q8)} .sq-name")              # shown as text
        open_reasons(page, Q8)
        page.click(f"{card(Q8)} .sq-drafto")
        page.click(f"{card(Q8)} .sq-c360")
        page.wait_for_selector("#c360 .c3-name")
        assert page.evaluate("window.__SQ_XSS") is None and page.errors == []
        assert page.query_selector("#sq img") is None and page.query_selector("#sq .sq-list script") is None
        assert page.query_selector("#sq i") is None
        assert ai_calls(srv) == 0
        log = open(srv["log"], encoding="utf-8").read()
        assert "Sentetik Kuyruk" not in log and "Tehlikeli" not in log           # names never logged
