"""M8-VKM-1 — corrections from the independent re-review of PR #31 at `847c055` (D-064). Synthetic data only.

A. Footer scope accuracy. A footer problem in column H is cleared only by an upload whose column-H footer cells were
   reconciled again and ALL matched. No footer cells (empty `footer_by_column`), a footer that checks only column D,
   or an H footer that is now non-comparable leave the H issue UNKNOWN (`not_checked`), status kept.
B. Concurrent refresh and review. A review / note based on generation A can never commit after generation B was
   installed, also while B's observer is still waiting for the issue-store lock. Decisions take PUBLISH_LOCK first and
   re-read the live generation under the issue-store lock (lock order PUBLISH_LOCK → vkm_store._LOCK →
   GenerationStore._lock); the publication observer waits only a bounded time.

NEGATIVE CONTROLS replay the pre-correction rules (`rows:footer` granted for any footer mapping; an expectation
computed before the lock) and show the wrong outcome they produced.
"""
import dataclasses
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import app as APP
from services import generation as GEN
from services import request_pin as RP
from services import store as ST
from services import vkm as V
from services import vkm_store as VS
from tests.fixtures import synthetic_erp as SE
from tests.fixtures.xlsx_synth import E, F
from tests.test_m8_accuracy2b import COLI, _num, agg_row, book, products
from tests.test_m8_vkm import by_check, dump, events, http, manifest_of, owner, write_folder  # noqa: F401
from tests.test_m8_vkm_integrity import fresh_db, obs_of


# ---------------------------------------------------------------------------------------------
# A. footer scope accuracy
# ---------------------------------------------------------------------------------------------
def d_only(rows, first, last, r):
    """A footer row that reconciles column D only."""
    out = ["TOPLAM"] + [None] * 9
    out[COLI["D"]] = F(f"SUM(D{first}:D{last})", sum(_num(x[COLI["D"]]) or 0 for x in rows))
    return out


def h_error(rows, first, last, r):
    """A SUM footer whose H cell propagates an Excel error from the rows (non-comparable)."""
    out = agg_row(rows, first, last, r, "SUM")
    out[COLI["H"]] = F(f"(SUM(H{first}:H{last}))", E("#REF!"))
    return out


def stock_upload(folder, **book_kw):
    rows = book_kw.pop("rows", None) or products()
    f = write_folder(folder, stock=rows)
    book(Path(f) / SE.STOCK_FILE, rows, **book_kw)
    return manifest_of(f)


def legacy_footer(obs, m):
    """NEGATIVE CONTROL — the pre-correction rule: `rows:footer` was granted whenever the stock rows were analysed and
    `footer_by_column` was a mapping (even `{}`), and it covered every footer issue of every column."""
    stock = next(r for r in m.core if r.key == "stock")
    rows = (stock.validation or {}).get("rows") or {}
    if not (rows.get("ok") and isinstance(rows.get("footer_by_column"), dict)):
        return obs
    con = sqlite3.connect(ST.DB_PATH)
    old = {V.scope_of(c, f, col) for c, f, col in con.execute(
        "SELECT check_code, field_name, column_letter FROM dq_issue WHERE source_key='stock'")
        if c in V.FOOTER_COLUMN_CHECKS or c == "footer_ambiguous"}
    con.close()
    return {**obs, "stock": dataclasses.replace(obs["stock"], checked=obs["stock"].checked | old)}


def footer_issue(check="footer_mismatch", column="H"):
    con = sqlite3.connect(ST.DB_PATH)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM dq_issue WHERE check_code=? AND column_letter=?", (check, column)).fetchone()
    con.close()
    return dict(r) if r else None


def h_mismatch_then(tmp_path, name, later, *, legacy=False):
    fresh_db(tmp_path, name)
    first = stock_upload(str(tmp_path / f"{name}-1"), tamper="H")
    VS.observe(obs_of(first))
    iid = footer_issue()["issue_id"]
    g = VS.vkm_generation()
    VS.review(iid, "kabul_edildi", vkm_generation_presented=g, actor="owner", expected=VS.stored_digests())
    o = obs_of(later)
    VS.observe(legacy_footer(o, later) if legacy else o)
    return iid


@pytest.mark.parametrize("variant", ["no_footer", "d_only", "h_non_comparable"])
def test_unreconciled_h_footer_never_resolves_an_h_mismatch(tmp_path, variant):
    kw = {"no_footer": dict(footer=()), "d_only": dict(footer=(d_only,)),
          "h_non_comparable": dict(footer=(h_error,))}[variant]
    if variant == "h_non_comparable":
        rows = products()
        rows[0][COLI["H"]] = E("#REF!")
        kw["rows"] = rows
    later = stock_upload(str(tmp_path / "later"), **kw)
    stock = next(r for r in later.core if r.key == "stock")
    by_col = stock.validation["rows"]["footer_by_column"]
    assert "H" not in by_col or by_col["H"]["matched"] < by_col["H"]["checked"]           # sanity: H not verified

    # NEGATIVE CONTROL: the old rule marks the H mismatch resolved (no-footer and D-only uploads)
    if variant != "h_non_comparable":
        h_mismatch_then(tmp_path, "legacy", later, legacy=True)
        assert footer_issue()["status"] == "cozuldu_kaynakta"

    iid = h_mismatch_then(tmp_path, "fixed", later)
    r = footer_issue()
    assert r["status"] == "kabul_edildi" and r["presence"] == "not_checked"               # UNKNOWN, decision kept
    assert events(iid)[-1][1:3] == ("not_checked", "kabul_edildi")
    if variant == "d_only":
        assert V.scope_of("footer_mismatch", None, "D") in obs_of(later)["stock"].checked  # D genuinely checked
    if variant == "h_non_comparable":
        assert footer_issue("footer_partial")["presence"] == "present"                     # the new problem is shown


def test_a_rechecked_matching_h_footer_resolves_the_h_mismatch(tmp_path):
    later = stock_upload(str(tmp_path / "later"))                                         # every column matches
    iid = h_mismatch_then(tmp_path, "fixed", later)
    r = footer_issue()
    assert r["status"] == "cozuldu_kaynakta" and r["presence"] == "absent"
    assert events(iid)[-1][1] == "not_present"


def test_footer_scopes_are_per_column_and_per_check(tmp_path):
    m = stock_upload(str(tmp_path / "m"), tamper="H")
    det = V.inspect_core(next(r for r in m.core if r.key == "stock"), m.joins)
    assert {f"footer:{c}:footer_mismatch" for c in "DFG"} <= det.checked
    assert "footer:H:footer_partial" not in det.checked and "footer:H:footer_missing_cache" not in det.checked
    assert "footer:H:footer_mismatch" in det.checked                     # only because the mismatch itself was seen
    assert "footer:ambiguous_rows" in det.checked
    empty = stock_upload(str(tmp_path / "e"), footer=())
    det = V.inspect_core(next(r for r in empty.core if r.key == "stock"), empty.joins)
    assert not [s for s in det.checked if s.startswith("footer:") and s != "footer:ambiguous_rows"]
    assert "rows:footer" not in det.checked


# ---------------------------------------------------------------------------------------------
# B. concurrent refresh and review
# ---------------------------------------------------------------------------------------------
def gated_observer(release, started, gate_core_id):
    """The real publication observer, held back for ONE generation (as if waiting for the issue-store lock)."""
    real = VS.make_observer(lambda: {})

    def obs(kind, manifest, key, store):
        if manifest is not None and manifest.core and manifest.core_id == gate_core_id:
            started.set()
            assert release.wait(20)
        real(kind, manifest, key, store)
    return obs


def test_review_based_on_a_never_commits_after_b_is_installed_while_b_observer_waits(tmp_path):
    a = manifest_of(write_folder(str(tmp_path / "a"), stock=products(dup=True)))
    b = manifest_of(write_folder(str(tmp_path / "b"), stock=products(dup=True), dup_sales=2))
    store = GEN.GenerationStore()
    release, started = threading.Event(), threading.Event()
    store.add_observer(gated_observer(release, started, b.core_id))
    store.install(a)
    iid = by_check("stock", "stock_duplicate_code")["issue_id"]
    g_a = VS.vkm_generation()
    precomputed = VS.expected_digests(store.current(), {})       # what the request computed before B (old code)

    pub = threading.Thread(target=store.install, args=(b,), daemon=True)
    pub.start()
    assert started.wait(10) and store.current().core_id == b.core_id      # B installed; its observer is waiting
    before = dump()

    # NEGATIVE CONTROL (on a copy): with the expectation computed before the lock, the decision on A commits
    copy = str(Path(ST.DB_PATH).with_name("legacy_race.db"))
    shutil.copy(ST.DB_PATH, copy)
    live, ST.DB_PATH = ST.DB_PATH, copy
    try:
        VS.review(iid, "incelendi", vkm_generation_presented=g_a, actor="owner", expected=precomputed)
        con = sqlite3.connect(copy)
        assert con.execute("SELECT status FROM dq_issue WHERE issue_id=?", (iid,)).fetchone()[0] == "incelendi"
        con.close()
    finally:
        ST.DB_PATH = live

    # fixed: the live generation is read under the write lock → refused, nothing written
    live_expected = lambda: VS.expected_digests(store.current(), {})     # noqa: E731
    for call in (lambda: VS.review(iid, "incelendi", vkm_generation_presented=g_a, actor="owner",
                                   expected=live_expected),
                 lambda: VS.add_note(iid, "A'ya göre not", vkm_generation_presented=g_a, actor="owner",
                                     expected=live_expected)):
        with pytest.raises(VS.VKMError) as e:
            call()
        assert e.value.code == "out_of_sync"
    assert dump() == before

    release.set()
    pub.join(10)
    assert not pub.is_alive()
    with pytest.raises(VS.VKMError) as e:                                 # after B is observed: A's token is stale
        VS.review(iid, "incelendi", vkm_generation_presented=g_a, actor="owner", expected=live_expected)
    assert e.value.code == "stale_generation"
    g_b = VS.vkm_generation()
    assert VS.review(iid, "incelendi", vkm_generation_presented=g_b, actor="owner",
                     expected=live_expected)["issue"]["status"] == "incelendi"


@pytest.mark.production_auth
def test_http_review_waits_for_an_in_flight_publication_and_is_then_refused(http, monkeypatch):
    c = owner(http)
    lst = c.get("/api/vkm/issues").json()
    iid, g_a = lst["issues"][0]["issue_id"], lst["vkm_generation"]
    newer = manifest_of(write_folder(str(Path(ST.DB_PATH).parent / "newer"), stock=products(dup=True), dup_sales=3))

    release, started = threading.Event(), threading.Event()
    real_observe = VS.observe

    def slow_observe(*a, **k):                    # B's observer waits (as for a busy issue-store lock)
        started.set()
        assert release.wait(20)
        return real_observe(*a, **k)
    monkeypatch.setattr(VS, "observe", slow_observe)

    def publish():                                # exactly how a refresh publishes
        with RP.PUBLISH_LOCK:
            APP.app.state.generations.install(newer)
    pub = threading.Thread(target=publish, daemon=True)
    pub.start()
    assert started.wait(10)
    before = dump()
    result = {}
    rev = threading.Thread(target=lambda: result.setdefault("r", c.post(
        f"/api/vkm/issues/{iid}/review", json={"action": "incelendi", "vkm_generation": g_a})), daemon=True)
    rev.start()
    rev.join(0.7)
    assert rev.is_alive(), "the decision must wait for the in-flight publication"
    assert dump() == before
    monkeypatch.setattr(VS, "observe", real_observe)
    release.set()
    pub.join(10)
    rev.join(10)
    assert not pub.is_alive() and not rev.is_alive()                      # no deadlock
    r = result["r"]
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "stale_generation"
    assert VS.list_issues(status="all")["issues"] and \
        next(i for i in VS.list_issues(status="all")["issues"] if i["issue_id"] == iid)["status"] != "incelendi"


def test_a_busy_issue_store_never_blocks_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(VS, "OBSERVER_LOCK_TIMEOUT", 0.3)
    a = manifest_of(write_folder(str(tmp_path / "a")))
    b = manifest_of(write_folder(str(tmp_path / "b"), dup_sales=2))
    store = GEN.GenerationStore()
    store.add_observer(VS.make_observer(lambda: {}))
    store.install(a)
    assert VS._LOCK.acquire(timeout=5)                                    # something holds the issue-store lock
    try:
        t0 = time.monotonic()
        with RP.PUBLISH_LOCK:
            assert store.install(b) is not None                           # publication completes …
        assert time.monotonic() - t0 < 3                                  # … within the observer's bound
    finally:
        VS._LOCK.release()
    assert store.current().core_id == b.core_id
    assert VS.sync_state(VS.expected_digests(b, {}))["state"] == "out_of_sync"   # visible, decisions refused
    assert VS.resync(b, {}, store.current)["changed"]                      # owner recovery
    assert VS.sync_state(VS.expected_digests(b, {}))["state"] == "in_sync"


def test_interleaved_publications_and_decisions_never_deadlock(tmp_path):
    ms = [manifest_of(write_folder(str(tmp_path / f"m{i}"), dup_sales=i)) for i in range(3)]
    store = GEN.GenerationStore()
    store.add_observer(VS.make_observer(lambda: {}))
    store.install(ms[0])
    live_expected = lambda: VS.expected_digests(store.current(), {})     # noqa: E731
    errors, outcomes = [], []
    stop = time.monotonic() + 3

    def publisher():
        i = 0
        while time.monotonic() < stop:
            with RP.PUBLISH_LOCK:
                store.install(ms[i % 3])
            i += 1

    def decider():
        while time.monotonic() < stop:
            try:
                rows = VS.list_issues(status="all")["issues"]
                if not rows:
                    continue
                with RP.PUBLISH_LOCK:                                    # the app's order
                    VS.add_note(rows[0]["issue_id"], "eşzamanlı not", vkm_generation_presented=VS.vkm_generation(),
                                actor="owner", expected=live_expected)
                outcomes.append("ok")
            except VS.VKMError as e:
                outcomes.append(e.code)
            except Exception as e:                                       # pragma: no cover - would be a defect
                errors.append(repr(e))
    threads = [threading.Thread(target=publisher, daemon=True)] + [threading.Thread(target=decider, daemon=True)
                                                                  for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert not any(t.is_alive() for t in threads), "deadlock"
    assert errors == []
    assert set(outcomes) <= {"ok", "stale_generation", "out_of_sync", "not_found"}
    assert "ok" in outcomes
    # every committed note was made against the generation that was observed at that moment: the store ends in sync
    assert VS.sync_state(live_expected())["state"] == "in_sync"
