"""M8-CP1 revision (D-061): three access levels — public viewer, financial viewer (password, read-only), owner.

Real session resolution (``production_auth``), synthetic data and synthetic passwords only. Reuses the in-process app
fixture of tests/test_m8_cp1_access.py.
"""
import json
import os
import sqlite3

import pytest

from services import access as AX
from services import accuracy as ACC
from services import owner_auth as AUTH
from services import store as ST
from tests.fixtures import fair_synth as FX
from tests.test_m8_cp1_access import (FIN_HASH, FIN_PASSWORD, HASH, OWNER_WRITES, PASSWORD, _bodies, _db_dump,  # noqa: F401
                                      _fill, app_env, login, owner_client, world)

pytestmark = pytest.mark.production_auth

FINANCE_SECTIONS = ("kredi", "gider_gelir", "butce")
DIAGNOSTIC_KEYS = {"file", "reloading", "error_code", "failed_step", "issue_codes", "loaded_at"}
OWNER_ONLY_TOP = {"operations", "system", "known_issues", "reload"}


def finance_login(cl, password=FIN_PASSWORD):
    r = cl.post("/api/auth/finance-login", json={"password": password})
    assert r.status_code == 200, r.text
    cl.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return r


def finance_client(app_env):
    cl = app_env()
    finance_login(cl)
    return cl


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _owner_basenames(world):
    return [os.path.basename(p) for p in world["owner_env"].values()]


def _sessions():
    con = sqlite3.connect(ST.DB_PATH)
    rows = con.execute("SELECT role FROM owner_sessions").fetchall()
    con.close()
    return sorted(r[0] for r in rows)


# 1 ---------------------------------------------------------------------------------------------
def test_anonymous_viewers_still_see_the_regular_dashboard(app_env):
    cl = app_env()
    assert cl.get("/").status_code != 401
    for p in ("/api/agent/actions", "/api/sales-queue", f"/api/crm/customers/{FX.P[1]}", "/api/fairs/campaigns",
              "/api/crm/followups?mode=all", f"/api/customer360/search?q={FX.P[1][:7]}"):
        r = cl.get(p)
        assert r.status_code == 200, (p, r.status_code)


# 2 ---------------------------------------------------------------------------------------------
def test_anonymous_viewers_get_no_financial_information(app_env, world):
    cl = app_env()
    r = cl.get("/api/finance/overview")
    assert r.status_code == 401
    assert r.json() == {"detail": {"error_code": "finance_auth_required",
                                   "message": "Yönetim (finans) bölümü için finans görüntüleme şifresi gerekir."}}
    for p in ("/api/owner/overview", "/api/owner/status"):
        assert cl.get(p).status_code == 401
    assert app_env.spy.owner_reload == 0


# 3 + 6 -----------------------------------------------------------------------------------------
def test_financial_password_unlocks_only_the_financial_projection(app_env, world):
    cl = app_env()
    r = finance_login(cl)
    body = r.json()
    assert body["authenticated"] is True and body["role"] == "finance" and body["csrf_token"]
    low = r.headers["set-cookie"].lower()
    assert "httponly" in low and "samesite=strict" in low and "secure" in low
    s = cl.get("/api/auth/session").json()
    assert s["role"] == "finance" and s["authenticated"] is True
    fin = cl.get("/api/finance/overview")
    assert fin.status_code == 200 and "no-store" in fin.headers["cache-control"]
    d = fin.json()
    assert d["audience"] == "finance" and d["read_only"] is True
    assert set(d) == {"today", "audience", "audience_note", "read_only", "kredi", "gider_gelir", "butce", "ai_used"}
    assert not (OWNER_ONLY_TOP & set(d))
    for sec in FINANCE_SECTIONS:
        assert not (DIAGNOSTIC_KEYS & set(d[sec])), sec
        assert {"key", "label", "configured", "state", "state_label"} <= set(d[sec])
    text = fin.text
    for base in _owner_basenames(world):
        assert base not in text, "no source file name for financial viewers"
    for p in ("/api/owner/overview", "/api/owner/status", "/api/accuracy/status", "/api/context", "/api/ai/calls",
              "/api/customer360/status?retry=true"):
        r = cl.get(p)
        assert r.status_code == 403, (p, r.status_code)
        assert r.json()["detail"]["error_code"] == "owner_required"
        for base in _owner_basenames(world):
            assert base not in r.text
    assert app_env.spy.c360_retry == 0


def test_financial_projection_keeps_the_financial_content_identical_to_the_owner_view(app_env):
    """Same data, same withheld notices, same notes; only the source-diagnostic head fields are removed."""
    owner = owner_client(app_env).get("/api/owner/overview").json()
    fin = finance_client(app_env).get("/api/finance/overview").json()
    for sec in FINANCE_SECTIONS:
        expected = {k: v for k, v in owner[sec].items() if k not in DIAGNOSTIC_KEYS}
        assert fin[sec] == expected, sec
    assert set(owner[FINANCE_SECTIONS[0]]) - set(fin[FINANCE_SECTIONS[0]]) <= DIAGNOSTIC_KEYS


def test_health_detail_stays_owner_only(app_env):
    app_env.repo.last_error = "FileNotFoundError: /Users/sentetik/gizli.xlsx"
    h = finance_client(app_env).get("/api/health").json()
    assert "gizli" not in json.dumps(h)


# 4 ---------------------------------------------------------------------------------------------
def test_incorrect_financial_password_is_refused(app_env):
    cl = app_env()
    for pw in ("yanlis-finans-parola", PASSWORD):          # the owner's password is not the financial password
        r = cl.post("/api/auth/finance-login", json={"password": pw})
        assert r.status_code == 401 and r.json()["detail"]["error_code"] == "invalid_credentials"
        assert "set-cookie" not in r.headers
    r = cl.post("/api/auth/login", json={"password": FIN_PASSWORD})      # …and never the owner's
    assert r.status_code == 401
    assert _sessions() == [] and cl.get("/api/finance/overview").status_code == 401


def test_financial_login_needs_https_off_this_computer_and_is_throttled_separately(app_env, monkeypatch):
    http = app_env(base_url="http://testserver")
    r = http.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "https_required"
    clock = {"t": 5_000_000.0}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    cl = app_env()
    for _ in range(AUTH.THROTTLE_PER_CLIENT):
        assert cl.post("/api/auth/finance-login", json={"password": "yanlis-finans-parola"}).status_code == 401
    r = cl.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    login(cl)                                               # guessing the financial password never locks the owner out
    clock["t"] += AUTH.THROTTLE_WINDOW_SECONDS + 1
    assert app_env().post("/api/auth/finance-login", json={"password": FIN_PASSWORD}).status_code == 200


def test_financial_login_fails_closed_when_not_configured(app_env, monkeypatch):
    monkeypatch.delenv(AUTH.FINANCE_PASSWORD_ENV)
    cl = app_env()
    r = cl.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "finance_not_configured"
    r = cl.get("/api/finance/overview")
    assert r.status_code == 401 and r.json()["detail"]["error_code"] == "finance_not_configured"
    assert cl.get("/api/auth/session").json()["finance_configured"] is False
    assert owner_client(app_env).get("/api/finance/overview").status_code == 200    # the owner still sees it


# 5 + 9 -----------------------------------------------------------------------------------------
def test_financial_sessions_expire(app_env, monkeypatch):
    clock = {"t": 6_000_000.0}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    cl = finance_client(app_env)
    clock["t"] += AUTH.session_idle_seconds() + 1                      # idle limit
    assert cl.get("/api/finance/overview").status_code == 401
    cl = finance_client(app_env)
    start = clock["t"]
    while clock["t"] < start + AUTH.session_absolute_seconds() - AUTH.session_idle_seconds() / 2:
        clock["t"] += AUTH.session_idle_seconds() / 2                  # active, never idle…
        assert cl.get("/api/finance/overview").status_code == 200
    clock["t"] = start + AUTH.session_absolute_seconds() + 1           # …but the absolute limit holds
    assert cl.get("/api/finance/overview").status_code == 401


def test_financial_logout_revokes_server_side(app_env):
    cl = finance_client(app_env)
    token = cl.cookies.get(AUTH.COOKIE_NAME)
    assert cl.post("/api/auth/logout").status_code == 200
    replay = app_env()
    replay.cookies.set(AUTH.COOKIE_NAME, token)
    assert replay.get("/api/finance/overview").status_code == 401


def test_owner_can_end_financial_sessions_without_logging_out(app_env):
    f1, f2 = finance_client(app_env), finance_client(app_env)
    owner = owner_client(app_env)
    assert f1.post("/api/auth/finance-sessions/revoke").status_code == 403       # a financial viewer cannot
    r = owner.post("/api/auth/finance-sessions/revoke")
    assert r.status_code == 200 and r.json()["revoked"] == 2
    assert f1.get("/api/finance/overview").status_code == 401 and f2.get("/api/finance/overview").status_code == 401
    assert owner.get("/api/owner/status").status_code == 200
    finance_client(app_env)
    assert owner.post("/api/auth/logout-all").status_code == 200                 # everything, owner included
    assert _sessions() == []


def test_password_rotation_revokes_only_that_roles_sessions(app_env, monkeypatch):
    fin, owner = finance_client(app_env), owner_client(app_env)
    monkeypatch.setenv(AUTH.FINANCE_PASSWORD_ENV, AUTH.hash_password("yeni-finans-parolasi-55", n=2 ** 14))
    assert fin.get("/api/finance/overview").status_code == 401                   # financial sessions ended
    assert owner.get("/api/owner/status").status_code == 200                     # the owner is unaffected
    fin2 = app_env()
    finance_login(fin2, "yeni-finans-parolasi-55")
    monkeypatch.setenv(AUTH.PASSWORD_ENV, AUTH.hash_password("yeni-yonetici-parolasi-66", n=2 ** 14))
    assert owner.get("/api/owner/status").status_code == 401                     # owner sessions ended
    assert fin2.get("/api/finance/overview").status_code == 200                  # financial viewer unaffected


def test_cli_revokes_by_role_and_refuses_a_shared_password(app_env, monkeypatch, capsys):
    finance_client(app_env), owner_client(app_env)
    assert AUTH._cli(["revoke-sessions", "--finance"]) == 0
    assert _sessions() == ["owner"]
    answers = iter([PASSWORD, PASSWORD])                   # the owner's password as the financial password
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert AUTH._cli(["hash-password", "--finance"]) == 2
    out = capsys.readouterr()
    assert "scrypt" not in out.out and "aynı" in out.err
    answers = iter([FIN_PASSWORD, FIN_PASSWORD])            # and vice versa
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert AUTH._cli(["hash-password"]) == 2
    answers = iter(["baska-finans-parolasi-88"] * 2)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert AUTH._cli(["hash-password", "--finance"]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith(AUTH.FINANCE_PASSWORD_ENV + "=scrypt:")
    assert AUTH._cli(["status"]) == 0
    st = capsys.readouterr().out
    assert "Yönetici girişi: yapılandırıldı" in st and "Finans görüntüleme girişi: yapılandırıldı" in st


# 7 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("method,path", OWNER_WRITES)
def test_financial_viewers_cannot_change_anything(app_env, method, path):
    cl = finance_client(app_env)                         # has a valid session AND its own CSRF token
    cl.get("/api/agent/actions")
    before = _db_dump(ST.DB_PATH)
    r = cl.request(method, _fill(path), json=_bodies(path),
                   headers={"X-StockAgent": "1", "Origin": "https://testserver"})
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "owner_required", (path, r.text[:200])
    assert _db_dump(ST.DB_PATH) == before
    assert app_env.spy.ai == 0 and app_env.spy.owner_reload == 0 and app_env.spy.c360_retry == 0


def test_no_mutating_route_is_reachable_with_a_read_level(monkeypatch):
    assert {lv for (m, _), lv in AX.ACCESS.items() if m in AX.MUTATING} <= {AX.OWNER_WRITE, AX.AUTH}
    assert {k for k, lv in AX.ACCESS.items() if lv == AX.FINANCE_READ} == {("GET", "/api/finance/overview")}
    monkeypatch.setattr(AX, "ACCESS", {**AX.ACCESS, ("POST", "/x"): AX.FINANCE_READ})
    assert AX.level("POST", "/x") == AX.OWNER_WRITE


def test_financial_password_never_creates_an_owner_session(app_env):
    cl = finance_client(app_env)
    assert _sessions() == ["finance"]
    login_role = cl.post("/api/auth/finance-login", json={"password": FIN_PASSWORD}).json()["role"]
    assert login_role == "finance" and _sessions() == ["finance"]      # rotated, still finance


# 8 ---------------------------------------------------------------------------------------------
def test_owner_login_grants_financial_viewing_and_administration(app_env):
    owner = owner_client(app_env)
    assert owner.get("/api/finance/overview").status_code == 200
    assert owner.get("/api/owner/overview").status_code == 200
    camp = owner.post("/api/fairs/campaigns", json=_bodies("/api/fairs/campaigns"))
    assert camp.status_code == 200
    r = owner.post("/api/auth/finance-login", json={"password": FIN_PASSWORD})   # no downgrade to financial viewer
    assert r.status_code == 200 and r.json()["role"] == "owner" and _sessions() == ["owner"]
    assert owner.get("/api/owner/status").status_code == 200


# 10 --------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_accuracy_gate_withholds_unverified_metrics_from_every_level(app_env):
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    assert not any(m.certified == ACC.State.VERIFIED for m in ACC.REGISTER.values())
    anon, fin, owner = app_env(), finance_client(app_env), owner_client(app_env)
    for cl in (anon, fin, owner):
        for p in ("/api/agent/actions", "/api/sales-queue", "/api/opportunities/summary"):
            r = cl.get(p)
            assert r.status_code == ACC.WITHHELD_HTTP_STATUS and r.json()["detail"]["error_code"] == "accuracy_withheld"
    for cl in (fin, owner):
        d = cl.get("/api/finance/overview").json()
        for sec in FINANCE_SECTIONS:
            assert d[sec]["data"] is None and d[sec]["withheld"]["error_code"] == "accuracy_withheld", sec
    assert app_env.spy.ai == 0
