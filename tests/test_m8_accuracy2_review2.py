"""M8-ACCURACY-2 — second review pass on PR #28 (two independently reproduced findings).

Finding 1: `--evidence-out` equal to (or aliasing) an input replaced the workbook with JSON. The reconciliation command
must never write over a selected workbook, the selection file or any existing file.

Finding 2: `GET /` served a previously generated dashboard with VERIFIED metric rows although the restart had rejected
its generation (source_changed). The exact HTML bytes must be authorized against the accepted, re-validated
generation and the current gate; pending, rejected, missing or mismatched evidence exposes no metric data.

REAL production gate. A metric is certified only on a synthetic register copy (fixture `promoted`). Synthetic data only.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as APP
from services import accuracy as ACC
from tests import release_state as REL
from services import generation as GEN
from services import refresh as RF
from services import request_pin as RP
from scripts import reconcile_sources as RS
from tests.fixtures import kredi_takip_synth as KT
from tests.test_m8_accuracy2 import (SE, TODAY, promoted, refresh, selection, stock_rows, world,  # noqa: F401
                                     write_stock)

pytestmark = pytest.mark.production_gate
ROOT = Path(__file__).resolve().parents[1]
NEW_ROW = ["SP-777", "Sentetik Yeni", 5, 0.2, 1.0, 3.0, 50, ""]


def sha(p):
    return GEN.sha256_file(str(p))


# =============================================================================================
# Finding 1 — reconciliation output never touches an input
# =============================================================================================
def _run(*args):
    return subprocess.run([sys.executable, "-m", "scripts.reconcile_sources", *args], cwd=ROOT, capture_output=True,
                          text=True)


@pytest.fixture
def kredi(tmp_path):
    p = KT.book(tmp_path / "k")
    return p, p.read_bytes()


@pytest.fixture
def core(tmp_path):
    d = SE.write_all(str(tmp_path / "core"))
    write_stock(d)
    selection(d)
    files = {n: (Path(d) / n).read_bytes() for n in os.listdir(d)}
    return Path(d), files


def _unchanged(core_dir, files):
    return {n: (core_dir / n).read_bytes() for n in os.listdir(core_dir)} == files


def test_evidence_out_equal_to_the_kredi_workbook_is_refused(kredi):
    p, before = kredi
    r = _run("--kredi", str(p), "--evidence-out", str(p))
    assert r.returncode == 2 and "kaynak dosyanın yerine yazılamaz" in r.stdout
    assert p.read_bytes() == before


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "dotdot", "symlinked_dir"])
def test_evidence_out_aliasing_the_kredi_workbook_is_refused(kredi, tmp_path, alias):
    p, before = kredi
    if alias == "symlink":
        dest = tmp_path / "evidence.json"
        os.symlink(p, dest)
    elif alias == "hardlink":
        dest = tmp_path / "evidence.json"
        os.link(p, dest)
    elif alias == "dotdot":
        dest = Path(str(p.parent / ".." / p.parent.name / p.name))
    else:
        (tmp_path / "via").symlink_to(p.parent, target_is_directory=True)
        dest = tmp_path / "via" / p.name
    r = _run("--kredi", str(p), "--evidence-out", str(dest))
    assert r.returncode == 2, r.stdout
    assert p.read_bytes() == before


@pytest.mark.parametrize("target", ["sources.json", SE.STOCK_FILE, SE.SALES_FILE, SE.AGING_FILE])
def test_evidence_out_never_replaces_a_core_input(core, target):
    d, files = core
    r = _run("--core", str(d), "--evidence-out", str(d / target))
    assert r.returncode == 2 and _unchanged(d, files), r.stdout


def test_evidence_out_symlink_to_the_selection_file_is_refused(core, tmp_path):
    d, files = core
    dest = tmp_path / "kanit.json"
    os.symlink(d / "sources.json", dest)
    r = _run("--core", str(d), "--evidence-out", str(dest))
    assert r.returncode == 2 and _unchanged(d, files)


def test_existing_destination_is_never_overwritten(kredi, tmp_path):
    p, before = kredi
    dest = tmp_path / "already.json"
    dest.write_text("owner notes", encoding="utf-8")
    r = _run("--kredi", str(p), "--evidence-out", str(dest))
    assert r.returncode == 2 and "zaten var" in r.stdout
    assert dest.read_text(encoding="utf-8") == "owner notes" and p.read_bytes() == before


def test_dangling_symlink_destination_is_never_followed(kredi, tmp_path):
    p, before = kredi
    target = tmp_path / "elsewhere.json"
    dest = tmp_path / "dangling.json"
    os.symlink(target, dest)
    r = _run("--kredi", str(p), "--evidence-out", str(dest))
    assert r.returncode == 2 and not target.exists() and p.read_bytes() == before


def test_write_failure_is_reported_and_leaves_nothing(kredi, tmp_path):
    p, before = kredi
    dest = tmp_path / "missing_dir" / "kanit.json"                    # parent does not exist (not auto-created)
    r = _run("--kredi", str(p), "--today", "2026-10-19", "--evidence-out", str(dest))
    assert r.returncode == 4 and "KANIT DOSYASI YAZILAMADI" in r.stdout and "GENEL SONUÇ" in r.stdout
    assert not dest.exists() and p.read_bytes() == before


def test_failure_while_writing_removes_the_partial_file(kredi, tmp_path, monkeypatch, capsys):
    p, before = kredi
    dest = tmp_path / "kanit.json"

    def boom(*a, **k):
        a[1].write('{"partial": ')
        raise OSError("disk full")
    monkeypatch.setattr(RS.json, "dump", boom)
    code = RS.main(["--kredi", str(p), "--today", "2026-10-19", "--evidence-out", str(dest)])
    assert code == 4 and "yazma hatası" in capsys.readouterr().out
    assert not dest.exists() and p.read_bytes() == before


def test_exclusive_create_cannot_truncate_a_file_that_appears_after_the_check(kredi, tmp_path, monkeypatch):
    p, before = kredi
    dest = tmp_path / "late.json"
    real = RS._evidence_clash
    monkeypatch.setattr(RS, "_evidence_clash", lambda path, prot: None)      # the checks are bypassed …
    os.link(p, dest)                                                          # … and an alias appears
    err = RS._write_evidence_exclusive(str(dest), {"x": 1}, [])
    assert err and p.read_bytes() == before and dest.read_bytes() == before   # O_EXCL refused it
    monkeypatch.setattr(RS, "_evidence_clash", real)


def test_private_destination_rules_still_hold(kredi):
    p, before = kredi
    assert _run("--kredi", str(p), "--evidence-out", str(ROOT / "kanit.json")).returncode == 2
    assert not (ROOT / "kanit.json").exists() and p.read_bytes() == before


def test_reconciliation_never_writes_inputs_in_a_successful_run(core, kredi, tmp_path):
    d, files = core
    p, before = kredi
    dest = tmp_path / "ok.json"
    r = _run("--kredi", str(p), "--core", str(d), "--today", TODAY.isoformat(), "--evidence-out", str(dest))
    assert r.returncode in (0, 1, 3) and dest.exists(), r.stdout
    assert oct(dest.stat().st_mode & 0o777) == "0o600"
    assert _unchanged(d, files) and p.read_bytes() == before
    assert json.loads(dest.read_text(encoding="utf-8"))["overall"] in ("PASS", "OPEN", "FAIL")


# =============================================================================================
# Finding 2 — GET / is authorized against the accepted generation, from the exact bytes served
# =============================================================================================
def data_of(text):
    m = re.search(r"const DATA=(\{.*?\});\n", text, re.S)
    return json.loads(m.group(1).replace("<\\/", "</")) if m else None


def displayed_stock_count(text):
    # Real rendered data independent of the retired reorder algorithm.
    match = re.search(r" · ([0-9,]+) ürün</div>", text)
    return int(match.group(1).replace(",", "")) if match else None


def live_client(w):
    """In-process (no restart): the app serves the world's store and dashboard."""
    c = TestClient(APP.app, headers={"X-StockAgent": "1"})
    c.__enter__()
    APP.app.state.generations = w.store
    APP.app.dependency_overrides[APP.get_repo] = lambda: w.repo
    return c


def restart(w, monkeypatch):
    """A real restart: the app's own startup restores from the world's dashboard + manifest + data folder."""
    monkeypatch.setattr(APP, "DASHBOARD_PATH", w.dash)
    monkeypatch.setattr(APP, "DATA_FOLDER", w.folder)
    monkeypatch.setattr(APP, "WARMUP_CONTEXT", False)
    APP.app.dependency_overrides.clear()
    c = TestClient(APP.app, headers={"X-StockAgent": "1"})
    c.__enter__()
    return c


def warm_up():
    APP.app.state.repo.context()                                     # what the warm-up thread does


def close(c):
    c.__exit__(None, None, None)
    APP.app.dependency_overrides.clear()


@pytest.fixture
def published(world, promoted, monkeypatch):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    monkeypatch.setattr(APP, "DASHBOARD_PATH", world.dash)
    body = Path(world.dash).read_bytes()
    assert displayed_stock_count(body.decode()), "synthetic VERIFIED metric is embedded"
    return {"body": body, "m": world.store.current()}


def assert_withheld(r):
    assert r.status_code == 503
    assert displayed_stock_count(r.text) is None and data_of(r.text) is None
    assert "Sentetik" not in r.text


def test_published_dashboard_is_served_with_its_generation(world, published):
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == published["body"]
        assert r.headers["X-Halsa-Generation"] == published["m"].generation_id
        assert displayed_stock_count(r.text)
        assert c.get("/api/agent/actions").status_code in (200, ACC.WITHHELD_HTTP_STATUS)   # API still gated
    finally:
        close(c)


def test_reproduction_source_changed_after_restart_is_withheld(world, published, monkeypatch):
    write_stock(world.folder, stock_rows([NEW_ROW]))                   # the stock workbook changes
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "source_changed"
        r = c.get("/")
        assert_withheld(r) or None
        assert r.headers["X-Halsa-Dashboard"] == "no_accepted_generation"
        warm_up()
        assert_withheld(c.get("/"))                                    # still withheld after the rebuild
        assert c.get("/api/context").status_code == ACC.WITHHELD_HTTP_STATUS
        st = {m["key"]: m for m in c.get("/api/accuracy/status").json()["metrics"]}["stock.reorder"]
        assert st["state"] != "verified"
        assert Path(world.dash).read_bytes() == published["body"]      # nothing was repaired or rewritten
    finally:
        close(c)


def test_pending_revalidation_then_successful_restore(world, published, monkeypatch):
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "pending_revalidation"
        assert APP.app.state.generations.current() is None             # a candidate gives no evidence …
        r = c.get("/")                                                 # … GET / waits for the re-derivation
        assert APP.app.state.generations.restore_reason is None
        m = APP.app.state.generations.current()
        # served: the dashboard RE-BUILT by this process from the re-derived evidence (never the pre-restart file)
        assert r.status_code == 200 and GEN.sha256_bytes(r.content) == m.dashboard_sha256
        assert r.content == Path(world.dash).read_bytes()
        assert displayed_stock_count(r.text) == displayed_stock_count(published["body"].decode())
        assert r.headers["X-Halsa-Generation"] == m.generation_id and m.core_id == published["m"].core_id
    finally:
        close(c)


def test_corrupted_manifest_is_withheld(world, published, monkeypatch):
    mp = GEN.manifest_path(world.dash)
    d = json.load(open(mp, encoding="utf-8"))
    next(r for r in d["core"] if r["key"] == "stock")["evidence"]["totals_consistent"] = False
    json.dump(d, open(mp, "w", encoding="utf-8"))
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "no_manifest_or_corrupted"
        assert_withheld(c.get("/"))
        warm_up()
        assert_withheld(c.get("/"))
    finally:
        close(c)


def _reseal(d):
    m = GEN.Manifest(created_at=d["created_at"], core=tuple(GEN.SourceRecord.from_dict(x) for x in d["core"]),
                     owner=(), joins=d["joins"], selection_origin=d["selection_origin"],
                     policy_version=d["policy_version"], dashboard_sha256=d.get("dashboard_sha256"))
    return m.to_dict()


def test_resealed_manifest_is_withheld(world, published, monkeypatch):
    mp = GEN.manifest_path(world.dash)
    d = json.load(open(mp, encoding="utf-8"))
    ev = next(r for r in d["core"] if r["key"] == "stock")["evidence"]
    ev["error_cells"] = (ev["error_cells"] or 0) + 5
    d["joins"]["sold_products_not_in_stock"] = (d["joins"].get("sold_products_not_in_stock") or 0) + 1
    sealed = _reseal(d)
    assert sealed["core_id"] != published["m"].core_id
    json.dump(sealed, open(mp, "w", encoding="utf-8"))
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "dashboard_generation_mismatch"
        warm_up()
        assert_withheld(c.get("/"))
    finally:
        close(c)


def test_evidence_mismatch_after_resealing_manifest_and_dashboard_is_withheld(world, published, monkeypatch):
    mp = GEN.manifest_path(world.dash)
    d = json.load(open(mp, encoding="utf-8"))
    next(r for r in d["core"] if r["key"] == "stock")["validation"]["error_cells"] = 99
    core_id = _reseal(d)["core_id"]
    html = Path(world.dash).read_text(encoding="utf-8").replace(published["m"].core_id, core_id)
    Path(world.dash).write_text(html, encoding="utf-8")
    d["dashboard_sha256"] = sha(world.dash)                            # the editor re-records the HTML digest too
    json.dump(_reseal(d), open(mp, "w", encoding="utf-8"))
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "pending_revalidation"
        assert_withheld(c.get("/"))
        warm_up()
        assert APP.app.state.generations.restore_reason == "evidence_mismatch"
        assert_withheld(c.get("/"))
    finally:
        close(c)


def test_edited_dashboard_bytes_are_withheld_live_and_after_restart(world, published, monkeypatch):
    html = Path(world.dash).read_text(encoding="utf-8")
    Path(world.dash).write_text(html.replace("Şirket Panosu", "Sirket Panosu", 1), encoding="utf-8")
    c = live_client(world)
    try:
        assert_withheld(c.get("/"))                                    # markers intact, bytes not the published ones
    finally:
        close(c)
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "dashboard_bytes_mismatch"
        warm_up()
        assert_withheld(c.get("/"))
    finally:
        close(c)


def test_failed_publication_rolls_back_to_the_served_dashboard(world, published, monkeypatch):
    write_stock(world.folder, stock_rows([NEW_ROW]))
    APP.app.state.generations = world.store
    with monkeypatch.context() as mp:                     # scoped: the DB isolation of the fixtures stays in place
        mp.setattr(world.store, "install", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic")))
        rep = RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY,
                             install=lambda s: APP._install_generation(world.repo, s))
    assert not rep["ok"] and rep["step"] == "swap"
    c = live_client(world)
    try:
        r = c.get("/")
        assert r.status_code == 200 and r.content == published["body"]
        assert r.headers["X-Halsa-Generation"] == published["m"].generation_id
    finally:
        close(c)


class Crash(BaseException):
    pass


def test_crash_between_file_swaps_is_withheld_after_restart(world, published, monkeypatch):
    write_stock(world.folder, stock_rows([NEW_ROW]))
    monkeypatch.setattr(RF, "_after_manifest_swap", lambda: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY, install=world.install)
    monkeypatch.setattr(RF, "_after_manifest_swap", lambda: None)
    c = restart(world, monkeypatch)
    try:
        assert APP.app.state.generations.restore_reason == "dashboard_generation_mismatch"
        warm_up()
        assert_withheld(c.get("/"))
    finally:
        close(c)


def test_display_authorization_is_re_evaluated_when_served(world, promoted, monkeypatch):
    """Built while the stock snapshot was fresh enough; served after the operational age limit has passed."""
    built = TODAY - dt.timedelta(days=10)
    selection(world.folder, stock_date=TODAY - dt.timedelta(days=50))
    assert refresh(world.folder, world.dash, install=world.install, today=built)["ok"]
    assert displayed_stock_count(Path(world.dash).read_text(encoding="utf-8"))          # displayed at build time (40 days)
    monkeypatch.setattr(APP, "DASHBOARD_PATH", world.dash)
    c = live_client(world)
    try:
        r = c.get("/")                                                          # today: 50 days > 45
        assert_withheld(r) or None
        assert r.headers["X-Halsa-Dashboard"] == "not_authorized"
    finally:
        close(c)


def test_get_root_waits_for_publication_and_serves_one_consistent_generation(world, published, monkeypatch):
    write_stock(world.folder, stock_rows([NEW_ROW]))
    c = live_client(world)
    got, started = {}, threading.Event()

    def reader():
        started.set()
        got["r"] = c.get("/")

    def mid_publication():
        t = threading.Thread(target=reader)
        t.start()
        started.wait(5)
        t.join(0.5)
        got["blocked"] = t.is_alive()                                    # waiting for the publish lock
        got["t"] = t
    monkeypatch.setattr(RF, "_after_manifest_swap", mid_publication)
    try:
        rep = RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY,
                             install=lambda s: APP._install_generation(world.repo, s))
        assert rep["ok"]
        got["t"].join(30)
        r = got["r"]
        assert got["blocked"] is True
        new = world.store.current()
        assert r.content == Path(world.dash).read_bytes() and new.dashboard_sha256 == GEN.sha256_bytes(r.content)
        assert r.status_code == 200 and r.headers["X-Halsa-Generation"] == new.generation_id
        assert displayed_stock_count(r.text) is None        # B's approval is missing → B's HTML embeds no reorder rows
    finally:
        close(c)


def test_production_register_dashboard_without_accepted_generation_is_not_served(world, monkeypatch):
    """The real register displays nothing (empty marker). That claim of the file is NOT evidence: after the generation
    is rejected (source changed + restart) the on-disk page is not served (third review of PR #28)."""
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    body = Path(world.dash).read_bytes()
    gen, shown = GEN.dashboard_markers(body)
    assert shown == () and gen == world.store.current().core_id
    REL.assert_release_state()                    # D-073: one certified metric, flag True
    assert REL.certified_keys() == list(REL.CERTIFIED)
    write_stock(world.folder, stock_rows([NEW_ROW]))
    c = restart(world, monkeypatch)
    try:
        r = c.get("/")
        assert r.status_code == 503 and r.content != body and data_of(r.text) is None
        assert r.headers["X-Halsa-Dashboard"] == "no_accepted_generation"
    finally:
        close(c)


def test_dashboard_markers_and_old_dashboards():
    b = (b'<meta name="stockagent-generation" content="ab12"><meta name="stockagent-displayed" '
         b'content="stock.reorder,stock.overstock">')
    assert GEN.dashboard_markers(b) == ("ab12", ("stock.reorder", "stock.overstock"))
    assert GEN.dashboard_markers(b'<meta name="stockagent-generation" content="ab12">') == ("ab12", None)
    assert APP._dashboard_authorized(b'<meta name="stockagent-generation" content="">', None)[:2] == \
        (False, "no_accepted_generation")


def test_request_pinning_and_publish_lock_still_in_place():
    import inspect
    assert "RP.begin()" in inspect.getsource(APP._accuracy_gate)
    assert "PUBLISH_LOCK" in inspect.getsource(RF._pipeline)
    assert "PUBLISH_LOCK" in inspect.getsource(APP.dashboard)
    assert RP.PUBLISH_LOCK is not None
