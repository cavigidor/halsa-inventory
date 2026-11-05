"""M8-CP2: hosting-ready runtime — one mounted data root, the hosted start command, a data-free liveness probe,
persistence across a real stop / start, fail-closed restarts, and a consistent backup with an isolated restore.

The integration test runs the REAL start command (`deploy/start_hosted.sh` → Uvicorn, one worker, `app:app` with the
production accuracy gate and the real logins) on an EMPTY temporary "disk" (HALSA_DATA_ROOT only — no other path
variable), as the host would. Synthetic workbooks and synthetic passwords only.
"""
import datetime as dt
import hashlib
import http.cookies
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest
from fastapi.testclient import TestClient

import app as APP
from services import access as AX
from services import accuracy as ACC
from services import backup as BK
from services import generation as GEN
from services import owner_auth as AUTH
from services import paths as PATHS
from services import store as ST
from tests import browser_auth as BA
from tests import release_state as REL
from tests.fixtures import fair_synth as FX
from tests.test_m8_d073_cari_promotion import EXPECTED, _cari

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = os.path.join(ROOT, "deploy", "start_hosted.sh")
PATH_VARS = ("DATA_FOLDER", "UPLOAD_DIR", "AGENT_DB", "DASHBOARD_PATH")


# ---------------------------------------------------------------------------------------------
# 1. one data root
# ---------------------------------------------------------------------------------------------
def _documented_render_root():
    with open(os.path.join(ROOT, ".env.example")) as fh:
        roots = [line.strip().removeprefix("# HALSA_DATA_ROOT=") for line in fh
                 if line.startswith("# HALSA_DATA_ROOT=")]
    assert roots == ["/var/data"]       # Render creates this mount, not an application-specific child
    return roots[0]


def test_render_configuration_uses_the_mount_itself_and_derives_exact_paths():
    env = {PATHS.ROOT_ENV: _documented_render_root()}
    PATHS.apply(env)
    assert {k: env[k] for k in PATH_VARS} == {
        "DATA_FOLDER": "/var/data/data", "UPLOAD_DIR": "/var/data/uploads",
        "AGENT_DB": "/var/data/state/agent_state.db", "DASHBOARD_PATH": "/var/data/published/dashboard.html"}


def test_data_root_fills_every_persistent_path(tmp_path):
    env = {PATHS.ROOT_ENV: str(tmp_path)}
    PATHS.apply(env)
    assert env["DATA_FOLDER"] == str(tmp_path / "data") and env["UPLOAD_DIR"] == str(tmp_path / "uploads")
    assert env["AGENT_DB"] == str(tmp_path / "state" / "agent_state.db")
    assert env["DASHBOARD_PATH"] == str(tmp_path / "published" / "dashboard.html")
    PATHS.check({k: env[k] for k in PATH_VARS}, env)                  # creates the layout
    assert all((tmp_path / d).is_dir() for d in ("data", "uploads", "state", "published"))
    assert PATHS.apply({}) is None                                    # no root: nothing changes (local use)
    keep = {PATHS.ROOT_ENV: str(tmp_path), "DATA_FOLDER": str(tmp_path / "eski")}
    PATHS.apply(keep)
    assert keep["DATA_FOLDER"] == str(tmp_path / "eski")              # an explicit value wins (validated below)


@pytest.mark.parametrize("bad,code", [
    ({"DATA_FOLDER": "/tmp/halsa-elsewhere/data"}, "DATA_FOLDER is outside"),
    ({"AGENT_DB": "state/agent_state.db"}, "AGENT_DB must be an absolute path"),
    ({"DASHBOARD_PATH": "/opt/render/project/src/dashboard.html"}, "DASHBOARD_PATH is outside"),
    ({"KREDI_PATH": "/opt/render/project/src/kredi.xlsx"}, "KREDI_PATH is outside"),
])
def test_a_path_off_the_mounted_disk_refuses_to_start(tmp_path, bad, code):
    env = {PATHS.ROOT_ENV: str(tmp_path), **bad}
    PATHS.apply(env)
    with pytest.raises(PATHS.DataRootError, match=code):
        PATHS.check({k: env[k] for k in PATH_VARS}, env)


def test_the_root_itself_must_be_a_mounted_absolute_directory(tmp_path):
    for root, msg in (("relative/dir", "absolute"), (str(tmp_path / "nope"), "is the disk mounted")):
        env = {PATHS.ROOT_ENV: root}
        PATHS.apply(env)
        with pytest.raises(PATHS.DataRootError, match=msg):
            PATHS.check({k: env[k] for k in PATH_VARS}, env)


def test_symlink_escape_is_outside_the_root(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "mount"
    root.mkdir()
    (root / "data").symlink_to(outside, target_is_directory=True)     # data/ points off the disk
    env = {PATHS.ROOT_ENV: str(root)}
    PATHS.apply(env)
    with pytest.raises(PATHS.DataRootError, match="DATA_FOLDER is outside"):
        PATHS.check({k: env[k] for k in PATH_VARS}, env)


def test_atomic_writes_stage_next_to_their_target_and_fsync(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real(fd))
    tmp, dest = tmp_path / "x.new", tmp_path / "x"
    tmp.write_bytes(b"v2")
    dest.write_bytes(b"v1")
    PATHS.durable_replace(str(tmp), str(dest))
    assert dest.read_bytes() == b"v2" and not tmp.exists() and len(synced) >= 2     # file + directory
    GEN.write_json_atomic(str(tmp_path / "sources.json"), {"a": 1})
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".sel-")] == []


# ---------------------------------------------------------------------------------------------
# 2. GET /livez: public, constant, data-free, side-effect-free
# ---------------------------------------------------------------------------------------------
def test_livez_is_registered_as_a_public_data_free_route():
    assert AX.ACCESS[("GET", "/livez")] == AX.PUBLIC and ("GET", "/livez") in AX.NO_SESSION
    assert ACC.ROUTES[("GET", "/livez")] == ("system", None)
    assert {k for k, v in AX.ACCESS.items() if v == AX.PUBLIC} == {("GET", "/giris"), ("GET", "/livez")}
    assert AX.ACCESS[("GET", "/api/health")] == AX.VIEWER          # readiness / data state stays protected


def test_livez_reads_and_changes_nothing(monkeypatch, tmp_path):
    with TestClient(APP.app) as c:
        def boom(*a, **k):
            raise AssertionError("liveness must not touch this")
        monkeypatch.setattr(AUTH, "request_session", boom)            # no session lookup
        monkeypatch.setattr(ST, "_conn", boom)                        # no SQLite at all
        monkeypatch.setattr(APP, "accuracy_gate", boom)               # no gate / generation / evidence
        monkeypatch.setattr(APP.app.state.repo, "context", boom)      # no context build / import / refresh
        monkeypatch.setattr(APP.app.state.agent, "provider", type("P", (), {"__getattr__": boom})())   # no AI
        c.cookies.set(AUTH.COOKIE_NAME, "a-stale-or-forged-cookie")
        for r in (c.get("/livez"), c.get("/livez?x=1"), c.get("/livez", headers={"X-Forwarded-Proto": "https"})):
            assert r.status_code == 200 and r.json() == {"status": "ok"} and r.content == b'{"status":"ok"}'
            assert "set-cookie" not in r.headers and "x-halsa-generation" not in r.headers
            assert r.headers["cache-control"] == "no-store"
        h = c.head("/livez")
        assert h.status_code == 200 and h.content == b""
        assert c.post("/livez", headers={"X-StockAgent": "1"}).status_code == 405


@pytest.mark.production_auth
def test_health_stays_protected_while_livez_is_open(monkeypatch):
    monkeypatch.setenv(AUTH.VIEWER_PASSWORD_ENV, BA.TEST_VIEWER_HASH)
    with TestClient(APP.app) as c:
        assert c.get("/livez").status_code == 200
        r = c.get("/api/health")
        assert r.status_code == 401 and r.json()["detail"]["error_code"] == "site_auth_required"


# ---------------------------------------------------------------------------------------------
# 3. the real start command on an empty disk: publish, change, stop, start, fail closed, back up, restore
# ---------------------------------------------------------------------------------------------
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Http:
    """A browser-like client: one cookie jar, the owner's CSRF token, same-origin headers."""

    def __init__(self, base, cookie=None):
        self.base, self.cookie, self.csrf = base, cookie, None

    def call(self, method, path, body=None, raw=None, headers=None):
        h = {"X-StockAgent": "1", "Origin": self.base, "Accept": "application/json", **(headers or {})}
        if self.cookie:
            h["Cookie"] = f"{AUTH.COOKIE_NAME}={self.cookie}"
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        data = raw
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status, hdr, content = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, hdr, content = e.code, e.headers, e.read()
        jar = http.cookies.SimpleCookie()
        for v in hdr.get_all("Set-Cookie") or ():
            jar.load(v)
        if AUTH.COOKIE_NAME in jar:
            self.cookie = jar[AUTH.COOKIE_NAME].value or None
        return status, hdr, content

    def json(self, method, path, body=None, **kw):
        s, h, c = self.call(method, path, body, **kw)
        return s, (json.loads(c) if c else None), h

    def login(self, path="/api/auth/login", password=BA.TEST_PASSWORD):
        s, d, _ = self.json("POST", path, {"password": password})
        assert s == 200, d
        self.csrf = d["csrf_token"]
        return self


class Hosted:
    def __init__(self, mount, logdir):
        self.mount, self.logdir, self.n = mount, logdir, 0

    def env(self, port):
        env = {k: v for k, v in os.environ.items()
               if k not in PATH_VARS + PATHS.INPUT_PATHS + ("STOCKAGENT_NO_DOTENV", "ENABLE_AI", "AI_PROVIDER")}
        env.update({PATHS.ROOT_ENV: str(self.mount), "PORT": str(port), "FORWARDED_ALLOW_IPS": "*",
                    "MANUAL_INFLATION_RATE": "0.40",
                    "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "PYTHONPATH": ROOT,
                    "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", ""),
                    **BA.server_env()})
        return env

    def __enter__(self):
        self.n += 1
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = open(self.logdir / f"server{self.n}.log", "w")
        self.proc = subprocess.Popen(["sh", START], cwd=ROOT, env=self.env(self.port), stdout=self.log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 90
        while True:                                                   # readiness of the PROCESS = /livez
            if self.proc.poll() is not None:
                self.log.flush()
                pytest.fail("server exited:\n" + (self.logdir / f"server{self.n}.log").read_text()[-3000:])
            try:
                if Http(self.base).call("GET", "/livez")[0] == 200:
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("no liveness within 90 s")
            time.sleep(0.2)
        return self

    def ready(self, owner):
        """The data side: wait until the warm-up and Customer 360 have settled (any terminal state)."""
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            s, d, _ = owner.json("GET", "/api/customer360/status")
            if s == 200 and d.get("state") in ("ready", "error", "unavailable"):
                return d
            time.sleep(0.3)
        pytest.fail("Customer 360 did not settle")

    def __exit__(self, *exc):
        os.killpg(self.proc.pid, signal.SIGTERM)                      # the host's stop signal
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        self.log.close()
        return False


def _tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def _upload_publish(owner, src, files):
    s, b, _ = owner.json("POST", "/api/uploads")
    assert s == 201, b
    bid, asg = b["batch_id"], {}
    for slot, (path, label) in files.items():
        s, d, _ = owner.json("POST", f"/api/uploads/{bid}/files", raw=path.read_bytes(),
                             headers={"X-Upload-Name": urllib.parse.quote(label),
                                      "Content-Type": "application/octet-stream"})
        assert s == 201, d
        asg[d["file"]["file_id"]] = {"slot": slot, "declarations": {}}

    def wait(job):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            _, j, _ = owner.json("GET", f"/api/uploads/jobs/{job}")
            if j["state"] not in ("queued", "running"):
                return j
            time.sleep(0.2)
        pytest.fail("job did not finish")
    s, d, _ = owner.json("POST", f"/api/uploads/{bid}/validate", {"assignments": asg})
    assert s == 202, d
    wait(d["job_id"])
    _, b, _ = owner.json("GET", f"/api/uploads/{bid}")
    assert b["status"] == "ready", b
    s, d, _ = owner.json("POST", f"/api/uploads/{bid}/publish", {"preview_token": b["preview_token"]})
    assert s == 202, d
    j = wait(d["job_id"])
    assert j["state"] == "done" and j["batch"]["status"] == "published", j
    return bid


def _served(owner):
    s, h, page = owner.call("GET", "/")
    _, st, _ = owner.json("GET", "/api/accuracy/status")
    return s, h, page, st


def test_hosted_runtime_persists_across_restart_and_fails_closed(tmp_path):
    # Reproduce the documented Render mount under a temporary prefix, never the machine's real /var/data.
    mount = tmp_path / _documented_render_root().lstrip("/")
    mount.mkdir(parents=True)                                         # only the mount exists, no halsa/ child
    assert list(mount.iterdir()) == []
    src = tmp_path / "src"                                            # the owner's computer (synthetic exports)
    FX.write_all(str(src))
    day = dt.date.today() - dt.timedelta(days=5)
    _cari().to_excel(src / "cari.xlsx", index=False)
    files = {"stock": (src / FX.STOCK_FILE, FX.STOCK_FILE), "sales": (src / FX.SALES_FILE, FX.SALES_FILE),
             "aging": (src / "cari.xlsx", f"Cari {day.isoformat()}.xlsx")}
    code_dir_before = {p: os.path.exists(os.path.join(ROOT, p)) for p in ("dashboard.html",
                                                                          "dashboard.generation.json")}
    srv = Hosted(mount, tmp_path)

    with srv:                                                         # --- first start: fresh hosting
        assert all((mount / d).is_dir() for d in ("data", "uploads", "state", "published"))
        db = mount / "state" / "agent_state.db"
        assert db.is_file()
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
            assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"actions", "owner_sessions"} <= tables
        anon = Http(srv.base)
        s, h, c = anon.call("GET", "/livez")
        assert s == 200 and c == b'{"status":"ok"}' and "Set-Cookie" not in h
        assert anon.json("GET", "/api/health")[0] == 401
        s, h, c = anon.call("GET", "/")
        assert s == 401 and h["X-Halsa-Auth"] == "site_required" and b"const DATA" not in c
        # behind the host's TLS proxy: X-Forwarded-Proto from the trusted proxy address makes the request HTTPS —
        # the login works and the session cookie is Secure; the same request without it is refused (fail closed)
        proxied = {"Host": "halsa.example", "Origin": "https://halsa.example", "X-Forwarded-Proto": "https",
                   "X-Forwarded-For": "203.0.113.7"}
        for path, password, role in (("/api/auth/viewer-login", BA.TEST_VIEWER_PASSWORD, AUTH.ROLE_VIEWER),
                                      ("/api/auth/finance-login", BA.TEST_FINANCE_PASSWORD, AUTH.ROLE_FINANCE),
                                      ("/api/auth/login", BA.TEST_PASSWORD, AUTH.ROLE_OWNER)):
            proxy_client = Http(srv.base)
            s, _, h = proxy_client.json("POST", path, {"password": password}, headers=proxied)
            assert s == 200 and "secure" in h["Set-Cookie"].lower() and "Strict-Transport-Security" in h
            s, session, _ = proxy_client.json("GET", "/api/auth/session", headers=proxied)
            assert s == 200 and session["authenticated"] and session["role"] == role
        plain = {"Host": "halsa.example", "Origin": "http://halsa.example"}
        s, d, h = Http(srv.base).json("POST", "/api/auth/viewer-login", {"password": BA.TEST_VIEWER_PASSWORD},
                                      headers=plain)
        assert s == 403 and d["detail"]["error_code"] == "https_required" and "Set-Cookie" not in h
        owner = Http(srv.base).login()                                # 2. owner login (loopback HTTP is allowed)
        s, _, page, st = _served(owner)
        assert s in (404, 503) and b"const DATA" not in page and st["generation_id"] is None   # nothing yet
        _upload_publish(owner, src, files)                            # 3. upload, validate, publish
        s, h, page, st = _served(owner)                               # 4. the accepted generation, gated
        assert s == 200 and h["X-Halsa-Generation"] == st["generation_id"]
        assert GEN.dashboard_markers(page)[1] == ("receivables.collections",)
        assert sum(1 for m in st["metrics"] if m["state"] == "verified") == 1
        s, cari, _ = owner.json("GET", "/api/finance/cari")
        assert s == 200 and {r["code"]: r["balance"] for r in cari["rows"]} == EXPECTED
        viewer = Http(srv.base).login("/api/auth/viewer-login", BA.TEST_VIEWER_PASSWORD)
        assert viewer.json("GET", "/api/finance/cari")[0] == 401        # D-075 unchanged on the host
        assert viewer.json("POST", "/api/uploads")[0] == 403             # owner-only writes unchanged
        srv.ready(owner)
        s, crm, _ = owner.json("POST", f"/api/crm/customers/{FX.P[1]}/interactions",   # 5. a saved CRM change
                               {"occurred_on": dt.date.today().isoformat(), "channel": "phone",
                                "note": "Sentetik kalıcılık notu"})
        assert s == 200, crm
        core1, cookie1 = st["core_id"], owner.cookie
        layout = _tree(mount)
        # backup WHILE the server runs (SQLite online backup, publication lock)
        env = {PATHS.ROOT_ENV: str(mount)}
        bdir = BK.create(str(tmp_path / "backups"), include_uploads=True, env=env)

    assert {k for k in layout if k.split(os.sep)[0] in ("data", "published", "state", "uploads")} == set(layout)
    assert {"data/sources.json", "published/dashboard.html", "published/dashboard.generation.json",
            "state/agent_state.db"} <= {k.replace(os.sep, "/") for k in layout}
    assert {p: os.path.exists(os.path.join(ROOT, p)) for p in code_dir_before} == code_dir_before   # code dir untouched
    sel = json.load(open(mount / "data" / GEN.SELECTION_FILE))["core"]
    kept = {k: v for k, v in _tree(mount / "data").items()}

    with srv:                                                         # 6. stop + start on the same disk
        owner = Http(srv.base, cookie=cookie1)                        # the owner's session itself survived (SQLite)
        _, sess, _ = owner.json("GET", "/api/auth/session")
        assert sess["authenticated"] is True and sess["role"] == "owner"
        srv.ready(owner)
        s, h, page, st = _served(owner)                               # 7. same generation, served and gated
        assert s == 200 and st["core_id"] == core1 and h["X-Halsa-Generation"] == st["generation_id"]
        assert GEN.dashboard_markers(page)[1] == ("receivables.collections",)
        assert json.load(open(mount / "data" / GEN.SELECTION_FILE))["core"] == sel
        assert {k: v for k, v in _tree(mount / "data").items() if k in kept} == kept      # uploaded files unchanged
        s, cari, _ = owner.json("GET", "/api/finance/cari")
        assert s == 200 and {r["code"]: r["balance"] for r in cari["rows"]} == EXPECTED
        _, c, _ = owner.json("GET", f"/api/crm/customers/{FX.P[1]}")
        assert any(i.get("note") == "Sentetik kalıcılık notu" for i in c["interactions"]), c   # saved change kept
        _, up, _ = owner.json("GET", "/api/uploads")
        assert any(b["status"] == "published" for b in up["history"])   # Upload Center history kept

    aging = mount / "data" / sel["aging"]["file"]                     # 9a. a changed source while stopped
    original = aging.read_bytes()
    aging.write_bytes(original + b"x")
    with srv:
        owner = Http(srv.base).login()
        s, _, page, st = _served(owner)
        assert s == 503 and b"const DATA" not in page and st["generation_id"] is None      # 8. nothing obsolete
        assert owner.json("GET", "/api/finance/cari")[0] in (409,)                          # no Cari rows
    aging.write_bytes(original)
    (mount / "published" / "dashboard.generation.json").write_text("{bozuk")  # 9b. a broken publication record
    with srv:
        owner = Http(srv.base).login()
        s, _, page, st = _served(owner)
        assert s == 503 and b"const DATA" not in page and st["generation_id"] is None
        assert owner.json("GET", "/api/finance/cari")[0] == 409

    # --- restore the backup into a NEW directory and start the real server on it
    target = tmp_path / "restored"
    BK.restore(bdir, str(target))
    restored = Hosted(target, tmp_path)
    with restored:
        owner = Http(restored.base).login()
        restored.ready(owner)
        s, h, page, st = _served(owner)
        assert s == 200 and st["core_id"] == core1 and h["X-Halsa-Generation"] == st["generation_id"]
        s, cari, _ = owner.json("GET", "/api/finance/cari")
        assert s == 200 and {r["code"]: r["balance"] for r in cari["rows"]} == EXPECTED
        _, c, _ = owner.json("GET", f"/api/crm/customers/{FX.P[1]}")
        assert any(i.get("note") == "Sentetik kalıcılık notu" for i in c["interactions"])
    REL.assert_release_state()


# ---------------------------------------------------------------------------------------------
# 4. backup / restore safety (no server)
# ---------------------------------------------------------------------------------------------
def _fake_mount(root):
    root.mkdir()
    env = {PATHS.ROOT_ENV: str(root)}
    PATHS.apply(env)
    PATHS.check({k: env[k] for k in PATH_VARS}, env)
    (root / "data" / "yukleme-abc.xlsx").write_bytes(b"PK synthetic")
    GEN.write_json_atomic(str(root / "data" / GEN.SELECTION_FILE), {"schema": "x"})
    (root / "data" / "half.xlsx.kopya").write_bytes(b"in flight")    # temp file of an interrupted copy
    (root / "published" / "dashboard.html").write_text("<html>")
    (root / "published" / "dashboard.generation.json").write_text("{}")
    (root / "uploads" / "blob.xlsx").write_bytes(b"PK blob")
    con = sqlite3.connect(env["AGENT_DB"])
    con.execute("CREATE TABLE t(x)")
    con.execute("INSERT INTO t VALUES ('sentetik')")
    con.commit()
    return env, con


def test_backup_is_consistent_skips_temp_files_and_restores_only_into_an_empty_directory(tmp_path):
    env, live = _fake_mount(tmp_path / "disk")
    live.execute("BEGIN IMMEDIATE")                                   # a writer holds the database meanwhile
    live.execute("INSERT INTO t VALUES ('uncommitted')")
    out = BK.create(str(tmp_path / "b"), env=env)
    live.rollback()
    live.close()
    doc = BK.verify(out)
    rels = {f["path"] for f in doc["files"]}
    assert rels == {"state/agent_state.db", "data/yukleme-abc.xlsx", "data/sources.json",
                    "published/dashboard.html", "published/dashboard.generation.json"}   # no temp, no uploads
    con = sqlite3.connect(os.path.join(out, "state", "agent_state.db"))
    assert [r[0] for r in con.execute("SELECT x FROM t")] == ["sentetik"]   # committed state only
    con.close()
    assert oct(os.stat(out).st_mode & 0o777) == "0o700"
    with_uploads = BK.create(str(tmp_path / "b2"), env=env, include_uploads=True)
    assert "uploads/blob.xlsx" in {f["path"] for f in BK.verify(with_uploads)["files"]}
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("keep")
    with pytest.raises(BK.BackupError, match="new or empty"):
        BK.restore(out, str(busy))
    assert (busy / "x").read_text() == "keep"                         # nothing overwritten
    res = BK.restore(out, str(tmp_path / "new"))
    assert res["files"] == 5 and (tmp_path / "new" / "data" / "yukleme-abc.xlsx").read_bytes() == b"PK synthetic"


def test_a_changed_or_unsafe_backup_is_refused(tmp_path):
    env, live = _fake_mount(tmp_path / "disk")
    live.close()
    out = BK.create(str(tmp_path / "b"), env=env)
    victim = os.path.join(out, "data", "yukleme-abc.xlsx")
    open(victim, "ab").write(b"x")
    with pytest.raises(BK.BackupError, match="missing or changed"):
        BK.restore(out, str(tmp_path / "r1"))
    assert not (tmp_path / "r1").exists() or not os.listdir(tmp_path / "r1")
    out2 = BK.create(str(tmp_path / "b2"), env=env)
    m = json.load(open(os.path.join(out2, BK.MANIFEST)))
    m["files"].append({"path": "data/../../etc/x", "sha256": "0", "bytes": 1})
    json.dump(m, open(os.path.join(out2, BK.MANIFEST), "w"))
    with pytest.raises(BK.BackupError, match="unsafe"):
        BK.verify(out2)


def test_backup_waits_for_no_publication(tmp_path):
    from services import refresh as RF
    env, live = _fake_mount(tmp_path / "disk")
    live.close()
    with RF.refresh_lock(env["DASHBOARD_PATH"] + ".refresh.lock"):      # a publication is running
        with pytest.raises(BK.BackupError, match="publication is running"):
            BK.create(str(tmp_path / "b"), env=env)
    assert not (tmp_path / "b").exists() or os.listdir(tmp_path / "b") == []


def test_backup_cli_prints_no_values(tmp_path, monkeypatch, capsys):
    env, live = _fake_mount(tmp_path / "disk")
    live.close()
    monkeypatch.setenv(PATHS.ROOT_ENV, str(tmp_path / "disk"))
    for k in PATH_VARS:
        monkeypatch.setenv(k, env[k])
    assert BK._cli(["create", "--dest", str(tmp_path / "b")]) == 0
    out = capsys.readouterr().out
    assert "backup created" in out and "sentetik" not in out and "PK" not in out
    (bdir,) = os.listdir(tmp_path / "b")
    assert BK._cli(["verify", str(tmp_path / "b" / bdir)]) == 0
    assert BK._cli(["restore", str(tmp_path / "b" / bdir), "--target", str(tmp_path / "r")]) == 0
    shutil.rmtree(tmp_path / "r")
