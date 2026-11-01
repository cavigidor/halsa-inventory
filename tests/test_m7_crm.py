"""M7-5 lightweight CRM — migration, interactions, current follow-up, KAPALI, Günlük Satış task, task-definition vs
action state, coverage, API, AI isolation, refresh / orphans, UI block.

Synthetic data only (tests/fixtures/fair_synth.py). No real data, no key, no network, no AI call.
Numbers in test names follow the M7-5 instruction (§82–§89).
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
from services import crm as CRM
from services import crm_store as CS
from services import customer360 as C360
from services import evidence as EV
from services import fair_store as FS
from services import sales_queue as SQ
from services import store as ST
from services.entity import ContextRepository
from tests.fixtures import c360_synth as CF
from tests.fixtures import fair_synth as FX
from tests.fixtures import synthetic_collections as SCOL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = FX.P
H = {"X-StockAgent": "1"}
TODAY = dt.date.today()


def d(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _synthetic_collection_reasons():
    """D-071: production derives no collection reason from Cari invoice age; the generic queue mechanics are exercised
    with explicitly synthetic reasons (tests/fixtures/synthetic_collections.py)."""
    with SCOL.synthetic_collection_reasons(SCOL.FAIR):
        yield

@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return FX.write_all(str(tmp_path_factory.mktemp("crm_data")))


@pytest.fixture(scope="module")
def ctx(data_dir):
    return CTX.build_context(data_dir, inflation_rate=0.40)


@pytest.fixture(scope="module")
def snap(data_dir, ctx):
    return C360.build_snapshot(data_dir, ctx)


@pytest.fixture()
def client(data_dir, ctx):
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    c360 = C360.Customer360Repository(data_dir, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        yield cl, repo, c360
    APP.app.dependency_overrides.clear()


def log_contact(cl, code, **kw):
    body = {"occurred_on": TODAY.isoformat(), "channel": "phone", **kw}
    return cl.post(f"/api/crm/customers/{code}/interactions", json=body, headers=H)


def set_fu(cl, code, **kw):
    return cl.put(f"/api/crm/customers/{code}/followup", json=kw, headers=H)


def queue(ctx, snap, **kw):
    kw.setdefault("upsert", False)
    kw.setdefault("limit", 50)
    return SQ.build_queue(ctx, snap, **kw)


def card(q, code):
    return next((k for k in q["items"] if k["customer_code"] == code), None)


def crm_reasons(q, code):
    k = card(q, code)
    return [r for r in (k["reasons"] if k else []) if r["category"] == "crm_followup"]


def tables(db):
    with sqlite3.connect(db) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def columns(db, table):
    with sqlite3.connect(db) as c:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _names(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


# =============================================================================================
# 82. migration (1–7)
# =============================================================================================
def test_01_05_06_07_fresh_database(tmp_path, monkeypatch):
    db = str(tmp_path / "fresh.db")
    monkeypatch.setattr(ST, "DB_PATH", db)
    ST.init_db()
    ST.init_db()                                                                     # 7 idempotent
    assert {"actions", "memory", "ai_cache", "ai_calls", "fair_campaigns", "crm_followups"} <= tables(db)
    assert set(CS._MEMORY_COLUMNS) <= columns(db, "memory")                          # 5
    assert {"customer_code", "purpose", "due_date", "next_action", "owner_label", "source_memory_id"} <= \
        columns(db, "crm_followups")                                                 # 6


def test_02_03_04_old_databases_upgrade(tmp_path, monkeypatch):
    db = str(tmp_path / "old.db")
    with sqlite3.connect(db) as c:      # an M5-era database with old memory rows, AI cache and M7-4 fair state
        c.execute("""CREATE TABLE actions(action_key TEXT PRIMARY KEY, generated_at TEXT, category TEXT,
                     entity_type TEXT, entity_id TEXT, priority TEXT, score REAL, title TEXT, content_hash TEXT,
                     status TEXT DEFAULT 'open', completed_at TEXT, defer_until TEXT, notes TEXT)""")
        c.execute("INSERT INTO actions(action_key,category,status) VALUES('k1','winback','completed')")
        c.execute("""CREATE TABLE memory(id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT, entity_id TEXT,
                     ts TEXT, kind TEXT, detail TEXT, next_followup TEXT)""")
        c.execute("INSERT INTO memory(entity_type,entity_id,ts,kind,detail,next_followup) VALUES"
                  "('customer',?,'2026-01-05T10:00:00','contacted','Eski sentetik not','2026-02-01')", (P[1],))
        c.execute("""CREATE TABLE ai_cache(action_id TEXT NOT NULL, prompt_version TEXT NOT NULL, model TEXT NOT NULL,
                     created_at TEXT, payload TEXT, PRIMARY KEY (action_id, prompt_version, model))""")
        c.execute("INSERT INTO ai_cache VALUES('k1','v1','m','t','{}')")
        FS.init_tables(c)
        c.execute("INSERT INTO fair_campaigns(name,edition_year,start_date,status) VALUES('Eski',2027,'2027-01-15',"
                  "'planned')")
    monkeypatch.setattr(ST, "DB_PATH", db)
    ST.init_db()
    ST.init_db()
    assert ST.get_statuses()["k1"]["status"] == "completed"                          # 2
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0] == 1
        assert c.execute("SELECT name FROM fair_campaigns").fetchone()[0] == "Eski"   # 4
    old = ST.get_memory("customer", P[1])                                            # 3
    assert old[0]["detail"] == "Eski sentetik not" and old[0]["kind"] == "contacted"
    rows, n = CS.legacy_memory(P[1])
    assert n == 1 and rows[0]["detail"] == "Eski sentetik not"
    assert set(CS._MEMORY_COLUMNS) <= columns(db, "memory") and "crm_followups" in tables(db)
    assert CS.structured_customers() == set()                     # legacy rows are not structured CRM coverage


# =============================================================================================
# 83. interactions (8–18)
# =============================================================================================
def test_08_valid_contact(client):
    cl, _, _ = client
    r = log_contact(cl, P[17], purpose="sales_followup", outcome="Görüşüldü", next_action="Fiyat listesi gönder",
                    owner_label="Ofis destek", note="Sentetik kısa not")
    assert r.status_code == 200
    li = r.json()["last_interaction"]
    assert (li["channel"], li["channel_label"], li["outcome"], li["next_action"], li["owner_label"], li["note"]) == \
        ("phone", "Telefon", "Görüşüldü", "Fiyat listesi gönder", "Ofis destek", "Sentetik kısa not")
    assert r.json()["followup"] is None                                             # 28: nothing auto-scheduled


@pytest.mark.parametrize("body,status", [
    ({"channel": "fax"}, 422),                                                       # 10
    ({"purpose": "fair_followup"}, 422),                                             # 11
    ({"outcome": "x" * 121}, 422),                                                   # 12
    ({"next_action": "x" * 161}, 422),                                               # 13
    ({"note": "x" * 501}, 422),                                                      # 14
    ({"owner_label": "x" * 81}, 422),                                                # 15
    ({"occurred_on": "2026-02-30"}, 422),
    ({"occurred_on": "1999-12-31"}, 422),
    ({"kind": "other_kind"}, 422),                                                   # no arbitrary kind
])
def test_10_to_15_validation(client, body, status):
    cl, _, _ = client
    assert log_contact(cl, P[17], **body).status_code == status
    assert CS.interaction_count(P[17]) == 0


def test_09_unknown_customer_and_untrimmed(client):
    cl, _, _ = client
    assert log_contact(cl, "999.99.9999").status_code == 404
    assert log_contact(cl, P[17] + " ").status_code == 404                          # D-003: never trimmed
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 0


def test_16_backdated_ok_future_rejected(client):
    cl, _, _ = client
    assert log_contact(cl, P[17], occurred_on=d(-20)).status_code == 200
    r = log_contact(cl, P[17], occurred_on=d(1))
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "contact_date_in_future"


def test_17_18_append_only_and_legacy_readable(client):
    cl, _, _ = client
    ST.add_memory("customer", P[17], "contacted", "Eski sentetik not")
    log_contact(cl, P[17], occurred_on=d(-5), outcome="İlk")
    log_contact(cl, P[17], occurred_on=d(-1), outcome="Düzeltme: ikinci")
    v = cl.get(f"/api/crm/customers/{P[17]}").json()
    assert [i["outcome"] for i in v["interactions"]] == ["Düzeltme: ikinci", "İlk"] and v["structured_interactions"] == 2
    assert v["legacy"]["count"] == 1 and v["legacy"]["items"][0]["detail"] == "Eski sentetik not"
    routes = {(m, r.path) for r in APP.app.routes for m in getattr(r, "methods", set())}
    assert not any(p.startswith("/api/crm") and m in ("PATCH", "DELETE") for m, p in routes)   # no history edits
    assert not any("interactions/{" in p for _, p in routes)


# =============================================================================================
# 84. current follow-up (19–28)
# =============================================================================================
def test_19_to_22_one_current_followup(client):
    cl, _, _ = client
    r = set_fu(cl, P[17], purpose="sales_followup", due_date=d(3), next_action="Ara", owner_label="Ofis")
    assert r.status_code == 200 and r.json()["followup"]["due_date"] == d(3)
    r = set_fu(cl, P[17], purpose="sales_followup", due_date=d(5), next_action="Teklif gönder")
    assert r.json()["followup"]["next_action"] == "Teklif gönder" and r.json()["followup"]["owner_label"] is None
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM crm_followups WHERE customer_code=?", (P[17],)).fetchone()[0] == 1
    r = set_fu(cl, P[17], clear=True)
    assert r.json()["followup"] is None and CS.get_followup(P[17]) is None
    assert set_fu(cl, P[17], purpose="sales_followup").status_code == 422           # date required
    assert set_fu(cl, P[17], purpose="sales_followup", due_date="2026-13-01").status_code == 422   # 27


def test_23_to_26_due_today_upcoming_order(client):
    cl, _, _ = client
    set_fu(cl, P[19], purpose="sales_followup", due_date=d(-3))
    set_fu(cl, P[18], purpose="sales_followup", due_date=d(-3))
    set_fu(cl, P[17], purpose="service", due_date=d(0))
    set_fu(cl, P[20], purpose="sales_followup", due_date=d(2))
    set_fu(cl, P[1], purpose="sales_followup", due_date=d(30))
    due = cl.get("/api/crm/followups", params={"mode": "due"}).json()
    assert [x["customer_code"] for x in due["items"]] == [P[18], P[19], P[17]]       # 23, 26
    assert [x["due_state"] for x in due["items"]] == ["overdue", "overdue", "today"]
    assert [x["customer_code"] for x in cl.get("/api/crm/followups", params={"mode": "today"}).json()["items"]] == [P[17]]
    up = cl.get("/api/crm/followups", params={"mode": "upcoming"}).json()             # 25
    assert [x["customer_code"] for x in up["items"]] == [P[20]]
    assert [x["customer_code"] for x in cl.get("/api/crm/followups", params={"mode": "upcoming", "days": 31}).json()
            ["items"]] == [P[20], P[1]]
    assert cl.get("/api/crm/followups", params={"mode": "bogus"}).status_code == 422


def test_28_no_auto_generated_next_date(client):
    cl, _, _ = client
    for outcome in ("Ulaşılamadı", "Teklif bekliyor", "Görüşüldü"):
        log_contact(cl, P[17], outcome=outcome)
    assert CS.get_followup(P[17]) is None
    src = open(os.path.join(ROOT, "services", "crm.py"), encoding="utf-8").read()
    assert "timedelta(days=7" not in src and "timedelta(days=30" not in src


def test_followup_in_same_entry_is_explicit(client):
    cl, _, _ = client
    r = log_contact(cl, P[17], outcome="Görüşüldü", followup={"purpose": "sales_followup", "due_date": d(4),
                                                              "next_action": "Numune gönder"})
    fu = CS.get_followup(P[17])
    assert fu["due_date"] == d(4) and fu["source_memory_id"] == r.json()["last_interaction"]["id"]
    assert r.json()["last_interaction"]["next_followup"] == d(4)


# =============================================================================================
# 85. KAPALI (29–34)
# =============================================================================================
def test_29_to_33_kapali(client):
    cl, _, _ = client
    assert log_contact(cl, P[10], purpose="collection", outcome="Ödeme konuşuldu").status_code == 200    # 29
    assert set_fu(cl, P[10], purpose="collection", due_date=d(0)).status_code == 200                     # 30
    assert set_fu(cl, P[10], purpose="service", due_date=d(0)).status_code == 200                        # 31
    for p in ("sales_followup", "other"):                                                                # 32
        r = set_fu(cl, P[10], purpose=p, due_date=d(0))
        assert r.status_code == 409 and r.json()["detail"]["error_code"] == "customer_kapali"
    r = log_contact(cl, P[10], followup={"purpose": "sales_followup", "due_date": d(0)})
    assert r.status_code == 409 and CS.interaction_count(P[10]) == 1               # nothing half-written
    v = cl.get(f"/api/crm/customers/{P[10]}").json()
    assert v["closed"] is True and v["allowed_followup_purposes"] == ["collection", "service"]
    r = set_fu(cl, P[11], purpose="sales_followup", due_date=d(0))                                       # 33
    assert r.status_code == 200 and r.json()["closed"] is None and r.json()["master_status"] == "duplicate"
    assert r.json()["warnings"][0]["code"] == "master_duplicate"


def test_34_later_kapali_suppresses_without_deleting(client, ctx, monkeypatch):
    cl, _, c360 = client
    set_fu(cl, P[17], purpose="sales_followup", due_date=d(0))
    set_fu(cl, P[18], purpose="collection", due_date=d(0))
    snap = c360.snapshot()
    real = snap.master_closed
    monkeypatch.setattr(snap, "master_closed", lambda c: True if c in (P[17], P[18]) else real(c))
    q = queue(ctx, snap)
    assert crm_reasons(q, P[17]) == []                                              # sales follow-up suppressed
    assert [r["kind"] for r in crm_reasons(q, P[18])] == ["operational"]            # collection follow-up stays
    assert CS.get_followup(P[17]) is not None                                       # state not deleted
    v = CRM.customer_view(snap, P[17])
    assert v["followup"]["suppressed_kapali"] is True
    assert "followup_suppressed_kapali" in {w["code"] for w in v["warnings"]}


# =============================================================================================
# 86. queue (35–48)
# =============================================================================================
def test_35_to_41_due_task(ctx, snap):
    CS.set_followup(P[17], purpose="sales_followup", due_date=d(0), next_action="Fiyat listesi gönder",
                    owner_label="Sentetik sorumlu")
    CS.set_followup(P[14], purpose="sales_followup", due_date=d(-40))               # big customer, long overdue
    CS.set_followup(P[18], purpose="sales_followup", due_date=d(1))                 # 36 future
    q = queue(ctx, snap)
    r17, r14 = crm_reasons(q, P[17])[0], crm_reasons(q, P[14])[0]                   # 35
    assert crm_reasons(q, P[18]) == []
    assert r17["category"] == "crm_followup" and r17["category_label"] == "Takip Zamanı"   # 37
    assert r17["score"] == r14["score"] == C.CRM_FOLLOWUP_SCORE == 60                # 38, 39, 40
    assert r17["confidence"] == "high" and "satış olasılığı veya müşteri değeri anlamına gelmez" in r17["reason"]  # 41
    assert "Sonraki iş: Fiyat listesi gönder" in r17["reason"]
    assert r17["facts"] == ["Takip amacı: Satış takibi", f"Takip tarihi: {d(0)}", "Sonraki iş: Fiyat listesi gönder"]
    assert not any("Sentetik sorumlu" in f for f in r17["facts"])                    # owner label never a fact
    assert r17["draft_purpose"] == "sales_followup" and r17["kind"] == "sales"


def test_42_to_46_sequence(ctx, snap, monkeypatch):
    for code in (P[15], P[13], P[17]):
        CS.set_followup(code, purpose="sales_followup", due_date=d(0))
    cid = FS.create_campaign(name="Sentetik", edition_year=2027, start_date="2027-01-15")["campaign_id"]
    for code in (P[13], P[17]):
        FS.add_member(cid, code)
        FS.update_member(cid, code, stage="called", next_action_date=d(0))
    fake_cs = A.make_action(source="cross_sell_anagrup", category="cross_sell", entity_type="customer",
                            entity_id=P[17], title="t", reason="Sentetik", facts=["Hedef grup: X"], drivers={},
                            score=69.0, confidence="medium")
    monkeypatch.setattr(ctx, "sales_queue_candidates", tuple(ctx.sales_queue_candidates) + (fake_cs,))
    q = queue(ctx, snap)
    assert [r["category"] for r in card(q, P[15])["reasons"]][:2] == ["collections", "crm_followup"]       # 42
    assert [r["category"] for r in card(q, P[13])["reasons"]] == ["crm_followup", "winback", "fair_followup"]  # 43, 44
    k17 = card(q, P[17])
    assert [r["category"] for r in k17["reasons"]] == ["crm_followup", "fair_followup", "cross_sell"]     # 45
    assert k17["queue_score"] == 69.0 and k17["primary_action_id"] == k17["reasons"][0]["action_id"]     # 46


def test_47_filter_beyond_first_page(ctx, snap):
    for i in range(17, 21):
        CS.set_followup(P[i], purpose="sales_followup", due_date=d(-1))
    q = queue(ctx, snap, flt="crm_followup", limit=2)
    assert q["visible_customers"] == 4 and q["returned"] == 2 and q["truncated"] is True
    assert {f["key"]: f["label"] for f in q["filters"]}["crm_followup"] == "Takip Zamanı"


def test_48_existing_reasons_unchanged(ctx, snap):
    base = {r["action_id"]: r["score"] for k in queue(ctx, snap)["items"] for r in k["reasons"]}
    CS.set_followup(P[13], purpose="sales_followup", due_date=d(0))
    CS.set_followup(P[15], purpose="collection", due_date=d(0))
    after = {r["action_id"]: r["score"] for k in queue(ctx, snap)["items"] for r in k["reasons"]
             if r["category"] != "crm_followup"}
    assert after == base
    assert not any(a["category"] == "crm_followup" for a in ctx["actions"])
    assert [c["key"] for c in A.categories_payload()] == ["collections", "overstock", "winback", "real_decline"]
    assert A.category("crm_followup").surfaces == (A.SURFACE_SALES_QUEUE,)
    assert A.action_center(SQ.crm_task_actions()) == []
    assert not any(a["category"] == "crm_followup" for a in ctx.sales_queue_candidates)   # never in the reservoir


def test_draft_rules_by_purpose(ctx, snap):
    CS.set_followup(P[17], purpose="service", due_date=d(0))
    CS.set_followup(P[18], purpose="other", due_date=d(0))
    CS.set_followup(P[19], purpose="collection", due_date=d(0))
    CS.set_followup(P[20], purpose="sales_followup", due_date=d(0))
    q = queue(ctx, snap, flt="crm_followup")
    assert card(q, P[17])["draft"]["available"] is False and card(q, P[18])["draft"]["available"] is False
    assert card(q, P[19])["draft"] == {**card(q, P[19])["draft"], "available": True, "default_purpose": "collection"}
    assert card(q, P[19])["collection_first"] is False                    # a CRM purpose is not a receivable fact
    assert card(q, P[20])["draft"]["default_purpose"] == "sales_followup"
    k13 = card(queue(ctx, snap), P[13])                                    # existing cards keep their drafts
    assert k13["draft"]["available"] is True and k13["draft"]["default_purpose"] == "winback"


# =============================================================================================
# 87. task definition vs action state (49–54)
# =============================================================================================
def test_49_to_54_definition_vs_state(client, ctx):
    cl, _, _ = client
    set_fu(cl, P[17], purpose="sales_followup", due_date=d(-1), next_action="Ara")
    k = card(cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json(), P[17])
    a1 = k["reasons"][0]["action_id"]
    assert cl.post(f"/api/actions/{a1}/complete", headers=H).status_code == 200
    assert CS.get_followup(P[17])["due_date"] == d(-1)                                   # 49 not erased
    assert card(cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json(), P[17]) is None
    assert ST.get_action(a1)["category"] == "crm_followup"                               # 54 same table
    set_fu(cl, P[17], purpose="sales_followup", due_date=d(0), next_action="Ara")         # 50, 51
    k2 = card(cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json(), P[17])
    a2 = k2["reasons"][0]["action_id"]
    assert a2 != a1 and ST.get_statuses()[a1]["status"] == "completed"
    set_fu(cl, P[17], purpose="sales_followup", due_date=d(0), next_action="Teklif gönder")
    a3 = card(cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json(), P[17])["reasons"][0]["action_id"]
    assert a3 not in (a1, a2)
    set_fu(cl, P[17], purpose="sales_followup", due_date=d(0), next_action="Teklif gönder", owner_label="Başka")
    a4 = card(cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json(), P[17])["reasons"][0]["action_id"]
    assert a4 == a3                                                                       # owner label: same task
    set_fu(cl, P[17], clear=True)                                                         # 52
    assert card(cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json(), P[17]) is None
    with sqlite3.connect(ST.DB_PATH) as c:                                                # 53 history kept
        assert c.execute("SELECT COUNT(*) FROM action_history WHERE action_key=?", (a1,)).fetchone()[0] >= 2
        assert c.execute("SELECT COUNT(*) FROM actions WHERE category='crm_followup'").fetchone()[0] >= 3


# =============================================================================================
# 88. coverage (55–60)
# =============================================================================================
def test_55_to_58_coverage(client):
    cl, _, _ = client
    ST.add_memory("customer", P[1], "contacted", "Eski not")                              # legacy
    log_contact(cl, P[17])
    log_contact(cl, P[17])
    log_contact(cl, P[16])                                                                # inactive customer
    set_fu(cl, P[18], purpose="sales_followup", due_date=d(-2))
    set_fu(cl, P[19], purpose="sales_followup", due_date=d(0))
    set_fu(cl, P[20], purpose="sales_followup", due_date=d(3))
    s = cl.get("/api/crm/summary").json()
    assert s["customers_with_structured_contact"] == 2 and s["active_customers_with_structured_contact"] == 1   # 55, 56
    assert s["structured_interactions"] == 3 and s["legacy_memory_rows"] == 1                                  # 57
    assert s["coverage_status"] == "incomplete" and s["coverage_label"] == "Eksik kayıt olabilir"               # 58
    assert (s["current_followups"], s["overdue"], s["due_today"], s["upcoming_7d"]) == (3, 1, 1, 1)
    assert s["total_customers"] == 20 and s["orphan_customer_records"] == 0


def test_58_coverage_never_upgrades_with_volume(client):
    cl, _, _ = client
    for code in FX.P.values():
        if code not in (P[10],):
            log_contact(cl, code)
    s = cl.get("/api/crm/summary").json()
    assert s["customers_with_structured_contact"] == 19 and s["coverage_status"] == "incomplete"
    assert "çalışan performansı karşılaştırmak için kullanılmaz" in s["coverage_note"]


def test_59_no_person_performance_metrics(client):
    cl, _, _ = client
    log_contact(cl, P[17], owner_label="Ofis A")
    log_contact(cl, P[18], owner_label="Ofis B")
    s = cl.get("/api/crm/summary").json()
    for k in s:
        assert not any(w in k for w in ("owner", "rep", "person", "employee", "salesperson", "per_", "rate",
                                        "conversion", "leader"))
    assert "Ofis A" not in json.dumps(s)
    for mod in ("crm.py", "crm_store.py"):
        src = open(os.path.join(ROOT, "services", mod), encoding="utf-8").read()
        assert "GROUP BY owner" not in src and "group by owner" not in src.lower()
        names = _names(os.path.join(ROOT, "services", mod))
        assert not ({"leaderboard", "per_owner", "by_owner", "Temsilci", "satici", "hedef_rep", "salespeople"} & names)
    # M7-6: `/api/owner/*` is the company-owner (Yönetim) audience, not a CRM owner-label metric
    routes = [r.path.replace("/api/owner/", "/api/yonetim/") for r in APP.app.routes]
    assert not any(w in p for p in routes for w in ("leader", "performance", "owner", "rep"))


def test_60_orphan_state_safe(client, ctx):
    cl, _, c360 = client
    CS.add_interaction("999.99.0001", occurred_on=d(-1), channel="phone", note="yetim not")
    CS.set_followup("999.99.0001", purpose="sales_followup", due_date=d(-1))
    s = cl.get("/api/crm/summary").json()
    assert s["orphan_customer_records"] == 1 and "999.99" not in json.dumps(s)
    f = cl.get("/api/crm/followups", params={"mode": "all"}).json()
    assert f["orphan_followups"] == 1 and all(x["customer_code"] != "999.99.0001" for x in f["items"])
    assert all(x["customer_code"] != "999.99.0001" for x in cl.get("/api/crm/recent").json()["items"])
    q = cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json()
    assert q["items"] == [] and q["diagnostics"]["unresolved_sales_customers"] >= 1
    assert CS.get_followup("999.99.0001") is not None                                      # never deleted


# =============================================================================================
# 89. API (61–71) + AI isolation + Excel reads
# =============================================================================================
def test_61_to_66_api_contracts(client):
    import schemas as S
    cl, _, _ = client
    S.CrmSummary(**cl.get("/api/crm/summary").json())
    log_contact(cl, P[17], outcome="Görüşüldü", followup={"purpose": "sales_followup", "due_date": d(0)})
    set_fu(cl, P[18], purpose="service", due_date=d(2))
    S.CrmFollowupList(**cl.get("/api/crm/followups").json())
    S.CrmFollowupList(**cl.get("/api/crm/followups", params={"mode": "upcoming"}).json())
    c = cl.get(f"/api/crm/customers/{P[17]}").json()
    S.CrmCustomer(**c)
    assert c["coverage_status"] == "incomplete" and c["last_interaction"]["outcome"] == "Görüşüldü"
    rec = cl.get("/api/crm/recent").json()
    S.CrmRecent(**rec)
    assert rec["items"][0]["customer_code"] == P[17] and "note" not in rec["items"][0]   # no notes in the feed
    det = cl.get(f"/api/customer360/{P[17]}").json()
    assert det["crm"]["followup"]["due_date"] == d(0) and det["crm"]["structured_interactions"] == 1
    assert cl.get(f"/api/crm/customers/{P[18]}").json()["followup"]["purpose"] == "service"
    assert set_fu(cl, P[18], clear=True).json()["followup"] is None


def test_67_writes_need_header(client):
    cl, _, _ = client
    for method, url, body in (("post", f"/api/crm/customers/{P[17]}/interactions",
                               {"occurred_on": TODAY.isoformat(), "channel": "phone"}),
                              ("put", f"/api/crm/customers/{P[17]}/followup",
                               {"purpose": "sales_followup", "due_date": d(0)})):
        assert getattr(cl, method)(url, json=body).status_code == 403
        assert getattr(cl, method)(url, json=body, headers={"X-StockAgent": "2"}).status_code == 403
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM crm_followups").fetchone()[0] == 0


def test_69_unknown_customer_everywhere(client):
    cl, _, _ = client
    for r in (cl.get("/api/crm/customers/999.99.9999"), set_fu(cl, "999.99.9999", purpose="service", due_date=d(0)),
              log_contact(cl, "999.99.9999")):
        assert r.status_code == 404 and r.json()["detail"]["error_code"] == "customer_not_found"


def test_70_71_privacy_and_zero_ai(client, data_dir, caplog):
    cl, _, _ = client
    APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
    hostile = '<script>window.__CRM_XSS=1</script>gizli'
    with caplog.at_level(logging.DEBUG):
        log_contact(cl, P[12], outcome=hostile, next_action=hostile, owner_label="Sorumlu X", note="Çok gizli not",
                    followup={"purpose": "sales_followup", "due_date": d(0)})
        set_fu(cl, P[12], clear=True)
        set_fu(cl, P[12], purpose="sales_followup", due_date=d(0), next_action="Sentetik iş")
        texts = [cl.get(u, params=p).text for u, p in (("/api/crm/summary", {}), ("/api/crm/followups", {}),
                                                         ("/api/crm/recent", {}), (f"/api/crm/customers/{P[12]}", {}),
                                                         ("/api/sales-queue", {"filter": "crm_followup"}),
                                                         (f"/api/customer360/{P[12]}", {}))]
        bad = log_contact(cl, P[12], note="x" * 501)
    assert bad.status_code == 422 and "x" * 50 not in bad.text                   # no echo of the rejected note
    for t in texts:
        assert data_dir not in t and "Traceback" not in t
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == 0
    app_logs = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    for s in ("Çok gizli not", "Sorumlu X", "gizli", "Sentetik iş", "930.10."):
        assert s not in app_logs
    for mod in ("crm.py", "crm_store.py"):
        names = _names(os.path.join(ROOT, "services", mod))
        assert not ({"log", "logging", "read_excel", "load_workbook", "agent", "evidence", "provider"} & names)


def test_crm_content_never_reaches_ai_packets(client, ctx):
    cl, repo, _ = client
    ST.add_memory("customer", P[13], "contacted", "Eski not (M5)")
    log_contact(cl, P[13], outcome="Gizli sonuç", next_action="Gizli iş", owner_label="Gizli kişi",
                note="Gizli CRM notu")
    mem = ST.get_memory("customer", P[13])
    assert [m["detail"] for m in mem] == ["Eski not (M5)"]                        # legacy reader unchanged
    facts = APP._customer_facts(repo, P[13])
    pkt = json.dumps(EV.customer_packet(facts, ctx, facts["history"]).to_payload(), ensure_ascii=False, default=str)
    for s in ("Gizli sonuç", "Gizli iş", "Gizli kişi", "Gizli CRM notu"):
        assert s not in pkt
    ev = open(os.path.join(ROOT, "services", "evidence.py"), encoding="utf-8").read()
    assert "crm" not in ev.lower()


def test_draft_for_queue_only_customer(client):
    cl, repo, _ = client
    assert repo.customer(P[19]) is None                                           # not in the M5 entity index
    CS.set_followup(P[19], purpose="sales_followup", due_date=d(0))
    r = cl.post("/api/agent/draft-message", headers=H,
                json={"customer_code": P[19], "channel": "whatsapp", "purpose": "sales_followup"})
    assert r.status_code == 200 and r.json()["available"] is False                # AI disabled: resolved, no call
    r = cl.post("/api/agent/draft-message", headers=H,
                json={"customer_code": P[10], "channel": "whatsapp", "purpose": "sales_followup"})
    assert r.status_code == 200 and r.json()["available"] is False                # KAPALI: refused before AI
    assert "KAPALI" in (r.json()["reason"] or "") or r.json()["ai_error_category"]
    assert cl.post("/api/agent/draft-message", headers=H,
                   json={"customer_code": "999.99.9999", "channel": "whatsapp",
                         "purpose": "sales_followup"}).status_code == 404


def test_zero_excel_reads(client, monkeypatch):
    cl, _, c360 = client
    c360.snapshot()

    def boom(*a, **k):
        raise AssertionError("Excel read after build")
    import openpyxl
    for mod, nm in ((pd, "read_excel"), (DB, "read_sales_raw"), (DB, "read_aging_raw"), (openpyxl, "load_workbook")):
        monkeypatch.setattr(mod, nm, boom)
    log_contact(cl, P[17], followup={"purpose": "sales_followup", "due_date": d(0)})
    for url, p in (("/api/crm/summary", {}), ("/api/crm/followups", {}), ("/api/crm/recent", {}),
                   (f"/api/crm/customers/{P[17]}", {}), ("/api/sales-queue", {"filter": "crm_followup"}),
                   (f"/api/customer360/{P[17]}", {})):
        assert cl.get(url, params=p).status_code == 200, url


def test_fair_and_crm_stay_separate(client):
    cl, _, _ = client
    c = cl.post("/api/fairs/campaigns", json={"name": "Sentetik", "edition_year": 2027, "start_date": "2027-01-15"},
                headers=H).json()
    cl.post(f"/api/fairs/campaigns/{c['campaign_id']}/customers", json={"customer_codes": [P[17]]}, headers=H)
    cl.put(f"/api/fairs/campaigns/{c['campaign_id']}/customers",
           json={"customer_code": P[17], "stage": "called", "next_action_date": d(0), "note": "fuar notu"}, headers=H)
    assert CS.get_followup(P[17]) is None and CS.interaction_count(P[17]) == 0      # no copy into CRM
    set_fu(cl, P[17], purpose="sales_followup", due_date=d(0))
    k = card(cl.get("/api/sales-queue").json(), P[17])
    assert [r["category"] for r in k["reasons"]] == ["crm_followup", "fair_followup"]   # both, not deduplicated
    assert FS.get_member(c["campaign_id"], P[17])["stage"] == "called"
    v = cl.get(f"/api/crm/customers/{P[17]}").json()
    assert "fuar notu" not in json.dumps(v, ensure_ascii=False)


def test_refresh_keeps_crm_state(tmp_path):
    folder = str(tmp_path / "ab")
    FX.write_all(folder, sales_file="Sentetik A fuar satışlar.xlsx")
    extra = [CF._line(9001, "2026-07-15", P[17], FX.name(17), P[17], FX.name(17), 777)]
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
        log_contact(cl, P[17], outcome="A döneminde")
        set_fu(cl, P[17], purpose="sales_followup", due_date=d(0))
        repo.replace_context(ctx_b)
        v = cl.get(f"/api/crm/customers/{P[17]}").json()
        q = cl.get("/api/sales-queue", params={"filter": "crm_followup"}).json()
    APP.app.dependency_overrides.clear()
    assert c360.builds == 2 and v["last_interaction"]["outcome"] == "A döneminde" and v["followup"] is not None
    assert card(q, P[17]) is not None


# =============================================================================================
# UI block
# =============================================================================================
@pytest.fixture(scope="module")
def built(data_dir, tmp_path_factory):
    dd = tmp_path_factory.mktemp("crm_dash")
    a, b = str(dd / "with.html"), str(dd / "without.html")
    DB.build(data_dir, a, today=dt.date(2026, 7, 1))
    DB.build(data_dir, b, today=dt.date(2026, 7, 1), crm_panel=False)
    return open(a, encoding="utf-8").read(), open(b, encoding="utf-8").read()


CRM_BUTTON = '<button class="tab" data-t="crm">Takip / CRM</button>'


def _block(h):
    return h[h.index("\n<!--CRM-TAB-START-->"): h.index("<!--CRM-TAB-END-->") + len("<!--CRM-TAB-END-->")]


def test_tab_present_rest_byte_identical(built):
    import re
    w, wo = built
    assert w.count(CRM_BUTTON) == 1 and w.replace(CRM_BUTTON, "", 1).replace(_block(w), "", 1) == wo
    assert re.findall(r'<button class="tab" data-t="([a-z0-9]+)"', w)[-7:] == ["sq", "c360", "opp", "fair", "crm", "own", "ai"]


def test_tab_script_safe(built):
    block = _block(built[0])
    js = block[block.index("<script>") + 8: block.rindex("</script>")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage",
                   "sessionStorage", "/api/agent/", "confirm(", "alert(", "prompt("):
        assert banned not in js
    assert "Takip / CRM için panoyu http://127.0.0.1:8000/ üzerinden açın." in js and "textContent" in js
    assert "Eksik kayıt olabilir" in block
    assert "çalışan performansı karşılaştırmak için kullanılmaz" in block
    for bad in ("leaderboard", "lider", "performans sıralaması", "dönüşüm oranı", "temsilci başına"):
        assert bad not in block.lower()
