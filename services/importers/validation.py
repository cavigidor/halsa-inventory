"""Cell normalization, header resolution and safe structural statistics.

All issue messages are fixed templates with counts, spec field names and cell/column positions.
No function here ever puts a cell value into a message, a region or a statistic (except date
min/max, which is source freshness).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from openpyxl.utils.cell import get_column_letter

from .excel import CellRead, label_text
from .models import CellKind, Confidence, FieldStats, NormalizedValue, Severity
from .spec import FieldSpec, FieldType, Header

# Validation codes stored on NormalizedValue.error (never the value itself).
TYPE_MISMATCH = "type_mismatch"
ID_FLOAT = "id_float"
ID_MALFORMED = "id_malformed"
ID_PLACEHOLDER = "id_placeholder"
MISSING_CACHE = "missing_cache"

_INT_SAFE = 2 ** 53


@dataclass(frozen=True, slots=True)
class Normalized:
    nv: NormalizedValue
    note: str | None = None   # a non-invalidating observation (e.g. "id_numeric_cell")


def _shape(text: str) -> str:
    s = re.sub(r"\d", "9", text)
    s = re.sub(r"[^\W\d_]", "A", s, flags=re.UNICODE)
    return s if len(s) <= 24 else s[:24] + "…"


def _convert(f: FieldSpec, v: Any) -> tuple[Any, str | None, str | None]:
    """→ (value, invalid_code, note). Strict: no locale parsing, no guessing of serial dates."""
    t = f.type
    if t == FieldType.ANY:
        return v, None, None
    if t == FieldType.OPAQUE_ID:
        if isinstance(v, bool) or isinstance(v, (dt.date, dt.datetime)):
            return None, TYPE_MISMATCH, None
        note = None
        if isinstance(v, float):
            # A float id has already lost information (leading zeros, digits beyond 2**53).
            return None, ID_FLOAT, None
        if isinstance(v, int):
            if abs(v) >= _INT_SAFE:
                return None, ID_FLOAT, None
            v, note = str(v), "id_numeric_cell"
        if not isinstance(v, str):
            return None, TYPE_MISMATCH, None
        for rx in f.placeholders:
            if re.fullmatch(rx, v):
                return None, ID_PLACEHOLDER, None
        if f.pattern and not re.fullmatch(f.pattern, v):
            return None, ID_MALFORMED, None
        return v, None, note
    if t == FieldType.STRING:
        if isinstance(v, str):
            return v, None, None
        if isinstance(v, int) and not isinstance(v, bool):
            return str(v), None, None
        return None, TYPE_MISMATCH, None
    if t == FieldType.INTEGER:
        if isinstance(v, bool):
            return None, TYPE_MISMATCH, None
        if isinstance(v, int):
            return v, None, None
        if isinstance(v, float) and v.is_integer():
            return int(v), None, None
        return None, TYPE_MISMATCH, None
    if t == FieldType.DECIMAL:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, TYPE_MISMATCH, None
        return float(v), None, None
    if t == FieldType.DATE:
        if isinstance(v, dt.datetime):
            return v.date(), None, None
        if isinstance(v, dt.date):
            return v, None, None
        return None, TYPE_MISMATCH, None
    if t == FieldType.BOOLEAN:
        return (v, None, None) if isinstance(v, bool) else (None, TYPE_MISMATCH, None)
    return None, TYPE_MISMATCH, None


def normalize_cell(f: FieldSpec, cell: CellRead, declared: Confidence) -> Normalized:
    """One cell → NormalizedValue. Excel errors and missing caches become `invalid` with a code,
    never a plain None. Successful parsing keeps the declared confidence (never upgrades it).
    `external_ref` is carried through so a source can apply its own external-dependency policy."""
    kind, ext = cell.kind, cell.external_ref
    if kind.is_error:
        return Normalized(NormalizedValue(None, Confidence.INVALID, kind, cell.error, ext))
    if kind == CellKind.FORMULA_MISSING_CACHE:
        return Normalized(NormalizedValue(None, Confidence.INVALID, kind, MISSING_CACHE, ext))
    if kind == CellKind.EMPTY:
        return Normalized(NormalizedValue(None, declared, kind))
    v = cell.value
    if isinstance(v, str):
        if f.trim:
            v = v.strip()
        if v == "":
            return Normalized(NormalizedValue(None, declared, kind, None, ext))
    value, invalid, note = _convert(f, v)
    if invalid:
        return Normalized(NormalizedValue(None, Confidence.INVALID, kind, invalid, ext))
    conf = declared
    if note == "id_numeric_cell" and conf == Confidence.TRUSTED:
        conf = Confidence.UNVERIFIED  # cannot prove leading zeros survived
    return Normalized(NormalizedValue(value, conf, kind, None, ext), note)


@dataclass
class FieldAccumulator:
    """Collects safe structural statistics for one field. Values are used only to count distinct
    entries; they never leave this object."""

    spec: FieldSpec
    declared: Confidence
    rows: int = 0
    filled: int = 0
    formula: int = 0
    errors: Counter = field(default_factory=Counter)
    missing_cache: int = 0
    invalid: Counter = field(default_factory=Counter)  # validation code → count (incl. Excel errors)
    notes: Counter = field(default_factory=Counter)
    kinds: Counter = field(default_factory=Counter)
    _seen: set = field(default_factory=set)
    valid_filled: int = 0
    length_min: int | None = None
    length_max: int | None = None
    shapes: Counter = field(default_factory=Counter)
    date_min: dt.date | None = None
    date_max: dt.date | None = None

    def add(self, cell: CellRead, n: Normalized) -> None:
        self.rows += 1
        nv = n.nv
        if cell.kind.is_formula:
            self.formula += 1
        if cell.kind.is_error:
            self.errors[cell.error] += 1
            self.kinds["error"] += 1
        elif cell.kind == CellKind.FORMULA_MISSING_CACHE:
            self.missing_cache += 1
            self.kinds["missing_cache"] += 1
        elif cell.kind != CellKind.EMPTY:
            self.kinds[type(cell.value).__name__] += 1
        if n.note:
            self.notes[n.note] += 1
        if nv.error is not None:
            self.filled += 1
            self.invalid[nv.error] += 1
            return
        if nv.value is None:
            return
        self.filled += 1
        self.valid_filled += 1
        self._seen.add(nv.value)
        v = nv.value
        if isinstance(v, str):
            ln = len(v)
            self.length_min = ln if self.length_min is None else min(self.length_min, ln)
            self.length_max = ln if self.length_max is None else max(self.length_max, ln)
            if self.spec.type == FieldType.OPAQUE_ID:
                self.shapes[_shape(v)] += 1
        elif isinstance(v, dt.date):
            self.date_min = v if self.date_min is None else min(self.date_min, v)
            self.date_max = v if self.date_max is None else max(self.date_max, v)

    @property
    def invalid_count(self) -> int:
        return sum(self.invalid.values())

    @property
    def excel_error_count(self) -> int:
        return sum(self.errors.values())

    @property
    def cell_invalid_count(self) -> int:
        """Invalid cells other than missing caches (those follow their own severity)."""
        return self.invalid_count - self.invalid.get(MISSING_CACHE, 0) - self.invalid.get(ID_PLACEHOLDER, 0)

    def stats(self, confidence: Confidence) -> FieldStats:
        null = self.rows - self.filled
        return FieldStats(
            field=self.spec.name,
            type=self.spec.type.value,
            confidence=confidence,
            rows=self.rows,
            filled=self.filled,
            null=null,
            fill_rate=round(self.filled / self.rows, 4) if self.rows else 0.0,
            distinct=len(self._seen),
            duplicates=self.valid_filled - len(self._seen),
            kinds=dict(sorted(self.kinds.items())),
            formula_count=self.formula,
            error_count=self.excel_error_count,
            errors_by_type=dict(sorted(self.errors.items())),
            missing_cache_count=self.missing_cache,
            invalid_count=self.invalid_count,
            length_min=self.length_min,
            length_max=self.length_max,
            shapes=dict(self.shapes.most_common(5)) if self.spec.type == FieldType.OPAQUE_ID else {},
            date_min=self.date_min,
            date_max=self.date_max,
        )


# ---------------------------------------------------------------------------------------------
# Header resolution (table layout)
# ---------------------------------------------------------------------------------------------
@dataclass
class HeaderMap:
    columns: dict[str, int]                         # field name → 1-based column
    duplicates: dict[str, list[int]]                # raw header → columns (internal only)
    problems: list[tuple[Severity, str, str, str | None, str]]  # (severity, code, field, region, message)
    unmapped: int = 0


def resolve_headers(header_cells: list[CellRead], fields: tuple[FieldSpec, ...]) -> HeaderMap:
    """Exact match on the raw header text, by (name, occurrence). No trimming, no case folding,
    no Unicode normalization, no pandas-style suffixes."""
    by_name: dict[str, list[int]] = {}
    for i, c in enumerate(header_cells, start=1):
        text = label_text(c)
        if text is None or text == "":
            continue
        by_name.setdefault(text, []).append(i)
    duplicates = {k: v for k, v in by_name.items() if len(v) > 1}
    columns: dict[str, int] = {}
    problems: list[tuple[Severity, str, str, str | None, str]] = []
    for f in fields:
        h: Header = f.header  # type: ignore[assignment]
        cols = by_name.get(h.name, [])
        if not cols or (h.occurrence is not None and h.occurrence > len(cols)):
            sev = Severity.ERROR if f.required else Severity.WARNING
            code = "header_missing" if f.required else "header_missing_optional"
            msg = ("Required header not found at the declared header row."
                   if f.required else "Optional header not found at the declared header row; field unusable.")
            problems.append((sev, code, f.name, None, msg))
            continue
        if len(cols) > 1 and h.occurrence is None:
            problems.append((Severity.ERROR, "ambiguous_header", f.name,
                             ",".join(get_column_letter(c) for c in cols),
                             f"Header appears {len(cols)} times; the spec must select an occurrence."))
            continue
        columns[f.name] = cols[(h.occurrence or 1) - 1]
    mapped = set(columns.values())
    unmapped = sum(1 for cols in by_name.values() for c in cols if c not in mapped)
    return HeaderMap(columns=columns, duplicates=duplicates, problems=problems, unmapped=unmapped)
