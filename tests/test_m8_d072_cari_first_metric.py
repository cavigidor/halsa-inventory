"""D-072: owner decisions for the first Cari metric (`receivables.collections`, "Cari Bakiye (fatura yaşı)").

D1 report date = the date in the ORIGINAL file name, else the upload date (provenance recorded); D2 'Toplam Döviz
Bakiye' is USD and kept apart; D3 the export is authoritative and reproduced independently; D4 120 = Müşteri,
320 = Satıcı. Synthetic data only (invented codes, names and amounts). No metric is promoted in production.
"""
import dataclasses
import datetime as dt
import json
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as DB
from services import accuracy as ACC
from services import cari_policy as CARI
from services import context as CTX
from services import customer360 as C360
from services import generation as GEN
from tests.fixtures import c360_synth as CF
from tests.fixtures import synthetic_erp as SE
from tests.test_m8_cp3_upload_center import (FILES, H, new_batch, prepared, publish, published_set, srv,  # noqa: F401
                                             upload, validate, wait)
from tests.fixtures import fair_synth as FX

TODAY = dt.date(2026, 7, 10)
CARI_NAME = "Cari 30 Haziran 2026.xlsx"          # synthetic export, as-of 2026-06-30 by its name (D1)
BUCKETS = SE.AGING_COLUMNS[3:-1]


# ---------------------------------------------------------------------------------------------
# D1 — the date in the original file name; upload date only when the name has none
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,want", [
    ("Cari 5 Ekim 2026.xlsx", dt.date(2026, 10, 5)), ("CARİ 05 EKİM 2026.xlsx", dt.date(2026, 10, 5)),
    ("cari_5_ekim_2026.xlsx", dt.date(2026, 10, 5)), ("Cari 5 ekım 2026.xlsx", dt.date(2026, 10, 5)),
    ("Cari 1 Şubat 2026.xlsx", dt.date(2026, 2, 1)), ("Cari 1 Subat 2026.xlsx", dt.date(2026, 2, 1)),
    ("Cari 12 Aralık 2025.xls", dt.date(2025, 12, 12)), ("Cari 2026-10-05.xlsx", dt.date(2026, 10, 5)),
    ("Cari 05.10.2026.xlsx", dt.date(2026, 10, 5)), ("Cari 5 Ekim 2026 (5 Ekim 2026).xlsx", dt.date(2026, 10, 5)),
])
def test_filename_date_is_the_report_date(name, want):
    assert CARI.report_date(name, dt.date(2026, 11, 1)) == (want, "filename")


@pytest.mark.parametrize("name", ["Cari Ekim.xlsx", "Cari raporu.xlsx", "yukleme-ab12.xlsx", "Cari 5 Ekim.xlsx", None])
def test_no_date_in_the_name_falls_back_to_the_upload_date(name):
    assert CARI.report_date(name, dt.date(2026, 11, 1)) == (dt.date(2026, 11, 1), "upload_date")


@pytest.mark.parametrize("name", ["Cari 31 Şubat 2026.xlsx", "Cari 1 Ekim 2026 - 5 Ekim 2026.xlsx",
                                  "Cari 2026-13-01.xlsx"])
def test_impossible_or_several_dates_are_refused_not_guessed(name):
    with pytest.raises(CARI.AmbiguousFilenameDate):
        CARI.report_date(name, dt.date(2026, 11, 1))


def test_newest_bucket_and_sheet_title_never_decide_the_date(tmp_path):
    # the newest bucket (Haziran-2026) is older than the filename date and a title row names another date: neither
    # moves the date; only a bucket NEWER than the date contradicts it (existing D-051 check, kept)
    day, origin = CARI.report_date("Cari 5 Ekim 2026.xlsx", dt.date(2026, 11, 1))
    folder = _folder(tmp_path / "f", cari_name="Cari 5 Ekim 2026.xlsx", snapshot=day, origin=origin)
    rec = _manifest(folder, today=dt.date(2026, 10, 20)).record("aging")
    assert rec.evidence.report_date == dt.date(2026, 10, 5) and rec.evidence.report_date_origin == "filename"
    assert rec.evidence.date_contradictions == 0 and rec.evidence.period_complete is True
    early = _folder(tmp_path / "g", cari_name="Cari 31 Mayıs 2026.xlsx", snapshot=dt.date(2026, 5, 31),
                    origin="filename")
    assert _manifest(early, today=TODAY).record("aging").evidence.date_contradictions == 1   # Haziran > 31 May


# ---------------------------------------------------------------------------------------------
# D1 through the existing upload flow: persisted, carried over, re-derived after a restart
# ---------------------------------------------------------------------------------------------
def _selection(s):
    return json.load(open(s.data / GEN.SELECTION_FILE))["core"]


def test_upload_takes_the_date_from_the_original_name_and_persists_its_provenance(srv):
    s = srv
    bid, asg = new_batch(s), {}
    for slot in ("stock", "sales", "aging"):
        r = upload(s, bid, FILES[slot], label=CARI_NAME if slot == "aging" else None)
        asg[r.json()["file"]["file_id"]] = {"slot": slot, "declarations": {}}
    _, b = validate(s, bid, asg)
    src = {x["slot"]: x for x in b["preview"]["sources"]}
    decl = {d["field"]: d["value"] for d in src["aging"]["declarations"]}
    assert decl["snapshot_date"] == "2026-06-30" and decl["date_origin"].startswith("dosya adındaki tarih")
    assert CARI_NAME in decl["date_origin"]
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "published"
    core = _selection(s)
    assert core["aging"] == {"file": core["aging"]["file"], "snapshot_date": "2026-06-30", "period_complete": True,
                             "date_origin": "filename", "date_source": CARI_NAME}
    assert UF_stored(core["aging"]["file"])                              # the stored name carries no date
    rec = APP.app.state.generations.current().record("aging")
    assert (rec.evidence.report_date, rec.evidence.report_date_origin) == (dt.date(2026, 6, 30), "filename")
    core_id = s.c.get("/api/accuracy/status").json()["core_id"]
    with TestClient(APP.app, base_url="https://testserver", headers=H) as c2:       # restart: same generation
        APP.app.state.repo.context()
        st = c2.get("/api/accuracy/status").json()
        assert st["core_id"] == core_id and st["restore_reason"] is None
        aging = next(x for x in st["sources"] if x["key"] == "aging")
        assert aging["declared"]["date_origin"] == "filename" and aging["declared"]["snapshot_date"] == "2026-06-30"


def UF_stored(name):
    from services import upload_files as UF
    return UF.is_stored_name(name)


def test_a_carried_cari_report_keeps_its_date_and_provenance(srv):
    s = srv
    bid, asg = new_batch(s), {}
    for slot in ("stock", "sales", "aging"):
        r = upload(s, bid, FILES[slot], label=CARI_NAME if slot == "aging" else None)
        asg[r.json()["file"]["file_id"]] = {"slot": slot, "declarations": {}}
    validate(s, bid, asg)
    publish(s, bid)
    before = _selection(s)["aging"]
    bid2, asg2 = prepared(s, ("stock",), names={"stock": FILES["stock"]},
                          decl={"stock": {"snapshot_date": "2026-06-30"}})
    _, b = validate(s, bid2, asg2)
    assert b["status"] == "ready", b["preview"]["errors"]
    _, pj = publish(s, bid2)
    assert pj["batch"]["status"] == "published"
    assert _selection(s)["aging"] == before                              # carried unchanged, provenance included


def test_a_typed_date_that_contradicts_the_name_and_an_ambiguous_name_are_refused(srv):
    s = srv
    bid = new_batch(s)
    fid = upload(s, bid, FILES["aging"], label=CARI_NAME).json()["file"]["file_id"]
    r = s.c.post(f"/api/uploads/{bid}/validate",
                 json={"assignments": {fid: {"slot": "aging", "declarations": {"snapshot_date": "2026-06-29"}}}})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "cari_date_conflict"
    ok = s.c.post(f"/api/uploads/{bid}/validate",
                  json={"assignments": {fid: {"slot": "aging", "declarations": {"snapshot_date": "2026-06-30"}}}})
    assert ok.status_code == 202                                         # the same date is accepted
    wait(s, ok.json()["job_id"])
    bid2 = new_batch(s)
    fid2 = upload(s, bid2, FILES["aging"], label="Cari 31 Şubat 2026.xlsx").json()["file"]["file_id"]
    r2 = s.c.post(f"/api/uploads/{bid2}/validate",
                  json={"assignments": {fid2: {"slot": "aging", "declarations": {}}}})
    assert r2.status_code == 422 and r2.json()["detail"]["error_code"] == "cari_date_ambiguous"


def test_selection_rejects_an_unknown_date_origin(tmp_path):
    folder = _folder(tmp_path / "f", snapshot=dt.date(2026, 6, 30), origin="sheet_title")
    with pytest.raises(GEN.SelectionError):
        GEN.load_selection(folder)
    folder2 = _folder(tmp_path / "g", snapshot=None, origin="filename")
    with pytest.raises(GEN.SelectionError):                              # a provenance without a date
        GEN.load_selection(folder2)


# ---------------------------------------------------------------------------------------------
# D2 — the USD column is kept apart and never read by the balance
# ---------------------------------------------------------------------------------------------
def test_usd_column_never_changes_or_joins_the_tl_balance():
    frame = SE.aging_frame()
    rows, base = DB.m_collections(None, frame)
    changed = frame.copy()
    changed[CARI.FX_COLUMN] = 123_456.78                                # any USD value
    rows2, again = DB.m_collections(None, changed)
    assert again == base and rows2 == rows
    assert all("usd" not in k.lower() and "doviz" not in k.lower() for r in rows for k in r)
    req = ACC.REGISTER["receivables.collections"].requires[0]
    assert CARI.FX_COLUMN not in req.fields                              # the metric declares no USD field
    codes = {b.code for b in ACC.REGISTER["receivables.collections"].blockers}
    assert "CARI-FX" not in codes and "CARI-ACCOUNT-CLASS" not in codes and "AGING-DATE" not in codes
    assert "USD" in CARI.FX_NOTE


# ---------------------------------------------------------------------------------------------
# D4 — 120 customers, 320 suppliers; shared accounts never attributed
# ---------------------------------------------------------------------------------------------
def _row(code, buckets=None):
    d = {c: 0.0 for c in BUCKETS}
    d.update(buckets or {})
    return {"Hesap kodu": code, "Hesap adı": "Sentetik Hesap", "Temsilci kodu": "T", **d, CARI.FX_COLUMN: 0}


def test_account_list_classes_and_the_one_confirmed_sign_meaning():
    rows, _ = DB.m_collections(None, pd.DataFrame([_row("120.01.0001", {BUCKETS[0]: -10.5}),
                                                   _row("120.01.0002", {BUCKETS[0]: 10.5}),
                                                   _row("320.01.0001", {BUCKETS[0]: -7.0}),
                                                   _row("150.01.0001", {BUCKETS[0]: -1.0})],
                                                  columns=SE.AGING_COLUMNS))
    r = {x["code"]: x for x in rows}
    assert [r[c]["class_label"] for c in ("120.01.0001", "320.01.0001", "150.01.0001")] == \
        ["Müşteri (120)", "Satıcı (320)", "Sınıflandırılmadı"]
    assert r["120.01.0001"]["direction_label"] == "Halsa müşteriye borçlu"
    assert r["120.01.0002"]["direction_label"] == "Pozitif bakiye"                 # positive meaning unconfirmed
    assert r["320.01.0001"]["direction_label"] == "Negatif bakiye"
    assert r["150.01.0001"]["direction_label"] == "Negatif bakiye"
    assert all("overdue" not in x and "vade" not in json.dumps(x, ensure_ascii=False).lower() for x in rows)


def _remap(v):
    return v.replace("910.99.", "120.99.") if isinstance(v, str) else v


@pytest.fixture(scope="module")
def c360_120(tmp_path_factory):
    """The Customer 360 fixture with its 910.99.* invoice accounts re-coded as 120.99.* customer accounts."""
    folder = str(tmp_path_factory.mktemp("d072_c360"))
    rows = [{k: _remap(v) for k, v in r.items()} for r in CF.sales_rows()]
    master = [{k: _remap(v) for k, v in r.items()} for r in CF.master_rows()]
    CF.write_all(folder, sales_rows_override=rows, master_rows_override=master)
    frame = CF.aging_frame()
    frame["Hesap kodu"] = frame["Hesap kodu"].map(_remap)
    frame[CF.AGING_COLUMNS[3:]] = frame[CF.AGING_COLUMNS[3:]].astype(float)
    frame.loc[frame["Hesap kodu"] == "120.99.0001", CF.AGING_COLUMNS[5]] = -2_500.5    # unique to 910.00.0001
    frame.loc[frame["Hesap kodu"] == "120.99.0002", CF.AGING_COLUMNS[3]] = -4_000.25   # shared by two customers
    frame.to_excel(os.path.join(folder, CF.AGING_FILE), index=False)
    return C360.build_snapshot(folder, CTX.build_context(folder, inflation_rate=0.40))


def _acc(snap, code):
    return {a["account_code"]: a for a in C360.customer_detail(snap, code)["receivables"]["accounts"]}


def test_c360_unique_120_negative_account_is_confirmed_and_shared_stays_neutral(c360_120):
    snap = c360_120
    assert snap.account_customers["120.99.0001"] == ["910.00.0001"]
    a = _acc(snap, "910.00.0001")["120.99.0001"]["aging"]
    assert (a["account_class"], a["class_label"]) == ("customer", "Müşteri (120)")
    assert (a["balance"], a["direction_label"], a["overdue"]) == (-2_500.5, "Halsa müşteriye borçlu", None)
    assert sorted(snap.account_customers["120.99.0002"]) == ["910.00.0002", "910.00.0003"]
    for code in ("910.00.0002", "910.00.0003"):
        acc = _acc(snap, code)["120.99.0002"]
        assert acc["shared"] is True and acc["warning"] == C360.SHARED_ACCOUNT_WARNING
        assert (acc["aging"]["balance"], acc["aging"]["direction_label"]) == (-4_000.25, "Negatif bakiye")
        assert not any("total" in k for k in C360.customer_detail(snap, code)["receivables"])


def test_c360_unique_120_positive_account_stays_neutral(c360_120):
    a = _acc(c360_120, "910.00.0001")["910.00.0001"]["aging"]           # the customer's own (unclassified) code
    assert a["direction_label"] in ("Pozitif bakiye", "Negatif bakiye", "Sıfır bakiye")
    assert CARI.direction_label("debit", "customer", customer_account=True) == "Pozitif bakiye"


# ---------------------------------------------------------------------------------------------
# D3 — independent reproduction and the field-level mapping check feed the evidence (fail closed)
# ---------------------------------------------------------------------------------------------
def _folder(path, *, cari_name=CARI_NAME, snapshot=dt.date(2026, 6, 30), origin="filename", aging=None,
            approval=None):
    folder = SE.write_all(str(path))
    os.rename(os.path.join(folder, SE.AGING_FILE), os.path.join(folder, cari_name))
    if aging is not None:
        aging.to_excel(os.path.join(folder, cari_name), index=False)
    entry = {"file": cari_name}
    if snapshot is not None:
        entry.update({"snapshot_date": snapshot.isoformat(), "period_complete": True})
    if origin is not None:
        entry["date_origin"] = origin
        if origin == "filename":
            entry["date_source"] = cari_name
    if approval is not None:
        entry["approval"] = {"sha256": GEN.sha256_file(os.path.join(folder, cari_name)), "decision_ref": "D-999",
                             **approval}
    doc = {"schema": GEN.SELECTION_SCHEMA, "core": {"stock": {"file": SE.STOCK_FILE}, "sales": {"file": SE.SALES_FILE},
                                                    "aging": entry}}
    with open(os.path.join(folder, GEN.SELECTION_FILE), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return folder


def _manifest(folder, today=TODAY, frames_patch=None):
    paths, decl, origin = GEN.select_core(folder)
    frozen = GEN.freeze(paths)
    frames = GEN.frames_from_frozen(frozen)
    if frames_patch:
        frames = frames_patch(frames)
    return GEN.build_manifest(frozen, frames, decl, origin, today=today)


def test_reproduction_and_field_mapping_establish_totals_and_mapping(tmp_path):
    rec = _manifest(_folder(tmp_path / "f")).record("aging")
    cari = rec.observed["cari"]
    assert cari["template"] == "approved" and cari["reproduction"] == "match" and cari["mismatches"] == 0
    assert cari["accounts"] == cari["independent_accounts"] == 4
    assert rec.evidence.totals_consistent is True and rec.evidence.mapping_approved is True


def test_a_pipeline_that_disagrees_with_the_workbook_is_invalid(tmp_path):
    def tamper(frames):
        f = dict(frames)
        a = f["aging"].copy()
        a.loc[a["Hesap kodu"] == "900.00.0002", BUCKETS[-2]] = 1        # the pipeline would read another amount
        f["aging"] = a
        return f
    rec = _manifest(_folder(tmp_path / "f"), frames_patch=tamper).record("aging")
    assert rec.observed["cari"]["reproduction"] == "mismatch" and rec.evidence.totals_consistent is False
    assert any(s == ACC.State.INVALID for s, _ in rec.evidence.problems(ACC.REGISTER["receivables.collections"]
                                                                        .requires[0]))


def _scoped(rec):
    return rec.evidence.problems(ACC.REGISTER["receivables.collections"].requires[0])


def test_the_usd_column_is_neither_required_nor_read_by_the_mapping_check(tmp_path):
    """Correction 1: mapping approval rests on the fields the metric reads; the USD column may be absent or carry any
    value without changing mapping, totals, balances or the scoped judgement."""
    base = _manifest(_folder(tmp_path / "base")).record("aging")
    without = _manifest(_folder(tmp_path / "nofx", aging=SE.aging_frame().drop(columns=[CARI.FX_COLUMN]))) \
        .record("aging")
    changed = SE.aging_frame()
    changed[CARI.FX_COLUMN] = [-1.5, 9_999_999.99, 0, 42, None]                    # any USD values, one blank
    other = _manifest(_folder(tmp_path / "fxchg", aging=changed)).record("aging")
    for rec, present in ((base, True), (without, False), (other, True)):
        cari = rec.observed["cari"]
        assert cari["template"] == "approved" and cari["mapping"]["status"] == "approved"
        assert cari["mapping"]["fx_column_present"] is present and cari["mapping"]["rule"] == CARI.MAPPING_RULE_ID
        assert cari["reproduction"] == "match" and cari["mismatches"] == 0
        assert rec.evidence.mapping_approved is True and rec.evidence.totals_consistent is True
        assert _scoped(rec) == []
    assert DB.m_collections(None, SE.aging_frame())[1] == \
        DB.m_collections(None, SE.aging_frame().drop(columns=[CARI.FX_COLUMN]))[1] == \
        DB.m_collections(None, changed)[1]
    assert CARI.mapping_check(SE.AGING_COLUMNS[:-1])["status"] == "approved"
    assert CARI.mapping_check(SE.AGING_COLUMNS + ["Açıklama"])["status"] == "approved"   # unused extra column


def _dup(frame, column):
    out = frame.copy()
    i = list(out.columns).index(column)
    out.insert(i + 1, column + "__dup", out[column])
    out.columns = [column if c == column + "__dup" else c for c in out.columns]   # two identical header cells
    return out


@pytest.mark.parametrize("case,reason", [
    ("drop_account", "missing:Hesap kodu"), ("drop_name", "missing:Hesap adı"),
    ("drop_rep", "missing:Temsilci kodu"), ("dup_account", "duplicated:Hesap kodu"),
    ("dup_rep", "duplicated:Temsilci kodu"), ("corrupt_name", "missing:Hesap adı"),
    ("drop_month", "bucket_sequence"), ("dup_month", "duplicated_month_bucket"),
    ("corrupt_month", "invalid_month_bucket"), ("swap_months", "bucket_sequence"),
    ("second_oldest_bucket", "bucket_sequence"), ("no_oldest_bucket", "bucket_sequence"),
    ("legacy_single", "legacy_single_balance_column"),
])
def test_a_missing_duplicated_or_corrupted_required_column_blocks_verification(tmp_path, case, reason):
    f = SE.aging_frame()
    edits = {
        "drop_account": lambda: f.drop(columns=["Hesap kodu"]),
        "drop_name": lambda: f.drop(columns=["Hesap adı"]),
        "drop_rep": lambda: f.drop(columns=["Temsilci kodu"]),
        "dup_account": lambda: _dup(f, "Hesap kodu"),
        "dup_rep": lambda: _dup(f, "Temsilci kodu"),
        "corrupt_name": lambda: f.rename(columns={"Hesap adı": "Hesap ad"}),
        "drop_month": lambda: f.drop(columns=["Şubat-2026"]),                     # gap in the month sequence
        "dup_month": lambda: _dup(f, "Mart-2026"),
        "corrupt_month": lambda: f.rename(columns={"Mart-2026": "Mart 2026"}),    # read as a bucket, not valid
        "swap_months": lambda: f[[*SE.AGING_COLUMNS[:5], "Şubat-2026", "Ocak-2026", *SE.AGING_COLUMNS[7:]]],
        "second_oldest_bucket": lambda: f.rename(columns={"Aralık-2025": "Aralık-2025 ve öncesi"}),
        "no_oldest_bucket": lambda: f.rename(columns={"Kasım-2025 ve öncesi": "Kasım-2025"}),
        "legacy_single": lambda: f.assign(**{"Vadesi Geçen Bakiye": 0}),
    }
    frame = edits[case]()
    assert CARI.mapping_check(list(frame.columns)) == {**CARI.mapping_check(list(frame.columns)),
                                                     "status": "not_matched", "reason": reason}
    rec = _manifest(_folder(tmp_path / "f", aging=frame)).record("aging")
    assert rec.observed["cari"]["template"] != "approved" and rec.evidence.mapping_approved is not True
    assert _scoped(rec) != []                                                    # the metric is not verifiable
    if case.startswith("dup_"):
        assert rec.evidence.ambiguous_headers                                     # source scan still flags it


def test_columns_the_calculation_reads_must_be_the_columns_identified(tmp_path):
    # the workbook header is valid, but the frame the calculation reads lacks one (all-zero) month bucket: the
    # balances still agree, yet the identified and the calculated columns differ → mapping stays unknown
    def drop_bucket(frames):
        return {**frames, "aging": frames["aging"].drop(columns=["Aralık-2025"])}
    rec = _manifest(_folder(tmp_path / "f"), frames_patch=drop_bucket).record("aging")
    cari = rec.observed["cari"]
    assert cari["reproduction"] == "match"
    assert cari["template"] == "not_matched" and cari["mapping"]["reason"] == "calculation_columns_differ"
    assert rec.evidence.mapping_approved is None and _scoped(rec) != []


def test_reproduction_still_catches_a_changed_amount_without_the_usd_column(tmp_path):
    frame = SE.aging_frame().drop(columns=[CARI.FX_COLUMN])
    def tamper(frames):
        a = frames["aging"].copy()
        a["Ocak-2026"] = a["Ocak-2026"].astype(float)
        a.loc[a["Hesap kodu"] == "900.00.0006", "Ocak-2026"] = 30_000.01                   # one kuruş
        return {**frames, "aging": a}
    rec = _manifest(_folder(tmp_path / "f", aging=frame), frames_patch=tamper).record("aging")
    assert rec.observed["cari"]["template"] == "approved"
    assert rec.observed["cari"]["reproduction"] == "mismatch" and rec.observed["cari"]["mismatches"] == 1
    assert rec.evidence.totals_consistent is False
    assert any(s == ACC.State.INVALID for s, _ in _scoped(rec))


def test_an_explicit_false_declaration_still_wins(tmp_path):
    rec = _manifest(_folder(tmp_path / "g", approval={"totals_consistent": False, "mapping_approved": False})) \
        .record("aging")
    assert rec.observed["cari"]["template"] == "approved"
    assert rec.evidence.totals_consistent is False and rec.evidence.mapping_approved is False


def test_rederivation_after_restart_is_identical(tmp_path):
    folder = _folder(tmp_path / "f")
    a, b = _manifest(folder), _manifest(folder)
    assert a.core_id == b.core_id and a.record("aging").observed == b.record("aging").observed


# ---------------------------------------------------------------------------------------------
# the metric-specific release gate (D-069 Option B) — production register (D-073: this metric certified)
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_production_gate_verifies_the_cari_metric_on_its_own_evidence(tmp_path):
    m = _manifest(_folder(tmp_path / "f"))
    gate = ACC.current_gate(today=TODAY).bound_to(m.core_snapshot())
    d = gate.decide("receivables.collections")
    # no source, date, class or FX problem; D-073 replaced REAL-DATA by the reconciliation reference and approved the
    # Cari freshness policy — the factual list is VERIFIED on its own scoped evidence
    assert d.state == ACC.State.VERIFIED and d.codes == ()
    ev = m.record("aging").evidence
    scoped = ev.problems(ACC.REGISTER["receivables.collections"].requires[0])
    assert scoped == [] and set(ev.problems()) >= set(scoped)            # the strict judgement is computed unchanged
    assert len(ACC.REGISTER) == 33
    assert [k for k, x in ACC.REGISTER.items() if x.certified == ACC.State.VERIFIED] == ["receivables.collections"]
    assert ACC.GENERATION_BOUND_API_EVIDENCE is True and ACC.POLICY_VERSION == "m8-cari-d073"


@pytest.mark.production_gate
def test_the_production_register_displays_exactly_this_metric(tmp_path):
    folder = _folder(tmp_path / "f")
    m = _manifest(folder)
    gate = ACC.current_gate(today=TODAY).bound_to(m.core_snapshot())
    shown = {k for k in ACC.REGISTER if gate.decide(k).displayable}
    assert shown == {"receivables.collections"}
    late = ACC.current_gate(today=dt.date(2026, 9, 1)).bound_to(m.core_snapshot())
    assert not late.decide("receivables.collections").displayable      # older than the freshness limit → withheld
    out = tmp_path / "page.html"
    paths, decl, origin = GEN.select_core(folder)
    frozen = GEN.freeze(paths)
    DB.build(folder, str(out), today=TODAY, sources=paths, frames=GEN.frames_from_frozen(frozen), gate=gate,
             manifest=m)
    html = out.read_text(encoding="utf-8")
    assert '<meta name="stockagent-displayed" content="receivables.collections">' in html
    assert "Cari rapor tarihi: 30 Haziran 2026 (dosya adındaki tarih (D-072))." in html
    assert "Vadesi Geçen" not in html and "kovalayın" not in html
