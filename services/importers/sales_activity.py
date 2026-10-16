"""M6-2 — order activity/source normalization for the main sales report (`Srmmerk`).

Source-specific module on top of the M6-1 framework (`run_import` + `SourceSpec`); it adds no reader of
its own. It turns the sales-order lines of the 2020–2026 sales report into one source attribution per
order document. Docs: docs/m6_order_source.md; decision D-036.

What it decides: the structural parse of `Srmmerk` (edition year, event stem, type, sequence) and the
order-document grain. What it deliberately does NOT decide (owner policy): whether ETKİNLİK / SAHA count
as fairs, any conversion window, revenue basis, salesperson of record. No amounts are imported.

Privacy: normalized in-memory records keep the raw label and stem (local attribution needs them). The
safe summary and the validation report carry counts only — never labels, stems, customer codes or
names; a runtime verification check enforces that.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportResult, run_import
from .entity_resolution import MasterIndex, match_report
from .keys import record_key
from .models import (
    Audience,
    Confidence,
    IdentifierRole,
    MatchReport,
    MatchStatus,
    NormalizedRecord,
    Resolution,
    Severity,
    ValidationReport,
    VerificationResult,
)
from .spec import DateRule, FieldSpec, FieldType, SourceSpec, TableLayout, VerifySpec

# ---------------------------------------------------------------------------------------------
# Source specs
# ---------------------------------------------------------------------------------------------
SALES_FILE_PATTERNS = ("*satış*.xlsx", "*Satış*.xlsx", "*SATIŞ*.xlsx")
CUSTOMER_MASTER = "sales_customer_master"

#: The sales-order lines, only the columns attribution and future linkage need. Deliberately not
#: imported: amounts (revenue basis is an owner decision), `Temsilci` / `Açık / Kapalı` / `Sektör`
#: (invoice-account XLOOKUPs, DQ-5), names. The data sheet is declared by position (index 0) because
#: its name carries the year range and changes every year; the header row and headers are exact.
SALES_LINES_SPEC = SourceSpec(
    key="sales_activity",
    audience=Audience.SHARED,
    filename_patterns=SALES_FILE_PATTERNS,
    sheet=0,
    layout=TableLayout(header_row=1),
    fields=(
        FieldSpec("series", FieldType.OPAQUE_ID, header="sip_evrakno_seri", nullable=False, formulas="forbid"),
        FieldSpec("number", FieldType.INTEGER, header="sip_evrakno_sira", nullable=False, formulas="forbid"),
        FieldSpec("order_customer", FieldType.OPAQUE_ID, header="Pro_kodu", role=IdentifierRole.ORDER_CUSTOMER,
                  master=CUSTOMER_MASTER, formulas="forbid"),
        FieldSpec("invoice_account", FieldType.OPAQUE_ID, header="Musterikod", role=IdentifierRole.INVOICE_ACCOUNT,
                  master=CUSTOMER_MASTER, formulas="forbid"),
        FieldSpec("sku", FieldType.OPAQUE_ID, header="sto_kod", role=IdentifierRole.PRODUCT, formulas="forbid"),
        FieldSpec("source_label", FieldType.STRING, header="Srmmerk", nullable=False, formulas="forbid"),
        FieldSpec("order_date", FieldType.DATE, header="Tarih", formulas="forbid"),
    ),
    default_confidence=Confidence.TRUSTED,   # D-023 / D-034: verified ERP order-line fields
    business_date=DateRule.column_max("order_date"),
    identity_fields=("series", "number", "sku"),
    verify=VerifySpec(min_rows=1),
    description="Main sales report: sales-order lines (order document × SKU), activity/source attribution only.",
)

#: The `liste` sheet of the same workbook: the customer master key space (`KODU`) for both identity roles.
CUSTOMER_MASTER_SPEC = SourceSpec(
    key=CUSTOMER_MASTER,
    audience=Audience.SHARED,
    filename_patterns=SALES_FILE_PATTERNS,
    sheet="liste",
    layout=TableLayout(header_row=1),
    fields=(FieldSpec("code", FieldType.OPAQUE_ID, header="KODU"),),
    default_confidence=Confidence.TRUSTED,
    description="Customer master (liste.KODU), used only as the exact-match key space.",
)


class SourceSelectionError(LookupError):
    """No, or more than one, sales workbook matched the declared patterns (message carries counts only)."""


def find_sales_workbook(folder: str | Path) -> Path:
    """Deterministic selection: exactly one `.xlsx` in `folder` must match the declared patterns.
    Never picks by modification time; M5's own selector is untouched."""
    matches = sorted(p for p in Path(folder).glob("*.xlsx") if SALES_LINES_SPEC.matches_filename(p.name)
                     and not p.name.startswith("~$"))
    if len(matches) != 1:
        raise SourceSelectionError(f"expected exactly one sales workbook, found {len(matches)}")
    return matches[0]


# ---------------------------------------------------------------------------------------------
# Label parser
# ---------------------------------------------------------------------------------------------
class SourceType(str, Enum):
    """Canonical order source types. Kept separate on purpose: whether ETKİNLİK or SAHA count as fairs
    is an owner decision (D-036). There is no `is_fair` flag."""

    GENERAL = "general"
    FUAR = "fuar"
    ETKINLIK = "etkinlik"
    SAHA = "saha"


GENERAL_LABEL = "GENEL"
#: Verified type tokens (M6-0) → canonical type, longest first so the right-hand match is unambiguous.
TYPE_TOKENS: tuple[tuple[str, SourceType], ...] = (
    ("SAHAÇALIŞMASI", SourceType.SAHA),
    ("ETKİNLİĞİ", SourceType.ETKINLIK),
    ("ETKİNLİK", SourceType.ETKINLIK),
    ("FUAR", SourceType.FUAR),
    ("SAHA", SourceType.SAHA),
)
#: Structural sanity bounds for the edition year. Broad on purpose (not the years currently present):
#: future editions parse without code changes; a value outside is a malformed label, not a new year.
MIN_EDITION_YEAR = 2000
MAX_EDITION_YEAR = 2099

_LABEL_RE = re.compile(r"(?P<year>\d{4})(?P<rest>\D.*)", re.DOTALL)
_SEQ_RE = re.compile(r"(?P<body>.*?)(?P<seq>[1-9]\d*)", re.DOTALL)


class LabelError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code   # a fixed code, never the label


@dataclass(frozen=True, slots=True)
class ParsedLabel:
    raw: str
    source_type: SourceType
    edition_year: int | None = None
    stem: str | None = None
    sequence: int | None = None

    @property
    def is_event(self) -> bool:
        return self.source_type is not SourceType.GENERAL

    @property
    def event_id(self) -> str | None:
        """Deterministic event-edition id: year + exact stem + canonical type + sequence."""
        if not self.is_event:
            return None
        return record_key("sales_event", [self.edition_year, self.stem, self.source_type.value, self.sequence])


def parse_label(raw: Any) -> ParsedLabel:
    """Parse one `Srmmerk` value by structural grammar only.

    `GENEL` → general. Otherwise `<YYYY><stem><TYPE>[sequence]`: a four-digit edition year, a non-empty
    stem that does not start with a digit, a verified type token matched from the right (longest
    first), and an optional positive integer without leading zero. Anything else raises `LabelError`.
    No case folding, no trimming, no dictionaries of names; only NFC normalization so that a decomposed
    `İ` / `Ç` still matches the type token. Different stems are never merged.
    """
    if not isinstance(raw, str):
        raise LabelError("label_not_text")
    label = unicodedata.normalize("NFC", raw)
    if label != label.strip():
        raise LabelError("label_whitespace")
    if label == GENERAL_LABEL:
        return ParsedLabel(raw=raw, source_type=SourceType.GENERAL)
    m = _LABEL_RE.fullmatch(label)
    if not m:
        raise LabelError("label_no_year")
    year = int(m.group("year"))
    if not MIN_EDITION_YEAR <= year <= MAX_EDITION_YEAR:
        raise LabelError("label_year_out_of_bounds")
    rest, sequence = m.group("rest"), None
    s = _SEQ_RE.fullmatch(rest)
    if s:
        rest, sequence = s.group("body"), int(s.group("seq"))
    for token, stype in TYPE_TOKENS:
        if rest.endswith(token):
            stem = rest[: -len(token)]
            break
    else:
        raise LabelError("label_unknown_type")
    if not stem.strip():
        raise LabelError("label_no_stem")
    return ParsedLabel(raw=raw, source_type=stype, edition_year=year, stem=stem, sequence=sequence)


# ---------------------------------------------------------------------------------------------
# Normalized order model (in memory only)
# ---------------------------------------------------------------------------------------------
@dataclass(slots=True)
class OrderSource:
    order_key: str                     # record_key("sales_order", [series, number]); never the label
    series: str
    number: int
    source_type: SourceType
    raw_label: str                     # internal only
    edition_year: int | None
    event_stem: str | None             # internal only (may be a partner name)
    event_sequence: int | None
    event_id: str | None
    order_date: dt.date | None         # None when the lines disagree or carry no date
    order_date_status: str             # ok | conflict | missing
    order_customer: str | None         # Pro_kodu (order-source role); None if empty or inconsistent
    invoice_account: str | None        # Musterikod (invoice role); None if empty or inconsistent
    customer: str | None               # canonical D-003 customer; None if inconsistent across lines
    customer_fallback: bool            # True when the canonical customer came from Musterikod
    customer_status: str               # ok | conflict | missing
    customer_link: Resolution | None   # exact match of `customer` against liste.KODU
    line_keys: tuple[str, ...]
    source_confidence: Confidence      # of type / year / stem / sequence

    @property
    def is_event(self) -> bool:
        return self.source_type is not SourceType.GENERAL

    @property
    def is_fuar(self) -> bool:
        """Strict fact only: the label's type token is FUAR. Not a business 'fair' grouping."""
        return self.source_type is SourceType.FUAR

    @property
    def edition_year_offset(self) -> int | None:
        """edition year − order year (independent facts; never corrected from each other)."""
        if self.edition_year is None or self.order_date is None:
            return None
        return self.edition_year - self.order_date.year


def canonical_customer(order_customer: str | None, invoice_account: str | None) -> tuple[str | None, bool]:
    """D-003: the order-source customer (`Pro_kodu`); the invoice account only when `Pro_kodu` is empty
    (absent or whitespace only). A non-empty `Pro_kodu` is used exactly as written. → (customer, fallback)."""
    if order_customer is not None and order_customer.strip() != "":
        return order_customer, False
    return invoice_account, invoice_account is not None


# ---------------------------------------------------------------------------------------------
# Safe summary
# ---------------------------------------------------------------------------------------------
class OrderSourceSummary(BaseModel):
    """Counts only. Order counts are at order-document grain, not sales lines."""

    model_config = ConfigDict(extra="forbid")

    lines: int = 0
    orders: int = 0
    general_orders: int = 0
    event_orders: int = 0
    orders_by_type: dict[str, int] = Field(default_factory=lambda: {t.value: 0 for t in SourceType})
    distinct_labels: int = 0
    distinct_event_stems: int = 0
    distinct_events: int = 0
    orders_with_sequence: int = 0
    parsed_orders: int = 0
    unparsed_orders: int = 0
    unparsed_labels: int = 0
    unparsed_by_code: dict[str, int] = Field(default_factory=dict)
    label_conflict_orders: int = 0
    edition_year_min: int | None = None
    edition_year_max: int | None = None
    edition_years: int = 0
    edition_year_differs: int = 0
    edition_year_offsets: dict[str, int] = Field(default_factory=dict)   # "+1" / "-1" / "other" → orders
    order_date_unusable: int = 0
    customer_fallback_orders: int = 0
    customer_conflict_orders: int = 0
    invoice_account_conflict_orders: int = 0
    multi_line_orders: int = 0
    business_date: dt.date | None = None


@dataclass
class OrderSourceResult:
    ok: bool
    failed_step: str | None
    file: str
    orders: list[OrderSource]
    lines: list[NormalizedRecord]
    report: ValidationReport                 # the sales-lines report plus source-specific issues
    verification: VerificationResult         # source-specific checks (the framework's are in lines_result)
    counts: OrderSourceSummary
    match_reports: list[MatchReport] = field(default_factory=list)
    lines_result: ImportResult | None = None
    master_result: ImportResult | None = None

    def summary(self) -> dict[str, Any]:
        """Safe to print, log or return from a future API: no labels, stems, codes, names, amounts or paths."""
        return {
            "ok": self.ok,
            "failed_step": self.failed_step,
            "source": SALES_LINES_SPEC.key,
            "file": self.file,
            "counts": self.counts.model_dump(mode="json"),
            "report": self.report.to_dict(),
            "framework_verification": (self.lines_result.verification.model_dump(mode="json")
                                       if self.lines_result else None),
            "verification": self.verification.model_dump(mode="json"),
            "match_reports": [m.model_dump(mode="json") for m in self.match_reports],
            "customer_master": (self.master_result.summary()["report"] if self.master_result else None),
        }


# ---------------------------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------------------------
def import_order_sources(path: str | Path, *, master: MasterIndex | None = None,
                         load_master: bool = True) -> OrderSourceResult:
    """validate (M6-1) → parse + group by order document → source-specific checks → verify.

    Source-fatal (ok=False, nothing exposed): any framework error (missing/renamed headers, blank
    `Srmmerk`, missing or malformed order identity, Excel errors or formulas in these columns), an
    unparseable label, or different labels within one order document.
    Record-level (order kept, field unusable, counted): conflicting or missing order dates, conflicting
    customers/invoice accounts across an order's lines, unmatched customers. Edition year ≠ order year
    is valid data (info only).
    """
    path = Path(path)
    master_result = None
    if master is None and load_master:
        master_result = run_import(path, CUSTOMER_MASTER_SPEC)
        if master_result.ok:
            master = MasterIndex.from_result(master_result, ["code"], name=CUSTOMER_MASTER)
    masters: Mapping[str, MasterIndex] = {CUSTOMER_MASTER: master} if master is not None else {}
    lines_result = run_import(path, SALES_LINES_SPEC, masters=masters)
    report = lines_result.report
    counts = OrderSourceSummary(lines=len(lines_result.records), business_date=lines_result.business_date.value)
    result = OrderSourceResult(ok=False, failed_step=lines_result.failed_step, file=path.name, orders=[], lines=[],
                               report=report, verification=VerificationResult(), counts=counts,
                               lines_result=lines_result, master_result=master_result)
    if master is None and load_master:
        report.add(Severity.WARNING, "customer_master_unavailable",
                   "The customer master sheet could not be imported; customers are not resolved.")
    if not lines_result.ok:
        return result

    orders = _group(lines_result.records, counts, report)
    if report.errors:
        result.failed_step = "attribute"
        return result

    if master is not None:
        resolutions = [master.resolve(o.customer) if o.customer is not None else Resolution(MatchStatus.NULL)
                       for o in orders]
        for o, res in zip(orders, resolutions):
            o.customer_link = res
        result.match_reports = list(lines_result.match_reports) + [
            match_report(resolutions, master, field_name="customer", role=IdentifierRole.ORDER_CUSTOMER)]
    else:
        result.match_reports = list(lines_result.match_reports)

    _verify(result, orders, lines_result.records)
    if not result.verification.ok:
        failed = [c.name for c in result.verification.checks if not c.ok]
        report.add(Severity.ERROR, "order_source_verification_failed",
                   "Order-source verification failed: " + ", ".join(failed) + ".", count=len(failed))
        result.failed_step = "verify"
        return result
    result.ok, result.failed_step = True, None
    result.orders, result.lines = orders, lines_result.records
    return result


def _group(records: list[NormalizedRecord], counts: OrderSourceSummary,
           report: ValidationReport) -> list[OrderSource]:
    groups: dict[tuple[str, int], list[NormalizedRecord]] = {}
    for rec in records:
        groups.setdefault((rec.get("series"), rec.get("number")), []).append(rec)

    parsed_cache: dict[str, ParsedLabel | LabelError] = {}
    orders: list[OrderSource] = []
    unparsed_orders, conflict_orders = 0, 0
    unparsed_codes: Counter = Counter()
    date_conflict = date_missing = cust_conflict = inv_conflict = fallback = 0
    offsets: Counter = Counter()

    for (series, number), lines in groups.items():
        order_key = record_key("sales_order", [series, number])
        labels = {r.get("source_label") for r in lines}
        if len(labels) != 1:
            conflict_orders += 1
            continue
        raw = next(iter(labels))
        parsed = parsed_cache.get(raw)
        if parsed is None:
            try:
                parsed = parse_label(raw)
            except LabelError as exc:
                parsed = exc
            parsed_cache[raw] = parsed
        if isinstance(parsed, LabelError):
            unparsed_orders += 1
            unparsed_codes[parsed.code] += 1
            continue

        dates = {r.get("order_date") for r in lines}
        if None in dates and len(dates) == 1:
            order_date, date_status = None, "missing"
            date_missing += 1
        elif len(dates) != 1:
            order_date, date_status = None, "conflict"
            date_conflict += 1
        else:
            order_date, date_status = next(iter(dates)), "ok"

        pros = {r.get("order_customer") for r in lines}
        invs = {r.get("invoice_account") for r in lines}
        order_customer = next(iter(pros)) if len(pros) == 1 else None
        invoice_account = next(iter(invs)) if len(invs) == 1 else None
        if len(invs) > 1:
            inv_conflict += 1
        canon = {canonical_customer(r.get("order_customer"), r.get("invoice_account")) for r in lines}
        if len(canon) == 1:
            customer, used_fallback = next(iter(canon))
            customer_status = "ok" if customer is not None else "missing"
        else:
            customer, used_fallback, customer_status = None, False, "conflict"
            cust_conflict += 1
        fallback += used_fallback

        o = OrderSource(
            order_key=order_key, series=series, number=number, source_type=parsed.source_type, raw_label=raw,
            edition_year=parsed.edition_year, event_stem=parsed.stem, event_sequence=parsed.sequence,
            event_id=parsed.event_id, order_date=order_date, order_date_status=date_status,
            order_customer=order_customer, invoice_account=invoice_account, customer=customer,
            customer_fallback=used_fallback, customer_status=customer_status, customer_link=None,
            line_keys=tuple(r.key for r in lines), source_confidence=Confidence.TRUSTED)
        off = o.edition_year_offset
        if off:
            offsets["+1" if off == 1 else "-1" if off == -1 else "other"] += 1
        orders.append(o)

    # Counts (orders, not lines).
    counts.orders = len(groups)
    counts.multi_line_orders = sum(1 for lines in groups.values() if len(lines) > 1)
    counts.label_conflict_orders = conflict_orders
    counts.unparsed_orders = unparsed_orders
    counts.unparsed_labels = sum(1 for p in parsed_cache.values() if isinstance(p, LabelError))
    counts.unparsed_by_code = dict(sorted(unparsed_codes.items()))
    counts.parsed_orders = len(orders)
    by_type = Counter(o.source_type.value for o in orders)
    counts.orders_by_type = {t.value: by_type.get(t.value, 0) for t in SourceType}
    counts.general_orders = by_type.get(SourceType.GENERAL.value, 0)
    counts.event_orders = len(orders) - counts.general_orders
    counts.distinct_labels = len({r.get("source_label") for r in records})
    events = [o for o in orders if o.is_event]
    counts.distinct_event_stems = len({o.event_stem for o in events})
    counts.distinct_events = len({o.event_id for o in events})
    counts.orders_with_sequence = sum(1 for o in events if o.event_sequence is not None)
    years = [o.edition_year for o in events if o.edition_year is not None]
    counts.edition_year_min, counts.edition_year_max = (min(years), max(years)) if years else (None, None)
    counts.edition_years = len(set(years))
    counts.edition_year_differs = sum(offsets.values())
    counts.edition_year_offsets = dict(sorted(offsets.items()))
    counts.order_date_unusable = date_conflict + date_missing
    counts.customer_fallback_orders = fallback
    counts.customer_conflict_orders = cust_conflict
    counts.invoice_account_conflict_orders = inv_conflict

    # Issues: messages are fixed templates with counts.
    if conflict_orders:
        report.add(Severity.ERROR, "source_label_conflict",
                   f"{conflict_orders} order document(s) carry more than one source label; attribution is ambiguous.",
                   field="source_label", count=conflict_orders)
    if unparsed_orders:
        detail = ", ".join(f"{k} ×{v}" for k, v in sorted(unparsed_codes.items()))
        report.add(Severity.ERROR, "source_label_unparsed",
                   f"{counts.unparsed_labels} distinct source label(s) on {unparsed_orders} order(s) do not match the "
                   f"approved grammar ({detail}); update the importer deliberately.",
                   field="source_label", count=unparsed_orders)
    if date_conflict:
        report.add(Severity.WARNING, "order_date_conflict",
                   f"{date_conflict} order(s) have different dates on their lines; their order date is unusable.",
                   field="order_date", count=date_conflict)
    if date_missing:
        report.add(Severity.WARNING, "order_date_missing", f"{date_missing} order(s) have no date.",
                   field="order_date", count=date_missing)
    if cust_conflict:
        report.add(Severity.WARNING, "order_customer_conflict",
                   f"{cust_conflict} order(s) have different customers on their lines; the order customer is unusable.",
                   field="order_customer", count=cust_conflict)
    if inv_conflict:
        report.add(Severity.INFO, "invoice_account_conflict",
                   f"{inv_conflict} order(s) are invoiced to more than one account.",
                   field="invoice_account", count=inv_conflict)
    if fallback:
        report.add(Severity.INFO, "customer_fallback",
                   f"{fallback} order(s) have no order-source customer; D-003 falls back to the invoice account.",
                   field="order_customer", count=fallback)
    if counts.edition_year_differs:
        report.add(Severity.INFO, "edition_year_differs",
                   f"{counts.edition_year_differs} event order(s) are dated in a different year than the event "
                   "edition (valid data; not corrected).", field="source_label", count=counts.edition_year_differs)
    return orders


def _verify(result: OrderSourceResult, orders: list[OrderSource], lines: list[NormalizedRecord]) -> None:
    v, c = result.verification, result.counts
    v.add("orders_reconcile", c.parsed_orders == c.orders == len(orders), f"{c.parsed_orders} of {c.orders}")
    v.add("type_counts_reconcile", sum(c.orders_by_type.values()) == c.orders)
    v.add("lines_reconcile", sum(len(o.line_keys) for o in orders) == len(lines) == c.lines,
          f"{sum(len(o.line_keys) for o in orders)} of {len(lines)}")
    keys = [o.order_key for o in orders]
    v.add("order_keys_unique", len(keys) == len(set(keys)))
    line_keys = [k for o in orders for k in o.line_keys]
    v.add("line_keys_unique", len(line_keys) == len(set(line_keys)))
    shape_ok = all(
        (o.edition_year is not None and bool(o.event_stem) and o.event_id is not None) if o.is_event
        else (o.edition_year is None and o.event_stem is None and o.event_sequence is None and o.event_id is None)
        for o in orders)
    v.add("event_fields_consistent", shape_ok)
    v.add("source_confidence_trusted", all(o.source_confidence is Confidence.TRUSTED for o in orders))
    # Privacy guard: no raw label or event stem may appear in the safe summary.
    blob = json.dumps(result.summary(), ensure_ascii=False, default=str)
    sensitive = {o.raw_label for o in orders if o.is_event} | {o.event_stem.strip() for o in orders if o.event_stem}
    leaked = sum(1 for s in sensitive if len(s) >= 3 and s in blob)
    v.add("summary_has_no_labels", leaked == 0, f"{leaked} leak(s)")
