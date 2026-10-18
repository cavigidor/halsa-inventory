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

Nothing here computes a business number. The algorithms stay where they are, unchanged, so they can be verified
later. There is no environment variable, flag or HTTP parameter that turns the gate off. The only way to show a
metric is a reviewed code change that certifies it with its evidence.
"""
from __future__ import annotations

import datetime as dt
import enum
import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------------------------------------
# states and user-facing text
# ---------------------------------------------------------------------------------------------
WITHHELD_MESSAGE = "Bu analiz henüz doğrulanmadığı için gösterilmiyor."
WITHHELD_CODE = "accuracy_withheld"
WITHHELD_HTTP_STATUS = 409          # the resource exists; its verification state conflicts with displaying it
MAX_REASONS_SHOWN = 4               # keep the father-facing notice short
POLICY_VERSION = "m8-accuracy-1"    # embedded in every gated dashboard build (served `/` checks it)


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
class Metric:
    key: str
    title_tr: str
    surfaces: tuple[str, ...]
    sources: tuple[str, ...] = ()
    definitions: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    certified: State = State.NOT_VERIFIED
    approval_refs: tuple[str, ...] = ()        # required for VERIFIED
    regression_tests: tuple[str, ...] = ()     # required for VERIFIED
    reconciliation_ref: str | None = None      # required for VERIFIED (real-data reconciliation evidence)


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
                "docs/m6_source_inventory.md §12 (1,178 of 3,314 products on the 5 Oct 2026 export).")
B_STOCK_DATE = Blocker("STOCK-DATE", NV, "Stok raporunun tarihi doğrulanmıyor.",
                       "dashboard_builder.select_sources picks the newest file by mtime; no report date is read.")
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

# ---------------------------------------------------------------------------------------------
# the metric register (certified states). Keys are stable; docs/accuracy/metric_register.md mirrors them.
# ---------------------------------------------------------------------------------------------
_M = Metric
REGISTER: dict[str, Metric] = {m.key: m for m in (
    # ---- static dashboard modules (dashboard_builder.build) ----
    _M("stock.reorder", "Azalan Stok", ("dashboard:reorder", "dashboard:stat:min_alti", "api:/api/context"),
       ("stock",), ("stock_coverage_rule",), blockers=(B_DQ4, B_STOCK_DATE, B_REAL)),
    _M("stock.overstock", "Fazla Stok", ("dashboard:overstock", "dashboard:stat:maks_ustu",
                                         "dashboard:stat:fazla_stok_degeri"),
       ("stock",), ("stock_coverage_rule", "inventory_valuation"), blockers=(B_DQ4, B_STOCK_DATE, B_INV_VALUE, B_REAL)),
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
       blockers=(B_REVENUE, B_AMOUNT_ZERO, B_DQ5, B_REAL)),
    _M("finance.margin", "Kâr Marjı", ("dashboard:margin",), ("sales",), ("revenue_basis", "margin_cost_basis"),
       blockers=(B_DQ1, B_REVENUE, B_REAL), certified=INV),
    _M("receivables.risk", "Riskli Müşteri", ("dashboard:risk",), ("sales", "aging"),
       ("overdue_definition", "revenue_basis", "salesperson_of_record"),
       blockers=(B_AGING_DATE, B_OVERDUE_DEF, B_REVENUE, B_SALESPERSON, B_REAL)),
    _M("receivables.collections", "Tahsilat (vadesi geçen alacak)", ("dashboard:collections",
                                                                      "dashboard:stat:vadesi_gecen_toplam"),
       ("aging",), ("overdue_definition",), blockers=(B_AGING_DATE, B_OVERDUE_DEF, B_REAL)),
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
       ("sales",), ("fair_taxonomy", "revenue_basis"), blockers=(B_FAIR, B_REVENUE, B_REAL)),
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
       certified=INV),
    _M("owner.butce", "Bütçe", ("api:owner.overview.butce",), ("butce",), ("butce_file_of_record",),
       blockers=(B_BUTCE,), certified=INV),
    _M("owner.operations", "Yönetim operasyon sayıları", ("api:owner.overview.operations",),
       depends_on=("sales_queue", "opportunities.cross_sell")),
    _M("scenario.receivables", "Senaryo hesabı", ("api:/api/scenario",), definitions=("inflation_source",),
       depends_on=("receivables.collections",), blockers=(B_SCENARIO,)),
    _M("collections.detailed", "Detaylı tahsilat (evrak bazlı)", (), ("detailed_collections",),
       blockers=(B_SOURCE_MISSING,)),
    _M("trodat.sales", "Trodat satış raporu", (), ("trodat",), ("trodat_sku_mapping",), blockers=(B_TRODAT,)),
)}

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
                        "actions": "actions.queue", "products": None}

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
    ("GET", "/api/customer360/search"): ("reference", None),
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
    ("GET", "/api/fairs/campaigns/{campaign_id}/customers"): ("redacted", ("fairs.history", "fairs.segments")),
    ("POST", "/api/fairs/campaigns/{campaign_id}/customers"): ("operational", None),
    ("PUT", "/api/fairs/campaigns/{campaign_id}/customers"): ("redacted", ("fairs.history", "fairs.segments")),
    ("GET", "/api/crm/summary"): ("redacted", ("crm.active_customers",)),
    ("GET", "/api/crm/followups"): ("operational", None),
    ("GET", "/api/crm/recent"): ("operational", None),
    ("GET", "/api/crm/customers/{customer_code}"): ("operational", None),
    ("POST", "/api/crm/customers/{customer_code}/interactions"): ("operational", None),
    ("PUT", "/api/crm/customers/{customer_code}/followup"): ("operational", None),
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
                      definitions: dict[str, Definition] | None = None) -> None:
    reg = REGISTER if register is None else register
    defs = DEFINITIONS if definitions is None else definitions
    for d in defs.values():
        if d.approved and not (d.decision_ref and _DECISION_RE.match(d.decision_ref)):
            raise RegisterError(f"definition {d.key}: approved without a D-xxx decision reference")
    for k, m in reg.items():
        if k != m.key:
            raise RegisterError(f"register key {k} != metric key {m.key}")
        for dk in m.definitions:
            if dk not in defs:
                raise RegisterError(f"{k}: unknown definition {dk}")
        for dep in m.depends_on:
            if dep not in reg:
                raise RegisterError(f"{k}: unknown dependency {dep}")
        for b in m.blockers:
            if b.state == State.VERIFIED:
                raise RegisterError(f"{k}: a blocker cannot be VERIFIED")
            if not b.reason_tr.strip():
                raise RegisterError(f"{k}: blocker {b.code} without a reason")
        if m.certified == State.VERIFIED:
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
    if register is None:        # the production register must cover every metric-bound route
        for (_, _), (kind, ref) in ROUTES.items():
            if kind == "metric" and ref not in reg:
                raise RegisterError(f"route references unknown metric {ref}")


# ---------------------------------------------------------------------------------------------
# runtime source evidence (can only downgrade)
# ---------------------------------------------------------------------------------------------
@dataclass
class SourceEvidence:
    """Facts about one source as it was actually read. Counts only, never values, names or paths."""
    source: str
    available: bool = True
    layout_supported: bool = True
    report_date: dt.date | None = None
    report_date_origin: str | None = None          # "declared", "max_transaction_date", …
    max_age_days: int | None = 45
    error_cells: int = 0                            # Excel error values (#REF!, #DIV/0!, …) in required fields
    missing_cached_formulas: int = 0
    missing_required_values: int = 0
    duplicate_records: int = 0
    join_unmatched: int = 0
    join_ambiguous: int = 0
    # Unknown (None) is never treated as passing: each must be established explicitly by a collector or a recorded
    # reconciliation step.
    joins_validated: bool | None = None
    period_complete: bool | None = None
    totals_consistent: bool | None = None
    mapping_approved: bool | None = None
    notes: tuple[str, ...] = ()

    def problems(self, today: dt.date) -> list[tuple[State, str]]:
        out: list[tuple[State, str]] = []
        if not self.available:
            return [(NV, "Kaynak rapor sağlanmadı.")]
        if not self.layout_supported:
            out.append((INV, "Kaynak dosyanın düzeni desteklenmiyor."))
        if self.error_cells:
            out.append((INV, "Kaynakta hatalı Excel formülleri var."))
        if self.missing_cached_formulas:
            out.append((INV, "Kaynakta hesaplanmamış (önbelleksiz) formüller var."))
        if self.missing_required_values:
            out.append((INV, "Gerekli alanlarda eksik değerler var."))
        if self.duplicate_records:
            out.append((INV, "Kaynakta yinelenen kayıtlar var."))
        if self.join_unmatched or self.join_ambiguous or self.joins_validated is False:
            out.append((INV, "Müşteri veya ürün eşleşmesi hatalı."))
        elif self.joins_validated is None:
            out.append((NV, "Müşteri veya ürün eşleşmeleri doğrulanmadı."))
        if self.totals_consistent is False:
            out.append((INV, "Kaynaktaki toplamlar birbiriyle çelişiyor."))
        if self.report_date is None:
            out.append((NV, "Kaynak raporun tarihi bilinmiyor."))
        elif self.max_age_days is not None and (today - self.report_date).days > self.max_age_days:
            out.append((NV, "Kaynak rapor güncel değil."))
        elif self.report_date > today:
            out.append((INV, "Kaynak raporun tarihi gelecekte görünüyor."))
        if self.period_complete is False:
            out.append((NV, "Raporlama dönemi eksik (kısmi dönem)."))
        elif self.period_complete is None:
            out.append((NV, "Raporlama döneminin tamlığı doğrulanmadı."))
        if self.mapping_approved is False:
            out.append((NV, "Kaynak eşlemesi onaylanmadı."))
        elif self.mapping_approved is None:
            out.append((NV, "Kaynak eşlemesinin onayı bilinmiyor."))
        if self.totals_consistent is None:
            out.append((NV, "Kaynak toplamlarının mutabakatı yapılmadı."))
        return out


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
    """Decides, per metric, whether it may be displayed. Fail-closed: unknown keys, missing evidence and any error
    while deciding all mean 'withheld'."""

    def __init__(self, register: dict[str, Metric] | None = None, definitions: dict[str, Definition] | None = None,
                 evidence: dict[str, SourceEvidence] | None = None, today: dt.date | None = None):
        self.register = REGISTER if register is None else register
        self.definitions = DEFINITIONS if definitions is None else definitions
        self.evidence = dict(evidence or {})
        self.today = today or dt.date.today()
        self._memo: dict[str, Decision] = {}

    def with_evidence(self, evidence: dict[str, SourceEvidence] | None) -> "Gate":
        """A gate that also knows `evidence` (collected from the frames actually read). Evidence this gate already
        holds for a source is kept, so an explicitly declared observation is never replaced."""
        merged = {**(evidence or {}), **self.evidence}
        return type(self)(self.register, self.definitions, merged, self.today)

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
        for src in m.sources:
            ev = self.evidence.get(src)
            if ev is None:
                states.append(NV)
                reasons.append("Kaynak doğrulama kanıtı yok.")
                codes.append(f"NO-EVIDENCE:{src}")
                continue
            for st, why in ev.problems(self.today):
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
def current_gate(today: dt.date | None = None, evidence: dict[str, SourceEvidence] | None = None) -> Gate:
    return Gate(REGISTER, DEFINITIONS, evidence, today)


# ---------------------------------------------------------------------------------------------
# evidence collectors for the core sources (counts only; never raise)
# ---------------------------------------------------------------------------------------------
_ERR_RE = re.compile(r"^#(REF!|DIV/0!|N/A|VALUE!|NAME\?|NUM!|NULL!)$")


def _excel_errors(series) -> int:
    try:
        s = series.dropna()
        s = s[s.map(lambda v: isinstance(v, str))]
        return int(s.str.strip().str.match(_ERR_RE).sum())
    except Exception:
        return 0


def collect_core_evidence(stock_df=None, sales_df=None, aging_df=None, today: dt.date | None = None,
                          declared_dates: dict[str, dt.date] | None = None) -> dict[str, SourceEvidence]:
    """Evidence for stock / sales / Cari aging from the RAW frames as read (before any coercion). The stock and
    aging exports carry no report date the code can read, so it stays unknown unless explicitly declared."""
    declared = declared_dates or {}
    out: dict[str, SourceEvidence] = {}
    try:
        if stock_df is not None:
            req = ["Stok_Kodu", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye", "Envanter değeri"]
            have = [c for c in req if c in stock_df.columns]
            errs = sum(_excel_errors(stock_df[c]) for c in have)
            cov = stock_df["STOK Kontrol %"] if "STOK Kontrol %" in stock_df.columns else None
            miss = int(cov.isna().sum()) if cov is not None else 0
            dup = int(stock_df["Stok_Kodu"].astype(str).duplicated().sum()) if "Stok_Kodu" in stock_df.columns else 0
            out["stock"] = SourceEvidence("stock", layout_supported=len(have) == len(req), error_cells=errs,
                                          missing_required_values=miss, duplicate_records=dup,
                                          joins_validated=True, period_complete=True, mapping_approved=True,
                                          report_date=declared.get("stock"),
                                          report_date_origin="declared" if declared.get("stock") else None)
    except Exception:
        out["stock"] = SourceEvidence("stock", layout_supported=False)
    try:
        if sales_df is not None:
            import pandas as pd
            req = ["sip_evrakno_seri", "sip_evrakno_sira", "Musterikod", "Pro_kodu", "sto_kod", "Tarih", "miktar",
                   "Nettutar"]
            have = [c for c in req if c in sales_df.columns]
            errs = sum(_excel_errors(sales_df[c]) for c in have)
            amt = pd.to_numeric(sales_df["Nettutar"], errors="coerce") if "Nettutar" in sales_df.columns else None
            dates = pd.to_datetime(sales_df["Tarih"], errors="coerce") if "Tarih" in sales_df.columns else None
            miss = (int(amt.isna().sum()) if amt is not None else 0) + (int(dates.isna().sum()) if dates is not None else 0)
            dup = int(sales_df.duplicated().sum())
            asof = dates.max() if dates is not None else None
            rd = declared.get("sales") or (asof.date() if asof is not None and pd.notna(asof) else None)
            out["sales"] = SourceEvidence("sales", layout_supported=len(have) == len(req), error_cells=errs,
                                          missing_required_values=miss, duplicate_records=dup, report_date=rd,
                                          mapping_approved=True,
                                          report_date_origin="declared" if declared.get("sales") else
                                          ("max_transaction_date" if rd else None))
    except Exception:
        out["sales"] = SourceEvidence("sales", layout_supported=False)
    try:
        if aging_df is not None:
            cols = [str(c).strip() for c in aging_df.columns]
            ok = "Hesap kodu" in cols
            buckets = [c for c in aging_df.columns if "öncesi" in str(c) or any(
                m in str(c) for m in ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
                                      "Eylül", "Ekim", "Kasım", "Aralık"))]
            errs = sum(_excel_errors(aging_df[c]) for c in buckets)
            key = next((c for c in aging_df.columns if str(c).strip() == "Hesap kodu"), None)
            dup = int(aging_df[key].astype(str).duplicated().sum()) if key is not None else 0
            out["aging"] = SourceEvidence("aging", layout_supported=ok and bool(buckets), error_cells=errs,
                                          duplicate_records=dup, joins_validated=True, period_complete=True,
                                          mapping_approved=True, report_date=declared.get("aging"),
                                          report_date_origin="declared" if declared.get("aging") else None)
    except Exception:
        out["aging"] = SourceEvidence("aging", layout_supported=False)
    return out


# ---------------------------------------------------------------------------------------------
# shared response-layer redaction helpers (used by app.py endpoints; pure functions on response dicts)
# ---------------------------------------------------------------------------------------------
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
    out = dict(row)
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

__all__ = ["State", "Definition", "Blocker", "Metric", "SourceEvidence", "Decision", "Gate", "current_gate",
           "collect_core_evidence", "validate_register", "RegisterError", "REGISTER", "DEFINITIONS", "ROUTES",
           "STAT_METRIC", "MODULE_METRIC", "accuracy_known_issues", "PANEL_METRIC", "REFRESH_COUNT_METRIC", "WITHHELD_MESSAGE",
           "WITHHELD_CODE", "WITHHELD_HTTP_STATUS", "POLICY_VERSION", "worst"]
