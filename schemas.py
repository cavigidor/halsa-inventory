"""API sözleşmesi ve AI çıktı şemaları (Pydantic v2)."""
from typing import Optional, List, Any, Dict, Literal
from pydantic import BaseModel, Field

Priority = Literal["critical", "high", "medium", "low"]
Confidence = Literal["high", "medium", "low"]

# ---------- generic ----------
class Health(BaseModel):
    status: str = "ok"
    ai_available: bool
    ai_provider: str
    macro_available: bool
    data_folder: str

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

class ActionsResponse(BaseModel):
    ai_available: bool
    ai_reason: Optional[str] = None
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
class _AIActionItem(BaseModel):
    entity_id: str
    interpretation: str
    recommendation: str
    confidence: Confidence

class AIActionsOut(BaseModel):
    items: List[_AIActionItem]

class CustomerAnalysisAI(BaseModel):
    summary: str
    risks: List[str]
    opportunities: List[str]
    recommended_action: str
    confidence: Confidence

class ProductAnalysisAI(BaseModel):
    status: str
    risk: str
    opportunity: str
    best_buyers: List[str]
    recommendation: str
    confidence: Confidence

class MessageDraftAI(BaseModel):
    message: str
    warnings: List[str] = []
