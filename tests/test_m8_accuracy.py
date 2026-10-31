"""M8-ACCURACY — factual integrity and fail-closed display (synthetic data only; no AI calls, no network).

Every test here runs the REAL production gate (marker `production_gate`) unless it builds its own `Gate` explicitly to
prove a specific rule. They establish that invalid or unverified information can never become verified business
output, in the dashboard HTML or through any API, and that unaffected (verified) outputs stay unchanged.
"""
import dataclasses
import datetime as dt
import json
import os
import re
import subprocess
import sys

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as db
from services import accuracy as ACC
from services import refresh as RF
from services import store as ST
from services.agent import BusinessActionAgent
from services.ai.mock_provider import MockProvider
from services.entity import ContextRepository
from tests.fixtures import synthetic_erp as SE
from tests.fixtures.xlsx_synth import E, F, MISSING, write_xlsx

pytestmark = pytest.mark.production_gate
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = dt.date(2026, 7, 10)
S, NV, INV = ACC.State.VERIFIED, ACC.State.NOT_VERIFIED, ACC.State.INVALID


# ---------------------------------------------------------------------------------------------
# helpers: a tiny test register with one metric that CAN be verified when everything is in place
# ---------------------------------------------------------------------------------------------
DEF_OK = ACC.Definition("t.def", "Test tanımı", True, "D-999")
DEF_OPEN = ACC.Definition("t.def", "Test tanımı", False)


def _metric(key="t.metric", sources=("src",), deps=(), certified=S, blockers=(), defs=("t.def",),
            temporal="operational"):
    kw = dict(approval_refs=("D-999",), regression_tests=("tests/test_m8_accuracy.py",),
              reconciliation_ref="synthetic") if certified == S else {}
    age = ACC.DEFAULT_OPERATIONAL_MAX_AGE_DAYS if temporal == "operational" else None
    return ACC.Metric(key, key, ("test",), sources, defs, deps, blockers, certified, temporal=temporal,
                      max_source_age_days=age, **kw)


def clean(source="src", **kw):
    """Evidence where every check was actually made and passed (the only shape that can verify)."""
    rd = kw.pop("report_date", TODAY - dt.timedelta(days=3))
    base = dict(available=True, layout_supported=True, report_date=rd, report_date_origin="declared",
                period_start=dt.date(2025, 1, 1), period_end=rd, period_complete=True, error_cells=0,
                missing_cached_formulas=0, missing_required_values=0, duplicate_records=0, join_unmatched=0,
                join_ambiguous=0, joins_validated=True, totals_consistent=True, mapping_approved=True,
                blank_amount_cells=0, invalid_dates=0, ambiguous_headers=0, external_references=0, date_contradictions=0,
                selection_explicit=True, fields=_clean_fields(source), joins=_clean_joins())
    base.update(kw)
    return ACC.SourceEvidence(source, **base)


def _clean_fields(source):
    """M8-ACCURACY-2B: field-level evidence where every inspected field passed (scoped metrics need it)."""
    from services import source_validation as SV
    spec = SV.CORE.get(source)
    names = list(spec["required"]) + (["bucket:*"] if spec.get("buckets") else []) if spec else []
    return {h: {"error_cells": 0, "missing_cached_formulas": 0, "blank_cells": 0, "invalid_dates": 0,
                "external_refs": 0, "duplicate_header": False, "amount": False} for h in names}


def _clean_joins():
    from services import source_validation as SV
    return {j: {"unmatched": 0, "ambiguous": 0, "validated": True} for j in SV.JOINS}


GEN = "gen-test-1"


def snap(evidence, generation=GEN):
    return ACC.EvidenceSnapshot(generation, evidence, TODAY)


def gate(metrics, evidence=None, definitions=None, generation=GEN, expected=GEN):
    reg = {m.key: m for m in metrics}
    defs = definitions or {"t.def": DEF_OK}
    ACC.validate_register(reg, defs)
    return ACC.Gate(reg, defs, snap(evidence or {}, generation), TODAY, expected)


# ---------------------------------------------------------------------------------------------
# Part A — the register and the states
# ---------------------------------------------------------------------------------------------
def test_production_register_is_valid_and_nothing_is_verified_today():
    ACC.validate_register()
    g = ACC.current_gate(today=TODAY)
    states = {k: g.decide(k).state for k in ACC.REGISTER}
    assert S not in states.values(), "no metric may be VERIFIED without owner approval and real-data evidence"
    # even perfectly clean evidence for every source cannot promote anything past its certification
    g2 = ACC.current_gate(today=TODAY, snapshot=snap({s: clean(s) for s in ("stock", "sales", "aging", "hedef",
                                                                            "kredi", "gider_gelir", "liste")}),
                          expected_generation=GEN)
    assert all(not g2.decide(k).displayable for k in ACC.REGISTER)


def test_known_invalid_outputs_are_classified_invalid():
    g = ACC.current_gate(today=TODAY)
    for key, code in (("finance.margin", "DQ-1"), ("stock.clearance_buyers", "DQ-2"),
                      ("customers.winback", "DQ-6"), ("owner.gider_gelir", "GG-TOTALS"),
                      ("owner.butce", "BUTCE-ERRORS")):
        d = g.decide(key)
        assert d.state == INV and code in d.codes, key
    for key in ("stock.reorder", "stock.overstock"):
        d = g.decide(key)
        assert d.state == INV and "STOCK-THRESHOLDS-RETIRED" in d.codes
    for key in ("receivables.collections", "owner.kredi",
                "opportunities.cross_sell", "fairs.history", "crm.active_customers"):
        assert g.decide(key).state == NV, key


def test_kredi_durum_and_owner_definitions_stay_unapproved():
    for k in ("kredi_durum", "gg_accounting_basis", "gg_business_line_mapping", "butce_file_of_record",
              "revenue_basis", "lapsed_window", "cross_sell_thresholds", "fair_taxonomy", "hedef_file_of_record"):
        assert ACC.DEFINITIONS[k].approved is False, k
    assert ACC.DEFINITIONS["customer_identity"].approved and ACC.DEFINITIONS["customer_identity"].decision_ref == "D-003"


@pytest.mark.parametrize("bad", [
    lambda: _metric(blockers=(ACC.Blocker("X", NV, "neden"),)),                       # VERIFIED with an open blocker
    lambda: dataclasses.replace(_metric(), approval_refs=()),                            # no approval reference
    lambda: dataclasses.replace(_metric(), approval_refs=("owner said ok",)),            # not a D-xxx reference
    lambda: dataclasses.replace(_metric(), regression_tests=()),                         # no regression coverage
    lambda: dataclasses.replace(_metric(), reconciliation_ref=None),                     # no reconciliation evidence
])
def test_register_rejects_verified_without_evidence(bad):
    with pytest.raises(ACC.RegisterError):
        ACC.validate_register({"t.metric": bad()}, {"t.def": DEF_OK})


def test_register_rejects_unapproved_definition_and_cycles():
    with pytest.raises(ACC.RegisterError):                      # VERIFIED on an unapproved definition
        ACC.validate_register({"t.metric": _metric()}, {"t.def": DEF_OPEN})
    with pytest.raises(ACC.RegisterError):                      # "approved" without a decision reference
        ACC.validate_register({}, {"t.def": ACC.Definition("t.def", "x", True, None)})
    a = _metric("a", deps=("b",), certified=NV)
    b = _metric("b", deps=("a",), certified=NV)
    with pytest.raises(ACC.RegisterError):
        ACC.validate_register({"a": a, "b": b}, {"t.def": DEF_OK})


def test_tests_use_the_real_production_gate_and_production_code_never_imports_the_harness():
    g = ACC.current_gate(today=TODAY)
    assert type(g) is ACC.Gate and not g.decide("stock.reorder").displayable
    for path in ["app.py", "dashboard_builder.py", "config.py", "schemas.py", "weekly_agent.py"] + [
            os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(ROOT, "services")) for f in fs if f.endswith(".py")]:
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        assert "accuracy_harness" not in src and "AlgorithmHarnessGate" not in src, path
    assert "os.environ" not in open(os.path.join(ROOT, "services", "accuracy.py"), encoding="utf-8").read()


def test_a_fully_evidenced_metric_is_verified():
    assert gate([_metric()], {"src": clean()}).decide("t.metric").state == S


# ---------------------------------------------------------------------------------------------
# Part F — each data-quality failure downgrades; nothing invalid or unverified becomes VERIFIED
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("evidence,expected", [
    (dict(missing_required_values=1), INV),                         # missing financial values
    (dict(error_cells=1), INV),                                     # invalid Excel formulas
    (dict(missing_cached_formulas=1), INV),                         # missing cached formula results
    (dict(duplicate_records=1), INV),                               # duplicate source records
    (dict(join_unmatched=1), INV),                                  # incorrect customer joins
    (dict(join_ambiguous=1), INV),
    (dict(joins_validated=False), INV),
    (dict(joins_validated=None), NV),
    (dict(report_date=TODAY - dt.timedelta(days=400)), NV),         # stale report date (operational metric)
    (dict(report_date=None), NV),                                   # unknown report date
    (dict(report_date=TODAY + dt.timedelta(days=5)), INV),          # impossible (future) report date
    (dict(mapping_approved=False), NV),                             # unapproved business mapping
    (dict(mapping_approved=None), NV),
    (dict(period_complete=False), NV),                              # partial reporting period
    (dict(period_complete=None), NV),
    (dict(totals_consistent=False), INV),                           # contradictory source totals
    (dict(totals_consistent=None), NV),
    (dict(layout_supported=False), INV),                            # unsupported source layout
    (dict(available=False), NV),                                    # source not supplied
    (dict(available=None), NV),                                     # reading not established
    (dict(layout_supported=None), NV),
    (dict(error_cells=None), NV),                                   # a check that was never made is not "0 errors"
    (dict(missing_cached_formulas=None), NV),
    (dict(missing_required_values=None), NV),
    (dict(duplicate_records=None), NV),
    (dict(inspection_failures=("excel_errors",)), INV),             # an inspection that failed
])
def test_every_data_quality_failure_withholds(evidence, expected):
    d = gate([_metric()], {"src": clean(**evidence)}).decide("t.metric")
    assert d.state == expected and not d.displayable
    assert d.reasons_tr and all(isinstance(r, str) and r for r in d.reasons_tr)


def test_missing_evidence_is_not_verified():
    assert gate([_metric()], {}).decide("t.metric").state == NV


def test_unverified_business_definition_withholds_at_runtime():
    reg = {"t.metric": _metric()}
    d = ACC.Gate(reg, {"t.def": DEF_OPEN}, snap({"src": clean()}), TODAY, GEN).decide("t.metric")
    assert d.state == NV and any("Tanım onaylanmadı" in r for r in d.reasons_tr)


def test_verified_source_alongside_invalid_source():
    good = _metric("good", sources=("a",))
    other = _metric("other", sources=("b",))
    combined = _metric("combined", sources=("a", "b"))
    via_dep = _metric("via_dep", sources=("a",), deps=("other",))
    g = gate([good, other, combined, via_dep], {"a": clean("a"), "b": clean("b", error_cells=3)})
    assert g.decide("good").state == S                       # the independent verified output stays visible
    assert g.decide("other").state == INV
    assert g.decide("combined").state == INV                 # a combined output needs ALL inputs verified
    assert g.decide("via_dep").state == INV and "DEP:other" in g.decide("via_dep").codes


def test_gate_fails_closed_on_unknown_keys_and_internal_errors(monkeypatch):
    g = gate([_metric()], {"src": clean()})
    assert g.decide("does.not.exist").state == INV
    monkeypatch.setattr(ACC.Gate, "_decide", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    d = ACC.Gate(today=TODAY).decide("stock.reorder")
    assert d.state == INV and not d.displayable and "RuntimeError" not in json.dumps(d.notice())


def test_notice_has_no_numbers_paths_or_exception_text():
    for k in ACC.REGISTER:
        n = ACC.current_gate(today=TODAY).decide(k).notice()
        assert n["message"] == ACC.WITHHELD_MESSAGE == "Bu analiz henüz doğrulanmadığı için gösterilmiyor."
        blob = json.dumps(n, ensure_ascii=False)
        assert "/" not in n["message"] and "Traceback" not in blob and "\\\\" not in blob
        assert len(n["reasons"]) <= ACC.MAX_REASONS_SHOWN


# ---------------------------------------------------------------------------------------------
# evidence collection from raw core frames
# ---------------------------------------------------------------------------------------------
def test_invalid_formulas_and_missing_caches_in_a_stock_export_are_caught(tmp_path):
    cols = ["Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye",
            "Envanter değeri", "DURUM"]
    rows = [["Sentetik stok"], ["(test)"], [None], cols,
            ["SP-1", "Sentetik A", 5, 0.5, 1.0, 3.0, 100, ""],
            ["SP-2", "Sentetik B", 5, F("C6/0", E("#DIV/0!")), 1.0, 3.0, 100, ""],    # Excel error
            ["SP-3", "Sentetik C", 5, F("C7*2", MISSING), 1.0, 3.0, 100, ""]]          # no cached result
    p = tmp_path / "STOK Sentetik.xlsx"
    write_xlsx(p, {"Sheet1": rows})
    ev = ACC.collect_core_evidence(stock_df=db.read_stock(str(p)))["stock"]
    # the M5 pandas reader turns both into NaN; either way the source is not usable as verified input
    assert ev.missing_required_values == 2
    reg = {"stock.reorder": _metric("stock.reorder", sources=("stock",), defs=())}
    ev2 = dataclasses.replace(ev, report_date=TODAY, totals_consistent=True)
    assert ACC.Gate(reg, {}, snap({"stock": ev2}), TODAY, GEN).decide("stock.reorder").state == INV
    # string error values (as some exports deliver them) are counted as formula errors
    df = pd.DataFrame({c: [1, 2] for c in cols})
    df["STOK Kontrol %"] = ["#DIV/0!", 0.5]
    df["Stok_Kodu"] = ["SP-1", "SP-1"]                                                   # duplicate product row
    ev3 = ACC.collect_core_evidence(stock_df=df)["stock"]
    assert ev3.error_cells == 1 and ev3.duplicate_records == 1


def test_sales_evidence_counts_missing_amounts_duplicates_and_reports_the_data_date():
    df = pd.DataFrame(SE.sales_rows())
    df.loc[0, "Nettutar"] = None                                   # missing financial value
    df = pd.concat([df, df.iloc[[3]]], ignore_index=True)          # exact duplicate line
    ev = ACC.collect_core_evidence(sales_df=df)["sales"]
    assert ev.missing_required_values == 1 and ev.duplicate_records == 1
    # M8-ACCURACY-2: the newest transaction date is an OBSERVED fact, never the report date
    assert ev.latest_transaction_date == SE.AS_OF and ev.report_date is None and ev.report_date_origin is None
    assert ev.joins_validated is None and ev.totals_consistent is None      # never assumed


def test_unsupported_layout_is_invalid():
    ev = ACC.collect_core_evidence(stock_df=pd.DataFrame({"Kod": ["A"]}),
                                   aging_df=pd.DataFrame({"Kod": ["A"], "Bakiye": [1]}))
    assert ev["stock"].layout_supported is False and ev["aging"].layout_supported is False


# ---------------------------------------------------------------------------------------------
# Part C — DQ-6 (new): win-back overdue joins invoice-account debt by the order-source code
# ---------------------------------------------------------------------------------------------
def _dq6_frames():
    sales = pd.DataFrame(SE.sales_rows())
    aging = SE.aging_frame()
    extra = {c: 0 for c in SE.AGING_COLUMNS[3:]}
    extra.update({"Hesap kodu": "900.99.0001", "Hesap adı": "Sentetik Fatura Merkezi", "Temsilci kodu": "TEMSILCI-A",
                  "Mayıs-2026": 12_000})
    aging = pd.concat([aging, pd.DataFrame([extra])], ignore_index=True)
    return sales, aging


def test_dq6_winback_overdue_identity_mismatch():
    """Pins the defect: the order-source customer 900.00.0001 is invoiced through 900.99.0001, which owes money,
    but the dashboard win-back row shows 0 overdue. The register therefore marks the module INVALID (DQ-6)."""
    sales, aging = _dq6_frames()
    _, owed = db.m_collections(None, aging)
    sdf = db.load_sales(None, sales)
    lap = {r["code"]: r for r in db.m_lapsed(sdf, owed, db.cust_attrs(sdf))}
    assert owed["900.99.0001"] == 12_000
    assert lap["900.00.0001"]["overdue"] == 0                     # claims "no overdue" — the identity mismatch
    d = ACC.current_gate(today=TODAY).decide("customers.winback")
    assert d.state == INV and "DQ-6" in d.codes


@pytest.mark.xfail(strict=True, reason="DQ-6 open: needs an owner-approved rule (no allocation of invoice-account "
                                        "debt to order-source customers, D-042); remove the mark when fixed")
def test_dq6_winback_never_claims_zero_overdue_for_a_debtor_invoice_account():
    sales, aging = _dq6_frames()
    _, owed = db.m_collections(None, aging)
    sdf = db.load_sales(None, sales)
    lap = {r["code"]: r for r in db.m_lapsed(sdf, owed, db.cust_attrs(sdf))}
    assert lap["900.00.0001"]["overdue"] != 0


# ---------------------------------------------------------------------------------------------
# Part D — the dashboard HTML (backend build): withheld outputs are never embedded
# ---------------------------------------------------------------------------------------------
def _data_blob(html):
    m = re.search(r"const DATA=(\{.*?\});\n", html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


def _names_and_codes(data_dir):
    """Every customer / product name and code the synthetic analyses produce (to prove none reaches the HTML)."""
    src = db.select_sources(data_dir)
    low, over, _ = db.m_stock(src["stock"])
    coll, owed = db.m_collections(src["aging"])
    sdf = db.load_sales(src["sales"])
    attr = db.cust_attrs(sdf)
    rows = low + over + coll + db.m_lapsed(sdf, owed, attr) + db.m_margin(sdf, attr)
    vals = {str(r.get(k)) for r in rows for k in ("name", "code") if r.get(k)}
    return {v for v in vals if len(v) >= 5}


def test_production_dashboard_embeds_no_withheld_rows(tmp_path):
    data = SE.write_all(str(tmp_path / "data"))
    out = str(tmp_path / "dashboard.html")
    stats = {}
    _, counts = db.build(data, out, today=TODAY, stats_out=stats)
    html = open(out, encoding="utf-8").read()
    assert counts["low"] is None and counts["lapsed"] and counts["margin"]  # Stock retired; unrelated algorithms ran
    blob = _data_blob(html)
    assert set(blob) == set(ACC.MODULE_METRIC)
    for k, m in blob.items():
        assert m["rows"] == [] and m["cols"] == [], k                      # nothing to render
        assert m["withheld"]["message"] == ACC.WITHHELD_MESSAGE and m["withheld"]["metric"] == ACC.MODULE_METRIC[k]
    leaked = [v for v in _names_and_codes(data) if v in html]
    assert not leaked, leaked
    assert f'<meta name="stockagent-accuracy" content="{ACC.POLICY_VERSION}">' in html
    assert '<div class="stats"></div>' in html and "Geri kazanım dağılımı" not in html
    assert "hiçbir şey tahmin değil" not in html
    # server-driven panels: withheld ones carry a notice and NO script (they cannot call their APIs)
    for pid, marker in (("sq", "SQ"), ("c360", "C360"), ("opp", "OPP")):
        block = html.split(f"<!--{marker}-TAB-START-->")[1].split(f"<!--{marker}-TAB-END-->")[0]
        assert 'data-withheld="1"' in block and "<script" not in block and ACC.WITHHELD_MESSAGE in block
    ai = html.split("<!--AI-TAB-START-->")[1].split("<!--AI-TAB-END-->")[0]
    assert 'data-withheld="1"' in ai and ACC.WITHHELD_MESSAGE in ai
    assert all(v != "verified" for v in stats["accuracy"].values())


def test_unaffected_verified_metric_renders_unchanged_beside_an_invalid_one(tmp_path):
    """A verified output must stay exactly as computed while an invalid one next to it is withheld."""
    data = SE.write_all(str(tmp_path / "data"))
    reg = dict(ACC.REGISTER)
    reg["stock.reorder"] = dataclasses.replace(reg["stock.reorder"], blockers=(), definitions=(), certified=S,
                                               approval_refs=("D-999",), regression_tests=("this test",),
                                               reconciliation_ref="synthetic fixture")
    ACC.validate_register(reg)                                  # a synthetic copy: not the production register
    with pytest.raises(ACC.RegisterError):                      # …the production rules refuse any promotion today
        ACC.validate_register(reg, production=True)
    from services import generation as GEN
    src = db.select_sources(data)
    frozen = GEN.freeze(src)
    frames = GEN.frames_from_frozen(frozen)
    m = GEN.build_manifest(frozen, frames, {}, "explicit", TODAY)
    clean_snap = ACC.EvidenceSnapshot(m.generation_id, {"stock": clean("stock")})
    g = ACC.Gate(reg, ACC.DEFINITIONS, clean_snap, TODAY, m.generation_id)
    m_clean = GEN.Manifest(m.created_at, tuple(dataclasses.replace(r, evidence=clean(r.key)) if r.key == "stock" else r
                                               for r in m.core), selection_origin="explicit")
    out = str(tmp_path / "d.html")
    db.build(data, out, today=TODAY, gate=ACC.Gate(reg, ACC.DEFINITIONS, None, TODAY), frames=frames, sources=src,
             manifest=m_clean)
    blob = _data_blob(open(out, encoding="utf-8").read())
    low, _, _ = db.m_stock(src["stock"])
    assert blob["reorder"]["rows"] == json.loads(json.dumps(low)) and "withheld" not in blob["reorder"]
    assert blob["margin"]["rows"] == [] and blob["margin"]["withheld"]["verification"] == "invalid"
    # the evidence actually collected from the synthetic files would NOT verify it (no declared date, joins unknown…)
    out2 = str(tmp_path / "d2.html")
    db.build(data, out2, today=TODAY, gate=ACC.Gate(reg, ACC.DEFINITIONS, None, TODAY), frames=frames, sources=src,
             manifest=m)
    assert _data_blob(open(out2, encoding="utf-8").read())["reorder"]["rows"] == []
    assert g.decide("stock.reorder").state == S                    # (the gate itself accepts clean bound evidence)


def _frames(data):
    src = db.select_sources(data)
    return {"stock": db.read_stock(src["stock"]), "sales": db.read_sales_raw(src["sales"]),
            "aging": db.read_aging_raw(src["aging"])}


def test_withheld_module_is_rendered_as_a_notice_by_the_page_script(tmp_path):
    """Dashboard attempt to render a withheld metric: the page script finds no rows and shows the notice."""
    data = SE.write_all(str(tmp_path / "data"))
    out = str(tmp_path / "dashboard.html")
    db.build(data, out, today=TODAY)
    html = open(out, encoding="utf-8").read()
    assert "if(m.withheld){" in html and "p.dataset.withheld='1'" in html


# ---------------------------------------------------------------------------------------------
# Part D — the API (shared response layer)
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch, synthetic_ctx):
    monkeypatch.setenv("AGENT_DB", str(tmp_path / "state.db"))
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "state.db"))
    ST.init_db()
    provider = MockProvider()
    calls = {"n": 0}
    real = provider.generate_structured

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(provider, "generate_structured", counting)
    APP.app.state.repo = ContextRepository(context=synthetic_ctx)
    APP.app.state.provider = provider
    APP.app.state.agent = BusinessActionAgent(provider)
    c = TestClient(APP.app, headers={"X-StockAgent": "1"})
    c.ai_calls = calls
    return c


def _fill(path):
    return (path.replace("{customer_code}", "900.00.0006").replace("{product_code}", "SP-002")
            .replace("{event_id}", "x").replace("{campaign_id}", "1").replace("{action_id}", "x"))


def test_every_route_is_classified():
    seen = set()
    for r in APP.app.routes:
        for m in getattr(r, "methods", None) or ():
            if m in ("HEAD", "OPTIONS"):
                continue
            seen.add((m, r.path))
    missing = sorted(seen - set(ACC.ROUTES))
    assert not missing, missing
    assert not sorted(set(ACC.ROUTES) - seen), "ROUTES lists routes the app does not have"


@pytest.mark.parametrize("method,path", sorted(k for k, v in ACC.ROUTES.items() if v[0] == "metric"))
def test_api_attempts_to_access_withheld_metrics_get_no_data(client, method, path):
    body = {"/api/agent/actions/enrich": {"action_ids": ["x"]},
            "/api/agent/draft-message": {"customer_code": "900.00.0006", "channel": "whatsapp",
                                         "purpose": "sales_followup"},
            "/api/scenario": {"financing_rate": 40}}.get(path)
    r = client.request(method, _fill(path), json=body)
    assert r.status_code == ACC.WITHHELD_HTTP_STATUS
    d = r.json()["detail"]
    assert d["error_code"] == "accuracy_withheld" and d["message"] == ACC.WITHHELD_MESSAGE
    assert set(d) == {"error_code", "state", "metric", "title", "verification", "message", "reasons"}
    assert "900.00" not in r.text and "Sentetik" not in r.text and "₺" not in r.text
    assert client.ai_calls["n"] == 0                                   # no AI call can happen


def test_unclassified_route_is_withheld(client, monkeypatch):
    routes = dict(ACC.ROUTES)
    routes.pop(("GET", "/api/crm/recent"))
    monkeypatch.setattr(ACC, "ROUTES", routes)
    r = client.get("/api/crm/recent")
    assert r.status_code == 409 and r.json()["detail"]["metric"].startswith("route:")


def test_system_and_reference_routes_still_answer(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/agent/categories").status_code == 200
    assert client.get("/api/macro").status_code == 200


def test_ai_is_off_and_never_called_even_with_a_provider_available(client):
    assert client.get("/api/health").json()["ai_available"] is True       # a provider exists (mock)…
    for path, body in (("/api/agent/actions/enrich", {"action_ids": ["a"]}),
                       ("/api/agent/customer/900.00.0006/analyze", None),
                       ("/api/agent/product/SP-002/analyze", None),
                       ("/api/agent/draft-message", {"customer_code": "900.00.0006", "channel": "whatsapp",
                                                     "purpose": "sales_followup"})):
        assert client.post(path, json=body).status_code == 409
    assert client.ai_calls["n"] == 0                                         # …but it is never reached


def test_default_ai_provider_is_disabled(monkeypatch):
    import config as C
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert C.ai_settings()["provider"] == "disabled"


def test_redactions_in_shared_response_layer():
    g = ACC.current_gate(today=TODAY)
    crm = ACC.redact_crm_summary({"active_customers": 5, "active_customers_with_structured_contact": 2,
                                  "total_customers": 9}, g)
    assert crm["active_customers"] is None and crm["active_customers_with_structured_contact"] is None
    assert crm["total_customers"] == 9 and crm["withheld"][0]["metric"] == "crm.active_customers"
    camp = ACC.redact_fair_campaign({"linked_event": {"configured": True, "found": True, "event_id": "E",
                                                      "orders": 3, "net_order_amount": 1000.0}}, g)
    le = camp["linked_event"]
    assert le["withheld"] and le["found"] is None and "orders" not in le and "net_order_amount" not in le
    mem = ACC.redact_fair_member({"segments": ["winback"], "collection_first": True, "erp_fuar_orders": 2,
                                  "erp_evidence": "x", "erp_mismatch": "erp_order_stage_differs", "stage": "invited"}, g)
    assert mem["segments"] == [] and mem["collection_first"] is None and mem["erp_fuar_orders"] is None
    assert mem["erp_mismatch"] is None and mem["stage"] == "invited"           # user-entered state is kept
    for key in ("owner.kredi", "owner.gider_gelir", "owner.butce"):
        sec = ACC.redact_owner_section({"data": {"schedule": [1]}, "state": "loaded"}, key, g)
        assert sec["data"] is None and sec["withheld"]["metric"] == key
    ops = ACC.redact_operations({"daily_sales_customers": 4, "cross_sell_share": 0.7, "crm_overdue": 1}, g)
    assert ops["daily_sales_customers"] is None and ops["cross_sell_share"] is None and ops["crm_overdue"] == 1
    rep = ACC.redact_refresh_report({"counts": {"products": 5, "low_stock": 1, "lapsed": 2, "overstock_value": 9,
                                                "overdue_total": 7, "actions": 3, "surprise": 1}}, g)
    # M8-ACCURACY-2B: the product count is a withheld metric too (stock.product_counts: code authority, report scope)
    assert rep["counts"] == {"products": None, "low_stock": None, "lapsed": None, "overstock_value": None,
                             "overdue_total": None, "actions": None, "surprise": None}


def test_crm_and_fair_campaign_endpoints_redact_but_keep_operational_records(tmp_path, monkeypatch):
    """Operational records (CRM entries, fair campaign members and stages) stay usable; the analytic classifications
    and ERP-derived evidence attached to them are withheld field by field."""
    from services import context as CTX
    from services import customer360 as C360
    from tests.fixtures import fair_synth as FX
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "s.db"))
    ST.init_db()
    data = FX.write_all(str(tmp_path / "d"))
    ctx = CTX.build_context(data, inflation_rate=0.40)
    repo = ContextRepository(data_folder=data, context=ctx)
    c360 = C360.Customer360Repository(data, repo, background=False)
    ed = next(iter(ctx.fairs.editions.values()))
    code = FX.P[1]
    with TestClient(APP.app, headers={"X-StockAgent": "1"}) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        try:
            r = cl.post(f"/api/crm/customers/{code}/interactions",
                        json={"occurred_on": FX.AS_OF.isoformat(), "channel": "phone", "purpose": "sales_followup",
                              "outcome": "Sentetik görüşme"})
            assert r.status_code == 200, r.text
            s = cl.get("/api/crm/summary").json()
            assert s["active_customers"] is None and s["active_customers_with_structured_contact"] is None
            assert s["structured_interactions"] == 1 and s["withheld"][0]["metric"] == "crm.active_customers"
            camp = cl.post("/api/fairs/campaigns", json={"name": "Sentetik Kampanya", "edition_year": ed.edition_year,
                                                         "start_date": "2027-01-10", "event_stem": ed.stem,
                                                         "event_sequence": ed.sequence}).json()
            le = camp["linked_event"]
            assert le["withheld"] is True and le["found"] is None and le["orders"] is None
            assert le["net_order_amount"] is None and le["message"] == ACC.WITHHELD_MESSAGE
            cid = camp["campaign_id"]
            assert cl.post(f"/api/fairs/campaigns/{cid}/customers", json={"customer_codes": [code]}).status_code == 200
            items = cl.get(f"/api/fairs/campaigns/{cid}/customers?segment=winback").json()["items"]
            assert [i["customer_code"] for i in items] == [code]          # segment filter ignored while withheld
            it = items[0]
            assert it["segments"] == [] and it["collection_first"] is None and it["erp_fuar_orders"] is None
            assert it["stage"] == "invite_candidate"                       # the user's own record is intact
            assert cl.get("/api/fairs/candidates?segment=winback").status_code == 409
            assert cl.get("/api/fairs/history").status_code == 409
            lst = cl.get("/api/fairs/campaigns").json()["items"]
            assert lst[0]["linked_event"]["withheld"] is True
            assert cl.get("/api/customer360/search?q=" + code).status_code == 200      # directory lookup only
            assert cl.get("/api/customer360/" + code).status_code == 409
        finally:
            APP.app.dependency_overrides.clear()


def test_owner_overview_withholds_financial_sections(client, monkeypatch):
    from services import owner_view as OV
    owner = OV.OwnerRepository(OV.config_from_env({}), background=False)

    class _C360:
        hedef_path = None

        def snapshot(self):
            from services import customer360 as C360
            raise C360.Customer360NotReady("building")

        def signature_for(self, ctx):
            return None
    APP.app.dependency_overrides[APP.get_owner] = lambda: owner
    APP.app.dependency_overrides[APP.get_c360] = lambda: _C360()
    try:
        r = client.get("/api/owner/overview")
    finally:
        APP.app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    j = r.json()
    for k in ("kredi", "gider_gelir", "butce"):
        assert j[k]["data"] is None and j[k]["withheld"]["message"] == ACC.WITHHELD_MESSAGE
    assert j["operations"]["withheld"]["metric"] == "owner.operations"
    codes = {i["code"] for i in j["known_issues"]}
    assert {"m8_accuracy_withheld", "dq6_winback_overdue_identity", "dq1_margin"} <= codes


# ---------------------------------------------------------------------------------------------
# served dashboard, refresh and the legacy weekly e-mail
# ---------------------------------------------------------------------------------------------
def test_root_refuses_a_dashboard_built_without_the_gate(client, tmp_path, monkeypatch):
    legacy = tmp_path / "dashboard.html"
    legacy.write_text("<!doctype html><title>Şirket Panosu</title><script>const DATA={}</script>", encoding="utf-8")
    monkeypatch.setattr(APP, "DASHBOARD_PATH", str(legacy))
    r = client.get("/")
    assert r.status_code == 503 and "doğrulanmış" in r.text and str(tmp_path) not in r.text
    assert "const DATA" not in r.text                     # the file itself is never returned


def test_refresh_success_publishes_only_a_gated_dashboard_and_no_unverified_totals(tmp_path):
    data = SE.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    rep = RF.run_refresh(data, dash, inflation_rate=0.40, today=SE.AS_OF)
    assert rep["ok"], rep
    assert all(rep["counts"][k] is None for k in ("products", "low_stock", "lapsed", "overstock_value",
                                                   "overdue_total", "actions"))     # 2B: products withheld too
    html = open(dash, encoding="utf-8").read()
    assert all(m["rows"] == [] for m in _data_blob(html).values())


def test_data_quality_failure_during_refresh_keeps_previous_and_publishes_nothing(tmp_path):
    data = SE.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40, today=SE.AS_OF)["ok"]
    before = open(dash, encoding="utf-8").read()
    # a newer sales export without the amount column: an unsupported layout
    bad = pd.DataFrame(SE.sales_rows()).drop(columns=["Nettutar"])
    p = os.path.join(data, SE.SALES_FILE)
    bad.to_excel(p, index=False)
    os.utime(p, None)
    rep = RF.run_refresh(data, dash, inflation_rate=0.40, today=SE.AS_OF)
    assert rep["ok"] is False and rep["previous_data_kept"] is True
    assert open(dash, encoding="utf-8").read() == before            # the previous (gated) dashboard stays
    assert all(v is None for k, v in rep["counts"].items() if k != "products")


def test_refresh_warning_never_prints_an_unverified_total(tmp_path):
    ctx_old = {"company": {"products_scanned": 5, "working_capital": {"lapsed_count": 100, "low_stock_count": 1,
                                                                      "overstock_value": 1, "overdue_total": 1}},
               "actions": [], "as_of_data_date": "2026-06-30"}
    ctx_new = {"company": {"products_scanned": 5, "working_capital": {"lapsed_count": 1, "low_stock_count": 1,
                                                                      "overstock_value": 1, "overdue_total": 1}},
               "actions": [], "as_of_data_date": "2026-06-30"}
    p = tmp_path / "x.html"
    p.write_text("x", encoding="utf-8")
    ver = RF.verify(ctx_new, {"asof": "2026-06-30", "products": 5, "lapsed": 1, "low": 1, "overdue_total": 1,
                              "overstock_value": 1}, str(p), "2026-06-30", ctx_old)
    w = [x for x in ver["warnings"] if x.startswith("Kayıp müşteri")]
    assert w and "100" not in w[0] and "gösterilmiyor" in w[0]


def test_weekly_email_agent_refuses_to_run(tmp_path):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "weekly_agent.py"), "--force"], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "GMAIL_APP_PASSWORD": "", "PYTHONPATH": ROOT})
    assert r.returncode == 2 and "M8-ACCURACY" in r.stderr and not r.stdout.strip()
