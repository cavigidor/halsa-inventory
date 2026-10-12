"""M5-5: controlled refresh (design §9, D-019, D-032). Synthetic workbooks in temporary folders only;
spy/mock providers only (no key, no network, no paid call)."""
import datetime as dt
import os
import subprocess
import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as DB
from services import context as CTX
from services import refresh as RF
from services import store as ST
from services.agent import BusinessActionAgent
from services.ai.mock_provider import MockProvider
from services.entity import ContextRepository
from tests.fixtures import synthetic_erp as SE

H = {"X-StockAgent": "1"}
TODAY = dt.date(2026, 10, 5)
BASE_DATE = "2026-06-30"          # latest date in the synthetic sales file
NEW_DATE = "2026-07-15"


class SpyProvider(MockProvider):
    def __init__(self):
        self.calls, self.model = 0, "mock-1"

    def generate_structured(self, *a, **k):
        self.calls += 1
        return super().generate_structured(*a, **k)


# ---------------- synthetic workbook helpers ----------------
def write(folder, *, sales=None, stock=None, aging="default", extra_sales_rows=()):
    os.makedirs(folder, exist_ok=True)
    rows = (sales if sales is not None else SE.sales_rows()) + list(extra_sales_rows)
    pd.DataFrame(rows).to_excel(os.path.join(folder, SE.SALES_FILE), index=False)
    st = SE.stock_frame() if stock is None else stock
    with pd.ExcelWriter(os.path.join(folder, SE.STOCK_FILE)) as w:
        pd.DataFrame([["Sentetik stok raporu"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        st.to_excel(w, index=False, startrow=3)
    if aging is not None:
        (SE.aging_frame() if isinstance(aging, str) else aging).to_excel(os.path.join(folder, SE.AGING_FILE), index=False)
    return folder


def newer_row(date=NEW_DATE):
    return SE._row(date, "900.00.0006", "Sentetik Aktif", "900.00.0006", "Sentetik Aktif", 5_000)


@pytest.fixture
def env(tmp_path):
    data = write(str(tmp_path / "data"))
    return {"data": data, "dash": str(tmp_path / "dashboard.html"), "dir": str(tmp_path)}


def run(env, **kw):
    kw.setdefault("today", TODAY)
    kw.setdefault("inflation_rate", 0.40)
    return RF.run_refresh(env["data"], env["dash"], **kw)


def staging_files(env):
    return sorted(f for f in os.listdir(env["dir"]) if f.endswith((".new", ".tmp", ".lock")))


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# =============== validator (1–14) ===============
def test_valid_stock_sales_aging_pass(env):
    v = RF.validate(DB.select_sources(env["data"]), today=TODAY)
    assert v["ok"] and v["errors"] == [] and v["as_of_data_date"] == BASE_DATE
    assert set(v["frames"]) == {"stock", "sales", "aging"}
    assert all(c["ok"] for c in v["checks"])


def test_valid_without_aging_passes(tmp_path):
    data = write(str(tmp_path / "d"), aging=None)
    v = RF.validate(DB.select_sources(data), today=TODAY)
    assert v["ok"] and "aging" not in v["frames"]
    assert any("Cari" in w for w in v["warnings"])


@pytest.mark.parametrize("missing,needle", [(SE.STOCK_FILE, "Stok dosyası bulunamadı"),
                                            (SE.SALES_FILE, "Satış dosyası bulunamadı")])
def test_missing_required_file_fails(env, missing, needle):
    os.remove(os.path.join(env["data"], missing))
    v = RF.validate(DB.select_sources(env["data"]), today=TODAY)
    assert not v["ok"] and any(needle in e for e in v["errors"])


@pytest.mark.parametrize("victim", [SE.STOCK_FILE, SE.SALES_FILE, SE.AGING_FILE])
def test_unreadable_workbook_fails(env, victim):
    with open(os.path.join(env["data"], victim), "wb") as f:
        f.write(b"this is not an excel workbook")
    v = RF.validate(DB.select_sources(env["data"]), today=TODAY)
    assert not v["ok"] and any("okunamadı" in e and victim in e for e in v["errors"])


def test_missing_required_stock_column_fails(tmp_path):
    data = write(str(tmp_path / "d"), stock=SE.stock_frame().drop(columns=["maxseviye"]))
    v = RF.validate(DB.select_sources(data), today=TODAY)
    assert not v["ok"] and any("'maxseviye'" in e and "Başlık satırı 4" in e for e in v["errors"])


@pytest.mark.parametrize("col", ["Tarih", "Pro_kodu", "Musterikod", "sto_kod", "anagrup", "sip_evrakno_sira"])
def test_missing_required_sales_column_fails(tmp_path, col):
    rows = [{k: v for k, v in r.items() if k != col} for r in SE.sales_rows()]
    data = write(str(tmp_path / "d"), sales=rows)
    v = RF.validate(DB.select_sources(data), today=TODAY)
    assert not v["ok"] and any(repr(col) in e for e in v["errors"])


def test_optional_sales_columns_stay_optional(tmp_path):
    rows = [{k: v for k, v in r.items() if k not in ("Temsilci", "Sektör", "Net_Tutar_Maliyet_Dusulmus")}
            for r in SE.sales_rows()]
    data = write(str(tmp_path / "d"), sales=rows)
    v = RF.validate(DB.select_sources(data), today=TODAY)
    assert v["ok"] and any("isteğe bağlı" in w for w in v["warnings"])


@pytest.mark.parametrize("variant,needle", [
    ("no_key", "'Hesap kodu'"),
    ("no_buckets", "yaşlandırma sütunları"),
    ("no_accounts", "hesap satırı yok"),
])
def test_malformed_aging_fails_when_selected(tmp_path, variant, needle):
    ag = SE.aging_frame()
    if variant == "no_key":
        ag = ag.drop(columns=["Hesap kodu"])
    elif variant == "no_buckets":
        ag = ag[["Hesap kodu", "Hesap adı", "Temsilci kodu", "Toplam Döviz Bakiye"]]
    else:
        ag = ag[ag["Hesap kodu"] == "TOPLAM"]
    data = write(str(tmp_path / "d"), aging=ag)
    v = RF.validate(DB.select_sources(data), today=TODAY)
    assert not v["ok"] and any(needle in e for e in v["errors"])


def test_single_overdue_column_aging_format_is_supported(tmp_path):
    ag = pd.DataFrame([{"Hesap kodu": "900.00.0002", "Hesap adı": "Sentetik T2 Ofis", "Temsilci kodu": "TEMSILCI-B",
                        "Vadesi geçen bakiye": 8_000}])
    data = write(str(tmp_path / "d"), aging=ag)
    assert RF.validate(DB.select_sources(data), today=TODAY)["ok"]


@pytest.mark.parametrize("which", ["stock", "sales"])
def test_empty_required_dataset_fails(tmp_path, which):
    if which == "stock":
        data = write(str(tmp_path / "d"), stock=SE.stock_frame().iloc[0:0])
        needle = "ürün satırı yok"
    else:
        data = write(str(tmp_path / "d"), sales=[])
        pd.DataFrame(columns=list(SE.sales_rows()[0])).to_excel(os.path.join(data, SE.SALES_FILE), index=False)
        needle = "satır yok"
    v = RF.validate(DB.select_sources(data), today=TODAY)
    assert not v["ok"] and any(needle in e for e in v["errors"])


def test_sales_without_parseable_dates_fails(tmp_path):
    rows = [{**r, "Tarih": "tarih değil"} for r in SE.sales_rows()]
    data = write(str(tmp_path / "d"), sales=rows)
    v = RF.validate(DB.select_sources(data), today=TODAY)
    assert not v["ok"] and v["as_of_data_date"] is None
    assert any("veri tarihi belirlenemedi" in e for e in v["errors"])


def test_older_data_date_than_live_fails(env):
    v = RF.validate(DB.select_sources(env["data"]), previous_date="2026-07-31", today=TODAY)
    assert not v["ok"] and any("eski" in e and BASE_DATE in e and "2026-07-31" in e for e in v["errors"])


@pytest.mark.parametrize("previous", [BASE_DATE, "2026-05-31", None])
def test_same_or_newer_data_date_is_allowed(env, previous):
    assert RF.validate(DB.select_sources(env["data"]), previous_date=previous, today=TODAY)["ok"]


def test_report_lists_exact_selected_filenames_and_no_paths(env):
    rep = run(env)
    assert rep["sources"] == {"stock": SE.STOCK_FILE, "sales": SE.SALES_FILE, "aging": SE.AGING_FILE}
    blob = str(rep)
    assert env["dir"] not in blob and "/" not in "".join(v for v in rep["sources"].values() if v)


# =============== pipeline (15–32) ===============
def test_validation_and_build_use_the_same_frozen_sources(env, monkeypatch):
    """A newer file that appears after validation is ignored: build uses the validated frames."""
    reads, seen = [], {}
    for name in ("read_stock", "read_sales_raw", "read_aging_raw"):
        orig = getattr(DB, name)
        monkeypatch.setattr(DB, name, (lambda o, n: lambda p: (reads.append((n, os.path.basename(p))), o(p))[1])(orig, name))
    orig_validate, orig_ctx, orig_build = RF.validate, CTX.build_context, DB.build

    def validate_then_drop_newer(sources, *a, **k):
        v = orig_validate(sources, *a, **k)
        seen["validated"] = (dict(sources), v["frames"]["sales"])
        rows = SE.sales_rows() + [newer_row("2026-09-30")]
        pd.DataFrame(rows).to_excel(os.path.join(env["data"], "Zzz yeni satislar.xlsx"), index=False)
        os.utime(os.path.join(env["data"], "Zzz yeni satislar.xlsx"), (2_000_000_000, 2_000_000_000))
        return v

    def ctx_spy(folder, **k):
        seen["ctx"] = (k["sources"], k["frames"]["sales"])
        return orig_ctx(folder, **k)

    def build_spy(folder, out, **k):
        seen["dash"] = (k["sources"], k["frames"]["sales"])
        return orig_build(folder, out, **k)

    monkeypatch.setattr(RF, "validate", validate_then_drop_newer)
    monkeypatch.setattr(CTX, "build_context", ctx_spy)
    monkeypatch.setattr(DB, "build", build_spy)
    rep = run(env)
    assert rep["ok"] and rep["as_of_data_date"] == BASE_DATE and rep["sources"]["sales"] == SE.SALES_FILE
    src, frame = seen["validated"]
    assert seen["ctx"][0] == src == seen["dash"][0]
    assert seen["ctx"][1] is frame and seen["dash"][1] is frame, "same in-memory frame, not a re-read"
    assert sorted(n for n, _ in reads) == ["read_aging_raw", "read_sales_raw", "read_stock"], "each source read once"
    assert f'data-asof="{BASE_DATE}"' in read(env["dash"])


def test_nothing_goes_live_before_verification(env, monkeypatch):
    run(env)                                       # first build: a live dashboard exists
    old_html = read(env["dash"])
    write(env["data"], extra_sales_rows=[newer_row()])
    repo = ContextRepository(context=CTX.build_context(env["data"], inflation_rate=0.4))
    old_ctx = repo.current()
    installed = []
    orig_verify = RF.verify

    def verify_spy(ctx, stats, html_path, *a, **k):
        assert installed == [] and repo.current() is old_ctx, "staged context not installed yet"
        assert read(env["dash"]) == old_html, "staged dashboard not live yet"
        assert html_path.endswith(".new") and os.path.exists(html_path)
        return orig_verify(ctx, stats, html_path, *a, **k)

    monkeypatch.setattr(RF, "verify", verify_spy)
    rep = run(env, previous_context=old_ctx, install=lambda c: (installed.append(c), repo.replace_context(c)))
    assert rep["ok"] and len(installed) == 1 and repo.current() is installed[0]


def test_context_and_dashboard_totals_reconcile(env):
    v = RF.validate(DB.select_sources(env["data"]), today=TODAY)
    src = DB.select_sources(env["data"])
    ctx = CTX.build_context(env["data"], inflation_rate=0.4, sources=src, frames=v["frames"])
    stats = {}
    staged = env["dash"] + ".new"
    DB.build(env["data"], staged, today=TODAY, sources=src, frames=v["frames"], stats_out=stats)
    ok = RF.verify(ctx, stats, staged, BASE_DATE)
    assert ok["ok"], ok["errors"]
    assert ok["counts"]["overdue_total"] == round(stats["overdue_total"]) and ok["counts"]["overstock_value"] == round(stats["overstock_value"])
    bad = RF.verify(ctx, {**stats, "overdue_total": stats["overdue_total"] + 1}, staged, BASE_DATE)
    assert not bad["ok"] and any("vadesi geçen toplam" in e for e in bad["errors"])
    wrong_date = RF.verify(ctx, stats, staged, "2026-07-01")
    assert not wrong_date["ok"]


def _assert_untouched(env, old_html, old_prev, repo=None, old_ctx=None):
    assert read(env["dash"]) == old_html
    prev = env["dash"].replace(".html", ".prev.html")
    assert (read(prev) if os.path.exists(prev) else None) == old_prev
    if repo is not None:
        assert repo.current() is old_ctx
    assert staging_files(env) == []


@pytest.mark.parametrize("phase", ["validate", "build", "verify", "busy"])
def test_failure_in_any_phase_changes_nothing(env, monkeypatch, phase):
    run(env)
    run(env)                                         # now both dashboard.html and dashboard.prev.html exist
    old_html, old_prev = read(env["dash"]), read(env["dash"].replace(".html", ".prev.html"))
    repo = ContextRepository(context=CTX.build_context(env["data"], inflation_rate=0.4))
    old_ctx = repo.current()
    write(env["data"], extra_sales_rows=[newer_row()])
    if phase == "validate":
        os.remove(os.path.join(env["data"], SE.STOCK_FILE))
    elif phase == "build":
        monkeypatch.setattr(CTX, "build_context", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    elif phase == "verify":
        monkeypatch.setattr(DB, "_ai_panel", lambda meta: ("", ""))        # staged HTML misses the AI tab
    spy = SpyProvider()
    APP.app.state.provider = spy
    if phase == "busy":
        with RF.refresh_lock(RF.paths_for(env["dash"])["lock"]):
            rep = run(env, previous_context=old_ctx, install=repo.replace_context)
        assert staging_files(env) == []
    else:
        rep = run(env, previous_context=old_ctx, install=repo.replace_context)
    assert rep["ok"] is False and rep["step"] == phase and rep["previous_data_kept"] is True
    assert rep["as_of_data_date"] == BASE_DATE
    _assert_untouched(env, old_html, old_prev, repo, old_ctx)
    assert spy.calls == 0


def test_success_keeps_previous_and_installs_new_dashboard_and_context(env, monkeypatch):
    run(env)
    first = read(env["dash"])
    repo = ContextRepository(context=CTX.build_context(env["data"], inflation_rate=0.4))
    repo._build_indices()
    assert repo._cust_idx is not None
    write(env["data"], extra_sales_rows=[newer_row()])
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(RF.os, "replace", lambda a, b: (replaced.append((os.path.basename(a), os.path.basename(b))),
                                                        real_replace(a, b))[1])
    rep = run(env, previous_context=repo.current(), install=repo.replace_context)
    assert rep["ok"] and rep["step"] == "done" and rep["previous_as_of_data_date"] == BASE_DATE
    assert rep["as_of_data_date"] == NEW_DATE and rep["previous_data_kept"] is False
    assert ("dashboard.html.new", "dashboard.html") in replaced, "atomic os.replace of the staged file"
    assert read(env["dash"].replace(".html", ".prev.html")) == first
    assert f'data-asof="{NEW_DATE}"' in read(env["dash"])
    assert repo.current()["as_of_data_date"] == NEW_DATE
    assert repo._cust_idx is None and repo._prod_idx is None, "indices invalidated"
    assert repo.customer("900.00.0006")["name"] == "Sentetik Aktif"
    assert staging_files(env) == []


def test_install_failure_restores_previous_dashboard(env):
    run(env)
    old = read(env["dash"])
    write(env["data"], extra_sales_rows=[newer_row()])

    def boom(ctx):
        raise RuntimeError("install failed")
    rep = run(env, install=boom)
    assert rep["ok"] is False and rep["step"] == "swap" and "geri yüklendi" in rep["message"]
    assert rep["previous_data_kept"] is True and read(env["dash"]) == old
    assert staging_files(env) == []


def test_action_state_history_and_ai_cache_survive_refresh(env):
    spy = SpyProvider()
    repo = ContextRepository(context=CTX.build_context(env["data"], inflation_rate=0.4))
    APP.app.state.repo, APP.app.state.provider, APP.app.state.agent = repo, spy, BusinessActionAgent(spy)
    c = TestClient(APP.app, headers=H)
    ids = [a["action_id"] for a in c.get("/api/agent/actions").json()["actions"]]
    c.post("/api/agent/actions/enrich", json={"action_ids": ids[:2]})
    c.post(f"/api/actions/{ids[2]}/complete")
    agent = APP.app.state.agent
    with ST._conn() as con:
        hist_before = con.execute("SELECT COUNT(*) FROM action_history").fetchone()[0]
    statuses = ST.get_statuses()
    cache = ST.cache_get_many(ids, agent.prompt_version, agent.model_key)
    calls = spy.calls
    rep = run(env, previous_context=repo.current(), install=repo.replace_context)
    assert rep["ok"]
    assert ST.get_statuses() == statuses
    with ST._conn() as con:
        assert con.execute("SELECT COUNT(*) FROM action_history").fetchone()[0] == hist_before
    assert ST.cache_get_many(ids, agent.prompt_version, agent.model_key) == cache and len(cache) == 2
    after = c.get("/api/agent/actions").json()["actions"]
    assert ids[2] not in [a["action_id"] for a in after], "completed identical action not reopened"
    assert sum(a["ai_cached"] for a in after) == 2, "unchanged facts keep their cached AI text"
    assert spy.calls == calls, "refresh makes zero provider calls"


def test_refresh_module_never_touches_ai():
    imports = [l.strip() for l in read(RF.__file__).splitlines() if l.strip().startswith(("import ", "from "))]
    assert imports and not [l for l in imports if "ai" in l.split() or ".ai" in l or "agent" in l or "openai" in l]
    assert "generate_structured" not in read(RF.__file__)


def test_lock_is_exclusive_across_processes_and_released(env):
    lock = RF.paths_for(env["dash"])["lock"]
    holder = subprocess.Popen([sys.executable, "-c", (
        "import fcntl,os,sys,time\n"
        f"fd=os.open({lock!r},os.O_RDWR|os.O_CREAT,0o600); fcntl.flock(fd,fcntl.LOCK_EX)\n"
        "print('locked',flush=True); time.sleep(30)")], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        rep = run(env)
        assert rep["step"] == "busy" and rep["ok"] is False and not os.path.exists(env["dash"])
        cli = subprocess.run([sys.executable, "-m", "services.refresh", "--data", env["data"], "--dashboard", env["dash"]],
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), capture_output=True,
                             text=True, timeout=120, env={**os.environ, "STOCKAGENT_NO_DOTENV": "1"})
        assert cli.returncode == 3 and "kilit" in cli.stdout and "Başka bir yenileme" in cli.stdout
    finally:
        holder.kill()
        holder.wait()
    assert run(env)["ok"], "lock released when the holding process ends"
    assert not os.path.exists(lock)


def test_failed_refresh_releases_the_lock(env, monkeypatch):
    monkeypatch.setattr(CTX, "build_context", lambda *a, **k: (_ for _ in ()).throw(MemoryError()))
    assert run(env)["step"] == "build"
    assert not os.path.exists(RF.paths_for(env["dash"])["lock"])
    monkeypatch.undo()
    assert run(env)["ok"]


def test_cli_and_api_share_the_same_pipeline(env, monkeypatch):
    calls = []
    orig = RF._pipeline
    monkeypatch.setattr(RF, "_pipeline", lambda *a, **k: (calls.append("pipeline"), orig(*a, **k))[1])
    assert RF.main(["--data", env["data"], "--dashboard", env["dash"]]) == 0
    monkeypatch.setattr(APP, "DATA_FOLDER", env["data"])
    monkeypatch.setattr(APP, "DASHBOARD_PATH", env["dash"])
    APP.app.state.repo = ContextRepository(context=CTX.build_context(env["data"], inflation_rate=0.4))
    assert TestClient(APP.app, headers=H).post("/api/context/refresh").json()["ok"]
    assert calls == ["pipeline", "pipeline"]


def test_cli_subprocess_success_and_failure(env):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    e = {**os.environ, "STOCKAGENT_NO_DOTENV": "1"}
    ok = subprocess.run([sys.executable, "-m", "services.refresh", "--data", env["data"], "--dashboard", env["dash"]],
                        cwd=root, capture_output=True, text=True, timeout=120, env=e)
    assert ok.returncode == 0 and ok.stdout.startswith("OK:") and SE.SALES_FILE in ok.stdout
    assert os.path.exists(env["dash"]) and staging_files(env) == []
    os.remove(os.path.join(env["data"], SE.SALES_FILE))
    bad = subprocess.run([sys.executable, "-m", "services.refresh", "--data", env["data"], "--dashboard", env["dash"]],
                         cwd=root, capture_output=True, text=True, timeout=120, env=e)
    assert bad.returncode == 1 and "BAŞARISIZ (doğrulama)" in bad.stdout and RF.PREVIOUS_KEPT in bad.stdout
    assert env["dir"] not in bad.stdout


# =============== API (33–41) ===============
@pytest.fixture
def api(env, monkeypatch):
    monkeypatch.setattr(APP, "DATA_FOLDER", env["data"])
    monkeypatch.setattr(APP, "DASHBOARD_PATH", env["dash"])
    spy = SpyProvider()
    APP.app.state.repo = ContextRepository(context=CTX.build_context(env["data"], inflation_rate=0.4))
    APP.app.state.provider, APP.app.state.agent = spy, BusinessActionAgent(spy)
    RF.run_refresh(env["data"], env["dash"], inflation_rate=0.4)          # a live dashboard exists
    return TestClient(APP.app, headers=H), spy


def test_refresh_endpoint_requires_guard_header(api):
    c, spy = api
    assert c.post("/api/context/refresh", headers={"X-StockAgent": "0"}).status_code == 403
    assert TestClient(APP.app).post("/api/context/refresh").status_code == 403
    assert c.get("/api/context/refresh").status_code == 405


def test_refresh_endpoint_success_installs_new_data(api, env):
    c, spy = api
    write(env["data"], extra_sales_rows=[newer_row()])
    r = c.post("/api/context/refresh")
    j = r.json()
    assert r.status_code == 200 and j["ok"] and j["step"] == "done"
    assert j["sources"]["sales"] == SE.SALES_FILE and j["previous_as_of_data_date"] == BASE_DATE
    assert j["as_of_data_date"] == NEW_DATE and j["counts"]["actions"] >= 1 and isinstance(j["warnings"], list)
    assert c.get("/api/health").json()["as_of_data_date"] == NEW_DATE
    assert c.get("/api/context").json()["as_of_data_date"] == NEW_DATE
    assert f'data-asof="{NEW_DATE}"' in c.get("/").text
    assert spy.calls == 0


@pytest.mark.parametrize("phase", ["validate", "build", "verify"])
def test_refresh_endpoint_failure_names_the_step(api, env, monkeypatch, phase):
    c, spy = api
    if phase == "validate":
        os.remove(os.path.join(env["data"], SE.SALES_FILE))
    elif phase == "build":
        monkeypatch.setattr(DB, "build", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    else:
        monkeypatch.setattr(RF, "verify", lambda *a, **k: {"ok": False, "errors": ["Bağlam ve pano tutmuyor: test."],
                                                           "warnings": [], "counts": {}})
    j = c.post("/api/context/refresh").json()
    assert j["ok"] is False and j["step"] == phase and j["previous_data_kept"] is True and j["message"]
    assert c.get("/api/health").json()["as_of_data_date"] == BASE_DATE
    assert env["dir"] not in str(j) and "Traceback" not in str(j)
    assert spy.calls == 0


def test_refresh_endpoint_busy_is_409(api, env):
    c, spy = api
    with RF.refresh_lock(RF.paths_for(env["dash"])["lock"]):
        r = c.post("/api/context/refresh")
    assert r.status_code == 409 and r.json()["step"] == "busy" and r.json()["ok"] is False
    assert spy.calls == 0


def test_health_exposes_no_absolute_local_path(api, env):
    c, _ = api
    h = c.get("/api/health").json()
    assert h["data_folder"] == "data" and env["dir"] not in str(h)
