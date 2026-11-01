"""Source generations (M8-ACCURACY-2): one validated, immutable manifest per published set of source revisions.

A generation records, for every selected CORE ERP source (stock, sales order lines, Cari aging; plus the customer
master sheet inside the sales workbook) and every loaded OWNER source (Kredi, Gider/Gelir, Bütçe):
- the explicitly selected file (basename) and how it was selected;
- the SHA-256 of the ORIGINAL workbook bytes (any change — a value, a cell type, a formula vs its cached value, a
  workbook revision — gives a new fingerprint; a re-save with no visible change does too, which only re-runs checks);
- its source type and date semantics, the DECLARED business dates (snapshot / reporting period) and the OBSERVED
  dates (latest transaction, newest aging bucket) — never the file's modification time, which is kept for information
  only;
- the formula-aware validation counts, the join report and the resulting `SourceEvidence`.

Bytes are read ONCE (`FrozenSource`); every reader of that generation (pandas frames, the formula-aware inspection,
the owner importers) parses those same bytes, so validation and calculation can never see different file contents.

Binding (no evidence crosses a generation): a consumer gets evidence for a source only when the data it SERVES was
computed from bytes with exactly the recorded fingerprint (`Manifest.bound_snapshot`). The dashboard build, the API,
the Yönetim owner views and the refresh all go through this one object (`GenerationStore`).

Identity covers the evaluation, not only the sources (review of PR #28 issue 2): `core_id` digests the policy version,
every core record's FULL content (source identity, declarations, observed dates, validation results, evidence) and the
join report; `generation_id` adds the owner records' full content. `file_modified` and `created_at` are informational
and excluded. A manifest file whose content does not reproduce its recorded ids is rejected as corrupted — this is an
UNKEYED checksum: it detects accidental corruption, not deliberate edits (whoever can edit the file can recompute it).

Restart therefore never trusts the saved evaluation: a persisted manifest is only a CANDIDATE (dashboard built from
it, every source file still has its recorded fingerprint). The first context build re-derives every validation result
and all evidence from the original workbook bytes and the current selection file (`rederive_core`) and accepts the
generation only if the re-derived manifest has exactly the recorded `core_id`; the re-derived manifest — never the
file's content — is what gets installed. Any difference fails closed (no evidence).

Manifests contain hashes, basenames, dates and counts only — never workbook values, names, amounts or paths.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import io
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from services import accuracy as ACC

SELECTION_FILE = "sources.json"
SELECTION_SCHEMA = "halsa-source-selection/1"
MANIFEST_SCHEMA = "halsa-generation/1"
CORE_KEYS = ("stock", "sales", "aging")
OWNER_KEYS = ("kredi", "gider_gelir", "butce")
SOURCE_TYPES = {"stock": "stock_snapshot", "sales": "sales_order_lines", "aging": "cari_aging_snapshot",
                "liste": "customer_master_sheet", "kredi": "loan_installments", "gider_gelir": "income_expense_report",
                "butce": "budget_workbook"}
DATE_KINDS = {"stock": "current_state_snapshot", "aging": "current_state_snapshot", "sales": "transaction_history",
              "liste": "current_state_snapshot", "kredi": "installment_schedule_snapshot",
              "gider_gelir": "reporting_period", "butce": "planning_period"}
_DECL_FIELDS = ("snapshot_date", "report_date", "period_start", "period_end", "period_complete",
                "date_origin", "date_source")      # D-072 D1: where the report date came from (provenance)


class SelectionError(ValueError):
    """The explicit selection file is present but invalid (fails closed; never falls back silently)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------------------------
# frozen bytes
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FrozenSource:
    key: str
    path: str                 # local only; never serialized
    data: bytes = field(repr=False)
    sha256: str = ""
    size: int = 0
    file_modified: str | None = None   # informational ONLY; never a business date

    @property
    def file(self) -> str:
        return os.path.basename(self.path)

    @classmethod
    def read(cls, key: str, path: str) -> "FrozenSource":
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            mtime = dt.datetime.fromtimestamp(os.stat(path).st_mtime).isoformat(timespec="seconds")
        except OSError:
            mtime = None
        return cls(key, path, data, sha256_bytes(data), len(data), mtime)

    def stream(self) -> io.BytesIO:
        return io.BytesIO(self.data)


# ---------------------------------------------------------------------------------------------
# explicit selection (data/sources.json; private, gitignored with data/)
# ---------------------------------------------------------------------------------------------
def _date(v, what) -> dt.date | None:
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(v))
    except ValueError:
        raise SelectionError(f"{what}: tarih YYYY-AA-GG olmalı")


_DECISION = re.compile(r"^D-\d{3}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _declaration(key: str, entry: dict) -> dict:
    out = {}
    for f in ("snapshot_date", "report_date", "period_start", "period_end"):
        out[f] = _date(entry.get(f), f"{key}.{f}")
    pc = entry.get("period_complete")
    if pc not in (None, True, False):
        raise SelectionError(f"{key}.period_complete: true / false olmalı")
    out["period_complete"] = pc
    # D-072 D1 provenance of the date: "declared" (default), "filename" (the original file name) or "upload_date"
    # (the name held no date). The original name is kept only as a bounded display label.
    origin = entry.get("date_origin")
    if origin not in (None, "declared", "filename", "upload_date"):
        raise SelectionError(f"{key}.date_origin: declared / filename / upload_date olmalı")
    if origin is not None and not (out["snapshot_date"] or out["report_date"]):
        raise SelectionError(f"{key}.date_origin: tarih olmadan tarih kaynağı olamaz")
    out["date_origin"] = origin
    src = entry.get("date_source")
    if src is not None and (not isinstance(src, str) or len(src) > 255):
        raise SelectionError(f"{key}.date_source: en fazla 255 karakterlik dosya adı olmalı")
    out["date_source"] = src
    ap = entry.get("approval")
    if ap is not None:
        # A recorded reconciliation / mapping approval for ONE exact source revision (its SHA-256), citing a decision.
        # It can only set the two facts the code cannot establish itself; it never promotes a metric (the code
        # register and GENERATION_BOUND_API_EVIDENCE still decide).
        if (not isinstance(ap, dict) or not _SHA.match(str(ap.get("sha256", ""))) or
                not _DECISION.match(str(ap.get("decision_ref", ""))) or
                any(ap.get(f) not in (None, True, False) for f in ("totals_consistent", "mapping_approved"))):
            raise SelectionError(f"{key}.approval: sha256 (64 hex), decision_ref (D-xxx) ve true/false alanlar olmalı")
        out["approval"] = {"sha256": ap["sha256"], "decision_ref": ap["decision_ref"],
                           "totals_consistent": ap.get("totals_consistent"),
                           "mapping_approved": ap.get("mapping_approved")}
    return out


def load_selection(data_folder: str) -> dict | None:
    """The explicit core selection, or None when no selection file exists. Files must be plain basenames that exist
    in the data folder (no paths, no traversal). An invalid file raises SelectionError (fail closed)."""
    p = os.path.join(data_folder, SELECTION_FILE)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raise SelectionError("sources.json okunamadı veya geçerli JSON değil")
    if not isinstance(raw, dict) or raw.get("schema") != SELECTION_SCHEMA or not isinstance(raw.get("core"), dict):
        raise SelectionError(f"sources.json şeması '{SELECTION_SCHEMA}' ve 'core' bölümü olmalı")
    out = {}
    for key, entry in raw["core"].items():
        if key not in CORE_KEYS or not isinstance(entry, dict):
            raise SelectionError(f"bilinmeyen kaynak: {key}")
        name = entry.get("file")
        if (not isinstance(name, str) or not name or os.path.basename(name) != name or name.startswith(".")
                or name.startswith("~$")):
            raise SelectionError(f"{key}.file yalnızca dosya adı olmalı")
        full = os.path.join(data_folder, name)
        if not os.path.isfile(full):
            raise SelectionError(f"{key}: seçilen dosya veri klasöründe yok")
        out[key] = {"path": full, "declared": _declaration(key, entry)}
    for key in ("stock", "sales"):
        if key not in out:
            raise SelectionError(f"sources.json içinde '{key}' seçimi zorunlu")
    return out


def select_core(data_folder: str) -> tuple[dict[str, str | None], dict[str, dict], str]:
    """(paths, declarations, origin). origin = "explicit" (sources.json) or "mtime_fallback" (the legacy M5 choice:
    still builds the preserved algorithms, but evidence then records the selection as NOT explicit → nothing can be
    verified)."""
    sel = load_selection(data_folder)
    if sel is not None:
        paths = {k: (sel[k]["path"] if k in sel else None) for k in CORE_KEYS}
        decl = {k: (sel[k]["declared"] if k in sel else {f: None for f in _DECL_FIELDS}) for k in CORE_KEYS}
        return paths, decl, "explicit"
    import dashboard_builder as db
    paths = db.select_sources(data_folder)
    return paths, {k: {f: None for f in _DECL_FIELDS} for k in CORE_KEYS}, "mtime_fallback"


def freeze(paths: Mapping[str, str | None]) -> dict[str, FrozenSource]:
    return {k: FrozenSource.read(k, p) for k, p in paths.items() if p}


# ---------------------------------------------------------------------------------------------
# M8-CP3 (Excel Upload Center): writing the explicit selections, owner workbook selection
# ---------------------------------------------------------------------------------------------
# The core selection keeps its existing file and schema (data/sources.json). The owner workbooks published through the
# Upload Center are recorded in a second private file next to it; the owner's environment paths (KREDI_PATH, …) still
# work, and an uploaded workbook takes precedence for its own source only. Both files hold basenames and the owner's
# declarations only — never a path, a value, a fingerprint-derived approval or anything inferred.
OWNER_SELECTION_FILE = "owner_sources.json"
OWNER_SELECTION_SCHEMA = "halsa-owner-selection/1"
OWNER_OPTION_FIELDS = {"kredi": ("snapshot_date",), "gider_gelir": ("report_year",),
                       "butce": ("planning_year", "snapshot_date")}


def write_json_atomic(path: str, doc: Mapping[str, Any]) -> None:
    """tmp + fsync + os.replace in the same folder: a reader sees the old or the new file, never a partial one."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sel-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_jsonable(dict(doc)), fh, ensure_ascii=False, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def core_selection_document(entries: Mapping[str, Mapping[str, Any]]) -> dict:
    """`data/sources.json` content for {key: {"file": basename, <declarations>}} (the schema `load_selection` reads).
    Unknown (None) declarations are left out: unknown stays unknown."""
    core = {}
    for key in CORE_KEYS:
        e = entries.get(key)
        if not e:
            continue
        out = {"file": e["file"]}
        for f in _DECL_FIELDS:
            v = e.get(f)
            if v is not None:
                out[f] = _jsonable(v)
        if e.get("approval"):
            out["approval"] = dict(e["approval"])      # an existing, fingerprint-bound owner approval is carried as is
        core[key] = out
    return {"schema": SELECTION_SCHEMA, "core": core}


def owner_selection_document(entries: Mapping[str, Mapping[str, Any]]) -> dict:
    owner = {}
    for key in OWNER_KEYS:
        e = entries.get(key)
        if not e:
            continue
        out = {"file": e["file"]}
        for f in OWNER_OPTION_FIELDS[key]:
            if e.get(f) is not None:
                out[f] = str(e[f])
        owner[key] = out
    return {"schema": OWNER_SELECTION_SCHEMA, "owner": owner}


def load_owner_selection(data_folder: str) -> dict | None:
    """{key: {"path", "options"}} from `owner_sources.json`, or None when the file does not exist. Plain basenames in
    the data folder only; an invalid file raises SelectionError (the caller decides; nothing is guessed)."""
    p = os.path.join(data_folder, OWNER_SELECTION_FILE)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        raise SelectionError("owner_sources.json okunamadı")
    if not isinstance(raw, dict) or raw.get("schema") != OWNER_SELECTION_SCHEMA or not isinstance(raw.get("owner"), dict):
        raise SelectionError(f"owner_sources.json şeması '{OWNER_SELECTION_SCHEMA}' olmalı")
    out = {}
    for key, entry in raw["owner"].items():
        if key not in OWNER_KEYS or not isinstance(entry, dict):
            raise SelectionError(f"bilinmeyen yönetim kaynağı: {key}")
        name = entry.get("file")
        if (not isinstance(name, str) or not name or os.path.basename(name) != name or name.startswith(".")
                or name.startswith("~$")):
            raise SelectionError(f"{key}.file yalnızca dosya adı olmalı")
        opts = {}
        for f in OWNER_OPTION_FIELDS[key]:
            v = entry.get(f)
            if v is not None and not isinstance(v, str):
                raise SelectionError(f"{key}.{f} metin olmalı")
            opts[f] = v or None
        out[key] = {"path": os.path.join(data_folder, name), "options": opts}
    return out


def frames_from_frozen(frozen: Mapping[str, FrozenSource]) -> dict:
    """The M5 raw frames, parsed from the frozen bytes with the pipeline's own readers."""
    import dashboard_builder as db
    readers = {"stock": db.read_stock, "sales": db.read_sales_raw, "aging": db.read_aging_raw}
    return {k: readers[k](fs.stream()) for k, fs in frozen.items() if k in readers}


# ---------------------------------------------------------------------------------------------
# records and manifest
# ---------------------------------------------------------------------------------------------
def _jsonable(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, (tuple, set, frozenset)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


def evidence_to_dict(ev: ACC.SourceEvidence) -> dict:
    return {f.name: _jsonable(getattr(ev, f.name)) for f in dataclasses.fields(ev)}


def evidence_from_dict(d: dict) -> ACC.SourceEvidence:
    kw = {}
    for f in dataclasses.fields(ACC.SourceEvidence):
        if f.name not in d:
            continue
        v = d[f.name]
        if f.name in ("report_date", "period_start", "period_end", "latest_transaction_date",
                      "earliest_transaction_date") and v:
            v = dt.date.fromisoformat(v)
        if f.name in ("inspection_failures", "notes"):
            v = tuple(v or ())
        kw[f.name] = v
    return ACC.SourceEvidence(**kw)


@dataclass(frozen=True)
class SourceRecord:
    key: str
    role: str                                   # "core" | "owner"
    source_type: str
    date_kind: str
    file: str
    sha256: str
    size_bytes: int
    selection_origin: str                       # "explicit" | "mtime_fallback" | "env_path"
    declared: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    evidence: ACC.SourceEvidence | None = None
    file_modified: str | None = None            # informational only

    def identity(self) -> list:
        """Which source revision this is (key, fingerprint, selection, declarations)."""
        return [self.key, self.sha256, self.selection_origin, _jsonable(self.declared)]

    def content(self) -> dict:
        """Everything the record asserts, incl. validation results and evidence (not the informational file time)."""
        d = self.to_dict()
        d.pop("file_modified", None)
        return d

    def to_dict(self) -> dict:
        d = {f.name: _jsonable(getattr(self, f.name)) for f in dataclasses.fields(self) if f.name != "evidence"}
        d["evidence"] = evidence_to_dict(self.evidence) if self.evidence is not None else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SourceRecord":
        kw = {f.name: d.get(f.name) for f in dataclasses.fields(cls) if f.name != "evidence"}
        for k in ("declared", "observed", "validation"):
            kw[k] = kw.get(k) or {}
        ev = d.get("evidence")
        return cls(**kw, evidence=evidence_from_dict(ev) if ev else None)


def _digest(obj) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode())


@dataclass(frozen=True)
class Manifest:
    created_at: str
    core: tuple[SourceRecord, ...]
    owner: tuple[SourceRecord, ...] = ()
    joins: dict = field(default_factory=dict)
    selection_origin: str = "mtime_fallback"
    policy_version: str = ACC.POLICY_VERSION
    dashboard_sha256: str | None = None          # the exact HTML bytes published with this generation (GET / checks)

    @property
    def core_id(self) -> str:
        """Digest of the policy, every core record's full content and the join report (see the module docstring)."""
        recs = sorted((r.content() for r in self.core), key=lambda c: (c["key"], c["sha256"]))
        return _digest([self.policy_version, self.selection_origin, recs, _jsonable(self.joins)])

    @property
    def generation_id(self) -> str:
        recs = sorted((r.content() for r in self.owner), key=lambda c: (c["key"], c["sha256"]))
        return _digest([self.core_id, self.dashboard_sha256, recs])

    def record(self, key: str) -> SourceRecord | None:
        for r in (*self.core, *self.owner):
            if r.key == key:
                return r
        return None

    def with_owner(self, key: str, record: SourceRecord | None) -> "Manifest":
        owner = tuple(r for r in self.owner if r.key != key) + ((record,) if record is not None else ())
        return dataclasses.replace(self, owner=tuple(sorted(owner, key=lambda r: r.key)))

    def bound_snapshot(self, served: Mapping[str, str | None]) -> ACC.EvidenceSnapshot:
        """Evidence ONLY for sources whose served fingerprint equals the recorded one. Every other recorded source is
        listed as unbound (its evidence belongs to another generation and is never used)."""
        ev, unbound = {}, set()
        for r in (*self.core, *self.owner):
            if r.evidence is None:
                continue
            if served.get(r.key) is not None and served.get(r.key) == r.sha256:
                ev[r.key] = r.evidence
            else:
                unbound.add(r.key)
        return ACC.EvidenceSnapshot(self.generation_id, ev, None, frozenset(unbound))

    def core_snapshot(self) -> ACC.EvidenceSnapshot:
        """For the dashboard build, which renders exactly these core bytes (owner data is not in the HTML)."""
        return self.bound_snapshot({r.key: r.sha256 for r in self.core})

    def to_dict(self) -> dict:
        return {"schema": MANIFEST_SCHEMA, "created_at": self.created_at, "policy_version": self.policy_version,
                "selection_origin": self.selection_origin, "core_id": self.core_id,
                "generation_id": self.generation_id, "dashboard_sha256": self.dashboard_sha256,
                "core": [r.to_dict() for r in self.core],
                "owner": [r.to_dict() for r in self.owner], "joins": self.joins}

    @classmethod
    def from_dict(cls, d: dict) -> "Manifest":
        if d.get("schema") != MANIFEST_SCHEMA:
            raise ValueError("schema")
        m = cls(created_at=d["created_at"], core=tuple(SourceRecord.from_dict(x) for x in d.get("core") or ()),
                owner=tuple(SourceRecord.from_dict(x) for x in d.get("owner") or ()), joins=d.get("joins") or {},
                selection_origin=d.get("selection_origin") or "mtime_fallback",
                policy_version=d.get("policy_version") or "", dashboard_sha256=d.get("dashboard_sha256"))
        if m.core_id != d.get("core_id") or m.generation_id != d.get("generation_id"):
            raise ValueError("manifest identity mismatch")       # corrupted (unkeyed checksum): never a candidate
        return m


# ---------------------------------------------------------------------------------------------
# building the core part (the refresh calls this on the frozen bytes it validated)
# ---------------------------------------------------------------------------------------------
def _date_checks(key: str, declared: dict, observed: dict, today: dt.date) -> tuple[int, dt.date | None]:
    """Contradictions between DECLARED and OBSERVED dates (counts). Returns (contradictions, report_date)."""
    bad = 0
    snap = declared.get("snapshot_date")
    rep = declared.get("report_date")
    report_date = snap or rep
    for d in (snap, rep):
        if d is not None and d > today:
            bad += 1
    if key == "sales":
        latest, earliest = observed.get("latest_transaction_date"), observed.get("earliest_transaction_date")
        ps, pe = declared.get("period_start"), declared.get("period_end")
        if pe is not None and latest is not None and latest > pe:
            bad += 1                                   # rows after the declared period
        if ps is not None and earliest is not None and earliest < ps:
            bad += 1                                   # rows before the declared period
        if rep is not None and latest is not None and latest > rep:
            bad += 1                                   # transactions after the report date
    if key == "aging":
        lb = observed.get("latest_bucket")
        if snap is not None and lb is not None and tuple(lb) > (snap.year, snap.month):
            bad += 1                                   # a bucket newer than the declared snapshot
    return bad, report_date


def build_core_records(frozen: Mapping[str, FrozenSource], frames: Mapping[str, Any], declarations: Mapping[str, dict],
                       origin: str, today: dt.date | None = None) -> tuple[tuple[SourceRecord, ...], dict]:
    """Formula-aware inspection + joins + declared/observed dates for each frozen core source. Never raises."""
    from services import source_validation as SV
    today = today or dt.date.today()
    insp = {k: SV.inspect_core(k, fs.data) for k, fs in frozen.items()}
    pandas_ev = ACC.collect_core_evidence(frames.get("stock"), frames.get("sales"), frames.get("aging"), today=today)
    master = None
    if "sales" in frozen:
        try:
            import pandas as pd
            master = pd.read_excel(frozen["sales"].stream(), sheet_name="liste")
        except Exception:
            master = None
    try:
        joins = SV.validate_joins(frames.get("sales"), frames.get("stock"), frames.get("aging"), master)
    except Exception as e:
        joins = {"failure": type(e).__name__}
    jev = SV.join_evidence(joins) if "failure" not in joins else {}
    jchecks = SV.join_checks(joins) if "failure" not in joins else None
    records = []
    for key, fs in sorted(frozen.items()):
        ins = insp[key]
        declared = dict(declarations.get(key) or {})
        observed: dict = {}
        if key == "sales" and frames.get("sales") is not None:
            pev = pandas_ev.get("sales")
            observed["latest_transaction_date"] = pev.latest_transaction_date if pev else None   # max(Tarih)
            try:
                import pandas as pd
                dates = pd.to_datetime(frames["sales"]["Tarih"], errors="coerce")
                observed["earliest_transaction_date"] = dates.min().date() if dates.notna().any() else None
            except Exception:
                observed["earliest_transaction_date"] = None
        if key == "aging":
            observed["latest_bucket"] = ins.latest_bucket
        contradictions, report_date = _date_checks(key, declared, observed, today)
        cari = _cari_evidence(fs, frames.get("aging")) if key == "aging" else None
        if cari is not None:
            observed["cari"] = cari
        pdup = pandas_ev.get(key)
        fails = tuple(x for x in ((ins.failure,) if not ins.ok else ()) if x)
        pc = declared.get("period_complete")
        period_complete = (True if pc is True and contradictions == 0 else (False if pc is False else None))
        j = jev.get(key, {})
        ap = declared.get("approval")
        applies = bool(ap) and ap.get("sha256") == fs.sha256
        # M8-ACCURACY-2B: stock rows — duplicates among real product codes only (blank identities are counted as
        # missing identity, never as duplicates); a recognized footer whose cached total contradicts the rows it
        # covers makes the source's totals contradictory (INVALID)
        rows = ins.rows if (key == "stock" and ins.rows is not None and ins.rows.ok) else None
        dup = (rows.duplicate_code_occurrences if rows is not None
               else (pdup.duplicate_records if pdup is not None else None))
        # … and a footer reconciliation that is only PARTIAL (error-propagated / missing cache / unsupported /
        # ambiguous) caps the totals judgement at "not verified": no declaration can turn it into True
        # (an export without any footer row has no footer evidence: the declaration alone decides, as before)
        fstat = rows.footer_status() if (rows is not None and rows.footer_checks.get("checked")) else None
        declared_totals = ap.get("totals_consistent") if applies else None
        if fstat == "FAIL":
            totals = False
        elif fstat == "OPEN":
            totals = None
        else:
            totals = declared_totals
        mapping = ap.get("mapping_approved") if applies else None
        if cari is not None:
            # D-072 D3: the Cari export is authoritative and has no workbook-native total row; its balances are
            # reproduced by a second, independent reader of the same records. An explicit False declaration still
            # wins (fail closed); a mismatch is a contradiction (INVALID); a failed reproduction proves nothing.
            if declared_totals is not False and cari["reproduction"] == "match":
                totals = True
            elif cari["reproduction"] == "mismatch":
                totals = False
            # D-072 + D-071: the field-level check of the inputs the metric reads fixes column identity (USD not read)
            if mapping is not False and cari["template"] == "approved":
                mapping = True
        # the strict verdict above stays source-wide; these keep its origin apart for scoped metrics (review 2)
        fscope = (rows.footer_scope() if rows is not None
                  else ("source_wide" if (key == "stock" and ins.rows is not None) else None))
        observed["approval_status"] = ("applied" if applies else ("fingerprint_mismatch" if ap else "not_supplied"))
        ev = ACC.SourceEvidence(
            key, available=True, layout_supported=ins.layout_supported, report_date=report_date,
            report_date_origin=(declared.get("date_origin") or "declared") if report_date else None,
            date_kind=DATE_KINDS[key],
            period_start=declared.get("period_start"), period_end=declared.get("period_end") or report_date,
            period_complete=period_complete, latest_transaction_date=observed.get("latest_transaction_date"),
            earliest_transaction_date=observed.get("earliest_transaction_date"),
            error_cells=ins.error_cells, missing_cached_formulas=ins.missing_cached_formulas,
            missing_required_values=ins.missing_required_values, blank_amount_cells=ins.blank_amount_cells,
            invalid_dates=ins.invalid_dates, ambiguous_headers=ins.ambiguous_headers,
            external_references=(None if ins.external_links is None or ins.external_references is None
                                 else ins.external_links + ins.external_references),
            duplicate_records=dup,
            join_unmatched=j.get("join_unmatched"), join_ambiguous=j.get("join_ambiguous"),
            joins_validated=j.get("joins_validated"),
            totals_consistent=totals, declared_totals_consistent=declared_totals, footer_totals=fstat,
            footer_scope=fscope,
            fields=ins.fields() if ins.ok else None, joins=jchecks,
            mapping_approved=mapping,
            date_contradictions=contradictions, selection_explicit=(origin == "explicit"),
            inspection_failures=fails + (pdup.inspection_failures if pdup is not None else ()))
        records.append(SourceRecord(key, "core", SOURCE_TYPES[key], DATE_KINDS[key], fs.file, fs.sha256, fs.size,
                                    origin, declared, observed, ins.summary(), ev, fs.file_modified))
        if key == "sales":
            j2 = jev.get("liste", {})
            mev = ACC.SourceEvidence(
                "liste", available=master is not None, layout_supported=(master is not None and "KODU" in master.columns)
                if master is not None else None, report_date=report_date,
                report_date_origin="declared" if report_date else None, date_kind=DATE_KINDS["liste"],
                join_unmatched=j2.get("join_unmatched"), join_ambiguous=j2.get("join_ambiguous"),
                joins_validated=j2.get("joins_validated"), selection_explicit=(origin == "explicit"), joins=jchecks)
            records.append(SourceRecord("liste", "core", SOURCE_TYPES["liste"], DATE_KINDS["liste"], fs.file,
                                        fs.sha256, fs.size, origin, declared, {}, {"master_available": master is not None},
                                        mev, fs.file_modified))
    return tuple(records), joins


def _cari_evidence(fs: "FrozenSource", frame) -> dict | None:
    """D-072: the column identity of the fields the Cari metric reads (`cari_policy.mapping_check`, confirmed against
    the columns the calculation actually reads) and an independent reproduction of every account balance (openpyxl on
    the frozen bytes vs the pipeline's pandas result). Counts only; never raises."""
    from services import cari_policy as CARI
    try:
        import dashboard_builder as db
        head, independent = CARI.independent_balances(fs.data)
        mapping = CARI.mapping_check(head)
        if mapping["status"] == "approved":
            cols = [str(c).strip() for c in frame.columns] if frame is not None else []
            if (CARI.calculation_buckets(cols) != mapping["bucket_headers"]
                    or any(cols.count(f) != 1 for f in CARI.REQUIRED_FIELDS)):
                mapping = {**mapping, "status": "not_matched", "reason": "calculation_columns_differ"}
        rows, pipeline = db.m_collections(None, frame) if frame is not None else ([], None)
    except Exception as e:
        return {"template": "unknown", "reproduction": "failed", "failure": type(e).__name__}
    mapping.pop("bucket_headers", None)
    if pipeline is None:
        return {"template": mapping["status"], "mapping": mapping, "reproduction": "failed", "failure": "no_frame"}
    keys = set(independent) | set(pipeline)
    mismatches = sum(1 for k in keys if independent.get(k, "missing") != pipeline.get(k, "missing"))
    ok = bool(keys) and mismatches == 0
    return {"template": mapping["status"], "mapping": mapping,
            "reproduction": "match" if ok else ("mismatch" if keys else "failed"),
            "accounts": len(pipeline), "independent_accounts": len(independent), "mismatches": mismatches,
            "unknown_balances": sum(1 for v in pipeline.values() if v is None),
            "classes": {c: sum(1 for r in rows if r["account_class"] == c) for c in
                        (CARI.CLASS_CUSTOMER, CARI.CLASS_SUPPLIER, CARI.CLASS_UNCONFIRMED)}}


def build_manifest(frozen, frames, declarations, origin, today=None, owner: tuple[SourceRecord, ...] = ()) -> Manifest:
    core, joins = build_core_records(frozen, frames, declarations, origin, today)
    return Manifest(dt.datetime.now().isoformat(timespec="seconds"), core, tuple(owner), _jsonable(joins), origin)


def rederive_core(data_folder: str, candidate: Manifest, today: dt.date | None = None):
    """Re-derive a candidate generation's core evaluation from the ORIGINAL bytes (restart / rebuild).

    Returns (manifest, paths, frames) built from the files the current selection names — or None when the selection
    no longer names the recorded files, a file is missing, or a fingerprint differs. The caller compares the returned
    manifest's `core_id` with the candidate's; nothing from the candidate's saved validation or evidence is reused."""
    recs = {r.key: r for r in candidate.core if r.key != "liste"}
    try:
        paths, declarations, origin = select_core(data_folder)
    except SelectionError:
        return None
    if origin != candidate.selection_origin:
        return None
    chosen = {k: p for k, p in paths.items() if p}
    if set(chosen) != set(recs) or any(os.path.basename(chosen[k]) != recs[k].file for k in recs):
        return None
    try:
        frozen = freeze(chosen)
    except OSError:
        return None
    if any(frozen[k].sha256 != recs[k].sha256 for k in recs):
        return None
    frames = frames_from_frozen(frozen)
    m = build_manifest(frozen, frames, declarations, origin, today)
    return m, {k: paths.get(k) for k in CORE_KEYS}, frames


# ---------------------------------------------------------------------------------------------
# owner records (built by the owner loaders from the exact bytes they imported)
# ---------------------------------------------------------------------------------------------
def _issue_total(report, codes) -> int:
    n = 0
    for i in getattr(report, "issues", []):
        if i.code in codes:
            n += int(i.count or 1)
    return n


def owner_record(key: str, frozen: FrozenSource, result, *, declared: dict | None = None,
                 today: dt.date | None = None, origin: str = "env_path") -> SourceRecord:
    """Evidence for one owner source from its importer result (counts only). Unknown facts stay None."""
    declared = dict(declared or {})
    report = getattr(result, "report", None)
    ok = bool(getattr(result, "ok", False))
    counts = getattr(result, "counts", None)
    failed_layout = getattr(result, "failed_step", None) == "layout"
    totals = None
    if key == "kredi" and counts is not None:
        ctrl, filt = counts.control_totals_match, counts.filtered_totals_match
        totals = False if (ctrl is False or filt is False) else (True if ctrl is True and filt is not False else None)
    elif key == "gider_gelir" and report is not None:
        bad = _issue_total(report, {"annual_total_mismatch", "summary_expense_pivot_mismatch"})
        totals = False if bad else None
    snap = declared.get("snapshot_date")
    ev = ACC.SourceEvidence(
        key, available=True, layout_supported=(False if failed_layout else (True if ok else None)),
        report_date=snap, report_date_origin="declared" if snap else None, date_kind=DATE_KINDS[key],
        period_complete=None,
        error_cells=_issue_total(report, {"excel_error"}) if report is not None else None,
        missing_cached_formulas=_issue_total(report, {"formula_missing_cache"}) if report is not None else None,
        missing_required_values=_issue_total(report, {"missing_required_value", "null_values"}) if report else None,
        duplicate_records=_issue_total(report, {"duplicate_installment"}) if report is not None else None,
        external_references=(getattr(report.external_links, "count", 0) + getattr(report.external_links, "formula_refs", 0))
        if report is not None else None,
        joins_validated=(True if (key == "kredi" and ok) else None), join_unmatched=0 if key == "kredi" and ok else None,
        join_ambiguous=0 if key == "kredi" and ok else None, totals_consistent=totals, mapping_approved=None,
        date_contradictions=(1 if (snap is not None and today is not None and snap > today) else 0),
        selection_explicit=True, invalid_dates=None, blank_amount_cells=None, ambiguous_headers=None,
        inspection_failures=(() if ok else (f"import:{getattr(result, 'failed_step', None) or 'failed'}",)))
    validation = {"ok": ok, "failed_step": getattr(result, "failed_step", None)}
    if key == "kredi" and counts is not None:
        validation.update({k: getattr(counts, k) for k in (
            "layout", "installments", "contracts", "control_total_row", "control_totals_match", "saved_filter",
            "hidden_installment_rows", "visible_installment_rows", "subtotal_row", "filtered_totals_match",
            "negative_remaining_rows", "closed_with_positive_remaining")})
    return SourceRecord(key, "owner", SOURCE_TYPES[key], DATE_KINDS[key], frozen.file, frozen.sha256, frozen.size,
                        origin, declared, {}, validation, ev, frozen.file_modified)


# ---------------------------------------------------------------------------------------------
# the store shared by dashboard / API / owner views / refresh
# ---------------------------------------------------------------------------------------------
def manifest_path(dashboard_path: str) -> str:
    root, _ = os.path.splitext(dashboard_path)
    return root + ".generation.json"


_GEN_RE = re.compile(r'<meta name="stockagent-generation" content="([0-9a-f]*)"')


_DISPLAYED_RE = re.compile(rb'<meta name="stockagent-displayed" content="([^"]*)"')
_GEN_RE_B = re.compile(rb'<meta name="stockagent-generation" content="([0-9a-f]*)"')


def dashboard_markers(body: bytes) -> tuple[str | None, tuple[str, ...] | None]:
    """(generation core_id, displayed metric keys) read from the exact HTML bytes. `displayed` is None when the
    marker is absent (a dashboard built before this rule: its contents cannot be re-authorized)."""
    head = body[:16384]
    g = _GEN_RE_B.search(head)
    d = _DISPLAYED_RE.search(head)
    gen = (g.group(1).decode() or None) if g else None
    if d is None:
        return gen, None
    import html as _html
    raw = _html.unescape(d.group(1).decode("utf-8", "replace"))
    return gen, tuple(k for k in raw.split(",") if k)


def dashboard_generation(dashboard_path: str) -> str | None:
    try:
        with open(dashboard_path, encoding="utf-8") as fh:
            m = _GEN_RE.search(fh.read(8192))
        return (m.group(1) or None) if m else None
    except OSError:
        return None


def write_manifest(path: str, manifest: Manifest) -> None:
    """Atomic write (tmp + os.replace). Contains no workbook values."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".gen-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest.to_dict(), fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def read_manifest(path: str) -> Manifest | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return Manifest.from_dict(json.load(fh))
    except (OSError, ValueError, KeyError, TypeError):
        return None


class GenerationStore:
    """The ONE current manifest. Installed atomically with the context it describes; owner records replaced as the
    owner repository publishes them. Readers get an immutable Manifest (never a half-updated one).

    A manifest read from disk at restart is only a `pending` candidate: it gives no evidence until the context build
    re-derives it from the original bytes and `accept_rederived` installs the re-derived one."""

    def __init__(self, manifest: Manifest | None = None) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._m = manifest
        self._pending: Manifest | None = None
        self._dash_pending: str | None = None        # core_id whose dashboard re-publication is expected (2B)
        self.restore_reason: str | None = None
        self._observers: list = []                   # M8-VKM-1: notified after a generation is ACCEPTED
        self._hold_tid: int | None = None            # M8-CP3: notifications held during one publication
        self._held: list = []

    # -- M8-VKM-1: observers of accepted generations (Veri Kontrol Merkezi) ---------------------------------------
    def add_observer(self, fn) -> None:
        """`fn(kind, manifest, key, store)` is called after a core generation is installed or re-derived and accepted
        (kind "core") and after an owner source's record is replaced (kind "owner", key). Called outside this store's
        lock; an observer's failure never affects publication. Observers only READ the manifest."""
        self._observers.append(fn)

    def _notify(self, kind: str, manifest: "Manifest | None", key: str | None = None) -> None:
        with self._lock:
            if self._hold_tid is not None and self._hold_tid == threading.get_ident():
                self._held.append((kind, manifest, key))      # delivered only if the publication commits
                return
        self._deliver(kind, manifest, key)

    def _deliver(self, kind: str, manifest: "Manifest | None", key: str | None = None) -> None:
        for fn in list(self._observers):
            try:
                fn(kind, manifest, key, self)
            except Exception:
                pass

    # -- M8-CP3: one multi-step publication (core + owner) is all-or-nothing --------------------------------------
    def hold_notifications(self) -> None:
        """Queue the observer notifications raised by THIS thread (the publisher, holding PUBLISH_LOCK) until
        `release_notifications`. A publication that is rolled back therefore never reaches the observers (no VKM
        observation, no history event). Other threads are unaffected."""
        with self._lock:
            self._hold_tid, self._held = threading.get_ident(), []

    def release_notifications(self, *, deliver: bool) -> None:
        """End the hold of this thread: deliver the queued notifications in order (commit) or drop them (rollback).
        A no-op when this thread holds nothing."""
        with self._lock:
            if self._hold_tid != threading.get_ident():
                return
            held, self._held, self._hold_tid = self._held, [], None
        if deliver:
            for kind, manifest, key in held:
                self._deliver(kind, manifest, key)

    def snapshot_state(self) -> tuple:
        """The served / pending generation state (immutable manifests), for a publication's rollback."""
        with self._lock:
            return (self._m, self._pending, self._dash_pending, self.restore_reason)

    def restore_state(self, state: tuple) -> None:
        """Put back a `snapshot_state` exactly (rollback of a failed publication). Notifies no observer."""
        with self._cond:
            self._m, self._pending, self._dash_pending, self.restore_reason = state
            self._cond.notify_all()

    # -- dashboard re-publication after a restart (M8-ACCURACY-2B) ---------------------------------------------
    @property
    def dashboard_pending(self) -> str | None:
        with self._lock:
            return self._dash_pending

    def dashboard_failed(self, core_id: str) -> None:
        """The expected re-publication for `core_id` will not happen (failed / nobody publishes)."""
        with self._cond:
            if self._dash_pending == core_id:
                self._dash_pending = None
                self._cond.notify_all()

    def wait_dashboard(self, timeout: float) -> bool:
        """Wait (bounded) while the CURRENT generation's dashboard is being re-published. True when nothing is
        pending any more (published, failed or superseded); False on timeout. Never waits without a limit."""
        import time as _t
        deadline = _t.monotonic() + max(0.0, timeout)
        with self._cond:
            while (self._dash_pending is not None and self._m is not None and self._m.core_id == self._dash_pending):
                left = deadline - _t.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            return True

    def current(self) -> Manifest | None:
        with self._lock:
            return self._m

    @property
    def pending(self) -> Manifest | None:
        with self._lock:
            return self._pending

    def candidate(self) -> Manifest | None:
        """The generation a context build should re-derive: a restart candidate, else the published one."""
        with self._lock:
            return self._pending or self._m

    def accept_rederived(self, candidate: Manifest, rederived: Manifest) -> Manifest | None:
        """Install `rederived` iff it reproduces the candidate's identity exactly (sources, declarations, validation,
        evidence, joins). Otherwise fail closed: no core generation, reason `evidence_mismatch`."""
        with self._lock:
            if candidate is not self._pending and candidate is not self._m:
                return None                                       # superseded meanwhile (a refresh published)
            if rederived.core_id != candidate.core_id:
                self._pending = None
                if candidate is self._m:
                    self._m = self._owner_only(self._m)
                self.restore_reason = "evidence_mismatch"
                return None
            if self._m is not None and not rederived.owner:
                rederived = dataclasses.replace(rederived, owner=self._m.owner)
            # the dashboard file on disk is NOT carried over: its recorded digest came from the (untrusted) saved file.
            # Until the server re-publishes a dashboard built from the re-derived evidence (`set_dashboard`), GET /
            # serves no dashboard bytes; the re-publication is now EXPECTED (GET / may wait for it, bounded).
            rederived = dataclasses.replace(rederived, dashboard_sha256=None)
            self._m, self._pending = rederived, None
            self._dash_pending = rederived.core_id
            self.restore_reason = None
        self._notify("core", rederived)
        return rederived

    def set_dashboard(self, core_id: str, dashboard_sha256: str) -> Manifest | None:
        """Record the dashboard bytes this process published for the current generation `core_id` (owner records
        kept). None when another core generation is current meanwhile."""
        with self._lock:
            if self._m is None or self._m.core_id != core_id:
                return None
            self._m = dataclasses.replace(self._m, dashboard_sha256=dashboard_sha256)
            if self._dash_pending == core_id:
                self._dash_pending = None
                self._cond.notify_all()
            return self._m

    def reject_candidate(self, candidate: Manifest, reason: str) -> None:
        with self._lock:
            if candidate is self._pending:
                self._pending = None
            if candidate is self._m:
                self._m = self._owner_only(self._m)
            self.restore_reason = reason

    @staticmethod
    def _owner_only(m: Manifest | None) -> Manifest | None:
        if m is None or not m.owner:
            return None
        return Manifest(dt.datetime.now().isoformat(timespec="seconds"), (), m.owner)

    def install(self, manifest: Manifest | None, *, keep_owner: bool = True) -> Manifest | None:
        """A new CORE generation. Owner records of the previous manifest are carried over only as records of their own
        (still-served) revisions; the served-fingerprint check decides whether they bind."""
        with self._lock:
            if manifest is not None and keep_owner and self._m is not None and not manifest.owner:
                manifest = dataclasses.replace(manifest, owner=self._m.owner)
            self._m = manifest
            self._pending = None                                  # a published generation supersedes a candidate
            self._dash_pending = None                             # … and any expected re-publication (2B)
            self._cond.notify_all()
        if manifest is not None:
            self._notify("core", manifest)
        return manifest

    def replace_owner(self, key: str, record: SourceRecord | None) -> Manifest:
        """An owner source was (re)loaded: its record (or None) replaces the previous one. Without a published core
        generation, a manifest with no core records holds the owner records."""
        with self._lock:
            base = self._m or Manifest(dt.datetime.now().isoformat(timespec="seconds"), ())
            self._m = base.with_owner(key, record)
            m = self._m
        self._notify("owner", m, key)
        return m

    def restore(self, path: str, dashboard_path: str, data_folder: str) -> Manifest | None:
        """Restart: a manifest becomes a PENDING candidate only if it is intact, the served dashboard was built from
        it and every core file still has its recorded fingerprint. It provides no evidence until the context build
        re-derives it (`ContextRepository._build` → `rederive_core` → `accept_rederived`). Owner records are not
        restored (the owner sources are re-read and re-recorded on their first load)."""
        m = read_manifest(path)
        if m is None:
            self.restore_reason = "no_manifest_or_corrupted" if os.path.exists(path) else "no_manifest"
            return None
        if dashboard_generation(dashboard_path) != m.core_id:
            self.restore_reason = "dashboard_generation_mismatch"
            return None
        if not m.dashboard_sha256 or sha256_file(dashboard_path) != m.dashboard_sha256:
            self.restore_reason = "dashboard_bytes_mismatch"      # not the HTML published with this generation
            return None
        for r in m.core:
            if r.key == "liste":
                continue
            p = os.path.join(data_folder, r.file)
            if not os.path.isfile(p) or sha256_file(p) != r.sha256:
                self.restore_reason = "source_changed"
                return None
        m = dataclasses.replace(m, owner=())
        with self._lock:
            self._pending = m
        self.restore_reason = "pending_revalidation"
        return m


__all__ = ["FrozenSource", "SourceRecord", "Manifest", "GenerationStore", "SelectionError", "load_selection",
           "select_core", "freeze", "build_core_records", "build_manifest", "rederive_core", "dashboard_markers", "owner_record", "manifest_path",
           "write_manifest", "read_manifest", "dashboard_generation", "sha256_bytes", "sha256_file", "CORE_KEYS",
           "OWNER_KEYS", "SELECTION_FILE", "SELECTION_SCHEMA", "OWNER_SELECTION_FILE", "OWNER_SELECTION_SCHEMA",
           "OWNER_OPTION_FIELDS", "write_json_atomic", "core_selection_document", "owner_selection_document",
           "load_owner_selection"]
