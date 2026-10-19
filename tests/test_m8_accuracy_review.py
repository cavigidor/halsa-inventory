"""M8-ACCURACY correction pass (independent review of PR #27). REAL production gate only (marker `production_gate`).

Covers: customer-directory field redaction, Excel inspection failures that must never look clean, missing API source
evidence, evidence bound to one source generation, operational vs historical time rules, no unintended AI calls, and
no unverified value on any permitted endpoint. Synthetic data only; no network, no key, no paid call.
"""
import dataclasses
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as db
from services import accuracy as ACC
from services import context as CTX
from services import customer360 as C360
from services import fair_store as FS
from services import owner_view as OV
from services import refresh as RF
from services import store as ST
from services.agent import BusinessActionAgent
from services.ai.mock_provider import MockProvider
from services.entity import ContextRepository
from tests.fixtures import fair_synth as FX
from tests.fixtures import owner_synth as OS
from tests.fixtures import synthetic_erp as SE

pytestmark = pytest.mark.production_gate
TODAY = dt.date(2026, 7, 10)
S, NV, INV = ACC.State.VERIFIED, ACC.State.NOT_VERIFIED, ACC.State.INVALID


def full(source="src", **kw):
    rd = kw.pop("report_date", TODAY - dt.timedelta(days=3))
    base = dict(available=True, layout_supported=True, report_date=rd, report_date_origin="declared",
                period_start=dt.date(2025, 1, 1), period_end=rd, period_complete=True, error_cells=0,
                missing_cached_formulas=0, missing_required_values=0, duplicate_records=0, join_unmatched=0,
                join_ambiguous=0, joins_validated=True, totals_consistent=True, mapping_approved=True,
                blank_amount_cells=0, invalid_dates=0, ambiguous_headers=0, external_references=0, date_contradictions=0,
                selection_explicit=True)
    base.update(kw)
    return ACC.SourceEvidence(source, **base)


def certified(key="t.m", temporal="operational", sources=("src",)):
    return ACC.Metric(key, key, ("test",), sources, (), (), (), S, temporal=temporal,
                      max_source_age_days=45 if temporal == "operational" else None, approval_refs=("D-999",),
                      regression_tests=("t",), reconciliation_ref="synthetic")


def decide(metric, ev, generation="g1", expected="g1"):
    reg = {metric.key: metric}
    ACC.validate_register(reg, {})
    snap = ACC.EvidenceSnapshot(generation, {s: ev for s in metric.sources}) if ev is not None else None
    return ACC.Gate(reg, {}, snap, TODAY, expected).decide(metric.key)


# ---------------------------------------------------------------------------------------------
# a running production app with synthetic data, CRM records, a fair campaign and configured owner sources
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("review")
    data = FX.write_all(str(root / "data"))
    ctx = CTX.build_context(data, inflation_rate=0.40)
    owner_dir = Path(root / "owner")
    env = {"KREDI_PATH": str(OS.write_kredi(owner_dir, dt.date.today())),
           "GIDER_GELIR_PATH": str(OS.write_gider_gelir(owner_dir)), "BUTCE_PATH": str(OS.write_butce(owner_dir))}
    return {"data": data, "ctx": ctx, "owner_env": env}


@pytest.fixture
def client(world, tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "s.db"))
    ST.init_db()
    provider = MockProvider()
    calls = {"n": 0}
    real = provider.generate_structured

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(provider, "generate_structured", counting)
    repo = ContextRepository(data_folder=world["data"], context=world["ctx"])
    c360 = C360.Customer360Repository(world["data"], repo, background=False)
    owner = OV.OwnerRepository(OV.config_from_env(world["owner_env"]), background=False)
    with TestClient(APP.app, headers={"X-StockAgent": "1"}) as cl:
        APP.app.state.provider = provider
        APP.app.state.agent = BusinessActionAgent(provider)
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        APP.app.dependency_overrides[APP.get_owner] = lambda: owner
        APP.app.dependency_overrides[APP.get_agent] = lambda: APP.app.state.agent
        cl.ai_calls = calls
        cl.snap = c360.snapshot()
        try:
            yield cl
        finally:
            APP.app.dependency_overrides.clear()


def _seed_operational(cl):
    code = FX.P[1]
    assert cl.post(f"/api/crm/customers/{code}/interactions",
                   json={"occurred_on": FX.AS_OF.isoformat(), "channel": "phone", "purpose": "sales_followup",
                         "outcome": "Sentetik görüşme", "followup": {"purpose": "sales_followup",
                                                                     "due_date": dt.date.today().isoformat()}}
                   ).status_code == 200
    camp = cl.post("/api/fairs/campaigns", json={"name": "Sentetik Kampanya", "edition_year": 2027,
                                                 "start_date": "2027-01-10"}).json()
    assert cl.post(f"/api/fairs/campaigns/{camp['campaign_id']}/customers",
                   json={"customer_codes": [code, FX.P[2]]}).status_code == 200
    return code, camp["campaign_id"]


# ---------------------------------------------------------------------------------------------
# TASK 1 — customer directory: code and recorded name only
# ---------------------------------------------------------------------------------------------
def test_customer_search_keeps_code_and_name_but_withholds_derived_fields(client):
    code = FX.P[1]
    raw, _ = client.snap.search(code, 10)
    assert raw and raw[0]["last_order_date"] is not None             # the field exists and is derived…
    r = client.get("/api/customer360/search", params={"q": code})
    assert r.status_code == 200
    j = r.json()
    hit = j["results"][0]
    assert hit["customer_code"] == code and hit["display_name"] == raw[0]["display_name"]
    assert hit["last_order_date"] is None and hit["closed"] is None        # …and is withheld by the backend
    assert {w["metric"] for w in j["withheld"]} == {"customer.last_order_date", "customer.master_status"}
    assert all(w["message"] == ACC.WITHHELD_MESSAGE for w in j["withheld"])
    assert raw[0]["last_order_date"] not in r.text


def test_name_search_for_a_kapali_master_shows_no_status(client):
    closed = [c for c in client.snap.customers if client.snap.master_closed(c) is True]
    assert closed, "fixture must contain a trusted-master KAPALI customer"
    j = client.get("/api/customer360/search", params={"q": closed[0]}).json()
    assert j["results"] and all(h["closed"] is None for h in j["results"])


def test_crm_and_fair_rows_withhold_directory_attributes_but_keep_records(client):
    code, cid = _seed_operational(client)
    fu = client.get("/api/crm/followups?mode=all").json()["items"]
    assert fu and all(i["closed"] is None and i["master_status"] == "withheld" for i in fu)
    assert all(not str(w.get("code", "")).startswith("master_") for i in fu for w in i["warnings"])
    cust = client.get(f"/api/crm/customers/{code}").json()
    assert cust["closed"] is None and cust["master_status"] == "withheld" and cust["structured_interactions"] == 1
    mem = client.get(f"/api/fairs/campaigns/{cid}/customers").json()["items"]
    assert {m["customer_code"] for m in mem} == {code, FX.P[2]}
    assert all(m["last_order_date"] is None and m["closed"] is None and m["master_status"] == "withheld"
               for m in mem)


def test_kapali_safeguard_still_restricts_actions_server_side(client):
    """The status is not displayed, but the conservative rule it drives (no sales follow-up for a trusted-KAPALI
    customer) stays enforced; it restricts an action and shows no analysis."""
    closed = [c for c in client.snap.customers if client.snap.master_closed(c) is True][0]
    r = client.put(f"/api/crm/customers/{closed}/followup",
                   json={"purpose": "sales_followup", "due_date": dt.date.today().isoformat()})
    assert r.status_code in (409, 422)
    assert r.json()["detail"].get("error_code") != ACC.WITHHELD_CODE


# ---------------------------------------------------------------------------------------------
# TASK 2 — Excel inspection never fails open
# ---------------------------------------------------------------------------------------------
def test_numeric_columns_are_inspected_not_skipped():
    assert ACC._excel_errors(pd.Series([1.0, 2.0, None])) == 0          # previously raised and was reported as 0
    assert ACC._excel_errors(pd.Series(["#REF!", " #DIV/0! ", "ok", 3])) == 2


def test_inspection_error_is_a_failure_not_zero(monkeypatch):
    assert ACC._excel_errors(pd.DataFrame({"a": [1], "b": ["#REF!"]})) is None    # unreadable shape → None, not 0
    monkeypatch.setattr(ACC, "_excel_errors", lambda s: None)
    ev = ACC.collect_core_evidence(stock_df=SE.stock_frame())["stock"]
    assert ev.error_cells is None and "excel_errors" in ev.inspection_failures
    assert decide(certified(), dataclasses.replace(full(), error_cells=None,
                                                   inspection_failures=ev.inspection_failures)).state == INV


def test_collector_crash_fails_closed(monkeypatch):
    def boom(df, declared):
        raise ValueError("synthetic")
    monkeypatch.setitem(ACC._COLLECTORS, "sales", boom)
    ev = ACC.collect_core_evidence(sales_df=pd.DataFrame(SE.sales_rows()))["sales"]
    assert ev.inspection_failures == ("collector:ValueError",)
    assert ev.available is None and ev.error_cells is None and ev.missing_required_values is None
    assert any(st == INV for st, _ in ev.problems())


def test_duplicate_header_columns_cannot_be_inspected_and_fail_closed():
    df = SE.stock_frame()
    df = pd.concat([df, df[["STOK Kontrol %"]]], axis=1)                      # two columns with the same header
    ev = ACC.collect_core_evidence(stock_df=df)["stock"]
    assert ev.inspection_failures and any(st == INV for st, _ in ev.problems())


def test_collectors_never_assert_what_they_did_not_check():
    evs = ACC.collect_core_evidence(SE.stock_frame(), pd.DataFrame(SE.sales_rows()), SE.aging_frame(), today=TODAY)
    for ev in evs.values():
        assert ev.joins_validated is None and ev.totals_consistent is None
        assert ev.period_complete is None and ev.mapping_approved is None
        assert ev.missing_cached_formulas is None                        # pandas cannot tell; never claimed as 0
        assert ev.selection_explicit is None and ev.external_references is None
    assert evs["aging"].missing_required_values is None
    assert all(ev.report_date is None for ev in evs.values())         # no declared date → unknown


def test_default_evidence_is_never_clean():
    assert all(st != S for st, _ in ACC.SourceEvidence("x").problems())
    assert len(ACC.SourceEvidence("x").problems()) >= 8
    assert decide(certified(), ACC.SourceEvidence("src")).state == NV


# ---------------------------------------------------------------------------------------------
# TASK 3 — evidence bound to one source generation; API has none yet
# ---------------------------------------------------------------------------------------------
def test_api_gate_has_no_source_evidence():
    g = APP.accuracy_gate()
    assert g.snapshot is None and g.expected_generation is None
    for k, m in ACC.REGISTER.items():
        d = g.decide(k)
        assert not d.displayable
        for src in m.sources:
            assert f"NO-EVIDENCE:{src}" in d.codes, (k, src)


def test_a_promoted_metric_would_still_be_withheld_by_the_api(monkeypatch):
    reg = dict(ACC.REGISTER)
    reg["stock.reorder"] = dataclasses.replace(reg["stock.reorder"], blockers=(), definitions=(), certified=S,
                                               approval_refs=("D-999",), regression_tests=("t",),
                                               reconciliation_ref="synthetic")
    monkeypatch.setattr(ACC, "REGISTER", reg)
    monkeypatch.setattr(ACC, "current_gate", lambda today=None, snapshot=None, expected_generation=None:
                        ACC.Gate(reg, ACC.DEFINITIONS, snapshot, today, expected_generation))
    d = APP.accuracy_gate().decide("stock.reorder")
    assert d.state == NV and "NO-EVIDENCE:stock" in d.codes


def test_production_register_refuses_promotion_until_generation_bound_api_evidence():
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    reg = dict(ACC.REGISTER)
    reg["stock.reorder"] = dataclasses.replace(reg["stock.reorder"], blockers=(),
                                               definitions=("operational_freshness",), certified=S,
                                               approval_refs=("D-999",), regression_tests=("t",),
                                               reconciliation_ref="synthetic")
    defs = dict(ACC.DEFINITIONS, operational_freshness=ACC.Definition("operational_freshness", "x", True, "D-999"))
    with pytest.raises(ACC.RegisterError, match="generation-bound"):
        ACC.validate_register(reg, defs, production=True)
    reg["stock.reorder"] = dataclasses.replace(reg["stock.reorder"], definitions=())
    with pytest.raises(ACC.RegisterError, match="freshness"):          # the 45-day rule is not an approved policy
        ACC.validate_register(reg, defs, production=True)


def test_evidence_from_another_generation_never_certifies():
    m = certified()
    assert decide(m, full(), "g1", "g1").state == S
    d = decide(m, full(), "g-old", "g-new")
    assert d.state == NV and "EVIDENCE-GENERATION:src" in d.codes
    assert decide(m, full(), None, None).state == NV                     # an uncomputable generation certifies nothing


def test_bound_to_drops_evidence_of_an_older_generation():
    reg = {"t.m": certified()}
    old = ACC.EvidenceSnapshot("g-old", {"src": full()})
    new = ACC.EvidenceSnapshot("g-new", {"src": ACC.SourceEvidence("src")})
    g = ACC.Gate(reg, {}, old, TODAY, "g-old")
    assert g.decide("t.m").state == S
    g2 = g.bound_to(new)
    assert g2.expected_generation == "g-new" and g2.snapshot.evidence["src"] == ACC.SourceEvidence("src")
    assert g2.decide("t.m").state == NV
    with pytest.raises(TypeError):
        g2.snapshot.evidence["src"] = full()                             # immutable snapshot


def test_refresh_with_changed_sources_publishes_a_new_generation(tmp_path):
    data = SE.write_all(str(tmp_path / "data"))
    dash = str(tmp_path / "dashboard.html")
    assert RF.run_refresh(data, dash, inflation_rate=0.40, today=SE.AS_OF)["ok"]
    g1 = _generation(dash)
    df = SE.stock_frame()
    df.loc[0, "TOPLAM STOK MİKTAR "] = 7
    with pd.ExcelWriter(str(Path(data) / SE.STOCK_FILE)) as w:
        pd.DataFrame([["Sentetik stok raporu"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        df.to_excel(w, index=False, startrow=3)
    assert RF.run_refresh(data, dash, inflation_rate=0.40, today=SE.AS_OF)["ok"]
    g2 = _generation(dash)
    assert g1 and g2 and g1 != g2


def _generation(path):
    html = open(path, encoding="utf-8").read()
    marker = '<meta name="stockagent-generation" content="'
    return html.split(marker, 1)[1].split('"', 1)[0]


# ---------------------------------------------------------------------------------------------
# TASK 4 — historical validity vs operational freshness
# ---------------------------------------------------------------------------------------------
def test_old_but_complete_historical_period_is_not_withheld_for_age():
    old = TODAY - dt.timedelta(days=900)
    assert decide(certified(temporal="historical"), full(report_date=old)).state == S
    d = decide(certified(temporal="operational"), full(report_date=old))
    assert d.state == NV and any("güncel değil" in r for r in d.reasons_tr)


@pytest.mark.parametrize("ev,expected", [
    (dict(period_complete=False), NV),                               # incomplete reporting period
    (dict(period_complete=None), NV),
    (dict(period_end=None), NV),                                      # historical period must be dated
    (dict(report_date=None), NV),                                     # unknown source date
    (dict(period_end=TODAY + dt.timedelta(days=30)), INV),            # period ends after the report: impossible
])
def test_historical_metric_needs_a_dated_complete_period(ev, expected):
    assert decide(certified(temporal="historical"), full(**ev)).state == expected


def test_a_current_date_never_approves_anything():
    fresh = TODAY
    assert decide(certified(), full(report_date=fresh, error_cells=2)).state == INV
    assert decide(certified(), full(report_date=fresh, joins_validated=None)).state == NV
    m = dataclasses.replace(certified(), definitions=("open",))
    reg = {m.key: m}
    d = ACC.Gate(reg, {"open": ACC.Definition("open", "Açık tanım")},
                 ACC.EvidenceSnapshot("g", {"src": full(report_date=fresh)}), TODAY, "g").decide(m.key)
    assert d.state == NV


def test_register_temporal_rules():
    with pytest.raises(ACC.RegisterError):
        ACC.validate_register({"x": dataclasses.replace(certified("x"), max_source_age_days=None)}, {})
    with pytest.raises(ACC.RegisterError):
        ACC.validate_register({"x": dataclasses.replace(certified("x", "historical"), max_source_age_days=45)}, {})
    kinds = {k: m.temporal for k, m in ACC.REGISTER.items()}
    assert kinds["stock.reorder"] == "operational" and kinds["receivables.collections"] == "operational"
    assert kinds["owner.gider_gelir"] == "historical" and kinds["fairs.history"] == "historical"


# ---------------------------------------------------------------------------------------------
# TASK 5 — permitted endpoints expose no unverified value and never call AI
# ---------------------------------------------------------------------------------------------
FORBIDDEN_NONNULL = {"last_order_date", "closed", "net_order_amount", "overdue", "spend", "revenue", "amount",
                     "score", "queue_score", "erp_fuar_orders", "erp_evidence", "erp_mismatch", "collection_first",
                     "active_customers_with_structured_contact", "daily_sales_customers",
                     "collection_first_customers", "cross_sell_customers", "cross_sell_active_customers",
                     "cross_sell_share", "fair_tasks_due", "crm_followups_in_queue", "orders", "customers"}


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + "." + k, k, v
            yield from _walk(v, path + "." + k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def _permitted_gets(code, cid):
    out = []
    for (method, path), (kind, _) in ACC.ROUTES.items():
        if method != "GET" or kind == "metric" or path in ("/", "/docs", "/redoc", "/openapi.json",
                                                          "/docs/oauth2-redirect"):
            continue
        out.append(path.replace("{customer_code}", code).replace("{campaign_id}", str(cid)))
    return out


def test_permitted_endpoints_expose_no_unverified_values_and_no_ai(client):
    code, cid = _seed_operational(client)
    paths = _permitted_gets(code, cid) + ["/api/customer360/search?q=" + FX.P[1][:7],
                                          "/api/crm/followups?mode=all"]
    seen = {}
    for p in paths:
        r = client.get(p)
        assert r.status_code == 200, (p, r.status_code, r.text[:200])
        seen[p] = r.json()
    assert len(seen) >= 12
    for p, body in seen.items():
        text = json.dumps(body, ensure_ascii=False)
        assert "₺" not in text, p
        for where, key, value in _walk(body):
            if key in FORBIDDEN_NONNULL and ".linked_event." not in where and "stage_counts" not in where:
                assert value in (None, [], 0) or key == "customers" and isinstance(value, int) and ".stores" in where \
                    or p.startswith("/api/customer360/status"), (p, where, value)
            if key == "segments":
                assert value == [], (p, where)
            if key == "data" and ".owner_sources" not in where:
                assert value is None, (p, where)
    ov = seen["/api/owner/overview"]
    assert all(ov[k]["withheld"] for k in ("kredi", "gider_gelir", "butce"))
    assert ov["kredi"]["data"] is None and ov["kredi"]["state"] not in ("not_configured",)   # configured, withheld
    assert client.ai_calls["n"] == 0
    assert client.get("/api/ai/calls").json()["calls"] == []


def test_every_ai_route_is_refused_before_the_provider(client):
    code, _ = _seed_operational(client)
    for path, body in (("/api/agent/actions/enrich", {"action_ids": ["a"]}),
                       (f"/api/agent/customer/{code}/analyze", None), ("/api/agent/product/SP-1/analyze", None),
                       ("/api/agent/draft-message", {"customer_code": code, "channel": "whatsapp",
                                                     "purpose": "sales_followup"})):
        r = client.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"]["error_code"] == ACC.WITHHELD_CODE
    assert client.ai_calls["n"] == 0
