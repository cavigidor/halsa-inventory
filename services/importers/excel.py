"""Formula-aware, read-only workbook access.

Every workbook is opened twice with openpyxl in read-only mode:
- the **formula view** (`data_only=False`) tells literal cells from formula cells;
- the **cached-value view** (`data_only=True`) gives the result Excel saved at its last calculation.
Python never recalculates a formula (openpyxl does not evaluate them either). A formula with no
saved result is `FORMULA_MISSING_CACHE`, never a silent None. Excel error values are classified by
cell type, never coerced to NaN.

External workbooks are detected from the package relationships and from formula text; they are
never opened or followed. Nothing here writes to the file.
"""
from __future__ import annotations

import datetime as dt
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

import openpyxl

from .models import CellKind

EXCEL_ERRORS: frozenset[str] = frozenset({"#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!"})
OTHER_ERROR = "#OTHER"

# `[1]Sheet!A1` (how Excel stores links) or `'C:\x\[Book.xlsx]Sheet'!A1` / `[Book.xlsx]Sheet!A1`.
# Structured table references (`Table1[Col]`, `[@Col]`) are not matched: an external reference's
# bracket follows a formula delimiter or a quote, and holds a number or a workbook file name.
EXTERNAL_REF_RE = re.compile(r"(?:^|[=(,;+\-*/&^<>:'!\s\\])\[(?:\d+|[^\[\]]*\.xl[a-z]{1,2})\]", re.IGNORECASE)

_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_EXTERNAL_LINK_TYPE = "/externalLink"


@dataclass(frozen=True, slots=True)
class CellRead:
    kind: CellKind
    value: Any = None            # cached / literal value; None for empty, missing cache and errors
    error: str | None = None     # normalized Excel error code for ERROR / FORMULA_ERROR
    external_ref: bool = False   # formula references another workbook
    formula: str | None = None   # the formula text for formula cells (never evaluated)


EMPTY_CELL = CellRead(CellKind.EMPTY)


def _error_code(value: Any) -> str:
    text = str(value).strip().upper() if value is not None else ""
    return text if text in EXCEL_ERRORS else OTHER_ERROR


def _formula_text(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("="):
        return value
    text = getattr(value, "text", None)  # ArrayFormula / DataTableFormula
    if isinstance(text, str):
        return text
    return None


def classify(fcell: Any, vcell: Any) -> CellRead:
    """Combine one cell from the formula view and the same cell from the cached-value view."""
    f_value = getattr(fcell, "value", None)
    f_type = getattr(fcell, "data_type", None)
    v_value = getattr(vcell, "value", None)
    v_type = getattr(vcell, "data_type", None)
    # Only a cell typed as a formula counts; a literal text cell that starts with "=" stays literal.
    formula = _formula_text(f_value) if (f_type == "f" or hasattr(f_value, "text")) else None
    if formula is not None:
        ext = bool(EXTERNAL_REF_RE.search(formula))
        if v_type == "e":
            return CellRead(CellKind.FORMULA_ERROR, None, _error_code(v_value), ext, formula)
        if v_value is None:
            return CellRead(CellKind.FORMULA_MISSING_CACHE, None, None, ext, formula)
        return CellRead(CellKind.FORMULA_VALUE, v_value, None, ext, formula)
    if f_type == "e" or v_type == "e":
        return CellRead(CellKind.ERROR, None, _error_code(v_value if v_value is not None else f_value))
    if v_value is None:
        return EMPTY_CELL
    return CellRead(CellKind.LITERAL, v_value)


def label_text(read: CellRead) -> str | None:
    """Exact text of a header/label cell. No trimming, no case folding, no Unicode changes.
    Integers and dates (common in month/year headers) are rendered canonically."""
    v = read.value
    if v is None or read.kind.is_error:
        return None
    if isinstance(v, bool):
        return str(v).upper()
    if isinstance(v, str):
        return v
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time() else v.isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


class SheetPair:
    """One worksheet in both views."""

    def __init__(self, formula_ws: Any, value_ws: Any) -> None:
        self._f = formula_ws
        self._v = value_ws

    def iter_rows(self, min_row: int = 1, max_row: int | None = None, min_col: int = 1,
                  max_col: int | None = None) -> Iterator[tuple[int, list[CellRead]]]:
        """Yield (row_number, cells) in parallel from both views. Missing cells are EMPTY."""
        f_rows = self._f.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col)
        v_rows = self._v.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col)
        row_no = min_row
        for f_row, v_row in _zip_longest(f_rows, v_rows):
            f_row = f_row or ()
            v_row = v_row or ()
            width = max(len(f_row), len(v_row))
            cells = [classify(f_row[i] if i < len(f_row) else None, v_row[i] if i < len(v_row) else None)
                     for i in range(width)]
            yield row_no, cells
            row_no += 1

    def row(self, row: int, max_col: int | None = None) -> list[CellRead]:
        for _, cells in self.iter_rows(min_row=row, max_row=row, max_col=max_col):
            return cells
        return []

    def grid(self, min_row: int, min_col: int, max_row: int, max_col: int) -> dict[tuple[int, int], CellRead]:
        """Read an explicit window into {(row, col): CellRead}. Empty cells are omitted."""
        out: dict[tuple[int, int], CellRead] = {}
        for r, cells in self.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
            for i, c in enumerate(cells):
                if c.kind != CellKind.EMPTY:
                    out[(r, min_col + i)] = c
        return out

    def cell(self, row: int, col: int) -> CellRead:
        return self.grid(row, col, row, col).get((row, col), EMPTY_CELL)


def _zip_longest(a: Iterator, b: Iterator) -> Iterator[tuple[Any, Any]]:
    sentinel = object()
    while True:
        x = next(a, sentinel)
        y = next(b, sentinel)
        if x is sentinel and y is sentinel:
            return
        yield (None if x is sentinel else x), (None if y is sentinel else y)


class WorkbookPair:
    """Context manager: the workbook opened read-only in both views. Never saved.

    `content` (M8-ACCURACY-2): the workbook's bytes as frozen once by the caller. Both views are then parsed from those
    exact bytes, never from the (mutable) file again."""

    def __init__(self, path: str | Path, content: bytes | None = None) -> None:
        self.path = Path(path)
        self._content = content
        self._f = None
        self._v = None

    def _src(self):
        return io.BytesIO(self._content) if self._content is not None else self.path

    def __enter__(self) -> "WorkbookPair":
        self._f = openpyxl.load_workbook(self._src(), read_only=True, data_only=False, keep_links=False)
        self._v = openpyxl.load_workbook(self._src(), read_only=True, data_only=True, keep_links=False)
        return self

    def __exit__(self, *exc: Any) -> None:
        for wb in (self._f, self._v):
            if wb is not None:
                wb.close()

    @property
    def sheet_names(self) -> list[str]:
        return list(self._f.sheetnames)

    def sheet(self, ref: str | int) -> SheetPair | None:
        names = self.sheet_names
        if isinstance(ref, int):
            if not 0 <= ref < len(names):
                return None
            name = names[ref]
        else:
            if ref not in names:
                return None
            name = ref
        f_ws, v_ws = self._f[name], self._v[name]
        # ERP exports often carry a wrong <dimension> tag; read what is really in the sheet.
        for ws in (f_ws, v_ws):
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()
        return SheetPair(f_ws, v_ws)


def sanitize_name(target: str) -> str:
    """Basename of a link target, without directories, scheme or query; safe characters only."""
    parsed = urlparse(target)
    path = unquote(parsed.path or target).replace("\\", "/")
    base = posixpath.basename(path.rstrip("/")) or "external"
    base = re.sub(r"[^\w.\- ]", "_", base, flags=re.UNICODE)
    return base[:80]


def external_link_targets(path: str | Path, content: bytes | None = None) -> list[str]:
    """Sanitized basenames of external workbooks the package links to (from relationships only).

    Raises zipfile.BadZipFile for a non-xlsx file. The linked files are never opened.
    """
    names: list[str] = []
    with zipfile.ZipFile(io.BytesIO(content) if content is not None else path) as z:
        members = set(z.namelist())
        rels = "xl/_rels/workbook.xml.rels"
        link_parts: list[str] = []
        if rels in members:
            root = ET.fromstring(z.read(rels))
            for rel in root.iter(f"{_REL_NS}Relationship"):
                if (rel.get("Type") or "").endswith(_EXTERNAL_LINK_TYPE):
                    target = rel.get("Target") or ""
                    link_parts.append(posixpath.normpath(posixpath.join("xl", target.lstrip("/")))
                                      if not target.startswith("/") else target.lstrip("/"))
        # Also count link parts that exist without a workbook relationship (defensive).
        for m in members:
            if re.fullmatch(r"xl/externalLinks/externalLink\d+\.xml", m) and m not in link_parts:
                link_parts.append(m)
        for part in sorted(set(link_parts)):
            folder, file = posixpath.split(part)
            part_rels = posixpath.join(folder, "_rels", file + ".rels")
            target_name = "external"
            if part_rels in members:
                root = ET.fromstring(z.read(part_rels))
                for rel in root.iter(f"{_REL_NS}Relationship"):
                    if rel.get("TargetMode") == "External" or "externalLinkPath" in (rel.get("Type") or ""):
                        target_name = sanitize_name(rel.get("Target") or "")
                        break
            names.append(target_name)
    return names


# ---------------------------------------------------------------------------------------------
# M8-ACCURACY-2: row visibility and saved AutoFilter, read from the sheet XML (openpyxl's read-only mode does not
# expose them). Read-only; nothing is evaluated or changed.
# ---------------------------------------------------------------------------------------------
_MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_ODR_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def column_index(letters: str) -> int:
    """'A' → 1, 'L' → 12, 'AA' → 27."""
    from openpyxl.utils.cell import column_index_from_string
    return column_index_from_string(letters.upper())


@dataclass(frozen=True)
class SheetLayoutFlags:
    hidden_rows: frozenset[int]          # 1-based row numbers marked hidden (by a filter or manually)
    auto_filter_ref: str | None          # e.g. "A2:W380"
    filter_columns: tuple[int, ...]      # 0-based colId values that carry saved filter criteria
    filtered_rows_marked: bool           # <sheetPr filterMode="1"> or hidden rows inside the filter range


def _sheet_part(z: zipfile.ZipFile, index: int) -> str | None:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = [s for s in wb.iter(f"{_MAIN_NS}sheet")]
    if not 0 <= index < len(sheets):
        return None
    rid = sheets[index].get(f"{_ODR_NS}id")
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    for rel in rels.iter(f"{_REL_NS}Relationship"):
        if rel.get("Id") == rid:
            target = rel.get("Target") or ""
            return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
    return None


def sheet_layout_flags(path: str | Path, sheet_index: int = 0, content: bytes | None = None) -> SheetLayoutFlags:
    """Hidden rows and the saved AutoFilter of one worksheet. Raises on an unreadable package (callers fail closed)."""
    hidden: set[int] = set()
    ref, cols, filter_mode = None, [], False
    with zipfile.ZipFile(io.BytesIO(content) if content is not None else path) as z:
        part = _sheet_part(z, sheet_index)
        if part is None or part not in z.namelist():
            raise KeyError("sheet part")
        xml = z.read(part)                       # read-only; small owner sheets (core sheets never use this)
        for _, el in ET.iterparse(io.BytesIO(xml), events=("end",)):
            tag = el.tag
            if tag == f"{_MAIN_NS}row":
                if el.get("hidden") in ("1", "true"):
                    hidden.add(int(el.get("r")))
                el.clear()
            elif tag == f"{_MAIN_NS}sheetPr":
                filter_mode = el.get("filterMode") in ("1", "true")
            elif tag == f"{_MAIN_NS}autoFilter":
                ref = el.get("ref")
                cols = [int(fc.get("colId")) for fc in el.iter(f"{_MAIN_NS}filterColumn") if fc.get("colId")]
    return SheetLayoutFlags(frozenset(hidden), ref, tuple(sorted(cols)), filter_mode or bool(hidden and ref))
