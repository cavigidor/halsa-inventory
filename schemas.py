"""API sözleşmesi ve AI çıktı şemaları (Pydantic v2)."""
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
