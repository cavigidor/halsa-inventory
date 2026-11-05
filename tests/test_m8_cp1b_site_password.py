"""M8-CP1B (D-074): the shared website password in front of everything — real sessions (`production_auth`).

Anonymous visitors get only the Turkish site-password page and the owner-login page; every data API needs a session;
missing configuration never falls back to public viewing; expired, logged-out, revoked or password-changed sessions
show nothing; no role gains a permission it did not have. Synthetic data only; synthetic test passwords.
"""
import json

import pytest

import app as APP
from services import access as AX
from services import owner_auth as AUTH
from tests.test_m8_cp1_access import (FIN_PASSWORD, HASH, OWNER_WRITES, PASSWORD, VIEW_HASH, VIEW_PASSWORD,  # noqa: F401
                                      VIEWER_GETS, _bodies, _fill, app_env, login, owner_client, viewer_client,
                                      viewer_login, world)

pytestmark = pytest.mark.production_auth

DATA_APIS = [p for (_, p) in VIEWER_GETS if p.startswith("/api/")]


def _site_page(r):
    assert r.status_code == 401 and r.headers["x-halsa-auth"] == "site_required"
    assert "Site şifresi" in r.text and "const DATA" not in r.text and "stockagent-generation" not in r.text
    assert "x-halsa-generation" not in r.headers and "no-store" in r.headers["cache-control"]


def test_only_the_login_pages_are_public():
    # D-076 (CP2) adds only the data-free host liveness probe (no session lookup at all)
    assert {k for k, v in AX.ACCESS.items() if v == AX.PUBLIC} == {("GET", "/giris"), ("GET", "/livez")}
    assert AX.NO_SESSION == {("GET", "/livez")}
    assert AX.HTML_PAGES == {"/", "/yukleme"}
    assert AX.level("POST", "/giris") is None and AX.level("POST", "/") is None


@pytest.mark.parametrize("path", DATA_APIS)
def test_direct_api_calls_without_a_session_return_nothing(app_env, path):
    anon = app_env()
    for hdr in ({}, {"X-StockAgent": "1", "Accept": "application/json"}):     # the legacy guard is not identity
        r = anon.get(_fill(path), headers=hdr)
        assert r.status_code == 401 and r.json()["detail"]["error_code"] == "site_auth_required", path
        assert set(r.json()) == {"detail"}


def test_bookmarks_and_pages_without_a_session_show_the_password_page(app_env):
    anon = app_env()
    for path in ("/", "/?x=1", "/yukleme"):
        _site_page(anon.get(path))
    h = anon.head("/")
    assert h.status_code in (401, 405) and not h.content                 # HEAD never returns the page
    assert anon.get("/giris").status_code == 200


def test_a_previously_authorized_page_is_never_served_without_a_session(app_env):
    viewer = viewer_client(app_env)
    seen = viewer.get("/")
    assert seen.status_code != 401
    anon = app_env()
    _site_page(anon.get("/"))                                         # the same server, no cookie: nothing
    viewer.post("/api/auth/logout")
    _site_page(viewer.get("/"))                                       # after logout: nothing either


def test_missing_website_password_refuses_instead_of_going_public(app_env, monkeypatch):
    monkeypatch.delenv(AUTH.VIEWER_PASSWORD_ENV)
    anon = app_env()
    r = anon.get("/api/agent/categories")
    assert r.status_code == 401 and r.json()["detail"]["error_code"] == "site_not_configured"
    assert anon.get("/").status_code == 401
    assert anon.post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD}).json()["detail"]["error_code"] == \
        "site_not_configured"
    assert anon.get("/api/auth/session").json()["viewer_configured"] is False
    owner = owner_client(app_env)                                     # the other passwords still open the site
    assert owner.get("/api/agent/categories").status_code == 200
    monkeypatch.setenv(AUTH.VIEWER_PASSWORD_ENV, "malformed-hash")
    assert AUTH.configured_hash(AUTH.ROLE_VIEWER) is None
    assert app_env().get("/api/agent/categories").json()["detail"]["error_code"] == "site_not_configured"


def test_wrong_password_throttling_and_transport_apply_to_the_website_login(app_env, monkeypatch):
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    cl = app_env()
    for _ in range(AUTH.THROTTLE_PER_CLIENT):
        r = cl.post("/api/auth/viewer-login", json={"password": "yanlis-parola-1234"})
        assert r.status_code == 401 and r.json()["detail"]["error_code"] == "invalid_credentials"
    r = cl.post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD})
    assert r.status_code == 429 and "set-cookie" not in r.headers
    assert app_env().post("/api/auth/login", json={"password": PASSWORD}).status_code == 200   # other roles unaffected
    clock["t"] += AUTH.THROTTLE_WINDOW_SECONDS + 1
    assert cl.post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD}).status_code == 200
    remote = app_env(base_url="http://halsa.example")                 # plain HTTP off this computer
    r = remote.post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD})
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "https_required"
    r = app_env().post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD},
                       headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "origin_rejected"


def test_website_session_cookie_and_body(app_env):
    cl = app_env()
    r = viewer_login(cl)
    low = r.headers["set-cookie"].lower()
    assert "httponly" in low and "samesite=strict" in low and "secure" in low
    body = r.json()
    assert body["role"] == "viewer" and body["authenticated"] is True and VIEW_PASSWORD not in json.dumps(body)


def test_expired_idle_and_revoked_sessions_show_nothing(app_env, monkeypatch):
    clock = {"t": 2_000_000.0}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    v = viewer_client(app_env)
    assert v.get("/api/agent/categories").status_code == 200
    clock["t"] += AUTH.session_idle_seconds() + 1                     # idle expiry
    assert v.get("/api/agent/categories").json()["detail"]["error_code"] == "site_auth_required"
    v = viewer_client(app_env)
    for _ in range(AUTH.session_absolute_seconds() // AUTH.session_idle_seconds() + 1):   # active, but past 12 h
        clock["t"] += AUTH.session_idle_seconds() - 60
        v.get("/api/agent/categories")
    assert v.get("/api/agent/categories").status_code == 401
    v, f = viewer_client(app_env), app_env()
    r = f.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})
    assert r.status_code == 200
    owner = owner_client(app_env)
    assert owner.post("/api/auth/viewer-sessions/revoke").json()["revoked"] >= 1
    assert v.get("/api/agent/categories").status_code == 401        # website sessions ended …
    assert f.get("/api/agent/categories").status_code == 200        # … financial and owner sessions stay
    assert owner.get("/api/agent/categories").status_code == 200


def test_changing_the_website_password_revokes_only_website_sessions(app_env, monkeypatch):
    v, owner = viewer_client(app_env), owner_client(app_env)
    monkeypatch.setenv(AUTH.VIEWER_PASSWORD_ENV, AUTH.hash_password("yeni-site-parolasi-2026", n=2 ** 14))
    assert v.get("/api/agent/categories").status_code == 401
    assert owner.get("/api/agent/categories").status_code == 200
    assert app_env().post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD}).status_code == 401


def test_the_website_role_gains_nothing_else(app_env):
    v = viewer_client(app_env)
    assert v.get("/api/finance/overview").json()["detail"]["error_code"] == "finance_auth_required"
    for path in ("/api/owner/overview", "/api/owner/status", "/api/accuracy/status", "/api/context", "/api/uploads",
                 "/api/vkm/issues", "/api/ai/calls"):
        r = v.get(path)
        assert r.status_code == 403 and r.json()["detail"]["error_code"] == "owner_required", path
    for method, path in OWNER_WRITES:
        r = v.request(method, _fill(path), json=_bodies(path))
        assert r.status_code == 403 and r.json()["detail"]["error_code"] == "owner_required", (method, path)
    assert app_env.spy.ai == 0 and app_env.spy.owner_reload == 0


def test_logins_never_downgrade_and_rotate(app_env):
    owner = owner_client(app_env)
    r = owner.post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "owner"       # still the owner
    fin = app_env()
    fin.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})
    assert fin.post("/api/auth/viewer-login", json={"password": VIEW_PASSWORD}).json()["role"] == "finance"
    v = viewer_client(app_env)
    old = v.cookies.get(AUTH.COOKIE_NAME)
    r = v.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})   # upgrade: a new (finance) session
    assert r.json()["role"] == "finance" and v.cookies.get(AUTH.COOKIE_NAME) != old
    stale = app_env()
    stale.cookies.set(AUTH.COOKIE_NAME, old)
    assert stale.get("/api/agent/categories").status_code == 401      # the rotated website session is gone


def test_provisioning_refuses_a_password_used_by_another_role(monkeypatch, capsys):
    monkeypatch.setenv(AUTH.PASSWORD_ENV, HASH)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": PASSWORD)
    assert AUTH._cli(["hash-password", "--viewer"]) == 2               # the owner's password: refused
    assert "aynı" in capsys.readouterr().err
    monkeypatch.setenv(AUTH.VIEWER_PASSWORD_ENV, VIEW_HASH)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": VIEW_PASSWORD)
    assert AUTH._cli(["hash-password", "--finance"]) == 2              # the website password: refused for finance
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "baska-bir-site-parolasi")
    assert AUTH._cli(["hash-password", "--viewer"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(AUTH.VIEWER_PASSWORD_ENV + "=scrypt:") and "baska-bir" not in out
