"""D-073 (prepared, owner approval PENDING): controlled VERIFIED promotion of `receivables.collections` only.

Production state stays withheld (the 45-day Cari freshness policy is not yet approved). The activation set — approve
`cari_balance_freshness` (D-073), certify the metric VERIFIED, `GENERATION_BOUND_API_EVIDENCE = True` — is applied by
tests/fixtures/d073_activation.py to the REAL production module, so the real gate, the real register validation, the
real dashboard builder and the real app routes decide. Synthetic data only (invented codes, names and amounts).
"""
import dataclasses
import datetime as dt
import json
import os
import re

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as DB
from services import access as AX
from services import accuracy as ACC
from services import cari_policy as CARI
from services import generation as GEN
from services import owner_auth as AUTH
from services import refresh as RF
from tests import accuracy_harness as H
from tests import auth_harness as AH
from tests.fixtures import d073_activation as ACT
from tests.fixtures import synthetic_erp as SE
from tests.test_m8_cp1_access import FIN_HASH, FIN_PASSWORD, HASH, PASSWORD
from tests.test_m8_cp3_upload_center import H as HDR, new_batch, publish, srv, upload, validate  # noqa: F401
from tests.test_m8_d072_cari_first_metric import _folder, _manifest

KEY = "receivables.collections"
OCT5 = dt.date(2026, 10, 5)
NAME = "Cari 5 Ekim 2026.xlsx"            # the owner's naming; the as-of date is in the name (D-072 D1)


# ---------------------------------------------------------------------------------------------
# synthetic Cari export: customer (120) and supplier (320) accounts, both signs, kuruş, a zero, an unclassified
# account, a non-account total row and USD values that must never reach a TL balance
# ---------------------------------------------------------------------------------------------
def _cari(amounts=None, fx=None):
    a = {"120.01.0001": {"Mart-2026": -1500.25}, "120.01.0002": {"Kasım-2025 ve öncesi": 2000.0, "Haziran-2026": 10.5},
         "320.01.0001": {"Mayıs-2026": -700.0}, "900.00.0005": {}, **(amounts or {})}
    names = {"120.01.0001": "Sentetik Musteri A", "120.01.0002": "Sentetik Musteri B",
             "320.01.0001": "Sentetik Tedarikci", "900.00.0005": "Sentetik Diger"}
    usd = {"120.01.0001": 4321.0, "320.01.0001": 99.0, **(fx or {})}
    rows = [{"Hesap kodu": "TOPLAM", "Hesap adı": "ara toplam", "Temsilci kodu": None,
             **{c: 999_999.0 for c in SE.AGING_COLUMNS[3:]}}]
    for code, b in a.items():
        r = {"Hesap kodu": code, "Hesap adı": names.get(code, "Sentetik Hesap"), "Temsilci kodu": "TEMSILCI-A",
             **{c: 0.0 for c in SE.AGING_COLUMNS[3:]}}
        r.update(b)
        r[CARI.FX_COLUMN] = usd.get(code, 0.0)
        rows.append(r)
    return pd.DataFrame(rows, columns=SE.AGING_COLUMNS)


EXPECTED = {"120.01.0001": -1500.25, "120.01.0002": 2010.5, "320.01.0001": -700.0, "900.00.0005": 0.0}


def _data_blob(html):
    m = re.search(r"const DATA=(\{.*?\});\n", html, re.S)
    d = json.loads(m.group(1).replace("<\\/", "</"))
    return d if "modules" in d else {"modules": d, **({"stats": d["stats"]} if "stats" in d else {})}


def _gate(m, today, register=None, definitions=None):
    reg, defs = register or ACC.REGISTER, definitions or ACC.DEFINITIONS
    return ACC.Gate(reg, defs, None, today).bound_to(m.core_snapshot())


def _built(folder, m, gate, out):
    paths, _, _ = GEN.select_core(folder)
    DB.build(folder, str(out), today=gate.today, sources=paths, frames=GEN.frames_from_frozen(GEN.freeze(paths)),
             gate=gate, manifest=m)
    html = out.read_text(encoding="utf-8")
    return html, _data_blob(html)


# ---------------------------------------------------------------------------------------------
# 1. production register: prepared, inert, and nothing else changed
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_production_register_is_prepared_but_promotes_nothing():
    m = ACC.REGISTER[KEY]
    assert m.certified == ACC.State.NOT_VERIFIED and m.blockers == ()            # REAL-DATA replaced by the ref
    assert m.definitions == ("cari_account_class", "cari_balance_freshness") and m.max_source_age_days == 45
    assert m.approval_refs == ("D-069", "D-071", "D-072", "D-073")
    assert m.reconciliation_ref == ACC.CARI_RECONCILIATION_REF and m.reconciliation_ref.startswith("D-073")
    assert not re.search(r"Sentetik|[A-ZÇĞİÖŞÜ]{3,} [A-ZÇĞİÖŞÜ]{3,}", m.reconciliation_ref)   # counts only
    for ref in m.regression_tests:                                                # every named test exists
        path, _, fn = ref.partition("::")
        assert os.path.exists(path), ref
        assert not fn or f"def {fn}(" in open(path, encoding="utf-8").read(), ref
    fresh = ACC.DEFINITIONS["cari_balance_freshness"]
    assert fresh.approved is False and fresh.decision_ref is None and fresh.max_age_days == 45
    assert ACC.DEFINITIONS["operational_freshness"].approved is False
    # unrelated metrics keep the general (unapproved) freshness policy and their own limits — unchanged
    for k, x in ACC.REGISTER.items():
        if k != KEY and x.temporal == "operational" and x.sources:
            assert "operational_freshness" in x.definitions and "cari_balance_freshness" not in x.definitions, k
            assert x.max_source_age_days == ACC.DEFAULT_OPERATIONAL_MAX_AGE_DAYS, k
    assert len(ACC.REGISTER) == 33 and not any(x.certified == ACC.State.VERIFIED for x in ACC.REGISTER.values())
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False and ACC.POLICY_VERSION == "m8-cari-d072"
    d = ACC.current_gate().decide(KEY)
    assert not d.displayable and "DEF:cari_balance_freshness" in d.codes and "REAL-DATA" not in d.codes


@pytest.mark.production_gate
def test_production_gate_withholds_the_metric_on_fresh_real_shaped_evidence(tmp_path):
    """Everything is established except the pending owner approval: the decision names exactly that."""
    m = _manifest(_folder(tmp_path / "f", cari_name=NAME, snapshot=OCT5, aging=_cari()), today=dt.date(2026, 11, 1))
    d = _gate(m, dt.date(2026, 11, 1)).decide(KEY)
    assert d.state == ACC.State.NOT_VERIFIED and set(d.codes) == {"DEF:cari_balance_freshness"}


def test_activation_is_exactly_three_changes():
    reg, defs = ACT.activated()
    changed_metrics = {k for k in reg if reg[k] != ACC.REGISTER[k]}
    changed_defs = {k for k in defs if defs[k] != ACC.DEFINITIONS[k]}
    assert changed_metrics == {KEY} and changed_defs == {"cari_balance_freshness"}
    assert dataclasses.replace(reg[KEY], certified=ACC.State.NOT_VERIFIED) == ACC.REGISTER[KEY]
    assert (defs["cari_balance_freshness"].approved, defs["cari_balance_freshness"].decision_ref) == (True, "D-073")
    assert dataclasses.replace(defs["cari_balance_freshness"], approved=False, decision_ref=None) == \
        ACC.DEFINITIONS["cari_balance_freshness"]


def test_production_validation_accepts_the_full_activation_and_refuses_every_partial_one(monkeypatch):
    reg, defs = ACT.activated()
    monkeypatch.setattr(ACC, "GENERATION_BOUND_API_EVIDENCE", True)
    ACC.validate_register(reg, defs, production=True)                              # complete activation: valid
    with pytest.raises(ACC.RegisterError, match="unapproved definitions"):         # certified, policy not approved
        ACC.validate_register(reg, dict(ACC.DEFINITIONS), production=True)
    bad = dict(reg)
    bad[KEY] = dataclasses.replace(reg[KEY], max_source_age_days=46)               # code ≠ approved policy
    with pytest.raises(ACC.RegisterError, match="differs from the cari_balance_freshness policy"):
        ACC.validate_register(bad, defs, production=True)
    bad[KEY] = dataclasses.replace(reg[KEY], reconciliation_ref="PENDING-OWNER-LOCAL-ACCEPTANCE")
    with pytest.raises(ACC.RegisterError, match="real-data reconciliation"):
        ACC.validate_register(bad, defs, production=True)
    bad[KEY] = dataclasses.replace(reg[KEY], blockers=(ACC.B_REAL,))
    with pytest.raises(ACC.RegisterError, match="open blockers"):
        ACC.validate_register(bad, defs, production=True)
    bad[KEY] = dataclasses.replace(reg[KEY], definitions=("cari_account_class",))  # no freshness policy at all
    with pytest.raises(ACC.RegisterError, match="freshness"):
        ACC.validate_register(bad, defs, production=True)
    monkeypatch.setattr(ACC, "GENERATION_BOUND_API_EVIDENCE", False)              # flag not set
    with pytest.raises(ACC.RegisterError, match="generation-bound evidence"):
        ACC.validate_register(reg, defs, production=True)
    for k in ACC.REGISTER:                                                         # no other metric can be promoted
        if k == KEY:
            continue
        other = dict(reg)
        other[k] = dataclasses.replace(reg[k], certified=ACC.State.VERIFIED)
        monkeypatch.setattr(ACC, "GENERATION_BOUND_API_EVIDENCE", True)
        with pytest.raises(ACC.RegisterError):
            ACC.validate_register(other, defs, production=True)


# ---------------------------------------------------------------------------------------------
# 2. freshness: 45 days from the file-name date
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("today,shown", [(dt.date(2026, 10, 5), True), (dt.date(2026, 11, 1), True),
                                         (dt.date(2026, 11, 19), True), (dt.date(2026, 11, 20), False),
                                         (dt.date(2027, 3, 1), False)])
def test_the_5_october_report_is_eligible_through_19_november_only(tmp_path, today, shown):
    reg, defs = ACT.activated()
    m = _manifest(_folder(tmp_path / "f", cari_name=NAME, snapshot=OCT5, aging=_cari()), today=today)
    assert m.record("aging").evidence.report_date == OCT5
    assert m.record("aging").evidence.report_date_origin == "filename"
    d = _gate(m, today, reg, defs).decide(KEY)
    assert d.displayable is shown
    if not shown:
        assert d.state == ACC.State.NOT_VERIFIED and d.codes == ("SRC:aging",)
        assert any("güncel değil" in r for r in d.reasons_tr)


def test_a_report_dated_in_the_future_is_invalid(tmp_path):
    reg, defs = ACT.activated()
    m = _manifest(_folder(tmp_path / "f", cari_name=NAME, snapshot=OCT5, aging=_cari()), today=dt.date(2026, 10, 4))
    assert _gate(m, dt.date(2026, 10, 4), reg, defs).decide(KEY).state == ACC.State.INVALID


def test_unrelated_metrics_keep_their_freshness_behaviour(tmp_path):
    reg, defs = ACT.activated()
    m = _manifest(_folder(tmp_path / "f", cari_name=NAME, snapshot=OCT5, aging=_cari()), today=dt.date(2026, 11, 1))
    g, base = _gate(m, dt.date(2026, 11, 1), reg, defs), _gate(m, dt.date(2026, 11, 1))
    for k in ACC.REGISTER:
        if k == KEY:
            continue
        assert g.decide(k).state == base.decide(k).state, k          # the verdict never changes
        # the only possible difference: a dependent no longer lists the (now verified) Cari list as a reason
        assert set(base.decide(k).codes) - set(g.decide(k).codes) <= {f"DEP:{KEY}"}, k
        assert set(g.decide(k).codes) <= set(base.decide(k).codes), k
        if "operational_freshness" in reg[k].definitions:
            assert "DEF:operational_freshness" in g.decide(k).codes, k


# ---------------------------------------------------------------------------------------------
# 3. activated: only this metric, factual content, no overdue, USD never in a TL balance
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_activated_production_gate_verifies_only_the_cari_list(tmp_path, monkeypatch):
    ACT.activate(monkeypatch)
    today = dt.date(2026, 11, 1)
    folder = _folder(tmp_path / "f", cari_name=NAME, snapshot=OCT5, aging=_cari())
    m = _manifest(folder, today=today)
    gate = ACC.current_gate(today=today).bound_to(m.core_snapshot())
    shown = {k for k in ACC.REGISTER if gate.decide(k).displayable}
    assert shown == {KEY}
    assert len([k for k in ACC.REGISTER if k != KEY and not gate.decide(k).displayable]) == 32
    html, data = _built(folder, m, gate, tmp_path / "page.html")
    assert f'<meta name="stockagent-displayed" content="{KEY}">' in html
    assert f'<meta name="stockagent-generation" content="{m.core_id}">' in html
    mods = data["modules"]
    coll = mods["collections"]
    assert "withheld" not in coll
    rows = {r["code"]: r for r in coll["rows"]}
    assert {c: r["balance"] for c, r in rows.items()} == EXPECTED                 # exact, signed, kuruş kept
    assert rows["120.01.0001"]["class_label"] == "Müşteri (120)"
    assert rows["120.01.0001"]["direction_label"] == "Halsa müşteriye borçlu"
    assert rows["320.01.0001"]["class_label"] == "Satıcı (320)"
    assert rows["320.01.0001"]["direction_label"] == CARI.DIRECTION_LABELS["credit"]
    assert rows["120.01.0002"]["direction_label"] == CARI.DIRECTION_LABELS["debit"]
    assert rows["900.00.0005"]["class_label"] == "Sınıflandırılmadı"
    assert "TOPLAM" not in rows                                                     # non-account row never a row
    assert all("overdue" not in r and "usd" not in json.dumps(r).lower() for r in coll["rows"])
    assert "Cari rapor tarihi: 5 Ekim 2026 (dosya adındaki tarih (D-072))." in coll["note"]
    assert CARI.FX_NOTE in coll["note"] and CARI.OVERDUE_RETIRED_REASON in coll["note"]
    for k, mod in mods.items():                                                     # every other module withheld
        if k != "collections":
            assert mod["rows"] == [] and mod.get("withheld"), k
    for text in ("Vadesi Geçen", "vadesi geçen toplam", "kovalayın", "Riskli Müşteri listesi", "Tahsilat önceliği"):
        assert text not in html.replace(CARI.OVERDUE_RETIRED_REASON, ""), text


@pytest.mark.production_gate
def test_usd_values_never_change_a_tl_balance_or_the_decision(tmp_path, monkeypatch):
    ACT.activate(monkeypatch)
    today = dt.date(2026, 11, 1)
    out = []
    for i, fx in enumerate(({}, {"120.01.0001": -88_888.88, "120.01.0002": 1e9, "900.00.0005": 5.0})):
        folder = _folder(tmp_path / f"f{i}", cari_name=NAME, snapshot=OCT5, aging=_cari(fx=fx))
        m = _manifest(folder, today=today)
        gate = ACC.current_gate(today=today).bound_to(m.core_snapshot())
        _, data = _built(folder, m, gate, tmp_path / f"p{i}.html")
        out.append(([(r["code"], r["balance"], r["direction_label"]) for r in data["modules"]["collections"]["rows"]],
                    gate.decide(KEY).state))
    assert out[0] == out[1] and out[0][1] == ACC.State.VERIFIED
    no_fx = _cari().drop(columns=[CARI.FX_COLUMN])
    m = _manifest(_folder(tmp_path / "nofx", cari_name=NAME, snapshot=OCT5, aging=no_fx), today=today)
    assert ACC.current_gate(today=today).bound_to(m.core_snapshot()).decide(KEY).displayable


@pytest.mark.production_gate
def test_refresh_report_never_unredacts_an_overdue_total(monkeypatch):
    ACT.activate(monkeypatch)
    gate = ACC.current_gate()                                    # verified list possible; the overdue total never
    rep = ACC.redact_refresh_report({"counts": {"overdue_total": 7, "products": 3}}, gate)
    assert rep["counts"]["overdue_total"] is None
    assert ACC.REFRESH_COUNT_METRIC["overdue_total"] == "receivables.risk"
    assert not ACC.current_gate().decide("receivables.risk").displayable


# ---------------------------------------------------------------------------------------------
# 4. evidence that is missing, changed, mismatched or unbound blocks display
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_missing_changed_mismatched_or_unbound_evidence_blocks_the_metric(tmp_path, monkeypatch):
    ACT.activate(monkeypatch)
    today = dt.date(2026, 11, 1)
    folder = _folder(tmp_path / "f", cari_name=NAME, snapshot=OCT5, aging=_cari())
    m = _manifest(folder, today=today)
    snap = m.core_snapshot()
    assert ACC.current_gate(today=today, snapshot=snap, expected_generation=snap.generation_id).decide(KEY).displayable
    # no evidence at all / evidence of another generation / the source recorded but not the served bytes
    assert ACC.current_gate(today=today).decide(KEY).codes == ("NO-EVIDENCE:aging",)
    other = ACC.current_gate(today=today, snapshot=snap, expected_generation="another-generation").decide(KEY)
    assert not other.displayable and "EVIDENCE-GENERATION:aging" in other.codes
    unbound = ACC.EvidenceSnapshot(snap.generation_id, {k: v for k, v in snap.evidence.items() if k != "aging"},
                                   unbound=frozenset({"aging"}))
    d = ACC.current_gate(today=today, snapshot=unbound, expected_generation=snap.generation_id).decide(KEY)
    assert not d.displayable and "EVIDENCE-GENERATION:aging" in d.codes
    # the pipeline disagrees with the workbook (one kuruş) → INVALID
    def tamper(frames):
        a = frames["aging"].copy()
        a.loc[a["Hesap kodu"] == "120.01.0002", "Haziran-2026"] = 10.51
        return {**frames, "aging": a}
    bad = _manifest(folder, today=today, frames_patch=tamper)
    assert ACC.current_gate(today=today).bound_to(bad.core_snapshot()).decide(KEY).state == ACC.State.INVALID
    # the mapping is not established (a duplicated month column) → withheld
    dup = _cari()
    dup.insert(6, "Ocak-2026__x", dup["Ocak-2026"])
    dup.columns = ["Ocak-2026" if c == "Ocak-2026__x" else c for c in dup.columns]
    md = _manifest(_folder(tmp_path / "dup", cari_name=NAME, snapshot=OCT5, aging=dup), today=today)
    assert not ACC.current_gate(today=today).bound_to(md.core_snapshot()).decide(KEY).displayable
    # no report date (the date is unknown) → withheld
    nodate = _manifest(_folder(tmp_path / "nd", cari_name="Cari raporu.xlsx", snapshot=None, origin=None,
                               aging=_cari()), today=today)
    assert not ACC.current_gate(today=today).bound_to(nodate.core_snapshot()).decide(KEY).displayable


# ---------------------------------------------------------------------------------------------
# 5. the real app: upload → publish → dashboard / API agree; stale; new month; rollback; restart; permissions
# ---------------------------------------------------------------------------------------------
def _publish_cari(s, day, frame, stem):
    path = s.src / f"{stem}.xlsx"
    frame.to_excel(path, index=False)
    bid, asg = new_batch(s), {}
    for slot, name, label in (("stock", SE_FILES["stock"], None), ("sales", SE_FILES["sales"], None),
                              ("aging", path.name, f"Cari {day.isoformat()}.xlsx")):
        r = upload(s, bid, name, label=label)
        assert r.status_code == 201, r.text
        asg[r.json()["file"]["file_id"]] = {"slot": slot, "declarations": {}}
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready", b["preview"]["errors"] if b["preview"] else b["message"]
    return bid


def _served(c):
    r = c.get("/")
    st = c.get("/api/accuracy/status").json()
    return r, st


from tests.test_m8_cp3_upload_center import FILES as SE_FILES  # noqa: E402


@pytest.mark.production_gate
def test_app_serves_one_generation_on_page_and_api_and_withholds_when_stale(srv, monkeypatch):
    ACT.activate(monkeypatch)
    s, today = srv, dt.date.today()
    day = today - dt.timedelta(days=10)
    _, pj = publish(s, _publish_cari(s, day, _cari(), "cari_a"))
    assert pj["batch"]["status"] == "published", pj
    r, st = _served(s.c)
    assert r.status_code == 200, r.headers.get("X-Halsa-Dashboard")
    gen, displayed = GEN.dashboard_markers(r.content)
    assert displayed == (KEY,) and gen == st["core_id"]
    assert r.headers["X-Halsa-Generation"] == st["generation_id"] and st["generation_bound_api_evidence"] is True
    states = {x["key"]: x["state"] for x in st["metrics"]}
    assert states[KEY] == "verified" and sum(v == "verified" for v in states.values()) == 1
    aging = next(x for x in st["sources"] if x["key"] == "aging")
    assert aging["bound"] and aging["declared"]["date_origin"] == "filename"
    assert aging["declared"]["snapshot_date"] == day.isoformat()
    rows = {x["code"]: x["balance"] for x in _data_blob(r.text)["modules"]["collections"]["rows"]}
    assert rows == EXPECTED
    for path in ("/api/context", "/api/agent/actions", "/api/sales-queue", "/api/scenario"):   # still withheld
        resp = s.c.post(path, json={}) if path == "/api/scenario" else s.c.get(path)
        assert resp.status_code == 409, path

    # time passes: 46 days after the report date the SAME page is no longer authorized → unavailable, never zero
    later = today + dt.timedelta(days=36)
    monkeypatch.setattr(ACC, "current_gate", lambda today=None, snapshot=None, expected_generation=None:
                        H.production_gate(today=later, snapshot=snapshot, expected_generation=expected_generation))
    r2, st2 = _served(s.c)
    assert r2.status_code == 503 and r2.headers["X-Halsa-Dashboard"] == "not_authorized"
    assert {x["key"]: x["state"] for x in st2["metrics"]}[KEY] == "not_verified"
    assert "1500" not in r2.text and "Bakiye" not in r2.text
    rep = s.c.post("/api/context/refresh").json()                                  # owner refresh: rebuilt, withheld
    assert rep["ok"], rep
    r3, st3 = _served(s.c)
    assert r3.status_code == 200 and GEN.dashboard_markers(r3.content)[1] == ()
    coll = _data_blob(r3.text)["modules"]["collections"]
    assert coll["rows"] == [] and coll["withheld"]["message"] == ACC.WITHHELD_MESSAGE
    assert "-1500" not in r3.text and "2010" not in r3.text


@pytest.mark.production_gate
def test_new_month_rebinds_rollback_keeps_and_restart_never_serves_an_obsolete_page(srv, monkeypatch):
    ACT.activate(monkeypatch)
    s, today = srv, dt.date.today()
    _, pj = publish(s, _publish_cari(s, today - dt.timedelta(days=30), _cari(), "cari_a"))
    assert pj["batch"]["status"] == "published"
    r1, st1 = _served(s.c)
    assert r1.status_code == 200
    old_page = open(s.dash, "rb").read()

    # a new monthly upload: independently validated, a new generation, its own file-name date
    new = _cari(amounts={"120.01.0001": {"Mart-2026": -1200.0}})
    _, pj2 = publish(s, _publish_cari(s, today - dt.timedelta(days=2), new, "cari_b"))
    assert pj2["batch"]["status"] == "published"
    r2, st2 = _served(s.c)
    assert r2.status_code == 200 and st2["generation_id"] != st1["generation_id"]
    assert r2.headers["X-Halsa-Generation"] == st2["generation_id"]
    assert GEN.dashboard_markers(r2.content) == (st2["core_id"], (KEY,))
    aging = next(x for x in st2["sources"] if x["key"] == "aging")
    assert aging["declared"]["snapshot_date"] == (today - dt.timedelta(days=2)).isoformat() and aging["bound"]
    assert {x["code"]: x["balance"] for x in _data_blob(r2.text)["modules"]["collections"]["rows"]}["120.01.0001"] \
        == -1200.0
    current_page = open(s.dash, "rb").read()

    # the previous, once-authorized page put back on disk is never served
    open(s.dash, "wb").write(old_page)
    r_old, _ = _served(s.c)
    assert r_old.status_code == 503 and r_old.headers["X-Halsa-Dashboard"] in ("generation_mismatch", "bytes_mismatch")
    open(s.dash, "wb").write(current_page)
    assert s.c.get("/").status_code == 200

    # a failed publication rolls back: the current generation stays served and authorized
    def boom(*a, **k):
        raise OSError("disk dolu")
    with monkeypatch.context() as mp:
        mp.setattr(RF, "_after_manifest_swap", boom)
        _, pj3 = publish(s, _publish_cari(s, today - dt.timedelta(days=1), _cari(), "cari_c"))
        assert pj3["state"] == "failed" and pj3["batch"]["status"] == "publish_failed"
    r3, st3 = _served(s.c)
    assert r3.status_code == 200 and st3["generation_id"] == st2["generation_id"] and r3.content == current_page

    # restart: re-derived from the bytes, same generation; the page served is re-authorized
    with TestClient(APP.app, base_url="https://testserver", headers=HDR) as c2:
        APP.app.state.repo.context()
        r4, st4 = _served(c2)
        assert st4["core_id"] == st2["core_id"] and st4["restore_reason"] is None
        assert r4.status_code == 200 and r4.headers["X-Halsa-Generation"] == st4["generation_id"]
        assert GEN.dashboard_markers(r4.content)[1] == (KEY,)

    # the stored Cari source changes on disk → nothing binds after a restart; nothing is shown
    sel = json.load(open(s.data / GEN.SELECTION_FILE))["core"]["aging"]["file"]
    with open(s.data / sel, "ab") as fh:
        fh.write(b"tampered")
    with TestClient(APP.app, base_url="https://testserver", headers=HDR) as c3:
        r5, st5 = _served(c3)
        assert r5.status_code == 503
        assert st5["restore_reason"] is not None or st5["generation_id"] is None


@pytest.mark.production_gate
def test_permissions_are_unchanged_by_the_promotion(srv, monkeypatch):
    ACT.activate(monkeypatch)
    s = srv
    _, pj = publish(s, _publish_cari(s, dt.date.today() - dt.timedelta(days=5), _cari(), "cari_a"))
    assert pj["batch"]["status"] == "published"
    monkeypatch.setattr(AUTH, "request_session", AH.production_request_session)   # real sessions from here on
    monkeypatch.setenv(AUTH.PASSWORD_ENV, HASH)
    monkeypatch.setenv(AUTH.FINANCE_PASSWORD_ENV, FIN_HASH)
    assert AX.ACCESS[("GET", "/")] == AX.VIEWER and AX.ACCESS[("POST", "/api/uploads")] == AX.OWNER_WRITE
    assert AX.ACCESS[("GET", "/api/finance/overview")] == AX.FINANCE_READ
    with TestClient(APP.app, base_url="https://testserver", headers=HDR) as viewer:
        assert viewer.get("/").status_code == 200                                  # public viewer: the verified list
        assert viewer.post("/api/uploads").status_code == 401
        assert viewer.post("/api/context/refresh").status_code == 401
        assert viewer.get("/api/finance/overview").status_code == 401
        assert viewer.get("/api/owner/overview").status_code == 401
    with TestClient(APP.app, base_url="https://testserver", headers=HDR) as fin:
        r = fin.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})
        assert r.status_code == 200
        fin.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert fin.get("/").status_code == 200
        ov = fin.get("/api/finance/overview")
        assert ov.status_code == 200
        for sec in ("kredi", "gider_gelir", "butce"):                              # financial sections still withheld
            assert ov.json()[sec]["data"] is None and ov.json()[sec]["withheld"]["error_code"] == "accuracy_withheld"
        assert fin.post("/api/uploads").status_code in (401, 403)
        assert fin.get("/api/owner/overview").status_code in (401, 403)
    with TestClient(APP.app, base_url="https://testserver", headers=HDR) as own:
        r = own.post("/api/auth/login", json={"password": PASSWORD})
        assert r.status_code == 200
        own.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert own.get("/").status_code == 200
        assert own.post("/api/uploads").status_code == 201
