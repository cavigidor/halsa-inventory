"""API sözleşmesi ve AI çıktı şemaları (Pydantic v2)."""
from typing import Optional, List, Any, Dict, Literal
from pydantic import BaseModel, Field, ConfigDict

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
    category: str
    entity_type: str
    entity_id: str
    title: str
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

class ActionsResponse(BaseModel):
    ai_available: bool
    ai_reason: Optional[str] = None
    ai_error_category: Optional[str] = None
    ai_warnings: List[str] = []
    generated_at: str
    count: int
    actions: List[ActionOut]

# ---------- customer / product analysis ----------
class CustomerAnalysis(BaseModel):
    available: bool = True
    customer_code: str
    facts: Dict[str, Any]
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
class DraftRequest(BaseModel):
    customer_code: str
    message_type: Literal["whatsapp", "email", "collection", "sales_offer", "winback"]
    product_code: Optional[str] = None
    additional_instruction: Optional[str] = None

class DraftResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    channel: Optional[str] = None
    message: Optional[str] = None
    warnings: List[str] = []
    ai_error_category: Optional[str] = None
    evidence: List[EvidenceFact] = []
    note: str = "Yalnızca taslak. Otomatik gönderim yapılmaz."

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
_D_POINTS = "Her madde tek kısa cümle; en fazla 1 [[FACT:..]]; madde sayısı en fazla 3."
_D_INTERP = "1-2 kısa yorum cümlesi; en fazla 2 [[FACT:..]]; gerçekleri sıralama."
_D_ACTION = ("Somut, uygulanabilir sonraki adım (kim, ne yapmalı); 1-2 cümle; numaralandırma yok; "
             "en fazla 1 [[FACT:..]].")
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
