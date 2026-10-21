"""Formula-aware validation of the CORE ERP sources and their joins (M8-ACCURACY-2). Counts only; never values.

Core sources (stock, sales order lines, Cari aging) feed the M5 pandas pipeline, which cannot tell an Excel error, a
formula without a saved result and a blank cell apart. This module inspects the SAME frozen bytes the pipeline read,
in one streaming pass over the worksheet XML (formula and cached result of every cell together; the M6 two-view
reader is ~10× slower on a 180k-row sheet), using the framework's vocabulary (`CellKind`, Excel error codes, external
reference detection). `tests/test_m8_accuracy2.py` proves it classifies cells exactly like `services.importers.excel
.classify`.

It distinguishes: valid numeric zero, empty cell, missing required value, Excel error, formula with an Excel error,
formula without a cached result, external workbook link / reference, invalid date, duplicate records, header
ambiguity. Joins are REPORTED (counts of unmatched / ambiguous identities); no matching rule is invented, no identity
is combined, D-003 is unchanged, DQ-5 / DQ-6 stay open.

Nothing is written, repaired or coerced. Nothing here ever reaches AI or logs records.
"""
from __future__ import annotations

import datetime as dt
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

from services.importers.excel import EXCEL_ERRORS, EXTERNAL_REF_RE, external_link_targets
from services.importers.models import CellKind

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_ODR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_REF_RE = re.compile(r"^([A-Z]{1,3})(\d+)$")
_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
_BUCKET_RE = re.compile(r"(" + "|".join(_MONTHS) + r")\s*[-–/ ]\s*(\d{4})")
EXCEL_DATE_MIN, EXCEL_DATE_MAX = 1, 2958465          # serials 1900-01-01 … 9999-12-31


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


@dataclass
class ColumnScan:
    header: str
    col: int
    present: int = 0                  # cells in the XML with content (value, formula or error)
    literal: int = 0
    literal_zero: int = 0             # a typed numeric 0 (valid zero, not a blank)
    error: int = 0                    # literal Excel error value
    formula_value: int = 0
    formula_zero: int = 0
    formula_missing_cache: int = 0
    formula_error: int = 0
    external_ref: int = 0
    text: int = 0
    invalid_dates: int = 0            # date columns only
    numeric_values: list[float] = field(default_factory=list, repr=False)   # only when asked (never serialized)

    def empty(self, data_rows: int) -> int:
        return max(0, data_rows - self.present)

    def counts(self, data_rows: int) -> dict[str, int]:
        return {"empty": self.empty(data_rows), "literal": self.literal, "literal_zero": self.literal_zero,
                "error": self.error, "formula_value": self.formula_value, "formula_zero": self.formula_zero,
                "formula_missing_cache": self.formula_missing_cache, "formula_error": self.formula_error,
                "external_ref": self.external_ref, "text": self.text, "invalid_dates": self.invalid_dates}


@dataclass
class SheetScan:
    headers: dict[int, str]
    duplicate_headers: dict[str, int]       # header text → occurrences (>1)
    columns: dict[str, ColumnScan]
    data_rows: int
    external_links: int
    ok: bool = True
    failure: str | None = None


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    out: list[str] = []
    for _, el in ET.iterparse(io.BytesIO(z.read("xl/sharedStrings.xml")), events=("end",)):
        if el.tag == f"{_NS}si":
            out.append("".join(t.text or "" for t in el.iter(f"{_NS}t")))
            el.clear()
    return out


def _sheet_part(z: zipfile.ZipFile, index: int) -> str:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = list(wb.iter(f"{_NS}sheet"))
    rid = sheets[index].get(f"{_ODR}id")
    for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(f"{_REL}Relationship"):
        if rel.get("Id") == rid:
            t = rel.get("Target") or ""
            return t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join("xl", t))
    raise KeyError("sheet part")


def _is_date_text(text: str) -> bool:
    t = text.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%Y %H:%M:%S"):
        try:
            dt.datetime.strptime(t, fmt)
            return True
        except ValueError:
            continue
    return False


def scan_sheet(content: bytes, *, sheet_index: int = 0, header_row: int = 1, wanted: tuple[str, ...] = (),
               date_columns: tuple[str, ...] = (), keep_numbers: tuple[str, ...] = (),
               strip_headers: bool = False, header_match=None, exclude_rows: frozenset[int] = frozenset()) -> SheetScan:
    """One streaming pass. `wanted` headers are matched exactly (or stripped when `strip_headers`), first occurrence —
    the occurrence pandas uses — and a duplicated wanted header is reported. `header_match(label)` may select extra
    columns (e.g. aging buckets). `exclude_rows` (Excel row numbers) are RECOGNIZED report-control rows
    (`analyze_stock_rows`): they are not data rows and are reported separately, never silently."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        strings = _shared_strings(z)
        part = _sheet_part(z, sheet_index)
        links = len(external_link_targets("x.xlsx", content=content))
        headers: dict[int, str] = {}
        by_col: dict[int, ColumnScan] = {}
        data_rows_seen: set[int] = set()
        with z.open(part) as fh:
            for _, el in ET.iterparse(fh, events=("end",)):
                if el.tag != f"{_NS}row":
                    continue
                r = int(el.get("r") or 0)
                pos = 0
                for c in el.findall(f"{_NS}c"):
                    pos += 1
                    ref = c.get("r")
                    m = _REF_RE.match(ref) if ref else None
                    col = _col_index(m.group(1)) if m else pos
                    t = c.get("t")
                    f = c.find(f"{_NS}f")
                    v = c.find(f"{_NS}v")
                    is_ = c.find(f"{_NS}is")
                    raw = v.text if v is not None else None
                    if t == "s" and raw is not None:
                        try:
                            text_val = strings[int(raw)]
                        except (ValueError, IndexError):
                            text_val = None
                    elif t == "inlineStr" and is_ is not None:
                        text_val = "".join(x.text or "" for x in is_.iter(f"{_NS}t"))
                    elif t == "str":
                        text_val = raw
                    else:
                        text_val = None
                    if r == header_row:
                        label = text_val if text_val is not None else raw
                        if label is not None:
                            headers[col] = label.strip() if strip_headers else label
                        continue
                    if r < header_row or r in exclude_rows:
                        continue
                    if f is None and raw is None and is_ is None:
                        continue                                   # an empty cell element
                    data_rows_seen.add(r)
                    s_ = by_col.get(col)
                    if s_ is not None:
                        _count(s_, f, t, raw, text_val, date_columns, keep_numbers)
                el.clear()
                if r == header_row:
                    by_col = _select(headers, wanted, header_match)
        cols = {s.header: s for s in by_col.values()}
    dups: dict[str, int] = {}
    for name in set(headers.values()):
        n = sum(1 for h in headers.values() if h == name)
        if n > 1 and (name in wanted or (header_match and header_match(name))):
            dups[name] = n
    return SheetScan(headers, dups, cols, len(data_rows_seen), links)


def _select(headers: dict[int, str], wanted, header_match) -> dict[int, ColumnScan]:
    out: dict[int, ColumnScan] = {}
    seen: set[str] = set()
    for col in sorted(headers):
        h = headers[col]
        if h in seen:
            continue
        if h in wanted or (header_match is not None and header_match(h)):
            out[col] = ColumnScan(h, col)
            seen.add(h)
    return out


def _count(s: ColumnScan, f, t, raw, text_val, date_columns, keep_numbers) -> None:
    s.present += 1
    if f is not None:
        if f.text and EXTERNAL_REF_RE.search(f.text):
            s.external_ref += 1
        if t == "e":
            s.formula_error += 1
        elif raw is None:
            s.formula_missing_cache += 1
        else:
            s.formula_value += 1
            if t in (None, "n"):
                try:
                    if float(raw) == 0:
                        s.formula_zero += 1
                    if s.header in keep_numbers:
                        s.numeric_values.append(float(raw))
                except ValueError:
                    pass
            if s.header in date_columns and not _date_cell_ok(t, raw, text_val):
                s.invalid_dates += 1
        return
    if t == "e":
        s.error += 1
        return
    s.literal += 1
    if t in (None, "n") and raw is not None:
        try:
            x = float(raw)
            if x == 0:
                s.literal_zero += 1
            if s.header in keep_numbers:
                s.numeric_values.append(x)
        except ValueError:
            pass
    elif t in ("s", "str", "inlineStr"):
        s.text += 1
        if text_val is not None and text_val.strip().upper() in EXCEL_ERRORS:
            s.error += 1           # an error code stored as text (pandas reads it as a string, never a number)
            s.literal -= 1
    if s.header in date_columns and not _date_cell_ok(t, raw, text_val):
        s.invalid_dates += 1


def _date_cell_ok(t, raw, text_val) -> bool:
    if t == "d":
        return raw is not None
    if t in (None, "n"):
        try:
            x = float(raw)
        except (TypeError, ValueError):
            return False
        return EXCEL_DATE_MIN <= x <= EXCEL_DATE_MAX
    if t in ("s", "str", "inlineStr"):
        return bool(text_val) and _is_date_text(text_val)
    return False


# ---------------------------------------------------------------------------------------------
# ---------------------------------------------------------------------------------------------
# stock report rows (M8-ACCURACY-2B): product rows vs the export's own report-control (total) rows
# ---------------------------------------------------------------------------------------------
_AGG_RE = re.compile(r"^\(?(SUM|SUBTOTAL)\((?:(\d+),)?\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)\)\)?$")
_CELLREF_RE = re.compile(r"\$?([A-Z]{1,3})\$?(\d+)")
_SUBTOTAL_SUM = {"9", "109"}


@dataclass
class _Cell:
    formula: str | None
    kind: str                          # "number" | "text" | "error" | "bool" | "blank"
    value: Any


@dataclass
class StockRowAnalysis:
    """Row-level facts of the supported stock export (counts only; nothing is dropped or repaired).

    product row      = the identity cell (first `Stok_Kodu` column, the one the M5 pipeline reads) is non-blank;
    footer row       = a trailing row RECOGNIZED as the export's report-control rows (see `analyze_stock_rows`);
    ambiguous row    = a trailing row without identity that does not satisfy the footer rules (fails closed: it is
                       counted as a row with missing identity);
    missing identity = a row with cells but no identity that is not a recognized footer."""
    ok: bool
    failure: str | None = None
    header_row: int = 0
    first_data_row: int | None = None
    last_product_row: int | None = None
    parsed_rows: int = 0                       # rows below the header with at least one cell
    footer_rows: tuple[int, ...] = ()          # recognized report-control rows (Excel row numbers)
    footer_kinds: dict[str, int] = field(default_factory=dict)   # aggregate / label
    ambiguous_rows: tuple[int, ...] = ()
    missing_identity_rows: int = 0             # blank identity, not a recognized footer (incl. ambiguous rows)
    code_rows: int = 0
    distinct_codes: int = 0
    duplicate_code_occurrences: int = 0        # code_rows - distinct_codes (never deduplicated)
    identity_header_columns: int = 0           # columns labelled like the identity header (>1 = ambiguous label)
    footer_checks: dict[str, int] = field(default_factory=dict)  # checked / matched / mismatched / error_propagated / unsupported / missing_cache
    footer_by_column: dict[str, dict[str, int]] = field(default_factory=dict)   # column letter → same counts
    footer_by_header: dict[str, str] = field(default_factory=dict)  # header → PASS/OPEN/FAIL (field-level evidence)
    coverage: dict[str, int] = field(default_factory=dict)
    template: dict[str, Any] = field(default_factory=dict)       # identity column decision (check_stock_template)

    LABELS_TR = {"parsed_rows": "Okunan satır", "footer_rows": "Tanınan rapor toplam satırı",
                 "code_rows": "Ürün kodlu satır", "distinct_codes": "Farklı ürün kodu",
                 "duplicate_code_occurrences": "Yinelenen ürün kodu tekrarı",
                 "missing_identity_rows": "Kimliği eksik / belirsiz satır"}

    def counts(self) -> dict[str, int]:
        return {"parsed_rows": self.parsed_rows, "footer_rows": len(self.footer_rows), "code_rows": self.code_rows,
                "distinct_codes": self.distinct_codes, "duplicate_code_occurrences": self.duplicate_code_occurrences,
                "missing_identity_rows": self.missing_identity_rows}

    def summary(self) -> dict[str, Any]:
        return {"ok": self.ok, "failure": self.failure, "counts": self.counts(), "footer_kinds": dict(self.footer_kinds),
                "ambiguous_rows": len(self.ambiguous_rows), "footer_checks": dict(self.footer_checks),
                "footer_by_column": {k: dict(v) for k, v in self.footer_by_column.items()},
                "footer_status": self.footer_status(),
                "identity_header_columns": self.identity_header_columns, "coverage": dict(self.coverage),
                "template": dict(self.template)}

    def footer_status(self) -> str:
        """PASS only when every checked footer cell reconciled numerically; FAIL on a contradiction; OPEN otherwise
        (error-propagated, missing cache, unsupported, ambiguous rows, or nothing checked). Partial ≠ verified."""
        return _footer_status(self.footer_checks or {}, self.ambiguous_rows)

    def footer_scope(self) -> str | None:
        """"per_field" when each column's footer result can stand on its own: rows recognized, at least one cell
        checked AND matched, no ambiguous trailing row. "source_wide" otherwise (a footer that reconciles nothing, or
        ambiguous rows, says nothing column-specific). None when the export has no footer cell at all."""
        c = self.footer_checks or {}
        if not self.ok or not c.get("checked"):
            return None if self.ok else "source_wide"
        return "per_field" if (c.get("matched") and not self.ambiguous_rows) else "source_wide"


def _read_cells(content: bytes, sheet_index: int = 0) -> tuple[dict[int, dict[int, _Cell]], list[int]]:
    """Every non-empty cell (formula text + cached value), by Excel row / column. Small sheets only (stock export)."""
    rows: dict[int, dict[int, _Cell]] = {}
    shared: dict[str, tuple[str, str]] = {}           # shared-formula index → (master formula, master cell ref)
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        strings = _shared_strings(z)
        part = _sheet_part(z, sheet_index)
        hidden: list[int] = []
        with z.open(part) as fh:
            for _, el in ET.iterparse(fh, events=("end",)):
                if el.tag != f"{_NS}row":
                    continue
                r = int(el.get("r") or 0)
                if el.get("hidden") in ("1", "true"):
                    hidden.append(r)
                pos = 0
                for c in el.findall(f"{_NS}c"):
                    pos += 1
                    ref = c.get("r")
                    m = _REF_RE.match(ref) if ref else None
                    col = _col_index(m.group(1)) if m else pos
                    t = c.get("t")
                    f = c.find(f"{_NS}f")
                    v = c.find(f"{_NS}v")
                    is_ = c.find(f"{_NS}is")
                    raw = v.text if v is not None else None
                    ftxt = (f.text or "") if f is not None else None
                    if f is not None and f.get("t") == "shared":
                        # Excel stores a run of identical formulas once ("shared"); the other cells carry only the
                        # index. Expand them exactly as Excel does (relative references shifted to the cell) so a
                        # dependent cell is judged by its real formula (found on the real export, M8-ACCURACY-2B).
                        si = f.get("si")
                        if ftxt and ref:
                            shared[si] = (ftxt, ref)
                        elif si in shared and ref:
                            ftxt = _translate(shared[si][0], shared[si][1], ref)
                        else:
                            ftxt = "\x00unresolved-shared"           # never matches a footer rule (fails closed)
                    if ftxt is None and raw is None and is_ is None:
                        continue
                    if t == "s" and raw is not None:
                        try:
                            cell = _Cell(ftxt, "text", strings[int(raw)])
                        except (ValueError, IndexError):
                            cell = _Cell(ftxt, "text", None)
                    elif t == "inlineStr" and is_ is not None:
                        cell = _Cell(ftxt, "text", "".join(x.text or "" for x in is_.iter(f"{_NS}t")))
                    elif t == "str":
                        cell = _Cell(ftxt, "text" if raw not in (None, "") else "blank", raw)
                    elif t == "e":
                        cell = _Cell(ftxt, "error", raw)
                    elif t == "b":
                        cell = _Cell(ftxt, "bool", raw)
                    elif raw is None:
                        cell = _Cell(ftxt, "blank", None)
                    else:
                        try:
                            cell = _Cell(ftxt, "number", float(raw))
                        except ValueError:
                            cell = _Cell(ftxt, "text", raw)
                    if cell.kind == "text" and isinstance(cell.value, str) and cell.value.strip() == "" and ftxt is None:
                        continue
                    rows.setdefault(r, {})[col] = cell
                el.clear()
    return rows, hidden


def _translate(formula: str, origin: str, dest: str) -> str:
    """The master formula of a shared-formula run, as it reads in cell `dest` (Excel's relative reference rule)."""
    from openpyxl.formula.translate import Translator
    return Translator("=" + formula, origin=origin).translate_formula(dest)[1:]


# ---------------------------------------------------------------------------------------------
# owner-approved stock export templates (M8-ACCURACY-2B, D-056) — COLUMN IDENTITY ONLY
# ---------------------------------------------------------------------------------------------
# The DinamikCRM stock export labels two different columns `Stok_Kodu`. The owner confirmed (2026-10-21): physical
# column A = the product / stock code, physical column J = the country of origin. A workbook with a duplicated
# identity header is accepted ONLY when its whole header row is exactly an approved template (column count and the
# SHA-256 of every header label in order); any moved, renamed, added or removed column is refused (fail closed),
# never resolved by picking the first header. The approval says nothing about report date, completeness, quantities,
# totals or the duplicated product code.
STOCK_TEMPLATES: dict[str, dict[str, Any]] = {
    "dinamikcrm_stok_v1": {
        "title_tr": "DinamikCRM stok raporu (sahip onaylı sütun kimliği, D-056)",
        "header_row": 4,
        # measured owner-locally on the 5 Oct 2026 export (header row only; no data value is stored or committed)
        "columns": 89,
        "header_sha256": "d3ba17a4ff313868d3a01ae213a3bd5f47e50672d78f1c74d0ccfa37a44d881c",
        "identity": "A",              # product / stock code
        "country_of_origin": "J",     # also labelled `Stok_Kodu`; never read by the pipeline, never a product code
        "fields": {                   # pipeline field → physical column (strict position check)
            "Stok_Kodu": "A", "Stok_Adı": "B", "TOPLAM STOK MİKTAR ": "W", "STOK Kontrol %": "AI",
            "Envanter değeri": "AV", "minseviye": "BN", "maxseviye": "BO", "DURUM": "CB",
        },
    },
}


def _col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def header_signature(labels: dict[int, str]) -> tuple[int, str]:
    """(column count, SHA-256) of a header row: every label in physical order, blanks included. No label is stored."""
    import hashlib
    n = max((c for c, lab in labels.items() if lab != ""), default=0)    # trailing empty (styled) cells ignored
    canon = "\x1f".join(labels.get(c, "") for c in range(1, n + 1))
    return n, hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass
class TemplateCheck:
    status: str                       # approved_template | single_identity_header | unapproved_duplicate_identity |
                                      # identity_header_missing
    template: str | None = None
    identity_column: str | None = None
    country_of_origin_column: str | None = None
    columns: int = 0
    header_sha256: str | None = None
    identity_header_columns: int = 0
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in ("approved_template", "single_identity_header")

    def summary(self) -> dict[str, Any]:
        return {"status": self.status, "template": self.template, "identity_column": self.identity_column,
                "country_of_origin_column": self.country_of_origin_column, "columns": self.columns,
                "header_sha256": self.header_sha256, "identity_header_columns": self.identity_header_columns,
                "problems": list(self.problems)}


def check_stock_template(labels: dict[int, str], identity: str = "Stok_Kodu") -> TemplateCheck:
    """Which physical column is the product code? Deterministic, never 'the first one' for a duplicated header."""
    from services.importers.excel import column_index
    cols, sha = header_signature(labels)
    id_cols = sorted(c for c, lab in labels.items() if lab == identity)
    base = dict(columns=cols, header_sha256=sha, identity_header_columns=len(id_cols))
    if not id_cols:
        return TemplateCheck("identity_header_missing", **base)
    for name, t in STOCK_TEMPLATES.items():
        if t.get("header_sha256") and t["header_sha256"] == sha and t.get("columns") == cols:
            idc, cc = column_index(t["identity"]), column_index(t["country_of_origin"])
            problems = []
            if labels.get(idc) != identity:
                problems.append("identity_column_moved")
            for field_name, letter in (t.get("fields") or {}).items():
                if labels.get(column_index(letter)) != field_name:
                    problems.append(f"field_moved:{field_name}")
            if problems:                                             # (cannot happen with an identical signature)
                return TemplateCheck("unapproved_duplicate_identity", name, problems=tuple(problems), **base)
            return TemplateCheck("approved_template", name, t["identity"], t["country_of_origin"], **base)
    if len(id_cols) == 1:
        return TemplateCheck("single_identity_header", None, _col_letter(id_cols[0]), None, **base)
    return TemplateCheck("unapproved_duplicate_identity", None, None, None,
                         problems=("duplicate_identity_header_outside_an_approved_template",), **base)


def _footer_status(c: dict[str, int], ambiguous) -> str:
    if c.get("mismatched"):
        return "FAIL"
    if not c.get("checked") or c.get("matched", 0) != c.get("checked") or ambiguous:
        return "OPEN"
    return "PASS"


def _blank(cell: _Cell | None) -> bool:
    return cell is None or cell.kind == "blank" or (cell.kind == "text" and str(cell.value or "").strip() == "")


def _row_local(formula: str, row: int) -> bool:
    refs = _CELLREF_RE.findall(formula)
    return bool(refs) and ":" not in formula and all(int(n) == row for _, n in refs)


def analyze_stock_rows(content: bytes, *, header_row: int = 4, identity: str = "Stok_Kodu", name: str = "Stok_Adı",
                       quantity: str = "TOPLAM STOK MİKTAR ", coverage: str = "STOK Kontrol %") -> StockRowAnalysis:
    """Deterministic recognition of the supported stock export's trailing report-control rows. Never raises.

    Only rows AFTER the last product row can be footers; a blank-identity row between product rows is always a row
    with missing identity. A trailing row is a recognized footer when the identity and name cells are blank and:
    - AGGREGATE: it has at least one aggregate cell, every aggregate cell is `SUM(X a:X b)` or `SUBTOTAL(9|109, X a:X b)`
      over its OWN column X with exactly the data range a = first data row, b = last product row; any other formula
      only references its own row and has a blank cached result; it has no numeric literal (text labels allowed in
      columns it does not aggregate); or
    - LABEL: no number and no aggregate; the text cells that sit in columns an ADJACENT recognized aggregate row
      aggregates all hold one identical text, any other text cell repeats the adjacent aggregate row's own text label
      in the same column, and any formula is row-local with a blank cached result.
    Everything else is AMBIGUOUS (fails closed as missing identity). Every aggregate cell is reconciled against the
    rows it covers: matched / mismatched (contradictory totals) / error_propagated (an error cell inside the range,
    so Excel itself caches an error) / missing_cache / unsupported (SUBTOTAL on a sheet with hidden rows)."""
    try:
        rows, hidden = _read_cells(content)
    except Exception as e:
        return StockRowAnalysis(False, f"read:{type(e).__name__}")
    hdr = rows.get(header_row, {})
    labels = {c: (str(x.value) if x.value is not None else "") for c, x in hdr.items()}
    id_cols = sorted(c for c, lab in labels.items() if lab == identity)
    name_cols = sorted(c for c, lab in labels.items() if lab == name)
    tc = check_stock_template(labels, identity)
    if not id_cols:
        return StockRowAnalysis(False, "identity_header_missing", header_row, template=tc.summary())
    if not tc.ok:                    # a duplicated identity header outside an approved template: never guess
        return StockRowAnalysis(False, "identity_header_ambiguous", header_row, identity_header_columns=len(id_cols),
                                template=tc.summary())
    from services.importers.excel import column_index
    idc = column_index(tc.identity_column)
    namec = name_cols[0] if name_cols else None
    qtyc = next((c for c, lab in sorted(labels.items()) if lab == quantity), None)
    covc = next((c for c, lab in sorted(labels.items()) if lab == coverage), None)
    data = sorted(r for r in rows if r > header_row and rows[r])
    out = StockRowAnalysis(True, None, header_row, identity_header_columns=len(id_cols), template=tc.summary())
    out.parsed_rows = len(data)
    if not data:
        return out
    product = [r for r in data if not _blank(rows[r].get(idc))]
    out.first_data_row = data[0]
    if not product:
        out.missing_identity_rows = len(data)
        out.ambiguous_rows = tuple(data)
        return out
    first, last = data[0], product[-1]
    out.last_product_row = last
    codes = [str(rows[r][idc].value).strip() if rows[r][idc].kind != "number" else repr(rows[r][idc].value)
             for r in product]
    out.code_rows, out.distinct_codes = len(codes), len(set(codes))
    out.duplicate_code_occurrences = out.code_rows - out.distinct_codes
    middle_blank = [r for r in data if r <= last and _blank(rows[r].get(idc))]
    trailing = [r for r in data if r > last]

    def aggregate_cells(r):
        res, ok = {}, True
        for c, cell in rows[r].items():
            if cell.formula is None:
                continue
            m = _AGG_RE.match(cell.formula.strip().lstrip("="))
            if m:
                fn, c1, a, c2, b = m.group(2), m.group(3), int(m.group(4)), m.group(5), int(m.group(6))
                own = _col_index(c1) == c == _col_index(c2)
                if m.group(1) == "SUBTOTAL" and fn not in _SUBTOTAL_SUM:
                    ok = False
                elif m.group(1) == "SUM" and fn is not None:
                    ok = False
                elif not own or a != first or b != last:
                    ok = False
                else:
                    res[c] = (m.group(1), cell)
            elif not (_row_local(cell.formula, r) and _blank(cell)):
                ok = False
        return res, ok

    kinds: dict[int, str] = {}
    aggs: dict[int, dict] = {}
    for r in trailing:
        cells = rows[r]
        if not (_blank(cells.get(idc)) and (namec is None or _blank(cells.get(namec)))):
            kinds[r] = "ambiguous"
            continue
        agg, ok = aggregate_cells(r)
        numbers = [c for c, x in cells.items() if x.formula is None and x.kind == "number"]
        if agg and ok and not numbers and not any(x.formula is None and x.kind not in ("text", "blank") for x in
                                                  cells.values()):
            kinds[r], aggs[r] = "aggregate", agg
    for r in trailing:
        if r in kinds:
            continue
        cells = rows[r]
        texts = {c: x for c, x in cells.items() if x.formula is None and x.kind == "text"}
        others = [x for c, x in cells.items() if c not in texts and not (x.formula is not None and
                                                                          _row_local(x.formula, r) and _blank(x))]
        neighbours = [n for n in (r - 1, r + 1) if kinds.get(n) == "aggregate"]
        label_ok = False
        for n in neighbours:
            in_agg = {c: x for c, x in texts.items() if c in aggs[n]}
            # a text that the adjacent aggregate row carries too, in the same column (a shared row label)
            shared = {c: x for c, x in texts.items() if c not in aggs[n] and rows[n].get(c) is not None
                      and rows[n][c].formula is None and rows[n][c].kind == "text"
                      and str(rows[n][c].value).strip() == str(x.value).strip()}
            if (in_agg and not others and len(in_agg) + len(shared) == len(texts)
                    and len({str(x.value).strip() for x in in_agg.values()}) == 1):
                label_ok = True
                break
        kinds[r] = "label" if label_ok else "ambiguous"
    out.footer_rows = tuple(r for r in trailing if kinds[r] in ("aggregate", "label"))
    out.footer_kinds = {k: sum(1 for r in out.footer_rows if kinds[r] == k) for k in ("aggregate", "label")}
    out.ambiguous_rows = tuple(r for r in trailing if kinds[r] == "ambiguous")
    out.missing_identity_rows = len(middle_blank) + len(out.ambiguous_rows)
    # reconcile every aggregate cell against the rows it covers (first..last), exactly as Excel would sum them
    chk = {"checked": 0, "matched": 0, "mismatched": 0, "error_propagated": 0, "missing_cache": 0, "unsupported": 0}
    covered = [r for r in data if first <= r <= last]
    by_col: dict[str, dict[str, int]] = {}
    for r, agg in aggs.items():
        for c, (fn, cell) in agg.items():
            chk["checked"] += 1
            vals = [rows[x].get(c) for x in covered]
            if fn == "SUBTOTAL" and hidden:
                res = "unsupported"
            elif any(v is not None and v.kind == "error" for v in vals):
                res = "error_propagated" if cell.kind == "error" else "mismatched"
            elif cell.kind == "blank":
                res = "missing_cache"
            elif cell.kind == "number" and abs(cell.value - sum(v.value for v in vals if v is not None and
                                                                  v.kind == "number")) <= max(
                    0.01, 1e-9 * abs(sum(v.value for v in vals if v is not None and v.kind == "number"))):
                res = "matched"
            else:
                res = "mismatched"
            chk[res] += 1
            d = by_col.setdefault(_col_letter(c), {k: 0 for k in chk})     # physical column letter (no labels)
            d["checked"] += 1
            d[res] += 1
    out.footer_checks = chk
    out.footer_by_column = by_col
    for letter, d in by_col.items():                          # header → worst footer result (in memory only)
        lab = labels.get(_col_index(letter), "")
        st = _footer_status(d, ())
        prev = out.footer_by_header.get(lab)
        out.footer_by_header[lab] = st if prev is None else max(prev, st, key=("PASS", "OPEN", "FAIL").index)
    # coverage: mathematically undefined (#DIV/0!) vs other errors vs blank vs a recorded number (no substitution)
    if covc is not None:
        cov = {"undefined_div0": 0, "other_error": 0, "blank": 0, "numeric": 0, "undefined_with_stock": 0,
               "stock_quantity_missing": 0}
        for r in product:
            x = rows[r].get(covc)
            q = rows[r].get(qtyc) if qtyc else None
            if x is not None and x.kind == "error":
                key = "undefined_div0" if str(x.value) == "#DIV/0!" else "other_error"
                cov[key] += 1
                if key == "undefined_div0" and q is not None and q.kind == "number" and q.value > 0:
                    cov["undefined_with_stock"] += 1
            elif _blank(x):
                cov["blank"] += 1
            elif x.kind == "number":
                cov["numeric"] += 1
            if q is None or q.kind not in ("number",):
                cov["stock_quantity_missing"] += 1
        out.coverage = cov
    return out


# core source definitions (the columns the CURRENT M5 pipeline reads; nothing new is required)
# ---------------------------------------------------------------------------------------------
CORE = {
    "stock": {"header_row": 4, "strip": False,
              "required": ("Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye",
                           "Envanter değeri", "DURUM"),
              "identity": ("Stok_Kodu",), "nonblank": ("Stok_Kodu",),
              "amounts": ("TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye", "Envanter değeri"),
              "dates": ()},
    "sales": {"header_row": 1, "strip": False,
              "required": ("Tarih", "Nettutar", "miktar", "sip_evrakno_seri", "sip_evrakno_sira", "Pro_kodu",
                           "ProjeIsmi", "Musterikod", "Musteri_ismi", "sto_kod", "StokIsmi", "anagrup"),
              "identity": ("sip_evrakno_seri", "sip_evrakno_sira", "sto_kod"),
              "nonblank": ("Tarih", "Nettutar", "miktar", "sip_evrakno_seri", "sip_evrakno_sira", "Musterikod",
                           "sto_kod"),
              "amounts": ("Nettutar", "miktar"), "dates": ("Tarih",)},
    "aging": {"header_row": 1, "strip": True,
              "required": ("Hesap kodu", "Hesap adı", "Temsilci kodu"), "identity": ("Hesap kodu",),
              "nonblank": ("Hesap kodu",), "amounts": (), "dates": (), "buckets": True},
}


def _is_bucket(label: str) -> bool:
    return "öncesi" in label or any(m in label for m in _MONTHS)


@dataclass
class CoreInspection:
    key: str
    ok: bool
    failure: str | None
    layout_supported: bool | None
    data_rows: int = 0
    error_cells: int | None = None
    missing_cached_formulas: int | None = None
    missing_required_values: int | None = None
    blank_amount_cells: int | None = None
    zero_amount_cells: int | None = None
    invalid_dates: int | None = None
    external_links: int | None = None
    external_references: int | None = None
    ambiguous_headers: int | None = None
    missing_headers: tuple[str, ...] = ()
    latest_bucket: tuple[int, int] | None = None     # aging: newest month bucket (year, month) from the headers
    columns: dict[str, dict[str, int]] = field(default_factory=dict)
    duplicate_headers: tuple[str, ...] = ()
    rows: StockRowAnalysis | None = None             # stock: product / footer / identity row analysis (2B)

    def fields(self) -> dict[str, dict[str, Any]]:
        """Field-level evidence (M8-ACCURACY-2B): per inspected column, so a metric is judged by the fields it USES.
        Aging month buckets are combined into `bucket:*` (the collections metric reads all of them)."""
        out: dict[str, dict[str, Any]] = {}
        resolved = self.resolved_headers()
        for h, c in self.columns.items():
            key = "bucket:*" if (self.key == "aging" and _is_bucket(h)) else h
            d = out.setdefault(key, {"error_cells": 0, "missing_cached_formulas": 0, "blank_cells": 0,
                                     "invalid_dates": 0, "external_refs": 0, "duplicate_header": False,
                                     "amount": key == "bucket:*" or h in CORE[self.key]["amounts"]})
            d["error_cells"] += c.get("error", 0) + c.get("formula_error", 0)
            d["missing_cached_formulas"] += c.get("formula_missing_cache", 0)
            d["blank_cells"] += c.get("empty", 0)
            d["invalid_dates"] += c.get("invalid_dates", 0)
            d["external_refs"] += c.get("external_ref", 0)
            d["duplicate_header"] = d["duplicate_header"] or (h in self.duplicate_headers and h not in resolved)
            if self.rows is not None and self.rows.ok and self.rows.footer_checks.get("checked"):
                # PASS / OPEN / FAIL for this field's own footer cells; "not_aggregated" when no footer totals it
                prev = d.get("footer_totals")
                cur = self.rows.footer_by_header.get(h, "not_aggregated")
                order = ("not_aggregated", "PASS", "OPEN", "FAIL")
                d["footer_totals"] = cur if prev is None else max(prev, cur, key=order.index)
        return out

    def resolved_headers(self) -> frozenset[str]:
        """Duplicated headers whose column identity an approved template fixes (D-056: stock `Stok_Kodu` = column A)."""
        t = (self.rows.template if self.rows is not None else {}) or {}
        return frozenset({"Stok_Kodu"}) if t.get("status") == "approved_template" else frozenset()

    def summary(self) -> dict:
        d = {k: getattr(self, k) for k in ("ok", "failure", "layout_supported", "data_rows", "error_cells",
                                           "missing_cached_formulas", "missing_required_values", "blank_amount_cells",
                                           "zero_amount_cells", "invalid_dates", "external_links",
                                           "external_references", "ambiguous_headers")}
        d["missing_headers"] = list(self.missing_headers)
        d["latest_bucket"] = list(self.latest_bucket) if self.latest_bucket else None
        d["duplicate_headers"] = list(self.duplicate_headers)
        if self.rows is not None:
            d["rows"] = self.rows.summary()                 # stock: counts only (2B)
        return d


def inspect_core(key: str, content: bytes) -> CoreInspection:
    """Formula-aware inspection of one core workbook from its frozen bytes. Never raises: an inspection that cannot
    run is a failure (the caller marks the source INVALID), never a clean result."""
    spec = CORE[key]
    rows = analyze_stock_rows(content, header_row=spec["header_row"]) if key == "stock" else None
    exclude = frozenset(rows.footer_rows) if (rows is not None and rows.ok) else frozenset()
    try:
        scan = scan_sheet(content, header_row=spec["header_row"], wanted=spec["required"],
                          date_columns=spec["dates"], strip_headers=spec["strip"],
                          header_match=_is_bucket if spec.get("buckets") else None, exclude_rows=exclude)
    except Exception as e:                                    # unreadable package / XML: fail closed
        return CoreInspection(key, False, f"scan:{type(e).__name__}", None)
    missing = tuple(h for h in spec["required"] if h not in scan.columns)
    buckets = [s for h, s in scan.columns.items() if spec.get("buckets") and _is_bucket(h)]
    inspected = list(scan.columns.values())
    n = scan.data_rows
    refused = rows is not None and rows.failure in ("identity_header_ambiguous", "identity_header_missing")
    out = CoreInspection(key, True, None, layout_supported=not missing and not refused and
                         (bool(buckets) or not spec.get("buckets")),
                         data_rows=n, missing_headers=missing, duplicate_headers=tuple(sorted(scan.duplicate_headers)),
                         rows=rows)
    out.error_cells = sum(s.error + s.formula_error for s in inspected)
    out.missing_cached_formulas = sum(s.formula_missing_cache for s in inspected)
    out.external_links = scan.external_links
    out.external_references = sum(s.external_ref for s in inspected)
    out.ambiguous_headers = len(scan.duplicate_headers)
    out.missing_required_values = sum(scan.columns[h].empty(n) for h in spec["nonblank"] if h in scan.columns)
    amount_cols = [scan.columns[h] for h in spec["amounts"] if h in scan.columns] + buckets
    out.blank_amount_cells = sum(s.empty(n) for s in amount_cols)
    out.zero_amount_cells = sum(s.literal_zero + s.formula_zero for s in amount_cols)
    out.invalid_dates = sum(scan.columns[h].invalid_dates for h in spec["dates"] if h in scan.columns)
    out.columns = {h: s.counts(n) for h, s in scan.columns.items()}
    if buckets:
        found = [(int(m.group(2)), _MONTHS.index(m.group(1)) + 1) for b in buckets for m in [_BUCKET_RE.search(b.header)]
                 if m]
        out.latest_bucket = max(found) if found else None
    return out


# ---------------------------------------------------------------------------------------------
# joins (counts only; nothing is matched by a new rule)
# ---------------------------------------------------------------------------------------------
def _codes(series) -> list[str]:
    return [str(x) for x in series.dropna().tolist()]


def _ws(codes) -> int:
    return sum(1 for c in set(codes) if c != c.strip())


def _trim_collisions(codes) -> int:
    seen: dict[str, set[str]] = {}
    for c in set(codes):
        seen.setdefault(c.strip(), set()).add(c)
    return sum(1 for v in seen.values() if len(v) > 1)


def validate_joins(sales=None, stock=None, aging=None, master=None) -> dict[str, Any]:
    """Privacy-safe join report over the raw frames as read. Codes are compared EXACTLY as recorded (D-003 canonical
    customer = Pro_kodu, else Musterikod). Whitespace variants are counted, never merged."""
    rep: dict[str, Any] = {"master_available": master is not None}
    if sales is not None and {"Pro_kodu", "Musterikod"} <= set(sales.columns):
        pro = sales["Pro_kodu"]
        pk = pro.astype(str).str.strip()
        has_pro = pro.notna() & (pk != "") & (pk.str.lower() != "nan")
        canon = pro.where(has_pro, sales["Musterikod"]).astype(str)
        inv = sales["Musterikod"].astype(str)
        rep["sales_rows"] = int(len(sales))
        rep["order_source_fallback_rows"] = int((~has_pro).sum())                     # D-003 fallback (by design)
        rep["order_source_differs_from_invoice_rows"] = int((has_pro & (pro.astype(str) != inv)).sum())
        pairs = canon.to_frame("c").assign(i=inv)
        per_c = pairs.groupby("c")["i"].nunique()
        per_i = pairs.groupby("i")["c"].nunique()
        rep["order_customers"] = int(per_c.size)
        rep["invoice_accounts"] = int(per_i.size)
        rep["order_customers_with_multiple_invoice_accounts"] = int((per_c > 1).sum())   # one-to-many (DQ-5/DQ-6)
        rep["invoice_accounts_with_multiple_order_customers"] = int((per_i > 1).sum())
        rep["order_codes_with_whitespace"] = _ws(_codes(canon))
        rep["invoice_codes_with_whitespace"] = _ws(_codes(inv))
        rep["order_codes_colliding_after_trim"] = _trim_collisions(_codes(canon))
        if master is not None and "KODU" in master.columns:
            mk = _codes(master["KODU"])
            mset, mtrim = set(mk), {c.strip() for c in mk}
            rep["master_rows"] = len(mk)
            rep["master_duplicate_codes"] = sum(1 for c in set(mk) if mk.count(c) > 1) if len(mk) < 200000 else None
            oc, ic = set(_codes(canon)), set(_codes(inv))
            rep["order_customers_unmatched_in_master"] = sum(1 for c in oc if c not in mset)
            rep["invoice_accounts_unmatched_in_master"] = sum(1 for c in ic if c not in mset)
            rep["matches_only_after_trim"] = sum(1 for c in oc | ic if c not in mset and c.strip() in mtrim)
    if aging is not None and sales is not None and "Musterikod" in sales.columns:
        key = next((c for c in aging.columns if str(c).strip() == "Hesap kodu"), None)
        if key is not None:
            acc = [c for c in _codes(aging[key]) if re.match(r"^\d", c)]
            inv_set = set(_codes(sales["Musterikod"]))
            pro = sales.get("Pro_kodu")
            canon_set = set(_codes(pro)) if pro is not None else set()
            rep["aging_accounts"] = len(set(acc))
            rep["aging_duplicate_accounts"] = len(acc) - len(set(acc))
            rep["aging_accounts_not_in_sales_invoice_accounts"] = sum(1 for c in set(acc) if c not in inv_set)
            rep["aging_accounts_matching_order_code_only"] = sum(1 for c in set(acc)
                                                                 if c not in inv_set and c in canon_set)   # DQ-6
    if stock is not None and sales is not None and "sto_kod" in sales.columns and "Stok_Kodu" in stock.columns:
        sold = set(_codes(sales["sto_kod"]))
        sc = _codes(stock["Stok_Kodu"])
        rep["sold_products"] = len(sold)
        rep["stock_products"] = len(set(sc))
        rep["stock_duplicate_codes"] = len(sc) - len(set(sc))
        rep["sold_products_not_in_stock"] = sum(1 for c in sold if c not in set(sc))
        rep["product_codes_with_whitespace"] = _ws(list(sold) + sc)
        if "StokIsmi" in sales.columns:
            rep["products_with_multiple_names"] = int((sales.groupby(sales["sto_kod"].astype(str))["StokIsmi"]
                                                       .nunique() > 1).sum())
    return rep


# Named cross-source relationships (M8-ACCURACY-2B). A metric declares which of these it USES
# (services/accuracy.Metric.requires); a problem in one relationship never judges a metric that does not use it.
# Nothing here approves a relationship: `validated` is True only when every count ran and found nothing.
JOINS: dict[str, dict[str, Any]] = {
    "sales_product_in_stock": {"title_tr": "Satılan ürün kodu stok raporunda",
                               "unmatched": ("sold_products_not_in_stock",),
                               "ambiguous": ("stock_duplicate_codes", "products_with_multiple_names")},
    "order_customer_in_master": {"title_tr": "Sipariş müşterisi müşteri ana listesinde (D-003)",
                                 "unmatched": ("order_customers_unmatched_in_master",),
                                 "ambiguous": ("master_duplicate_codes", "matches_only_after_trim",
                                               "order_codes_colliding_after_trim")},
    "invoice_account_in_master": {"title_tr": "Fatura hesabı müşteri ana listesinde",
                                  "unmatched": ("invoice_accounts_unmatched_in_master",),
                                  "ambiguous": ("master_duplicate_codes",)},
    "order_customer_invoice_account": {"title_tr": "Sipariş müşterisi ↔ fatura hesabı ilişkisi (DQ-5)",
                                       "unmatched": (),
                                       "ambiguous": ("order_customers_with_multiple_invoice_accounts",
                                                     "invoice_accounts_with_multiple_order_customers")},
    "aging_account_in_sales_invoices": {"title_tr": "Cari hesap satış fatura hesaplarında (DQ-6)",
                                        "unmatched": ("aging_accounts_not_in_sales_invoice_accounts",),
                                        "ambiguous": ("aging_accounts_matching_order_code_only",
                                                      "aging_duplicate_accounts")},
}


def join_checks(rep: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-relationship counts from `validate_joins` (unknown stays None; never approved by being common)."""
    out = {}
    for jid, spec in JOINS.items():
        def total(keys):
            vals = [rep.get(k) for k in keys]
            return None if any(v is None for v in vals) else int(sum(vals))
        u, a = total(spec["unmatched"]), total(spec["ambiguous"])
        out[jid] = {"unmatched": u, "ambiguous": a,
                    "validated": (None if (u is None or a is None) else (u == 0 and a == 0))}
    return out


def join_evidence(rep: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-source join fields for `SourceEvidence`: unmatched / ambiguous counts, and `joins_validated` True only when
    every check ran and found nothing. Unknown (a check that could not run) stays None."""
    def total(keys):
        vals = [rep.get(k) for k in keys]
        return None if any(v is None for v in vals) else int(sum(vals))
    sales_unmatched = total(("order_customers_unmatched_in_master", "invoice_accounts_unmatched_in_master",
                             "sold_products_not_in_stock"))
    sales_amb = total(("order_customers_with_multiple_invoice_accounts", "order_codes_colliding_after_trim",
                       "matches_only_after_trim", "products_with_multiple_names", "master_duplicate_codes"))
    aging_unmatched = total(("aging_accounts_not_in_sales_invoice_accounts",))
    aging_amb = total(("aging_accounts_matching_order_code_only", "aging_duplicate_accounts"))
    stock_unmatched = total(("sold_products_not_in_stock",))
    stock_amb = total(("stock_duplicate_codes",))

    def verdict(u, a):
        if u is None or a is None:
            return None if not (u or a) else False
        return u == 0 and a == 0
    return {"sales": {"join_unmatched": sales_unmatched, "join_ambiguous": sales_amb,
                      "joins_validated": verdict(sales_unmatched, sales_amb)},
            "liste": {"join_unmatched": total(("order_customers_unmatched_in_master",)),
                      "join_ambiguous": total(("master_duplicate_codes",)),
                      "joins_validated": verdict(total(("order_customers_unmatched_in_master",)),
                                                 total(("master_duplicate_codes",)))},
            "aging": {"join_unmatched": aging_unmatched, "join_ambiguous": aging_amb,
                      "joins_validated": verdict(aging_unmatched, aging_amb)},
            "stock": {"join_unmatched": stock_unmatched, "join_ambiguous": stock_amb,
                      "joins_validated": verdict(stock_unmatched, stock_amb)}}


__all__ = ["scan_sheet", "inspect_core", "validate_joins", "join_evidence", "join_checks", "analyze_stock_rows",
           "StockRowAnalysis", "CoreInspection", "SheetScan", "ColumnScan", "CORE", "JOINS"]
