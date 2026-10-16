"""Core types for the M6 importer framework.

Everything a report or result carries is defined here. Reports are Pydantic models so they
serialize safely (JSON for logs or a future API); normalized records are small dataclasses that
stay in memory and are never part of a serialized report.

Two separate concepts are kept apart on purpose (D-023, M6-1 §28):
- **technical validity**: did the cell parse, is it an Excel error, is the formula cache missing;
- **business confidence**: `Confidence`, declared per source/field by the spec.
Parsing success never upgrades confidence. Only technical problems downgrade it (to `invalid`).
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Confidence(str, Enum):
    """D-023 data-confidence states. The one canonical vocabulary; do not add parallel ones."""

    TRUSTED = "trusted"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    UNVERIFIED = "unverified"
    INVALID = "invalid"

    @property
    def rank(self) -> int:
        """Worse is higher. Used only to *downgrade*; nothing ever moves a value to a lower rank."""
        return _CONFIDENCE_RANK[self]

    @property
    def usable_for_decisions(self) -> bool:
        """D-023 §12: `stale` and `invalid` fields cannot feed decision logic."""
        return self not in (Confidence.STALE, Confidence.INVALID)


_CONFIDENCE_RANK = {
    Confidence.TRUSTED: 0,
    Confidence.UNVERIFIED: 1,
    Confidence.INCOMPLETE: 2,
    Confidence.STALE: 3,
    Confidence.INVALID: 4,
}


def downgrade(current: Confidence, *others: Confidence) -> Confidence:
    """Return the worst of the given states. Confidence can only go down, never up."""
    worst = current
    for other in others:
        if other.rank > worst.rank:
            worst = other
    return worst


class Audience(str, Enum):
    """Data classification only (no auth, no roles in M6-1).

    `shared`: sales / shared operational sources (sales report, Hedef, collections, Trodat, fairs).
    `owner`: owner / Yönetim sources (gider/gelir, kredi takip, bütçe). These must never reach
    salesperson views or evidence packets once a UI exists.
    """

    SHARED = "shared"
    OWNER = "owner"


class Severity(str, Enum):
    ERROR = "error"      # fail closed: no normalized result
    WARNING = "warning"  # import may proceed if the spec allows warnings
    INFO = "info"        # structural metadata


class CellKind(str, Enum):
    """What the workbook holds in one cell, from the formula view and the cached-value view."""

    EMPTY = "empty"
    LITERAL = "literal"                              # a typed value, no formula
    ERROR = "error"                                  # a literal Excel error value
    FORMULA_VALUE = "formula_value"                  # formula + cached result from Excel's last calculation
    FORMULA_MISSING_CACHE = "formula_missing_cache"  # formula, no cached result saved
    FORMULA_ERROR = "formula_error"                  # formula whose cached result is an Excel error

    @property
    def is_formula(self) -> bool:
        return self in (CellKind.FORMULA_VALUE, CellKind.FORMULA_MISSING_CACHE, CellKind.FORMULA_ERROR)

    @property
    def is_error(self) -> bool:
        return self in (CellKind.ERROR, CellKind.FORMULA_ERROR)


class IdentifierRole(str, Enum):
    """Conceptual role of an identifier. Codes may share one key space (the customer master)
    but the roles are not interchangeable (M6-0: order source vs invoice account)."""

    ORDER_CUSTOMER = "order_customer"
    INVOICE_ACCOUNT = "invoice_account"
    PRODUCT = "product"
    LOAN = "loan"
    OTHER = "other"


class MatchStatus(str, Enum):
    MATCHED = "matched"                    # exactly one master record
    UNMATCHED = "unmatched"                # no master record
    AMBIGUOUS = "ambiguous"                # matches different master records through different key fields
    NULL = "null"                          # no usable identifier (empty or invalid)
    DUPLICATE_MASTER = "duplicate_master"  # the master key itself is not unique; never resolved to one


class DateOrigin(str, Enum):
    """How a business date was obtained (small explicit provenance, not a lineage platform)."""

    LITERAL = "literal"                # a literal date cell
    CACHED_FORMULA = "cached_formula"  # a formula cell's cached value (Excel's last calculation)
    CONTENT = "content"                # derived from the content (max of a column, bucket headers)
    DECLARED = "declared"              # supplied at import time by the operator
    FILENAME = "filename"              # parsed from the file name (only if the spec opts in)


# ---------------------------------------------------------------------------------------------
# Report models (safe to print / log / return). They contain shapes and counts, never values.
# ---------------------------------------------------------------------------------------------
class _Safe(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid", use_enum_values=False)


class ValidationIssue(_Safe):
    severity: Severity
    code: str
    source: str
    sheet: str | None = None
    field: str | None = None     # spec field name (spec-authored, never a cell value)
    region: str | None = None    # cell / column letters / range, never a value
    message: str                 # fixed template text + counts only
    count: int = 1


class FieldStats(_Safe):
    field: str
    type: str
    confidence: Confidence
    rows: int = 0
    filled: int = 0
    null: int = 0
    fill_rate: float = 0.0
    distinct: int = 0
    duplicates: int = 0
    kinds: dict[str, int] = Field(default_factory=dict)        # python type names of raw cells
    formula_count: int = 0
    error_count: int = 0
    errors_by_type: dict[str, int] = Field(default_factory=dict)
    missing_cache_count: int = 0
    invalid_count: int = 0
    length_min: int | None = None   # strings / identifiers only
    length_max: int | None = None
    shapes: dict[str, int] = Field(default_factory=dict)  # identifiers only: 9 = digit, A = letter
    date_min: dt.date | None = None  # dates only (source freshness)
    date_max: dt.date | None = None


class ExternalLinks(_Safe):
    count: int = 0                       # external workbook relationships
    names: list[str] = Field(default_factory=list)  # sanitized basenames only
    formula_refs: int = 0                # formula cells that reference another workbook


class BusinessDate(_Safe):
    value: dt.date | None = None
    strategy: str = "none"
    origin: DateOrigin | None = None
    granularity: Literal["day", "month"] = "day"


class ValidationReport(_Safe):
    source: str
    audience: Audience
    file: str                       # basename only
    sheet: str | None = None
    layout: str | None = None
    rows_read: int = 0
    blank_rows_skipped: int = 0
    business_date: BusinessDate | None = None
    external_links: ExternalLinks = Field(default_factory=ExternalLinks)
    fields: list[FieldStats] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    def add(self, severity: Severity, code: str, message: str, *, field: str | None = None,
            region: str | None = None, count: int = 1) -> ValidationIssue:
        issue = ValidationIssue(severity=severity, code=code, source=self.source, sheet=self.sheet,
                                field=field, region=region, message=message, count=count)
        self.issues.append(issue)
        return issue

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def valid(self) -> bool:
        return not self.errors

    def codes(self, severity: Severity | None = None) -> list[str]:
        return [i.code for i in self.issues if severity is None or i.severity == severity]

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d["valid"] = self.valid
        d["error_count"] = len(self.errors)
        d["warning_count"] = len(self.warnings)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


class VerificationCheck(_Safe):
    name: str
    ok: bool
    detail: str = ""   # counts only


class VerificationResult(_Safe):
    ok: bool = True
    checks: list[VerificationCheck] = Field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(VerificationCheck(name=name, ok=ok, detail=detail))
        self.ok = self.ok and ok


class MatchReport(_Safe):
    """Aggregate entity-resolution report. Never contains codes or names."""

    field: str
    role: IdentifierRole
    master: str
    total: int = 0
    non_null: int = 0
    counts: dict[MatchStatus, int] = Field(default_factory=lambda: {s: 0 for s in MatchStatus})
    rates: dict[str, float] = Field(default_factory=dict)  # matched/unmatched/... of non_null; null of total
    master_size: int = 0
    master_duplicate_keys: int = 0   # number of distinct master keys that occur more than once
    master_invalid_keys: int = 0     # master rows whose key was empty/invalid (not indexed)


# ---------------------------------------------------------------------------------------------
# In-memory staging records (never serialized into a report).
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class NormalizedValue:
    value: Any                   # None when empty *or* invalid; check `confidence`/`kind` to tell them apart
    confidence: Confidence
    kind: CellKind               # provenance: literal vs cached formula vs error vs missing cache
    error: str | None = None     # Excel error code (e.g. "#DIV/0!") or a validation code (e.g. "id_malformed")

    @property
    def is_null(self) -> bool:
        return self.value is None and self.error is None

    @property
    def is_invalid(self) -> bool:
        return self.confidence == Confidence.INVALID and self.error is not None

    @property
    def from_last_excel_calculation(self) -> bool:
        return self.kind == CellKind.FORMULA_VALUE


@dataclass(frozen=True, slots=True)
class Resolution:
    status: MatchStatus
    master_row: int | None = None   # internal index into the master; None unless matched


@dataclass(slots=True)
class NormalizedRecord:
    key: str                       # deterministic synthetic record key
    row: int                       # internal sheet row (1-based) or 0 for matrix cell records
    values: dict[str, NormalizedValue]
    links: dict[str, Resolution] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        nv = self.values.get(name)
        return None if nv is None else nv.value


@dataclass(frozen=True)
class AttributeSource:
    """Declared provenance of an entity attribute (supports the DQ-5 fix later).

    Example: KAPALI status comes from master `customer_master`, column `CARİ FİRMA`, keyed by the
    master key `KODU`, joined through the identifier role `order_customer`.
    """

    attribute: str
    master: str
    master_field: str
    master_key: str
    via_role: IdentifierRole
    note: str = ""
