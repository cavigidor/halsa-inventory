"""M6-3 — Hedef raporu importer (account grain) with explicit field confidence.

Source-specific module on the M6-1 framework (`run_import` + `SourceSpec`). Docs: docs/m6_hedef.md;
decision D-037.

- **Explicit file only.** The owner has not chosen the maintained Hedef workbook, so nothing here picks
  one: `import_hedef(path, ...)` takes an explicit path; `find_hedef_candidates(folder)` only lists
  candidates with their detected schema.
- **Two exact schema variants** (`current_2026`, 45 columns; `legacy_snapshot`, 56 columns). A workbook
  must contain every header of exactly one variant, or the import fails closed. The variant describes
  structure only, never authority.
- **Field confidence (D-023)** is declared per field and never upgraded: identity `trusted`; location,
  sector, assignment, discount tier and yearly metrics `unverified`; `Haftalık Kaşe Adedi`, `Risk` and
  `Kredi` always `stale`; `Ziyaret Sayısı` `incomplete`; legacy placeholder/target columns `invalid` and
  never part of the account model.
- **Identity:** `cari_kod` is the order-customer / canonical customer concept (D-003), exact-matched to
  `liste.KODU`; never trimmed; duplicates are fatal.
- **No analytics:** no penetration, potential, credit, territory, ranking or actions. The optional
  sales reconciliation is a technical identity check against the existing sales `Nettutar`, not a
  revenue policy, and outputs aggregate counts only.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportResult, run_import
from .entity_resolution import MasterIndex
from .excel import WorkbookPair, label_text
from .keys import record_key
from .models import (
    Audience,
    Confidence,
    IdentifierRole,
    MatchReport,
    MatchStatus,
    NormalizedRecord,
    NormalizedValue,
    Resolution,
    Severity,
    ValidationReport,
    VerificationResult,
)
from .sales_activity import CUSTOMER_MASTER, CUSTOMER_MASTER_SPEC, SALES_FILE_PATTERNS, canonical_customer
from .spec import DateRule, FieldSpec, FieldType, SourceSpec, TableLayout, VerifySpec

HEDEF_FILE_PATTERNS = ("*hedef*.xlsx", "*Hedef*.xlsx", "*HEDEF*.xlsx")
#: Opt-in filename date for dated snapshots named like `31-12-2025 Hedef raporu.xlsx`. Never applied
#: unless the caller passes it; the plain `hedef raporu.xlsx` has no date in its name.
DATED_SNAPSHOT_FILENAME = r"^(?P<day>\d{2})-(?P<month>\d{2})-(?P<year>\d{4}) "


class SchemaVersion(str, Enum):
    """Structure only. Says nothing about which file is the maintained source of record."""

    CURRENT_2026 = "current_2026"
    LEGACY_SNAPSHOT = "legacy_snapshot"


# Yearly metrics: normalized name → header template.
STAMP_QTY = "stamp_qty"
STAMP_QTY_DIRECT = "stamp_qty_direct"
STAMP_QTY_DISTRIBUTOR = "stamp_qty_distributor"
REPORTED_NET_REVENUE = "reported_net_revenue"
REPORTED_DIRECT_REVENUE = "reported_direct_revenue"
REPORTED_DISTRIBUTOR_REVENUE = "reported_distributor_revenue"
METRICS = (STAMP_QTY, STAMP_QTY_DIRECT, STAMP_QTY_DISTRIBUTOR,
           REPORTED_NET_REVENUE, REPORTED_DIRECT_REVENUE, REPORTED_DISTRIBUTOR_REVENUE)
_HEADER = {
    STAMP_QTY: "{y} Kaşe Adedi",
    STAMP_QTY_DIRECT: "{y} Direkt Satış Kaşe Adedi",
    STAMP_QTY_DISTRIBUTOR: "{y} Dağıtıcı Üzerinden Kaşe Adedi",
    REPORTED_NET_REVENUE: "{y} Net Ciro",
    REPORTED_DIRECT_REVENUE: "DİREKT SATIŞ TUTARI {y}",
    REPORTED_DISTRIBUTOR_REVENUE: "DAĞITICI ÜZERİNDEN SATIŞ TUTARI {y}",
}
_TYPE = {STAMP_QTY: FieldType.INTEGER, STAMP_QTY_DIRECT: FieldType.INTEGER, STAMP_QTY_DISTRIBUTOR: FieldType.INTEGER,
         REPORTED_NET_REVENUE: FieldType.DECIMAL, REPORTED_DIRECT_REVENUE: FieldType.DECIMAL,
         REPORTED_DISTRIBUTOR_REVENUE: FieldType.DECIMAL}

#: Which metric/year columns each variant really has (verified on the real files; nothing synthesized).
VARIANT_YEARS: dict[SchemaVersion, dict[str, tuple[int, ...]]] = {
    SchemaVersion.CURRENT_2026: {
        STAMP_QTY: (2021, 2022, 2023, 2024, 2025, 2026),
        STAMP_QTY_DIRECT: (2021, 2022, 2023, 2024, 2025, 2026),
        STAMP_QTY_DISTRIBUTOR: (2021, 2022, 2023, 2024, 2025, 2026),
        REPORTED_NET_REVENUE: (2022, 2023, 2024, 2025, 2026),
        REPORTED_DIRECT_REVENUE: (2022, 2023, 2024, 2025, 2026),
        REPORTED_DISTRIBUTOR_REVENUE: (2022, 2023, 2024, 2025, 2026),
    },
    SchemaVersion.LEGACY_SNAPSHOT: {
        STAMP_QTY: (2020, 2021, 2022, 2023, 2024, 2025),
        STAMP_QTY_DIRECT: (2021, 2022, 2023, 2024, 2025),
        STAMP_QTY_DISTRIBUTOR: (2021, 2022, 2023, 2024, 2025),
        REPORTED_NET_REVENUE: (2020, 2021, 2022, 2023, 2024, 2025),
        REPORTED_DIRECT_REVENUE: (2020, 2021, 2022, 2023, 2024, 2025),
        REPORTED_DISTRIBUTOR_REVENUE: (2020, 2021, 2022, 2023, 2024, 2025),
    },
}

#: Legacy columns that exist only as placeholders / old targets (1–80 distinct values). Required for
#: schema identity, imported as `invalid`, and never part of the account model.
LEGACY_PLACEHOLDER_HEADERS = (
    "2015_kase_adedi", "2016_Kase_adedi", "2017_kase_adedi",
    "2015_ciro_TROTEC_dahil", "2016_ciro_TROTEC_dahil", "2017_ciro_TROTEC_dahil",
    "2020 Hedef Kaşe Adedi", "2021 Hedef Kaşe Adedi", "2020 Hedef Ciro", "2021 Hedef Ciro",
)

STALE_FIELDS = ("historical_weekly_stamp_capacity", "historical_risk_limit", "historical_unsecured_credit_limit")
_U, _S, _I = Confidence.UNVERIFIED, Confidence.STALE, Confidence.INCOMPLETE


def _base_fields() -> tuple[FieldSpec, ...]:
    tol = dict(required=True, tolerate_invalid_cells=True)
    return (
        FieldSpec("customer_code", FieldType.OPAQUE_ID, header="cari_kod", nullable=False,
                  confidence=Confidence.TRUSTED, role=IdentifierRole.ORDER_CUSTOMER, master=CUSTOMER_MASTER),
        FieldSpec("hedef_rep", FieldType.STRING, header="Temsilci", confidence=_U, **tol),
        FieldSpec("customer_rep", FieldType.STRING, header="Müşteri Temsilcisi", confidence=_U, **tol),
        FieldSpec("sector_code", FieldType.STRING, header="Sektör Kodu", confidence=_U, **tol),
        FieldSpec("district", FieldType.STRING, header="İlce", confidence=_U, **tol),
        FieldSpec("city", FieldType.STRING, header="İl", confidence=_U, **tol),
        FieldSpec("discount_tier", FieldType.DECIMAL, header="isk1", confidence=_U, **tol),
        FieldSpec("historical_weekly_stamp_capacity", FieldType.DECIMAL, header="Haftalık Kaşe Adedi",
                  confidence=_S, **tol),
        FieldSpec("historical_risk_limit", FieldType.DECIMAL, header="Risk", confidence=_S, **tol),
        FieldSpec("historical_unsecured_credit_limit", FieldType.DECIMAL, header="Kredi", confidence=_S, **tol),
        FieldSpec("visit_count", FieldType.INTEGER, header="Ziyaret Sayısı", confidence=_I, **tol),
    )


def metric_field(metric: str, year: int) -> str:
    return f"{metric}_{year}"


def _spec(version: SchemaVersion) -> SourceSpec:
    fields = list(_base_fields())
    for metric, years in VARIANT_YEARS[version].items():
        for y in years:
            fields.append(FieldSpec(metric_field(metric, y), _TYPE[metric], header=_HEADER[metric].format(y=y),
                                    confidence=_U, required=True, tolerate_invalid_cells=True))
    if version is SchemaVersion.LEGACY_SNAPSHOT:
        for i, h in enumerate(LEGACY_PLACEHOLDER_HEADERS):
            fields.append(FieldSpec(f"legacy_placeholder_{i}", FieldType.DECIMAL, header=h,
                                    confidence=Confidence.INVALID, required=True, tolerate_invalid_cells=True))
    return SourceSpec(
        key=f"hedef_{version.value}",
        audience=Audience.SHARED,
        filename_patterns=HEDEF_FILE_PATTERNS,
        sheet=0,
        layout=TableLayout(header_row=1),
        fields=tuple(fields),
        default_confidence=Confidence.UNVERIFIED,
        business_date=DateRule(),            # set per call: declared date or opt-in filename date
        identity_fields=("customer_code",),
        verify=VerifySpec(min_rows=1),
        description=f"Hedef raporu, {version.value} layout (structure only; not a source-of-record decision).",
    )


SPECS: dict[SchemaVersion, SourceSpec] = {v: _spec(v) for v in SchemaVersion}

#: Assignment drift check only: liste.KODU + liste.TEMSİLCİ from the sales workbook.
MASTER_ASSIGNMENT_SPEC = SourceSpec(
    key="hedef_master_assignment_check",
    audience=Audience.SHARED,
    filename_patterns=SALES_FILE_PATTERNS,
    sheet="liste",
    layout=TableLayout(header_row=1),
    fields=(FieldSpec("code", FieldType.OPAQUE_ID, header="KODU", confidence=Confidence.TRUSTED),
            FieldSpec("master_rep", FieldType.STRING, header="TEMSİLCİ", required=False, tolerate_invalid_cells=True)),
    default_confidence=Confidence.UNVERIFIED,
    description="Technical assignment-drift check against the customer master's current TEMSİLCİ.",
)

#: Technical identity/reconciliation check only: existing sales `Nettutar` by year. Not a revenue policy.
SALES_RECONCILIATION_SPEC = SourceSpec(
    key="hedef_sales_reconciliation_check",
    audience=Audience.SHARED,
    filename_patterns=SALES_FILE_PATTERNS,
    sheet=0,
    layout=TableLayout(header_row=1),
    fields=(FieldSpec("order_customer", FieldType.OPAQUE_ID, header="Pro_kodu", role=IdentifierRole.ORDER_CUSTOMER),
            FieldSpec("invoice_account", FieldType.OPAQUE_ID, header="Musterikod", role=IdentifierRole.INVOICE_ACCOUNT),
            FieldSpec("order_date", FieldType.DATE, header="Tarih", nullable=False),
            FieldSpec("nettutar_existing", FieldType.DECIMAL, header="Nettutar", nullable=False)),
    default_confidence=Confidence.UNVERIFIED,
    business_date=DateRule.column_max("order_date"),
    description="Existing sales Nettutar, used only to check that Hedef revenue follows D-003 identity.",
)


# ---------------------------------------------------------------------------------------------
# Schema detection and candidates
# ---------------------------------------------------------------------------------------------
def required_headers(version: SchemaVersion) -> frozenset[str]:
    return frozenset(f.header.name for f in SPECS[version].fields)  # type: ignore[union-attr]


def detect_schema(path: str | Path) -> SchemaVersion | None:
    """The variant whose headers are all present (exact text) in header row 1 of the first sheet.
    None when no variant, or more than one, matches. Uses the M6-1 reader; reads one row only."""
    with WorkbookPair(path) as wb:
        sheet = wb.sheet(0)
        if sheet is None:
            return None
        headers = {label_text(c) for c in sheet.row(1)}
    hits = [v for v in SchemaVersion if required_headers(v) <= headers]
    return hits[0] if len(hits) == 1 else None


def find_hedef_candidates(folder: str | Path) -> list[dict[str, Any]]:
    """List Hedef-looking workbooks with their detected schema. Never selects one: the maintained file is an
    owner decision. Returns basenames only, in name order (not modification time)."""
    out = []
    for p in sorted(Path(folder).glob("*.xlsx")):
        if p.name.startswith("~$") or not SPECS[SchemaVersion.CURRENT_2026].matches_filename(p.name):
            continue
        try:
            schema = detect_schema(p)
        except Exception:  # unreadable candidate: listed, never selected
            schema = None
        out.append({"file": p.name, "schema": schema.value if schema else None})
    return out


# ---------------------------------------------------------------------------------------------
# Normalized account model (in memory)
# ---------------------------------------------------------------------------------------------
@dataclass(slots=True)
class HedefAccount:
    account_key: str                                   # record_key("hedef_account", [cari_kod]); variant-independent
    customer_code: str                                 # exact cari_kod (never trimmed)
    master_link: Resolution | None                     # None = master not supplied (not resolved)
    city: NormalizedValue
    district: NormalizedValue
    sector_code: NormalizedValue
    hedef_rep: NormalizedValue                         # Hedef `Temsilci`
    customer_rep: NormalizedValue                      # Hedef `Müşteri Temsilcisi` (a different field)
    discount_tier: NormalizedValue
    visit_count: NormalizedValue                       # incomplete: zero may mean "not logged"
    historical_weekly_stamp_capacity: NormalizedValue  # stale, reference only
    historical_risk_limit: NormalizedValue             # stale, never current credit exposure
    historical_unsecured_credit_limit: NormalizedValue  # stale, never current credit authority
    yearly: dict[str, dict[int, NormalizedValue]]      # metric → year → value; only years the variant has
    source_row: int


class ReconciliationSummary(BaseModel):
    """Technical identity/reconciliation check against existing sales `Nettutar` (not a revenue policy)."""

    model_config = ConfigDict(extra="forbid")
    label: str = "technical identity/reconciliation check against existing sales Nettutar (not canonical revenue)"
    year: int
    tolerance: float
    key: str = "order_customer (D-003 canonical customer)"
    comparable_hedef_accounts: int = 0
    exact_matches: int = 0
    mismatches: int = 0
    missing_sales_side: int = 0
    missing_hedef_side: int = 0
    exact_match_rate: float = 0.0
    invoice_account_diagnostic_exact_matches: int = 0
    invoice_account_diagnostic_rate: float = 0.0
    sales_customers: int = 0
    sales_customers_in_hedef: int = 0
    sales_coverage_rate: float = 0.0


class AssignmentSummary(BaseModel):
    """Aggregate agreement between Hedef `Temsilci` and the master's current `TEMSİLCİ` (no names).

    `same` is exact text. Representative names are labels, not identifiers, and the real Hedef file pads them
    with a trailing space, so a second, labelled count ignores surrounding whitespace (values stay unchanged).
    """

    model_config = ConfigDict(extra="forbid")
    comparable: int = 0
    same: int = 0
    different: int = 0
    same_ignoring_surrounding_whitespace: int = 0
    missing_on_hedef: int = 0
    missing_on_master: int = 0
    not_resolvable: int = 0
    agreement_rate: float = 0.0
    agreement_rate_ignoring_surrounding_whitespace: float = 0.0
    hedef_rep_with_surrounding_whitespace: int = 0
    hedef_rep_equals_customer_rep: int = 0


class HedefSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str | None = None
    accounts: int = 0
    unique_accounts: int = 0
    duplicate_codes: int = 0
    codes_with_surrounding_whitespace: int = 0
    customer_resolution: str = "not_supplied"     # resolved | not_supplied
    match_counts: dict[str, int] = Field(default_factory=dict)
    snapshot_date: dt.date | None = None
    snapshot_date_origin: str | None = None
    field_fill: dict[str, int] = Field(default_factory=dict)
    field_confidence: dict[str, str] = Field(default_factory=dict)
    weekly_capacity_filled: int = 0
    weekly_capacity_nonzero: int = 0
    risk_filled: int = 0
    kredi_filled: int = 0
    visit_count_filled: int = 0
    visit_count_nonzero: int = 0
    year_metrics: dict[str, list[int]] = Field(default_factory=dict)
    hedef_rep_filled: int = 0
    customer_rep_filled: int = 0
    reconciliation: ReconciliationSummary | None = None
    assignment: AssignmentSummary | None = None


@dataclass
class HedefResult:
    ok: bool
    failed_step: str | None
    file: str
    schema_version: SchemaVersion | None
    accounts: list[HedefAccount]
    report: ValidationReport
    verification: VerificationResult
    counts: HedefSummary
    match_reports: list[MatchReport] = field(default_factory=list)
    import_result: ImportResult | None = None

    def summary(self) -> dict[str, Any]:
        """Safe: counts and structure only (no codes, names, reps, cities, amounts, capacities or paths)."""
        return {
            "ok": self.ok,
            "failed_step": self.failed_step,
            "source": "hedef",
            "file": self.file,
            "counts": self.counts.model_dump(mode="json"),
            "report": self.report.to_dict(),
            "framework_verification": (self.import_result.verification.model_dump(mode="json")
                                       if self.import_result else None),
            "verification": self.verification.model_dump(mode="json"),
            "match_reports": [m.model_dump(mode="json") for m in self.match_reports],
        }


# ---------------------------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------------------------
def master_from_sales_workbook(sales_path: str | Path) -> MasterIndex | None:
    """Exact-match master (liste.KODU) from an explicitly supplied sales workbook (M6-2's master spec)."""
    res = run_import(sales_path, CUSTOMER_MASTER_SPEC)
    return MasterIndex.from_result(res, ["code"], name=CUSTOMER_MASTER) if res.ok else None


def import_hedef(path: str | Path, *, master: MasterIndex | None = None, snapshot_date: dt.date | None = None,
                 filename_date_regex: str | None = None) -> HedefResult:
    """Import one explicitly chosen Hedef workbook.

    Snapshot date: `snapshot_date` (declared) wins; else `filename_date_regex` if the caller opts in (fails
    closed when the name does not carry it); else unknown (warning). Never mtime, never today, never the
    latest year column. Without `master` the accounts are staged but explicitly unresolved.
    Fatal: unsupported/ambiguous schema, framework errors on `cari_kod` (missing, blank, Excel error),
    duplicate account codes, failed verification. Optional-field problems are cell-level warnings.
    """
    path = Path(path)
    version = None
    try:
        version = detect_schema(path)
    except Exception:
        version = None
    if version is None:
        report = ValidationReport(source="hedef", audience=Audience.SHARED, file=path.name, sheet="#0", layout="table")
        report.add(Severity.ERROR, "unsupported_schema",
                   "The workbook matches no supported Hedef schema (or more than one); nothing is guessed.")
        return HedefResult(False, "detect", path.name, None, [], report, VerificationResult(), HedefSummary())

    spec = SPECS[version]
    if snapshot_date is not None:
        spec = replace(spec, business_date=DateRule.declared())
    elif filename_date_regex is not None:
        spec = replace(spec, business_date=DateRule.from_filename(filename_date_regex))
    masters = {CUSTOMER_MASTER: master} if master is not None else {}
    res = run_import(path, spec, declared_date=snapshot_date, masters=masters)
    report = res.report
    counts = HedefSummary(schema_version=version.value)
    result = HedefResult(False, res.failed_step, path.name, version, [], report, VerificationResult(), counts,
                         import_result=res)
    if not res.ok:
        return result

    codes = [r.get("customer_code") for r in res.records]
    dup = sum(n - 1 for n in Counter(codes).values() if n > 1)
    counts.accounts, counts.unique_accounts, counts.duplicate_codes = len(codes), len(set(codes)), dup
    if dup:
        report.add(Severity.ERROR, "duplicate_account_code",
                   f"{dup} account code(s) repeat; one Hedef row must be one account.", field="customer_code", count=dup)
        result.failed_step = "accounts"
        return result
    ws = sum(1 for c in codes if c != c.strip())
    counts.codes_with_surrounding_whitespace = ws
    if ws:
        report.add(Severity.INFO, "codes_with_surrounding_whitespace",
                   f"{ws} account code(s) carry surrounding whitespace; kept exactly (no trimming).",
                   field="customer_code", count=ws)
    if snapshot_date is None and filename_date_regex is None:
        report.add(Severity.WARNING, "snapshot_date_unknown",
                   "The workbook carries no snapshot date and none was declared; the snapshot date is unknown.")

    accounts = [_account(r, version) for r in res.records]
    if master is not None:
        counts.customer_resolution = "resolved"
        for acc, rec in zip(accounts, res.records):
            acc.master_link = rec.links.get("customer_code")
        result.match_reports = list(res.match_reports)
        if res.match_reports:
            counts.match_counts = {k.value: v for k, v in res.match_reports[0].counts.items()}
    _fill_counts(counts, res, accounts, version)
    _verify(result, accounts, res.records)
    if not result.verification.ok:
        failed = [c.name for c in result.verification.checks if not c.ok]
        report.add(Severity.ERROR, "hedef_verification_failed", "Hedef verification failed: " + ", ".join(failed) + ".",
                   count=len(failed))
        result.failed_step = "verify"
        return result
    result.ok, result.failed_step, result.accounts = True, None, accounts
    return result


def _account(rec: NormalizedRecord, version: SchemaVersion) -> HedefAccount:
    v = rec.values
    yearly = {m: {y: v[metric_field(m, y)] for y in years} for m, years in VARIANT_YEARS[version].items()}
    code = rec.get("customer_code")
    return HedefAccount(
        account_key=record_key("hedef_account", [code]), customer_code=code, master_link=None,
        city=v["city"], district=v["district"], sector_code=v["sector_code"], hedef_rep=v["hedef_rep"],
        customer_rep=v["customer_rep"], discount_tier=v["discount_tier"], visit_count=v["visit_count"],
        historical_weekly_stamp_capacity=v["historical_weekly_stamp_capacity"],
        historical_risk_limit=v["historical_risk_limit"],
        historical_unsecured_credit_limit=v["historical_unsecured_credit_limit"],
        yearly=yearly, source_row=rec.row)


def _filled(nv: NormalizedValue) -> bool:
    return nv.value is not None


def _fill_counts(c: HedefSummary, res: ImportResult, accounts: list[HedefAccount], version: SchemaVersion) -> None:
    c.snapshot_date = res.business_date.value
    c.snapshot_date_origin = res.business_date.origin.value if res.business_date.origin else None
    model_fields = ("city", "district", "sector_code", "hedef_rep", "customer_rep", "discount_tier", "visit_count",
                    *STALE_FIELDS)
    c.field_fill = {f: sum(1 for a in accounts if _filled(getattr(a, f))) for f in model_fields}
    c.field_confidence = {f: res.field_confidence[f].value for f in ("customer_code", *model_fields)}
    cap = [a.historical_weekly_stamp_capacity for a in accounts]
    c.weekly_capacity_filled = sum(1 for nv in cap if _filled(nv))
    c.weekly_capacity_nonzero = sum(1 for nv in cap if _filled(nv) and nv.value != 0)
    c.risk_filled = c.field_fill["historical_risk_limit"]
    c.kredi_filled = c.field_fill["historical_unsecured_credit_limit"]
    c.visit_count_filled = c.field_fill["visit_count"]
    c.visit_count_nonzero = sum(1 for a in accounts if _filled(a.visit_count) and a.visit_count.value != 0)
    c.year_metrics = {m: list(years) for m, years in VARIANT_YEARS[version].items()}
    c.hedef_rep_filled, c.customer_rep_filled = c.field_fill["hedef_rep"], c.field_fill["customer_rep"]


def _verify(result: HedefResult, accounts: list[HedefAccount], records: list[NormalizedRecord]) -> None:
    v = result.verification
    v.add("accounts_reconcile", len(accounts) == len(records) == result.counts.accounts)
    keys = [a.account_key for a in accounts]
    v.add("account_keys_unique", len(keys) == len(set(keys)))
    stale_ok = all(getattr(a, f).confidence in (Confidence.STALE, Confidence.INVALID) for a in accounts
                   for f in STALE_FIELDS)
    v.add("stale_fields_stay_stale", stale_ok)
    v.add("visit_count_incomplete", all(a.visit_count.confidence in (Confidence.INCOMPLETE, Confidence.INVALID)
                                        for a in accounts))
    v.add("identity_trusted",
          result.import_result is not None
          and result.import_result.field_confidence["customer_code"] is Confidence.TRUSTED)
    v.add("no_placeholders_in_model", all(not any(k.startswith("legacy_placeholder") for k in a.yearly)
                                          for a in accounts))
    # Privacy guard: no code or representative/city text appears in the safe summary.
    blob = json.dumps(result.summary(), ensure_ascii=False, default=str)
    sensitive = {a.customer_code.strip() for a in accounts} | {
        str(nv.value) for a in accounts for nv in (a.hedef_rep, a.customer_rep, a.city, a.district)
        if isinstance(nv.value, str)}
    leaked = sum(1 for s in sensitive if len(s) >= 4 and s in blob)
    v.add("summary_has_no_identities", leaked == 0, f"{leaked} leak(s)")


# ---------------------------------------------------------------------------------------------
# Optional technical checks (aggregate only)
# ---------------------------------------------------------------------------------------------
def reconcile_with_sales(result: HedefResult, sales_path: str | Path, *, year: int,
                         tolerance: float = 0.01) -> ReconciliationSummary:
    """Compare Hedef `<year> Net Ciro` with the existing sales `Nettutar` summed by the D-003 canonical customer
    (`Pro_kodu`, `Musterikod` only when empty). The invoice-account sum is a separate, labelled diagnostic; it
    never replaces the canonical key. Hedef values are never modified. Output: counts and rates only."""
    if not result.ok:
        raise ValueError("reconciliation needs a successful Hedef import")
    if year not in VARIANT_YEARS[result.schema_version][REPORTED_NET_REVENUE]:  # type: ignore[index]
        raise ValueError("this Hedef variant has no reported net revenue for that year")
    sales = run_import(sales_path, SALES_RECONCILIATION_SPEC)
    if not sales.ok:
        raise ValueError("the sales workbook did not pass validation")
    by_customer: Counter = Counter()
    by_invoice: Counter = Counter()
    customers: set[str] = set()
    for r in sales.records:
        cust, _ = canonical_customer(r.get("order_customer"), r.get("invoice_account"))
        if cust is not None:
            customers.add(cust)
        d, amount = r.get("order_date"), r.get("nettutar_existing")
        if d is None or amount is None or d.year != year:
            continue
        if cust is not None:
            by_customer[cust] += amount
        if r.get("invoice_account") is not None:
            by_invoice[r.get("invoice_account")] += amount
    hedef: dict[str, float] = {}
    for a in result.accounts:
        nv = a.yearly[REPORTED_NET_REVENUE][year]
        if nv.value is not None and nv.confidence is not Confidence.INVALID:
            hedef[a.customer_code] = nv.value
    comparable = {c: v for c, v in hedef.items() if v != 0}
    s = ReconciliationSummary(year=year, tolerance=tolerance)
    s.comparable_hedef_accounts = len(comparable)
    for code, value in comparable.items():
        if code not in by_customer:
            s.missing_sales_side += 1
        elif abs(by_customer[code] - value) <= tolerance:
            s.exact_matches += 1
        else:
            s.mismatches += 1
        if code in by_invoice and abs(by_invoice[code] - value) <= tolerance:
            s.invoice_account_diagnostic_exact_matches += 1
    s.missing_hedef_side = sum(1 for c, v in by_customer.items() if abs(v) > tolerance and c not in comparable)
    if comparable:
        s.exact_match_rate = round(s.exact_matches / len(comparable), 4)
        s.invoice_account_diagnostic_rate = round(s.invoice_account_diagnostic_exact_matches / len(comparable), 4)
    accounts = {a.customer_code for a in result.accounts}
    s.sales_customers = len(customers)
    s.sales_customers_in_hedef = sum(1 for c in customers if c in accounts)
    s.sales_coverage_rate = round(s.sales_customers_in_hedef / len(customers), 4) if customers else 0.0
    result.counts.reconciliation = s
    return s


def assignment_drift(result: HedefResult, sales_path: str | Path) -> AssignmentSummary:
    """Aggregate agreement between Hedef `Temsilci` and the master's current `TEMSİLCİ` for the same code
    (exact text). Neither field is chosen as correct; no names leave this function."""
    if not result.ok:
        raise ValueError("assignment drift needs a successful Hedef import")
    master = run_import(sales_path, MASTER_ASSIGNMENT_SPEC)
    if not master.ok:
        raise ValueError("the customer master did not pass validation")
    reps: dict[str, list[Any]] = {}
    for r in master.records:
        if r.get("code") is not None:
            reps.setdefault(r.get("code"), []).append(r.get("master_rep"))
    s = AssignmentSummary()
    for a in result.accounts:
        h = a.hedef_rep.value
        if h is not None and h == a.customer_rep.value:
            s.hedef_rep_equals_customer_rep += 1
        if h is not None and h != h.strip():
            s.hedef_rep_with_surrounding_whitespace += 1
        rows = reps.get(a.customer_code)
        if rows is None or len(rows) != 1:
            s.not_resolvable += 1
            continue
        m = rows[0]
        if h is None:
            s.missing_on_hedef += 1
        elif m is None:
            s.missing_on_master += 1
        else:
            s.comparable += 1
            if h == m:
                s.same += 1
            else:
                s.different += 1
            if h.strip() == m.strip():
                s.same_ignoring_surrounding_whitespace += 1
    if s.comparable:
        s.agreement_rate = round(s.same / s.comparable, 4)
        s.agreement_rate_ignoring_surrounding_whitespace = round(s.same_ignoring_surrounding_whitespace / s.comparable, 4)
    result.counts.assignment = s
    return s


def master_link_status(account: HedefAccount) -> MatchStatus | None:
    return account.master_link.status if account.master_link else None


__all__ = [
    "SchemaVersion", "SPECS", "HedefAccount", "HedefResult", "HedefSummary", "ReconciliationSummary",
    "AssignmentSummary", "detect_schema", "find_hedef_candidates", "import_hedef", "master_from_sales_workbook",
    "reconcile_with_sales", "assignment_drift", "metric_field", "DATED_SNAPSHOT_FILENAME", "METRICS",
    "VARIANT_YEARS", "LEGACY_PLACEHOLDER_HEADERS", "STALE_FIELDS", "master_link_status",
]
