"""M7-5: Playwright scenarios for the Takip / CRM tab (and its links into Müşteri 360 and Günlük Satış) in real
Chromium against the real FastAPI app.

Each test starts its own server (free port, temporary folder with SYNTHETIC workbooks, temporary dashboard and SQLite
DB, AI_PROVIDER=disabled, no .env). No real data, no key, no network. Scenario numbers follow §90 (1–18).
Skipped without Playwright unless REQUIRE_BROWSER_TESTS=1.
"""
import datetime as dt
import json

import pytest

from tests.fixtures import fair_synth as FX
from tests.test_m7_fair_browser import (_get, _post, ai_calls, browser, fair_server, no_horizontal_overflow,  # noqa: F401
                                        open_page)
from services import refresh as RF

P = FX.P
TODAY = dt.date.today()


def d(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


XSS_OUT = '<img src=x onerror="window.__CRM_XSS=1">Sonuç'
XSS_NEXT = '<script>window.__CRM_XSS=2</script>iş'
XSS_NOTE = '<b onmouseover="window.__CRM_XSS=3">not</b>' + "uzunkelime" * 12


def open_crm(page, base):
    page.goto(base + "/")
    page.click('.tab[data-t="crm"]')
    page.wait_for_selector('#crm [data-sec="summary"]:not([hidden]) .cr-tile')
    page.wait_for_selector('#crm [data-sec="recent"]:not([hidden])')


def pick_customer(page, query, code):
    page.fill("#crm .cr-q", query)
    page.wait_for_selector(f'#crm .cr-hit[data-code="{code}"]')
    page.click(f'#crm .cr-hit[data-code="{code}"]')
    page.wait_for_selector(f'#crm .cr-panel[data-code="{code}"] .ci-save')


def tile(page, key):
    return page.inner_text(f'#crm .cr-tile[data-k="{key}"] .n')


def crm_requests(page):
    return [(m, u) for m, u, _ in page.requests if "/api/" in u]


# ---------------------------------------------------------------------------------------------
# 1–9, 18: tab, coverage, empty state, search, phone interaction, outcome, follow-up, due, upcoming, zero AI
# ---------------------------------------------------------------------------------------------
def test_crm_workflow(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[-6:] == ["sq", "c360", "opp", "fair", "crm", "ai"]                          # (1)
        assert page.inner_text('.tab[data-t="crm"]') == "Takip / CRM"
        assert not any("/api/crm" in u for _, u in crm_requests(page))                         # lazy
        open_crm(page, srv["base"])
        cov = page.inner_text("#crm .cr-cov")                                                   # (2)
        assert "Eksik kayıt olabilir" in cov and "çalışan performansı karşılaştırmak için kullanılmaz" in cov
        assert "Henüz yapılandırılmış CRM kaydı yok" in page.inner_text("#crm .cr-recent")      # (3)
        assert "Kayıt olmaması temas olmadığı anlamına gelmez" in page.inner_text("#crm .cr-recent")
        assert "Zamanı gelen takip yok" in page.inner_text("#crm .cr-due")
        assert tile(page, "customers_with_structured_contact") == "0" and tile(page, "total_customers") == "20"
        pick_customer(page, "Sentetik Fuar 17", P[17])                                          # (4)
        assert page.inner_text('#crm .cr-panel[data-code="%s"] h4' % P[17]) in (P[17], FX.name(17))
        assert page.get_attribute("#crm .cr-fu", "data-state") == "none"
        assert page.input_value("#crm .ci-date") == TODAY.isoformat()
        page.select_option("#crm .ci-channel", "phone")                                         # (5)
        page.select_option("#crm .ci-purpose", "sales_followup")
        page.fill("#crm .ci-outcome", "Görüşüldü, fiyat bekliyor")                              # (6)
        page.fill("#crm .ci-next", "Fiyat listesi gönder")
        page.fill("#crm .ci-note", "Sentetik kısa not")
        page.click("#crm .ci-save")
        page.wait_for_selector("#crm .cr-timeline li[data-id]")
        tl = page.inner_text("#crm .cr-timeline")
        assert "Telefon" in tl and "Sonuç: Görüşüldü, fiyat bekliyor" in tl and "Not: Sentetik kısa not" in tl
        assert page.get_attribute("#crm .cr-fu", "data-state") == "none"                       # nothing auto-planned
        page.wait_for_function("() => document.querySelector('#crm .cr-tile[data-k=\"customers_with_structured_contact\"] .n').textContent === '1'")
        page.wait_for_selector(f'#crm .cr-recent li[data-code="{P[17]}"]')
        assert "Sentetik kısa not" not in page.inner_text("#crm .cr-recent")                    # notes never in feed
        page.fill("#crm .cf-date", d(-1))                                                       # (7)
        page.select_option("#crm .cf-purpose", "sales_followup")
        page.fill("#crm .cf-next", "Fiyat listesi gönder")
        page.click("#crm .cf-save")
        page.wait_for_selector('#crm .cr-fu[data-state="overdue"]')
        page.wait_for_selector(f'#crm .cr-due .cr-card[data-code="{P[17]}"][data-due="overdue"]')   # (8)
        assert "Sonraki iş: Fiyat listesi gönder" in page.inner_text(f'#crm .cr-due .cr-card[data-code="{P[17]}"]')
        # (9) a second customer, scheduled in the same entry as its interaction, lands in "upcoming"
        pick_customer(page, "Sentetik Fuar 18", P[18])
        page.select_option("#crm .ci-channel", "visit")
        page.check("#crm .ci-setfu")
        page.fill("#crm .ci-fudate", d(3))
        page.select_option("#crm .ci-fupurpose", "service")
        page.click("#crm .ci-save")
        page.wait_for_selector(f'#crm .cr-up .cr-card[data-code="{P[18]}"][data-due="upcoming"]')
        assert page.query_selector(f'#crm .cr-due .cr-card[data-code="{P[18]}"]') is None
        page.wait_for_function("() => document.querySelector('#crm .cr-tile[data-k=\"current_followups\"] .n').textContent === '2'")
        # a future contact date is refused with a plain message
        page.fill("#crm .ci-date", d(2))
        page.evaluate("document.querySelector('#crm .ci-date').removeAttribute('max')")
        page.click("#crm .ci-save")
        page.wait_for_selector("#crm .ci-err:not([hidden])")
        assert "bugünden sonra olamaz" in page.inner_text("#crm .ci-err")
        s = _get(srv["base"] + "/api/crm/summary")
        assert s["structured_interactions"] == 2 and s["coverage_status"] == "incomplete"
        page.click("#crm .cr-clear")                                                            # clear P18
        page.wait_for_selector('#crm .cr-fu[data-state="none"]')
        page.wait_for_function(f"() => !document.querySelector('#crm .cr-up .cr-card[data-code=\"{P[18]}\"]')")
        assert ai_calls(srv) == 0 and page.errors == []                                         # (18)
        assert not any("/api/agent/" in u for _, u in crm_requests(page))
        writes = [p for m, u, p in page.requests if m in ("POST", "PUT") and "/api/crm/" in u]
        assert writes and all("ai" not in json.loads(p) for p in writes)


# ---------------------------------------------------------------------------------------------
# 10, 11, 18: Müşteri 360 card, Günlük Satış "Takip Zamanı" filter, explicit draft only
# ---------------------------------------------------------------------------------------------
def test_customer360_card_and_daily_sales(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = open_page(browser)
        page.goto(srv["base"] + "/")
        page.click('.tab[data-t="c360"]')
        page.evaluate(f"window.StockAgentC360.open({json.dumps(P[17])})")
        page.wait_for_selector('#c360 [data-sec="crm"] .c3-crmlog')
        card = page.inner_text('#c360 [data-sec="crm"]')
        assert "Eksik kayıt olabilir" in card and "kayıt yok (bilinmiyor)" in card
        page.click('#c360 [data-sec="crm"] .c3-crmlog')                                          # (10) jump to CRM tab
        page.wait_for_selector(f'#crm .cr-panel[data-code="{P[17]}"] .ci-save')
        assert page.is_visible("#crm")
        page.fill("#crm .ci-outcome", "Numune istendi")
        page.fill("#crm .ci-next", "Numune gönder")
        page.check("#crm .ci-setfu")
        page.fill("#crm .ci-fudate", d(0))
        page.select_option("#crm .ci-fupurpose", "sales_followup")
        page.click("#crm .ci-save")
        page.wait_for_selector('#crm .cr-fu[data-state="today"]')
        page.click('#crm .cr-panel .cr-c360')
        page.wait_for_function("() => (document.querySelector('#c360 [data-sec=\"crm\"]')||{}).textContent && "
                               "document.querySelector('#c360 [data-sec=\"crm\"]').textContent.includes('Numune istendi')")
        card = page.inner_text('#c360 [data-sec="crm"]')
        assert "Numune gönder" in card and "Satış takibi" in card
        # (11) Günlük Satış: the due follow-up appears under "Takip Zamanı"
        page.click('.tab[data-t="sq"]')
        page.wait_for_selector("#sq .sq-card")
        assert page.inner_text('#sq .sq-f[data-k="crm_followup"]').startswith("Takip Zamanı")
        page.click('#sq .sq-f[data-k="crm_followup"]')
        page.wait_for_selector(f'#sq .sq-card[data-code="{P[17]}"]')
        assert page.eval_on_selector_all("#sq .sq-card", "els => els.map(e => e.dataset.code)") == [P[17]]
        assert "Takip Zamanı" in page.inner_text(f'#sq .sq-card[data-code="{P[17]}"] .sq-chips')
        prim = page.inner_text(f'#sq .sq-card[data-code="{P[17]}"] .sq-primary')
        assert "Planlanan müşteri takibi" in prim
        assert not any("/api/agent/" in u for _, u in crm_requests(page))
        page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-drafto')
        assert page.eval_on_selector(f'#sq .sq-card[data-code="{P[17]}"] .sq-draft select',
                                     "s => s.value") == "sales_followup"
        with page.expect_request("**/api/agent/draft-message") as req:
            page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-dgo')
        assert set(json.loads(req.value.post_data)) == {"customer_code", "channel", "purpose"}   # no CRM text sent
        page.wait_for_selector(f'#sq .sq-card[data-code="{P[17]}"] .sq-draft .sq-err')
        # completing the queue task keeps the CRM follow-up
        page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-toggle')
        page.click(f'#sq .sq-card[data-code="{P[17]}"] .sq-r .sq-done')
        page.wait_for_function(f"() => !document.querySelector('#sq .sq-card[data-code=\"{P[17]}\"]')")
        assert _get(srv["base"] + f"/api/crm/customers/{P[17]}")["followup"]["due_date"] == d(0)
        # a service follow-up shows no draft button
        _post(srv["base"] + f"/api/crm/customers/{P[18]}/followup", {"purpose": "service", "due_date": d(0)},
              method="PUT")
        page.reload()
        page.click('.tab[data-t="sq"]')
        page.wait_for_selector('#sq .sq-f[data-k="crm_followup"]')
        page.click('#sq .sq-f[data-k="crm_followup"]')
        page.wait_for_selector(f'#sq .sq-card[data-code="{P[18]}"]')
        assert page.query_selector(f'#sq .sq-card[data-code="{P[18]}"] .sq-drafto') is None
        assert ai_calls(srv) == 0 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 12, 13, 14: KAPALI sales blocked, collection allowed, fair and CRM separate
# ---------------------------------------------------------------------------------------------
def test_kapali_and_fair_separation(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = open_page(browser)
        open_crm(page, srv["base"])
        pick_customer(page, "Sentetik Fuar 10", P[10])
        assert page.query_selector('#crm .cr-panel .cr-head .cr-kapali') is not None
        opts = page.eval_on_selector_all("#crm .cf-purpose option", "os => os.map(o => o.value)")
        assert opts == ["collection", "service"]                                                # (12)
        assert "yalnızca tahsilat veya hizmet" in page.inner_text("#crm .cr-fform")
        # a forged sales option is still refused by the server, with a plain message
        page.evaluate("(() => { const s = document.querySelector('#crm .cf-purpose'); const o = document.createElement('option');"
                      " o.value = 'sales_followup'; o.textContent = 'x'; s.append(o); s.value = 'sales_followup'; })()")
        page.fill("#crm .cf-date", d(0))
        page.click("#crm .cf-save")
        page.wait_for_selector("#crm .cf-err:not([hidden])")
        assert "KAPALI müşteri için yalnızca tahsilat veya hizmet" in page.inner_text("#crm .cf-err")
        assert _get(srv["base"] + f"/api/crm/customers/{P[10]}")["followup"] is None
        page.select_option("#crm .ci-purpose", "collection")                                    # history always OK
        page.fill("#crm .ci-outcome", "Ödeme planı konuşuldu")
        page.click("#crm .ci-save")
        page.wait_for_selector("#crm .cr-timeline li[data-id]")
        page.select_option("#crm .cf-purpose", "collection")                                    # (13)
        page.fill("#crm .cf-date", d(0))
        page.click("#crm .cf-save")
        page.wait_for_selector('#crm .cr-fu[data-state="today"]')
        page.wait_for_selector(f'#crm .cr-due .cr-card[data-code="{P[10]}"] .cr-kapali')
        q = _get(srv["base"] + "/api/sales-queue?filter=crm_followup")
        k = next(x for x in q["items"] if x["customer_code"] == P[10])
        assert k["draft"]["default_purpose"] == "collection" and k["reasons"][0]["kind"] == "operational"
        # (14) a fair task and a CRM follow-up stay separate records
        c = _post(srv["base"] + "/api/fairs/campaigns", {"name": "Sentetik Fuar Takip", "edition_year": 2027,
                                                          "start_date": "2027-01-15", "status": "active"})
        _post(srv["base"] + f"/api/fairs/campaigns/{c['campaign_id']}/customers", {"customer_codes": [P[17]]})
        _post(srv["base"] + f"/api/fairs/campaigns/{c['campaign_id']}/customers",
              {"customer_code": P[17], "stage": "called", "next_action_date": d(0), "note": "fuar notu"}, method="PUT")
        page.reload()
        open_crm(page, srv["base"])
        assert page.query_selector(f'#crm .cr-due .cr-card[data-code="{P[17]}"]') is None       # not copied
        pick_customer(page, "Sentetik Fuar 17", P[17])
        assert "fuar notu" not in page.inner_text("#crm .cr-panel")
        page.fill("#crm .cf-date", d(0))
        page.select_option("#crm .cf-purpose", "sales_followup")
        page.click("#crm .cf-save")
        page.wait_for_selector(f'#crm .cr-due .cr-card[data-code="{P[17]}"]')
        k = next(x for x in _get(srv["base"] + "/api/sales-queue")["items"] if x["customer_code"] == P[17])
        assert [r["category"] for r in k["reasons"]] == ["crm_followup", "fair_followup"]
        rows = _get(srv["base"] + f"/api/fairs/campaigns/{c['campaign_id']}/customers")["items"]
        assert rows[0]["stage"] == "called"
        assert ai_calls(srv) == 0 and page.errors == []


# ---------------------------------------------------------------------------------------------
# 15, 16: hostile text, phone width
# ---------------------------------------------------------------------------------------------
def test_hostile_text_and_phone_width(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = open_page(browser, viewport={"width": 390, "height": 844})
        page.add_init_script("document.addEventListener('DOMContentLoaded',()=>{const s=document.createElement('style');"
                             "s.textContent='*{letter-spacing:1.2px!important}';document.head.append(s);});")
        open_crm(page, srv["base"])
        pick_customer(page, "930.10.0012", P[12])                                               # hostile name
        page.fill("#crm .ci-outcome", XSS_OUT)
        page.fill("#crm .ci-next", XSS_NEXT)
        page.fill("#crm .ci-owner", "<i>etiket</i>")
        page.fill("#crm .ci-note", XSS_NOTE)
        page.check("#crm .ci-setfu")
        page.fill("#crm .ci-fudate", d(0))
        page.click("#crm .ci-save")
        page.wait_for_selector("#crm .cr-timeline li[data-id]")
        page.wait_for_selector(f'#crm .cr-due .cr-card[data-code="{P[12]}"]')
        page.wait_for_selector(f'#crm .cr-recent li[data-code="{P[12]}"]')
        assert "<img" in page.inner_text("#crm .cr-timeline") and "<script>" in page.inner_text("#crm .cr-due")
        assert "<i>etiket</i>" in page.inner_text("#crm .cr-panel")
        assert "<img" in page.inner_text("#crm .cr-recent")
        assert page.evaluate("window.__CRM_XSS") is None and page.evaluate("window.__FAIR_XSS") is None
        for bad in ("#crm img", "#crm script", "#crm .cr-timeline b[onmouseover]", "#crm i"):
            assert page.query_selector(bad) is None, bad
        # (16) phone width
        assert no_horizontal_overflow(page)
        for sel in ("#crm .cr-cov", "#crm .ci-save", "#crm .cf-save", "#crm .cr-tiles", f'#crm .cr-due .cr-card[data-code="{P[12]}"]',
                    "#crm .ci-note", "#crm .cr-timeline"):
            box = page.eval_on_selector(sel, "e => { const r = e.getBoundingClientRect(); return [r.left, r.right]; }")
            assert box[0] >= 0 and box[1] <= 390, sel
        assert page.eval_on_selector("#crm .ci-save", "e => e.getBoundingClientRect().height") >= 36
        page.mouse.wheel(0, 4000)
        assert no_horizontal_overflow(page) and page.errors == [] and ai_calls(srv) == 0
        log = open(srv["log"], encoding="utf-8").read()
        for s in ("Sonuç", "uzunkelime", "etiket", "window.__CRM_XSS"):
            assert s not in log


# ---------------------------------------------------------------------------------------------
# 17: file:// mode
# ---------------------------------------------------------------------------------------------
def test_file_mode_requires_server(browser, tmp_path):
    data = FX.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40)["ok"]
    page = open_page(browser)
    page.goto("file://" + dash)
    page.click('.tab[data-t="crm"]')
    page.wait_for_selector("#crm .cr-state:not([hidden])")
    assert page.inner_text("#crm .cr-state") == "Takip / CRM için panoyu http://127.0.0.1:8000/ üzerinden açın."
    for sec in ("summary", "due", "upcoming", "customer", "recent"):
        assert page.is_hidden(f'#crm [data-sec="{sec}"]')
    assert "Eksik kayıt olabilir" in page.inner_text("#crm .cr-cov")
    page.evaluate(f"window.StockAgentCRM.open({json.dumps(P[17])})")                            # still no request
    page.click('.tab[data-t="reorder"]')
    assert page.query_selector("#reorder table") is not None
    assert [u for _, u, _ in page.requests if not u.startswith("file://")] == [] and page.errors == []
