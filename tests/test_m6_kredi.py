"""Kredi takip importer tests (owner / Yönetim source).

All workbooks are synthetic and generated at runtime; contract codes, lender codes and names, slip numbers
and amounts are invented. Tests 01–42 follow the acceptance list.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from services.importers import Audience, CellKind, Confidence, record_key
from services.importers import kredi as K
from services.importers.kredi import LoanStatus, LoanType, find_kredi_candidates, import_kredi
from tests.fixtures.xlsx_synth import E, F, write_xlsx

ROOT = Path(__file__).resolve().parents[1]
D = dt.date
NAME = "kredi sözleşmeleri.xlsx"
HEADERS = ["SÖZLEŞME KODU", "AÇIKLAMA", "BANKA KODU", "BANKA ADI", "KREDİ TİPİ", "DÖVİZ CİNSİ", "KAPANIŞ TARİHİ",
           "DURUM", "KREDİ TUTARI", "TAKSİT SAYISI", "TAKSİT NO", "VADE TARİHİ", "TAKSİT TUTARI", "TAKSİT ANAPARA",
           "TAKSİT FAİZ", "TAKSİT BSMV", "TAKSİT KKDF", "KALAN ANAPARA", "ÖDEME FİŞİ SERİ NO", "ÖDEME FİŞİ SIRA NO",
           "ÖDEME FİŞ TARİHİ", "ÖDENEN TAKSİT TUTARI", "KALAN TAKSİT TUTARI"]
LENDER_NAMES = ("Kurgu Katılım Bankası", "Hayali Yatırım Bankası")
CONTRACTS = ("KRD-ZQX-001", "KRD-ZQX-002")
AMOUNTS = (987654.0, 4111.11, 3500.25)


def inst(contract=CONTRACTS[0], no=1, due=D(2026, 3, 1), *, paid=False, principal=3500.25, interest=500.5,
         bsmv=110.36, kkdf=0, lender=0, kind="Tüketici kredisi modeli", currency="TL", status=None, count=12,
         amount=AMOUNTS[0], remaining_principal=1000.0, total=None, paid_amount=None, remaining_amount=None,
         slip_date=None, **over):
    total = round(principal + interest + bsmv + kkdf, 2) if total is None else total
    if paid_amount is None:
        paid_amount = total if paid else 0
    if remaining_amount is None:
        remaining_amount = round(total - paid_amount, 2)
    row = {"SÖZLEŞME KODU": contract, "AÇIKLAMA": "Kurgu açıklama metni", "BANKA KODU": f"BNK-0{lender + 1}",
           "BANKA ADI": LENDER_NAMES[lender], "KREDİ TİPİ": kind, "DÖVİZ CİNSİ": currency,
           "KAPANIŞ TARİHİ": D(2026, 1, 15) if paid else None, "DURUM": status or ("Kapalı" if paid else "Aktif"),
           "KREDİ TUTARI": amount, "TAKSİT SAYISI": count, "TAKSİT NO": no, "VADE TARİHİ": due,
           "TAKSİT TUTARI": total, "TAKSİT ANAPARA": principal, "TAKSİT FAİZ": interest, "TAKSİT BSMV": bsmv,
           "TAKSİT KKDF": kkdf, "KALAN ANAPARA": remaining_principal, "ÖDEME FİŞİ SERİ NO": "ODM",
           "ÖDEME FİŞİ SIRA NO": 70001 + no if paid else 0,
           "ÖDEME FİŞ TARİHİ": (slip_date or due) if paid else slip_date, "ÖDENEN TAKSİT TUTARI": paid_amount,
           "KALAN TAKSİT TUTARI": remaining_amount}
    row.update(over)
    return row


def base_rows():
    return [inst(no=1, due=D(2026, 1, 1), paid=True), inst(no=2, due=D(2026, 2, 1), paid=True),
            inst(no=3, due=D(2026, 3, 1)), inst(no=4, due=D(2027, 7, 1)),
            inst(CONTRACTS[1], no=10, due=D(2026, 4, 1), lender=1, count=0, amount=250000.0),
            inst(CONTRACTS[1], no=11, due=D(2026, 5, 1), lender=1, count=0, amount=250000.0)]


def footer(rows):
    sums = {h: round(sum(r[h] for r in rows if isinstance(r[h], (int, float))), 2) for h in
            ("KREDİ TUTARI", "TAKSİT TUTARI", "TAKSİT ANAPARA", "TAKSİT FAİZ", "TAKSİT BSMV", "TAKSİT KKDF",
             "ÖDENEN TAKSİT TUTARI", "KALAN TAKSİT TUTARI")}
    return {"SÖZLEŞME KODU": len(rows), **sums}


def book(tmp_path, rows=None, *, with_footer=True, name=NAME, headers=HEADERS, footer_row=None):
    rows = base_rows() if rows is None else rows
    data = [headers] + [[r.get(h) for h in headers] for r in rows]
    if with_footer:
        f = footer_row if footer_row is not None else footer(rows)
        data.append([f.get(h) for h in headers])
    return write_xlsx(tmp_path / name, {"Sayfa1": data})


def run(tmp_path, rows=None, **kw):
    return import_kredi(book(tmp_path, rows, **{k: v for k, v in kw.items() if k in ("with_footer", "name",
                                                                                         "headers", "footer_row")}),
                        **{k: v for k, v in kw.items() if k == "snapshot_date"})


def codes(res, sev=None):
    return res.report.codes(sev)


def blob(res):
    return json.dumps(res.summary(), ensure_ascii=False, default=str) + res.report.to_json()


# =============================================================================================
# Structure (1–6)
# =============================================================================================
def test_01_valid_current_schema(tmp_path):
    res = run(tmp_path)
    assert res.ok, res.report.to_json()
    assert (res.counts.contracts, res.counts.installments) == (2, 6)
    assert res.counts.control_total_row and res.counts.control_totals_match


def test_02_missing_required_header_fails(tmp_path):
    res = run(tmp_path, headers=[h for h in HEADERS if h != "TAKSİT BSMV"])
    assert not res.ok and "header_missing" in codes(res)


def test_03_renamed_header_fails(tmp_path):
    for old, new in (("KALAN ANAPARA", "KALAN ANAPARA "), ("TAKSİT NO", "TAKSIT NO"), ("DURUM", "Durum")):
        res = run(tmp_path, headers=[new if h == old else h for h in HEADERS])
        assert not res.ok and "header_missing" in codes(res), old


def test_04_unexpected_loan_type_fails_closed(tmp_path):
    res = run(tmp_path, base_rows() + [inst(CONTRACTS[1], no=12, lender=1, count=0, amount=250000.0, kind="Rotatif")])
    assert not res.ok and "unsupported_loan_type" in codes(res)  # also inconsistent within the contract
    rows = base_rows()
    for r in rows:
        r["KREDİ TİPİ"] = "Rotatif"
    res = run(tmp_path, rows)
    assert not res.ok and "unsupported_loan_type" in codes(res) and res.installments == []
    rows = base_rows()
    rows[0]["KREDİ TİPİ"] = rows[1]["KREDİ TİPİ"] = rows[2]["KREDİ TİPİ"] = rows[3]["KREDİ TİPİ"] = "Spot kredi"
    assert "unknown_loan_type" in codes(run(tmp_path, rows))
    assert set(K.SUPPORTED_LOAN_TYPES) == {LoanType.AMORTIZING}


def test_05_explicit_path_no_mtime_selection(tmp_path):
    book(tmp_path)
    book(tmp_path, name="kredi borçları.xlsx")
    os.utime(tmp_path / NAME, (1, 1))
    (tmp_path / "~$kredi sözleşmeleri.xlsx").write_bytes(b"lock")
    out = find_kredi_candidates(tmp_path)
    assert out == [{"file": "kredi borçları.xlsx", "headers_compatible": True, "layout": "erp_export"},
                   {"file": NAME, "headers_compatible": True, "layout": "erp_export"}]   # M8-ACCURACY-2: + layout
    src = (ROOT / "services/importers/kredi.py").read_text()
    for banned in ("getmtime", "st_mtime", "_latest(", "today(", "now("):
        assert banned not in src, banned


def test_06_owner_audience(tmp_path):
    res = run(tmp_path)
    assert K.KREDI_SPEC.audience is Audience.OWNER and res.audience is Audience.OWNER
    assert res.summary()["audience"] == "owner" and res.report.audience is Audience.OWNER
    assert next(c for c in res.verification.checks if c.name == "audience_owner").ok


# =============================================================================================
# Identities (7–13)
# =============================================================================================
def test_07_stable_contract_key(tmp_path):
    a, b = run(tmp_path), run(tmp_path)
    assert [c.contract_key for c in a.contracts] == [c.contract_key for c in b.contracts]
    assert a.contracts[0].contract_key == record_key("kredi_contract", [CONTRACTS[0]])
    code = f"from services.importers import record_key; print(record_key('kredi_contract', ['{CONTRACTS[0]}']))"
    outs = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONHASHSEED": s, "PATH": "/usr/bin:/bin"}).stdout for s in ("3", "99")}
    assert outs == {a.contracts[0].contract_key + "\n"}


def test_08_stable_installment_key(tmp_path):
    res = run(tmp_path)
    i = res.installments[2]
    assert i.installment_key == record_key("kredi_installment", [CONTRACTS[0], 3])
    assert [x.installment_key for x in run(tmp_path).installments] == [x.installment_key for x in res.installments]
    # Does not depend on the row position or the file name.
    rows = list(reversed(base_rows()))
    other = import_kredi(book(tmp_path, rows, name="kredi borçları 27 haziran 2026.xlsx"))
    assert {x.installment_key for x in other.installments} == {x.installment_key for x in res.installments}


def test_09_duplicate_installment_detected(tmp_path):
    rows = base_rows() + [inst(no=3, due=D(2026, 3, 15))]
    res = run(tmp_path, rows)
    assert not res.ok and res.failed_step == "installments" and res.installments == []
    issue = next(i for i in res.report.issues if i.code == "duplicate_installment")
    assert issue.severity.value == "error" and issue.count == 1
    assert res.counts.duplicate_installment_identities == 1


def test_10_contract_level_inconsistency(tmp_path):
    for field, header, value in (("lender_code", "BANKA KODU", "BNK-09"), ("currency", "DÖVİZ CİNSİ", "USD"),
                                 ("original_amount", "KREDİ TUTARI", 1.0), ("installment_count", "TAKSİT SAYISI", 24)):
        rows = base_rows()
        rows[1][header] = value
        res = run(tmp_path, rows)
        assert not res.ok
        issue = next(i for i in res.report.issues if i.code == "contract_field_inconsistent")
        assert issue.field == field and issue.count == 1


def test_11_installment_number_validation(tmp_path):
    rows = base_rows()
    rows[3]["TAKSİT NO"] = 13                    # count 12
    assert "installment_number_exceeds_count" in codes(run(tmp_path, rows))
    rows = base_rows()
    rows[3]["TAKSİT NO"] = 0
    assert "installment_number_not_positive" in codes(run(tmp_path, rows))
    res = run(tmp_path)                          # contract 2 records count 0 → not recorded, no upper bound
    assert res.ok and res.counts.contracts_without_installment_count == 1
    assert "installment_count_not_recorded" in codes(res)
    # Installments need not start at 1 (exports omit past rows); no continuity is required.
    assert [i.installment_number for i in res.installments if i.contract_key == res.contracts[1].contract_key] == [10, 11]


def test_12_future_due_date_accepted(tmp_path):
    res = run(tmp_path)
    assert res.ok and res.counts.due_date_max == D(2027, 7, 1)
    assert res.counts.future_due_installments_vs_latest_payment == 4


def test_13_invalid_due_date_fails(tmp_path):
    rows = base_rows()
    rows[2]["VADE TARİHİ"] = "31.02.2026"
    res = run(tmp_path, rows)
    assert not res.ok and "type_mismatch" in codes(res)
    rows = base_rows()
    rows[2]["VADE TARİHİ"] = None
    res = run(tmp_path, rows)
    assert not res.ok and next(i for i in res.report.issues if i.code == "missing_required_value").field == "due_date"


# =============================================================================================
# Arithmetic (14–22)
# =============================================================================================
def _rule(res, name):
    return next(r for r in res.import_result.rule_results if r.name == name)


def test_14_components_identity_passes(tmp_path):
    res = run(tmp_path)
    r = _rule(res, "installment_components")
    assert (r.failed, r.checked, r.skipped) == (0, 6, 1)   # the control-total row is skipped, never checked
    assert res.counts.rules["installment_components"]["failed"] == 0


def _break(tmp_path, header, delta=5.0):
    rows = base_rows()
    rows[2][header] = round(rows[2][header] + delta, 2)
    return run(tmp_path, rows, footer_row=footer(base_rows()) if header == "TAKSİT TUTARI" else None)


def test_15_principal_mismatch_fails(tmp_path):
    res = _break(tmp_path, "TAKSİT ANAPARA")
    assert not res.ok and _rule(res, "installment_components").failed == 1


def test_16_interest_mismatch_fails(tmp_path):
    assert _rule(_break(tmp_path, "TAKSİT FAİZ"), "installment_components").failed == 1


def test_17_bsmv_mismatch_fails(tmp_path):
    assert _rule(_break(tmp_path, "TAKSİT BSMV"), "installment_components").failed == 1


def test_18_kkdf_mismatch_fails(tmp_path):
    assert _rule(_break(tmp_path, "TAKSİT KKDF", 1.0), "installment_components").failed == 1


def test_19_paid_plus_remaining_passes(tmp_path):
    assert _rule(run(tmp_path), "paid_plus_remaining").failed == 0


def test_20_paid_plus_remaining_mismatch_fails(tmp_path):
    rows = base_rows()
    rows[0]["KALAN TAKSİT TUTARI"] = 10.0         # paid in full but 10 remaining
    res = run(tmp_path, rows)
    assert not res.ok and _rule(res, "paid_plus_remaining").failed == 1
    assert "rule_failed" in codes(res, K.Severity.ERROR)


def test_21_tolerance_deterministic(tmp_path):
    assert K.AMOUNT_TOLERANCE == 0.01
    ok = base_rows()
    ok[2]["TAKSİT TUTARI"] = round(ok[2]["TAKSİT TUTARI"] + 0.01, 2)   # within 0.01 → passes
    assert _rule(run(tmp_path, ok, footer_row=footer(ok)), "installment_components").failed == 0
    bad = base_rows()
    bad[2]["TAKSİT TUTARI"] = round(bad[2]["TAKSİT TUTARI"] + 0.02, 2)  # beyond → fails, every time
    results = {_rule(run(tmp_path, bad, footer_row=footer(bad)), "installment_components").failed for _ in range(3)}
    assert results == {1}


def test_22_no_silent_repair(tmp_path):
    rows = base_rows()
    rows[2]["TAKSİT TUTARI"] = 9999.0
    res = run(tmp_path, rows, footer_row=footer(rows))
    assert not res.ok and res.installments == []
    assert res.import_result.records == []      # nothing staged, nothing recomputed
    src = (ROOT / "services/importers/kredi.py").read_text()
    assert "installment_total =" not in src.replace("installment_total=v[", "")


# =============================================================================================
# Missingness (23–27)
# =============================================================================================
def test_23_blank_payment_slip_on_unpaid_allowed(tmp_path):
    rows = base_rows()
    rows[2]["ÖDEME FİŞİ SERİ NO"] = None
    rows[2]["ÖDEME FİŞİ SIRA NO"] = None
    res = run(tmp_path, rows)
    assert res.ok and res.installments[2].payment_slip_series.is_null


def test_24_blank_payment_date_when_unpaid_allowed(tmp_path):
    res = run(tmp_path)
    unpaid = [i for i in res.installments if i.paid_amount.value == 0]
    assert unpaid and all(i.payment_date.value is None for i in unpaid)
    assert "paid_without_payment_date" not in codes(res)
    rows = base_rows()
    rows[0]["ÖDEME FİŞ TARİHİ"] = None           # paid but no date → warning, never invented
    res = run(tmp_path, rows)
    assert res.ok and "paid_without_payment_date" in codes(res, K.Severity.WARNING)
    assert res.installments[0].payment_date.value is None


def test_25_missing_remaining_principal_policy(tmp_path):
    rows = base_rows()
    rows[3]["KALAN ANAPARA"] = None
    res = run(tmp_path, rows)
    assert res.ok
    nv = res.installments[3].remaining_principal
    assert nv.value is None and nv.is_null and res.counts.field_fill["remaining_principal"] == 5


def test_26_blank_numeric_is_not_zero(tmp_path):
    rows = base_rows()
    rows[2]["TAKSİT KKDF"] = None
    res = run(tmp_path, rows)
    assert not res.ok and next(i for i in res.report.issues if i.code == "missing_required_value").field == "kkdf"


def test_27_true_zero_stays_zero(tmp_path):
    res = run(tmp_path)
    i = res.installments[2]
    assert i.kkdf.value == 0.0 and not i.kkdf.is_null and i.paid_amount.value == 0.0
    assert i.payment_slip_number.value == 0  # recorded as written, not turned into "missing"


# =============================================================================================
# Confidence (28–31)
# =============================================================================================
def test_28_valid_fields_keep_declared_confidence(tmp_path):
    res = run(tmp_path)
    fc = res.import_result.field_confidence
    for f in ("contract_code", "due_date", "installment_total", "principal", "paid_amount", "remaining_principal"):
        assert fc[f] is Confidence.TRUSTED, f
    for f in ("status", "closing_date", "payment_slip_series", "payment_slip_number"):
        assert fc[f] is Confidence.UNVERIFIED, f


def test_29_technical_error_downgrades_invalid(tmp_path):
    rows = base_rows()
    rows[0]["KAPANIŞ TARİHİ"] = E("#VALUE!")       # informational field: cell-level invalid
    res = run(tmp_path, rows)
    assert res.ok and res.installments[0].closing_date.confidence is Confidence.INVALID
    assert res.installments[0].closing_date.error == "#VALUE!"
    rows = base_rows()
    rows[0]["TAKSİT FAİZ"] = E("#REF!")            # money field: fatal
    res = run(tmp_path, rows)
    assert not res.ok and "excel_error" in codes(res, K.Severity.ERROR)


def test_30_formulas_handled_by_framework(tmp_path):
    rows = base_rows()
    rows[2]["TAKSİT TUTARI"] = F("N4+O4+P4+Q4", rows[2]["TAKSİT TUTARI"])
    res = run(tmp_path, rows, footer_row=footer(base_rows()))
    assert not res.ok and "formula_forbidden" in codes(res)


def test_31_no_field_upgraded(tmp_path):
    res = run(tmp_path)
    assert all(i.status in (LoanStatus.ACTIVE, LoanStatus.CLOSED) for i in res.installments)
    assert all(i.closing_date.confidence in (Confidence.UNVERIFIED, Confidence.INVALID) for i in res.installments)
    assert all(i.installment_total.kind is CellKind.LITERAL for i in res.installments)
    assert next(c for c in res.import_result.verification.checks if c.name == "confidence_never_upgraded").ok
    assert next(c for c in res.verification.checks if c.name == "money_fields_trusted").ok


# =============================================================================================
# Privacy (32–37)
# =============================================================================================
def test_32_summary_excludes_amounts(tmp_path):
    b = blob(run(tmp_path))
    for amount in ("987654", "4111.11", "3500.25", "500.5", "250000", "1000.0"):
        assert amount not in b, amount


def test_33_summary_excludes_contract_codes(tmp_path):
    assert "ZQX" not in blob(run(tmp_path))


def test_34_summary_excludes_lenders(tmp_path):
    b = blob(run(tmp_path))
    assert not any(n in b for n in LENDER_NAMES) and "BNK-0" not in b
    assert run(tmp_path).counts.distinct_lenders == 2


def test_35_summary_excludes_payment_references(tmp_path):
    b = blob(run(tmp_path))
    assert "70002" not in b and "70003" not in b and '"ODM"' not in b


def test_36_report_excludes_absolute_path(tmp_path):
    deep = tmp_path / "Users" / "owner" / "Gizli"
    deep.mkdir(parents=True)
    res = import_kredi(book(deep))
    assert res.file == NAME and str(tmp_path) not in blob(res) and "Gizli" not in blob(res)


def test_37_sensitive_values_not_in_errors(tmp_path):
    rows = base_rows() + [inst(no=3, due=D(2026, 9, 9), principal=4111.11)]
    rows[1]["BANKA KODU"] = "BNK-GIZLI-77"
    res = run(tmp_path, rows, footer_row={"SÖZLEŞME KODU": 555})
    assert not res.ok
    b = blob(res)
    for leaked in ("ZQX", "GIZLI", "4111", "Kurgu", "Hayali", "555"):
        assert leaked not in b, leaked


# =============================================================================================
# Isolation (38–42)
# =============================================================================================
LIVE = ("app.py", "dashboard_builder.py", "services/refresh.py", "services/context.py", "services/action_sources.py",
        "services/actions.py", "services/entity.py", "services/evidence.py", "services/agent.py")


def test_38_no_m5_import():
    for live in LIVE:
        text = (ROOT / live).read_text()
        assert "importers" not in text and "import_kredi" not in text and "LoanInstallment" not in text, live


def test_39_no_api_exposure():
    app = (ROOT / "app.py").read_text()                     # M7-6: owner_view section only, no importer, no rows
    assert "import_kredi" not in app and "importers" not in app and "loan" not in app.lower()
    assert "LoanInstallment" not in app and "lender" not in app.lower()


def test_40_no_dashboard_exposure():
    text = (ROOT / "dashboard_builder.py").read_text()
    assert "import_kredi" not in text and "LoanInstallment" not in text


def test_41_no_ai_imports_or_calls():
    src = (ROOT / "services/importers/kredi.py").read_text()
    for banned in ("openai", "anthropic", "services.ai", "services.agent", "httpx", "requests", "urllib"):
        assert banned not in src, banned
    code = ("import sys, services.importers.kredi;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_42_no_persistence_or_refresh(tmp_path):
    p = book(tmp_path)
    before = (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir()))
    assert import_kredi(p).ok
    assert (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir())) == before
    src = (ROOT / "services/importers/kredi.py").read_text()
    for banned in ("write_text", "write_bytes", ".save(", "sqlite", "replace_context", "open(", "openpyxl",
                   "load_workbook", "pandas"):
        assert banned not in src, banned
    assert "kredi" not in (ROOT / "services/refresh.py").read_text().lower()


# =============================================================================================
# Control total, dates, statuses
# =============================================================================================
def test_control_total_row_verified(tmp_path):
    res = run(tmp_path, footer_row={**footer(base_rows()), "TAKSİT ANAPARA": 1.0})
    assert not res.ok and "control_totals_mismatch" in codes(res)
    res = run(tmp_path, with_footer=False)
    assert res.ok and not res.counts.control_total_row and "no_control_total_row" in codes(res)
    assert run(tmp_path).counts.installments == 6   # the total row is never an installment


def test_latest_payment_date_is_not_snapshot_date(tmp_path):
    res = run(tmp_path)
    assert res.counts.latest_recorded_payment_date == D(2026, 2, 1)
    assert res.counts.snapshot_date is None and res.counts.snapshot_date_origin is None
    res = run(tmp_path, snapshot_date=D(2026, 2, 9))
    assert (res.counts.snapshot_date, res.counts.snapshot_date_origin) == (D(2026, 2, 9), "declared")


def test_status_normalized_and_unknown_fails(tmp_path):
    res = run(tmp_path)
    assert res.counts.status_counts == {"active": 4, "closed": 2}
    rows = base_rows()
    rows[2]["DURUM"] = "Askıda"
    assert "unknown_status" in codes(run(tmp_path, rows))
    # Status may differ between installments of one contract (row-level in some exports): not an error.
    rows = base_rows()
    rows[2]["DURUM"] = "Kapalı"
    assert run(tmp_path, rows).ok


def test_currency_kept_as_source_code(tmp_path):
    rows = base_rows()
    for r in rows[4:]:
        r["DÖVİZ CİNSİ"] = "EUR"
    res = run(tmp_path, rows)
    assert res.ok and res.counts.currencies == ["EUR", "TL"]
    assert {c.currency for c in res.contracts} == {"TL", "EUR"}
    src = (ROOT / "services/importers/kredi.py").read_text().lower()
    assert "exchange" not in src and "fx_rate" not in src and "kur " not in src
