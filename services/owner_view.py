"""M7-6 — Yönetim (owner / management) view: explicit owner sources, compact cached views, system status.

Docs: docs/m7_owner_integration.md; decision D-047.

Owner sources (Kredi, Gider/Gelir, Bütçe) are `Audience.OWNER` (D-023 §13) and stay OPTIONAL, EXPLICIT-PATH inputs:
- configured only by `KREDI_PATH` (+ `KREDI_SNAPSHOT_DATE`), `GIDER_GELIR_PATH` (+ `GIDER_GELIR_REPORT_YEAR`),
  `BUTCE_PATH` (+ `BUTCE_PLANNING_YEAR`, `BUTCE_SNAPSHOT_DATE`); nothing is ever discovered, selected or ordered by
  modification time, and no file of record is decided here;
- imported only through the existing importer contracts (`import_kredi`, `import_gider_gelir`, `import_butce`);
- loaded lazily (first open of the Yönetim tab), cached for the process lifetime as COMPACT derived views (no raw
  importer results, workbook rows or frames are retained), re-read only by an explicit, guarded reload;
- each source independent: one failure never discards or blocks another;
- never required by the core refresh, the dashboard, Customer 360, Günlük Satış, Fuar Merkezi or CRM;
- never sent to AI, never in evidence packets, actions, drafts or logs. The status uses basenames only.

The application is local single-user without authentication: the tab is labelled as owner information, it is NOT
access-controlled. A future multi-user deployment must enforce owner/admin roles.

Approved derivations only:
- Kredi: scheduled loan obligations (remaining installment amounts of active installments by due-date horizon,
  grouped by currency; no FX; no remaining-principal total; not a cash-flow forecast).
- Gider/Gelir: source-native monthly reported sales / cost / gross profit / expense, the four native lines kept
  separate, totals only where M6 verifies them; accounting basis unverified; no management mapping.
- Bütçe: validity / coverage counts only (no budget-vs-actual, no reconciliation with Gider/Gelir).
"""
from __future__ import annotations

import datetime as dt
import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from services import request_pin as RP

SOURCES = ("kredi", "gider_gelir", "butce")
SOURCE_LABELS = {"kredi": "Şirket Finansmanı / Kredi Takibi", "gider_gelir": "Gider / Gelir", "butce": "Bütçe"}
NOT_CONFIGURED, LOADING, READY, FAILED = "not_configured", "loading", "ready", "failed"
STATE_LABELS = {NOT_CONFIGURED: "Yapılandırılmadı", LOADING: "Yükleniyor", READY: "Hazır", FAILED: "Okunamadı"}
HORIZONS = (30, 60, 90)
#: neutral broadness note when this share of active customers has a cross-sell target (thresholds NOT changed)
CROSS_SELL_BROAD_SHARE = 0.5

KREDI_PURPOSE = ("Halsa'nın kendi banka kredisi taksitleri (şirket finansmanı). Müşteri kredisi, Hedef Risk/Kredi "
                 "veya alacak DEĞİLDİR.")
KREDI_SCHEDULE_NOTE = ("Planlı kredi yükümlülükleri: kaynakta kayıtlı, ödenmemiş taksit tutarları (KALAN TAKSİT "
                       "TUTARI) vade tarihine göre. Nakit akış tahmini değildir: tedarikçi ödemeleri, alacak "
                       "tahsilatları, satın alma, vergi ve maaşlar dahil değildir.")
KREDI_SNAPSHOT_UNKNOWN = ("Kaynağın kesin anlık tarihi bilinmiyor; ödeme planı kaynakta kayıtlı kalan tutarlara "
                          "dayanır.")
KREDI_HORIZON_NOTE = ("30 / 60 / 90 gün bugünün yerel tarihine göre hesaplanır; bu, kaynak dosyanın güncel olduğu "
                      "anlamına gelmez. Canlı banka bakiyesi değildir.")
KREDI_OVERDUE_NOTE = ("Vadesi geçmiş görünen taksitler kaynak tarihinden sonra ödenmiş olabilir; bu yüzden ayrı "
                      "gösterilir ve 30/60/90 toplamlarına katılmaz.")
KREDI_PRINCIPAL_NOTE = ("Kalan anapara toplamı gösterilmez: KALAN ANAPARA her taksit satırında tekrarlanan bir "
                        "durum bilgisidir ve satırlar toplanamaz; sözleşme düzeyinde tek ve kesin bir değer "
                        "türetilemiyor.")
GG_BASIS_NOTE = ("Muhasebe esası (nakit / tahakkuk) doğrulanmadı. Değerler kaynakta raporlanan tutarlardır; nakit "
                 "veya tahakkuk gelir/gideri olarak okunmamalıdır.")
GG_MAPPING_NOTE = ("İş kolları kaynaktaki haliyle ayrı tutulur (KIRTASIYE / TRODAT / TROTEC / IMALAT); yönetim "
                   "eşlemesi (kaşe / kırtasiye / diğer-ithalat) henüz tanımlanmadı.")
GG_NO_RECON_NOTE = "Bütçe dosyasıyla karşılaştırma veya fark analizi yapılmaz (dosya kaydı ve esas çözülmedi)."
BUTCE_WARNING = ("Bütçe kaynağı kısmen kullanılabilir; geçersiz / dış bağımlı hücreler otomatik karar veya "
                 "bütçe-vs-gerçek analizinde kullanılmaz.")
BUTCE_COMPLETE_NOTE = "Bütçe kaynağında geçersiz veya dış bağımlı hücre bulunmadı; yine de bütçe-vs-gerçek analizi yapılmaz."
OWNER_AUDIENCE_NOTE = ("Yönetim bilgisidir. Uygulama yerel ve tek kullanıcılıdır: bu sekme erişim kontrollü DEĞİLDİR. "
                       "Çok kullanıcılı kurulumda sahip/yönetici rolleri zorunlu olacaktır.")
CROSS_SELL_BROAD_NOTE = ("Çapraz satış modeli geniş bir aday kitlesi üretiyor; eşikler yönetim tarafından henüz gözden "
                         "geçirilmedi.")
LINE_LABELS = {"kirtasiye": "KIRTASIYE", "trodat": "TRODAT", "trotec": "TROTEC", "imalat": "IMALAT"}
GG_BUDGET_LINES = (("Satış", "Satış"), ("Satış maliyeti", "Satış maliyeti"), ("Brüt Kar", "Brüt kâr"),
                   ("gider", "Gider"), ("NET KAR", "Net kâr"), ("KAR-ZARAR", "Kâr / zarar"))


# ---------------------------------------------------------------------------------------------
# configuration (explicit paths only)
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class OwnerSourceConfig:
    key: str
    path: str | None
    options: dict = field(default_factory=dict)       # raw strings from the environment

    @property
    def configured(self) -> bool:
        return bool(self.path)

    @property
    def basename(self) -> str | None:
        return os.path.basename(self.path) if self.path else None


ENV_KEYS = {"kredi": ("KREDI_PATH", {"snapshot_date": "KREDI_SNAPSHOT_DATE"}),
            "gider_gelir": ("GIDER_GELIR_PATH", {"report_year": "GIDER_GELIR_REPORT_YEAR"}),
            "butce": ("BUTCE_PATH", {"planning_year": "BUTCE_PLANNING_YEAR", "snapshot_date": "BUTCE_SNAPSHOT_DATE"})}


def config_from_env(env: dict | None = None) -> dict[str, OwnerSourceConfig]:
    env = os.environ if env is None else env
    out = {}
    for key, (path_var, opts) in ENV_KEYS.items():
        path = (env.get(path_var) or "").strip() or None
        out[key] = OwnerSourceConfig(key, path, {k: (env.get(v) or "").strip() or None for k, v in opts.items()})
    return out


class _ConfigError(ValueError):
    pass


def _opt_date(v: str | None) -> dt.date | None:
    if v is None:
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise _ConfigError("date")


def _opt_year(v: str | None) -> int | None:
    if v is None:
        return None
    if not v.isdigit() or len(v) != 4:
        raise _ConfigError("year")
    return int(v)


# ---------------------------------------------------------------------------------------------
# compact views (built once per load; no importer result, row or frame is kept)
# ---------------------------------------------------------------------------------------------
def _num(nv) -> float | None:
    v = getattr(nv, "value", None)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _sum(values) -> float | None:
    """Sum of present values; None when none is present (missing is never zero)."""
    xs = [v for v in values if v is not None]
    return round(sum(xs), 2) if xs else None


@dataclass
class KrediView:
    file: str
    contracts: int
    installments: int
    lenders: int
    currencies: list[str]
    snapshot_date: str | None
    snapshot_origin: str | None
    latest_payment_date: str | None
    status_counts: dict[str, int]
    obligations: list[tuple[str, dt.date, float]]      # (currency, due date, remaining amount) — active, > 0
    closed_with_remaining: int
    warnings: list[dict]


def build_kredi_view(res) -> KrediView:
    cur = {k.contract_key: k.currency for k in res.contracts}
    # The trusted payment fields decide what is unpaid (remaining_amount, paid + remaining = installment is verified
    # by the importer). DURUM is an UNVERIFIED field: in the real export most 'Kapalı' contracts still carry unpaid
    # future installments with no payment slip, so it never removes an obligation; it is only counted.
    obligations, closed_rem = [], 0
    for i in res.installments:
        rem, due = _num(i.remaining_amount), i.due_date.value
        if rem is None or rem <= 0 or due is None:
            continue
        if i.status.value != "active":
            closed_rem += 1
        obligations.append((cur[i.contract_key], due, rem))
    c = res.counts
    warnings = []
    if closed_rem:
        warnings.append({"code": "status_closed_with_remaining", "count": closed_rem,
                         "message": f"{closed_rem} taksitte DURUM 'Kapalı' görünüyor ama ödenmemiş kalan tutar var. DURUM "
                                    "doğrulanmamış bir alandır; ödeme durumu kalan tutar ve ödeme fişinden okunur. Erken "
                                    "kapama veya yeniden yapılandırma kaynakta görünmüyorsa yükümlülük farklı olabilir."})
    if c.paid_without_payment_date:
        warnings.append({"code": "paid_without_payment_date", "count": c.paid_without_payment_date,
                         "message": f"{c.paid_without_payment_date} taksitte ödenen tutar var ama ödeme fişi tarihi yok."})
    if c.payment_date_without_paid_amount:
        warnings.append({"code": "payment_date_without_paid_amount", "count": c.payment_date_without_paid_amount,
                         "message": f"{c.payment_date_without_paid_amount} taksitte ödeme fişi tarihi var ama ödenen tutar yok."})
    if c.contracts_without_installment_count:
        warnings.append({"code": "installment_count_not_recorded", "count": c.contracts_without_installment_count,
                         "message": f"{c.contracts_without_installment_count} sözleşmede taksit sayısı kayıtlı değil."})
    return KrediView(file=res.file, contracts=c.contracts, installments=c.installments, lenders=c.distinct_lenders,
                     currencies=list(c.currencies), snapshot_date=c.snapshot_date.isoformat() if c.snapshot_date else None,
                     snapshot_origin=c.snapshot_date_origin,
                     latest_payment_date=(c.latest_recorded_payment_date.isoformat()
                                          if c.latest_recorded_payment_date else None),
                     status_counts=dict(c.status_counts), obligations=sorted(obligations, key=lambda o: (o[0], o[1])),
                     closed_with_remaining=closed_rem, warnings=warnings)


def kredi_schedule(view: KrediView, today: dt.date) -> list[dict]:
    """Scheduled obligations per currency (never summed across currencies; no FX). Horizons are cumulative
    calendar windows from `today` (today ≤ due ≤ today + N); past-due remaining amounts are a separate bucket."""
    by_cur: dict[str, list[tuple[dt.date, float]]] = defaultdict(list)
    for cur, due, rem in view.obligations:
        by_cur[cur].append((due, rem))
    out = []
    for cur in sorted(set(view.currencies) | set(by_cur)):
        rows = by_cur.get(cur, [])
        future = [(d, r) for d, r in rows if d >= today]
        past = [(d, r) for d, r in rows if d < today]
        horizons = []
        for n in HORIZONS:
            lim = today + dt.timedelta(days=n)
            sel = [r for d, r in future if d <= lim]
            horizons.append({"days": n, "installments": len(sel), "amount": round(sum(sel), 2)})
        lim = today + dt.timedelta(days=HORIZONS[-1])
        after = [r for d, r in future if d > lim]
        out.append({"currency": cur,
                    "next_due_date": min(d for d, _ in future).isoformat() if future else None,
                    "horizons": horizons,
                    "after_90": {"installments": len(after), "amount": round(sum(after), 2)},
                    "past_due": {"installments": len(past), "amount": round(sum(r for _, r in past), 2)},
                    "total_remaining": {"installments": len(rows), "amount": round(sum(r for _, r in rows), 2)}})
    return out


@dataclass
class GiderGelirView:
    file: str
    report_year: int | None
    report_year_origin: str | None
    revision_label: str | None
    months: list[dict]
    lines: list[dict]
    budget: list[dict]
    months_with_revenue: list[int]
    months_with_expenses: list[int]
    records: dict[str, int]
    warnings: list[dict]
    accounting_basis: str
    management_mapping: str


def build_gider_gelir_view(res) -> GiderGelirView:
    rev = defaultdict(list)                                  # (metric, month) -> values over the four lines
    line_rev = defaultdict(list)                             # (line, metric) -> values
    for r in res.revenue:
        v = _num(r.value)
        rev[(r.metric, r.month)].append(v)
        line_rev[(r.native_line.value, r.metric)].append(v)
    exp = defaultdict(list)
    line_exp = defaultdict(list)
    for m in res.expense_months:
        exp[m.month].append(_num(m.reported_expense))
        for ln, nv in m.allocations.items():
            line_exp[ln.value].append(_num(nv))
    c = res.counts
    shown = sorted(set(c.months_with_revenue) | set(c.months_with_expenses))
    months = [{"month": m, "reported_sales": _sum(rev.get(("reported_sales", m), [])),
               "reported_cost": _sum(rev.get(("reported_cost", m), [])),
               "reported_gross_profit": _sum(rev.get(("reported_gross_profit", m), [])),
               "reported_expense": _sum(exp.get(m, []))} for m in shown]
    lines = [{"line": ln, "label": LINE_LABELS[ln],
              "reported_sales": _sum(line_rev.get((ln, "reported_sales"), [])),
              "reported_cost": _sum(line_rev.get((ln, "reported_cost"), [])),
              "reported_gross_profit": _sum(line_rev.get((ln, "reported_gross_profit"), [])),
              "allocated_expense": _sum(line_exp.get(ln, []))} for ln in LINE_LABELS]
    by_label = {s.line_label: _num(s.value) for s in res.summary_records
                if s.period_kind.value == "source_reported_budget" and s.year == c.report_year}
    budget = [{"line": lab, "label": disp, "value": by_label.get(lab)} for lab, disp in GG_BUDGET_LINES
              if lab in by_label]
    warnings = [{"code": "accounting_basis_unverified", "count": None, "message": "Muhasebe esası doğrulanmadı."},
                {"code": "management_mapping_not_supplied", "count": None,
                 "message": "Yönetim iş kolu eşlemesi tanımlanmadı."}]
    for code, n, msg in (
            ("manual_allocation_overrides", c.allocation_cells_not_matching_ratio,
             "aylık dağıtım hücresi oran × toplam ile uyuşmuyor (kaynakta elle girilmiş)"),
            ("annual_total_mismatch", c.annual_totals_not_matching_months,
             "gider satırında yıllık toplam aylık toplamlarla uyuşmuyor"),
            ("gross_profit_entered_separately", c.gross_profit_not_sales_minus_cost,
             "ayda brüt kâr satış − maliyet değil (kaynak değeri korunur)"),
            ("ratio_outside_unit_interval", c.ratios_outside_unit_interval,
             "gider satırında 0–1 dışında dağıtım oranı (kaynakta yeniden dağıtım)")):
        if n:
            warnings.append({"code": code, "count": n, "message": f"{n} {msg}."})
    xs = (c.cross_sheet_checks or {}).get("summary_expense_vs_expense_table", {}).get("failed", 0)
    if xs:
        warnings.append({"code": "summary_expense_pivot_mismatch", "count": xs,
                         "message": f"{xs} ÖZET gider hücresi gider tablosundan farklı (ÖZET pivotu yapıştırılmış değer)."})
    if c.template_header_year is not None and c.report_year is not None and c.template_header_year != c.report_year:
        warnings.append({"code": "template_header_year_mismatch", "count": None,
                         "message": "Gider tablosunun ay başlıkları önceki yılı taşıyor; yalnızca ay okunur."})
    return GiderGelirView(file=res.file, report_year=c.report_year, report_year_origin=c.report_year_origin,
                          revision_label=c.revision_label, months=months, lines=lines, budget=budget,
                          months_with_revenue=list(c.months_with_revenue),
                          months_with_expenses=list(c.months_with_expenses),
                          records={"revenue": c.revenue_records, "expense_rows": c.expense_rows,
                                   "expense_months": c.expense_month_records, "summary": c.summary_records},
                          warnings=warnings, accounting_basis=c.accounting_basis,
                          management_mapping=c.management_mapping)


@dataclass
class ButceView:
    file: str
    health: dict


def build_butce_view(res) -> ButceView:
    c = res.counts
    errs = dict(c.excel_errors)
    other = c.other_errors if c.other_errors else sum(n for k, n in errs.items() if k not in ("#REF!", "#DIV/0!"))
    invalid_cells = c.invalid_facts + c.invalid_cells_in_validated_regions
    return ButceView(file=res.file, health={
        "planning_year": c.planning_year, "planning_year_origin": c.planning_year_origin,
        "revision_label": c.revision_label,
        "snapshot_date": c.snapshot_date.isoformat() if c.snapshot_date else None,
        "snapshot_origin": c.snapshot_date_origin, "source_status": c.source_status,
        "candidate_cells": c.candidate_value_cells, "blank_candidates": c.blank_candidates,
        "usable_unverified": c.usable_unverified, "invalid_facts": c.invalid_facts,
        "invalid_cells_in_validated_regions": c.invalid_cells_in_validated_regions, "invalid_cells": invalid_cells,
        "coverage_rate": round(float(c.coverage_rate), 4), "coverage_pct": round(float(c.coverage_rate) * 100, 2),
        "ref_errors": c.ref_errors, "div0_errors": c.div0_errors, "other_errors": other,
        "missing_cached_formulas": c.formula_missing_cache, "external_links": c.external_links,
        "external_formula_cells": c.external_formula_cells, "external_dependent_cells": c.external_dependent_facts,
        "external_dependency_regions": len(c.external_dependency_regions), "authoritative": bool(c.authoritative),
        "file_of_record": c.file_of_record})


# ---------------------------------------------------------------------------------------------
# loaders (the existing importer contracts, called through their modules so tests can observe them)
# ---------------------------------------------------------------------------------------------
class SourceChanged(RuntimeError):
    """The workbook changed while it was being read (fingerprint before ≠ after)."""


def _load_kredi(cfg: OwnerSourceConfig):
    """M8-ACCURACY-2: the bytes are frozen once; the importer parses exactly those bytes; the record carries their
    fingerprint and the validation evidence."""
    from services import generation as GEN
    from services.importers import kredi as KR
    snap = _opt_date(cfg.options.get("snapshot_date"))
    fs = GEN.FrozenSource.read("kredi", cfg.path)
    res = KR.import_kredi(cfg.path, snapshot_date=snap, content=fs.data)
    rec = GEN.owner_record("kredi", fs, res, declared={"snapshot_date": snap}, today=dt.date.today())
    return res, (build_kredi_view(res) if res.ok else None), rec


def _guarded(key: str, cfg: OwnerSourceConfig, run, declared: dict):
    """Importers that read from a path: the fingerprint is taken before and after; a change in between fails."""
    from services import generation as GEN
    fs = GEN.FrozenSource.read(key, cfg.path)
    res = run()
    if GEN.sha256_file(cfg.path) != fs.sha256:
        raise SourceChanged(key)
    return res, GEN.owner_record(key, fs, res, declared=declared, today=dt.date.today())


def _load_gider_gelir(cfg: OwnerSourceConfig):
    from services.importers import gider_gelir as GG
    year = _opt_year(cfg.options.get("report_year"))
    res, rec = _guarded("gider_gelir", cfg, lambda: GG.import_gider_gelir(cfg.path, report_year=year), {})
    return res, (build_gider_gelir_view(res) if res.ok else None), rec


def _load_butce(cfg: OwnerSourceConfig):
    from services.importers import butce as BU
    year = _opt_year(cfg.options.get("planning_year"))
    snap = _opt_date(cfg.options.get("snapshot_date"))
    res, rec = _guarded("butce", cfg, lambda: BU.import_butce(cfg.path, planning_year=year, snapshot_date=snap),
                        {"snapshot_date": snap})
    return res, (build_butce_view(res) if res.ok else None), rec


LOADERS: dict[str, Callable] = {"kredi": _load_kredi, "gider_gelir": _load_gider_gelir, "butce": _load_butce}
FAIL_MESSAGES = {"config_invalid": "Yapılandırma değeri geçersiz (tarih YYYY-AA-GG, yıl YYYY olmalı).",
                 "file_missing": "Yapılandırılan dosya bulunamadı.",
                 "source_changed": "Dosya okunurken değişti; yeniden yükleyin.",
                 "exception": "Kaynak okunamadı.",
                 "import_failed": "Kaynak doğrulanamadı; hiçbir değer kullanılmadı."}


def _issue_codes(res) -> list[str]:
    try:
        from services.importers.models import Severity
        codes = res.report.codes(Severity.ERROR)
    except Exception:
        return []
    return sorted({str(c) for c in codes})[:12]


# ---------------------------------------------------------------------------------------------
# repository: lazy load, process-lifetime cache, explicit reload, per-source isolation
# ---------------------------------------------------------------------------------------------
class OwnerRepository:
    """Per-source state `not_configured | loading | ready | failed` and a compact view per ready source.

    - `ensure_loaded()`: first access starts ONE load of every configured source (background thread in the app).
    - `reload()`: explicit; ignored while a load is in flight (`started=False`); a new generation otherwise.
    - A source's view is replaced atomically when its own load finishes, only if that load's generation is still the
      current one, so a late result of an older load never overwrites a newer one. During a reload the previous view
      stays available (flagged `reloading`); a failed reload replaces it with the failure (never silently stale).
    - Core refresh never touches this object, and this object never touches the core context.
    """

    def __init__(self, configs: dict[str, OwnerSourceConfig] | None = None, *, background: bool = True,
                 loaders: dict[str, Callable] | None = None, on_record: Callable | None = None) -> None:
        self._configs = configs if configs is not None else config_from_env()
        self._on_record = on_record        # M8-ACCURACY-2: publishes each loaded source's record to the generation
        self._background = background
        self._loaders = loaders or LOADERS
        self._lock = threading.Lock()
        self._gen = 0
        self._inflight = False
        self._loaded_once = False
        self._entries: dict[str, dict] = {}
        for k in SOURCES:
            cfg = self._configs.get(k) or OwnerSourceConfig(k, None)
            self._entries[k] = {"state": NOT_CONFIGURED if not cfg.configured else LOADING, "view": None,
                                "error_code": None, "issue_codes": [], "loaded_at": None, "reloading": False,
                                "fingerprint": None, "record": None}
        self.loads: dict[str, int] = {k: 0 for k in SOURCES}

    # -- public --------------------------------------------------------------------------
    def config(self, key: str) -> OwnerSourceConfig:
        return self._configs.get(key) or OwnerSourceConfig(key, None)

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded_once:
                return
            self._loaded_once = True
            gen = self._start_locked()
        self._dispatch(gen)

    def reload(self) -> dict:
        with self._lock:
            if self._inflight:
                return {"started": False, "generation": self._gen}
            self._loaded_once = True
            gen = self._start_locked()
        self._dispatch(gen)
        return {"started": True, "generation": gen}

    def snapshot(self) -> dict[str, dict]:
        """A consistent copy of every source entry (views are immutable once built). Pinned per HTTP request
        (services/request_pin): a reload during a request never changes what that request returns."""
        return RP.pinned(self, "owner", self._snapshot_now)

    def _snapshot_now(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._entries.items()}

    @property
    def generation(self) -> int:
        return self._gen

    @property
    def inflight(self) -> bool:
        return self._inflight

    # -- internals -----------------------------------------------------------------------
    def _start_locked(self) -> int:
        self._gen += 1
        self._inflight = any(self.config(k).configured for k in SOURCES)
        for k in SOURCES:
            e = self._entries[k]
            if self.config(k).configured:
                e["reloading"] = e["view"] is not None
                if e["view"] is None:
                    e["state"] = LOADING
        return self._gen

    def _dispatch(self, gen: int) -> None:
        if not self._inflight:
            return
        if self._background:
            threading.Thread(target=self._run, args=(gen,), name="owner-sources", daemon=True).start()
        else:
            self._run(gen)

    def _run(self, gen: int) -> None:
        try:
            for k in SOURCES:
                cfg = self.config(k)
                if not cfg.configured:
                    continue
                entry = self._load_one(k, cfg)
                # the view and its generation record are published together (one step for every reader)
                with RP.PUBLISH_LOCK:
                    with self._lock:
                        if gen != self._gen:            # a newer load started: never overwrite it
                            return
                        self._entries[k] = entry
                    if self._on_record is not None:
                        try:
                            self._on_record(k, entry.get("record"))
                        except Exception:
                            pass
        finally:
            with self._lock:
                if gen == self._gen:
                    self._inflight = False

    def _load_one(self, key: str, cfg: OwnerSourceConfig) -> dict:
        base = {"view": None, "issue_codes": [], "loaded_at": dt.datetime.now().isoformat(timespec="seconds"),
                "reloading": False, "fingerprint": None, "record": None}
        if not os.path.isfile(cfg.path):
            return {**base, "state": FAILED, "error_code": "file_missing"}
        try:
            self.loads[key] += 1
            out = self._loaders[key](cfg)
        except _ConfigError:
            return {**base, "state": FAILED, "error_code": "config_invalid"}
        except SourceChanged:
            return {**base, "state": FAILED, "error_code": "source_changed"}
        except Exception:                            # never a stack trace, path or cell content
            return {**base, "state": FAILED, "error_code": "exception"}
        res, view = out[0], out[1]
        record = out[2] if len(out) > 2 else None
        base["record"] = record
        if view is None:
            return {**base, "state": FAILED, "error_code": "import_failed", "issue_codes": _issue_codes(res),
                    "failed_step": getattr(res, "failed_step", None)}
        # the served view's fingerprint: evidence binds only while this exact revision is served
        return {**base, "state": READY, "error_code": None, "view": view,
                "fingerprint": record.sha256 if record is not None else None}


# ---------------------------------------------------------------------------------------------
# API payloads
# ---------------------------------------------------------------------------------------------
def source_head(repo: OwnerRepository, key: str, entry: dict) -> dict:
    cfg = repo.config(key)
    code = entry.get("error_code")
    return {"key": key, "label": SOURCE_LABELS[key], "configured": cfg.configured, "file": cfg.basename,
            "state": entry["state"], "state_label": STATE_LABELS[entry["state"]], "reloading": bool(entry.get("reloading")),
            "error_code": code, "message": FAIL_MESSAGES.get(code) if code else None,
            "failed_step": entry.get("failed_step"), "issue_codes": list(entry.get("issue_codes") or []),
            "loaded_at": entry.get("loaded_at")}


def kredi_section(repo: OwnerRepository, entry: dict, today: dt.date) -> dict:
    out = {**source_head(repo, "kredi", entry), "purpose": KREDI_PURPOSE, "schedule_note": KREDI_SCHEDULE_NOTE,
           "horizon_note": KREDI_HORIZON_NOTE, "overdue_note": KREDI_OVERDUE_NOTE, "principal_note": KREDI_PRINCIPAL_NOTE,
           "today": today.isoformat(), "data": None}
    v: KrediView | None = entry.get("view")
    if v is not None:
        out["data"] = {"contracts": v.contracts, "installments": v.installments, "lenders": v.lenders,
                       "currencies": v.currencies, "status_counts": v.status_counts,
                       "snapshot_date": v.snapshot_date, "snapshot_origin": v.snapshot_origin,
                       "snapshot_note": None if v.snapshot_date else KREDI_SNAPSHOT_UNKNOWN,
                       "latest_payment_date": v.latest_payment_date, "schedule": kredi_schedule(v, today),
                       "warnings": v.warnings, "remaining_principal_total": None}
    return out


def gider_gelir_section(repo: OwnerRepository, entry: dict) -> dict:
    out = {**source_head(repo, "gider_gelir", entry), "basis_note": GG_BASIS_NOTE, "mapping_note": GG_MAPPING_NOTE,
           "reconciliation_note": GG_NO_RECON_NOTE, "data": None}
    v: GiderGelirView | None = entry.get("view")
    if v is not None:
        out["data"] = {"report_year": v.report_year, "report_year_origin": v.report_year_origin,
                       "revision_label": v.revision_label, "snapshot_date": None, "months": v.months,
                       "lines": v.lines, "budget": v.budget, "months_with_revenue": v.months_with_revenue,
                       "months_with_expenses": v.months_with_expenses, "records": v.records,
                       "warnings": v.warnings, "accounting_basis": v.accounting_basis,
                       "management_mapping": v.management_mapping}
    return out


def butce_section(repo: OwnerRepository, entry: dict) -> dict:
    out = {**source_head(repo, "butce", entry), "warning": BUTCE_WARNING, "data": None}
    v: ButceView | None = entry.get("view")
    if v is not None:
        out["data"] = dict(v.health)
        if v.health.get("source_status") == "complete":
            out["warning"] = BUTCE_COMPLETE_NOTE
    return out


def operations(ctx, snap, queue_counts: dict | None, crm_summary: dict | None, campaigns: list[dict]) -> dict:
    """Counts only, from already-computed live state. No new scoring; no person metrics."""
    model = getattr(ctx, "opportunities", None) if ctx is not None else None
    active = getattr(model, "active_customers", None) if model is not None else None
    targets = len(getattr(model, "targets", {}) or {}) if model is not None else None
    share = round(targets / active, 4) if (active and targets is not None) else None
    qc = queue_counts or {}
    crm = crm_summary or {}
    return {
        "state": "ready" if (snap is not None and queue_counts is not None) else "building",
        "customer360_customers": len(snap.customers) if snap is not None else None,
        "daily_sales_customers": qc.get("all"), "collection_first_customers": qc.get("collection_first"),
        "fair_tasks_due": qc.get("fair_followup"), "crm_followups_in_queue": qc.get("crm_followup"),
        "crm_overdue": crm.get("overdue"), "crm_due_today": crm.get("due_today"),
        "crm_current_followups": crm.get("current_followups"),
        "crm_structured_interactions": crm.get("structured_interactions"),
        "crm_coverage_status": crm.get("coverage_status", "incomplete"),
        "crm_coverage_label": crm.get("coverage_label", "Eksik kayıt olabilir"),
        "active_fair_campaigns": sum(1 for c in campaigns if c.get("status") == "active"),
        "planned_fair_campaigns": sum(1 for c in campaigns if c.get("status") == "planned"),
        "cross_sell_customers": targets, "cross_sell_active_customers": active, "cross_sell_share": share,
        "cross_sell_note": CROSS_SELL_BROAD_NOTE if (share is not None and share >= CROSS_SELL_BROAD_SHARE) else None,
    }


KNOWN_ISSUES = (
    ("dq1_margin", "DQ-1 · Marj: eksik maliyet sıfır sayılıyor",
     "Panodaki marj hesabı eksik maliyeti 0 kabul ediyor; Yönetim bu marjı kullanmaz. Düzeltme sahip onayı bekliyor.",
     "open"),
    ("dq2_stok_eritme_kapali", "DQ-2 · Stok Eritme listesinde KAPALI müşteriler",
     "Panonun Stok Eritme alıcı listesi KAPALI müşterileri içerebilir. Değişiklik sahip onayı bekliyor.", "open"),
    ("dq4_stock_coverage", "DQ-4 · Stok kapsamı #DIV/0! kör noktası",
     "Stok kapsamı hesaplanamayan ürünler Azalan / Fazla Stok listelerine girmez; düşük/yüksek stok olarak "
     "sınıflanmaz. Kural sahip onayı bekliyor.", "open"),
    ("dq5_m5_attributes", "DQ-5 · M5 müşteri özellikleri fatura hesabından",
     "Eski M5 ekranlarında Temsilci / Açık-Kapalı / Sektör fatura hesabı aramasından gelir. Yeni M7 ekranları "
     "Müşteri 360'ın ana kayıt mimarisini kullanır. M5 davranışı değiştirilmedi.", "open"),
    ("crm_incomplete", "CRM kapsamı eksik",
     "Eksik kayıt olabilir: CRM kayıtları tüm temasları kapsamıyor; kayıt olmaması temas olmadığı anlamına gelmez "
     "ve kişi performansı için kullanılmaz (D-024).", "open"),
    ("detailed_collections_missing", "Detaylı Tahsilat kaynağı yok",
     "Evrak Detaylı Tahsilat Raporu sağlanmadı; M6-4 bloke. Hesap bazlı Cari yaşlandırmadan türetilmez.", "blocked"),
    ("revenue_basis_unresolved", "Ciro esası çözülmedi",
     "Satış raporundaki Net Sipariş Tutarı kanonik ciro değildir; fatura / iade esası sahip kararı bekliyor.", "open"),
    ("cross_sell_review", "Çapraz satış eşikleri gözden geçirilmedi",
     "Model geniş bir aday kitlesi üretebilir; eşikler değiştirilmedi.", "open"),
    ("hedef_stale_fields", "Hedef: kapasite / Risk / Kredi eski olabilir",
     "Hedef raporundaki kapasite, Risk ve Kredi alanları güncel olmayabilir; yalnızca bilgi amaçlıdır ve puanlamada "
     "kullanılmaz.", "open"),
    ("owner_file_of_record", "Sahip kaynakları: dosya kaydı kararı yok",
     "Kredi, Gider/Gelir, Bütçe ve Hedef için hangi dosyanın esas olduğu karara bağlanmadı; yalnızca açıkça "
     "yapılandırılan dosya okunur.", "open"),
    ("fair_decisions", "Fuar kararları açık",
     "ETKİNLİK / SAHA sınıflaması, fuar sonrası dönüşüm penceresi ve fuar maliyeti (ROI) karara bağlanmadı.", "open"),
    ("salesperson_of_record", "Kayıtlı satış temsilcisi kararı yok",
     "Temsilci bazlı sıralama veya performans gösterilmez.", "open"),
)


def known_issues(dq: dict | None, ops: dict | None, crm: dict | None) -> list[dict]:
    dq, ops, crm = dq or {}, ops or {}, crm or {}
    counts = {"dq4_stock_coverage": dq.get("stock_coverage_unavailable_products"),
              "crm_incomplete": crm.get("structured_interactions"),
              "cross_sell_review": ops.get("cross_sell_customers")}
    labels = {"dq4_stock_coverage": "kapsamı hesaplanamayan ürün", "crm_incomplete": "yapılandırılmış etkileşim",
              "cross_sell_review": "çapraz satış adayı müşteri"}
    return [{"code": code, "title": title, "detail": detail, "status": status, "count": counts.get(code),
             "count_label": labels.get(code)} for code, title, detail, status in KNOWN_ISSUES]


__all__ = ["OwnerRepository", "OwnerSourceConfig", "config_from_env", "build_kredi_view", "kredi_schedule",
           "build_gider_gelir_view", "build_butce_view", "kredi_section", "gider_gelir_section", "butce_section",
           "operations", "known_issues", "SOURCES", "OWNER_AUDIENCE_NOTE"]
