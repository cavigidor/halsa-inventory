"""M8-ACCURACY-2 Part 7 — the revised "kredi takip" layout, schedule views and the owner-local reconciliation command.

SYNTHETIC workbooks only (tests/fixtures/kredi_takip_synth.py, tests/test_m6_kredi.py). The real private workbook is
never read by tests. The Kredi module stays withheld: nothing here certifies it.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as APP
from services import accuracy as ACC
from services import generation as GEN
from services import kredi_schedule as KS
from services import owner_view as OV
from services.importers import kredi as K
from tests import test_m6_kredi as TK
from tests.fixtures import kredi_takip_synth as KT
from tests.fixtures.xlsx_synth import F, write_xlsx

pytestmark = pytest.mark.production_gate
ROOT = Path(__file__).resolve().parents[1]
D = dt.date
TODAY = D(2026, 10, 19)


def takip(tmp_path, **kw):
    return K.import_kredi(KT.book(tmp_path, **kw))


def codes(res, sev=None):
    return set(res.report.codes(sev))


def by_no(res):
    return {i.installment_number: i for i in res.installments}


# ---------------------------------------------------------------------------------------------
# 1–5: layouts, control row, SUBTOTAL rows, hidden installments
# ---------------------------------------------------------------------------------------------
def test_01_original_erp_layout_unchanged(tmp_path):
    res = TK.run(tmp_path)
    assert res.ok and res.counts.layout == K.LAYOUT_ERP and res.counts.header_row == 1
    assert res.counts.saved_filter is False and res.counts.subtotal_row is False
    assert res.counts.hidden_installment_rows == 0 and res.counts.installments == 6
    assert res.counts.control_totals_match is True


def test_02_revised_two_row_header_layout(tmp_path):
    res = takip(tmp_path)
    assert res.ok, res.report.to_json()
    c = res.counts
    assert (c.layout, c.header_row) == (K.LAYOUT_TAKIP, 2)
    assert (c.contracts, c.installments) == (2, 9)
    assert c.summary_row_checks.get("matched") == c.summary_row_checks.get("checked") == 5
    assert K.find_kredi_candidates(str(tmp_path))[0]["layout"] == K.LAYOUT_TAKIP


def test_03_erp_control_row_is_validated_and_never_an_installment(tmp_path):
    res = takip(tmp_path)
    assert res.counts.control_total_row and res.counts.control_totals_match
    assert all(i.installment_number != 9 or i.contract_key for i in res.installments)
    assert len(res.installments) == len(KT.rows_2026(TODAY))          # control + SUBTOTAL rows never imported
    rows = KT.rows_2026(TODAY)
    # a contradictory full-source control total is an error (nothing is repaired)
    orig = TK.footer
    try:
        TK.footer = lambda r: {**orig(r), "TAKSİT TUTARI": 1.0}
        bad = K.import_kredi(KT.book(tmp_path / "bad", rows))
    finally:
        TK.footer = orig
    assert not bad.ok and "control_totals_mismatch" in codes(bad, "error") and bad.installments == []
    missing = takip(tmp_path / "noctl", control_row=False)
    assert not missing.ok and "control_total_row_missing" in codes(missing, "error")


def test_04_subtotal_rows_checked_against_visible_rows(tmp_path):
    ok = takip(tmp_path / "ok")
    assert ok.counts.subtotal_row and ok.counts.filtered_totals_match is True
    assert ok.counts.subtotal_checks["mismatched"] == 0 and ok.counts.subtotal_checks["matched"] == 9
    stale = takip(tmp_path / "stale", stale_subtotal=True)
    assert stale.ok                                                   # the full source is still valid…
    assert stale.counts.filtered_totals_match is False and "filtered_subtotal_mismatch" in codes(stale, "warning")
    assert stale.counts.installments == 9                             # …and nothing was repaired or dropped
    nocache = takip(tmp_path / "nc", missing_subtotal_cache=True)
    assert nocache.counts.filtered_totals_match is None
    assert nocache.counts.subtotal_checks["missing_cache"] >= 1
    assert "filtered_subtotal_unverified" in codes(nocache, "warning")


def test_05_hidden_installments_are_imported(tmp_path):
    res = takip(tmp_path)
    c = res.counts
    assert c.saved_filter and c.filter_columns == ["VADE TARİHİ"]
    assert (c.hidden_installment_rows, c.visible_installment_rows) == (5, 4)
    hidden = {i.installment_number for i in res.installments if i.hidden_in_source}
    assert hidden == {1, 2, 3, 6, 12}
    assert "saved_filter_view" in codes(res, "info")


def test_unsupported_and_ambiguous_layouts_fail_closed(tmp_path):
    lit = takip(tmp_path / "lit", row1="literal")
    assert not lit.ok and "layout_unsupported" in codes(lit, "error") and lit.installments == []
    odd = takip(tmp_path / "odd", extra_row1_formula="SUM(A3:A5)")
    assert not odd.ok and "unsupported_summary_formula" in codes(odd, "error")
    nosub = takip(tmp_path / "nosub", subtotal_row=False)
    assert not nosub.ok and "layout_unsupported" in codes(nosub, "error")
    amb = tmp_path / "amb"
    amb.mkdir()
    rows = [TK.HEADERS, TK.HEADERS] + [[r.get(h) for h in TK.HEADERS] for r in TK.base_rows()]
    write_xlsx(amb / "k.xlsx", {"S": rows})
    res = K.import_kredi(amb / "k.xlsx")
    assert not res.ok and "layout_ambiguous" in codes(res, "error")


def test_duplicate_identity_still_rejected_in_revised_layout(tmp_path):
    rows = KT.rows_2026(TODAY)
    rows.append(dict(rows[3]))
    res = takip(tmp_path, rows=rows)
    assert not res.ok and "duplicate_installment" in codes(res, "error")


def test_revised_workbook_is_never_modified(tmp_path):
    p = KT.book(tmp_path)
    before = p.read_bytes()
    K.import_kredi(p)
    assert p.read_bytes() == before


# ---------------------------------------------------------------------------------------------
# 6–10: filters, breakdowns, paid states, negative residual, Kapalı with remaining
# ---------------------------------------------------------------------------------------------
def test_06_explicit_year_and_month_filters_ignore_the_saved_view(tmp_path):
    res = takip(tmp_path)
    nov = KS.select(res.installments, res.contracts, year=2026, month=11)
    assert {i.installment_number for i in nov} == {4, 10}
    y2026 = KS.select(res.installments, res.contracts, year=2026)
    assert len(y2026) == 8 and any(i.hidden_in_source for i in y2026)     # hidden rows are part of the year
    assert len(KS.select(res.installments, res.contracts, year=2027)) == 1
    rng = KS.select(res.installments, res.contracts, due_from=D(2026, 10, 1), due_to=D(2026, 12, 31))
    assert {i.installment_number for i in rng} == {3, 4, 5, 10, 11}
    visible = KS.select(res.installments, res.contracts, visible_only=True)
    assert all(not i.hidden_in_source for i in visible) and len(visible) == 4
    bank = KS.select(res.installments, res.contracts, lender="BNK-02")
    assert {i.installment_number for i in bank} == {10, 11, 12}


def test_07_monthly_principal_interest_tax_breakdown(tmp_path):
    rows = KT.rows_2026(TODAY)
    res = takip(tmp_path, rows=rows)
    b = KS.breakdown(res.installments, res.contracts, ("year", "month"))
    nov = b[("TL", 2026, 11)]
    exp = [r for r in rows if r["VADE TARİHİ"].year == 2026 and r["VADE TARİHİ"].month == 11]
    assert nov["count"] == 2
    for f, h in (("principal", "TAKSİT ANAPARA"), ("interest", "TAKSİT FAİZ"), ("bsmv", "TAKSİT BSMV"),
                 ("kkdf", "TAKSİT KKDF"), ("installment_total", "TAKSİT TUTARI"),
                 ("paid_amount", "ÖDENEN TAKSİT TUTARI"), ("remaining_amount", "KALAN TAKSİT TUTARI")):
        assert nov[f] == round(sum(r[h] for r in exp), 2), f
    assert set(KS.breakdown(res.installments, res.contracts, ("lender",))) == {("TL", "BNK-01"), ("TL", "BNK-02")}
    by_day = KS.breakdown(res.installments, res.contracts, ("due_date",))
    assert ("TL", TODAY) in by_day


def test_currencies_are_never_merged(tmp_path):
    rows = KT.rows_2026(TODAY)
    for r in rows:
        if r["SÖZLEŞME KODU"] == TK.CONTRACTS[1]:
            r["DÖVİZ CİNSİ"] = "EUR"
    res = takip(tmp_path, rows=rows)
    t = KS.totals(res.installments, res.contracts)
    assert set(t) == {"TL", "EUR"}
    assert all(k[0] in ("TL", "EUR") for k in KS.breakdown(res.installments, res.contracts))
    assert t["TL"]["count"] + t["EUR"]["count"] == 9


def test_08_paid_partially_paid_and_unpaid(tmp_path):
    res = takip(tmp_path)
    i = by_no(res)
    assert i[1].remaining_amount.value == 0 and i[1].paid_amount.value == i[1].installment_total.value
    assert i[5].paid_amount.value == 1000.0 and 0 < i[5].remaining_amount.value < i[5].installment_total.value
    assert i[4].paid_amount.value == 0 and i[4].remaining_amount.value == i[4].installment_total.value
    cats = KS.categorize(res.installments, TODAY)
    assert {x.installment_number for x in cats[KS.RECORDED_SETTLED]} == {1}
    assert {x.installment_number for x in cats[KS.PAST_DUE_REMAINING]} == {2}
    assert {x.installment_number for x in cats[KS.DUE_TODAY_REMAINING]} == {3}
    assert {x.installment_number for x in cats[KS.FUTURE_REMAINING]} == {4, 5, 6, 10, 11}
    assert len(cats[KS.ALL]) == 9


def test_09_negative_remaining_is_kept_and_flagged(tmp_path):
    res = takip(tmp_path)
    neg = by_no(res)[12]
    assert neg.remaining_amount.value == -9.0                         # never zeroed or repaired
    assert res.counts.negative_remaining_rows == 1 and "negative_remaining_amount" in codes(res, "warning")
    cats = KS.categorize(res.installments, TODAY)
    assert neg in cats[KS.INVALID_OR_CONTRADICTORY] and neg not in cats[KS.RECORDED_SETTLED]
    t = KS.totals(res.installments, res.contracts)["TL"]
    assert t["remaining_amount"] == round(sum(r["KALAN TAKSİT TUTARI"] for r in KT.rows_2026(TODAY)), 2)


def test_10_kapali_with_positive_remaining_is_not_treated_as_paid(tmp_path):
    res = takip(tmp_path)
    k = by_no(res)[11]
    assert k.status.value == "closed" and k.remaining_amount.value > 0
    assert res.counts.closed_with_positive_remaining == 1 and "status_closed_with_remaining" in codes(res, "info")
    cats = KS.categorize(res.installments, TODAY)
    assert k in cats[KS.FUTURE_REMAINING] and k in cats[KS.INVALID_OR_CONTRADICTORY]
    assert not ACC.DEFINITIONS["kredi_durum"].approved                 # DURUM semantics unchanged and unresolved


def test_missing_amount_stays_unknown(tmp_path):
    rows = KT.rows_2026(TODAY)
    res = takip(tmp_path, rows=rows)
    import dataclasses
    inst = res.installments[0]
    broken = dataclasses.replace(inst, interest=dataclasses.replace(inst.interest, value=None))
    t = KS.totals([broken, *res.installments[1:]], res.contracts)["TL"]
    assert t["interest"] is None and t["missing"]["interest"] == 1 and t["principal"] is not None


def test_saved_view_reconciliation_separates_full_and_filtered(tmp_path):
    res = takip(tmp_path)
    v = KS.saved_view_reconciliation(res.installments, res.contracts, TODAY)
    assert (v["source_installments"], v["visible_installments"], v["hidden_installments"]) == (9, 4, 5)
    hp = v["hidden_with_positive_remaining"]
    assert (hp["count"], hp["past_due"], hp["due_today"], hp["future"]) == (3, 1, 1, 1)
    assert v["full_source_totals"]["TL"]["count"] == 9 and v["visible_totals"]["TL"]["count"] == 4
    assert v["full_source_totals"]["TL"]["installment_total"] > v["visible_totals"]["TL"]["installment_total"]
    assert set(v["visible_by_month"]) == {("TL", 2026, 11), ("TL", 2026, 12)}


# ---------------------------------------------------------------------------------------------
# 11 + 12 + 13 + 15: fingerprints, missing caches, failed validation, owner reload with the revised layout
# ---------------------------------------------------------------------------------------------
def test_11_changed_fingerprint_on_any_byte_change(tmp_path):
    a = KT.book(tmp_path / "a")
    b = KT.book(tmp_path / "b", stale_subtotal=True)                   # same installments, other SUBTOTAL caches
    ra, rb = K.import_kredi(a), K.import_kredi(b)
    assert [i.installment_key for i in ra.installments] == [i.installment_key for i in rb.installments]
    assert GEN.sha256_file(str(a)) != GEN.sha256_file(str(b))


def test_12_missing_cached_formula_in_an_installment_fails_closed(tmp_path):
    from tests.fixtures.xlsx_synth import MISSING
    rows = KT.rows_2026(TODAY)
    rows[1]["TAKSİT FAİZ"] = F("N4*0", MISSING)                       # a hidden row: still validated
    res = takip(tmp_path, rows=rows)
    assert not res.ok and res.installments == []
    assert "formula_missing_cache" in codes(res, "error")


def test_13_failed_validation_in_owner_view_never_shows_data(tmp_path):
    p = KT.book(tmp_path, control_row=False)
    repo = OV.OwnerRepository({"kredi": OV.OwnerSourceConfig("kredi", str(p))}, background=False)
    repo.ensure_loaded()
    e = repo.snapshot()["kredi"]
    assert e["state"] == "failed"


def test_15_owner_reload_with_the_revised_layout(tmp_path):
    p = KT.book(tmp_path)
    store = GEN.GenerationStore()
    repo = OV.OwnerRepository({"kredi": OV.OwnerSourceConfig("kredi", str(p))}, background=False,
                              on_record=store.replace_owner)
    repo.ensure_loaded()
    r1 = store.current().record("kredi")
    assert r1.sha256 == GEN.sha256_file(str(p)) and r1.validation["layout"] == K.LAYOUT_TAKIP
    KT.book(tmp_path, stale_subtotal=True)
    repo.reload()
    r2 = store.current().record("kredi")
    assert r2.sha256 != r1.sha256 and r2.validation["filtered_totals_match"] is False


def test_owner_kredi_section_stays_withheld_and_leaks_nothing(tmp_path):
    p = KT.book(tmp_path)
    store = GEN.GenerationStore()
    own = OV.OwnerRepository({"kredi": OV.OwnerSourceConfig("kredi", str(p)),
                              "gider_gelir": OV.OwnerSourceConfig("gider_gelir", None),
                              "butce": OV.OwnerSourceConfig("butce", None)}, background=False,
                             on_record=store.replace_owner)
    with TestClient(APP.app, headers={"X-StockAgent": "1"}) as cl:
        APP.app.state.generations = store
        APP.app.dependency_overrides[APP.get_owner] = lambda: own
        try:
            text = cl.get("/api/owner/overview").text + cl.get("/api/accuracy/status").text
            st = {m["key"]: m for m in cl.get("/api/accuracy/status").json()["metrics"]}["owner.kredi"]
        finally:
            APP.app.dependency_overrides.clear()
            APP.app.state.generations = None
    assert st["state"] != "verified"
    for secret in ("KRD-ZQX", "Kurgu Katılım", "Hayali Yatırım", "250000", "4120.11", "4111.11", "987654",
                   "Kurgu açıklama"):
        assert secret not in text, secret


# ---------------------------------------------------------------------------------------------
# 17 + 18: no financial data in summaries / evidence; no AI; the reconciliation command
# ---------------------------------------------------------------------------------------------
SECRETS = ("KRD-ZQX", "Kurgu Katılım", "Hayali Yatırım", "250,000", "250000", "4,120.11", "4120.11", "987654",
           "Kurgu açıklama", "BNK-0")


def test_17_summary_and_owner_record_carry_no_financial_values(tmp_path):
    res = takip(tmp_path)
    text = json.dumps(res.summary(), ensure_ascii=False, default=str) + res.report.to_json()
    store = GEN.GenerationStore()
    repo = OV.OwnerRepository({"kredi": OV.OwnerSourceConfig("kredi", str(KT.book(tmp_path / "o")))},
                              background=False, on_record=store.replace_owner)
    repo.ensure_loaded()
    text += json.dumps(store.current().record("kredi").to_dict(), ensure_ascii=False, default=str)
    for s in SECRETS:
        assert s not in text, s


def _reconcile(*args):
    return subprocess.run([sys.executable, "-m", "scripts.reconcile_sources", *args], cwd=ROOT, capture_output=True,
                          text=True)


def test_reconcile_command_default_output_is_privacy_safe(tmp_path):
    p = KT.book(tmp_path)
    ev = tmp_path / "evidence.json"
    r = _reconcile("--kredi", str(p), "--today", TODAY.isoformat(), "--evidence-out", str(ev))
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "teknik doğrulamadır" in out.lower() or "finansal onay değildir" in out.lower() or "onay" in out.lower()
    for s in SECRETS:
        assert s not in out, s
        assert s not in ev.read_text(encoding="utf-8"), s
    d = json.loads(ev.read_text(encoding="utf-8"))
    res = {x["code"]: x for x in d["results"]}
    assert res["layout"]["status"] == "PASS" and res["import"]["installments"] == 9
    assert res["saved_filter"]["hidden_rows"] == 5 and res["saved_filter"]["visible_rows"] == 4
    assert res["hidden_positive_remaining"]["status"] == "OPEN" and res["hidden_positive_remaining"]["count"] == 3
    assert res["negative_remaining"]["status"] == "OPEN"
    assert res["kapali_positive_remaining"]["status"] == "OPEN"
    assert res["snapshot_date"]["status"] == "OPEN"                   # unknown unless declared; never the file time
    assert d["kredi"]["sha256"] == GEN.sha256_file(str(p))


def test_reconcile_values_only_on_request_and_exit_codes(tmp_path):
    p = KT.book(tmp_path)
    shown = _reconcile("--kredi", str(p), "--today", TODAY.isoformat(), "--show-values")
    assert shown.returncode == 0 and "installment_total=" in shown.stdout
    bad = _reconcile("--kredi", str(KT.book(tmp_path / "s", stale_subtotal=True)), "--today", TODAY.isoformat())
    assert bad.returncode == 1 and "[FAIL]" in bad.stdout
    wrong = _reconcile("--kredi", str(p), "--expect-sha256", "0" * 64)
    assert wrong.returncode == 1
    assert _reconcile().returncode == 2
    assert _reconcile("--kredi", str(tmp_path / "yok.xlsx")).returncode == 2
    declared = _reconcile("--kredi", str(p), "--today", TODAY.isoformat(), "--snapshot-date", "2026-10-18")
    assert "[PASS] Kaynağın iş anlık tarihi beyan edildi" in declared.stdout


def test_reconcile_never_writes_the_source(tmp_path):
    p = KT.book(tmp_path)
    before = p.read_bytes()
    _reconcile("--kredi", str(p), "--today", TODAY.isoformat())
    assert p.read_bytes() == before


def test_18_no_ai_in_kredi_paths():
    for rel in ("services/kredi_schedule.py", "services/importers/kredi.py", "scripts/reconcile_sources.py",
                "services/generation.py", "services/source_validation.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for token in ("openai", "anthropic", "services.ai", "get_provider", "requests.", "httpx", "urllib.request"):
            assert token not in src, (rel, token)


def test_kredi_is_not_unlocked():
    m = ACC.REGISTER["owner.kredi"]
    assert m.certified != ACC.State.VERIFIED and m.blockers
    assert ACC.current_gate(today=TODAY).decide("owner.kredi").state != ACC.State.VERIFIED
