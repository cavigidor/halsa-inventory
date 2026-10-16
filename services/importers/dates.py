"""Business-date strategies. The business date is the date the data describes, never the file's
modification time. Header year labels are not authoritative (gider/gelir headers say 2025 in the
2026 file), so a source must declare its strategy explicitly."""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Sequence

from openpyxl.utils.cell import range_boundaries

from .excel import CellRead, SheetPair
from .models import BusinessDate, CellKind, Confidence, DateOrigin, NormalizedRecord, Severity, ValidationReport
from .spec import DateRule, cell_rc

_MONTHS = {
    # Turkish (NFC, lower-cased with Turkish rules applied below) and English
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6, "temmuz": 7,
    "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_BUCKET_RE = re.compile(r"^\s*(?P<month>[^\W\d_]+)[\s\-/]+(?P<year>\d{4})\s*$", re.UNICODE)


def _tr_lower(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("I", "ı").replace("İ", "i")
    return text.lower()


def parse_bucket_label(read: CellRead) -> dt.date | None:
    """`Ekim-2026` / `October 2026` / a date cell → first day of that month."""
    v = read.value
    if isinstance(v, dt.datetime):
        return dt.date(v.year, v.month, 1)
    if isinstance(v, dt.date):
        return dt.date(v.year, v.month, 1)
    if isinstance(v, str):
        m = _BUCKET_RE.match(v)
        if m:
            word = m.group("month")
            month = _MONTHS.get(_tr_lower(word)) or _MONTHS.get(unicodedata.normalize("NFC", word).lower())
            if month:
                return dt.date(int(m.group("year")), month, 1)
    return None


def resolve_business_date(rule: DateRule, *, report: ValidationReport, records: Sequence[NormalizedRecord],
                          sheet: SheetPair | None, basename: str,
                          declared: dt.date | None) -> BusinessDate:
    s = rule.strategy
    if s == "none":
        report.add(Severity.INFO, "business_date_none", "The spec declares no business-date strategy.")
        return BusinessDate(strategy="none")

    if s == "declared":
        if declared is None:
            report.add(Severity.ERROR, "business_date_missing",
                       "This source needs a declared snapshot date at import time.")
            return BusinessDate(strategy=s)
        return BusinessDate(value=declared, strategy=s, origin=DateOrigin.DECLARED)

    if s == "filename":
        m = re.search(rule.filename_regex or "", unicodedata.normalize("NFC", basename))
        try:
            if not m:
                raise ValueError
            day = int(m.groupdict().get("day") or 1)
            value = dt.date(int(m.group("year")), int(m.group("month")), day)
        except (ValueError, TypeError):
            report.add(Severity.ERROR, "business_date_invalid", "The file name does not carry the declared date pattern.")
            return BusinessDate(strategy=s)
        gran = "day" if m.groupdict().get("day") else "month"
        return BusinessDate(value=value, strategy=s, origin=DateOrigin.FILENAME, granularity=gran)

    if s == "column_max":
        dates, formula = [], False
        for rec in records:
            nv = rec.values.get(rule.field or "")
            if nv is not None and isinstance(nv.value, dt.date) and nv.confidence != Confidence.INVALID:
                dates.append(nv.value)
                formula = formula or nv.kind == CellKind.FORMULA_VALUE
        if not dates:
            report.add(Severity.ERROR, "business_date_missing", "The business-date column has no valid date.",
                       field=rule.field)
            return BusinessDate(strategy=s)
        return BusinessDate(value=max(dates), strategy=s,
                            origin=DateOrigin.CACHED_FORMULA if formula else DateOrigin.CONTENT)

    if sheet is None:
        return BusinessDate(strategy=s)

    if s == "snapshot_cell":
        r, c = cell_rc(rule.cell or "")
        read = sheet.cell(r, c)
        v = read.value
        if read.kind in (CellKind.LITERAL, CellKind.FORMULA_VALUE) and isinstance(v, (dt.date, dt.datetime)):
            value = v.date() if isinstance(v, dt.datetime) else v
            origin = DateOrigin.CACHED_FORMULA if read.kind == CellKind.FORMULA_VALUE else DateOrigin.LITERAL
            return BusinessDate(value=value, strategy=s, origin=origin)
        report.add(Severity.ERROR, "business_date_invalid", "The snapshot-date cell does not hold a valid date.",
                   region=rule.cell)
        return BusinessDate(strategy=s)

    if s == "bucket_headers":
        min_col, min_row, max_col, max_row = range_boundaries(rule.cells or "")
        months, bad = [], 0
        for (_, _), read in sorted(sheet.grid(min_row, min_col, max_row, max_col).items()):
            parsed = parse_bucket_label(read)
            if parsed is None:
                bad += 1
            else:
                months.append(parsed)
        if bad:
            report.add(Severity.ERROR, "business_date_invalid",
                       f"{bad} cell(s) in the declared bucket-header range are not month labels.",
                       region=rule.cells, count=bad)
            return BusinessDate(strategy=s)
        if not months:
            report.add(Severity.ERROR, "business_date_missing", "The bucket-header range is empty.", region=rule.cells)
            return BusinessDate(strategy=s)
        return BusinessDate(value=max(months), strategy=s, origin=DateOrigin.CONTENT, granularity="month")

    raise ValueError(f"unknown business-date strategy {s}")
