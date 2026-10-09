"""FastAPI backend. AI anahtarı olmadan da açılır; AI uçları yapılandırılmış
'kullanılamıyor' döner (HTTP 500 değil). Determinist uçlar her zaman çalışır."""
import os, sys, logging, datetime as dt
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, Depends, HTTPException
import config as C
import schemas as S
from services import store as ST
from services.entity import ContextRepository
from services.agent import BusinessActionAgent
from services.ai.factory import get_provider
from services import scenarios as SCN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")

DATA_FOLDER = os.environ.get("DATA_FOLDER", os.path.join(ROOT, "data"))
INFLATION = C.MANUAL_INFLATION_RATE

app = FastAPI(title="AI Aksiyon Merkezi API", version="0.3")


@app.on_event("startup")
def _startup():
    ST.init_db()
    app.state.repo = ContextRepository(data_folder=DATA_FOLDER, inflation_rate=INFLATION)
    app.state.provider = get_provider()
    app.state.agent = BusinessActionAgent(app.state.provider)
    log.info("startup: provider=%s model=%s available=%s data=%s",
             app.state.provider.name, getattr(app.state.provider, "model", None),
             app.state.provider.is_available(), DATA_FOLDER)


# ---- dependencies (overridable in tests) ----
def get_repo() -> ContextRepository:
    return app.state.repo

def get_agent() -> BusinessActionAgent:
    return app.state.agent


def _ctx_or_none(repo):
    try:
        return repo.context()
    except Exception:
        return None


def _action_id(a):
    return ST.content_key(a["category"], a["entity_id"], ST.hash_facts(a["facts"]))


# ---------------- health / context ----------------
@app.get("/api/health", response_model=S.Health)
def health(agent: BusinessActionAgent = Depends(get_agent)):
    p = agent.provider
    return S.Health(ai_available=agent.available, ai_provider=p.name,
                    ai_model=getattr(p, "model", None),
                    ai_reason=(None if agent.available else getattr(p, "reason", None)),
                    macro_available=False, data_folder=DATA_FOLDER)


@app.get("/api/ai/calls")
def ai_calls(limit: int = 50):
    """AI call metadata only (provider, model, latency, tokens, error category). No content."""
    return {"calls": ST.recent_ai_calls(max(1, min(limit, 500)))}


@app.get("/api/context", response_model=S.ContextResponse)
def context(repo: ContextRepository = Depends(get_repo)):
    ctx = repo.context()
    counts = {k: len(ctx.get(k, [])) for k in
              ("collections", "overstock", "winback", "shrinking", "low_stock", "risk", "actions")}
    return S.ContextResponse(generated_at=ctx["generated_at"], as_of_data_date=ctx["as_of_data_date"],
                             source_files=ctx["source_files"], company=ctx["company"],
                             counts=counts, data=ctx)


# ---------------- daily actions ----------------
@app.get("/api/agent/actions", response_model=S.ActionsResponse)
def agent_actions(repo: ContextRepository = Depends(get_repo),
                  agent: BusinessActionAgent = Depends(get_agent)):
    ctx = repo.context()
    det = ctx["actions"]
    # persist + get status; drop completed/dismissed/deferred-not-due
    ST.upsert_actions([{**a, "entity_id": a["entity_id"]} for a in det])
    statuses = ST.get_statuses()
    visible = []
    for a in det:
        aid = _action_id(a)
        row = statuses.get(aid, {"status": "open"})
        if ST.is_hidden(row):
            continue
        visible.append((aid, a, row.get("status", "open")))
    enrich = agent.enrich_actions([a for _, a, _ in visible], ctx)
    by_ref = enrich["by_ref"]
    out = []
    for (aid, a, status), ref in zip(visible, enrich["refs"]):
        ai = by_ref.get(ref)
        out.append(S.ActionOut(
            action_id=aid, category=a["category"], entity_type=a["entity_type"],
            entity_id=a["entity_id"], title=a["title"], facts=a["facts"], drivers=a.get("drivers", {}),
            score=a["score"], priority=a["priority"], confidence=a["confidence"],
            requires_approval=True, status=status,
            interpretation=(ai["interpretation"] if ai else None),
            recommendation=(ai["recommendation"] if ai else None),
            evidence=(ai["evidence"] if ai else [])))
    return S.ActionsResponse(ai_available=enrich["ai_available"], ai_reason=enrich["reason"],
                             ai_error_category=enrich.get("category"), ai_warnings=enrich.get("warnings", []),
                             generated_at=ctx["generated_at"], count=len(out), actions=out)


# ---------------- customer / product ----------------
@app.get("/api/agent/customer/{customer_code}", response_model=S.CustomerAnalysis)
def agent_customer(customer_code: str, repo: ContextRepository = Depends(get_repo),
                   agent: BusinessActionAgent = Depends(get_agent)):
    facts = repo.customer(customer_code)
    if not facts:
        raise HTTPException(404, f"Müşteri bulunamadı: {customer_code}")
    facts = {**facts, "history": ST.get_memory("customer", customer_code)}
    data, r = agent.analyze_customer(facts, ctx=_ctx_or_none(repo))
    if data is None:
        return S.CustomerAnalysis(customer_code=customer_code, facts=facts, ai_available=agent.available,
                                  reason=r["reason"], ai_error_category=r["category"])
    return S.CustomerAnalysis(customer_code=customer_code, facts=facts, ai_available=True,
                              summary=data["summary"], risks=data["risks"],
                              opportunities=data["opportunities"],
                              recommended_action=data["recommended_action"], confidence=data["confidence"],
                              evidence=data["evidence"], ai_warnings=data.get("warnings", []))


@app.get("/api/agent/product/{product_code}", response_model=S.ProductAnalysis)
def agent_product(product_code: str, repo: ContextRepository = Depends(get_repo),
                  agent: BusinessActionAgent = Depends(get_agent)):
    facts = repo.product(product_code)
    if not facts:
        raise HTTPException(404, f"Ürün bulunamadı: {product_code}")
    data, r = agent.analyze_product(facts, ctx=_ctx_or_none(repo))
    if data is None:
        return S.ProductAnalysis(product_code=product_code, facts=facts, ai_available=agent.available,
                                 reason=r["reason"], ai_error_category=r["category"])
    return S.ProductAnalysis(product_code=product_code, facts=facts, ai_available=True,
                             status=data["status"], risk=data["risk"], opportunity=data["opportunity"],
                             best_buyers=data["best_buyers"], recommendation=data["recommendation"],
                             confidence=data["confidence"], evidence=data["evidence"],
                             ai_warnings=data.get("warnings", []))


# ---------------- draft message ----------------
@app.post("/api/agent/draft-message", response_model=S.DraftResponse)
def draft_message(req: S.DraftRequest, repo: ContextRepository = Depends(get_repo),
                  agent: BusinessActionAgent = Depends(get_agent)):
    cust = repo.customer(req.customer_code)
    if not cust:
        raise HTTPException(404, f"Müşteri bulunamadı: {req.customer_code}")
    prod = repo.product(req.product_code) if req.product_code else None
    cust = {**cust, "history": ST.get_memory("customer", req.customer_code)}
    data, r = agent.draft_message(req.message_type, cust, prod, req.additional_instruction,
                                  ctx=_ctx_or_none(repo))
    if data is None:
        return S.DraftResponse(available=False, reason=r["reason"], ai_error_category=r["category"],
                               channel=req.message_type)
    return S.DraftResponse(available=True, channel=req.message_type, message=data["message"],
                           warnings=data.get("warnings", []), evidence=data["evidence"])


# ---------------- scenario (deterministic) ----------------
@app.post("/api/scenario", response_model=S.ScenarioResponse)
def scenario(req: S.ScenarioRequest, repo: ContextRepository = Depends(get_repo)):
    ctx = repo.context()
    receivables = ctx["company"]["working_capital"]["overdue_total"]
    fin = (req.financing_rate / 100.0) if req.financing_rate is not None else None
    infl = (req.inflation_rate / 100.0) if req.inflation_rate is not None else ctx["config"]["inflation_rate"]
    assumptions = {"usdtry_change_pct": req.usdtry_change_pct, "eurtry_change_pct": req.eurtry_change_pct,
                   "supplier_price_change_pct": req.supplier_price_change_pct,
                   "inflation_pct": req.inflation_rate, "avg_days_outstanding": req.avg_days_outstanding}
    r = SCN.run_scenario(receivables_total=receivables, financing_rate=fin, inflation_rate=infl,
                         fx_sensitive_inventory_value=None, assumptions=assumptions)
    return S.ScenarioResponse(**r)


# ---------------- macro (interface + fallback only in M2) ----------------
@app.get("/api/macro", response_model=S.MacroResponse)
def macro():
    if not C.ENABLE_MACRO:
        return S.MacroResponse(available=False, reason="ENABLE_MACRO=false")
    return S.MacroResponse(available=False, values={}, reason="No macro provider configured (Milestone 4).")


# ---------------- action state ----------------
@app.post("/api/actions/{action_id}/complete", response_model=S.ActionStatusResponse)
def complete(action_id: str, body: S.NotesRequest | None = None):
    ST.set_status(action_id, "completed", notes=(body.notes if body else None))
    return S.ActionStatusResponse(ok=True, action_id=action_id, status="completed")


@app.post("/api/actions/{action_id}/defer", response_model=S.ActionStatusResponse)
def defer(action_id: str, body: S.DeferRequest | None = None):
    ST.set_status(action_id, "deferred", notes=(body.notes if body else None),
                  until=(body.until if body else None))
    return S.ActionStatusResponse(ok=True, action_id=action_id, status="deferred")


@app.post("/api/actions/{action_id}/dismiss", response_model=S.ActionStatusResponse)
def dismiss(action_id: str, body: S.NotesRequest | None = None):
    ST.set_status(action_id, "ignored", notes=(body.notes if body else None))
    return S.ActionStatusResponse(ok=True, action_id=action_id, status="ignored")
