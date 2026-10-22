"""M7-6 Yönetim / owner integration / performance / polish — owner config, Kredi 30/60/90, Gider/Gelir, Bütçe health,
status + known issues, DQ-3, sales-frame handoff, owner reload, full integration.

Synthetic data only (tests/fixtures: fair_synth, owner_synth). No real data, no key, no network, no AI call.
Numbers in test names follow the M7-6 instruction (§77–§85).
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import logging
import os
import pickle
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import config as C
import dashboard_builder as DB
from services import context as CTX
from services import crm_store as CS
from services import customer360 as C360
from services import evidence as EV
from services import fair_store as FS
from services import owner_view as OV
from services import sales_projection as SP
from services import sales_queue as SQ
from services import store as ST
from services.entity import ContextRepository
from services.importers import butce as BU
from services.importers import gider_gelir as GG
from services.importers import kredi as KR
from tests.fixtures import c360_synth as CF
from tests.fixtures import fair_synth as FX
from tests.fixtures import owner_synth as OS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = {"X-StockAgent": "1"}
P = FX.P
TODAY = dt.date(2026, 3, 1)


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def owner_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("owner")
    return {"dir": d, "kredi": OS.write_kredi(d, TODAY), "gg": OS.write_gider_gelir(d), "butce": OS.write_butce(d)}


def cfgs(kredi=None, gg=None, butce=None, **opts):
    return {"kredi": OV.OwnerSourceConfig("kredi", str(kredi) if kredi else None,
                                          {"snapshot_date": opts.get("kredi_snapshot")}),
            "gider_gelir": OV.OwnerSourceConfig("gider_gelir", str(gg) if gg else None,
                                                {"report_year": opts.get("report_year")}),
            "butce": OV.OwnerSourceConfig("butce", str(butce) if butce else None,
                                          {"planning_year": opts.get("planning_year"),
                                           "snapshot_date": opts.get("butce_snapshot")})}


def repo_for(**kw):
    r = OV.OwnerRepository(cfgs(**kw), background=False)
    r.ensure_loaded()
    return r


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return FX.write_all(str(tmp_path_factory.mktemp("own_data")))


@pytest.fixture(scope="module")
def ctx(data_dir):
    return CTX.build_context(data_dir, inflation_rate=0.40)


@pytest.fixture()
def client(data_dir, owner_dir):
    ctx = CTX.build_context(data_dir, inflation_rate=0.40)
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    c360 = C360.Customer360Repository(data_dir, repo, background=False)
    own = OV.OwnerRepository(cfgs(kredi=owner_dir["kredi"], gg=owner_dir["gg"], butce=owner_dir["butce"]),
                             background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        APP.app.dependency_overrides[APP.get_owner] = lambda: own
        yield cl, repo, c360, own
    APP.app.dependency_overrides.clear()


def _names(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def states(r):
    return {k: v["state"] for k, v in r.snapshot().items()}


# =============================================================================================
# 77. owner config (1–10)
# =============================================================================================
def test_01_no_owner_sources(client, data_dir):
    cl, *_ = client
    APP.app.dependency_overrides[APP.get_owner] = lambda: OV.OwnerRepository(cfgs(), background=False)
    d = cl.get("/api/owner/overview").json()
    for k in ("kredi", "gider_gelir", "butce"):
        assert d[k]["state"] == "not_configured" and d[k]["data"] is None and d[k]["file"] is None
    assert d["operations"]["state"] == "ready" and d["system"]["core"]["context_state"] == "ready"
    assert cl.get(f"/api/customer360/{P[17]}").status_code == 200                          # core unaffected
    assert cl.get("/api/sales-queue").status_code == 200
    assert OV.config_from_env({}) == {k: OV.OwnerSourceConfig(k, None, v.options)
                                      for k, v in OV.config_from_env({}).items()}
    assert all(not c.configured for c in OV.config_from_env({}).values())


@pytest.mark.parametrize("only", ["kredi", "gg", "butce"])
def test_02_03_04_single_source(owner_dir, only):
    r = repo_for(**{only: owner_dir[only]})
    key = {"kredi": "kredi", "gg": "gider_gelir", "butce": "butce"}[only]
    assert states(r) == {k: ("ready" if k == key else "not_configured") for k in OV.SOURCES}
    assert r.loads[key] == 1 and sum(r.loads.values()) == 1


def test_05_all_three(owner_dir):
    r = repo_for(kredi=owner_dir["kredi"], gg=owner_dir["gg"], butce=owner_dir["butce"])
    assert set(states(r).values()) == {"ready"}


def test_06_one_fails_others_succeed(owner_dir, tmp_path):
    bad = OS.write_broken(tmp_path, "BUTCE_2026 Bozuk.xlsx")
    r = repo_for(kredi=owner_dir["kredi"], gg=owner_dir["gg"], butce=bad)
    assert states(r) == {"kredi": "ready", "gider_gelir": "ready", "butce": "failed"}
    e = r.snapshot()["butce"]
    assert e["view"] is None and e["error_code"] in ("import_failed", "exception")
    missing = repo_for(kredi=tmp_path / "yok.xlsx", gg=owner_dir["gg"])
    assert states(missing)["kredi"] == "failed" and missing.snapshot()["kredi"]["error_code"] == "file_missing"
    assert states(missing)["gider_gelir"] == "ready"
    cfgbad = repo_for(kredi=owner_dir["kredi"], kredi_snapshot="31.12.2026", butce=owner_dir["butce"],
                      planning_year="26")
    assert states(cfgbad) == {"kredi": "failed", "gider_gelir": "not_configured", "butce": "failed"}
    assert cfgbad.snapshot()["kredi"]["error_code"] == "config_invalid"
    # a source whose importer raises never breaks the others
    boom = dict(OV.LOADERS, gider_gelir=lambda c: (_ for _ in ()).throw(RuntimeError("/gizli/yol")))
    r2 = OV.OwnerRepository(cfgs(kredi=owner_dir["kredi"], gg=owner_dir["gg"]), background=False, loaders=boom)
    r2.ensure_loaded()
    assert states(r2) == {"kredi": "ready", "gider_gelir": "failed", "butce": "not_configured"}
    assert "/gizli/yol" not in json.dumps(OV.source_head(r2, "gider_gelir", r2.snapshot()["gider_gelir"]))


def test_07_08_explicit_paths_only_never_candidates(owner_dir, monkeypatch, tmp_path):
    def nope(*a, **k):
        raise AssertionError("candidate discovery used")
    for mod, fn in ((KR, "find_kredi_candidates"), (GG, "find_gider_gelir_candidates"), (BU, "find_butce_candidates")):
        monkeypatch.setattr(mod, fn, nope)
    # a perfectly valid Kredi file in the folder is ignored while not configured
    OS.write_kredi(tmp_path, TODAY)
    r = repo_for(gg=owner_dir["gg"])
    assert states(r)["kredi"] == "not_configured"
    src = open(os.path.join(ROOT, "services", "owner_view.py"), encoding="utf-8").read()
    for banned in ("find_kredi_candidates", "find_gider_gelir_candidates", "find_butce_candidates", "glob(", "listdir"):
        assert banned not in src
    env = {"KREDI_PATH": str(owner_dir["kredi"]), "BUTCE_PLANNING_YEAR": "2026"}
    c = OV.config_from_env(env)
    assert c["kredi"].path == str(owner_dir["kredi"]) and not c["butce"].configured
    assert c["butce"].options["planning_year"] == "2026"


def test_09_no_mtime_business_date(owner_dir):
    names = _names(os.path.join(ROOT, "services", "owner_view.py"))
    assert not ({"getmtime", "st_mtime", "st_mtime_ns", "stat", "getctime"} & names)
    os.utime(owner_dir["kredi"], (2_000_000_000, 2_000_000_000))
    r = repo_for(kredi=owner_dir["kredi"])
    d = OV.kredi_section(r, r.snapshot()["kredi"], TODAY)["data"]
    assert d["snapshot_date"] is None and d["snapshot_note"] == OV.KREDI_SNAPSHOT_UNKNOWN
    r2 = repo_for(kredi=owner_dir["kredi"], kredi_snapshot="2026-02-20", butce=owner_dir["butce"],
                  butce_snapshot="2026-01-31")
    d2 = OV.kredi_section(r2, r2.snapshot()["kredi"], TODAY)["data"]
    assert (d2["snapshot_date"], d2["snapshot_origin"], d2["snapshot_note"]) == ("2026-02-20", "declared", None)
    b = OV.butce_section(r2, r2.snapshot()["butce"])["data"]
    assert (b["snapshot_date"], b["snapshot_origin"]) == ("2026-01-31", "declared")


def test_10_path_privacy(client, owner_dir, caplog):
    cl, *_ = client
    with caplog.at_level(logging.DEBUG):
        texts = [cl.get("/api/owner/overview").text, cl.get("/api/owner/status").text,
                 cl.post("/api/owner/reload", headers=H).text]
    d = json.loads(texts[0])
    assert d["kredi"]["file"] == OS.KREDI_FILE and d["butce"]["file"] == OS.BUTCE_FILE
    folder = str(owner_dir["dir"])
    for t in texts:
        assert folder not in t and "/tmp" not in t and os.path.expanduser("~") not in t
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert folder not in logs and OS.KREDI_FILE not in logs


# =============================================================================================
# 78. Kredi owner view (11–19)
# =============================================================================================
def kredi(tmp_path, today=TODAY, **kw):
    p = OS.write_kredi(tmp_path, today, **kw)
    r = repo_for(kredi=p)
    return OV.kredi_section(r, r.snapshot()["kredi"], today)


def test_11_12_13_14_horizons(tmp_path):
    k = kredi(tmp_path)
    sched = k["data"]["schedule"]
    assert [c["currency"] for c in sched] == ["TL"]
    tl = sched[0]
    total = OS.TK.inst()["TAKSİT TUTARI"]
    h = {x["days"]: x for x in tl["horizons"]}
    # +10 (partly paid: remaining only), +20 (2nd contract), +45, +80, +100, +200
    assert (h[30]["installments"], h[60]["installments"], h[90]["installments"]) == (2, 3, 4)
    assert h[30]["amount"] == round((total - 1000.0) + total, 2)                    # 12: remaining_amount used
    assert h[90]["amount"] == round((total - 1000.0) + 3 * total, 2)
    assert tl["after_90"] == {"installments": 2, "amount": round(2 * total, 2)}       # 13
    assert tl["past_due"] == {"installments": 1, "amount": total}                    # 14: separate bucket
    assert tl["total_remaining"]["installments"] == 7                                 # paid one never counted
    assert tl["next_due_date"] == (TODAY + dt.timedelta(days=10)).isoformat()
    assert "30/60/90 toplamlarına katılmaz" in k["overdue_note"]
    # boundaries are inclusive and cumulative
    assert OV.kredi_schedule(OV.KrediView("f", 1, 1, 1, ["TL"], None, None, None, {},
                                          [("TL", TODAY + dt.timedelta(days=30), 5.0),
                                           ("TL", TODAY + dt.timedelta(days=31), 7.0),
                                           ("TL", TODAY, 1.0)], 0, []), TODAY)[0]["horizons"][0] == \
        {"days": 30, "installments": 2, "amount": 6.0}


def test_14b_unverified_status_never_drops_unpaid(tmp_path):
    """DURUM is unverified: an installment marked 'Kapalı' with an unpaid remaining amount (no slip) stays an
    obligation and is counted in a warning; a paid one (remaining 0) is never an obligation."""
    i = OS.TK.inst
    c1 = OS.TK.CONTRACTS[0]
    rows = [i(c1, 1, TODAY - dt.timedelta(days=30), paid=True),
            i(c1, 2, TODAY + dt.timedelta(days=15), status="Kapalı"),
            i(c1, 3, TODAY + dt.timedelta(days=40))]
    p = OS.TK.book(tmp_path, rows)
    r = repo_for(kredi=p)
    d = OV.kredi_section(r, r.snapshot()["kredi"], TODAY)["data"]
    tl = d["schedule"][0]
    assert [h["installments"] for h in tl["horizons"]] == [1, 2, 2] and tl["total_remaining"]["installments"] == 2
    w = {x["code"]: x for x in d["warnings"]}
    assert w["status_closed_with_remaining"]["count"] == 1 and "doğrulanmamış" in w["status_closed_with_remaining"]["message"]


def test_15_16_currencies_separate_no_fx(tmp_path):
    k = kredi(tmp_path, currency2="EUR")
    sched = {c["currency"]: c for c in k["data"]["schedule"]}
    assert set(sched) == {"EUR", "TL"} and sched["EUR"]["total_remaining"]["installments"] == 2
    assert sched["TL"]["total_remaining"]["installments"] == 5
    src = open(os.path.join(ROOT, "services", "owner_view.py"), encoding="utf-8").read().lower()
    for banned in ("exchange", "fx_rate", "kur(", "tcmb", "convert"):
        assert banned not in src


def test_17_snapshot_unknown_warning(tmp_path):
    k = kredi(tmp_path)
    assert k["data"]["snapshot_note"] == "Kaynağın kesin anlık tarihi bilinmiyor; ödeme planı kaynakta kayıtlı kalan " \
                                         "tutarlara dayanır."
    assert "Canlı banka bakiyesi değildir" in k["horizon_note"] and "Nakit akış tahmini değildir" in k["schedule_note"]
    assert k["data"]["latest_payment_date"] == (TODAY - dt.timedelta(days=60)).isoformat()


def test_18_no_remaining_principal_overcount(tmp_path):
    k = kredi(tmp_path)
    assert k["data"]["remaining_principal_total"] is None
    assert "KALAN ANAPARA" in k["principal_note"]
    names = _names(os.path.join(ROOT, "services", "owner_view.py"))
    assert "remaining_principal" not in names            # never read, never summed


def test_19_no_customer_credit_semantics(client):
    cl, *_ = client
    d = cl.get("/api/owner/overview").json()["kredi"]
    assert d["label"] == "Şirket Finansmanı / Kredi Takibi" and "Müşteri kredisi" in d["purpose"]
    assert "DEĞİLDİR" in d["purpose"]
    blob = json.dumps(d, ensure_ascii=False)
    for secret in OS.TK.LENDER_NAMES + OS.TK.CONTRACTS + ("BNK-01", "BNK-02"):
        assert secret not in blob
    det = cl.get(f"/api/customer360/{P[17]}").text                 # Customer 360 never carries company financing
    assert "Finansman" not in det and "kredi_" not in det.lower() and "installment" not in det


# =============================================================================================
# 79. Gider/Gelir (20–27)
# =============================================================================================
@pytest.fixture(scope="module")
def gg_section(owner_dir):
    r = repo_for(gg=owner_dir["gg"])
    return OV.gider_gelir_section(r, r.snapshot()["gider_gelir"])


def test_20_24_native_lines_no_mapping(gg_section):
    d = gg_section["data"]
    assert [l["line"] for l in d["lines"]] == ["kirtasiye", "trodat", "trotec", "imalat"]
    assert [l["label"] for l in d["lines"]] == ["KIRTASIYE", "TRODAT", "TROTEC", "IMALAT"]
    assert d["management_mapping"] == "not_supplied"
    blob = json.dumps(gg_section, ensure_ascii=False).lower()
    assert "kaşe" not in blob.replace("(kaşe / kırtasiye / diğer-ithalat)", "")
    assert "management_category" not in blob


def test_21_basis_unverified(gg_section):
    assert gg_section["data"]["accounting_basis"] == "unverified"
    assert "doğrulanmadı" in gg_section["basis_note"]
    blob = json.dumps(gg_section, ensure_ascii=False)
    for bad in ("nakit gelir", "nakit gider", "tahakkuk geliri", "cash_revenue", "accrual"):
        assert bad not in blob


def test_22_year_revision(gg_section):
    d = gg_section["data"]
    assert (d["report_year"], d["report_year_origin"], d["revision_label"], d["snapshot_date"]) == \
        (2026, "filename", "Eylül", None)


def test_23_monthly_values(gg_section, owner_dir):
    res = GG.import_gider_gelir(owner_dir["gg"])
    months = {m["month"]: m for m in gg_section["data"]["months"]}
    assert sorted(months) == sorted(set(res.counts.months_with_revenue) | set(res.counts.months_with_expenses))
    for m, row in months.items():
        sales = sum(r.value.value for r in res.revenue if r.month == m and r.metric == "reported_sales")
        gp = sum(r.value.value for r in res.revenue if r.month == m and r.metric == "reported_gross_profit")
        exp = sum(e.reported_expense.value or 0 for e in res.expense_months if e.month == m)
        assert row["reported_sales"] == round(sales, 2) and row["reported_gross_profit"] == round(gp, 2)
        assert row["reported_expense"] == round(exp, 2)
    trodat = next(l for l in gg_section["data"]["lines"] if l["line"] == "trodat")
    assert trodat["reported_sales"] == round(sum(r.value.value for r in res.revenue if r.native_line.value == "trodat"
                                                 and r.metric == "reported_sales"), 2)
    assert {b["line"] for b in gg_section["data"]["budget"]} <= {"Satış", "Satış maliyeti", "Brüt Kar", "gider",
                                                                "NET KAR", "KAR-ZARAR"}


def test_25_warning_counts(owner_dir, tmp_path):
    p = OS.write_gider_gelir(tmp_path, alloc_override=True)
    r = repo_for(gg=p)
    d = OV.gider_gelir_section(r, r.snapshot()["gider_gelir"])["data"]
    codes = {w["code"]: w for w in d["warnings"]}
    assert {"accounting_basis_unverified", "management_mapping_not_supplied", "manual_allocation_overrides"} <= set(codes)
    assert codes["manual_allocation_overrides"]["count"] >= 1
    blob = json.dumps(d, ensure_ascii=False)
    for s in OS.TG.SECRET_NAMES + OS.TG.SECRET_CC:                                       # no expense names
        assert s not in blob


def test_26_27_no_m5_margin_no_reconciliation():
    src = open(os.path.join(ROOT, "services", "owner_view.py"), encoding="utf-8").read()
    names = _names(os.path.join(ROOT, "services", "owner_view.py"))
    assert not ({"m_margin", "_prof", "Net_Tutar_Maliyet_Dusulmus", "variance", "budget_vs_actual"} & names)
    assert "m_margin" not in src and "Net_Tutar_Maliyet_Dusulmus" not in src
    # Bütçe and Gider/Gelir views are built independently (no function takes both)
    for fn in ("build_gider_gelir_view", "build_butce_view"):
        node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == fn)
        assert len(node.args.args) == 1


# =============================================================================================
# 80. Bütçe (28–34)
# =============================================================================================
@pytest.fixture(scope="module")
def butce_section(owner_dir):
    r = repo_for(butce=owner_dir["butce"])
    return OV.butce_section(r, r.snapshot()["butce"]), BU.import_butce(owner_dir["butce"])


def test_28_to_32_health(butce_section):
    sec, res = butce_section
    h = sec["data"]
    assert h["source_status"] == "partially_usable" and sec["warning"] == OV.BUTCE_WARNING          # 28
    assert h["ref_errors"] == res.counts.ref_errors > 0                                             # 29
    assert h["div0_errors"] == res.counts.div0_errors                                               # 30
    assert h["external_links"] == 1 and h["external_dependent_cells"] == res.counts.external_dependent_facts > 0  # 31
    assert h["coverage_rate"] == round(res.counts.coverage_rate, 4) and 0 < h["coverage_pct"] <= 100  # 32
    assert h["usable_unverified"] == res.counts.usable_unverified
    assert h["invalid_cells"] == res.counts.invalid_facts + res.counts.invalid_cells_in_validated_regions
    assert h["authoritative"] is False and h["file_of_record"] == "unresolved"


def test_28b_complete_source_note(tmp_path):
    p = OS.write_butce(tmp_path, clean=True)
    r = repo_for(butce=p)
    sec = OV.butce_section(r, r.snapshot()["butce"])
    if sec["data"]["source_status"] == "complete":
        assert sec["warning"] == OV.BUTCE_COMPLETE_NOTE
    else:
        assert sec["warning"] == OV.BUTCE_WARNING


def test_33_34_no_repair_no_budget_vs_actual(butce_section, owner_dir):
    sec, _ = butce_section
    assert set(sec["data"]) == set(json.loads(json.dumps(sec["data"])))
    blob = json.dumps(sec, ensure_ascii=False)
    for bad in ("actual", "variance", "sapma", "gerçekleşen"):
        assert bad not in blob.lower()
    names = _names(os.path.join(ROOT, "services", "owner_view.py"))
    assert not ({"save", "load_workbook", "write", "facts", "usable_facts"} & names)
    for s in OS.TB.GROUP_TITLES.values():
        assert s not in blob


# =============================================================================================
# 81. status / limitations (35–43)
# =============================================================================================
def test_35_to_43_status_and_known_issues(client):
    cl, *_ = client
    d = cl.get("/api/owner/status").json()
    assert d["system"]["blocked"] == [{"key": "detailed_collections", "label": "Detaylı Tahsilat",
                                       "status_label": "Sağlanmadı / M6-4 bloke"}]                       # 35
    assert d["system"]["stores"]["crm_coverage_label"] == "Eksik kayıt olabilir"                       # 36
    codes = {i["code"]: i for i in d["known_issues"]}
    assert {"hedef_stale_fields", "dq1_margin", "dq2_stok_eritme_kapali", "dq4_stock_coverage", "dq5_m5_attributes",
            "cross_sell_review", "crm_incomplete", "detailed_collections_missing", "revenue_basis_unresolved",
            "owner_file_of_record"} <= set(codes)                                                     # 37–42
    assert {i["status"] for i in d["known_issues"]} <= {"open", "blocked"}                           # 43
    assert codes["detailed_collections_missing"]["status"] == "blocked"
    assert "dq3" not in json.dumps(codes).lower()                                                    # DQ-3 is fixed
    assert codes["dq4_stock_coverage"]["count"] == 0 and codes["crm_incomplete"]["count"] == 0
    assert d["access_controlled"] is True and "erişim kontrollüdür" in d["audience_note"]   # M8-CP1 (D-061)
    core = d["system"]["core"]
    assert (core["sales_file"], core["as_of_data_date"]) == (FX.SALES_FILE, "2026-06-30")
    assert d["system"]["hedef"] == {"configured": False, "file": None, "status": "not_configured"}
    assert d["system"]["stores"]["sqlite_available"] is True


def test_dq4_count_from_stock_frame(tmp_path):
    from tests.fixtures import synthetic_erp as SE
    d = SE.write_all(str(tmp_path / "se"))
    ctx = CTX.build_context(d, inflation_rate=0.4)
    st = DB.read_stock(DB.select_sources(d)["stock"])
    assert ctx.data_quality["stock_coverage_unavailable_products"] == int(DB.num(st[DB.SC["cov"]]).isna().sum())
    assert "data_quality" not in ctx and "data_quality" not in json.dumps(ctx, default=str)


# =============================================================================================
# 82. DQ-3 (44–48)
# =============================================================================================
def _mom_frame(order):
    rows = []
    for c in order:
        rows += [{"_cust": c, "_cname": f"Sentetik {c}", "_date": pd.Timestamp("2025-02-01"), "_yr": 2025,
                  "Nettutar": 40000.0},
                 {"_cust": c, "_cname": f"Sentetik {c}", "_date": pd.Timestamp("2026-02-01"), "_yr": 2026,
                  "Nettutar": 30000.0}]
    rows += [{"_cust": "ZZ-9", "_cname": "Sentetik Z", "_date": pd.Timestamp("2025-02-01"), "_yr": 2025, "Nettutar": 50000.0},
             {"_cust": "ZZ-9", "_cname": "Sentetik Z", "_date": pd.Timestamp("2026-02-01"), "_yr": 2026, "Nettutar": 60000.0}]
    df = pd.DataFrame(rows)
    df["sto_kod"], df["StokIsmi"], df["miktar"], df["_ord"] = "S1", "Ürün", 1, df.index.astype(str)
    return df


def test_44_to_47_tie_order(monkeypatch):
    monkeypatch.setattr(DB, "_cust_detail", lambda df: {})
    codes = ["C-30", "C-10", "C-20", "A-99"]
    a = DB.m_momentum(_mom_frame(codes), {})
    b = DB.m_momentum(_mom_frame(list(reversed(codes))), {})
    assert [r["code"] for r in a] == ["A-99", "C-10", "C-20", "C-30", "ZZ-9"]       # 44: equal pct → code order
    assert a == b                                                                   # 47: insertion order irrelevant
    assert {r["code"]: (r["pct"], r["y2025"], r["y2026"]) for r in a} == \
        {**{c: (-25.0, 40000, 30000) for c in codes}, "ZZ-9": (20.0, 50000, 60000)}  # 45, 46


def test_48_hash_seed_independent(synthetic_dir, tmp_path):
    outs = []
    for seed in ("1", "12345"):
        out = tmp_path / f"d{seed}.json"
        code = ("import json,sys,dashboard_builder as DB;"
                "src=DB.select_sources(sys.argv[1]);sdf=DB.load_sales(src['sales']);attr=DB.cust_attrs(sdf);"
                "json.dump(DB.m_momentum(sdf,attr),open(sys.argv[2],'w'),default=str)")
        subprocess.run([sys.executable, "-c", code, synthetic_dir, str(out)], cwd=ROOT, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed})
        outs.append(out.read_text())
    assert outs[0] == outs[1]


def test_48b_dashboard_bytes_hash_seed_independent(synthetic_dir, tmp_path):
    outs = []
    for seed in ("1", "777"):
        out = tmp_path / f"o{seed}.html"
        code = ("import sys,datetime as dt,dashboard_builder as DB;"
                "DB.build(sys.argv[1],sys.argv[2],today=dt.date(2026,7,1))")
        subprocess.run([sys.executable, "-c", code, synthetic_dir, str(out)], cwd=ROOT, check=True,
                       env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True)
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]


# =============================================================================================
# 83. performance reuse (49–53)
# =============================================================================================
def test_49_50_handoff_identical_snapshot_without_second_read(data_dir, monkeypatch):
    ctx_a = CTX.build_context(data_dir, inflation_rate=0.4)
    ctx_b = CTX.build_context(data_dir, inflation_rate=0.4)
    ctx_b.sales_c360_handoff = None                                    # old path: Customer 360 reads the workbook
    reads = []
    real = DB.read_sales_raw
    monkeypatch.setattr(DB, "read_sales_raw", lambda p: (reads.append(p), real(p))[1])
    sa = C360.build_snapshot(data_dir, ctx_a)
    assert reads == []                                                 # 50: no second sales read
    sb = C360.build_snapshot(data_dir, ctx_b)
    assert len(reads) == 1
    for f in ("customers", "search_rows", "account_customers", "account_names", "salespeople", "events", "master_status",
              "events_status", "aging", "as_of", "sales_file"):
        assert getattr(sa, f) == getattr(sb, f), f
    for f in ("years", "products", "categories", "groups", "accounts"):
        pd.testing.assert_frame_equal(getattr(sa, f), getattr(sb, f))
    da, db_ = dict(sa.diagnostics), dict(sb.diagnostics)
    da.pop("build_ms"), db_.pop("build_ms")
    assert da == db_
    for code in list(sa.customers)[:20]:
        assert C360.customer_detail(sa, code) == C360.customer_detail(sb, code)       # 49: event section identical
    assert ctx_a.sales_c360_handoff.available is False                                # released after one use


def test_51_52_handoff_private(ctx, data_dir):
    c = CTX.build_context(data_dir, inflation_rate=0.4)
    assert isinstance(c.sales_c360_handoff, SP.SalesFrameHandoff) and c.sales_c360_handoff.available
    blob = json.dumps(c, default=str)
    assert "sales_c360_handoff" not in c and "Handoff" not in blob and "_skuname" not in blob
    assert "rows" not in repr(c.sales_c360_handoff) and "available=True" in repr(c.sales_c360_handoff)
    restored = pickle.loads(pickle.dumps(c.sales_c360_handoff))
    assert restored.available is False                                                # never pickled with data
    ev = open(os.path.join(ROOT, "services", "evidence.py"), encoding="utf-8").read()
    assert "handoff" not in ev and "sales_projection" not in ev
    for f in ("services/evidence.py", "services/agent.py", "services/context.py"):
        assert "customer360" not in open(os.path.join(ROOT, f), encoding="utf-8").read()


def test_53_refresh_a_to_b_atomic(tmp_path):
    folder = str(tmp_path / "ab")
    FX.write_all(folder, sales_file="Sentetik A fuar satışlar.xlsx")
    extra = [CF._line(9001, "2026-07-15", P[17], FX.name(17), P[17], FX.name(17), 777)]
    FX.write_all(folder, sales_file="Sentetik B fuar satışlar.xlsx", extra_rows=extra)
    base = {"stock": os.path.join(folder, FX.STOCK_FILE), "aging": os.path.join(folder, FX.AGING_FILE)}
    mk = lambda n: CTX.build_context(folder, inflation_rate=0.4, sources={**base, "sales": os.path.join(folder, n)})
    ctx_a, ctx_b = mk("Sentetik A fuar satışlar.xlsx"), mk("Sentetik B fuar satışlar.xlsx")
    repo = ContextRepository(data_folder=folder, context=ctx_a)
    c360 = C360.Customer360Repository(folder, repo, background=False)
    assert c360.snapshot().as_of.isoformat() == "2026-06-30"
    repo.replace_context(ctx_b)
    snap_b = c360.snapshot()
    assert snap_b.as_of.isoformat() == "2026-07-15" and snap_b.sales_file == "Sentetik B fuar satışlar.xlsx"
    assert not ctx_a.sales_c360_handoff.available and not ctx_b.sales_c360_handoff.available
    # a handoff never serves another file
    h = SP.SalesFrameHandoff("x.xlsx", pd.DataFrame({"a": [1]}))
    assert h.take("y.xlsx") is None and h.available and h.take("x.xlsx") is not None and h.take("x.xlsx") is None


def test_handoff_missing_columns_falls_back(tmp_path):
    assert SP.prepare_handoff("a.xlsx", pd.DataFrame({"Tarih": []})) is None
    assert SP.prepare_handoff("a.xlsx", None) is None


# =============================================================================================
# 84. owner reload (54–60)
# =============================================================================================
def test_54_55_57_58_reload(client, monkeypatch):
    cl, repo, c360, own = client
    APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
    cl.get("/api/owner/overview")
    assert own.loads == {"kredi": 1, "gider_gelir": 1, "butce": 1}
    assert cl.post("/api/owner/reload").status_code == 403                                     # 55
    assert cl.post("/api/owner/reload", headers={"X-StockAgent": "0"}).status_code == 403
    calls = []
    monkeypatch.setattr(CTX, "build_context", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(DB, "read_sales_raw", lambda *a, **k: calls.append(2))
    monkeypatch.setattr(DB, "read_stock", lambda *a, **k: calls.append(3))
    ctx_before = repo.current()
    r = cl.post("/api/owner/reload", headers=H).json()                                        # 54
    assert r["started"] is True and own.loads == {"kredi": 2, "gider_gelir": 2, "butce": 2}
    assert {s["state"] for s in r["sources"]} == {"ready"}
    assert calls == [] and repo.current() is ctx_before                                       # 58: no core refresh
    with sqlite3.connect(ST.DB_PATH) as c:                                                    # 57
        assert c.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == 0
    names = _names(os.path.join(ROOT, "services", "owner_view.py"))
    assert not ({"agent", "evidence", "provider", "get_provider", "log", "logging"} & names)


def test_core_refresh_never_reloads_owner(client, monkeypatch):
    cl, repo, c360, own = client
    cl.get("/api/owner/overview")
    before = dict(own.loads)
    src = open(os.path.join(ROOT, "services", "refresh.py"), encoding="utf-8").read()
    assert "owner" not in src.lower()
    ctx_src = open(os.path.join(ROOT, "services", "context.py"), encoding="utf-8").read()
    assert "owner_view" not in ctx_src and "import_kredi" not in ctx_src
    repo.replace_context(CTX.build_context(repo._folder, inflation_rate=0.4))
    cl.get("/api/owner/overview")
    assert own.loads == before


def test_56_failure_isolation_on_reload(owner_dir, tmp_path):
    k = tmp_path / OS.KREDI_FILE
    k.write_bytes(owner_dir["kredi"].read_bytes())
    r = repo_for(kredi=k, gg=owner_dir["gg"])
    assert states(r) == {"kredi": "ready", "gider_gelir": "ready", "butce": "not_configured"}
    k.write_bytes(b"bozuk")
    r.reload()
    assert states(r) == {"kredi": "failed", "gider_gelir": "ready", "butce": "not_configured"}
    assert r.snapshot()["kredi"]["view"] is None                       # never silently stale


def test_59_cached_get_no_excel_read(client, monkeypatch):
    cl, repo, c360, own = client
    cl.get("/api/owner/overview")
    c360.snapshot()

    def boom(*a, **k):
        raise AssertionError("Excel read after cache")
    import openpyxl
    for mod, nm in ((pd, "read_excel"), (DB, "read_sales_raw"), (DB, "read_aging_raw"), (DB, "read_stock"),
                    (openpyxl, "load_workbook"), (KR, "import_kredi"), (GG, "import_gider_gelir"), (BU, "import_butce")):
        monkeypatch.setattr(mod, nm, boom)
    for url in ("/api/owner/overview", "/api/owner/status", "/api/sales-queue", f"/api/customer360/{P[17]}",
                "/api/opportunities/summary", "/api/fairs/history", "/api/fairs/campaigns", "/api/crm/summary"):
        assert cl.get(url).status_code == 200, url


def test_60_rapid_reload_safe(owner_dir):
    gate, started = threading.Event(), threading.Event()
    real = OV.LOADERS["kredi"]

    def slow(cfg):
        started.set()
        gate.wait(5)
        return real(cfg)
    r = OV.OwnerRepository(cfgs(kredi=owner_dir["kredi"]), background=True, loaders=dict(OV.LOADERS, kredi=slow))
    r.ensure_loaded()
    assert started.wait(5) and r.inflight
    assert r.snapshot()["kredi"]["state"] == "loading"
    outs = [r.reload() for _ in range(5)]                              # ignored while in flight
    assert all(o["started"] is False for o in outs) and r.generation == 1
    gate.set()
    for _ in range(200):
        if not r.inflight:
            break
        threading.Event().wait(0.02)
    assert r.snapshot()["kredi"]["state"] == "ready" and r.loads["kredi"] == 1
    # a late result of an older generation never overwrites a newer one
    r2 = OV.OwnerRepository(cfgs(kredi=owner_dir["kredi"]), background=False)
    r2._gen = 5
    r2._run(3)
    assert r2.snapshot()["kredi"]["state"] == "loading" and r2.snapshot()["kredi"]["view"] is None


def test_reload_keeps_previous_view_while_loading(owner_dir):
    gate = threading.Event()
    real = OV.LOADERS["kredi"]
    first = [True]

    def maybe_slow(cfg):
        if not first[0]:
            gate.wait(5)
        first[0] = False
        return real(cfg)
    r = OV.OwnerRepository(cfgs(kredi=owner_dir["kredi"]), background=True, loaders=dict(OV.LOADERS, kredi=maybe_slow))
    r.ensure_loaded()
    for _ in range(200):
        if not r.inflight:
            break
        threading.Event().wait(0.02)
    assert r.snapshot()["kredi"]["state"] == "ready"
    r.reload()
    e = r.snapshot()["kredi"]
    assert e["state"] == "ready" and e["reloading"] is True and e["view"] is not None    # no mixed / empty section
    gate.set()
    for _ in range(200):
        if not r.inflight:
            break
        threading.Event().wait(0.02)
    assert r.snapshot()["kredi"]["reloading"] is False


# =============================================================================================
# 85. full integration (61–70)
# =============================================================================================
def test_61_m5_top15_unchanged(data_dir, monkeypatch):
    """The M5 top 15 equals the plain M5 producers (no M7 source) cut to MAX_DAILY_ACTIONS, with or without the
    M7-6 frame handoff; the DQ-3 change only fixes the order of equal-pct rows."""
    from services import actions as A
    seen = {}
    real = CTX.build_action_candidates
    monkeypatch.setattr(CTX, "build_action_candidates", lambda *a, **k: (seen.setdefault("a", a), real(*a, **k))[1])
    ctx = CTX.build_context(data_dir, inflation_rate=0.40)
    c, coll, over, lap, shr, infl = seen["a"]
    inputs = {"ctx": c, "coll_scored": coll, "over_cand": over, "lap": lap, "shrink_real_neg": shr, "infl": infl}
    acts = []
    for src in [x for x in A._SOURCES if x.name not in ("cross_sell_anagrup", "fair_campaign", "crm_followup")]:
        for a in src.produce(inputs) or []:
            a.setdefault("source", src.name)
            acts.append(A.validate(a))
    acts.sort(key=lambda a: -a["score"])
    assert acts[:C.MAX_DAILY_ACTIONS] == ctx["actions"]
    raw = DB.read_sales_raw(DB.select_sources(data_dir)["sales"])
    frames_ctx = CTX.build_context(data_dir, inflation_rate=0.40, frames={"sales": raw})
    assert frames_ctx["actions"] == ctx["actions"] and frames_ctx["company"] == ctx["company"]


def test_62_to_66_rules_and_scores_unchanged():
    assert (C.CRM_FOLLOWUP_SCORE, C.FAIR_TASK_SCORE) == (60, 55)
    assert (C.CROSS_SELL_RECENT_DAYS, C.CROSS_SELL_MIN_SOURCE_BUYERS, C.CROSS_SELL_MIN_TARGET_BUYERS,
            C.CROSS_SELL_MIN_PAIR_BUYERS, C.CROSS_SELL_MIN_CONFIDENCE, C.CROSS_SELL_MIN_LIFT,
            C.CROSS_SELL_MAX_TARGETS_PER_CUSTOMER, C.CROSS_SELL_SCORE_CAP) == (365, 20, 20, 10, 0.30, 1.20, 3, 69)
    assert SQ.SEQUENCE_RANK == {"collections": 0, "crm_followup": 1, "winback": 2, "fair_followup": 3}
    from services import crm as CRM
    assert (CRM.COVERAGE_STATUS, CRM.KAPALI_ALLOWED) == ("incomplete", ("collection", "service"))


def test_67_state_survives_restart(tmp_path, data_dir, monkeypatch):
    db = str(tmp_path / "persist.db")
    monkeypatch.setattr(ST, "DB_PATH", db)
    ST.init_db()
    ctx = CTX.build_context(data_dir, inflation_rate=0.4)
    a = ctx["actions"][0]
    ST.upsert_actions([a])
    ST.set_status(a["action_id"], "completed")
    CS.add_interaction(P[17], occurred_on="2026-06-01", channel="phone", note="Sentetik")
    CS.set_followup(P[17], purpose="sales_followup", due_date="2026-07-01")
    cid = FS.create_campaign(name="Sentetik", edition_year=2027, start_date="2027-01-15")["campaign_id"]
    FS.add_member(cid, P[18])
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO ai_cache VALUES('k1','v1','m','t','{}')")
    for _ in range(2):                                                    # "restart": re-run the startup migration
        ST.init_db()
        with TestClient(APP.app):
            pass
    monkeypatch.setattr(ST, "DB_PATH", db)
    assert ST.get_statuses()[a["action_id"]]["status"] == "completed"
    assert CS.interaction_count(P[17]) == 1 and CS.get_followup(P[17])["due_date"] == "2026-07-01"
    assert FS.get_member(cid, P[18]) is not None
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0] == 1


def test_68_controlled_refresh_preserves_state(tmp_path, monkeypatch):
    from services import refresh as RF
    folder = str(tmp_path / "data")
    FX.write_all(folder)
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(folder, dash, inflation_rate=0.4)["ok"]
    CS.add_interaction(P[17], occurred_on="2026-06-01", channel="phone")
    CS.set_followup(P[17], purpose="service", due_date="2026-07-01")
    cid = FS.create_campaign(name="Sentetik", edition_year=2027, start_date="2027-01-15")["campaign_id"]
    FS.add_member(cid, P[18])
    extra = [CF._line(9001, "2026-07-15", P[17], FX.name(17), P[17], FX.name(17), 777)]
    FX.write_all(folder, sales_file="Zz Sentetik B fuar satışlar.xlsx", extra_rows=extra)
    os.remove(os.path.join(folder, FX.SALES_FILE))
    rep = RF.run_refresh(folder, dash, inflation_rate=0.4)
    assert rep["ok"] and rep["as_of_data_date"] == "2026-07-15"
    assert CS.interaction_count(P[17]) == 1 and CS.get_followup(P[17]) is not None
    assert FS.get_member(cid, P[18]) is not None


def test_69_file_mode_messages(data_dir, tmp_path):
    out = str(tmp_path / "d.html")
    DB.build(data_dir, out, today=dt.date(2026, 7, 1))
    h = open(out, encoding="utf-8").read()
    for msg in ("Fuar Merkezi", "Takip / CRM", "Yönetim"):
        assert f"{msg} için panoyu http://127.0.0.1:8000/ üzerinden açın." in h, msg
    for tab in ("sq", "c360", "opp", "fair", "crm", "own", "ai"):                # every server tab is present
        assert f'data-t="{tab}"' in h
    assert h.count("location.protocol==='file:'") + h.count('location.protocol==="file:"') >= 6


def test_70_zero_automatic_ai(client):
    cl, *_ = client
    APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
    for url in ("/api/owner/overview", "/api/owner/status"):
        r = cl.get(url)
        assert r.status_code == 200 and r.json()["ai_used"] is False
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == 0
    ev = open(os.path.join(ROOT, "services", "evidence.py"), encoding="utf-8").read()
    for s in ("owner_view", "kredi", "gider_gelir", "butce"):
        assert s not in ev.lower()


def test_owner_privacy_secrets(client, caplog):
    cl, *_ = client
    with caplog.at_level(logging.DEBUG):
        blob = cl.get("/api/owner/overview").text + cl.get("/api/owner/status").text
    for s in OS.SECRETS:
        assert s not in blob, s
    logs = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    for s in OS.SECRETS:
        assert s not in logs


# =============================================================================================
# UI block
# =============================================================================================
@pytest.fixture(scope="module")
def built(data_dir, tmp_path_factory):
    dd = tmp_path_factory.mktemp("own_dash")
    a, b = str(dd / "with.html"), str(dd / "without.html")
    DB.build(data_dir, a, today=dt.date(2026, 7, 1))
    DB.build(data_dir, b, today=dt.date(2026, 7, 1), own_panel=False)
    return open(a, encoding="utf-8").read(), open(b, encoding="utf-8").read()


OWN_BUTTON = '<button class="tab" data-t="own">Yönetim</button>'


def _block(h):
    return h[h.index("\n<!--OWN-TAB-START-->"): h.index("<!--OWN-TAB-END-->") + len("<!--OWN-TAB-END-->")]


def test_tab_present_rest_byte_identical(built):
    import re
    w, wo = built
    assert w.count(OWN_BUTTON) == 1 and w.replace(OWN_BUTTON, "", 1).replace(_block(w), "", 1) == wo
    assert re.findall(r'<button class="tab" data-t="([a-z0-9]+)"', w)[-7:] == \
        ["sq", "c360", "opp", "fair", "crm", "own", "ai"]


def test_tab_script_safe(built):
    block = _block(built[0])
    js = block[block.index("<script>") + 8: block.rindex("</script>")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage",
                   "sessionStorage", "/api/agent/", "confirm(", "alert(", "prompt(", "/api/context/refresh"):
        assert banned not in js
    assert "Yönetim için panoyu http://127.0.0.1:8000/ üzerinden açın." in js and "textContent" in js
    assert "Yönetim verilerini yeniden oku" in block and "erişim kontrollüdür" in block   # M8-CP1 (D-061)
    for bad in ("leaderboard", "temsilci sıralaması", "nakit akış tahmini:", "roi"):
        assert bad not in block.lower()
