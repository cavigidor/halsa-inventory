"""Kredi takip importer — company loan-contract installments (owner / Yönetim source).

Pulled forward while M6-4 (detailed collections) is blocked by a missing export; see docs/m6_kredi.md and
D-038. This is Halsa's own financing (bank loans), not customer credit, not Hedef `Risk` / `Kredi`, not
receivables or collections.

Source module on the M6-1 framework (`run_import` + `SourceSpec`; no reader of its own):
- **Audience `owner`.** Never for salesperson views, actions, evidence packets or AI.
- **Grain:** one row = one loan-contract installment, key `SÖZLEŞME KODU` × `TAKSİT NO`. A repeated pair
  is fatal (no native discriminator exists). The ERP export ends with one control-total row (first cell
  = row count, amount columns = column sums); it is detected, verified against the sums, and excluded.
- **Supported loan type:** `Tüketici kredisi modeli` (amortizing) only. `Rotatif` (revolving) is known
  but unsupported: amortizing identities do not describe it, so the import fails closed. Unknown types
  and statuses fail closed too.
- **Arithmetic (fatal, tolerance 0.01 TL):** installment = principal + interest + BSMV + KKDF, and
  paid + remaining = installment. An incorrect payment total must never reach cash planning.
- **Dates:** the latest recorded payment-slip date is reported as an activity date, never as the
  workbook's snapshot date; the snapshot date is unknown unless declared. Future due dates are normal.
- **No cash-planning metrics,** no FX, no UI, no API, no persistence. Safe summaries carry counts only.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportResult, run_import
from .excel import WorkbookPair, label_text
from .keys import record_key
from .models import (
    Audience,
    Confidence,
    NormalizedRecord,
    NormalizedValue,
    Severity,
    ValidationReport,
    VerificationResult,
)
from .spec import DateRule, FieldSpec, FieldType, RowRule, SourceSpec, TableLayout, VerifySpec

KREDI_FILE_PATTERNS = ("*kredi*.xlsx", "*Kredi*.xlsx", "*KREDİ*.xlsx")
#: Currency arithmetic tolerance (kuruş rounding), absolute TL.
AMOUNT_TOLERANCE = 0.01
#: Control-total row tolerance (sums of up to a few hundred rounded amounts).
CONTROL_TOTAL_TOLERANCE = 0.05


class LoanType(str, Enum):
    AMORTIZING = "amortizing"     # source "Tüketici kredisi modeli"
    REVOLVING = "revolving"       # source "Rotatif" — known, NOT supported by this importer


class LoanStatus(str, Enum):
    ACTIVE = "active"             # source "Aktif"
    CLOSED = "closed"             # source "Kapalı"


LOAN_TYPES = {"Tüketici kredisi modeli": LoanType.AMORTIZING, "Rotatif": LoanType.REVOLVING}
SUPPORTED_LOAN_TYPES = frozenset({LoanType.AMORTIZING})
STATUSES = {"Aktif": LoanStatus.ACTIVE, "Kapalı": LoanStatus.CLOSED}

_T, _U = Confidence.TRUSTED, Confidence.UNVERIFIED


def _f(name: str, type_: FieldType, header: str, conf: Confidence = _T, *, tolerant: bool = False) -> FieldSpec:
    # nullable at framework level: the trailing control-total row is blank in these columns; the module
    # enforces presence on installment rows itself (and fails closed). `tolerant` only for informational
    # fields (closing date, payment-slip reference): a bad cell there is a cell-level warning.
    return FieldSpec(name, type_, header=header, confidence=conf, formulas="forbid", tolerate_invalid_cells=tolerant)


def _identity(name: str, total: str, parts: tuple[str, ...]) -> RowRule:
    """total == sum(parts) within AMOUNT_TOLERANCE, on installment rows only: `installment_number` is a rule
    input, so the control-total row (which has none) is skipped rather than checked."""
    def check(v: dict[str, Any]) -> bool:
        # Rounded to 1e-6 before comparing so float noise never decides the boundary: a difference of up to
        # and including AMOUNT_TOLERANCE passes, anything larger fails — deterministically.
        return abs(round(float(v[total]) - sum(float(v[p]) for p in parts), 6)) <= AMOUNT_TOLERANCE

    return RowRule(name, (total, *parts, "installment_number"), check, Severity.ERROR,
                   f"{total} = " + " + ".join(parts) + f" (±{AMOUNT_TOLERANCE} TL)")


#: Exact headers of the ERP loan-contract export (`kredi sözleşmeleri.xlsx`; 23 columns). Not imported:
#: `AÇIKLAMA` (free text), `BANKA ADI` (the lender code suffices).
KREDI_SPEC = SourceSpec(
    key="kredi_installments",
    audience=Audience.OWNER,
    filename_patterns=KREDI_FILE_PATTERNS,
    sheet=0,
    layout=TableLayout(header_row=1),
    fields=(
        FieldSpec("contract_code", FieldType.OPAQUE_ID, header="SÖZLEŞME KODU", nullable=False, confidence=_T,
                  formulas="forbid"),
        _f("lender_code", FieldType.OPAQUE_ID, "BANKA KODU"),
        _f("loan_type", FieldType.STRING, "KREDİ TİPİ"),
        _f("currency", FieldType.STRING, "DÖVİZ CİNSİ"),
        _f("closing_date", FieldType.DATE, "KAPANIŞ TARİHİ", _U, tolerant=True),
        _f("status", FieldType.STRING, "DURUM", _U),
        _f("original_amount", FieldType.DECIMAL, "KREDİ TUTARI"),
        _f("installment_count", FieldType.INTEGER, "TAKSİT SAYISI"),
        _f("installment_number", FieldType.INTEGER, "TAKSİT NO"),
        _f("due_date", FieldType.DATE, "VADE TARİHİ"),
        _f("installment_total", FieldType.DECIMAL, "TAKSİT TUTARI"),
        _f("principal", FieldType.DECIMAL, "TAKSİT ANAPARA"),
        _f("interest", FieldType.DECIMAL, "TAKSİT FAİZ"),
        _f("bsmv", FieldType.DECIMAL, "TAKSİT BSMV"),
        _f("kkdf", FieldType.DECIMAL, "TAKSİT KKDF"),
        _f("remaining_principal", FieldType.DECIMAL, "KALAN ANAPARA"),
        _f("payment_slip_series", FieldType.OPAQUE_ID, "ÖDEME FİŞİ SERİ NO", _U, tolerant=True),
        _f("payment_slip_number", FieldType.INTEGER, "ÖDEME FİŞİ SIRA NO", _U, tolerant=True),
        _f("payment_date", FieldType.DATE, "ÖDEME FİŞ TARİHİ"),
        _f("paid_amount", FieldType.DECIMAL, "ÖDENEN TAKSİT TUTARI"),
        _f("remaining_amount", FieldType.DECIMAL, "KALAN TAKSİT TUTARI"),
    ),
    default_confidence=_U,
    business_date=DateRule(),   # no snapshot date in the workbook; declared per call if known
    identity_fields=("contract_code", "installment_number"),
    rules=(
        _identity("installment_components", "installment_total", ("principal", "interest", "bsmv", "kkdf")),
        _identity("paid_plus_remaining", "installment_total", ("paid_amount", "remaining_amount")),
    ),
    verify=VerifySpec(min_rows=1),
    allow_warnings=True,
    description="ERP loan-contract export: one row per installment (company financing, owner only).",
)

REQUIRED_ON_INSTALLMENT = ("lender_code", "loan_type", "currency", "status", "original_amount", "installment_count",
                           "installment_number", "due_date", "installment_total", "principal", "interest", "bsmv",
                           "kkdf", "paid_amount", "remaining_amount")
CONTRACT_LEVEL = ("lender_code", "loan_type", "currency", "original_amount", "installment_count")
CONTROL_TOTAL_FIELDS = ("original_amount", "installment_total", "principal", "interest", "bsmv", "kkdf",
                        "paid_amount", "remaining_amount")


# ---------------------------------------------------------------------------------------------
# Models (in memory)
# ---------------------------------------------------------------------------------------------
@dataclass(slots=True)
class LoanContract:
    contract_key: str                 # record_key("kredi_contract", [contract code])
    contract_code: str
    lender_code: str
    loan_type: LoanType
    currency: str                     # source currency code, kept as written; no FX
    original_amount: NormalizedValue
    installment_count: NormalizedValue  # 0 = not recorded by the ERP (one real contract); never inferred
    installment_keys: tuple[str, ...]


@dataclass(slots=True)
class LoanInstallment:
    installment_key: str              # record_key("kredi_installment", [contract code, installment no])
    contract_key: str
    installment_number: int
    status: LoanStatus                # source DURUM (unverified: row vs contract semantics differ across exports)
    closing_date: NormalizedValue
    due_date: NormalizedValue
    installment_total: NormalizedValue
    principal: NormalizedValue
    interest: NormalizedValue
    bsmv: NormalizedValue
    kkdf: NormalizedValue
    remaining_principal: NormalizedValue   # separate fact; never derived from other fields
    paid_amount: NormalizedValue
    remaining_amount: NormalizedValue
    payment_date: NormalizedValue
    payment_slip_series: NormalizedValue
    payment_slip_number: NormalizedValue   # 0 on every unpaid row in the real export (recorded as is)
    source_row: int


class KrediSummary(BaseModel):
    """Counts only — no contract codes, lender codes or names, amounts or document numbers."""

    model_config = ConfigDict(extra="forbid")
    rows_read: int = 0
    control_total_row: bool = False
    control_totals_match: bool | None = None
    contracts: int = 0
    installments: int = 0
    duplicate_installment_identities: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    distinct_lenders: int = 0
    currencies: list[str] = Field(default_factory=list)
    loan_types: dict[str, int] = Field(default_factory=dict)
    due_date_min: dt.date | None = None
    due_date_max: dt.date | None = None
    future_due_installments_vs_latest_payment: int = 0
    latest_recorded_payment_date: dt.date | None = None
    snapshot_date: dt.date | None = None
    snapshot_date_origin: str | None = None
    field_fill: dict[str, int] = Field(default_factory=dict)
    rules: dict[str, dict[str, int]] = Field(default_factory=dict)
    invalid_installment_rows: int = 0
    contracts_without_installment_count: int = 0
    paid_without_payment_date: int = 0
    payment_date_without_paid_amount: int = 0


@dataclass
class KrediResult:
    ok: bool
    failed_step: str | None
    file: str
    contracts: list[LoanContract]
    installments: list[LoanInstallment]
    report: ValidationReport
    verification: VerificationResult
    counts: KrediSummary
    import_result: ImportResult | None = None

    @property
    def audience(self) -> Audience:
        return KREDI_SPEC.audience

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failed_step": self.failed_step,
            "source": KREDI_SPEC.key,
            "audience": self.audience.value,
            "file": self.file,
            "counts": self.counts.model_dump(mode="json"),
            "report": self.report.to_dict(),
            "framework_verification": (self.import_result.verification.model_dump(mode="json")
                                       if self.import_result else None),
            "verification": self.verification.model_dump(mode="json"),
        }


# ---------------------------------------------------------------------------------------------
# Candidates (never selects)
# ---------------------------------------------------------------------------------------------
def required_headers() -> frozenset[str]:
    return frozenset(f.header.name for f in KREDI_SPEC.fields)  # type: ignore[union-attr]


def find_kredi_candidates(folder: str | Path) -> list[dict[str, Any]]:
    """List Kredi-looking workbooks (basename, whether the header row carries the supported columns).
    Never selects one and never orders by modification time. Header compatibility says nothing about
    loan types or arithmetic — only `import_kredi` decides whether a workbook is supported."""
    out = []
    for p in sorted(Path(folder).glob("*.xlsx")):
        if p.name.startswith("~$") or not KREDI_SPEC.matches_filename(p.name):
            continue
        try:
            with WorkbookPair(p) as wb:
                sheet = wb.sheet(0)
                headers = {label_text(c) for c in sheet.row(1)} if sheet else set()
            compatible = required_headers() <= headers
        except Exception:
            compatible = False
        out.append({"file": p.name, "headers_compatible": compatible})
    return out


# ---------------------------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------------------------
def import_kredi(path: str | Path, *, snapshot_date: dt.date | None = None) -> KrediResult:
    """Import one explicitly chosen loan-contract export. Fail-closed on schema, identity, type/status
    drift, contract-level inconsistency, impossible installment numbers, control-total mismatch and both
    arithmetic identities. Payment-slip anomalies are warnings."""
    path = Path(path)
    spec = replace(KREDI_SPEC, business_date=DateRule.declared()) if snapshot_date is not None else KREDI_SPEC
    res = run_import(path, spec, declared_date=snapshot_date)
    report = res.report
    counts = KrediSummary(rows_read=report.rows_read)
    result = KrediResult(False, res.failed_step, path.name, [], [], report, VerificationResult(), counts,
                         import_result=res)
    counts.rules = {r.name: {"checked": r.checked, "failed": r.failed, "skipped": r.skipped}
                    for r in res.rule_results}
    if not res.ok:
        return result

    records = list(res.records)
    footer = _control_total_row(records)
    if footer is not None:
        records = records[:-1]
        counts.control_total_row = True
        counts.control_totals_match = _control_totals_match(footer, records)
        if not counts.control_totals_match:
            report.add(Severity.ERROR, "control_totals_mismatch",
                       "The export's control-total row does not equal the column sums.")
    else:
        report.add(Severity.INFO, "no_control_total_row", "No trailing control-total row was found.")

    _validate_installments(records, report, counts)
    if report.errors:
        result.failed_step = "installments"
        return result

    contracts, installments = _build(records)
    _fill_counts(counts, res, contracts, installments, snapshot_date)
    _verify(result, contracts, installments, records)
    if not result.verification.ok:
        failed = [c.name for c in result.verification.checks if not c.ok]
        report.add(Severity.ERROR, "kredi_verification_failed", "Kredi verification failed: " + ", ".join(failed) + ".",
                   count=len(failed))
        result.failed_step = "verify"
        return result
    result.ok, result.failed_step = True, None
    result.contracts, result.installments = contracts, installments
    return result


def _control_total_row(records: list[NormalizedRecord]) -> NormalizedRecord | None:
    """The ERP's trailing total row: last row, no installment number / lender / due date, and a first cell
    equal to the number of installment rows above it."""
    if len(records) < 2:
        return None
    last = records[-1]
    if any(last.get(f) is not None for f in ("installment_number", "lender_code", "due_date", "loan_type")):
        return None
    return last if last.get("contract_code") == str(len(records) - 1) else None


def _control_totals_match(footer: NormalizedRecord, rows: list[NormalizedRecord]) -> bool:
    for f in CONTROL_TOTAL_FIELDS:
        total = footer.get(f)
        if total is None:
            continue
        if abs(sum((r.get(f) or 0.0) for r in rows) - total) > CONTROL_TOTAL_TOLERANCE:
            return False
    return True


def _validate_installments(rows: list[NormalizedRecord], report: ValidationReport, counts: KrediSummary) -> None:
    missing = Counter()
    for r in rows:
        for f in REQUIRED_ON_INSTALLMENT:
            nv = r.values.get(f)
            if nv is None or nv.value is None:
                missing[f] += 1
    for f, n in sorted(missing.items()):
        report.add(Severity.ERROR, "missing_required_value", f"{n} installment row(s) have no value.", field=f, count=n)

    types = Counter(r.get("loan_type") for r in rows if r.get("loan_type") is not None)
    unknown_types = sum(n for t, n in types.items() if t not in LOAN_TYPES)
    unsupported = sum(n for t, n in types.items() if t in LOAN_TYPES and LOAN_TYPES[t] not in SUPPORTED_LOAN_TYPES)
    if unknown_types:
        report.add(Severity.ERROR, "unknown_loan_type", f"{unknown_types} row(s) carry an unknown loan type.",
                   field="loan_type", count=unknown_types)
    if unsupported:
        report.add(Severity.ERROR, "unsupported_loan_type",
                   f"{unsupported} row(s) are revolving (Rotatif) loans; this importer supports amortizing loans "
                   "only and does not apply installment identities to them.", field="loan_type", count=unsupported)
    unknown_status = sum(1 for r in rows if r.get("status") is not None and r.get("status") not in STATUSES)
    if unknown_status:
        report.add(Severity.ERROR, "unknown_status", f"{unknown_status} row(s) carry an unknown status.",
                   field="status", count=unknown_status)

    pairs = Counter((r.get("contract_code"), r.get("installment_number")) for r in rows)
    dup = sum(n - 1 for n in pairs.values() if n > 1)
    counts.duplicate_installment_identities = dup
    if dup:
        report.add(Severity.ERROR, "duplicate_installment",
                   f"{dup} repeated contract × installment pair(s); no native field distinguishes them.",
                   field="installment_number", count=dup)

    by_contract: dict[Any, list[NormalizedRecord]] = defaultdict(list)
    for r in rows:
        by_contract[r.get("contract_code")].append(r)
    for f in CONTRACT_LEVEL:
        n = sum(1 for rs in by_contract.values() if len({r.get(f) for r in rs}) > 1)
        if n:
            report.add(Severity.ERROR, "contract_field_inconsistent",
                       f"{n} contract(s) carry different values across their installments.", field=f, count=n)

    bad_low = sum(1 for r in rows if isinstance(r.get("installment_number"), int) and r.get("installment_number") < 1)
    bad_high = sum(1 for r in rows if isinstance(r.get("installment_number"), int)
                   and isinstance(r.get("installment_count"), int) and r.get("installment_count") > 0
                   and r.get("installment_number") > r.get("installment_count"))
    if bad_low:
        report.add(Severity.ERROR, "installment_number_not_positive", f"{bad_low} installment number(s) are below 1.",
                   field="installment_number", count=bad_low)
    if bad_high:
        report.add(Severity.ERROR, "installment_number_exceeds_count",
                   f"{bad_high} installment number(s) exceed the contract's declared installment count.",
                   field="installment_number", count=bad_high)
    no_count = sum(1 for rs in by_contract.values() if all(r.get("installment_count") == 0 for r in rs))
    counts.contracts_without_installment_count = no_count
    if no_count:
        report.add(Severity.INFO, "installment_count_not_recorded",
                   f"{no_count} contract(s) record an installment count of 0; the count is treated as not recorded "
                   "and the upper-bound check is skipped for them.", field="installment_count", count=no_count)

    paid_no_date = sum(1 for r in rows if (r.get("paid_amount") or 0) != 0 and r.get("payment_date") is None)
    date_no_paid = sum(1 for r in rows if (r.get("paid_amount") or 0) == 0 and r.get("payment_date") is not None)
    counts.paid_without_payment_date, counts.payment_date_without_paid_amount = paid_no_date, date_no_paid
    if paid_no_date:
        report.add(Severity.WARNING, "paid_without_payment_date",
                   f"{paid_no_date} installment(s) record a paid amount but no payment-slip date.",
                   field="payment_date", count=paid_no_date)
    if date_no_paid:
        report.add(Severity.WARNING, "payment_date_without_paid_amount",
                   f"{date_no_paid} installment(s) carry a payment-slip date but no paid amount.",
                   field="payment_date", count=date_no_paid)
    counts.invalid_installment_rows = sum(1 for r in rows if any(nv.confidence is Confidence.INVALID
                                                                 for nv in r.values.values()))


def _build(rows: list[NormalizedRecord]) -> tuple[list[LoanContract], list[LoanInstallment]]:
    installments: list[LoanInstallment] = []
    by_contract: dict[str, list[LoanInstallment]] = defaultdict(list)
    first: dict[str, NormalizedRecord] = {}
    for r in rows:
        code = r.get("contract_code")
        ckey = record_key("kredi_contract", [code])
        v = r.values
        inst = LoanInstallment(
            installment_key=record_key("kredi_installment", [code, r.get("installment_number")]),
            contract_key=ckey, installment_number=r.get("installment_number"), status=STATUSES[r.get("status")],
            closing_date=v["closing_date"], due_date=v["due_date"], installment_total=v["installment_total"],
            principal=v["principal"], interest=v["interest"], bsmv=v["bsmv"], kkdf=v["kkdf"],
            remaining_principal=v["remaining_principal"], paid_amount=v["paid_amount"],
            remaining_amount=v["remaining_amount"], payment_date=v["payment_date"],
            payment_slip_series=v["payment_slip_series"], payment_slip_number=v["payment_slip_number"],
            source_row=r.row)
        installments.append(inst)
        by_contract[code].append(inst)
        first.setdefault(code, r)
    contracts = []
    for code, insts in by_contract.items():
        r = first[code]
        contracts.append(LoanContract(
            contract_key=record_key("kredi_contract", [code]), contract_code=code, lender_code=r.get("lender_code"),
            loan_type=LOAN_TYPES[r.get("loan_type")], currency=r.get("currency"),
            original_amount=r.values["original_amount"], installment_count=r.values["installment_count"],
            installment_keys=tuple(i.installment_key for i in insts)))
    return contracts, installments


def _fill_counts(c: KrediSummary, res: ImportResult, contracts: list[LoanContract],
                 installments: list[LoanInstallment], snapshot_date: dt.date | None) -> None:
    c.contracts, c.installments = len(contracts), len(installments)
    c.status_counts = dict(sorted(Counter(i.status.value for i in installments).items()))
    c.distinct_lenders = len({k.lender_code for k in contracts})
    c.currencies = sorted({k.currency for k in contracts})
    c.loan_types = dict(sorted(Counter(k.loan_type.value for k in contracts).items()))
    dues = [i.due_date.value for i in installments if i.due_date.value is not None]
    c.due_date_min, c.due_date_max = (min(dues), max(dues)) if dues else (None, None)
    pays = [i.payment_date.value for i in installments if i.payment_date.value is not None]
    c.latest_recorded_payment_date = max(pays) if pays else None
    if c.latest_recorded_payment_date:
        c.future_due_installments_vs_latest_payment = sum(1 for d in dues if d > c.latest_recorded_payment_date)
    c.snapshot_date = snapshot_date
    c.snapshot_date_origin = "declared" if snapshot_date is not None else None
    fields = ("remaining_principal", "payment_date", "payment_slip_series", "payment_slip_number", "closing_date")
    c.field_fill = {f: sum(1 for i in installments if getattr(i, f).value is not None) for f in fields}


def _verify(result: KrediResult, contracts: list[LoanContract], installments: list[LoanInstallment],
            rows: list[NormalizedRecord]) -> None:
    v = result.verification
    v.add("installments_reconcile", len(installments) == len(rows) == result.counts.installments)
    v.add("contracts_reconcile", sum(len(k.installment_keys) for k in contracts) == len(installments))
    keys = [i.installment_key for i in installments]
    v.add("installment_keys_unique", len(keys) == len(set(keys)))
    v.add("audience_owner", result.audience is Audience.OWNER)
    v.add("supported_types_only", all(k.loan_type in SUPPORTED_LOAN_TYPES for k in contracts))
    money = ("installment_total", "principal", "interest", "bsmv", "kkdf", "paid_amount", "remaining_amount")
    v.add("money_fields_trusted", all(getattr(i, f).confidence is Confidence.TRUSTED for i in installments for f in money))
    blob = json.dumps(result.summary(), ensure_ascii=False, default=str)
    sensitive = {k.contract_code for k in contracts} | {k.lender_code for k in contracts}
    leaked = sum(1 for s in sensitive if len(s) >= 3 and s in blob)
    v.add("summary_has_no_identifiers", leaked == 0, f"{leaked} leak(s)")


__all__ = ["KREDI_SPEC", "LoanType", "LoanStatus", "LoanContract", "LoanInstallment", "KrediSummary", "KrediResult",
           "import_kredi", "find_kredi_candidates", "required_headers", "AMOUNT_TOLERANCE", "SUPPORTED_LOAN_TYPES"]
