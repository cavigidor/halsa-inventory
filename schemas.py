"""API sözleşmesi ve AI çıktı şemaları (Pydantic v2)."""
import datetime as dt
from typing import Optional, List, Any, Dict, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

Priority = Literal["critical", "high", "medium", "low"]
Confidence = Literal["high", "medium", "low"]

# ---------- generic ----------
class Health(BaseModel):
    status: str = "ok"
    ai_available: bool
    ai_provider: str
    ai_model: Optional[str] = None
    ai_reason: Optional[str] = None
    macro_available: bool
    data_folder: str
    context_ready: bool = False          # M5-1: warm-up finished (deterministic context built)
    context_error: Optional[str] = None  # set when the warm-up failed (e.g. data files missing)
    as_of_data_date: Optional[str] = None  # M5-2: data date of the ready context (dashboard mismatch check)


class CategoryOut(BaseModel):
    """Server-described action category (D-022): the UI builds filters/badges from this list."""
    key: str
    label_tr: str
    color: str
    entity_type: str
    source: str
    draft_purpose: Optional[str] = None   # M5-4: default draft purpose for customer actions of this category


class DisplayFact(BaseModel):
    """A Python-formatted fact for display (label + Turkish display value). Never AI text."""
    label: str
    display_value: str


class EvidenceFact(BaseModel):
    """A Python-computed fact the AI text relied on. display_value comes from Python only."""
    fact_id: str
    label: str
    display_value: str

class Unavailable(BaseModel):
    available: bool = False
    reason: str

# ---------- context ----------
class ContextResponse(BaseModel):
    available: bool = True
    generated_at: str
    as_of_data_date: str
    source_files: Dict[str, Optional[str]]
    company: Dict[str, Any]
    counts: Dict[str, int]
    data: Dict[str, Any]  # full agent_context

# ---------- actions ----------
class ActionOut(BaseModel):
    action_id: str
    source: str = "legacy"               # which deterministic action source produced it (D-022)
    category: str
    entity_type: str
    entity_id: str
    title: str
    reason: str = ""                     # deterministic Turkish sentence: why the action exists (D-015)
    facts: List[str]
    drivers: Dict[str, Any] = {}
    score: float
    priority: Priority
    confidence: Confidence
    requires_approval: bool = True
    status: str = "open"
    interpretation: Optional[str] = None
    recommendation: Optional[str] = None
    evidence: List[EvidenceFact] = []
    ai_cached: bool = False              # AI text served from the cache (no provider call)
    ai_generated_at: Optional[str] = None

class ActionsResponse(BaseModel):
    ai_available: bool
    ai_reason: Optional[str] = None
    ai_error_category: Optional[str] = None
    ai_warnings: List[str] = []
    generated_at: str
    count: int
    actions: List[ActionOut]

class EnrichRequest(BaseModel):
    """Explicit AI request for at most 5 open actions (D-017)."""
    action_ids: List[str] = Field(min_length=1, max_length=5)


class EnrichResponse(BaseModel):
    ai_available: bool
    ai_reason: Optional[str] = None
    ai_error_category: Optional[str] = None
    ai_warnings: List[str] = []
    provider_called: bool = False        # False when everything came from the cache or AI is off
    enriched: List[str] = []             # action_ids that got fresh AI text in this request
    cached: List[str] = []               # action_ids answered from the cache
    failed: List[str] = []               # action_ids without AI text after this request
    actions: List[ActionOut] = []        # the requested actions, in Python score order

# ---------- customer / product analysis ----------
class CustomerAnalysis(BaseModel):
    available: bool = True
    customer_code: str
    facts: Dict[str, Any]
    display_facts: List[DisplayFact] = []     # M5-4 drawer: Python-formatted facts (GET, no AI)
    ai_available: bool
    summary: Optional[str] = None
    risks: List[str] = []
    opportunities: List[str] = []
    recommended_action: Optional[str] = None
    confidence: Optional[Confidence] = None
    reason: Optional[str] = None
    ai_error_category: Optional[str] = None
    evidence: List[EvidenceFact] = []
    ai_warnings: List[str] = []

class ProductAnalysis(BaseModel):
    available: bool = True
    product_code: str
    facts: Dict[str, Any]
    ai_available: bool
    status: Optional[str] = None
    risk: Optional[str] = None
    opportunity: Optional[str] = None
    best_buyers: List[str] = []
    recommendation: Optional[str] = None
    confidence: Optional[Confidence] = None
    reason: Optional[str] = None
    ai_error_category: Optional[str] = None
    evidence: List[EvidenceFact] = []
    ai_warnings: List[str] = []

# ---------- draft message ----------
Channel = Literal["whatsapp", "email"]
Purpose = Literal["collection", "offer", "winback", "sales_followup", "fair_invite"]
LegacyMessageType = Literal["whatsapp", "email", "collection", "sales_offer", "winback"]


class DraftRequest(BaseModel):
    """Canonical (M5-4, D-030): channel + purpose. `message_type` is the legacy mixed field, accepted only
    for old callers and mapped once in services/drafts.LEGACY_MESSAGE_TYPES; it cannot be combined."""
    model_config = ConfigDict(extra="forbid")
    customer_code: str
    channel: Optional[Channel] = None
    purpose: Optional[Purpose] = None
    message_type: Optional[LegacyMessageType] = None     # deprecated
    product_code: Optional[str] = None
    additional_instruction: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _channel_purpose(self):
        from services.drafts import LEGACY_MESSAGE_TYPES
        if self.message_type is not None:
            if self.channel is not None or self.purpose is not None:
                raise ValueError("send either channel + purpose, or the legacy message_type, not both")
            self.channel, self.purpose = LEGACY_MESSAGE_TYPES[self.message_type]
        elif self.channel is None or self.purpose is None:
            raise ValueError("channel and purpose are required")
        return self

class DraftResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    channel: Optional[str] = None
    purpose: Optional[str] = None
    message: Optional[str] = None
    warnings: List[str] = []
    ai_error_category: Optional[str] = None
    evidence: List[EvidenceFact] = []
    note: str = "Yalnızca taslak. Otomatik gönderim yapılmaz."

# ---------- controlled refresh (M5-5) ----------
class RefreshCheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class RefreshReport(BaseModel):
    """Result of validate → build → verify → swap. step: busy|validate|build|verify|swap|done.
    Filenames only (never absolute paths), no workbook rows."""
    ok: bool
    step: Literal["busy", "validate", "build", "verify", "swap", "done"]
    step_tr: str
    message: str
    sources: Dict[str, Optional[str]]
    previous_as_of_data_date: Optional[str] = None
    candidate_as_of_data_date: Optional[str] = None    # date found in the selected sales file
    as_of_data_date: Optional[str] = None              # date in service after this call
    counts: Dict[str, Optional[int]] = {}
    warnings: List[str] = []
    errors: List[str] = []
    checks: List[RefreshCheck] = []
    previous_data_kept: bool = True
    duration_ms: int = 0


# ---------- scenario ----------
class ScenarioRequest(BaseModel):
    usdtry_change_pct: float = 0
    eurtry_change_pct: float = 0
    supplier_price_change_pct: float = 0
    inflation_rate: Optional[float] = Field(None, description="yüzde, ör. 35")
    financing_rate: Optional[float] = Field(None, description="yüzde, ör. 40")
    avg_days_outstanding: int = 120

class ScenarioResponse(BaseModel):
    assumptions: Dict[str, Any]
    is_forecast: bool = False
    results: Dict[str, Any]

# ---------- macro ----------
class MacroResponse(BaseModel):
    available: bool
    values: Dict[str, Any] = {}
    reason: Optional[str] = None

# ---------- action state ----------
class DeferRequest(BaseModel):
    until: Optional[str] = None
    notes: Optional[str] = None

class NotesRequest(BaseModel):
    notes: Optional[str] = None

class ActionStatusResponse(BaseModel):
    ok: bool
    action_id: str
    status: str
    defer_until: Optional[str] = None    # M5-3: the stored date after a defer (ISO)

# ========== AI OUTPUT SCHEMAS (validated strictly) ==========
# Rules shared by all AI outputs (Milestone 3):
# - every field required, no extra fields (OpenAI Structured Outputs strict mode);
# - numbers are NEVER written by the model: prose references facts as [[FACT:F001]]
#   and Python substitutes the exact display value (services/ai/grounding.py);
# - evidence_fact_ids lists the facts a statement relies on; unknown ids are rejected.
_STRICT = ConfigDict(extra="forbid")

# Field guidance sent to the model inside the strict JSON schema (Q-1: interpret, don't list facts).
_D_SUMMARY = ("2-3 kısa YORUM cümlesi: durum ne anlama geliyor ve neden önemli. Gerçekleri sıralama; "
              "en önemli en fazla 4 gerçeği [[FACT:..]] ile an. Yıllık rakamları art arda dizme. "
              "'Paket' kelimesini kullanma.")
_D_POINTS = ("Her madde tek kısa cümle, düz metin; madde sayısı en fazla 3. [[FACT:..]] KULLANMA "
             "(dayanakları evidence_fact_ids'e yaz).")
_D_INTERP = "1-2 kısa yorum cümlesi; en fazla 2 [[FACT:..]]; gerçekleri sıralama."
_D_ACTION = ("Somut, uygulanabilir sonraki ticari adım (kim, ne yapmalı); 1-2 cümle; düz metin, "
             "[[FACT:..]] KULLANMA; numaralandırma yok; veri tamamlama önerme.")
_D_EVID = "Metindeki iddiaların dayandığı tüm fact_id'ler (metinde anılmayanlar da olabilir)."


class AIActionItem(BaseModel):
    model_config = _STRICT
    action_ref: str = Field(description="items[].action_ref from the evidence packet, e.g. A01")
    interpretation: str = Field(description=_D_INTERP)
    recommendation: str = Field(description=_D_ACTION)
    evidence_fact_ids: List[str] = Field(description=_D_EVID)
    confidence: Confidence


class AIActionsOut(BaseModel):
    model_config = _STRICT
    items: List[AIActionItem]
    warnings: List[str]


class CustomerAnalysisAI(BaseModel):
    model_config = _STRICT
    summary: str = Field(description=_D_SUMMARY)
    risks: List[str] = Field(description=_D_POINTS)
    opportunities: List[str] = Field(description=_D_POINTS + " KAPALI müşteride boş liste.")
    recommended_action: str = Field(description=_D_ACTION)
    evidence_fact_ids: List[str] = Field(description=_D_EVID)
    warnings: List[str]
    confidence: Confidence


class ProductAnalysisAI(BaseModel):
    model_config = _STRICT
    status: str = Field(description=_D_INTERP)
    risk: str = Field(description=_D_INTERP)
    opportunity: str = Field(description=_D_INTERP)
    best_buyer_fact_ids: List[str] = Field(description="fact ids of 'Aday alıcı N' facts, best first")
    recommendation: str = Field(description=_D_ACTION)
    evidence_fact_ids: List[str] = Field(description=_D_EVID)
    warnings: List[str]
    confidence: Confidence


class MessageDraftAI(BaseModel):
    model_config = _STRICT
    message: str = Field(description="Müşteriye gidecek kısa, nazik taslak; içsel analiz/puan yok; "
                                     "en fazla 2 [[FACT:..]].")
    evidence_fact_ids: List[str] = Field(description=_D_EVID)
    warnings: List[str]


# ---------- M7-1 Customer 360 (deterministic, read-only; never AI) ----------
C360Confidence = Literal["trusted", "unverified", "incomplete", "stale", "invalid", "unavailable"]


class C360Value(BaseModel):
    value: Any = None
    confidence: C360Confidence
    note: Optional[str] = None


class C360Source(BaseModel):
    key: str
    label: str
    status: str
    confidence: C360Confidence
    file: Optional[str] = None          # basename only, never a path
    as_of: Optional[str] = None
    detail: Optional[str] = None


class C360Identity(BaseModel):
    customer_code: str
    display_name: str
    display_name_source: Literal["master", "sales_project", "invoice_account", "code"]
    sales_name: Optional[str] = None
    fallback_to_invoice_account: bool
    code_has_surrounding_whitespace: bool


class C360Master(BaseModel):
    status: Literal["matched", "unmatched", "duplicate", "unavailable"]
    duplicate_rows: int = 0
    name: C360Value
    status_raw: C360Value
    closed: Optional[bool] = None       # only from a unique master row; None = unknown
    sector: C360Value
    rep: C360Value


class C360Salesperson(BaseModel):
    source: Literal["order_satici", "master_temsilci", "hedef_temsilci", "hedef_musteri_temsilcisi"]
    label: str
    values: List[str] = []
    confidence: C360Confidence


class C360Year(BaseModel):
    year: int
    net_order_amount: Optional[float] = None
    quantity: Optional[float] = None
    orders: int
    lines: int
    missing_amount_lines: int = 0


class C360Sales(BaseModel):
    amount_label: str
    amount_note: str
    confidence: C360Confidence
    first_order_date: Optional[str] = None
    last_order_date: Optional[str] = None
    orders: int
    lines: int
    active_years: List[int]
    current_year: int
    has_current_year_orders: bool
    recent_orders: int
    recent_window_days: int
    zero_amount_lines: int
    missing_amount_lines: int
    distinct_products: int
    total_net_order_amount: Optional[float] = None
    years: List[C360Year]


class C360Product(BaseModel):
    sku: str
    name: Optional[str] = None
    net_order_amount: Optional[float] = None
    quantity: Optional[float] = None
    orders: int
    lines: int
    last_order_date: Optional[str] = None
    category: Optional[str] = None
    group: Optional[str] = None


class C360Mix(BaseModel):
    label: str
    net_order_amount: Optional[float] = None
    lines: int
    share: Optional[float] = None


class C360Products(BaseModel):
    limit: int
    amount_label: str
    top: List[C360Product]
    categories: List[C360Mix]
    groups: List[C360Mix]


class C360Aging(BaseModel):
    available: bool
    present_in_file: bool
    overdue: Optional[float] = None
    oldest_bucket: Optional[str] = None
    confidence: C360Confidence


class C360InvoiceAccount(BaseModel):
    account_code: str
    account_name: Optional[str] = None
    is_customer_code: bool
    orders: int
    lines: int
    net_order_amount: Optional[float] = None
    first_order_date: Optional[str] = None
    last_order_date: Optional[str] = None
    shared: bool
    shared_with_customers: int
    aging: C360Aging
    warning: Optional[str] = None


class C360Receivables(BaseModel):
    aging_status: str
    aging_file: Optional[str] = None
    level: Literal["invoice_account"] = "invoice_account"
    accounts: List[C360InvoiceAccount]
    limitations: List[str]
    detailed_collections: Literal["not_supplied"] = "not_supplied"
    detailed_collections_note: str


class C360HedefField(BaseModel):
    key: str
    label: str
    value: Any = None
    confidence: C360Confidence
    note: Optional[str] = None


class C360HedefYear(BaseModel):
    year: int
    stamp_qty: C360Value
    reported_net_revenue: C360Value


class C360Hedef(BaseModel):
    status: Literal["not_configured", "failed", "unmatched", "matched"]
    file: Optional[str] = None
    snapshot_date: Optional[str] = None
    snapshot_date_origin: Optional[str] = None
    schema_version: Optional[str] = None
    fields: List[C360HedefField] = []
    years: List[C360HedefYear] = []
    notes: List[str] = []


class C360EventOrder(BaseModel):
    order_date: Optional[str] = None
    source_type: Literal["general", "fuar", "etkinlik", "saha"]
    edition_year: Optional[int] = None
    event_name: Optional[str] = None     # local display only: never logged, summarized or sent to AI
    event_stem: Optional[str] = None
    edition_year_offset: Optional[int] = None


class C360FairCampaign(BaseModel):
    """M7-4: read-only fair-campaign membership (local operational state; edited only in Fuar Merkezi)."""
    campaign_id: int
    name: str
    status: str
    edition_year: int
    start_date: str
    stage: str
    stage_label: str
    next_action_date: Optional[str] = None
    has_note: bool = False
    linked_event: bool = False
    erp_fuar_orders: int = 0
    erp_evidence: Optional[str] = None


class C360Events(BaseModel):
    status: str
    orders_by_type: Dict[str, int]
    distinct_events_by_type: Dict[str, int]
    recent: List[C360EventOrder]
    recent_limit: int
    confidence: C360Confidence
    note: str
    campaigns: List[C360FairCampaign] = []


class C360Action(BaseModel):
    action_id: str
    category: Optional[str] = None
    category_label: Optional[str] = None
    title: Optional[str] = None
    reason: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    origin: Literal["current", "stored"]
    generated_at: Optional[str] = None
    defer_until: Optional[str] = None
    completed_at: Optional[str] = None


class C360Memory(BaseModel):
    ts: Optional[str] = None
    kind: Optional[str] = None
    detail: Optional[str] = None
    next_followup: Optional[str] = None


class C360Activity(BaseModel):
    current_actions: List[C360Action]
    stored_actions: List[C360Action]
    memory: List[C360Memory]


class C360Warning(BaseModel):
    code: str
    message: str


class C360OpportunityItem(BaseModel):
    """M7-3: one cross-sell target GROUP (anagrup) of this customer; never a SKU, never money."""
    target_group: str
    source_group: str
    source_buyers: int
    target_buyers: int
    pair_buyers: int
    confidence: float
    lift: float
    score: float
    priority: Priority
    never_purchased: Literal[True] = True
    supporting_sources: List[str] = []


class C360Opportunities(BaseModel):
    status: Literal["available", "none", "unavailable"]
    label: str
    dimension: Literal["anagrup"] = "anagrup"
    dimension_label: str
    recent_window_days: int
    closed: Optional[bool] = None
    items: List[C360OpportunityItem] = []
    notes: List[str] = []


# ---------- M7-5 lightweight CRM (local, explicit; never AI) ----------
CrmChannel = Literal["phone", "whatsapp", "email", "visit", "other"]
CrmPurpose = Literal["sales_followup", "collection", "service", "other"]


class CrmInteractionOut(BaseModel):
    id: int
    occurred_on: Optional[str] = None
    channel: Optional[str] = None
    channel_label: Optional[str] = None
    purpose: Optional[str] = None
    purpose_label: Optional[str] = None
    outcome: Optional[str] = None
    next_action: Optional[str] = None
    next_followup: Optional[str] = None
    owner_label: Optional[str] = None    # manual, non-authoritative workflow text
    note: Optional[str] = None           # local only; never logged, never sent to AI
    recorded_at: Optional[str] = None


class CrmFollowupOut(BaseModel):
    purpose: str
    purpose_label: str
    due_date: str
    due_state: Optional[Literal["overdue", "today", "upcoming", "later"]] = None
    next_action: Optional[str] = None
    owner_label: Optional[str] = None
    updated_at: Optional[str] = None
    suppressed_kapali: bool = False


class CrmLegacyItem(BaseModel):
    ts: Optional[str] = None
    kind: Optional[str] = None
    detail: Optional[str] = None
    next_followup: Optional[str] = None


class CrmLegacy(BaseModel):
    count: int
    label: str
    items: List[CrmLegacyItem]


class CrmCustomer(BaseModel):
    customer_code: str
    coverage_status: Literal["incomplete"] = "incomplete"
    coverage_label: str
    coverage_note: str
    unknown_note: str
    closed: Optional[bool] = None
    master_status: str
    warnings: List[Dict[str, str]] = []
    allowed_followup_purposes: List[str]
    structured_interactions: int
    last_interaction: Optional[CrmInteractionOut] = None
    followup: Optional[CrmFollowupOut] = None
    interactions: List[CrmInteractionOut]
    legacy: CrmLegacy
    ai_used: Literal[False] = False


class CrmFollowupSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: CrmPurpose
    due_date: dt.date
    next_action: Optional[str] = Field(default=None, max_length=160)
    owner_label: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _bounds(self):
        if not (dt.date(2000, 1, 1) <= self.due_date <= dt.date(2099, 12, 31)):
            raise ValueError("due_date out of range")
        return self


class CrmFollowupUpdate(BaseModel):
    """PUT body: either `clear: true`, or a full current follow-up definition (one per customer)."""
    model_config = ConfigDict(extra="forbid")
    clear: bool = False
    purpose: Optional[CrmPurpose] = None
    due_date: Optional[dt.date] = None
    next_action: Optional[str] = Field(default=None, max_length=160)
    owner_label: Optional[str] = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def _check(self):
        if not self.clear:
            if self.purpose is None or self.due_date is None:
                raise ValueError("purpose and due_date are required unless clear is true")
            if not (dt.date(2000, 1, 1) <= self.due_date <= dt.date(2099, 12, 31)):
                raise ValueError("due_date out of range")
        return self


class CrmInteractionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurred_on: dt.date
    channel: CrmChannel
    purpose: Optional[CrmPurpose] = None
    outcome: Optional[str] = Field(default=None, max_length=120)
    next_action: Optional[str] = Field(default=None, max_length=160)
    owner_label: Optional[str] = Field(default=None, max_length=80)
    note: Optional[str] = Field(default=None, max_length=500)
    followup: Optional[CrmFollowupSet] = None     # explicit; never inferred from the outcome

    @model_validator(mode="after")
    def _check(self):
        if self.occurred_on < dt.date(2000, 1, 1):
            raise ValueError("occurred_on out of range")
        return self


class CrmFollowupRow(CrmFollowupOut):
    customer_code: str
    display_name: str
    closed: Optional[bool] = None
    master_status: str
    warnings: List[Dict[str, str]] = []


class CrmFollowupList(BaseModel):
    mode: Literal["due", "today", "upcoming", "all"]
    today: str
    days: int
    total: int
    limit: int
    truncated: bool
    orphan_followups: int
    items: List[CrmFollowupRow]
    coverage_status: Literal["incomplete"] = "incomplete"
    coverage_note: str
    ai_used: Literal[False] = False


class CrmRecentItem(BaseModel):
    id: int
    customer_code: str
    display_name: str
    occurred_on: Optional[str] = None
    channel: Optional[str] = None
    channel_label: Optional[str] = None
    purpose_label: Optional[str] = None
    outcome: Optional[str] = None
    has_next_action: bool


class CrmRecent(BaseModel):
    limit: int
    items: List[CrmRecentItem]
    note: str
    coverage_note: str
    ai_used: Literal[False] = False


class CrmSummary(BaseModel):
    """Counts only: system coverage and workload. Never per person / owner / salesperson (D-024)."""
    model_config = ConfigDict(extra="forbid")
    coverage_status: Literal["incomplete"] = "incomplete"
    coverage_label: str
    coverage_note: str
    unknown_note: str
    today: str
    total_customers: int
    active_customers: int
    customers_with_structured_contact: int
    active_customers_with_structured_contact: int
    structured_interactions: int
    legacy_memory_rows: int
    current_followups: int
    overdue: int
    due_today: int
    upcoming_7d: int
    orphan_customer_records: int
    ai_used: Literal[False] = False


class Customer360Detail(BaseModel):
    customer_code: str
    as_of_data_date: str
    identity: C360Identity
    sources: List[C360Source]
    master: C360Master
    salespeople: List[C360Salesperson]
    sales: C360Sales
    products: C360Products
    opportunities: C360Opportunities
    receivables: C360Receivables
    hedef: C360Hedef
    events: C360Events
    crm: Optional[CrmCustomer] = None    # M7-5: CRM / Takip (local, explicit)
    activity: C360Activity
    warnings: List[C360Warning]
    ai_used: Literal[False] = False


class C360SearchHit(BaseModel):
    customer_code: str
    display_name: str
    closed: Optional[bool] = None
    last_order_date: Optional[str] = None
    match: Literal["exact_code", "code_prefix", "name_prefix", "name_substring", "code_substring"]


class Customer360Search(BaseModel):
    query: str
    limit: int
    min_chars: int
    count: int
    truncated: bool
    as_of_data_date: str
    results: List[C360SearchHit]


class Customer360Status(BaseModel):
    state: Literal["ready", "building", "error"]
    as_of_data_date: Optional[str] = None
    sales_file: Optional[str] = None
    customers: Optional[int] = None
    hedef_status: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None


# ---------- M7-2 Günlük Satış (deterministic customer-centric queue; never AI) ----------
class SalesQueueReason(BaseModel):
    action_id: str                      # the M5 action id: one state, shared with the AI Aksiyon Merkezi
    source: Optional[str] = None
    category: str
    category_label: str
    kind: Literal["collection", "sales", "operational"]
    reason: Optional[str] = None
    score: float
    priority: Optional[Priority] = None
    confidence: Optional[Confidence] = None
    draft_purpose: Optional[str] = None
    status: str = "open"
    defer_until: Optional[str] = None
    scope: Literal["customer", "invoice_account"]
    account_code: Optional[str] = None  # collections: the invoice account the receivable belongs to
    facts: List[str] = []               # M5 facts minus the legacy "Temsilci:" line (DQ-5)


class SalesQueueWarning(BaseModel):
    code: str
    message: str


class SalesQueueDraft(BaseModel):
    available: bool = True               # M7-5: False when no reason has a fitting draft purpose
    default_purpose: str
    allowed_purposes: List[str]
    collection_target: Optional[str] = None
    sales_target: str


class SalesQueueItem(BaseModel):
    customer_code: str
    display_name: str
    last_order_date: Optional[str] = None
    closed: Optional[bool] = None
    master_status: Literal["matched", "unmatched", "duplicate", "unavailable"]
    queue_score: float
    priority: Priority
    collection_first: bool
    primary_action_id: str
    reasons: List[SalesQueueReason]
    warnings: List[SalesQueueWarning] = []
    customer360_available: bool = True
    draft: SalesQueueDraft


class SalesQueueFilter(BaseModel):
    key: str
    label: str


class SalesQueueResponse(BaseModel):
    as_of_data_date: Optional[str] = None
    generated_at: Optional[str] = None
    customer360_state: Literal["ready"] = "ready"
    sources_consistent: Literal[True] = True
    filter: str
    limit: int
    visible_customers: int
    returned: int
    truncated: bool
    filter_counts: Dict[str, int]
    filters: List[SalesQueueFilter]
    diagnostics: Dict[str, Any]          # aggregate counts only, never customer identities
    items: List[SalesQueueItem]
    ai_used: Literal[False] = False


# ---------- M7-3 opportunity intelligence (deterministic, read-only; never AI) ----------
class OpportunityPair(BaseModel):
    source_group: str
    target_group: str
    source_buyers: int
    target_buyers: int
    pair_buyers: int
    confidence: float
    target_prevalence: float
    lift: float


class OpportunityThresholds(BaseModel):
    recent_days: int
    min_source_buyers: int
    min_target_buyers: int
    min_pair_buyers: int
    min_confidence: float
    min_lift: float
    max_targets_per_customer: int
    score_cap: int


class OpportunitySummary(BaseModel):
    status: Literal["ok", "unavailable"]
    reason: Optional[str] = None
    dimension: Literal["anagrup"] = "anagrup"
    dimension_label: str
    as_of_data_date: Optional[str] = None
    window_start: Optional[str] = None
    thresholds: OpportunityThresholds
    active_customers: int
    groups_in_model: int
    eligible_pairs: int
    customers_with_opportunities: int    # model level: before KAPALI, action state and the queue reservoir
    opportunity_targets: int
    targets_by_priority: Dict[str, int]
    pair_limit: int
    pairs_truncated: bool
    pairs: List[OpportunityPair]
    queue_reservoir: int                 # cross-sell reasons kept in the private Günlük Satış reservoir
    notes: List[str]
    ai_used: Literal[False] = False


class TerritoryRow(BaseModel):
    label: str                           # Hedef value as written (unverified); rendered as text only
    matched_customers: int
    active_customers: int
    current_year_customers: int
    cross_sell_customers: int
    cross_sell_targets: int


class TerritoryCoverage(BaseModel):
    hedef_accounts: int
    matched_customers: int
    missing_value_customers: int
    cross_sell_model: str


class TerritoryResponse(BaseModel):
    status: Literal["ok", "not_configured", "failed"]
    message: Optional[str] = None
    dimension: Literal["city", "district", "sector"]
    dimension_label: str
    confidence: Literal["unverified"] = "unverified"
    note: str
    limit: int
    total_rows: int
    truncated: bool
    rows: List[TerritoryRow]
    coverage: Optional[TerritoryCoverage] = None
    hedef_file: Optional[str] = None
    hedef_snapshot_date: Optional[str] = None
    as_of_data_date: str
    ai_used: Literal[False] = False


# ---------- M7-4 Fuar Merkezi (historical FUAR intelligence + manual fair campaigns; never AI) ----------
FairStage = Literal["invite_candidate", "called", "accepted", "hotel_confirmed", "meeting_planned", "attended",
                    "offer", "order", "follow_up", "declined", "removed"]
FairActiveStage = Literal["invite_candidate", "called", "accepted", "hotel_confirmed", "meeting_planned", "attended",
                          "offer", "order", "follow_up"]


class FairTop(BaseModel):
    label: str
    name: Optional[str] = None
    net_order_amount: Optional[float] = None
    quantity: float
    orders: int
    lines: int


class FairEditionSummary(BaseModel):
    event_id: str
    edition_year: int
    label: str                           # local only (may be a partner name); never logged
    stem: str
    sequence: Optional[int] = None
    first_order_date: Optional[str] = None
    last_order_date: Optional[str] = None
    orders: int
    lines: int
    customers: int
    products: int
    quantity: float
    net_order_amount: Optional[float] = None
    missing_amount_lines: int
    date_unusable_orders: int
    customer_conflict_orders: int


class FairHistoryList(BaseModel):
    status: Literal["ok", "unavailable"]
    as_of_data_date: Optional[str] = None
    amount_label: str
    amount_note: str
    notes: List[str]
    years: List[int]
    year: Optional[int] = None
    total: int
    limit: int
    truncated: bool
    diagnostics: Dict[str, Any]          # counts only
    items: List[FairEditionSummary]
    ai_used: Literal[False] = False


class FairEditionCustomer(BaseModel):
    customer_code: str
    display_name: str
    in_customer360: bool
    closed: Optional[bool] = None
    master_status: str
    warnings: List[Dict[str, str]] = []
    orders: int
    lines: int
    net_order_amount: Optional[float] = None
    quantity: float
    first_order_date: Optional[str] = None
    last_order_date: Optional[str] = None
    in_campaign: bool = False


class FairEditionDetail(FairEditionSummary):
    amount_label: str
    amount_note: str
    notes: List[str]
    customer_sort: str
    customer_limit: int
    customers_truncated: bool
    top_products: List[FairTop]
    top_groups: List[FairTop]
    top_categories: List[FairTop]
    customer_rows: List[FairEditionCustomer]
    ai_used: Literal[False] = False


class FairCampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    edition_year: int = Field(ge=2000, le=2099)
    start_date: dt.date
    end_date: Optional[dt.date] = None
    event_stem: Optional[str] = Field(default=None, max_length=120)
    event_sequence: Optional[int] = Field(default=None, ge=1, le=999)
    status: Literal["planned", "active"] = "planned"

    @model_validator(mode="after")
    def _check(self):
        if not self.name.strip():
            raise ValueError("name must not be blank")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if self.event_stem is not None and not self.event_stem.strip():
            self.event_stem = None
        return self


class FairCampaignUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    edition_year: Optional[int] = Field(default=None, ge=2000, le=2099)
    start_date: Optional[dt.date] = None
    end_date: Optional[dt.date] = None
    clear_end_date: bool = False
    event_stem: Optional[str] = Field(default=None, max_length=120)
    clear_event_link: bool = False
    event_sequence: Optional[int] = Field(default=None, ge=1, le=999)
    status: Optional[Literal["planned", "active"]] = None

    @model_validator(mode="after")
    def _check(self):
        if self.name is not None and not self.name.strip():
            raise ValueError("name must not be blank")
        return self


class FairLinkedEvent(BaseModel):
    configured: bool
    found: bool
    event_id: Optional[str] = None
    label: Optional[str] = None
    orders: Optional[int] = None
    customers: Optional[int] = None
    net_order_amount: Optional[float] = None
    first_order_date: Optional[str] = None
    last_order_date: Optional[str] = None


class FairCampaignOut(BaseModel):
    campaign_id: int
    name: str
    edition_year: int
    start_date: str
    end_date: Optional[str] = None
    event_stem: Optional[str] = None
    event_sequence: Optional[int] = None
    status: Literal["planned", "active", "archived"]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    total_customers: int
    active_customers: int
    stage_counts: Dict[str, int]
    due_tasks: int
    linked_event: FairLinkedEvent


class FairStageOut(BaseModel):
    key: str
    label: str
    terminal: bool


class FairCampaignList(BaseModel):
    stages: List[FairStageOut]
    amount_label: str
    items: List[FairCampaignOut]
    ai_used: Literal[False] = False


class FairCampaignCustomer(BaseModel):
    customer_code: str
    display_name: str
    in_customer360: bool
    last_order_date: Optional[str] = None
    closed: Optional[bool] = None
    master_status: str
    warnings: List[Dict[str, str]] = []
    stage: FairStage
    stage_label: str
    next_action_date: Optional[str] = None
    due: bool
    note: Optional[str] = None           # local only; never logged, never sent to AI
    has_note: bool
    segments: List[str]
    collection_first: bool
    erp_fuar_orders: int
    erp_evidence: Optional[str] = None
    erp_mismatch: Optional[Literal["erp_order_stage_differs", "stage_order_without_erp"]] = None
    updated_at: Optional[str] = None


class FairCampaignCustomers(BaseModel):
    campaign_id: int
    total: int
    limit: int
    truncated: bool
    note: str
    items: List[FairCampaignCustomer]
    ai_used: Literal[False] = False


class FairCustomersAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_codes: List[str] = Field(min_length=1, max_length=200)
    stage: FairActiveStage = "invite_candidate"


class FairAddResult(BaseModel):
    added: List[str]
    already_present: List[str]
    blocked_kapali: List[str]
    invalid: List[str]
    warnings: List[Dict[str, str]] = []
    counts: Dict[str, int]
    ai_used: Literal[False] = False


class FairCustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_code: str = Field(min_length=1, max_length=64)
    stage: Optional[FairStage] = None
    next_action_date: Optional[dt.date] = None
    clear_next_action: bool = False
    note: Optional[str] = Field(default=None, max_length=500)
    clear_note: bool = False


class FairCandidate(BaseModel):
    customer_code: str
    display_name: str
    last_order_date: Optional[str] = None
    closed: Optional[bool] = None
    master_status: str
    warnings: List[Dict[str, str]] = []
    segments: List[str]
    queue_score: Optional[float] = None
    collection_first: bool
    historical_fuar: bool
    in_campaign: bool
    campaign_stage: Optional[str] = None


class FairCandidates(BaseModel):
    segment: str
    total: int
    limit: int
    truncated: bool
    kapali_excluded: int
    segment_counts: Dict[str, int]
    segments: List[Dict[str, str]]
    note: str
    items: List[FairCandidate]
    ai_used: Literal[False] = False


# ---------------------------------------------------------------------------------------------
# M7-6 Yönetim (owner view; read-only, deterministic, never AI). Outputs are strict so nothing unplanned leaks.
# ---------------------------------------------------------------------------------------------
OwnerState = Literal["not_configured", "loading", "ready", "failed"]


class _OwnerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OwnerWarning(_OwnerOut):
    code: str
    count: Optional[int] = None
    message: str


class OwnerSourceHead(_OwnerOut):
    key: Literal["kredi", "gider_gelir", "butce"]
    label: str
    configured: bool
    file: Optional[str] = None                 # basename only, never a path
    state: OwnerState
    state_label: str
    reloading: bool = False
    error_code: Optional[str] = None
    message: Optional[str] = None
    failed_step: Optional[str] = None
    issue_codes: List[str] = []
    loaded_at: Optional[str] = None


class KrediBucket(_OwnerOut):
    installments: int
    amount: float


class KrediHorizon(KrediBucket):
    days: int


class KrediCurrencySchedule(_OwnerOut):
    currency: str
    next_due_date: Optional[str] = None
    horizons: List[KrediHorizon]
    after_90: KrediBucket
    past_due: KrediBucket
    total_remaining: KrediBucket


class KrediData(_OwnerOut):
    contracts: int
    installments: int
    lenders: int
    currencies: List[str]
    status_counts: Dict[str, int]
    snapshot_date: Optional[str] = None
    snapshot_origin: Optional[str] = None
    snapshot_note: Optional[str] = None
    latest_payment_date: Optional[str] = None
    schedule: List[KrediCurrencySchedule]
    warnings: List[OwnerWarning]
    remaining_principal_total: None = None     # deliberately never derived (not additive across rows)


class OwnerKredi(OwnerSourceHead):
    purpose: str
    schedule_note: str
    horizon_note: str
    overdue_note: str
    principal_note: str
    today: str
    data: Optional[KrediData] = None


class GiderGelirMonth(_OwnerOut):
    month: int
    reported_sales: Optional[float] = None
    reported_cost: Optional[float] = None
    reported_gross_profit: Optional[float] = None
    reported_expense: Optional[float] = None


class GiderGelirLine(_OwnerOut):
    line: Literal["kirtasiye", "trodat", "trotec", "imalat"]
    label: str
    reported_sales: Optional[float] = None
    reported_cost: Optional[float] = None
    reported_gross_profit: Optional[float] = None
    allocated_expense: Optional[float] = None


class GiderGelirBudgetLine(_OwnerOut):
    line: str
    label: str
    value: Optional[float] = None


class GiderGelirData(_OwnerOut):
    report_year: Optional[int] = None
    report_year_origin: Optional[str] = None
    revision_label: Optional[str] = None
    snapshot_date: None = None
    months: List[GiderGelirMonth]
    lines: List[GiderGelirLine]
    budget: List[GiderGelirBudgetLine]
    months_with_revenue: List[int]
    months_with_expenses: List[int]
    records: Dict[str, int]
    warnings: List[OwnerWarning]
    accounting_basis: Literal["unverified"]
    management_mapping: Literal["not_supplied"]


class OwnerGiderGelir(OwnerSourceHead):
    basis_note: str
    mapping_note: str
    reconciliation_note: str
    data: Optional[GiderGelirData] = None


class ButceHealth(_OwnerOut):
    planning_year: Optional[int] = None
    planning_year_origin: Optional[str] = None
    revision_label: Optional[str] = None
    snapshot_date: Optional[str] = None
    snapshot_origin: Optional[str] = None
    source_status: Optional[str] = None
    candidate_cells: int
    blank_candidates: int
    usable_unverified: int
    invalid_facts: int
    invalid_cells_in_validated_regions: int
    invalid_cells: int
    coverage_rate: float
    coverage_pct: float
    ref_errors: int
    div0_errors: int
    other_errors: int
    missing_cached_formulas: int
    external_links: int
    external_formula_cells: int
    external_dependent_cells: int
    external_dependency_regions: int
    authoritative: Literal[False]
    file_of_record: str


class OwnerButce(OwnerSourceHead):
    warning: str
    data: Optional[ButceHealth] = None


class OwnerOperations(_OwnerOut):
    state: Literal["ready", "building"]
    customer360_customers: Optional[int] = None
    daily_sales_customers: Optional[int] = None
    collection_first_customers: Optional[int] = None
    fair_tasks_due: Optional[int] = None
    crm_followups_in_queue: Optional[int] = None
    crm_overdue: Optional[int] = None
    crm_due_today: Optional[int] = None
    crm_current_followups: Optional[int] = None
    crm_structured_interactions: Optional[int] = None
    crm_coverage_status: Literal["incomplete"]
    crm_coverage_label: str
    active_fair_campaigns: int
    planned_fair_campaigns: int
    cross_sell_customers: Optional[int] = None
    cross_sell_active_customers: Optional[int] = None
    cross_sell_share: Optional[float] = None
    cross_sell_note: Optional[str] = None


class OwnerCoreSources(_OwnerOut):
    context_state: Literal["ready", "building", "error"]
    sales_file: Optional[str] = None
    stock_file: Optional[str] = None
    aging_file: Optional[str] = None
    as_of_data_date: Optional[str] = None


class OwnerHedefStatus(_OwnerOut):
    configured: bool
    file: Optional[str] = None
    status: str


class OwnerStores(_OwnerOut):
    customer360_state: str
    customer360_customers: Optional[int] = None
    crm_coverage_status: Literal["incomplete"]
    crm_coverage_label: str
    fair_campaigns: int
    sqlite_available: bool


class OwnerBlockedSource(_OwnerOut):
    key: str
    label: str
    status_label: str


class OwnerSystem(_OwnerOut):
    core: OwnerCoreSources
    hedef: OwnerHedefStatus
    owner_sources: List[OwnerSourceHead]
    stores: OwnerStores
    blocked: List[OwnerBlockedSource]


class OwnerKnownIssue(_OwnerOut):
    code: str
    title: str
    detail: str
    status: Literal["open", "blocked"]
    count: Optional[int] = None
    count_label: Optional[str] = None


class OwnerReloadState(_OwnerOut):
    inflight: bool
    generation: int


class OwnerOverview(_OwnerOut):
    today: str
    audience: Literal["owner"] = "owner"
    audience_note: str
    access_controlled: Literal[False] = False
    reload: OwnerReloadState
    operations: OwnerOperations
    kredi: OwnerKredi
    gider_gelir: OwnerGiderGelir
    butce: OwnerButce
    system: OwnerSystem
    known_issues: List[OwnerKnownIssue]
    ai_used: Literal[False] = False


class OwnerStatus(_OwnerOut):
    audience_note: str
    access_controlled: Literal[False] = False
    reload: OwnerReloadState
    system: OwnerSystem
    known_issues: List[OwnerKnownIssue]
    ai_used: Literal[False] = False


class OwnerReloadResponse(_OwnerOut):
    started: bool
    reload: OwnerReloadState
    sources: List[OwnerSourceHead]
    ai_used: Literal[False] = False
