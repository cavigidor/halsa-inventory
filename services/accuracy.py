"""M8-ACCURACY — factual-integrity register and fail-closed display gate.

Owner directive (2026-10-18): NO VERIFIED EVIDENCE = NO BUSINESS CONCLUSION. Every analytical output (number,
classification, ranking, recommendation) has one internal state:

- ``VERIFIED``     — source identified and validated, report date known, required inputs present, deterministic
                     calculation, arithmetic / reconciliation checks pass, joins validated, business definition
                     approved, automated regression coverage, and the displayed description matches what the
                     calculation establishes;
- ``NOT_VERIFIED`` — possibly correct, but freshness, completeness, mapping, meaning or approval is insufficient;
- ``INVALID``      — a known error, inconsistency, missing required field, invalid formula, unsupported operation or
                     failed validation makes the result unreliable.

Only VERIFIED results may be shown as business facts. Everything else is WITHHELD from the dashboard HTML and the API
(backend / shared response layer, never only hidden in JavaScript) and replaced by a short neutral Turkish notice.

How the decision is made (``Gate.decide``), always fail-closed:

1. The in-code register below is a CEILING. A metric can be certified VERIFIED only together with an approval
   reference for every business definition it uses, named regression tests and a real-data reconciliation
   reference; ``validate_register()`` enforces this at import time.
2. Runtime evidence about the sources actually used (report date, Excel errors, missing cached formula values, missing
   required values, duplicates, join problems, partial periods, contradictory totals, unsupported layouts, unapproved
   mappings) can only DOWNGRADE that ceiling. Missing evidence is itself a downgrade (NOT_VERIFIED).
3. A combined output is the worst of its own state and the state of every output it depends on.
4. Evidence is bound to ONE source generation (`services/generation.Manifest`: SHA-256 of every selected workbook's
   original bytes). A consumer gets evidence for a source only when the data it SERVES came from exactly the recorded
   bytes (`Manifest.bound_snapshot`); evidence from another generation never certifies anything.
5. Time: an OPERATIONAL output (current state, e.g. stock levels, receivables) also needs a fresh source; a HISTORICAL
   output (a closed past period) needs a complete, dated period but no age limit. Age never approves anything.

Nothing here computes a business number. The algorithms stay where they are, unchanged, so they can be verified
later. There is no environment variable, flag or HTTP parameter that turns the gate off. The only way to show a
metric is a reviewed code change that certifies it with its evidence.
"""
from __future__ import annotations

import dataclasses as _dc
import datetime as dt
import enum
import hashlib
import re
import types
from dataclasses import dataclass, field
from typing import Iterable, Mapping

# ---------------------------------------------------------------------------------------------
# states and user-facing text
# ---------------------------------------------------------------------------------------------
WITHHELD_MESSAGE = "Bu analiz henüz doğrulanmadığı için gösterilmiyor."
WITHHELD_CODE = "accuracy_withheld"
WITHHELD_HTTP_STATUS = 409          # the resource exists; its verification state conflicts with displaying it
MAX_REASONS_SHOWN = 4               # keep the father-facing notice short
POLICY_VERSION = "m8-accuracy-1"    # embedded in every gated dashboard build (served `/` checks it)
DEFAULT_OPERATIONAL_MAX_AGE_DAYS = 45      # PROVISIONAL engineering default, not an owner-approved policy
TEMPORAL_KINDS = ("operational", "historical")
# M8-ACCURACY-2: the generation-bound evidence mechanism (services/generation.py) is implemented and shared by the
# dashboard, the API, the owner views and the refresh, with synthetic transition tests. It stays False until the owner
# has run the documented real-data acceptance (docs/accuracy/release_gate.md §8): while False, the production register
# refuses any VERIFIED certification (validate_register), so no production metric can be promoted.
GENERATION_BOUND_API_EVIDENCE = False


class State(str, enum.Enum):
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    INVALID = "invalid"


_RANK = {State.VERIFIED: 0, State.NOT_VERIFIED: 1, State.INVALID: 2}


def worst(*states: State) -> State:
    return max(states, key=lambda s: _RANK[s]) if states else State.VERIFIED


# ---------------------------------------------------------------------------------------------
# register model
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Definition:
    """A business definition a metric depends on. Approved only with a recorded owner decision (D-xxx)."""
    key: str
    title_tr: str
    approved: bool = False
    decision_ref: str | None = None        # e.g. "D-003" — required when approved
    question_ref: str | None = None        # where the open question is tracked


@dataclass(frozen=True)
class Blocker:
    """A known, documented reason a metric cannot be VERIFIED. `reason_tr` is shown to the user (short, no numbers,
    no names, no paths); `evidence` points to the code / test / doc that establishes it (English, docs only)."""
    code: str
    state: State
    reason_tr: str
    evidence: str = ""


@dataclass(frozen=True)
class Requirement:
    """What a metric ACTUALLY reads from one source (M8-ACCURACY-2B): the fields (column headers as the M5 pipeline
    reads them; `bucket:*` = every aging month bucket) and the named cross-source joins
    (services/source_validation.JOINS). Declared only after tracing the metric's code; an undeclared metric keeps
    the strict source-wide judgement."""
    source: str
    fields: tuple[str, ...] = ()
    joins: tuple[str, ...] = ()


@dataclass(frozen=True)
class Metric:
    key: str
    title_tr: str
    surfaces: tuple[str, ...]
    sources: tuple[str, ...] = ()
    definitions: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    certified: State = State.NOT_VERIFIED
    # "operational": current state → needs a fresh source (max_source_age_days); "historical": a closed past period
    # → needs a dated, complete period, no age limit. Both need a known report date and verified completeness.
    temporal: str = "operational"
    max_source_age_days: int | None = DEFAULT_OPERATIONAL_MAX_AGE_DAYS
    approval_refs: tuple[str, ...] = ()        # required for VERIFIED
    regression_tests: tuple[str, ...] = ()     # required for VERIFIED
    reconciliation_ref: str | None = None      # required for VERIFIED (real-data reconciliation evidence)
    # M8-ACCURACY-2B: field / join dependencies per source. Empty = not audited yet → every problem of every source
    # it uses applies (the strict PR #27 / #28 behaviour). Non-empty = one Requirement per source in `sources`.
    requires: tuple[Requirement, ...] = ()

    def scope(self, source: str) -> "Requirement | None":
        for r in self.requires:
            if r.source == source:
                return r
        return None


# ---------------------------------------------------------------------------------------------
# business definitions (only D-003 is an existing, owner-backed decision)
# ---------------------------------------------------------------------------------------------
_Q = "docs/m8_owner_questions_tr.md"
DEFINITIONS: dict[str, Definition] = {d.key: d for d in (
    Definition("customer_identity", "Müşteri kimliği = sipariş kaynağı (Pro_kodu)", True, "D-003"),
    Definition("customer_code_whitespace", "Müşteri kodlarında baş/son boşluk politikası", question_ref=f"{_Q} #10"),
    Definition("revenue_basis", "Ciro esası (sipariş / teslim / fatura; iptal ve iade)", question_ref=f"{_Q} #3"),
    Definition("lapsed_window", "Kayıp müşteri nitelik dönemi (2020–2025 mi 2021–2025 mi)", question_ref=f"{_Q} #11"),
    Definition("kapali_attribute_source", "KAPALI / temsilci / sektör kaynağı (DQ-5)", question_ref=f"{_Q} #10"),
    Definition("salesperson_of_record", "Kayıtlı satış temsilcisi kaynağı", question_ref=f"{_Q} #10"),
    Definition("stock_coverage_rule", "Kapsamı hesaplanamayan ürünlerin kuralı (DQ-4)", question_ref=f"{_Q} #9"),
    Definition("inventory_valuation", "Envanter değerinin hesap esası", question_ref="docs/accuracy/open_verification_items.md"),
    Definition("margin_cost_basis", "Maliyeti eksik satırların kâr hesabı (DQ-1)", question_ref=f"{_Q} #7"),
    Definition("overdue_definition", "Cari yaşlandırma sütunlarının 'vadesi geçmiş' anlamı", question_ref=f"{_Q} #15"),
    Definition("priority_thresholds", "Tahsilat / aksiyon öncelik eşikleri", question_ref=f"{_Q} #15"),
    Definition("inflation_source", "Reel büyüme için enflasyon kaynağı", question_ref="TASKS.md Milestone 4"),
    Definition("active_customer_window", "'Aktif müşteri' penceresi (son 365 gün)", question_ref=f"{_Q} #12"),
    Definition("cross_sell_thresholds", "Çapraz satış eşikleri", question_ref=f"{_Q} #12"),
    Definition("fair_taxonomy", "FUAR / ETKİNLİK / SAHA sınıflaması ve dönüşüm penceresi", question_ref=f"{_Q} #16"),
    Definition("hedef_file_of_record", "Hedef esas dosyası ve güncelliği", question_ref=f"{_Q} #2"),
    Definition("kredi_durum", "Kredi DURUM / 'Kapalı' anlamı", question_ref="TASKS.md (2026-10-19 yanıtı)"),
    Definition("kredi_file_of_record", "Kredi esas dosyası ve döküm tarihi", question_ref=f"{_Q} #2"),
    Definition("gg_accounting_basis", "Gider/Gelir muhasebe esası (nakit / tahakkuk)", question_ref=f"{_Q} #4"),
    Definition("gg_business_line_mapping", "Gider/Gelir iş kolu eşlemesi", question_ref=f"{_Q} #4"),
    Definition("butce_file_of_record", "Bütçe esas dosyası ve bozuk formüllerin onarımı", question_ref=f"{_Q} #5"),
    Definition("trodat_sku_mapping", "Trodat model → Halsa stok kodu eşlemesi ve birimi", question_ref=f"{_Q} #6"),
    Definition("last_order_date_basis", "'Son sipariş tarihi' esası (iptal / teslim edilmemiş siparişler)",
               question_ref=f"{_Q} #3"),
    Definition("operational_freshness", "Güncel durum analizleri için kaynak güncelliği politikası (45 gün geçici)",
               question_ref="docs/accuracy/release_gate.md"),
)}


# ---------------------------------------------------------------------------------------------
# blockers (reason_tr is shown; evidence is for the register)
# ---------------------------------------------------------------------------------------------
NV, INV = State.NOT_VERIFIED, State.INVALID
B_REAL = Blocker("REAL-DATA", NV, "Gerçek verilerle mutabakat henüz yapılmadı.",
                 "No real ERP data in the CI/cloud environment; tests/test_local_real_data.py is owner-local only.")
B_REVENUE = Blocker("REVENUE-BASIS", NV, "Ciro esası (sipariş, teslim, fatura; iptal ve iade) onaylanmadı.",
                    "dashboard_builder.load_sales sums Nettutar (ordered net, includes Kalan/Vazgecilen parts; "
                    "docs/m6_source_inventory.md §2.2); owner question #3 open.")
B_AMOUNT_ZERO = Blocker("AMT-ZERO", NV, "Eksik satış tutarları hesapta sıfır sayılıyor.",
                        "dashboard_builder.load_sales: Nettutar fillna(0) (comment: 'M5 still sums 0').")
B_DQ5 = Blocker("DQ-5", NV, "KAPALI, temsilci ve sektör bilgisi fatura hesabından alınıyor (DQ-5).",
                "dashboard_builder.cust_attrs applies sales lookup columns (keyed by Musterikod) to Pro_kodu customers.")
B_WINDOW = Blocker("LAPSED-WINDOW", NV, "Kayıp müşteri dönemi (2020 mi, 2021 mi) karara bağlanmadı.",
                   "dashboard_builder.QUAL_YEARS = 2021–2025 vs spec 2020–2025 (TASKS Blocked).")
B_DQ6 = Blocker("DQ-6", INV, "Vadesi geçen borç, sipariş müşterisi kodu ile fatura hesabı kodu karıştırılarak "
                "eşleniyor; borç sıfır görünebilir (DQ-6).",
                "dashboard_builder.m_lapsed owed.get(order-source code) over a map keyed by Cari 'Hesap kodu' "
                "(invoice account); tests/test_m8_accuracy.py::test_dq6_winback_overdue_identity_mismatch.")
B_DQ4 = Blocker("DQ-4", NV, "Kapsamı hesaplanamayan (#DIV/0!) ürünler listeye girmiyor; liste eksik (DQ-4).",
                "dashboard_builder.m_stock requires STOK Kontrol % (num() coerces #DIV/0! to NaN); "
                "docs/m6_source_inventory.md §12 (1,178 of the 3,311 product rows on the 5 Oct 2026 export; the "
                "export's 3 trailing rows are report totals, M8-ACCURACY-2B).")
B_STOCK_DATE = Blocker("STOCK-DATE", NV, "Stok raporunun tarihi doğrulanmıyor.",
                       "dashboard_builder.select_sources picks the newest file by mtime; no report date is read.")
# STOCK-CODE-AUTHORITY (2A/2B) is resolved by the owner's column-identity decision D-056 (2026-10-21): column A is
# the product code, column J the country of origin. It is no longer a static blocker; instead the evidence decides —
# the duplicated `Stok_Kodu` header counts as resolved ONLY when the workbook's header row is exactly the approved
# template (services/source_validation.STOCK_TEMPLATES); any other duplicated-identity layout is refused (INVALID).
# The approval covers column identity only — not freshness, completeness, quantities, totals or product correctness.
B_STOCK_SCOPE = Blocker("STOCK-REPORT-SCOPE", NV, "Stok raporunun hangi ürünleri listelediği (kapsamı) ve tekrarlanan "
                        "ürün kodunun anlamı doğrulanmadı.",
                        "M8-ACCURACY-2A: one product code appears in two rows with different values; report scope "
                        "not documented.")
B_INV_VALUE = Blocker("INV-VALUE", NV, "Envanter değerinin hesap esası onaylanmadı.",
                      "m_stock uses the export's cached 'Envanter değeri' column; its basis is not documented.")
B_DQ2 = Blocker("DQ-2", INV, "KAPALI müşteriler alıcı listesinde yer alabiliyor (DQ-2).",
                "dashboard_builder.m_overstock_move has no KAPALI filter (fixed only in the AI context, D-009).")
B_DQ1 = Blocker("DQ-1", INV, "Maliyeti eksik satırlar kâr hesabında sıfır sayılıyor (DQ-1).",
                "dashboard_builder.load_sales _prof fillna(0); strict xfail "
                "tests/test_domain_rules.py::test_dashboard_margin_does_not_treat_missing_cost_as_zero.")
B_AGING_DATE = Blocker("AGING-DATE", NV, "Cari yaşlandırma raporunun tarihi doğrulanmıyor.",
                       "dashboard_builder.m_collections reads no report date; file chosen by mtime.")
B_OVERDUE_DEF = Blocker("OVERDUE-DEF", NV, "Yaşlandırma sütunlarının 'vadesi geçmiş' anlamı ve boş hücrelerin "
                        "sıfır sayılması onaylanmadı.",
                        "m_collections sums every month bucket as 'Vadesi Geçen' and fills blanks with 0; "
                        "'Toplam Döviz Bakiye' is ignored.")
B_SALESPERSON = Blocker("SALESPERSON", NV, "Kayıtlı satış temsilcisi kaynağı karara bağlanmadı.",
                        "Rep columns come from cached sales lookups / Cari 'Temsilci kodu'; owner question #10.")
B_PRIORITY = Blocker("PRIORITY", NV, "Öncelik puanları ve eşikleri onaylanmadı.",
                     "services/scoring.py weights and action_sources.COLLECTIONS_THRESHOLD; owner question #15.")
B_INFLATION = Blocker("INFLATION", NV, "Reel büyüme için kullanılan enflasyon oranı doğrulanmış bir kaynaktan "
                      "gelmiyor.", "config.MANUAL_INFLATION_RATE (manual env value); Milestone 4 not built.")
B_XSELL = Blocker("XSELL", NV, "Çapraz satış eşikleri onaylanmadı; geçmiş birliktelik gelecekteki alım niyetini "
                  "kanıtlamaz.", "config.CROSS_SELL_* unchanged; ~78 % of active customers get a target (TASKS).")
B_ACTIVE = Blocker("ACTIVE-WINDOW", NV, "'Aktif müşteri' tanımı (son 365 gün) onaylanmadı.",
                   "services/crm.ACTIVE_WINDOW_DAYS / config.CROSS_SELL_RECENT_DAYS = 365.")
B_FAIR = Blocker("FAIR-TAXONOMY", NV, "Fuar sınıflaması (FUAR / ETKİNLİK / SAHA) ve ciro esası karara bağlanmadı.",
                 "D-045 keeps FUAR-only history; owner question #16 open.")
B_HEDEF = Blocker("HEDEF", NV, "Hedef raporunun esas dosyası belirlenmedi; kapasite, Risk ve Kredi alanları eski.",
                  "D-037; docs/m6_source_inventory.md §5.")
B_WHITESPACE = Blocker("CODE-WHITESPACE", NV, "Müşteri kodlarındaki baş/son boşluk politikası karara bağlanmadı.",
                       "TASKS 'Identity whitespace policy' (trailing spaces on sales codes kept exactly).")
B_KREDI_DURUM = Blocker("KREDI-DURUM", NV, "Kredi DURUM / 'Kapalı' anlamı henüz netleştirilmedi.",
                        "services/owner_view.build_kredi_view counts DURUM only (D-047); answer due 2026-10-19.")
B_KREDI_SNAP = Blocker("KREDI-SNAPSHOT", NV, "Kredi dökümünün tarihi ve esas dosyası doğrulanmadı.",
                       "Snapshot date optional (KREDI_SNAPSHOT_DATE); two candidate files pass (TASKS).")
B_GG_BASIS = Blocker("GG-BASIS", NV, "Gider/Gelir muhasebe esası (nakit / tahakkuk) doğrulanmadı.",
                     "owner_view GG_BASIS_NOTE; D-040.")
B_GG_MAP = Blocker("GG-MAPPING", NV, "İş kolu eşlemesi (KIRTASIYE, TRODAT, TROTEC, IMALAT) onaylanmadı.",
                   "D-040; management_mapping = not_supplied.")
B_GG_TOTALS = Blocker("GG-TOTALS", INV, "Kaynakta yıllık toplam ile aylık toplamlar, ÖZET ile gider tablosu arasında "
                      "uyuşmazlık var.", "docs/m6_gider_gelir.md: '<Y> TOPLAM = Σ months' is a warning (1 real row "
                      "differs); ÖZET pivot vs expense table mismatch is a warning; 23 manual allocation overrides.")
B_BUTCE = Blocker("BUTCE-ERRORS", INV, "Bütçe dosyasında #REF! / #DIV/0! hataları ve dış dosya bağlantısı var; esas "
                  "dosya belirlenmedi.", "docs/m6_source_inventory.md §10; D-041 (never authoritative).")
B_AI = Blocker("AI", NV, "Yapay zekâ yorumları kanıt sayılmaz; yönetim panosunda kapalıdır.",
               "Owner directive Part E; services/agent.py output is an interpretation, never evidence.")
B_SCENARIO = Blocker("SCENARIO", NV, "Senaryo, doğrulanmamış vadesi geçen toplamı ve elle girilen varsayımları "
                     "kullanıyor.", "app.scenario uses working_capital.overdue_total + request assumptions.")
B_SOURCE_MISSING = Blocker("SOURCE-MISSING", NV, "Kaynak rapor sağlanmadı.",
                           "Evrak Detaylı Tahsilat Raporu not supplied (M6-4 blocked).")
B_TRODAT = Blocker("TRODAT-MAPPING", NV, "Trodat ürün eşlemesi ve birimi doğrulanmadı.", "D-039.")
B_LAST_ORDER = Blocker("LAST-ORDER-DATE", NV, "Son sipariş tarihi satış satırlarından türetiliyor; iptal edilen veya "
                       "teslim edilmeyen siparişler de sayılıyor ve kaynağın tamlığı doğrulanmadı.",
                       "services/customer360.py: last_order_date = max Tarih of the customer's order lines "
                       "(Kalan / Vazgecilen parts included, docs/m6_source_inventory.md §2.2).")
B_MASTER_STATUS = Blocker("MASTER-STATUS", NV, "Müşterinin açık / KAPALI durumunun esas kaynağı karara bağlanmadı "
                          "(DQ-5).", "services/customer360.master_closed reads liste CARİ FİRMA; the attribute source "
                          "of record is owner question #10; about 10 duplicate master codes (M6-0 §3).")

# ---------------------------------------------------------------------------------------------
# the metric register (certified states). Keys are stable; docs/accuracy/metric_register.md mirrors them.
# ---------------------------------------------------------------------------------------------
_M = Metric
REGISTER: dict[str, Metric] = {m.key: m for m in (
    # ---- static dashboard modules (dashboard_builder.build) ----
    _M("stock.reorder", "Azalan Stok", ("dashboard:reorder", "dashboard:stat:min_alti", "api:/api/context"),
       ("stock",), ("stock_coverage_rule",), blockers=(B_DQ4, B_STOCK_DATE, B_STOCK_SCOPE, B_REAL),
       # traced: dashboard_builder.m_stock `low` (code, name, quantity, coverage, min, max, DURUM); no join
       requires=(Requirement("stock", ("Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye",
                                        "maxseviye", "DURUM")),)),
    _M("stock.overstock", "Fazla Stok", ("dashboard:overstock", "dashboard:stat:maks_ustu",
                                         "dashboard:stat:fazla_stok_degeri"),
       ("stock",), ("stock_coverage_rule", "inventory_valuation"),
       blockers=(B_DQ4, B_STOCK_DATE, B_INV_VALUE, B_STOCK_SCOPE, B_REAL),
       # traced: m_stock `over` (code, name, quantity, coverage, max, inventory value, DURUM); no join
       requires=(Requirement("stock", ("Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "maxseviye",
                                        "Envanter değeri", "DURUM")),)),
    _M("stock.product_counts", "Stok ürün sayıları", ("dashboard:header:urun_sayisi", "refresh:products"),
       ("stock",), blockers=(B_STOCK_SCOPE, B_STOCK_DATE, B_REAL),
       # traced: dashboard_builder.stock_row_counts (identity column only); no join
       requires=(Requirement("stock", ("Stok_Kodu",)),)),
    _M("stock.clearance_buyers", "Stok Eritme", ("dashboard:osmove",), ("stock", "sales"),
       ("revenue_basis", "kapali_attribute_source", "customer_identity"), ("stock.overstock",),
       blockers=(B_DQ2, B_AMOUNT_ZERO, B_REAL), certified=INV),
    _M("customers.winback", "Geri Kazanım", ("dashboard:winback", "dashboard:stat:kayip_musteri",
                                              "dashboard:stat:borclu_kayip", "dashboard:tiers"),
       ("sales", "aging"), ("customer_identity", "lapsed_window", "revenue_basis", "kapali_attribute_source",
                            "salesperson_of_record"),
       blockers=(B_DQ6, B_WINDOW, B_REVENUE, B_AMOUNT_ZERO, B_DQ5, B_REAL), certified=INV),
    _M("customers.momentum", "Büyüme / Küçülme", ("dashboard:momentum",), ("sales",),
       ("customer_identity", "revenue_basis", "kapali_attribute_source", "salesperson_of_record"),
       blockers=(B_REVENUE, B_AMOUNT_ZERO, B_DQ5, B_REAL)),
    _M("customers.order_size", "Sipariş Büyüklüğü", ("dashboard:ordersize",), ("sales",),
       ("customer_identity", "revenue_basis", "kapali_attribute_source", "salesperson_of_record"),
       blockers=(B_REVENUE, B_AMOUNT_ZERO, B_DQ5, B_REAL), temporal="historical", max_source_age_days=None),
    _M("finance.margin", "Kâr Marjı", ("dashboard:margin",), ("sales",), ("revenue_basis", "margin_cost_basis"),
       blockers=(B_DQ1, B_REVENUE, B_REAL), certified=INV, temporal="historical", max_source_age_days=None),
    _M("receivables.risk", "Riskli Müşteri", ("dashboard:risk",), ("sales", "aging"),
       ("overdue_definition", "revenue_basis", "salesperson_of_record"),
       blockers=(B_AGING_DATE, B_OVERDUE_DEF, B_REVENUE, B_SALESPERSON, B_REAL)),
    _M("receivables.collections", "Tahsilat (vadesi geçen alacak)", ("dashboard:collections",
                                                                      "dashboard:stat:vadesi_gecen_toplam"),
       ("aging",), ("overdue_definition",), blockers=(B_AGING_DATE, B_OVERDUE_DEF, B_REAL),
       # traced: dashboard_builder.m_collections (account code, name, representative, every month bucket); no join
       # (it neither reads sales nor the customer master). Supplier-prefix accounts: open question (2A), not a rule.
       requires=(Requirement("aging", ("Hesap kodu", "Hesap adı", "Temsilci kodu", "bucket:*")),)),
    # ---- server-driven views / APIs ----
    _M("actions.real_decline", "Reel Satış Düşüşü", ("api:/api/agent/actions",), ("sales",),
       ("inflation_source", "revenue_basis"), ("customers.momentum",), blockers=(B_INFLATION, B_REVENUE)),
    _M("actions.queue", "AI Aksiyon Merkezi — günlük aksiyon listesi", ("panel:ai", "api:/api/agent/actions"),
       definitions=("priority_thresholds",),
       depends_on=("receivables.collections", "stock.clearance_buyers", "customers.winback", "actions.real_decline"),
       blockers=(B_PRIORITY,)),
    _M("ai.interpretation", "Yapay zekâ yorumu ve önerisi", ("api:/api/agent/actions/enrich",
                                                              "api:/api/agent/customer/{customer_code}/analyze",
                                                              "api:/api/agent/product/{product_code}/analyze"),
       ("ai_provider",), depends_on=("actions.queue",), blockers=(B_AI,)),
    _M("ai.drafts", "Mesaj taslağı (yapay zekâ)", ("api:/api/agent/draft-message",), ("ai_provider",),
       depends_on=("agent.customer_facts",), blockers=(B_AI,)),
    _M("agent.customer_facts", "Müşteri bilgi kartı (aksiyon çekmecesi)", ("api:/api/agent/customer/{customer_code}",),
       depends_on=("customers.winback", "receivables.collections", "customers.momentum", "receivables.risk")),
    _M("agent.product_facts", "Ürün bilgi kartı", ("api:/api/agent/product/{product_code}",),
       depends_on=("stock.overstock", "stock.reorder", "stock.clearance_buyers")),
    _M("context.full", "Tüm analiz bağlamı", ("api:/api/context",),
       depends_on=("stock.reorder", "stock.overstock", "stock.clearance_buyers", "customers.winback",
                   "receivables.collections", "receivables.risk", "actions.queue")),
    _M("opportunities.cross_sell", "Çapraz satış fırsatları", ("panel:opp", "api:/api/opportunities/summary"),
       ("sales",), ("cross_sell_thresholds", "active_customer_window", "customer_identity"),
       blockers=(B_XSELL, B_ACTIVE, B_REAL)),
    _M("opportunities.territories", "Bölge / sektör fırsat sayıları", ("api:/api/opportunities/territories",),
       ("sales", "hedef"), ("hedef_file_of_record",), ("opportunities.cross_sell",), blockers=(B_HEDEF,)),
    _M("sales_queue", "Günlük Satış listesi", ("panel:sq", "api:/api/sales-queue"),
       definitions=("priority_thresholds",),
       depends_on=("receivables.collections", "customers.winback", "actions.real_decline",
                   "opportunities.cross_sell"), blockers=(B_PRIORITY,)),
    _M("customer360.profile", "Müşteri 360 profili", ("panel:c360", "api:/api/customer360/{customer_code}"),
       ("sales", "aging", "hedef"), ("customer_identity", "customer_code_whitespace", "revenue_basis",
                                     "salesperson_of_record", "hedef_file_of_record"),
       ("opportunities.cross_sell",), blockers=(B_REVENUE, B_WHITESPACE, B_SALESPERSON, B_HEDEF, B_AGING_DATE)),
    _M("fairs.history", "Fuar geçmişi (FUAR siparişleri)", ("api:/api/fairs/history",
                                                             "api:/api/fairs/history/{event_id}",
                                                             "api:fairs.campaign.linked_event",
                                                             "api:fairs.campaign.member_erp_evidence"),
       ("sales",), ("fair_taxonomy", "revenue_basis"), blockers=(B_FAIR, B_REVENUE, B_REAL), temporal="historical",
       max_source_age_days=None),
    _M("fairs.segments", "Fuar aday segmentleri", ("api:/api/fairs/candidates", "api:fairs.campaign.member_segments"),
       definitions=("active_customer_window",),
       depends_on=("fairs.history", "customers.winback", "actions.real_decline", "opportunities.cross_sell",
                   "receivables.collections"), blockers=(B_ACTIVE,)),
    _M("crm.active_customers", "CRM 'aktif müşteri' sayıları", ("api:crm.summary.active_customers",),
       ("sales",), ("active_customer_window",), blockers=(B_ACTIVE,)),
    _M("owner.kredi", "Kredi ödeme takvimi", ("api:owner.overview.kredi",), ("kredi",),
       ("kredi_durum", "kredi_file_of_record"), blockers=(B_KREDI_DURUM, B_KREDI_SNAP)),
    _M("owner.gider_gelir", "Gider / Gelir", ("api:owner.overview.gider_gelir",), ("gider_gelir",),
       ("gg_accounting_basis", "gg_business_line_mapping"), blockers=(B_GG_TOTALS, B_GG_BASIS, B_GG_MAP),
       certified=INV, temporal="historical", max_source_age_days=None),
    _M("owner.butce", "Bütçe", ("api:owner.overview.butce",), ("butce",), ("butce_file_of_record",),
       blockers=(B_BUTCE,), certified=INV, temporal="historical", max_source_age_days=None),
    _M("owner.operations", "Yönetim operasyon sayıları", ("api:owner.overview.operations",),
       depends_on=("sales_queue", "opportunities.cross_sell")),
    _M("scenario.receivables", "Senaryo hesabı", ("api:/api/scenario",), definitions=("inflation_source",),
       depends_on=("receivables.collections",), blockers=(B_SCENARIO,)),
    _M("collections.detailed", "Detaylı tahsilat (evrak bazlı)", (), ("detailed_collections",),
       blockers=(B_SOURCE_MISSING,)),
    _M("trodat.sales", "Trodat satış raporu", (), ("trodat",), ("trodat_sku_mapping",), blockers=(B_TRODAT,),
       temporal="historical", max_source_age_days=None),
    # ---- customer directory attributes (search, CRM, fair members) ----
    _M("customer.last_order_date", "Müşterinin son sipariş tarihi", ("api:/api/customer360/search",
                                                                      "api:crm.followups", "api:fairs.campaign.members"),
       ("sales",), ("last_order_date_basis", "customer_identity", "customer_code_whitespace"),
       blockers=(B_LAST_ORDER, B_WHITESPACE, B_REAL)),
    _M("customer.master_status", "Müşterinin açık / KAPALI durumu", ("api:/api/customer360/search", "api:crm.customer",
                                                                     "api:crm.followups", "api:fairs.campaign.members"),
       ("liste",), ("kapali_attribute_source",), blockers=(B_MASTER_STATUS, B_REAL)),
)}

def _with_freshness(reg: dict[str, Metric]) -> dict[str, Metric]:
    """Every operational, source-bound metric depends on the (unapproved) freshness policy definition."""
    out = {}
    for k, m in reg.items():
        if m.temporal == "operational" and m.sources and "operational_freshness" not in m.definitions:
            m = _dc.replace(m, definitions=m.definitions + ("operational_freshness",))
        out[k] = m
    return out


REGISTER = _with_freshness(REGISTER)

# Dashboard overview stat cards → the metric that owns the number (dashboard_builder.build `stats`).
STAT_METRIC = {"min altı": "stock.reorder", "maks üstü": "stock.overstock", "fazla stok değeri": "stock.overstock",
               "kayıp müşteri": "customers.winback", "vadesi geçen toplam": "receivables.collections",
               "borçlu kayıp müşteri": "customers.winback"}
# Static dashboard modules → metric.
MODULE_METRIC = {"reorder": "stock.reorder", "overstock": "stock.overstock", "osmove": "stock.clearance_buyers",
                 "winback": "customers.winback", "momentum": "customers.momentum", "ordersize": "customers.order_size",
                 "margin": "finance.margin", "risk": "receivables.risk", "collections": "receivables.collections"}
# Server-driven dashboard panels → metric (owner `own`, CRM `crm` and fairs `fair` are redacted field by field).
PANEL_METRIC = {"ai": "actions.queue", "sq": "sales_queue", "c360": "customer360.profile",
                "opp": "opportunities.cross_sell"}
# Controlled-refresh report counts → metric (`products` is the stock export's row count: system information).
REFRESH_COUNT_METRIC = {"low_stock": "stock.reorder", "lapsed": "customers.winback",
                        "overstock_value": "stock.overstock", "overdue_total": "receivables.collections",
                        "actions": "actions.queue", "products": "stock.product_counts"}

# ---------------------------------------------------------------------------------------------
# API route classification. EVERY route must be listed (tests enforce it); an unlisted route is withheld.
#   ("metric", key)  → the whole response is withheld unless the metric is VERIFIED
#   ("redacted", key-set) → the endpoint redacts the named metrics field by field (shared helpers below)
#   ("system", None) → service / source status, no business conclusion
#   ("reference", None) → directory lookup (code, name, master status, last order date as recorded)
#   ("operational", None) → user-entered records and state changes (CRM, fair campaigns, action state, refresh)
# ---------------------------------------------------------------------------------------------
ROUTES: dict[tuple[str, str], tuple[str, str | tuple[str, ...] | None]] = {
    ("GET", "/"): ("system", None),                         # gated at build time (POLICY_VERSION marker checked)
    ("GET", "/openapi.json"): ("system", None),
    ("GET", "/docs"): ("system", None),
    ("GET", "/docs/oauth2-redirect"): ("system", None),
    ("GET", "/redoc"): ("system", None),
    ("GET", "/api/health"): ("system", None),
    ("GET", "/api/agent/categories"): ("system", None),
    ("GET", "/api/ai/calls"): ("system", None),
    ("GET", "/api/macro"): ("system", None),
    ("GET", "/api/accuracy/status"): ("system", None),      # verification states + generation identity; no values
    ("GET", "/api/context"): ("metric", "context.full"),
    ("POST", "/api/context/refresh"): ("redacted", ("refresh.counts",)),
    ("GET", "/api/agent/actions"): ("metric", "actions.queue"),
    ("POST", "/api/agent/actions/enrich"): ("metric", "ai.interpretation"),
    ("GET", "/api/agent/customer/{customer_code}"): ("metric", "agent.customer_facts"),
    ("POST", "/api/agent/customer/{customer_code}/analyze"): ("metric", "ai.interpretation"),
    ("GET", "/api/agent/product/{product_code}"): ("metric", "agent.product_facts"),
    ("POST", "/api/agent/product/{product_code}/analyze"): ("metric", "ai.interpretation"),
    ("POST", "/api/agent/draft-message"): ("metric", "ai.drafts"),
    ("POST", "/api/scenario"): ("metric", "scenario.receivables"),
    ("GET", "/api/customer360/status"): ("system", None),
    ("GET", "/api/customer360/search"): ("redacted", ("customer.last_order_date", "customer.master_status")),
    ("GET", "/api/customer360/{customer_code}"): ("metric", "customer360.profile"),
    ("GET", "/api/sales-queue"): ("metric", "sales_queue"),
    ("GET", "/api/opportunities/summary"): ("metric", "opportunities.cross_sell"),
    ("GET", "/api/opportunities/territories"): ("metric", "opportunities.territories"),
    ("GET", "/api/fairs/history"): ("metric", "fairs.history"),
    ("GET", "/api/fairs/history/{event_id}"): ("metric", "fairs.history"),
    ("GET", "/api/fairs/candidates"): ("metric", "fairs.segments"),
    ("GET", "/api/fairs/campaigns"): ("redacted", ("fairs.history",)),
    ("POST", "/api/fairs/campaigns"): ("redacted", ("fairs.history",)),
    ("GET", "/api/fairs/campaigns/{campaign_id}"): ("redacted", ("fairs.history",)),
    ("PUT", "/api/fairs/campaigns/{campaign_id}"): ("redacted", ("fairs.history",)),
    ("POST", "/api/fairs/campaigns/{campaign_id}/archive"): ("redacted", ("fairs.history",)),
    ("GET", "/api/fairs/campaigns/{campaign_id}/customers"): ("redacted", ("fairs.history", "fairs.segments",
                                                                         "customer.last_order_date",
                                                                         "customer.master_status")),
    ("POST", "/api/fairs/campaigns/{campaign_id}/customers"): ("operational", None),
    ("PUT", "/api/fairs/campaigns/{campaign_id}/customers"): ("redacted", ("fairs.history", "fairs.segments",
                                                                         "customer.last_order_date",
                                                                         "customer.master_status")),
    ("GET", "/api/crm/summary"): ("redacted", ("crm.active_customers",)),
    ("GET", "/api/crm/followups"): ("redacted", ("customer.master_status",)),
    ("GET", "/api/crm/recent"): ("operational", None),
    ("GET", "/api/crm/customers/{customer_code}"): ("redacted", ("customer.master_status",)),
    ("POST", "/api/crm/customers/{customer_code}/interactions"): ("redacted", ("customer.master_status",)),
    ("PUT", "/api/crm/customers/{customer_code}/followup"): ("redacted", ("customer.master_status",)),
    ("GET", "/api/owner/overview"): ("redacted", ("owner.kredi", "owner.gider_gelir", "owner.butce",
                                                  "owner.operations", "opportunities.cross_sell")),
    ("GET", "/api/owner/status"): ("redacted", ("opportunities.cross_sell",)),
    ("POST", "/api/owner/reload"): ("operational", None),
    ("POST", "/api/actions/{action_id}/complete"): ("operational", None),
    ("POST", "/api/actions/{action_id}/defer"): ("operational", None),
    ("POST", "/api/actions/{action_id}/dismiss"): ("operational", None),
    ("POST", "/api/actions/{action_id}/reopen"): ("operational", None),
}


# ---------------------------------------------------------------------------------------------
# register validation (import time): VERIFIED needs approvals, tests and reconciliation evidence
# ---------------------------------------------------------------------------------------------
class RegisterError(ValueError):
    pass


_DECISION_RE = re.compile(r"^D-\d{3}$")


def validate_register(register: dict[str, Metric] | None = None,
                      definitions: dict[str, Definition] | None = None, *, production: bool | None = None) -> None:
    """`production` (default: True when validating the module's own REGISTER) adds the release-level rules: every
    metric-bound route must exist in the register, and no metric may be certified VERIFIED until generation-bound
    evidence is shared with the API (GENERATION_BOUND_API_EVIDENCE, an M8-ACCURACY-2 acceptance criterion)."""
    production = (register is None) if production is None else production
    reg = REGISTER if register is None else register
    defs = DEFINITIONS if definitions is None else definitions
    for d in defs.values():
        if d.approved and not (d.decision_ref and _DECISION_RE.match(d.decision_ref)):
            raise RegisterError(f"definition {d.key}: approved without a D-xxx decision reference")
    for k, m in reg.items():
        if k != m.key:
            raise RegisterError(f"register key {k} != metric key {m.key}")
        if m.temporal not in TEMPORAL_KINDS:
            raise RegisterError(f"{k}: temporal must be one of {TEMPORAL_KINDS}")
        if m.temporal == "operational" and not (isinstance(m.max_source_age_days, int) and m.max_source_age_days > 0):
            raise RegisterError(f"{k}: an operational metric needs a positive max_source_age_days")
        if m.temporal == "historical" and m.max_source_age_days is not None:
            raise RegisterError(f"{k}: a historical metric is judged by period completeness, not by age")
        for dk in m.definitions:
            if dk not in defs:
                raise RegisterError(f"{k}: unknown definition {dk}")
        for dep in m.depends_on:
            if dep not in reg:
                raise RegisterError(f"{k}: unknown dependency {dep}")
        if m.requires:
            _validate_requires(k, m)
        for b in m.blockers:
            if b.state == State.VERIFIED:
                raise RegisterError(f"{k}: a blocker cannot be VERIFIED")
            if not b.reason_tr.strip():
                raise RegisterError(f"{k}: blocker {b.code} without a reason")
        if m.certified == State.VERIFIED:
            if production and m.temporal == "operational" and m.sources and "operational_freshness" not in m.definitions:
                raise RegisterError(f"{k}: an operational metric needs the approved freshness policy definition")
            if m.blockers:
                raise RegisterError(f"{k}: certified VERIFIED with open blockers")
            unapproved = [dk for dk in m.definitions if not defs[dk].approved]
            if unapproved:
                raise RegisterError(f"{k}: certified VERIFIED with unapproved definitions {unapproved}")
            if not m.approval_refs or not all(_DECISION_RE.match(r) for r in m.approval_refs):
                raise RegisterError(f"{k}: certified VERIFIED without D-xxx approval references")
            if not m.regression_tests:
                raise RegisterError(f"{k}: certified VERIFIED without regression tests")
            if not m.reconciliation_ref:
                raise RegisterError(f"{k}: certified VERIFIED without real-data reconciliation evidence")
        elif m.certified not in (State.NOT_VERIFIED, State.INVALID):
            raise RegisterError(f"{k}: unknown certified state")
    # no cycles
    def visit(key, stack):
        if key in stack:
            raise RegisterError(f"dependency cycle: {' -> '.join(stack + (key,))}")
        for dep in reg[key].depends_on:
            visit(dep, stack + (key,))
    for k in reg:
        visit(k, ())
    if production:
        for (_, _), (kind, ref) in ROUTES.items():       # the production register covers every gated route
            refs = (ref,) if kind == "metric" else (ref if kind == "redacted" else ())
            for r in refs:
                if r not in reg and r != "refresh.counts":
                    raise RegisterError(f"route references unknown metric {r}")
        if not GENERATION_BOUND_API_EVIDENCE:
            early = [k for k, m in reg.items() if m.certified == State.VERIFIED]
            if early:
                raise RegisterError(f"{early}: no metric may be certified VERIFIED before generation-bound evidence is "
                                    "shared by the dashboard, the API, the owner views and the refresh (M8-ACCURACY-2)")


def _validate_requires(key: str, m: "Metric") -> None:
    """A declared metric names every source it uses exactly once, only fields the source inspection knows and only
    named joins (M8-ACCURACY-2B)."""
    from services import source_validation as SV
    srcs = [r.source for r in m.requires]
    if sorted(srcs) != sorted(set(srcs)) or set(srcs) != set(m.sources):
        raise RegisterError(f"{key}: requires must name each of its sources exactly once")
    for r in m.requires:
        spec = SV.CORE.get(r.source)
        if spec is None:
            raise RegisterError(f"{key}: field-level requirements are only defined for core sources ({r.source})")
        known = set(spec["required"]) | ({"bucket:*"} if spec.get("buckets") else set())
        unknown = [f for f in r.fields if f not in known]
        if unknown or not r.fields:
            raise RegisterError(f"{key}: unknown or empty field requirements {unknown} for {r.source}")
        bad = [j for j in r.joins if j not in SV.JOINS]
        if bad:
            raise RegisterError(f"{key}: unknown joins {bad}")


# ---------------------------------------------------------------------------------------------
# runtime source evidence (can only downgrade). Unknown (None) is NEVER treated as passing.
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceEvidence:
    """Facts about one source as it was actually read. Counts only, never values, names or paths.

    Every field starts as unknown (None). A collector sets only what it really inspected; an inspection that raised is
    recorded in `inspection_failures` (INVALID), never as zero errors. The report date and the period are facts about
    the source; how old a source may be is a property of the METRIC (operational vs historical), decided by the gate."""
    source: str
    available: bool | None = None
    layout_supported: bool | None = None
    report_date: dt.date | None = None             # the source's business date: DECLARED snapshot / report date only
    report_date_origin: str | None = None          # "declared" (never a file timestamp, never a transaction date)
    date_kind: str | None = None                   # current_state_snapshot / transaction_history / …
    latest_transaction_date: dt.date | None = None  # OBSERVED: newest transaction row (not a report date)
    earliest_transaction_date: dt.date | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    period_complete: bool | None = None
    error_cells: int | None = None                  # Excel error values (#REF!, #DIV/0!, …) in required fields
    missing_cached_formulas: int | None = None
    missing_required_values: int | None = None
    duplicate_records: int | None = None
    join_unmatched: int | None = None
    join_ambiguous: int | None = None
    joins_validated: bool | None = None
    totals_consistent: bool | None = None
    mapping_approved: bool | None = None
    blank_amount_cells: int | None = None           # empty amount cells (distinct from a recorded numeric zero)
    invalid_dates: int | None = None
    ambiguous_headers: int | None = None            # a required header that appears more than once
    external_references: int | None = None          # external workbook links + formula references
    date_contradictions: int | None = None          # declared dates contradicting observed dates / today
    selection_explicit: bool | None = None          # the file was named explicitly (not picked by modification time)
    inspection_failures: tuple[str, ...] = ()       # inspections that could not run (fail closed)
    notes: tuple[str, ...] = ()
    # M8-ACCURACY-2B: field-level and join-level evidence (None = not inspected → a scoped metric is NOT_VERIFIED)
    fields: dict | None = None                      # header → error_cells / missing_cached_formulas / blank_cells /
                                                    #          invalid_dates / external_refs / duplicate_header
    joins: dict | None = None                       # join id → unmatched / ambiguous / validated
    # M8-ACCURACY-2B review 2: where the source-level totals verdict comes from. `totals_consistent` (above) stays the
    # STRICT source-wide verdict = the fingerprint-bound declaration capped by the footer reconciliation. These three
    # let a SCOPED metric separate a column-footer problem from a genuinely source-wide one:
    declared_totals_consistent: bool | None = None  # the declaration alone (before any footer evidence)
    footer_totals: str | None = None                # whole-footer status PASS / OPEN / FAIL; None = no footer evidence
    footer_scope: str | None = None                 # "per_field": each column's footer result can stand on its own
                                                    # (rows recognized, ≥1 cell checked AND matched, no ambiguous
                                                    # row); "source_wide": it cannot; None: no footer rows

    def problems(self, scope: "Requirement | None" = None) -> list[tuple[State, str]]:
        """Source-intrinsic problems (not time). Unknown → NOT_VERIFIED; a known defect or failed inspection → INVALID.

        `scope` (M8-ACCURACY-2B): the fields and joins one metric actually uses. Field-level checks (Excel errors,
        missing caches, blank amounts, invalid dates, duplicated headers) and join checks are then judged on those
        fields / joins only; a dependency without evidence is NOT_VERIFIED. Source-integrity checks (availability,
        inspection failures, explicit selection, external links, layout, missing identity, duplicate records,
        totals, mapping) always apply. Without a scope every check applies (the strict source-wide judgement)."""
        if scope is not None:
            return self._scoped_problems(scope)
        out: list[tuple[State, str]] = []
        if self.available is False:
            return [(NV, "Kaynak rapor sağlanmadı.")]
        if self.available is None:
            out.append((NV, "Kaynağın okunduğu doğrulanmadı."))
        if self.inspection_failures:
            out.append((INV, "Kaynak denetimi tamamlanamadı."))
        if self.selection_explicit is not True:
            out.append((NV, "Kaynak dosya açıkça seçilmedi (değişiklik tarihine göre seçim kanıt sayılmaz)."))
        for value, bad_state, bad, unknown in (
                (self.external_references, INV, "Kaynak dış çalışma kitaplarına bağlı.", "Dış bağlantı denetimi yapılmadı."),
                (self.invalid_dates, INV, "Kaynakta geçersiz tarihler var.", "Tarih denetimi yapılmadı."),
                (self.date_contradictions, INV, "Beyan edilen tarihler kaynakla çelişiyor.",
                 "Tarih tutarlılığı denetlenmedi."),
                (self.ambiguous_headers, NV, "Kaynakta yinelenen başlıklar var.", "Başlık denetimi yapılmadı."),
                (self.blank_amount_cells, NV, "Tutar alanlarında boş hücreler var (sıfır sayılması onaylanmadı).",
                 "Boş tutar denetimi yapılmadı.")):
            if value is None:
                out.append((NV, unknown))
            elif value:
                out.append((bad_state, bad))
        if self.layout_supported is False:
            out.append((INV, "Kaynak dosyanın düzeni desteklenmiyor."))
        elif self.layout_supported is None:
            out.append((NV, "Kaynak dosyanın düzeni doğrulanmadı."))
        for value, bad, unknown in (
                (self.error_cells, "Kaynakta hatalı Excel formülleri var.", "Excel hata denetimi yapılmadı."),
                (self.missing_cached_formulas, "Kaynakta hesaplanmamış (önbelleksiz) formüller var.",
                 "Formül önbelleği denetimi yapılmadı."),
                (self.missing_required_values, "Gerekli alanlarda eksik değerler var.", "Eksik değer denetimi yapılmadı."),
                (self.duplicate_records, "Kaynakta yinelenen kayıtlar var.", "Yinelenen kayıt denetimi yapılmadı.")):
            if value is None:
                out.append((NV, unknown))
            elif value:
                out.append((INV, bad))
        if self.join_unmatched or self.join_ambiguous or self.joins_validated is False:
            out.append((INV, "Müşteri veya ürün eşleşmesi hatalı."))
        elif self.joins_validated is not True:
            out.append((NV, "Müşteri veya ürün eşleşmeleri doğrulanmadı."))
        if self.totals_consistent is False:
            out.append((INV, "Kaynaktaki toplamlar birbiriyle çelişiyor."))
        elif self.totals_consistent is None:
            out.append((NV, "Kaynak toplamlarının mutabakatı yapılmadı."))
        # a record whose totals verdict is more optimistic than its own footer reconciliation is corrupt: fail closed
        if (self.footer_totals == "FAIL" and self.totals_consistent is not False) or \
                (self.footer_totals == "OPEN" and self.totals_consistent is True) or \
                (self.footer_totals is not None and self.footer_totals not in self._FOOTER_STATES):
            out.append((INV, "Kaynak toplam kanıtı rapor toplam satırlarıyla tutarsız."))
        if self.mapping_approved is False:
            out.append((NV, "Kaynak eşlemesi onaylanmadı."))
        elif self.mapping_approved is None:
            out.append((NV, "Kaynak eşlemesinin onayı bilinmiyor."))
        return out

    _FIELD_LEVEL = {"Kaynakta geçersiz tarihler var.", "Tarih denetimi yapılmadı.", "Kaynakta yinelenen başlıklar var.",
                    "Başlık denetimi yapılmadı.", "Tutar alanlarında boş hücreler var (sıfır sayılması onaylanmadı).",
                    "Boş tutar denetimi yapılmadı.", "Kaynakta hatalı Excel formülleri var.",
                    "Excel hata denetimi yapılmadı.", "Kaynakta hesaplanmamış (önbelleksiz) formüller var.",
                    "Formül önbelleği denetimi yapılmadı.", "Müşteri veya ürün eşleşmesi hatalı.",
                    "Müşteri veya ürün eşleşmeleri doğrulanmadı."}

    _TOTALS = {"Kaynaktaki toplamlar birbiriyle çelişiyor.", "Kaynak toplamlarının mutabakatı yapılmadı."}
    _FOOTER_STATES = ("PASS", "OPEN", "FAIL")
    _FOOTER_FIELD_OK = ("PASS", "not_aggregated")       # not_aggregated: the footer does not total this column

    def _field_level_totals(self, scope: "Requirement") -> bool:
        """May this metric's totals be judged on its own fields' footers instead of the whole-footer verdict?
        Only when the per-column evidence can stand on its own, the record is internally consistent (the strict
        verdict is exactly the declaration capped by the footer status) and the metric reads at least one field.
        Anything else keeps the strict source-wide verdict (fail closed)."""
        if self.footer_scope != "per_field" or self.footer_totals not in self._FOOTER_STATES:
            return False
        expected = {"FAIL": False, "OPEN": None}.get(self.footer_totals, self.declared_totals_consistent)
        if self.totals_consistent is not expected:
            return False
        return bool(scope.fields) and self.fields is not None

    def _scoped_problems(self, scope: "Requirement") -> list[tuple[State, str]]:
        # every source-integrity check of the strict judgement, minus the field- and join-level ones …
        field_totals = self._field_level_totals(scope)
        out = [p for p in self.problems() if p[1] not in self._FIELD_LEVEL
               and not (field_totals and p[1] in self._TOTALS)]
        if self.available is False:
            return out
        if field_totals:
            # … the source-level totals verdict without the column footers: the declaration (source-wide) …
            if self.declared_totals_consistent is False:
                out.append((INV, "Kaynaktaki toplamlar birbiriyle çelişiyor."))
            elif self.declared_totals_consistent is not True:
                out.append((NV, "Kaynak toplamlarının mutabakatı yapılmadı."))
            # … plus every used field's own footer result (FAIL / OPEN are judged in the field loop below)
            for f in scope.fields:
                d = self.fields.get(f)
                if d is not None and d.get("footer_totals") not in self._FOOTER_FIELD_OK + ("FAIL", "OPEN"):
                    out.append((NV, f"Kullanılan alanın rapor toplamı kanıtı yok: {f}."))
        # … which are then judged on the fields this metric reads
        if self.fields is None:
            out.append((NV, "Alan düzeyinde kaynak kanıtı yok."))
        else:
            for f in scope.fields:
                d = self.fields.get(f)
                if d is None:
                    out.append((NV, f"Alan denetimi yapılmadı: {f}."))
                    continue
                if d.get("error_cells"):
                    out.append((INV, f"Kullanılan alanda hatalı Excel formülleri var: {f}."))
                if d.get("missing_cached_formulas"):
                    out.append((INV, f"Kullanılan alanda hesaplanmamış formüller var: {f}."))
                if d.get("invalid_dates"):
                    out.append((INV, f"Kullanılan alanda geçersiz tarihler var: {f}."))
                if d.get("duplicate_header"):
                    out.append((NV, f"Kullanılan alanın başlığı kaynakta birden fazla sütunda var: {f}."))
                if d.get("footer_totals") == "FAIL":
                    out.append((INV, f"Kullanılan alanın rapor toplamı satırlarla çelişiyor: {f}."))
                elif d.get("footer_totals") == "OPEN":
                    out.append((NV, f"Kullanılan alanın rapor toplamı doğrulanamadı (kısmi mutabakat): {f}."))
                if d.get("amount") and d.get("blank_cells"):     # as in the strict judgement: amount fields only
                    out.append((NV, f"Kullanılan tutar alanında boş hücreler var (sıfır sayılması onaylanmadı): {f}."))
        # … and on the joins it uses (a metric that uses none is not judged by any join)
        for j in scope.joins:
            d = (self.joins or {}).get(j)
            if d is None:
                out.append((NV, f"Eşleşme denetimi yapılmadı: {j}."))
            elif d.get("validated") is False or d.get("unmatched") or d.get("ambiguous"):
                out.append((INV, f"Kullanılan eşleşme sorunlu: {j}."))
            elif d.get("validated") is not True:
                out.append((NV, f"Kullanılan eşleşme doğrulanmadı: {j}."))
        return out


def temporal_problems(metric: Metric, ev: SourceEvidence, today: dt.date) -> list[tuple[State, str]]:
    """Time rules per metric kind. Age only ever withholds; a recent date never approves anything.
    - unknown report date → NOT_VERIFIED (both kinds); a future date → INVALID;
    - completeness must be established (None → NOT_VERIFIED, False = partial period → NOT_VERIFIED);
    - operational: older than max_source_age_days → NOT_VERIFIED (current operational freshness);
    - historical: no age limit (historical reporting validity), but the period must be dated and complete."""
    out: list[tuple[State, str]] = []
    if ev.report_date is None:
        out.append((NV, "Kaynak raporun tarihi bilinmiyor."))
    elif ev.report_date > today:
        out.append((INV, "Kaynak raporun tarihi gelecekte görünüyor."))
    elif metric.temporal == "operational" and (today - ev.report_date).days > (metric.max_source_age_days or 0):
        out.append((NV, "Kaynak rapor güncel değil (güncel durum analizi için)."))
    if ev.period_complete is False:
        out.append((NV, "Raporlama dönemi eksik (kısmi dönem)."))
    elif ev.period_complete is None:
        out.append((NV, "Raporlama döneminin tamlığı doğrulanmadı."))
    if metric.temporal == "historical":
        if ev.period_end is None:
            out.append((NV, "Tarihsel dönemin bitişi belirtilmedi."))
        elif ev.report_date is not None and ev.period_end > ev.report_date:
            out.append((INV, "Tarihsel dönem raporun tarihinden sonra bitiyor."))
    return out


@dataclass(frozen=True)
class EvidenceSnapshot:
    """Immutable evidence for ONE source generation. `generation_id` is a content hash of the exact frames the outputs
    were computed from (None when it could not be computed → the snapshot certifies nothing)."""
    generation_id: str | None
    evidence: Mapping[str, SourceEvidence]
    collected_on: dt.date | None = None
    unbound: frozenset[str] = frozenset()      # recorded sources whose served data is NOT the recorded revision

    def __post_init__(self):
        object.__setattr__(self, "evidence", types.MappingProxyType(dict(self.evidence)))
        object.__setattr__(self, "unbound", frozenset(self.unbound))


# ---------------------------------------------------------------------------------------------
# decisions
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    key: str
    title_tr: str
    state: State
    reasons_tr: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()

    @property
    def displayable(self) -> bool:
        return self.state == State.VERIFIED

    def notice(self) -> dict:
        """The neutral withheld notice (no numbers, no paths, no exception text)."""
        return {"error_code": WITHHELD_CODE, "state": "withheld", "metric": self.key, "title": self.title_tr,
                "verification": self.state.value, "message": WITHHELD_MESSAGE,
                "reasons": list(self.reasons_tr[:MAX_REASONS_SHOWN])}


def _dedupe(items: Iterable[str]) -> tuple[str, ...]:
    seen, out = set(), []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


class Gate:
    """Decides, per metric, whether it may be displayed. Fail-closed: unknown keys, missing or unbound evidence and any
    error while deciding all mean 'withheld'.

    `snapshot` is used only when `expected_generation` is given and equals `snapshot.generation_id`: the gate decides
    for one published generation and accepts evidence only from that generation."""

    def __init__(self, register: dict[str, Metric] | None = None, definitions: dict[str, Definition] | None = None,
                 snapshot: EvidenceSnapshot | None = None, today: dt.date | None = None,
                 expected_generation: str | None = None):
        self.register = REGISTER if register is None else register
        self.definitions = DEFINITIONS if definitions is None else definitions
        self.snapshot = snapshot
        self.expected_generation = expected_generation
        self.today = today or dt.date.today()
        self._memo: dict[str, Decision] = {}

    def bound_to(self, collected: EvidenceSnapshot) -> "Gate":
        """The gate for the generation `collected` was computed from (a dashboard build binds itself to the frames it
        renders). Evidence this gate already holds is kept only if it belongs to that same generation; evidence from
        any other generation is dropped, never carried over."""
        ev = dict(collected.evidence)
        if (self.snapshot is not None and collected.generation_id is not None
                and self.snapshot.generation_id == collected.generation_id):
            ev.update(self.snapshot.evidence)
        snap = EvidenceSnapshot(collected.generation_id, ev, collected.collected_on, collected.unbound)
        return type(self)(self.register, self.definitions, snap, self.today, collected.generation_id)

    def _evidence(self) -> tuple[Mapping[str, SourceEvidence], str | None]:
        if self.snapshot is None:
            return {}, "NO-EVIDENCE"
        if (self.expected_generation is None or self.snapshot.generation_id is None
                or self.snapshot.generation_id != self.expected_generation):
            return {}, "EVIDENCE-GENERATION"
        return self.snapshot.evidence, None

    def decide(self, key: str) -> Decision:
        if key in self._memo:
            return self._memo[key]
        try:
            d = self._decide(key, ())
        except Exception:      # deciding must never fail open
            d = Decision(key, key, State.INVALID, ("Doğrulama durumu belirlenemedi.",), ("GATE-ERROR",))
        self._memo[key] = d
        return d

    def displayable(self, key: str) -> bool:
        return self.decide(key).displayable

    def _decide(self, key: str, stack: tuple[str, ...]) -> Decision:
        m = self.register.get(key)
        if m is None:
            return Decision(key, key, State.INVALID, ("Bu çıktı doğrulama kaydında tanımlı değil.",), ("UNREGISTERED",))
        if key in stack:
            return Decision(key, m.title_tr, State.INVALID, ("Doğrulama bağımlılığı döngüsel.",), ("CYCLE",))
        states, reasons, codes = [m.certified], [], []
        for b in m.blockers:
            states.append(b.state)
            reasons.append(b.reason_tr)
            codes.append(b.code)
        for dk in m.definitions:
            d = self.definitions.get(dk)
            if d is None or not d.approved:
                states.append(NV)
                reasons.append(f"Tanım onaylanmadı: {d.title_tr if d else dk}.")
                codes.append(f"DEF:{dk}")
        evidence, unbound = self._evidence()
        stale = self.snapshot.unbound if (self.snapshot is not None and unbound is None) else frozenset()
        for src in m.sources:
            ev = evidence.get(src)
            if ev is None:
                code = unbound or ("EVIDENCE-GENERATION" if src in stale else "NO-EVIDENCE")
                states.append(NV)
                reasons.append("Kanıt başka bir veri üretimine ait." if code == "EVIDENCE-GENERATION"
                               else "Kaynak doğrulama kanıtı yok.")
                codes.append(f"{code}:{src}")
                continue
            for st, why in ev.problems(m.scope(src) if m.requires else None) + temporal_problems(m, ev, self.today):
                states.append(st)
                reasons.append(why)
                codes.append(f"SRC:{src}")
        for dep in m.depends_on:
            dd = self._decide(dep, stack + (key,))
            if not dd.displayable:
                states.append(dd.state)
                reasons.append(f"Dayandığı analiz doğrulanmadı: {dd.title_tr}.")
                codes.append(f"DEP:{dep}")
        state = worst(*states)
        if m.certified != State.VERIFIED and state == State.VERIFIED:      # never promote past the certification
            state = m.certified
        if state != State.VERIFIED and not reasons:
            reasons.append("Doğrulama kriterleri karşılanmadı.")
        return Decision(key, m.title_tr, state, _dedupe(reasons), _dedupe(codes))


# The production gate. Tests that exercise preserved algorithms replace this factory from tests/ only.
# M8-ACCURACY-2: the API passes the published generation's snapshot, bound to the fingerprints it is serving
# (app.accuracy_gate). No metric can be VERIFIED in production regardless, because validate_register refuses any
# VERIFIED certification while GENERATION_BOUND_API_EVIDENCE is False (owner real-data acceptance pending).
def current_gate(today: dt.date | None = None, snapshot: EvidenceSnapshot | None = None,
                 expected_generation: str | None = None) -> Gate:
    return Gate(REGISTER, DEFINITIONS, snapshot, today, expected_generation)


# ---------------------------------------------------------------------------------------------
# evidence collectors for the core sources (counts only). They never raise and never report a clean result for
# something they could not inspect: an inspection error is recorded as a failure (INVALID); a check they cannot make
# stays None (NOT_VERIFIED).
# ---------------------------------------------------------------------------------------------
_ERR_RE = re.compile(r"^#(REF!|DIV/0!|N/A|VALUE!|NAME\?|NUM!|NULL!)$")


def _excel_errors(series) -> int | None:
    """Number of Excel error strings in `series`; None when the inspection itself failed (never 0 on failure)."""
    # Note (found in review): the earlier version applied `.str` to numeric-only columns, raised, and returned 0, so
    # every numeric column looked "inspected and clean". A column without text simply has no error strings.
    try:
        s = series.dropna()
        texts = [v.strip() for v in s.tolist() if isinstance(v, str)]
        return sum(1 for v in texts if _ERR_RE.match(v))
    except Exception:
        return None


def _sum_counts(values, failures: list, label: str) -> int | None:
    total = 0
    for v in values:
        if v is None:
            failures.append(label)
            return None
        total += v
    return total


def _stock_evidence(df, declared) -> SourceEvidence:
    req = ["Stok_Kodu", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye", "Envanter değeri"]
    have = [c for c in req if c in df.columns]
    fails: list[str] = []
    errs = _sum_counts([_excel_errors(df[c]) for c in have], fails, "excel_errors")
    miss = int(df["STOK Kontrol %"].isna().sum()) if "STOK Kontrol %" in df.columns else None
    # M8-ACCURACY-2B: duplicates among REAL product codes only — astype(str) turned every blank code into "nan", so
    # blank rows (e.g. the export's report-total rows) were counted as duplicates of each other
    if "Stok_Kodu" in df.columns:
        codes = df["Stok_Kodu"].dropna().astype(str).str.strip()
        dup = int(codes[codes != ""].duplicated().sum())
    else:
        dup = None
    return SourceEvidence("stock", available=True, layout_supported=len(have) == len(req), error_cells=errs,
                          missing_required_values=miss, duplicate_records=dup, report_date=declared.get("stock"),
                          report_date_origin="declared" if declared.get("stock") else None,
                          inspection_failures=tuple(fails))


def _sales_evidence(df, declared) -> SourceEvidence:
    import pandas as pd
    req = ["sip_evrakno_seri", "sip_evrakno_sira", "Musterikod", "Pro_kodu", "sto_kod", "Tarih", "miktar", "Nettutar"]
    have = [c for c in req if c in df.columns]
    fails: list[str] = []
    errs = _sum_counts([_excel_errors(df[c]) for c in have], fails, "excel_errors")
    amt = pd.to_numeric(df["Nettutar"], errors="coerce") if "Nettutar" in df.columns else None
    dates = pd.to_datetime(df["Tarih"], errors="coerce") if "Tarih" in df.columns else None
    miss = (int(amt.isna().sum()) + int(dates.isna().sum())) if (amt is not None and dates is not None) else None
    dup = int(df.duplicated().sum())
    asof = dates.max() if dates is not None else None
    latest = asof.date() if asof is not None and pd.notna(asof) else None
    rd = declared.get("sales")
    # The newest transaction date is an OBSERVED fact, never the report date (M8-ACCURACY-2, Task 5).
    return SourceEvidence("sales", available=True, layout_supported=len(have) == len(req), error_cells=errs,
                          missing_required_values=miss, duplicate_records=dup, report_date=rd,
                          report_date_origin="declared" if rd else None, latest_transaction_date=latest,
                          inspection_failures=tuple(fails))


_AY = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _aging_evidence(df, declared) -> SourceEvidence:
    cols = [str(c).strip() for c in df.columns]
    buckets = [c for c in df.columns if "öncesi" in str(c) or any(m in str(c) for m in _AY)]
    fails: list[str] = []
    errs = _sum_counts([_excel_errors(df[c]) for c in buckets], fails, "excel_errors") if buckets else None
    key = next((c for c in df.columns if str(c).strip() == "Hesap kodu"), None)
    dup = int(df[key].astype(str).duplicated().sum()) if key is not None else None
    return SourceEvidence("aging", available=True, layout_supported="Hesap kodu" in cols and bool(buckets),
                          error_cells=errs, duplicate_records=dup, report_date=declared.get("aging"),
                          report_date_origin="declared" if declared.get("aging") else None,
                          inspection_failures=tuple(fails))


_COLLECTORS = {"stock": _stock_evidence, "sales": _sales_evidence, "aging": _aging_evidence}


def collect_core_evidence(stock_df=None, sales_df=None, aging_df=None, today: dt.date | None = None,
                          declared_dates: dict[str, dt.date] | None = None) -> dict[str, SourceEvidence]:
    """Evidence for stock / sales / Cari aging from the RAW frames as read (before any coercion). The stock and
    aging exports carry no report date the code can read, so it stays unknown unless explicitly declared. Joins,
    control totals, period completeness and mappings cannot be established here and stay unknown."""
    declared = declared_dates or {}
    out: dict[str, SourceEvidence] = {}
    for key, df in (("stock", stock_df), ("sales", sales_df), ("aging", aging_df)):
        if df is None:
            continue
        try:
            out[key] = _COLLECTORS[key](df, declared)
        except Exception as e:      # fail closed: the source is not shown as clean
            out[key] = SourceEvidence(key, inspection_failures=(f"collector:{type(e).__name__}",))
    return out


# ---------------------------------------------------------------------------------------------
# shared response-layer redaction helpers (used by app.py endpoints; pure functions on response dicts)
# ---------------------------------------------------------------------------------------------
DIRECTORY_MASTER_WARNINGS = ("master_duplicate", "master_unmatched", "master_unavailable")


def redact_directory(row: dict, gate: Gate) -> dict:
    """Customer directory rows (search hits, CRM follow-ups / customer, fair members): the code and the recorded
    display name stay; the derived last-order date and the open / KAPALI status interpretation are withheld unless
    verified. Operational safeguards that depend on the master status (refusing a KAPALI sales follow-up or invite,
    `allowed_followup_purposes`, `suppressed_kapali`) keep working server-side; they restrict an action and show no
    analysis."""
    out = dict(row)
    if "last_order_date" in out and not gate.displayable("customer.last_order_date"):
        out["last_order_date"] = None
    if not gate.displayable("customer.master_status"):
        if "closed" in out:
            out["closed"] = None
        if "master_status" in out:
            out["master_status"] = "withheld"
        if "warnings" in out:
            out["warnings"] = [w for w in (out.get("warnings") or []) if w.get("code") not in DIRECTORY_MASTER_WARNINGS]
    return out


def directory_notices(gate: Gate) -> list[dict]:
    return [gate.decide(k).notice() for k in ("customer.last_order_date", "customer.master_status")
            if not gate.displayable(k)]


def redact_crm_summary(body: dict, gate: Gate) -> dict:
    out = dict(body)
    if not gate.displayable("crm.active_customers"):
        out["active_customers"] = None
        out["active_customers_with_structured_contact"] = None
        out["withheld"] = [gate.decide("crm.active_customers").notice()]
    return out


def redact_fair_campaign(summary: dict, gate: Gate) -> dict:
    out = dict(summary)
    le = dict(out.get("linked_event") or {})
    if not gate.displayable("fairs.history") and le.get("configured"):
        n = gate.decide("fairs.history").notice()
        out["linked_event"] = {"configured": True, "found": None, "event_id": le.get("event_id"),
                               "withheld": True, "message": n["message"], "reasons": n["reasons"]}
    return out


def redact_fair_member(row: dict, gate: Gate) -> dict:
    out = redact_directory(row, gate)
    if not gate.displayable("fairs.segments"):
        out["segments"] = []
        out["collection_first"] = None
    if not gate.displayable("fairs.history"):
        out["erp_fuar_orders"] = None
        out["erp_evidence"] = None
        out["erp_mismatch"] = None
    return out


def redact_owner_section(section: dict, key: str, gate: Gate) -> dict:
    out = dict(section)
    if not gate.displayable(key):
        out["data"] = None
        out["withheld"] = gate.decide(key).notice()
    return out


OPERATIONS_ANALYTIC_FIELDS = ("daily_sales_customers", "collection_first_customers", "fair_tasks_due",
                              "crm_followups_in_queue", "cross_sell_customers", "cross_sell_active_customers",
                              "cross_sell_share", "cross_sell_note")


def redact_operations(ops: dict, gate: Gate) -> dict:
    out = dict(ops)
    if not gate.displayable("owner.operations"):
        for k in OPERATIONS_ANALYTIC_FIELDS:
            if k in out:
                out[k] = None
        out["withheld"] = gate.decide("owner.operations").notice()
    return out


def redact_known_issues(issues: list[dict], gate: Gate) -> list[dict]:
    out = []
    for i in issues:
        i = dict(i)
        if i.get("code") == "cross_sell_review" and not gate.displayable("opportunities.cross_sell"):
            i["count"], i["count_label"] = None, None
        out.append(i)
    return out


def redact_refresh_report(rep: dict, gate: Gate) -> dict:
    out = dict(rep)
    counts = dict(out.get("counts") or {})
    for k in list(counts):
        mk = REFRESH_COUNT_METRIC.get(k, "unregistered")
        if mk is None:
            continue
        if mk == "unregistered" or not gate.displayable(mk):
            counts[k] = None
    out["counts"] = counts
    return out


def accuracy_known_issues() -> list[dict]:
    """Known-issue entries added by M8-ACCURACY for the Yönetim panel (fixed text; no counts, no paths)."""
    return [
        {"code": "m8_accuracy_withheld", "title": "Doğrulanmamış analizler gösterilmiyor",
         "detail": "Sahip yönergesi gereği yalnızca kaynağı, tarihi, tanımı ve hesabı doğrulanmış sonuçlar gösterilir. "
                   "Diğer analizlerin yerinde neyin eksik olduğunu söyleyen bir not vardır; hesaplama kodu korunur.",
         "status": "open", "count": None, "count_label": None},
        {"code": "dq6_winback_overdue_identity", "title": "DQ-6 · Geri Kazanım: borç eşlemesinde kimlik karışıklığı",
         "detail": "Eski Geri Kazanım listesi fatura hesabı borcunu sipariş müşterisi koduyla arıyor; başka bir fatura "
                   "hesabından faturalanan müşteride borç sıfır görünebilir. Liste gösterilmiyor; düzeltme sahip onayı "
                   "bekliyor.", "status": "open", "count": None, "count_label": None},
    ]


validate_register()

__all__ = ["State", "Definition", "Blocker", "Metric", "SourceEvidence", "EvidenceSnapshot", "Decision", "Gate",
           "current_gate", "collect_core_evidence", "temporal_problems",
           "redact_directory", "GENERATION_BOUND_API_EVIDENCE", "validate_register", "Requirement", "RegisterError", "REGISTER", "DEFINITIONS", "ROUTES",
           "STAT_METRIC", "MODULE_METRIC", "accuracy_known_issues", "PANEL_METRIC", "REFRESH_COUNT_METRIC", "WITHHELD_MESSAGE",
           "WITHHELD_CODE", "WITHHELD_HTTP_STATUS", "POLICY_VERSION", "worst"]
