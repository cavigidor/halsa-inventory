"""M8-VKM-1 (D-062): Veri Kontrol Merkezi in a real browser (Playwright + Chromium, real FastAPI server, synthetic data).

The server is the same one the M7 / CP1 browser tests start (temporary folder of SYNTHETIC workbooks, real refresh,
restart re-derivation, real owner login through `tests/browser_auth.py`). Observation happens at startup through the
GenerationStore observer, exactly as in production. Skipped without Playwright unless REQUIRE_BROWSER_TESTS=1.
"""
import json
import time
import urllib.error
import urllib.request

import pytest

from tests.fixtures import fair_synth as FX
from tests.test_m7_fair_browser import _get, browser, fair_server, no_horizontal_overflow, open_page  # noqa: F401
from tests.test_m8_cp1_browser import viewer_page

NOT_APPROVAL = "İnceleme bir onay değildir; metrikler yalnızca doğrulanmış kanıtla gösterilir."


def _anon_status(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _observed(base, timeout=30):
    """The server observes its accepted generation at startup; wait until the issue store has it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = _get(base + "/api/vkm/issues?status=all")
        if d["sources"] and d["issues"]:
            return d
        time.sleep(0.2)
    pytest.fail("Veri Kontrol Merkezi observed nothing")


def open_vkm(page, base):
    page.goto(base + "/")
    page.click('.tab[data-t="vkm"]')


def _canaries():
    """Synthetic customer codes and names of the fixture (never real data)."""
    return {*FX.P.values(), *(FX.name(i) for i in FX.P)}


def test_viewer_sees_only_the_turkish_counts_summary(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        _observed(srv["base"])
        page = viewer_page(browser)
        page.requests = []
        page.on("request", lambda r: page.requests.append(r.url))
        page.goto(srv["base"] + "/")
        assert page.inner_text('.tab[data-t="vkm"]') == "Veri Kontrol"
        assert not any("/api/vkm" in u for u in page.requests)                   # lazy: nothing before the tab
        page.click('.tab[data-t="vkm"]')
        page.wait_for_selector('#vkm[data-access="viewer"] .vk-sum:not([hidden]) .vk-tile')
        text = page.inner_text("#vkm")
        assert page.text_content("#vkm h2") == "Veri Kontrol Merkezi" and NOT_APPROVAL in text
        assert "Açık konu" in text and "Son gözlem:" in text
        assert page.is_visible("#vkm .vk-viewer") and page.get_attribute("#vkm .vk-viewer a", "href") == "/giris"
        assert page.is_hidden("#vkm .vk-owner")
        assert page.query_selector("#vkm .vk-item") is None                     # no issue rows for a viewer
        assert "VKM-" not in text                                               # no issue IDs, locators or notes
        assert not any(c in text for c in _canaries())
        assert any(u.endswith("/api/vkm/summary") for u in page.requests)
        assert not any("/api/vkm/issues" in u for u in page.requests)          # the viewer page never asks
        for p in ("/api/vkm/issues", "/api/vkm/issues?status=all"):
            assert _anon_status(srv["base"] + p) == 401                         # and the server would refuse
        assert page.errors == []


def test_owner_filters_reviews_annotates_and_the_decision_persists(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        first = _observed(srv["base"])
        gate_before = _get(srv["base"] + "/api/accuracy/status")["metrics"]
        page = open_page(browser)
        open_vkm(page, srv["base"])
        page.wait_for_selector('#vkm[data-access="owner"] .vk-item')
        text = page.inner_text("#vkm")
        assert NOT_APPROVAL in text and "Son yükleme gözlemi" in text
        srcs = page.eval_on_selector_all("#vkm .vk-src", "els => els.map(e => [e.dataset.key, e.dataset.state])")
        assert ("stock", "present") in [tuple(s) for s in srcs]
        assert ("kredi", "missing") in [tuple(s) for s in srcs]                 # no Kredi file → shown as missing
        ids = page.eval_on_selector_all("#vkm .vk-item", "els => els.map(e => e.dataset.id)")
        assert ids and all(i.startswith("VKM-") for i in ids)
        assert len(ids) == len([i for i in first["issues"] if i["status"] in
                                ("acik", "yeniden_acildi", "incelendi", "kabul_edildi")])

        # filter: one source only, then clear
        page.select_option('#vkm .vk-f[data-f="source"]', "kredi")
        page.wait_for_function("() => [...document.querySelectorAll('#vkm .vk-item')].length > 0 && "
                               "[...document.querySelectorAll('#vkm .vk-item')].every(e => e.dataset.type === 'missing_source')")
        assert "Beklenen dosya yok" in page.inner_text("#vkm .vk-list")
        page.click("#vkm .vk-clear")
        page.wait_for_function(f"() => document.querySelectorAll('#vkm .vk-item').length === {len(ids)}")

        # open one issue: plain-Turkish explanation, action, location, dates, history
        target = ids[0]
        page.click(f'#vkm .vk-item[data-id="{target}"]')
        page.wait_for_selector(f'#vkm .vk-detail[data-id="{target}"] .vk-hist li')
        detail = page.text_content("#vkm .vk-detail")         # h4 labels are CSS-uppercased; check the DOM text
        for h in ("Ne anlama geliyor?", "Ne yapmalı?", "Nerede?", "Ne zaman?", "Etkilenen analizler", "Geçmiş",
                  "İlk görülme", "Bu yüklemede", NOT_APPROVAL):
            assert h in detail, h
        assert "İlk kez görüldü" in detail
        # review with a note; the reviewer is the logged-in owner (no reviewer field in the form)
        assert page.query_selector("#vkm .vk-detail input[name='reviewer']") is None
        page.fill("#vkm .vk-notein", "Sentetik inceleme notu <b>kalın değil</b>")
        page.click('#vkm .vk-detail .vk-act[data-action="incelendi"]')
        page.wait_for_function("() => document.querySelector('#vkm .vk-detail .vk-msg').textContent === 'Kaydedildi.'")
        assert page.get_attribute("#vkm .vk-detail", "data-status") == "incelendi"
        hist = page.inner_text("#vkm .vk-detail .vk-hist")
        assert "İncelendi olarak işaretlendi" in hist and "Yönetici" in hist
        assert "Sentetik inceleme notu <b>kalın değil</b>" in hist                # shown as text, never as HTML
        assert page.query_selector("#vkm .vk-hist b b") is None
        # a note on its own
        page.fill("#vkm .vk-notein", "İkinci not")
        page.click("#vkm .vk-detail .vk-addnote")
        page.wait_for_function("() => document.querySelector('#vkm .vk-detail .vk-msg').textContent === 'Kaydedildi.'")
        assert "Not eklendi" in page.inner_text("#vkm .vk-detail .vk-hist")
        # an empty note is refused in the page before any request
        page.fill("#vkm .vk-notein", "   ")
        page.click("#vkm .vk-detail .vk-addnote")
        assert page.inner_text("#vkm .vk-detail .vk-msg") == "Not boş olamaz."

        # reload: the decision and both notes persisted on the server; the issue is still listed (not resolved)
        page.reload()
        page.click('.tab[data-t="vkm"]')
        page.wait_for_selector(f'#vkm .vk-item[data-id="{target}"][data-status="incelendi"]')
        page.click(f'#vkm .vk-item[data-id="{target}"]')
        page.wait_for_selector(f'#vkm .vk-detail[data-id="{target}"] .vk-hist li')
        hist = page.inner_text("#vkm .vk-detail .vk-hist")
        assert "Sentetik inceleme notu" in hist and "İkinci not" in hist
        after = _get(srv["base"] + f"/api/vkm/issues/{target}")
        assert after["issue"]["status"] == "incelendi" and after["issue"]["presence"] == "present"
        # reviewing certified nothing: every gate decision is exactly what it was before the review
        assert _get(srv["base"] + "/api/accuracy/status")["metrics"] == gate_before
        assert _get(srv["base"] + "/api/vkm/summary")["notice"] == NOT_APPROVAL
        assert page.errors == []


def test_stale_review_is_refused_and_the_page_reloads_the_list(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        _observed(srv["base"])
        page = open_page(browser)
        open_vkm(page, srv["base"])
        page.wait_for_selector('#vkm[data-access="owner"] .vk-item')
        target = page.eval_on_selector("#vkm .vk-item", "e => e.dataset.id")
        page.click(f'#vkm .vk-item[data-id="{target}"]')
        page.wait_for_selector(f'#vkm .vk-detail[data-id="{target}"] .vk-act')
        # the page presents an observation token that no longer matches (as after a new upload was observed)
        page.route("**/api/vkm/issues/*/review", lambda route: route.continue_(
            post_data=json.dumps({**json.loads(route.request.post_data), "vkm_generation": "vkm-00000000000000000000"})))
        page.click('#vkm .vk-detail .vk-act[data-action="kabul_edildi"]')
        page.wait_for_function("() => (document.querySelector('#vkm .vk-detail .vk-msg')||{}).textContent && "
                               "document.querySelector('#vkm .vk-detail .vk-msg').textContent.includes('yeniden gözlendi')")
        assert _get(srv["base"] + f"/api/vkm/issues/{target}")["issue"]["status"] == "acik"     # nothing changed
        assert page.errors == []


def test_phone_layout_has_no_horizontal_scroll(browser, tmp_path):
    with fair_server(tmp_path) as srv:
        _observed(srv["base"])
        page = open_page(browser, viewport={"width": 360, "height": 780})
        open_vkm(page, srv["base"])
        page.wait_for_selector('#vkm[data-access="owner"] .vk-item')
        assert no_horizontal_overflow(page)
        page.click("#vkm .vk-item")
        page.wait_for_selector("#vkm .vk-detail .vk-hist li")
        assert no_horizontal_overflow(page)
        vp = viewer_page(browser)
        vp.set_viewport_size({"width": 360, "height": 780})
        open_vkm(vp, srv["base"])
        vp.wait_for_selector('#vkm[data-access="viewer"] .vk-tile')
        assert no_horizontal_overflow(vp)
        assert page.errors == [] and vp.errors == []
