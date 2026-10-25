"""M8-CP3 review fix (D-066): a publication of a mixed core + owner batch is ALL-OR-NOTHING.

Failure injection at every step of the commit — the first and the second selection-file write, the owner
configuration staging, the owner workbook installation before anything is published and after a partial installation
(one record published, the next one failing), on the core + owner path and on the owner-only path. After every
injected failure ALL published surfaces must be exactly as before (dashboard and manifest bytes, both selection files,
data-folder listing, live context object, served generation and evidence binding, owner configuration and views,
finance views, Veri Kontrol tables incl. their append-only history, CRM and fair state), and a restart must serve the
same generation. A later clean publication must then succeed and be observed normally.

SYNTHETIC workbooks only; production accuracy gate; no metric is VERIFIED anywhere.
"""
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as APP
from services import accuracy as ACC
from services import generation as GEN
from services import owner_view as OV
from services import store as ST
from tests.fixtures import fair_synth as FX
from tests.test_m8_cp3_upload_center import (H, c360_ready, live_files, prepared, publish, published_set,  # noqa: F401
                                             srv, validate)

pytestmark = pytest.mark.production_gate

CODE = FX.P[1]


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def seed(s):
    """A published base (core + Kredi), then CRM, a fair campaign and a VKM review decision."""
    published_set(s, slots=("stock", "sales", "aging", "kredi"))
    c360_ready(s)
    assert s.c.post(f"/api/crm/customers/{CODE}/interactions",
                    json={"occurred_on": "2026-06-20", "channel": "phone", "note": "Sentetik CRM notu"}).status_code \
        in (200, 201)
    assert s.c.post("/api/fairs/campaigns", json={"name": "Sentetik Kampanya", "edition_year": 2027,
                                                  "start_date": "2027-01-15"}).status_code in (200, 201)
    issues = s.c.get("/api/vkm/issues").json()
    r = s.c.post(f"/api/vkm/issues/{issues['issues'][0]['issue_id']}/review",
                 json={"action": "incelendi", "note": "Sentetik inceleme", "vkm_generation": issues["vkm_generation"]})
    assert r.status_code == 200
    s.c.get("/api/finance/overview")                       # the lazy owner load runs (and finishes) first
    APP.app.state.owner.ensure_loaded()
    _wait_owner_loaded()


def _wait_owner_loaded(timeout=30):
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not APP.app.state.owner.inflight:
            return
        time.sleep(0.05)
    pytest.fail("owner sources did not finish loading")


def _tree_digest(root):
    import hashlib
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            if n.endswith((".db", ".db-wal", ".db-shm", ".db-journal")):
                continue
            p = os.path.join(d, n)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def surfaces(s):
    """Every surface a publication can change (bytes, identities, states, history) — values never printed."""
    st = s.c.get("/api/accuracy/status").json()
    page = s.c.get("/")
    owner = APP.app.state.owner
    with sqlite3.connect(ST.DB_PATH) as c:
        vkm = {t: c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
               for t in ("dq_issue", "dq_issue_event", "dq_observation", "dq_source_state")}
        private = {t: c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()       # CRM, fairs, actions, notes
                   for t in ("crm_followups", "actions", "action_history", "memory",
                             "fair_campaigns", "fair_customers", "fair_history")}
    return {
        "files": live_files(s),
        "data_folder": sorted(n for n in os.listdir(s.data) if n != "uploads"),
        "source_bytes": _tree_digest(s.data),                       # data-folder sources AND stored uploads
        "private_tables": private,
        "generation": (st["generation_id"], st["core_id"], st["selection_origin"],
                       sorted((x["key"], x["sha256"], x["bound"], x["selection_origin"]) for x in st["sources"])),
        "metrics": st["metrics"],
        "page": (page.status_code, page.headers.get("X-Halsa-Generation"), page.text),
        "context_id": id(APP.app.state.repo.live()),
        "owner_configs": owner.configs(),
        "owner_views": {k: (e["state"], e.get("fingerprint"), id(e.get("view"))) for k, e in owner.snapshot().items()},
        "finance": {k: v.get("state") for k, v in s.c.get("/api/finance/overview").json().items()
                    if isinstance(v, dict) and "state" in v},
        "vkm": vkm,
        "vkm_sync": s.c.get("/api/vkm/issues").json()["sync"]["state"],
        "crm": json.dumps(s.c.get(f"/api/crm/customers/{CODE}").json(), sort_keys=True, ensure_ascii=False),
        "fairs": json.dumps(s.c.get("/api/fairs/campaigns").json()["items"], sort_keys=True, ensure_ascii=False),
    }


def mixed_batch(s, owner_slots=("gider_gelir",)):
    decl = {"gider_gelir": {"report_year": "2026"}, "butce": {"planning_year": "2026"}}
    bid, asg = prepared(s, ("stock", *owner_slots), names={"stock": "stok2.xlsx"}, decl=decl)
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready", b["preview"]["errors"]
    return bid


def owner_only_batch(s, owner_slots=("gider_gelir", "butce")):
    decl = {"gider_gelir": {"report_year": "2026"}, "butce": {"planning_year": "2026"}}
    bid, asg = prepared(s, owner_slots, decl=decl)
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready", b["preview"]["errors"]
    return bid


def assert_failed_and_unchanged(s, before, pj):
    assert pj["state"] == "failed" and pj["batch"]["status"] == "publish_failed", pj
    assert "önceki" in pj["message"].lower()                      # the owner is told the previous data is kept
    after = surfaces(s)
    for k in before:
        assert after[k] == before[k], k
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    assert not [m for m in after["metrics"] if m["state"] == "verified"]


def assert_restart_serves_the_same(s, before):
    with TestClient(APP.app, base_url="https://testserver", headers=H) as c2:           # a new startup
        APP.app.state.repo.context()
        st = c2.get("/api/accuracy/status").json()
        assert st["restore_reason"] is None and not st["restore_pending"]
        assert st["core_id"] == before["generation"][1]
        assert c2.get("/").status_code == 200
        assert APP.app.state.owner.configs() == before["owner_configs"]
        assert c2.get("/api/vkm/issues").json()["sync"]["state"] == "in_sync"


class Boom(RuntimeError):
    pass


@pytest.fixture
def inject():
    """Failure injection on its own MonkeyPatch: undoing it must not undo the fixture's data-folder patches."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


# ---------------------------------------------------------------------------------------------
# 1. selection files and owner configuration (before the dashboard swap)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("which", [GEN.OWNER_SELECTION_FILE, GEN.SELECTION_FILE])
def test_a_failing_selection_write_restores_everything(srv, inject, which):
    s = srv
    seed(s)
    bid = mixed_batch(s)
    before = surfaces(s)
    real = GEN.write_json_atomic
    calls = []

    def flaky(path, doc):
        calls.append(os.path.basename(path))
        if os.path.basename(path) == which:
            raise Boom("disk dolu")
        return real(path, doc)
    inject.setattr(GEN, "write_json_atomic", flaky)
    _, pj = publish(s, bid)
    if which == GEN.OWNER_SELECTION_FILE:
        assert calls == [GEN.SELECTION_FILE, GEN.OWNER_SELECTION_FILE]   # sources.json WAS rewritten, then restored
    assert_failed_and_unchanged(s, before, pj)
    inject.undo()
    assert_restart_serves_the_same(s, before)


def test_a_half_written_owner_selection_is_restored_byte_for_byte(srv, inject):
    """The second write fails AFTER replacing the file (worst case for a non-atomic writer)."""
    s = srv
    seed(s)
    bid = mixed_batch(s)
    before = surfaces(s)
    real = GEN.write_json_atomic

    def torn(path, doc):
        if os.path.basename(path) == GEN.OWNER_SELECTION_FILE:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"schema": "halsa-owner-selection/1", "owner": {"kre')
            raise Boom("yarım yazıldı")
        return real(path, doc)
    inject.setattr(GEN, "write_json_atomic", torn)
    _, pj = publish(s, bid)
    assert_failed_and_unchanged(s, before, pj)
    inject.undo()
    assert_restart_serves_the_same(s, before)


def test_a_failing_owner_configuration_stage_restores_everything(srv, inject):
    s = srv
    seed(s)
    bid = mixed_batch(s)
    before = surfaces(s)
    inject.setattr(OV.OwnerRepository, "stage_configs",
                        lambda self, changes: (_ for _ in ()).throw(Boom("yapılandırma")))
    _, pj = publish(s, bid)
    assert_failed_and_unchanged(s, before, pj)
    inject.undo()
    assert_restart_serves_the_same(s, before)


# ---------------------------------------------------------------------------------------------
# 2. owner workbook installation AFTER the core dashboard / context / generation changed
# ---------------------------------------------------------------------------------------------
def test_owner_install_failing_before_any_entry_is_published_undoes_the_core_publication(srv, inject):
    s = srv
    seed(s)
    bid = mixed_batch(s)
    before = surfaces(s)
    seen = {}
    real_install = APP.app.state.uploads.deps.install

    def install_and_watch(staged):
        real_install(staged)
        seen["core_installed"] = APP.app.state.generations.current().core_id     # the core really changed first
    inject.setattr(APP.app.state.uploads.deps, "install", install_and_watch)
    inject.setattr(OV.OwnerRepository, "install_entries",
                        lambda self, entries: (_ for _ in ()).throw(Boom("kurulamadı")))
    _, pj = publish(s, bid)
    assert seen["core_installed"] != before["generation"][1]
    assert_failed_and_unchanged(s, before, pj)
    assert "Yayın tamamlanamadı" in pj["message"]
    inject.undo()
    assert_restart_serves_the_same(s, before)


def _fail_on_nth_record(inject, n):
    gens = APP.app.state.generations
    real = gens.replace_owner
    calls = {"n": 0}

    def flaky(key, record):
        calls["n"] += 1
        if calls["n"] == n:
            raise Boom("kayıt yayınlanamadı")
        return real(key, record)
    inject.setattr(gens, "replace_owner", flaky)
    return calls


def test_a_partially_installed_mixed_batch_is_fully_undone(srv, inject):
    s = srv
    seed(s)
    bid = mixed_batch(s, ("gider_gelir", "butce"))
    before = surfaces(s)
    calls = _fail_on_nth_record(inject, 2)                    # the first owner record IS published
    _, pj = publish(s, bid)
    assert calls["n"] == 2
    assert_failed_and_unchanged(s, before, pj)
    inject.undo()
    assert_restart_serves_the_same(s, before)


def test_owner_only_batch_partial_installation_is_fully_undone(srv, inject):
    s = srv
    seed(s)
    bid = owner_only_batch(s)
    before = surfaces(s)
    calls = _fail_on_nth_record(inject, 2)
    _, pj = publish(s, bid)
    assert calls["n"] == 2
    assert_failed_and_unchanged(s, before, pj)
    inject.undo()
    assert_restart_serves_the_same(s, before)


def test_owner_only_batch_selection_failure_is_fully_undone(srv, inject):
    s = srv
    seed(s)
    bid = owner_only_batch(s)
    before = surfaces(s)
    inject.setattr(OV.OwnerRepository, "stage_configs",
                        lambda self, changes: (_ for _ in ()).throw(Boom("yapılandırma")))
    _, pj = publish(s, bid)
    assert_failed_and_unchanged(s, before, pj)
    inject.undo()
    assert_restart_serves_the_same(s, before)


# ---------------------------------------------------------------------------------------------
# 3. after a rolled-back attempt, a clean publication commits and is observed normally
# ---------------------------------------------------------------------------------------------
def test_a_clean_publication_after_a_rollback_commits_everything_and_is_observed(srv, inject):
    s = srv
    seed(s)
    bid = mixed_batch(s, ("gider_gelir", "butce"))
    _fail_on_nth_record(inject, 1)
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "publish_failed"
    inject.undo()
    with sqlite3.connect(ST.DB_PATH) as c:
        obs_before = c.execute("SELECT COUNT(*) FROM dq_observation").fetchone()[0]
        events_before = c.execute("SELECT COUNT(*) FROM dq_issue_event").fetchone()[0]
    bid = mixed_batch(s, ("gider_gelir", "butce"))
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "published"
    st = s.c.get("/api/accuracy/status").json()
    srcs = {x["key"]: x for x in st["sources"]}
    assert all(srcs[k]["bound"] for k in ("stock", "sales", "aging", "kredi", "gider_gelir", "butce"))
    owner = APP.app.state.owner.snapshot()
    assert {k: owner[k]["state"] for k in ("kredi", "gider_gelir", "butce")} == {k: OV.READY for k in
                                                                               ("kredi", "gider_gelir", "butce")}
    assert all(owner[k]["fingerprint"] == srcs[k]["sha256"] for k in ("kredi", "gider_gelir", "butce"))
    with sqlite3.connect(ST.DB_PATH) as c:                         # observed once, after the commit
        assert c.execute("SELECT COUNT(*) FROM dq_observation").fetchone()[0] > obs_before
        assert c.execute("SELECT COUNT(*) FROM dq_issue_event").fetchone()[0] >= events_before
    assert s.c.get("/api/vkm/issues").json()["sync"]["state"] == "in_sync"
    c360_ready(s)                                                   # the new generation's customer view
    assert "Sentetik CRM notu" in surfaces(s)["crm"] and "Sentetik Kampanya" in surfaces(s)["fairs"]
    assert not [m for m in st["metrics"] if m["state"] == "verified"]


def test_held_notifications_belong_to_the_publishing_thread_only():
    import threading
    store = GEN.GenerationStore()
    got = []
    store.add_observer(lambda kind, m, key, st: got.append((kind, key, threading.get_ident())))
    rec = GEN.SourceRecord("kredi", "owner", "loan_installments", "installment_schedule_snapshot", "x.xlsx", "a" * 64,
                           1, "upload")
    store.hold_notifications()
    store.replace_owner("kredi", rec)                               # held (this thread)
    t = threading.Thread(target=lambda: store.replace_owner("butce", None))
    t.start()
    t.join()                                                        # another thread: delivered at once
    assert [g[1] for g in got] == ["butce"]
    store.release_notifications(deliver=False)                      # rollback: the held one is dropped
    assert [g[1] for g in got] == ["butce"]
    store.hold_notifications()
    store.replace_owner("kredi", rec)
    store.release_notifications(deliver=True)                       # commit: delivered in order
    assert [g[1] for g in got] == ["butce", "kredi"]
    snap = store.snapshot_state()
    store.replace_owner("kredi", None)
    store.restore_state(snap)
    assert store.current().record("kredi") is not None and [g[1] for g in got][-1] == "kredi"
