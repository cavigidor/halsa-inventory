"""M8-VKM-1 — Veri Kontrol Merkezi (docs/m8_veri_kontrol_merkezi.md §6, D-062). Synthetic data only.

The eleven acceptance areas of the design (§6) plus the task's explicit areas:
 1 each issue type comes from its deterministic check and from nothing else (unit + synthetic workbooks)
 2 stable, value-free issue IDs across uploads with different bytes
 3 review / acknowledgement never changes a gate decision, evidence, manifest ID; no metric VERIFIED
 4 a suspected duplicate is never deleted or merged (source, counts, issue list)
 5 source bytes and SHA-256 unchanged by detection, review and carry-over
 6 carry-over: still_present (decision kept) / grown → yeniden_acildi / absent → cozuldu_kaynakta / back → reopened
 7 missing Kredi: missing_source, earlier Kredi issues source_missing, none resolved
 8 authorization (real sessions): anonymous / financial refused, reviewer from the session, CSRF / Origin, stale
   generation, viewer payload free of canaries
 9 append-only history (database triggers) that survives a restart
10 privacy: logs, IDs and API errors carry no synthetic customer names, codes, amounts or notes
11 Turkish text for every type / status / check and the "review is not approval" sentence
+ repeated refresh / restart idempotency through the real publication hooks.
"""
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as db
from services import access as AX
from services import accuracy as ACC
from services import generation as GEN
from services import owner_auth as AUTH
from services import refresh as RF
from services import store as ST
from services import vkm as V
from services import vkm_store as VS
from services.entity import ContextRepository
from tests.fixtures import synthetic_erp as SE
from tests.test_m8_accuracy2b import book, products

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "sentetik-vkm-yonetici-parolasi"          # synthetic test value
HASH = AUTH.hash_password(PASSWORD, n=2 ** 14)
FIN_PASSWORD = "sentetik-vkm-finans-parolasi"        # synthetic test value
FIN_HASH = AUTH.hash_password(FIN_PASSWORD, n=2 ** 14)
CANARY_AMOUNT = 987654.32
CANARY_NOTE = "KANARYA-NOT-Sentetik-Musteri-7781 özel not"


# ---------------------------------------------------------------------------------------------
# synthetic sources and generations
# ---------------------------------------------------------------------------------------------
def sales_rows(dup=0):
    rows = SE.sales_rows()
    rows[0] = {**rows[0], "Nettutar": CANARY_AMOUNT}
    return rows + [dict(rows[1]) for _ in range(dup)]


def write_folder(folder, *, stock=None, dup_sales=0, aging=True):
    os.makedirs(folder, exist_ok=True)
    pd.DataFrame(sales_rows(dup_sales)).to_excel(os.path.join(folder, SE.SALES_FILE), index=False)
    if stock is None:
        book(Path(folder) / SE.STOCK_FILE, products())
    else:
        book(Path(folder) / SE.STOCK_FILE, stock)
    if aging:
        SE.aging_frame().to_excel(os.path.join(folder, SE.AGING_FILE), index=False)
    elif os.path.exists(os.path.join(folder, SE.AGING_FILE)):
        os.remove(os.path.join(folder, SE.AGING_FILE))
    return folder


def manifest_of(folder):
    """The generation the refresh would build for these bytes (selection → frozen bytes → evidence → manifest)."""
    paths, decl, origin = GEN.select_core(folder)
    frozen = GEN.freeze({k: p for k, p in paths.items() if p})
    return GEN.build_manifest(frozen, GEN.frames_from_frozen(frozen), decl, origin)


def canaries():
    out = set()
    for r in SE.sales_rows():
        for k in ("Pro_kodu", "ProjeIsmi", "Musterikod", "Musteri_ismi", "sto_kod", "StokIsmi"):
            if r.get(k) and len(str(r[k])) >= 4:
                out.add(str(r[k]))
    for r in products(dup=True):
        out.update({str(r[1]), str(r[2])})
    for _, r in SE.aging_frame().iterrows():
        out.update({str(r["Hesap kodu"]), str(r["Hesap adı"])})
    out.update({"987654", "987.654", CANARY_NOTE, "KANARYA"})
    return {c for c in out if c and c != "nan"}


def assert_no_canary(text, extra=()):
    for c in canaries() | set(extra):
        assert c not in text, c


def events(issue_id=None):
    con = sqlite3.connect(ST.DB_PATH)
    q = "SELECT issue_id, event, status_after, reason, actor, note FROM dq_issue_event"
    rows = con.execute(q + (" WHERE issue_id=? ORDER BY event_id" if issue_id else " ORDER BY event_id"),
                       (issue_id,) if issue_id else ()).fetchall()
    con.close()
    return rows


def issue(issue_id):
    con = sqlite3.connect(ST.DB_PATH)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM dq_issue WHERE issue_id=?", (issue_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def by_check(source_key, check):
    con = sqlite3.connect(ST.DB_PATH)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM dq_issue WHERE source_key=? AND check_code=?", (source_key, check)).fetchone()
    con.close()
    return dict(r) if r else None


def observe_core(m, owner_paths=None):
    return VS.observe(VS.core_observations(m, owner_paths or {}, VS.previously_seen_sources()))


# ---------------------------------------------------------------------------------------------
# 1. deterministic detection — unit level: a CLEAN record produces nothing; one fact → exactly one check
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def base_manifest(tmp_path_factory):
    return manifest_of(write_folder(str(tmp_path_factory.mktemp("vkm_base") / "data")))


def clean(rec):
    ev = rec.evidence
    fields = {k: {**v, "error_cells": 0, "missing_cached_formulas": 0, "blank_cells": 0, "invalid_dates": 0,
                  "external_refs": 0, "duplicate_header": False} for k, v in (ev.fields or {}).items()}
    ev = dataclasses.replace(ev, available=True, layout_supported=True, report_date=dt.date(2026, 6, 30),
                             report_date_origin="declared", selection_explicit=True, period_complete=True,
                             date_contradictions=0, external_references=0, error_cells=0, missing_cached_formulas=0,
                             missing_required_values=0, duplicate_records=0, blank_amount_cells=0, invalid_dates=0,
                             ambiguous_headers=0, inspection_failures=(), mapping_approved=True,
                             declared_totals_consistent=None, fields=fields or None)
    val = dict(rec.validation)
    val["missing_headers"] = []
    if "rows" in val:
        rows = json.loads(json.dumps(val["rows"]))
        rows["counts"].update(duplicate_code_occurrences=0, missing_identity_rows=0)
        rows["footer_by_column"] = {k: {**d, "mismatched": 0, "error_propagated": 0, "unsupported": 0,
                                        "missing_cache": 0} for k, d in rows.get("footer_by_column", {}).items()}
        rows["ambiguous_rows"] = 0
        rows["coverage"] = {**rows.get("coverage", {}), "undefined_div0": 0, "other_error": 0}
        val["rows"] = rows
    return dataclasses.replace(rec, evidence=ev, validation=val)


def ev_set(rec, **kw):
    return dataclasses.replace(rec, evidence=dataclasses.replace(rec.evidence, **kw))


def field_set(rec, name, **kw):
    f = json.loads(json.dumps(rec.evidence.fields))
    f[name].update(kw)
    return ev_set(rec, fields=f)


def rows_set(rec, **kw):
    v = json.loads(json.dumps(rec.validation))
    for k, val in kw.items():
        if k in ("duplicate_code_occurrences", "missing_identity_rows"):
            v["rows"]["counts"][k] = val
        elif k == "coverage":
            v["rows"]["coverage"].update(val)
        elif k == "footer":
            v["rows"]["footer_by_column"] = val
        else:
            v["rows"][k] = val
    return dataclasses.replace(rec, validation=v)


CLEAN_JOINS = {"sold_products_not_in_stock": 0, "products_with_multiple_names": 0,
               "order_customers_unmatched_in_master": 0, "invoice_accounts_unmatched_in_master": 0,
               "order_codes_with_whitespace": 0, "invoice_codes_with_whitespace": 0,
               "product_codes_with_whitespace": 0, "matches_only_after_trim": 0, "order_codes_colliding_after_trim": 0,
               "order_customers_with_multiple_invoice_accounts": 0, "invoice_accounts_with_multiple_order_customers": 0,
               "master_duplicate_codes": 0, "aging_accounts_not_in_sales_invoice_accounts": 0,
               "aging_accounts_matching_order_code_only": 0}

CORE_CASES = [
    # (source, mutation, expected check)
    ("sales", lambda r: ev_set(r, inspection_failures=("scan:X",)), "inspection_failed"),
    ("stock", lambda r: ev_set(r, layout_supported=False), "layout_unsupported"),
    ("sales", lambda r: field_set(r, "Nettutar", duplicate_header=True), "duplicate_header"),
    ("aging", lambda r: ev_set(r, selection_explicit=False), "selection_not_explicit"),
    ("stock", lambda r: ev_set(r, report_date=None), "report_date_missing"),
    ("sales", lambda r: ev_set(r, period_complete=None), "period_unknown"),
    ("sales", lambda r: ev_set(r, period_complete=False), "period_incomplete"),
    ("sales", lambda r: ev_set(r, date_contradictions=2), "date_contradiction"),
    ("aging", lambda r: ev_set(r, external_references=3), "external_links"),
    ("stock", lambda r: ev_set(r, mapping_approved=False), "mapping_not_approved"),
    ("sales", lambda r: ev_set(r, mapping_approved=None), "mapping_unknown"),
    ("stock", lambda r: ev_set(r, declared_totals_consistent=False), "declared_totals_inconsistent"),
    ("sales", lambda r: field_set(r, "Nettutar", error_cells=2), "field_excel_errors"),
    ("stock", lambda r: field_set(r, "Envanter değeri", missing_cached_formulas=4), "field_missing_cache"),
    ("sales", lambda r: field_set(r, "Tarih", invalid_dates=1), "field_invalid_dates"),
    ("sales", lambda r: field_set(r, "Nettutar", blank_cells=1), "required_blank"),
    ("aging", lambda r: field_set(r, "bucket:*", blank_cells=7), "amount_blank"),
    ("stock", lambda r: rows_set(r, duplicate_code_occurrences=1), "stock_duplicate_code"),
    ("stock", lambda r: rows_set(r, missing_identity_rows=2), "stock_missing_identity"),
    ("stock", lambda r: rows_set(r, footer={"H": {"checked": 2, "mismatched": 1}}), "footer_mismatch"),
    ("stock", lambda r: rows_set(r, footer={"H": {"checked": 2, "error_propagated": 2}}), "footer_partial"),
    ("stock", lambda r: rows_set(r, footer={"D": {"checked": 2, "missing_cache": 1}}), "footer_missing_cache"),
    ("stock", lambda r: rows_set(r, ambiguous_rows=1), "footer_ambiguous"),
    ("stock", lambda r: rows_set(r, coverage={"undefined_div0": 5, "undefined_with_stock": 2}),
     "stock_coverage_undefined"),
    ("stock", lambda r: rows_set(r, coverage={"other_error": 1}), "stock_coverage_error"),
    ("sales", lambda r: ev_set(r, duplicate_records=2), "sales_duplicate_rows"),
    ("aging", lambda r: ev_set(r, duplicate_records=1), "aging_duplicate_accounts"),
]
JOIN_CASES = [("sold_products_not_in_stock", "sales", "join_sales_product_not_in_stock"),
              ("products_with_multiple_names", "sales", "products_multiple_names"),
              ("order_customers_unmatched_in_master", "sales", "join_order_customer_not_in_master"),
              ("invoice_accounts_unmatched_in_master", "sales", "join_invoice_account_not_in_master"),
              ("order_codes_with_whitespace", "sales", "code_whitespace"),
              ("order_customers_with_multiple_invoice_accounts", "sales", "dq5_multiple_invoice_accounts"),
              ("master_duplicate_codes", "liste", "master_duplicate_codes"),
              ("aging_accounts_not_in_sales_invoice_accounts", "aging", "join_aging_account_not_in_sales"),
              ("aging_accounts_matching_order_code_only", "aging", "dq6_aging_order_code_only")]


def test_clean_records_produce_no_issue(base_manifest):
    for r in base_manifest.core:
        assert V.detect_core(clean(r), CLEAN_JOINS) == [], r.key
    assert V.detect_core(clean(base_manifest.record("sales")), {"failure": "X"})[0].check == "joins_not_checked"


@pytest.mark.parametrize("source,mutate,check", CORE_CASES, ids=[c[2] for c in CORE_CASES])
def test_each_core_check_comes_from_its_own_fact_and_nothing_else(base_manifest, source, mutate, check):
    rec = mutate(clean(base_manifest.record(source)))
    got = V.detect_core(rec, CLEAN_JOINS)
    assert [i.check for i in got] == [check]
    i = got[0]
    assert i.issue_type == V.CHECKS[check].issue_type and i.severity in V.SEVERITIES
    assert i.issue_id == V.issue_id(i.issue_type, source, i.locator)


@pytest.mark.parametrize("key,source,check", JOIN_CASES, ids=[c[2] for c in JOIN_CASES])
def test_each_relationship_check_is_attributed_to_one_source(base_manifest, key, source, check):
    joins = {**CLEAN_JOINS, key: 3}
    found = {r.key: [i.check for i in V.detect_core(clean(r), joins)] for r in base_manifest.core}
    assert found[source] == [check]
    assert all(v == [] for k, v in found.items() if k != source), found


def kredi_record(**validation):
    ev = ACC.SourceEvidence("kredi", available=True, layout_supported=True, report_date=dt.date(2026, 6, 30),
                            error_cells=0, missing_cached_formulas=0, missing_required_values=0, duplicate_records=0,
                            external_references=0, totals_consistent=True, date_contradictions=0,
                            selection_explicit=True)
    val = {"ok": True, "failed_step": None, "negative_remaining_rows": 0, "closed_with_positive_remaining": 0,
           "hidden_installment_rows": 0, **validation}
    return GEN.SourceRecord("kredi", "owner", "loan_installments", "installment_schedule_snapshot", "k.xlsx",
                            "a" * 64, 10, "env_path", {"snapshot_date": dt.date(2026, 6, 30)}, {}, val, ev)


OWNER_CASES = [
    (lambda r: ev_set(r, error_cells=2), "excel_errors"),
    (lambda r: ev_set(r, missing_cached_formulas=1), "missing_cache"),
    (lambda r: ev_set(r, missing_required_values=3), "owner_required_blank"),
    (lambda r: ev_set(r, duplicate_records=1), "owner_duplicates"),
    (lambda r: ev_set(r, totals_consistent=False), "totals_contradiction"),
    (lambda r: ev_set(r, report_date=None), "report_date_missing"),
    (lambda r: dataclasses.replace(r, validation={**r.validation, "negative_remaining_rows": 2}),
     "kredi_negative_remaining"),
    (lambda r: dataclasses.replace(r, validation={**r.validation, "closed_with_positive_remaining": 4}),
     "kredi_closed_with_remaining"),
    (lambda r: dataclasses.replace(r, validation={**r.validation, "hidden_installment_rows": 9}), "kredi_hidden_rows"),
    (lambda r: dataclasses.replace(ev_set(r, layout_supported=False, inspection_failures=("import:layout",)),
                                   validation={**r.validation, "ok": False, "failed_step": "layout"}),
     "layout_unsupported"),
    (lambda r: dataclasses.replace(ev_set(r, inspection_failures=("import:schema",)),
                                   validation={**r.validation, "ok": False, "failed_step": "schema"}), "import_failed"),
]


def test_clean_owner_record_produces_no_issue():
    assert V.detect_owner(kredi_record()) == []


@pytest.mark.parametrize("mutate,check", OWNER_CASES, ids=[c[1] for c in OWNER_CASES])
def test_each_owner_check_comes_from_its_own_fact_and_nothing_else(mutate, check):
    assert [i.check for i in V.detect_owner(mutate(kredi_record()))] == [check]


def test_every_issue_type_of_the_design_is_produced_by_some_check():
    produced = {c.issue_type for c in V.CHECKS.values()}
    assert produced == set(V.TYPES), set(V.TYPES) - produced
    assert V.missing_issue("kredi").issue_type == "missing_source"


def test_kredi_durum_is_never_interpreted_as_payment():
    t = V.texts("kredi_closed_with_remaining", "kredi")
    assert "ödeme bilgisi olarak yorumlamaz" in t["explain"] and "netleşmedi" in t["explain"]
    rec = dataclasses.replace(kredi_record(), validation={**kredi_record().validation, "closed_with_positive_remaining": 4})
    assert V.detect_owner(rec)[0].issue_type == "unresolved_mapping"        # never "paid" / "resolved"


# ---------------------------------------------------------------------------------------------
# 1 (workbook level) + 2: real synthetic workbooks through the real pipeline
# ---------------------------------------------------------------------------------------------
def test_synthetic_workbooks_produce_their_issues(tmp_path):
    from tests.test_m8_accuracy2b import agg_row
    rows = products(dup=True)
    f = write_folder(str(tmp_path / "d"), stock=rows, dup_sales=2)
    book(Path(f) / SE.STOCK_FILE, rows, footer=("sum", "label", lambda r, a, b, n: agg_row(r, a, b, n, "9", tamper="H")))
    m = manifest_of(f)
    checks = {(i.source_key, i.check) for r in m.core for i in V.detect_core(r, m.joins)}
    for want in [("stock", "stock_duplicate_code"), ("stock", "stock_coverage_undefined"), ("stock", "footer_mismatch"),
                 ("sales", "sales_duplicate_rows"), ("stock", "selection_not_explicit"), ("stock", "report_date_missing"),
                 ("sales", "join_sales_product_not_in_stock"), ("sales", "period_unknown")]:
        assert want in checks, want
    mm = [i for r in m.core for i in V.detect_core(r, m.joins) if i.check == "footer_mismatch"]
    assert [i.column for i in mm] == ["H"]
    dup = next(i for r in m.core for i in V.detect_core(r, m.joins) if i.check == "stock_duplicate_code")
    assert dict(dup.counts)["occurrences"] == 1


def test_missing_aging_export_is_a_missing_source(tmp_path):
    m = manifest_of(write_folder(str(tmp_path / "d"), aging=False))
    obs = VS.core_observations(m, {}, ())
    assert obs["aging"].state == "missing" and obs["kredi"].state == "missing"
    assert obs["stock"].state == "present" and obs["sales"].state == "present"
    assert [i.check for i in obs["aging"].all_issues("aging")] == ["missing_source"]


def test_issue_ids_are_stable_across_uploads_and_contain_no_value(tmp_path):
    a = manifest_of(write_folder(str(tmp_path / "a"), stock=products(n=5, dup=True)))
    rows_b = products(n=8, dup=True)                       # other products, other quantities: other bytes
    rows_b = [[*r[:3], (r[3] or 0) + 11, *r[4:]] for r in rows_b]
    b = manifest_of(write_folder(str(tmp_path / "b"), stock=rows_b))
    assert a.record("stock").sha256 != b.record("stock").sha256
    ids_a = {i.check: i.issue_id for r in a.core for i in V.detect_core(r, a.joins)}
    ids_b = {i.check: i.issue_id for r in b.core for i in V.detect_core(r, b.joins)}
    assert ids_a["stock_duplicate_code"] == ids_b["stock_duplicate_code"]
    assert ids_a["stock_coverage_undefined"] == ids_b["stock_coverage_undefined"]
    for r in (*a.core, *b.core):
        for i in V.detect_core(r, a.joins):
            assert re.fullmatch(r"VKM-[0-9a-f]{16}", i.issue_id)
            blob = json.dumps([i.issue_id, i.locator, i.labels, V.texts(i.check, i.source_key, i.field_name,
                                                                         i.column)], ensure_ascii=False)
            assert_no_canary(blob)


# ---------------------------------------------------------------------------------------------
# 6 / 7 / repeated observation: carry-over in the store
# ---------------------------------------------------------------------------------------------
def test_carryover_still_present_grown_absent_and_returned(tmp_path):
    f = str(tmp_path / "d")
    m1 = manifest_of(write_folder(f, stock=products(dup=True)))
    observe_core(m1)
    dup = by_check("stock", "stock_duplicate_code")
    gen = VS.vkm_generation()
    VS.review(dup["issue_id"], "kabul_edildi", vkm_generation_presented=gen, actor="owner", note="bilinen")
    # same problem in next month's (different) bytes → still_present, decision kept
    rows2 = [[*r[:3], (r[3] or 0) + 5, *r[4:]] for r in products(dup=True)]
    m2 = manifest_of(write_folder(f, stock=rows2))
    observe_core(m2)
    d = issue(dup["issue_id"])
    assert d["status"] == "kabul_edildi" and d["presence"] == "present"
    assert events(dup["issue_id"])[-1][1] == "still_present"
    assert d["first_seen_source_sha"] == m1.record("stock").sha256 and d["last_seen_source_sha"] == m2.record("stock").sha256
    # grown (two duplicated codes) → yeniden_acildi
    rows3 = rows2 + [["G1", "SP-101", "Sentetik Ürün 1 (ikinci)", 3, 2.0, 1.0, 3.0, 5.0, "", None]]
    observe_core(manifest_of(write_folder(f, stock=rows3)))
    d = issue(dup["issue_id"])
    assert d["status"] == "yeniden_acildi" and events(dup["issue_id"])[-1][1:4] == ("reopened", "yeniden_acildi",
                                                                                  "counts_increased")
    # absent from an UPLOADED source → resolved in the source (not by review)
    observe_core(manifest_of(write_folder(f, stock=products(dup=False))))
    d = issue(dup["issue_id"])
    assert d["status"] == "cozuldu_kaynakta" and d["presence"] == "absent"
    assert events(dup["issue_id"])[-1][1] == "not_present"
    # it comes back → reopened
    observe_core(manifest_of(write_folder(f, stock=products(dup=True))))
    d = issue(dup["issue_id"])
    assert d["status"] == "yeniden_acildi" and d["presence"] == "present"
    assert events(dup["issue_id"])[-1][1:4] == ("reopened", "yeniden_acildi", "returned")


def test_missing_kredi_month_never_resolves_earlier_kredi_issues(tmp_path):
    m = manifest_of(write_folder(str(tmp_path / "d")))
    rec = dataclasses.replace(kredi_record(), validation={**kredi_record().validation, "closed_with_positive_remaining": 4,
                                                          "negative_remaining_rows": 1})
    VS.observe({"kredi": VS.owner_observation(m.with_owner("kredi", rec), "kredi", rec)})
    kr = [by_check("kredi", c)["issue_id"] for c in ("kredi_closed_with_remaining", "kredi_negative_remaining")]
    VS.review(kr[0], "incelendi", vkm_generation_presented=VS.vkm_generation(), actor="owner")
    # next month: no Kredi file (not configured) → missing_source; earlier issues source_missing, none resolved
    observe_core(m)
    miss = by_check("kredi", "missing_source")
    assert miss and miss["status"] == "acik" and miss["presence"] == "present"
    for iid in kr:
        d = issue(iid)
        assert d["status"] != "cozuldu_kaynakta" and d["presence"] == "source_missing"
        assert events(iid)[-1][1] == "source_missing"
    assert issue(kr[0])["status"] == "incelendi"                                    # the decision is kept
    # configured but the file is gone → also missing (the loader is never reached)
    observe_core(m, {"kredi": str(tmp_path / "yok.xlsx")})
    assert issue(kr[1])["presence"] == "source_missing"
    # the file is back: the missing_source issue resolves in the source; still-present issues come back present
    VS.observe({"kredi": VS.owner_observation(m.with_owner("kredi", rec), "kredi", rec)})
    assert issue(miss["issue_id"])["status"] == "cozuldu_kaynakta"
    assert issue(kr[0])["presence"] == "present" and issue(kr[0])["status"] == "incelendi"
    assert events(kr[0])[-1][3] == "source_returned"


def test_optional_owner_sources_are_expected_only_once_configured_or_seen(base_manifest, tmp_path):
    obs = VS.core_observations(base_manifest, {}, ())
    assert "kredi" in obs and "gider_gelir" not in obs and "butce" not in obs
    obs = VS.core_observations(base_manifest, {"butce": str(tmp_path / "yok.xlsx")}, {"gider_gelir"})
    assert obs["butce"].state == "missing" and obs["gider_gelir"].state == "missing"
    p = tmp_path / "var.xlsx"
    p.write_bytes(b"x")
    assert "butce" not in VS.core_observations(base_manifest, {"butce": str(p)}, ())   # its own loader observes it


def test_repeated_observation_changes_and_appends_nothing(base_manifest):
    assert observe_core(base_manifest)["changed"]
    before = (events(), VS.vkm_generation(), VS.list_issues(status="all")["issues"])
    for _ in range(3):
        assert observe_core(base_manifest)["changed"] == []
    assert (events(), VS.vkm_generation(), VS.list_issues(status="all")["issues"]) == before


# ---------------------------------------------------------------------------------------------
# 4 / 9: no deletion, append-only history (enforced by the database itself), restart
# ---------------------------------------------------------------------------------------------
def test_history_is_append_only_and_issues_are_never_deleted(base_manifest):
    observe_core(base_manifest)
    con = sqlite3.connect(ST.DB_PATH)
    for sql in ("UPDATE dq_issue_event SET note='x'", "DELETE FROM dq_issue_event", "DELETE FROM dq_issue",
                "UPDATE dq_observation SET state='x'", "DELETE FROM dq_observation"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql)
    con.close()
    for mod in ("services/vkm.py", "services/vkm_store.py", "app.py"):
        src = (ROOT / mod).read_text(encoding="utf-8")
        assert not re.search(r"(?i)\bUPDATE\s+dq_issue_event\b|\bDELETE\s+FROM\s+dq_", src), mod
        assert not re.search(r"(?i)\bUPDATE\s+dq_observation\b", src), mod


def test_history_and_decisions_survive_a_restart(base_manifest):
    observe_core(base_manifest)
    iid = VS.list_issues()["issues"][0]["issue_id"]
    VS.review(iid, "incelendi", vkm_generation_presented=VS.vkm_generation(), actor="owner", note="kalıcı not")
    before = (issue(iid), events(iid))
    ST.init_db()                                             # what the next process start does (CREATE IF NOT EXISTS)
    VS.init_tables(sqlite3.connect(ST.DB_PATH))
    assert (issue(iid), events(iid)) == before
    assert observe_core(base_manifest)["changed"] == []     # the restart's re-derived generation changes nothing
    assert issue(iid)["status"] == "incelendi"


def test_a_suspected_duplicate_is_never_deleted_or_merged(tmp_path):
    f = write_folder(str(tmp_path / "d"), stock=products(dup=True), dup_sales=2)
    sha = {n: GEN.sha256_file(os.path.join(f, n)) for n in os.listdir(f)}
    m = manifest_of(f)
    observe_core(m)
    for check in ("stock_duplicate_code", "sales_duplicate_rows"):
        iid = by_check("stock" if check.startswith("stock") else "sales", check)["issue_id"]
        before_counts = issue(iid)["detail_counts"]
        g = VS.vkm_generation()
        VS.review(iid, "incelendi", vkm_generation_presented=g, actor="owner")
        VS.review(iid, "kabul_edildi", vkm_generation_presented=g, actor="owner")
        observe_core(manifest_of(f))                        # carry-over of the same bytes
        d = issue(iid)
        assert d is not None and d["detail_counts"] == before_counts and d["presence"] == "present"
        assert iid in {x["issue_id"] for x in VS.list_issues()["issues"]}
    assert {n: GEN.sha256_file(os.path.join(f, n)) for n in os.listdir(f)} == sha      # 5: bytes untouched
    m2 = manifest_of(f)
    assert m2.record("stock").validation["rows"]["counts"]["duplicate_code_occurrences"] == 1
    assert m2.record("sales").evidence.duplicate_records == 2
    assert m2.core_id == m.core_id


# ---------------------------------------------------------------------------------------------
# 3: review never certifies — gate decisions, evidence, manifest identical; nothing VERIFIED
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_review_and_acknowledgement_never_change_gate_evidence_or_manifest(base_manifest):
    gate = lambda: ACC.current_gate(snapshot=base_manifest.core_snapshot(),       # noqa: E731
                                    expected_generation=base_manifest.generation_id)
    decisions = lambda: {k: (gate().decide(k).state, gate().decide(k).codes, gate().decide(k).reasons_tr)  # noqa: E731
                         for k in ACC.REGISTER}
    manifest_before = json.dumps(base_manifest.to_dict(), sort_keys=True, default=str)
    d0 = decisions()
    observe_core(base_manifest)
    for i in VS.list_issues()["issues"]:
        g = VS.vkm_generation()
        VS.review(i["issue_id"], "incelendi", vkm_generation_presented=g, actor="owner", note="incelendi")
        VS.review(i["issue_id"], "kabul_edildi", vkm_generation_presented=g, actor="owner")
        VS.add_note(i["issue_id"], "onay değildir", vkm_generation_presented=g, actor="owner")
    assert decisions() == d0
    assert json.dumps(base_manifest.to_dict(), sort_keys=True, default=str) == manifest_before
    assert all(s != ACC.State.VERIFIED for s, _, _ in d0.values())
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    ACC.validate_register()
    assert all(m.certified != ACC.State.VERIFIED for m in ACC.REGISTER.values())


def test_vkm_modules_never_write_evidence_or_the_gate():
    for mod in ("services/vkm.py", "services/vkm_store.py"):
        src = (ROOT / mod).read_text(encoding="utf-8")
        for forbidden in ("GENERATION_BOUND_API_EVIDENCE =", "totals_consistent=", "mapping_approved=",
                          "period_complete=", "certified=", "write_manifest", "replace_owner(", ".install(",
                          "approval_refs", "openpyxl", "to_excel", "load_workbook"):
            assert forbidden not in src, (mod, forbidden)


# ---------------------------------------------------------------------------------------------
# review rules (store level)
# ---------------------------------------------------------------------------------------------
def test_review_transitions_notes_and_stale_generation(base_manifest):
    observe_core(base_manifest)
    iid = VS.list_issues()["issues"][0]["issue_id"]
    g = VS.vkm_generation()
    with pytest.raises(VS.VKMError) as e:
        VS.review(iid, "incelendi", vkm_generation_presented="vkm-eski", actor="owner")
    assert e.value.status == 409 and e.value.code == "stale_generation"
    with pytest.raises(VS.VKMError) as e:
        VS.review(iid, "acik", vkm_generation_presented=g, actor="owner")          # already open
    assert e.value.code == "transition_not_allowed"
    with pytest.raises(VS.VKMError) as e:
        VS.add_note(iid, "  \x00 ", vkm_generation_presented=g, actor="owner")
    assert e.value.code == "note_empty"
    with pytest.raises(VS.VKMError) as e:
        VS.add_note(iid, "x" * 501, vkm_generation_presented=g, actor="owner")
    assert e.value.code == "note_too_long"
    d = VS.review(iid, "incelendi", vkm_generation_presented=g, actor="owner", note="a\x07b")
    assert d["issue"]["status"] == "incelendi" and d["history"][0]["note"] == "ab"
    assert [a["key"] for a in d["issue"]["allowed_actions"]] == ["kabul_edildi", "acik"]
    d = VS.review(iid, "acik", vkm_generation_presented=g, actor="owner")
    assert d["issue"]["status"] == "acik" and d["history"][0]["reason"] == "owner"
    # a resolved issue cannot be reviewed (only noted)
    con = sqlite3.connect(ST.DB_PATH)
    con.execute("UPDATE dq_issue SET status='cozuldu_kaynakta', presence='absent' WHERE issue_id=?", (iid,))
    con.commit()
    con.close()
    with pytest.raises(VS.VKMError) as e:
        VS.review(iid, "incelendi", vkm_generation_presented=g, actor="owner")
    assert e.value.code == "transition_not_allowed"
    assert VS.add_note(iid, "kaynakta düzeldi", vkm_generation_presented=g, actor="owner")["issue"]["notes"] == 2
    # a new observation makes the old token stale
    VS.observe({"stock": VS.Observation("missing", "g2")})
    with pytest.raises(VS.VKMError) as e:
        VS.add_note(iid, "x", vkm_generation_presented=g, actor="owner")
    assert e.value.code == "stale_generation"


# ---------------------------------------------------------------------------------------------
# 11: Turkish UI text
# ---------------------------------------------------------------------------------------------
def test_turkish_text_for_every_type_status_check_and_event():
    tr = re.compile(r"[a-zçğıöşüA-ZÇĞİÖŞÜ]")
    for d in (V.STATUS_TR, V.SEVERITY_TR, V.PRESENCE_TR, V.EVENT_TR, V.REASON_TR, V.SOURCE_TR):
        assert all(v and tr.search(v) for v in d.values())
    assert set(V.STATUS_TR) == set(V.STATUSES) and set(V.EVENT_TR) == set(V.EVENTS)
    assert all(t["label"] for t in V.TYPES.values())
    for check in V.CHECKS:
        for src in (*V.CORE_SOURCES, *V.OWNER_SOURCES):
            t = V.texts(check, src, "Nettutar", "D")
            assert t["title"] and t["explain"] and t["action"]
            assert not any("{" in v or "}" in v for v in t.values()), (check, src)
    assert V.NOT_APPROVAL_TR == "İnceleme bir onay değildir; metrikler yalnızca doğrulanmış kanıtla gösterilir."


def test_dashboard_has_the_vkm_tab_before_the_server_driven_tabs(base_manifest, tmp_path):
    f = write_folder(str(tmp_path / "d"))
    out = str(tmp_path / "dash.html")
    db.build(f, out)
    h = Path(out).read_text(encoding="utf-8")
    tabs = re.findall(r'<button class="tab" data-t="([a-z0-9]+)"', h)
    assert tabs[-8:] == ["vkm", "sq", "c360", "opp", "fair", "crm", "own", "ai"]
    block = h[h.index("<!--VKM-TAB-START-->"):h.index("<!--VKM-TAB-END-->")]
    assert '<button class="tab" data-t="vkm">Veri Kontrol</button>' in h and "Veri Kontrol Merkezi" in block
    assert V.NOT_APPROVAL_TR in block
    js = block[block.index("<script>"):]
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js
    assert "/api/vkm/summary" in js and "localStorage" not in js


# ---------------------------------------------------------------------------------------------
# 8 / 10: HTTP — real sessions (production_auth), privacy of every response, logs and errors
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def http(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(AUTH.PASSWORD_ENV, HASH)
    monkeypatch.setenv(AUTH.FINANCE_PASSWORD_ENV, FIN_HASH)
    f = write_folder(str(tmp_path / "d"), stock=products(dup=True), dup_sales=2)
    observe_core(manifest_of(f))
    rec = dataclasses.replace(kredi_record(), validation={**kredi_record().validation, "closed_with_positive_remaining": 2})
    VS.observe({"kredi": VS.owner_observation(manifest_of(f).with_owner("kredi", rec), "kredi", rec)})
    APP.app.dependency_overrides[APP.get_repo] = lambda: ContextRepository(context={"actions": []})
    clients = []
    caplog.set_level(logging.DEBUG)

    def make(base_url="https://testserver", **kw):
        kw.setdefault("headers", {"X-StockAgent": "1"})
        c = TestClient(APP.app, base_url=base_url, **kw)
        c.__enter__()
        clients.append(c)
        return c
    yield make
    for c in clients:
        c.__exit__(None, None, None)
    APP.app.dependency_overrides.clear()


def owner(make):
    c = make()
    r = c.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def finance(make):
    c = make()
    r = c.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def dump():
    con = sqlite3.connect(ST.DB_PATH)
    out = {t: sorted(map(repr, con.execute(f"SELECT * FROM {t}").fetchall()))
           for t in ("dq_issue", "dq_issue_event", "dq_observation", "dq_source_state")}
    con.close()
    return out


def test_vkm_routes_are_registered_at_the_agreed_levels():
    assert AX.ACCESS[("GET", "/api/vkm/summary")] == AX.VIEWER
    assert AX.ACCESS[("GET", "/api/vkm/issues")] == AX.OWNER_READ
    assert AX.ACCESS[("GET", "/api/vkm/issues/{issue_id}")] == AX.OWNER_READ
    assert AX.ACCESS[("POST", "/api/vkm/issues/{issue_id}/review")] == AX.OWNER_WRITE
    assert AX.ACCESS[("POST", "/api/vkm/issues/{issue_id}/notes")] == AX.OWNER_WRITE
    for k in [k for k in AX.ACCESS if "/api/vkm/" in k[1]]:
        assert ACC.ROUTES[k][0] in ("system", "operational")


@pytest.mark.production_auth
def test_viewer_summary_is_allow_listed_and_free_of_private_detail(http):
    owner_c = owner(http)
    iid = owner_c.get("/api/vkm/issues").json()["issues"][0]["issue_id"]
    g = owner_c.get("/api/vkm/issues").json()["vkm_generation"]
    assert owner_c.post(f"/api/vkm/issues/{iid}/notes", json={"note": CANARY_NOTE, "vkm_generation": g}).status_code == 200
    for c in (http(), finance(http), owner_c):
        r = c.get("/api/vkm/summary")
        assert r.status_code == 200
        j = r.json()
        assert set(j) == {"observed", "last_observed_on", "open_total", "by_severity", "by_status", "by_type", "notice",
                          "detail_requires_owner", "ai_used"}
        assert j["observed"] is True and j["open_total"] > 0 and j["notice"] == V.NOT_APPROVAL_TR
        assert all(set(t) == {"key", "label", "open"} for t in j["by_type"])
        text = r.text
        assert "VKM-" not in text and "sha" not in text.lower() and ".xlsx" not in text
        for src_label in V.SOURCE_TR.values():
            assert src_label not in text
        assert_no_canary(text)


@pytest.mark.production_auth
@pytest.mark.parametrize("path", ["/api/vkm/issues", "/api/vkm/issues/VKM-0123456789abcdef"])
def test_issue_details_are_owner_only(http, path):
    r = http().get(path)
    assert r.status_code == 401 and r.json()["detail"]["error_code"] == "auth_required"
    r = finance(http).get(path)
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "owner_required"


@pytest.mark.production_auth
def test_anonymous_and_financial_decisions_are_refused_and_change_nothing(http):
    owner_c = owner(http)
    lst = owner_c.get("/api/vkm/issues").json()
    iid, g = lst["issues"][0]["issue_id"], lst["vkm_generation"]
    before = dump()
    for c, code in ((http(), "auth_required"), (finance(http), "owner_required")):
        for path, body in ((f"/api/vkm/issues/{iid}/review", {"action": "incelendi", "vkm_generation": g}),
                           (f"/api/vkm/issues/{iid}/review", {"action": "kabul_edildi", "vkm_generation": g}),
                           (f"/api/vkm/issues/{iid}/review", {"action": "acik", "vkm_generation": g}),
                           (f"/api/vkm/issues/{iid}/notes", {"note": CANARY_NOTE, "vkm_generation": g})):
            r = c.post(path, json=body, headers={"X-StockAgent": "1", "X-CSRF-Token": "gecersiz"})
            assert r.status_code in (401, 403) and r.json()["detail"]["error_code"] == code, (path, r.text)
    assert dump() == before


@pytest.mark.production_auth
def test_owner_writes_need_csrf_and_same_origin_and_the_reviewer_is_the_session(http):
    c = owner(http)
    lst = c.get("/api/vkm/issues").json()
    iid, g = lst["issues"][0]["issue_id"], lst["vkm_generation"]
    tok = c.headers.pop("X-CSRF-Token")
    before = dump()
    body = {"action": "incelendi", "vkm_generation": g}
    for hdr in ({}, {"X-CSRF-Token": "yanlis"}, {"X-CSRF-Token": tok + "x"}):
        r = c.post(f"/api/vkm/issues/{iid}/review", json=body, headers=hdr)
        assert r.status_code == 403 and r.json()["detail"]["error_code"] == "csrf_failed"
    r = c.post(f"/api/vkm/issues/{iid}/review", json=body, headers={"X-CSRF-Token": tok, "Origin": "https://kotu.example"})
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "origin_rejected"
    r = c.post(f"/api/vkm/issues/{iid}/review", json={**body, "reviewer": "baskasi"}, headers={"X-CSRF-Token": tok})
    assert r.status_code == 422                                   # the reviewer is never a request field
    assert dump() == before
    r = c.post(f"/api/vkm/issues/{iid}/review", json={**body, "note": "ilk inceleme"},
               headers={"X-CSRF-Token": tok, "Origin": "https://testserver"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["issue"]["status"] == "incelendi" and d["history"][0]["actor"] == "owner"
    assert d["history"][0]["note"] == "ilk inceleme" and d["history"][0]["actor_label"] == "Yönetici"
    assert events(iid)[-1][4] == "owner"


@pytest.mark.production_auth
def test_stale_generation_and_bad_input_are_refused_without_echo(http):
    c = owner(http)
    lst = c.get("/api/vkm/issues").json()
    iid, g = lst["issues"][0]["issue_id"], lst["vkm_generation"]
    VS.observe({"stock": VS.Observation("missing", "yeni-yukleme")})        # a new upload is observed meanwhile
    before = dump()
    r = c.post(f"/api/vkm/issues/{iid}/review", json={"action": "incelendi", "vkm_generation": g})
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "stale_generation"
    assert dump() == before
    g2 = c.get("/api/vkm/issues").json()["vkm_generation"]
    assert g2 != g
    r = c.post(f"/api/vkm/issues/{iid}/notes", json={"note": CANARY_NOTE * 40, "vkm_generation": g2})
    assert r.status_code == 422 and CANARY_NOTE not in r.text and "KANARYA" not in r.text
    r = c.post(f"/api/vkm/issues/{iid}/notes", json={"note": CANARY_NOTE * 13, "vkm_generation": g2})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "note_too_long" and "KANARYA" not in r.text
    r = c.post(f"/api/vkm/issues/{iid}/review", json={"action": "silindi", "vkm_generation": g2, "note": CANARY_NOTE})
    assert r.status_code == 422 and "KANARYA" not in r.text
    for bad in ("x", "VKM-XYZ", "VKM-0123456789abcdeg", "VKM-" + "0" * 16):
        r = c.get(f"/api/vkm/issues/{bad}")
        assert r.status_code == 404 and bad not in r.text
    assert c.get("/api/vkm/issues?status=silinmis").status_code == 422
    assert dump() == before


@pytest.mark.production_auth
def test_owner_views_and_logs_carry_no_customer_value(http, caplog):
    c = owner(http)
    lst = c.get("/api/vkm/issues?status=all")
    assert lst.status_code == 200 and lst.headers["Cache-Control"] == "no-store"
    j = lst.json()
    texts = [lst.text]
    assert {s["key"] for s in j["sources"]} >= {"stock", "sales", "aging", "liste", "kredi"}
    assert {i["check"] for i in j["issues"]} >= {"stock_duplicate_code", "sales_duplicate_rows",
                                                  "kredi_closed_with_remaining"}
    for i in j["issues"]:
        r = c.get(f"/api/vkm/issues/{i['issue_id']}")
        assert r.status_code == 200
        texts.append(r.text)
        assert r.json()["issue"]["explanation"] and r.json()["issue"]["action"]
    g = j["vkm_generation"]
    iid = j["issues"][0]["issue_id"]
    c.post(f"/api/vkm/issues/{iid}/review", json={"action": "kabul_edildi", "vkm_generation": g, "note": CANARY_NOTE})
    assert CANARY_NOTE in c.get(f"/api/vkm/issues/{iid}").text           # the owner sees their own note …
    assert CANARY_NOTE not in http().get("/api/vkm/summary").text        # … nobody else does
    for t in texts:
        assert_no_canary(t)
        assert ".xlsx" not in t and "/" + "Users" not in t
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert_no_canary(log_text)
    assert "KANARYA" not in log_text


@pytest.mark.production_auth
@pytest.mark.production_gate
def test_logging_in_and_reviewing_never_reveal_or_certify_a_metric(http):
    c = owner(http)
    j = c.get("/api/vkm/issues").json()
    for i in j["issues"][:5]:
        c.post(f"/api/vkm/issues/{i['issue_id']}/review", json={"action": "kabul_edildi", "vkm_generation": j["vkm_generation"]})
    st = c.get("/api/accuracy/status").json()
    assert all(m["state"] != "verified" for m in st["metrics"])
    assert st["generation_bound_api_evidence"] is False


# ---------------------------------------------------------------------------------------------
# the real publication hooks: refresh / repeated refresh / restart / owner load
# ---------------------------------------------------------------------------------------------
def test_refresh_restart_and_owner_load_observe_through_the_generation_store(tmp_path, monkeypatch):
    from services import owner_view as OV
    from tests.fixtures import owner_synth as OS
    folder = write_folder(str(tmp_path / "data"), stock=products(dup=True))
    dash = str(tmp_path / "dashboard.html")
    store = GEN.GenerationStore()
    store.add_observer(VS.make_observer(lambda: {}))
    repo = ContextRepository(data_folder=folder, inflation_rate=0.40, generation_store=store)

    def install(staged):
        repo.replace_context(staged)
        store.install(staged.generation_manifest)
    sha = {n: GEN.sha256_file(os.path.join(folder, n)) for n in os.listdir(folder)}
    assert RF.run_refresh(folder, dash, inflation_rate=0.40, install=install)["ok"]
    first = dump()
    assert first["dq_issue"] and by_check("stock", "stock_duplicate_code")
    assert RF.run_refresh(folder, dash, inflation_rate=0.40, install=install)["ok"]      # same bytes again
    assert dump() == first
    # a real restart: restore the candidate, re-derive from the bytes, accept → observed, changes nothing
    store2 = GEN.GenerationStore()
    store2.add_observer(VS.make_observer(lambda: {}))
    assert store2.restore(GEN.manifest_path(dash), dash, folder) is not None
    ContextRepository(data_folder=folder, inflation_rate=0.40, generation_store=store2).context()
    assert store2.current() is not None and store2.current().core_id == store.current().core_id
    assert dump() == first
    # owner source loaded later: its record is observed by the same hook
    kpath = OS.write_kredi(tmp_path / "owner", dt.date.today())
    owner = OV.OwnerRepository(OV.config_from_env({"KREDI_PATH": str(kpath)}), background=False,
                               on_record=lambda k, r: store2.replace_owner(k, r))
    owner.ensure_loaded()
    assert dump()["dq_observation"] != first["dq_observation"]
    con = sqlite3.connect(ST.DB_PATH)
    assert con.execute("SELECT state FROM dq_source_state WHERE source_key='kredi'").fetchone()[0] == "present"
    con.close()
    assert {n: GEN.sha256_file(os.path.join(folder, n)) for n in os.listdir(folder)} == sha      # 5


def test_a_failing_observer_never_breaks_publication(tmp_path, monkeypatch):
    folder = write_folder(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    store = GEN.GenerationStore()
    store.add_observer(VS.make_observer(lambda: {}))

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(VS, "observe", boom)
    repo = ContextRepository(data_folder=folder, inflation_rate=0.40, generation_store=store)

    def install(staged):
        repo.replace_context(staged)
        store.install(staged.generation_manifest)
    assert RF.run_refresh(folder, dash, inflation_rate=0.40, install=install)["ok"]
    assert store.current() is not None


def test_a_superseded_generation_is_not_written(tmp_path):
    a = manifest_of(write_folder(str(tmp_path / "a"), stock=products(dup=True)))
    b = manifest_of(write_folder(str(tmp_path / "b"), stock=products(dup=False)))
    store = GEN.GenerationStore()
    store.install(b)
    obs = VS.make_observer(lambda: {})
    obs("core", a, None, store)                                     # A's late notification: B is current
    assert by_check("stock", "stock_duplicate_code") is None
    obs("core", b, None, store)
    assert dump()["dq_source_state"]


def test_get_requests_never_write_the_issue_store(tmp_path):
    observe_core(manifest_of(write_folder(str(tmp_path / "d"))))
    before = dump()
    with TestClient(APP.app, base_url="https://testserver", headers={"X-StockAgent": "1"}) as c:
        APP.app.dependency_overrides[APP.get_repo] = lambda: ContextRepository(context={"actions": []})
        for p in ("/api/vkm/summary", "/api/vkm/issues", "/api/vkm/issues?status=all"):
            assert c.get(p).status_code == 200                       # harness owner (test-only) for the owner reads
    APP.app.dependency_overrides.clear()
    assert dump() == before


def test_digest_is_stable_and_bound_to_the_issue_set(base_manifest):
    obs = VS.core_observations(base_manifest, {}, ())
    again = VS.core_observations(base_manifest, {}, ())
    assert {k: o.digest(k) for k, o in obs.items()} == {k: o.digest(k) for k, o in again.items()}
    assert obs["kredi"].digest("kredi") != VS.Observation("present", "x", "a" * 64).digest("kredi")
    assert hashlib.sha256(b"").hexdigest() not in json.dumps({k: o.digest(k) for k, o in obs.items()})


def test_unknown_and_refused_mapping_are_one_stable_issue_and_owner_sources_are_not_judged(base_manifest):
    """D-062: 'no recorded approval' and 'declared not approved' are two states of the same unresolved mapping (one ID,
    so a later declaration does not orphan the history); owner workbooks have no approval path and are not flagged."""
    stock = clean(next(r for r in base_manifest.core if r.key == "stock"))
    unknown = [i for i in V.detect_core(ev_set(stock, mapping_approved=None)) if i.issue_type == "unresolved_mapping"]
    refused = [i for i in V.detect_core(ev_set(stock, mapping_approved=False)) if i.issue_type == "unresolved_mapping"]
    assert [i.check for i in unknown] == ["mapping_unknown"] and [i.check for i in refused] == ["mapping_not_approved"]
    assert unknown[0].issue_id == refused[0].issue_id
    assert not [i for i in V.detect_core(ev_set(stock, mapping_approved=True)) if i.issue_type == "unresolved_mapping"]
    liste = next((r for r in base_manifest.core if r.key == "liste"), None)
    if liste is not None and liste.evidence is not None:
        assert not [i for i in V.detect_core(ev_set(clean(liste), mapping_approved=None)) if i.check == "mapping_unknown"]
