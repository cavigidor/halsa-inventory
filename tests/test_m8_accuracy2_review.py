"""M8-ACCURACY-2 review corrections (independent review of PR #28).

Issue 1 — request-level generation consistency: the evidence that authorizes a response must belong to the exact data
in that response, even when a refresh, a failed refresh (rollback) or an owner reload happens between the accuracy
middleware's decision and the endpoint's reads, and under simultaneous requests.

Issue 2 — persisted evidence integrity: an edited or corrupted `*.generation.json` (source fingerprints unchanged)
must never be restored as trusted evidence; restart re-derives the evaluation from the original workbook bytes.

REAL production gate (marker `production_gate`). A metric is certified ONLY on a synthetic register copy inside these
tests; the production register certifies nothing. Synthetic workbooks only; no network, no AI.
"""
import dataclasses
import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as APP
from services import accuracy as ACC
from services import generation as GEN
from services import refresh as RF
from services import request_pin as RP
from tests.test_m8_accuracy2 import (SE, S, TODAY, World, promoted, selection, stock_rows, world,  # noqa: F401
                                     write_stock)

pytestmark = pytest.mark.production_gate
NEW_ROW = ["SP-777", "Sentetik Yeni", 5, 0.2, 1.0, 3.0, 50, ""]          # a new low-stock product → generation B


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def promoted_context(promoted, monkeypatch):
    """Synthetic register copy: `/api/context` (context.full) certified on the stock source alone, so a response
    can be VERIFIED and the test can see WHICH generation's data it carries."""
    reg = dict(ACC.REGISTER)
    reg["context.full"] = dataclasses.replace(
        reg["context.full"], sources=("stock",), depends_on=(), blockers=(), definitions=(), certified=S,
        temporal="operational", max_source_age_days=45, approval_refs=("D-999",),
        regression_tests=("tests/test_m8_accuracy2_review.py",), reconciliation_ref="synthetic")
    ACC.validate_register(reg, production=False)
    monkeypatch.setattr(ACC, "REGISTER", reg)
    return reg


def publish(w):
    APP.app.state.generations = w.store
    rep = RF.run_refresh(w.folder, w.dash, inflation_rate=0.40, today=TODAY, previous_context=w.repo.live(),
                         install=lambda staged: APP._install_generation(w.repo, staged))
    assert rep["ok"], rep
    return w.store.current()


def product_count(ctx):
    return ctx['company']['products_scanned']  # independent descriptive value changes with the source revision


@pytest.fixture
def two_generations(world, promoted_context):
    """Generation A (approved, VERIFIED) is published; generation B's stock bytes are prepared (B is a different
    workbook revision whose fingerprint the approval does not name, so B can never be VERIFIED)."""
    path = Path(world.folder) / SE.STOCK_FILE
    a_bytes = path.read_bytes()
    write_stock(world.folder, stock_rows([NEW_ROW]))
    b_bytes = path.read_bytes()
    path.write_bytes(a_bytes)
    selection(world.folder)                                          # approval bound to A's fingerprint
    gen_a = publish(world)
    ctx_a = world.repo.live()
    return {"path": path, "a": a_bytes, "b": b_bytes, "gen_a": gen_a, "ctx_a": ctx_a}


class Interleave:
    """Dependency override for `get_repo`: the middleware's gate capture is call 1; the endpoint's dependency is
    call 2 — `action()` runs there, i.e. strictly AFTER authorization and BEFORE the endpoint reads its data."""

    def __init__(self, repo, action, at=2):
        self.repo, self.action, self.at, self.n, self.fired = repo, action, at, 0, 0

    def __call__(self):
        self.n += 1
        if self.n == self.at:
            self.fired += 1
            self.action()
        return self.repo


def client_with(w, dep):
    c = TestClient(APP.app, headers={"X-StockAgent": "1"})
    c.__enter__()
    APP.app.state.generations = w.store
    APP.app.dependency_overrides[APP.get_repo] = dep
    return c


def refresh_to(w, data, *, fail_install=False, monkeypatch=None):
    def act():
        Path(w.folder, SE.STOCK_FILE).write_bytes(data)
        if fail_install:
            with monkeypatch.context() as mp:             # scoped: never undo the fixtures' DB isolation
                mp.setattr(w.store, "install", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic")))
                rep = RF.run_refresh(w.folder, w.dash, inflation_rate=0.40, today=TODAY,
                                     previous_context=w.repo.live(),
                                     install=lambda staged: APP._install_generation(w.repo, staged))
            assert not rep["ok"] and rep["step"] == "swap"
        else:
            rep = RF.run_refresh(w.folder, w.dash, inflation_rate=0.40, today=TODAY, previous_context=w.repo.live(),
                                 install=lambda staged: APP._install_generation(w.repo, staged))
            assert rep["ok"], rep
    return act


# ---------------------------------------------------------------------------------------------
# Issue 1 — request-level consistency
# ---------------------------------------------------------------------------------------------
def test_refresh_between_authorization_and_read_returns_the_authorized_generation(world, two_generations):
    g = two_generations
    dep = Interleave(world.repo, refresh_to(world, g["b"]))
    c = client_with(world, dep)
    try:
        r = c.get("/api/context")
        assert dep.fired == 1                                         # B was published mid-request
        gen_b = world.store.current()
        assert gen_b.core_id != g["gen_a"].core_id and world.repo.live() is not g["ctx_a"]
        assert product_count(world.repo.live()) != product_count(g["ctx_a"])   # A and B really differ
        assert r.status_code == 200
        assert r.headers["X-Halsa-Generation"] == g["gen_a"].generation_id
        assert product_count(r.json()["data"]) == product_count(g["ctx_a"])    # A's data, authorized by A's evidence
        # the next request sees B, which is not verified → withheld, and says so
        APP.app.dependency_overrides[APP.get_repo] = lambda: world.repo
        r2 = c.get("/api/context")
        assert r2.status_code == ACC.WITHHELD_HTTP_STATUS
        assert r2.headers["X-Halsa-Generation"] == gen_b.generation_id
    finally:
        c.__exit__(None, None, None)
        APP.app.dependency_overrides.clear()


def test_negative_control_without_pinning_the_race_is_real(world, two_generations, monkeypatch):
    """Proves the test above detects the defect: with pinning disabled the same interleaving returns B's numbers
    under A's authorization (the TOCTOU the review found)."""
    g = two_generations
    monkeypatch.setattr(RP, "pinned", lambda owner, slot, fetch: fetch())
    dep = Interleave(world.repo, refresh_to(world, g["b"]))
    c = client_with(world, dep)
    try:
        r = c.get("/api/context")
        assert r.status_code == 200 and r.headers["X-Halsa-Generation"] == g["gen_a"].generation_id
        assert product_count(r.json()["data"]) == product_count(world.repo.live()) != product_count(g["ctx_a"])
    finally:
        c.__exit__(None, None, None)
        APP.app.dependency_overrides.clear()


def test_failed_refresh_during_a_request_rolls_back_and_the_request_stays_consistent(world, two_generations,
                                                                                   monkeypatch):
    g = two_generations
    dash_before = open(world.dash, encoding="utf-8").read()
    man_before = open(GEN.manifest_path(world.dash), encoding="utf-8").read()
    dep = Interleave(world.repo, refresh_to(world, g["b"], fail_install=True, monkeypatch=monkeypatch))
    c = client_with(world, dep)
    try:
        r = c.get("/api/context")
        assert dep.fired == 1 and r.status_code == 200
        assert r.headers["X-Halsa-Generation"] == g["gen_a"].generation_id
        assert product_count(r.json()["data"]) == product_count(g["ctx_a"])
        # rollback: dashboard, manifest, context and store all still A
        assert open(world.dash, encoding="utf-8").read() == dash_before
        assert open(GEN.manifest_path(world.dash), encoding="utf-8").read() == man_before
        assert world.repo.live() is g["ctx_a"] and world.store.current() is g["gen_a"]
        APP.app.dependency_overrides[APP.get_repo] = lambda: world.repo
        r2 = c.get("/api/context")
        assert r2.status_code == 200 and r2.headers["X-Halsa-Generation"] == g["gen_a"].generation_id
    finally:
        c.__exit__(None, None, None)
        APP.app.dependency_overrides.clear()


def test_indices_of_a_pinned_older_context_never_leak_to_newer_requests(world, two_generations, monkeypatch):
    g = two_generations
    original = world.repo._compute_indices
    def probe(ctx):
        customers, products = original(ctx)
        customers['SYN-INDEX-PROBE'] = {'products_scanned': product_count(ctx)}
        return customers, products
    monkeypatch.setattr(world.repo, '_compute_indices', probe)
    tok = RP.begin()
    try:
        assert world.repo.current() is g["ctx_a"]                     # pinned A
        refresh_to(world, g["b"])()                                   # B published during the request
        assert world.repo.customer('SYN-INDEX-PROBE')['products_scanned'] == product_count(g['ctx_a'])
    finally:
        RP.end(tok)
    assert world.repo.customer('SYN-INDEX-PROBE')['products_scanned'] == product_count(world.repo.live())
    assert product_count(world.repo.live()) != product_count(g['ctx_a'])


def _owner_world(tmp_path, world):
    from services import owner_view as OV
    from tests.fixtures import kredi_takip_synth as KT
    p = KT.book(Path(tmp_path) / "owner")
    own = OV.OwnerRepository({"kredi": OV.OwnerSourceConfig("kredi", str(p)),
                              "gider_gelir": OV.OwnerSourceConfig("gider_gelir", None),
                              "butce": OV.OwnerSourceConfig("butce", None)}, background=False,
                             on_record=world.store.replace_owner)
    own.ensure_loaded()
    APP.app.state.generations = world.store
    APP.app.dependency_overrides[APP.get_repo] = lambda: world.repo
    APP.app.dependency_overrides[APP.get_owner] = lambda: own
    return own, p, KT


def _kredi_codes(gate):
    return set(gate.decide("owner.kredi").codes)


def test_owner_reload_after_authorization_keeps_the_authorized_owner_view(tmp_path, world, two_generations):
    own, p, KT = _owner_world(tmp_path, world)
    try:
        f1 = GEN.sha256_file(str(p))
        tok = RP.begin()
        try:
            gate = APP.accuracy_gate()                                # authorization: binds the F1 owner record
            assert "EVIDENCE-GENERATION:kredi" not in _kredi_codes(gate)
            KT.book(Path(tmp_path) / "owner", stale_subtotal=True)    # a new revision …
            own.reload()                                              # … published during the request
            assert world.store.current().record("kredi").sha256 != f1
            assert own.snapshot()["kredi"]["fingerprint"] == f1       # the request still returns F1's view
            assert APP.accuracy_gate() is gate                        # and is still judged by F1's evidence
        finally:
            RP.end(tok)
        assert own.snapshot()["kredi"]["fingerprint"] != f1           # later requests: the new revision
    finally:
        APP.app.dependency_overrides.clear()


def test_owner_reload_between_view_and_gate_never_binds_the_newer_evidence(tmp_path, world, two_generations):
    own, p, KT = _owner_world(tmp_path, world)
    try:
        tok = RP.begin()
        try:
            f1 = own.snapshot()["kredi"]["fingerprint"]               # the endpoint read its view first
            KT.book(Path(tmp_path) / "owner", stale_subtotal=True)
            own.reload()                                              # F2 published before the gate is built
            gate = APP.accuracy_gate()
            assert "EVIDENCE-GENERATION:kredi" in _kredi_codes(gate)  # F2's evidence never authorizes F1's view
            assert gate.decide("owner.kredi").state != S
            assert own.snapshot()["kredi"]["fingerprint"] == f1
        finally:
            RP.end(tok)
    finally:
        APP.app.dependency_overrides.clear()


def test_owner_view_and_its_record_are_published_together(tmp_path, world, two_generations):
    """No reader can see the new owner view with the old record (or the reverse): both swap under PUBLISH_LOCK."""
    own, p, KT = _owner_world(tmp_path, world)
    seen = []
    orig = world.store.replace_owner

    def spy(k, r):
        got = {}

        def probe():                                                  # another thread tries to capture now
            got["ok"] = RP.PUBLISH_LOCK.acquire(timeout=0.2)
            if got["ok"]:
                RP.PUBLISH_LOCK.release()
        t = threading.Thread(target=probe)
        t.start()
        t.join()
        seen.append(got["ok"])
        return orig(k, r)
    own._on_record = spy
    try:
        KT.book(Path(tmp_path) / "owner", stale_subtotal=True)
        own.reload()
        assert seen == [False]                                        # the lock was held across view + record
    finally:
        APP.app.dependency_overrides.clear()


def test_simultaneous_requests_never_mix_generations(world, two_generations):
    g = two_generations
    c = client_with(world, lambda: world.repo)
    results, errors = [], []
    stop = threading.Event()

    def refresher():
        flip = True
        try:
            for _ in range(6):
                refresh_to(world, g["b"] if flip else g["a"])()
                flip = not flip
        except Exception as e:                                       # pragma: no cover - surfaced below
            errors.append(repr(e))
        finally:
            stop.set()

    def reader():
        while not stop.is_set():
            r = c.get("/api/context")
            results.append((r.status_code, r.headers.get("X-Halsa-Generation"),
                            product_count(r.json()["data"]) if r.status_code == 200 else None))

    readers = [threading.Thread(target=reader) for _ in range(4)]
    t = threading.Thread(target=refresher)
    try:
        for x in readers:
            x.start()
        t.start()
        t.join(timeout=300)
        for x in readers:
            x.join(timeout=60)
    finally:
        c.__exit__(None, None, None)
        APP.app.dependency_overrides.clear()
    assert not errors, errors
    statuses = {s for s, _, _ in results}
    assert statuses <= {200, ACC.WITHHELD_HTTP_STATUS}, statuses      # never a 500
    ok = [(h, codes) for s, h, codes in results if s == 200]
    assert ok, "no verified response observed"
    gen_a = g["gen_a"].generation_id
    for h, codes in ok:
        # only A is approved: every displayed response is A, carries A's data and says A
        assert h == gen_a and codes == product_count(g["ctx_a"])
    assert any(s == ACC.WITHHELD_HTTP_STATUS for s, _, _ in results)    # B was live at times: withheld, never shown


def test_publication_holds_the_lock_across_dashboard_manifest_and_context(world, two_generations, monkeypatch):
    g = two_generations
    held = []
    monkeypatch.setattr(RF, "_after_manifest_swap", lambda: held.append(RP.PUBLISH_LOCK._is_owned()))
    orig = APP._install_generation

    def install(repo, staged):
        held.append(RP.PUBLISH_LOCK._is_owned())
        return orig(repo, staged)
    Path(world.folder, SE.STOCK_FILE).write_bytes(g["b"])
    rep = RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY,
                         install=lambda s: install(world.repo, s))
    assert rep["ok"] and held == [True, True]


class Crash(BaseException):
    """A process dying between the two file replacements (not catchable as Exception)."""


def test_crash_between_manifest_and_dashboard_swap_is_never_restored(world, two_generations, monkeypatch):
    g = two_generations
    Path(world.folder, SE.STOCK_FILE).write_bytes(g["b"])
    monkeypatch.setattr(RF, "_after_manifest_swap", lambda: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY,
                       install=lambda s: APP._install_generation(world.repo, s))
    assert GEN.read_manifest(GEN.manifest_path(world.dash)).core_id != GEN.dashboard_generation(world.dash)
    s = GEN.GenerationStore()
    assert s.restore(GEN.manifest_path(world.dash), world.dash, world.folder) is None
    assert s.restore_reason == "dashboard_generation_mismatch"
    assert world.store.current() is g["gen_a"]                        # in-process: nothing was installed


def test_swap_failure_between_files_restores_both(world, two_generations, monkeypatch):
    g = two_generations
    before = (open(world.dash, encoding="utf-8").read(), open(GEN.manifest_path(world.dash), encoding="utf-8").read())
    Path(world.folder, SE.STOCK_FILE).write_bytes(g["b"])
    monkeypatch.setattr(RF, "_after_manifest_swap", lambda: (_ for _ in ()).throw(OSError("disk")))
    rep = RF.run_refresh(world.folder, world.dash, inflation_rate=0.40, today=TODAY,
                         install=lambda s: APP._install_generation(world.repo, s))
    assert not rep["ok"] and rep["step"] == "swap"
    assert (open(world.dash, encoding="utf-8").read(), open(GEN.manifest_path(world.dash), encoding="utf-8").read()) \
        == before
    assert world.store.current() is g["gen_a"] and world.repo.live() is g["ctx_a"]


# ---------------------------------------------------------------------------------------------
# Issue 2 — persisted evidence integrity
# ---------------------------------------------------------------------------------------------
def _stock(d):
    return next(r for r in d["core"] if r["key"] == "stock")


def _flip(v):
    return (not v) if isinstance(v, bool) else (True if v is None else (v + 7 if isinstance(v, int) else "x"))


MUTATIONS = {
    "totals_consistent": lambda d: _stock(d)["evidence"].__setitem__(
        "totals_consistent", _flip(_stock(d)["evidence"]["totals_consistent"])),
    "joins_validated": lambda d: _stock(d)["evidence"].__setitem__(
        "joins_validated", _flip(_stock(d)["evidence"]["joins_validated"])),
    "mapping_approved": lambda d: _stock(d)["evidence"].__setitem__(
        "mapping_approved", _flip(_stock(d)["evidence"]["mapping_approved"])),
    "evidence.error_cells": lambda d: _stock(d)["evidence"].__setitem__(
        "error_cells", _flip(_stock(d)["evidence"]["error_cells"])),
    "evidence.missing_cached_formulas": lambda d: _stock(d)["evidence"].__setitem__(
        "missing_cached_formulas", _flip(_stock(d)["evidence"]["missing_cached_formulas"])),
    "evidence.period_complete": lambda d: _stock(d)["evidence"].__setitem__(
        "period_complete", _flip(_stock(d)["evidence"]["period_complete"])),
    "evidence.inspection_failures": lambda d: _stock(d)["evidence"].__setitem__("inspection_failures", []) or
    _stock(d)["evidence"].__setitem__("selection_explicit", _flip(_stock(d)["evidence"]["selection_explicit"])),
    "validation.error_cells": lambda d: _stock(d)["validation"].__setitem__(
        "error_cells", _flip(_stock(d)["validation"].get("error_cells"))),
    "observed.approval_status": lambda d: _stock(d)["observed"].__setitem__("approval_status", "applied!"),
    "joins.report": lambda d: d["joins"].__setitem__(
        "sold_products_not_in_stock", _flip(d["joins"].get("sold_products_not_in_stock"))),
    "liste.evidence": lambda d: next(r for r in d["core"] if r["key"] == "liste")["evidence"].__setitem__(
        "joins_validated", _flip(next(r for r in d["core"] if r["key"] == "liste")["evidence"]["joins_validated"])),
}


def _reseal(d):
    """What a deliberate editor can do: recompute the (unkeyed) ids over the edited content."""
    m = GEN.Manifest(created_at=d["created_at"], core=tuple(GEN.SourceRecord.from_dict(x) for x in d["core"]),
                     owner=tuple(GEN.SourceRecord.from_dict(x) for x in d.get("owner") or ()), joins=d["joins"],
                     selection_origin=d["selection_origin"], policy_version=d["policy_version"],
                     dashboard_sha256=d.get("dashboard_sha256"))
    return m.to_dict()


@pytest.fixture
def published(world, promoted):
    selection(world.folder)
    m = publish(world)
    mp = GEN.manifest_path(world.dash)
    return {"m": m, "path": mp, "raw": json.load(open(mp, encoding="utf-8"))}


def _shas(d):
    return {r["key"]: r["sha256"] for r in d["core"]}


def _restart(world):
    w = World(world.folder, world.dash, None)
    cand = w.store.restore(GEN.manifest_path(world.dash), world.dash, world.folder)
    return w, cand


@pytest.mark.parametrize("field", sorted(MUTATIONS))
def test_edited_evidence_with_unchanged_ids_is_rejected_as_corrupted(world, published, field):
    d = json.loads(json.dumps(published["raw"]))
    MUTATIONS[field](d)
    assert d != published["raw"] and _shas(d) == _shas(published["raw"])     # fingerprints untouched
    json.dump(d, open(published["path"], "w", encoding="utf-8"))
    assert GEN.read_manifest(published["path"]) is None
    w, cand = _restart(world)
    assert cand is None and w.store.restore_reason == "no_manifest_or_corrupted"
    assert w.store.current() is None and w.store.pending is None


@pytest.mark.parametrize("field", sorted(MUTATIONS))
def test_resealed_evidence_does_not_match_the_served_dashboard(world, published, field):
    d = json.loads(json.dumps(published["raw"]))
    MUTATIONS[field](d)
    sealed = _reseal(d)
    assert _shas(sealed) == _shas(published["raw"]) and sealed["core_id"] != published["raw"]["core_id"]
    json.dump(sealed, open(published["path"], "w", encoding="utf-8"))
    assert GEN.read_manifest(published["path"]) is not None                  # a self-consistent file …
    w, cand = _restart(world)
    assert cand is None and w.store.restore_reason == "dashboard_generation_mismatch"   # … from another evaluation


@pytest.mark.parametrize("field", sorted(MUTATIONS))
def test_resealed_evidence_and_dashboard_fail_closed_on_rederivation(world, published, field):
    """Worst case: the manifest AND the dashboard's generation marker are edited consistently. The restart treats the
    file as a candidate only and re-derives the evaluation from the original bytes; the difference fails closed."""
    d = json.loads(json.dumps(published["raw"]))
    MUTATIONS[field](d)
    core_id = _reseal(d)["core_id"]                                           # (core_id excludes the HTML digest)
    html = open(world.dash, encoding="utf-8").read().replace(published["raw"]["core_id"], core_id)
    open(world.dash, "w", encoding="utf-8").write(html)
    d["dashboard_sha256"] = GEN.sha256_file(world.dash)                       # the editor re-records the HTML too
    sealed = _reseal(d)
    assert sealed["core_id"] == core_id
    json.dump(sealed, open(published["path"], "w", encoding="utf-8"))
    w, cand = _restart(world)
    assert cand is not None and w.store.restore_reason == "pending_revalidation"
    assert w.store.current() is None                                          # a candidate gives no evidence
    ctx = w.repo.context()                                                    # re-derivation from the bytes
    assert w.store.restore_reason == "evidence_mismatch"
    assert w.store.current() is None and w.store.pending is None
    assert getattr(ctx, "generation", None) is None
    st, j = _api(w)
    assert st["state"] != "verified" and j["generation_id"] is None


def _api(w):
    with w.client() as c:
        j = c.get("/api/accuracy/status").json()
    return {m["key"]: m for m in j["metrics"]}["stock.reorder"], j


def test_untouched_manifest_is_rederived_not_trusted(world, published):
    w, cand = _restart(world)
    assert cand is not None
    st, j = _api(w)                                                           # before the context build
    assert j["restore_pending"] is True and st["state"] != "verified"
    w.repo.context()
    m = w.store.current()
    assert m is not None and m is not cand and m.core_id == cand.core_id == published["m"].core_id
    assert w.store.restore_reason is None
    st, j = _api(w)
    assert st["state"] == "verified" and j["restore_pending"] is False


def test_changed_declarations_after_publication_fail_closed_on_restart(world, published):
    """Same workbook bytes, but the selection file now declares another date: the saved evaluation no longer
    describes the inputs, so it is not restored."""
    sel = json.load(open(os.path.join(world.folder, GEN.SELECTION_FILE), encoding="utf-8"))
    sel["core"]["stock"]["snapshot_date"] = (TODAY - __import__("datetime").timedelta(days=1)).isoformat()
    json.dump(sel, open(os.path.join(world.folder, GEN.SELECTION_FILE), "w", encoding="utf-8"))
    w, cand = _restart(world)
    assert cand is not None
    w.repo.context()
    assert w.store.restore_reason == "evidence_mismatch" and w.store.current() is None


def test_truncated_manifest_is_never_a_candidate(world, published):
    raw = open(published["path"], encoding="utf-8").read()
    open(published["path"], "w", encoding="utf-8").write(raw[: len(raw) // 2])
    w, cand = _restart(world)
    assert cand is None and w.store.restore_reason == "no_manifest_or_corrupted"


def test_manifest_ids_cover_every_evidence_field():
    rec = GEN.SourceRecord("stock", "core", "stock_snapshot", "current_state_snapshot", "s.xlsx", "a" * 64, 1,
                           "explicit", {}, {}, {"error_cells": 0}, ACC.SourceEvidence("stock", error_cells=0))
    base = GEN.Manifest("t", (rec,), joins={"x": 0})
    for f in dataclasses.fields(ACC.SourceEvidence):
        if f.name == "source":
            continue
        cur = getattr(rec.evidence, f.name)
        alt = (not cur) if isinstance(cur, bool) else (("x",) if f.name in ("inspection_failures", "notes") else
                                                      (__import__("datetime").date(2020, 1, 1) if "date" in f.name
                                                       and "origin" not in f.name and "kind" not in f.name else 3))
        if alt == cur:
            alt = 4
        changed = GEN.Manifest("t", (dataclasses.replace(rec, evidence=dataclasses.replace(rec.evidence,
                                                                                           **{f.name: alt})),),
                               joins={"x": 0})
        assert changed.core_id != base.core_id, f.name
    assert GEN.Manifest("t", (dataclasses.replace(rec, validation={"error_cells": 1}),), joins={"x": 0}).core_id \
        != base.core_id
    assert GEN.Manifest("t", (rec,), joins={"x": 1}).core_id != base.core_id
    assert GEN.Manifest("other time", (dataclasses.replace(rec, file_modified="2020"),), joins={"x": 0}).core_id \
        == base.core_id                                                   # informational fields only
