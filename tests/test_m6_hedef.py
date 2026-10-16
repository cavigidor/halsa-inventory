"""M6-3 Hedef raporu importer tests.

All workbooks are synthetic and generated at runtime; codes, names, representatives, cities and amounts are
invented. Tests 01–54 follow the M6-3 acceptance list.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


from services.importers import Confidence, IdentifierRole, MatchStatus, record_key
from services.importers import hedef as H
from services.importers.hedef import (
    DATED_SNAPSHOT_FILENAME,
    REPORTED_NET_REVENUE,
    STAMP_QTY,
    STAMP_QTY_DIRECT,
    STAMP_QTY_DISTRIBUTOR,
    SchemaVersion,
    assignment_drift,
    detect_schema,
    find_hedef_candidates,
    import_hedef,
    master_from_sales_workbook,
    reconcile_with_sales,
)
from tests.fixtures.xlsx_synth import E, write_xlsx

ROOT = Path(__file__).resolve().parents[1]
CUR, LEG = SchemaVersion.CURRENT_2026, SchemaVersion.LEGACY_SNAPSHOT
SALES = "2020-5 ekim satışlar.xlsx"
REPS = ("Kurgu Temsilci Alfa", "Kurgu Temsilci Beta")
CUSTOMER_REPS = ("Hayali Müşteri Temsilcisi Gama",)
NAMES = ("Uydurma Kırtasiye Ltd", "Kurgu Ofis AŞ", "Hayali Kaşe Atölyesi")
CITIES = ("Kurguşehir", "Hayalabat")
SENSITIVE_NUMBERS = (987654.32, 123457, 55555, 44444, 4321)


def headers(version: SchemaVersion) -> list[str]:
    hs = [f.header.name for f in H.SPECS[version].fields]
    return [hs[0], "cari_unvan1", *hs[1:]]


def row(version: SchemaVersion, code: str, i: int = 0, **over) -> dict:
    d = {"cari_kod": code, "cari_unvan1": NAMES[i % 3], "Temsilci": REPS[i % 2], "Müşteri Temsilcisi": CUSTOMER_REPS[0],
         "Sektör Kodu": "S01", "İlce": "Merkezköy", "İl": CITIES[i % 2], "isk1": 5, "Haftalık Kaşe Adedi": 120,
         "Risk": 55555, "Kredi": 44444, "Ziyaret Sayısı": 0}
    for f in H.SPECS[version].fields:
        name = f.header.name
        if name not in d:
            d[name] = 0 if f.name.startswith("legacy_placeholder") else (4321 if "Ciro" in name or "TUTARI" in name else 7)
    d.update(over)
    return d


def hedef_book(tmp_path, rows: list[dict] | None = None, version: SchemaVersion = CUR, name: str = "hedef raporu.xlsx",
               hdrs: list[str] | None = None) -> Path:
    hdrs = hdrs or headers(version)
    rows = rows if rows is not None else [row(version, "100.01.0001", 0), row(version, "100.01.0002", 1),
                                          row(version, "100.01.0003", 2)]
    data = [hdrs] + [[r.get(h) for h in hdrs] for r in rows]
    return write_xlsx(tmp_path / name, {"Sayfa1": data})


def sales_book(tmp_path, master=None, lines=None) -> Path:
    master = master or [["100.01.0001", REPS[0]], ["100.01.0002", REPS[0]], ["100.01.0003", None]]
    liste = [["KODU", "ÜNVANI 1", "TEMSİLCİ"]] + [[c, "Kurgu", rep] for c, rep in master]
    lines = lines if lines is not None else [
        # Pro_kodu, Musterikod, Tarih, Nettutar
        ["100.01.0001", "100.01.0001", dt.date(2025, 3, 1), 3000.0],
        ["100.01.0001", "100.01.0009", dt.date(2025, 4, 1), 1321.0],   # invoiced elsewhere, same order customer
        ["100.01.0002", "100.01.0002", dt.date(2025, 5, 1), 4000.0],   # Hedef says 4321 → mismatch
        ["", "100.01.0004", dt.date(2025, 6, 1), 10.0],                # D-003 fallback; not in Hedef
        ["100.01.0003", "100.01.0003", dt.date(2024, 6, 1), 999.0],    # other year
    ]
    data = [["sip_evrakno_seri", "sip_evrakno_sira", "Musterikod", "Pro_kodu", "sto_kod", "Srmmerk", "Tarih", "Nettutar"]]
    for i, (pro, mus, d, amount) in enumerate(lines):
        data.append(["AA", 1000 + i, mus, pro, "P-1", "GENEL", d, amount])
    return write_xlsx(tmp_path / SALES, {"2020-2026 satışlar": data, "liste": liste})


def run(tmp_path, rows=None, version=CUR, **kw):
    return import_hedef(hedef_book(tmp_path, rows, version), **kw)


def by_code(res):
    return {a.customer_code: a for a in res.accounts}


def blob(res) -> str:
    return json.dumps(res.summary(), ensure_ascii=False, default=str) + res.report.to_json()


# =============================================================================================
# Schema (1–8)
# =============================================================================================
def test_01_current_schema_succeeds(tmp_path):
    res = run(tmp_path)
    assert res.ok, res.report.to_json()
    assert res.schema_version is CUR and res.counts.schema_version == "current_2026" and len(res.accounts) == 3


def test_02_legacy_schema_succeeds(tmp_path):
    res = run(tmp_path, [row(LEG, "100.01.0001"), row(LEG, "100.01.0002", 1)], version=LEG)
    assert res.ok, res.report.to_json()
    assert res.schema_version is LEG and len(res.accounts) == 2


def test_03_unknown_schema_fails(tmp_path):
    hdrs = [h for h in headers(CUR) if h != "2026 Net Ciro"]      # one current column renamed away
    res = import_hedef(hedef_book(tmp_path, hdrs=hdrs))
    assert not res.ok and res.failed_step == "detect" and "unsupported_schema" in res.report.codes()
    both = list(dict.fromkeys(headers(CUR) + headers(LEG)))       # superset of both → ambiguous → fail
    res = import_hedef(hedef_book(tmp_path, [row(LEG, "1")], hdrs=both, name="hedef iki.xlsx"))
    assert not res.ok and res.failed_step == "detect"


def test_04_missing_cari_kod_fails(tmp_path):
    hdrs = [h for h in headers(CUR) if h != "cari_kod"]
    assert not import_hedef(hedef_book(tmp_path, hdrs=hdrs)).ok
    res = run(tmp_path, [row(CUR, "100.01.0001"), row(CUR, None, 1)])   # blank code in a row
    assert not res.ok and "null_values" in res.report.codes()


def test_05_renamed_required_header_fails(tmp_path):
    hdrs = ["Haftalık Kaşe Sayısı" if h == "Haftalık Kaşe Adedi" else h for h in headers(CUR)]
    res = import_hedef(hedef_book(tmp_path, hdrs=hdrs))
    assert not res.ok and res.failed_step == "detect"


def test_06_duplicate_codes_fail(tmp_path):
    res = run(tmp_path, [row(CUR, "100.01.0001"), row(CUR, "100.01.0002", 1), row(CUR, "100.01.0001", 2)])
    assert not res.ok and res.failed_step == "accounts" and res.accounts == []
    issue = next(i for i in res.report.issues if i.code == "duplicate_account_code")
    assert issue.severity.value == "error" and issue.count == 1


def test_07_extra_column_does_not_change_output(tmp_path):
    base = run(tmp_path)
    hdrs = headers(CUR) + ["Ek Not"]
    rows = [dict(row(CUR, f"100.01.000{i + 1}", i), **{"Ek Not": "x"}) for i in range(3)]
    extra = import_hedef(hedef_book(tmp_path, rows, hdrs=hdrs, name="hedef ek.xlsx"))
    assert extra.ok
    def snap(res):
        return [(a.account_key, a.city.value, a.historical_risk_limit.value,
                 {m: {y: nv.value for y, nv in ys.items()} for m, ys in a.yearly.items()}) for a in res.accounts]
    assert snap(base) == snap(extra)


def test_08_header_matching_exact(tmp_path):
    for wrong in ("Risk ", "risk", "İL"):
        target = {"Risk ": "Risk", "risk": "Risk", "İL": "İl"}[wrong]
        hdrs = [wrong if h == target else h for h in headers(CUR)]
        assert not import_hedef(hedef_book(tmp_path, hdrs=hdrs, name=f"hedef {len(wrong)}.xlsx")).ok


# =============================================================================================
# Confidence (9–15)
# =============================================================================================
def test_09_weekly_capacity_always_stale(tmp_path):
    res = run(tmp_path)
    assert all(a.historical_weekly_stamp_capacity.confidence is Confidence.STALE for a in res.accounts)
    assert res.counts.field_confidence["historical_weekly_stamp_capacity"] == "stale"


def test_10_risk_always_stale(tmp_path):
    assert all(a.historical_risk_limit.confidence is Confidence.STALE for a in run(tmp_path).accounts)


def test_11_kredi_always_stale(tmp_path):
    assert all(a.historical_unsecured_credit_limit.confidence is Confidence.STALE for a in run(tmp_path).accounts)


def test_12_parsing_never_upgrades_stale(tmp_path):
    res = run(tmp_path, [row(CUR, "100.01.0001", **{"Haftalık Kaşe Adedi": 250, "Risk": 1, "Kredi": 2})])
    a = res.accounts[0]
    assert a.historical_weekly_stamp_capacity.value == 250.0 and a.historical_weekly_stamp_capacity.confidence is Confidence.STALE
    assert next(c for c in res.verification.checks if c.name == "stale_fields_stay_stale").ok
    for f in H.STALE_FIELDS:
        assert not getattr(a, f).confidence.usable_for_decisions


def test_13_visit_count_incomplete(tmp_path):
    res = run(tmp_path)
    assert all(a.visit_count.confidence is Confidence.INCOMPLETE for a in res.accounts)
    assert res.accounts[0].visit_count.value == 0  # a recorded zero, which may mean "not logged"


def test_14_legacy_placeholders_not_exposed(tmp_path):
    res = run(tmp_path, [row(LEG, "100.01.0001")], version=LEG)
    a = res.accounts[0]
    exposed = {f for f in H.HedefAccount.__slots__} | {f"{m}_{y}" for m, ys in a.yearly.items() for y in ys}
    assert not any("placeholder" in f or "hedef_" in f.replace("hedef_rep", "") for f in exposed)
    assert res.import_result.field_confidence["legacy_placeholder_0"] is Confidence.INVALID
    assert "legacy_placeholder_0" not in res.counts.field_confidence


def test_15_invalid_cell_becomes_invalid(tmp_path):
    res = run(tmp_path, [row(CUR, "100.01.0001", **{"Risk": E("#REF!"), "2025 Net Ciro": "yok"}),
                         row(CUR, "100.01.0002", 1)])
    assert res.ok
    a = by_code(res)["100.01.0001"]
    assert a.historical_risk_limit.value is None and a.historical_risk_limit.confidence is Confidence.INVALID
    assert a.historical_risk_limit.error == "#REF!" and not a.historical_risk_limit.is_null
    nv = a.yearly[REPORTED_NET_REVENUE][2025]
    assert nv.confidence is Confidence.INVALID and nv.error == "type_mismatch"
    assert {"excel_error", "type_mismatch"} <= set(res.report.codes())
    # Cell-level only: the other account's stale value is intact.
    assert by_code(res)["100.01.0002"].historical_risk_limit.value == 55555.0


# =============================================================================================
# Identity (16–23)
# =============================================================================================
def test_16_code_is_order_customer_identity():
    f = H.SPECS[CUR].field("customer_code")
    assert f.header.name == "cari_kod" and f.role is IdentifierRole.ORDER_CUSTOMER and f.confidence is Confidence.TRUSTED


def test_17_exact_master_match(tmp_path):
    master = master_from_sales_workbook(sales_book(tmp_path))
    res = run(tmp_path, master=master)
    assert res.counts.customer_resolution == "resolved"
    assert all(a.master_link.status is MatchStatus.MATCHED for a in res.accounts)
    assert res.counts.match_counts["matched"] == 3


def test_18_unmatched_stays_unmatched(tmp_path):
    master = master_from_sales_workbook(sales_book(tmp_path))
    res = run(tmp_path, [row(CUR, "100.01.0001"), row(CUR, "999.99.0001", 1)], master=master)
    assert by_code(res)["999.99.0001"].master_link.status is MatchStatus.UNMATCHED
    assert "999.99.0001" not in blob(res)


def test_19_duplicate_master_stays_duplicate(tmp_path):
    master = master_from_sales_workbook(sales_book(tmp_path, master=[["100.01.0001", None], ["100.01.0001", None],
                                                                     ["100.01.0002", None]]))
    res = run(tmp_path, master=master)
    a = by_code(res)["100.01.0001"]
    assert a.master_link.status is MatchStatus.DUPLICATE_MASTER and a.master_link.master_row is None


def test_20_whitespace_code_not_trimmed(tmp_path):
    master = master_from_sales_workbook(sales_book(tmp_path))
    res = run(tmp_path, [row(CUR, "100.01.0001 "), row(CUR, "100.01.0002", 1)], master=master)
    a = by_code(res)["100.01.0001 "]
    assert a.customer_code == "100.01.0001 " and a.master_link.status is MatchStatus.UNMATCHED
    assert res.counts.codes_with_surrounding_whitespace == 1
    assert "codes_with_surrounding_whitespace" in res.report.codes()
    assert a.account_key != record_key("hedef_account", ["100.01.0001"])


def test_21_name_never_used_for_matching(tmp_path):
    src = (ROOT / "services/importers/hedef.py").read_text()
    assert "cari_unvan1" not in src
    res = run(tmp_path)
    assert not any("unvan" in k or "name" in k for k in res.import_result.field_confidence)
    assert not hasattr(res.accounts[0], "name")


def test_22_no_fuzzy_matching(tmp_path):
    master = master_from_sales_workbook(sales_book(tmp_path))
    near = ["100.01.0001 ", " 100.01.0001", "100.1.0001", "10001001", "100.01.000l"]
    res = run(tmp_path, [row(CUR, c, i) for i, c in enumerate(near)], master=master)
    assert all(a.master_link.status is MatchStatus.UNMATCHED for a in res.accounts)
    src = (ROOT / "services/importers/hedef.py").read_text().lower()
    for banned in ("difflib", "levenshtein", "fuzz", "casefold", ".lower(", ".upper(", ".strip()]"):
        assert banned not in src, banned


def test_23_stable_key_across_snapshots(tmp_path):
    cur = run(tmp_path)
    leg = import_hedef(hedef_book(tmp_path, [row(LEG, "100.01.0002", 1)], LEG, name="31-12-2025 Hedef raporu.xlsx"))
    assert by_code(cur)["100.01.0002"].account_key == by_code(leg)["100.01.0002"].account_key \
        == record_key("hedef_account", ["100.01.0002"])


# =============================================================================================
# Yearly metrics (24–30)
# =============================================================================================
def test_24_yearly_stamp_metric_imported(tmp_path):
    res = run(tmp_path, [row(CUR, "100.01.0001", **{"2024 Kaşe Adedi": 31})])
    nv = res.accounts[0].yearly[STAMP_QTY][2024]
    assert nv.value == 31 and nv.confidence is Confidence.UNVERIFIED


def test_25_missing_year_absent(tmp_path):
    a = run(tmp_path).accounts[0]
    assert 2020 not in a.yearly[STAMP_QTY] and 2021 not in a.yearly[REPORTED_NET_REVENUE]
    assert sorted(a.yearly[REPORTED_NET_REVENUE]) == [2022, 2023, 2024, 2025, 2026]


def test_26_blank_stays_missing_not_zero(tmp_path):
    res = run(tmp_path, [row(CUR, "100.01.0001", **{"2023 Kaşe Adedi": None, "Haftalık Kaşe Adedi": None})])
    a = res.accounts[0]
    assert a.yearly[STAMP_QTY][2023].value is None and a.yearly[STAMP_QTY][2023].is_null
    assert a.historical_weekly_stamp_capacity.value is None
    assert res.counts.weekly_capacity_filled == 0


def test_27_direct_and_distributor_separate(tmp_path):
    a = run(tmp_path, [row(CUR, "100.01.0001", **{"2025 Kaşe Adedi": 10, "2025 Direkt Satış Kaşe Adedi": 6,
                                                   "2025 Dağıtıcı Üzerinden Kaşe Adedi": 4})]).accounts[0]
    assert (a.yearly[STAMP_QTY][2025].value, a.yearly[STAMP_QTY_DIRECT][2025].value,
            a.yearly[STAMP_QTY_DISTRIBUTOR][2025].value) == (10, 6, 4)


def test_28_revenue_is_source_reported(tmp_path):
    a = run(tmp_path, [row(CUR, "100.01.0001", **{"2025 Net Ciro": 1234.5})]).accounts[0]
    assert a.yearly[REPORTED_NET_REVENUE][2025].value == 1234.5
    assert "reported_net_revenue" in H.METRICS and not any("delivered" in m for m in H.METRICS)


def test_29_legacy_does_not_synthesize_2026(tmp_path):
    a = run(tmp_path, [row(LEG, "100.01.0001")], version=LEG).accounts[0]
    assert all(2026 not in ys for ys in a.yearly.values())
    assert 2020 in a.yearly[STAMP_QTY] and 2020 not in a.yearly[STAMP_QTY_DIRECT]


def test_30_placeholder_targets_excluded(tmp_path):
    res = run(tmp_path, [row(LEG, "100.01.0001", **{"2020 Hedef Ciro": 99999, "2015_kase_adedi": 1})], version=LEG)
    a = res.accounts[0]
    values = [nv.value for ys in a.yearly.values() for nv in ys.values()]
    assert 99999 not in values and 99999.0 not in values
    assert set(H.LEGACY_PLACEHOLDER_HEADERS) <= H.required_headers(LEG)   # still required for schema identity


# =============================================================================================
# Business date (31–35)
# =============================================================================================
def test_31_declared_snapshot_date(tmp_path):
    res = run(tmp_path, snapshot_date=dt.date(2026, 2, 9))
    assert (res.counts.snapshot_date, res.counts.snapshot_date_origin) == (dt.date(2026, 2, 9), "declared")


def test_32_filename_date_only_when_opted_in(tmp_path):
    p = hedef_book(tmp_path, [row(LEG, "100.01.0001")], LEG, name="31-12-2025 Hedef raporu.xlsx")
    assert import_hedef(p).counts.snapshot_date is None                          # not opted in
    res = import_hedef(p, filename_date_regex=DATED_SNAPSHOT_FILENAME)
    assert (res.counts.snapshot_date, res.counts.snapshot_date_origin) == (dt.date(2025, 12, 31), "filename")
    plain = hedef_book(tmp_path)
    res = import_hedef(plain, filename_date_regex=DATED_SNAPSHOT_FILENAME)        # opted in, no date in name
    assert not res.ok and "business_date_invalid" in res.report.codes()


def test_33_undated_never_uses_mtime(tmp_path):
    p = hedef_book(tmp_path)
    os.utime(p, (1_700_000_000, 1_700_000_000))
    res = import_hedef(p)
    assert res.ok and res.counts.snapshot_date is None and res.counts.snapshot_date_origin is None
    assert "snapshot_date_unknown" in res.report.codes()


def test_34_undated_does_not_invent_today(tmp_path):
    res = run(tmp_path)
    assert res.counts.snapshot_date != dt.date.today() and res.counts.snapshot_date is None
    src = (ROOT / "services/importers/hedef.py").read_text()
    assert "today(" not in src and "getmtime" not in src and "st_mtime" not in src


def test_35_date_provenance(tmp_path):
    res = run(tmp_path, snapshot_date=dt.date(2025, 11, 30))
    assert res.counts.snapshot_date_origin == "declared"
    assert res.import_result.business_date.origin.value == "declared"
    assert res.counts.year_metrics[STAMP_QTY][-1] == 2026  # latest year column is structure, not a date


# =============================================================================================
# Privacy (36–43)
# =============================================================================================
def _full(tmp_path):
    p = sales_book(tmp_path)
    res = import_hedef(hedef_book(tmp_path, [row(CUR, "100.01.0001", 0, **{"2025 Net Ciro": 4321.0, "Risk": 123457,
                                                                         "Haftalık Kaşe Adedi": 987654.32}),
                                             row(CUR, "100.01.0002", 1), row(CUR, "100.01.0003", 2)]),
                       master=master_from_sales_workbook(p))
    reconcile_with_sales(res, p, year=2025)
    assignment_drift(res, p)
    return res


def test_36_no_customer_codes(tmp_path):
    assert "100.01" not in blob(_full(tmp_path))


def test_37_no_customer_names(tmp_path):
    b = blob(_full(tmp_path))
    assert not any(n in b for n in NAMES)


def test_38_no_salesperson_names(tmp_path):
    b = blob(_full(tmp_path))
    assert not any(n in b for n in (*REPS, *CUSTOMER_REPS, "Kurgu", "Hayali"))


def test_39_no_risk_kredi_values(tmp_path):
    b = blob(_full(tmp_path))
    assert "123457" not in b and "55555" not in b and "44444" not in b


def test_40_no_weekly_capacity_values(tmp_path):
    assert "987654" not in blob(_full(tmp_path))


def test_41_no_revenue_values(tmp_path):
    b = blob(_full(tmp_path))
    assert "4321" not in b and "3000" not in b and "1321" not in b


def test_42_no_absolute_path(tmp_path):
    deep = tmp_path / "Users" / "owner" / "Gizli"
    deep.mkdir(parents=True)
    res = import_hedef(hedef_book(deep))
    assert res.file == "hedef raporu.xlsx" and str(tmp_path) not in blob(res) and "Gizli" not in blob(res)


def test_43_sensitive_values_not_in_errors(tmp_path):
    res = run(tmp_path, [row(CUR, "SNTL-777"), row(CUR, "SNTL-777", 1, Risk="Gizli Limit 123457")])
    assert not res.ok
    b = blob(res)
    for leaked in ("SNTL", "Gizli Limit", "123457", *NAMES, *CITIES):
        assert leaked not in b, leaked
    assert next(c for c in run(tmp_path).verification.checks if c.name == "summary_has_no_identities").ok


# =============================================================================================
# Reconciliation (44–49)
# =============================================================================================
def test_44_reconciliation_by_order_customer(tmp_path):
    res = _full(tmp_path)
    r = res.counts.reconciliation
    # account 1: 3000 + 1321 (invoiced elsewhere) = 4321 → exact by order customer
    assert (r.comparable_hedef_accounts, r.exact_matches, r.mismatches, r.missing_sales_side) == (3, 1, 1, 1)
    assert r.missing_hedef_side == 1 and r.exact_match_rate == round(1 / 3, 4)
    assert r.key.startswith("order_customer")


def test_45_invoice_account_not_substituted(tmp_path):
    r = _full(tmp_path).counts.reconciliation
    # By invoice account account 1 would be 3000 ≠ 4321: the diagnostic shows it, the primary result does not use it.
    assert r.invoice_account_diagnostic_exact_matches == 0 and r.exact_matches == 1


def test_46_reconciliation_counts_aggregate_only():
    fields = set(H.ReconciliationSummary.model_fields)
    assert fields == {"label", "year", "tolerance", "key", "comparable_hedef_accounts", "exact_matches", "mismatches",
                      "missing_sales_side", "missing_hedef_side", "exact_match_rate",
                      "invoice_account_diagnostic_exact_matches", "invoice_account_diagnostic_rate", "sales_customers",
                      "sales_customers_in_hedef", "sales_coverage_rate"}


def test_47_reconciliation_does_not_overwrite(tmp_path):
    res = _full(tmp_path)
    assert by_code(res)["100.01.0002"].yearly[REPORTED_NET_REVENUE][2025].value == 4321.0  # sales said 4000


def test_48_no_customer_level_differences_leak(tmp_path):
    b = blob(_full(tmp_path))
    for leaked in ("4000", "4321", "100.01.0002", "-321"):
        assert leaked not in b, leaked


def test_49_reconciliation_labelled_technical(tmp_path):
    r = _full(tmp_path).counts.reconciliation
    assert "technical" in r.label and "not canonical revenue" in r.label
    assert r.sales_customers == 4 and r.sales_customers_in_hedef == 3 and r.sales_coverage_rate == 0.75
    assert H.SALES_RECONCILIATION_SPEC.field("nettutar_existing").confidence is None  # spec default: unverified


# =============================================================================================
# Assignment (50–54)
# =============================================================================================
def test_50_reps_separate(tmp_path):
    a = run(tmp_path).accounts[0]
    assert a.hedef_rep.value == REPS[0] and a.customer_rep.value == CUSTOMER_REPS[0]
    assert H.SPECS[CUR].field("hedef_rep").header.name == "Temsilci"
    assert H.SPECS[CUR].field("customer_rep").header.name == "Müşteri Temsilcisi"


def test_51_master_rep_not_substituted(tmp_path):
    res = _full(tmp_path)
    a = by_code(res)["100.01.0003"]
    assert a.hedef_rep.value == REPS[0]   # master has no TEMSİLCİ for it; nothing is filled in either way


def test_52_assignment_counts_aggregate(tmp_path):
    s = _full(tmp_path).counts.assignment
    # 0001: Alfa vs Alfa → same; 0002: Beta vs Alfa → different; 0003: master empty → missing on master
    assert (s.comparable, s.same, s.different, s.missing_on_master, s.missing_on_hedef) == (2, 1, 1, 1, 0)
    assert s.agreement_rate == 0.5 and s.hedef_rep_equals_customer_rep == 0


def test_53_no_salesperson_names_in_assignment(tmp_path):
    dumped = json.dumps(_full(tmp_path).counts.assignment.model_dump(), ensure_ascii=False)
    assert not any(n in dumped for n in (*REPS, *CUSTOMER_REPS))


def test_54_no_ranking_or_scoring():
    fields = set(H.AssignmentSummary.model_fields) | set(H.HedefSummary.model_fields)
    for banned in ("rank", "score", "top", "best", "worst", "performance", "penetration", "potential"):
        assert not any(banned in f for f in fields), banned
    src = (ROOT / "services/importers/hedef.py").read_text().lower()
    for banned in ("sorted(by_customer", "most_common(", "penetration =", "potential ="):
        assert banned not in src


# =============================================================================================
# Framework use, isolation, candidates
# =============================================================================================
def test_uses_framework_and_no_ai_or_live_integration(tmp_path, monkeypatch):
    calls = []
    real = H.run_import
    monkeypatch.setattr(H, "run_import", lambda p, spec, **kw: (calls.append(spec.key), real(p, spec, **kw))[1])
    assert import_hedef(hedef_book(tmp_path)).ok and calls == ["hedef_current_2026"]
    src = (ROOT / "services/importers/hedef.py").read_text()
    for banned in ("openpyxl", "load_workbook", "pandas", "read_excel", "openai", "anthropic", "services.ai",
                   "write_text", "write_bytes", ".save(", "sqlite", "open("):
        assert banned not in src, banned
    code = ("import sys, services.importers.hedef;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
    for live in ("app.py", "dashboard_builder.py", "services/refresh.py", "services/context.py",
                 "services/action_sources.py", "services/actions.py", "services/entity.py"):
        text = (ROOT / live).read_text()
        assert "importers" not in text and "import_hedef" not in text, live


def test_candidates_listed_never_selected(tmp_path):
    hedef_book(tmp_path)
    hedef_book(tmp_path, [row(LEG, "1")], LEG, name="31-12-2025 Hedef raporu.xlsx")
    (tmp_path / "~$hedef raporu.xlsx").write_bytes(b"lock")
    (tmp_path / "Hedef bozuk.xlsx").write_bytes(b"not a workbook")
    sales_book(tmp_path)
    out = find_hedef_candidates(tmp_path)
    assert out == [{"file": "31-12-2025 Hedef raporu.xlsx", "schema": "legacy_snapshot"},
                   {"file": "Hedef bozuk.xlsx", "schema": None},
                   {"file": "hedef raporu.xlsx", "schema": "current_2026"}]
    assert detect_schema(tmp_path / "hedef raporu.xlsx") is CUR
    assert not hasattr(H, "select_hedef") and not hasattr(H, "latest_hedef")


def test_without_master_resolution_is_explicitly_absent(tmp_path):
    res = run(tmp_path)
    assert res.ok and res.counts.customer_resolution == "not_supplied" and res.match_reports == []
    assert all(a.master_link is None and H.master_link_status(a) is None for a in res.accounts)
