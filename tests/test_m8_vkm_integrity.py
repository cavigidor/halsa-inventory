"""M8-VKM-1 — corrections from the independent review of PR #31 (D-063). Synthetic data only.

§1  A PRESENT but uninspectable source never resolves previously detected issues: refused stock layout, failed source
    inspection, failed / incomplete relationship checks → presence UNKNOWN (`not_checked`), review status kept; only an
    upload on which the check really ran and found nothing resolves the issue.
§2  Observation identity and the stale-review token are bound to the material accepted generation (core_id / record
    content) and the declared period: same bytes + changed declarations is a NEW observation; an exact repeat refresh
    or restart is the SAME observation (nothing appended).
§3  A failed observation after a successful publication is visible ("out of sync"), every decision is refused until
    the owner re-runs the observation (owner_write, idempotent), and publication never depends on the recorder.

Each section has NEGATIVE CONTROLS replaying the old behaviour (the pre-review reconciliation rule, the pre-review
digest, a review checked only against the store itself) to show the regression the fix prevents.
"""
import dataclasses
import hashlib
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

import app as APP
from services import accuracy as ACC
from services import generation as GEN
from services import store as ST
from services import vkm as V
from services import vkm_store as VS
from tests.fixtures import synthetic_erp as SE
from tests.test_m8_accuracy2b import HEAD, book, products
from tests.test_m8_vkm import (by_check, dump, events, finance, http, insync, manifest_of, observe_core,  # noqa: F401
                               owner, write_folder)


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def legacy(obs: dict) -> dict:
    """NEGATIVE CONTROL — the pre-review rule: a present upload counted as having evaluated EVERY check, so every
    previously stored issue missing from it was 'resolved in the source'."""
    con = sqlite3.connect(ST.DB_PATH)
    every = {r[0]: set() for r in con.execute("SELECT DISTINCT source_key FROM dq_issue")}
    for key, check, fname in con.execute("SELECT source_key, check_code, field_name FROM dq_issue"):
        every[key].add(V.scope_of(check, fname))
    con.close()
    return {k: (dataclasses.replace(o, checked=o.checked | frozenset(every.get(k, ()))) if o.state == "present" else o)
            for k, o in obs.items()}


def legacy_digest(o: VS.Observation, key: str) -> str:
    """NEGATIVE CONTROL — the pre-review observation digest (no provenance, no period, no scopes)."""
    body = ["vkm-1", o.state, o.source_sha256,
            sorted((i.issue_id, sorted(i.counts.items()), list(i.labels)) for i in o.all_issues(key))]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def fresh_db(tmp_path, name):
    """Run a scenario in a separate private store (for side-by-side negative controls)."""
    ST.DB_PATH = str(tmp_path / f"{name}.db")
    ST.init_db()


def obs_of(m):
    return VS.core_observations(m, {}, VS.previously_seen_sources())


def review_all_open(source_key, action="kabul_edildi"):
    g = VS.vkm_generation()
    for r in VS.list_issues(status="open", source=source_key)["issues"]:
        VS.review(r["issue_id"], action, vkm_generation_presented=g, actor="owner", expected=insync())


def broken_stock(folder):
    """The stock export with a renamed product-code header: the approved template no longer matches (D-056)."""
    head = ["Stok Kodu (yeni)" if h == "Stok_Kodu" else h for h in HEAD]
    book(Path(folder) / SE.STOCK_FILE, products(dup=True), head=head)
    return folder


def with_record(m, key, **changes):
    core = tuple(dataclasses.replace(r, **changes) if r.key == key else r for r in m.core)
    return dataclasses.replace(m, core=core)


# =============================================================================================
# §1 — unknown evidence is never a resolution
# =============================================================================================
def test_refused_stock_layout_never_resolves_and_a_corrected_upload_does(tmp_path):
    good = manifest_of(write_folder(str(tmp_path / "a"), stock=products(dup=True)))
    bad = manifest_of(broken_stock(write_folder(str(tmp_path / "b"), stock=products(dup=True))))
    fixed = manifest_of(write_folder(str(tmp_path / "c"), stock=products(dup=False)))
    assert next(r for r in bad.core if r.key == "stock").evidence.layout_supported is False      # sanity: refused

    # NEGATIVE CONTROL: the old rule resolves the duplicate on the unreadable upload
    fresh_db(tmp_path, "legacy")
    VS.observe(obs_of(good))
    VS.observe(legacy(obs_of(bad)))
    assert by_check("stock", "stock_duplicate_code")["status"] == "cozuldu_kaynakta"

    # fixed rule
    fresh_db(tmp_path, "fixed")
    VS.observe(obs_of(good))
    dup = by_check("stock", "stock_duplicate_code")
    review_all_open("stock")
    VS.observe(obs_of(bad))
    after = by_check("stock", "stock_duplicate_code")
    assert after["status"] == "kabul_edildi" and after["presence"] == "not_checked"           # decision kept, UNKNOWN
    assert events(dup["issue_id"])[-1][1:3] == ("not_checked", "kabul_edildi")
    assert by_check("stock", "layout_unsupported")["presence"] == "present"
    cov = by_check("stock", "stock_coverage_undefined")
    assert cov["status"] != "cozuldu_kaynakta" and cov["presence"] == "not_checked"
    before = dump()
    VS.observe(obs_of(bad))                                                                 # same upload again
    assert dump() == before
    # a corrected, inspectable upload without duplicates really resolves it
    VS.observe(obs_of(fixed))
    done = by_check("stock", "stock_duplicate_code")
    assert done["status"] == "cozuldu_kaynakta" and done["presence"] == "absent"
    assert events(dup["issue_id"])[-1][1] == "not_present"
    assert by_check("stock", "layout_unsupported")["status"] == "cozuldu_kaynakta"


@pytest.mark.parametrize("variant", ["no_evidence", "inspection_failures"])
def test_failed_source_inspection_never_resolves(tmp_path, variant):
    good = manifest_of(write_folder(str(tmp_path / "a"), stock=products(dup=True), dup_sales=2))
    stock = next(r for r in good.core if r.key == "stock")
    if variant == "no_evidence":
        failed = with_record(good, "stock", evidence=None, validation={"ok": False})
    else:
        failed = with_record(good, "stock", validation={**stock.validation, "rows": {"ok": False}},
                             evidence=dataclasses.replace(stock.evidence, inspection_failures=("scan:stock",),
                                                          fields=None, layout_supported=None))
    watched = ("stock_duplicate_code", "stock_coverage_undefined")

    fresh_db(tmp_path, "legacy")                                                    # NEGATIVE CONTROL
    VS.observe(obs_of(good))
    VS.observe(legacy(obs_of(failed)))
    assert all(by_check("stock", c)["status"] == "cozuldu_kaynakta" for c in watched)

    fresh_db(tmp_path, "fixed")
    VS.observe(obs_of(good))
    VS.observe(obs_of(failed))
    for c in watched:
        r = by_check("stock", c)
        assert r["status"] == "acik" and r["presence"] == "not_checked", c
    assert by_check("stock", "inspection_failed")["presence"] == "present"
    # with partial evidence the declarations are still judged; with NO evidence even that check is UNKNOWN
    want = "not_checked" if variant == "no_evidence" else "present"
    assert by_check("stock", "report_date_missing")["presence"] == want
    assert by_check("stock", "report_date_missing")["status"] == "acik"
    # the sales export was inspected normally: its duplicate is still present
    assert by_check("sales", "sales_duplicate_rows")["presence"] == "present"


def test_failed_or_incomplete_relationship_checks_never_resolve(tmp_path):
    good = manifest_of(write_folder(str(tmp_path / "a")))
    unmatched = ("sales", "join_sales_product_not_in_stock")
    assert unmatched in {(i.source_key, i.check) for r in good.core for i in V.detect_core(r, good.joins)}
    failed = dataclasses.replace(good, joins={"failure": "join_exception"})
    partial = dataclasses.replace(good, joins={k: v for k, v in good.joins.items()
                                               if k not in ("sold_products_not_in_stock", "product_codes_with_whitespace",
                                                            "products_with_multiple_names")})
    clean = dataclasses.replace(good, joins={**good.joins, "sold_products_not_in_stock": 0})

    fresh_db(tmp_path, "legacy")                                                    # NEGATIVE CONTROL
    VS.observe(obs_of(good))
    VS.observe(legacy(obs_of(failed)))
    assert by_check(*unmatched)["status"] == "cozuldu_kaynakta"

    for name, m in (("failed", failed), ("partial", partial)):
        fresh_db(tmp_path, name)
        VS.observe(obs_of(good))
        review_all_open("sales", "incelendi")
        VS.observe(obs_of(m))
        r = by_check(*unmatched)
        assert r["status"] == "incelendi" and r["presence"] == "not_checked", name
    assert by_check("sales", "joins_not_checked") is None                       # partial: the check ran, keys missing
    fresh_db(tmp_path, "failed2")
    VS.observe(obs_of(good))
    VS.observe(obs_of(failed))
    assert by_check("sales", "joins_not_checked")["presence"] == "present"
    # a valid relationship check that finds nothing resolves it
    VS.observe(obs_of(clean))
    assert by_check(*unmatched)["status"] == "cozuldu_kaynakta"


def test_a_restored_check_that_still_finds_the_problem_keeps_the_decision(tmp_path):
    good = manifest_of(write_folder(str(tmp_path / "a"), stock=products(dup=True)))
    bad = manifest_of(broken_stock(write_folder(str(tmp_path / "b"), stock=products(dup=True))))
    again = manifest_of(write_folder(str(tmp_path / "c"), stock=products(dup=True), dup_sales=1))
    VS.observe(obs_of(good))
    review_all_open("stock", "incelendi")
    iid = by_check("stock", "stock_duplicate_code")["issue_id"]
    VS.observe(obs_of(bad))
    VS.observe(obs_of(again))
    r = by_check("stock", "stock_duplicate_code")
    assert r["status"] == "incelendi" and r["presence"] == "present"
    assert events(iid)[-1][1:4] == ("still_present", "incelendi", "check_restored")


def test_missing_relationship_input_is_not_checked_and_missing_file_is_source_missing(tmp_path):
    full = manifest_of(write_folder(str(tmp_path / "a")))
    no_aging = manifest_of(write_folder(str(tmp_path / "b"), aging=False))
    VS.observe(obs_of(full))
    aging_join = by_check("aging", "join_aging_account_not_in_sales")
    VS.observe(obs_of(no_aging))
    if aging_join is not None:
        r = by_check("aging", "join_aging_account_not_in_sales")
        assert r["presence"] == "source_missing" and r["status"] != "cozuldu_kaynakta"
    assert by_check("aging", "missing_source")["presence"] == "present"


def test_every_detected_issue_lies_in_a_checked_scope_and_every_check_has_a_scope(tmp_path):
    for m in (manifest_of(write_folder(str(tmp_path / "a"), stock=products(dup=True), dup_sales=2)),
              manifest_of(broken_stock(write_folder(str(tmp_path / "b"))))):
        for r in m.core:
            det = V.inspect_core(r, m.joins)
            assert all(i.scope in det.checked for i in det.issues), r.key
    assert not [c for c in V.CHECKS if V.scope_of(c, "x").startswith("check:")]
    assert V.inspect_core(next(r for r in m.core if r.key == "stock"), m.joins).checked.isdisjoint(
        {"rows:counts", "rows:footer", "rows:coverage"})                      # refused layout: rows never evaluated


# =============================================================================================
# §2 — observation identity bound to the accepted generation and the declared period
# =============================================================================================
def declare(folder, *, period_start, approve_stock=False):
    sales = {"file": SE.SALES_FILE, "report_date": SE.AS_OF.isoformat(), "period_start": period_start,
             "period_end": SE.AS_OF.isoformat(), "period_complete": True}
    stock = {"file": SE.STOCK_FILE, "snapshot_date": SE.AS_OF.isoformat()}
    if approve_stock:
        stock["approval"] = {"sha256": GEN.sha256_file(os.path.join(folder, SE.STOCK_FILE)), "decision_ref": "D-999",
                             "mapping_approved": True}
    core = {"stock": stock, "sales": sales, "aging": {"file": SE.AGING_FILE, "snapshot_date": SE.AS_OF.isoformat()}}
    with open(os.path.join(folder, GEN.SELECTION_FILE), "w", encoding="utf-8") as fh:
        json.dump({"schema": GEN.SELECTION_SCHEMA, "core": core}, fh)


def test_same_bytes_with_a_changed_declared_period_is_a_new_observation(tmp_path):
    f = write_folder(str(tmp_path / "d"), stock=products(dup=True))
    declare(f, period_start="2020-01-01")
    m1 = manifest_of(f)
    declare(f, period_start="2019-01-01")                                       # only the declaration changes
    m2 = manifest_of(f)
    sales1, sales2 = (next(r for r in m.core if r.key == "sales") for m in (m1, m2))
    assert sales1.sha256 == sales2.sha256 and m1.core_id != m2.core_id
    o1, o2 = obs_of(m1), obs_of(m2)
    assert {i.issue_id for i in o1["sales"].issues} == {i.issue_id for i in o2["sales"].issues}
    # NEGATIVE CONTROL: the pre-review digest cannot tell the two generations apart
    assert legacy_digest(o1["sales"], "sales") == legacy_digest(o2["sales"], "sales")
    assert o1["sales"].digest("sales") != o2["sales"].digest("sales")

    VS.observe(o1)
    g1 = VS.vkm_generation()
    iid = VS.list_issues(source="stock")["issues"][0]["issue_id"]
    n_before = len(events(iid))
    VS.observe(obs_of(m2))
    con = sqlite3.connect(ST.DB_PATH)
    gen, start = con.execute("SELECT generation_id, period_start FROM dq_source_state WHERE source_key='sales'").fetchone()
    con.close()
    assert gen == m2.core_id and start == "2019-01-01"
    assert VS.list_issues(source="stock")["issues"][0]["last_seen_generation"] == m2.core_id[:12]
    assert len(events(iid)) == n_before + 1 and events(iid)[-1][1] == "still_present"
    g2 = VS.vkm_generation()
    assert g2 != g1
    with pytest.raises(VS.VKMError) as e:                                       # a review made on the old page
        VS.review(iid, "incelendi", vkm_generation_presented=g1, actor="owner", expected=insync())
    assert e.value.code == "stale_generation"
    VS.review(iid, "incelendi", vkm_generation_presented=g2, actor="owner", expected=insync())


def test_same_bytes_with_a_new_mapping_approval_is_judged_again(tmp_path):
    f = write_folder(str(tmp_path / "d"))
    declare(f, period_start="2020-01-01")
    VS.observe(obs_of(manifest_of(f)))
    assert by_check("stock", "mapping_unknown")["presence"] == "present"
    declare(f, period_start="2020-01-01", approve_stock=True)
    VS.observe(obs_of(manifest_of(f)))
    assert by_check("stock", "mapping_unknown")["status"] == "cozuldu_kaynakta"


def test_exact_repeat_refresh_and_restart_append_nothing(tmp_path):
    f = write_folder(str(tmp_path / "d"), stock=products(dup=True), dup_sales=1)
    declare(f, period_start="2020-01-01")
    m = manifest_of(f)
    store = GEN.GenerationStore()
    store.add_observer(VS.make_observer(lambda: {}))
    store.install(m)
    before, g = dump(), VS.vkm_generation()
    store.install(manifest_of(f))                                               # repeated refresh, same bytes
    restarted = GEN.GenerationStore()                                           # restart: a new process re-derives
    restarted.add_observer(VS.make_observer(lambda: {}))
    restarted.install(manifest_of(f))
    assert manifest_of(f).core_id == m.core_id
    assert dump() == before and VS.vkm_generation() == g


# =============================================================================================
# §3 — a failed observation is visible, blocks decisions, and is recoverable
# =============================================================================================
def test_no_served_generation_refuses_decisions_and_resync(tmp_path):
    VS.observe(obs_of(manifest_of(write_folder(str(tmp_path / "d")))))
    iid = VS.list_issues()["issues"][0]["issue_id"]
    assert VS.sync_state(None)["state"] == "no_generation"
    with pytest.raises(VS.VKMError) as e:
        VS.review(iid, "incelendi", vkm_generation_presented=VS.vkm_generation(), actor="owner", expected=None)
    assert e.value.code == "out_of_sync"
    with pytest.raises(VS.VKMError) as e:
        VS.resync(None, {}, lambda: None)
    assert e.value.code == "no_generation"


def test_resync_is_idempotent_and_never_writes_a_superseded_generation(tmp_path):
    a = manifest_of(write_folder(str(tmp_path / "a")))
    b = manifest_of(write_folder(str(tmp_path / "b"), dup_sales=2))
    VS.observe(obs_of(a))
    before = dump()
    with pytest.raises(VS.VKMError) as e:                                       # B is being served meanwhile
        VS.resync(a, {}, lambda: b)
    assert e.value.code == "stale_generation"
    assert dump() == before
    assert VS.resync(a, {}, lambda: a)["changed"] == []                         # already in sync: writes nothing
    assert dump() == before
    assert VS.resync(b, {}, lambda: b)["changed"]
    assert VS.sync_state(VS.expected_digests(b, {}))["state"] == "in_sync"


@pytest.mark.production_auth
def test_observer_failure_is_visible_blocks_decisions_and_owner_resync_recovers(http, monkeypatch, caplog):
    anon, fin, c = http(), finance(http), owner(http)     # every client before the failing publication: a new
    nocsrf = http()                                         # TestClient re-runs the app startup
    nocsrf.cookies = c.cookies
    lst = c.get("/api/vkm/issues").json()
    assert lst["sync"]["state"] == "in_sync"
    iid, g = lst["issues"][0]["issue_id"], lst["vkm_generation"]

    real_observe = VS.observe

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(VS, "observe", broken)
    before = dump()
    newer = manifest_of(write_folder(str(Path(ST.DB_PATH).parent / "newer"), stock=products(dup=True), dup_sales=3))
    published = APP.app.state.generations.install(newer)                         # publication succeeds regardless
    assert published is not None and APP.app.state.generations.current().core_id == newer.core_id
    assert dump() == before                                                      # the recorder wrote nothing
    assert "database is locked" not in caplog.text                               # error class only, never content

    # visible everywhere, and GETs still write nothing
    assert anon.get("/api/vkm/summary").json()["sync"] == "out_of_sync"           # public viewer too
    lst = c.get("/api/vkm/issues").json()
    assert lst["sync"]["state"] == "out_of_sync" and "sales" in {s["key"] for s in lst["sync"]["sources"]}
    assert lst["sync"].get("last_failure_at")
    d = c.get(f"/api/vkm/issues/{iid}").json()
    assert d["issue"]["decisions_blocked"] is True and d["issue"]["allowed_actions"] == []
    assert dump() == before

    # decisions on the stale list are refused and change nothing
    for path, body in ((f"/api/vkm/issues/{iid}/review", {"action": "incelendi", "vkm_generation": g}),
                       (f"/api/vkm/issues/{iid}/notes", {"note": "eski listeye not", "vkm_generation": g})):
        r = c.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"]["error_code"] == "out_of_sync"
    assert dump() == before

    # NEGATIVE CONTROL (on a copy of the store): the pre-review check compared the store only with itself, so the
    # same stale decision was accepted
    copy = str(Path(ST.DB_PATH).with_name("legacy_copy.db"))
    shutil.copy(ST.DB_PATH, copy)
    live = ST.DB_PATH
    ST.DB_PATH = copy
    try:
        VS.review(iid, "incelendi", vkm_generation_presented=g, actor="owner", expected=VS.stored_digests())
        con = sqlite3.connect(copy)
        assert con.execute("SELECT status FROM dq_issue WHERE issue_id=?", (iid,)).fetchone()[0] == "incelendi"
        con.close()
    finally:
        ST.DB_PATH = live

    # recovery is owner-only, CSRF-protected and idempotent
    assert anon.post("/api/vkm/sync", json={}).status_code == 401
    assert fin.post("/api/vkm/sync", json={}).status_code == 403
    assert nocsrf.post("/api/vkm/sync", json={}).status_code == 403
    assert dump() == before
    r = c.post("/api/vkm/sync", json={})                                         # still failing: reported, no echo
    assert r.status_code == 503 and "locked" not in r.text
    monkeypatch.setattr(VS, "observe", real_observe)
    r = c.post("/api/vkm/sync", json={})
    assert r.status_code == 200 and "sales" in r.json()["changed"] and r.json()["sync"]["state"] == "in_sync"
    after_sync = dump()
    again = c.post("/api/vkm/sync", json={})
    assert again.status_code == 200 and again.json()["changed"] == [] and dump() == after_sync
    lst = c.get("/api/vkm/issues").json()
    assert lst["sync"]["state"] == "in_sync" and lst["vkm_generation"] != g
    r = c.post(f"/api/vkm/issues/{iid}/review", json={"action": "incelendi", "vkm_generation": g})
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "stale_generation"    # old page: still stale
    r = c.post(f"/api/vkm/issues/{iid}/review", json={"action": "incelendi", "vkm_generation": lst["vkm_generation"]})
    assert r.status_code == 200 and r.json()["issue"]["status"] == "incelendi"


@pytest.mark.production_gate
def test_out_of_sync_and_resync_never_change_the_gate(http, monkeypatch):
    c = owner(http)
    gate = ACC.current_gate()
    before = {k: (gate.decide(k).state, gate.decide(k).codes) for k in ACC.REGISTER}
    real_observe = VS.observe
    monkeypatch.setattr(VS, "observe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    APP.app.state.generations.install(manifest_of(write_folder(str(Path(ST.DB_PATH).parent / "n"), dup_sales=4)))
    monkeypatch.setattr(VS, "observe", real_observe)        # restore ONLY this patch (never monkeypatch.undo here)
    assert c.post("/api/vkm/sync", json={}).status_code == 200
    gate = ACC.current_gate()
    assert {k: (gate.decide(k).state, gate.decide(k).codes) for k in ACC.REGISTER} == before
    assert not any(gate.decide(k).state == ACC.State.VERIFIED for k in ACC.REGISTER)
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False


def test_observer_failure_is_logged_without_content(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    m = manifest_of(write_folder(str(tmp_path / "d")))
    store = GEN.GenerationStore()
    store.add_observer(VS.make_observer(lambda: {}))
    monkeypatch.setattr(VS, "observe", lambda *a, **k: (_ for _ in ()).throw(ValueError(SE.AGING_FILE)))
    assert store.install(m) is m or store.current() is not None
    assert SE.AGING_FILE not in caplog.text and "ValueError" in caplog.text
    assert VS.sync_state(VS.expected_digests(m, {}))["state"] == "out_of_sync"
