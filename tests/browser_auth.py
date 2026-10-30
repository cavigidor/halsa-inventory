"""M8-CP1 browser-test helper: the browser tests use the REAL owner login against their uvicorn subprocess.

The password below is synthetic and exists only for tests; it is not a credential for any real system. Each test
server gets its hash through the environment (exactly how the owner configures the real server), and pages that
exercise owner actions are given that server's real session cookie. Anonymous (viewer) pages get no cookie.
"""
import http.cookies
import json
import urllib.request

from services import owner_auth as AUTH

TEST_PASSWORD = "sentetik-test-yonetici-sifresi"          # synthetic, tests only
TEST_HASH = AUTH.hash_password(TEST_PASSWORD, n=2 ** 14)   # minimum accepted cost keeps the suite fast
TEST_FINANCE_PASSWORD = "sentetik-test-finans-sifresi"     # synthetic, tests only (D-061 financial viewer)
TEST_FINANCE_HASH = AUTH.hash_password(TEST_FINANCE_PASSWORD, n=2 ** 14)

_current = {"base": None}
_sessions: dict[str, tuple[str, str]] = {}


def server_env() -> dict:
    return {AUTH.PASSWORD_ENV: TEST_HASH, AUTH.FINANCE_PASSWORD_ENV: TEST_FINANCE_HASH}


def register(base: str) -> None:
    """Called by each browser-test server once it is ready: owner pages opened next belong to this server."""
    # A replacement subprocess can reuse the same port but has a fresh SQLite session database.
    # URL equality does not make a cookie from the previous server valid in this one.
    _sessions.pop(base, None)
    _current["base"] = base


def login(base: str, password: str = TEST_PASSWORD) -> tuple[str, str]:
    """Real POST /api/auth/login → (session cookie value, CSRF token); cached per server."""
    if base in _sessions:
        return _sessions[base]
    req = urllib.request.Request(base + "/api/auth/login", data=json.dumps({"password": password}).encode(),
                                 method="POST", headers={"Content-Type": "application/json", "X-StockAgent": "1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read().decode())
        jar = http.cookies.SimpleCookie()
        for h in r.headers.get_all("Set-Cookie") or ():
            jar.load(h)
    token = jar[AUTH.COOKIE_NAME].value
    _sessions[base] = (token, body["csrf_token"])
    return _sessions[base]


def headers(base: str) -> dict:
    """Owner headers for direct urllib calls (seeding state through the API)."""
    token, csrf = login(base)
    return {"Cookie": f"{AUTH.COOKIE_NAME}={token}", "X-CSRF-Token": csrf}


def probe_headers(url: str) -> dict:
    """The tests' own verification probes (e.g. "no AI call was made" via the owner-only /api/ai/calls) act as the
    owner once the server is ready and registered; before that (readiness polling of viewer routes) they send none."""
    base = "/".join(url.split("/")[:3])
    return headers(base) if _current["base"] == base else {}


def authorize(context, base: str | None = None) -> None:
    """Give a Playwright browser context the owner session of the current (or given) test server."""
    base = base or _current["base"]
    if not base:
        return
    token, _ = login(base)
    context.add_cookies([{"name": AUTH.COOKIE_NAME, "value": token, "url": base, "httpOnly": True,
                          "sameSite": "Strict"}])
