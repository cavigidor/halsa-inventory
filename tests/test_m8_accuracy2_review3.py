"""M8-ACCURACY-2 — third review pass on PR #28: GET / must never serve on-disk HTML on the page's own claims.

Finding: `_dashboard_authorized()` served a page whose `stockagent-displayed` marker was empty even with no accepted
generation, other bytes or an edited file. The marker is a claim of the HTML, not evidence. Now the on-disk page is
served only when its exact bytes, generation marker and publication record match the accepted, re-validated
manifest — and after a restart only a dashboard RE-BUILT by the server from the re-derived evidence is served.

REAL production gate (`production_gate`). Synthetic data only. No AI provider.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import app as APP
import dashboard_builder as db
from services import accuracy as ACC
from services import generation as GEN
from services import refresh as RF
from tests.test_m8_accuracy2 import (SE, TODAY, promoted, refresh, selection, stock_rows, world,  # noqa: F401
                                     write_stock)
from tests.test_m8_accuracy2_review2 import (NEW_ROW, close, data_of, live_client, reorder_rows,  # noqa: F401
                                             restart)

pytestmark = pytest.mark.production_gate
ROOT = Path(__file__).resolve().parents[1]
INJECT = ('<script>const DATA={"reorder":{"rows":[{"code":"ENJ-1","name":"Enjekte Kalem","stock":1}]}};\n'
          '</script><p>Kâr marjı %87 (enjekte)</p>')


def withheld(r, reason=None):
    assert r.status_code == 503, r.status_code
    assert "Enjekte" not in r.text and "enjekte" not in r.text and "Sentetik" not in r.text
    assert data_of(r.text) is None and "const DATA" not in r.text
    if reason:
        assert r.headers["X-Halsa-Dashboard"] == reason, r.headers.get("X-Halsa-Dashboard")


def inject(path, html_text=INJECT):
    """Insert fake metric data into the page; its displayed marker keeps claiming that nothing is displayed."""
    t = Path(path).read_text(encoding="utf-8")
    Path(path).write_text(t.replace("</head>", html_text + "</head>", 1), encoding="utf-8")


@pytest.fixture
def empty_published(world, monkeypatch):
    """The REAL register: nothing is VERIFIED, so the published page's displayed marker is empty."""
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    monkeypatch.setattr(APP, "DASHBOARD_PATH", world.dash)
    body = Path(world.dash).read_bytes()
    assert GEN.dashboard_markers(body)[1] == ()
    return body


@pytest.fixture
def metric_published(world, promoted, monkeypatch):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    monkeypatch.setattr(APP, "DASHBOARD_PATH", world.dash)
    body = Path(world.dash).read_bytes()
    assert GEN.dashboard_markers(body)[1] and reorder_rows(body.decode())
    return body


# 1 ----------------------------------------------------------------------------------------------
def test_valid_accepted_empty_dashboard_is_served(world, empty_published, monkeypatch):
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == empty_published
        assert r.headers["X-Halsa-Generation"] == world.store.current().generation_id
    finally:
        close(c)
    c = restart(world, monkeypatch)                                  # and after a restart + re-validation
    try:
        r = c.get("/")
        m = APP.app.state.generations.current()
        assert r.status_code == 200 and GEN.sha256_bytes(r.content) == m.dashboard_sha256
        assert GEN.dashboard_markers(r.content) == (m.core_id, ())
    finally:
        close(c)


# 2 ----------------------------------------------------------------------------------------------
def test_edited_empty_dashboard_with_injected_data_is_refused(world, empty_published, monkeypatch):
    inject(world.dash)                                               # marker still says: displays nothing
    assert GEN.dashboard_markers(Path(world.dash).read_bytes())[1] == ()
    c = live_client(world)
    try:
        withheld(c.get("/"), "bytes_mismatch")
    finally:
        close(c)
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "dashboard_bytes_mismatch"
        withheld(c.get("/"), "no_accepted_generation")
    finally:
        close(c)


def test_editing_manifest_and_page_together_never_serves_the_edited_page(world, empty_published, monkeypatch):
    """Worst case: whoever edits the page also re-records its digest in the manifest (ids recomputed). The restart
    re-derives the generation from the workbook bytes and serves only a dashboard the server RE-BUILDS from it."""
    inject(world.dash)
    mp = GEN.manifest_path(world.dash)
    d = json.load(open(mp, encoding="utf-8"))
    d["dashboard_sha256"] = GEN.sha256_file(world.dash)
    m = GEN.Manifest(d["created_at"], tuple(GEN.SourceRecord.from_dict(x) for x in d["core"]), (), d["joins"],
                     d["selection_origin"], d["policy_version"], d["dashboard_sha256"])
    json.dump(m.to_dict(), open(mp, "w", encoding="utf-8"))
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "pending_revalidation"
        r = c.get("/")
        assert r.status_code == 200 and "Enjekte" not in r.text and "enjekte" not in r.text
        assert GEN.sha256_bytes(r.content) == APP.app.state.generations.current().dashboard_sha256
        assert "Enjekte" not in Path(world.dash).read_text(encoding="utf-8")   # the edited file was replaced
    finally:
        close(c)


# 3 ----------------------------------------------------------------------------------------------
def test_empty_dashboard_without_accepted_generation_after_source_change(world, empty_published, monkeypatch):
    write_stock(world.folder, stock_rows([NEW_ROW]))
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "source_changed"
        r = c.get("/")
        withheld(r, "no_accepted_generation")
        assert r.content != empty_published and "Pano şu anda gösterilemiyor" in r.text
    finally:
        close(c)


def test_empty_dashboard_without_any_store_or_manifest_is_not_served(world, empty_published, monkeypatch, tmp_path):
    c = live_client(world)
    try:
        APP.app.state.generations = None                             # no generation store at all
        withheld(c.get("/"), "no_accepted_generation")
        cli = tmp_path / "cli.html"                                   # a CLI build: gated, empty marker, no manifest
        db.build(world.folder, str(cli), today=TODAY)
        assert GEN.dashboard_markers(cli.read_bytes())[1] == ()
        monkeypatch.setattr(APP, "DASHBOARD_PATH", str(cli))
        APP.app.state.generations = world.store
        withheld(c.get("/"), "generation_mismatch")
    finally:
        close(c)


def test_pending_candidate_that_fails_revalidation_is_not_served(world, empty_published, monkeypatch):
    c = restart(world, monkeypatch)                                  # candidate pending (files still match) …
    try:
        assert APP.app.state.generations.restore_reason == "pending_revalidation"
        write_stock(world.folder, stock_rows([NEW_ROW]))              # … the source changes before re-validation
        withheld(c.get("/"), "no_accepted_generation")
        assert APP.app.state.generations.restore_reason == "source_changed"
    finally:
        close(c)


# 4 ----------------------------------------------------------------------------------------------
def test_valid_authorized_synthetic_metric_is_served(world, metric_published, monkeypatch):
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == metric_published and reorder_rows(r.text)
    finally:
        close(c)
    c = restart(world, monkeypatch)
    try:
        r = c.get("/")
        assert r.status_code == 200 and reorder_rows(r.text) == reorder_rows(metric_published.decode())
    finally:
        close(c)


# 5 ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("edit, reason", [
    (lambda t: t.replace('content="stock.', 'content="x.', 1), "bytes_mismatch"),            # list altered
    (lambda t: __import__("re").sub(r'(<meta name="stockagent-displayed" content=")[^"]*"', r'\1"', t, 1),
     "bytes_mismatch"),                                                                     # metric hidden
    (lambda t: t.replace("<title>", '<meta name="stockagent-displayed" content=""><title>', 1), "bytes_mismatch"),
    (lambda t: __import__("re").sub(r'(<meta name="stockagent-generation" content=")[0-9a-f]+', r'\g<1>ab', t, 1),
     "generation_mismatch"),
    (lambda t: t.replace('name="stockagent-accuracy" content="', 'name="stockagent-accuracy" content="old-', 1),
     "policy_marker_missing"),
    (lambda t: t.replace('<meta name="stockagent-displayed"', '<meta name="stockagent-shown"', 1), "bytes_mismatch"),
])
def test_tampered_markers_are_refused(world, metric_published, edit, reason):
    p = Path(world.dash)
    p.write_text(edit(p.read_text(encoding="utf-8")), encoding="utf-8")
    assert p.read_bytes() != metric_published
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 503 and r.headers["X-Halsa-Dashboard"] == reason
        assert reorder_rows(r.text) == [] and data_of(r.text) is None
    finally:
        close(c)


def test_tampered_empty_marker_on_the_real_register_page_is_refused(world, empty_published):
    p = Path(world.dash)
    p.write_text(p.read_text(encoding="utf-8").replace('stockagent-displayed" content=""',
                                                      'stockagent-displayed" content="stock.reorder"', 1),
                 encoding="utf-8")
    c = live_client(world)
    try:
        withheld(c.get("/"), "bytes_mismatch")
    finally:
        close(c)


# 6 ----------------------------------------------------------------------------------------------
def test_refresh_publishes_new_bytes_and_rollback_keeps_the_previous(world, empty_published, monkeypatch):
    APP.app.state.generations = world.store
    write_stock(world.folder, stock_rows([NEW_ROW]))
    assert RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY,
                          install=lambda s: APP._install_generation(world.repo, s))["ok"]
    b_body = Path(world.dash).read_bytes()
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == b_body
        assert r.headers["X-Halsa-Generation"] == world.store.current().generation_id
    finally:
        close(c)
    write_stock(world.folder, stock_rows([NEW_ROW, ["SP-778", "Sentetik Ek", 1, 0.1, 1.0, 3.0, 5, ""]]))
    APP.app.state.generations = world.store
    with monkeypatch.context() as mp:
        mp.setattr(world.store, "install", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic")))
        rep = RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY,
                             install=lambda s: APP._install_generation(world.repo, s))
    assert not rep["ok"] and rep["step"] == "swap"
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == b_body              # rolled back to B, still served
    finally:
        close(c)


def test_failed_republication_after_restart_withholds(world, empty_published, monkeypatch):
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(db, "build", broken)                         # the server cannot re-build the page
    c = restart(world, monkeypatch)
    try:
        r = c.get("/")
        assert APP.app.state.generations.current() is not None        # the generation itself re-validated …
        assert APP.app.state.generations.current().dashboard_sha256 is None
        withheld(r, "bytes_mismatch")                                # … but no bytes were published by this process
        assert Path(world.dash).read_bytes() == empty_published       # nothing half-written
    finally:
        close(c)


def test_restart_only_serves_bytes_published_by_this_process(world, empty_published, monkeypatch):
    c = restart(world, monkeypatch)
    try:
        r = c.get("/")
        m = APP.app.state.generations.current()
        on_disk = GEN.read_manifest(GEN.manifest_path(world.dash))
        assert r.status_code == 200 and GEN.sha256_bytes(r.content) == m.dashboard_sha256
        assert on_disk.dashboard_sha256 == m.dashboard_sha256 and on_disk.core_id == m.core_id
        s = GEN.GenerationStore()                                    # the re-published pair restores again
        assert s.restore(GEN.manifest_path(world.dash), world.dash, world.folder) is not None
    finally:
        close(c)


# 7 + 8 ------------------------------------------------------------------------------------------
def test_no_production_metric_is_verified_and_the_flag_stays_off():
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    ACC.validate_register()
    assert all(m.certified != ACC.State.VERIFIED for m in ACC.REGISTER.values())


def test_get_root_never_calls_an_ai_provider(world, empty_published, monkeypatch):
    c = live_client(world)
    try:
        APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
        assert c.get("/").status_code == 200
        assert c.get("/api/ai/calls").json()["calls"] == []
    finally:
        close(c)
    code = ("import sys, app; app._dashboard_authorized(b'', None);"
            "print(sorted(m for m in sys.modules if m.startswith(('openai', 'anthropic'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={**__import__("os").environ, "AI_PROVIDER": "disabled", "STOCKAGENT_NO_DOTENV": "1"})
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stderr
