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
    assert "overdue" not in r and r["account_class"] == "customer" and r["class_label"] == "Müşteri (120)"   # D-072
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
    assert neg["direction_label"] == "Halsa müşteriye borçlu" and neg["oldest"] == BUCKETS[2]   # D-072: 120 customer
    assert pos["direction_label"] == "Pozitif bakiye"                   # positive meaning still unconfirmed
    assert balances == {"120.01.0003": 2_000, "120.01.0004": -7_250}


def test_320_accounts_are_suppliers_120_customers_and_no_other_prefix_is_classified():
    rows, _ = _by_code(_aging(_row("320.01.0001", {OLDEST: -3_000}), _row("120.01.0005", {OLDEST: 10}),
                              _row("150.01.0001", {OLDEST: 10}), _row("3201", {OLDEST: 10})))
    assert rows["320.01.0001"]["account_class"] == "supplier"
    assert rows["320.01.0001"]["class_label"] == "Satıcı (320)"
    assert rows["320.01.0001"]["direction_label"] == "Negatif bakiye"   # sign named neutrally, no debtor / creditor claim
    assert rows["320.01.0001"]["balance"] == -3_000                     # signed source amount kept
    assert rows["3201"]["account_class"] == "supplier"                  # prefix rule, code read as recorded
    assert rows["120.01.0005"]["account_class"] == "customer"           # D-072 D4
    assert rows["150.01.0001"]["account_class"] == "unconfirmed"
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


def test_c360_negative_balance_on_an_unclassified_prefix_stays_neutral(c360):
    # D-072 D4: the customer meaning needs a 120 customer account; this fixture's 910 prefix is unclassified.
    # (Confirmed wording for 120 accounts: tests/test_m8_d072_cari_first_metric.py.)
    a = _accounts(c360, "910.00.0001")["910.99.0001"]
    assert a["balance"] == -2_500 and a["direction"] == "credit"
    assert a["direction_label"] == "Negatif bakiye" and a["overdue"] is None
    assert a["overdue_status"] == "unavailable" and a["oldest_bucket"] == CF.AGING_COLUMNS[5]


def test_c360_shared_account_balance_is_never_allocated_and_has_no_overdue(c360):
    seen = [_accounts(c360, code)["910.99.0002"] for code in ("910.00.0002", "910.00.0003")]
    assert seen[0] == seen[1] and seen[0]["balance"] == 4_000 and seen[0]["overdue"] is None
    for code in ("910.00.0002", "910.00.0003"):
        rec = C360.customer_detail(c360, code)["receivables"]
        assert not any("total" in k or "overdue" in k for k in rec)
        assert CARI.OVERDUE_RETIRED_REASON in rec["limitations"] and CARI.SUPPLIER_NOTE in rec["limitations"]


# PR #41 final review: customer wording only for a negative balance on an invoice account uniquely associated with
# that customer and not a 320 supplier; shared accounts, suppliers and every positive balance get neutral sign names.
@pytest.fixture(scope="module")
def c360_shared_negative(tmp_path_factory):
    folder = CF.write_all(str(tmp_path_factory.mktemp("r5b1_shared")))
    frame = CF.aging_frame()
    frame[CF.AGING_COLUMNS[3:]] = frame[CF.AGING_COLUMNS[3:]].astype(float)
    frame.loc[frame["Hesap kodu"] == "910.99.0002", CF.AGING_COLUMNS[3]] = -4_000.25    # shared invoice account
    frame.loc[frame["Hesap kodu"] == "910.99.0001", CF.AGING_COLUMNS[5]] = -2_500.5     # unique to 910.00.0001
    path = os.path.join(folder, CF.AGING_FILE)
    frame.to_excel(path, index=False)
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    snap = C360.build_snapshot(folder, CTX.build_context(folder, inflation_rate=0.40))
    return snap, path, sha


def test_c360_unique_negative_unclassified_account_stays_neutral(c360_shared_negative):
    # D-072 D4: unique association alone is not enough; the account must be a 120 customer account
    snap, _, _ = c360_shared_negative
    assert snap.account_customers["910.99.0001"] == ["910.00.0001"]
    a = _accounts(snap, "910.00.0001")["910.99.0001"]
    assert (a["balance"], a["direction"], a["direction_label"]) == (-2_500.5, "credit", "Negatif bakiye")


def test_c360_shared_negative_account_is_neutral_for_each_customer_and_never_divided(c360_shared_negative):
    snap, path, _ = c360_shared_negative
    owners = snap.account_customers["910.99.0002"]
    assert sorted(owners) == ["910.00.0002", "910.00.0003"]
    full = {r["code"]: r["balance"] for r in DB.m_collections(path)[0]}["910.99.0002"]
    assert full == -4_000.25
    for code in owners:
        d = C360.customer_detail(snap, code)
        acc = {a["account_code"]: a for a in d["receivables"]["accounts"]}["910.99.0002"]
        assert acc["aging"]["balance"] == full                                   # complete balance, not a share
        assert acc["aging"]["direction"] == "credit" and acc["aging"]["direction_label"] == "Negatif bakiye"
        assert "borçlu" not in acc["aging"]["direction_label"]
        assert acc["shared"] is True and acc["warning"] == C360.SHARED_ACCOUNT_WARNING
        assert "shared_invoice_account" in {w["code"] for w in d["warnings"]}
        assert not any("total" in k for k in d["receivables"])


def test_c360_unique_positive_customer_account_stays_neutral(c360_shared_negative):
    snap, _, _ = c360_shared_negative
    assert snap.account_customers["910.00.0001"] == ["910.00.0001"]
    a = _accounts(snap, "910.00.0001")["910.00.0001"]
    assert a["balance"] > 0 and a["direction"] == "debit" and a["direction_label"] == "Pozitif bakiye"
    assert "Müşteri Halsa'ya borçlu" not in json.dumps(C360.customer_detail(snap, "910.00.0001"), ensure_ascii=False)
    assert CARI.direction_label("debit", "unconfirmed", customer_account=True) == "Pozitif bakiye"


@pytest.mark.parametrize("sign", (-1, 1))
def test_c360_supplier_account_is_neutral_regardless_of_sign(c360_shared_negative, sign):
    snap, _, _ = c360_shared_negative
    aging = dict(snap.aging)
    aging["910.99.0001"] = {**aging["910.99.0001"], "account_class": "supplier", "balance": sign * 2_500.5,
                            "direction": "credit" if sign < 0 else "debit"}
    a = _accounts(dataclasses.replace(snap, aging=aging), "910.00.0001")["910.99.0001"]
    assert a["class_label"] == "Satıcı (320)" and a["balance"] == sign * 2_500.5
    assert a["direction_label"] == ("Negatif bakiye" if sign < 0 else "Pozitif bakiye")


@pytest.mark.production_gate
def test_c360_wording_change_leaves_values_schema_and_gate_unchanged(c360_shared_negative):
    from schemas import C360Aging
    snap, path, sha = c360_shared_negative
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha            # source bytes untouched
    for code in ("910.00.0001", "910.00.0002", "910.00.0003"):
        for acc in C360.customer_detail(snap, code)["receivables"]["accounts"]:
            ag = acc["aging"]
            assert set(ag) == set(C360Aging.model_fields)                        # API schema unchanged
            C360Aging(**ag)
    g = ACC.current_gate()
    assert not g.decide("customer360.profile").displayable
    assert len(ACC.REGISTER) == 33 and not any(m.certified == ACC.State.VERIFIED for m in ACC.REGISTER.values())
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False


def test_c360_supplier_account_is_not_shown_as_a_customer_receivable(c360):
    aging = dict(c360.aging)
    aging["910.99.0001"] = {**aging["910.99.0001"], "account_class": "supplier"}
    a = _accounts(dataclasses.replace(c360, aging=aging), "910.00.0001")["910.99.0001"]
    assert a["class_label"] == "Satıcı (320)" and a["balance"] == -2_500
    assert a["direction_label"] == "Negatif bakiye"                     # not "Halsa müşteriye borçlu" for a supplier


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
    assert not coll.displayable and "REAL-DATA" in coll.codes          # D-072 removed CARI-ACCOUNT-CLASS / CARI-FX
    assert "OVERDUE-DEF" not in coll.codes and "DEF:overdue_definition" not in coll.codes
    assert not ACC.DEFINITIONS["overdue_definition"].approved
    assert ACC.DEFINITIONS["cari_account_class"].approved and ACC.DEFINITIONS["cari_account_class"].decision_ref == "D-072"
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


# ---------------------------------------------------------------------------------------------
# review corrections (PR #41): exact signed amounts; no unjustified debtor / creditor wording
# ---------------------------------------------------------------------------------------------
DECIMAL_CASES = (("120.01.0101", {OLDEST: 1250.25}, 1250.25, "debit"),          # positive decimal
                 ("120.01.0102", {OLDEST: -7250.75}, -7250.75, "credit"),       # negative decimal
                 ("120.01.0103", {NEWEST: -0.40}, -0.4, "credit"),              # small negative, not rounded to 0
                 ("120.01.0104", {NEWEST: 0.01}, 0.01, "debit"),                # small positive
                 ("120.01.0105", {OLDEST: 0.1, NEWEST: 0.2}, 0.3, "debit"),     # exact decimal sum, no float residue
                 ("120.01.0106", {OLDEST: 5, NEWEST: -5}, 0.0, "zero"),         # genuine zero stays zero
                 ("120.01.0107", {}, 0.0, "zero"))


def _decimal_frame():
    frame = _aging(*[_row(code, b) for code, b, _, _ in DECIMAL_CASES], _row("120.01.0108", {OLDEST: 3.5}))
    frame.loc[frame["Hesap kodu"] == "120.01.0108", BUCKETS[4]] = None              # blank → unknown
    return frame


def test_signed_amounts_keep_their_decimals_and_sign():
    frame = _decimal_frame()
    before = frame.copy(deep=True)
    rows, balances = _by_code(frame)
    for code, _, want, direction in DECIMAL_CASES:
        assert rows[code]["balance"] == want and rows[code]["direction"] == direction, code
        assert balances[code] == want
    assert rows["120.01.0103"]["balance"] != 0 and repr(rows["120.01.0105"]["balance"]) == "0.3"
    assert repr(rows["120.01.0106"]["balance"]) == "0.0"                             # not -0.0
    assert rows["120.01.0108"]["balance"] is None and rows["120.01.0108"]["direction"] is None
    pd.testing.assert_frame_equal(frame, before)


def test_c360_and_dashboard_receive_the_same_exact_amounts(tmp_path):
    folder = CF.write_all(str(tmp_path / "c360"))
    frame = CF.aging_frame()
    frame[CF.AGING_COLUMNS[3:]] = frame[CF.AGING_COLUMNS[3:]].astype(float)
    frame.loc[frame["Hesap kodu"] == "910.99.0001", CF.AGING_COLUMNS[5]] = -0.40      # customer invoice account
    frame.loc[frame["Hesap kodu"] == "910.00.0001", CF.AGING_COLUMNS[4]] = 1250.25
    path = os.path.join(folder, CF.AGING_FILE)
    frame.to_excel(path, index=False)
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    rows, _ = DB.m_collections(path)
    dash = {r["code"]: r["balance"] for r in rows}
    snap = C360.build_snapshot(folder, CTX.build_context(folder, inflation_rate=0.40))
    acc = _accounts(snap, "910.00.0001")
    assert acc["910.99.0001"]["balance"] == dash["910.99.0001"] == -0.4
    assert acc["910.99.0001"]["direction_label"] == "Negatif bakiye"       # D-072: 910 prefix is unclassified
    assert acc["910.00.0001"]["balance"] == dash["910.00.0001"] == 1250.25 + 750      # buckets of the fixture
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == sha


def test_supplier_and_unclassified_accounts_get_no_debtor_or_receivable_wording():
    rows, _ = _by_code(_aging(_row("320.01.0201", {OLDEST: 900}), _row("320.01.0202", {OLDEST: -900}),
                              _row("120.01.0201", {OLDEST: 900}), _row("120.01.0202", {OLDEST: -900}),
                              _row("150.01.0201", {OLDEST: -1})))
    debtor_words = ("borçlu", "alacak", "borcu")
    assert rows["120.01.0202"]["direction_label"] == "Halsa müşteriye borçlu"    # D-072: the one confirmed meaning
    assert rows["120.01.0201"]["direction_label"] == "Pozitif bakiye"
    for code, r in rows.items():
        if code == "120.01.0202":
            continue
        assert r["direction_label"] in ("Pozitif bakiye", "Negatif bakiye", "Sıfır bakiye")
        assert not any(w in r["direction_label"].lower() for w in debtor_words)
    for direction in ("debit", "credit", "zero"):
        for cls in ("supplier", "unconfirmed"):
            assert CARI.direction_label(direction, cls, customer_account=False) == CARI.DIRECTION_LABELS[direction]
        assert CARI.direction_label(direction, "supplier", customer_account=True) == CARI.DIRECTION_LABELS[direction]
    assert CARI.direction_label("credit", "unconfirmed", customer_account=True) == "Negatif bakiye"   # D-072: not 120
    assert CARI.direction_label("credit", "customer", customer_account=True) == "Halsa müşteriye borçlu"
    assert CARI.direction_label("credit", "customer", customer_account=False) == "Negatif bakiye"  # shared in C360
    assert CARI.direction_label("debit", "customer", customer_account=True) == "Pozitif bakiye"
    note = CARI.BALANCE_NOTE.lower()
    assert "hesap sahibi halsa'ya borçlu" not in note and "doğrulanmadı" in note


def test_cari_table_shows_fractional_lira_in_the_browser(tmp_path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if os.environ.get("REQUIRE_BROWSER_TESTS") == "1":
            pytest.fail("REQUIRE_BROWSER_TESTS=1 but playwright is not installed")
        pytest.skip("playwright not installed")
    folder = SE.write_all(str(tmp_path / "data"))
    _decimal_frame().to_excel(os.path.join(folder, SE.AGING_FILE), index=False)
    out = tmp_path / "page.html"
    DB.build(folder, str(out))                                    # algorithm harness: the Cari module is embedded
    html = out.read_text(encoding="utf-8")
    assert "fmtMoneyExact(b)" in html                             # the Customer 360 card uses the exact formatter
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as e:
            if os.environ.get("REQUIRE_BROWSER_TESTS") == "1":
                pytest.fail(f"Chromium could not start: {e}")
            pytest.skip("Chromium unavailable")
        page = b.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(out.as_uri())
        page.click('.tab[data-t="collections"]')
        cells = page.eval_on_selector_all("#collections tbody tr", "trs => trs.map(t => t.innerText)")
        b.close()
    text = "\n".join(cells)
    for shown in ("₺1.250,25", "-7.250,75", "-0,40", "₺0,01", "₺0,30", "₺0,00"):
        assert shown in text, (shown, text)
    assert "₺-0\t" not in text and "-₺0\t" not in text and errors == []


def test_buyer_overdue_driver_is_unreachable(synthetic_dir, monkeypatch):
    """Review item 4: `score_buyers` (which passes has_overdue=False to `scoring.buyer_score`) runs only for clearance
    candidates, and `m_overstock_move` returns none since D-070; the only buyer consumer (`product_packet`) is behind the
    retired product AI. So the `overdue_penalty` driver reaches no output, API or AI packet; the code is left as is."""
    from services import scoring as SC

    def never(*a, **k):
        pytest.fail("buyer_score reached")
    monkeypatch.setattr(SC, "buyer_score", never)
    ctx = CTX.build_context(synthetic_dir, inflation_rate=0.40)
    assert ctx["overstock"] == [] and DB.m_overstock_move(pd.DataFrame(), [{"code": "SYN"}]) == []
    blob = json.dumps(ctx, ensure_ascii=False, default=str)
    assert "overdue_penalty" not in blob and "top_buyers" not in blob and "has_overdue" not in blob
    assert ContextRepository(context=ctx).product("SP-002") is None          # no product card → no buyer packet


from tests.test_m8_accuracy import client  # noqa: E402,F401 — real production gate, synthetic context
