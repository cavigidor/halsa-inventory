"""M8-R5B1 / D-071: Cari aging buckets are invoice age, not overdue debt. Synthetic data only (invented codes, names
and amounts). Covers: long-outstanding and recent invoices without due dates, signed balances, 320 suppliers,
unclassified accounts, blank and zero buckets, shared invoice accounts, action queue / AI evidence, Customer 360,
dashboard, refresh and API output, no premature VERIFIED, CRM and Kredi due dates unchanged, source bytes intact."""
import dataclasses
import hashlib
import json
import os
import re

import pandas as pd
import pytest

import dashboard_builder as DB
from services import accuracy as ACC
from services import action_sources as AS
from services import cari_policy as CARI
from services import context as CTX
from services import crm as CRM
from services import customer360 as C360
from services import evidence as EV
from services import kredi_schedule as KS
from services import refresh as RF
from services import scenarios as SCN
from services.entity import ContextRepository
from tests.fixtures import c360_synth as CF
from tests.fixtures import synthetic_erp as SE

BUCKETS = SE.AGING_COLUMNS[3:-1]          # oldest → newest month buckets (no FX column)
OLDEST, NEWEST = BUCKETS[0], BUCKETS[-1]


def _row(code, buckets=None, name="Sentetik Hesap", rep="TEMSILCI-A", fx=0):
    d = {c: 0 for c in BUCKETS}
    d.update(buckets or {})
    return {"Hesap kodu": code, "Hesap adı": name, "Temsilci kodu": rep, **d, "Toplam Döviz Bakiye": fx}


def _aging(*rows):
    return pd.DataFrame(list(rows), columns=SE.AGING_COLUMNS)


def _by_code(frame):
    rows, balances = DB.m_collections(None, frame)
    return {r["code"]: r for r in rows}, balances


# ---------------------------------------------------------------------------------------------
# what the export establishes: signed balance, invoice age, the 320 supplier class
# ---------------------------------------------------------------------------------------------
def test_long_outstanding_invoice_is_invoice_age_not_overdue():
    rows, _ = _by_code(_aging(_row("120.01.0001", {OLDEST: 40_000})))
    r = rows["120.01.0001"]
    assert r["balance"] == 40_000 and r["oldest"] == OLDEST
    assert "overdue" not in r and r["account_class"] == "unconfirmed" and r["class_label"] == "Sınıflandırılmadı"
    assert AS.produce_collections({"coll_scored": [{**r, "overdue": 40_000, "priority_score": 99,
                                                    "drivers": {"age_months": 12, "ratio": 1},
                                                    "revenue_25_26": 1, "still_active": True}]}) == []


def test_recent_invoice_overdue_status_is_unknown_not_zero():
    rows, _ = _by_code(_aging(_row("120.01.0002", {NEWEST: 9_000})))
    assert rows["120.01.0002"]["oldest"] == NEWEST and rows["120.01.0002"]["balance"] == 9_000
    p = EV.customer_packet({"customer_code": "120.01.0002", "name": "Sentetik Yeni"})
    assert CARI.OVERDUE_UNAVAILABLE in p.missing                     # unknown: may or may not be overdue by terms
    assert all("vadesi geçen" not in f["label"].lower() for f in p.facts)
    assert "has_overdue" not in p.flags
    assert any(w["code"] == "cari_overdue_unavailable" for w in p.warnings)


def test_positive_and_negative_balances_keep_their_sign():
    rows, balances = _by_code(_aging(_row("120.01.0003", {OLDEST: 1_500, NEWEST: 500}),
                                     _row("120.01.0004", {BUCKETS[2]: -7_250})))
    pos, neg = rows["120.01.0003"], rows["120.01.0004"]
    assert (pos["balance"], pos["direction"]) == (2_000, "debit")
    assert (neg["balance"], neg["direction"]) == (-7_250, "credit")       # not dropped, not zeroed, not flipped
    assert neg["direction_label"] == "Halsa hesap sahibine borçlu" and neg["oldest"] == BUCKETS[2]
    assert balances == {"120.01.0003": 2_000, "120.01.0004": -7_250}


def test_320_accounts_are_suppliers_and_no_other_prefix_is_classified():
    rows, _ = _by_code(_aging(_row("320.01.0001", {OLDEST: -3_000}), _row("120.01.0005", {OLDEST: 10}),
                              _row("150.01.0001", {OLDEST: 10}), _row("3201", {OLDEST: 10})))
    assert rows["320.01.0001"]["account_class"] == "supplier"
    assert rows["320.01.0001"]["class_label"] == "Tedarikçi (320)" and rows["320.01.0001"]["direction_label"] == "—"
    assert rows["320.01.0001"]["balance"] == -3_000                     # signed source amount kept
    assert rows["3201"]["account_class"] == "supplier"                  # prefix rule, code read as recorded
    assert rows["120.01.0005"]["account_class"] == rows["150.01.0001"]["account_class"] == "unconfirmed"
    assert CARI.account_class(" 320.01") == "unconfirmed"               # never trimmed (D-042)


def test_blank_bucket_makes_the_balance_unknown_and_zero_stays_zero():
    frame = _aging(_row("120.01.0006", {OLDEST: 100}), _row("120.01.0007"))
    frame.loc[0, BUCKETS[3]] = None                                     # a blank amount cell
    before = frame.copy(deep=True)
    rows, balances = _by_code(frame)
    assert rows["120.01.0006"]["balance"] is None and rows["120.01.0006"]["direction"] is None
    assert rows["120.01.0006"]["direction_label"].startswith("Bakiye bilinmiyor")
    assert rows["120.01.0007"]["balance"] == 0 and rows["120.01.0007"]["direction"] == "zero"
    assert rows["120.01.0007"]["oldest"] == "—"
    assert balances["120.01.0006"] is None
    pd.testing.assert_frame_equal(frame, before)                        # the caller's frame is never modified


def test_source_bytes_and_rows_are_untouched(tmp_path):
    path = tmp_path / "Cari Sentetik.xlsx"
    _aging(_row("320.01.0002", {OLDEST: -1}), _row("120.01.0008", {NEWEST: 2})).to_excel(path, index=False)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    rows, _ = DB.m_collections(str(path))
    assert [r["code"] for r in rows] == ["120.01.0008", "320.01.0002"]   # every account row kept, neutral order
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


# ---------------------------------------------------------------------------------------------
# downstream: context, actions, AI evidence, drafts
# ---------------------------------------------------------------------------------------------
def test_context_has_no_collection_urgency_risk_or_overdue_total(synthetic_ctx):
    assert synthetic_ctx["collections"] == [] and synthetic_ctx["risk"] == []
    assert synthetic_ctx["company"]["working_capital"]["overdue_total"] is None
    cats = {a["category"] for a in synthetic_ctx["actions"]} | {a["category"] for a in
                                                                synthetic_ctx.sales_queue_candidates}
    assert "collections" not in cats
    blob = json.dumps(synthetic_ctx, ensure_ascii=False, default=str)
    assert "Vadesi geçen" not in blob and "has_overdue" not in blob and "priority_score" not in blob


def test_winback_rows_and_actions_carry_no_debt_claim(synthetic_ctx):
    assert synthetic_ctx["winback"] and all("overdue" not in r for r in synthetic_ctx["winback"])
    for a in (x for x in synthetic_ctx["actions"] if x["category"] == "winback"):
        text = " ".join([a["reason"], *a["facts"]]).lower()
        assert "vadesi" not in text and "tahsil" not in text and "borç" not in text
        assert a["confidence"] == "high"


def test_ai_customer_packet_and_drafts_have_no_overdue_facts(synthetic_ctx):
    repo = ContextRepository(context=synthetic_ctx)
    for code in ("900.00.0001", "900.00.0002", "900.00.0006"):
        payload = EV.customer_packet(repo.customer(code), synthetic_ctx).to_payload()
        labels = " ".join(f["label"] for f in payload["facts"]).lower()
        assert "vadesi" not in labels and "borç" not in labels and "tahsilat" not in labels
        assert CARI.OVERDUE_UNAVAILABLE in payload["missing"] and "has_overdue" not in payload["flags"]
    # a stale injected row cannot bring the old facts back into customer evidence
    stale = {"collections": [{"code": "900.00.0002", "name": "x", "overdue": 8_000, "oldest": OLDEST,
                              "priority_score": 90, "revenue_25_26": 1, "still_active": True}],
             "risk": [{"code": "900.00.0002", "name": "x", "overdue": 8_000, "rev": 1, "ratio": 800}]}
    cust = ContextRepository(context={**synthetic_ctx, **stale}).customer("900.00.0002")
    assert not {"overdue", "oldest_overdue", "collections_score", "debt_to_revenue_pct"} & set(cust)
    d = EV.draft_packet("whatsapp", "collection", cust)
    assert CARI.OVERDUE_UNAVAILABLE in d.missing and not any(f["kind"] == "money" for f in d.facts)
    assert any(w["code"] == "cari_overdue_unavailable" for w in d.warnings)


def test_prompt_forbids_overdue_claims_from_invoice_age():
    text = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "business_agent.txt"),
                encoding="utf-8").read()
    assert "vade DEĞİLDİR" in text and "önce tahsilat görüşmesi" not in text and "has_overdue" not in text
    assert "yalnızca tahsilat ve risk" not in text


# ---------------------------------------------------------------------------------------------
# Customer 360: invoice-account grain, shared accounts, supplier, negative balance
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def c360(tmp_path_factory):
    folder = CF.write_all(str(tmp_path_factory.mktemp("r5b1_c360")))
    frame = CF.aging_frame()
    frame.loc[frame["Hesap kodu"] == "910.99.0001", CF.AGING_COLUMNS[5]] = -2_500   # credit on a customer account
    frame.to_excel(os.path.join(folder, CF.AGING_FILE), index=False)
    ctx = CTX.build_context(folder, inflation_rate=0.40)
    return C360.build_snapshot(folder, ctx)


def _accounts(snap, code):
    return {a["account_code"]: a["aging"] for a in C360.customer_detail(snap, code)["receivables"]["accounts"]}


def test_c360_negative_customer_balance_means_halsa_owes_the_customer(c360):
    a = _accounts(c360, "910.00.0001")["910.99.0001"]
    assert a["balance"] == -2_500 and a["direction"] == "credit"
    assert a["direction_label"] == "Halsa müşteriye borçlu" and a["overdue"] is None
    assert a["overdue_status"] == "unavailable" and a["oldest_bucket"] == CF.AGING_COLUMNS[5]


def test_c360_shared_account_balance_is_never_allocated_and_has_no_overdue(c360):
    seen = [_accounts(c360, code)["910.99.0002"] for code in ("910.00.0002", "910.00.0003")]
    assert seen[0] == seen[1] and seen[0]["balance"] == 4_000 and seen[0]["overdue"] is None
    for code in ("910.00.0002", "910.00.0003"):
        rec = C360.customer_detail(c360, code)["receivables"]
        assert not any("total" in k or "overdue" in k for k in rec)
        assert CARI.OVERDUE_RETIRED_REASON in rec["limitations"] and CARI.SUPPLIER_NOTE in rec["limitations"]


def test_c360_supplier_account_is_not_shown_as_a_customer_receivable(c360):
    aging = dict(c360.aging)
    aging["910.99.0001"] = {**aging["910.99.0001"], "account_class": "supplier"}
    a = _accounts(dataclasses.replace(c360, aging=aging), "910.00.0001")["910.99.0001"]
    assert a["class_label"] == "Tedarikçi (320)" and a["direction_label"] == "—" and a["balance"] == -2_500


# ---------------------------------------------------------------------------------------------
# dashboard, refresh, scenario, API; no premature VERIFIED; unrelated due dates unchanged
# ---------------------------------------------------------------------------------------------
def _data_blob(html):
    m = re.search(r"const DATA=(\{.*?\});\n", html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_dashboard_shows_factual_balances_and_no_overdue_outputs(synthetic_dir, tmp_path):
    stats, out = {}, tmp_path / "page.html"
    DB.build(synthetic_dir, str(out), stats_out=stats)          # algorithm harness: every module embedded
    data = _data_blob(out.read_text(encoding="utf-8"))
    mods = data["modules"] if "modules" in data else data
    coll = mods["collections"]
    assert coll["title"] == "Cari Bakiye (fatura yaşı)"
    assert [c["k"] for c in coll["cols"]] == ["name", "rep", "class_label", "balance", "direction_label", "oldest"]
    assert {r["code"] for r in coll["rows"]} >= {"900.00.0005", "900.00.0007"}      # zero and KAPALI kept
    assert mods["risk"]["rows"] == [] and mods["risk"]["note"] == CARI.OVERDUE_RETIRED_REASON
    assert all(c["k"] != "overdue" for c in mods["winback"]["cols"])
    labels = [s[0] if isinstance(s, list) else s for s in data.get("stats", [])]
    assert "vadesi geçen toplam" not in labels and "borçlu kayıp müşteri" not in labels
    assert stats["overdue_total"] is None
    assert "Listenin başındakileri kovalayın" not in out.read_text(encoding="utf-8")


def test_refresh_refuses_a_number_in_place_of_the_unavailable_total(synthetic_dir, tmp_path):
    ctx = CTX.build_context(synthetic_dir, inflation_rate=.4)
    stats, path = {}, tmp_path / "page.html"
    DB.build(synthetic_dir, str(path), stats_out=stats)
    assert RF.verify(ctx, stats, str(path), ctx["as_of_data_date"])["ok"]
    bad = RF.verify(ctx, {**stats, "overdue_total": 0}, str(path), ctx["as_of_data_date"])
    assert not bad["ok"] and any("vadesi geçen toplam" in e for e in bad["errors"])


def test_scenario_has_no_receivables_basis():
    r = SCN.run_scenario(receivables_total=None, financing_rate=0.5, inflation_rate=0.4,
                         fx_sensitive_inventory_value=None, assumptions={})
    assert r["results"]["receivables_funding_cost"]["available"] is False
    assert r["results"]["receivables_real_erosion"]["available"] is False


@pytest.mark.production_gate
def test_register_withholds_and_promotes_nothing():
    g = ACC.current_gate()
    risk, coll = g.decide("receivables.risk"), g.decide("receivables.collections")
    assert risk.state == ACC.State.INVALID and CARI.OVERDUE_RETIRED_CODE in risk.codes
    assert not coll.displayable and "CARI-ACCOUNT-CLASS" in coll.codes
    assert "OVERDUE-DEF" not in coll.codes and "DEF:overdue_definition" not in coll.codes
    assert not ACC.DEFINITIONS["overdue_definition"].approved and not ACC.DEFINITIONS["cari_account_class"].approved
    assert len(ACC.REGISTER) == 33 and not any(m.certified == ACC.State.VERIFIED for m in ACC.REGISTER.values())
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    assert "vadesi geçen toplam" not in ACC.STAT_METRIC and "borçlu kayıp müşteri" not in ACC.STAT_METRIC
    for key in ("actions.queue", "sales_queue", "agent.customer_facts", "scenario.receivables", "customer360.profile"):
        assert not g.decide(key).displayable, key


@pytest.mark.production_gate
def test_real_api_still_withholds_cari_outputs(client):  # noqa: F811
    for path in ("/api/context", "/api/agent/actions", "/api/agent/customer/900.00.0007"):
        r = client.get(path)
        assert r.status_code == 409 and "900.00.0007" not in r.text, path


def test_crm_and_kredi_due_dates_are_unchanged():
    assert CRM.due_state("2026-06-01", "2026-06-30") == "overdue"          # a user-entered date, genuinely past
    assert CRM.due_state("2026-06-30", "2026-06-30") == "today"
    assert KS.PAST_DUE_REMAINING == "past_due_with_remaining"              # Kredi installments keep their due dates


from tests.test_m8_accuracy import client  # noqa: E402,F401 — real production gate, synthetic context
