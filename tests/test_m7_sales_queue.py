"""M7-2 Günlük Satış — candidate seam, grouping, collection mapping, KAPALI, state, API, refresh, separation.

Synthetic workbooks only (tests/fixtures/queue_synth.py and tests/fixtures/synthetic_erp.py): invented codes, names
and amounts. No real data, no key, no network, no AI call. Numbers in comments refer to the M7-2 instruction.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
import threading

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import config as C
import dashboard_builder as DB
from services import actions as A
from services import context as CTX
from services import customer360 as C360
from services import evidence as EV
from services import sales_queue as SQ
from services import store as ST
from services.entity import ContextRepository
from tests.fixtures import queue_synth as Q

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q1, Q2, Q3, Q4, Q5, Q6, Q7, Q8, Q9 = (Q.Q[i] for i in range(1, 10))


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return Q.write_all(str(tmp_path_factory.mktemp("queue_data")))


@pytest.fixture(scope="module")
def ctx(data_dir):
    return CTX.build_context(data_dir, inflation_rate=0.40)


@pytest.fixture(scope="module")
def snap(data_dir, ctx):
    return C360.build_snapshot(data_dir, ctx)


def cand(ctx, category, entity):
    hits = [a for a in ctx.sales_queue_candidates if a["category"] == category and str(a["entity_id"]) == entity]
    assert len(hits) == 1, (category, entity)
    return hits[0]


def queue(ctx, snap, **kw):
    kw.setdefault("upsert", False)
    return SQ.build_queue(ctx, snap, **kw)


def card(q, code):
    hits = [k for k in q["items"] if k["customer_code"] == code]
    return hits[0] if hits else None


def _old_produce_all(inputs, limit=None):
    """The pre-M7-2 implementation of actions.produce_all, copied verbatim (regression reference), over the four
    pre-M7-3 sources (M7-3 registers the sales-queue-only cross-sell source after them)."""
    acts = []
    for s in [x for x in A._SOURCES if x.name not in ("cross_sell_anagrup", "fair_campaign", "crm_followup")]:
        for a in s.produce(inputs) or []:
            a.setdefault("source", s.name)
            acts.append(A.validate(a))
    acts.sort(key=lambda a: -a["score"])
    return acts[: (limit or C.MAX_DAILY_ACTIONS)]


def _capture_inputs(monkeypatch, folder):
    seen = {}
    real = CTX.build_action_candidates

    def spy(ctx, coll_scored, over_cand, lap, shrink_real_neg, infl, cross_sell=None):
        seen["inputs"] = {"ctx": ctx, "coll_scored": coll_scored, "over_cand": over_cand, "lap": lap,
                          "shrink_real_neg": shrink_real_neg, "infl": infl, "cross_sell": cross_sell}
        return real(ctx, coll_scored, over_cand, lap, shrink_real_neg, infl, cross_sell=cross_sell)
    monkeypatch.setattr(CTX, "build_action_candidates", spy)
    built = CTX.build_context(folder, inflation_rate=0.40)
    return built, seen["inputs"]


# =============================================================================================
# 57. candidate seam (1–6)
# =============================================================================================
def test_01_uncut_customer_candidates_exist_before_top15_cut(ctx):
    assert len(ctx["actions"]) == C.MAX_DAILY_ACTIONS == 15
    assert all(a["entity_type"] == "product" for a in ctx["actions"]), "fixture: products fill the M5 top-15"
    cats = {a["category"] for a in ctx.sales_queue_candidates}
    assert cats == {"collections", "winback", "real_decline"}
    assert all(a["entity_type"] == "customer" for a in ctx.sales_queue_candidates)
    assert max(a["score"] for a in ctx.sales_queue_candidates) < min(a["score"] for a in ctx["actions"])
    scores = [a["score"] for a in ctx.sales_queue_candidates]
    assert scores == sorted(scores, reverse=True)                       # Python score order, uncut


def test_02_products_do_not_take_daily_sales_slots(ctx, snap):
    q = queue(ctx, snap)
    assert q["items"] and all(r["category"] in SQ.QUEUE_CATEGORIES for k in q["items"] for r in k["reasons"])
    assert q["diagnostics"]["other_categories_ignored"] == 0
    assert not any(a["entity_type"] == "product" for a in ctx.sales_queue_candidates)


@pytest.mark.parametrize("which", ["queue", "m5"])
def test_03_m5_top15_unchanged(which, monkeypatch, data_dir, synthetic_dir):
    folder = data_dir if which == "queue" else synthetic_dir
    built, inputs = _capture_inputs(monkeypatch, folder)
    old = _old_produce_all(inputs)
    assert [a["action_id"] for a in built["actions"]] == [a["action_id"] for a in old]
    assert built["actions"] == old                                    # scores, facts, ids, order, priorities
    assert len(built["actions"]) <= C.MAX_DAILY_ACTIONS
    old_customers = {str(a["entity_id"]) for a in old if a["entity_type"] == "customer"}
    extra = built.get("action_customers") or {}
    assert {str(r["code"]) for v in extra.values() for r in v} <= old_customers   # M5-4 rows unchanged


def test_04_candidate_pool_bounded(monkeypatch, data_dir):
    monkeypatch.setattr(C, "MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY", 1)    # M7-3: the bound is per category
    small = CTX.build_context(data_dir, inflation_rate=0.40)
    full = CTX.build_context(data_dir, inflation_rate=0.40).sales_queue_candidates
    cats = [a["category"] for a in small.sales_queue_candidates]
    assert sorted(cats) == sorted({a["category"] for a in full}) and len(cats) == len(set(cats))
    best = {}
    for a in full:
        best.setdefault(a["category"], a["action_id"])
    assert [a["action_id"] for a in small.sales_queue_candidates] == [a["action_id"] for a in full
                                                                       if best[a["category"]] == a["action_id"]]
    assert isinstance(small.sales_queue_candidates, tuple)              # immutable once built


def _private_ids(ctx):
    top = {a["action_id"] for a in ctx["actions"]}
    ids = [a["action_id"] for a in ctx.sales_queue_candidates if a["action_id"] not in top]
    assert ids
    return ids


def test_05_private_candidates_not_in_public_context(ctx, data_dir, tmp_path):
    ids = _private_ids(ctx)
    blob = json.dumps(ctx, ensure_ascii=False, default=str)
    assert "sales_queue" not in blob and not any(i in blob for i in ids)
    assert set(ctx.keys()) == set(json.loads(blob).keys())
    out, written = CTX.write_context(data_dir, str(tmp_path / "agent_context.json"), inflation_rate=0.40)
    txt = open(out, encoding="utf-8").read()
    assert "sales_queue" not in txt and not any(i in txt for i in _private_ids(written))
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        body = cl.get("/api/context").text
        acts = cl.get("/api/agent/actions").text
    APP.app.dependency_overrides.clear()
    assert "sales_queue" not in body and not any(i in body for i in ids)
    assert not any(i in acts for i in ids)                              # M5 list: top-15 only


def test_06_private_candidates_not_in_evidence_packets(ctx):
    ids = _private_ids(ctx)
    pkt = json.dumps(EV.actions_packet(ctx["actions"], ctx), ensure_ascii=False, default=str)
    assert not any(i in pkt for i in ids) and "sales_queue" not in pkt
    src = open(os.path.join(ROOT, "services", "evidence.py"), encoding="utf-8").read()
    assert "sales_queue" not in src
    for f in ("agent.py", "ai/grounding.py", "ai/openai_provider.py"):
        p = os.path.join(ROOT, "services", f)
        if os.path.exists(p):
            assert "sales_queue" not in open(p, encoding="utf-8").read()


def test_entity_fallback_keeps_public_customers_identical(data_dir, monkeypatch):
    monkeypatch.setattr(C, "MAX_DAILY_ACTIONS", 1)                     # tiny public lists → queue codes outside
    ctx = CTX.build_context(data_dir, inflation_rate=0.40, top_n=1)
    plain = CTX.AgentContext(dict(ctx))                    # same items, no private attributes
    a = ContextRepository(data_folder=data_dir, context=plain)
    b = ContextRepository(data_folder=data_dir, context=ctx)
    a._build_indices()
    b._build_indices()
    for code, facts in a._cust_idx.items():
        assert b._cust_idx[code] == facts                  # M5 facts of known customers unchanged
    assert set(b._cust_idx) - set(a._cust_idx)              # queue customers resolvable for drafts
    assert b.customer(Q.Q1_SECOND) is not None and a.customer(Q.Q1_SECOND) is None


# =============================================================================================
# 58. grouping (7–13)
# =============================================================================================
def test_07_one_customer_one_reason(ctx, snap):
    k = card(queue(ctx, snap), Q2)
    assert k is not None and len(k["reasons"]) == 1 and k["reasons"][0]["category"] == "winback"
    w = cand(ctx, "winback", Q2)
    assert k["reasons"][0]["action_id"] == w["action_id"] and k["queue_score"] == w["score"]
    assert k["collection_first"] is False and k["primary_action_id"] == w["action_id"]


def test_08_one_customer_several_reasons_one_card(ctx, snap):
    q = queue(ctx, snap)
    codes = [k["customer_code"] for k in q["items"]]
    assert len(codes) == len(set(codes))
    k = card(q, Q1)
    assert sorted(r["category"] for r in k["reasons"]) == ["collections", "collections", "real_decline"]


def test_09_queue_score_is_max(ctx, snap):
    k = card(queue(ctx, snap), Q1)
    scores = [cand(ctx, "collections", Q1)["score"], cand(ctx, "collections", Q.Q1_SECOND)["score"],
              cand(ctx, "real_decline", Q1)["score"]]
    assert k["queue_score"] == max(scores)


def test_10_secondary_reason_adds_nothing(ctx, snap, monkeypatch):
    q = queue(ctx, snap)
    k = card(q, Q1)
    assert k["queue_score"] < sum(r["score"] for r in k["reasons"])
    only = tuple(a for a in ctx.sales_queue_candidates
                 if not (a["category"] == "real_decline" and a["entity_id"] == Q1))
    monkeypatch.setattr(ctx, "sales_queue_candidates", only)
    assert card(queue(ctx, snap), Q1)["queue_score"] == k["queue_score"]


def test_11_collection_first_primary(ctx, snap):
    k = card(queue(ctx, snap), Q1)
    assert k["collection_first"] is True
    assert k["reasons"][0]["action_id"] == k["primary_action_id"]
    assert k["reasons"][0]["category"] == "collections"
    assert k["priority"] == C.band(k["queue_score"])                    # sequence only, no score bonus


def test_11b_collection_first_even_when_sales_scores_higher(ctx, snap, monkeypatch):
    dec = dict(cand(ctx, "real_decline", Q1), score=99.0)
    rest = tuple(a for a in ctx.sales_queue_candidates if a["action_id"] != dec["action_id"])
    monkeypatch.setattr(ctx, "sales_queue_candidates", (dec,) + rest)
    k = card(queue(ctx, snap), Q1)
    assert k["queue_score"] == 99.0 and k["collection_first"] is True
    assert k["reasons"][0]["category"] == "collections" and k["primary_action_id"] != dec["action_id"]
    assert k["priority"] == "critical"


def test_12_deterministic_tie_order(ctx, snap, monkeypatch):
    w2, w8 = cand(ctx, "winback", Q2), cand(ctx, "winback", Q8)
    c3 = cand(ctx, "collections", Q3)
    tied = (dict(w8, score=60.0), dict(w2, score=60.0), dict(c3, score=60.0))
    monkeypatch.setattr(ctx, "sales_queue_candidates", tied)
    q1 = queue(ctx, snap)
    monkeypatch.setattr(ctx, "sales_queue_candidates", tuple(reversed(tied)))
    q2 = queue(ctx, snap)
    order = [k["customer_code"] for k in q1["items"]]
    assert order == [k["customer_code"] for k in q2["items"]]
    assert order == [Q3, Q2, Q8]                                        # tie: collection-first, then code


def test_13_priority_band_from_config(ctx, snap):
    for k in queue(ctx, snap)["items"]:
        assert k["priority"] == C.band(k["queue_score"])
    src = open(os.path.join(ROOT, "services", "sales_queue.py"), encoding="utf-8").read()
    for banned in ("BANDS", ">= 85", ">= 70", ">= 50", "bonus"):
        assert banned not in src


# =============================================================================================
# 59. collection mapping (14–19)
# =============================================================================================
def test_14_one_account_one_customer(ctx, snap):
    k = card(queue(ctx, snap), Q3)
    r = k["reasons"][0]
    assert r["category"] == "collections" and r["account_code"] == Q3 and r["scope"] == "invoice_account"
    assert snap.account_customers[Q3] == [Q3]


def test_15_multiple_exclusive_accounts_same_customer(ctx, snap):
    k = card(queue(ctx, snap), Q1)
    accs = sorted(r["account_code"] for r in k["reasons"] if r["category"] == "collections")
    assert accs == sorted([Q1, Q.Q1_SECOND])
    assert k["draft"]["collection_target"] == k["reasons"][0]["account_code"]


def test_16_shared_account_not_assigned(ctx, snap):
    q = queue(ctx, snap)
    assert len(snap.account_customers[Q.SHARED]) == 2
    assert card(q, Q5) is None and card(q, Q6) is None
    assert not any(r.get("account_code") == Q.SHARED for k in q["items"] for r in k["reasons"])
    assert q["diagnostics"]["shared_collection_accounts_not_assigned"] == 1
    assert any(a["entity_id"] == Q.SHARED for a in ctx.sales_queue_candidates)   # still an M5 candidate


def test_17_unlinked_account_not_assigned(ctx, snap):
    q = queue(ctx, snap)
    assert Q.UNLINKED not in snap.account_customers
    assert not any(r.get("account_code") == Q.UNLINKED for k in q["items"] for r in k["reasons"])
    assert q["diagnostics"]["unlinked_collection_accounts"] == 1


def test_18_shared_balance_never_divided(ctx, snap):
    q = queue(ctx, snap)
    shared = cand(ctx, "collections", Q.SHARED)
    for k in q["items"]:
        for r in k["reasons"]:
            assert r["action_id"] != shared["action_id"]
            assert r["facts"] == [f for f in cand_by_id(ctx, r["action_id"])["facts"]
                                  if not f.startswith("Temsilci:")]       # facts are M5's, never re-computed
    src = open(os.path.join(ROOT, "services", "sales_queue.py"), encoding="utf-8").read()
    assert "/ len(" not in src and "share" not in src.replace("shared", "")


def cand_by_id(ctx, aid):
    return next(a for a in ctx.sales_queue_candidates if a["action_id"] == aid)


def test_19_account_scope_explicit(ctx, snap):
    k = card(queue(ctx, snap), Q1)
    assert {w["code"] for w in k["warnings"]} >= {"collection_account_scope"}
    for r in k["reasons"]:
        assert r["scope"] == ("invoice_account" if r["category"] == "collections" else "customer")
        assert (r["account_code"] is not None) == (r["category"] == "collections")


# =============================================================================================
# 60. KAPALI (20–24)
# =============================================================================================
def test_20_open_customer_sales_reasons_kept(ctx, snap):
    q = queue(ctx, snap)
    assert snap.master_closed(Q2) is False and card(q, Q2) is not None
    assert snap.master_closed(Q9) is False and card(q, Q9)["reasons"][0]["category"] == "real_decline"


def test_21_trusted_kapali_sales_reason_removed(ctx, snap):
    q = queue(ctx, snap)
    assert snap.master_closed(Q4) is True and any(a["entity_id"] == Q4 for a in ctx.sales_queue_candidates)
    assert card(q, Q4) is None
    assert q["diagnostics"]["closed_sales_reasons_removed"] == 2                # Q3 winback + Q4 winback


def test_22_kapali_collection_kept(ctx, snap):
    k = card(queue(ctx, snap), Q3)
    assert k["closed"] is True and [r["category"] for r in k["reasons"]] == ["collections"]
    assert k["draft"]["allowed_purposes"] == ["collection"] and k["draft"]["default_purpose"] == "collection"


def test_23_kapali_collection_plus_sales_collection_only(ctx, snap):
    assert any(a["category"] == "winback" and a["entity_id"] == Q3 for a in ctx.sales_queue_candidates)
    k = card(queue(ctx, snap), Q3)
    assert all(r["kind"] == "collection" for r in k["reasons"])
    assert k["queue_score"] == cand(ctx, "collections", Q3)["score"]


def test_24_duplicate_master_no_invented_closed(ctx, snap):
    k = card(queue(ctx, snap), Q7)
    assert snap.master_closed(Q7) is None and k["closed"] is None and k["master_status"] == "duplicate"
    assert [r["category"] for r in k["reasons"]] == ["winback"]
    assert "master_duplicate" in {w["code"] for w in k["warnings"]}
    assert k["draft"]["allowed_purposes"] == list(SQ.DRAFT_PURPOSES)


def test_cached_sales_column_not_used_for_kapali(ctx, snap):
    # The sales file's cached "AÇIK" (DQ-5) does not keep Q3's winback; the trusted master does the gating.
    assert Q3 in snap.customers and snap.master_closed(Q3) is True
    assert "winback" not in [r["category"] for r in card(queue(ctx, snap), Q3)["reasons"]]


def test_legacy_rep_fact_never_shown(ctx, snap):
    q = queue(ctx, snap)
    assert not any(f.startswith("Temsilci:") for k in q["items"] for r in k["reasons"] for f in r["facts"])


# =============================================================================================
# 61. state (25–31)
# =============================================================================================
def _seed(ctx):
    ST.upsert_actions(list(ctx.sales_queue_candidates))


def test_25_completed_hidden(ctx, snap):
    _seed(ctx)
    w = cand(ctx, "winback", Q2)
    ST.set_status(w["action_id"], "completed")
    q = SQ.build_queue(ctx, snap)
    assert card(q, Q2) is None and q["diagnostics"]["hidden_by_state"] == 1


def test_26_ignored_hidden(ctx, snap):
    _seed(ctx)
    ST.set_status(cand(ctx, "winback", Q8)["action_id"], "ignored")
    assert card(SQ.build_queue(ctx, snap), Q8) is None


def test_27_future_deferred_hidden_past_visible(ctx, snap):
    _seed(ctx)
    w = cand(ctx, "winback", Q2)
    future = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    ST.set_status(w["action_id"], "deferred", until=future)
    assert card(SQ.build_queue(ctx, snap), Q2) is None
    past = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    ST.set_status(w["action_id"], "deferred", until=past)
    k = card(SQ.build_queue(ctx, snap), Q2)
    assert k is not None and k["reasons"][0]["status"] == "deferred"


def test_28_reopened_returns(ctx, snap):
    _seed(ctx)
    w = cand(ctx, "winback", Q2)
    ST.set_status(w["action_id"], "completed")
    ST.set_status(w["action_id"], "open")
    assert card(SQ.build_queue(ctx, snap), Q2)["reasons"][0]["status"] == "open"


def test_29_hidden_reason_keeps_other_reasons(ctx, snap):
    _seed(ctx)
    before = card(SQ.build_queue(ctx, snap), Q1)
    first = before["primary_action_id"]
    ST.set_status(first, "completed")
    after = card(SQ.build_queue(ctx, snap), Q1)
    assert after is not None and first not in [r["action_id"] for r in after["reasons"]]
    assert len(after["reasons"]) == len(before["reasons"]) - 1
    assert after["queue_score"] == max(r["score"] for r in after["reasons"])        # recomputed
    assert after["collection_first"] is True                                     # the second account remains


def test_30_all_hidden_removes_customer(ctx, snap):
    _seed(ctx)
    for r in card(SQ.build_queue(ctx, snap), Q1)["reasons"]:
        ST.set_status(r["action_id"], "ignored")
    assert card(SQ.build_queue(ctx, snap), Q1) is None


@pytest.fixture()
def m5_client(synthetic_dir, synthetic_ctx):
    repo = ContextRepository(data_folder=synthetic_dir, context=synthetic_ctx)
    c360 = C360.Customer360Repository(synthetic_dir, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        yield cl, synthetic_ctx
    APP.app.dependency_overrides.clear()


def test_31_m5_and_m7_share_state(m5_client):
    cl, sctx = m5_client
    m5 = cl.get("/api/agent/actions").json()["actions"]
    q = cl.get("/api/sales-queue").json()
    shared = [r["action_id"] for k in q["items"] for r in k["reasons"]]
    common = [a["action_id"] for a in m5 if a["action_id"] in shared]
    assert common, "customer actions in the M5 top-15 keep the same ids in the queue"
    a1, a2 = common[0], common[-1]
    assert cl.post(f"/api/actions/{a1}/complete", headers={"X-StockAgent": "1"}).status_code == 200   # via M7
    m5_ids = [a["action_id"] for a in cl.get("/api/agent/actions").json()["actions"]]
    q_ids = [r["action_id"] for k in cl.get("/api/sales-queue").json()["items"] for r in k["reasons"]]
    assert a1 not in m5_ids and a1 not in q_ids
    ST.set_status(a2, "ignored")                                                    # as the M5 tab would
    q_ids = [r["action_id"] for k in cl.get("/api/sales-queue").json()["items"] for r in k["reasons"]]
    assert a2 not in q_ids
    cl.post(f"/api/actions/{a1}/reopen", headers={"X-StockAgent": "1"})
    assert a1 in [a["action_id"] for a in cl.get("/api/agent/actions").json()["actions"]]


def test_state_for_reason_outside_m5_top15(ctx, snap, data_dir):
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    c360 = C360.Customer360Repository(data_dir, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        aid = cand(ctx, "winback", Q2)["action_id"]
        h = {"X-StockAgent": "1"}
        assert cl.post(f"/api/actions/{aid}/complete", headers=h).status_code == 404   # never generated yet
        cl.get("/api/sales-queue")                                                     # upserts the page
        assert cl.post(f"/api/actions/{aid}/defer", json={"until": (dt.date.today() + dt.timedelta(days=2))
                                                         .isoformat()}, headers=h).status_code == 200
        assert card(cl.get("/api/sales-queue").json(), Q2) is None
        assert cl.post(f"/api/actions/{aid}/reopen", headers=h).status_code == 200
        assert card(cl.get("/api/sales-queue").json(), Q2) is not None
    APP.app.dependency_overrides.clear()
    assert ctx["actions"][0]["category"] == "overstock"                                # M5 list untouched


# =============================================================================================
# 62. API (32–39)
# =============================================================================================
class _NoAIProvider:
    name, model = "guard", "guard"

    def is_available(self):
        return True

    def __getattr__(self, item):
        raise AssertionError(f"AI provider touched: {item}")


@pytest.fixture()
def client(data_dir, ctx):
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    c360 = C360.Customer360Repository(data_dir, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        yield cl, repo, c360
    APP.app.dependency_overrides.clear()


def _db_counts():
    with sqlite3.connect(ST.DB_PATH) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("ai_cache", "ai_calls", "memory")}


def test_32_typed_response(client):
    cl, _, _ = client
    r = cl.get("/api/sales-queue")
    assert r.status_code == 200
    b = r.json()
    for k in ("as_of_data_date", "generated_at", "filter", "limit", "visible_customers", "returned", "truncated",
              "filter_counts", "filters", "diagnostics", "items", "ai_used", "customer360_state"):
        assert k in b
    assert b["ai_used"] is False and b["customer360_state"] == "ready" and b["sources_consistent"] is True
    it = b["items"][0]
    for k in ("customer_code", "display_name", "last_order_date", "closed", "queue_score", "priority",
              "collection_first", "primary_action_id", "reasons", "warnings", "draft"):
        assert k in it
    assert [f["key"] for f in b["filters"]] == ["all", "collection_first", "collections", "winback", "real_decline",
                                                "cross_sell", "fair_followup", "crm_followup"]   # M7-3/4/5
    assert [f["label"] for f in b["filters"]][:2] == ["Tümü", "Tahsilat önce"]
    S = APP.S
    S.SalesQueueResponse(**b)


def test_33_stable_order(client):
    cl, _, _ = client
    a = cl.get("/api/sales-queue").json()["items"]
    b = cl.get("/api/sales-queue").json()["items"]
    assert [k["customer_code"] for k in a] == [k["customer_code"] for k in b]
    keys = [(-k["queue_score"], not k["collection_first"], k["customer_code"]) for k in a]
    assert keys == sorted(keys)
    assert [k["customer_code"] for k in a] == [Q3, Q1, Q9, Q2, Q7, Q8]


def test_34_bounded_limit_and_filters(client):
    cl, _, _ = client
    b = cl.get("/api/sales-queue", params={"limit": 2}).json()
    assert b["returned"] == 2 and b["truncated"] is True and b["visible_customers"] == 6
    assert cl.get("/api/sales-queue", params={"limit": 999}).json()["limit"] == C.MAX_SALES_QUEUE_LIMIT
    assert cl.get("/api/sales-queue", params={"limit": 0}).json()["limit"] == 1
    assert cl.get("/api/sales-queue").json()["limit"] == C.MAX_DAILY_SALES_QUEUE
    cf = cl.get("/api/sales-queue", params={"filter": "collection_first"}).json()
    assert {k["customer_code"] for k in cf["items"]} == {Q1, Q3} and cf["filter"] == "collection_first"
    wb = cl.get("/api/sales-queue", params={"filter": "winback"}).json()
    assert {k["customer_code"] for k in wb["items"]} == {Q2, Q7, Q8}
    rd = cl.get("/api/sales-queue", params={"filter": "real_decline"}).json()
    assert {k["customer_code"] for k in rd["items"]} == {Q1, Q9}
    assert cl.get("/api/sales-queue", params={"filter": "bogus"}).json()["filter"] == "all"
    assert cf["filter_counts"] == {"all": 6, "collection_first": 2, "collections": 2, "winback": 3,
                                   "real_decline": 2, "cross_sell": 0, "fair_followup": 0, "crm_followup": 0}


def test_35_building_state(client, data_dir, ctx):
    cl, repo, _ = client
    gate = threading.Event()

    def slow(*a, **k):
        gate.wait(10)
        return C360.build_snapshot(*a, **k)
    c360 = C360.Customer360Repository(data_dir, repo, background=True, builder=slow)
    APP.app.dependency_overrides[APP.get_c360] = lambda: c360
    r = cl.get("/api/sales-queue")
    assert r.status_code == 503 and r.json()["detail"]["state"] == "building"
    assert r.json()["detail"]["message"] == "Müşteri verisi hazırlanıyor…"
    r2 = cl.get("/api/sales-queue")
    assert r2.status_code == 503
    gate.set()
    for _ in range(200):
        if c360.state()["state"] == "ready":
            break
        threading.Event().wait(0.05)
    assert c360.builds == 1                                                    # never a second build
    assert cl.get("/api/sales-queue").status_code == 200


def test_36_source_mismatch(client, data_dir, ctx):
    cl, repo, c360 = client
    c360.snapshot()
    other = CTX.AgentContext({**ctx, "generated_at": "2000-01-01T00:00:00"})
    other.sales_queue_candidates, other.sales_queue_rows = ctx.sales_queue_candidates, ctx.sales_queue_rows
    signature_holder = c360.snapshot().signature
    repo._ctx = other                                     # live context changes; snapshot still of the old one
    try:
        assert c360.signature_for(other) != signature_holder
        cl_get = cl.get("/api/sales-queue")
        # Either the repository noticed and is rebuilding, or the route refused the mismatch: never mixed.
        if cl_get.status_code == 200:
            assert c360.snapshot().signature == c360.signature_for(other)
        else:
            assert cl_get.status_code == 503 and cl_get.json()["detail"]["state"] in ("building", "error")
    finally:
        repo._ctx = ctx
    bad = {**ctx, "as_of_data_date": "2001-01-01"}
    r = None
    c2 = C360.Customer360Repository(data_dir, ContextRepository(data_folder=data_dir, context=bad), background=False)
    APP.app.dependency_overrides[APP.get_c360] = lambda: c2
    r = cl.get("/api/sales-queue")
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "source_date_mismatch"


def test_36b_route_refuses_signature_mismatch(client, ctx):
    cl, repo, c360 = client
    snap = c360.snapshot()

    class Frozen:
        def snapshot(self):
            return snap

        def signature_for(self, c):
            return ("different",)
    APP.app.dependency_overrides[APP.get_c360] = lambda: Frozen()
    r = cl.get("/api/sales-queue")
    assert r.status_code == 503 and r.json()["detail"]["state"] == "building"


def test_36c_context_without_candidates(client, ctx, data_dir):
    cl, repo, _ = client
    plain = dict(ctx)                                     # e.g. a context loaded from JSON
    repo2 = ContextRepository(data_folder=data_dir, context=plain)
    c2 = C360.Customer360Repository(data_dir, repo2, background=False)
    APP.app.dependency_overrides[APP.get_repo] = lambda: repo2
    APP.app.dependency_overrides[APP.get_c360] = lambda: c2
    r = cl.get("/api/sales-queue")
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "queue_candidates_unavailable"


def test_37_no_absolute_paths(client, data_dir):
    cl, _, _ = client
    for params in ({}, {"filter": "collection_first"}, {"limit": 50}):
        t = cl.get("/api/sales-queue", params=params).text
        assert data_dir not in t and "/tmp" not in t and "Traceback" not in t and ".xlsx" not in t


def test_38_no_ai_provider_call(client):
    cl, _, _ = client
    APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
    before = _db_counts()
    for params in ({}, {"filter": "winback"}, {"limit": 3}):
        assert cl.get("/api/sales-queue", params=params).status_code == 200
    assert _db_counts() == before and before["ai_calls"] == 0
    src = open(os.path.join(ROOT, "services", "sales_queue.py"), encoding="utf-8").read()
    for banned in ("services.ai", "agent", "openai", "anthropic", "evidence", "prompt"):
        assert banned not in src.lower().replace("# m7-2", "")


def test_39_safe_diagnostics(client, caplog):
    cl, _, _ = client
    with caplog.at_level(logging.DEBUG):
        d = cl.get("/api/sales-queue").json()["diagnostics"]
    assert set(d) == {"candidates", "candidate_reasons_by_category", "hidden_by_state", "visible_reasons",
                      "grouped_customers", "shared_collection_accounts_not_assigned", "unlinked_collection_accounts",
                      "unresolved_sales_customers", "closed_sales_reasons_removed", "other_categories_ignored"}
    for k, v in d.items():
        if k == "candidate_reasons_by_category":
            assert set(v) == set(SQ.QUEUE_CATEGORIES) and all(isinstance(x, int) for x in v.values())
        else:
            assert isinstance(v, int)
    blob = json.dumps(d)
    assert "910." not in blob and "Sentetik" not in blob
    assert "910." not in caplog.text and "Sentetik" not in caplog.text


def test_unresolved_sales_customer_counted(ctx, snap, monkeypatch):
    ghost = dict(cand(ctx, "winback", Q2), entity_id="910.77.0077", action_id="ghost")
    monkeypatch.setattr(ctx, "sales_queue_candidates", ctx.sales_queue_candidates + (ghost,))
    q = queue(ctx, snap)
    assert q["diagnostics"]["unresolved_sales_customers"] == 1 and card(q, "910.77.0077") is None


def test_draft_targets_resolve_through_existing_endpoint(client, ctx):
    cl, repo, _ = client
    k = card(cl.get("/api/sales-queue").json(), Q1)
    assert repo.customer(k["draft"]["collection_target"]) is not None
    assert repo.customer(k["draft"]["sales_target"]) is not None
    r = cl.post("/api/agent/draft-message", headers={"X-StockAgent": "1"},
                json={"customer_code": k["draft"]["collection_target"], "channel": "whatsapp",
                      "purpose": "collection"})
    assert r.status_code == 200 and r.json()["available"] is False          # AI disabled: no draft, no call


# =============================================================================================
# 63. refresh A → B
# =============================================================================================
def test_refresh_a_to_b_never_mixed(tmp_path):
    folder = str(tmp_path / "ab")
    Q.write_all(folder, sales_file="Sentetik A kuyruk satışlar.xlsx")
    # B: Q2 orders again in 2026 (its winback disappears) and a new invented customer appears.
    extra = [Q.F._line(9001, "2026-06-30", Q2, "Sentetik Kuyruk İki", Q2, "Sentetik Kuyruk İki", 90_000),
             Q.F._line(9002, "2026-07-15", "910.10.0099", "Sentetik Yeni", "910.10.0099", "Sentetik Yeni", 500)]
    Q.write_all(folder, sales_file="Sentetik B kuyruk satışlar.xlsx", extra_rows=extra)
    base = {"stock": os.path.join(folder, Q.STOCK_FILE), "aging": os.path.join(folder, Q.AGING_FILE)}
    ctx_a = CTX.build_context(folder, inflation_rate=0.4,
                              sources={**base, "sales": os.path.join(folder, "Sentetik A kuyruk satışlar.xlsx")})
    ctx_b = CTX.build_context(folder, inflation_rate=0.4,
                              sources={**base, "sales": os.path.join(folder, "Sentetik B kuyruk satışlar.xlsx")})
    repo = ContextRepository(data_folder=folder, context=ctx_a)
    c360 = C360.Customer360Repository(folder, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        qa = cl.get("/api/sales-queue").json()
        assert qa["as_of_data_date"] == "2026-06-30" and card(qa, Q2) is not None
        a_ids = {r["action_id"] for k in qa["items"] for r in k["reasons"]}
        repo.replace_context(ctx_b)                                   # controlled refresh swap
        qb = cl.get("/api/sales-queue").json()
    APP.app.dependency_overrides.clear()
    assert c360.builds == 2 and c360.snapshot().as_of == dt.date(2026, 7, 15)
    assert qb["as_of_data_date"] == "2026-07-15" and qb["generated_at"] == ctx_b["generated_at"]
    assert card(qb, Q2) is None                                       # A's winback dropped
    b_ids = {r["action_id"] for k in qb["items"] for r in k["reasons"]}
    assert b_ids <= {a["action_id"] for a in ctx_b.sales_queue_candidates}
    assert not (b_ids - {a["action_id"] for a in ctx_b.sales_queue_candidates})
    assert ctx_a.sales_queue_candidates is not ctx_b.sales_queue_candidates
    assert a_ids - b_ids                                              # old A candidates no longer served


def test_refresh_uses_staged_agent_context():
    src = open(os.path.join(ROOT, "services", "refresh.py"), encoding="utf-8").read()
    assert "CTX.build_context(" in src and "copy.deepcopy" not in src   # the staged object (with candidates) is installed


# =============================================================================================
# zero Excel reads, separation, UI block
# =============================================================================================
def test_no_excel_read_on_queue_or_state(client, ctx, monkeypatch):
    cl, _, c360 = client
    c360.snapshot()                                                    # build once (allowed)

    def boom(*a, **k):
        raise AssertionError("Excel read after build")
    for mod, name in ((pd, "read_excel"), (DB, "read_sales_raw"), (DB, "read_aging_raw")):
        monkeypatch.setattr(mod, name, boom)
    import openpyxl
    monkeypatch.setattr(openpyxl, "load_workbook", boom)
    q = cl.get("/api/sales-queue").json()
    aid = q["items"][0]["primary_action_id"]
    h = {"X-StockAgent": "1"}
    assert cl.post(f"/api/actions/{aid}/complete", headers=h).status_code == 200
    cl.get("/api/sales-queue", params={"filter": "winback"})
    assert cl.post(f"/api/actions/{aid}/reopen", headers=h).status_code == 200
    assert cl.get("/api/sales-queue").status_code == 200


def test_no_new_rules_or_categories():
    src = open(os.path.join(ROOT, "services", "sales_queue.py"), encoding="utf-8").read()
    for banned in ("penetration", "potential", "territory", "fair_invite_due",
                   "new_buyer", "growth", "salesperson", "Hedef", "hedef", "read_excel", "openpyxl", "THRESHOLD"):
        assert banned not in src
    assert set(SQ.QUEUE_CATEGORIES) == {"collections", "winback", "real_decline", "cross_sell",
                                        "fair_followup", "crm_followup"}                   # M7-3/4/5 add one each
    assert set(SQ.QUEUE_CATEGORIES) == {c.key for c in A.categories(A.SURFACE_SALES_QUEUE)}


@pytest.fixture(scope="module")
def built(data_dir, tmp_path_factory):
    d = tmp_path_factory.mktemp("sq_dash")
    a, b = str(d / "with.html"), str(d / "without.html")
    DB.build(data_dir, a, today=dt.date(2026, 7, 1))
    DB.build(data_dir, b, today=dt.date(2026, 7, 1), sq_panel=False)
    return open(a, encoding="utf-8").read(), open(b, encoding="utf-8").read()


SQ_BUTTON = '<button class="tab" data-t="sq">Günlük Satış</button>'


def _sq_block(h):
    return h[h.index("\n<!--SQ-TAB-START-->"): h.index("<!--SQ-TAB-END-->") + len("<!--SQ-TAB-END-->")]


def test_tab_before_c360_rest_byte_identical(built):
    import re
    with_sq, without = built
    assert with_sq.count(SQ_BUTTON) == 1 and with_sq.count('<div id="sq" class="panel"') == 1
    assert with_sq.replace(SQ_BUTTON, "", 1).replace(_sq_block(with_sq), "", 1) == without
    tabs = re.findall(r'<button class="tab" data-t="([a-z0-9]+)"', with_sq)
    assert tabs[-7:] == ["sq", "c360", "opp", "fair", "crm", "own", "ai"]
    assert "Bugün kimi aramalıyım?" in _sq_block(with_sq)


def test_tab_script_safe(built):
    block = _sq_block(built[0])
    js = block[block.index("<script>") + 8: block.rindex("</script>")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage",
                   "sessionStorage", "/analyze", "enrich"):
        assert banned not in js
    assert "/api/sales-queue" in js and "/api/agent/draft-message" in js and "textContent" in js
    assert js.count("/api/agent/") == 1                       # only the explicit draft call
    assert "Günlük Satış için panoyu http://127.0.0.1:8000/ üzerinden açın." in js
    assert "Müşteri verisi hazırlanıyor…" in js
    assert "Q.Q" not in block and "910." not in block          # no data baked into the page
