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
               strip_headers: bool = False, header_match=None) -> SheetScan:
    """One streaming pass. `wanted` headers are matched exactly (or stripped when `strip_headers`), first occurrence —
    the occurrence pandas uses — and a duplicated wanted header is reported. `header_match(label)` may select extra
    columns (e.g. aging buckets)."""
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
                    if r < header_row:
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

    def summary(self) -> dict:
        d = {k: getattr(self, k) for k in ("ok", "failure", "layout_supported", "data_rows", "error_cells",
                                           "missing_cached_formulas", "missing_required_values", "blank_amount_cells",
                                           "zero_amount_cells", "invalid_dates", "external_links",
                                           "external_references", "ambiguous_headers")}
        d["missing_headers"] = list(self.missing_headers)
        d["latest_bucket"] = list(self.latest_bucket) if self.latest_bucket else None
        return d


def inspect_core(key: str, content: bytes) -> CoreInspection:
    """Formula-aware inspection of one core workbook from its frozen bytes. Never raises: an inspection that cannot
    run is a failure (the caller marks the source INVALID), never a clean result."""
    spec = CORE[key]
    try:
        scan = scan_sheet(content, header_row=spec["header_row"], wanted=spec["required"],
                          date_columns=spec["dates"], strip_headers=spec["strip"],
                          header_match=_is_bucket if spec.get("buckets") else None)
    except Exception as e:                                    # unreadable package / XML: fail closed
        return CoreInspection(key, False, f"scan:{type(e).__name__}", None)
    missing = tuple(h for h in spec["required"] if h not in scan.columns)
    buckets = [s for h, s in scan.columns.items() if spec.get("buckets") and _is_bucket(h)]
    inspected = list(scan.columns.values())
    n = scan.data_rows
    out = CoreInspection(key, True, None, layout_supported=not missing and (bool(buckets) or not spec.get("buckets")),
                         data_rows=n, missing_headers=missing)
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


__all__ = ["scan_sheet", "inspect_core", "validate_joins", "join_evidence", "CoreInspection", "SheetScan",
           "ColumnScan", "CORE"]
