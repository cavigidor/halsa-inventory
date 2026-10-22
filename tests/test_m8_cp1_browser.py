"""M8-CP1 (D-060) in a real browser (Playwright + Chromium, real FastAPI server, synthetic data).

The father's experience: open the link, no login, every tab navigable, nothing changeable. The owner's experience:
log in once through /giris, change data, log out. The server uses the REAL owner login (no harness).
"""
import pytest

from tests import browser_auth as BA
from tests.test_m7_crm_browser import P, open_crm, pick_customer
from tests.test_m7_fair_browser import _get, browser, fair_server  # noqa: F401  (fixture re-export)

TABS = ["sq", "c360", "opp", "fair", "crm", "own", "ai"]


def viewer_page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})       # NO owner cookie
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    return page


def test_father_opens_the_link_and_navigates_without_login(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        page = viewer_page(browser)
        r = page.goto(srv["base"] + "/")
        assert r.status == 200 and page.url.rstrip("/") == srv["base"]          # no redirect to a login page
        page.wait_for_selector('#halsa-access[data-mode="viewer"]')
        bar = page.inner_text("#halsa-access")
        assert "Görüntüleme modu" in bar and page.get_attribute("#halsa-access a", "href") == "/giris"
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs[-7:] == TABS
        for t in tabs:                                                          # every tab opens, read-only
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
        page = viewer_page(browser)
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
        page.wait_for_selector('#halsa-access[data-mode="viewer"]')
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
        page.wait_for_selector('#halsa-access[data-mode="viewer"]')
        page.click('.tab[data-t="own"]')
        page.wait_for_selector("#own .ow-login:not([hidden])")                  # locked again
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
