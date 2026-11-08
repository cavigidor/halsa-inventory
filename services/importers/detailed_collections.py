"""Evrak Detaylı Tahsilat Raporu importer — open receivable documents per account (M6-4, D-081).

Source module on the M6-1 framework (`run_import` + `SourceSpec`); finance-restricted like account-level Cari balances
(D-075): never for the general report, salesperson views, actions, evidence packets or AI.

Verified layout (`dd.mm.yyyy Evrak Detaylı Tahsilat Raporu.xlsx`, first sheet; a second sheet `Kolon Açıklamaları`
describes the columns and is not data): 23 exact headers in row 1, one row per open document (line), from row 2.
`Temsilci kodu` occurs twice (D = the customer card's representative, Q = the document's); both are read by occurrence.

Meaning, as the source's own column descriptions state it (verified arithmetically, never assumed):
- `Vade tarih` = `Evrak tarihi` + `Vade gün` (the invoice's own due date);
- column J, headed `<dd.mm.yyyy> için vade`, = `Vade tarih` − that date (negative = the invoice due date has passed);
- `Sipariş ile Vade Tarihi  Gün Farkı` (W) = `Sipariş Tarihi` + `Sipariş Vadesi` − the report date: the ORDER's
  payment term, i.e. the agreement recorded on the sales order. Negative = that agreed term has passed;
- `Meblağ` = the open amount still to be collected (≤ `Evrak tutarı`); `Ana döviz TL bakiye` = its TL equivalent;
  `Satır Bakiye` = the account's running open balance in file order (informational).
- The workbook's only conditional format colours `Hesap kodu` white-on-red when W < 0: the "red entries". The rule is
  read from the workbook and reported; a row is marked red only by that same rule and only when an order date exists
  (without one the export writes W = 0 and the order-based due date is unknown, never "due today").

Report date: the file name's leading `dd.mm.yyyy` and the J header's date must be the same day (else fail closed).
Nothing here certifies an overdue amount, a collection priority or a customer risk: the order term is the source's
recorded agreement, not yet confirmed by the owner as the binding one (D-071 stays in force for the Cari aging).
Safe summaries carry counts only.
"""
from __future__ import annotations

import datetime as dt
import io
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportResult, run_import
from .keys import record_key
from .models import Audience, Confidence, Severity, ValidationReport, VerificationResult
from .spec import FieldSpec, FieldType, Header, RowRule, SourceSpec, TableLayout, VerifySpec

FILE_PATTERNS = ("*Evrak Detaylı Tahsilat Raporu*.xlsx", "*Evrak Detayli Tahsilat Raporu*.xlsx",
                 "*evrak detaylı tahsilat raporu*.xlsx", "*EVRAK DETAYLI TAHSİLAT RAPORU*.xlsx")
FILENAME_DATE = re.compile(r"^(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})\b")
J_HEADER = re.compile(r"^(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4}) için vade$")
ORDER_DUE_HEADER = "Sipariş ile Vade Tarihi  Gün Farkı"
RED_RULE = 'AND($A2<>"",$W2<0)'
KNOWN_KINDS = frozenset({"Genel"})
TOL = 0.01

_T, _U = Confidence.TRUSTED, Confidence.UNVERIFIED


def _f(name, type_, header, conf=_U, *, nullable=True, pattern=None):
    return FieldSpec(name, type_, header=header, confidence=conf, nullable=nullable, pattern=pattern, formulas="warn")


def _rule(name, fields, check, description):
    return RowRule(name, fields, check, Severity.WARNING, description)


def build_spec(j_header: str) -> SourceSpec:
    """The exact spec; the J header carries the report date and is therefore part of each file's spec."""
    return SourceSpec(
        key="detailed_collections",
        audience=Audience.OWNER,          # finance-restricted (D-075); see the module docstring
        filename_patterns=FILE_PATTERNS,
        sheet=0,
        layout=TableLayout(header_row=1),
        fields=(
            _f("account_code", FieldType.OPAQUE_ID, "Hesap kodu", _T, nullable=False, pattern=r"\d{3}(\.\d+)+"),
            _f("account_name", FieldType.STRING, "Hesap adı"),
            _f("group_code", FieldType.STRING, "Grup kodu"),
            _f("card_rep", FieldType.STRING, Header("Temsilci kodu", 1)),
            _f("document_no", FieldType.OPAQUE_ID, "Evrak no", _T, nullable=False),
            _f("legal_document_no", FieldType.OPAQUE_ID, "Belge no"),
            _f("document_date", FieldType.DATE, "Evrak tarihi", _T, nullable=False),
            _f("due_date", FieldType.DATE, "Vade tarih", _T, nullable=False),
            _f("term_days", FieldType.INTEGER, "Vade gün", _T, nullable=False),
            _f("days_to_due", FieldType.INTEGER, j_header, _T, nullable=False),
            _f("open_amount", FieldType.DECIMAL, "Meblağ", _T, nullable=False),
            _f("kind", FieldType.STRING, "Türü"),
            _f("document_total", FieldType.DECIMAL, "Evrak tutarı", _T, nullable=False),
            _f("running_balance", FieldType.DECIMAL, "Satır Bakiye"),
            _f("currency", FieldType.STRING, "Orj. döviz", _T, nullable=False),
            _f("open_amount_tl", FieldType.DECIMAL, "Ana döviz TL bakiye", _T, nullable=False),
            _f("document_rep", FieldType.STRING, Header("Temsilci kodu", 2)),
            _f("document_rep_name", FieldType.STRING, "Temsilci adı"),
            _f("project_code", FieldType.OPAQUE_ID, "Proje kodu"),
            _f("order_no", FieldType.OPAQUE_ID, "Sipariş No"),
            _f("order_date", FieldType.DATE, "Sipariş Tarihi"),
            _f("order_term_days", FieldType.INTEGER, "Sipariş Vadesi"),
            _f("days_to_order_due", FieldType.INTEGER, ORDER_DUE_HEADER),
        ),
        default_confidence=_U,
        identity_fields=("account_code", "document_no"),
        rules=(
            _rule("due_is_document_date_plus_term", ("due_date", "document_date", "term_days"),
                  lambda v: v["due_date"] == v["document_date"] + dt.timedelta(days=int(v["term_days"])),
                  "Vade tarih = Evrak tarihi + Vade gün"),
            _rule("open_amount_positive", ("open_amount",), lambda v: float(v["open_amount"]) > 0, "Meblağ > 0"),
            _rule("open_not_above_total", ("open_amount", "document_total"),
                  lambda v: float(v["open_amount"]) <= float(v["document_total"]) + TOL, "Meblağ ≤ Evrak tutarı"),
        ),
        verify=VerifySpec(min_rows=1),
        allow_warnings=True,
        description="ERP 'Evrak Detaylı Tahsilat Raporu': one row per open receivable document line.",
    )


@dataclass(slots=True)
class CollectionDocument:
    record_key: str
    source_row: int
    account_code: str
    account_name: str | None
    card_rep: str | None
    document_rep: str | None
    document_rep_name: str | None
    group_code: str | None
    project_code: str | None
    document_no: str
    legal_document_no: str | None
    document_date: dt.date
    due_date: dt.date
    term_days: int
    days_to_due_source: int
    days_to_due: int                       # recomputed from the report date
    open_amount: float
    document_total: float
    currency: str
    open_amount_tl: float
    running_balance: float | None
    kind: str | None
    order_no: str | None
    order_date: dt.date | None
    order_term_days: int | None
    order_due_date: dt.date | None         # order date + order term; None without an order date
    days_to_order_due: int | None          # None without an order date (the source writes 0)
    days_to_order_due_source: int | None
    source_red: bool                       # the workbook's own red rule (W < 0), only with an order date
    invoice_due_passed: bool               # days_to_due < 0
    partially_open: bool                   # open amount < document total


class CollectionsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_date: dt.date | None = None
    report_date_origin: str | None = None
    filename_date: dt.date | None = None
    header_date: dt.date | None = None
    rows_read: int = 0
    documents: int = 0
    accounts: int = 0
    distinct_document_numbers: int = 0
    repeated_document_numbers: int = 0
    blank_legal_document_numbers: int = 0
    non_tl_rows: int = 0
    currencies: list[str] = Field(default_factory=list)
    unknown_kinds: int = 0
    rows_without_order_date: int = 0
    due_date_rule_failed: int = 0
    days_to_due_mismatch: int = 0
    days_to_order_due_mismatch: int = 0
    open_amount_not_positive: int = 0
    open_above_total: int = 0
    tl_amount_differs_for_tl: int = 0
    partially_open_documents: int = 0
    running_balance_accounts_inconsistent: int = 0
    invoice_due_passed_rows: int = 0
    source_red_rule: str | None = None
    source_red_rule_found: bool = False
    source_red_rows: int = 0
    source_red_accounts: int = 0
    order_term_differs_from_invoice_term: int = 0
    certified: bool = False


@dataclass
class DetailedCollectionsResult:
    ok: bool
    failed_step: str | None
    file: str
    documents: list[CollectionDocument]
    report: ValidationReport
    verification: VerificationResult
    counts: CollectionsSummary
    import_result: ImportResult | None = None
    issues_by_account: dict[str, list[str]] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        """Safe: counts and structure only (no codes, names, amounts or paths)."""
        return {"ok": self.ok, "failed_step": self.failed_step, "source": "detailed_collections", "file": self.file,
                "counts": self.counts.model_dump(mode="json"), "report": self.report.to_dict(),
                "verification": self.verification.model_dump(mode="json")}


# ---------------------------------------------------------------------------------------------
def _date_from(m) -> dt.date | None:
    if not m:
        return None
    try:
        return dt.date(int(m.group("year")), int(m.group("month")), int(m.group("day")))
    except ValueError:
        return None


def _probe(content: bytes) -> tuple[str | None, list[str], bool]:
    """Row-1 headers of the first sheet and whether its conditional formats contain the known red rule."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False)   # conditional formats need the full reader
    try:
        ws = wb.worksheets[0]
        head = [None if c.value is None else str(c.value) for c in ws[1]]
        red = False
        for cf in ws.conditional_formatting:
            if any(str(r).startswith("A") for r in cf.sqref.ranges) and any(
                    RED_RULE in (rule.formula or []) for rule in cf.rules):
                red = True
        return (head[9] if len(head) > 9 else None), head, red
    finally:
        wb.close()


def _failed(path: Path, report: ValidationReport, counts: CollectionsSummary, step: str,
            res: ImportResult | None = None) -> DetailedCollectionsResult:
    v = VerificationResult()
    v.add("imported", False, step)
    return DetailedCollectionsResult(False, step, path.name, [], report, v, counts, import_result=res)


def import_detailed_collections(path: str | Path, *, content: bytes | None = None) -> DetailedCollectionsResult:
    """Import one explicitly chosen detailed-collections export. Fail-closed on layout, schema, identity and a report
    date that the file name and the J header do not agree on. Arithmetic deviations are reported per check (rows kept
    as recorded, never repaired); the order-based due date is unknown without an order date."""
    path = Path(path)
    counts = CollectionsSummary()
    report = ValidationReport(source="detailed_collections", audience=Audience.OWNER, file=path.name, sheet="#0",
                              layout="table")
    if content is None:
        try:
            content = path.read_bytes()
        except OSError:
            report.add(Severity.ERROR, "unreadable_workbook", "The workbook could not be read.")
            return _failed(path, report, counts, "validate")
    try:
        j_header, head, red_rule = _probe(content)
    except Exception:                                                     # noqa: BLE001
        report.add(Severity.ERROR, "unreadable_workbook", "The workbook could not be opened as an .xlsx package.")
        return _failed(path, report, counts, "validate")
    counts.header_date = _date_from(J_HEADER.match(j_header or ""))
    counts.filename_date = _date_from(FILENAME_DATE.match(unicodedata.normalize("NFC", path.name)))
    if counts.header_date is None:
        report.add(Severity.ERROR, "report_date_header_missing",
                   "Column J must be headed '<dd.mm.yyyy> için vade' (the report date); it is not.")
        return _failed(path, report, counts, "validate")
    if counts.filename_date is not None and counts.filename_date != counts.header_date:
        report.add(Severity.ERROR, "report_date_conflict",
                   "The file name's date and column J's date differ; the report date is ambiguous.")
        return _failed(path, report, counts, "validate")
    counts.report_date = counts.header_date
    counts.report_date_origin = "filename+header" if counts.filename_date else "header"
    if counts.filename_date is None:
        report.add(Severity.WARNING, "report_date_from_header_only",
                   "The file name carries no date; the report date comes from column J's header.")
    counts.source_red_rule_found = red_rule
    counts.source_red_rule = "W<0 (Sipariş ile Vade Tarihi Gün Farkı)" if red_rule else None
    if not red_rule:
        report.add(Severity.WARNING, "source_red_rule_not_found",
                   "The workbook's red marker rule (column A when W < 0) was not found; rows are not marked red.")

    res = run_import(path, build_spec(j_header), content=content)
    for issue in res.report.issues:
        report.issues.append(issue)
    report.rows_read = res.report.rows_read
    counts.rows_read = res.report.rows_read
    if not res.ok:
        return _failed(path, report, counts, res.failed_step or "validate", res)

    rep = counts.report_date
    docs: list[CollectionDocument] = []
    per_issue: dict[str, set] = defaultdict(set)
    occ: Counter = Counter()
    for r in res.records:
        g = r.get
        acct, docno = str(g("account_code")), str(g("document_no"))
        occ[(acct, docno)] += 1
        order_date = g("order_date")
        term = g("order_term_days")
        order_due = order_date + dt.timedelta(days=int(term)) if (order_date is not None and term is not None) else None
        w_src = g("days_to_order_due")
        w = (order_due - rep).days if order_due is not None else None
        j = (g("due_date") - rep).days
        open_amt, total, tl = float(g("open_amount")), float(g("document_total")), float(g("open_amount_tl"))
        d = CollectionDocument(
            record_key=record_key("collection_document", [acct, docno], occ[(acct, docno)]), source_row=r.row,
            account_code=acct, account_name=g("account_name"), card_rep=g("card_rep"), document_rep=g("document_rep"),
            document_rep_name=(g("document_rep_name") or "").strip() or None, group_code=g("group_code"),
            project_code=g("project_code"), document_no=docno, legal_document_no=g("legal_document_no"),
            document_date=g("document_date"), due_date=g("due_date"), term_days=int(g("term_days")),
            days_to_due_source=int(g("days_to_due")), days_to_due=j, open_amount=open_amt, document_total=total,
            currency=str(g("currency")), open_amount_tl=tl,
            running_balance=None if g("running_balance") is None else float(g("running_balance")), kind=g("kind"),
            order_no=g("order_no"), order_date=order_date, order_term_days=None if term is None else int(term),
            order_due_date=order_due, days_to_order_due=w,
            days_to_order_due_source=None if w_src is None else int(w_src),
            source_red=bool(red_rule and order_due is not None and w_src is not None and int(w_src) < 0),
            invoice_due_passed=j < 0, partially_open=open_amt < total - TOL)
        docs.append(d)
        if d.days_to_due != d.days_to_due_source:
            counts.days_to_due_mismatch += 1
            per_issue["days_to_due_mismatch"].add(acct)
        if order_due is not None and w_src is not None and w != int(w_src):
            counts.days_to_order_due_mismatch += 1
            per_issue["days_to_order_due_mismatch"].add(acct)
        if order_date is None:
            counts.rows_without_order_date += 1
            per_issue["no_order_date"].add(acct)
        if d.due_date != d.document_date + dt.timedelta(days=d.term_days):
            counts.due_date_rule_failed += 1
        if open_amt <= 0:
            counts.open_amount_not_positive += 1
        if open_amt > total + TOL:
            counts.open_above_total += 1
        if d.currency == "TL" and abs(tl - open_amt) > TOL:
            counts.tl_amount_differs_for_tl += 1
        if d.kind is not None and d.kind not in KNOWN_KINDS:
            counts.unknown_kinds += 1
        if d.order_term_days is not None and d.order_term_days != d.term_days:
            counts.order_term_differs_from_invoice_term += 1

    by_acc: dict[str, list[CollectionDocument]] = defaultdict(list)
    for d in docs:
        by_acc[d.account_code].append(d)
    for acct, rows in by_acc.items():
        cum = 0.0
        for d in rows:
            cum += d.open_amount
            if d.running_balance is None or abs(round(d.running_balance - cum, 2)) > TOL:
                counts.running_balance_accounts_inconsistent += 1
                per_issue["running_balance"].add(acct)
                break
    numbers = Counter(d.document_no for d in docs)
    counts.documents = len(docs)
    counts.accounts = len(by_acc)
    counts.distinct_document_numbers = len(numbers)
    counts.repeated_document_numbers = sum(1 for n in numbers.values() if n > 1)
    counts.blank_legal_document_numbers = sum(1 for d in docs if not d.legal_document_no)
    counts.non_tl_rows = sum(1 for d in docs if d.currency != "TL")
    counts.currencies = sorted({d.currency for d in docs})
    counts.partially_open_documents = sum(d.partially_open for d in docs)
    counts.invoice_due_passed_rows = sum(d.invoice_due_passed for d in docs)
    counts.source_red_rows = sum(d.source_red for d in docs)
    counts.source_red_accounts = len({d.account_code for d in docs if d.source_red})
    for d in docs:
        if numbers[d.document_no] > 1:
            per_issue["repeated_document_no"].add(d.account_code)

    def warn(n, code, msg, sev=Severity.WARNING):
        if n:
            report.add(sev, code, msg, count=n)
    warn(counts.repeated_document_numbers, "repeated_document_number",
         f"{counts.repeated_document_numbers} document number(s) appear on more than one row; rows are kept separately.")
    warn(counts.days_to_due_mismatch, "days_to_due_mismatch",
         f"{counts.days_to_due_mismatch} row(s): column J differs from due date − report date.")
    warn(counts.days_to_order_due_mismatch, "days_to_order_due_mismatch",
         f"{counts.days_to_order_due_mismatch} row(s): W differs from order date + order term − report date.")
    warn(counts.rows_without_order_date, "order_date_missing",
         f"{counts.rows_without_order_date} row(s) have no order date: the order-based due date is unknown "
         "(the export writes 0 there).")
    warn(counts.running_balance_accounts_inconsistent, "running_balance_inconsistent",
         f"{counts.running_balance_accounts_inconsistent} account(s): 'Satır Bakiye' is not the running sum of 'Meblağ' "
         "in file order (informational column).")
    warn(counts.non_tl_rows, "non_tl_rows",
         f"{counts.non_tl_rows} row(s) in a foreign currency: TL totals use 'Ana döviz TL bakiye'.", Severity.INFO)
    warn(counts.blank_legal_document_numbers, "legal_document_number_blank",
         f"{counts.blank_legal_document_numbers} row(s) have no 'Belge no'.", Severity.INFO)
    warn(counts.unknown_kinds, "unknown_kind", f"{counts.unknown_kinds} row(s) have an unknown 'Türü'.")
    warn(counts.tl_amount_differs_for_tl, "tl_amount_differs",
         f"{counts.tl_amount_differs_for_tl} TL row(s): 'Ana döviz TL bakiye' differs from 'Meblağ'.")

    v = VerificationResult()
    v.add("records_match_rows", len(docs) == len(res.records))
    v.add("report_date_known", counts.report_date is not None)
    v.add("open_amounts_positive", counts.open_amount_not_positive == 0, "rows kept; reported")
    v.add("invoice_due_rule", counts.due_date_rule_failed == 0, "rows kept; reported")
    return DetailedCollectionsResult(True, None, path.name, docs, report, v, counts, import_result=res,
                                     issues_by_account={k: sorted(s) for k, s in per_issue.items()})
