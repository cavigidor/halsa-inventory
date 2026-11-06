"""M8-CP1 / CP1B (D-060, D-061, D-074) in a real browser (Playwright + Chromium, real FastAPI server, synthetic data).

The father's experience (D-074): open the link → one Turkish site-password page → the dashboard, every tab navigable,
nothing changeable; the financial password once more for Yönetim. The owner's experience: log in once through /giris,
change data, log out. Logging out ends viewing too. The server uses the REAL logins (no harness).
"""
import pytest

from tests import browser_auth as BA
from tests.test_m7_crm_browser import P, open_crm, pick_customer
from tests.test_m7_fair_browser import _get, browser, fair_server  # noqa: F401  (fixture re-export)

TABS = ["sq", "c360", "opp", "fair", "crm", "own", "ai"]


def anon_page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})       # no cookie at all
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    return page


def viewer_page(browser):
    """D-074: a browser that already entered the shared website password (no owner / financial session)."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    BA.authorize_viewer(ctx)
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    return page


def _site_login(page, password):
    page.fill("#pw", password)
    page.click("#b")


def test_father_enters_the_site_password_once_and_navigates(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = anon_page(browser)
        r = page.goto(srv["base"] + "/")
        assert r.status == 401 and page.url.rstrip("/") == srv["base"]          # same address, password page
        assert "Site şifresi" in page.inner_text("main") and page.is_visible("#pw")
        assert "const DATA" not in page.content() and page.query_selector(".tab") is None   # no business data
        assert page.get_attribute('main a[href="/giris"]', "href") == "/giris"
        _site_login(page, "yanlis-parola-0000")
        page.wait_for_function("() => document.getElementById('m').textContent === 'Şifre hatalı.'")
        assert page.input_value("#pw") == ""
        _site_login(page, BA.TEST_VIEWER_PASSWORD)
        page.wait_for_selector('#halsa-access[data-mode="viewer"]')              # the same page, now the dashboard
        assert page.url.rstrip("/") == srv["base"]
        cookies = {c["name"]: c for c in page.context.cookies()}
        assert cookies["halsa_owner"]["httpOnly"] and cookies["halsa_owner"]["sameSite"] == "Strict"
        bar = page.inner_text("#halsa-access")
        assert "Görüntüleme (site girişi)" in bar and page.get_attribute("#halsa-access a", "href") == "/giris"
        page.reload()                                                           # entered once per session
        page.wait_for_selector('#halsa-access[data-mode="viewer"]')
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[-7:] == TABS
        assert page.is_hidden('.tab[data-t="up"]')                              # M8-CP3: owner-only upload tab
        for t in (t for t in tabs if t != "up"):                                # every other tab opens, read-only
            page.click(f'.tab[data-t="{t}"]')
            assert page.is_visible(f"#{t}")
        page.click('.tab[data-t="ai"]')
        page.wait_for_selector("#ai .aic")                                      # the action queue is readable
        page.click('.tab[data-t="crm"]')
        page.wait_for_selector('#crm [data-sec="summary"]:not([hidden]) .cr-tile')   # CRM is readable
        page.click('.tab[data-t="own"]')                                        # finances: password form (D-061)
        page.wait_for_selector("#own .ow-login:not([hidden])")
        assert page.is_hidden('#own [data-sec="kredi"]')
        assert page.errors == []


def test_a_bookmarked_section_opens_after_the_site_password(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = anon_page(browser)
        assert page.goto(srv["base"] + "/#yonetim").status == 401
        _site_login(page, BA.TEST_VIEWER_PASSWORD)
        page.wait_for_selector("#own.on .ow-login:not([hidden])")               # bookmark kept: Yönetim, locked
        assert page.url == srv["base"] + "/#yonetim" and page.is_hidden('#own [data-sec="kredi"]')
        assert page.errors == []


def test_logout_and_revocation_end_viewing(browser, tmp_path):
    import urllib.request
    with fair_server(tmp_path) as srv:
        page = viewer_page(browser)
        page.goto(srv["base"] + "/")
        page.wait_for_selector('#halsa-access[data-mode="viewer"]')
        page.click("#halsa-access button")                                      # Çıkış yap
        page.wait_for_selector("#pw")                                           # back to the site-password page
        assert "halsa_owner" not in {c["name"] for c in page.context.cookies()}
        assert page.query_selector(".tab") is None
        BA._viewer_sessions.pop(srv["base"], None)                              # that cookie is gone: a new login
        page2 = viewer_page(browser)                                            # another device, then revoked
        page2.goto(srv["base"] + "/")
        page2.wait_for_selector('#halsa-access[data-mode="viewer"]')
        req = urllib.request.Request(srv["base"] + "/api/auth/viewer-sessions/revoke", data=b"{}", method="POST",
                                     headers={**BA.headers(srv["base"]), "Content-Type": "application/json",
                                              "X-StockAgent": "1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
        page2.click('.tab[data-t="crm"]')                                        # the next data call is refused…
        page2.wait_for_selector("#pw", timeout=15000)                           # …and the page shows the password
        page2.reload()
        page2.wait_for_selector("#pw")
        assert page2.query_selector(".tab") is None


def test_father_cannot_change_anything_and_is_told_why(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = viewer_page(browser)
        page.goto(srv["base"] + "/")
        page.click('.tab[data-t="ai"]')
        page.wait_for_selector("#ai .aic")
        before = page.eval_on_selector_all("#ai .aic", "els => els.map(e => e.dataset.id)")
        page.click(f'#ai .aic[data-id="{before[0]}"] .aic-done')
        page.wait_for_selector("#halsa-access-msg:not([hidden])")
        msg = page.inner_text("#halsa-access-msg")
        assert "yalnızca yönetici girişiyle" in msg and page.get_attribute("#halsa-access-msg a", "href") == "/giris"
        page.wait_for_selector(f'#ai .aic[data-id="{before[0]}"] .aic-serr')
        page.reload()                                                           # nothing was saved on the server
        page.click('.tab[data-t="ai"]')
        page.wait_for_selector("#ai .aic")
        assert page.eval_on_selector_all("#ai .aic", "els => els.map(e => e.dataset.id)") == before
        open_crm(page, srv["base"])                                             # a CRM note cannot be saved either
        pick_customer(page, "Sentetik Fuar 17", P[17])
        page.select_option("#crm .ci-channel", "phone")
        page.select_option("#crm .ci-purpose", "sales_followup")
        page.fill("#crm .ci-outcome", "Görüşüldü")
        page.click("#crm .ci-save")
        page.wait_for_selector("#crm .ci-err:not([hidden])")
        assert "yönetici girişi" in page.inner_text("#crm .ci-err")
        assert page.query_selector("#crm .cr-timeline li[data-id]") is None
        assert _get(srv["base"] + "/api/crm/recent?limit=5")["items"] == []     # confirmed on the server
        assert page.errors == []


def test_owner_logs_in_changes_data_and_logs_out(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = anon_page(browser)                                               # the owner needs no site password
        page.goto(srv["base"] + "/giris")
        page.fill("#pw", "yanlis-parola-0000")
        page.click("#b")
        page.wait_for_function("() => document.getElementById('m').textContent === 'Şifre hatalı.'")
        assert page.input_value("#pw") == ""                                    # the field is cleared
        page.fill("#pw", BA.TEST_PASSWORD)
        page.click("#b")
        page.wait_for_url(srv["base"] + "/")
        page.wait_for_selector('#halsa-access[data-mode="owner"]')
        cookies = {c["name"]: c for c in page.context.cookies()}
        c = cookies["halsa_owner"]
        assert c["httpOnly"] and c["sameSite"] == "Strict"
        assert page.evaluate("document.cookie").find("halsa_owner") == -1       # not readable by page scripts
        page.click('.tab[data-t="ai"]')
        page.wait_for_selector("#ai .aic")
        target = page.eval_on_selector("#ai .aic", "e => e.dataset.id")
        page.click(f'#ai .aic[data-id="{target}"] .aic-done')
        page.wait_for_selector(f'#ai .aic[data-id="{target}"]', state="detached")  # saved
        page.click('.tab[data-t="own"]')
        page.wait_for_selector("#own .ow-bar:not([hidden])")                    # Yönetim opens for the owner
        page.click("#halsa-access button")                                      # Çıkış yap
        page.wait_for_selector("#pw")                                           # D-074: logged out = nothing visible
        assert "halsa_owner" not in {c["name"] for c in page.context.cookies()}
        assert page.errors == []


# ---------------- D-061: financial viewer (father) — password once, read-only Yönetim ----------------
def test_father_unlocks_yonetim_with_the_financial_password_read_only(browser, tmp_path):
    from tests.test_m7_owner_browser import all_owner, server
    import os
    owner_files = all_owner(tmp_path)
    with server(tmp_path, owner=owner_files) as srv:
        page = viewer_page(browser)
        page.goto(srv["base"] + "/#yonetim")                                   # direct link to the financial section
        page.wait_for_selector("#own.on .ow-login:not([hidden])")
        assert "finans görüntüleme şifresini" in page.inner_text("#own .ow-login")
        assert page.is_hidden('#own [data-sec="kredi"]') and page.is_hidden('#own [data-sec="system"]')
        page.fill("#own .ow-pw", "yanlis-parola-0000")
        page.click("#own .ow-unlock")
        page.wait_for_function("() => document.querySelector('#own .ow-loginmsg').textContent === 'Şifre hatalı.'")
        assert page.input_value("#own .ow-pw") == ""
        page.fill("#own .ow-pw", BA.TEST_FINANCE_PASSWORD)
        page.click("#own .ow-unlock")
        for sec in ("kredi", "gg", "butce"):                                    # financial sections only
            page.wait_for_selector(f'#own [data-sec="{sec}"][data-state="ready"]')
        assert page.get_attribute("#own", "data-access") == "finance"
        assert page.is_hidden('#own [data-sec="ops"]') and page.is_hidden('#own [data-sec="system"]')
        assert page.is_hidden("#own .ow-bar"), "no reload control for a financial viewer"
        assert page.is_hidden("#own .ow-login")
        shown = page.inner_text("#own")
        for f in owner_files.values():                                          # no source file names
            assert os.path.basename(str(f)) not in shown
        assert "Kaynak:" not in shown
        page.wait_for_selector('#halsa-access[data-mode="finance"]')
        assert "Finans görüntüleme açık" in page.inner_text("#halsa-access")
        page.reload()                                                           # entered once per session
        page.click('.tab[data-t="own"]')
        page.wait_for_selector('#own [data-sec="kredi"][data-state="ready"]')
        assert page.is_hidden("#own .ow-login")
        page.click('.tab[data-t="ai"]')                                         # still read-only everywhere else
        page.wait_for_selector("#ai .aic")
        before = page.eval_on_selector_all("#ai .aic", "els => els.map(e => e.dataset.id)")
        page.click(f'#ai .aic[data-id="{before[0]}"] .aic-done')
        page.wait_for_selector("#halsa-access-msg:not([hidden])")
        assert "Finans görüntüleme şifresi yalnızca finans bölümlerini" in page.inner_text("#halsa-access-msg")
        page.reload()
        page.click('.tab[data-t="ai"]')
        page.wait_for_selector("#ai .aic")
        assert page.eval_on_selector_all("#ai .aic", "els => els.map(e => e.dataset.id)") == before
        page.click("#halsa-access button")                                     # Çıkış yap
        page.wait_for_selector("#pw")                                          # D-074: the site-password page
        assert page.query_selector("#own") is None                             # nothing financial remains
        assert page.errors == []


def test_owner_sees_full_yonetim_without_the_financial_password(browser, tmp_path):
    from tests.test_m7_owner_browser import all_owner, open_own, server
    with server(tmp_path, owner=all_owner(tmp_path)) as srv:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        BA.authorize(ctx)                                                       # owner session
        page = ctx.new_page()
        open_own(page, srv["base"])
        assert page.get_attribute("#own", "data-access") == "owner"
        assert page.is_visible('#own [data-sec="ops"]') and page.is_visible('#own [data-sec="system"]')
        assert page.is_visible("#own .ow-bar") and page.is_hidden("#own .ow-login")


def test_cari_list_needs_the_financial_password_in_the_browser(browser, tmp_path):
    """D-075: the website viewer's Cari tab shows only the Turkish financial-password notice (the page holds no Cari
    record); the same browser sees the factual list after the financial password; logging out removes it."""
    import json
    import urllib.request
    with fair_server(tmp_path) as srv:
        page = viewer_page(browser)
        page.goto(srv["base"] + "/")
        page.wait_for_selector('#halsa-access[data-mode="viewer"]')
        page.click('.tab[data-t="collections"]')
        page.wait_for_selector('#collections[data-finance="locked"] .fin-gate:not([hidden])')
        gate = page.inner_text("#collections .fin-gate")
        assert "finans görüntüleme şifresi gerekir" in gate and "Yönetim sekmesine git" in gate
        assert page.eval_on_selector_all("#collections tbody tr", "trs => trs.length") == 0
        viewer_html = page.content()
        assert page.evaluate("() => DATA.collections.rows.length") == 0
        # the factual rows, as a financial session receives them from the server
        cookie, _ = BA._post_login(srv["base"], "/api/auth/finance-login", BA.TEST_FINANCE_PASSWORD)
        req = urllib.request.Request(srv["base"] + "/api/finance/cari",
                                     headers={"Cookie": f"{BA.AUTH.COOKIE_NAME}={cookie}", "Accept": "application/json", "X-StockAgent": "1"})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.load(r)["rows"]
        assert rows
        # Sales-derived customer identities can overlap Cari account codes; the protected data is the balance
        # record, not the customer's public commercial identity. Prove the identity's independent sales provenance.
        import dashboard_builder as DB
        from services import sales_projection as SP
        sales_path=DB.select_sources(srv["data"])["sales"]
        sales_codes=set(SP.prepare_sales(DB.read_sales_raw(sales_path))["_cust"])
        data=page.evaluate("DATA")
        for x in rows:
            if x["code"] not in sales_codes:
                assert x["code"] not in viewer_html
        for key in ("momentum","ordersize","winback","margin","purchases_yearly","purchases_products","sales_source_issues"):
            for row in data.get(key,{}).get("rows",[]):
                assert row.get("code") is None or row["code"] in sales_codes
                assert not ({"balance","oldest","direction","direction_label","aging"}&set(row))
        assert data["collections"]["rows"]==[]
        page.click("#collections .fin-gate a")                               # → Yönetim, financial password
        page.wait_for_selector("#own.on .ow-login:not([hidden])")
        page.fill("#own .ow-pw", BA.TEST_FINANCE_PASSWORD)
        page.click("#own .ow-unlock")
        page.wait_for_selector('#halsa-access[data-mode="finance"]')
        page.click('.tab[data-t="collections"]')                             # no second login
        page.wait_for_selector('#collections[data-finance="open"] tbody tr')
        assert page.is_hidden("#collections .fin-gate")
        assert page.eval_on_selector_all("#collections tbody tr", "trs => trs.length") == len(rows)
        assert rows[0]["name"] in page.inner_text("#collections")
        page.click("#halsa-access button")                                   # Çıkış yap
        page.wait_for_selector("#pw")
        assert page.query_selector("#collections") is None and rows[0]["code"] not in page.content()
        assert page.errors == []
