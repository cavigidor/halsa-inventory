"""D-075 (owner decision 2026-11-03): account-level Cari balances need the financial password.

The published dashboard is the same bytes for every role, so it never embeds a Cari record; the factual list is served
only by GET /api/finance/cari to a financial-viewer or owner session, VERIFIED, bound to the served generation and
computed from the aging bytes of that generation. The real app, access middleware, accuracy gate and dashboard builder
decide (`production_gate`; real sessions after the owner's publication). Synthetic data only.
"""
import datetime as dt
import json
import os

import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as DB
from services import access as AX
from services import accuracy as ACC
from services import generation as GEN
from services import owner_auth as AUTH
from tests import accuracy_harness as H
from tests import auth_harness as AH
from tests import release_state as REL
from tests.test_m8_cp1_access import FIN_HASH, FIN_PASSWORD, HASH, PASSWORD, VIEW_HASH, VIEW_PASSWORD
from tests.test_m8_cp3_upload_center import H as HDR, publish, srv  # noqa: F401
from tests.test_m8_d072_cari_first_metric import _folder, _manifest
from tests.test_m8_d073_cari_promotion import EXPECTED, NAME, OCT5, _at, _cari, _data_blob, _publish_cari

KEY = "receivables.collections"
# account-level values of the synthetic export: none may reach a website-only session
CANARIES = ("120.01.0001", "120.01.0002", "320.01.0001", "Sentetik Musteri A", "Sentetik Musteri B",
            "Sentetik Tedarikci", "1500.25", "1500,25", "-1500", "2010.5", "2010,5", "-700", "Mart-2026")


def _no_cari_records(text):
    hits = [c for c in CANARIES if c in text]
    assert not hits, hits


# ---------------------------------------------------------------------------------------------
# 1. register and route classification
# ---------------------------------------------------------------------------------------------
def test_route_is_finance_only_and_gated_by_the_metric():
    assert AX.ACCESS[("GET", "/api/finance/cari")] == AX.FINANCE_READ
    assert ACC.ROUTES[("GET", "/api/finance/cari")] == ("metric", KEY)
    REL.assert_release_state()                                   # 33 metrics, only the Cari list certified, flag True
    assert ACC.REGISTER[KEY].max_source_age_days == 60 and ACC.POLICY_VERSION == "m8-cari-d075"


# ---------------------------------------------------------------------------------------------
# 2. the published page never embeds Cari records (even when the list is VERIFIED)
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_the_published_page_carries_no_account_level_cari_record(tmp_path):
    today = dt.date(2026, 11, 1)
    folder = _folder(tmp_path / "f", cari_name=NAME, snapshot=OCT5, aging=_cari())
    m = _manifest(folder, today=today)
    gate = ACC.current_gate(today=today).bound_to(m.core_snapshot())
    assert gate.decide(KEY).displayable
    paths, _, _ = GEN.select_core(folder)
    out = tmp_path / "page.html"
    DB.build(folder, str(out), today=today, sources=paths, frames=GEN.frames_from_frozen(GEN.freeze(paths)),
             gate=gate, manifest=m)
    html = out.read_text(encoding="utf-8")
    coll = _data_blob(html)["modules"]["collections"]
    assert coll["rows"] == [] and coll["finance"] == 1 and coll["endpoint"] == "/api/finance/cari"
    assert [c["k"] for c in coll["cols"]] == ["name", "rep", "class_label", "balance", "direction_label", "oldest"]
    assert "finans görüntüleme şifresi gerekir" in coll["finance_message"]
    assert "Cari rapor tarihi: 5 Ekim 2026" in coll["note"]                   # the date is not an account record
    assert f'<meta name="stockagent-displayed" content="{KEY}">' in html
    _no_cari_records(html)


# ---------------------------------------------------------------------------------------------
# 3. the real app: every role, raw responses
# ---------------------------------------------------------------------------------------------
def _real_sessions(monkeypatch):
    monkeypatch.setattr(AUTH, "request_session", AH.production_request_session)
    monkeypatch.setenv(AUTH.PASSWORD_ENV, HASH)
    monkeypatch.setenv(AUTH.FINANCE_PASSWORD_ENV, FIN_HASH)
    monkeypatch.setenv(AUTH.VIEWER_PASSWORD_ENV, VIEW_HASH)


class _Browser(TestClient):
    """Another browser on the RUNNING server: no new app startup (entering a TestClient re-runs the startup, i.e. a
    restart before the warm-up has rebuilt the context)."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _client(path=None, password=None):
    c = _Browser(APP.app, base_url="https://testserver", headers=HDR)
    if path:
        r = c.post(path, json={"password": password})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def viewer():
    return _client("/api/auth/viewer-login", VIEW_PASSWORD)


def finance():
    return _client("/api/auth/finance-login", FIN_PASSWORD)


def owner():
    return _client("/api/auth/login", PASSWORD)


VIEWER_APIS = ["/api/agent/actions", "/api/sales-queue", "/api/customer360/status", "/api/crm/summary",
               "/api/crm/followups?mode=all", "/api/crm/recent", "/api/fairs/campaigns", "/api/opportunities/summary",
               "/api/vkm/summary", "/api/health", "/api/agent/categories", "/api/macro", "/yukleme",
               "/api/agent/customer/120.01.0001", "/api/customer360/search?q=120", "/api/customer360/120.01.0001",
               "/api/fairs/candidates", "/api/opportunities/territories", "/api/auth/session"]


@pytest.mark.production_gate
def test_each_role_gets_exactly_its_cari_access(srv, monkeypatch):
    s, today = srv, dt.date.today()
    _, pj = publish(s, _publish_cari(s, today - dt.timedelta(days=10), _cari(), "cari_a"))
    assert pj["batch"]["status"] == "published"
    _real_sessions(monkeypatch)
    with _client() as anon:
        r = anon.get("/")
        assert r.status_code == 401 and "Site şifresi" in r.text
        _no_cari_records(r.text)
        r = anon.get("/api/finance/cari")
        assert r.status_code == 401 and set(r.json()) == {"detail"}
        _no_cari_records(r.text)
    with viewer() as v:
        page = v.get("/")
        assert page.status_code == 200 and GEN.dashboard_markers(page.content)[1] == (KEY,)
        coll = _data_blob(page.text)["modules"]["collections"]
        assert coll["rows"] == [] and coll["finance"] == 1
        _no_cari_records(page.text)                                  # raw HTML, inline JSON, script: nothing
        r = v.get("/api/finance/cari")
        assert r.status_code == 401 and r.json()["detail"]["error_code"] == "finance_auth_required"
        _no_cari_records(r.text)
        for path in VIEWER_APIS:                                     # no other viewer path carries Cari records
            _no_cari_records(v.get(path).text)
        for path in ("/api/context", "/api/accuracy/status", "/api/owner/overview", "/api/uploads"):
            assert v.get(path).status_code == 403, path
        assert v.post("/api/uploads").status_code == 403
    gen = None
    for role in (finance, owner):
        with role() as c:
            r = c.get("/api/finance/cari")
            assert r.status_code == 200, r.text
            assert "no-store" in r.headers["cache-control"]
            d = r.json()
            assert {x["code"]: x["balance"] for x in d["rows"]} == EXPECTED     # exact signed balances, all accounts
            assert d["count"] == 4 and d["report_date"] == (today - dt.timedelta(days=10)).isoformat()
            assert d["report_date_origin"] == "filename" and "dosya adındaki tarih" in d["date_note"]
            assert r.headers["X-Halsa-Generation"] == c.get("/").headers["X-Halsa-Generation"] == d["generation_id"]
            assert gen in (None, d["generation_id"])
            gen = d["generation_id"]
            rows = {x["code"]: x for x in d["rows"]}
            assert rows["120.01.0001"]["class_label"] == "Müşteri (120)"
            assert rows["320.01.0001"]["class_label"] == "Satıcı (320)"
            assert all("overdue" not in x and "priority" not in x and "risk" not in x for x in d["rows"])
            _no_cari_records(c.get("/").text)                       # the page is the same for every role
            assert c.get("/api/finance/overview").status_code == 200
    with finance() as f:
        assert f.post("/api/uploads").status_code == 403            # financial password: still no owner right
        assert f.get("/api/owner/overview").status_code == 403
    with owner() as o:
        assert o.post("/api/uploads").status_code == 201            # owner-only writes unchanged


@pytest.mark.production_gate
def test_logout_revocation_and_expiry_remove_cari_access(srv, monkeypatch):
    s = srv
    _, pj = publish(s, _publish_cari(s, dt.date.today() - dt.timedelta(days=3), _cari(), "cari_a"))
    assert pj["batch"]["status"] == "published"
    _real_sessions(monkeypatch)
    with finance() as f:
        assert f.get("/api/finance/cari").status_code == 200
        assert f.post("/api/auth/logout").status_code == 200
        assert f.get("/api/finance/cari").status_code == 401
    with finance() as f, owner() as o:
        assert f.get("/api/finance/cari").status_code == 200
        assert o.post("/api/auth/finance-sessions/revoke").json()["revoked"] >= 1
        r = f.get("/api/finance/cari")
        assert r.status_code == 401 and "120.01.0001" not in r.text
        assert o.get("/api/finance/cari").status_code == 200        # the owner session is not affected
    clock = {"t": AUTH._now()}
    monkeypatch.setattr(AUTH, "_now", lambda: clock["t"])
    with finance() as f:
        assert f.get("/api/finance/cari").status_code == 200
        clock["t"] += AUTH.session_idle_seconds() + 1
        assert f.get("/api/finance/cari").status_code == 401
    monkeypatch.setenv(AUTH.FINANCE_PASSWORD_ENV, AUTH.hash_password("yeni-finans-parolasi-2026", n=2 ** 14))
    with _client() as f2:                                            # old financial password no longer works
        assert f2.post("/api/auth/finance-login", json={"password": FIN_PASSWORD}).status_code == 401
        assert f2.get("/api/finance/cari").status_code == 401


# ---------------------------------------------------------------------------------------------
# 4. evidence: stale, mismatched, other-generation → withheld for every role
# ---------------------------------------------------------------------------------------------
@pytest.mark.production_gate
def test_stale_changed_or_replaced_evidence_is_withheld_for_every_role(srv, monkeypatch):
    s, today = srv, dt.date.today()
    day = today - dt.timedelta(days=10)
    _, pj = publish(s, _publish_cari(s, day, _cari(), "cari_a"))
    assert pj["batch"]["status"] == "published"
    _real_sessions(monkeypatch)
    with finance() as f, owner() as o:
        first = f.get("/api/finance/cari").json()
        _at(monkeypatch, day + dt.timedelta(days=60))
        assert f.get("/api/finance/cari").status_code == 200       # day 60: still fresh
        _at(monkeypatch, day + dt.timedelta(days=61))               # day 61: stale for everyone
        for c in (f, o):
            r = c.get("/api/finance/cari")
            assert r.status_code == ACC.WITHHELD_HTTP_STATUS and r.json()["detail"]["error_code"] == "accuracy_withheld"
            _no_cari_records(r.text)
        monkeypatch.setattr(ACC, "current_gate", H.production_gate)   # back to today
        warm = f.get("/api/finance/cari")                             # populates the row cache for this generation
        assert warm.status_code == 200 and warm.json()["rows"]
        sel = json.load(open(s.data / GEN.SELECTION_FILE))["core"]["aging"]["file"]
        src = s.data / sel
        original = src.read_bytes()

        def withheld(c):
            r = c.get("/api/finance/cari")
            assert r.status_code == ACC.WITHHELD_HTTP_STATUS, r.text
            assert r.json()["detail"]["error_code"] == ACC.WITHHELD_CODE
            assert set(r.json()["detail"]) <= {"error_code", "message"}       # the fixed neutral answer only
            assert sel not in r.text and str(s.data) not in r.text and "Traceback" not in r.text
            _no_cari_records(r.text)

        # the selected Cari bytes change on disk under the accepted generation; the WARM cache is not cleared by the
        # test: every request re-verifies the current file against the manifest before any (cached) row is returned
        src.write_bytes(original + b"x")
        for c in (f, o):
            withheld(c)
        src.write_bytes(original)                                     # the exact original bytes: shown again
        back = f.get("/api/finance/cari")
        assert back.status_code == 200 and back.json()["rows"] == warm.json()["rows"]
        # the selected file is missing (still a warm cache): fail closed, no server error, no private detail
        hidden = s.data.parent / (sel + ".moved")
        os.replace(src, hidden)
        try:
            for c in (f, o):
                withheld(c)
        finally:
            os.replace(hidden, src)
        assert f.get("/api/finance/cari").json()["rows"] == warm.json()["rows"]
    # a new monthly report: a new generation; the previous rows are never served again
    monkeypatch.setattr(AUTH, "request_session", AH.harness_request_session)    # the owner publishes (harness)
    new = _cari(amounts={"120.01.0001": {"Mart-2026": -1200.0}})
    _, pj2 = publish(s, _publish_cari(s, today - dt.timedelta(days=2), new, "cari_b"))
    assert pj2["batch"]["status"] == "published"
    _real_sessions(monkeypatch)
    with finance() as f:
        d = f.get("/api/finance/cari").json()
        assert d["generation_id"] != first["generation_id"]
        assert {x["code"]: x["balance"] for x in d["rows"]}["120.01.0001"] == -1200.0
        assert d["report_date"] == (today - dt.timedelta(days=2)).isoformat()


# ---------------------------------------------------------------------------------------------
# 5. Customer 360: the account-level Cari fields need the financial password too
# ---------------------------------------------------------------------------------------------
def _det():
    return {"receivables": {"accounts": [{"account_code": "120.01.0001", "aging": {
        "available": True, "present_in_file": True, "balance": -1500.25, "oldest_bucket": "Mart-2026",
        "account_class": "customer", "class_label": "Müşteri (120)", "direction": "credit",
        "direction_label": "Halsa müşteriye borçlu", "overdue": None, "overdue_status": "unavailable",
        "confidence": "trusted"}}]}}


@pytest.mark.parametrize("role,redacted", [(None, True), (AUTH.ROLE_VIEWER, True), (AUTH.ROLE_FINANCE, False),
                                           (AUTH.ROLE_OWNER, False)])
def test_customer360_cari_fields_follow_the_financial_password(role, redacted):
    sess = None if role is None else AUTH.Session("t", "c", 0.0, 9e9, 9e9, role)
    ag = APP._c360_cari_redacted(_det(), sess)["receivables"]["accounts"][0]["aging"]
    if redacted:
        assert ag["balance"] is None and ag["oldest_bucket"] is None and ag["direction"] is None
        assert ag["direction_label"] is None and ag["finance_required"] is True
        assert ag["class_label"] == "Müşteri (120)" and ag["present_in_file"] is True      # non-financial stays
    else:
        assert ag["balance"] == -1500.25 and ag["oldest_bucket"] == "Mart-2026" and "finance_required" not in ag


@pytest.fixture(scope="module")
def c360_world(tmp_path_factory):
    """The synthetic Customer 360 workbooks (tests/fixtures/c360_synth.py: invented 910.xx codes) with a real context,
    a real Customer 360 repository and the accuracy harness gate, so the real endpoint answers a schema-valid 200."""
    from services import context as CTX
    from services import customer360 as C360
    from services.entity import ContextRepository
    from tests.fixtures import c360_synth as F
    data = F.write_all(str(tmp_path_factory.mktemp("c360_d075")))
    ctx = CTX.build_context(data, inflation_rate=0.40)
    repo = ContextRepository(data_folder=data, context=ctx)
    return repo, C360.Customer360Repository(data, repo, background=False)


C360_CODE = "910.00.0001"            # synthetic: one own account with a positive balance + a zero-balance account
FIN_FIELDS = ("balance", "oldest_bucket", "direction", "direction_label")


def _c360_as(role, c360_world, monkeypatch):
    repo, c360 = c360_world
    sess = AUTH.Session("t", "c", 0.0, 9e9, 9e9, role)
    monkeypatch.setattr(AUTH, "request_session", lambda request: sess)
    APP.app.dependency_overrides[APP.get_repo] = lambda: repo
    APP.app.dependency_overrides[APP.get_c360] = lambda: c360
    try:
        with TestClient(APP.app, base_url="https://testserver", headers=HDR) as c:
            r = c.get(f"/api/customer360/{C360_CODE}")
    finally:
        APP.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text                                # the real, schema-validated response
    return r.json()


def test_customer360_endpoint_redacts_cari_fields_for_website_viewers(c360_world, monkeypatch):
    """The real endpoint, schema-valid 200 responses for the same customer: the website viewer gets null balance,
    invoice month and sign (+ finance_required); the financial viewer and the owner keep them; everything else in the
    profile (codes, names, order facts, class label, CRM, sales …) is identical for all three."""
    fin = _c360_as(AUTH.ROLE_FINANCE, c360_world, monkeypatch)
    own = _c360_as(AUTH.ROLE_OWNER, c360_world, monkeypatch)
    web = _c360_as(AUTH.ROLE_VIEWER, c360_world, monkeypatch)
    assert fin == own
    accs = {a["account_code"]: a for a in fin["receivables"]["accounts"]}
    assert accs["910.00.0001"]["aging"]["balance"] == 2000.0 and accs["910.00.0001"]["aging"]["direction"] == "debit"
    assert accs["910.00.0001"]["aging"]["oldest_bucket"] and accs["910.00.0001"]["aging"]["direction_label"]
    assert all(a["aging"]["finance_required"] is False for a in accs.values())
    for a in web["receivables"]["accounts"]:
        ag = a["aging"]
        assert ag["finance_required"] is True and all(ag[k] is None for k in FIN_FIELDS)
        assert ag["present_in_file"] is True and ag["class_label"] == accs[a["account_code"]]["aging"]["class_label"]
    strip = lambda d: {**d, "receivables": {**d["receivables"], "accounts": [
        {**a, "aging": {k: v for k, v in a["aging"].items() if k not in FIN_FIELDS + ("finance_required",)}}
        for a in d["receivables"]["accounts"]]}}
    assert strip(web) == strip(fin)                                     # nonfinancial content unchanged
    assert "2000.0" not in json.dumps(web["receivables"])
