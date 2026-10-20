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

Two source layouts are recognized deterministically (M8-ACCURACY-2, `probe_layout`); anything else fails closed:
- **`erp_export`** — the original ERP export: the 23 headers in row 1, installments from row 2, one trailing ERP
  control-total row.
- **`filtered_takip`** — the owner's revised "kredi takip" workbook: row 1 holds only summary formulas linked to the
  SUBTOTAL row, the same 23 headers in row 2, installments from row 3, the ERP control-total row, and a last row of
  Excel `SUBTOTAL(...)` formulas. The saved AutoFilter (hidden rows) is a VIEW: every row is imported and validated,
  hidden or not; the SUBTOTAL cells are checked against the visible rows separately from the full-source ERP totals.
  Neither summary row is ever an installment.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import re

from .base import ImportResult, run_import
from .excel import CellRead, WorkbookPair, column_index, label_text, sheet_layout_flags
from .keys import record_key
from .models import (
    Audience,
    CellKind,
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
    hidden_in_source: bool = False         # the workbook's saved filter hides this row (a view, never a payment fact)


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
    # --- M8-ACCURACY-2: layout, saved view and source exceptions (counts only) ---
    layout: str | None = None
    header_row: int | None = None
    saved_filter: bool = False
    filter_columns: list[str] = Field(default_factory=list)       # header names of filtered columns
    hidden_installment_rows: int = 0
    visible_installment_rows: int = 0
    subtotal_row: bool = False
    subtotal_checks: dict[str, int] = Field(default_factory=dict)  # checked / matched / mismatched / unsupported / missing_cache
    summary_row_checks: dict[str, int] = Field(default_factory=dict)
    filtered_totals_match: bool | None = None                      # SUBTOTAL cells == visible rows (None: not checked)
    negative_remaining_rows: int = 0
    negative_amount_rows: int = 0                                  # any other money field below zero
    closed_with_positive_remaining: int = 0


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
# Layout detection (deterministic; never guesses a header location)
# ---------------------------------------------------------------------------------------------
LAYOUT_ERP = "erp_export"
LAYOUT_TAKIP = "filtered_takip"
SUBTOTAL_RE = re.compile(r"^=\s*SUBTOTAL\(\s*(\d+)\s*,\s*\$?([A-Z]{1,3})\$?(\d+)\s*:\s*\$?([A-Z]{1,3})\$?(\d+)\s*\)\s*$",
                         re.IGNORECASE)
CELL_REF_RE = re.compile(r"^=\s*\$?([A-Z]{1,3})\$?(\d+)\s*$", re.IGNORECASE)
SUBTOTAL_SUM = frozenset({9, 109})
SUBTOTAL_COUNT_NUMBERS = frozenset({2, 102})
SUBTOTAL_COUNT_VALUES = frozenset({3, 103})


@dataclass
class LayoutProbe:
    layout: str | None = None
    header_row: int | None = None
    data_end_row: int | None = None        # last row handed to the framework (the ERP control-total row included)
    subtotal_row: int | None = None
    last_row: int | None = None
    problems: list[tuple[str, str]] = field(default_factory=list)   # (code, message), fatal
    grid: dict[int, list[CellRead]] = field(default_factory=dict)   # every row as read (small owner sheet)
    headers_found: bool = False            # the full header set is present at `header_row`
    hidden_rows: frozenset[int] = frozenset()
    auto_filter_ref: str | None = None
    filter_columns: tuple[int, ...] = ()


def _row_labels(cells: list[CellRead]) -> set[str]:
    return {t for t in (label_text(c) for c in cells) if t is not None}


def probe_layout(path: str | Path, content: bytes) -> LayoutProbe:
    """Classify the workbook as `erp_export`, `filtered_takip` or unsupported, from its content only."""
    probe = LayoutProbe()
    req = required_headers()
    with WorkbookPair(path, content=content) as wb:
        sheet = wb.sheet(KREDI_SPEC.sheet)
        if sheet is None:
            probe.problems.append(("sheet_missing", "The declared sheet does not exist."))
            return probe
        formulas: dict[tuple[int, int], str] = {}
        for r, cells in sheet.iter_rows(min_row=1):
            probe.grid[r] = cells
            for c_no, c in enumerate(cells, start=1):
                if c.formula:
                    formulas[(r, c_no)] = c.formula
    probe._formulas = formulas  # type: ignore[attr-defined]
    try:
        flags = sheet_layout_flags(path, 0, content=content)
        probe.hidden_rows, probe.auto_filter_ref, probe.filter_columns = (flags.hidden_rows, flags.auto_filter_ref,
                                                                          flags.filter_columns)
    except Exception:
        probe.problems.append(("sheet_flags_unreadable", "Row visibility / saved filter could not be read."))
        return probe
    nonempty = [r for r, cells in probe.grid.items() if any(c.kind is not CellKind.EMPTY for c in cells)]
    if not nonempty:
        probe.problems.append(("empty_sheet", "The sheet has no content."))
        return probe
    probe.last_row = max(nonempty)
    row1, row2 = probe.grid.get(1, []), probe.grid.get(2, [])
    h1, h2 = req <= _row_labels(row1), req <= _row_labels(row2)
    if h1 and h2:
        probe.problems.append(("layout_ambiguous", "Both row 1 and row 2 carry the full header set."))
        return probe
    if h1:
        probe.layout, probe.header_row, probe.headers_found = LAYOUT_ERP, 1, True
        return probe
    if not h2:
        # No revised-layout signature: validate strictly as the original export (row-1 headers), so the framework
        # reports the exact header problems (header_missing, …) and fails closed.
        probe.layout, probe.header_row = LAYOUT_ERP, 1
        return probe
    # filtered_takip: row 1 = summary formulas only; last row = SUBTOTAL formulas only; row above = ERP totals
    row1_cells = [(c, cell) for c, cell in enumerate(row1, start=1) if cell.kind is not CellKind.EMPTY]
    if not row1_cells or any(not cell.kind.is_formula for _, cell in row1_cells):
        probe.problems.append(("layout_unsupported", "Row 1 above the headers must contain only summary formulas."))
        return probe
    last = probe.last_row
    last_cells = [(c, cell) for c, cell in enumerate(probe.grid[last], start=1) if cell.kind is not CellKind.EMPTY]
    if not last_cells or any(not (cell.kind.is_formula and SUBTOTAL_RE.match(formulas.get((last, c), "")))
                             for c, cell in last_cells):
        probe.problems.append(("layout_unsupported", "The last row must contain only Excel SUBTOTAL formulas."))
        return probe
    for c, _ in row1_cells:
        f = formulas.get((1, c), "")
        ref = CELL_REF_RE.match(f)
        if not (SUBTOTAL_RE.match(f) or (ref and int(ref.group(2)) == last)):
            probe.problems.append(("unsupported_summary_formula",
                                   "A row-1 formula is neither a SUBTOTAL nor a reference to the SUBTOTAL row."))
            return probe
    if last - 1 <= 3:
        probe.problems.append(("layout_unsupported", "No installment rows between the headers and the totals."))
        return probe
    probe.layout, probe.header_row, probe.subtotal_row, probe.data_end_row = LAYOUT_TAKIP, 2, last, last - 1
    probe.headers_found = True
    return probe


def _num_value(read: CellRead) -> float | None:
    v = read.value
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and not read.kind.is_error else None


def _subtotal_value(probe: LayoutProbe, fn: int, col: int, r1: int, r2: int) -> float | None:
    """Excel SUBTOTAL over the VISIBLE rows of one column range (hidden rows excluded). None: unsupported."""
    vals: list[CellRead] = []
    for r in range(r1, r2 + 1):
        if r in probe.hidden_rows:
            continue
        cells = probe.grid.get(r, [])
        vals.append(cells[col - 1] if col - 1 < len(cells) else CellRead(CellKind.EMPTY))
    if fn in SUBTOTAL_SUM:
        return round(sum(x for x in (_num_value(v) for v in vals) if x is not None), 6)
    if fn in SUBTOTAL_COUNT_NUMBERS:
        return float(sum(1 for v in vals if _num_value(v) is not None))
    if fn in SUBTOTAL_COUNT_VALUES:
        return float(sum(1 for v in vals if v.kind is not CellKind.EMPTY))
    return None


def _check_summary_rows(probe: LayoutProbe) -> tuple[dict[str, int], dict[str, int]]:
    """SUBTOTAL row cells vs the visible rows, and row-1 summary formulas vs the SUBTOTAL row."""
    formulas = getattr(probe, "_formulas", {})
    sub = {"checked": 0, "matched": 0, "mismatched": 0, "unsupported": 0, "missing_cache": 0}
    top = dict(sub)
    sub_values: dict[int, float | None] = {}
    last = probe.subtotal_row
    for c, cell in enumerate(probe.grid.get(last, []), start=1):
        if cell.kind is CellKind.EMPTY:
            continue
        m = SUBTOTAL_RE.match(formulas.get((last, c), ""))
        if cell.kind is CellKind.FORMULA_MISSING_CACHE or cell.kind.is_error:
            sub["missing_cache" if cell.kind is CellKind.FORMULA_MISSING_CACHE else "mismatched"] += 1
            sub_values[c] = None
            continue
        fn, col = int(m.group(1)), column_index(m.group(2).upper())
        expected = _subtotal_value(probe, fn, col, int(m.group(3)), int(m.group(5)))
        cached = _num_value(cell)
        sub_values[c] = cached
        if expected is None or cached is None:
            sub["unsupported"] += 1
            continue
        sub["checked"] += 1
        tol = CONTROL_TOTAL_TOLERANCE if fn in SUBTOTAL_SUM else 0.0
        sub["matched" if abs(expected - cached) <= tol else "mismatched"] += 1
    for c, cell in enumerate(probe.grid.get(1, []), start=1):
        if cell.kind is CellKind.EMPTY:
            continue
        if cell.kind is CellKind.FORMULA_MISSING_CACHE or cell.kind.is_error:
            top["missing_cache" if cell.kind is CellKind.FORMULA_MISSING_CACHE else "mismatched"] += 1
            continue
        f = formulas.get((1, c), "")
        cached = _num_value(cell)
        ref = CELL_REF_RE.match(f)
        if ref:
            target = sub_values.get(column_index(ref.group(1).upper()))
        else:
            m = SUBTOTAL_RE.match(f)
            target = _subtotal_value(probe, int(m.group(1)), column_index(m.group(2).upper()),
                                     int(m.group(3)), int(m.group(5))) if m else None
        if target is None or cached is None:
            top["unsupported"] += 1
            continue
        top["checked"] += 1
        top["matched" if abs(target - cached) <= CONTROL_TOTAL_TOLERANCE else "mismatched"] += 1
    return sub, top


# ---------------------------------------------------------------------------------------------
# Candidates (never selects)
# ---------------------------------------------------------------------------------------------
def required_headers() -> frozenset[str]:
    return frozenset(f.header.name for f in KREDI_SPEC.fields)  # type: ignore[union-attr]


def find_kredi_candidates(folder: str | Path) -> list[dict[str, Any]]:
    """List Kredi-looking workbooks (basename, whether row 1 or row 2 carries the supported columns, and the detected
    layout). Never selects one and never orders by modification time. Header compatibility says nothing about loan
    types or arithmetic — only `import_kredi` decides whether a workbook is supported."""
    out = []
    for p in sorted(Path(folder).glob("*.xlsx")):
        if p.name.startswith("~$") or not KREDI_SPEC.matches_filename(p.name):
            continue
        try:
            probe = probe_layout(p, p.read_bytes())
            layout = probe.layout if (not probe.problems and probe.headers_found) else None
        except Exception:
            layout = None
        out.append({"file": p.name, "headers_compatible": layout is not None, "layout": layout})
    return out


# ---------------------------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------------------------
def _failed_layout(path: Path, probe: LayoutProbe) -> KrediResult:
    report = ValidationReport(source=KREDI_SPEC.key, audience=KREDI_SPEC.audience, file=path.name,
                              sheet=KREDI_SPEC.sheet_label(), layout="table")
    for code, msg in probe.problems or [("layout_unsupported", "Unsupported workbook layout.")]:
        report.add(Severity.ERROR, code, msg)
    counts = KrediSummary(layout=None)
    return KrediResult(False, "layout", path.name, [], [], report, VerificationResult(), counts)


def import_kredi(path: str | Path, *, snapshot_date: dt.date | None = None, content: bytes | None = None) -> KrediResult:
    """Import one explicitly chosen loan-contract workbook (`erp_export` or `filtered_takip` layout). Fail-closed on
    layout ambiguity, schema, identity, type/status drift, contract-level inconsistency, impossible installment
    numbers, control-total mismatch and both arithmetic identities. Payment-slip anomalies, negative amounts and a
    saved-filter SUBTOTAL that disagrees with the visible rows are reported (never repaired).

    `content`: the workbook bytes frozen once by the caller (M8-ACCURACY-2); otherwise read once here. Every reader
    below parses these same bytes."""
    path = Path(path)
    if content is None:
        try:
            content = path.read_bytes()
        except OSError:
            return _failed_layout(path, LayoutProbe(problems=[("unreadable_workbook", "The workbook could not be read.")]))
    try:
        probe = probe_layout(path, content)
    except Exception:
        probe = LayoutProbe(problems=[("unreadable_workbook", "The workbook could not be read.")])
    if probe.problems or probe.layout is None:
        return _failed_layout(path, probe)
    spec = replace(KREDI_SPEC, layout=TableLayout(header_row=probe.header_row, data_end_row=probe.data_end_row))
    if snapshot_date is not None:
        spec = replace(spec, business_date=DateRule.declared())
    res = run_import(path, spec, declared_date=snapshot_date, content=content)
    report = res.report
    counts = KrediSummary(rows_read=report.rows_read, layout=probe.layout, header_row=probe.header_row)
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
    elif probe.layout == LAYOUT_TAKIP:
        report.add(Severity.ERROR, "control_total_row_missing",
                   "The revised layout requires the ERP control-total row directly above the SUBTOTAL row.")
    else:
        report.add(Severity.INFO, "no_control_total_row", "No trailing control-total row was found.")

    _source_view(probe, report, counts, records)
    _validate_installments(records, report, counts)
    if report.errors:
        result.failed_step = "installments"
        return result

    contracts, installments = _build(records, probe.hidden_rows)
    _fill_counts(counts, res, contracts, installments, snapshot_date)
    _exceptions(report, counts, installments)
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


def _source_view(probe: LayoutProbe, report: ValidationReport, counts: KrediSummary,
                 rows: list[NormalizedRecord]) -> None:
    """Saved filter / hidden rows and (revised layout) the SUBTOTAL view. Hidden rows are never skipped."""
    hidden = probe.hidden_rows
    counts.hidden_installment_rows = sum(1 for r in rows if r.row in hidden)
    counts.visible_installment_rows = len(rows) - counts.hidden_installment_rows
    counts.saved_filter = probe.auto_filter_ref is not None
    header = probe.grid.get(probe.header_row or 1, [])
    counts.filter_columns = [label_text(header[c]) or f"#{c}" for c in probe.filter_columns if c < len(header)]
    if counts.saved_filter or hidden:
        report.add(Severity.INFO, "saved_filter_view",
                   f"The workbook carries a saved filter view; {counts.hidden_installment_rows} installment row(s) "
                   "are hidden in it. Every row is imported and validated; the filter is not a payment status.",
                   count=counts.hidden_installment_rows)
    if probe.layout != LAYOUT_TAKIP:
        return
    counts.subtotal_row = True
    counts.subtotal_checks, counts.summary_row_checks = _check_summary_rows(probe)
    bad = counts.subtotal_checks.get("mismatched", 0) + counts.summary_row_checks.get("mismatched", 0)
    unknown = (counts.subtotal_checks.get("missing_cache", 0) + counts.summary_row_checks.get("missing_cache", 0)
               + counts.subtotal_checks.get("unsupported", 0) + counts.summary_row_checks.get("unsupported", 0))
    counts.filtered_totals_match = False if bad else (None if unknown else True)
    if bad:
        report.add(Severity.WARNING, "filtered_subtotal_mismatch",
                   f"{bad} SUBTOTAL / summary cell(s) disagree with the visible rows (the saved view is stale or "
                   "edited). The full-source import is unaffected; the filtered totals are contradictory.", count=bad)
    if unknown:
        report.add(Severity.WARNING, "filtered_subtotal_unverified",
                   f"{unknown} SUBTOTAL / summary cell(s) have no saved result or an unsupported function.",
                   count=unknown)


def _exceptions(report: ValidationReport, counts: KrediSummary, installments: list[LoanInstallment]) -> None:
    """Source-recorded exceptions: reported, never zeroed, never repaired, never read as a payment status."""
    money = ("installment_total", "principal", "interest", "bsmv", "kkdf", "paid_amount")
    neg_rem = sum(1 for i in installments if isinstance(i.remaining_amount.value, (int, float))
                  and i.remaining_amount.value < 0)
    neg_other = sum(1 for i in installments if any(isinstance(getattr(i, f).value, (int, float))
                                                   and getattr(i, f).value < 0 for f in money))
    closed_pos = sum(1 for i in installments if i.status is LoanStatus.CLOSED
                     and isinstance(i.remaining_amount.value, (int, float)) and i.remaining_amount.value > 0)
    counts.negative_remaining_rows, counts.negative_amount_rows = neg_rem, neg_other
    counts.closed_with_positive_remaining = closed_pos
    if neg_rem:
        report.add(Severity.WARNING, "negative_remaining_amount",
                   f"{neg_rem} installment(s) record a negative remaining amount (kept as recorded, never zeroed).",
                   field="remaining_amount", count=neg_rem)
    if neg_other:
        report.add(Severity.WARNING, "negative_amount", f"{neg_other} installment(s) carry a negative money value.",
                   count=neg_other)
    if closed_pos:
        report.add(Severity.INFO, "status_closed_with_remaining",
                   f"{closed_pos} installment(s) are 'Kapalı' but record a positive remaining amount; DURUM is not "
                   "a payment status (unverified).", field="status", count=closed_pos)


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


def _build(rows: list[NormalizedRecord], hidden: frozenset[int] = frozenset()
           ) -> tuple[list[LoanContract], list[LoanInstallment]]:
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
            source_row=r.row, hidden_in_source=r.row in hidden)
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
           "import_kredi", "find_kredi_candidates", "required_headers", "probe_layout", "LayoutProbe", "LAYOUT_ERP",
           "LAYOUT_TAKIP", "AMOUNT_TOLERANCE", "SUPPORTED_LOAN_TYPES"]
