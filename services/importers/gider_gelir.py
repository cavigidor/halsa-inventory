"""M6-6 — Gider/Gelir importer (owner / Yönetim). Docs: docs/m6_gider_gelir.md; decision D-040.

Source module on the M6-1 framework: three explicit specs (one table, two matrices) read by `run_import`,
orchestrated by `import_gider_gelir`. No reader of its own; nothing recalculated; owner audience only.

Verified layout (`<YYYY>_gider_gelir_<label>.xlsx`, the 2026 template):
- `<Y> GİDERLER TABLOSU` (table, header row 1): one row per expense (cost center × expense name), rows until the
  first row without a cost center; that row is the column-totals control row; anything after it is a trailing
  note (not imported). Monthly totals + four native-line allocations per month; allocation ratios per line.
  The monthly headers carry the template's previous year (`2025 Ocak` in the 2026 file): only the MONTH is used.
- `<Y> GELİRLER TABLOSU ` (matrix, note the trailing space in the sheet name): four native-line blocks plus a
  TOPLAM block, each with Satış / Satış maliyeti / Brüt Kar by month and a ratio row (excluded; its
  `#DIV/0!` in empty months is expected and isolated).
- `ÖZET <Y>` (matrix): normalized only where it holds facts no other sheet has — historical yearly totals,
  the source-reported budget column, and the accounting-entry lines. Its report-year P&L columns reference
  GELİRLER and its expense line equals the GİDERLER totals: those are verification only (never imported twice).
- `Sheet1`: a pasted pivot copy of the ÖZET expense pivot — presentation only, not read.

Native business lines KIRTASIYE / TRODAT / TROTEC / IMALAT are kept separate; no management mapping; accounting
basis (cash vs accrual) unverified; monetary values `unverified`; safe summaries carry counts only.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from openpyxl.utils.cell import column_index_from_string, get_column_letter
from pydantic import BaseModel, ConfigDict, Field

from .base import ImportResult, run_import
from .keys import record_key
from .models import Audience, Confidence, NormalizedRecord, NormalizedValue, Severity, ValidationReport, VerificationResult
from .spec import Anchor, FieldSpec, FieldType, MatrixLayout, MatrixRange, SourceSpec, TableLayout, VerifySpec

GIDER_GELIR_FILENAME = re.compile(r"^(?P<year>\d{4})_gider_gelir_(?P<label>[^/\\]+?)\s*\.xlsx$", re.IGNORECASE)
TOL = 0.01                    # monetary tolerance, absolute
RATIO_SUM_TOL = 0.001         # ratios are stored as rounded percentages
#: Verified templates: report year → the (wrong) year the expense-sheet monthly headers carry.
SUPPORTED_TEMPLATES = {2026: 2025}


class NativeLine(str, Enum):
    """Gider/Gelir's own business lines. Never mapped to management categories here."""

    KIRTASIYE = "kirtasiye"
    TRODAT = "trodat"
    TROTEC = "trotec"
    IMALAT = "imalat"


LINES = (NativeLine.KIRTASIYE, NativeLine.TRODAT, NativeLine.TROTEC, NativeLine.IMALAT)
MONTHS_TR_TITLE = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
                   "Aralık")
#: ÖZET spells two months without Turkish letters (`NISAN`, `AGUSTOS`); encoded exactly as the source.
MONTHS_OZET = ("OCAK", "ŞUBAT", "MART", "NISAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AGUSTOS", "EYLÜL", "EKİM", "KASIM",
               "ARALIK")
OZET_LINE_HEADERS = ("KIRTASIYE", "TRODAT", "TROTEC", "IMALAT")


def _cols(start: str, n: int, step: int = 1) -> list[str]:
    s = column_index_from_string(start)
    return [get_column_letter(s + i * step) for i in range(n)]


# ---------------------------------------------------------------------------------------------
# GİDERLER (table)
# ---------------------------------------------------------------------------------------------
#: Exact monthly headers of the 2026 template (monthly total + four allocations per month), verbatim,
#: including the carried-over year, spaces and newlines.
_EXPENSE_MONTH_HEADERS = (
    ("{t}\n Ocak", "Kırtasiye Ocak  ", "Trodat Ocak  ", "Trotec Ocak  ", "İmalat Ocak  "),
    ("{t}\nŞubat", "Kırtasiye Şubat", "Trodat Şubat", "Trotec Şubat  ", "İmalat Şubat  "),
    ("{t}\nMart", "Kırtasiye Mart  ", "Trodat Mart", "Trotec Mart", "İmalat Mart"),
    ("{t}\nNisan", "Kırtasiye Nisan ", "Trodat Nisan  ", "Trotec Nisan  ", "İmalat Nisan  "),
    ("{t} \nMayıs", "Kırtasiye Mayıs", "Trodat Mayıs", "Trotec Mayıs ", "İmalat Mayıs"),
    ("{t} \nHaziran", "Kırtasiye\nHaziran", "Trodat\nHaziran", "Trotec\nHaziran", "İmalat\nHaziran"),
    ("{t} \nTemmuz", "Kırtasiye Temmuz", "Trodat Temmuz", "Trotec Temmuz", "İmalat Temmuz"),
    ("{t} \nAğustos", "Kırtasiye Ağustos", "Trodat Ağustos", "Trotec Ağustos", "İmalat Ağustos"),
    ("{t} \nEylül", "Kırtasiye Eylül", "Trodat Eylül", "Trotec Eylül", "İmalat Eylül"),
    ("{t} \nEkim", "Kırtasiye Ekim", "Trodat Ekim", "Trotec Ekim", "İmalat Ekim"),
    ("{t} \nKasım", "Kırtasiye Kasım", "Trodat Kasım", "Trotec Kasım ", "İmalat Kasım"),
    ("{t} \nAralık", "Kırtasiye Aralık ", "Trodat Aralık ", "Trotec Aralık ", "İmalat Aralık"),
)
RATIO_HEADERS = ("Orantı/ kırtasiye", "Orantı/ Trodat", "Orantı/ Trotec", "Orantı/ İmalat")
ANNUAL_ALLOCATION_HEADERS = ("Kırtasiye", "Trodat", "Trotec", "İmalat")


def _historical_expense_headers(year: int) -> list[tuple[int, str]]:
    # 2026 template: Y-1 / Y-2 "TOPLAM", Y-3 / Y-4 "GERÇEK" with a newline, Y-5 "GERÇEK" with a space.
    return [(year - 1, f"{year - 1}\nTOPLAM"), (year - 2, f"{year - 2}\nTOPLAM"), (year - 3, f"{year - 3}\nGERÇEK"),
            (year - 4, f"{year - 4}\nGERÇEK"), (year - 5, f"{year - 5} GERÇEK")]


def _mf(name: str, header: str, conf: Confidence = Confidence.UNVERIFIED, **kw) -> FieldSpec:
    return FieldSpec(name, FieldType.DECIMAL, header=header, confidence=conf, **kw)


def expense_spec(year: int) -> SourceSpec:
    t = SUPPORTED_TEMPLATES[year]
    fields = [
        FieldSpec("cost_center", FieldType.STRING, header="GIDER YERİ", confidence=Confidence.TRUSTED),
        FieldSpec("expense_name", FieldType.STRING, header="MASRAF İSMİ", confidence=Confidence.TRUSTED),
        _mf("budget", f"{year}\nBÜTÇE"),
        _mf("report_year_total", f"{year}\nTOPLAM"),
        *[_mf(f"historical_{y}", h) for y, h in _historical_expense_headers(year)],
        *[_mf(f"ratio_{ln.value}", h) for ln, h in zip(LINES, RATIO_HEADERS)],
        _mf("ratio_total", "TOPLAM"),
        *[_mf(f"annual_{ln.value}", h) for ln, h in zip(LINES, ANNUAL_ALLOCATION_HEADERS)],
    ]
    for m, hs in enumerate(_EXPENSE_MONTH_HEADERS, start=1):
        fields.append(_mf(f"m{m}_total", hs[0].format(t=t)))
        fields += [_mf(f"m{m}_{ln.value}", h) for ln, h in zip(LINES, hs[1:])]
    return SourceSpec(
        key="gider_gelir_expenses", audience=Audience.OWNER, filename_patterns=("*gider_gelir*.xlsx",),
        sheet=f"{year} GİDERLER TABLOSU", layout=TableLayout(header_row=1), fields=tuple(fields),
        default_confidence=Confidence.UNVERIFIED, external_links=Severity.ERROR, verify=VerifySpec(min_rows=1),
        description="Gider/Gelir expense table (owner only).")


# ---------------------------------------------------------------------------------------------
# GELİRLER (matrix)
# ---------------------------------------------------------------------------------------------
GELIR_BLOCKS = (  # (title row, exact title, native line or None for the TOPLAM block)
    (1, "Kırtasiye", NativeLine.KIRTASIYE), (8, "Trodat", NativeLine.TRODAT), (15, "Trotec", NativeLine.TROTEC),
    (22, "imalat", NativeLine.IMALAT), (29, "TOPLAM", None))
GELIR_METRICS = (("Satış", "reported_sales"), ("Satış maliyeti", "reported_cost"), ("Brüt Kar", "reported_gross_profit"))
GELIR_HEADER = (*MONTHS_TR_TITLE, "TOTAL")


def revenue_spec(year: int) -> SourceSpec:
    anchors, ranges = [], []
    for title_row, title, _ in GELIR_BLOCKS:
        b = f"b{title_row}"
        anchors.append(Anchor(f"{b}_title", title, expected=f"B{title_row}", search=f"B{title_row}:B{title_row + 1}"))
        for i, (label, _) in enumerate(GELIR_METRICS):
            r = title_row + 2 + i
            anchors.append(Anchor(f"{b}_{i}", label, expected=f"A{r}", search=f"A{title_row + 2}:A{title_row + 4}"))
        ranges.append(MatrixRange(f"{b}_header", f"B{title_row + 1}:N{title_row + 1}", FieldType.STRING))
        ranges.append(MatrixRange(f"{b}_values", f"B{title_row + 2}:N{title_row + 4}", FieldType.DECIMAL))
        ranges.append(MatrixRange(f"{b}_ratio", f"B{title_row + 5}:N{title_row + 5}", FieldType.DECIMAL, required=False,
                                  tolerate_invalid_cells=True, confidence=Confidence.UNVERIFIED))
    return SourceSpec(
        key="gider_gelir_revenue", audience=Audience.OWNER, filename_patterns=("*gider_gelir*.xlsx",),
        sheet=f"{year} GELİRLER TABLOSU ", layout=MatrixLayout(bounds="A1:N35", anchors=tuple(anchors),
                                                                ranges=tuple(ranges)),
        fields=(), default_confidence=Confidence.UNVERIFIED, external_links=Severity.ERROR,
        description="Gider/Gelir revenue blocks by native line (owner only).")


# ---------------------------------------------------------------------------------------------
# ÖZET (matrix)
# ---------------------------------------------------------------------------------------------
#: Rows of ÖZET that are normalized (label at A, exact) and what is taken from each.
OZET_PL_ROWS = {3: "Satış", 4: "Satış maliyeti", 5: "Brüt Kar", 6: "gider", 7: "NET KAR", 21: "KAR-ZARAR"}
OZET_ENTRY_ROWS = {10: "Kur farkı geliri", 11: "Kur farkı gideri", 12: "Amortisman", 13: "KK Gider",
                   14: "Reeskont Gideri", 15: "Reeskont Geliri"}
OZET_UNLABELLED_ROWS = (16, 17)    # value rows without a label: counted, never normalized


def ozet_headers(year: int, with_total: bool) -> tuple[str, ...]:
    hist = tuple(f"TOPLAM {y}" for y in range(year - 5, year))
    return (*hist, f"Bütçe {year}", f"TOPLAM {year}", *OZET_LINE_HEADERS, *MONTHS_OZET) + (("TOPLAM",) if with_total else ())


def ozet_spec(year: int) -> SourceSpec:
    anchors = [Anchor(f"pl_{r}", lab, expected=f"A{r}", search=f"A{r}:A{r}") for r, lab in OZET_PL_ROWS.items()]
    anchors += [Anchor(f"entry_{r}", lab, expected=f"A{r}", search=f"A{r}:A{r}") for r, lab in OZET_ENTRY_ROWS.items()]
    anchors += [Anchor("entries_title", "Muhasebe kayıtları", expected="A8", search="A8:A8"),
                Anchor("pivot_title", "Row Labels", expected="A24", search="A24:A24"),
                Anchor("pivot_total", "Grand Total", expected="A51", search="A51:A51")]
    ranges = (
        MatrixRange("header_pl", "B2:X2", FieldType.STRING), MatrixRange("header_entries", "B9:Y9", FieldType.STRING),
        MatrixRange("header_pivot", "B24:X24", FieldType.STRING),
        MatrixRange("ratio_row", "B1:Y1", FieldType.DECIMAL, required=False, tolerate_invalid_cells=True),
        MatrixRange("pl", "B3:Y7", FieldType.DECIMAL),
        MatrixRange("entries", "B10:Y18", FieldType.DECIMAL),
        MatrixRange("kar_zarar", "B21:Y21", FieldType.DECIMAL),
        MatrixRange("pivot_labels", "A25:A50", FieldType.STRING),
        MatrixRange("pivot_values", "B25:X51", FieldType.DECIMAL),
    )
    return SourceSpec(
        key="gider_gelir_summary", audience=Audience.OWNER, filename_patterns=("*gider_gelir*.xlsx",),
        sheet=f"ÖZET {year}", layout=MatrixLayout(bounds="A1:Y51", anchors=tuple(anchors), ranges=ranges),
        fields=(), default_confidence=Confidence.UNVERIFIED, external_links=Severity.ERROR,
        description="Gider/Gelir summary (owner only).")


# ---------------------------------------------------------------------------------------------
# Models (in memory)
# ---------------------------------------------------------------------------------------------
@dataclass(slots=True)
class ExpenseLine:
    expense_key: str                       # record_key("gider_expense", [cost center, expense name])
    cost_center: str                       # exact (e.g. a 2-digit code + name); internal only
    expense_name: str                      # internal only
    ratios: dict[NativeLine, NormalizedValue]
    source_reported_budget: NormalizedValue
    historical: dict[int, NormalizedValue]          # year → reported yearly total
    annual_total_matches_months: bool
    source_row: int
    management_category: str = "not_mapped"


@dataclass(slots=True)
class ExpenseMonth:
    record_key: str                        # record_key("gider_expense_month", [expense key, year, month])
    expense_key: str
    year: int
    month: int
    reported_expense: NormalizedValue
    allocations: dict[NativeLine, NormalizedValue]   # workbook cached values (never recomputed)
    allocations_match_ratios: bool


@dataclass(slots=True)
class RevenueRecord:
    record_key: str                        # record_key("gider_gelir_revenue", [line, metric, year, month])
    native_line: NativeLine
    metric: str                            # reported_sales | reported_cost | reported_gross_profit
    year: int
    month: int
    value: NormalizedValue
    management_category: str = "not_mapped"


class PeriodKind(str, Enum):
    HISTORICAL_YEAR_TOTAL = "historical_year_total"
    SOURCE_REPORTED_BUDGET = "source_reported_budget"
    REPORT_YEAR_TOTAL = "report_year_total"
    BUSINESS_LINE_TOTAL = "business_line_total"
    MONTH = "month"


@dataclass(slots=True)
class SummaryRecord:
    record_key: str                        # record_key("gider_gelir_summary", [line label, kind, year, month, line])
    line_key: str                          # record_key("gider_gelir_summary_line", [exact label])
    line_label: str                        # internal only
    period_kind: PeriodKind
    year: int
    month: int | None
    business_line: NativeLine | None
    value: NormalizedValue


class GiderGelirSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_year: int | None = None
    report_year_origin: str | None = None
    revision_label: str | None = None
    snapshot_date: dt.date | None = None
    sheets_validated: list[str] = Field(default_factory=list)
    sheets_excluded: list[str] = Field(default_factory=list)
    summary_records: int = 0
    summary_lines: int = 0
    expense_rows: int = 0
    expense_month_records: int = 0
    cost_centers: int = 0
    revenue_records: int = 0
    native_lines: list[str] = Field(default_factory=list)
    historical_years: list[int] = Field(default_factory=list)
    months_with_expenses: list[int] = Field(default_factory=list)
    months_with_revenue: list[int] = Field(default_factory=list)
    formula_cells: int = 0
    excel_errors: dict[str, int] = Field(default_factory=dict)
    ratio_rows_checked: int = 0
    ratio_rows_failed: int = 0
    ratios_outside_unit_interval: int = 0
    allocation_cells_checked: int = 0
    allocation_cells_not_matching_ratio: int = 0
    annual_totals_not_matching_months: int = 0
    gross_profit_not_sales_minus_cost: int = 0
    cross_sheet_checks: dict[str, dict[str, int]] = Field(default_factory=dict)
    template_header_year: int | None = None
    unlabelled_value_cells: int = 0
    trailing_rows: int = 0
    external_links: int = 0
    management_mapping: str = "not_supplied"
    accounting_basis: str = "unverified"


@dataclass
class GiderGelirResult:
    ok: bool
    failed_step: str | None
    file: str
    expenses: list[ExpenseLine]
    expense_months: list[ExpenseMonth]
    revenue: list[RevenueRecord]
    summary_records: list[SummaryRecord]
    report: ValidationReport           # composite report (component issues + source checks)
    verification: VerificationResult
    counts: GiderGelirSummary
    components: dict[str, ImportResult]

    @property
    def audience(self) -> Audience:
        return Audience.OWNER

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failed_step": self.failed_step,
            "source": "gider_gelir",
            "audience": self.audience.value,
            "file": self.file,
            "counts": self.counts.model_dump(mode="json"),
            "report": self.report.to_dict(),
            "components": {k: {"ok": v.ok, "failed_step": v.failed_step, "report": v.report.to_dict(),
                               "verification": v.verification.model_dump(mode="json")} for k, v in self.components.items()},
            "verification": self.verification.model_dump(mode="json"),
        }


# ---------------------------------------------------------------------------------------------
# Year
# ---------------------------------------------------------------------------------------------
def parse_filename(name: str) -> tuple[int, str] | None:
    m = GIDER_GELIR_FILENAME.match(unicodedata.normalize("NFC", name))
    return (int(m.group("year")), m.group("label")) if m else None


def find_gider_gelir_candidates(folder: str | Path) -> list[dict[str, Any]]:
    """List Gider/Gelir-looking workbooks with the year/label in their name and whether that year's template is
    supported. Never selects one."""
    out = []
    for p in sorted(Path(folder).glob("*.xlsx")):
        if p.name.startswith("~$") or "gider_gelir" not in unicodedata.normalize("NFC", p.name).lower():
            continue
        parsed = parse_filename(p.name)
        out.append({"file": p.name, "report_year": parsed[0] if parsed else None,
                    "revision_label": parsed[1] if parsed else None,
                    "template_supported": bool(parsed and parsed[0] in SUPPORTED_TEMPLATES)})
    return out


# ---------------------------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------------------------
def import_gider_gelir(path: str | Path, *, report_year: int | None = None) -> GiderGelirResult:
    """Import one explicitly chosen Gider/Gelir workbook. Fails as a whole if any required component fails."""
    path = Path(path)
    report = ValidationReport(source="gider_gelir", audience=Audience.OWNER, file=path.name, layout="workbook")
    counts = GiderGelirSummary(sheets_excluded=["Sheet1"])
    result = GiderGelirResult(False, "year", path.name, [], [], [], [], report, VerificationResult(), counts, {})
    parsed = parse_filename(path.name)
    if parsed:
        counts.revision_label = parsed[1]
    if report_year is not None and parsed and parsed[0] != report_year:
        report.add(Severity.ERROR, "report_year_conflict", "The declared report year differs from the filename year.")
        return result
    year = report_year if report_year is not None else (parsed[0] if parsed else None)
    if year is None:
        report.add(Severity.ERROR, "report_year_unknown", "No declared report year and no approved filename pattern.")
        return result
    counts.report_year, counts.report_year_origin = year, ("declared" if report_year is not None else "filename")
    if year not in SUPPORTED_TEMPLATES:
        report.add(Severity.ERROR, "unsupported_template", "No verified Gider/Gelir template for this report year.")
        return result
    counts.template_header_year = SUPPORTED_TEMPLATES[year]

    comps = {"summary": run_import(path, ozet_spec(year)), "expenses": run_import(path, expense_spec(year)),
             "revenue": run_import(path, revenue_spec(year))}
    result.components = comps
    counts.external_links = max(c.report.external_links.count for c in comps.values())
    counts.formula_cells = sum(f.formula_count for c in comps.values() for f in c.report.fields)
    for name, c in comps.items():
        for f in c.report.fields:
            for code, k in f.errors_by_type.items():
                counts.excel_errors[f"{name}.{f.field}.{code}"] = counts.excel_errors.get(f"{name}.{f.field}.{code}", 0) + k
    failed = [n for n, c in comps.items() if not c.ok]
    if failed:
        report.add(Severity.ERROR, "component_failed", "Required component(s) failed: " + ", ".join(failed) + ".",
                   count=len(failed))
        result.failed_step = "components"
        return result
    counts.sheets_validated = [f"ÖZET {year}", f"{year} GİDERLER TABLOSU", f"{year} GELİRLER TABLOSU "]

    ozet = _grid(comps["summary"].records)
    gelir = _grid(comps["revenue"].records)
    _check_headers(ozet, gelir, year, report)
    expenses, months = _expenses(comps["expenses"].records, year, report, counts)
    revenue = _revenue(gelir, year, report, counts)
    summary = _summary(ozet, year, report, counts)
    if report.errors:
        result.failed_step = "source_checks"
        return result
    _cross_sheet(ozet, gelir, months, expenses, comps["expenses"].records, report, counts)
    if report.errors:
        result.failed_step = "cross_sheet"
        return result

    counts.summary_records, counts.summary_lines = len(summary), len({s.line_key for s in summary})
    counts.expense_rows, counts.expense_month_records = len(expenses), len(months)
    counts.cost_centers = len({e.cost_center for e in expenses})
    counts.revenue_records = len(revenue)
    counts.native_lines = [ln.value for ln in LINES]
    counts.historical_years = sorted({s.year for s in summary if s.period_kind is PeriodKind.HISTORICAL_YEAR_TOTAL})
    counts.months_with_expenses = sorted({m.month for m in months if m.reported_expense.value not in (None, 0)})
    counts.months_with_revenue = sorted({r.month for r in revenue if r.value.value not in (None, 0)})
    result.expenses, result.expense_months, result.revenue, result.summary_records = expenses, months, revenue, summary
    _verify(result)
    if not result.verification.ok:
        bad = [c.name for c in result.verification.checks if not c.ok]
        report.add(Severity.ERROR, "gider_gelir_verification_failed", "Verification failed: " + ", ".join(bad) + ".",
                   count=len(bad))
        result.failed_step = "verify"
        result.expenses, result.expense_months, result.revenue, result.summary_records = [], [], [], []
        return result
    result.ok, result.failed_step = True, None
    return result


def _grid(records: list[NormalizedRecord]) -> dict[str, NormalizedValue]:
    """coordinate → value (all matrix ranges of one sheet)."""
    return {r.get("column_label"): r.values["value"] for r in records if "block" in r.values}


def _n(nv: NormalizedValue | None) -> float:
    return float(nv.value) if nv is not None and isinstance(nv.value, (int, float)) else 0.0


def _close(a: float, b: float, tol: float = TOL) -> bool:
    return abs(round(a - b, 6)) <= tol


def _check_headers(ozet, gelir, year: int, report: ValidationReport) -> None:
    checks = (("B2:X2", ozet_headers(year, False), ozet, 2), ("B9:Y9", ozet_headers(year, True), ozet, 9),
              ("B24:X24", ozet_headers(year, False), ozet, 24))
    for region, expected, grid, row in checks:
        cols = _cols("B", len(expected))
        bad = sum(1 for c, e in zip(cols, expected) if (grid.get(f"{c}{row}") or NormalizedValue(None, Confidence.INVALID,
                                                                                              None)).value != e)
        if bad:
            report.add(Severity.ERROR, "header_mismatch", f"{bad} ÖZET header cell(s) differ from the template.",
                       region=region, count=bad)
    for title_row, _, _ in GELIR_BLOCKS:
        row = title_row + 1
        bad = sum(1 for c, e in zip(_cols("B", 13), GELIR_HEADER) if (gelir.get(f"{c}{row}") or
                                                                       NormalizedValue(None, Confidence.INVALID, None)).value != e)
        if bad:
            report.add(Severity.ERROR, "header_mismatch", f"{bad} GELİRLER header cell(s) differ from the template.",
                       region=f"B{row}:N{row}", count=bad)


def _expenses(records: list[NormalizedRecord], year: int, report: ValidationReport, counts: GiderGelirSummary):
    rows: list[NormalizedRecord] = []
    footer: NormalizedRecord | None = None
    trailing = 0
    for r in records:
        if footer is not None:
            trailing += 1
        elif r.get("cost_center") is None:
            footer = r
        else:
            rows.append(r)
    counts.trailing_rows = trailing
    if footer is None:
        report.add(Severity.ERROR, "expense_totals_row_missing", "The expense table's column-totals row was not found.")
    missing_name = sum(1 for r in rows if r.get("expense_name") is None)
    if missing_name:
        report.add(Severity.ERROR, "expense_name_missing", f"{missing_name} expense row(s) have no expense name.",
                   count=missing_name)
    ids = Counter((r.get("cost_center"), r.get("expense_name")) for r in rows)
    dup = sum(n - 1 for n in ids.values() if n > 1)
    if dup:
        report.add(Severity.ERROR, "duplicate_expense", f"{dup} repeated cost center × expense name pair(s).", count=dup)
    report.add(Severity.WARNING, "template_header_year_mismatch",
               f"The expense sheet's monthly headers carry {SUPPORTED_TEMPLATES[year]} (template carry-over); the "
               f"period year comes from the report-year rule ({year}); only the month is read from them.")

    expenses, months = [], []
    ratio_bad = blank = outside = alloc_bad = alloc_checked = annual_bad = annual_alloc_bad = 0
    for r in rows:
        v = r.values
        ratios = {ln: v[f"ratio_{ln.value}"] for ln in LINES}
        if any(nv.value is None for nv in ratios.values()):
            blank += 1
        elif not _close(sum(_n(nv) for nv in ratios.values()), 1.0, RATIO_SUM_TOL):
            ratio_bad += 1
        if any(nv.value is not None and not 0 <= nv.value <= 1 for nv in ratios.values()):
            outside += 1
        key = record_key("gider_expense", [r.get("cost_center"), r.get("expense_name")])
        month_sum = 0.0
        for m in range(1, 13):
            total = v[f"m{m}_total"]
            allocs = {ln: v[f"m{m}_{ln.value}"] for ln in LINES}
            matches = True
            for ln in LINES:
                alloc_checked += 1
                if not _close(_n(allocs[ln]), _n(total) * _n(ratios[ln])):
                    alloc_bad += 1
                    matches = False
            month_sum += _n(total)
            months.append(ExpenseMonth(record_key("gider_expense_month", [key, year, m]), key, year, m, total, allocs,
                                       matches))
        annual_ok = _close(_n(v["report_year_total"]), month_sum)
        annual_bad += not annual_ok
        hist = {y: v[f"historical_{y}"] for y, _ in _historical_expense_headers(year) if v[f"historical_{y}"].value is not None}
        expenses.append(ExpenseLine(key, r.get("cost_center"), r.get("expense_name"), ratios, v["budget"], hist,
                                    annual_ok, r.row))
        for ln in LINES:  # annual line allocation = sum of monthly allocations (workbook formulas)
            if not _close(_n(v[f"annual_{ln.value}"]), sum(_n(v[f"m{m}_{ln.value}"]) for m in range(1, 13))):
                annual_alloc_bad += 1
    counts.ratio_rows_checked = len(rows)
    counts.ratio_rows_failed = ratio_bad + blank
    counts.ratios_outside_unit_interval = outside
    counts.allocation_cells_checked, counts.allocation_cells_not_matching_ratio = alloc_checked, alloc_bad
    counts.annual_totals_not_matching_months = annual_bad
    if annual_alloc_bad:
        report.add(Severity.ERROR, "annual_allocation_mismatch",
                   f"{annual_alloc_bad} annual business-line allocation(s) differ from their monthly allocations.",
                   count=annual_alloc_bad)
    if blank:
        report.add(Severity.ERROR, "allocation_ratio_missing", f"{blank} expense row(s) lack an allocation ratio.",
                   count=blank)
    if ratio_bad:
        report.add(Severity.ERROR, "allocation_ratio_sum",
                   f"{ratio_bad} expense row(s) have allocation ratios that do not sum to 1 (±{RATIO_SUM_TOL}).",
                   count=ratio_bad)
    if outside:
        report.add(Severity.WARNING, "allocation_ratio_outside_unit_interval",
                   f"{outside} expense row(s) use a ratio outside 0–1 (a reallocation in the source); kept as recorded.",
                   count=outside)
    if alloc_bad:
        report.add(Severity.WARNING, "allocation_not_total_times_ratio",
                   f"{alloc_bad} monthly allocation cell(s) differ from total × ratio (manual entries in the source); "
                   "kept as recorded and flagged.", count=alloc_bad)
    if annual_bad:
        report.add(Severity.WARNING, "annual_total_not_sum_of_months",
                   f"{annual_bad} expense row(s) have a {year} total that differs from their monthly totals.",
                   count=annual_bad)
    if footer is not None:
        bad_cols = sum(1 for m in range(1, 13)
                       if not _close(_n(footer.values[f"m{m}_total"]), sum(_n(r.values[f"m{m}_total"]) for r in rows)))
        if not _close(_n(footer.values["report_year_total"]), sum(_n(r.values["report_year_total"]) for r in rows)):
            bad_cols += 1
        if bad_cols:
            report.add(Severity.ERROR, "expense_totals_row_mismatch",
                       f"{bad_cols} column total(s) of the expense table differ from the expense rows.", count=bad_cols)
    if trailing:
        report.add(Severity.INFO, "expense_trailing_rows",
                   f"{trailing} row(s) after the column-totals row are notes and are not imported.", count=trailing)
    return expenses, months


def _revenue(gelir, year: int, report: ValidationReport, counts: GiderGelirSummary) -> list[RevenueRecord]:
    out: list[RevenueRecord] = []
    month_cols = _cols("B", 12)
    gp_diff = row_bad = block_bad = 0
    for title_row, _, line in GELIR_BLOCKS:
        base = title_row + 2
        for i, (_, metric) in enumerate(GELIR_METRICS):
            r = base + i
            vals = [gelir[f"{c}{r}"] for c in month_cols]
            if not _close(_n(gelir[f"N{r}"]), sum(_n(nv) for nv in vals)):
                row_bad += 1
            if line is None:
                continue
            for m, nv in enumerate(vals, start=1):
                if nv.value is None and nv.error is None:
                    continue
                out.append(RevenueRecord(record_key("gider_gelir_revenue", [line.value, metric, year, m]), line, metric,
                                         year, m, nv))
        if line is not None:
            gp_diff += sum(1 for c in month_cols if gelir[f"{c}{base + 2}"].value is not None and not _close(
                _n(gelir[f"{c}{base + 2}"]), _n(gelir[f"{c}{base}"]) - _n(gelir[f"{c}{base + 1}"])))
    for i in range(3):   # TOPLAM block = sum of the four native-line blocks
        for c in [*month_cols, "N"]:
            if not _close(_n(gelir[f"{c}{31 + i}"]), sum(_n(gelir[f"{c}{3 + 7 * k + i}"]) for k in range(4))):
                block_bad += 1
    counts.gross_profit_not_sales_minus_cost = gp_diff
    if row_bad:
        report.add(Severity.ERROR, "revenue_row_total_mismatch", f"{row_bad} revenue row total(s) differ from their months.",
                   count=row_bad)
    if block_bad:
        report.add(Severity.ERROR, "revenue_block_total_mismatch",
                   f"{block_bad} TOPLAM-block cell(s) differ from the sum of the four native lines.", count=block_bad)
    if gp_diff:
        report.add(Severity.INFO, "gross_profit_not_sales_minus_cost",
                   f"{gp_diff} month(s) report a gross profit that is not sales − cost; the source value is kept "
                   "(no identity is enforced).", count=gp_diff)
    ratio_errors = sum(1 for title_row, _, _ in GELIR_BLOCKS for c in _cols("B", 13)
                       if gelir[f"{c}{title_row + 5}"].error is not None)
    if ratio_errors:
        report.add(Severity.INFO, "known_ratio_errors",
                   f"{ratio_errors} margin-ratio cell(s) are Excel errors (empty months); ratio rows are not imported.",
                   count=ratio_errors)
    return out


def _summary(ozet, year: int, report: ValidationReport, counts: GiderGelirSummary) -> list[SummaryRecord]:
    out: list[SummaryRecord] = []
    hist_cols = list(zip(_cols("B", 5), range(year - 5, year)))
    line_cols = list(zip(_cols("I", 4), LINES))
    month_cols = list(zip(_cols("M", 12), range(1, 13)))

    def add(label: str, kind: PeriodKind, y: int, month: int | None, line: NativeLine | None, coord: str) -> None:
        nv = ozet[coord]
        if nv.value is None and nv.error is None:
            return
        lk = record_key("gider_gelir_summary_line", [label])
        out.append(SummaryRecord(record_key("gider_gelir_summary", [label, kind.value, y, month,
                                                                    line.value if line else None]),
                                 lk, label, kind, y, month, line, nv))

    for row, label in {**OZET_PL_ROWS, **OZET_ENTRY_ROWS}.items():
        for col, y in hist_cols:
            add(label, PeriodKind.HISTORICAL_YEAR_TOTAL, y, None, None, f"{col}{row}")
        add(label, PeriodKind.SOURCE_REPORTED_BUDGET, year, None, None, f"G{row}")
    for row, label in OZET_ENTRY_ROWS.items():   # report-year facts that exist only in ÖZET
        add(label, PeriodKind.REPORT_YEAR_TOTAL, year, None, None, f"H{row}")
        for col, line in line_cols:
            add(label, PeriodKind.BUSINESS_LINE_TOTAL, year, None, line, f"{col}{row}")
        for col, m in month_cols:
            add(label, PeriodKind.MONTH, year, m, None, f"{col}{row}")
    counts.unlabelled_value_cells = sum(1 for row in OZET_UNLABELLED_ROWS for c in _cols("B", 24)
                                        if ozet[f"{c}{row}"].value not in (None, 0))
    if counts.unlabelled_value_cells:
        report.add(Severity.INFO, "unlabelled_value_rows",
                   f"{counts.unlabelled_value_cells} value cell(s) in unlabelled ÖZET rows are not normalized (they "
                   "count in the workbook's own subtotal).", count=counts.unlabelled_value_cells)
    ratio_errors = sum(1 for c in _cols("B", 24) if ozet[f"{c}1"].error is not None)
    if ratio_errors:
        report.add(Severity.INFO, "known_ratio_errors",
                   f"{ratio_errors} ÖZET margin-ratio cell(s) are Excel errors (empty months); the row is not imported.",
                   count=ratio_errors)
    bad = 0   # report-year identities of the summary (workbook formulas)
    for col, _ in month_cols:
        g = lambda r: _n(ozet[f"{col}{r}"])
        bad += not _close(g(7), g(5) - g(6))
        bad += not _close(g(18), sum(g(r) for r in range(10, 18)))
        bad += not _close(g(21), g(7) + g(18))
    if bad:
        report.add(Severity.ERROR, "summary_identity_mismatch",
                   f"{bad} ÖZET monthly identity check(s) failed (net = gross − expense; subtotal; result).", count=bad)
    return out


def _cross_sheet(ozet, gelir, months: list[ExpenseMonth], expenses: list[ExpenseLine], exp_records, report,
                 counts: GiderGelirSummary) -> None:
    month_cols = _cols("M", 12)
    # 1) ÖZET report-year P&L = GELİRLER (formula references) — fatal on mismatch.
    checked = bad = 0
    for oz_row, gl_row in ((3, 31), (4, 32), (5, 33)):
        for i, col in enumerate(month_cols):
            checked += 1
            bad += not _close(_n(ozet[f"{col}{oz_row}"]), _n(gelir[f"{_cols('B', 12)[i]}{gl_row}"]))
        for k, col in enumerate(_cols("I", 4)):
            checked += 1
            bad += not _close(_n(ozet[f"{col}{oz_row}"]), _n(gelir[f"N{3 + 7 * k + (oz_row - 3)}"]))
        checked += 1
        bad += not _close(_n(ozet[f"H{oz_row}"]), _n(gelir[f"N{gl_row}"]))
    counts.cross_sheet_checks["summary_vs_revenue"] = {"checked": checked, "failed": bad}
    if bad:
        report.add(Severity.ERROR, "summary_vs_revenue_mismatch",
                   f"{bad} ÖZET P&L cell(s) differ from the GELİRLER values they reference.", count=bad)
    # 2) ÖZET expense line (a pasted pivot) = GİDERLER totals — warning on mismatch (pivot can lag).
    checked = bad = 0
    by_month = defaultdict(float)
    for m in months:
        by_month[m.month] += _n(m.reported_expense)
    for i, col in enumerate(month_cols, start=1):
        checked += 1
        bad += not _close(_n(ozet[f"{col}6"]), by_month[i], 0.5)
    rows = [r for r in exp_records if r.get("cost_center") is not None]
    checked += 1
    bad += not _close(_n(ozet["H6"]), sum(_n(r.values["report_year_total"]) for r in rows), 0.5)
    checked += 1
    bad += not _close(_n(ozet["G6"]), sum(_n(e.source_reported_budget) for e in expenses), 0.5)
    for k, ln in enumerate(LINES):
        checked += 1
        bad += not _close(_n(ozet[f"{_cols('I', 4)[k]}6"]), sum(_n(m.allocations[ln]) for m in months), 0.5)
    counts.cross_sheet_checks["summary_expense_vs_expense_table"] = {"checked": checked, "failed": bad}
    if bad:
        report.add(Severity.WARNING, "summary_expense_vs_expense_table",
                   f"{bad} ÖZET expense cell(s) differ from the expense table (the ÖZET pivot is pasted values).",
                   count=bad)


def _verify(result: GiderGelirResult) -> None:
    v = result.verification
    v.add("audience_owner", result.audience is Audience.OWNER and all(
        c.audience is Audience.OWNER for c in result.components.values()))
    v.add("expense_keys_unique", len({e.expense_key for e in result.expenses}) == len(result.expenses))
    v.add("expense_month_keys_unique", len({m.record_key for m in result.expense_months}) == len(result.expense_months))
    v.add("revenue_keys_unique", len({r.record_key for r in result.revenue}) == len(result.revenue))
    v.add("summary_keys_unique", len({s.record_key for s in result.summary_records}) == len(result.summary_records))
    v.add("no_management_mapping", all(e.management_category == "not_mapped" for e in result.expenses)
          and all(r.management_category == "not_mapped" for r in result.revenue))
    money = [m.reported_expense for m in result.expense_months] + [r.value for r in result.revenue] + [
        s.value for s in result.summary_records]
    v.add("money_never_trusted", all(nv.confidence in (Confidence.UNVERIFIED, Confidence.INVALID) for nv in money))
    blob = json.dumps(result.summary(), ensure_ascii=False, default=str)
    labels = {e.cost_center for e in result.expenses} | {e.expense_name for e in result.expenses}
    amounts = {repr(float(nv.value)) for nv in money if isinstance(nv.value, (int, float)) and abs(nv.value) >= 1000}
    leaked = sum(1 for s in labels if len(s) >= 4 and s in blob) + sum(1 for a in amounts if a in blob)
    v.add("summary_has_no_labels_or_amounts", leaked == 0, f"{leaked} leak(s)")


__all__ = ["NativeLine", "PeriodKind", "ExpenseLine", "ExpenseMonth", "RevenueRecord", "SummaryRecord",
           "GiderGelirSummary", "GiderGelirResult", "import_gider_gelir", "find_gider_gelir_candidates",
           "parse_filename", "expense_spec", "revenue_spec", "ozet_spec", "SUPPORTED_TEMPLATES", "LINES"]
