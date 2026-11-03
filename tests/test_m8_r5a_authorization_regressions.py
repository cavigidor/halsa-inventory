"""M8-R5A review regressions (integration check PR #39, findings 2 and 3).

A. Product-count authorization: after D-070 the stock product count ("N ürün" in the dashboard header) is the
   remaining rendered stock value the generation / tamper / restart tests rely on. An unverified
   `stock.product_counts` never exposes it — not in the published HTML, not through `GET /`, not through
   `/api/context`, not in the refresh report — under the REAL production register and gate. A synthetic VERIFIED
   copy of the register is the control: then exactly the count is shown.
B. Policy-version invalidation: a page and manifest published under an earlier policy — `m8-accuracy-1` (before
   D-070), `m8-r5a-stock-retirement` (before D-071, whose pages carried Cari "Vadesi Geçen" overdue claims) and the
   `m8-r5b1-cari-invoice-age` (before D-072's Cari labels and date provenance) and the immediate predecessor
   `m8-cari-d072` (before D-073 certified the Cari list, i.e. a page built while it was always withheld) — are never
   accepted as current: refused live and after a restart, with no old business data
   or old Cari claim served; a page regenerated under the current policy follows the normal authorization rules.

REAL production gate (`production_gate`). Synthetic workbooks only. No AI, no network, no private file.
"""
import dataclasses
import re
from pathlib import Path

import pytest

import app as APP
import dashboard_builder as DB
from services import accuracy as ACC
from services import generation as GEN
from tests.test_m8_accuracy2 import (TODAY, api_state, promoted, refresh, selection, stock_rows,  # noqa: F401
                                     world)
from tests.test_m8_accuracy2_review2 import (close, data_of, displayed_stock_count, live_client, restart,
                                             warm_up)

pytestmark = pytest.mark.production_gate
OLD_POLICY = "m8-accuracy-1"                       # the marker every page / manifest carried before D-070
R5A_POLICY = "m8-r5a-stock-retirement"             # D-070, replaced by D-071
R5B1_POLICY = "m8-r5b1-cari-invoice-age"           # D-071, replaced by D-072
D072_POLICY = "m8-cari-d072"                       # the immediate predecessor (D-072), replaced by D-073
PREVIOUS_POLICIES = (OLD_POLICY, R5A_POLICY, R5B1_POLICY, D072_POLICY)
# What an R5A-era page said about the Cari export (the D-071 build no longer produces it). Emulated in the old-policy
# build so the refused page provably carries an old overdue claim; the refresh digests the page AFTER it is written,
# so the old page and its old manifest stay internally consistent.
LEGACY_CARI = ('<!-- legacy Tahsilat: {"title":"Tahsilat","note":"Vadesi geçen alacaklar, en büyük önce. Listenin '
               'başındakileri kovalayın.","rows":[{"name":"Sentetik Kapali Ltd","overdue":60000,'
               '"oldest":"Kasım-2025 ve öncesi"}]} Vadesi Geçen -->')
COUNT = len(stock_rows())


def _displayed(text):
    m = re.search(r'<meta name="stockagent-displayed" content="([^"]*)">', text)
    return set(filter(None, (m.group(1) if m else "").split(",")))


def _publish(w, monkeypatch):
    selection(w.folder)                            # every stock declaration made: only the register can withhold
    rep = refresh(w.folder, w.dash, install=w.install)
    assert rep["ok"], rep
    monkeypatch.setattr(APP, "DASHBOARD_PATH", w.dash)
    return rep, Path(w.dash).read_text(encoding="utf-8")


# =============================================================================================
# A. product-count authorization
# =============================================================================================
def test_unverified_product_count_is_never_exposed(world, monkeypatch):
    assert ACC.REGISTER["stock.product_counts"].certified != ACC.State.VERIFIED       # the production register
    rep, html = _publish(world, monkeypatch)
    st, _ = api_state(world, "stock.product_counts")
    assert st["state"] != "verified"
    # rendered HTML: no header count, not declared as displayed
    assert displayed_stock_count(html) is None and " ürün</div>" not in html
    assert "stock.product_counts" not in _displayed(html)
    # the refresh report never prints the unverified count
    assert rep["counts"].get("products") is None
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == Path(world.dash).read_bytes()  # served, yet without the count
        assert displayed_stock_count(r.text) is None
        # the API carrying the count (context.full → company.products_scanned) is withheld before the endpoint runs
        ctx = c.get("/api/context")
        assert ctx.status_code == ACC.WITHHELD_HTTP_STATUS
        assert "products_scanned" not in ctx.text
        assert f'"products_scanned": {COUNT}' not in c.get("/api/accuracy/status").text
    finally:
        close(c)


def test_verified_synthetic_control_shows_exactly_the_count(world, promoted, monkeypatch):  # noqa: F811
    _, html = _publish(world, monkeypatch)
    st, _ = api_state(world, "stock.product_counts")
    assert st["state"] == "verified"                                                   # synthetic register copy
    assert displayed_stock_count(html) == COUNT
    assert "stock.product_counts" in _displayed(html)
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and displayed_stock_count(r.text) == COUNT
        assert r.headers["X-Halsa-Generation"] == world.store.current().generation_id
    finally:
        close(c)
    with pytest.raises(ACC.RegisterError):
        ACC.validate_register(promoted, production=True)                               # never a production promotion


# =============================================================================================
# B. old-policy page and manifest
# =============================================================================================
def _publish_as_previous_policy(w, monkeypatch, old=OLD_POLICY):
    """Exactly what the earlier build wrote: the old HTML policy marker AND an old-policy manifest (core_id); for the
    R5A policy also the old Cari overdue claim (LEGACY_CARI)."""
    real_build = GEN.build_manifest
    real_html = DB._html
    with monkeypatch.context() as mp:
        mp.setattr(ACC, "POLICY_VERSION", old)
        mp.setattr(GEN, "build_manifest",
                   lambda *a, **k: dataclasses.replace(real_build(*a, **k), policy_version=old))
        if old == R5A_POLICY:
            mp.setattr(DB, "_html", lambda *a, **k: real_html(*a, **k).replace("</body>", LEGACY_CARI + "</body>"))
        _, html = _publish(w, monkeypatch)
    assert ACC.POLICY_VERSION != old and ACC.POLICY_VERSION == "m8-cari-d073"
    assert f'<meta name="stockagent-accuracy" content="{old}">' in html
    assert GEN.read_manifest(GEN.manifest_path(w.dash)).policy_version == old
    assert (LEGACY_CARI in html) == (old == R5A_POLICY)
    return html


def _refused_without_data(r, old_html, old=OLD_POLICY):
    assert r.status_code == 503
    assert data_of(r.text) is None and displayed_stock_count(r.text) is None
    assert r.content != old_html.encode() and old not in r.text and "Sentetik" not in r.text
    assert "Vadesi" not in r.text and "overdue" not in r.text and "kovalayın" not in r.text   # no old Cari claim


@pytest.mark.parametrize("old", PREVIOUS_POLICIES)
def test_old_policy_page_and_manifest_are_refused_live_and_after_restart(world, promoted, monkeypatch, old):  # noqa: F811
    old_html = _publish_as_previous_policy(world, monkeypatch, old)
    assert displayed_stock_count(old_html) == COUNT            # the old page DOES carry business data to protect
    c = live_client(world)
    try:
        r = c.get("/")
        _refused_without_data(r, old_html, old)
        assert r.headers["X-Halsa-Dashboard"] == "policy_marker_missing"
    finally:
        close(c)
    c = restart(world, monkeypatch)
    try:
        warm_up()
        r = c.get("/")
        _refused_without_data(r, old_html, old)
        assert r.headers["X-Halsa-Dashboard"] == "no_accepted_generation"
        assert APP.app.state.generations.restore_reason == "evidence_mismatch"
        assert c.get("/api/accuracy/status").json()["generation_id"] is None
    finally:
        close(c)


@pytest.mark.parametrize("old", PREVIOUS_POLICIES)
def test_regenerated_current_policy_page_follows_normal_authorization(world, promoted, monkeypatch, old):  # noqa: F811
    _publish_as_previous_policy(world, monkeypatch, old)
    _, html = _publish(world, monkeypatch)                      # a controlled refresh under the CURRENT policy
    assert f'<meta name="stockagent-accuracy" content="{ACC.POLICY_VERSION}">' in html
    assert LEGACY_CARI not in html and "Listenin başındakileri kovalayın" not in html
    m = world.store.current()
    assert m.policy_version == ACC.POLICY_VERSION
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == Path(world.dash).read_bytes()
        assert displayed_stock_count(r.text) == COUNT and r.headers["X-Halsa-Generation"] == m.generation_id
    finally:
        close(c)
    c = restart(world, monkeypatch)                             # and it survives a restart like any current page
    try:
        warm_up()
        r = c.get("/")
        assert r.status_code == 200 and displayed_stock_count(r.text) == COUNT
        assert APP.app.state.generations.current().core_id == m.core_id
    finally:
        close(c)


@pytest.mark.parametrize("old", PREVIOUS_POLICIES)
def test_regenerated_page_without_verification_still_hides_the_count(world, monkeypatch, old):
    # the same regeneration under the PRODUCTION register: current policy, but the count stays withheld
    _publish_as_previous_policy(world, monkeypatch, old)
    _, html = _publish(world, monkeypatch)
    assert f'<meta name="stockagent-accuracy" content="{ACC.POLICY_VERSION}">' in html
    assert displayed_stock_count(html) is None and "stock.product_counts" not in _displayed(html)
    # the real gate also withholds the Cari list and the withdrawn risk list; nothing Cari-derived is displayed
    assert not {"receivables.collections", "receivables.risk"} & _displayed(html)
    assert LEGACY_CARI not in html and data_of(html) is not None
