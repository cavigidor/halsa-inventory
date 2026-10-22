"""M8-CP1 (D-060): simplified access — viewing needs no login; administration needs the owner session.

Every HTTP test here resolves the owner session FOR REAL (``production_auth``): cookie → SQLite → CSRF. Synthetic data
only; the password is synthetic and exists only in this file. The preserved algorithms stay behind the test-only
accuracy harness (as in the rest of the suite) so viewer reads return real synthetic data; the tests that check the
accuracy gate itself also use ``production_gate``.
"""
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as APP
from services import access as AX
from services import accuracy as ACC
from services import context as CTX
from services import customer360 as C360
from services import owner_auth as AUTH
from services import owner_view as OV
from services import store as ST
from services.agent import BusinessActionAgent
from services.ai.mock_provider import MockProvider
from services.entity import ContextRepository
from tests.fixtures import fair_synth as FX
from tests.fixtures import owner_synth as OS

pytestmark = pytest.mark.production_auth

PASSWORD = "sentetik-yonetici-parolasi-42"         # synthetic test value
HASH = AUTH.hash_password(PASSWORD, n=2 ** 14)
FIN_PASSWORD = "sentetik-finans-parolasi-77"       # synthetic test value (D-061 financial viewer)
FIN_HASH = AUTH.hash_password(FIN_PASSWORD, n=2 ** 14)
CANARY = "/Users/sentetik/gizli/klasor/satis.xlsx"


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("cp1")
    data = FX.write_all(str(root / "data"))
    ctx = CTX.build_context(data, inflation_rate=0.40)
    owner_dir = Path(root / "owner")
    env = {"KREDI_PATH": str(OS.write_kredi(owner_dir, dt.date.today())),
           "GIDER_GELIR_PATH": str(OS.write_gider_gelir(owner_dir)), "BUTCE_PATH": str(OS.write_butce(owner_dir))}
    return {"data": data, "ctx": ctx, "owner_env": env}


class _Spy:
    def __init__(self):
        self.ai = 0
        self.c360_retry = 0
        self.owner_reload = 0


@pytest.fixture
def app_env(world, tmp_path, monkeypatch):
    """The real app with synthetic sources; returns a factory of clients (anonymous by default)."""
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "state.db"))
    ST.init_db()
    monkeypatch.setenv(AUTH.PASSWORD_ENV, HASH)
    monkeypatch.setenv(AUTH.FINANCE_PASSWORD_ENV, FIN_HASH)
    spy = _Spy()
    provider = MockProvider()
    real = provider.generate_structured

    def counting(*a, **k):
        spy.ai += 1
        return real(*a, **k)
    monkeypatch.setattr(provider, "generate_structured", counting)
    repo = ContextRepository(data_folder=world["data"], context=world["ctx"])
    c360 = C360.Customer360Repository(world["data"], repo, background=False)
    owner = OV.OwnerRepository(OV.config_from_env(world["owner_env"]), background=False)
    real_retry, real_reload = c360.retry, owner.reload

    def retry():
        spy.c360_retry += 1
        return real_retry()

    def reload():
        spy.owner_reload += 1
        return real_reload()
    monkeypatch.setattr(c360, "retry", retry)
    monkeypatch.setattr(owner, "reload", reload)
    agent = BusinessActionAgent(provider)
    APP.app.state.provider, APP.app.state.agent = provider, agent
    APP.app.dependency_overrides[APP.get_repo] = lambda: repo
    APP.app.dependency_overrides[APP.get_c360] = lambda: c360
    APP.app.dependency_overrides[APP.get_owner] = lambda: owner
    APP.app.dependency_overrides[APP.get_agent] = lambda: agent
    clients = []

    def make(base_url="https://testserver", **kw):
        kw.setdefault("headers", {"X-StockAgent": "1"})
        c = TestClient(APP.app, base_url=base_url, **kw)
        c.__enter__()
        clients.append(c)
        return c
    make.spy, make.repo, make.c360, make.owner = spy, repo, c360, owner
    try:
        yield make
    finally:
        for c in clients:
            c.__exit__(None, None, None)
        APP.app.dependency_overrides.clear()


def login(cl, password=PASSWORD):
    r = cl.post("/api/auth/login", json={"password": password})
    assert r.status_code == 200, r.text
    tok = r.json()["csrf_token"]
    cl.headers["X-CSRF-Token"] = tok
    return r


def owner_client(app_env):
    cl = app_env()
    login(cl)
    return cl


def _fill(path, code=None, cid=1):
    code = code or FX.P[1]
    return (path.replace("{customer_code}", code).replace("{product_code}", "SP-002").replace("{event_id}", "x")
            .replace("{campaign_id}", str(cid)).replace("{action_id}", "x"))


def _bodies(path):
    today = dt.date.today().isoformat()
    return {
        "/api/agent/actions/enrich": {"action_ids": ["x"]},
        "/api/agent/draft-message": {"customer_code": FX.P[1], "channel": "whatsapp", "purpose": "sales_followup"},
        "/api/scenario": {"financing_rate": 40},
        "/api/fairs/campaigns": {"name": "Sentetik Fuar", "edition_year": 2027, "start_date": "2027-01-10"},
        "/api/fairs/campaigns/{campaign_id}": {"name": "Yeni ad"},
        "/api/fairs/campaigns/{campaign_id}/customers": {"customer_codes": [FX.P[2]]},
        "/api/crm/customers/{customer_code}/interactions": {
            "occurred_on": FX.AS_OF.isoformat(), "channel": "phone", "purpose": "sales_followup",
            "outcome": "Sentetik görüşme"},
        "/api/crm/customers/{customer_code}/followup": {"purpose": "sales_followup", "due_date": today},
        "/api/auth/login": {"password": "yanlis-parola-0000"},
    }.get(path, {})


def _db_dump(path):
    """Every row of every table except the auth bookkeeping (sessions / login failures)."""
    con = sqlite3.connect(path)
    out = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if t in ("owner_sessions", "owner_login_failures", "sqlite_sequence"):
            continue
        out[t] = sorted(map(repr, con.execute(f"SELECT * FROM {t}").fetchall()))
    con.close()
    return out


VIEWER_GETS = sorted(k for k, v in AX.ACCESS.items() if v == AX.VIEWER and k[0] == "GET")
OWNER_READS = sorted(k for k, v in AX.ACCESS.items() if v == AX.OWNER_READ)
OWNER_WRITES = sorted(k for k, v in AX.ACCESS.items() if v == AX.OWNER_WRITE)


# ---------------------------------------------------------------------------------------------
# A. route register: complete, consistent, fail closed
# ---------------------------------------------------------------------------------------------
def _app_routes():
    seen = set()
    for r in APP.app.routes:
        for m in getattr(r, "methods", None) or ():
            if m not in ("HEAD", "OPTIONS"):
                seen.add((m, r.path))
    return seen


def test_every_route_has_exactly_one_access_level():
    seen = _app_routes()
    assert sorted(seen - set(AX.ACCESS)) == [], "unclassified routes"
    assert sorted(set(AX.ACCESS) - seen) == [], "ACCESS lists routes the app does not have"
    assert set(AX.ACCESS) == set(ACC.ROUTES), "access and accuracy registers must cover the same routes"
    assert set(AX.ACCESS.values()) <= set(AX.LEVELS)


def test_no_state_changing_route_is_viewer(monkeypatch):
    for (method, path), lv in AX.ACCESS.items():
        if method in AX.MUTATING:
            assert lv in (AX.OWNER_WRITE, AX.AUTH), (method, path, lv)
    assert {p for (m, p), lv in AX.ACCESS.items() if lv == AX.AUTH} == {
        "/api/auth/session", "/api/auth/login", "/api/auth/finance-login", "/api/auth/logout"}
    monkeypatch.setattr(AX, "ACCESS", {**AX.ACCESS, ("POST", "/x"): AX.VIEWER})
    assert AX.level("POST", "/x") == AX.OWNER_WRITE                 # a viewer route can never mutate (fail closed)
    assert AX.level("GET", "/nope") is None and AX.level("POST", "/") is None


def test_owner_only_classification_matches_the_agreed_model():
    must_be_owner = {("POST", "/api/context/refresh"), ("POST", "/api/owner/reload"),
                     ("POST", "/api/agent/actions/enrich"), ("POST", "/api/agent/draft-message"),
                     ("POST", "/api/agent/customer/{customer_code}/analyze"),
                     ("POST", "/api/agent/product/{product_code}/analyze"),
                     ("POST", "/api/actions/{action_id}/complete"), ("PUT", "/api/crm/customers/{customer_code}/followup"),
                     ("POST", "/api/crm/customers/{customer_code}/interactions"), ("POST", "/api/fairs/campaigns"),
                     ("GET", "/api/owner/overview"), ("GET", "/api/owner/status"), ("GET", "/api/ai/calls"),
                     ("GET", "/api/context"), ("GET", "/api/accuracy/status")}
    for k in must_be_owner:
        assert AX.ACCESS[k] in (AX.OWNER_READ, AX.OWNER_WRITE), k
    for k in [("GET", "/"), ("GET", "/api/agent/actions"), ("GET", "/api/sales-queue"),
              ("GET", "/api/customer360/{customer_code}"), ("GET", "/api/crm/followups"), ("GET", "/api/fairs/campaigns")]:
        assert AX.ACCESS[k] == AX.VIEWER, k


def test_unclassified_route_is_refused_even_for_the_owner(app_env, monkeypatch):
    cl = owner_client(app_env)
    acc = dict(AX.ACCESS)
    acc.pop(("GET", "/api/crm/recent"))
    monkeypatch.setattr(AX, "ACCESS", acc)
    r = cl.get("/api/crm/recent")
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "access_unclassified"


def test_api_documentation_and_schema_are_not_served(app_env):
    cl = app_env()
    for p in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert cl.get(p).status_code == 404, p
    login(cl)
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert cl.get(p).status_code == 404, p


# ---------------------------------------------------------------------------------------------
# B. anonymous viewer
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("method,path", [k for k in VIEWER_GETS if k[1] not in ("/", "/giris")])
def test_viewer_reads_need_no_login(app_env, method, path):
    cl = app_env()
    r = cl.get(_fill(path))
    assert r.status_code not in (401, 403), (path, r.status_code, r.text[:300])
    assert "auth_required" not in r.text


def test_dashboard_and_login_page_open_without_login(app_env):
    cl = app_env()
    r = cl.get("/")
    assert r.status_code != 401 and "auth_required" not in r.text      # no login wall in front of the dashboard
    g = cl.get("/giris")
    assert g.status_code == 200 and "Yönetici girişi" in g.text and "görüntülemek için giriş gerekmez" in g.text
    assert 'type="password"' in g.text and "autocomplete=\"current-password\"" in g.text


def test_viewer_session_endpoint_reports_viewer(app_env):
    cl = app_env()
    j = cl.get("/api/auth/session").json()
    assert j == {"configured": True, "finance_configured": True, "authenticated": False, "role": None,
                 "csrf_token": None, "expires_at": None, "idle_expires_at": None}


@pytest.mark.parametrize("method,path", OWNER_READS)
def test_owner_only_reads_refuse_viewers_without_data(app_env, method, path):
    cl = app_env()
    r = cl.request(method, _fill(path))
    assert r.status_code == 401
    assert r.json() == {"detail": {"error_code": "auth_required", "message": APP.AUTH_REQUIRED_TEXT}}
    assert app_env.spy.ai == 0 and app_env.spy.owner_reload == 0


@pytest.mark.parametrize("method,path", OWNER_WRITES)
def test_anonymous_writes_are_refused_and_change_nothing(app_env, method, path):
    cl = app_env()
    cl.get("/api/agent/actions")                                     # materialize the published queue first
    before = _db_dump(ST.DB_PATH)
    r = cl.request(method, _fill(path), json=_bodies(path),
                   headers={"X-StockAgent": "1", "X-CSRF-Token": "gecersiz"})   # the old guard header is not identity
    assert r.status_code == 401 and r.json()["detail"]["error_code"] == "auth_required", (path, r.text[:200])
    assert _db_dump(ST.DB_PATH) == before
    assert app_env.spy.ai == 0 and app_env.spy.owner_reload == 0 and app_env.spy.c360_retry == 0


def test_customer360_retry_is_an_owner_action(app_env):
    cl = app_env()
    assert cl.get("/api/customer360/status").status_code == 200
    assert cl.get("/api/customer360/status?retry=false").status_code == 200
    for v in ("true", "1", "yes"):
        r = cl.get(f"/api/customer360/status?retry={v}")
        assert r.status_code == 401 and r.json()["detail"]["error_code"] == "auth_required"
    assert app_env.spy.c360_retry == 0
    login(cl)
    assert cl.get("/api/customer360/status?retry=true").status_code == 200
    assert app_env.spy.c360_retry == 1


def test_read_only_requests_never_change_human_decisions(app_env):
    """A full sweep of every viewer GET (twice, anonymous) leaves every table byte-identical after the first sweep, and
    the first sweep only ever inserts the published deterministic queue (status 'open', event 'generated')."""
    owner = owner_client(app_env)
    code = FX.P[1]
    assert owner.post(f"/api/crm/customers/{code}/interactions", json=_bodies(
        "/api/crm/customers/{customer_code}/interactions")).status_code == 200
    camp = owner.post("/api/fairs/campaigns", json=_bodies("/api/fairs/campaigns")).json()
    cid = camp["campaign_id"]
    assert owner.post(f"/api/fairs/campaigns/{cid}/customers", json={"customer_codes": [code]}).status_code == 200
    anon = app_env()
    paths = [_fill(p, code, cid) for (_, p) in VIEWER_GETS] + [
        "/api/crm/followups?mode=all", f"/api/customer360/search?q={code[:7]}"]
    before = _db_dump(ST.DB_PATH)
    for p in paths:
        anon.get(p)
    first = _db_dump(ST.DB_PATH)
    assert first["memory"] == before["memory"] and first["crm_followups"] == before["crm_followups"]
    for t in [t for t in before if t.startswith("fair")]:
        assert first[t] == before[t], t
    con = sqlite3.connect(ST.DB_PATH)
    assert {r[0] for r in con.execute("SELECT DISTINCT status FROM actions")} <= {"open"}
    assert {r[0] for r in con.execute("SELECT DISTINCT event FROM action_history")} <= {"generated"}
    con.close()
    for p in paths:
        anon.get(p)
    assert _db_dump(ST.DB_PATH) == first
    assert app_env.spy.ai == 0 and app_env.spy.c360_retry == 0 and app_env.spy.owner_reload == 0


def test_health_hides_local_error_text_from_viewers(app_env):
    app_env.repo.last_error = "FileNotFoundError: " + CANARY
    anon = app_env()
    h = anon.get("/api/health").json()
    assert h["context_error"] == APP.HEALTH_ERROR_VIEWER_TEXT and CANARY not in json.dumps(h)
    assert owner_client(app_env).get("/api/health").json()["context_error"].endswith(CANARY)


def test_security_headers(app_env):
    cl = app_env()
    r = cl.get("/api/agent/categories")
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "same-origin"
    assert r.headers["strict-transport-security"].startswith("max-age=")         # https base URL
    assert "no-store" in cl.get("/api/auth/session").headers["cache-control"]
    assert "no-store" in cl.get("/api/owner/status").headers["cache-control"]   # even the 401
    http = app_env(base_url="http://testserver")
    assert "strict-transport-security" not in http.get("/api/agent/categories").headers


# ---------------------------------------------------------------------------------------------
# C. owner login
# ---------------------------------------------------------------------------------------------
def test_successful_login_sets_a_hardened_cookie_and_stores_only_a_hash(app_env):
    cl = app_env()
    r = login(cl)
    sc = r.headers["set-cookie"]
    assert sc.startswith(AUTH.COOKIE_NAME + "=")
    low = sc.lower()
    assert "httponly" in low and "samesite=strict" in low and "secure" in low and "path=/" in low
    assert f"max-age={AUTH.session_absolute_seconds()}" in low
    token = re.match(AUTH.COOKIE_NAME + r"=([^;]+)", sc).group(1)
    assert len(token) >= 40
    body = r.json()
    assert body["authenticated"] and body["csrf_token"] and token not in json.dumps(body)
    dump = json.dumps(_raw_sessions())
    assert token not in dump and hashlib.sha256(token.encode()).hexdigest() in dump
    s = cl.get("/api/auth/session").json()
    assert s["authenticated"] and s["csrf_token"] == body["csrf_token"]


def _raw_sessions():
    con = sqlite3.connect(ST.DB_PATH)
    rows = con.execute("SELECT * FROM owner_sessions").fetchall()
    con.close()
    return rows


def test_wrong_password_is_refused_generically(app_env):
    cl = app_env()
    r = cl.post("/api/auth/login", json={"password": "yanlis-parola-1234"})
    assert r.status_code == 401
    assert r.json() == {"detail": {"error_code": "invalid_credentials", "message": "Şifre hatalı."}}
    assert "set-cookie" not in r.headers and _raw_sessions() == []
    assert cl.get("/api/auth/session").json()["authenticated"] is False


def test_login_body_is_bounded_and_never_echoed(app_env):
    cl = app_env()
    secret = "Z" * 300
    r = cl.post("/api/auth/login", json={"password": secret})
    assert r.status_code == 422 and secret not in r.text
    r = cl.post("/api/auth/login", json={"password": PASSWORD, "user": "x"})
    assert r.status_code == 422 and PASSWORD not in r.text


def test_password_is_never_accepted_over_plain_http_off_this_computer(app_env):
    cl = app_env(base_url="http://testserver")                       # client host "testclient", not loopback
    r = cl.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "https_required"
    assert "set-cookie" not in r.headers
    local = app_env(base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50123))
    r = local.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200 and "secure" not in r.headers["set-cookie"].lower()   # local http: not Secure


def test_transport_rules():
    st = AUTH.secure_transport
    assert st("https", "panel.example.com", "203.0.113.9")
    assert st("http", "127.0.0.1:8000", "127.0.0.1") and st("http", "localhost:8000", "::1")
    assert st("http", "[::1]:8000", "::1")
    assert not st("http", "panel.example.com", "127.0.0.1")         # proxy without forwarded headers
    assert not st("http", "127.0.0.1:8000", "192.168.1.20")
    assert not st("http", "192.168.1.5:8000", "192.168.1.20")
    assert AUTH.origin_ok(None, "https", "x.example") and AUTH.origin_ok("https://x.example", "https", "x.example")
    assert not AUTH.origin_ok("https://evil.example", "https", "x.example")
    assert not AUTH.origin_ok("null", "https", "x.example")
    assert not AUTH.origin_ok("http://x.example", "https", "x.example")


def test_admin_login_fails_closed_when_not_configured(app_env, monkeypatch):
    monkeypatch.delenv(AUTH.PASSWORD_ENV)
    cl = app_env()
    r = cl.post("/api/auth/login", json={"password": PASSWORD})
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "admin_not_configured"
    r = cl.post("/api/actions/x/complete")
    assert r.status_code == 401 and r.json()["detail"]["error_code"] == "admin_not_configured"
    assert cl.get("/api/agent/categories").status_code == 200                      # viewing still works
    assert cl.get("/api/auth/session").json()["configured"] is False
    for bad in ("plaintext-password", "scrypt:1024:8:1:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43,   # too weak
                "scrypt:32768:8:1:c2FsdA:" + "A" * 43,                                      # salt too short
                "bcrypt:x", "scrypt:32768:8:1:%%%:%%%"):
        monkeypatch.setenv(AUTH.PASSWORD_ENV, bad)
        assert AUTH.configured_hash() is None, bad
        assert cl.post("/api/auth/login", json={"password": PASSWORD}).status_code == 503


def test_login_throttling_per_client(app_env, monkeypatch):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    cl = app_env()
    for _ in range(AUTH.THROTTLE_PER_CLIENT):
        assert cl.post("/api/auth/login", json={"password": "yanlis-parola-1234"}).status_code == 401
    r = cl.post("/api/auth/login", json={"password": PASSWORD})                   # even the right password
    assert r.status_code == 429 and r.json()["detail"]["error_code"] == "login_throttled"
    assert 0 < int(r.headers["retry-after"]) <= AUTH.THROTTLE_WINDOW_SECONDS + 1
    assert "set-cookie" not in r.headers
    clock["t"] += AUTH.THROTTLE_WINDOW_SECONDS + 1
    assert cl.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    con = sqlite3.connect(ST.DB_PATH)
    keys = {r[0] for r in con.execute("SELECT client_key FROM owner_login_failures")}
    con.close()
    assert "testclient" not in json.dumps(sorted(keys))                         # no raw client address stored


def test_login_throttling_is_also_global(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "t.db"))
    ST.init_db()
    for i in range(AUTH.THROTTLE_GLOBAL):
        AUTH.record_failure(AUTH.client_key(f"198.51.100.{i}"))                 # one failure each, many clients
    assert AUTH.throttle_wait(AUTH.client_key("203.0.113.77")) > 0              # a new client is throttled too


# ---------------------------------------------------------------------------------------------
# D. sessions: expiry, logout, revocation
# ---------------------------------------------------------------------------------------------
def test_absolute_session_expiry(app_env, monkeypatch):
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    cl = owner_client(app_env)
    for _ in range(30):                                   # stay active: idle never reached…
        clock["t"] += AUTH.session_idle_seconds() / 2
        if clock["t"] >= 2_000_000.0 + AUTH.session_absolute_seconds():
            break
        assert cl.get("/api/auth/session").json()["authenticated"] is True
    clock["t"] = 2_000_000.0 + AUTH.session_absolute_seconds() + 1
    assert cl.get("/api/auth/session").json()["authenticated"] is False          # …but the absolute limit holds
    assert cl.get("/api/owner/status").status_code == 401
    assert _raw_sessions() == []


def test_idle_session_expiry(app_env, monkeypatch):
    clock = {"t": 3_000_000.0}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    cl = owner_client(app_env)
    clock["t"] += AUTH.session_idle_seconds() - 5
    assert cl.get("/api/owner/status").status_code == 200
    clock["t"] += AUTH.session_idle_seconds() + 1
    assert cl.get("/api/owner/status").status_code == 401


def test_session_bounds_from_environment(monkeypatch):
    monkeypatch.setenv("HALSA_SESSION_HOURS", "9999")
    monkeypatch.setenv("HALSA_SESSION_IDLE_MINUTES", "0")
    assert AUTH.session_absolute_seconds() == 72 * 3600 and AUTH.session_idle_seconds() == 5 * 60
    monkeypatch.setenv("HALSA_SESSION_HOURS", "abc")
    assert AUTH.session_absolute_seconds() == 12 * 3600


def test_logout_revokes_the_session_server_side(app_env):
    cl = owner_client(app_env)
    token = cl.cookies.get(AUTH.COOKIE_NAME)
    r = cl.post("/api/auth/logout")
    assert r.status_code == 200 and r.json()["authenticated"] is False
    assert AUTH.COOKIE_NAME in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
    replay = app_env()
    replay.cookies.set(AUTH.COOKIE_NAME, token)                                  # a stolen copy of the old cookie
    assert replay.get("/api/owner/status").status_code == 401
    assert replay.get("/api/auth/session").json()["authenticated"] is False


def test_logout_needs_the_csrf_token_while_logged_in(app_env):
    cl = owner_client(app_env)
    r = cl.post("/api/auth/logout", headers={"X-CSRF-Token": "yanlis"})
    assert r.status_code == 403 and cl.get("/api/auth/session").json()["authenticated"] is True


def test_logout_everywhere_and_password_change_revoke_all_sessions(app_env, monkeypatch):
    a, b = owner_client(app_env), owner_client(app_env)
    assert a.post("/api/auth/logout-all").status_code == 200
    assert b.get("/api/owner/status").status_code == 401
    c = owner_client(app_env)
    new_hash = AUTH.hash_password("baska-sentetik-parola-99", n=2 ** 14)
    monkeypatch.setenv(AUTH.PASSWORD_ENV, new_hash)                              # owner sets a new password
    assert c.get("/api/owner/status").status_code == 401
    login(c, "baska-sentetik-parola-99")
    assert c.get("/api/owner/status").status_code == 200


def test_login_rotates_the_session_and_sessions_are_bounded(app_env):
    cl = owner_client(app_env)
    first = cl.cookies.get(AUTH.COOKIE_NAME)
    login(cl)
    replay = app_env()
    replay.cookies.set(AUTH.COOKIE_NAME, first)
    assert replay.get("/api/owner/status").status_code == 401
    for _ in range(AUTH.MAX_SESSIONS[AUTH.ROLE_OWNER] + 3):
        owner_client(app_env)
    assert len(_raw_sessions()) == AUTH.MAX_SESSIONS[AUTH.ROLE_OWNER]


def test_forged_or_garbage_cookie_is_anonymous(app_env):
    cl = app_env()
    for v in ("x", "A" * 43, "A" * 500):
        cl.cookies.set(AUTH.COOKIE_NAME, v)
        assert cl.get("/api/owner/status").status_code == 401
        assert cl.get("/api/agent/categories").status_code == 200


# ---------------------------------------------------------------------------------------------
# E. CSRF and owner mutations
# ---------------------------------------------------------------------------------------------
def test_csrf_is_required_for_every_owner_write(app_env):
    cl = owner_client(app_env)
    tok = cl.headers.pop("X-CSRF-Token")
    cl.get("/api/agent/actions")
    aid = cl.get("/api/agent/actions").json()["actions"][0]["action_id"]
    before = _db_dump(ST.DB_PATH)
    for hdr in ({}, {"X-CSRF-Token": "yanlis"}, {"X-CSRF-Token": tok + "x"}):
        r = cl.post(f"/api/actions/{aid}/complete", headers=hdr)
        assert r.status_code == 403 and r.json()["detail"]["error_code"] == "csrf_failed"
    r = cl.post(f"/api/actions/{aid}/complete", headers={"X-CSRF-Token": tok, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "origin_rejected"
    assert _db_dump(ST.DB_PATH) == before
    r = cl.post(f"/api/actions/{aid}/complete", headers={"X-CSRF-Token": tok, "Origin": "https://testserver"})
    assert r.status_code == 200 and r.json()["status"] == "completed"


def test_csrf_token_of_one_session_does_not_work_for_another(app_env):
    a, b = owner_client(app_env), owner_client(app_env)
    b.headers["X-CSRF-Token"] = a.headers["X-CSRF-Token"]
    assert b.post("/api/fairs/campaigns", json=_bodies("/api/fairs/campaigns")).status_code == 403


@pytest.mark.parametrize("method,path", [k for k in OWNER_WRITES if k[1] != "/api/auth/logout-all"])
def test_owner_with_csrf_passes_the_access_layer(app_env, method, path):
    """Logged in + CSRF: the request reaches the endpoint (any refusal now is the endpoint's own validation or the
    accuracy gate, never authorization)."""
    cl = owner_client(app_env)
    cid = cl.post("/api/fairs/campaigns", json=_bodies("/api/fairs/campaigns")).json()["campaign_id"]
    r = cl.request(method, _fill(path, cid=cid), json=_bodies(path))
    detail = (r.json() or {}).get("detail") if r.headers.get("content-type", "").startswith("application/json") else None
    code = detail.get("error_code") if isinstance(detail, dict) else None
    assert code not in ("auth_required", "admin_not_configured", "csrf_failed", "origin_rejected",
                        "access_unclassified"), (path, r.status_code, r.text[:200])
    assert r.status_code != 401


def test_protected_crm_fair_and_action_mutations_work_for_the_owner(app_env):
    cl = owner_client(app_env)
    code = FX.P[1]
    r = cl.post(f"/api/crm/customers/{code}/interactions",
                json=_bodies("/api/crm/customers/{customer_code}/interactions"))
    assert r.status_code == 200
    assert cl.put(f"/api/crm/customers/{code}/followup",
                  json=_bodies("/api/crm/customers/{customer_code}/followup")).status_code == 200
    camp = cl.post("/api/fairs/campaigns", json=_bodies("/api/fairs/campaigns"))
    assert camp.status_code == 200
    cid = camp.json()["campaign_id"]
    assert cl.post(f"/api/fairs/campaigns/{cid}/customers", json={"customer_codes": [code]}).status_code == 200
    aid = cl.get("/api/agent/actions").json()["actions"][0]["action_id"]
    assert cl.post(f"/api/actions/{aid}/defer", json={}).json()["status"] == "deferred"
    assert cl.post(f"/api/actions/{aid}/reopen").json()["status"] == "open"
    assert cl.post("/api/owner/reload").status_code == 200 and app_env.spy.owner_reload == 1
    anon = app_env()                                                  # …and a viewer sees the result read-only
    assert anon.get(f"/api/crm/customers/{code}").status_code == 200
    assert any(c["campaign_id"] == cid for c in anon.get("/api/fairs/campaigns").json()["items"])


def test_protected_report_refresh(app_env, monkeypatch):
    """The only data-replacing operation today (controlled refresh of the selected reports) is owner-only."""
    called = {"n": 0}
    monkeypatch.setattr(APP.RF, "run_refresh", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {
        "ok": False, "step": "busy", "step_tr": "meşgul", "message": "x", "warnings": [], "errors": [],
        "sources": {}, "counts": {}})
    assert app_env().post("/api/context/refresh").status_code == 401 and called["n"] == 0
    assert owner_client(app_env).post("/api/context/refresh").status_code in (200, 409) and called["n"] == 1


# ---------------------------------------------------------------------------------------------
# F. the accuracy gate is independent of authentication
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_logging_in_never_reveals_an_unverified_metric(app_env):
    assert not any(m.certified == ACC.State.VERIFIED for m in ACC.REGISTER.values())
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    anon, owner = app_env(), owner_client(app_env)
    for path in ("/api/agent/actions", "/api/sales-queue", f"/api/customer360/{FX.P[1]}",
                 "/api/opportunities/summary", "/api/fairs/history"):
        for cl in (anon, owner):
            r = cl.get(path)
            assert r.status_code == ACC.WITHHELD_HTTP_STATUS, (path, r.status_code)
            assert r.json()["detail"]["error_code"] == "accuracy_withheld"
    for metric_path in ("/api/context",):                              # owner-only AND withheld
        assert anon.get(metric_path).status_code == 401
        assert owner.get(metric_path).status_code == ACC.WITHHELD_HTTP_STATUS
    ov = owner.get("/api/owner/overview").json()
    for sec in ("kredi", "gider_gelir", "butce"):
        assert ov[sec]["data"] is None and ov[sec]["withheld"]["error_code"] == "accuracy_withheld", sec
    assert owner.post("/api/agent/actions/enrich", json={"action_ids": ["x"]}).status_code == ACC.WITHHELD_HTTP_STATUS
    assert app_env.spy.ai == 0


# ---------------------------------------------------------------------------------------------
# G. password hashing and provisioning
# ---------------------------------------------------------------------------------------------
def test_password_hash_format_and_verification():
    h1, h2 = AUTH.hash_password(PASSWORD, n=2 ** 14), AUTH.hash_password(PASSWORD, n=2 ** 14)
    assert h1 != h2 and h1.startswith("scrypt:16384:8:1:") and "$" not in h1 and PASSWORD not in h1
    assert AUTH.verify_password(PASSWORD, h1) and AUTH.verify_password(PASSWORD, h2)
    assert not AUTH.verify_password(PASSWORD + "x", h1) and not AUTH.verify_password("", h1)
    assert not AUTH.verify_password("x" * 300, h1)
    assert AUTH.parse_hash(AUTH.hash_password(PASSWORD)).n == AUTH.SCRYPT_N        # default cost
    with pytest.raises(ValueError):
        AUTH.hash_password("kisa")                                                 # below the minimum length
    with pytest.raises(ValueError):
        AUTH.hash_password(PASSWORD, n=2 ** 10)                                    # too weak


def test_cli_provisioning_prints_only_the_hash_line(monkeypatch, capsys, tmp_path):
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert AUTH._cli(["hash-password"]) == 0
    out = capsys.readouterr()
    line = out.out.strip()
    assert line.startswith(AUTH.PASSWORD_ENV + "=scrypt:") and PASSWORD not in out.out + out.err
    assert AUTH.verify_password(PASSWORD, line.split("=", 1)[1])
    answers = iter([PASSWORD, PASSWORD + "x"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert AUTH._cli(["hash-password"]) == 2 and "scrypt" not in capsys.readouterr().out
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "cli.db"))
    ST.init_db()
    monkeypatch.setenv(AUTH.PASSWORD_ENV, HASH)
    AUTH.create_session(HASH)
    assert AUTH._cli(["revoke-sessions"]) == 0 and AUTH.session_count() == 0
    assert AUTH._cli(["status"]) == 0 and "yapılandırıldı" in capsys.readouterr().out


def test_no_credentials_or_hashes_are_committed():
    root = Path(__file__).resolve().parent.parent
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    for name in ("HALSA_OWNER_PASSWORD_HASH", "HALSA_FINANCE_PASSWORD_HASH"):
        m = re.search(rf"^{name}=(.*)$", env_example, re.M)
        assert m is not None and m.group(1).strip() == "", f".env.example documents {name} with an empty value"
    for p in [root / "app.py", root / "config.py", *(root / "services").glob("*.py")]:
        assert not re.search(r"scrypt:\d+:\d+:\d+:[A-Za-z0-9_-]{16,}:[A-Za-z0-9_-]{40,}", p.read_text("utf-8")), p
