"""FastAPI backend. AI anahtarı olmadan da açılır; AI uçları yapılandırılmış
'kullanılamıyor' döner (HTTP 500 değil). Determinist uçlar her zaman çalışır."""
import os, sys, logging, threading, datetime as dt
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import config as C
import schemas as S
from services import store as ST
from services.entity import ContextRepository
from services.agent import BusinessActionAgent
from services.ai.factory import get_provider
from services import scenarios as SCN
from services import actions as A
from services import refresh as RF
from services import evidence as EV
from services import customer360 as C360
from services import action_sources  # noqa: F401  (registers the built-in deterministic sources)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")

DATA_FOLDER = os.environ.get("DATA_FOLDER", os.path.join(ROOT, "data"))
DASHBOARD_PATH = os.environ.get("DASHBOARD_PATH", os.path.join(ROOT, "dashboard.html"))
INFLATION = C.MANUAL_INFLATION_RATE
WARMUP_CONTEXT = os.environ.get("WARMUP_CONTEXT", "true").lower() != "false"
GUARD_HEADER, GUARD_VALUE = "x-stockagent", "1"
# M7-1 Customer 360: Hedef only from an explicitly configured file (no automatic Feb/June/other selection).
HEDEF_PATH = os.environ.get("HEDEF_PATH") or None
HEDEF_SNAPSHOT_DATE = os.environ.get("HEDEF_SNAPSHOT_DATE") or None
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

app = FastAPI(title="AI Aksiyon Merkezi API", version="0.4")


# ---- local CSRF guard (D-020): every mutating request needs X-StockAgent: 1 ----
# Browsers cannot add a custom header cross-origin without a CORS preflight, and no CORS is allowed,
# so other websites cannot trigger actions or paid AI calls on this local server.
@app.middleware("http")
async def _require_guard_header(request: Request, call_next):
    if request.method in MUTATING_METHODS and request.headers.get(GUARD_HEADER) != GUARD_VALUE:
        return JSONResponse(status_code=403,
                            content={"detail": "Missing header 'X-StockAgent: 1' (local CSRF guard)."})
    return await call_next(request)


def _warm_up(repo, c360=None):
    """Build the deterministic context in the background so the first request is not a long wait.
    M7-1: then start the Customer 360 build (deterministic, no AI) for the same live sources."""
    try:
        repo.context()
        log.info("warm-up: context ready")
    except Exception as e:  # recorded on repo.last_error; requests still report it
        log.warning("warm-up failed: %s", type(e).__name__)
        return
    if c360 is not None:
        c360.state()


@app.on_event("startup")
def _startup():
    ST.init_db()
    app.state.repo = ContextRepository(data_folder=DATA_FOLDER, inflation_rate=INFLATION)
    app.state.provider = get_provider()
    app.state.agent = BusinessActionAgent(app.state.provider)
    app.state.c360 = C360.Customer360Repository(DATA_FOLDER, app.state.repo, hedef_path=HEDEF_PATH,
                                                hedef_snapshot=HEDEF_SNAPSHOT_DATE, background=True)
    if WARMUP_CONTEXT:
        threading.Thread(target=_warm_up, args=(app.state.repo, app.state.c360), name="context-warmup",
                         daemon=True).start()
    log.info("startup: provider=%s model=%s available=%s data=%s",
             app.state.provider.name, getattr(app.state.provider, "model", None),
             app.state.provider.is_available(), DATA_FOLDER)


# ---- dependencies (overridable in tests) ----
def get_repo() -> ContextRepository:
    return app.state.repo

def get_agent() -> BusinessActionAgent:
    return app.state.agent

def get_c360() -> C360.Customer360Repository:
    return app.state.c360


def _ctx_or_none(repo):
    try:
        return repo.context()
    except Exception:
        return None


def _open_actions(ctx):
    """All open deterministic actions in Python score order: [(action, status)].
    Persists new actions; hides completed / dismissed / deferred-until-future ones."""
    det = [A.normalize(a) for a in ctx["actions"]]
    ST.upsert_actions(det)
    statuses = ST.get_statuses()
    out = []
    for a in det:
        row = statuses.get(a["action_id"], {"status": "open"})
        if ST.is_hidden(row):
            continue
        out.append((a, row.get("status", "open")))
    return out


def _action_out(a, status, cached=None):
    p = (cached or {}).get("payload") or {}
    return S.ActionOut(
        action_id=a["action_id"], source=a["source"], category=a["category"],
        entity_type=a["entity_type"], entity_id=a["entity_id"], title=a["title"], reason=a["reason"],
        facts=a["facts"], drivers=a.get("drivers", {}), score=a["score"], priority=a["priority"],
        confidence=a["confidence"], requires_approval=True, status=status,
        interpretation=p.get("interpretation"), recommendation=p.get("recommendation"),
        evidence=p.get("evidence", []), ai_cached=bool(cached) and not cached.get("fresh"),
        ai_generated_at=(cached or {}).get("created_at"))


# ---------------- health / context ----------------
@app.get("/", include_in_schema=False)
def dashboard():
    """Same-origin dashboard (D-020): open http://127.0.0.1:8000/ instead of the file."""
    path = DASHBOARD_PATH
    if not os.path.isfile(path):
        return HTMLResponse(status_code=404, content=(
            "<!doctype html><meta charset='utf-8'><p>dashboard.html bulunamadı. "
            "Önce panoyu oluşturun: <code>python dashboard_builder.py data</code></p>"))
    return FileResponse(path, media_type="text/html")


@app.get("/api/health", response_model=S.Health)
def health(agent: BusinessActionAgent = Depends(get_agent), repo: ContextRepository = Depends(get_repo)):
    p = agent.provider
    ready = repo.is_ready()
    asof = (repo.context() or {}).get("as_of_data_date") if ready else None   # no build: already cached
    return S.Health(ai_available=agent.available, ai_provider=p.name,
                    ai_model=getattr(p, "model", None),
                    ai_reason=(None if agent.available else getattr(p, "reason", None)),
                    macro_available=False, data_folder=os.path.basename(os.path.normpath(DATA_FOLDER)),   # name only (M5-5: no local paths)
                    context_ready=ready, context_error=getattr(repo, "last_error", None),
                    as_of_data_date=asof)


@app.get("/api/agent/categories", response_model=list[S.CategoryOut])
def agent_categories():
    """Registered deterministic action categories (D-022). New sources appear here automatically."""
    return A.categories_payload()


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


@app.post("/api/context/refresh", response_model=S.RefreshReport,
          responses={409: {"model": S.RefreshReport, "description": "another refresh is running"}})
def context_refresh(repo: ContextRepository = Depends(get_repo)):
    """Controlled refresh (M5-5, D-019): validate → build staging → verify → swap, shared with
    `python -m services.refresh`. Deterministic: never calls the AI provider; action state and AI cache stay.
    On success the verified context is installed in this server and dashboard.html is replaced atomically."""
    rep = RF.run_refresh(DATA_FOLDER, DASHBOARD_PATH, previous_context=repo.current(),
                         install=repo.replace_context, inflation_rate=INFLATION)
    body = S.RefreshReport(**rep).model_dump()
    return JSONResponse(status_code=409 if rep["step"] == "busy" else 200, content=body)


# ---------------- daily actions ----------------
@app.get("/api/agent/actions", response_model=S.ActionsResponse)
def agent_actions(repo: ContextRepository = Depends(get_repo),
                  agent: BusinessActionAgent = Depends(get_agent)):
    """All open deterministic actions, ranked by Python. NEVER calls the AI provider (D-017);
    cached AI text (same facts, prompt version and model) is attached when present."""
    ctx = repo.context()
    visible = _open_actions(ctx)
    cache = ST.cache_get_many([a["action_id"] for a, _ in visible], agent.prompt_version, agent.model_key)
    out = [_action_out(a, st, cache.get(a["action_id"])) for a, st in visible]
    return S.ActionsResponse(ai_available=agent.available,
                             ai_reason=(None if agent.available else getattr(agent.provider, "reason", None)),
                             ai_error_category=None, ai_warnings=[],
                             generated_at=ctx["generated_at"], count=len(out), actions=out)


@app.post("/api/agent/actions/enrich", response_model=S.EnrichResponse)
def agent_actions_enrich(req: S.EnrichRequest, repo: ContextRepository = Depends(get_repo),
                         agent: BusinessActionAgent = Depends(get_agent)):
    """Explicit, paid AI request for ≤5 OPEN actions (D-017). One grounded provider call for the
    uncached ones (plus at most the M3 numeric-guard retry). Order and ranking stay Python's."""
    ids = req.action_ids
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "action_ids must be unique")
    ctx = repo.context()
    visible = _open_actions(ctx)
    open_ids = {a["action_id"] for a, _ in visible}
    unknown = [i for i in ids if i not in open_ids]
    if unknown:
        raise HTTPException(422, {"message": "Unknown or closed action_ids", "action_ids": unknown})
    wanted = set(ids)
    selected = [(a, st) for a, st in visible if a["action_id"] in wanted]      # Python score order

    if not agent.available:
        return S.EnrichResponse(ai_available=False, ai_reason=getattr(agent.provider, "reason", None),
                                ai_error_category=getattr(agent.provider, "category", "disabled"),
                                failed=[a["action_id"] for a, _ in selected],
                                actions=[_action_out(a, st) for a, st in selected])

    pv, mk = agent.prompt_version, agent.model_key
    cache = ST.cache_get_many([a["action_id"] for a, _ in selected], pv, mk)
    cached_ids = [a["action_id"] for a, _ in selected if a["action_id"] in cache]
    todo = [a for a, _ in selected if a["action_id"] not in cache]
    enriched, reason, category, warnings, called = [], None, None, [], False
    if todo:
        called = True
        r = agent.enrich_actions(todo, ctx)
        reason, category, warnings = r.get("reason"), r.get("category"), r.get("warnings", [])
        for ref, a in zip(r["refs"], todo):
            it = r["by_ref"].get(ref)
            if not it:
                continue
            payload = {"interpretation": it.get("interpretation"), "recommendation": it.get("recommendation"),
                       "evidence": it.get("evidence", []), "confidence": it.get("confidence")}
            ts = ST.cache_put(a["action_id"], pv, mk, payload)
            cache[a["action_id"]] = {"payload": payload, "created_at": ts, "fresh": True}
            enriched.append(a["action_id"])
    failed = [a["action_id"] for a, _ in selected if a["action_id"] not in cache]
    return S.EnrichResponse(ai_available=True, ai_reason=reason, ai_error_category=category,
                            ai_warnings=warnings, provider_called=called, enriched=enriched,
                            cached=cached_ids, failed=failed,
                            actions=[_action_out(a, st, cache.get(a["action_id"])) for a, st in selected])


# ---------------- customer / product ----------------
# D-020 / D-031: paid AI is POST-only. The GETs return deterministic facts (the drawer uses them) and never
# reach the provider; the AI analysis moved to explicit, guarded POST .../analyze endpoints.
def _customer_facts(repo, customer_code):
    facts = repo.customer(customer_code)
    if not facts:
        raise HTTPException(404, f"Müşteri bulunamadı: {customer_code}")
    return {**facts, "history": ST.get_memory("customer", customer_code)}


def _product_facts(repo, product_code):
    facts = repo.product(product_code)
    if not facts:
        raise HTTPException(404, f"Ürün bulunamadı: {product_code}")
    return facts


def _display_facts(facts, ctx):
    """Python-formatted label/value pairs for the customer drawer (same formatters as the evidence packets)."""
    pkt = EV.customer_packet(facts, ctx, facts.get("history"))
    return [{"label": f["label"], "display_value": f["display_value"]} for f in pkt.facts if f["label"] != "Müşteri"]


@app.get("/api/agent/customer/{customer_code}", response_model=S.CustomerAnalysis)
def agent_customer(customer_code: str, repo: ContextRepository = Depends(get_repo),
                   agent: BusinessActionAgent = Depends(get_agent)):
    """Deterministic customer facts + stored history. Never calls the AI provider."""
    facts = _customer_facts(repo, customer_code)
    return S.CustomerAnalysis(customer_code=customer_code, facts=facts, ai_available=agent.available,
                              display_facts=_display_facts(facts, _ctx_or_none(repo)))


@app.post("/api/agent/customer/{customer_code}/analyze", response_model=S.CustomerAnalysis)
def agent_customer_analyze(customer_code: str, repo: ContextRepository = Depends(get_repo),
                           agent: BusinessActionAgent = Depends(get_agent)):
    """Explicit, paid AI analysis of one customer (guarded POST). No UI in M5-4."""
    facts = _customer_facts(repo, customer_code)
    ctx = _ctx_or_none(repo)
    data, r = agent.analyze_customer(facts, ctx=ctx)
    disp = _display_facts(facts, ctx)
    if data is None:
        return S.CustomerAnalysis(customer_code=customer_code, facts=facts, ai_available=agent.available,
                                  display_facts=disp, reason=r["reason"], ai_error_category=r["category"])
    return S.CustomerAnalysis(customer_code=customer_code, facts=facts, ai_available=True, display_facts=disp,
                              summary=data["summary"], risks=data["risks"],
                              opportunities=data["opportunities"],
                              recommended_action=data["recommended_action"], confidence=data["confidence"],
                              evidence=data["evidence"], ai_warnings=data.get("warnings", []))


@app.get("/api/agent/product/{product_code}", response_model=S.ProductAnalysis)
def agent_product(product_code: str, repo: ContextRepository = Depends(get_repo),
                  agent: BusinessActionAgent = Depends(get_agent)):
    """Deterministic product facts. Never calls the AI provider."""
    return S.ProductAnalysis(product_code=product_code, facts=_product_facts(repo, product_code),
                             ai_available=agent.available)


@app.post("/api/agent/product/{product_code}/analyze", response_model=S.ProductAnalysis)
def agent_product_analyze(product_code: str, repo: ContextRepository = Depends(get_repo),
                          agent: BusinessActionAgent = Depends(get_agent)):
    """Explicit, paid AI analysis of one product (guarded POST). No UI in M5-4."""
    facts = _product_facts(repo, product_code)
    data, r = agent.analyze_product(facts, ctx=_ctx_or_none(repo))
    if data is None:
        return S.ProductAnalysis(product_code=product_code, facts=facts, ai_available=agent.available,
                                 reason=r["reason"], ai_error_category=r["category"])
    return S.ProductAnalysis(product_code=product_code, facts=facts, ai_available=True,
                             status=data["status"], risk=data["risk"], opportunity=data["opportunity"],
                             best_buyers=data["best_buyers"], recommendation=data["recommendation"],
                             confidence=data["confidence"], evidence=data["evidence"],
                             ai_warnings=data.get("warnings", []))


# ---------------- M7-1 Customer 360 (deterministic, read-only; never calls the AI provider) ----------------
# A separate full-customer repository (services/customer360.py, D-042), rebuilt whenever the live context's sources
# change. These GETs never mutate state and never reach the provider; errors carry fixed text, never paths.
def _c360_snapshot(c360):
    try:
        return c360.snapshot()
    except C360.Customer360NotReady as e:
        raise HTTPException(503, {"state": e.state, "message": "Müşteri 360 hazırlanıyor…"})
    except C360.Customer360Error as e:
        raise HTTPException(503, {"state": "error", "error_code": e.code, "message": e.message})


@app.get("/api/customer360/status", response_model=S.Customer360Status)
def customer360_status(retry: bool = False, c360: C360.Customer360Repository = Depends(get_c360)):
    if retry:
        c360.retry()
    return S.Customer360Status(**c360.state())


@app.get("/api/customer360/search", response_model=S.Customer360Search)
def customer360_search(q: str = "", limit: int = C360.SEARCH_DEFAULT,
                       c360: C360.Customer360Repository = Depends(get_c360)):
    snap = _c360_snapshot(c360)
    limit = max(1, min(limit, C360.SEARCH_MAX))
    hits, truncated = snap.search(q, limit)
    return S.Customer360Search(query=q, limit=limit, min_chars=C360.SEARCH_MIN_CHARS, count=len(hits),
                               truncated=truncated, as_of_data_date=snap.as_of.isoformat(), results=hits)


@app.get("/api/customer360/{customer_code}", response_model=S.Customer360Detail)
def customer360_detail(customer_code: str, c360: C360.Customer360Repository = Depends(get_c360),
                       repo: ContextRepository = Depends(get_repo)):
    snap = _c360_snapshot(c360)
    det = C360.customer_detail(snap, customer_code, ctx=repo.current(),
                               stored_actions=ST.get_entity_actions("customer", customer_code),
                               memory=ST.get_memory("customer", customer_code),
                               categories={c.key: c.label_tr for c in A.categories()})
    if det is None:
        raise HTTPException(404, f"Müşteri bulunamadı: {customer_code}")
    return det


# ---------------- draft message (copy-only; nothing is ever sent) ----------------
@app.post("/api/agent/draft-message", response_model=S.DraftResponse)
def draft_message(req: S.DraftRequest, repo: ContextRepository = Depends(get_repo),
                  agent: BusinessActionAgent = Depends(get_agent)):
    """channel × purpose draft (D-018, D-030). KAPALI customers: collection only (refused before any AI call).
    Returns text to review and copy; there is no send capability anywhere in the app."""
    cust = _customer_facts(repo, req.customer_code)
    prod = repo.product(req.product_code) if req.product_code else None
    data, r = agent.draft_message(req.channel, req.purpose, cust, prod, req.additional_instruction,
                                  ctx=_ctx_or_none(repo))
    if data is None:
        return S.DraftResponse(available=False, reason=r["reason"], ai_error_category=r["category"],
                               channel=req.channel, purpose=req.purpose)
    return S.DraftResponse(available=True, channel=req.channel, purpose=req.purpose, message=data["message"],
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
# Persisted in the local SQLite store (services/store.py). Never touches the AI provider or the AI cache.
DEFER_DEFAULT_DAYS = 7   # design §7: "Ertele" defaults to +7 days


def _require_action(action_id):
    """Mutations are server-confirmed (M5-3): an unknown action_id is a 404, not a silent no-op."""
    if ST.get_action(action_id) is None:
        raise HTTPException(404, f"Aksiyon bulunamadı: {action_id}")


def _defer_until(body):
    """ISO date strictly after today; default today + 7. A deferred action without a date would never hide."""
    raw = body.until if body else None
    today = dt.date.today()
    if raw is None or not str(raw).strip():
        return (today + dt.timedelta(days=DEFER_DEFAULT_DAYS)).isoformat()
    try:
        d = dt.date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        raise HTTPException(422, "until must be an ISO date (YYYY-MM-DD)")
    if d <= today:
        raise HTTPException(422, "until must be a future date")
    return d.isoformat()


@app.post("/api/actions/{action_id}/complete", response_model=S.ActionStatusResponse)
def complete(action_id: str, body: S.NotesRequest | None = None):
    _require_action(action_id)
    ST.set_status(action_id, "completed", notes=(body.notes if body else None))
    return S.ActionStatusResponse(ok=True, action_id=action_id, status="completed")


@app.post("/api/actions/{action_id}/defer", response_model=S.ActionStatusResponse)
def defer(action_id: str, body: S.DeferRequest | None = None):
    _require_action(action_id)
    until = _defer_until(body)
    ST.set_status(action_id, "deferred", notes=(body.notes if body else None), until=until)
    return S.ActionStatusResponse(ok=True, action_id=action_id, status="deferred", defer_until=until)


@app.post("/api/actions/{action_id}/dismiss", response_model=S.ActionStatusResponse)
def dismiss(action_id: str, body: S.NotesRequest | None = None):
    _require_action(action_id)
    ST.set_status(action_id, "ignored", notes=(body.notes if body else None))
    return S.ActionStatusResponse(ok=True, action_id=action_id, status="ignored")


@app.post("/api/actions/{action_id}/reopen", response_model=S.ActionStatusResponse)
def reopen(action_id: str):
    """Undo ("Geri al", M5-3, D-029): back to open. set_status clears completed_at / defer_until / notes and
    appends a 'status:open' history event. The action row, its history and the AI cache are kept."""
    _require_action(action_id)
    ST.set_status(action_id, "open")
    return S.ActionStatusResponse(ok=True, action_id=action_id, status="open")
