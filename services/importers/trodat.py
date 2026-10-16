"""M6-5 — Trodat sales report importer (native model × variant × month).

Source module on the M6-1 framework: an explicit `MatrixLayout` (anchors + ranges) read by `run_import`;
no reader of its own. Docs: docs/m6_trodat.md; decision D-039.

What it does: normalizes the Trodat model/variant lines of the `Halsa_Turkey` sheet into month records with
a neutral `reported_quantity` (business meaning **unverified**), checks the workbook's own totals, and keeps
Trodat identity separate from Halsa SKUs. Halsa SKU mapping is **not supplied** and never guessed (no fuzzy,
substring, name or number-similarity matching).

Verified layout (Nov and Dec 2025 reports, identical):
- Section 1, rows 2–29 under the month header in row 1: model lines (rows 2–27), an "Other Printy" line
  (28) and a "Professional" line (29); row 30 blank; row 31 "Total" = monthly sums of rows 2–30.
- Section 2, rows 32–39 (no title): model lines, some with display-pack variants (`displayx<N>`); row 40
  "Total" expands display packs by N per month. The workbook's own section-2 controls are not internally
  consistent (some row totals pack-expanded, one not; a monthly formula multiplier that disagrees with its
  pack label), so they are checked and reported as warnings, not used as pass/fail.
- Section 3, title "Daters Numberers" (row 42), own month header (row 43, `December` spelled out), model
  lines 44–45, "Numberers" (46) and "Stamp pads" (47); no total row.
- Row 41 (ratios against a yearly series) and rows 50–65 (yearly "TRODAT SALES" series, a different
  metric) are outside the model × month grain and are not read.
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

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportResult, run_import
from .keys import record_key
from .models import Audience, Confidence, NormalizedRecord, NormalizedValue, Severity, ValidationReport, VerificationResult
from .spec import Anchor, FieldType, MatrixLayout, MatrixRange, SourceSpec

SHEET = "Halsa_Turkey"
TRODAT_FILE_PATTERNS = ("*trodat*.xlsx", "*Trodat*.xlsx", "*TRODAT*.xlsx")
#: Approved filename pattern of the monthly report, e.g. "trodat sales report Halsa NOVEMBER 2025 .xlsx".
#: Only the year is used; the month word is kept as a label and never becomes a snapshot date.
TRODAT_FILENAME = re.compile(r"^trodat sales report halsa (?P<label>[^\W\d_]+) (?P<year>\d{4})\s*\.xlsx$",
                             re.IGNORECASE)
MIN_YEAR, MAX_YEAR = 2000, 2099
TOLERANCE = 0.01
MONTH_COLUMNS = "CDEFGHIJKLMN"   # Jan … Dec
TOTAL_COLUMN = "O"
MONTH_LABELS_SECTION_1 = ("Jan", "Feb", "March", "April", "May", "June", "July", "August", "Sep", "Oct", "Nov",
                          "Dec", "Total")
MONTH_LABELS_SECTION_3 = MONTH_LABELS_SECTION_1[:11] + ("December", "Total")
PACK_VARIANT = re.compile(r"displayx(?P<n>[1-9]\d*)")


class SourceFamily(str, Enum):
    """Families as the workbook labels them. No mapping to Halsa categories."""

    PRINTY = "printy"
    PROFESSIONAL = "professional"
    DATERS_NUMBERERS = "daters_numberers"   # the section-3 title, as written
    NUMBERERS = "numberers"
    STAMP_PADS = "stamp_pads"


@dataclass(frozen=True)
class _Section:
    name: str
    first_row: int
    last_row: int
    header_range: str
    total_row: int | None
    family_lines: dict[int, tuple[str, SourceFamily]]
    model_family: SourceFamily | None
    model_family_confidence: Confidence | None


SECTIONS = (
    # Section 1's model rows carry no family label; "printy" is inferred from the section's "Other Printy"
    # residual line, so its confidence is unverified.
    _Section("section_1", 2, 29, "C1:O1", 31, {28: ("Other Printy", SourceFamily.PRINTY),
                                               29: ("Professional", SourceFamily.PROFESSIONAL)},
             SourceFamily.PRINTY, Confidence.UNVERIFIED),
    _Section("section_2", 32, 39, "C1:O1", 40, {}, None, None),
    _Section("section_3", 44, 47, "C43:O43", None, {46: ("Numberers", SourceFamily.NUMBERERS),
                                                    47: ("Stamp pads", SourceFamily.STAMP_PADS)},
             SourceFamily.DATERS_NUMBERERS, Confidence.TRUSTED),
)
EXPECTED_HEADERS = {"C1:O1": MONTH_LABELS_SECTION_1, "C43:O43": MONTH_LABELS_SECTION_3}


def _ranges() -> tuple[MatrixRange, ...]:
    out = [MatrixRange("header_1", "C1:O1", FieldType.STRING), MatrixRange("header_3", "C43:O43", FieldType.STRING),
           MatrixRange("extra_columns", "P1:P47", FieldType.STRING, required=False, tolerate_invalid_cells=True),
           MatrixRange("section_1_gap", "A30:O30", FieldType.STRING, required=False, tolerate_invalid_cells=True)]
    for s in SECTIONS:
        r = f"{s.first_row}:{s.last_row}"
        a, b = r.split(":")
        out += [MatrixRange(f"{s.name}_codes", f"A{a}:A{b}", FieldType.OPAQUE_ID),
                MatrixRange(f"{s.name}_variants", f"B{a}:B{b}", FieldType.STRING, required=False,
                            tolerate_invalid_cells=True),
                MatrixRange(f"{s.name}_values", f"C{a}:N{b}", FieldType.DECIMAL),
                MatrixRange(f"{s.name}_row_totals", f"O{a}:O{b}", FieldType.DECIMAL)]
        if s.total_row:
            out.append(MatrixRange(f"{s.name}_totals", f"C{s.total_row}:O{s.total_row}", FieldType.DECIMAL))
    return tuple(out)


TRODAT_SPEC = SourceSpec(
    key="trodat_sales",
    audience=Audience.SHARED,
    filename_patterns=TRODAT_FILE_PATTERNS,
    sheet=SHEET,
    layout=MatrixLayout(
        bounds="A1:P47",
        anchors=(
            Anchor("section_1_other_printy", "Other Printy", expected="A28", search="A2:A31"),
            Anchor("section_1_professional", "Professional", expected="A29", search="A2:A31"),
            Anchor("section_1_total", "Total", expected="A31", search="A2:A31"),
            Anchor("section_2_total", "Total", expected="A40", search="A32:A41"),
            Anchor("section_3_title", "Daters Numberers", expected="A42", search="A42:A47"),
            Anchor("section_3_numberers", "Numberers", expected="A46", search="A42:A47"),
            Anchor("section_3_stamp_pads", "Stamp pads", expected="A47", search="A42:A47"),
        ),
        ranges=_ranges(),
    ),
    fields=(),
    default_confidence=Confidence.UNVERIFIED,
    description="Trodat sales report (Halsa_Turkey sheet): native model × variant × month matrix.",
)


# ---------------------------------------------------------------------------------------------
# Models (in memory)
# ---------------------------------------------------------------------------------------------
@dataclass(slots=True)
class TrodatLine:
    line_key: str                     # record_key("trodat_model", [model, variant]) or ("trodat_line", [label])
    line_kind: str                    # "model" | "family_line"
    model_code: str | None            # exact native code (opaque string); None for family lines
    variant: str | None               # column B as written; None when blank
    line_label: str | None            # "Other Printy" / "Professional" / "Numberers" / "Stamp pads"
    family: SourceFamily | None       # None for the unlabelled section 2
    family_confidence: Confidence | None
    section: str
    control_verified: bool            # False where the workbook's own controls are inconsistent (section 2)
    source_row: int
    halsa_sku: None = None            # never assigned here; mapping is not supplied


@dataclass(slots=True)
class TrodatModelMonth:
    record_key: str                   # record_key("trodat_model_month", [line_key, year, month])
    line_key: str
    model_code: str | None
    variant: str | None
    family: SourceFamily | None
    report_year: int
    month: int
    reported_quantity: NormalizedValue   # unverified meaning; as reported (display packs not expanded)


class TrodatSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet: str = SHEET
    report_year: int | None = None
    report_year_origin: str | None = None       # declared | filename
    filename_month_label: str | None = None     # a label only; never a snapshot date
    snapshot_date: dt.date | None = None        # unknown (the workbook carries none)
    model_lines: int = 0
    family_lines: int = 0
    lines_with_variant: int = 0
    lines_by_section: dict[str, int] = Field(default_factory=dict)
    lines_by_family: dict[str, int] = Field(default_factory=dict)
    detail_records: int = 0
    months_with_values: list[int] = Field(default_factory=list)
    filled_cells: int = 0
    blank_cells: int = 0
    integer_like_cells: int = 0
    decimal_cells: int = 0
    invalid_cells: int = 0
    formula_cells: int = 0
    row_total_checks: dict[str, int] = Field(default_factory=dict)
    section_total_checks: dict[str, dict[str, int]] = Field(default_factory=dict)
    halsa_sku_mapping: str = "not_supplied"
    value_semantics: str = "reported_quantity (unverified; appears quantity-like, meaning not confirmed)"


@dataclass
class TrodatResult:
    ok: bool
    failed_step: str | None
    file: str
    lines: list[TrodatLine]
    records: list[TrodatModelMonth]
    report: ValidationReport
    verification: VerificationResult
    counts: TrodatSummary
    import_result: ImportResult | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "failed_step": self.failed_step,
            "source": TRODAT_SPEC.key,
            "file": self.file,
            "counts": self.counts.model_dump(mode="json"),
            "report": self.report.to_dict(),
            "framework_verification": (self.import_result.verification.model_dump(mode="json")
                                       if self.import_result else None),
            "verification": self.verification.model_dump(mode="json"),
        }


# ---------------------------------------------------------------------------------------------
# Report year and candidates
# ---------------------------------------------------------------------------------------------
def parse_report_filename(name: str) -> tuple[int, str] | None:
    """(year, month label) from the approved filename pattern, else None. Never a snapshot date."""
    m = TRODAT_FILENAME.match(unicodedata.normalize("NFC", name))
    if not m:
        return None
    year = int(m.group("year"))
    return (year, m.group("label")) if MIN_YEAR <= year <= MAX_YEAR else None


def find_trodat_candidates(folder: str | Path) -> list[dict[str, Any]]:
    """List Trodat-looking workbooks with the year/label their name carries. Never selects one."""
    out = []
    for p in sorted(Path(folder).glob("*.xlsx")):
        if p.name.startswith("~$") or not TRODAT_SPEC.matches_filename(p.name):
            continue
        parsed = parse_report_filename(p.name)
        out.append({"file": p.name, "report_year": parsed[0] if parsed else None,
                    "filename_month_label": parsed[1] if parsed else None})
    return out


# ---------------------------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------------------------
def import_trodat(path: str | Path, *, report_year: int | None = None) -> TrodatResult:
    """Import one explicitly chosen Trodat report. The report year is `report_year` if declared, else the
    approved filename pattern; otherwise the import fails (no mtime, no current year)."""
    path = Path(path)
    res = run_import(path, TRODAT_SPEC)
    report = res.report
    counts = TrodatSummary()
    result = TrodatResult(False, res.failed_step, path.name, [], [], report, VerificationResult(), counts,
                          import_result=res)
    parsed = parse_report_filename(path.name)
    if report_year is not None:
        if not MIN_YEAR <= report_year <= MAX_YEAR:
            raise ValueError("declared report year out of range")
        counts.report_year, counts.report_year_origin = report_year, "declared"
    elif parsed:
        counts.report_year, counts.report_year_origin = parsed[0], "filename"
    else:
        report.add(Severity.ERROR, "report_year_unknown",
                   "The report year is neither declared nor in the approved filename pattern.")
    if parsed:
        counts.filename_month_label = parsed[1]
    if not res.ok or report.errors:
        result.failed_step = result.failed_step or "year"
        return result

    grid = _grid(res.records)
    _check_layout(grid, report)
    lines, raw_values = _lines(grid, report)
    if report.errors:
        result.failed_step = "layout"
        return result
    _check_totals(grid, lines, raw_values, report, counts)
    if report.errors:
        result.failed_step = "totals"
        return result
    records = _records(lines, raw_values, counts.report_year)  # type: ignore[arg-type]
    _fill_counts(counts, res, lines, raw_values, records)
    result.lines, result.records = lines, records
    _verify(result)
    if not result.verification.ok:
        failed = [c.name for c in result.verification.checks if not c.ok]
        report.add(Severity.ERROR, "trodat_verification_failed", "Trodat verification failed: " + ", ".join(failed) + ".",
                   count=len(failed))
        result.failed_step, result.lines, result.records = "verify", [], []
        return result
    result.ok, result.failed_step = True, None
    return result


def _grid(records: list[NormalizedRecord]) -> dict[str, dict[str, NormalizedValue]]:
    """block name → cell coordinate → value (from the framework's range records)."""
    grid: dict[str, dict[str, NormalizedValue]] = defaultdict(dict)
    for r in records:
        if "block" in r.values:
            grid[r.get("block")][r.get("column_label")] = r.values["value"]
    return grid


def _check_layout(grid: dict[str, dict[str, NormalizedValue]], report: ValidationReport) -> None:
    for rng, expected in EXPECTED_HEADERS.items():
        block = "header_1" if rng == "C1:O1" else "header_3"
        cols = "CDEFGHIJKLMNO"
        row = rng[1:].split(":")[0]
        got = [grid[block].get(f"{c}{row}") for c in cols]
        bad = sum(1 for nv, label in zip(got, expected) if nv is None or nv.value != label)
        if bad:
            report.add(Severity.ERROR, "month_header_mismatch",
                       f"{bad} header cell(s) differ from the expected month labels.", region=rng, count=bad)
    extra = sum(1 for nv in grid["extra_columns"].values() if nv.value is not None or nv.error is not None)
    if extra:
        report.add(Severity.ERROR, "unexpected_column", f"{extra} cell(s) found right of the Total column.",
                   region="P1:P47", count=extra)
    gap = sum(1 for nv in grid["section_1_gap"].values() if nv.value is not None or nv.error is not None)
    if gap:
        report.add(Severity.ERROR, "section_gap_not_empty",
                   "The blank row inside section 1's total range is not empty.", region="A30:O30", count=gap)


def _lines(grid, report) -> tuple[list[TrodatLine], dict[str, dict[int, NormalizedValue]]]:
    lines: list[TrodatLine] = []
    values: dict[str, dict[int, NormalizedValue]] = {}
    codeless = blank = 0
    seen: Counter = Counter()
    for s in SECTIONS:
        for row in range(s.first_row, s.last_row + 1):
            code_nv = grid[f"{s.name}_codes"][f"A{row}"]
            var_nv = grid[f"{s.name}_variants"][f"B{row}"]
            months = {i + 1: grid[f"{s.name}_values"][f"{c}{row}"] for i, c in enumerate(MONTH_COLUMNS)}
            has_values = any(nv.value is not None or nv.error is not None for nv in months.values())
            if row in s.family_lines:
                label, fam = s.family_lines[row]
                line = TrodatLine(record_key("trodat_line", [label]), "family_line", None, None, label, fam,
                                  Confidence.TRUSTED, s.name, s.name != "section_2", row)
            elif code_nv.value is None:
                if has_values:
                    codeless += 1
                else:
                    blank += 1
                continue
            else:
                variant = var_nv.value if var_nv.value is not None else None
                line = TrodatLine(record_key("trodat_model", [code_nv.value, variant]), "model", code_nv.value,
                                  variant, None, s.model_family, s.model_family_confidence, s.name,
                                  s.name != "section_2", row)
                seen[(code_nv.value, variant)] += 1
            lines.append(line)
            values[line.line_key] = months
    if codeless:
        report.add(Severity.ERROR, "detail_row_without_code", f"{codeless} row(s) have values but no model code.",
                   count=codeless)
    if blank:
        report.add(Severity.ERROR, "unexpected_blank_row", f"{blank} declared detail row(s) are empty.", count=blank)
    dup = sum(n - 1 for n in seen.values() if n > 1)
    if dup:
        report.add(Severity.ERROR, "duplicate_model",
                   f"{dup} model × variant identity(ies) repeat across rows or sections.", count=dup)
    return lines, values


def _num(nv: NormalizedValue | None) -> float:
    return float(nv.value) if nv is not None and isinstance(nv.value, (int, float)) else 0.0


def _close(a: float, b: float) -> bool:
    return abs(round(a - b, 6)) <= TOLERANCE


def _multiplier(variant: str | None) -> int:
    m = PACK_VARIANT.fullmatch(variant or "")
    return int(m.group("n")) if m else 1


def _check_totals(grid, lines: list[TrodatLine], values, report: ValidationReport, counts: TrodatSummary) -> None:
    checks = Counter({"checked": 0, "plain": 0, "pack_expanded": 0, "mismatched": 0, "missing": 0})
    for line in lines:
        total_nv = grid[f"{line.section}_row_totals"][f"{TOTAL_COLUMN}{line.source_row}"]
        months = values[line.line_key]
        plain = sum(_num(nv) for nv in months.values())
        checks["checked"] += 1
        if total_nv.value is None:
            if any(nv.value is not None for nv in months.values()):
                checks["missing"] += 1
            continue
        mult = _multiplier(line.variant)
        if _close(total_nv.value, plain):
            checks["plain"] += 1
        elif mult > 1 and _close(total_nv.value, plain * mult):
            checks["pack_expanded"] += 1
        else:
            checks["mismatched"] += 1
    counts.row_total_checks = dict(sorted(checks.items()))  # all five keys, zeros included
    if checks["mismatched"]:
        report.add(Severity.ERROR, "row_total_mismatch",
                   f"{checks['mismatched']} line total(s) equal neither the sum of their months nor the "
                   "pack-expanded sum.", count=checks["mismatched"])
    if checks["missing"]:
        report.add(Severity.ERROR, "row_total_missing", f"{checks['missing']} line total(s) are empty.",
                   count=checks["missing"])
    if checks["pack_expanded"]:
        report.add(Severity.INFO, "row_total_pack_expanded",
                   f"{checks['pack_expanded']} display-pack line total(s) are pack-expanded in the workbook; "
                   "monthly values are kept as reported.", count=checks["pack_expanded"])

    by_section: dict[str, list[TrodatLine]] = defaultdict(list)
    for line in lines:
        by_section[line.section].append(line)
    for s in SECTIONS:
        if s.total_row is None:
            continue
        totals = grid[f"{s.name}_totals"]
        expanded = s.name == "section_2"
        month_bad = 0
        for i, col in enumerate(MONTH_COLUMNS, start=1):
            expect = sum(_num(values[ln.line_key][i]) * (_multiplier(ln.variant) if expanded else 1)
                         for ln in by_section[s.name])
            if not _close(_num(totals[f"{col}{s.total_row}"]), expect):
                month_bad += 1
        annual = _num(totals[f"{TOTAL_COLUMN}{s.total_row}"])
        if expanded:  # the workbook's annual = sum of the line totals as written
            annual_expect = sum(_num(grid[f"{s.name}_row_totals"][f"{TOTAL_COLUMN}{ln.source_row}"])
                                for ln in by_section[s.name])
        else:
            annual_expect = sum(_num(totals[f"{c}{s.total_row}"]) for c in MONTH_COLUMNS)
        annual_bad = 0 if _close(annual, annual_expect) else 1
        counts.section_total_checks[s.name] = {"months_checked": 12, "month_mismatches": month_bad,
                                               "annual_mismatches": annual_bad}
        if month_bad or annual_bad:
            if expanded:
                report.add(Severity.WARNING, "section_total_inconsistent",
                           f"Section 2's workbook totals are not internally consistent ({month_bad} month(s), "
                           f"{annual_bad} annual); its lines are staged with control_verified = False.",
                           region=f"C{s.total_row}:O{s.total_row}", count=month_bad + annual_bad)
            else:
                report.add(Severity.ERROR, "section_total_mismatch",
                           f"{month_bad} month total(s) and {annual_bad} annual total(s) differ from the detail lines.",
                           region=f"C{s.total_row}:O{s.total_row}", count=month_bad + annual_bad)


def _records(lines: list[TrodatLine], values, year: int) -> list[TrodatModelMonth]:
    out = []
    for line in lines:
        for month, nv in values[line.line_key].items():
            if nv.value is None and nv.error is None:
                continue   # blank month: missing, never a zero record
            out.append(TrodatModelMonth(record_key("trodat_model_month", [line.line_key, year, month]), line.line_key,
                                        line.model_code, line.variant, line.family, year, month, nv))
    return out


def _fill_counts(c: TrodatSummary, res: ImportResult, lines, values, records) -> None:
    c.model_lines = sum(1 for ln in lines if ln.line_kind == "model")
    c.family_lines = sum(1 for ln in lines if ln.line_kind == "family_line")
    c.lines_with_variant = sum(1 for ln in lines if ln.variant is not None)
    c.lines_by_section = dict(sorted(Counter(ln.section for ln in lines).items()))
    c.lines_by_family = dict(sorted(Counter(ln.family.value if ln.family else "unlabelled" for ln in lines).items()))
    cells = [nv for ln in lines for nv in values[ln.line_key].values()]
    c.filled_cells = sum(1 for nv in cells if nv.value is not None)
    c.blank_cells = sum(1 for nv in cells if nv.value is None and nv.error is None)
    c.invalid_cells = sum(1 for nv in cells if nv.error is not None)
    c.integer_like_cells = sum(1 for nv in cells if isinstance(nv.value, float) and nv.value.is_integer())
    c.decimal_cells = c.filled_cells - c.integer_like_cells
    c.detail_records = len(records)
    c.months_with_values = sorted({r.month for r in records if r.reported_quantity.value is not None})
    c.formula_cells = sum(f.formula_count for f in res.report.fields)


def _verify(result: TrodatResult) -> None:
    v = result.verification
    keys = [r.record_key for r in result.records]
    v.add("record_keys_unique", len(keys) == len(set(keys)))
    v.add("line_keys_unique", len({ln.line_key for ln in result.lines}) == len(result.lines))
    v.add("no_halsa_sku_assigned", all(ln.halsa_sku is None for ln in result.lines))
    v.add("values_unverified", all(r.reported_quantity.confidence in (Confidence.UNVERIFIED, Confidence.INVALID)
                                   for r in result.records))
    v.add("records_only_from_detail_lines", all(r.line_key in {ln.line_key for ln in result.lines}
                                                for r in result.records))
    blob = json.dumps(result.summary(), ensure_ascii=False, default=str)
    codes = {ln.model_code for ln in result.lines if ln.model_code} | {ln.variant for ln in result.lines if ln.variant}
    leaked = sum(1 for s in codes if len(s) >= 3 and s in blob)
    v.add("summary_has_no_model_codes", leaked == 0, f"{leaked} leak(s)")


__all__ = ["TRODAT_SPEC", "SourceFamily", "TrodatLine", "TrodatModelMonth", "TrodatSummary", "TrodatResult",
           "import_trodat", "find_trodat_candidates", "parse_report_filename", "SECTIONS", "TOLERANCE"]
