"""M8-CP1 (D-060, D-061): password roles for a browser web app. Viewing the normal dashboard needs no login.

Two roles, each with its OWN password hash and its own sessions (no usernames, no accounts):
- ``owner``   — administration: every data-changing or administrative operation, and every owner-only read
                (``HALSA_OWNER_PASSWORD_HASH``);
- ``finance`` — read-only financial viewing (Yönetim: Kredi / Gider-Gelir / Bütçe) for the owner's father and other
                explicitly trusted financial viewers (``HALSA_FINANCE_PASSWORD_HASH``). It never grants any owner
                permission; the owner's session also allows financial viewing.

Deliberately small and conventional:
- a password is never stored or committed; only an scrypt hash (Python's ``hashlib.scrypt``) lives in the private
  environment (the gitignored ``.env``). Provision with ``python -m services.owner_auth hash-password [--finance]``;
- a session is a random 256-bit token in an HttpOnly, SameSite=Strict cookie (Secure over HTTPS). The server keeps
  only its SHA-256 and the role in SQLite, with an absolute and an idle expiry; logout, "log out everywhere", the
  owner's "end financial sessions", the ``revoke-sessions`` command and any change of THAT role's password hash
  revoke it (changing one role's password never touches the other role's sessions);
- every state-changing request also needs the session's CSRF token in ``X-CSRF-Token`` (and a same-origin
  ``Origin`` when the browser sends one);
- failed logins are throttled per client and globally (stored in SQLite, so a restart does not reset them);
- fail closed: no or malformed hash → that role's login disabled and its routes refused; a password is never accepted
  over plain HTTP except on the loopback interface (local use on the owner's own computer).

No usernames, accounts, e-mail, OAuth or recovery flow (owner decision 2026-10-22). Nothing here logs a password,
token, hash or client address.
"""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import sys
import time
from dataclasses import dataclass

from services import store as ST

log = logging.getLogger("owner_auth")

ROLE_OWNER, ROLE_FINANCE = "owner", "finance"
ROLES = (ROLE_OWNER, ROLE_FINANCE)
PASSWORD_ENV = "HALSA_OWNER_PASSWORD_HASH"
FINANCE_PASSWORD_ENV = "HALSA_FINANCE_PASSWORD_HASH"
PASSWORD_ENVS = {ROLE_OWNER: PASSWORD_ENV, ROLE_FINANCE: FINANCE_PASSWORD_ENV}
COOKIE_NAME = "halsa_owner"                             # one cookie; the server-side row says which role
CSRF_HEADER = "x-csrf-token"

# scrypt parameters (RFC 7914). N = 2**15, r = 8, p = 1 is the commonly recommended interactive-login setting.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
SCRYPT_N_MIN, SCRYPT_N_MAX = 2 ** 14, 2 ** 20          # weaker or absurd parameters are refused (fail closed)
SCRYPT_DKLEN, SALT_BYTES = 32, 16
SCRYPT_MAXMEM = 1024 * 1024 * 1024                     # bound for N_MAX * r; normal logins use ~32 MB
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256

MAX_SESSIONS = {ROLE_OWNER: 5, ROLE_FINANCE: 10}       # per role; oldest sessions are dropped beyond this
LAST_SEEN_WRITE_SECONDS = 60                            # idle-timer write granularity

THROTTLE_WINDOW_SECONDS = 15 * 60
THROTTLE_PER_CLIENT = 5                                 # failures per client per window (per role)
THROTTLE_GLOBAL = 20                                    # failures from everyone per window, per role (one password
                                                        # each; guessing the finance password never locks the owner out)

_now = time.time                                        # monkeypatched by tests


def _bounded_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, "") or default)
    except ValueError:
        v = default
    return min(max(v, lo), hi)


def session_absolute_seconds() -> int:
    return _bounded_env("HALSA_SESSION_HOURS", 12, 1, 72) * 3600


def session_idle_seconds() -> int:
    return _bounded_env("HALSA_SESSION_IDLE_MINUTES", 120, 5, 720) * 60


# ---------------- password hash ----------------
def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P,
                  salt: bytes | None = None) -> str:
    """``scrypt:N:r:p:<salt>:<hash>`` (URL-safe base64, no ``$`` so .env files and shells keep it intact)."""
    if not isinstance(password, str) or not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise ValueError(f"password must be {MIN_PASSWORD_LENGTH}–{MAX_PASSWORD_LENGTH} characters")
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM,
                        dklen=SCRYPT_DKLEN)
    encoded = f"scrypt:{n}:{r}:{p}:{_b64e(salt)}:{_b64e(dk)}"
    if parse_hash(encoded) is None:
        raise ValueError("refusing to produce a hash with unsupported parameters")
    return encoded


@dataclass(frozen=True)
class _Params:
    n: int
    r: int
    p: int
    salt: bytes
    dk: bytes


def parse_hash(encoded: str | None) -> _Params | None:
    """Strict parser; anything unexpected → None (login disabled)."""
    if not encoded or not isinstance(encoded, str):
        return None
    parts = encoded.strip().split(":")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, dk = _b64d(parts[4]), _b64d(parts[5])
    except (ValueError, binascii.Error):
        return None
    if not (SCRYPT_N_MIN <= n <= SCRYPT_N_MAX and n & (n - 1) == 0 and 1 <= r <= 32 and 1 <= p <= 4):
        return None
    if len(salt) < SALT_BYTES or len(dk) != SCRYPT_DKLEN:
        return None
    return _Params(n, r, p, salt, dk)


def verify_password(password: str, encoded: str) -> bool:
    prm = parse_hash(encoded)
    if prm is None or not isinstance(password, str) or not (1 <= len(password) <= MAX_PASSWORD_LENGTH):
        return False
    try:
        dk = hashlib.scrypt(password.encode("utf-8"), salt=prm.salt, n=prm.n, r=prm.r, p=prm.p,
                            maxmem=SCRYPT_MAXMEM, dklen=len(prm.dk))
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(dk, prm.dk)


_warned: set[str] = set()


def configured_hash(role: str = ROLE_OWNER) -> str | None:
    """The role's password hash from the private environment, or None (→ that role is disabled). Read on every call so
    a changed hash takes effect (and revokes that role's old sessions) without code changes."""
    env = PASSWORD_ENVS[role]
    raw = (os.environ.get(env) or "").strip()
    if not raw:
        return None
    if parse_hash(raw) is None:
        if env not in _warned:                          # never log the value
            log.warning("%s is set but malformed or too weak; that login is disabled", env)
            _warned.add(env)
        return None
    return raw


def credential_id(encoded: str, role: str = ROLE_OWNER) -> str:
    """Binds sessions to the role's configured hash: replacing that password revokes that role's sessions only."""
    prefix = "halsa-owner-credential|" if role == ROLE_OWNER else f"halsa-{role}-credential|"
    return hashlib.sha256((prefix + encoded).encode()).hexdigest()[:32]


# ---------------- storage (same private SQLite file as the rest of the operator state) ----------------
def init_tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS owner_sessions(
        token_hash TEXT PRIMARY KEY, credential_id TEXT NOT NULL,
        created_at REAL NOT NULL, last_seen REAL NOT NULL, expires_at REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS owner_login_failures(
        id INTEGER PRIMARY KEY AUTOINCREMENT, client_key TEXT NOT NULL, at REAL NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS owner_login_failures_at ON owner_login_failures(at)")
    # D-061: the role of each session and failure (older rows are owner rows); non-destructive migration
    for table in ("owner_sessions", "owner_login_failures"):
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if "role" not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN role TEXT NOT NULL DEFAULT 'owner'")


def _conn():
    c = ST._conn()
    init_tables(c)                                      # idempotent; a fresh state DB never makes auth crash
    return c


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def csrf_for(token: str) -> str:
    """The session's CSRF token, derived from the (HttpOnly) session token: only a page that can read
    /api/auth/session with the cookie — i.e. this origin — learns it. Nothing extra is stored."""
    return hmac.new(token.encode("utf-8"), b"halsa-owner-csrf-v1", hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Session:
    token_hash: str
    csrf_token: str
    created_at: float
    expires_at: float
    idle_expires_at: float
    role: str = ROLE_OWNER

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER

    @property
    def can_view_finance(self) -> bool:
        return self.role in (ROLE_OWNER, ROLE_FINANCE)

    def csrf_ok(self, presented: str | None) -> bool:
        return bool(presented) and isinstance(presented, str) and hmac.compare_digest(
            presented.encode("utf-8", "replace"), self.csrf_token.encode("utf-8"))


def create_session(encoded_hash: str, role: str = ROLE_OWNER) -> tuple[str, str, float]:
    """New session of `role` for that role's configured credential → (token, csrf_token, expires_at). Only the token's
    SHA-256 is stored."""
    if role not in ROLES:
        raise ValueError("unknown role")
    token = secrets.token_urlsafe(32)
    csrf = csrf_for(token)
    now = _now()
    exp = now + session_absolute_seconds()
    cid = credential_id(encoded_hash, role)
    with _conn() as c:
        c.execute("DELETE FROM owner_sessions WHERE expires_at <= ? OR (role = ? AND credential_id <> ?)",
                  (now, role, cid))
        c.execute("INSERT INTO owner_sessions(token_hash,credential_id,created_at,last_seen,expires_at,role) "
                  "VALUES(?,?,?,?,?,?)", (_sha(token), cid, now, now, exp, role))
        keep = [r[0] for r in c.execute("SELECT token_hash FROM owner_sessions WHERE role=? ORDER BY created_at DESC "
                                        "LIMIT ?", (role, MAX_SESSIONS[role]))]
        c.execute(f"DELETE FROM owner_sessions WHERE role=? AND token_hash NOT IN ({','.join('?' * len(keep))})",
                  [role, *keep])
    return token, csrf, exp


def resolve(token: str | None) -> Session | None:
    """The live session for this cookie value, or None. Expired, idle, revoked, unknown role or credential-changed
    (for that session's role) → None."""
    if not token or len(token) > 200:
        return None
    th, now, idle = _sha(token), _now(), session_idle_seconds()
    with _conn() as c:
        row = c.execute("SELECT * FROM owner_sessions WHERE token_hash=?", (th,)).fetchone()
        if row is None:
            return None
        role = row["role"]
        enc = configured_hash(role) if role in ROLES else None
        if (enc is None or row["credential_id"] != credential_id(enc, role) or row["expires_at"] <= now
                or row["last_seen"] + idle <= now):
            c.execute("DELETE FROM owner_sessions WHERE token_hash=?", (th,))
            return None
        last = row["last_seen"]
        if now - last >= LAST_SEEN_WRITE_SECONDS:
            c.execute("UPDATE owner_sessions SET last_seen=? WHERE token_hash=?", (now, th))
            last = now
    return Session(th, csrf_for(token), row["created_at"], row["expires_at"], min(row["expires_at"], last + idle),
                   role)


def request_session(request) -> Session | None:
    """The session (owner or finance) presented by this HTTP request (cookie), or None."""
    return resolve(request.cookies.get(COOKIE_NAME))


def revoke(token_hash: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM owner_sessions WHERE token_hash=?", (token_hash,))


def revoke_all(role: str | None = None) -> int:
    """End every session (role=None) or every session of one role."""
    with _conn() as c:
        if role is None:
            return c.execute("DELETE FROM owner_sessions").rowcount
        return c.execute("DELETE FROM owner_sessions WHERE role=?", (role,)).rowcount


def session_count(role: str | None = None) -> int:
    with _conn() as c:
        if role is None:
            return c.execute("SELECT COUNT(*) FROM owner_sessions WHERE expires_at > ?", (_now(),)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM owner_sessions WHERE expires_at > ? AND role=?",
                         (_now(), role)).fetchone()[0]


# ---------------- login throttling ----------------
def client_key(host: str | None) -> str:
    """Pseudonymous throttle key (the raw address is never stored)."""
    return _sha("halsa-login|" + (host or "unknown"))[:24]


def throttle_wait(key: str, role: str = ROLE_OWNER) -> int:
    """Seconds to wait before another attempt at this role's password is accepted (0 = allowed). Per client AND
    global, counted per role."""
    now = _now()
    since = now - THROTTLE_WINDOW_SECONDS
    with _conn() as c:
        c.execute("DELETE FROM owner_login_failures WHERE at <= ?", (since,))
        mine = [r[0] for r in c.execute("SELECT at FROM owner_login_failures WHERE client_key=? AND role=? AND at > ? "
                                        "ORDER BY at", (key, role, since))]
        every = [r[0] for r in c.execute("SELECT at FROM owner_login_failures WHERE role=? AND at > ? ORDER BY at",
                                         (role, since))]
    waits = []
    if len(mine) >= THROTTLE_PER_CLIENT:
        waits.append(mine[-THROTTLE_PER_CLIENT] + THROTTLE_WINDOW_SECONDS - now)
    if len(every) >= THROTTLE_GLOBAL:
        waits.append(every[-THROTTLE_GLOBAL] + THROTTLE_WINDOW_SECONDS - now)
    return max(1, int(max(waits)) + 1) if waits else 0


def record_failure(key: str, role: str = ROLE_OWNER) -> None:
    with _conn() as c:
        c.execute("INSERT INTO owner_login_failures(client_key, at, role) VALUES(?,?,?)", (key, _now(), role))


def clear_failures(key: str, role: str = ROLE_OWNER) -> None:
    with _conn() as c:
        c.execute("DELETE FROM owner_login_failures WHERE client_key=? AND role=?", (key, role))


# ---------------- transport ----------------
_LOOPBACK_NAMES = {"localhost"}


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip().lower()
    if h in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h.strip("[]")).is_loopback
    except ValueError:
        return False


def _host_name(host_header: str | None) -> str:
    h = (host_header or "").strip()
    if h.startswith("["):                               # [::1]:8000
        return h[1:h.find("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def secure_transport(scheme: str, host_header: str | None, client_host: str | None) -> bool:
    """A password may cross the network only over HTTPS. Plain HTTP is accepted only when BOTH the browser and the
    address it typed are on this computer (http://127.0.0.1:8000 on the owner's own machine)."""
    if scheme == "https":
        return True
    return _is_loopback(client_host) and _is_loopback(_host_name(host_header))


def cookie_secure(scheme: str) -> bool:
    return scheme == "https" or (os.environ.get("HALSA_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes"))


def origin_ok(origin: str | None, scheme: str, host_header: str | None) -> bool:
    """A browser-sent Origin must be this server; requests without an Origin (non-browser tools) still need the CSRF
    token. ``null`` (sandboxed / file pages) is refused."""
    if origin is None:
        return True
    return bool(host_header) and origin.strip().lower() == f"{scheme}://{host_header.strip().lower()}"


# ---------------- command line (provisioning; nothing is printed except the hash line) ----------------
_ROLE_TR = {ROLE_OWNER: "Yönetici", ROLE_FINANCE: "Finans görüntüleme"}


def _cli(argv: list[str]) -> int:
    import getpass
    cmd = argv[0] if argv else ""
    flags = set(argv[1:])
    role = ROLE_FINANCE if "--finance" in flags else (ROLE_OWNER if "--owner" in flags or cmd == "hash-password"
                                                      else None)
    if cmd == "hash-password":
        pw = getpass.getpass(f"{_ROLE_TR[role]} şifresi (en az {MIN_PASSWORD_LENGTH} karakter): ")
        if pw != getpass.getpass("Şifre (tekrar): "):
            print("Şifreler eşleşmiyor; hiçbir şey yazılmadı.", file=sys.stderr)
            return 2
        other = ROLE_FINANCE if role == ROLE_OWNER else ROLE_OWNER
        other_hash = configured_hash(other)
        if other_hash is not None and verify_password(pw, other_hash):     # one password must never serve both roles
            print(f"Reddedildi: bu şifre {_ROLE_TR[other].lower()} şifresiyle aynı. Farklı bir şifre seçin.",
                  file=sys.stderr)
            return 2
        try:
            enc = hash_password(pw)
        except ValueError as e:
            print(f"Reddedildi: {e}", file=sys.stderr)
            return 2
        print(f"{PASSWORD_ENVS[role]}={enc}")
        print("Bu satırı yalnızca özel .env dosyanıza ekleyin (asla GitHub'a değil), sonra sunucuyu yeniden başlatın. "
              "Bu rolün eski oturumları kapanır.", file=sys.stderr)
        return 0
    if cmd == "revoke-sessions":
        ST.init_db()
        n = revoke_all(role)
        print(f"{n} oturum kapatıldı" + (f" ({_ROLE_TR[role].lower()})." if role else " (tüm roller)."))
        return 0
    if cmd == "status":
        ST.init_db()
        for r in ROLES:
            state = "yapılandırıldı" if configured_hash(r) else "YAPILANDIRILMADI (kapalı)"
            print(f"{_ROLE_TR[r]} girişi: {state} · açık oturum: {session_count(r)}")
        return 0
    print("Kullanım: python -m services.owner_auth {hash-password [--finance] | revoke-sessions [--owner|--finance] "
          "| status}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - exercised through _cli in tests
    import config  # noqa: F401  (loads the private .env so `status` / `revoke-sessions` use the real state DB)
    ST.DB_PATH = os.environ.get("AGENT_DB", ST.DB_PATH)
    raise SystemExit(_cli(sys.argv[1:]))
