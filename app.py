"""FastAPI backend. AI anahtarı olmadan da açılır; AI uçları yapılandırılmış
'kullanılamıyor' döner (HTTP 500 değil). Determinist uçlar her zaman çalışır."""
import os, sys, logging, threading, datetime as dt
from typing import Literal
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
from services import sales_queue as SQ
from services import opportunities as OP
from services import fairs as FA
from services import fair_store as FS
from services import fair_center as FC
from services import crm as CRM
from services import owner_view as OV
from services import accuracy as ACC
from services import generation as GEN
from services import request_pin as RP  # noqa: E402
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


# ---- M8-ACCURACY: fail-closed factual-integrity gate in the shared response layer ----
# Every route is classified in services/accuracy.ROUTES. A route bound to a metric answers with a neutral withheld
# notice (HTTP 409, no data) unless the metric is VERIFIED; an unclassified route is withheld too. Endpoints that mix
# operational records with analytics redact field by field (ACC.redact_*). A withheld request never reaches an
# endpoint, a provider or the SQLite state.
from starlette.routing import Match


def _route_template(scope):
    for route in app.router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", None)
    return None


def served_fingerprints():
    """Fingerprints of the source revisions the API is SERVING right now: the live context's generation (core) and the
    owner views currently installed. Evidence binds only to these (services/generation.Manifest.bound_snapshot)."""
    served = {}
    try:
        repo = app.dependency_overrides.get(get_repo, get_repo)()
        ctx = repo.current() if repo is not None else None
        served.update(((getattr(ctx, "generation", None) or {}).get("served")) or {})
    except Exception:
        pass
    try:
        owner = app.dependency_overrides.get(get_owner, get_owner)()
        for k, e in owner.snapshot().items():
            served[k] = e.get("fingerprint")
    except Exception:
        pass
    return served


def _build_gate():
    """(manifest, served fingerprints, gate), captured together under the publish lock so no publication can fall
    between reading the manifest and reading the views being served."""
    store = getattr(app.state, "generations", None)
    with RP.PUBLISH_LOCK:
        m = store.current() if store is not None else None
        served = served_fingerprints()
    if m is None:
        return None, served, ACC.current_gate()
    return m, served, ACC.current_gate(snapshot=m.bound_snapshot(served), expected_generation=m.generation_id)


def accuracy_gate():
    """The gate for this request (production: services.accuracy.current_gate; no runtime switch exists), bound to the
    ONE published generation (M8-ACCURACY-2): evidence only for sources whose served data has the recorded fingerprint.
    No published generation → no evidence → every source-bound output withheld.

    Request-pinned (review of PR #28 issue 1): built ONCE per request, from the context / owner views that this request
    pins (services/request_pin). The endpoint then reads those same pinned objects, so the evidence that authorized the
    response always belongs to the data in it, even if a refresh or an owner reload publishes meanwhile."""
    v = RP.current_view()
    if v is None:
        return _build_gate()[2]
    with v.lock():
        if v.gate is None:
            v.manifest, v.served, v.gate = _build_gate()
        return v.gate


@app.middleware("http")
async def _accuracy_gate(request: Request, call_next):
    token = RP.begin()                                # one pinned view per request (services/request_pin)
    try:
        resp = await _accuracy_decide(request, call_next)
        v = RP.current_view()
        if v is not None and v.gate is not None:      # which generation authorized this response (no values)
            resp.headers["X-Halsa-Generation"] = v.manifest.generation_id if v.manifest is not None else "none"
        return resp
    finally:
        RP.end(token)


async def _accuracy_decide(request: Request, call_next):
    method = "GET" if request.method == "HEAD" else request.method
    tpl = _route_template(request.scope)
    if tpl is None or method == "OPTIONS":
        return await call_next(request)               # unknown path / method → the router answers 404 / 405
    kind, ref = ACC.ROUTES.get((method, tpl), ("unclassified", None))
    if kind == "metric":
        d = accuracy_gate().decide(ref)
    elif kind == "unclassified":
        d = ACC.Decision(f"route:{method} {tpl}", "Sınıflandırılmamış uç", ACC.State.INVALID,
                         ("Bu uç doğrulama kaydında sınıflandırılmadı.",), ("UNCLASSIFIED-ROUTE",))
    else:
        return await call_next(request)
    if d.displayable:
        return await call_next(request)
    return JSONResponse(status_code=ACC.WITHHELD_HTTP_STATUS, content={"detail": d.notice()})


# ---- M7-5: CRM validation errors never echo the submitted text (notes, outcomes, owner labels) ----
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    if not request.url.path.startswith("/api/crm/"):
        return await request_validation_exception_handler(request, exc)
    errors = [{"loc": list(e.get("loc", ())), "type": e.get("type"), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


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
    # M8-ACCURACY-2: restore the published generation only if the dashboard and every source still match it
    app.state.generations = GEN.GenerationStore()
    app.state.generations.restore(GEN.manifest_path(DASHBOARD_PATH), DASHBOARD_PATH, DATA_FOLDER)
    app.state.repo = ContextRepository(data_folder=DATA_FOLDER, inflation_rate=INFLATION,
                                       generation_store=app.state.generations)
    app.state.provider = get_provider()
    app.state.agent = BusinessActionAgent(app.state.provider)
    app.state.c360 = C360.Customer360Repository(DATA_FOLDER, app.state.repo, hedef_path=HEDEF_PATH,
                                                hedef_snapshot=HEDEF_SNAPSHOT_DATE, background=True)
    # M7-6 Yönetim: owner sources only from explicit env paths; loaded lazily when the tab is first opened.
    app.state.owner = OV.OwnerRepository(OV.config_from_env(), background=True,
                                         on_record=lambda k, r: app.state.generations.replace_owner(k, r))
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

def get_owner() -> OV.OwnerRepository:
    return app.state.owner


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
    # M8-ACCURACY: never serve a dashboard built before the factual-integrity gate (it embeds unverified rows).
    if not _gated_dashboard(path):
        return HTMLResponse(status_code=503, content=(
            "<!doctype html><meta charset='utf-8'><p>Bu pano doğrulama kuralları uygulanmadan oluşturulmuş; "
            "gösterilmiyor. Verileri yenileyin veya panoyu yeniden oluşturun.</p>"))
    store = getattr(app.state, "generations", None)
    with RP.PUBLISH_LOCK:                              # the bytes and the generation are captured as one step
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            return HTMLResponse(status_code=404, content="<!doctype html><meta charset='utf-8'><p>Pano okunamadı.</p>")
        m = store.current() if store is not None else None
    ok, reason, gen_id = _dashboard_authorized(body, m)
    if not ok:
        log.info("dashboard withheld: %s", reason)
        return HTMLResponse(status_code=503, headers={"X-Halsa-Dashboard": reason}, content=(
            "<!doctype html><meta charset='utf-8'><p>Pano şu anda doğrulanamadı; doğrulanmamış sonuçlar "
            "gösterilmiyor. Verileri yenileyin (Yenile) veya sunucunun hazırlanmasını bekleyin.</p>"))
    return HTMLResponse(content=body, headers={"X-Halsa-Generation": gen_id or "none"})


def _dashboard_authorized(body: bytes, m) -> tuple[bool, str, str | None]:
    """May these exact HTML bytes be served now? (M8-ACCURACY-2, fail closed)

    The HTML lists every metric whose output it embeds (`stockagent-displayed`). Evidence binds to it only when the
    ACCEPTED (re-validated) generation is the one the HTML was built from (`stockagent-generation` = `core_id`) and the
    bytes are exactly the bytes published with it (`dashboard_sha256`). Then the CURRENT gate — today's freshness,
    current register — must authorize every displayed metric. A pending, rejected, missing or mismatched generation
    gives the gate no evidence, so any embedded source-bound metric is refused (an HTML that displays nothing
    source-bound is still served: it embeds no metric data)."""
    gen, displayed = GEN.dashboard_markers(body)
    if displayed is None:
        return False, "displayed_marker_missing", None
    bound = (m is not None and bool(m.core) and gen is not None and gen == m.core_id
             and m.dashboard_sha256 is not None and GEN.sha256_bytes(body) == m.dashboard_sha256)
    gate = (ACC.current_gate(snapshot=m.core_snapshot(), expected_generation=m.generation_id) if bound
            else ACC.current_gate())
    try:
        refused = [k for k in displayed if not gate.decide(k).displayable]
    except Exception:
        return False, "authorization_error", None
    if refused:
        return False, ("not_authorized" if bound else "evidence_unavailable"), None
    return True, "ok", (m.generation_id if bound else None)


def _gated_dashboard(path):
    marker = f'<meta name="stockagent-accuracy" content="{ACC.POLICY_VERSION}">'.encode()
    try:
        with open(path, "rb") as f:
            return marker in f.read(4096)
    except OSError:
        return False


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
                         install=lambda staged: _install_generation(repo, staged), inflation_rate=INFLATION)
    rep = ACC.redact_refresh_report(rep, accuracy_gate())          # M8-ACCURACY: no unverified totals in the report
    body = S.RefreshReport(**rep).model_dump()
    return JSONResponse(status_code=409 if rep["step"] == "busy" else 200, content=body)


def _install_generation(repo, staged):
    """Install the verified context AND its generation together (M8-ACCURACY-2), under the publish lock (the refresh
    holds it around the file swap too). If the generation cannot be installed, the previous context is put back, so
    context and evidence never diverge."""
    store = getattr(app.state, "generations", None)
    with RP.PUBLISH_LOCK:
        live = getattr(repo, "live", None)
        previous = live() if callable(live) else repo.current()      # the LIVE context, not this request's pin
        repo.replace_context(staged)
        if store is None:
            return
        try:
            store.install(getattr(staged, "generation_manifest", None))
        except Exception:
            if previous is not None:
                repo.replace_context(previous)
            raise


@app.get("/api/accuracy/status")
def accuracy_status():
    """System information: the published generation (fingerprints, declared dates, validation counts — no values) and
    the verification state of every registered output for the data being served. No business number."""
    store = getattr(app.state, "generations", None)
    gate = accuracy_gate()
    v = RP.current_view()                                  # the manifest and fingerprints this gate was built from
    m, served = (v.manifest, v.served or {}) if v is not None else _build_gate()[:2]
    sources = []
    for r in ((*m.core, *m.owner) if m is not None else ()):
        sources.append({"key": r.key, "role": r.role, "source_type": r.source_type, "date_kind": r.date_kind,
                        "file": r.file, "sha256": r.sha256, "selection_origin": r.selection_origin,
                        "declared": GEN._jsonable(r.declared), "bound": served.get(r.key) == r.sha256})
    return {"generation_id": m.generation_id if m else None, "core_id": m.core_id if m else None,
            "selection_origin": m.selection_origin if m else None,
            "restore_reason": getattr(store, "restore_reason", None) if store else None,
            "restore_pending": bool(store is not None and store.pending is not None),
            "generation_bound_api_evidence": ACC.GENERATION_BOUND_API_EVIDENCE, "sources": sources,
            "metrics": [{"key": k, "state": gate.decide(k).state.value, "codes": list(gate.decide(k).codes)}
                        for k in ACC.REGISTER], "ai_used": False}


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
    gate = accuracy_gate()      # M8-ACCURACY: code + recorded name stay; derived last-order date / KAPALI withheld
    return S.Customer360Search(query=q, limit=limit, min_chars=C360.SEARCH_MIN_CHARS, count=len(hits),
                               truncated=truncated, as_of_data_date=snap.as_of.isoformat(),
                               results=[ACC.redact_directory(h, gate) for h in hits],
                               withheld=ACC.directory_notices(gate))


@app.get("/api/customer360/{customer_code}", response_model=S.Customer360Detail)
def customer360_detail(customer_code: str, c360: C360.Customer360Repository = Depends(get_c360),
                       repo: ContextRepository = Depends(get_repo)):
    snap = _c360_snapshot(c360)
    ctx = repo.current()
    same = ctx is not None and c360.signature_for(ctx) == snap.signature
    det = C360.customer_detail(snap, customer_code, ctx=ctx,
                               stored_actions=ST.get_entity_actions("customer", customer_code),
                               memory=ST.get_memory("customer", customer_code),
                               categories={c.key: c.label_tr for c in A.categories()},
                               opportunities=_same_source_opportunities(c360, snap, ctx),
                               # M7-4: membership is local state; ERP evidence only from same-source FUAR data
                               fair_campaigns=FC.customer360_campaigns(customer_code,
                                                                       getattr(ctx, "fairs", None) if same else None),
                               crm=CRM.customer_view(snap, customer_code))      # M7-5: local CRM / Takip
    if det is None:
        raise HTTPException(404, f"Müşteri bulunamadı: {customer_code}")
    return det


# ---------------- M7-2 Günlük Satış (deterministic, customer-centric; never calls the AI provider) ----------------
# Built from the live context's PRIVATE customer candidates (before the M5 top-15 cut), the Customer 360 snapshot of
# the same sources and the shared SQLite action state. No Excel read. Reason state changes use /api/actions/*.
@app.get("/api/sales-queue", response_model=S.SalesQueueResponse)
def sales_queue(limit: int = C.MAX_DAILY_SALES_QUEUE, filter: str = "all",
                repo: ContextRepository = Depends(get_repo), c360: C360.Customer360Repository = Depends(get_c360)):
    try:
        snap = c360.snapshot()
    except C360.Customer360NotReady as e:
        raise HTTPException(503, {"state": e.state, "message": "Müşteri verisi hazırlanıyor…"})
    except C360.Customer360Error as e:
        raise HTTPException(503, {"state": "error", "error_code": e.code, "message": e.message})
    ctx = repo.current()
    if ctx is None or c360.signature_for(ctx) != snap.signature:     # never mix candidates of A with customers of B
        raise HTTPException(503, {"state": "building", "message": "Müşteri verisi hazırlanıyor…"})
    try:
        q = SQ.build_queue(ctx, snap, limit=limit, flt=filter)
    except SQ.QueueUnavailable as e:
        raise HTTPException(503, {"state": "error", "error_code": e.code, "message": e.message})
    return S.SalesQueueResponse(**q)


def _same_source_opportunities(c360, snap, ctx):
    """The live cross-sell model only when the live context and the Customer 360 snapshot share their sources."""
    if ctx is None or c360.signature_for(ctx) != snap.signature:
        return None
    return getattr(ctx, "opportunities", None)


# ---------------- M7-3 opportunity intelligence (deterministic, read-only; never calls the AI provider) -------------
# The cross-sell model lives privately on the live context (built from its already-read sales frame); territory
# counts join it with the Customer 360 snapshot of the same sources and the explicit Hedef file. No Excel read.
@app.get("/api/opportunities/summary", response_model=S.OpportunitySummary)
def opportunities_summary(limit: int = 20, repo: ContextRepository = Depends(get_repo)):
    ctx = repo.current()
    model = getattr(ctx, "opportunities", None) if ctx is not None else None
    if model is None:
        raise HTTPException(503, {"state": "error", "error_code": "opportunities_unavailable",
                                  "message": "Fırsat modeli bu bağlamda yok; verileri yenileyin."})
    body = model.summary(limit)
    body["queue_reservoir"] = sum(1 for a in (getattr(ctx, "sales_queue_candidates", None) or ())
                                  if a.get("category") == "cross_sell")
    return S.OpportunitySummary(**body)


@app.get("/api/opportunities/territories", response_model=S.TerritoryResponse)
def opportunities_territories(dimension: Literal["city", "district", "sector"] = "city", limit: int = 50,
                              repo: ContextRepository = Depends(get_repo),
                              c360: C360.Customer360Repository = Depends(get_c360)):
    try:
        snap = c360.snapshot()
    except C360.Customer360NotReady as e:
        raise HTTPException(503, {"state": e.state, "message": "Müşteri verisi hazırlanıyor…"})
    except C360.Customer360Error as e:
        raise HTTPException(503, {"state": "error", "error_code": e.code, "message": e.message})
    ctx = repo.current()
    if ctx is None or c360.signature_for(ctx) != snap.signature:
        raise HTTPException(503, {"state": "building", "message": "Müşteri verisi hazırlanıyor…"})
    return S.TerritoryResponse(**OP.territories(snap, getattr(ctx, "opportunities", None), dimension, limit))


# ---------------- M7-4 Fuar Merkezi (historical FUAR facts + manual fair campaigns; never calls the AI provider) ---
# History comes from the live context's private FUAR index (FUAR-tagged orders only); campaigns are local SQLite state
# validated against the Customer 360 snapshot of the same sources. No Excel read. Mutations need X-StockAgent: 1
# (global middleware). Nothing here logs names, codes, notes or event names.
def _fair_history(repo):
    ctx = repo.current()
    hist = getattr(ctx, "fairs", None) if ctx is not None else None
    if hist is None:
        raise HTTPException(503, {"state": "error", "error_code": "fair_history_unavailable",
                                  "message": "Fuar geçmişi bu bağlamda yok; verileri yenileyin."})
    return ctx, hist


def _fair_ctx_snap(repo, c360):
    try:
        snap = c360.snapshot()
    except C360.Customer360NotReady as e:
        raise HTTPException(503, {"state": e.state, "message": "Müşteri verisi hazırlanıyor…"})
    except C360.Customer360Error as e:
        raise HTTPException(503, {"state": "error", "error_code": e.code, "message": e.message})
    ctx = repo.current()
    if ctx is None or c360.signature_for(ctx) != snap.signature:
        raise HTTPException(503, {"state": "building", "message": "Müşteri verisi hazırlanıyor…"})
    return ctx, snap


def _fair_error(e: "FC.FairError"):
    raise HTTPException(e.status, {"error_code": e.code, "message": e.message})


@app.get("/api/fairs/history", response_model=S.FairHistoryList)
def fairs_history(year: int | None = None, q: str | None = None, limit: int = 50,
                  repo: ContextRepository = Depends(get_repo)):
    ctx, hist = _fair_history(repo)
    n = max(1, min(int(limit), 200))
    items, total = hist.list(year=year, q=(q or "").strip() or None, limit=n)
    d = hist.diagnostics
    safe = {k: d.get(k) for k in ("editions", "edition_year_min", "edition_year_max", "fuar_orders", "fuar_lines",
                                  "fuar_customers", "fuar_lines_with_amount", "fuar_missing_amount_lines",
                                  "fuar_date_unusable_orders", "fuar_customer_conflict_orders", "label_conflict_orders",
                                  "unparsed_labels", "orders_by_type")}
    return S.FairHistoryList(status=hist.status, as_of_data_date=hist.as_of, amount_label=FA.AMOUNT_LABEL,
                             amount_note=FA.AMOUNT_NOTE, notes=FA.SCOPE_NOTES, years=hist.years(), year=year,
                             total=total, limit=n, truncated=total > n, diagnostics=safe, items=items)


@app.get("/api/fairs/history/{event_id}", response_model=S.FairEditionDetail)
def fairs_history_detail(event_id: str, campaign_id: int | None = None, customer_limit: int = 100,
                         repo: ContextRepository = Depends(get_repo),
                         c360: C360.Customer360Repository = Depends(get_c360)):
    ctx, snap = _fair_ctx_snap(repo, c360)
    hist = getattr(ctx, "fairs", None)
    e = hist.editions.get(event_id) if hist is not None else None
    if e is None:
        raise HTTPException(404, "Fuar bulunamadı.")
    return S.FairEditionDetail(**FC.edition_detail(e, snap, campaign_id, customer_limit))


def _stages():
    return [S.FairStageOut(key=k, label=v, terminal=k in FS.TERMINAL_STAGES) for k, v in FS.STAGE_LABELS.items()]


@app.get("/api/fairs/campaigns", response_model=S.FairCampaignList)
def fairs_campaigns(repo: ContextRepository = Depends(get_repo)):
    gate = accuracy_gate()
    ctx = repo.current()
    hist = getattr(ctx, "fairs", None) if ctx is not None else None
    return S.FairCampaignList(stages=_stages(), amount_label=FA.AMOUNT_LABEL,
                              items=[ACC.redact_fair_campaign(FC.campaign_summary(c, hist), gate) for c in FS.list_campaigns()])


@app.post("/api/fairs/campaigns", response_model=S.FairCampaignOut)
def fairs_campaign_create(body: S.FairCampaignCreate, repo: ContextRepository = Depends(get_repo)):
    c = FS.create_campaign(name=body.name.strip(), edition_year=body.edition_year,
                           start_date=body.start_date.isoformat(),
                           end_date=body.end_date.isoformat() if body.end_date else None,
                           event_stem=body.event_stem, event_sequence=body.event_sequence, status=body.status)
    ctx = repo.current()
    return ACC.redact_fair_campaign(FC.campaign_summary(c, getattr(ctx, "fairs", None) if ctx is not None else None),
                                     accuracy_gate())


@app.get("/api/fairs/campaigns/{campaign_id}", response_model=S.FairCampaignOut)
def fairs_campaign(campaign_id: int, repo: ContextRepository = Depends(get_repo)):
    try:
        c = FC.require_campaign(campaign_id)
    except FC.FairError as e:
        _fair_error(e)
    ctx = repo.current()
    return ACC.redact_fair_campaign(FC.campaign_summary(c, getattr(ctx, "fairs", None) if ctx is not None else None),
                                     accuracy_gate())


@app.put("/api/fairs/campaigns/{campaign_id}", response_model=S.FairCampaignOut)
def fairs_campaign_update(campaign_id: int, body: S.FairCampaignUpdate, repo: ContextRepository = Depends(get_repo)):
    fields = {}
    for k in ("name", "edition_year", "event_sequence", "status"):
        v = getattr(body, k)
        if v is not None:
            fields[k] = v.strip() if k == "name" else v
    if body.start_date is not None:
        fields["start_date"] = body.start_date.isoformat()
    if body.end_date is not None:
        fields["end_date"] = body.end_date.isoformat()
    if body.clear_end_date:
        fields["end_date"] = None
    if body.event_stem is not None:
        fields["event_stem"] = body.event_stem.strip() or None
    if body.clear_event_link:
        fields["event_stem"], fields["event_sequence"] = None, None
    try:
        FC.require_campaign(campaign_id)
        c = FS.update_campaign(campaign_id, fields)
    except FC.FairError as e:
        _fair_error(e)
    except FS.FairStoreError as e:
        raise HTTPException(409 if e.code == "campaign_archived" else 422, {"error_code": e.code, "message": e.message})
    ctx = repo.current()
    return ACC.redact_fair_campaign(FC.campaign_summary(c, getattr(ctx, "fairs", None) if ctx is not None else None),
                                     accuracy_gate())


@app.post("/api/fairs/campaigns/{campaign_id}/archive", response_model=S.FairCampaignOut)
def fairs_campaign_archive(campaign_id: int, repo: ContextRepository = Depends(get_repo)):
    try:
        FC.require_campaign(campaign_id)
        c = FS.archive_campaign(campaign_id)
    except FC.FairError as e:
        _fair_error(e)
    except FS.FairStoreError as e:
        raise HTTPException(409, {"error_code": e.code, "message": e.message})
    ctx = repo.current()
    return ACC.redact_fair_campaign(FC.campaign_summary(c, getattr(ctx, "fairs", None) if ctx is not None else None),
                                     accuracy_gate())


@app.get("/api/fairs/campaigns/{campaign_id}/customers", response_model=S.FairCampaignCustomers)
def fairs_campaign_customers(campaign_id: int, stage: str | None = None, due: bool = False,
                             segment: str | None = None, q: str | None = None, limit: int = 100,
                             repo: ContextRepository = Depends(get_repo),
                             c360: C360.Customer360Repository = Depends(get_c360)):
    try:
        c = FC.require_campaign(campaign_id)
    except FC.FairError as e:
        _fair_error(e)
    ctx, snap = _fair_ctx_snap(repo, c360)
    gate = accuracy_gate()
    # M8-ACCURACY: filtering by an analytic segment would reveal a withheld classification → ignored while withheld
    seg = segment if (segment in FC.SEGMENT_KEYS and gate.displayable("fairs.segments")) else None
    res = FC.campaign_customers(c, ctx, snap, stage=stage if stage in FS.STAGE_LABELS else None, due=due,
                                segment=seg, q=q, limit=limit)
    res = {**res, "items": [ACC.redact_fair_member(it, gate) for it in res["items"]]}
    return S.FairCampaignCustomers(campaign_id=campaign_id, **res)


@app.post("/api/fairs/campaigns/{campaign_id}/customers", response_model=S.FairAddResult)
def fairs_campaign_add(campaign_id: int, body: S.FairCustomersAdd, repo: ContextRepository = Depends(get_repo),
                       c360: C360.Customer360Repository = Depends(get_c360)):
    try:
        FC.require_campaign(campaign_id)
    except FC.FairError as e:
        _fair_error(e)
    _, snap = _fair_ctx_snap(repo, c360)
    try:
        return S.FairAddResult(**FC.add_customers(campaign_id, list(body.customer_codes), snap, body.stage))
    except FC.FairError as e:
        _fair_error(e)


@app.put("/api/fairs/campaigns/{campaign_id}/customers", response_model=S.FairCampaignCustomer)
def fairs_campaign_update_customer(campaign_id: int, body: S.FairCustomerUpdate,
                                   repo: ContextRepository = Depends(get_repo),
                                   c360: C360.Customer360Repository = Depends(get_c360)):
    try:
        c = FC.require_campaign(campaign_id)
    except FC.FairError as e:
        _fair_error(e)
    ctx, snap = _fair_ctx_snap(repo, c360)
    fields = {}
    if body.stage is not None:
        fields["stage"] = body.stage
    if body.next_action_date is not None:
        fields["next_action_date"] = body.next_action_date.isoformat()
    if body.clear_next_action:
        fields["next_action_date"] = None
    if body.note is not None:
        fields["note"] = body.note
    if body.clear_note:
        fields["note"] = None
    try:
        FC.update_customer(campaign_id, body.customer_code, snap, **fields)
    except FC.FairError as e:
        _fair_error(e)
    rows = FC.campaign_customers(c, ctx, snap, limit=500)["items"]
    row = next((r for r in rows if r["customer_code"] == body.customer_code), None)
    if row is None:
        raise HTTPException(404, "Müşteri bulunamadı.")
    return ACC.redact_fair_member(row, accuracy_gate())


@app.get("/api/fairs/candidates", response_model=S.FairCandidates)
def fairs_candidates(segment: str | None = None, q: str | None = None, campaign_id: int | None = None,
                     fuar_first: bool = False, limit: int = 50, repo: ContextRepository = Depends(get_repo),
                     c360: C360.Customer360Repository = Depends(get_c360)):
    ctx, snap = _fair_ctx_snap(repo, c360)
    return S.FairCandidates(**FC.candidates(ctx, snap, segment=segment, q=q, campaign_id=campaign_id,
                                            fuar_first=fuar_first, limit=limit))


# ---------------- M7-5 lightweight CRM (local SQLite + Customer 360 identity; never calls the AI provider) ----------
# Append-only structured interactions on the existing `memory` table and one current follow-up per customer.
# Coverage is always "incomplete" (D-024); nothing here counts or ranks people. Mutations need X-StockAgent: 1.
def _crm_snap(c360):
    try:
        return c360.snapshot()
    except C360.Customer360NotReady as e:
        raise HTTPException(503, {"state": e.state, "message": "Müşteri verisi hazırlanıyor…"})
    except C360.Customer360Error as e:
        raise HTTPException(503, {"state": "error", "error_code": e.code, "message": e.message})


def _crm_error(e: "CRM.CrmError"):
    raise HTTPException(e.status, {"error_code": e.code, "message": e.message})


@app.get("/api/crm/summary", response_model=S.CrmSummary)
def crm_summary(c360: C360.Customer360Repository = Depends(get_c360)):
    return S.CrmSummary(**ACC.redact_crm_summary(CRM.summary(_crm_snap(c360)), accuracy_gate()))


@app.get("/api/crm/followups", response_model=S.CrmFollowupList)
def crm_followups(mode: Literal["due", "today", "upcoming", "all"] = "due", days: int = 7, limit: int = 100,
                  c360: C360.Customer360Repository = Depends(get_c360)):
    body = CRM.followup_list(_crm_snap(c360), mode, days=days, limit=limit)
    gate = accuracy_gate()
    return S.CrmFollowupList(**{**body, "items": [ACC.redact_directory(it, gate) for it in body["items"]]})


@app.get("/api/crm/recent", response_model=S.CrmRecent)
def crm_recent(limit: int = 50, c360: C360.Customer360Repository = Depends(get_c360)):
    return S.CrmRecent(**CRM.recent(_crm_snap(c360), limit))


@app.get("/api/crm/customers/{customer_code}", response_model=S.CrmCustomer)
def crm_customer(customer_code: str, c360: C360.Customer360Repository = Depends(get_c360)):
    snap = _crm_snap(c360)
    try:
        CRM.require_customer(snap, customer_code)
    except CRM.CrmError as e:
        _crm_error(e)
    return ACC.redact_directory(CRM.customer_view(snap, customer_code), accuracy_gate())


@app.post("/api/crm/customers/{customer_code}/interactions", response_model=S.CrmCustomer)
def crm_add_interaction(customer_code: str, body: S.CrmInteractionCreate,
                        c360: C360.Customer360Repository = Depends(get_c360)):
    snap = _crm_snap(c360)
    data = body.model_dump()
    data["occurred_on"] = body.occurred_on.isoformat()
    if body.followup is not None:
        data["followup"] = {**body.followup.model_dump(), "due_date": body.followup.due_date.isoformat()}
    try:
        return ACC.redact_directory(CRM.add_interaction(snap, customer_code, data), accuracy_gate())
    except CRM.CrmError as e:
        _crm_error(e)


@app.put("/api/crm/customers/{customer_code}/followup", response_model=S.CrmCustomer)
def crm_set_followup(customer_code: str, body: S.CrmFollowupUpdate,
                     c360: C360.Customer360Repository = Depends(get_c360)):
    snap = _crm_snap(c360)
    data = body.model_dump()
    if body.due_date is not None:
        data["due_date"] = body.due_date.isoformat()
    try:
        return ACC.redact_directory(CRM.set_followup(snap, customer_code, data), accuracy_gate())
    except CRM.CrmError as e:
        _crm_error(e)


# ---------------- M7-6 Yönetim (owner view; read-only; never calls the AI provider) ----------------
# Owner sources are optional explicit-path inputs with their own lazy cache and an explicit, guarded reload that never
# touches the core context; the core refresh never reloads them. The operational summary reuses live state only.
def _owner_live(repo, c360):
    ctx = repo.current()
    snap, c3_state = None, "building"
    try:
        snap, c3_state = c360.snapshot(), "ready"
    except C360.Customer360NotReady as e:
        c3_state = e.state
    except C360.Customer360Error:
        c3_state = "error"
    same = ctx is not None and snap is not None and c360.signature_for(ctx) == snap.signature
    qcounts = None
    if same:
        try:
            qcounts = SQ.build_queue(ctx, snap, upsert=False, limit=1)["filter_counts"]
        except SQ.QueueUnavailable:
            qcounts = None
    crm = CRM.summary(snap) if snap is not None else None
    try:
        campaigns = FS.list_campaigns()
        sqlite_ok = True
    except Exception:
        campaigns, sqlite_ok = [], False
    ops = OV.operations(ctx if same else None, snap if same else None, qcounts, crm, campaigns)
    if ctx is not None:
        core = {"context_state": "ready", "sales_file": ctx["source_files"].get("sales"),
                "stock_file": ctx["source_files"].get("stock"), "aging_file": ctx["source_files"].get("aging"),
                "as_of_data_date": ctx.get("as_of_data_date")}
    else:
        core = {"context_state": "error" if getattr(repo, "last_error", None) else "building"}
    stores = {"customer360_state": c3_state, "customer360_customers": len(snap.customers) if snap is not None else None,
              "crm_coverage_status": CRM.COVERAGE_STATUS, "crm_coverage_label": CRM.COVERAGE_LABEL,
              "fair_campaigns": len(campaigns), "sqlite_available": sqlite_ok}
    hedef = {"configured": bool(c360.hedef_path), "file": os.path.basename(c360.hedef_path or "") or None,
             "status": snap.hedef_status if snap is not None else ("not_configured" if not c360.hedef_path
                                                                  else c3_state)}
    return ctx if same else None, ops, crm, core, stores, hedef


def _owner_system(owner, entries, core, stores, hedef):
    return {"core": core, "hedef": hedef,
            "owner_sources": [OV.source_head(owner, k, entries[k]) for k in OV.SOURCES],
            "stores": stores,
            "blocked": [{"key": "detailed_collections", "label": "Detaylı Tahsilat",
                         "status_label": "Sağlanmadı / M6-4 bloke"}]}


@app.get("/api/owner/overview", response_model=S.OwnerOverview)
def owner_overview(repo: ContextRepository = Depends(get_repo), c360: C360.Customer360Repository = Depends(get_c360),
                   owner: OV.OwnerRepository = Depends(get_owner)):
    owner.ensure_loaded()
    entries = owner.snapshot()
    today = dt.date.today()
    gate = accuracy_gate()
    ctx, ops, crm, core, stores, hedef = _owner_live(repo, c360)
    # M8-ACCURACY: owner financial sections and analytic counts are withheld field by field; source / system status
    # and the known-issues list stay visible.
    return S.OwnerOverview(
        today=today.isoformat(), audience_note=OV.OWNER_AUDIENCE_NOTE,
        reload={"inflight": owner.inflight, "generation": owner.generation},
        operations=ACC.redact_operations(ops, gate),
        kredi=ACC.redact_owner_section(OV.kredi_section(owner, entries["kredi"], today), "owner.kredi", gate),
        gider_gelir=ACC.redact_owner_section(OV.gider_gelir_section(owner, entries["gider_gelir"]), "owner.gider_gelir",
                                             gate),
        butce=ACC.redact_owner_section(OV.butce_section(owner, entries["butce"]), "owner.butce", gate),
        system=_owner_system(owner, entries, core, stores, hedef),
        known_issues=ACC.redact_known_issues(OV.known_issues(getattr(repo.current(), "data_quality", None), ops, crm),
                                             gate) + ACC.accuracy_known_issues())


@app.get("/api/owner/status", response_model=S.OwnerStatus)
def owner_status(repo: ContextRepository = Depends(get_repo), c360: C360.Customer360Repository = Depends(get_c360),
                 owner: OV.OwnerRepository = Depends(get_owner)):
    owner.ensure_loaded()
    entries = owner.snapshot()
    _, ops, crm, core, stores, hedef = _owner_live(repo, c360)
    return S.OwnerStatus(audience_note=OV.OWNER_AUDIENCE_NOTE,
                         reload={"inflight": owner.inflight, "generation": owner.generation},
                         system=_owner_system(owner, entries, core, stores, hedef),
                         known_issues=ACC.redact_known_issues(
                             OV.known_issues(getattr(repo.current(), "data_quality", None), ops, crm), accuracy_gate())
                         + ACC.accuracy_known_issues())


@app.post("/api/owner/reload", response_model=S.OwnerReloadResponse)
def owner_reload(owner: OV.OwnerRepository = Depends(get_owner)):
    """Re-read ONLY the explicitly configured owner workbooks (never the core sales / stock / Cari sources, never AI).
    Ignored while a load is already in flight."""
    r = owner.reload()
    entries = owner.snapshot()
    return S.OwnerReloadResponse(started=r["started"], reload={"inflight": owner.inflight, "generation": owner.generation},
                                 sources=[OV.source_head(owner, k, entries[k]) for k in OV.SOURCES])


# ---------------- draft message (copy-only; nothing is ever sent) ----------------
def _queue_only_customer(err, code):
    """M7-5: a customer that reaches Günlük Satış only through local tasks (a due CRM or fair follow-up) is not in the
    M5 entity index. For an explicit draft, use the minimum identity from the Customer 360 snapshot: code, display
    name and the trusted master KAPALI flag (so the collection-only refusal still applies). Never CRM or fair data."""
    if getattr(err, "status_code", None) != 404:
        raise err
    try:   # resolved lazily so callers without a Customer 360 repository keep the M5 behaviour (404)
        snap = app.dependency_overrides.get(get_c360, get_c360)().snapshot()
    except Exception:
        raise err
    base = snap.customers.get(code)
    if base is None:
        raise err
    return {"customer_code": code, "name": base["display_name"],
            "closed": True if snap.master_closed(code) is True else None, "history": []}


@app.post("/api/agent/draft-message", response_model=S.DraftResponse)
def draft_message(req: S.DraftRequest, repo: ContextRepository = Depends(get_repo),
                  agent: BusinessActionAgent = Depends(get_agent)):
    """channel × purpose draft (D-018, D-030). KAPALI customers: collection only (refused before any AI call).
    Returns text to review and copy; there is no send capability anywhere in the app."""
    try:
        cust = _customer_facts(repo, req.customer_code)
    except HTTPException as e:
        cust = _queue_only_customer(e, req.customer_code)
    cust = {**cust, "history": [m for m in cust.get("history") or [] if m.get("kind") != "crm_contact"]}
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
