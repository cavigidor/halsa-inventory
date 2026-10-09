"""Domain-rule regression on SYNTHETIC ERP exports (tests/fixtures/synthetic_erp.py).

These pin the deterministic business rules so Milestone 3+ work cannot change them.
"""
import pytest

import dashboard_builder as db
from services import evidence as EV
from services.entity import ContextRepository


@pytest.fixture(scope="module")
def frames(synthetic_dir):
    sdf = db.load_sales(db._latest(synthetic_dir, db.is_sales))
    coll, owed = db.m_collections(db._latest(synthetic_dir, db.is_aging))
    attr = db.cust_attrs(sdf)
    return {"sdf": sdf, "coll": coll, "owed": owed, "attr": attr,
            "lap": db.m_lapsed(sdf, owed, attr),
            "mom": db.m_momentum(sdf, attr),
            "risk": db.m_risk(sdf, owed, db.inv_closed(sdf), attr)}


# ---------------- customer identity = order source (Pro_kodu / ProjeIsmi) ----------------
def test_identity_uses_order_source_not_invoice_party(frames):
    lap = {r["code"]: r for r in frames["lap"]}
    assert "900.00.0001" in lap                      # Pro_kodu
    assert "900.99.0001" not in lap                  # Musterikod (invoice party) is NOT the identity
    assert lap["900.00.0001"]["name"] == "Sentetik T1 Kirtasiye"   # ProjeIsmi


def test_identity_falls_back_to_invoice_code_when_pro_kodu_empty(frames):
    assert "900.00.0008" in {r["code"] for r in frames["lap"]}


# ---------------- lapsed tiering ----------------
def test_lapsed_tiers(frames):
    tiers = {r["code"]: r["tier"] for r in frames["lap"]}
    assert tiers["900.00.0001"] == 1      # 3 strong years
    assert tiers["900.00.0002"] == 2      # 2 strong years
    assert tiers["900.00.0003"] == 3      # 1 strong year, in 2025
    assert "900.00.0004" not in tiers     # 1 strong year in 2023 -> T3 not allowed
    assert "900.00.0005" not in tiers     # total below 30.000
    assert "900.00.0006" not in tiers     # bought in 2026 -> not lapsed


def test_lapsed_thresholds_are_the_configured_ones():
    assert db.YEAR_THR == 10000 and db.MIN_TOTAL == 30000 and db.LAPSED_YEAR == 2026
    assert db.T3_YEARS == [2024, 2025]
    # NOTE: code window is 2021-2025; the written spec says 2020-2025. Open question in TASKS.md.
    assert db.QUAL_YEARS == [2021, 2022, 2023, 2024, 2025]


# ---------------- KAPALI ----------------
def test_kapali_excluded_from_opportunities(frames, synthetic_ctx):
    assert "900.00.0007" not in {r["code"] for r in frames["lap"]}
    assert "900.00.0007" not in {r["code"] for r in frames["mom"]}
    for o in synthetic_ctx["overstock"]:                       # buyer candidates = opportunities
        assert "900.00.0007" not in {b["code"] for b in o["top_buyers"]}
    assert all(not (a["category"] in ("winback", "real_decline") and a["entity_id"] == "900.00.0007")
               for a in synthetic_ctx["actions"])


def test_kapali_kept_in_risk_and_collections(frames, synthetic_ctx):
    risk = {r["code"]: r for r in frames["risk"]}
    assert risk["900.00.0007"]["closed"] is True
    assert "900.00.0007" in {r["code"] for r in frames["coll"]}
    assert "900.00.0007" in {r["code"] for r in synthetic_ctx["collections"]}


# ---------------- receivable aging (dynamic monthly buckets) ----------------
def test_receivable_aging(frames):
    coll = {r["code"]: r for r in frames["coll"]}
    assert coll["900.00.0007"]["overdue"] == 60_000                 # buckets summed
    assert coll["900.00.0007"]["oldest"] == "Kasım-2025 ve öncesi"  # oldest non-zero bucket
    assert coll["900.00.0006"]["oldest"] == "Ocak-2026"
    assert "900.00.0005" not in coll                                # zero balance excluded
    assert "TOPLAM" not in coll                                     # non-account rows excluded
    # 'Toplam Döviz Bakiye' is not an aging bucket and is not added to the balance
    assert coll["900.00.0007"]["overdue"] != 60_000 + 7_777


def test_context_reconciles_with_dashboard_on_synthetic_data(synthetic_dir, synthetic_ctx, frames):
    _, over, _ = db.m_stock(db._latest(synthetic_dir, db.is_stock))
    wc = synthetic_ctx["company"]["working_capital"]
    assert wc["overdue_total"] == round(sum(r["overdue"] for r in frames["coll"]))
    assert wc["overstock_value"] == round(sum(o["inv"] for o in over))


def test_stock_low_excludes_discontinued_and_overstock_flags_it(synthetic_dir):
    low, over, total = db.m_stock(db._latest(synthetic_dir, db.is_stock))
    assert [r["code"] for r in low] == ["SP-001"]
    assert {r["code"]: r["disc"] for r in over} == {"SP-002": False, "SP-003": True}
    assert total == 5


# ---------------- margin data quality ----------------
def test_margin_never_reaches_ai_context_or_packets(synthetic_ctx):
    import json
    blob = json.dumps(synthetic_ctx, ensure_ascii=False, default=str)
    assert "Net_Tutar_Maliyet_Dusulmus" not in blob and '"marg"' not in blob
    repo = ContextRepository(context=synthetic_ctx)
    p = EV.customer_packet(repo.customer("900.00.0006"), synthetic_ctx)
    assert any(w["code"] == "margin_data_gaps" for w in p.to_payload()["warnings"])
    assert all("marj" not in f["label"].lower() for f in p.facts)


@pytest.mark.xfail(strict=True, reason="KNOWN ISSUE (TASKS.md): dashboard margin treats missing cost as "
                                       "zero profit; margin should ignore rows without cost data")
def test_dashboard_margin_does_not_treat_missing_cost_as_zero(frames):
    rows = {r["code"]: r for r in db.m_margin(frames["sdf"], frames["attr"])}
    # 900.00.0006 has 25% profit on every row WITH cost data; missing-cost rows must not drag it down
    assert rows["900.00.0006"]["marg"] == pytest.approx(25.0, abs=0.1)


def test_missing_values_are_not_zero_in_packets(synthetic_ctx):
    repo = ContextRepository(context=synthetic_ctx)
    cust = dict(repo.customer("900.00.0001"))
    cust["overdue"] = None
    p = EV.customer_packet(cust, synthetic_ctx)
    assert "Vadesi geçen bakiye" in p.missing
    assert all(f["label"] != "Vadesi geçen bakiye" for f in p.facts)


def test_real_growth_unavailable_without_inflation(synthetic_dir):
    from services import context as CTX
    c = CTX.build_context(synthetic_dir, inflation_rate=None)
    if c["config"]["inflation_rate"] is None:
        assert all(a["category"] != "real_decline" for a in c["actions"])
        p = EV.actions_packet(c["actions"], c)
        assert any(w["code"] == "inflation_unavailable" for w in p.to_payload()["warnings"])
