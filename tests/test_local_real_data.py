"""LOCAL INTEGRATION tests against the REAL private ERP exports in ./data.

Skipped automatically when the files are absent (fresh checkout, CI). They only read
local files; nothing is uploaded, and no output is written.
Run on the Mac:  python -m pytest -m local_data -q
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest
import config as C
import dashboard_builder as db
from services import context as CTX

DATA = os.environ.get("STOCKAGENT_DATA", os.path.join(ROOT, "data"))
_HAVE = os.path.isdir(DATA) and bool(db._latest(DATA, db.is_stock)) and bool(db._latest(DATA, db.is_sales))
pytestmark = [pytest.mark.local_data,
              pytest.mark.skipif(not _HAVE, reason="private ERP exports not present (expected in CI)")]


@pytest.fixture(scope="module")
def ctx():
    return CTX.build_context(DATA, inflation_rate=0.40)

def test_context_reconciles_overdue(ctx):
    coll, owed = db.m_collections(db._latest(DATA, db.is_aging))
    assert ctx["company"]["working_capital"]["overdue_total"] == round(sum(r["overdue"] for r in coll))

def test_context_reconciles_overstock(ctx):
    _, over, _ = db.m_stock(db._latest(DATA, db.is_stock))
    assert ctx["company"]["working_capital"]["overstock_value"] == round(sum(o["inv"] for o in over))

def test_context_topn_respected(ctx):
    assert len(ctx["collections"]) <= C.MAX_CONTEXT_ITEMS_PER_CATEGORY
    assert len(ctx["winback"]) <= C.MAX_CONTEXT_ITEMS_PER_CATEGORY

def test_actions_have_required_shape(ctx):
    assert len(ctx["actions"]) <= C.MAX_DAILY_ACTIONS
    for a in ctx["actions"]:
        assert 0 <= a["score"] <= 100
        assert a["requires_approval"] is True
        assert a["interpretation"] is None  # AI not run yet
        assert a["facts"] and isinstance(a["facts"], list)

def test_real_decline_absent_without_inflation():
    c = CTX.build_context(DATA, inflation_rate=None)
    assert c["config"]["inflation_source"] in ("unavailable", "manual_config")
    if c["config"]["inflation_rate"] is None:
        assert all(a["category"] != "real_decline" for a in c["actions"])
        assert c["company"]["working_capital"]["shrinking_real_count"] is None


def test_kapali_never_overstock_buyer_candidate(ctx):
    sdf = db.load_sales(db._latest(DATA, db.is_sales))
    closed = {c for c, a in db.cust_attrs(sdf).items() if a.get("closed")}
    for o in ctx["overstock"]:
        assert not ({b["code"] for b in o["top_buyers"]} & closed)


def test_real_evidence_packets_are_minimized(ctx):
    from services import evidence as EV
    from services.entity import ContextRepository
    repo = ContextRepository(context=ctx)
    EV.validate_payload(EV.actions_packet(ctx["actions"], ctx).to_payload())
    for r in ctx["winback"][:5] + ctx["collections"][:5]:
        EV.validate_payload(EV.customer_packet(repo.customer(r["code"]), ctx).to_payload())
    for o in ctx["overstock"][:5]:
        EV.validate_payload(EV.product_packet(repo.product(o["code"]), ctx).to_payload())
