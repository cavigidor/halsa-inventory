"""M8-CP3 (D-065): the owner-only Excel Upload Center — upload, declarations, background validation, preview,
explicit publication, stale / duplicate handling, rollback, persistence, access and the accuracy gate.

In-process FastAPI app (real startup), SYNTHETIC workbooks only (tests/fixtures), production accuracy gate (no
metric is VERIFIED). Owner routes run through the test-only owner harness unless a test is marked `production_auth`.
"""
import dataclasses
import datetime as dt
import json
import os
import sqlite3
import threading
import time
import urllib.parse
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as db
from services import accuracy as ACC
from tests import release_state as REL
from services import generation as GEN
from services import owner_auth as AUTH
from services import refresh as RF
from services import store as ST
from services import upload_center as UPC
from services import upload_files as UF
from tests.fixtures import fair_synth as FX
from tests.fixtures import owner_synth as OS_

pytestmark = pytest.mark.production_gate

H = {"X-StockAgent": "1"}
SECRETS = (*FX.P.values(), *(FX.name(i) for i in FX.P), *OS_.SECRETS)


# ---------------------------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------------------------
def write_stock(path, extra=False):
    df = FX.stock_frame()
    if extra:
        df.loc[len(df)] = ["FS-002", "Sentetik ikinci ürün", 5, 1.5, 1.0, 3.0, 250, ""]
    with pd.ExcelWriter(path) as w:
        pd.DataFrame([["Sentetik stok"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        df.to_excel(w, index=False, startrow=3)
    return path


@pytest.fixture
def srv(tmp_path, monkeypatch):
    data, src = tmp_path / "data", tmp_path / "src"
    data.mkdir()
    FX.write_all(str(src))
    write_stock(src / "stok2.xlsx", extra=True)
    OS_.write_kredi(src, dt.date.today())
    OS_.write_gider_gelir(src)
    OS_.write_butce(src)
    monkeypatch.setattr(APP, "DATA_FOLDER", str(data))
    monkeypatch.setattr(APP, "DASHBOARD_PATH", str(tmp_path / "dashboard.html"))
    monkeypatch.setattr(APP, "UPLOAD_DIR", str(data / "uploads"))
    monkeypatch.setattr(APP, "WARMUP_CONTEXT", False)
    monkeypatch.setattr(APP, "INFLATION", 0.4)
    APP.app.dependency_overrides.clear()
    with TestClient(APP.app, base_url="https://testserver", headers=H) as c:
        ns = SimpleNamespace(c=c, data=data, src=src, dash=str(tmp_path / "dashboard.html"), tmp=tmp_path)
        yield ns
        APP.app.state.uploads.wait_idle(120)


FILES = {"stock": FX.STOCK_FILE, "sales": FX.SALES_FILE, "aging": FX.AGING_FILE, "kredi": OS_.KREDI_FILE,
         "gider_gelir": OS_.GG_FILE, "butce": OS_.BUTCE_FILE}


def new_batch(s):
    r = s.c.post("/api/uploads")
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


def upload(s, bid, name, label=None, client=None):
    r = (client or s.c).post(f"/api/uploads/{bid}/files", content=(s.src / name).read_bytes(),
                             headers={**H, "X-Upload-Name": urllib.parse.quote(label or name)})
    return r


def wait(s, job_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = s.c.get(f"/api/uploads/jobs/{job_id}").json()
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    pytest.fail("job did not finish")


def validate(s, bid, assignments):
    r = s.c.post(f"/api/uploads/{bid}/validate", json={"assignments": assignments})
    assert r.status_code == 202, r.text
    j = wait(s, r.json()["job_id"])
    return j, s.c.get(f"/api/uploads/{bid}").json()


def publish(s, bid, token=None):
    b = s.c.get(f"/api/uploads/{bid}").json()
    r = s.c.post(f"/api/uploads/{bid}/publish", json={"preview_token": token or b["preview_token"] or "x"})
    if r.status_code != 202:
        return r, None
    return r, wait(s, r.json()["job_id"])


def prepared(s, slots=("stock", "sales", "aging"), decl=None, names=None):
    """A new batch with one uploaded file per slot; returns (batch_id, assignments)."""
    bid = new_batch(s)
    asg = {}
    for slot in slots:
        r = upload(s, bid, (names or {}).get(slot, FILES[slot]))
        assert r.status_code == 201, r.text
        asg[r.json()["file"]["file_id"]] = {"slot": slot, "declarations": (decl or {}).get(slot, {})}
    return bid, asg


def published_set(s, **kw):
    bid, asg = prepared(s, **kw)
    j, b = validate(s, bid, asg)
    assert b["status"] == "ready", b["preview"]["errors"] if b["preview"] else b["message"]
    r, pj = publish(s, bid)
    assert pj["state"] == "done" and pj["batch"]["status"] == "published", pj
    return bid


def live_files(s):
    out = {}
    for p in (s.dash, GEN.manifest_path(s.dash), str(s.data / GEN.SELECTION_FILE),
              str(s.data / GEN.OWNER_SELECTION_FILE)):
        out[os.path.basename(p)] = open(p, "rb").read() if os.path.exists(p) else None
    return out


def vkm_counts():
    with sqlite3.connect(ST.DB_PATH) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("dq_issue", "dq_issue_event", "dq_observation", "dq_source_state")}


# ---------------------------------------------------------------------------------------------
# 1. correct multi-file upload → validation → preview → publication
# ---------------------------------------------------------------------------------------------
def test_multi_file_upload_validates_previews_and_publishes_explicitly(srv):
    s = srv
    assert s.c.get("/").status_code == 404                                   # nothing published yet
    assert "/yukleme" in s.c.get("/").text                                  # the page points to the Upload Center
    bid, asg = prepared(s, ("stock", "sales", "aging", "kredi", "gider_gelir", "butce"),
                        decl={"stock": {"snapshot_date": "2026-06-30"},
                              "sales": {"period_start": "2020-01-01", "period_end": "2026-06-30",
                                        "period_complete": True, "report_date": "2026-06-30"},
                              "gider_gelir": {"report_year": "2026"}, "butce": {"planning_year": "2026"}})
    before = live_files(s)
    j, b = validate(s, bid, asg)
    assert j["state"] == "done" and j["kind"] == "validate" and j["step"] == j["steps"]
    assert b["status"] == "ready" and b["publishable"] and b["preview_token"]
    p = b["preview"]
    assert p["errors"] == [] and not p["noop"] and p["core_changed"] and p["owner_changed"] == ["butce", "gider_gelir",
                                                                                              "kredi"]
    # recognized sheets and columns, declared dates, missing information
    src = {x["slot"]: x for x in p["sources"]}
    assert set(src) == {"stock", "sales", "aging", "kredi", "gider_gelir", "butce"}
    assert "liste" in src["sales"]["sheets"] and src["sales"]["master_sheet"] is True
    assert all(col["found"] for col in src["stock"]["columns"]) and src["stock"]["data_rows"] == 1
    assert {d["field"]: d["value"] for d in src["stock"]["declarations"]} == {"snapshot_date": "2026-06-30"}
    # D-072 D1: the Cari date comes from the original file name, else the upload date — with visible provenance
    aging_decl = {d["field"]: d["value"] for d in src["aging"]["declarations"]}
    assert src["aging"]["unknown_fields"] == [] and aging_decl["snapshot_date"] == dt.date.today().isoformat()
    assert aging_decl["date_origin"].startswith("dosya adında tarih yok — yükleme tarihi")
    assert not any("Cari" in m["message"] and "bilinmiyor" in m["message"] for m in p["missing"])
    assert src["kredi"]["state"] == "ready" and src["kredi"]["counts"]["installments"] > 0
    # reconciliation status, VKM delta, dashboard sections, withheld metrics
    assert {r["source"] for r in p["reconciliation"]} >= {"stock", "sales", "aging", "kredi"}
    assert p["vkm"]["counts"]["new"] > 0 and p["vkm"]["sources"]
    # D-073: the fresh Cari report (upload-date fallback, D-072 D1) makes exactly the factual Cari list displayable
    assert p["sections"]["metrics_verified"] == 1 and p["sections"]["metrics_withheld"] == len(ACC.REGISTER) - 1
    assert [t["metric"] for t in p["sections"]["tabs"] if t["displayable_after"]] == list(REL.CERTIFIED)
    assert {t["title"] for t in p["sections"]["tabs"] if t["displayable_after"]} == {"Cari Bakiye (fatura yaşı)"}
    assert p["as_of"]["candidate"] == "2026-06-30" and "gözlenen" in p["as_of"]["note"]
    assert p["generation_bound_api_evidence"] is True and p["ai_used"] is False
    # the preview changed nothing live
    assert live_files(s) == before and vkm_counts()["dq_observation"] == 0
    assert not any(cfg.configured for cfg in APP.app.state.owner.configs().values())

    r, pj = publish(s, bid)
    assert r.status_code == 202 and pj["state"] == "done" and pj["batch"]["status"] == "published"
    assert pj["kind"] == "publish" and pj["batch"]["published_at"]
    # dashboard, generation, explicit selections — every source bound, every metric but the D-073 Cari list withheld
    assert s.c.get("/").status_code == 200
    st = s.c.get("/api/accuracy/status").json()
    assert st["selection_origin"] == "explicit" and st["generation_bound_api_evidence"] is True
    srcs = {x["key"]: x for x in st["sources"]}
    assert set(srcs) == {"stock", "sales", "liste", "aging", "kredi", "gider_gelir", "butce"}
    assert all(x["bound"] for x in srcs.values())
    assert srcs["stock"]["declared"]["snapshot_date"] == "2026-06-30"
    assert srcs["aging"]["declared"]["snapshot_date"] == dt.date.today().isoformat()       # D-072 D1 upload-date fallback
    assert srcs["aging"]["declared"]["date_origin"] == "upload_date"
    assert {srcs[k]["selection_origin"] for k in ("kredi", "gider_gelir", "butce")} == {"upload"}
    assert {m["state"] for m in st["metrics"] if m["key"] not in REL.CERTIFIED} <= {"not_verified", "invalid",
                                                                                   "unavailable"}
    assert not REL.verified_outside_certified(st["metrics"])                      # D-073: only the Cari list
    sel = json.load(open(s.data / GEN.SELECTION_FILE))
    assert sel["schema"] == GEN.SELECTION_SCHEMA and set(sel["core"]) == {"stock", "sales", "aging"}
    assert sel["core"]["aging"] == {"file": sel["core"]["aging"]["file"], "snapshot_date": dt.date.today().isoformat(),
                                    "period_complete": True, "date_origin": "upload_date"}   # D-072 D1, provenance kept
    assert all(UF.is_stored_name(v["file"]) for v in sel["core"].values())
    osel = json.load(open(s.data / GEN.OWNER_SELECTION_FILE))
    assert osel["owner"]["gider_gelir"]["report_year"] == "2026" and "snapshot_date" not in osel["owner"]["kredi"]
    fin = s.c.get("/api/finance/overview").json()
    assert {fin[k]["state"] for k in ("kredi", "gider_gelir", "butce")} == {"ready"}
    assert s.c.get("/api/vkm/issues").json()["sync"]["state"] == "in_sync"
    ov = s.c.get("/api/uploads").json()
    assert {x["slot"] for x in ov["published"]} == {"stock", "sales", "aging", "kredi", "gider_gelir", "butce"}
    assert ov["history"][0]["status"] == "published" and ov["batch"] is None
    with sqlite3.connect(ST.DB_PATH) as c:
        events = [r[0] for r in c.execute("SELECT event FROM upload_event WHERE batch_id=? ORDER BY event_id", (bid,))]
    assert events[0] == "created" and events[-2:] == ["publication_started", "published"]


def test_published_upload_survives_a_restart_and_rederives_the_same_generation(srv):
    s = srv
    published_set(s, slots=("stock", "sales", "aging", "kredi"))
    core_id = s.c.get("/api/accuracy/status").json()["core_id"]
    with TestClient(APP.app, base_url="https://testserver", headers=H) as c2:       # a new startup
        ctx = APP.app.state.repo.context()
        st = c2.get("/api/accuracy/status").json()
        assert st["core_id"] == core_id and st["restore_reason"] is None and ctx["as_of_data_date"] == "2026-06-30"
        cfgs = APP.app.state.owner.configs()
        assert cfgs["kredi"].origin == "upload" and UF.is_stored_name(os.path.basename(cfgs["kredi"].path))
        assert c2.get("/api/finance/overview").json()["kredi"]["state"] in ("loading", "ready")


# ---------------------------------------------------------------------------------------------
# 2. report type is the owner's choice — never the file name
# ---------------------------------------------------------------------------------------------
def test_file_names_are_labels_only_and_never_select_a_type(srv):
    s = srv
    bid = new_batch(s)
    a = upload(s, bid, FX.AGING_FILE, label="STOK satış 2019-01-01.xlsx").json()
    assert a["file"]["label"] == "STOK satış 2019-01-01.xlsx"
    b = s.c.get(f"/api/uploads/{bid}").json()
    assert b["assignments"] == {}                                           # nothing preselected from the name
    r = s.c.post(f"/api/uploads/{bid}/validate", json={"assignments": {}})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "slot_missing"
    # the same bytes under another name are the same file (dedupe), whatever the label says
    again = upload(s, bid, FX.AGING_FILE, label="tamamen-farklı.xlsx").json()
    assert again["duplicate_in_batch"] and again["file"]["file_id"] == a["file"]["file_id"]


def test_renamed_files_validate_by_their_declared_type_and_content(srv):
    s = srv
    for slot in ("stock", "sales", "aging"):
        os.replace(s.src / FILES[slot], s.src / f"rapor-{slot}-adsız.xlsx")
    bid, asg = prepared(s, names={k: f"rapor-{k}-adsız.xlsx" for k in ("stock", "sales", "aging")})
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready", b["preview"]["errors"]


def test_wrong_report_type_is_refused_with_the_missing_columns_and_nothing_changes(srv):
    s = srv
    published_set(s)
    before = live_files(s)
    bid, asg = prepared(s, names={"stock": FX.AGING_FILE, "aging": FX.STOCK_FILE})        # swapped by the owner
    _, b = validate(s, bid, asg)
    assert b["status"] == "invalid" and not b["publishable"] and b["preview_token"] is None
    msgs = " ".join(e["message"] for e in b["preview"]["errors"])
    assert "beklenen düzeninde değil" in msgs and "yanlış rapor türü" in msgs
    assert "geçerli bir Excel çalışma kitabı değil" not in msgs       # it IS a valid workbook — of another report
    bid, asg = prepared(s, names={"aging": FX.STOCK_FILE})                                # stock report as Cari
    _, b = validate(s, bid, asg)
    msgs = " ".join(e["message"] for e in b["preview"]["errors"])
    assert b["status"] == "invalid" and "Cari (yaşlandırma) dosyasında gerekli sütunlar yok" in msgs
    assert "Hesap kodu" in msgs
    assert live_files(s) == before
    r, _ = publish(s, bid, token="anything")
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "not_ready"


def test_missing_required_columns_make_the_batch_invalid(srv):
    s = srv
    df = FX.stock_frame().drop(columns=["maxseviye"])
    with pd.ExcelWriter(s.src / "eksik.xlsx") as w:
        pd.DataFrame([["Sentetik stok"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        df.to_excel(w, index=False, startrow=3)
    bid, asg = prepared(s, names={"stock": "eksik.xlsx"})
    _, b = validate(s, bid, asg)
    assert b["status"] == "invalid"
    stock = next(x for x in b["preview"]["sources"] if x["slot"] == "stock")
    assert {c["name"]: c["found"] for c in stock["columns"]}["maxseviye"] in (False, None)
    assert any("maxseviye" in e["message"] for e in b["preview"]["errors"])


def test_owner_workbook_without_its_declared_year_cannot_be_read(srv):
    s = srv
    bid, asg = prepared(s, ("gider_gelir",))
    _, b = validate(s, bid, asg)
    assert b["status"] == "invalid"
    assert any("Rapor yılı beyan edilmedi" in e["message"] for e in b["preview"]["errors"])


# ---------------------------------------------------------------------------------------------
# 3. dates: declared or unknown — never inferred (Stok / Satış); Cari: D-072 D1 (file name, else upload date)
# ---------------------------------------------------------------------------------------------
def test_unknown_dates_stay_unknown_everywhere(srv):
    s = srv
    published_set(s)
    st = s.c.get("/api/accuracy/status").json()
    for x in st["sources"]:
        if x["key"] != "aging":
            assert all(v is None for k, v in x["declared"].items() if k != "approval"), x
    core = json.load(open(s.data / GEN.SELECTION_FILE))["core"]
    assert set(core["stock"]) == set(core["sales"]) == {"file"}
    today = dt.date.today().isoformat()
    assert core["aging"] == {"file": core["aging"]["file"], "snapshot_date": today, "period_complete": True,
                             "date_origin": "upload_date"}           # this synthetic file name carries no date
    m = APP.app.state.generations.current()
    for r in m.core:
        if r.key == "aging":
            assert (r.evidence.report_date.isoformat(), r.evidence.report_date_origin) == (today, "upload_date")
        else:
            assert r.evidence.report_date is None and r.evidence.report_date_origin is None


@pytest.mark.parametrize("decl,code", [
    ({"snapshot_date": "30.06.2026"}, "bad_date"), ({"snapshot_date": "2026-02-30"}, "bad_date"),
    ({"snapshot_date": 20260630}, "bad_date"), ({"report_year": "2026"}, "field_not_allowed"),
    ({"bilinmeyen": "x"}, "field_not_allowed"),
])
def test_invalid_declarations_are_refused_without_echo(srv, decl, code):
    s = srv
    bid, asg = prepared(s, ("stock",))
    (fid,) = asg
    r = s.c.post(f"/api/uploads/{bid}/validate", json={"assignments": {fid: {"slot": "stock", "declarations": decl}}})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == code
    assert "30.06.2026" not in r.text and "bilinmeyen" not in r.text
    assert s.c.get(f"/api/uploads/{bid}").json()["status"] == "draft"


def test_period_and_year_rules(srv):
    s = srv
    bid, asg = prepared(s, ("sales", "butce"))
    f_sales, f_butce = list(asg)
    bad = {f_sales: {"slot": "sales", "declarations": {"period_start": "2026-06-30", "period_end": "2020-01-01"}},
           f_butce: {"slot": "butce", "declarations": {}}}
    assert s.c.post(f"/api/uploads/{bid}/validate", json={"assignments": bad}).json()["detail"]["error_code"] == \
        "bad_period"
    bad[f_sales]["declarations"] = {}
    bad[f_butce]["declarations"] = {"planning_year": "26"}
    assert s.c.post(f"/api/uploads/{bid}/validate", json={"assignments": bad}).json()["detail"]["error_code"] == \
        "bad_year"
    same = {f_sales: {"slot": "sales", "declarations": {}}, f_butce: {"slot": "sales", "declarations": {}}}
    assert s.c.post(f"/api/uploads/{bid}/validate", json={"assignments": same}).json()["detail"]["error_code"] == \
        "slot_twice"


def test_stock_and_sales_are_required_until_an_explicit_selection_exists(srv):
    s = srv
    bid, asg = prepared(s, ("stock",))
    _, b = validate(s, bid, asg)
    assert b["status"] == "invalid" and b["preview"]["errors"][0]["code"] == "core_required"
    # after an explicit publication the other reports are carried over unchanged (with their own declarations)
    published_set(s, decl={"sales": {"report_date": "2026-06-30"}})
    bid, asg = prepared(s, ("stock",), names={"stock": "stok2.xlsx"})
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready", b["preview"]["errors"]
    src = {x["slot"]: x for x in b["preview"]["sources"]}
    assert src["sales"]["carried"] and src["aging"]["carried"] and not src["stock"]["carried"]
    assert {d["field"]: d["value"] for d in src["sales"]["declarations"]}["report_date"] == "2026-06-30"


# ---------------------------------------------------------------------------------------------
# 4. truncated / malformed uploads
# ---------------------------------------------------------------------------------------------
def test_truncated_and_malformed_uploads_are_refused_at_receipt(srv):
    s = srv
    bid = new_batch(s)
    data = (s.src / FX.SALES_FILE).read_bytes()
    r = s.c.post(f"/api/uploads/{bid}/files", content=data[:len(data) // 2], headers=H)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "corrupt"
    r = s.c.post(f"/api/uploads/{bid}/files", content=b"Stok_Kodu;Stok_Adi\n1;x", headers=H)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "not_xlsx"
    r = s.c.post(f"/api/uploads/{bid}/files", content=b"", headers=H)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "empty"
    assert s.c.get(f"/api/uploads/{bid}").json()["files"] == []
    assert os.listdir(s.data / "uploads" / "incoming") == []


def test_oversized_upload_is_refused_with_413(srv, monkeypatch):
    s = srv
    monkeypatch.setattr(UF, "MAX_BYTES", 2000)
    bid = new_batch(s)
    r = upload(s, bid, FX.SALES_FILE)
    assert r.status_code == 413 and r.json()["detail"]["error_code"] == "too_large"


def test_a_file_that_breaks_after_upload_fails_validation_safely(srv):
    s = srv
    bid, asg = prepared(s)
    first = json.loads(_row(bid)["files"])[0]
    blob = APP.app.state.uploads.blobs.path(first["sha256"])
    with open(blob, "ab") as fh:
        fh.write(b"bozuk")
    j, b = validate(s, bid, asg)
    assert j["state"] == "failed" and j["error_code"] == "file_missing" and b["status"] == "invalid"


def _row(bid):
    with ST._conn() as c:
        return dict(c.execute("SELECT * FROM upload_batch WHERE batch_id=?", (bid,)).fetchone())


# ---------------------------------------------------------------------------------------------
# 5. duplicates and stale previews
# ---------------------------------------------------------------------------------------------
def test_identical_upload_publishes_nothing_and_adds_no_history(srv):
    s = srv
    published_set(s, slots=("stock", "sales", "aging", "kredi"))
    before, vk = live_files(s), vkm_counts()
    gen = s.c.get("/api/accuracy/status").json()["generation_id"]
    bid, asg = prepared(s, ("stock", "sales", "aging", "kredi"))
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready" and b["preview"]["noop"]
    assert all(src["unchanged"] for src in b["preview"]["vkm"]["sources"] if src["key"] != "kredi")
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "unchanged" and "Değişiklik yoktu" in pj["batch"]["message"]
    assert live_files(s) == before and vkm_counts() == vk
    assert s.c.get("/api/accuracy/status").json()["generation_id"] == gen
    blobs = os.listdir(s.data / "uploads" / "blobs")
    assert len(blobs) == 4                                                  # stored once


def test_changed_declaration_is_not_an_identical_upload(srv):
    s = srv
    published_set(s)
    bid, asg = prepared(s, decl={"stock": {"snapshot_date": "2026-06-30"}})
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready" and not b["preview"]["noop"]


def test_a_newer_batch_supersedes_an_older_preview(srv):
    s = srv
    old, asg = prepared(s)
    _, b = validate(s, old, asg)
    token = b["preview_token"]
    new_batch(s)
    assert s.c.get(f"/api/uploads/{old}").json()["status"] == "superseded"
    r, _ = publish(s, old, token=token)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "not_latest"
    assert not os.path.exists(s.dash)


def test_a_preview_built_before_another_publication_is_refused_as_stale(srv):
    s = srv
    published_set(s)
    bid, asg = prepared(s, ("stock",), names={"stock": "stok2.xlsx"})
    _, b = validate(s, bid, asg)
    token, before = b["preview_token"], live_files(s)
    # meanwhile the live selection changes (as after a publication from elsewhere): the declarations differ
    sel = json.load(open(s.data / GEN.SELECTION_FILE))
    sel["core"]["sales"]["report_date"] = "2026-06-30"
    GEN.write_json_atomic(str(s.data / GEN.SELECTION_FILE), sel)
    r, _ = publish(s, bid, token=token)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "stale"
    assert s.c.get(f"/api/uploads/{bid}").json()["status"] == "stale"
    after = live_files(s)
    assert after["dashboard.html"] == before["dashboard.html"]


def test_a_core_publication_elsewhere_makes_the_preview_stale(srv):
    s = srv
    published_set(s)
    bid, asg = prepared(s, ("stock",), names={"stock": "stok2.xlsx"})
    _, b = validate(s, bid, asg)
    APP.app.state.generations.install(dataclasses.replace(APP.app.state.generations.current(),
                                                          created_at="x", joins={"changed": 1}))
    r, _ = publish(s, bid, token=b["preview_token"])
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "stale"


def test_wrong_token_and_double_publication_are_refused(srv):
    s = srv
    bid, asg = prepared(s)
    _, b = validate(s, bid, asg)
    r, _ = publish(s, bid, token="yanlis")
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "token"
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "published"
    r, _ = publish(s, bid, token=b["preview_token"])
    assert r.status_code == 409


def test_publication_requires_exactly_the_previewed_generation(srv):
    s = srv
    bid, asg = prepared(s)
    _, b = validate(s, bid, asg)
    with ST._conn() as c:                                   # as if the preview had described different content
        cand = json.loads(_row(bid)["candidate"])
        cand["core_id"] = "0" * 64
        c.execute("UPDATE upload_batch SET candidate=? WHERE batch_id=?", (json.dumps(cand), bid))
    _, pj = publish(s, bid)
    assert pj["state"] == "failed" and pj["batch"]["status"] == "publish_failed"
    assert "önizlemeyle aynı değil" in pj["message"]
    assert not os.path.exists(s.dash) and not os.path.exists(s.data / GEN.SELECTION_FILE)


# ---------------------------------------------------------------------------------------------
# 6. long-running validation (background job + polling)
# ---------------------------------------------------------------------------------------------
def test_validation_runs_in_the_background_and_reports_turkish_progress(srv, monkeypatch):
    s = srv
    gate, started = threading.Event(), threading.Event()
    orig = RF.run_refresh

    def slow(*a, **k):
        hooks = k.get("hooks")
        if hooks is not None:
            hooks.progress("core_validate")
        started.set()
        assert gate.wait(30)
        return orig(*a, **k)
    monkeypatch.setattr(RF, "run_refresh", slow)
    bid, asg = prepared(s)
    t0 = time.monotonic()
    r = s.c.post(f"/api/uploads/{bid}/validate", json={"assignments": asg})
    assert r.status_code == 202 and time.monotonic() - t0 < 5                  # the request never waits for the job
    job = r.json()
    assert started.wait(10)
    j = s.c.get(f"/api/uploads/jobs/{job['job_id']}").json()
    assert j["state"] == "running" and j["stage"] == "core_validate" and "doğrulanıyor" in j["stage_label"]
    assert 0 < j["step"] < j["steps"] and j["batch"]["status"] == "validating"
    # one job at a time; the batch cannot change while it is validated
    assert s.c.post("/api/uploads").json()["detail"]["error_code"] == "job_running"
    assert upload(s, bid, FX.STOCK_FILE).status_code == 409
    gate.set()
    j = wait(s, job["job_id"])
    assert j["state"] == "done" and j["batch"]["status"] == "ready" and j["stage_label"] == "Tamamlandı"


def test_an_interrupted_job_is_marked_and_must_be_validated_again(srv):
    s = srv
    bid, asg = prepared(s)
    with ST._conn() as c:
        c.execute("UPDATE upload_batch SET status='validating' WHERE batch_id=?", (bid,))
        c.execute("INSERT INTO upload_job(job_id,batch_id,kind,state,stage,created_at) VALUES('j-0000000000000001',?,"
                  "'validate','running','core_build','2026-10-25T00:00:00Z')", (bid,))
    APP.app.state.uploads.recover()
    j = s.c.get("/api/uploads/jobs/j-0000000000000001").json()
    assert j["state"] == "interrupted" and j["batch"]["status"] == "interrupted"
    assert "yeniden başladığı" in j["batch"]["status_label"]


# ---------------------------------------------------------------------------------------------
# 7. failed publication → rollback
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("where", ["swap", "install"])
def test_failed_publication_keeps_everything_previous(srv, monkeypatch, where):
    s = srv
    published_set(s, slots=("stock", "sales", "aging", "kredi"))
    before, gen = live_files(s), s.c.get("/api/accuracy/status").json()
    ctx_before = APP.app.state.repo.live()
    kredi_before = APP.app.state.owner.configs()["kredi"]
    bid, asg = prepared(s, ("stock", "gider_gelir"), names={"stock": "stok2.xlsx"},
                        decl={"gider_gelir": {"report_year": "2026"}})
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready"

    def boom(*a, **k):
        raise OSError("disk dolu")
    if where == "swap":
        monkeypatch.setattr(RF, "_after_manifest_swap", boom)
    else:
        monkeypatch.setattr(APP.app.state.uploads.deps, "install", boom)
    _, pj = publish(s, bid)
    assert pj["state"] == "failed" and pj["batch"]["status"] == "publish_failed"
    assert "Önceki" in pj["message"] or "önceki" in pj["message"]
    assert live_files(s) == before                                       # dashboard, manifest, both selections
    after = s.c.get("/api/accuracy/status").json()
    assert after["generation_id"] == gen["generation_id"] and after["core_id"] == gen["core_id"]
    assert APP.app.state.repo.live() is ctx_before
    assert APP.app.state.owner.configs()["kredi"] == kredi_before
    assert APP.app.state.owner.configs()["gider_gelir"].configured is False
    assert s.c.get("/").status_code == 200 and s.c.get("/api/vkm/issues").json()["sync"]["state"] == "in_sync"


def test_owner_only_publication_failure_restores_the_selection(srv, monkeypatch):
    s = srv
    published_set(s)
    bid, asg = prepared(s, ("kredi",))
    _, b = validate(s, bid, asg)
    before = live_files(s)
    monkeypatch.setattr(APP.app.state.owner, "install_entries", lambda e: (_ for _ in ()).throw(RuntimeError("x")))
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "publish_failed" and live_files(s) == before
    assert APP.app.state.owner.configs()["kredi"].configured is False


# ---------------------------------------------------------------------------------------------
# 8. CRM, fair and VKM state survive publication
# ---------------------------------------------------------------------------------------------
def c360_ready(s, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if s.c.get("/api/customer360/status").json().get("state") == "ready":
            return
        time.sleep(0.1)
    pytest.fail("Customer 360 not ready")


def test_crm_fair_and_vkm_state_persist_across_publication(srv):
    s = srv
    published_set(s)
    c360_ready(s)
    code = FX.P[1]
    r = s.c.post(f"/api/crm/customers/{code}/interactions",
                 json={"occurred_on": "2026-06-20", "channel": "phone", "note": "Sentetik CRM notu"})
    assert r.status_code in (200, 201), r.text
    r = s.c.post("/api/fairs/campaigns", json={"name": "Sentetik Kampanya", "edition_year": 2027,
                                               "start_date": "2027-01-15"})
    assert r.status_code in (200, 201), r.text
    issues = s.c.get("/api/vkm/issues").json()
    target = issues["issues"][0]["issue_id"]
    r = s.c.post(f"/api/vkm/issues/{target}/review", json={"action": "incelendi", "note": "Sentetik inceleme",
                                                          "vkm_generation": issues["vkm_generation"]})
    assert r.status_code == 200, r.text
    with sqlite3.connect(ST.DB_PATH) as c:
        hist_before = c.execute("SELECT COUNT(*) FROM dq_issue_event WHERE issue_id=?", (target,)).fetchone()[0]
    # a new core publication (changed stock report) and an owner workbook
    bid, asg = prepared(s, ("stock", "kredi"), names={"stock": "stok2.xlsx"})
    _, b = validate(s, bid, asg)
    _, pj = publish(s, bid)
    assert pj["batch"]["status"] == "published"
    c360_ready(s)
    crm = s.c.get(f"/api/crm/customers/{code}").json()
    assert "Sentetik CRM notu" in json.dumps(crm, ensure_ascii=False)
    assert any(x["name"] == "Sentetik Kampanya" for x in s.c.get("/api/fairs/campaigns").json()["items"])
    d = s.c.get(f"/api/vkm/issues/{target}").json()
    with sqlite3.connect(ST.DB_PATH) as c:
        hist_after = c.execute("SELECT COUNT(*) FROM dq_issue_event WHERE issue_id=?", (target,)).fetchone()[0]
    assert hist_after >= hist_before                                    # append-only, nothing removed
    assert any(e["event"] == "reviewed" for e in d["history"]) and "Sentetik inceleme" in json.dumps(d, ensure_ascii=False)
    assert d["issue"]["status"] in ("incelendi", "cozuldu_kaynakta", "yeniden_acildi")
    assert s.c.get("/api/vkm/issues").json()["sync"]["state"] == "in_sync"


def test_upload_history_is_append_only(srv):
    s = srv
    bid = new_batch(s)
    with sqlite3.connect(ST.DB_PATH) as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("DELETE FROM upload_event")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE upload_event SET event='x'")
    assert bid


# ---------------------------------------------------------------------------------------------
# 9. authentication, CSRF, same-origin
# ---------------------------------------------------------------------------------------------
UPLOAD_ROUTES = [("GET", "/api/uploads"), ("POST", "/api/uploads"), ("GET", "/api/uploads/jobs/j-0000000000000001"),
                 ("GET", "/api/uploads/b-00000000000000000000"), ("POST", "/api/uploads/b-00000000000000000000/files"),
                 ("POST", "/api/uploads/b-00000000000000000000/files/f-000000000000/remove"),
                 ("POST", "/api/uploads/b-00000000000000000000/validate"),
                 ("POST", "/api/uploads/b-00000000000000000000/publish"),
                 ("POST", "/api/uploads/b-00000000000000000000/discard")]


def test_every_upload_route_is_classified_owner_only():
    from services import access as AX
    tpl = {("GET", "/api/uploads"), ("POST", "/api/uploads"), ("GET", "/api/uploads/jobs/{job_id}"),
           ("GET", "/api/uploads/{batch_id}"), ("POST", "/api/uploads/{batch_id}/files"),
           ("POST", "/api/uploads/{batch_id}/files/{file_id}/remove"), ("POST", "/api/uploads/{batch_id}/validate"),
           ("POST", "/api/uploads/{batch_id}/publish"), ("POST", "/api/uploads/{batch_id}/discard")}
    for k in tpl:
        assert AX.ACCESS[k] in (AX.OWNER_READ, AX.OWNER_WRITE) and ACC.ROUTES[k][0] in ("system", "operational")
    assert AX.ACCESS[("GET", "/yukleme")] == AX.VIEWER and ACC.ROUTES[("GET", "/yukleme")] == ("system", None)


@pytest.mark.production_auth
def test_viewers_and_financial_viewers_cannot_upload_validate_or_publish(srv, monkeypatch):
    s = srv
    pw, fpw = "sentetik-yonetici-sifresi-cp3", "sentetik-finans-sifresi-cp3"
    monkeypatch.setenv(AUTH.PASSWORD_ENV, AUTH.hash_password(pw, n=2 ** 14))
    monkeypatch.setenv(AUTH.FINANCE_PASSWORD_ENV, AUTH.hash_password(fpw, n=2 ** 14))
    anon = TestClient(APP.app, base_url="https://testserver", headers=H)
    for method, path in UPLOAD_ROUTES:
        r = anon.request(method, path, json={})
        assert r.status_code == 401 and r.json()["detail"]["error_code"] == "auth_required", (method, path)
    fin = TestClient(APP.app, base_url="https://testserver", headers=H)
    lr = fin.post("/api/auth/finance-login", json={"password": fpw})
    assert lr.status_code == 200
    ftok = lr.json()["csrf_token"]
    for method, path in UPLOAD_ROUTES:
        r = fin.request(method, path, json={}, headers={"X-CSRF-Token": ftok})
        assert r.status_code == 403 and r.json()["detail"]["error_code"] == "owner_required", (method, path)
    # the owner: CSRF and same-origin are required on every mutation
    own = TestClient(APP.app, base_url="https://testserver", headers=H)
    tok = own.post("/api/auth/login", json={"password": pw}).json()["csrf_token"]
    assert own.post("/api/uploads").json()["detail"]["error_code"] == "csrf_failed"
    r = own.post("/api/uploads", headers={"X-CSRF-Token": tok, "Origin": "https://baska-site.example"})
    assert r.json()["detail"]["error_code"] == "origin_rejected"
    r = own.post("/api/uploads", headers={"X-CSRF-Token": tok, "Origin": "https://testserver"})
    assert r.status_code == 201
    bid = r.json()["batch_id"]
    r = own.post(f"/api/uploads/{bid}/files", content=(s.src / FX.STOCK_FILE).read_bytes())
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "csrf_failed"
    r = own.post(f"/api/uploads/{bid}/files", content=(s.src / FX.STOCK_FILE).read_bytes(),
                 headers={"X-CSRF-Token": tok})
    assert r.status_code == 201
    r = own.post("/api/uploads", headers={"X-StockAgent": "0", "X-CSRF-Token": tok})          # local guard too
    assert r.status_code == 403 and "X-StockAgent" in r.text
    # the page itself carries no data and is the same for everyone
    page = anon.get("/yukleme")
    assert page.status_code == 200 and "Dosya Yükleme" in page.text and "Değişiklikleri Yayınla" in page.text
    assert page.headers["cache-control"] == "no-store" and FX.P[1] not in page.text


# ---------------------------------------------------------------------------------------------
# 10. accuracy gate, privacy, selection safety
# ---------------------------------------------------------------------------------------------
def test_publication_never_promotes_a_metric_and_payloads_carry_no_values(srv):
    s = srv
    bid, asg = prepared(s, ("stock", "sales", "aging", "kredi", "gider_gelir"),
                        decl={"gider_gelir": {"report_year": "2026"}})
    _, b = validate(s, bid, asg)
    body = json.dumps(b, ensure_ascii=False) + json.dumps(s.c.get("/api/uploads").json(), ensure_ascii=False)
    assert not [x for x in SECRETS if x in body]
    _, pj = publish(s, bid)
    REL.assert_release_state()                    # D-073: one certified metric, flag True
    st = s.c.get("/api/accuracy/status").json()
    assert not REL.verified_outside_certified(st["metrics"])
    assert s.c.get("/api/sales-queue").status_code == ACC.WITHHELD_HTTP_STATUS
    html = s.c.get("/").text
    assert '<button class="tab" data-t="up" hidden>Dosya Yükleme</button>' in html
    assert FX.name(1) not in html                                      # no unverified rows in the published page
    body = json.dumps(s.c.get(f"/api/uploads/jobs/{pj['job_id']}").json(), ensure_ascii=False)
    assert not [x for x in SECRETS if x in body]


def test_preview_changes_no_live_state(srv):
    s = srv
    published_set(s, slots=("stock", "sales", "aging", "kredi"))
    before, vk = live_files(s), vkm_counts()
    folder_before = sorted(os.listdir(s.data))
    snap_before = APP.app.state.owner.snapshot()
    ctx = APP.app.state.repo.live()
    bid, asg = prepared(s, ("stock", "gider_gelir", "butce"), names={"stock": "stok2.xlsx"},
                        decl={"gider_gelir": {"report_year": "2026"}, "butce": {"planning_year": "2026"}})
    _, b = validate(s, bid, asg)
    assert b["status"] == "ready"
    assert live_files(s) == before and vkm_counts() == vk
    assert APP.app.state.repo.live() is ctx
    snap = APP.app.state.owner.snapshot()
    assert {k: v["state"] for k, v in snap.items()} == {k: v["state"] for k, v in snap_before.items()}
    assert sorted(os.listdir(s.data)) == folder_before                   # files are copied only on publication


def test_legacy_keyword_selection_never_picks_an_uploaded_file(srv):
    s = srv
    published_set(s)
    picked = db.select_sources(str(s.data))
    assert picked == {"stock": None, "sales": None, "aging": None}


def test_discard_and_remove(srv):
    s = srv
    bid, asg = prepared(s, ("stock", "sales"))
    fid = next(iter(asg))
    b = s.c.post(f"/api/uploads/{bid}/files/{fid}/remove").json()
    assert len(b["files"]) == 1 and b["status"] == "draft"
    b = s.c.post(f"/api/uploads/{bid}/discard").json()
    assert b["status"] == "discarded"
    assert upload(s, bid, FX.STOCK_FILE).status_code == 409
    assert s.c.get("/api/uploads").json()["batch"] is None


def test_upload_to_an_unknown_or_closed_batch_reads_nothing(srv):
    s = srv
    r = upload(s, None or "b-00000000000000000000", FX.STOCK_FILE)
    assert r.status_code == 404 and r.json()["detail"]["error_code"] == "batch_missing"
    r = s.c.post("/api/uploads/../../etc/files", content=b"x")
    assert r.status_code in (404, 405)
    assert not os.path.exists(s.data / "uploads" / "incoming") or os.listdir(s.data / "uploads" / "incoming") == []
    bid = new_batch(s)
    s.c.post(f"/api/uploads/{bid}/discard")
    assert upload(s, bid, FX.STOCK_FILE).json()["detail"]["error_code"] == "batch_closed"
