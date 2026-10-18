"""M7-4 Fuar Merkezi — historical FUAR attribution, amount semantics, campaigns, members, candidate segments, the
Günlük Satış fair task, action-state vs funnel-state separation, API, refresh, privacy.

Synthetic data only (tests/fixtures/fair_synth.py, c360_synth.py). No real data, no key, no network, no AI call.
Numbers in test names follow the M7-4 instruction (§86–§93).
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import logging
import os
import sqlite3

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import config as C
import dashboard_builder as DB
from services import actions as A
from services import context as CTX
from services import customer360 as C360
from services import fair_center as FC
from services import fair_store as FS
from services import fairs as FA
from services import sales_queue as SQ
from services import store as ST
from services.entity import ContextRepository
from services.importers.sales_activity import import_order_sources, parse_label
from tests.fixtures import c360_synth as CS
from tests.fixtures import fair_synth as FX

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = FX.P
H = {"X-StockAgent": "1"}
TODAY = dt.date.today()


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return FX.write_all(str(tmp_path_factory.mktemp("fair_data")))


@pytest.fixture(scope="module")
def ctx(data_dir):
    return CTX.build_context(data_dir, inflation_rate=0.40)


@pytest.fixture(scope="module")
def snap(data_dir, ctx):
    return C360.build_snapshot(data_dir, ctx)


@pytest.fixture(scope="module")
def hist(ctx):
    return ctx.fairs


def ed(hist, label):
    return next(e for e in hist.editions.values() if e.label == label)


@pytest.fixture()
def client(data_dir, ctx):
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    c360 = C360.Customer360Repository(data_dir, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        yield cl, repo, c360
    APP.app.dependency_overrides.clear()


def new_campaign(cl, **kw):
    body = {"name": "Sentetik Ocak Fuarı", "edition_year": 2027, "start_date": "2027-01-15", **kw}
    r = cl.post("/api/fairs/campaigns", json=body, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def add(cl, cid, *codes, stage=None):
    body = {"customer_codes": list(codes)}
    if stage:
        body["stage"] = stage
    r = cl.post(f"/api/fairs/campaigns/{cid}/customers", json=body, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def upd(cl, cid, code, **kw):
    return cl.put(f"/api/fairs/campaigns/{cid}/customers", json={"customer_code": code, **kw}, headers=H)


def queue(ctx, snap, **kw):
    kw.setdefault("upsert", False)
    kw.setdefault("limit", 50)
    return SQ.build_queue(ctx, snap, **kw)


def card(q, code):
    return next((k for k in q["items"] if k["customer_code"] == code), None)


def _names(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


# =============================================================================================
# 86. historical attribution (1–12)
# =============================================================================================
def test_01_fuar_parsed_as_historical_fair(hist):
    assert hist.status == "ok"
    assert {e.label for e in hist.editions.values()} == {FX.E25, FX.E26, FX.E26B, FX.EX}
    assert hist.diagnostics["editions"] == 4 and hist.diagnostics["edition_year_min"] == 2025


def test_02_03_04_etkinlik_saha_genel_excluded(hist, data_dir):
    labels = {e.label for e in hist.editions.values()}
    assert FX.ETK not in labels and FX.SAHA not in labels and "GENEL" not in labels
    assert hist.customer_editions[P[2]] == {ed(hist, FX.E25).event_id: 1}          # its ETKİNLİK/SAHA orders: no
    assert hist.diagnostics["orders_by_type"]["etkinlik"] == 1 and hist.diagnostics["orders_by_type"]["saha"] == 1
    assert P[13] not in hist.customer_editions and P[17] not in hist.customer_editions   # GENEL-only customers
    src = open(os.path.join(ROOT, "services", "fairs.py"), encoding="utf-8").read()
    assert "is_fair" not in src.replace('no `is_fair`', '')


def test_05_edition_identity_stable(hist, data_dir):
    e = ed(hist, FX.E25)
    assert e.event_id == parse_label(FX.E25).event_id == FA.fuar_event_id(2025, "SENTFUARA", None)
    again = FA.build_fair_history(DB.load_sales(os.path.join(data_dir, FX.SALES_FILE)), FX.AS_OF)
    assert list(again.editions) == list(hist.editions)


def test_06_sequence_differentiates(hist):
    a, b = ed(hist, FX.E26), ed(hist, FX.E26B)
    assert a.stem == b.stem == "SENTFUARA" and a.event_id != b.event_id and (a.sequence, b.sequence) == (None, 2)


def test_07_08_order_grain_and_line_sums(hist):
    e = ed(hist, FX.E25)
    assert (e.orders, e.lines) == (4, 5)
    p1 = next(r for r in e.customer_rows if r["customer_code"] == P[1])
    assert (p1["orders"], p1["lines"], p1["net_order_amount"], p1["quantity"]) == (1, 2, 10_000.0, 15.0)
    assert e.net_order_amount == 13_700.0 and e.quantity == 45.0


def test_09_canonical_customer_d003(hist, data_dir):
    sdf = DB.load_sales(os.path.join(data_dir, FX.SALES_FILE))
    assert set(hist.customer_editions) <= set(sdf["_cust"].astype(str))
    # identity fallback (empty Pro_kodu → Musterikod) and untrimmed codes come from load_sales (D-003)
    cs = CS.write_all(os.path.join(data_dir, "..", "cs"))
    h2 = FA.build_fair_history(DB.load_sales(os.path.join(cs, CS.SALES_FILE)))
    assert set(h2.customer_editions) == {"910.00.0001", "910.00.0008"}


def test_10_customer_conflict_not_assigned(hist):
    e = ed(hist, FX.E26)
    assert e.customer_conflict_orders == 1 and e.orders == 4 and e.net_order_amount == 5_000.0   # still in totals
    codes = {r["customer_code"] for r in e.customer_rows}
    assert P[5] not in codes and P[6] not in codes
    assert P[5] not in hist.customer_editions and P[6] not in hist.customer_editions
    assert hist.diagnostics["fuar_customer_conflict_orders"] == 1


def test_date_conflict_and_missing_amount(hist):
    e = ed(hist, FX.E26)
    assert e.date_unusable_orders == 1 and (e.first_order_date, e.last_order_date) == ("2026-01-18", "2026-01-21")
    p7 = next(r for r in e.customer_rows if r["customer_code"] == P[7])
    assert p7["orders"] == 1 and p7["first_order_date"] is None                # no invented date
    p8 = next(r for r in e.customer_rows if r["customer_code"] == P[8])
    assert p8["net_order_amount"] is None and e.missing_amount_lines == 1      # never filled with 0


def test_11_event_labels_separate(hist):
    x = ed(hist, FX.EX)
    assert x.stem != "SENTFUARA" and x.orders == 1 and x.edition_year == 2026


def test_12_no_follow_on_genel_attribution(hist):
    assert set(hist.customer_editions[P[1]]) == {ed(hist, FX.E25).event_id, ed(hist, FX.E26).event_id}
    assert ed(hist, FX.E25).net_order_amount == 13_700.0                        # P1's 9,999 GENEL order excluded


def test_label_conflict_order_excluded_and_m6_2_counts_match(tmp_path):
    d = FX.write_all(str(tmp_path / "lc"), label_conflict=True)
    h = FA.build_fair_history(DB.load_sales(os.path.join(d, FX.SALES_FILE)), FX.AS_OF)
    assert h.diagnostics["label_conflict_orders"] == 1 and P[9] not in h.customer_editions
    assert import_order_sources(os.path.join(d, FX.SALES_FILE)).ok is False   # M6-2 treats it as source-fatal
    cs = CS.write_all(str(tmp_path / "cs"))                                   # a file M6-2 accepts
    m62 = import_order_sources(os.path.join(cs, CS.SALES_FILE))
    h2 = FA.build_fair_history(DB.load_sales(os.path.join(cs, CS.SALES_FILE)))
    assert m62.ok and h2.diagnostics["orders_by_type"] == m62.counts.orders_by_type
    fuar_m62 = {o.event_id for o in m62.orders if o.is_fuar}
    assert set(h2.editions) == fuar_m62


# =============================================================================================
# 87. amount semantics (13–17)
# =============================================================================================
def test_13_to_17_amount_semantics(client, hist):
    cl, _, _ = client
    lst = cl.get("/api/fairs/history").json()
    det = cl.get(f"/api/fairs/history/{ed(hist, FX.E25).event_id}").json()
    assert lst["amount_label"] == det["amount_label"] == "Net Sipariş Tutarı (Nettutar)"          # 13
    assert "teslim edilen" in lst["amount_note"] and "Ciro esası kararı açık" in lst["amount_note"]
    keys = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k.lower())
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(lst)
    walk(det)
    for bad in ("ciro", "revenue", "delivered", "invoiced", "margin", "profit", "roi", "conversion"):
        assert not any(bad in k for k in keys), bad                                                  # 14–17
    txt = json.dumps(det, ensure_ascii=False)
    assert "Fuar Geliri" not in txt and "ROI" in " ".join(det["notes"])      # only the explicit "no ROI" note
    names = _names(os.path.join(ROOT, "services", "fairs.py"))
    assert not ({"_prof", "margin", "Net_Tutar_Maliyet_Dusulmus"} & names)
    src = open(os.path.join(ROOT, "services", "fairs.py"), encoding="utf-8").read()
    assert "gider" not in src.lower().replace("gider/gelir", "") and "butce" not in src.lower()


# =============================================================================================
# 88. campaigns (18–25)
# =============================================================================================
def test_18_22_create_and_optional_stem(client, hist):
    cl, _, _ = client
    c = new_campaign(cl)
    assert c["status"] == "planned" and c["total_customers"] == 0 and c["linked_event"] == {
        "configured": False, "found": False, "event_id": None, "label": None, "orders": None, "customers": None,
        "net_order_amount": None, "first_order_date": None, "last_order_date": None}
    linked = new_campaign(cl, name="Bağlı", edition_year=2026, start_date="2026-01-17", event_stem="SENTFUARA")
    le = linked["linked_event"]
    assert le["configured"] and le["found"] and le["orders"] == 4 and le["event_id"] == ed(hist, FX.E26).event_id
    near = new_campaign(cl, name="SENTFUARA", edition_year=2026, start_date="2026-01-17", event_stem="SENTFUAR")
    assert near["linked_event"]["found"] is False                         # no fuzzy / name-similarity match
    seq2 = new_campaign(cl, name="İkinci", edition_year=2026, start_date="2026-01-24", event_stem="SENTFUARA",
                        event_sequence=2)
    assert seq2["linked_event"]["event_id"] == ed(hist, FX.E26B).event_id


@pytest.mark.parametrize("body", [
    {"name": "   ", "edition_year": 2027, "start_date": "2027-01-15"},                              # 19
    {"name": "x" * 121, "edition_year": 2027, "start_date": "2027-01-15"},
    {"name": "A", "edition_year": 2027, "start_date": "2027-13-01"},                               # 20
    {"name": "A", "edition_year": 1999, "start_date": "2027-01-15"},
    {"name": "A", "edition_year": 2027, "start_date": "2027-01-15", "end_date": "2027-01-10"},      # 21
    {"name": "A", "edition_year": 2027, "start_date": "2027-01-15", "event_sequence": 0},
    {"name": "A", "edition_year": 2027, "start_date": "2027-01-15", "status": "archived"},
    {"name": "A", "edition_year": 2027, "start_date": "2027-01-15", "sql": "x"},
])
def test_19_20_21_validation(client, body):
    cl, _, _ = client
    assert cl.post("/api/fairs/campaigns", json=body, headers=H).status_code == 422
    assert cl.get("/api/fairs/campaigns").json()["items"] == []


def test_23_24_multiple_and_archive(client):
    cl, _, _ = client
    a = new_campaign(cl, name="A", start_date="2027-02-01")
    b = new_campaign(cl, name="B", start_date="2027-01-10", status="active")
    ids = [c["campaign_id"] for c in cl.get("/api/fairs/campaigns").json()["items"]]
    assert ids == [b["campaign_id"], a["campaign_id"]]                    # open campaigns by start date
    r = cl.post(f"/api/fairs/campaigns/{a['campaign_id']}/archive", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "archived"
    assert cl.get("/api/fairs/campaigns").json()["items"][-1]["campaign_id"] == a["campaign_id"]
    assert cl.put(f"/api/fairs/campaigns/{a['campaign_id']}", json={"name": "Z"}, headers=H).status_code == 409
    assert cl.post(f"/api/fairs/campaigns/{a['campaign_id']}/customers", json={"customer_codes": [P[1]]},
                   headers=H).status_code == 409
    u = cl.put(f"/api/fairs/campaigns/{b['campaign_id']}", json={"name": "B2", "end_date": "2027-01-12"}, headers=H)
    assert u.status_code == 200 and u.json()["name"] == "B2" and u.json()["end_date"] == "2027-01-12"
    bad = cl.put(f"/api/fairs/campaigns/{b['campaign_id']}", json={"end_date": "2027-01-01"}, headers=H)
    assert bad.status_code == 422
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM fair_campaigns").fetchone()[0] == 2    # archive, never delete


def test_25_state_survives_reopen_and_migration(tmp_path, monkeypatch):
    db = str(tmp_path / "old.db")
    with sqlite3.connect(db) as c:      # an M5-era database: actions only
        c.execute("""CREATE TABLE actions(action_key TEXT PRIMARY KEY, generated_at TEXT, category TEXT,
                     entity_type TEXT, entity_id TEXT, priority TEXT, score REAL, title TEXT, content_hash TEXT,
                     status TEXT DEFAULT 'open', completed_at TEXT, notes TEXT)""")
        c.execute("INSERT INTO actions(action_key,category,status) VALUES('k1','winback','completed')")
    monkeypatch.setattr(ST, "DB_PATH", db)
    ST.init_db()
    cid = FS.create_campaign(name="Kalıcı", edition_year=2027, start_date="2027-01-15")["campaign_id"]
    FS.add_member(cid, P[1])
    ST.init_db()                                                        # a restart: CREATE IF NOT EXISTS only
    assert FS.get_campaign(cid)["name"] == "Kalıcı" and FS.get_member(cid, P[1])["stage"] == "invite_candidate"
    assert ST.get_statuses()["k1"]["status"] == "completed"           # M5 state untouched
    with sqlite3.connect(db) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"actions", "action_history", "memory", "ai_cache", "ai_calls", "fair_campaigns", "fair_customers",
            "fair_history"} <= tables


# =============================================================================================
# 89. campaign customers (26–35)
# =============================================================================================
def test_26_to_30_add_rules(client):
    cl, _, _ = client
    cid = new_campaign(cl)["campaign_id"]
    r = add(cl, cid, P[1], P[1], "999.99.9999", P[10], P[11])
    assert r["added"] == [P[1], P[11]] and r["invalid"] == ["999.99.9999"] and r["blocked_kapali"] == [P[10]]
    assert r["counts"] == {"added": 2, "already_present": 0, "blocked_kapali": 1, "invalid": 1}
    assert r["warnings"][0]["customer_code"] == P[11] and r["warnings"][0]["code"] == "master_duplicate"   # 30
    again = add(cl, cid, P[1])
    assert again["already_present"] == [P[1]] and again["counts"]["added"] == 0                          # 27
    assert sum(1 for h in FS.history(cid, P[1]) if h["event"] == "added") == 1
    rows = cl.get(f"/api/fairs/campaigns/{cid}/customers").json()["items"]
    p11 = next(x for x in rows if x["customer_code"] == P[11])
    assert p11["closed"] is None and p11["master_status"] == "duplicate"                                 # 30
    assert add(cl, cid, P[2], stage="called")["added"] == [P[2]]
    assert cl.post(f"/api/fairs/campaigns/{cid}/customers", json={"customer_codes": [P[3]], "stage": "removed"},
                   headers=H).status_code == 422
    big = {"customer_codes": [f"x{i}" for i in range(C.MAX_FAIR_BULK_ADD + 1)]}
    assert cl.post(f"/api/fairs/campaigns/{cid}/customers", json=big, headers=H).status_code == 422


def test_31_to_35_updates_terminal_history(client):
    cl, _, _ = client
    cid = new_campaign(cl)["campaign_id"]
    add(cl, cid, P[17])
    r = upd(cl, cid, P[17], stage="called", next_action_date="2026-11-02", note="Sentetik not")
    assert r.status_code == 200 and (r.json()["stage"], r.json()["next_action_date"], r.json()["note"]) == \
        ("called", "2026-11-02", "Sentetik not")
    assert upd(cl, cid, P[17], note="x" * 501).status_code == 422                                        # 33
    assert upd(cl, cid, P[17], note="x" * 500).status_code == 200
    assert upd(cl, cid, P[17], stage="nonsense").status_code == 422
    assert upd(cl, cid, P[17], next_action_date="2026-02-30").status_code == 422
    r = upd(cl, cid, P[17], clear_next_action=True, clear_note=True)
    assert r.json()["next_action_date"] is None and r.json()["note"] is None
    for s in ("declined", "removed", "follow_up"):                                                        # 34
        assert upd(cl, cid, P[17], stage=s).status_code == 200
    ev = [h["event"] for h in reversed(FS.history(cid, P[17]))]
    assert ev == ["added", "stage_changed", "next_action_changed", "note_updated", "note_updated",
                  "next_action_changed", "note_updated", "stage_changed", "removed", "restored"]           # 35
    hist_txt = json.dumps(FS.history(cid), ensure_ascii=False)
    assert "Sentetik not" not in hist_txt and "xxxxx" not in hist_txt                     # no note text stored


def test_kapali_blocks_active_stage(client, monkeypatch):
    cl, _, c360 = client
    cid = new_campaign(cl)["campaign_id"]
    add(cl, cid, P[18])
    snap = c360.snapshot()
    real = snap.master_closed
    monkeypatch.setattr(snap, "master_closed", lambda c: True if c == P[18] else real(c))
    r = upd(cl, cid, P[18], stage="accepted")
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "customer_kapali"
    assert upd(cl, cid, P[18], stage="declined").status_code == 200                       # terminal allowed
    assert upd(cl, cid, P[18], stage="called").status_code == 409


# =============================================================================================
# 90. candidate segments (36–45)
# =============================================================================================
def seg(ctx, snap, s, **kw):
    FC._SEG_CACHE.clear()
    return FC.candidates(ctx, snap, segment=s, limit=200, **kw)


def codes(res):
    return {x["customer_code"] for x in res["items"]}


def test_36_historical_fuar_segment(ctx, snap):
    r = seg(ctx, snap, "historical_fuar")
    assert codes(r) == {P[1], P[2], P[3], P[4], P[7], P[8], P[12]} and r["kapali_excluded"] == 1   # P10 KAPALI
    assert r["segment_counts"]["historical_fuar"] == 8


def test_37_to_41_other_segments(ctx, snap, monkeypatch):
    act = seg(ctx, snap, "active")
    window_from = (snap.as_of - dt.timedelta(days=364)).isoformat()
    assert codes(act) == {c for c, b in snap.customers.items() if b["last_order_date"] >= window_from
                          and snap.master_closed(c) is not True}
    assert P[16] not in codes(act) and P[2] not in codes(act)                               # 2023 / 2025-06
    assert codes(seg(ctx, snap, "winback")) == {P[13]}
    assert codes(seg(ctx, snap, "real_decline")) == {P[14]}
    col = seg(ctx, snap, "collections")
    assert codes(col) == {P[15]} and col["items"][0]["collection_first"] is True
    monkeypatch.setattr(ctx, "reason_index", {**ctx.reason_index, "cross_sell": {P[17]: 60.0}})
    assert codes(seg(ctx, snap, "cross_sell")) == {P[17]}
    FC._SEG_CACHE.clear()


def test_42_several_segments_and_sort(ctx, snap):
    r = seg(ctx, snap, None)
    p1 = next(x for x in r["items"] if x["customer_code"] == P[1])
    assert set(p1["segments"]) == {"historical_fuar", "active"}
    keys = [(-(x["queue_score"] if x["queue_score"] is not None else -1.0)) for x in r["items"]]
    assert keys == sorted(keys)                                                     # queue score first
    ff = seg(ctx, snap, None, fuar_first=True)
    flags = [x["historical_fuar"] for x in ff["items"]]
    assert flags == sorted(flags, reverse=True)                                     # FUAR customers first


def test_43_segments_never_auto_add(client):
    cl, _, _ = client
    cid = new_campaign(cl)["campaign_id"]
    for s in FC.SEGMENT_KEYS:
        r = cl.get("/api/fairs/candidates", params={"segment": s, "campaign_id": cid})
        assert r.status_code == 200
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM fair_customers").fetchone()[0] == 0
    assert "otomatik" in r.json()["note"]


def test_44_45_no_potential_or_salesperson_segments(ctx, snap):
    assert FC.SEGMENT_KEYS == ("historical_fuar", "active", "winback", "real_decline", "cross_sell", "collections")
    r = seg(ctx, snap, None)
    keys = {k for x in r["items"] for k in x}
    assert not any(k for k in keys if "rep" in k or "potential" in k or "penetr" in k or "temsilci" in k)
    names = _names(os.path.join(ROOT, "services", "fair_center.py"))
    assert not ({"hedef", "historical_weekly_stamp_capacity", "salespeople", "rep", "hedef_rep"} & names)


# =============================================================================================
# 91. fair daily task (46–60)
# =============================================================================================
def make_member(code, next_date, stage="called", status="planned", name="Sentetik Fuar Kampanyası"):
    cid = FS.create_campaign(name=name, edition_year=2027, start_date="2027-01-15", status=status
                             if status != "archived" else "planned")["campaign_id"]
    FS.add_member(cid, code)
    FS.update_member(cid, code, stage=stage, next_action_date=next_date)
    if status == "archived":
        FS.archive_campaign(cid)
    return cid


def fair_reasons(q, code):
    k = card(q, code)
    return [r for r in (k["reasons"] if k else []) if r["category"] == "fair_followup"]


def d(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


def test_46_to_51_eligibility(ctx, snap):
    make_member(P[17], d(3))                                         # 46 future
    make_member(P[18], d(0))                                         # 47 today
    make_member(P[19], d(-5))                                        # 48 overdue
    cid = FS.create_campaign(name="Tarihsiz", edition_year=2027, start_date="2027-01-15")["campaign_id"]
    FS.add_member(cid, P[20])                                        # 49 no date
    make_member(P[1], d(-1), status="archived")                      # 50 archived campaign
    make_member(P[3], d(-1), stage="declined")                       # 51 terminal
    make_member(P[4], d(-1), stage="removed")
    q = queue(ctx, snap, flt="fair_followup")
    assert {k["customer_code"] for k in q["items"]} == {P[18], P[19]}
    assert q["filter_counts"]["fair_followup"] == 2


def test_52_53_stable_and_new_ids(ctx, snap):
    cid = make_member(P[17], d(-1))
    a = fair_reasons(queue(ctx, snap), P[17])[0]
    assert fair_reasons(queue(ctx, snap), P[17])[0]["action_id"] == a["action_id"]
    assert a["action_id"].startswith(f"fair_followup:{P[17]}:")
    ST.upsert_actions([{**a, "entity_type": "customer", "entity_id": P[17], "title": "t"}])
    ST.set_status(a["action_id"], "completed")
    assert fair_reasons(SQ.build_queue(ctx, snap, upsert=False, limit=50), P[17]) == []   # stays hidden
    FS.update_member(cid, P[17], next_action_date=d(0))
    b = fair_reasons(SQ.build_queue(ctx, snap, upsert=False, limit=50), P[17])[0]
    assert b["action_id"] != a["action_id"] and ST.get_statuses()[a["action_id"]]["status"] == "completed"   # 48
    FS.update_member(cid, P[17], stage="accepted")
    c = fair_reasons(queue(ctx, snap), P[17])[0]
    assert c["action_id"] not in (a["action_id"], b["action_id"])


def test_54_fixed_score(ctx, snap):
    make_member(P[17], d(-30), stage="offer")
    make_member(P[18], d(0), stage="invite_candidate")
    q = queue(ctx, snap)
    for code in (P[17], P[18]):
        r = fair_reasons(q, code)[0]
        assert r["score"] == C.FAIR_TASK_SCORE == 55 and r["confidence"] == "high" and r["priority"] == "medium"
        assert "katılım veya sipariş beklentisi değildir" in r["reason"]
        assert all("Not" not in f for f in r["facts"])


def test_55_56_57_58_sequence(ctx, snap, monkeypatch):
    make_member(P[15], d(-1))                                        # collections customer
    make_member(P[13], d(-1))                                        # winback customer
    make_member(P[17], d(-1))
    fake_cs = A.make_action(source="cross_sell_anagrup", category="cross_sell", entity_type="customer",
                            entity_id=P[17], title="t", reason="Sentetik çapraz satış", facts=["Hedef grup: X"],
                            drivers={}, score=69.0, confidence="medium")
    monkeypatch.setattr(ctx, "sales_queue_candidates", tuple(ctx.sales_queue_candidates) + (fake_cs,))
    q = queue(ctx, snap)
    assert [r["category"] for r in card(q, P[15])["reasons"]][:2] == ["collections", "fair_followup"]   # 55
    assert [r["category"] for r in card(q, P[13])["reasons"]] == ["winback", "fair_followup"]          # 56
    k17 = card(q, P[17])
    assert [r["category"] for r in k17["reasons"]] == ["fair_followup", "cross_sell"]                  # 57
    assert k17["queue_score"] == 69.0 and k17["primary_action_id"] == k17["reasons"][0]["action_id"]  # 58
    assert k17["draft"]["default_purpose"] == "fair_invite"


def test_59_fair_filter_beyond_first_page(ctx, snap):
    for i in range(17, 21):
        make_member(P[i], d(-1))
    q = queue(ctx, snap, flt="fair_followup", limit=1)
    assert q["visible_customers"] == 4 and q["returned"] == 1 and q["truncated"] is True
    assert {f["key"]: f["label"] for f in q["filters"]}["fair_followup"] == "Fuar Takibi"


def test_60_kapali_never_gets_fair_reason(ctx, snap):
    make_member(P[10], d(-1))                                        # direct store write (UI would block it)
    q = queue(ctx, snap)
    assert card(q, P[10]) is None and q["diagnostics"]["closed_sales_reasons_removed"] >= 1


def test_fair_category_surfaces_and_m5_unchanged(ctx):
    cat = A.category("fair_followup")
    assert cat.surfaces == (A.SURFACE_SALES_QUEUE,) and cat.draft_purpose == "fair_invite"
    assert [c["key"] for c in A.categories_payload()] == ["collections", "overstock", "winback", "real_decline"]
    make_member(P[17], d(-1))
    tasks = SQ.fair_task_actions()
    assert tasks and A.action_center(tasks) == []
    assert not any(a["category"] == "fair_followup" for a in ctx["actions"])
    assert "fair_followup" not in ctx.reason_index


# =============================================================================================
# 92. state separation (61–65)
# =============================================================================================
def test_61_to_65_action_vs_funnel_state(client, ctx):
    cl, _, _ = client
    cid = new_campaign(cl)["campaign_id"]
    add(cl, cid, P[17])
    upd(cl, cid, P[17], stage="accepted", next_action_date=d(0))
    k = card(cl.get("/api/sales-queue", params={"filter": "fair_followup"}).json(), P[17])
    aid = k["reasons"][0]["action_id"]
    assert cl.post(f"/api/actions/{aid}/complete", headers=H).status_code == 200
    m = FS.get_member(cid, P[17])
    assert m["stage"] == "accepted" and m["next_action_date"] == d(0)                       # 61
    assert card(cl.get("/api/sales-queue", params={"filter": "fair_followup"}).json(), P[17]) is None
    assert ST.get_action(aid)["category"] == "fair_followup"                                # 64 same table
    upd(cl, cid, P[17], stage="hotel_confirmed")                                           # 62
    assert ST.get_statuses()[aid]["status"] == "completed"
    k2 = card(cl.get("/api/sales-queue", params={"filter": "fair_followup"}).json(), P[17])
    assert k2 is not None and k2["reasons"][0]["action_id"] != aid                           # 63 next task
    assert ST.get_statuses().get(k2["reasons"][0]["action_id"], {"status": "open"})["status"] == "open"
    ev = {h["event"] for h in FS.history(cid)}
    assert "completed" not in ev and not any("status:" in e for e in ev)                    # 65 separate logs
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM action_history WHERE action_key=? AND event='status:completed'",
                         (aid,)).fetchone()[0] == 1


# =============================================================================================
# 93. API (66–75)
# =============================================================================================
def test_66_to_72_api(client, hist):
    cl, _, _ = client
    c = new_campaign(cl, edition_year=2026, start_date="2026-01-17", event_stem="SENTFUARA")
    cid = c["campaign_id"]
    lst = cl.get("/api/fairs/campaigns").json()
    assert [s["key"] for s in lst["stages"]] == [k for k, _ in FS.STAGES + FS.TERMINAL]
    assert lst["items"][0]["campaign_id"] == cid and cl.get(f"/api/fairs/campaigns/{cid}").status_code == 200
    add(cl, cid, P[1], P[17], P[15])
    upd(cl, cid, P[17], stage="order")
    rows = {x["customer_code"]: x for x in cl.get(f"/api/fairs/campaigns/{cid}/customers").json()["items"]}
    assert rows[P[1]]["erp_fuar_orders"] == 1 and rows[P[1]]["erp_evidence"] == FC.ERP_FOUND
    assert rows[P[1]]["erp_mismatch"] == "erp_order_stage_differs" and rows[P[1]]["stage"] == "invite_candidate"
    assert rows[P[17]]["erp_mismatch"] == "stage_order_without_erp"                          # shown, not synced
    assert rows[P[15]]["collection_first"] is True and "collections" in rows[P[15]]["segments"]
    s = cl.get(f"/api/fairs/campaigns/{cid}").json()
    assert s["stage_counts"]["invite_candidate"] == 2 and s["stage_counts"]["order"] == 1 and s["total_customers"] == 3
    f = cl.get(f"/api/fairs/campaigns/{cid}/customers", params={"stage": "order"}).json()
    assert [x["customer_code"] for x in f["items"]] == [P[17]]
    h = cl.get("/api/fairs/history", params={"year": 2026}).json()                           # 67
    assert {x["label"] for x in h["items"]} == {FX.E26, FX.E26B, FX.EX} and h["years"] == [2026, 2025]
    assert cl.get("/api/fairs/history", params={"q": "fuara"}).json()["total"] == 3
    det = cl.get(f"/api/fairs/history/{ed(hist, FX.E25).event_id}", params={"campaign_id": cid}).json()
    assert det["customer_sort"] == "Fuar etiketli sipariş tutarına göre"
    assert [r["customer_code"] for r in det["customer_rows"]] == [P[1], P[2], P[12], P[10]]
    assert det["customer_rows"][0]["display_name"] == FX.name(1) and det["customer_rows"][0]["in_campaign"]
    assert det["customer_rows"][3]["closed"] is True
    assert cl.get("/api/fairs/history/sales_event:nope").status_code == 404
    cand = cl.get("/api/fairs/candidates", params={"segment": "historical_fuar", "campaign_id": cid}).json()
    assert next(x for x in cand["items"] if x["customer_code"] == P[1])["in_campaign"] is True          # 68
    for url in (f"/api/fairs/campaigns/9999", f"/api/fairs/campaigns/9999/customers"):
        assert cl.get(url).status_code == 404                                                           # 72
    assert cl.put("/api/fairs/campaigns/9999", json={"name": "x"}, headers=H).status_code == 404
    assert cl.post("/api/fairs/campaigns/9999/archive", headers=H).status_code == 404
    assert upd(cl, cid, P[20], stage="called").status_code == 404                                         # 73
    import schemas as S
    S.FairCampaignList(**lst)
    S.FairEditionDetail(**det)


def test_70_mutations_need_header(client):
    cl, _, _ = client
    cid = new_campaign(cl)["campaign_id"]
    for method, url, body in (("post", "/api/fairs/campaigns", {"name": "x", "edition_year": 2027,
                                                                "start_date": "2027-01-01"}),
                              ("put", f"/api/fairs/campaigns/{cid}", {"name": "y"}),
                              ("post", f"/api/fairs/campaigns/{cid}/archive", {}),
                              ("post", f"/api/fairs/campaigns/{cid}/customers", {"customer_codes": [P[1]]}),
                              ("put", f"/api/fairs/campaigns/{cid}/customers", {"customer_code": P[1]})):
        r = getattr(cl, method)(url, json=body)
        assert r.status_code == 403, url
        r = getattr(cl, method)(url, json=body, headers={"X-StockAgent": "0"})
        assert r.status_code == 403
    assert FS.get_campaign(cid)["name"] == "Sentetik Ocak Fuarı" and FS.members(cid) == []


def test_74_75_no_paths_no_ai_no_logs(client, data_dir, hist, caplog):
    cl, _, _ = client
    APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
    with caplog.at_level(logging.DEBUG):
        c = new_campaign(cl, name="Sentetik <b>Kampanya</b>")
        add(cl, c["campaign_id"], P[1], P[12])
        upd(cl, c["campaign_id"], P[12], stage="called", note="Sentetik gizli not", next_action_date=d(0))
        texts = [cl.get(u, params=p).text for u, p in (
            ("/api/fairs/campaigns", {}), (f"/api/fairs/campaigns/{c['campaign_id']}/customers", {}),
            ("/api/fairs/history", {}), (f"/api/fairs/history/{ed(hist, FX.EX).event_id}", {}),
            ("/api/fairs/candidates", {"segment": "historical_fuar"}), ("/api/sales-queue", {"filter": "fair_followup"}),
            (f"/api/customer360/{P[12]}", {}))]
    for t in texts:
        assert data_dir not in t and "Traceback" not in t
    with sqlite3.connect(ST.DB_PATH) as cn:
        assert cn.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == 0
    app_logs = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    for bad in ("SENTFUARA", "Sentetik Fuar", "930.10.", "gizli not", "Kampanya"):
        assert bad not in app_logs
    for mod in ("fairs.py", "fair_store.py", "fair_center.py"):
        names = _names(os.path.join(ROOT, "services", mod))
        assert not ({"log", "logging", "read_excel", "load_workbook", "agent", "evidence"} & names), mod
    ev = open(os.path.join(ROOT, "services", "evidence.py"), encoding="utf-8").read()
    assert "fair" not in ev.lower().replace("fair_invite", "")


def test_zero_excel_reads_after_build(client, hist, monkeypatch):
    cl, _, c360 = client
    c360.snapshot()

    def boom(*a, **k):
        raise AssertionError("Excel read after build")
    import openpyxl
    for mod, nm in ((pd, "read_excel"), (DB, "read_sales_raw"), (DB, "read_aging_raw"), (openpyxl, "load_workbook")):
        monkeypatch.setattr(mod, nm, boom)
    cid = new_campaign(cl)["campaign_id"]
    add(cl, cid, P[1])
    upd(cl, cid, P[1], stage="called", next_action_date=d(0))
    for url, p in (("/api/fairs/history", {}), (f"/api/fairs/history/{ed(hist, FX.E25).event_id}", {}),
                   (f"/api/fairs/campaigns/{cid}/customers", {}), ("/api/fairs/candidates", {}),
                   ("/api/sales-queue", {"filter": "fair_followup"}), (f"/api/customer360/{P[1]}", {})):
        assert cl.get(url, params=p).status_code == 200, url


def test_customer360_fair_section(client, hist):
    cl, _, _ = client
    c = new_campaign(cl, edition_year=2026, start_date="2026-01-17", event_stem="SENTFUARA")
    add(cl, c["campaign_id"], P[1])
    upd(cl, c["campaign_id"], P[1], stage="accepted", next_action_date="2026-12-01", note="yerel not")
    ev = cl.get(f"/api/customer360/{P[1]}").json()["events"]
    camp = ev["campaigns"][0]
    assert (camp["stage"], camp["stage_label"], camp["next_action_date"], camp["has_note"]) == \
        ("accepted", "Kabul", "2026-12-01", True)
    assert camp["erp_fuar_orders"] == 1 and camp["erp_evidence"] == FC.ERP_FOUND
    assert "yerel not" not in json.dumps(ev, ensure_ascii=False)                     # indicator only
    assert cl.get(f"/api/customer360/{P[20]}").json()["events"]["campaigns"] == []


def test_refresh_a_to_b_campaigns_persist(tmp_path):
    folder = str(tmp_path / "ab")
    FX.write_all(folder, sales_file="Sentetik A fuar satışlar.xlsx")
    extra = [CS._line(9001, "2026-07-15", P[17], FX.name(17), P[17], FX.name(17), 777, label="2027SENTYENIFUAR",
                      sku="FS-009")]
    FX.write_all(folder, sales_file="Sentetik B fuar satışlar.xlsx", extra_rows=extra)
    base = {"stock": os.path.join(folder, FX.STOCK_FILE), "aging": os.path.join(folder, FX.AGING_FILE)}
    ctx_a = CTX.build_context(folder, inflation_rate=0.4,
                              sources={**base, "sales": os.path.join(folder, "Sentetik A fuar satışlar.xlsx")})
    ctx_b = CTX.build_context(folder, inflation_rate=0.4,
                              sources={**base, "sales": os.path.join(folder, "Sentetik B fuar satışlar.xlsx")})
    repo = ContextRepository(data_folder=folder, context=ctx_a)
    c360 = C360.Customer360Repository(folder, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        c = new_campaign(cl, edition_year=2027, start_date="2027-01-15", event_stem="SENTYENI")
        add(cl, c["campaign_id"], P[17])
        upd(cl, c["campaign_id"], P[17], stage="called")
        a_hist = cl.get("/api/fairs/history").json()
        a_rows = cl.get(f"/api/fairs/campaigns/{c['campaign_id']}/customers").json()["items"]
        repo.replace_context(ctx_b)
        b_hist = cl.get("/api/fairs/history").json()
        b_camp = cl.get(f"/api/fairs/campaigns/{c['campaign_id']}").json()
        b_rows = cl.get(f"/api/fairs/campaigns/{c['campaign_id']}/customers").json()["items"]
    APP.app.dependency_overrides.clear()
    assert a_hist["total"] == 4 and b_hist["total"] == 5 and b_hist["as_of_data_date"] == "2026-07-15"
    assert a_rows[0]["erp_fuar_orders"] == 0 and b_rows[0]["erp_fuar_orders"] == 1          # evidence updates
    assert b_rows[0]["stage"] == "called" and b_camp["linked_event"]["found"] is True        # state persists
    assert c360.builds == 2


# =============================================================================================
# UI block
# =============================================================================================
@pytest.fixture(scope="module")
def built(data_dir, tmp_path_factory):
    dd = tmp_path_factory.mktemp("fair_dash")
    a, b = str(dd / "with.html"), str(dd / "without.html")
    DB.build(data_dir, a, today=dt.date(2026, 7, 1))
    DB.build(data_dir, b, today=dt.date(2026, 7, 1), fair_panel=False)
    return open(a, encoding="utf-8").read(), open(b, encoding="utf-8").read()


FAIR_BUTTON = '<button class="tab" data-t="fair">Fuar Merkezi</button>'


def _block(h):
    return h[h.index("\n<!--FAIR-TAB-START-->"): h.index("<!--FAIR-TAB-END-->") + len("<!--FAIR-TAB-END-->")]


def test_tab_present_rest_byte_identical(built):
    import re
    w, wo = built
    assert w.count(FAIR_BUTTON) == 1 and w.replace(FAIR_BUTTON, "", 1).replace(_block(w), "", 1) == wo
    assert re.findall(r'<button class="tab" data-t="([a-z0-9]+)"', w)[-5:] == ["sq", "c360", "opp", "fair", "ai"]


def test_tab_script_safe(built):
    block = _block(built[0])
    js = block[block.index("<script>") + 8: block.rindex("</script>")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage",
                   "sessionStorage", "/api/agent/", "/analyze", "enrich", "confirm(", "alert(", "prompt("):
        assert banned not in js
    assert "Fuar Merkezi için panoyu http://127.0.0.1:8000/ üzerinden açın." in js and "textContent" in js
    assert "'X-StockAgent':'1'" in js and "Yeni fuar oluştur" in block
    assert "930." not in block and "SENTFUARA" not in block
    for bad in ("katılımcı", "ziyaretçi", "davetli", "dönüşüm oranı", "roi", "fuar geliri"):
        assert bad not in block.lower().replace("roi)", "")
