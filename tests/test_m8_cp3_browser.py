"""M8-CP3 (D-065): the Excel Upload Center in a real browser (Playwright + Chromium, real FastAPI server running the
PRODUCTION app and gate, real owner login, SYNTHETIC workbooks only). Skipped without Playwright unless
REQUIRE_BROWSER_TESTS=1.

Covers: owner upload of several files on the fixed /yukleme page before any dashboard exists, type selection that is
never taken from the file name, Turkish progress / preview / publication, the dashboard tab afterwards, a carried-over
second publication from the dashboard tab, viewers and financial viewers locked out, and desktop / phone layouts.
"""
import contextlib
import datetime as dt
import http.cookies
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pandas as pd
import pytest

from services import owner_auth as AUTH
from tests import browser_auth as BA
from tests.fixtures import fair_synth as FX
from tests.fixtures import owner_synth as OS_
from tests.test_m7_fair_browser import _free_port, browser, no_horizontal_overflow  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = (*FX.P.values(), *(FX.name(i) for i in FX.P if i != 12), *OS_.SECRETS)


def _status(url, headers=None, data=None, method="GET"):
    req = urllib.request.Request(url, headers=headers or {}, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@contextlib.contextmanager
def upload_server(tmp_path):
    """A server with an EMPTY data folder: nothing has ever been published (fresh hosting)."""
    data, src = tmp_path / "data", tmp_path / "src"
    data.mkdir()
    FX.write_all(str(src))
    OS_.write_kredi(src, dt.date.today())
    with pd.ExcelWriter(src / "rapor (2).xlsx") as w:                    # a renamed stock export, no keyword in the name
        pd.DataFrame([["Sentetik stok"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        df = FX.stock_frame()
        df.loc[len(df)] = ["FS-002", "Sentetik ikinci ürün", 5, 1.5, 1.0, 3.0, 250, ""]
        df.to_excel(w, index=False, startrow=3)
    dash = str(tmp_path / "dashboard.html")
    env = {**os.environ, "STOCKAGENT_NO_DOTENV": "1", "AI_PROVIDER": "disabled", "OPENAI_API_KEY": "",
           "MANUAL_INFLATION_RATE": "0.40", "DATA_FOLDER": str(data), "DASHBOARD_PATH": dash,
           "UPLOAD_DIR": str(data / "uploads"), "AGENT_DB": str(tmp_path / "state.db"), **BA.server_env()}
    for k in ("HEDEF_PATH", "KREDI_PATH", "GIDER_GELIR_PATH", "BUTCE_PATH"):
        env.pop(k, None)
    port = _free_port()
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            if proc.poll() is not None:
                pytest.fail("server exited early:\n" + open(tmp_path / "server.log").read()[-3000:])
            try:
                if _status(base + "/api/health")[0] == 200:
                    break
            except Exception:
                pass
            if time.monotonic() > deadline:
                pytest.fail("server not ready")
            time.sleep(0.2)
        BA.register(base)
        yield {"base": base, "src": src, "data": data, "dash": dash, "log": tmp_path / "server.log"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


def owner_page(browser, base, viewport=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    BA.authorize(ctx, base)
    page = ctx.new_page()
    page.errors, page.requests = [], []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("request", lambda r: page.requests.append((r.method, r.url)))
    return page


def anon_page(browser, cookie=None, base=None):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    if cookie:
        ctx.add_cookies([{"name": AUTH.COOKIE_NAME, "value": cookie, "url": base, "httpOnly": True,
                          "sameSite": "Strict"}])
    page = ctx.new_page()
    page.errors, page.requests = [], []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("request", lambda r: page.requests.append((r.method, r.url)))
    return page


def finance_cookie(base):
    req = urllib.request.Request(base + "/api/auth/finance-login", method="POST",
                                 data=json.dumps({"password": BA.TEST_FINANCE_PASSWORD}).encode(),
                                 headers={"Content-Type": "application/json", "X-StockAgent": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        jar = http.cookies.SimpleCookie()
        for h in r.headers.get_all("Set-Cookie") or ():
            jar.load(h)
    return jar[AUTH.COOKIE_NAME].value


def row(page, label):
    return f'#up .up-file:has(.up-fname:text-is("{label}"))'


def wait_rows(page, n, timeout=30000):
    page.wait_for_function(f"() => document.querySelectorAll('#up .up-file[data-id]').length === {n}",
                           timeout=timeout)


def choose(page, label, slot):
    page.select_option(row(page, label) + " .up-slot", slot)
    page.wait_for_selector(row(page, label) + '[data-ok="1"]')


def test_owner_uploads_validates_previews_and_publishes_from_the_fixed_page(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        assert _status(base + "/")[0] == 404                                # fresh server: nothing published
        page = owner_page(browser, base)
        page.goto(base + "/yukleme")
        page.wait_for_selector('#up[data-access="owner"] .up-owner:not([hidden])')
        assert page.title() == "Dosya Yükleme — Şirket Panosu"
        text = page.text_content("#up")                                   # headings are CSS-uppercased
        for t in ("Dosya Yükleme", "1. Dosyaları seçin", "Şu anda yayında", "Yayında bir rapor yok.",
                  "DinamikCRM'e hiçbir bağlantı yoktur"):
            assert t in text, t
        files = [str(srv["src"] / n) for n in (FX.STOCK_FILE, FX.SALES_FILE, FX.AGING_FILE, OS_.KREDI_FILE)]
        page.set_input_files("#up .up-input", files)
        wait_rows(page, 4)
        # nothing is preselected — not even for a file literally named "STOK …"
        assert page.eval_on_selector_all("#up .up-slot", "els => els.map(e => e.value)") == ["", "", "", ""]
        assert page.is_disabled("#up .up-validate")
        choose(page, FX.STOCK_FILE, "stock")
        choose(page, FX.SALES_FILE, "sales")
        choose(page, FX.AGING_FILE, "aging")
        assert page.is_disabled("#up .up-validate")                          # one file still has no type
        choose(page, OS_.KREDI_FILE, "kredi")
        page.fill(row(page, FX.STOCK_FILE) + ' input[data-f="snapshot_date"]', "2026-06-30")
        assert "Boş bırakılan alanlar “bilinmiyor” olarak kaydedilir." in page.inner_text(row(page, FX.SALES_FILE))
        page.click("#up .up-validate")
        page.wait_for_selector('#up .up-job:not([hidden])')
        assert "Doğrulama:" in page.inner_text("#up .up-job")
        page.wait_for_selector('#up .up-preview[data-status="ready"]', timeout=60000)
        prev = page.inner_text("#up .up-preview")
        for t in ("4. Önizleme", "Bu önizleme canlı verileri değiştirmez", "Kaynaklar, beyanlar, sayfalar ve sütunlar",
                  "Zorunlu sütunlar", "Mutabakat durumu", "Veri Kontrol üzerindeki etkisi", "Pano bölümlerine etkisi",
                  "Gizli kalan analizler", "Eksik kaynak bilgileri", "bilinmiyor (beyan edilmedi)", "30 Haziran 2026",
                  "Değişiklikleri Yayınla"):
            assert t in prev, t
        assert "Gösterilir (doğrulanmış)" not in prev                        # nothing becomes visible
        oks = page.eval_on_selector_all('#up .up-preview .up-chip[data-found="true"]', "els => els.map(e => e.textContent)")
        assert oks and all(t.startswith("✓ ") for t in oks)                  # found columns keep their tick
        assert not page.query_selector_all('#up .up-preview .up-chip[data-found="null"]')
        assert "sütunlar denetlenemedi" not in prev
        assert not [s for s in SECRETS if s in prev]
        assert _status(base + "/")[0] == 404                                # the preview published nothing
        page.click("#up .up-publish")
        assert page.is_visible("#up .up-confirm")
        page.click("#up .up-yes")
        page.wait_for_selector("#up .up-state.ok", timeout=60000)
        assert "Yayınlandı" in page.inner_text("#up .up-state")
        assert page.is_visible("#up .up-reload")
        pub = page.inner_text("#up .up-pub")
        assert "Stok: " + FX.STOCK_FILE in pub and "Kredi: " + OS_.KREDI_FILE in pub
        assert "Yayınlandı" in page.inner_text("#up .up-hist")
        # the dashboard now exists and the owner sees the tab there
        page.goto(base + "/")
        page.wait_for_selector('.tab[data-t="up"]:not([hidden])')
        assert page.inner_text('.tab[data-t="up"]') == "Dosya Yükleme"
        assert page.errors == []


def test_second_upload_from_the_dashboard_tab_carries_the_other_reports(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        page = owner_page(browser, base)
        page.goto(base + "/yukleme")
        page.wait_for_selector('#up[data-access="owner"]')
        page.set_input_files("#up .up-input", [str(srv["src"] / n) for n in (FX.STOCK_FILE, FX.SALES_FILE)])
        wait_rows(page, 2)
        choose(page, FX.STOCK_FILE, "stock")
        choose(page, FX.SALES_FILE, "sales")
        page.click("#up .up-validate")
        page.wait_for_selector('#up .up-preview[data-status="ready"]', timeout=60000)
        page.click("#up .up-publish")
        page.click("#up .up-yes")
        page.wait_for_selector("#up .up-state.ok", timeout=60000)
        # next month: only a new stock report, uploaded under a meaningless name from the dashboard tab
        page.goto(base + "/")
        page.click('.tab[data-t="up"]')
        page.wait_for_selector('#up[data-access="owner"] .up-owner:not([hidden])')
        page.set_input_files("#up .up-input", [str(srv["src"] / "rapor (2).xlsx")])
        wait_rows(page, 1)
        choose(page, "rapor (2).xlsx", "stock")
        page.click("#up .up-validate")
        page.wait_for_selector('#up .up-preview[data-status="ready"]', timeout=60000)
        prev = page.inner_text("#up .up-preview")
        assert "yayındaki rapor, değişmeden devralınır" in prev and "Cari raporu" in prev
        assert "Satış — " + FX.SALES_FILE in prev and "yukleme-" not in prev    # the owner's name, not the stored one
        page.click("#up .up-publish")
        page.click("#up .up-yes")
        page.wait_for_selector("#up .up-state.ok", timeout=60000)
        assert "Stok: rapor (2).xlsx" in page.inner_text("#up .up-pub")
        assert page.errors == []


def test_identical_upload_reports_no_change(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        page = owner_page(browser, base)
        for _ in range(2):
            page.goto(base + "/yukleme")
            page.wait_for_selector('#up[data-access="owner"]')
            page.set_input_files("#up .up-input", [str(srv["src"] / n) for n in (FX.STOCK_FILE, FX.SALES_FILE)])
            wait_rows(page, 2)
            choose(page, FX.STOCK_FILE, "stock")
            choose(page, FX.SALES_FILE, "sales")
            page.click("#up .up-validate")
            page.wait_for_selector('#up .up-preview[data-status="ready"]', timeout=60000)
            noop = page.get_attribute("#up .up-preview", "data-noop")
            page.click("#up .up-publish")
            page.click("#up .up-yes")
            page.wait_for_selector("#up .up-state.ok", timeout=60000)
        assert noop == "1" and "Değişiklik yoktu" in page.inner_text("#up .up-state")
        assert "Değişiklik yoktu — yayın yapılmadı" in page.inner_text("#up .up-hist")


def test_invalid_upload_shows_turkish_errors_and_cannot_be_published(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        page = owner_page(browser, base)
        page.goto(base + "/yukleme")
        page.wait_for_selector('#up[data-access="owner"]')
        bad = srv["src"] / "bozuk.xlsx"
        bad.write_bytes((srv["src"] / FX.SALES_FILE).read_bytes()[:3000])
        page.set_input_files("#up .up-input", [str(bad)])
        page.wait_for_selector("#up .up-state.err")
        assert "bozuk veya eksik" in page.inner_text("#up .up-state")
        page.set_input_files("#up .up-input", [str(srv["src"] / FX.AGING_FILE), str(srv["src"] / FX.SALES_FILE)])
        wait_rows(page, 2)
        choose(page, FX.AGING_FILE, "stock")                                # the wrong type, chosen by the owner
        choose(page, FX.SALES_FILE, "sales")
        page.click("#up .up-validate")
        page.wait_for_selector('#up .up-preview[data-status="invalid"]', timeout=60000)
        prev = page.inner_text("#up .up-preview")
        assert "Hatalar (yayını engeller)" in prev and "yanlış rapor türü" in prev
        # a source that could not be read as the chosen type: its required columns are UNKNOWN, never ticked
        # (owner-local acceptance 2026-10-27: a real Cari export chosen as "Stok" showed every column with ✓)
        unk = page.eval_on_selector_all('#up .up-preview .up-chip[data-found="null"]',
                                        "els => els.map(e => [e.textContent, e.className, e.title])")
        assert unk and all(t.startswith("? ") and "unk" in c and "ok" not in c.split() and ti == "Denetlenemedi"
                           for t, c, ti in unk)
        assert not page.query_selector_all('#up .up-preview .up-chip[data-found="null"].ok')
        assert "✓" not in "".join(t for t, _, _ in unk)
        assert "sütunlar denetlenemedi" in prev
        assert page.query_selector("#up .up-publish") is None
        assert _status(base + "/")[0] == 404


def test_viewers_and_financial_viewers_cannot_use_the_upload_center(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        for page in (anon_page(browser), anon_page(browser, finance_cookie(base), base)):
            page.goto(base + "/yukleme")
            page.wait_for_selector("#up .up-viewer:not([hidden])")
            assert page.is_hidden("#up .up-owner")
            assert "yalnızca yönetici girişiyle" in page.inner_text("#up .up-viewer")
            assert page.get_attribute("#up .up-viewer a", "href") == "/giris"
            assert not any("/api/uploads" in u for _, u in page.requests)    # the page never asks
            assert page.errors == []
        for path, method in (("/api/uploads", "GET"), ("/api/uploads", "POST")):
            code, _ = _status(base + path, headers={"X-StockAgent": "1"}, method=method,
                              data=b"{}" if method == "POST" else None)
            assert code == 401
        fin = finance_cookie(base)
        code, body = _status(base + "/api/uploads", headers={"Cookie": f"{AUTH.COOKIE_NAME}={fin}"})
        assert code == 403 and "owner_required" in body


def test_viewer_does_not_see_the_tab_on_the_dashboard(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        page = owner_page(browser, base)
        page.goto(base + "/yukleme")
        page.wait_for_selector('#up[data-access="owner"]')
        page.set_input_files("#up .up-input", [str(srv["src"] / n) for n in (FX.STOCK_FILE, FX.SALES_FILE)])
        wait_rows(page, 2)
        choose(page, FX.STOCK_FILE, "stock")
        choose(page, FX.SALES_FILE, "sales")
        page.click("#up .up-validate")
        page.wait_for_selector('#up .up-preview[data-status="ready"]', timeout=60000)
        page.click("#up .up-publish")
        page.click("#up .up-yes")
        page.wait_for_selector("#up .up-state.ok", timeout=60000)
        vp = anon_page(browser)
        vp.goto(base + "/")
        vp.wait_for_selector("#halsa-access:not([hidden])")
        time.sleep(0.3)
        assert vp.is_hidden('.tab[data-t="up"]')
        assert vp.errors == []


def test_phone_and_desktop_layouts_have_no_horizontal_scroll(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        for vw in ({"width": 360, "height": 780}, {"width": 1280, "height": 900}):
            page = owner_page(browser, base, viewport=vw)
            page.goto(base + "/yukleme")
            page.wait_for_selector('#up[data-access="owner"]')
            assert no_horizontal_overflow(page)
            if vw["width"] == 360:
                page.set_input_files("#up .up-input", [str(srv["src"] / n) for n in
                                                       (FX.STOCK_FILE, FX.SALES_FILE, OS_.KREDI_FILE)])
                wait_rows(page, 3)
                choose(page, FX.STOCK_FILE, "stock")
                choose(page, FX.SALES_FILE, "sales")
                choose(page, OS_.KREDI_FILE, "kredi")
                assert no_horizontal_overflow(page)
                page.click("#up .up-validate")
                page.wait_for_selector('#up .up-preview[data-status="ready"]', timeout=60000)
                assert no_horizontal_overflow(page)
                box = page.eval_on_selector("#up .up-publish", "e => e.getBoundingClientRect().width")
                assert box > 250                                            # full-width button on a phone
            assert page.errors == []


def test_hostile_file_names_are_shown_as_text_only(browser, tmp_path):
    with upload_server(tmp_path) as srv:
        base = srv["base"]
        hostile = srv["src"] / '<img src=x onerror="window.__UP_XSS=1">rapor.xlsx'
        shutil.copyfile(srv["src"] / FX.STOCK_FILE, hostile)
        page = owner_page(browser, base)
        page.goto(base + "/yukleme")
        page.wait_for_selector('#up[data-access="owner"]')
        page.set_input_files("#up .up-input", [str(hostile)])
        wait_rows(page, 1)
        assert page.inner_text("#up .up-fname") == hostile.name
        assert page.query_selector("#up img") is None and page.evaluate("window.__UP_XSS") is None
        page.select_option("#up .up-slot", "stock")                         # the only row
        page.wait_for_selector('#up .up-file[data-ok="1"]')
        page.click("#up .up-validate")
        page.wait_for_selector('#up .up-preview[data-status]', timeout=60000)
        assert hostile.name in page.inner_text("#up .up-preview")           # the label, as plain text
        assert page.query_selector("#up img") is None and page.evaluate("window.__UP_XSS") is None
        assert page.errors == []
