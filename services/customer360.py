"""M7-1 — Customer 360: a dedicated, deterministic, full-customer repository (docs/m7_customer_360.md, D-042).

Not the minimized agent context: the agent context stays top-N and AI-facing; Customer 360 indexes EVERY canonical
customer of the live sales workbook, privately, for the local UI. No AI provider is imported or called here.

Sources (all shared-audience; owner sources Kredi / Gider-Gelir / Bütçe and Trodat are never used):
- the exact sales workbook the LIVE M5 context was built from (basename from the context, resolved inside the
  configured data folder) — commercial history; its max `Tarih` must equal the context's `as_of_data_date`;
- the `liste` sheet of that same workbook — customer-master attributes, exact match on the canonical code (DQ-5-safe:
  the sales file's cached `Temsilci` / `Açık / Kapalı` / `Sektör` lookups are not used);
- the live context's Cari aging file, if any — invoice-account grain only;
- the M6-2 order-source importer on the same workbook — event / fair history;
- Hedef ONLY from an explicitly configured path (`HEDEF_PATH`, optional `HEDEF_SNAPSHOT_DATE`).

Cache: one snapshot per live-source signature (sales basename, as-of date, aging basename, context generated_at,
Hedef configuration). Any change → the cached snapshot is dropped at once (never served stale) and rebuilt.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

import dashboard_builder as db  # noqa: E402
from services import sales_projection as SP  # noqa: E402
from services.importers.base import run_import  # noqa: E402
from services import generation as GEN  # noqa: E402
from services import request_pin as RP  # noqa: E402
from services.importers.models import Audience, Confidence, Severity  # noqa: E402
from services.importers.spec import FieldSpec, FieldType, SourceSpec, TableLayout  # noqa: E402

log = logging.getLogger("customer360")

AMOUNT_LABEL = "Net Sipariş Tutarı (Nettutar)"
AMOUNT_NOTE = ("Satış raporunda kayıtlı sipariş net tutarıdır (sipariş edilen miktar üzerinden). Kanonik ciro "
               "esası (sipariş / teslim) henüz karara bağlanmadı; bu bir ciro tanımı değildir.")
RECENT_WINDOW_DAYS = 365
TOP_PRODUCTS = 10
TOP_MIX = 8
RECENT_EVENTS = 10
SEARCH_MAX = 50
SEARCH_DEFAULT = 20
SEARCH_MIN_CHARS = 2
MASTER_OPEN, MASTER_CLOSED = "Açık", "Kapalı"   # exact `CARİ FİRMA` values (verified on the real master)
SHARED_ACCOUNT_WARNING = ("Bu fatura hesabı birden fazla sipariş müşterisi tarafından kullanılıyor; alacak bu "
                          "müşteriye tek başına atanamaz.")
AGING_LIMITATIONS = [
    "Cari yaşlandırma fatura hesabı düzeyindedir; sipariş müşterisi (Pro_kodu) düzeyinde değildir.",
    "Belge vade tarihi, belge bazında gecikme günü, belge tipi ve belge bazlı tahsilat önceliği bu dosyada yoktur.",
    "Detaylı tahsilat raporu sağlanmadı.",
]
DETAILED_COLLECTIONS_NOTE = "Detaylı tahsilat raporu sağlanmadı."

SALES_REQUIRED = SP.SALES_REQUIRED          # M7-6: defined in services/sales_projection.py

_T, _U = Confidence.TRUSTED, Confidence.UNVERIFIED
_TOL = dict(required=False, tolerate_invalid_cells=True)
#: The `liste` sheet of the live sales workbook. Code and open/closed status are `trusted`; sector and rep are
#: `unverified` (current assignment only, D-034). The workbook itself was already selected by the live context.
MASTER_SPEC = SourceSpec(
    key="c360_customer_master",
    audience=Audience.SHARED,
    filename_patterns=("*.xlsx",),
    sheet="liste",
    layout=TableLayout(header_row=1),
    fields=(
        FieldSpec("code", FieldType.OPAQUE_ID, header="KODU", confidence=_T, required=True, tolerate_invalid_cells=True),
        FieldSpec("name", FieldType.STRING, header="ÜNVANI 1", confidence=_T, **_TOL),
        FieldSpec("status", FieldType.STRING, header="CARİ FİRMA", confidence=_T, **_TOL),
        FieldSpec("sector", FieldType.STRING, header="SEKTÖR", confidence=_U, **_TOL),
        FieldSpec("rep", FieldType.STRING, header="TEMSİLCİ", confidence=_U, **_TOL),
    ),
    default_confidence=_U,
    missing_cache=Severity.WARNING,
    description="Customer master (liste) for Customer 360 attributes (exact code match).",
)


class Customer360Error(Exception):
    """A safe, path-free failure: `code` is fixed, `message` is fixed Turkish text."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


class Customer360NotReady(Exception):
    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


def fold(text: Any) -> str:
    """UI text-search folding (not entity resolution): NFC, Turkish-aware lower case, dotted/dotless i treated alike."""
    s = unicodedata.normalize("NFC", str(text or ""))
    s = s.replace("İ", "i").replace("I", "ı").lower()
    return s.replace("i̇", "i").replace("ı", "i")


def _iso(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NaT:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(x).date().isoformat()


def _num(x) -> float | None:
    try:
        if x is None or pd.isna(x):
            return None
    except (TypeError, ValueError):
        return None
    return float(x)


def _text(x) -> str | None:
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    s = str(x)
    return s if s.strip() else None


# ---------------------------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceSignature:
    sales: str | None
    as_of: str | None
    aging: str | None
    generated_at: str | None
    hedef_path: str | None
    hedef_snapshot: str | None


def signature_of(ctx: dict, hedef_path: str | None, hedef_snapshot: str | None) -> SourceSignature:
    sf = (ctx or {}).get("source_files") or {}
    return SourceSignature(sf.get("sales"), (ctx or {}).get("as_of_data_date"), sf.get("aging"),
                           (ctx or {}).get("generated_at"), hedef_path or None, hedef_snapshot or None)


def _in_folder(folder: str, basename: str | None) -> str | None:
    """Resolve a context basename inside the data folder. Only a plain basename is accepted (no traversal)."""
    if not basename or os.path.basename(basename) != basename or basename in (".", ".."):
        return None
    p = os.path.join(folder, basename)
    return p if os.path.isfile(p) else None


# ---------------------------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------------------------
@dataclass
class MasterEntry:
    rows: list[dict]

    @property
    def duplicate(self) -> bool:
        return len(self.rows) > 1


@dataclass
class Customer360Snapshot:
    signature: SourceSignature
    as_of: dt.date
    sales_file: str
    aging_file: str | None
    built_ms: int
    customers: dict[str, dict]                 # code → base facts (names, dates, counts)
    search_rows: list[tuple]                   # (code, display_name, code_fold, name_fold)
    years: pd.DataFrame                        # index (_cust, _yr)
    products: pd.DataFrame                     # index _cust (sorted, top N per customer already cut)
    categories: pd.DataFrame
    groups: pd.DataFrame
    accounts: pd.DataFrame                     # index (_cust, _inv)
    account_customers: dict[str, list[str]]    # invoice account → canonical customers (sorted)
    account_names: dict[str, str | None]
    salespeople: dict[str, list[tuple[str, int]]]
    master_status: str                         # ok | unavailable
    master: dict[str, MasterEntry]
    aging_status: str                          # ok | not_supplied | failed
    aging: dict[str, dict]                     # invoice account → {overdue, oldest, present}
    events_status: str                         # ok | failed
    events: dict[str, dict]
    hedef_status: str                          # not_configured | ok | failed
    hedef_meta: dict
    hedef: dict[str, Any]
    diagnostics: dict = field(default_factory=dict)

    # ---- search ----------------------------------------------------------------------------
    def search(self, query: str, limit: int = SEARCH_DEFAULT) -> tuple[list[dict], bool]:
        """Deterministic UI search: exact code, code prefix, name prefix, name substring, code substring; ties by
        folded name then code. No fuzzy scoring, no ranking by sales."""
        q = (query or "").strip()
        limit = max(1, min(int(limit or SEARCH_DEFAULT), SEARCH_MAX))
        if len(q) < SEARCH_MIN_CHARS:
            return [], False
        qf = fold(q)
        hits = []
        for code, name, cf, nf in self.search_rows:
            if code == q or cf == qf:
                rank, kind = 0, "exact_code"
            elif cf.startswith(qf):
                rank, kind = 1, "code_prefix"
            elif nf.startswith(qf):
                rank, kind = 2, "name_prefix"
            elif qf in nf:
                rank, kind = 3, "name_substring"
            elif qf in cf:
                rank, kind = 4, "code_substring"
            else:
                continue
            hits.append((rank, nf, code, name, kind))
        hits.sort(key=lambda h: (h[0], h[1], h[2]))
        out = []
        for rank, _, code, name, kind in hits[:limit]:
            c = self.customers[code]
            out.append({"customer_code": code, "display_name": name, "closed": self.master_closed(code),
                        "last_order_date": c["last_order_date"], "match": kind})
        return out, len(hits) > limit

    # ---- master -----------------------------------------------------------------------------
    def master_entry(self, code: str) -> MasterEntry | None:
        return self.master.get(code)

    def master_closed(self, code: str) -> bool | None:
        """KAPALI from the master only, and only when the code resolves to exactly one master row."""
        e = self.master.get(code)
        if e is None or e.duplicate:
            return None
        st = e.rows[0].get("status")
        return True if st == MASTER_CLOSED else False if st == MASTER_OPEN else None

    def summary(self) -> dict:
        """Structural diagnostics only: counts, basenames, dates, statuses. No codes, names, labels or amounts."""
        return dict(self.diagnostics)


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------
def _same_generation(data_folder: str, ctx, sig, t0: float | None = None) -> int:
    """Customer 360 reads the sales / Cari files itself (formula-aware imports). When the context carries a source
    generation, each file must have the recorded fingerprint before AND after the build; otherwise the snapshot is
    refused (never a mix of revisions). Returns the elapsed build time in ms (for the snapshot)."""
    served = ((getattr(ctx, "generation", None) or {}).get("served")) or {}
    for key, fname in (("sales", sig.sales), ("aging", sig.aging)):
        exp = served.get(key)
        if not exp or not fname:
            continue
        p = _in_folder(data_folder, fname)
        if p is None or GEN.sha256_file(p) != exp:
            raise Customer360Error("source_changed", "Kaynak dosya, panonun veri üretiminden sonra değişmiş; "
                                                     "verileri yenileyin.")
    return int((time.monotonic() - t0) * 1000) if t0 is not None else 0


def build_snapshot(data_folder: str, ctx: dict, *, hedef_path: str | None = None,
                   hedef_snapshot: str | None = None) -> Customer360Snapshot:
    t0 = time.monotonic()
    sig = signature_of(ctx, hedef_path, hedef_snapshot)
    sales_path = _in_folder(data_folder, sig.sales)
    if sales_path is None:
        raise Customer360Error("sales_source_missing", "Canlı panonun satış dosyası veri klasöründe bulunamadı; "
                                                       "verileri yenileyin.")
    _same_generation(data_folder, ctx, sig)          # M8-ACCURACY-2: the files are still the context's revisions
    handoff = getattr(ctx, "sales_c360_handoff", None)
    df = handoff.take(os.path.basename(sales_path)) if handoff is not None else None
    if df is None:
        try:
            raw = db.read_sales_raw(sales_path)
        except Exception:
            raise Customer360Error("sales_unreadable", "Satış dosyası okunamadı.")
        missing = [c for c in SALES_REQUIRED if c not in raw.columns]
        if missing:
            raise Customer360Error("sales_columns_missing", f"Satış dosyasında {len(missing)} gerekli sütun eksik.")
        df = _prepare_sales(raw)
        del raw
    asof = df["_date"].max()
    asof_iso = asof.date().isoformat() if pd.notna(asof) else None
    if asof_iso is None or asof_iso != sig.as_of:
        raise Customer360Error("source_date_mismatch", "Müşteri 360 satış verisi panonun veri tarihiyle aynı değil; "
                                                       "verileri yenileyin.")
    asof_d = asof.date()

    customers, search_rows = {}, []
    master_status, master = _master(sales_path)
    base = _base_facts(df, asof_d)
    for code, b in base.items():
        e = master.get(code)
        mname = _text(e.rows[0].get("name")) if (e is not None and not e.duplicate) else None
        if mname:
            name, src = mname, "master"
        elif b["sales_name"]:
            name, src = b["sales_name"], ("invoice_account" if b["fallback"] else "sales_project")
        else:
            name, src = code, "code"
        b.update(display_name=name, display_name_source=src)
        customers[code] = b
        search_rows.append((code, name, fold(code), fold(name)))
    search_rows.sort(key=lambda r: (r[3], r[0]))

    years = _agg(df, ["_cust", "_yr"], missing=("_amt_missing", "sum"))
    products = _products(df)
    categories = _mix(df, "_kategori")
    groups = _mix(df, "_anagrup")
    accounts = _agg(df, ["_cust", "_inv"], first=("_date", "min"), last=("_date", "max"))
    account_customers = {str(k): sorted(map(str, v)) for k, v in df.groupby("_inv")["_cust"].unique().items()}
    account_names = {str(k): _text(v) for k, v in df.groupby("_inv")["_invname_raw"].first().items()}
    salespeople = {}
    if "_satici" in df.columns:
        sp = df.dropna(subset=["_satici"]).groupby(["_cust", "_satici"])["_ord"].nunique()
        for (c, s), n in sp.items():
            salespeople.setdefault(str(c), []).append((str(s), int(n)))
        for c in salespeople:
            salespeople[c].sort(key=lambda x: (-x[1], x[0]))

    aging_status, aging = _aging(data_folder, sig.aging)
    events_status, events, ev_diag = _events(sales_path)
    hedef_status, hedef_meta, hedef = _hedef(hedef_path, hedef_snapshot)

    snap = Customer360Snapshot(
        signature=sig, as_of=asof_d, sales_file=os.path.basename(sales_path), aging_file=sig.aging,
        built_ms=_same_generation(data_folder, ctx, sig, t0), customers=customers, search_rows=search_rows, years=years,
        products=products, categories=categories, groups=groups, accounts=accounts,
        account_customers=account_customers, account_names=account_names, salespeople=salespeople,
        master_status=master_status, master=master, aging_status=aging_status, aging=aging,
        events_status=events_status, events=events, hedef_status=hedef_status, hedef_meta=hedef_meta, hedef=hedef)
    snap.diagnostics = _diagnostics(snap, df, ev_diag)
    log.info("customer360: built customers=%d orders=%d lines=%d master=%s aging=%s events=%s hedef=%s (%d ms)",
             len(customers), snap.diagnostics["orders"], snap.diagnostics["lines"], master_status, aging_status,
             events_status, hedef_status, snap.built_ms)
    return snap


_prepare_sales = SP.prepare_sales          # M7-6: moved to services/sales_projection.py (shared, unchanged)


def _base_facts(df: pd.DataFrame, asof: dt.date) -> dict[str, dict]:
    g = df.groupby("_cust", sort=True)
    first, last = g["_date"].min(), g["_date"].max()
    orders, lines = g["_ord"].nunique(), g.size()
    missing, zero = g["_amt_missing"].sum(), (df["_amt"] == 0).groupby(df["_cust"]).sum()
    fallback = g["_fallback"].any()
    names = df.dropna(subset=["_name_raw"]).groupby("_cust")["_name_raw"].first()
    distinct_products = g["_sku"].nunique()
    recent_from = pd.Timestamp(asof) - pd.Timedelta(days=RECENT_WINDOW_DAYS - 1)
    recent = df[df["_date"] >= recent_from].groupby("_cust")["_ord"].nunique()
    yrs = df.dropna(subset=["_yr"]).groupby("_cust")["_yr"].unique()
    out = {}
    for code in g.size().index:
        c = str(code)
        nm = names.get(code)
        out[c] = {"code": c, "sales_name": (str(nm).strip() or None) if nm is not None and not pd.isna(nm) else None,
                  "fallback": bool(fallback.get(code, False)), "first_order_date": _iso(first.get(code)),
                  "last_order_date": _iso(last.get(code)), "orders": int(orders.get(code, 0)),
                  "lines": int(lines.get(code, 0)), "missing_amount_lines": int(missing.get(code, 0)),
                  "zero_amount_lines": int(zero.get(code, 0)), "distinct_products": int(distinct_products.get(code, 0)),
                  "recent_orders": int(recent.get(code, 0)),
                  "active_years": sorted(int(y) for y in yrs.get(code, [])) if code in yrs.index else []}
    return out


def _agg(df: pd.DataFrame, keys: list[str], **extra) -> pd.DataFrame:
    """Native groupby: amount / quantity keep missing as missing (sum with min_count=1, never a filled zero)."""
    g = df.groupby(keys, sort=True)
    out = pd.DataFrame({"amount": g["_amt"].sum(min_count=1), "qty": g["_qty"].sum(min_count=1),
                        "orders": g["_ord"].nunique(), "lines": g.size()})
    for name, (col, how) in extra.items():
        out[name] = g[col].agg(how)
    return out.sort_index()


def _products(df: pd.DataFrame) -> pd.DataFrame:
    p = _agg(df, ["_cust", "_sku"], last=("_date", "max"), name=("_skuname", "first"),
             category=("_kategori", "first"), group=("_anagrup", "first")).reset_index()
    p["_sort"] = p["amount"].fillna(float("-inf"))
    p = p.sort_values(["_cust", "_sort", "_sku"], ascending=[True, False, True])
    p = p.groupby("_cust", sort=False).head(TOP_PRODUCTS).drop(columns="_sort")
    return p.set_index("_cust")


def _mix(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if df[col].isna().all():
        return pd.DataFrame(columns=["label", "amount", "lines"]).rename_axis("_cust")
    m = _agg(df.dropna(subset=[col]), ["_cust", col]).reset_index().rename(columns={col: "label"})
    m["_sort"] = m["amount"].fillna(float("-inf"))
    m = m.sort_values(["_cust", "_sort", "label"], ascending=[True, False, True])
    return m.groupby("_cust", sort=False).head(TOP_MIX).drop(columns="_sort").set_index("_cust")


def _master(sales_path: str) -> tuple[str, dict[str, MasterEntry]]:
    try:
        res = run_import(sales_path, MASTER_SPEC)
    except Exception:
        return "unavailable", {}
    if not res.ok:
        return "unavailable", {}
    out: dict[str, MasterEntry] = {}
    for r in res.records:
        code = r.get("code")
        if code is None:
            continue
        out.setdefault(code, MasterEntry([])).rows.append(
            {"name": r.get("name"), "status": r.get("status"), "sector": r.get("sector"), "rep": r.get("rep")})
    return "ok", out


def _aging(folder: str, basename: str | None) -> tuple[str, dict[str, dict]]:
    """Account-level aging, the same column rules as M5's `m_collections`, plus presence of zero-balance accounts."""
    if not basename:
        return "not_supplied", {}
    path = _in_folder(folder, basename)
    if path is None:
        return "failed", {}
    try:
        a = db.read_aging_raw(path)
        a.columns = [str(c).strip() for c in a.columns]
        key = "Hesap kodu"
        months = db.AYLAR[1:]
        bkts = [c for c in a.columns if "öncesi" in c or any(m in c for m in months)]
        single = next((c for c in a.columns if "vadesi geçen" in c.lower()), None)
        for b in bkts:
            a[b] = db.num(a[b]).fillna(0)
        a = a[a[key].astype(str).str.match(r"^\d", na=False)]
        bal = db.num(a[single]).fillna(0) if single else (a[bkts].sum(axis=1) if bkts else 0)
        out = {}
        for i, (_, r) in enumerate(a.iterrows()):
            b = float(bal.iloc[i]) if hasattr(bal, "iloc") else float(bal)
            oldest = next((x for x in bkts if r[x] > 0), None)
            out[str(r[key])] = {"present": True, "overdue": b if b > 0 else 0.0, "oldest": oldest if b > 0 else None}
        return "ok", out
    except Exception:
        return "failed", {}


def _events(sales_path: str) -> tuple[str, dict[str, dict], dict]:
    from services.importers.sales_activity import SourceType, import_order_sources
    try:
        res = import_order_sources(sales_path)
    except Exception:
        return "failed", {}, {"events_failed_step": "exception"}
    if not res.ok:
        return "failed", {}, {"events_failed_step": res.failed_step}
    out: dict[str, dict] = {}
    for o in res.orders:
        if o.customer is None:
            continue
        e = out.setdefault(o.customer, {"orders_by_type": {t.value: 0 for t in SourceType}, "event_ids": {},
                                        "recent": []})
        e["orders_by_type"][o.source_type.value] += 1
        if o.is_event:
            e["event_ids"].setdefault(o.source_type.value, set()).add(o.event_id)
            e["recent"].append({"order_date": o.order_date.isoformat() if o.order_date else None,
                                "source_type": o.source_type.value, "edition_year": o.edition_year,
                                "event_name": o.raw_label, "event_stem": o.event_stem,
                                "edition_year_offset": o.edition_year_offset})
    for e in out.values():
        e["recent"].sort(key=lambda x: (x["order_date"] or "", x["event_name"] or ""), reverse=True)
        e["distinct_events_by_type"] = {k: len(v) for k, v in sorted(e.pop("event_ids").items())}
    return "ok", out, {"event_orders": res.counts.event_orders, "orders": res.counts.orders}


def _hedef(path: str | None, snapshot: str | None) -> tuple[str, dict, dict]:
    if not path:
        return "not_configured", {}, {}
    meta = {"file": os.path.basename(path)}
    try:
        snap = dt.date.fromisoformat(snapshot) if snapshot else None
    except ValueError:
        return "failed", {**meta, "error": "snapshot_date_invalid"}, {}
    if not os.path.isfile(path):
        return "failed", {**meta, "error": "file_missing"}, {}
    try:
        from services.importers.hedef import import_hedef
        res = import_hedef(path, snapshot_date=snap)
    except Exception:
        return "failed", {**meta, "error": "exception"}, {}
    if not res.ok:
        return "failed", {**meta, "error": res.failed_step or "import_failed"}, {}
    meta.update(schema_version=res.counts.schema_version, snapshot_date=snap.isoformat() if snap else None,
                snapshot_date_origin="declared" if snap else None, accounts=len(res.accounts))
    return "ok", meta, {a.customer_code: a for a in res.accounts}


def _diagnostics(s: Customer360Snapshot, df: pd.DataFrame, ev: dict) -> dict:
    codes = list(s.customers)
    m = Counter()
    for c in codes:
        e = s.master.get(c)
        m["unavailable" if s.master_status != "ok" else "unmatched" if e is None else
          "duplicate" if e.duplicate else "matched"] += 1
    per_cust = s.accounts.groupby(level=0).size()
    multi = int((per_cust > 1).sum())
    shared = sum(1 for v in s.account_customers.values() if len(v) > 1)
    hedef_matched = sum(1 for c in codes if c in s.hedef) if s.hedef_status == "ok" else None
    return {
        "as_of_data_date": s.as_of.isoformat(), "sales_file": s.sales_file, "aging_file": s.aging_file,
        "customers": len(codes), "orders": int(df["_ord"].nunique()), "lines": int(len(df)),
        "invoice_accounts": len(s.account_customers), "customers_with_multiple_invoice_accounts": multi,
        "shared_invoice_accounts": shared, "fallback_customers": sum(1 for c in s.customers.values() if c["fallback"]),
        "master_status": s.master_status, "master_resolution": dict(m),
        "master_duplicate_codes": sum(1 for e in s.master.values() if e.duplicate),
        "aging_status": s.aging_status, "aging_accounts": len(s.aging),
        "aging_accounts_linked": sum(1 for k in s.aging if k in s.account_customers),
        "events_status": s.events_status, "customers_with_event_orders": sum(
            1 for e in s.events.values() if sum(v for k, v in e["orders_by_type"].items() if k != "general")),
        **{k: v for k, v in ev.items() if isinstance(v, (int, str)) or v is None},
        "hedef_status": s.hedef_status, "hedef_configured": s.hedef_status != "not_configured",
        "hedef_customers_matched": hedef_matched,
        "hedef_match_rate": (round(hedef_matched / len(codes), 4) if hedef_matched is not None and codes else None),
        "build_ms": s.built_ms,
    }


# ---------------------------------------------------------------------------------------------
# Repository (cache + signature)
# ---------------------------------------------------------------------------------------------
class Customer360Repository:
    """Builds once per live-source signature and caches. Never serves a snapshot whose signature differs from the
    live context. `background=True` (the app): a missing/stale snapshot starts one build thread and callers get
    `Customer360NotReady('building')`; `background=False` (tests, CLI): builds synchronously."""

    def __init__(self, data_folder: str, context_repo, *, hedef_path: str | None = None,
                 hedef_snapshot: str | None = None, background: bool = True, builder=None):
        self._folder = data_folder
        self._ctx_repo = context_repo
        self._hedef_path = hedef_path or None
        self._hedef_snapshot = hedef_snapshot or None
        self._background = background
        self._builder = builder or build_snapshot
        self._lock = threading.Lock()
        self._snap: Customer360Snapshot | None = None
        self._building: SourceSignature | None = None
        self._error: tuple[SourceSignature, Customer360Error] | None = None
        self.builds = 0

    @property
    def hedef_path(self) -> str | None:
        """The explicitly configured Hedef file (M7-6 status shows its basename only)."""
        return self._hedef_path

    def signature_for(self, ctx: dict) -> SourceSignature:
        """The signature a snapshot must carry to match this context (M7-2 consistency check)."""
        return signature_of(ctx, self._hedef_path, self._hedef_snapshot)

    def retry(self) -> None:
        """Forget a failed build so the next access tries again (explicit user retry)."""
        with self._lock:
            self._error = None

    # -- live signature ---------------------------------------------------------------------
    def _live(self) -> tuple[dict | None, SourceSignature | None]:
        # maintenance uses the LIVE context (never a request's pinned one), so a request pinned to an older
        # generation cannot discard the snapshot of the newer one
        live = getattr(self._ctx_repo, "live", None)
        ctx = live() if callable(live) else self._ctx_repo.current()
        if ctx is None:
            return None, None
        return ctx, signature_of(ctx, self._hedef_path, self._hedef_snapshot)

    def state(self) -> dict:
        """Status without blocking. Starts a background build when needed (app mode)."""
        try:
            snap = self.snapshot()
            return {"state": "ready", "as_of_data_date": snap.as_of.isoformat(), "sales_file": snap.sales_file,
                    "customers": len(snap.customers), "hedef_status": snap.hedef_status}
        except Customer360NotReady as e:
            return {"state": e.state}
        except Customer360Error as e:
            return {"state": "error", "error_code": e.code, "message": e.message}

    def snapshot(self) -> Customer360Snapshot:
        """The current snapshot. Pinned per HTTP request (services/request_pin); endpoints still compare its signature
        with the (pinned) context and refuse a mismatch."""
        return RP.pinned(self, "c360", self._snapshot_now)

    def _snapshot_now(self) -> Customer360Snapshot:
        ctx, sig = self._live()
        with self._lock:
            if self._snap is not None and (sig is None or self._snap.signature != sig):
                self._snap = None                                   # stale: never served against newer data
            if self._snap is not None:
                return self._snap
            if sig is not None and self._error is not None and self._error[0] == sig:
                raise self._error[1]
            if self._building is not None:
                raise Customer360NotReady("building")
            if self._background:
                self._building = sig or SourceSignature(None, None, None, None, None, None)
                threading.Thread(target=self._build_bg, name="customer360-build", daemon=True).start()
                raise Customer360NotReady("building")
        # synchronous mode
        if ctx is None:
            ctx = self._ctx_repo.context()
            sig = signature_of(ctx, self._hedef_path, self._hedef_snapshot)
        return self._build_sync(ctx, sig)

    def _build_sync(self, ctx, sig) -> Customer360Snapshot:
        try:
            snap = self._builder(self._folder, ctx, hedef_path=self._hedef_path, hedef_snapshot=self._hedef_snapshot)
        except Customer360Error as e:
            with self._lock:
                self._error = (sig, e)
            raise
        except Exception as e:                                       # never a stack trace or a path to the caller
            log.warning("customer360: build failed (%s)", type(e).__name__)
            err = Customer360Error("build_failed", "Müşteri 360 verisi hazırlanamadı.")
            with self._lock:
                self._error = (sig, err)
            raise err
        with self._lock:
            self.builds += 1
            _, live = self._live()
            if live is None or live == snap.signature:
                self._snap, self._error = snap, None
        return snap

    def _build_bg(self) -> None:
        try:
            ctx = self._ctx_repo.context()                          # builds the M5 context first if needed
            sig = signature_of(ctx, self._hedef_path, self._hedef_snapshot)
            self._build_sync(ctx, sig)
        except Exception:
            pass                                                     # recorded in self._error by _build_sync
        finally:
            with self._lock:
                self._building = None


# ---------------------------------------------------------------------------------------------
# Detail assembly (from indexes; no Excel read)
# ---------------------------------------------------------------------------------------------
CONF_UNAVAILABLE = "unavailable"


def _val(value, confidence, note=None):
    return {"value": value, "confidence": confidence, "note": note}


def _rows(frame: pd.DataFrame, code: str) -> list[dict]:
    if frame is None or frame.empty or code not in frame.index:
        return []
    sub = frame.loc[[code]]
    return sub.reset_index().to_dict("records")


def customer_detail(snap: Customer360Snapshot, code: str, *, ctx: dict | None = None,
                    stored_actions: list[dict] | None = None, memory: list[dict] | None = None,
                    categories: dict[str, str] | None = None, opportunities=None,
                    fair_campaigns: list[dict] | None = None, crm: dict | None = None) -> dict | None:
    """`opportunities` (M7-3): the live context's cross-sell model, passed only when it belongs to the same sources
    as this snapshot; otherwise the section reports `unavailable`. Nothing here recomputes it."""
    base = snap.customers.get(code)
    if base is None:
        return None
    warnings: list[dict] = []
    asof = snap.as_of.isoformat()

    # identity / master
    e = snap.master_entry(code)
    if snap.master_status != "ok":
        mstatus = "unavailable"
        warnings.append({"code": "master_unavailable", "message": "Müşteri ana kaydı (liste) okunamadı."})
    elif e is None:
        mstatus = "unmatched"
        warnings.append({"code": "master_unmatched", "message": "Müşteri kodu ana kayıtta (liste) birebir bulunamadı."})
    elif e.duplicate:
        mstatus = "duplicate"
        warnings.append({"code": "master_duplicate", "message": "Bu kod ana kayıtta birden fazla satırda var; temsilci, "
                                                                "sektör ve KAPALI durumu tek bir satırdan seçilmedi."})
    else:
        mstatus = "matched"
    row = e.rows[0] if (mstatus == "matched") else {}
    unknown = CONF_UNAVAILABLE if mstatus != "matched" else None
    raw_status = row.get("status")
    closed = snap.master_closed(code) if mstatus == "matched" else None
    master = {"status": mstatus, "duplicate_rows": len(e.rows) if (e is not None and e.duplicate) else 0,
              "name": _val(row.get("name"), unknown or "trusted"),
              "status_raw": _val(raw_status, unknown or "trusted"),
              "closed": closed,
              "sector": _val(row.get("sector"), unknown or "unverified"),
              "rep": _val(row.get("rep"), unknown or "unverified")}
    if base["fallback"]:
        warnings.append({"code": "identity_fallback", "message": "Sipariş müşterisi (Pro_kodu) boş; fatura hesabı kodu "
                                                                 "kullanıldı (D-003)."})
    if code != code.strip():
        warnings.append({"code": "code_whitespace", "message": "Kodun başında veya sonunda boşluk var; olduğu gibi "
                                                               "kullanıldı (kırpılmadı)."})
    identity = {"customer_code": code, "display_name": base["display_name"],
                "display_name_source": base["display_name_source"], "sales_name": base["sales_name"],
                "fallback_to_invoice_account": base["fallback"], "code_has_surrounding_whitespace": code != code.strip()}

    # salespeople (kept separate; no salesperson of record)
    hacc = snap.hedef.get(code) if snap.hedef_status == "ok" else None
    people = [{"source": "order_satici", "label": "Sipariş satıcısı (Satici)",
               "values": [f"{n} ({k} sipariş)" for n, k in snap.salespeople.get(code, [])[:5]],
               "confidence": "trusted" if code in snap.salespeople else CONF_UNAVAILABLE},
              {"source": "master_temsilci", "label": "Ana kayıt temsilcisi (liste TEMSİLCİ)",
               "values": [row["rep"]] if row.get("rep") else [], "confidence": master["rep"]["confidence"]}]
    for key, label, attr in (("hedef_temsilci", "Hedef Temsilci", "hedef_rep"),
                             ("hedef_musteri_temsilcisi", "Hedef Müşteri Temsilcisi", "customer_rep")):
        nv = getattr(hacc, attr) if hacc is not None else None
        people.append({"source": key, "label": label,
                       "values": [str(nv.value)] if nv is not None and nv.value not in (None, "") else [],
                       "confidence": nv.confidence.value if nv is not None else CONF_UNAVAILABLE})

    # sales
    yrs = []
    for r in _rows(snap.years, code):
        yrs.append({"year": int(r["_yr"]), "net_order_amount": _num(r["amount"]), "quantity": _num(r["qty"]),
                    "orders": int(r["orders"]), "lines": int(r["lines"]), "missing_amount_lines": int(r["missing"])})
    total = sum(y["net_order_amount"] or 0 for y in yrs) if any(y["net_order_amount"] is not None for y in yrs) else None
    if base["missing_amount_lines"]:
        warnings.append({"code": "missing_amounts", "message": f"{base['missing_amount_lines']} sipariş satırında "
                                                               "Nettutar boş; toplamlara sıfır olarak eklenmedi."})
    sales = {"amount_label": AMOUNT_LABEL, "amount_note": AMOUNT_NOTE, "confidence": "trusted",
             "first_order_date": base["first_order_date"], "last_order_date": base["last_order_date"],
             "orders": base["orders"], "lines": base["lines"], "active_years": base["active_years"],
             "current_year": snap.as_of.year, "has_current_year_orders": snap.as_of.year in base["active_years"],
             "recent_orders": base["recent_orders"], "recent_window_days": RECENT_WINDOW_DAYS,
             "zero_amount_lines": base["zero_amount_lines"], "missing_amount_lines": base["missing_amount_lines"],
             "distinct_products": base["distinct_products"], "total_net_order_amount": total, "years": yrs}

    def mix(frame):
        return [{"label": str(r["label"]), "net_order_amount": _num(r["amount"]), "lines": int(r["lines"]),
                 "share": (round(_num(r["amount"]) / total, 4) if total and _num(r["amount"]) is not None else None)}
                for r in _rows(frame, code)]
    products = {"limit": TOP_PRODUCTS, "amount_label": AMOUNT_LABEL,
                "top": [{"sku": str(r["_sku"]), "name": _text(r["name"]), "net_order_amount": _num(r["amount"]),
                         "quantity": _num(r["qty"]), "orders": int(r["orders"]), "lines": int(r["lines"]),
                         "last_order_date": _iso(r["last"]), "category": _text(r["category"]),
                         "group": _text(r["group"])} for r in _rows(snap.products, code)],
                "categories": mix(snap.categories), "groups": mix(snap.groups)}

    # invoice accounts + aging (never allocated across shared accounts)
    accounts = []
    for r in _rows(snap.accounts, code):
        inv = str(r["_inv"])
        owners = snap.account_customers.get(inv, [])
        shared = len(owners) > 1
        ag = snap.aging.get(inv) if snap.aging_status == "ok" else None
        aging = {"available": snap.aging_status == "ok", "present_in_file": ag is not None,
                 "overdue": (ag["overdue"] if ag else None), "oldest_bucket": (ag["oldest"] if ag else None),
                 "confidence": "trusted" if snap.aging_status == "ok" else CONF_UNAVAILABLE}
        accounts.append({"account_code": inv, "account_name": snap.account_names.get(inv),
                         "is_customer_code": inv == code, "orders": int(r["orders"]), "lines": int(r["lines"]),
                         "net_order_amount": _num(r["amount"]), "first_order_date": _iso(r["first"]),
                         "last_order_date": _iso(r["last"]), "shared": shared,
                         "shared_with_customers": max(0, len(owners) - 1), "aging": aging,
                         "warning": SHARED_ACCOUNT_WARNING if shared else None})
    accounts.sort(key=lambda a: (-a["orders"], a["account_code"]))
    if any(a["shared"] for a in accounts):
        warnings.append({"code": "shared_invoice_account", "message": SHARED_ACCOUNT_WARNING})
    receivables = {"aging_status": snap.aging_status, "aging_file": snap.aging_file, "level": "invoice_account",
                   "accounts": accounts, "limitations": AGING_LIMITATIONS, "detailed_collections": "not_supplied",
                   "detailed_collections_note": DETAILED_COLLECTIONS_NOTE}

    # Hedef
    hedef = _hedef_section(snap, hacc)
    if snap.hedef_status == "failed":
        warnings.append({"code": "hedef_failed", "message": "Hedef dosyası yapılandırılmış ama içe aktarılamadı; "
                                                            "diğer bölümler etkilenmedi."})

    # events
    ev = snap.events.get(code)
    from services.importers.sales_activity import SourceType
    events = {"status": snap.events_status,
              "orders_by_type": (dict(ev["orders_by_type"]) if ev else
                                 ({t.value: 0 for t in SourceType} if snap.events_status == "ok" else {})),
              "distinct_events_by_type": dict(ev["distinct_events_by_type"]) if ev else {},
              "recent": list(ev["recent"][:RECENT_EVENTS]) if ev else [],
              "recent_limit": RECENT_EVENTS, "confidence": "trusted" if snap.events_status == "ok" else CONF_UNAVAILABLE,
              "note": "Fuar, etkinlik ve saha ayrı tutulur; dönüşüm penceresi veya fuar sonrası atıf hesaplanmaz.",
              "campaigns": list(fair_campaigns or [])}     # M7-4: read-only fair-campaign membership
    if snap.events_status != "ok":
        warnings.append({"code": "events_unavailable", "message": "Sipariş kaynağı (Srmmerk) verisi okunamadı."})

    # actions / stored history (read only)
    statuses = {a["action_key"]: a for a in (stored_actions or [])}
    current = []
    for a in (ctx or {}).get("actions", []):
        if a.get("entity_type") != "customer" or str(a.get("entity_id")) != code:
            continue
        st = statuses.get(a.get("action_id"), {})
        current.append({"action_id": a.get("action_id"), "category": a.get("category"),
                        "category_label": (categories or {}).get(a.get("category"), a.get("category")),
                        "title": a.get("title"), "reason": a.get("reason"), "priority": a.get("priority"),
                        "status": st.get("status", "open"), "origin": "current",
                        "generated_at": st.get("generated_at"), "defer_until": st.get("defer_until"),
                        "completed_at": st.get("completed_at")})
    cur_ids = {a["action_id"] for a in current}
    stored = [{"action_id": a["action_key"], "category": a.get("category"),
               "category_label": (categories or {}).get(a.get("category"), a.get("category")),
               "title": a.get("title"), "reason": None, "priority": a.get("priority"), "status": a.get("status"),
               "origin": "stored", "generated_at": a.get("generated_at"), "defer_until": a.get("defer_until"),
               "completed_at": a.get("completed_at")}
              for a in (stored_actions or []) if a["action_key"] not in cur_ids]
    activity = {"current_actions": current, "stored_actions": stored,
                "memory": [{"ts": m.get("ts"), "kind": m.get("kind"), "detail": m.get("detail"),
                            "next_followup": m.get("next_followup")} for m in (memory or [])]}

    sources = [
        {"key": "sales", "label": "Satış raporu", "status": "available", "confidence": "trusted",
         "file": snap.sales_file, "as_of": asof, "detail": "Sipariş satırları; müşteri kimliği Pro_kodu (D-003)."},
        {"key": "master", "label": "Müşteri ana kaydı (liste)", "status": mstatus,
         "confidence": "trusted" if mstatus == "matched" else CONF_UNAVAILABLE, "file": snap.sales_file,
         "as_of": None, "detail": "Kod ve açık/kapalı güvenilir; sektör ve temsilci doğrulanmadı."},
        {"key": "aging", "label": "Cari yaşlandırma", "status": snap.aging_status,
         "confidence": "trusted" if snap.aging_status == "ok" else CONF_UNAVAILABLE, "file": snap.aging_file,
         "as_of": None, "detail": "Fatura hesabı düzeyinde; belge düzeyinde değil."},
        {"key": "hedef", "label": "Hedef raporu", "status": hedef["status"],
         "confidence": "unverified" if hedef["status"] == "matched" else CONF_UNAVAILABLE,
         "file": snap.hedef_meta.get("file"), "as_of": snap.hedef_meta.get("snapshot_date"),
         "detail": "Yalnızca açıkça yapılandırılmış dosyadan; kayıt dosyası kararı açık."},
        {"key": "events", "label": "Sipariş kaynağı (Srmmerk)", "status": snap.events_status,
         "confidence": events["confidence"], "file": snap.sales_file, "as_of": asof, "detail": None},
        {"key": "detailed_collections", "label": "Detaylı tahsilat raporu", "status": "not_supplied",
         "confidence": CONF_UNAVAILABLE, "file": None, "as_of": None, "detail": DETAILED_COLLECTIONS_NOTE},
    ]
    from services import opportunities as OP
    opp = OP.customer_section(opportunities, code, closed)
    return {"customer_code": code, "as_of_data_date": asof, "identity": identity, "sources": sources,
            "master": master, "salespeople": people, "sales": sales, "products": products, "opportunities": opp,
            "receivables": receivables, "hedef": hedef, "events": events, "crm": crm, "activity": activity,
            "warnings": warnings, "ai_used": False}


HEDEF_FIELDS = (("city", "İl"), ("district", "İlçe"), ("sector_code", "Sektör kodu"),
                ("hedef_rep", "Hedef Temsilci"), ("customer_rep", "Hedef Müşteri Temsilcisi"),
                ("discount_tier", "İskonto kademesi (isk1)"),
                ("historical_weekly_stamp_capacity", "Haftalık kaşe adedi (tarihsel)"),
                ("historical_risk_limit", "Risk (tarihsel)"), ("historical_unsecured_credit_limit", "Kredi (tarihsel)"),
                ("visit_count", "Ziyaret sayısı"))
HEDEF_NOTES = {
    "stale": "Eski veri: yalnızca tarihsel referans; potansiyel, kredi veya risk kararı için kullanılmaz.",
    "incomplete": "Eksik kayıt olabilir: sıfır, ziyaret yapılmadığı anlamına gelmeyebilir.",
    "unverified": "Doğrulanmadı.",
}


def _hedef_section(snap: Customer360Snapshot, acc) -> dict:
    meta = snap.hedef_meta
    base = {"file": meta.get("file"), "snapshot_date": meta.get("snapshot_date"),
            "snapshot_date_origin": meta.get("snapshot_date_origin"), "schema_version": meta.get("schema_version"),
            "fields": [], "years": [],
            "notes": ["Hedef kayıt dosyası kararı açık; bu dosya yetkili kaynak değildir.",
                      "Haftalık kaşe adedinden potansiyel / penetrasyon hesaplanmaz.",
                      "Risk ve Kredi güncel kredi kararı değildir."]}
    if snap.hedef_status == "not_configured":
        return {**base, "status": "not_configured"}
    if snap.hedef_status != "ok":
        return {**base, "status": "failed"}
    if acc is None:
        return {**base, "status": "unmatched"}
    fields = []
    for key, label in HEDEF_FIELDS:
        nv = getattr(acc, key)
        conf = nv.confidence.value
        fields.append({"key": key, "label": label, "value": nv.value if not isinstance(nv.value, dt.date) else
                       nv.value.isoformat(), "confidence": conf, "note": HEDEF_NOTES.get(conf)})
    years = sorted({y for m in ("stamp_qty", "reported_net_revenue") for y in acc.yearly.get(m, {})})
    yrows = []
    for y in years:
        sq = acc.yearly.get("stamp_qty", {}).get(y)
        rv = acc.yearly.get("reported_net_revenue", {}).get(y)
        yrows.append({"year": y,
                      "stamp_qty": _val(sq.value if sq else None, sq.confidence.value if sq else CONF_UNAVAILABLE),
                      "reported_net_revenue": _val(rv.value if rv else None,
                                                   rv.confidence.value if rv else CONF_UNAVAILABLE)})
    return {**base, "status": "matched", "fields": fields, "years": yrows}


__all__ = ["AMOUNT_LABEL", "Customer360Error", "Customer360NotReady", "Customer360Repository",
           "Customer360Snapshot", "SHARED_ACCOUNT_WARNING", "SourceSignature", "build_snapshot", "customer_detail",
           "fold", "signature_of"]
