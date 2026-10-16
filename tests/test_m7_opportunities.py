"""M7-3 opportunity intelligence — action surfaces, per-category reservoir, cross-sell model, queue integration,
Customer 360 section, territory counts, API, refresh, separation.

Synthetic data only (tests/fixtures/opp_synth.py, queue_synth.py, synthetic_erp.py and small in-test frames).
No real data, no key, no network, no AI call. Numbers in test names follow the M7-3 instruction (§56–§62).
"""
from __future__ import annotations

import ast
import datetime as dt
import json
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
from services import evidence as EV
from services import opportunities as OP
from services import sales_queue as SQ
from services import store as ST
from services.entity import ContextRepository
from tests.fixtures import opp_synth as OS
from tests.fixtures import queue_synth as QS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
O = OS.O
H = {"X-StockAgent": "1"}


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return OS.write_all(str(tmp_path_factory.mktemp("opp_data")))


@pytest.fixture(scope="module")
def hedef_path(tmp_path_factory):
    return OS.write_hedef(str(tmp_path_factory.mktemp("opp_hedef") / OS.HEDEF_FILE))


@pytest.fixture(scope="module")
def ctx(data_dir):
    return CTX.build_context(data_dir, inflation_rate=0.40)


@pytest.fixture(scope="module")
def snap(data_dir, ctx):
    return C360.build_snapshot(data_dir, ctx)


@pytest.fixture(scope="module")
def snap_hedef(data_dir, ctx, hedef_path):
    return C360.build_snapshot(data_dir, ctx, hedef_path=hedef_path)


@pytest.fixture(scope="module")
def model(ctx):
    return ctx.opportunities


def queue(ctx, snap, **kw):
    kw.setdefault("upsert", False)
    kw.setdefault("statuses", {})
    return SQ.build_queue(ctx, snap, **kw)


def card(q, code):
    hits = [k for k in q["items"] if k["customer_code"] == code]
    return hits[0] if hits else None


def make_client(data_dir, ctx, hedef=None):
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    c360 = C360.Customer360Repository(data_dir, repo, hedef_path=hedef, background=False)
    return repo, c360


@pytest.fixture()
def client(data_dir, ctx, hedef_path):
    repo, c360 = make_client(data_dir, ctx, hedef_path)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        yield cl, repo, c360
    APP.app.dependency_overrides.clear()


def frame(rows):
    """Minimal normalized sales frame: (customer, ISO date, group[, amount])."""
    return pd.DataFrame([{"_cust": c, "_date": pd.Timestamp(d), "anagrup": g, "_cname": f"Sentetik {c}",
                          "Nettutar": (r[0] if r else 1.0)} for c, d, g, *r in rows])


AS_OF = dt.date(2026, 6, 30)
TH = OP.Thresholds(recent_days=365, min_source_buyers=3, min_target_buyers=3, min_pair_buyers=2,
                   min_confidence=0.5, min_lift=1.1, max_targets=3)


def small_rows():
    """10 active customers. P: c1–c6 · Q: c1–c4, c9 · R: c6–c10.  P→Q pair 4 (0.667, lift 1.333);
    Q→P pair 4 (0.8, lift 1.333); P→R pair 1 (support-rejected). c6 bought Q in 2019 (history);
    c11 bought P only in 2024 and R recently."""
    rows = []
    for i in range(1, 7):
        rows.append((f"c{i}", "2026-03-01", "P"))
    for i in (1, 2, 3, 4, 9):
        rows.append((f"c{i}", "2026-03-02", "Q"))
    for i in range(6, 11):
        rows.append((f"c{i}", "2026-03-03", "R"))
    rows += [("c6", "2019-05-05", "Q"), ("c11", "2024-01-01", "P"), ("c11", "2026-01-01", "R")]
    return rows


@pytest.fixture(scope="module")
def small():
    return OP.build_cross_sell(frame(small_rows()), AS_OF, TH)


def pair(m, a, b):
    hits = [p for p in m.pairs if p.source == a and p.target == b]
    return hits[0] if hits else None


def _old_produce_all(inputs, limit=None):
    """The pre-M7-3 produce_all (verbatim algorithm) over the four pre-M7-3 sources."""
    acts = []
    for s in [x for x in A._SOURCES if x.name not in ("cross_sell_anagrup", "fair_campaign")]:
        for a in s.produce(inputs) or []:
            a.setdefault("source", s.name)
            acts.append(A.validate(a))
    acts.sort(key=lambda a: -a["score"])
    return acts[: (limit or C.MAX_DAILY_ACTIONS)]


def _capture(monkeypatch, folder):
    seen = {}
    real = CTX.build_action_candidates

    def spy(*a, **k):
        seen["a"], seen["k"] = a, k
        return real(*a, **k)
    monkeypatch.setattr(CTX, "build_action_candidates", spy)
    built = CTX.build_context(folder, inflation_rate=0.40)
    c, coll, over, lap, shr, infl = seen["a"]
    return built, {"ctx": c, "coll_scored": coll, "over_cand": over, "lap": lap, "shrink_real_neg": shr,
                   "infl": infl, "cross_sell": seen["k"].get("cross_sell")}


# =============================================================================================
# 56. surfaces + reservoir (1–7)
# =============================================================================================
@pytest.mark.parametrize("which", ["opp", "m5", "queue"])
def test_01_m5_top15_unchanged(which, monkeypatch, data_dir, synthetic_dir, tmp_path):
    folder = {"opp": data_dir, "m5": synthetic_dir, "queue": None}[which] or QS.write_all(str(tmp_path / "q"))
    built, inputs = _capture(monkeypatch, folder)
    old = _old_produce_all(inputs)                         # the cross-sell model is IN the inputs
    assert built["actions"] == old                          # ids, scores, facts, order, priorities
    assert not any(a["category"] == "cross_sell" for a in built["actions"])
    assert A.produce_all(inputs) == old
    if which == "opp":
        assert inputs["cross_sell"] is not None and inputs["cross_sell"].targets


def test_01b_categories_endpoint_and_ai_cache_keys_unchanged(client, ctx):
    cl, _, _ = client
    cats = cl.get("/api/agent/categories").json()
    assert [c["key"] for c in cats] == ["collections", "overstock", "winback", "real_decline"]
    assert all(set(c) == {"key", "label_tr", "color", "entity_type", "source", "draft_purpose"} for c in cats)
    ids = [a["action_id"] for a in cl.get("/api/agent/actions").json()["actions"]]
    assert ids == [a["action_id"] for a in ctx["actions"] if not ST.is_hidden(ST.get_statuses().get(
        a["action_id"], {"status": "open"}))]
    assert A.category("cross_sell").surfaces == (A.SURFACE_SALES_QUEUE,)
    assert A.category("overstock").surfaces == (A.SURFACE_ACTION_CENTER,)
    for k in ("collections", "winback", "real_decline"):
        assert set(A.category(k).surfaces) == {A.SURFACE_ACTION_CENTER, A.SURFACE_SALES_QUEUE}


def test_02_m7_2_reasons_keep_ids_and_scores(monkeypatch, data_dir):
    built, inputs = _capture(monkeypatch, data_dir)
    old = {a["action_id"]: a for a in A.produce_candidates({**inputs, "cross_sell": None})}
    for a in built.sales_queue_candidates:
        if a["category"] != "cross_sell":
            assert old[a["action_id"]] == a


def _fake(cat, n, top):
    return [{"category": cat, "entity_type": "customer", "entity_id": f"{cat}-{i}", "score": top - i,
             "action_id": f"{cat}{i}"} for i in range(n)]


def test_03_per_category_reservoir_prevents_starvation():
    cands = sorted(_fake("real_decline", 10, 90) + _fake("winback", 5, 40) + _fake("cross_sell", 5, 60),
                   key=lambda a: -a["score"])
    res = CTX.sales_queue_reservoir(cands, per_category=3)
    cats = [a["category"] for a in res]
    assert cats.count("real_decline") == 3 and cats.count("winback") == 3 and cats.count("cross_sell") == 3
    assert [a["score"] for a in res] == sorted((a["score"] for a in res), reverse=True)   # global order kept
    old_global = [a for a in cands][:3]
    assert all(a["category"] == "real_decline" for a in old_global)    # a single global cap would starve the rest


def test_04_hidden_high_reason_lets_lower_one_surface(ctx, snap):
    q = queue(ctx, snap, flt="cross_sell", limit=1)
    top = q["items"][0]
    statuses = {r["action_id"]: {"status": "ignored"} for r in top["reasons"]}
    q2 = queue(ctx, snap, flt="cross_sell", limit=1, statuses=statuses)
    assert q2["items"][0]["customer_code"] != top["customer_code"]
    assert q2["visible_customers"] == q["visible_customers"] - 1


def test_05_pool_bounded(monkeypatch, data_dir):
    monkeypatch.setattr(C, "MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY", 3)
    c = CTX.build_context(data_dir, inflation_rate=0.40)
    per = {}
    for a in c.sales_queue_candidates:
        per[a["category"]] = per.get(a["category"], 0) + 1
    assert max(per.values()) <= 3 and len(c.sales_queue_candidates) <= 3 * len(SQ.QUEUE_CATEGORIES)
    assert isinstance(c.sales_queue_candidates, tuple)


def test_05b_legacy_env_variable_is_per_category(monkeypatch):
    import importlib
    monkeypatch.delenv("MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY", raising=False)
    monkeypatch.setenv("MAX_SALES_QUEUE_CANDIDATES", "7")
    try:
        importlib.reload(C)
        assert C.MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY == 7
        monkeypatch.setenv("MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY", "9")
        importlib.reload(C)
        assert C.MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY == 9
    finally:
        monkeypatch.delenv("MAX_SALES_QUEUE_CANDIDATES", raising=False)
        monkeypatch.delenv("MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY", raising=False)
        importlib.reload(C)
    assert C.MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY == 200


def test_06_cross_sell_has_its_own_reservoir(monkeypatch, data_dir, model):
    monkeypatch.setattr(C, "MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY", 5)
    c = CTX.build_context(data_dir, inflation_rate=0.40)
    cs = [a for a in c.sales_queue_candidates if a["category"] == "cross_sell"]
    assert len(cs) == 5 and sum(len(v) for v in model.targets.values()) > 5
    assert [a["score"] for a in cs] == sorted((a["score"] for a in cs), reverse=True)


def test_07_public_context_has_no_private_pool_or_model(ctx, data_dir, tmp_path):
    ids = [a["action_id"] for a in ctx.sales_queue_candidates if a["category"] == "cross_sell"]
    blob = json.dumps(ctx, ensure_ascii=False, default=str)
    for bad in ("cross_sell", "opportunit", "sales_queue", "SENT-MÜREKKEP", OS.X):
        assert bad not in blob
    assert not any(i in blob for i in ids)
    out, written = CTX.write_context(data_dir, str(tmp_path / "agent_context.json"), inflation_rate=0.40)
    txt = open(out, encoding="utf-8").read()
    assert "cross_sell" not in txt and "opportunit" not in txt
    pkt = json.dumps(EV.actions_packet(ctx["actions"], ctx), ensure_ascii=False, default=str)
    assert "cross_sell" not in pkt and not any(i in pkt for i in ids)
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        body = cl.get("/api/context").text + cl.get("/api/agent/actions").text
    APP.app.dependency_overrides.clear()
    assert "cross_sell" not in body and not any(i in body for i in ids)


# =============================================================================================
# 57. association model (8–18)
# =============================================================================================
def test_08_to_13_counts_confidence_prevalence_lift(small):
    assert small.status == "ok" and small.active_customers == 11              # c1–c10 + c11 (recent R)
    pq, qp = pair(small, "P", "Q"), pair(small, "Q", "P")
    assert (pq.source_buyers, pq.target_buyers, pq.pair_buyers) == (6, 5, 4)    # 8, 9, 10
    assert pq.confidence == pytest.approx(4 / 6)                                # 11
    assert pq.target_prevalence == pytest.approx(5 / 11)                        # 12
    assert pq.lift == pytest.approx((4 / 6) / (5 / 11))                         # 13
    assert (qp.source_buyers, qp.target_buyers, qp.pair_buyers) == (5, 6, 4)
    assert qp.confidence == pytest.approx(0.8) and qp.lift == pytest.approx(0.8 / (6 / 11))


def test_14_support_threshold():
    m = OP.build_cross_sell(frame(small_rows()), AS_OF, OP.Thresholds(365, 3, 3, 5, 0.5, 1.1, 3))
    assert pair(m, "P", "Q") is None and pair(m, "Q", "P") is None


def test_15_confidence_threshold():
    m = OP.build_cross_sell(frame(small_rows()), AS_OF, OP.Thresholds(365, 3, 3, 2, 0.7, 1.1, 3))
    assert pair(m, "P", "Q") is None and pair(m, "Q", "P") is not None


def test_16_lift_threshold():
    m = OP.build_cross_sell(frame(small_rows()), AS_OF, OP.Thresholds(365, 3, 3, 2, 0.5, 1.5, 3))
    assert m.pairs == ()


def test_17_source_and_target_buyer_thresholds():
    m = OP.build_cross_sell(frame(small_rows()), AS_OF, OP.Thresholds(365, 6, 3, 2, 0.5, 1.1, 3))
    assert pair(m, "Q", "P") is None and pair(m, "P", "Q") is not None       # Q has 5 source buyers
    m = OP.build_cross_sell(frame(small_rows()), AS_OF, OP.Thresholds(365, 3, 6, 2, 0.5, 1.1, 3))
    assert pair(m, "P", "Q") is None and pair(m, "Q", "P") is not None       # Q has 5 target buyers


def test_18_pair_below_support_rejected(small, model):
    assert pair(small, "P", "R") is None and pair(small, "R", "P") is None
    assert pair(model, OS.B, OS.T) is None and pair(model, OS.A, OS.T) is None   # 4 / 0 < 10
    assert pair(model, OS.A, OS.D) is None                                       # lift 0.80 < 1.20
    dt_ = pair(model, OS.D, OS.T)
    assert dt_ is not None and dt_.pair_buyers == C.CROSS_SELL_MIN_PAIR_BUYERS   # exactly at the guard


def test_fixture_counts_match_docstring(model):
    ab, ax, bx = pair(model, OS.A, OS.B), pair(model, OS.A, OS.X), pair(model, OS.B, OS.X)
    assert model.active_customers == 61
    assert (ab.source_buyers, ab.target_buyers, ab.pair_buyers) == (40, 28, 24) and ab.confidence == 0.6
    assert (ax.pair_buyers, round(ax.confidence, 2)) == (22, 0.55) and round(bx.confidence, 3) == 0.786


def test_no_dynamic_dimension_switch():
    f = frame(small_rows()).rename(columns={"anagrup": "kategori"})
    m = OP.build_cross_sell(f, AS_OF, TH)
    assert m.status == "unavailable" and m.reason == "dimension_missing" and m.targets == {}


# =============================================================================================
# 58. customer candidates (19–26)
# =============================================================================================
def test_19_recent_source_buyer_qualifies(small, model):
    assert [t.target for t in small.for_customer("c5")] == ["Q"]
    assert [t.target for t in small.for_customer("c9")] == ["P"]
    assert [t.target for t in model.for_customer(O[26])] == [OS.B, OS.X]


def test_20_historically_bought_target_rejected(small, model):
    assert small.for_customer("c6") == ()                      # bought Q in 2019
    assert [t.target for t in model.for_customer(O[25])] == [OS.X]   # bought B in 2023
    assert model.diagnostics["targets_rejected_already_purchased"] > 0


def test_21_source_only_outside_window_rejected(small, model):
    assert small.for_customer("c11") == ()                     # P only in 2024
    assert O[70] not in model.targets                          # not in the cohort
    assert all(t.source != OS.A for t in model.for_customer(O[61]))   # A only in 2025-01


def test_22_several_targets(model):
    assert len(model.for_customer(O[26])) == 2 and len(model.for_customer(O[41])) == 3


def test_23_24_same_target_deduplicated_strongest_source(model):
    ts = model.for_customer(O[23])
    assert [t.target for t in ts] == [OS.X]                    # one X opportunity, not one per source
    assert ts[0].source == OS.B and ts[0].supporting_sources == (OS.A,)
    assert ts[0].confidence == pytest.approx(22 / 28)


def test_25_at_most_three_targets(data_dir, model):
    assert max(len(v) for v in model.targets.values()) <= 3
    sdf = DB.load_sales(os.path.join(data_dir, OS.SALES_FILE))
    th = OP.Thresholds.from_config()
    m1 = OP.build_cross_sell(sdf, OS.AS_OF, OP.Thresholds(**{**th.__dict__, "max_targets": 1}))
    assert max(len(v) for v in m1.targets.values()) == 1
    assert all(m1.for_customer(c)[0] == model.for_customer(c)[0] for c in m1.targets)   # the strongest one kept


def test_26_stable_ordering_and_ids(data_dir, model):
    sdf = DB.load_sales(os.path.join(data_dir, OS.SALES_FILE))
    shuffled = sdf.sample(frac=1.0, random_state=7)
    m2 = OP.build_cross_sell(shuffled, OS.AS_OF)
    assert m2.targets == model.targets and m2.pairs == model.pairs
    for ts in model.targets.values():
        keys = [(-t.confidence, -t.lift, -t.pair_buyers, t.target) for t in ts]
        assert keys == sorted(keys)
    from services import action_sources as AS
    ids1 = [a["action_id"] for a in AS.produce_cross_sell({"cross_sell": model})]
    ids2 = [a["action_id"] for a in AS.produce_cross_sell({"cross_sell": m2})]
    assert ids1 == ids2 and len(ids1) == len(set(ids1))


def test_window_uses_data_date_not_today():
    m = OP.build_cross_sell(frame(small_rows()), AS_OF, TH)
    assert m.window_start == "2025-07-01" and m.as_of == "2026-06-30" and m.active_customers == 11
    later = OP.build_cross_sell(frame(small_rows()), dt.date(2027, 6, 30), TH)
    assert later.status == "unavailable" or later.active_customers == 0     # nothing within a later window
    names = _code_names(os.path.join(ROOT, "services", "opportunities.py"))
    assert "today" not in names and "now" not in names


# =============================================================================================
# 59. score (27–32)
# =============================================================================================
def test_27_score_is_capped_confidence(model):
    assert OP.cross_sell_score(0.6) == 60.0 and OP.cross_sell_score(0.917) == 69.0 and OP.cross_sell_score(0.3) == 30.0
    for ts in model.targets.values():
        for t in ts:
            assert t.score == min(69, round(t.confidence * 100, 1))


def test_28_29_never_high_or_critical(ctx):
    cs = [a for a in ctx.sales_queue_candidates if a["category"] == "cross_sell"]
    assert cs and all(a["priority"] in ("low", "medium") for a in cs) and max(a["score"] for a in cs) == 69.0
    assert all(a["confidence"] == "medium" and a["requires_approval"] for a in cs)


def test_30_no_amount_enters_score(data_dir, model):
    sdf = DB.load_sales(os.path.join(data_dir, OS.SALES_FILE))
    sdf["Nettutar"] = sdf["Nettutar"] * 1000 + 7
    m2 = OP.build_cross_sell(sdf, OS.AS_OF)
    assert m2.targets == model.targets


def _code_names(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            names.add(n.attr)
        elif isinstance(n, ast.Name):
            names.add(n.id)
    return names


def test_31_32_no_hedef_or_stale_capacity_in_score_or_model():
    p = os.path.join(ROOT, "services", "opportunities.py")
    names = _code_names(p)
    for banned in ("historical_weekly_stamp_capacity", "historical_risk_limit", "historical_unsecured_credit_limit",
                   "hedef_rep", "customer_rep", "visit_count", "discount_tier", "Nettutar", "_prof", "margin"):
        assert banned not in names
    src = open(p, encoding="utf-8").read()
    build = src[src.index("def build_cross_sell"): src.index("# Turkish formatting")]
    assert "hedef" not in build.lower() and "Nettutar" not in build
    from services import action_sources as AS
    import inspect
    import textwrap
    fn = ast.parse(textwrap.dedent(inspect.getsource(AS.produce_cross_sell))).body[0]
    used = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)} | \
        {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert not {x for x in used if "hedef" in x.lower() or "money" in x.lower() or "capacity" in x.lower()}


# =============================================================================================
# 60. queue sequence (33–40)
# =============================================================================================
def test_33_cross_sell_only_customer(ctx, snap):
    k = card(queue(ctx, snap, limit=50, flt="cross_sell"), O[23])
    assert [r["category"] for r in k["reasons"]] == ["cross_sell"]
    assert k["draft"]["default_purpose"] == "sales_followup" and k["priority"] == "medium"
    assert k["reasons"][0]["confidence"] == "medium" and k["reasons"][0]["category_label"] == "Çapraz Satış"


def test_34_collection_plus_cross_sell(ctx, snap):
    k = card(queue(ctx, snap, limit=50), O[26])
    assert [r["category"] for r in k["reasons"]] == ["collections", "cross_sell", "cross_sell"]
    assert k["collection_first"] and k["primary_action_id"] == k["reasons"][0]["action_id"]
    assert k["draft"]["default_purpose"] == "collection"


def test_35_winback_before_cross_sell(ctx, snap):
    k = card(queue(ctx, snap, limit=50, flt="winback"), O[27])
    cats = [(r["category"], r["score"]) for r in k["reasons"]]
    assert [c for c, _ in cats] == ["winback", "cross_sell", "cross_sell"]
    assert cats[0][1] < cats[1][1]                              # sequence, not score
    assert k["primary_action_id"] == k["reasons"][0]["action_id"] and k["draft"]["default_purpose"] == "winback"
    assert k["queue_score"] == max(s for _, s in cats)          # 37: still the max


def test_36_real_decline_plus_cross_sell_by_score(ctx, snap):
    k = card(queue(ctx, snap, limit=50, flt="real_decline"), O[28])
    scores = [r["score"] for r in k["reasons"]]
    assert {r["category"] for r in k["reasons"]} == {"real_decline", "cross_sell"}
    assert scores == sorted(scores, reverse=True)


def test_37_queue_score_is_max(ctx, snap):
    for k in queue(ctx, snap, limit=50)["items"]:
        assert k["queue_score"] == max(r["score"] for r in k["reasons"])
        assert k["priority"] == C.band(k["queue_score"])


def test_38_cross_sell_filter(ctx, snap):
    q = queue(ctx, snap, flt="cross_sell", limit=50)
    want = {str(a["entity_id"]) for a in ctx.sales_queue_candidates if a["category"] == "cross_sell"
            and snap.master_closed(str(a["entity_id"])) is not True}
    assert {k["customer_code"] for k in q["items"]} <= want and q["filter_counts"]["cross_sell"] == len(want)
    assert q["visible_customers"] == len(want) > C.MAX_DAILY_SALES_QUEUE   # beyond the global first 20
    assert all(any(r["category"] == "cross_sell" for r in k["reasons"]) for k in q["items"])
    assert {f["key"]: f["label"] for f in q["filters"]}["cross_sell"] == "Çapraz Satış"


def test_39_kapali_removes_cross_sell(ctx, snap, model):
    assert model.for_customer(O[30]) and snap.master_closed(O[30]) is True
    q = queue(ctx, snap, limit=50, flt="cross_sell")
    assert card(q, O[30]) is None and q["diagnostics"]["closed_sales_reasons_removed"] == 2


def test_40_state_complete_defer_dismiss_reopen(client, ctx):
    cl, _, _ = client
    q = cl.get("/api/sales-queue", params={"limit": 50}).json()
    k26 = card(q, O[26])
    cs = [r["action_id"] for r in k26["reasons"] if r["category"] == "cross_sell"]
    assert cl.post(f"/api/actions/{cs[0]}/complete", headers=H).status_code == 200
    until = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    assert cl.post(f"/api/actions/{cs[1]}/defer", json={"until": until}, headers=H).status_code == 200
    k = card(cl.get("/api/sales-queue", params={"limit": 50}).json(), O[26])
    assert k is not None and [r["category"] for r in k["reasons"]] == ["collections"]      # card remains
    q23 = cl.get("/api/sales-queue", params={"limit": 50, "filter": "cross_sell"}).json()
    only = card(q23, O[23])["reasons"][0]["action_id"]
    assert cl.post(f"/api/actions/{only}/dismiss", headers=H).status_code == 200
    assert card(cl.get("/api/sales-queue", params={"limit": 50, "filter": "cross_sell"}).json(), O[23]) is None
    for aid in (cs[0], cs[1], only):
        assert cl.post(f"/api/actions/{aid}/reopen", headers=H).status_code == 200
    k = card(cl.get("/api/sales-queue", params={"limit": 50}).json(), O[26])
    assert [r["category"] for r in k["reasons"]] == ["collections", "cross_sell", "cross_sell"]
    with sqlite3.connect(ST.DB_PATH) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"actions", "action_history", "memory", "ai_cache", "ai_calls"} or "actions" in tables
    assert ST.get_action(only)["category"] == "cross_sell"                       # the one shared state table


def test_cross_sell_draft_resolves_without_target_info(client, ctx):
    cl, repo, _ = client
    k = card(cl.get("/api/sales-queue", params={"limit": 50, "filter": "cross_sell"}).json(), O[23])
    facts = repo.customer(k["draft"]["sales_target"])
    assert facts is not None and set(facts) == {"customer_code", "name"}          # no group / cross-sell facts
    r = cl.post("/api/agent/draft-message", headers=H,
                json={"customer_code": k["draft"]["sales_target"], "channel": "whatsapp", "purpose": "sales_followup"})
    assert r.status_code == 200 and r.json()["available"] is False                # AI disabled: no call


# =============================================================================================
# 61. Customer 360 (41–47)
# =============================================================================================
def test_41_top_opportunities_shown(client, model):
    cl, _, _ = client
    o = cl.get(f"/api/customer360/{O[41]}").json()["opportunities"]
    assert o["status"] == "available" and len(o["items"]) == 3 and o["label"] == "Ürün kapsam fırsatı"
    assert [i["target_group"] for i in o["items"]] == [t.target for t in model.for_customer(O[41])]
    it = o["items"][0]
    assert set(it) >= {"target_group", "source_group", "confidence", "lift", "pair_buyers", "never_purchased"}


def test_42_never_purchased_claim_correct(client, data_dir, model):
    cl, _, _ = client
    sdf = DB.load_sales(os.path.join(data_dir, OS.SALES_FILE))
    for code in (O[23], O[25], O[26], O[41], O[45], O[61]):
        bought = set(sdf.loc[sdf["_cust"] == code, "anagrup"].dropna().astype(str))
        for it in cl.get(f"/api/customer360/{code}").json()["opportunities"]["items"]:
            assert it["never_purchased"] is True and it["target_group"] not in bought
            assert it["source_group"] in bought


def test_43_44_no_sku_no_penetration(client):
    cl, _, _ = client
    o = cl.get(f"/api/customer360/{O[41]}").json()["opportunities"]
    txt = json.dumps(o, ensure_ascii=False).lower()
    for bad in ("sku-", "sto_kod", "penetra", "potansiyel", "ciro beklentisi", "satın alma olasılığı:"):
        assert bad not in txt
    assert "sku" not in {k.lower() for it in o["items"] for k in it}
    assert any("stok ayrıca kontrol" in n for n in o["notes"])


def test_45_46_hedef_never_changes_cross_sell(data_dir, ctx, snap, snap_hedef, tmp_path, model):
    a = C360.customer_detail(snap, O[41], ctx=ctx, opportunities=model)["opportunities"]
    b = C360.customer_detail(snap_hedef, O[41], ctx=ctx, opportunities=model)["opportunities"]
    assert a == b                                               # huge stale capacity / Risk / Kredi: no effect
    bad = tmp_path / "bozuk hedef.xlsx"
    bad.write_bytes(b"not an excel file")
    snap_bad = C360.build_snapshot(data_dir, ctx, hedef_path=str(bad))
    assert snap_bad.hedef_status == "failed"
    c = C360.customer_detail(snap_bad, O[41], ctx=ctx, opportunities=model)["opportunities"]
    assert c == a and c["status"] == "available"


def test_c360_kapali_note_and_mismatch_unavailable(client, data_dir, ctx):
    cl, repo, c360 = client
    o = cl.get(f"/api/customer360/{O[30]}").json()["opportunities"]
    assert o["closed"] is True and o["items"] and any("KAPALI" in n for n in o["notes"])
    assert C360.customer_detail(c360.snapshot(), O[41], ctx=ctx, opportunities=None)["opportunities"]["status"] \
        == "unavailable"
    assert cl.get(f"/api/customer360/{O[70]}").json()["opportunities"]["status"] == "none"


def test_47_zero_excel_reads_after_build(client, monkeypatch):
    cl, _, c360 = client
    c360.snapshot()

    def boom(*a, **k):
        raise AssertionError("Excel read after build")
    import openpyxl
    for mod, name in ((pd, "read_excel"), (DB, "read_sales_raw"), (DB, "read_aging_raw"), (openpyxl, "load_workbook")):
        monkeypatch.setattr(mod, name, boom)
    assert cl.get(f"/api/customer360/{O[41]}").status_code == 200
    assert cl.get("/api/sales-queue", params={"filter": "cross_sell"}).status_code == 200
    assert cl.get("/api/opportunities/summary").status_code == 200
    for dim in ("city", "district", "sector"):
        assert cl.get("/api/opportunities/territories", params={"dimension": dim}).status_code == 200


# =============================================================================================
# 62. territory (48–57)
# =============================================================================================
def test_48_hedef_not_configured(data_dir, ctx, snap, model):
    t = OP.territories(snap, model, "city")
    assert t["status"] == "not_configured" and t["rows"] == []
    assert t["message"] == "Bölge / sektör görünümü için Hedef dosyası yapılandırılmadı."
    repo, c360 = make_client(data_dir, ctx, None)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        r = cl.get("/api/opportunities/territories").json()
        s = cl.get("/api/opportunities/summary").json()
    APP.app.dependency_overrides.clear()
    assert r["status"] == "not_configured" and s["status"] == "ok" and s["customers_with_opportunities"] > 0


def test_49_hedef_failed(data_dir, ctx, tmp_path, model):
    bad = tmp_path / "bozuk hedef.xlsx"
    bad.write_bytes(b"not an excel file")
    snap_bad = C360.build_snapshot(data_dir, ctx, hedef_path=str(bad))
    t = OP.territories(snap_bad, model, "district")
    assert t["status"] == "failed" and t["rows"] == [] and t["coverage"] is None
    assert str(tmp_path) not in json.dumps(t)
    assert card(queue(ctx, snap_bad, flt="cross_sell", limit=50), O[23]) is not None   # cross-sell unaffected


def rows_by_label(t):
    return {r["label"]: r for r in t["rows"]}


def test_50_city(snap_hedef, model):
    t = OP.territories(snap_hedef, model, "city")
    r = rows_by_label(t)
    assert r["Sentetik İl Bir"] == {"label": "Sentetik İl Bir", "matched_customers": 14, "active_customers": 14,
                                    "current_year_customers": 13, "cross_sell_customers": 14,
                                    "cross_sell_targets": 24}
    assert r[OS.XSS_CITY]["matched_customers"] == 6 and r[OS.XSS_CITY]["cross_sell_targets"] == 18
    assert t["coverage"] == {"hedef_accounts": 21, "matched_customers": 21, "missing_value_customers": 1,
                             "cross_sell_model": "ok"}


def test_51_district(snap_hedef, model):
    r = rows_by_label(OP.territories(snap_hedef, model, "district"))
    assert r["Sentetik İlçe 1"]["matched_customers"] == 14 and r["Sentetik İlçe 2"]["matched_customers"] == 6


def test_52_sector(snap_hedef, model):
    t = OP.territories(snap_hedef, model, "sector")
    assert [(x["label"], x["matched_customers"], x["cross_sell_targets"]) for x in t["rows"]] == \
        [("SK-1", 10, 20), ("SK-2", 10, 22)]                     # tie → label order


def test_53_unverified_and_note(snap_hedef, model):
    t = OP.territories(snap_hedef, model, "city")
    assert t["confidence"] == "unverified"
    assert t["note"] == ("Hedef bölge/sektör alanları doğrulanmamış kaynak bilgisidir; bu tablo satış performansı "
                         "sıralaması değildir.")


def test_54_no_salesperson_ranking(client):
    cl, _, _ = client
    for dim in ("city", "district", "sector"):
        r = cl.get("/api/opportunities/territories", params={"dimension": dim}).json()
        assert all(set(x) == {"label", "matched_customers", "active_customers", "current_year_customers",
                              "cross_sell_customers", "cross_sell_targets"} for x in r["rows"])
        assert "REP" not in json.dumps(r)
    assert cl.get("/api/opportunities/territories", params={"dimension": "rep"}).status_code == 422
    names = _code_names(os.path.join(ROOT, "services", "opportunities.py"))
    assert not ({"hedef_rep", "customer_rep", "_rep", "satici", "Temsilci"} & names)


def test_55_no_stale_capacity_ranking(data_dir, ctx, tmp_path, snap_hedef, model):
    from services.importers import hedef as HD
    headers = [f.header.name for f in HD.SPECS[HD.SchemaVersion.CURRENT_2026].fields]
    df = pd.read_excel(OS.write_hedef(str(tmp_path / "a.xlsx")))
    df["Haftalık Kaşe Adedi"] = [999_999 if i % 2 else 1 for i in range(len(df))]
    df["Risk"], df["Kredi"] = 5_000_000, 7_000_000
    p = str(tmp_path / OS.HEDEF_FILE)
    df[headers].to_excel(p, index=False)
    s2 = C360.build_snapshot(data_dir, ctx, hedef_path=p)
    for dim in ("city", "district", "sector"):
        a, b = OP.territories(snap_hedef, model, dim), OP.territories(s2, model, dim)
        assert a["rows"] == b["rows"]


def test_56_deterministic_ordering(snap_hedef, model):
    t = OP.territories(snap_hedef, model, "city")
    keys = [(-r["cross_sell_customers"], -r["matched_customers"], r["label"]) for r in t["rows"]]
    assert keys == sorted(keys) and OP.territories(snap_hedef, model, "city") == t


def test_57_bounded(client):
    cl, _, _ = client
    r = cl.get("/api/opportunities/territories", params={"dimension": "city", "limit": 1}).json()
    assert len(r["rows"]) == 1 and r["truncated"] is True and r["total_rows"] == 2
    assert cl.get("/api/opportunities/territories", params={"limit": 99999}).json()["limit"] == C.MAX_TERRITORY_ROWS
    s = cl.get("/api/opportunities/summary", params={"limit": 3}).json()
    assert len(s["pairs"]) == 3 and s["pairs_truncated"] is True
    assert cl.get("/api/opportunities/summary", params={"limit": 999}).json()["pair_limit"] == C.MAX_OPPORTUNITY_PAIRS


# =============================================================================================
# API / AI / privacy / refresh
# =============================================================================================
def test_summary_typed_and_safe(client, ctx):
    cl, _, _ = client
    r = cl.get("/api/opportunities/summary")
    assert r.status_code == 200
    s = r.json()
    assert s["ai_used"] is False and s["dimension"] == "anagrup" and s["active_customers"] == 61
    assert s["eligible_pairs"] == 10 and s["groups_in_model"] == 5
    assert s["customers_with_opportunities"] == len(ctx.opportunities.targets)
    assert s["queue_reservoir"] == sum(1 for a in ctx.sales_queue_candidates if a["category"] == "cross_sell")
    assert s["thresholds"] == {"recent_days": 365, "min_source_buyers": 20, "min_target_buyers": 20,
                               "min_pair_buyers": 10, "min_confidence": 0.3, "min_lift": 1.2,
                               "max_targets_per_customer": 3, "score_cap": 69}
    assert s["targets_by_priority"]["high"] == 0 and s["targets_by_priority"]["critical"] == 0
    assert "920.10." not in r.text and "Sentetik Fırsat" not in r.text          # no customer identities


def test_summary_without_model(client, data_dir, ctx):
    cl, _, _ = client
    repo2 = ContextRepository(data_folder=data_dir, context=dict(ctx))
    APP.app.dependency_overrides[APP.get_repo] = lambda: repo2
    r = cl.get("/api/opportunities/summary")
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "opportunities_unavailable"


def test_territory_signature_mismatch_refused(client):
    cl, _, c360 = client
    snap = c360.snapshot()

    class Frozen:
        def snapshot(self):
            return snap

        def signature_for(self, c):
            return ("other",)
    APP.app.dependency_overrides[APP.get_c360] = lambda: Frozen()
    r = cl.get("/api/opportunities/territories")
    assert r.status_code == 503 and r.json()["detail"]["state"] == "building"


def test_zero_ai_calls_and_no_paths(client, data_dir, hedef_path, caplog):
    import logging
    cl, _, _ = client
    APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
    with caplog.at_level(logging.DEBUG):
        texts = [cl.get(u, params=p).text for u, p in (
            ("/api/opportunities/summary", {}), ("/api/opportunities/territories", {"dimension": "city"}),
            ("/api/opportunities/territories", {"dimension": "sector"}), ("/api/sales-queue", {"filter": "cross_sell"}),
            (f"/api/customer360/{O[41]}", {}))]
    with sqlite3.connect(ST.DB_PATH) as c:
        assert c.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == 0
    for t in texts:
        assert data_dir not in t and os.path.dirname(hedef_path) not in t and "Traceback" not in t
    app_logs = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    for bad in ("SENT-MÜREKKEP", "SENT-KAŞE", "Sentetik İl Bir", "Sentetik Fırsat", "920.10."):
        assert bad not in app_logs                          # no labels, cities, names or codes in app logs
    tree = ast.parse(open(os.path.join(ROOT, "services", "opportunities.py"), encoding="utf-8").read())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
        {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"__future__", "datetime", "collections", "dataclasses", "numpy", "pandas", "config"}
    names = _code_names(os.path.join(ROOT, "services", "opportunities.py"))
    assert not ({"log", "logging", "read_excel", "load_workbook", "today", "now"} & names)


def test_refresh_a_to_b_never_mixed(tmp_path):
    folder = str(tmp_path / "ab")
    OS.write_all(folder, sales_file="Sentetik A fırsat satışlar.xlsx")
    # B: O25 buys the hostile group X (its only target disappears); a later data date moves the window.
    extra = [OS.F._line(9001, "2026-07-15", O[25], OS.name(25), O[25], OS.name(25), 1_000, sku="SKU-X", anagrup=OS.X)]
    OS.write_all(folder, sales_file="Sentetik B fırsat satışlar.xlsx", extra_rows=extra)
    base = {"stock": os.path.join(folder, OS.STOCK_FILE), "aging": os.path.join(folder, OS.AGING_FILE)}
    ctx_a = CTX.build_context(folder, inflation_rate=0.4,
                              sources={**base, "sales": os.path.join(folder, "Sentetik A fırsat satışlar.xlsx")})
    ctx_b = CTX.build_context(folder, inflation_rate=0.4,
                              sources={**base, "sales": os.path.join(folder, "Sentetik B fırsat satışlar.xlsx")})
    assert OS.X in [t.target for t in ctx_a.opportunities.for_customer(O[25])]
    assert OS.X not in [t.target for t in ctx_b.opportunities.for_customer(O[25])]     # B: bought X → no gap
    repo = ContextRepository(data_folder=folder, context=ctx_a)
    c360 = C360.Customer360Repository(folder, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        qa = cl.get("/api/sales-queue", params={"filter": "cross_sell", "limit": 50}).json()
        da = cl.get(f"/api/customer360/{O[25]}").json()["opportunities"]
        sa = cl.get("/api/opportunities/summary").json()
        repo.replace_context(ctx_b)
        qb = cl.get("/api/sales-queue", params={"filter": "cross_sell", "limit": 50}).json()
        db_ = cl.get(f"/api/customer360/{O[25]}").json()["opportunities"]
        sb = cl.get("/api/opportunities/summary").json()
    APP.app.dependency_overrides.clear()
    a25 = {r["action_id"] for r in card(qa, O[25])["reasons"]}
    b25 = {r["action_id"] for r in (card(qb, O[25]) or {"reasons": []})["reasons"]}
    assert a25 and not (a25 & b25)                                                  # no A reason served after B
    assert OS.X in [i["target_group"] for i in da["items"]]
    assert OS.X not in [i["target_group"] for i in db_["items"]]
    assert sa["as_of_data_date"] == "2026-06-30" and sb["as_of_data_date"] == "2026-07-15"
    assert c360.builds == 2 and c360.snapshot().as_of == dt.date(2026, 7, 15)
    b_ids = {a["action_id"] for a in ctx_b.sales_queue_candidates}
    assert {r["action_id"] for k in qb["items"] for r in k["reasons"]} <= b_ids


# =============================================================================================
# UI block
# =============================================================================================
@pytest.fixture(scope="module")
def built(data_dir, tmp_path_factory):
    d = tmp_path_factory.mktemp("opp_dash")
    a, b = str(d / "with.html"), str(d / "without.html")
    DB.build(data_dir, a, today=dt.date(2026, 7, 1))
    DB.build(data_dir, b, today=dt.date(2026, 7, 1), opp_panel=False)
    return open(a, encoding="utf-8").read(), open(b, encoding="utf-8").read()


OPP_BUTTON = '<button class="tab" data-t="opp">Fırsatlar</button>'


def _opp_block(h):
    return h[h.index("\n<!--OPP-TAB-START-->"): h.index("<!--OPP-TAB-END-->") + len("<!--OPP-TAB-END-->")]


def test_tab_present_rest_byte_identical(built):
    import re
    w, wo = built
    assert w.count(OPP_BUTTON) == 1 and w.replace(OPP_BUTTON, "", 1).replace(_opp_block(w), "", 1) == wo
    assert re.findall(r'<button class="tab" data-t="([a-z0-9]+)"', w)[-5:] == ["sq", "c360", "opp", "fair", "ai"]


def test_tab_script_safe(built):
    block = _opp_block(built[0])
    js = block[block.index("<script>") + 8: block.rindex("</script>")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage",
                   "sessionStorage", "/api/agent/", "method:'POST'", "/analyze", "enrich"):
        assert banned not in js
    assert "/api/opportunities/summary" in js and "/api/opportunities/territories" in js and "textContent" in js
    assert "Fırsatlar için panoyu http://127.0.0.1:8000/ üzerinden açın." in js
    assert "Günlük Satış'ta Çapraz Satış adaylarını göster" in block
    assert "920." not in block and "SENT-" not in block
    text = block[block.index("</style>"):].lower()            # CSS "margin" is layout, not money
    for bad in ("penetra", "potansiyel", "kâr", "marj", "profit"):
        assert bad not in text
