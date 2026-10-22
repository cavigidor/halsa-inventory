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


def _bounded_wait(raw: str | None, default: float = 60.0) -> float:
    try:
        v = float(raw) if raw not in (None, "") else default
    except ValueError:
        v = default
    return min(max(v, 0.0), 300.0)


# M8-ACCURACY-2B: how long GET / waits for an in-progress dashboard re-publication after a restart (never unbounded)
REPUBLISH_WAIT_SECONDS = _bounded_wait(os.environ.get("DASHBOARD_REPUBLISH_WAIT_SECONDS"))
GUARD_HEADER, GUARD_VALUE = "x-stockagent", "1"
# M7-1 Customer 360: Hedef only from an explicitly configured file (no automatic Feb/June/other selection).
HEDEF_PATH = os.environ.get("HEDEF_PATH") or None
HEDEF_SNAPSHOT_DATE = os.environ.get("HEDEF_SNAPSHOT_DATE") or None
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# M8-CP1 (D-060): no interactive API documentation, schema dump or debug UI is served (/docs, /redoc, /openapi.json
# answer 404). The schema is still available to code and tests through app.openapi().
app = FastAPI(title="AI Aksiyon Merkezi API", version="0.4", docs_url=None, redoc_url=None, openapi_url=None)


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


# ---- M8-CP1 (D-060): viewing needs no login; administration needs the owner session ----
# Registered AFTER the accuracy middleware, so it runs FIRST: an anonymous request for an owner-only route is refused
# before the accuracy gate, any endpoint, the provider or SQLite is reached. Every matched route must be classified in
# services/access.ACCESS (unclassified → 403 for everyone). The accuracy gate is unchanged and applies to the owner
# exactly as to a viewer: logging in never reveals an unverified metric. X-StockAgent stays as an extra local guard on
# mutations but is NOT authentication.
from starlette.concurrency import run_in_threadpool
from services import access as AX
from services import owner_auth as AUTH

AUTH_REQUIRED_TEXT = "Bu işlem yalnızca yönetici girişiyle yapılabilir. Görüntüleme için giriş gerekmez."
AUTH_NOT_CONFIGURED_TEXT = "Yönetici girişi bu sunucuda yapılandırılmadı; yönetim işlemleri kapalı."
# D-061: a financial-viewer session is authenticated but never an owner
OWNER_REQUIRED_TEXT = ("Bu işlem yalnızca yönetici girişiyle yapılabilir. Finans görüntüleme şifresi yalnızca "
                       "finans bölümlerini salt okunur açar.")
FINANCE_REQUIRED_TEXT = "Yönetim (finans) bölümü için finans görüntüleme şifresi gerekir."
FINANCE_NOT_CONFIGURED_TEXT = "Finans görüntüleme şifresi bu sunucuda tanımlanmadı; bölüm yalnızca yönetici girişiyle açılır."


def _deny(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"error_code": code, "message": message}},
                        headers=headers)


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


def _owner(request: Request):
    """The OWNER session the access middleware resolved for this request (None = viewer or financial viewer)."""
    return getattr(request.state, "owner", None)


def _session(request: Request):
    """Any session (owner or financial viewer) the access middleware resolved for this request, or None."""
    return getattr(request.state, "session", None)


def _security_headers(request: Request, resp, lv: str | None):
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")                               # no framing → no click-jacking of owner
    h.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.url.scheme == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if lv in (AX.FINANCE_READ, AX.OWNER_READ, AX.OWNER_WRITE, AX.AUTH) or _session(request) is not None:
        h["Cache-Control"] = "no-store"                                   # owner / finance responses never cached
    return resp


@app.middleware("http")
async def _access_control(request: Request, call_next):
    method = "GET" if request.method == "HEAD" else request.method
    tpl = _route_template(request.scope)
    request.state.owner = request.state.session = None
    if tpl is None or method == "OPTIONS":
        return _security_headers(request, await call_next(request), None)     # router answers 404 / 405
    lv = AX.level(method, tpl)
    if lv is None:
        return _security_headers(request, _deny(403, "access_unclassified",
                                                "Bu uç erişim kaydında sınıflandırılmadı."), None)
    try:
        sess = await run_in_threadpool(AUTH.request_session, request)
    except Exception as e:                                                # state store unreadable → anonymous
        log.warning("owner session check failed (%s)", type(e).__name__)
        sess = None
    request.state.session = sess
    request.state.owner = sess if (sess is not None and sess.is_owner) else None
    need_owner = lv in (AX.OWNER_READ, AX.OWNER_WRITE) or (
        lv == AX.VIEWER and AX.owner_query_used(method, tpl, request.query_params))
    if lv == AX.FINANCE_READ and (sess is None or not sess.can_view_finance):
        fin = AUTH.configured_hash(AUTH.ROLE_FINANCE) is not None
        return _security_headers(request, _deny(
            401, "finance_auth_required" if fin else "finance_not_configured",
            FINANCE_REQUIRED_TEXT if fin else FINANCE_NOT_CONFIGURED_TEXT), lv)
    if need_owner:
        if sess is None:
            configured = AUTH.configured_hash() is not None
            return _security_headers(request, _deny(
                401, "auth_required" if configured else "admin_not_configured",
                AUTH_REQUIRED_TEXT if configured else AUTH_NOT_CONFIGURED_TEXT), lv)
        if not sess.is_owner:                                             # financial viewer: never an owner
            return _security_headers(request, _deny(403, "owner_required", OWNER_REQUIRED_TEXT), lv)
        if method in AX.MUTATING:
            if not AUTH.origin_ok(request.headers.get("origin"), request.url.scheme, request.headers.get("host")):
                return _security_headers(request, _deny(403, "origin_rejected",
                                                        "İstek bu sitenin sayfasından gelmedi."), lv)
            if not sess.csrf_ok(request.headers.get(AUTH.CSRF_HEADER)):
                return _security_headers(request, _deny(403, "csrf_failed",
                                                        "Güvenlik doğrulaması başarısız; sayfayı yenileyin."), lv)
    return _security_headers(request, await call_next(request), lv)


# ---- M7-5: CRM validation errors never echo the submitted text (notes, outcomes, owner labels) ----
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    # M8-CP1: /api/auth/ is included so a rejected login body (e.g. an over-long password) is never echoed back
    if not request.url.path.startswith(("/api/crm/", "/api/auth/")):
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
                                       generation_store=app.state.generations,
                                       on_generation_accepted=_republish_dashboard)
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
    """Same-origin dashboard (D-020): open http://127.0.0.1:8000/ instead of the file.

    M8-ACCURACY-2 (fail closed, review of PR #28): the on-disk HTML is served ONLY when its exact bytes, its generation
    marker and its publication record match the currently ACCEPTED (re-validated) manifest, and the current gate
    authorizes every metric it embeds. Anything else — no accepted generation, a pending restart candidate that does
    not re-validate, other bytes, a missing marker — gets a server-generated neutral page; the file is never returned.
    Claims inside the HTML (e.g. "displays no metric") are never trusted on their own."""
    path = DASHBOARD_PATH
    store = getattr(app.state, "generations", None)
    if store is not None and store.pending is not None and store.current() is None:
        _await_revalidation()                         # restart: re-derive the candidate (and re-publish) first
    if store is not None:
        cur = store.current()
        # M8-ACCURACY-2B: a valid, re-validated generation whose dashboard is being re-published (e.g. by the warm-up
        # thread) — wait for it, bounded, instead of answering a needless 503. Nothing is served meanwhile; the exact
        # byte / generation checks below still decide.
        if (cur is not None and cur.dashboard_sha256 is None and store.dashboard_pending == cur.core_id
                and not store.wait_dashboard(REPUBLISH_WAIT_SECONDS)):
            return _dashboard_unavailable("publication_pending")
    with RP.PUBLISH_LOCK:                              # bytes and accepted generation captured as ONE step
        m = store.current() if store is not None else None
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except FileNotFoundError:
            body = None
        except OSError:
            return _dashboard_unavailable("unreadable")
    if body is None:
        return HTMLResponse(status_code=404, content=_neutral_page(
            "Pano henüz yayımlanmadı.",
            "Verileri yenileyerek panoyu oluşturun: <code>python -m services.refresh</code> veya panodaki Yenile."))
    ok, reason, gen_id = _dashboard_authorized(body, m)
    if not ok:
        log.info("dashboard withheld: %s", reason)
        return _dashboard_unavailable(reason)
    return HTMLResponse(content=body, headers={"X-Halsa-Generation": gen_id})


def _republish_dashboard(manifest, paths, frames):
    """After a restart the re-derived generation is accepted, but the dashboard file on disk is from before the
    restart (its recorded digest came from the untrusted saved manifest). Build a NEW dashboard from the re-derived
    evidence and the same frozen bytes, then publish it, its manifest and its digest as ONE step. Only bytes this
    process published are ever served (`_dashboard_authorized`)."""
    import dashboard_builder as db
    store = getattr(app.state, "generations", None)
    if store is None:
        return
    dash, folder = DASHBOARD_PATH, DATA_FOLDER
    staged = dash + ".restore.new"
    mstaged = GEN.manifest_path(dash) + ".restore.new"
    published = False
    try:
        db.build(folder, staged, sources=paths, frames=frames, manifest=manifest)
        digest = GEN.sha256_file(staged)
        with RP.PUBLISH_LOCK:
            cur = store.current()
            if cur is None or cur.core_id != manifest.core_id:   # a refresh published meanwhile: keep that one
                return
            GEN.write_manifest(mstaged, GEN.Manifest(cur.created_at, cur.core, (), cur.joins, cur.selection_origin,
                                                     cur.policy_version, digest))
            os.replace(staged, dash)
            os.replace(mstaged, GEN.manifest_path(dash))
            published = store.set_dashboard(cur.core_id, digest) is not None
        log.info("dashboard re-published after restart (generation %s)", manifest.core_id[:12])
    except Exception as e:
        log.warning("dashboard re-publication failed (%s); GET / stays withheld", type(e).__name__)
    finally:
        if not published:                                   # failed or superseded: release any waiting GET /
            store.dashboard_failed(manifest.core_id)
        for f in (staged, mstaged):
            try:
                os.remove(f)
            except OSError:
                pass


def _await_revalidation():
    """A restart leaves the published manifest as a pending candidate; the context build re-derives it from the
    original bytes (services/entity). Wait for that (the warm-up thread may already be doing it). Never raises."""
    try:
        app.dependency_overrides.get(get_repo, get_repo)().context()
    except Exception as e:                              # recorded on the repository; the page stays withheld
        log.info("dashboard: revalidation failed (%s)", type(e).__name__)


def _neutral_page(title: str, detail: str) -> str:
    """Server-generated status page: fixed Turkish text, no file content, no values, no paths."""
    return ("<!doctype html><html lang=\"tr\"><head><meta charset=\"utf-8\"><title>Pano durumu</title>"
            "</head><body><h1>" + title + "</h1><p>" + detail + "</p><p>Sahip yönergesi: doğrulanmış kanıt yoksa "
            "iş sonucu gösterilmez.</p></body></html>")


_UNAVAILABLE_TEXT = {
    "no_accepted_generation": "Yayımlanmış ve doğrulanmış bir veri kuşağı yok (ör. kaynak değişti, yeniden doğrulama "
                              "başarısız oldu). Verileri yenileyin.",
    "generation_mismatch": "Diskteki pano, doğrulanmış veri kuşağına ait değil. Verileri yenileyin.",
    "bytes_mismatch": "Diskteki pano, yayımlandığı halinden farklı. Verileri yenileyin.",
    "policy_marker_missing": "Bu pano doğrulama kuralları uygulanmadan oluşturulmuş. Verileri yenileyin.",
    "displayed_marker_missing": "Bu pano doğrulama kuralları uygulanmadan oluşturulmuş. Verileri yenileyin.",
    "not_authorized": "Panodaki bazı sonuçlar artık doğrulanmış sayılmıyor (ör. kaynak tarihi eskidi). Verileri "
                      "yenileyin.",
    "publication_pending": "Pano yeniden doğrulandı ve yeniden yayımlanıyor; birkaç saniye sonra sayfayı yenileyin.",
}


def _dashboard_unavailable(reason: str):
    return HTMLResponse(status_code=503, headers={"X-Halsa-Dashboard": reason}, content=_neutral_page(
        "Pano şu anda gösterilemiyor.",
        _UNAVAILABLE_TEXT.get(reason, "Pano doğrulanamadı; doğrulanmamış sonuçlar gösterilmiyor. Verileri yenileyin "
                                      "veya sunucunun hazırlanmasını bekleyin.")))


def _dashboard_authorized(body: bytes, m) -> tuple[bool, str, str | None]:
    """May these exact HTML bytes (captured under the publish lock together with `m`) be served now?

    Every condition is required, in this order (fail closed):
    1. an ACCEPTED, re-validated generation exists (`m`; a pending restart candidate is not accepted);
    2. the page's policy marker is the current policy (replaces the old pre-lock `_gated_dashboard` read);
    3. the page's generation marker equals `m.core_id`;
    4. the SHA-256 of the exact bytes equals `m.dashboard_sha256` (recorded at publication) — so neither the content
       nor the `stockagent-displayed` marker can have been edited;
    5. the displayed marker is present, and the CURRENT gate, bound to `m`'s evidence, authorizes every metric in it
       (today's freshness, current register). An empty list passes step 5 only because steps 1–4 proved the page is
       the published one."""
    if m is None or not m.core:
        return False, "no_accepted_generation", None
    if f'<meta name="stockagent-accuracy" content="{ACC.POLICY_VERSION}">'.encode() not in body[:4096]:
        return False, "policy_marker_missing", None
    gen, displayed = GEN.dashboard_markers(body)
    if gen is None or gen != m.core_id:
        return False, "generation_mismatch", None
    if not m.dashboard_sha256 or GEN.sha256_bytes(body) != m.dashboard_sha256:
        return False, "bytes_mismatch", None
    if displayed is None:
        return False, "displayed_marker_missing", None
    try:
        gate = ACC.current_gate(snapshot=m.core_snapshot(), expected_generation=m.generation_id)
        refused = [k for k in displayed if not gate.decide(k).displayable]
    except Exception:
        return False, "authorization_error", None
    if refused:
        return False, "not_authorized", None
    return True, "ok", m.generation_id


HEALTH_ERROR_VIEWER_TEXT = "Veriler hazırlanamadı. Ayrıntı yalnızca yöneticiye gösterilir."


@app.get("/api/health", response_model=S.Health)
def health(request: Request, agent: BusinessActionAgent = Depends(get_agent),
           repo: ContextRepository = Depends(get_repo)):
    p = agent.provider
    ready = repo.is_ready()
    asof = (repo.context() or {}).get("as_of_data_date") if ready else None   # no build: already cached
    err = getattr(repo, "last_error", None)
    if err and _owner(request) is None:      # M8-CP1: exception text can name local files → owner only
        err = HEALTH_ERROR_VIEWER_TEXT
    return S.Health(ai_available=agent.available, ai_provider=p.name,
                    ai_model=getattr(p, "model", None),
                    ai_reason=(None if agent.available else getattr(p, "reason", None)),
                    macro_available=False, data_folder=os.path.basename(os.path.normpath(DATA_FOLDER)),   # name only (M5-5: no local paths)
                    context_ready=ready, context_error=err,
                    as_of_data_date=asof)


# ---------------- M8-CP1 password logins (D-060, D-061) ----------------
# Two roles, two passwords (scrypt hashes in the private .env), no accounts. Viewing the normal dashboard needs neither.
#   owner   → administration (+ every owner-only read, + financial viewing)
#   finance → read-only financial viewing (Yönetim financial projection) only
_LOGIN_LOCK = threading.Lock()      # one verification at a time: throttle check + verify + record are atomic
_ROLE_LOG = {AUTH.ROLE_OWNER: "owner", AUTH.ROLE_FINANCE: "finance viewer"}


def _session_out(s, *, csrf: str | None = None, expires: float | None = None) -> S.OwnerSession:
    return S.OwnerSession(configured=AUTH.configured_hash() is not None,
                          finance_configured=AUTH.configured_hash(AUTH.ROLE_FINANCE) is not None,
                          authenticated=s is not None, role=s.role if s is not None else None,
                          csrf_token=csrf or (s.csrf_token if s is not None else None),
                          expires_at=_iso(expires if expires is not None else s.expires_at) if s is not None else None,
                          idle_expires_at=_iso(s.idle_expires_at) if s is not None else None)


@app.get("/api/auth/session", response_model=S.OwnerSession)
def auth_session(request: Request):
    """Who is this browser (viewer / financial viewer / owner)? The CSRF token is returned only to the session's own
    (same-origin) page; other sites cannot read it (no CORS)."""
    return _session_out(_session(request))


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


_NOT_CONFIGURED = {AUTH.ROLE_OWNER: ("admin_not_configured", AUTH_NOT_CONFIGURED_TEXT),
                   AUTH.ROLE_FINANCE: ("finance_not_configured", FINANCE_NOT_CONFIGURED_TEXT)}


def _password_login(body: "S.OwnerLogin", request: Request, role: str):
    """Shared, role-explicit password login. The role comes from the ENDPOINT, never from the request body, so the
    financial password can only ever create a financial-viewer session."""
    enc = AUTH.configured_hash(role)
    if enc is None:
        code, text = _NOT_CONFIGURED[role]
        return _deny(503, code, text)
    scheme, host = request.url.scheme, request.headers.get("host")
    if not AUTH.secure_transport(scheme, host, _client_host(request)):
        return _deny(403, "https_required", "Şifre yalnızca güvenli (HTTPS) bağlantıyla girilebilir.")
    if not AUTH.origin_ok(request.headers.get("origin"), scheme, host):
        return _deny(403, "origin_rejected", "İstek bu sitenin sayfasından gelmedi.")
    current = _session(request)
    if role == AUTH.ROLE_FINANCE and current is not None and current.is_owner:
        return JSONResponse(content=_session_out(current).model_dump())   # the owner already sees finance; no downgrade
    key = AUTH.client_key(_client_host(request))
    with _LOGIN_LOCK:
        wait = AUTH.throttle_wait(key, role)
        if wait:
            return _deny(429, "login_throttled", "Çok fazla hatalı deneme. Lütfen biraz sonra tekrar deneyin.",
                         headers={"Retry-After": str(wait)})
        if not AUTH.verify_password(body.password, enc):
            AUTH.record_failure(key, role)
            log.info("%s login rejected", _ROLE_LOG[role])      # no password, client or count in the log
            return _deny(401, "invalid_credentials", "Şifre hatalı.")
        AUTH.clear_failures(key, role)
        if current is not None:                                  # session rotation on every login
            AUTH.revoke(current.token_hash)
        token, csrf, exp = AUTH.create_session(enc, role)
    log.info("%s login accepted", _ROLE_LOG[role])
    s = AUTH.Session(AUTH._sha(token), csrf, 0.0, exp, exp, role)
    resp = JSONResponse(content=_session_out(s, csrf=csrf, expires=exp).model_dump(exclude={"idle_expires_at"}))
    resp.set_cookie(AUTH.COOKIE_NAME, token, max_age=AUTH.session_absolute_seconds(), path="/", httponly=True,
                    samesite="strict", secure=AUTH.cookie_secure(scheme))
    return resp


@app.post("/api/auth/login", response_model=S.OwnerSession)
def auth_login(body: S.OwnerLogin, request: Request):
    """Owner (administration) login."""
    return _password_login(body, request, AUTH.ROLE_OWNER)


@app.post("/api/auth/finance-login", response_model=S.OwnerSession)
def auth_finance_login(body: S.OwnerLogin, request: Request):
    """Financial-viewer login: read-only Yönetim financial sections only (D-061)."""
    return _password_login(body, request, AUTH.ROLE_FINANCE)


def _clear_cookie(resp, request: Request):
    resp.delete_cookie(AUTH.COOKIE_NAME, path="/", httponly=True, samesite="strict",
                       secure=AUTH.cookie_secure(request.url.scheme))
    return resp


@app.post("/api/auth/logout", response_model=S.OwnerSession)
def auth_logout(request: Request):
    """Ends THIS browser's session (owner or financial viewer)."""
    s = _session(request)
    if s is not None:
        if not (AUTH.origin_ok(request.headers.get("origin"), request.url.scheme, request.headers.get("host"))
                and s.csrf_ok(request.headers.get(AUTH.CSRF_HEADER))):
            return _deny(403, "csrf_failed", "Güvenlik doğrulaması başarısız; sayfayı yenileyin.")
        AUTH.revoke(s.token_hash)
    return _clear_cookie(JSONResponse(content=_session_out(None).model_dump()), request)


@app.post("/api/auth/logout-all", response_model=S.OwnerSession)
def auth_logout_all(request: Request):
    """Owner only (middleware): end every session — owner and financial viewers — on every device."""
    AUTH.revoke_all()
    return _clear_cookie(JSONResponse(content=_session_out(None).model_dump()), request)


@app.post("/api/auth/finance-sessions/revoke")
def auth_finance_sessions_revoke(request: Request):
    """Owner only (middleware): end every financial-viewer session (e.g. a lost phone); the owner stays logged in."""
    return {"revoked": AUTH.revoke_all(AUTH.ROLE_FINANCE)}


@app.get("/giris", include_in_schema=False)
def login_page():
    """Fixed Turkish owner-login page: no data, no file content, no values."""
    return HTMLResponse(content=LOGIN_PAGE_HTML)


LOGIN_PAGE_HTML = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Yönetici girişi</title>
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#f4f5f7;color:#1d2433;margin:0;
 display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
main{background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);padding:28px;max-width:400px;width:100%}
h1{font-size:1.4rem;margin:0 0 8px}p{line-height:1.5;margin:0 0 14px}label{display:block;font-weight:600;margin:0 0 6px}
input{width:100%;box-sizing:border-box;font-size:1.1rem;padding:12px;border:1px solid #b8bfcc;border-radius:8px}
button{margin-top:14px;width:100%;font-size:1.1rem;padding:12px;border:0;border-radius:8px;background:#1f5fbf;
 color:#fff;font-weight:600;cursor:pointer}button:disabled{opacity:.6}
.msg{min-height:1.5em;margin-top:12px;font-weight:600}.msg.err{color:#b3261e}.msg.ok{color:#1b7a3a}
a{color:#1f5fbf}.muted{color:#5b6475;font-size:.95rem}
</style></head><body><main>
<h1>Yönetici girişi</h1>
<p class="muted">Panoyu görüntülemek için giriş gerekmez. Bu giriş yalnızca veri yükleme, CRM / takip
değişiklikleri ve yönetim işlemleri içindir.</p>
<form id="f" autocomplete="on"><label for="pw">Yönetici şifresi</label>
<input id="pw" name="password" type="password" autocomplete="current-password" required maxlength="256">
<button id="b" type="submit">Giriş yap</button></form>
<p id="m" class="msg" role="status" aria-live="polite"></p>
<p><a href="/">← Panoya dön</a></p>
</main><script>
(function(){
var f=document.getElementById('f'),pw=document.getElementById('pw'),b=document.getElementById('b'),
    m=document.getElementById('m');
function say(t,k){m.textContent=t;m.className='msg '+(k||'');}
var T={invalid_credentials:'Şifre hatalı.',login_throttled:'Çok fazla hatalı deneme. Lütfen biraz sonra tekrar deneyin.',
 https_required:'Yönetici girişi yalnızca güvenli (HTTPS) bağlantıyla yapılabilir.',
 admin_not_configured:'Yönetici girişi bu sunucuda yapılandırılmadı.',origin_rejected:'Lütfen sayfayı yenileyin.'};
fetch('/api/auth/session',{headers:{'Accept':'application/json'}}).then(function(r){return r.json();}).then(function(s){
 if(s.authenticated){say('Yönetici olarak giriş yapılmış durumda.','ok');}
 else if(!s.configured){say(T.admin_not_configured,'err');b.disabled=true;}
}).catch(function(){});
f.addEventListener('submit',function(e){e.preventDefault();b.disabled=true;say('Kontrol ediliyor…');
 fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json',
  'X-StockAgent':'1'},body:JSON.stringify({password:pw.value})}).then(function(r){
  return r.json().catch(function(){return {};}).then(function(d){return {r:r,d:d};});}).then(function(x){
  pw.value='';
  if(x.r.ok){say('Giriş yapıldı. Panoya yönlendiriliyorsunuz…','ok');location.replace('/');return;}
  var c=(x.d&&x.d.detail&&x.d.detail.error_code)||'';say(T[c]||'Giriş yapılamadı.','err');b.disabled=false;pw.focus();
 }).catch(function(){say('Sunucuya ulaşılamadı.','err');b.disabled=false;});
});
})();
</script></body></html>"""


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
            "dashboard_publication_pending": bool(store is not None and store.dashboard_pending is not None),
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


@app.get("/api/finance/overview", response_model=S.FinanceOverview)
def finance_overview(owner: OV.OwnerRepository = Depends(get_owner)):
    """D-061 financial-viewer projection of Yönetim (financial viewer OR owner; read-only). Exactly the Kredi,
    Gider/Gelir and Bütçe sections, built and gate-redacted exactly like the owner overview, then stripped of source
    diagnostics (OV.finance_section). No operations, system state, known issues, reload state or file names. The
    accuracy gate applies unchanged: today every financial section is withheld. The first call may load the
    configured owner workbooks (the same lazy, read-only load the owner view does); it never reloads them."""
    owner.ensure_loaded()
    entries = owner.snapshot()
    today = dt.date.today()
    gate = accuracy_gate()
    return S.FinanceOverview(
        today=today.isoformat(), audience_note=OV.FINANCE_AUDIENCE_NOTE,
        kredi=OV.finance_section(ACC.redact_owner_section(OV.kredi_section(owner, entries["kredi"], today),
                                                          "owner.kredi", gate)),
        gider_gelir=OV.finance_section(ACC.redact_owner_section(OV.gider_gelir_section(owner, entries["gider_gelir"]),
                                                                "owner.gider_gelir", gate)),
        butce=OV.finance_section(ACC.redact_owner_section(OV.butce_section(owner, entries["butce"]), "owner.butce",
                                                          gate)))


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
