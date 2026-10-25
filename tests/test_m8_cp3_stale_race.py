"""M8-CP3 review fix (D-067): a preview is checked against the SERVED version once more, atomically under
PUBLISH_LOCK, immediately before any selection file, configuration or dashboard byte changes.

Controlled concurrency (events, no sleeps as synchronisation): batch A is previewed against the served version, its
publication job starts and passes its early checks (files copied, owner workbooks imported, selection documents
planned), then an independent publication B — the owner edits the core selection and runs the controlled refresh —
changes what is served. When A reaches its commit it must refuse as stale, and B must stay exactly as published:
dashboard and manifest bytes, both selection files, data-folder bytes B names, context object, generation and
evidence binding, owner configs and views, finance states, all Veri Kontrol tables (append-only history included),
CRM / fair / action tables — also across a restart. Core-only and mixed core + owner batches; the window before the
refresh lock and the window inside it; and the identical-upload ("Değişiklik yoktu") path.

SYNTHETIC workbooks only; production accuracy gate; no metric is VERIFIED anywhere.
"""
import json
import os
import threading

import pandas as pd
import pytest

import app as APP
from services import accuracy as ACC
from services import generation as GEN
from services import refresh as RF
from services import request_pin as RP
from services import upload_center as UC
from tests.fixtures import fair_synth as FX
from tests.test_m8_cp3_atomicity import assert_restart_serves_the_same, mixed_batch, seed, surfaces
from tests.test_m8_cp3_upload_center import (H, c360_ready, prepared, publish, published_set, srv, validate,  # noqa: F401
                                             wait)

pytestmark = pytest.mark.production_gate

B_STOCK = "stok_b.xlsx"


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def core_only_batch(s):
    bid, asg = prepared(s, ("stock",), names={"stock": "stok2.xlsx"})
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready" and not b["preview"]["noop"], b["preview"]["errors"]
    return bid


def b_selection_doc(s):
    """B: the owner points the core selection at another stock export (a third synthetic variant)."""
    df = FX.stock_frame()
    df.loc[len(df)] = ["FS-003", "Sentetik üçüncü ürün", 7, 2.5, 1.0, 4.0, 310, ""]
    with pd.ExcelWriter(s.data / B_STOCK) as w:
        pd.DataFrame([["Sentetik stok"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        df.to_excel(w, index=False, startrow=3)
    doc = json.loads((s.data / GEN.SELECTION_FILE).read_text(encoding="utf-8"))
    doc["core"]["stock"] = {"file": B_STOCK}
    return doc


def publish_b(s):
    """An independent, complete publication B through the existing controlled refresh (another thread / request)."""
    GEN.write_json_atomic(str(s.data / GEN.SELECTION_FILE), b_selection_doc(s))
    rep = s.c.post("/api/context/refresh").json()
    assert rep["ok"], rep
    c360_ready(s)                                                       # B's customer view, before any snapshot
    return s.c.get("/api/accuracy/status").json()["core_id"]


def start_publication(s, bid):
    b = s.c.get(f"/api/uploads/{bid}").json()
    r = s.c.post(f"/api/uploads/{bid}/publish", json={"preview_token": b["preview_token"]})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


class Gate:
    """Stops the Upload Center publication job at one point until the test releases it."""

    def __init__(self):
        self.reached, self.go = threading.Event(), threading.Event()

    def stop(self):
        self.reached.set()
        assert self.go.wait(120), "gate never released"

    def wait_reached(self):
        assert self.reached.wait(120), "publication job never reached the gate"


def gate_before_refresh_lock(monkeypatch):
    """A has passed every early check, copied its files and imported its owner workbooks; it is about to enter the
    (long) controlled refresh. Only the upload job's call is gated; B's refresh passes through."""
    g, real = Gate(), RF.run_refresh

    def gated(*a, **kw):
        h = kw.get("hooks")
        if h is not None and not h.stage_only and h.before_swap is not None and not g.reached.is_set():
            g.stop()
        return real(*a, **kw)
    monkeypatch.setattr(RF, "run_refresh", gated)
    return g


def gate_at_final_commit(monkeypatch):
    """A holds the refresh lock and has built and verified its candidate; it is about to take PUBLISH_LOCK."""
    g, real = Gate(), RF._progress

    def gated(hooks, stage):
        real(hooks, stage)
        if stage == "publish_core" and hooks is not None and hooks.before_swap is not None and not g.reached.is_set():
            g.stop()
    monkeypatch.setattr(RF, "_progress", gated)
    return g


def assert_refused_stale_and_b_kept(s, pj, b_state):
    assert pj["state"] == "failed" and pj["batch"]["status"] == "stale", pj
    assert "güncel değil" in pj["batch"]["message"] and "Hiçbir şey yayınlanmadı" in pj["batch"]["message"]
    after = surfaces(s)
    for k in b_state:
        if k in ("data_folder", "source_bytes"):
            continue
        assert after[k] == b_state[k], k
    # the data folder: only A's own copies may have disappeared again; every file B serves is untouched
    named = (after["files"][GEN.SELECTION_FILE] or b"") + (after["files"][GEN.OWNER_SELECTION_FILE] or b"")
    gone = set(b_state["data_folder"]) - set(after["data_folder"])
    assert set(after["data_folder"]) <= set(b_state["data_folder"])
    assert all(n.startswith("yukleme-") and n.encode() not in named for n in gone), gone
    assert {k: v for k, v in b_state["source_bytes"].items() if k in after["source_bytes"]} == after["source_bytes"]
    assert B_STOCK in after["data_folder"] and B_STOCK.encode() in named
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    assert not [m for m in after["metrics"] if m["state"] == "verified"]
    with APP.app.state.uploads._lock, UC.ST._conn() as c:             # the refusal is in the append-only history
        ev = [(e["event"], json.loads(e["detail"] or "{}").get("code"))
              for e in c.execute("SELECT * FROM upload_event WHERE batch_id=? ORDER BY event_id", (pj["batch"]["batch_id"],))]
    assert ev[-1] == ("publication_failed", "stale") and ("published", None) not in ev


# ---------------------------------------------------------------------------------------------
# 1. B publishes while A is between its early checks and the refresh (the reviewed race)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["core_only", "mixed"])
def test_a_publication_overtaken_by_a_newer_one_is_refused_and_the_newer_one_kept(srv, monkeypatch, kind):
    s = srv
    seed(s)
    a_base = s.c.get("/api/accuracy/status").json()["core_id"]
    bid = core_only_batch(s) if kind == "core_only" else mixed_batch(s)
    gate = gate_before_refresh_lock(monkeypatch)
    job = start_publication(s, bid)
    gate.wait_reached()
    b_core = publish_b(s)                                              # B wins while A is still preparing
    assert b_core != a_base
    b_state = surfaces(s)
    gate.go.set()
    pj = wait(s, job)
    assert_refused_stale_and_b_kept(s, pj, b_state)
    assert s.c.get("/api/accuracy/status").json()["core_id"] == b_core
    assert_restart_serves_the_same(s, b_state)


def test_after_a_stale_refusal_a_fresh_preview_of_the_same_files_publishes(srv, monkeypatch):
    s = srv
    seed(s)
    bid = core_only_batch(s)
    gate = gate_before_refresh_lock(monkeypatch)
    job = start_publication(s, bid)
    gate.wait_reached()
    b_core = publish_b(s)
    gate.go.set()
    assert wait(s, job)["batch"]["status"] == "stale"
    bid2 = core_only_batch(s)                                          # validated against B now
    _, pj = publish(s, bid2)
    assert pj["batch"]["status"] == "published", pj
    st = s.c.get("/api/accuracy/status").json()
    assert st["core_id"] not in (b_core, None)
    assert all(x["bound"] for x in st["sources"] if x["key"] in ("stock", "sales", "aging"))
    assert not [m for m in st["metrics"] if m["state"] == "verified"]


# ---------------------------------------------------------------------------------------------
# 2. the selection changes while A already holds the refresh lock (the last window before the commit)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["core_only", "mixed"])
def test_a_selection_change_just_before_the_commit_is_refused_without_touching_anything(srv, monkeypatch, kind):
    s = srv
    seed(s)
    bid = core_only_batch(s) if kind == "core_only" else mixed_batch(s)
    gate = gate_at_final_commit(monkeypatch)
    job = start_publication(s, bid)
    gate.wait_reached()
    with RP.PUBLISH_LOCK:                                              # another writer changes the selection
        GEN.write_json_atomic(str(s.data / GEN.SELECTION_FILE), b_selection_doc(s))
    b_state = surfaces(s)
    gate.go.set()
    pj = wait(s, job)
    assert_refused_stale_and_b_kept(s, pj, b_state)


# ---------------------------------------------------------------------------------------------
# 3. the identical-upload path never reports an obsolete preview as "unchanged"
# ---------------------------------------------------------------------------------------------
def test_an_identical_upload_overtaken_by_a_newer_publication_is_stale_not_unchanged(srv, monkeypatch):
    s = srv
    published_set(s, slots=("stock", "sales", "aging", "kredi"))
    bid, asg = prepared(s, ("stock", "sales", "aging", "kredi"))
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready" and b["preview"]["noop"]
    g, real = Gate(), UC.UploadCenter._stage

    def gated(self, job_id, stage):
        real(self, job_id, stage)
        if stage == "checking" and not g.reached.is_set():
            g.stop()
    monkeypatch.setattr(UC.UploadCenter, "_stage", gated)
    job = start_publication(s, bid)
    g.wait_reached()
    b_core = publish_b(s)
    b_state = surfaces(s)
    g.go.set()
    pj = wait(s, job)
    assert_refused_stale_and_b_kept(s, pj, b_state)
    assert "Değişiklik yoktu" not in pj["batch"]["message"]
    assert s.c.get("/api/accuracy/status").json()["core_id"] == b_core


def test_an_identical_upload_without_a_newer_publication_is_still_unchanged(srv):
    s = srv
    published_set(s, slots=("stock", "sales", "aging", "kredi"))
    before = surfaces(s)
    bid, asg = prepared(s, ("stock", "sales", "aging", "kredi"))
    _, b = validate(s, bid, asg)
    assert b["preview"]["noop"]
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "unchanged" and "Değişiklik yoktu" in pj["batch"]["message"]
    after = surfaces(s)
    assert {k: after[k] for k in ("files", "generation", "vkm", "owner_configs")} == \
           {k: before[k] for k in ("files", "generation", "vkm", "owner_configs")}


# ---------------------------------------------------------------------------------------------
# 4. the refresh hook itself (no Upload Center): a refusal changes nothing and is never a swap failure
# ---------------------------------------------------------------------------------------------
def test_the_precheck_runs_under_the_publish_lock_before_anything_else(srv, monkeypatch):
    s = srv
    published_set(s)
    c360_ready(s)
    before = surfaces(s)
    seen = {}

    def precheck():
        seen["locked"] = RP.PUBLISH_LOCK._is_owned()
        return "eski önizleme"

    def must_not_run():
        raise AssertionError("before_swap ran after a refusal")
    rep = RF.run_refresh(str(s.data), s.dash, previous_context=APP.app.state.repo.live(),   # selection: sources.json
                         install=lambda staged: pytest.fail("installed after a refusal"),
                         hooks=RF.PublishHooks(precheck=precheck, before_swap=must_not_run))
    assert seen == {"locked": True}
    assert not rep["ok"] and rep["step"] == "stale" and rep["previous_data_kept"] is True
    assert rep["message"] == "eski önizleme"
    assert surfaces(s) == before
