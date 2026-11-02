"""M8-ACCURACY-2B second review of PR #29 — column-footer problems vs genuinely source-wide totals.

The strict source-level verdict `totals_consistent` stays exactly as before (declaration capped by the whole-footer
reconciliation: a partial footer → None, a contradiction → False) and still governs every UNSCOPED metric. A metric
with an audited scope is judged instead on (a) the fingerprint-bound declaration — source-wide — and (b) the footer
result of each field it reads; an unrelated column's footer problem does not reach it. Fail closed whenever the
per-column evidence cannot stand on its own (no matched footer cell, ambiguous trailing rows, no footer evidence for a
used field, an internally inconsistent record).

Evidence is produced by the real pipeline (synthetic workbook → inspection → generation manifest) and judged by the
real `Gate`; only the time / approval facts the synthetic workbook cannot carry are filled in explicitly.
REAL production gate (`production_gate`). Synthetic workbooks only. No AI, no network.
"""
import dataclasses
import datetime as dt

import pytest

import dashboard_builder as db
from services import accuracy as ACC
from tests import release_state as REL
from services import generation as GEN
from tests.fixtures.xlsx_synth import F, MISSING
from tests.test_m8_accuracy2 import TODAY
from tests.test_m8_accuracy2b import COLI, _gate, _metric, agg_row, book, products
from tests.test_m8_accuracy2b_review import THEAD, _error_book, approved, tbook  # noqa: F401

pytestmark = pytest.mark.production_gate
S, NV, INV = ACC.State.VERIFIED, ACC.State.NOT_VERIFIED, ACC.State.INVALID

# the synthetic export aggregates D (quantity), F (minseviye), G (maxseviye) and H (Envanter değeri = cost)
QTY = _metric("t.quantity", (ACC.Requirement("stock", ("Stok_Kodu", "TOPLAM STOK MİKTAR ", "minseviye",
                                                       "maxseviye")),))
COST = _metric("t.cost", (ACC.Requirement("stock", ("Stok_Kodu", "Envanter değeri")),))
STRICT = _metric("t.strict")                                         # no declared dependencies


def pipeline_ev(path, *, totals=True, **override):
    """Stock evidence exactly as the generation manifest records it, with an owner declaration bound to the file's
    fingerprint; only the facts a workbook cannot carry (declared date, completeness, joins) are filled in."""
    frozen = {"stock": GEN.FrozenSource.read("stock", str(path))}
    decl = {"stock": {"approval": {"sha256": frozen["stock"].sha256, "totals_consistent": totals,
                                   "mapping_approved": True}}}
    m = GEN.build_manifest(frozen, {"stock": db.read_stock(str(path))}, decl, "explicit", TODAY)
    ev = m.record("stock").evidence
    fill = dict(report_date=TODAY - dt.timedelta(days=2), report_date_origin="declared", period_complete=True,
                joins_validated=True, join_unmatched=0, join_ambiguous=0)
    fill.update(override)
    return dataclasses.replace(ev, **fill)


def decide(ev, *metrics):
    g = _gate(list(metrics), ev)
    return {m.key: g.decide(m.key) for m in metrics}


def _cost_footer(kind):
    """Footer rows in which ONLY the cost column H is unreconciled ('open': no cached value) or contradictory."""
    def make(rows, f, l, n):
        out = agg_row(rows, f, l, n)
        if kind == "open":
            out[COLI["H"]] = F(f"(SUM(H{f}:H{l}))", MISSING)
        else:
            out[COLI["H"]] = F(f"(SUM(H{f}:H{l}))", 123456.0)
        return out
    return make


@pytest.fixture
def cost_open(tmp_path):
    return book(tmp_path / "open.xlsx", products(div0=()), footer=(_cost_footer("open"), "label", "subtotal"))


@pytest.fixture
def cost_fail(tmp_path):
    return book(tmp_path / "fail.xlsx", products(div0=()), footer=(_cost_footer("fail"), "label", "subtotal"))


@pytest.fixture
def clean(tmp_path):
    return book(tmp_path / "clean.xlsx", products(div0=()))


# ---------------------------------------------------------------------------------------------
# the baseline: everything reconciles → the synthetic metrics verify (inside the test register only)
# ---------------------------------------------------------------------------------------------
def test_baseline_fully_reconciled_footer_lets_every_synthetic_metric_verify(clean):
    ev = pipeline_ev(clean)
    assert ev.footer_totals == "PASS" and ev.footer_scope == "per_field" and ev.totals_consistent is True
    d = decide(ev, QTY, COST, STRICT)
    assert {k: v.state for k, v in d.items()} == {"t.quantity": S, "t.cost": S, "t.strict": S}


# ---------------------------------------------------------------------------------------------
# 1 / 3 / 4 — quantity footer PASS, unrelated cost footer OPEN
# ---------------------------------------------------------------------------------------------
def test_cost_footer_open_keeps_the_source_open_but_not_the_quantity_metric(cost_open):
    ev = pipeline_ev(cost_open)
    # the source-level reconciliation stays OPEN and the strict verdict is unchanged …
    assert ev.footer_totals == "OPEN" and ev.totals_consistent is None and ev.declared_totals_consistent is True
    assert (NV, "Kaynak toplamlarının mutabakatı yapılmadı.") in ev.problems()
    f = ev.fields
    assert f["TOPLAM STOK MİKTAR "]["footer_totals"] == "PASS" and f["Envanter değeri"]["footer_totals"] == "OPEN"
    d = decide(ev, QTY, COST, STRICT)
    assert d["t.quantity"].state == S                                  # 3: not inherited
    assert d["t.cost"].state == NV                                     # 4: withheld …
    assert any("Envanter değeri" in r and "doğrulanamadı" in r for r in d["t.cost"].reasons_tr)
    assert d["t.strict"].state == NV                                   # 8: unscoped stays strict


# ---------------------------------------------------------------------------------------------
# 2 / 3 / 4 — quantity footer PASS, unrelated cost footer FAIL
# ---------------------------------------------------------------------------------------------
def test_cost_footer_contradiction_invalidates_the_source_verdict_but_not_the_quantity_metric(cost_fail):
    ev = pipeline_ev(cost_fail)
    assert ev.footer_totals == "FAIL" and ev.totals_consistent is False
    assert (INV, "Kaynaktaki toplamlar birbiriyle çelişiyor.") in ev.problems()
    d = decide(ev, QTY, COST, STRICT)
    assert d["t.quantity"].state == S
    assert d["t.cost"].state == INV
    assert any("Envanter değeri" in r and "çelişiyor" in r for r in d["t.cost"].reasons_tr)
    assert d["t.strict"].state == INV


def test_the_real_data_shape_error_propagated_cost_column(tmp_path):
    """As on the real export: an error cell inside one aggregated column, Excel caches the propagated error."""
    ev = pipeline_ev(_error_book(tmp_path / "e.xlsx"))
    assert ev.footer_totals == "OPEN" and ev.footer_scope == "per_field"
    d = decide(ev, QTY, COST, STRICT)
    assert d["t.quantity"].state == S and d["t.cost"].state == INV and d["t.strict"].state == INV


# ---------------------------------------------------------------------------------------------
# 5 — genuinely source-wide problems still block every affected output
# ---------------------------------------------------------------------------------------------
def test_a_declared_source_wide_contradiction_blocks_scoped_metrics(clean):
    ev = pipeline_ev(clean, totals=False)
    assert ev.footer_totals == "PASS" and ev.totals_consistent is False
    for st in (d.state for d in decide(ev, QTY, COST, STRICT).values()):
        assert st == INV


def test_without_a_totals_declaration_nothing_verifies(cost_open):
    ev = pipeline_ev(cost_open, declared_totals_consistent=None)
    d = decide(ev, QTY, COST, STRICT)
    assert all(v.state != S for v in d.values())
    assert any(r == "Kaynak toplamlarının mutabakatı yapılmadı." for r in d["t.quantity"].reasons_tr)


def test_a_footer_that_reconciles_nothing_is_source_wide(tmp_path):
    p = book(tmp_path / "none.xlsx", products(div0=()), cache=False)       # every footer cell lacks its cache
    ev = pipeline_ev(p)
    assert ev.footer_totals == "OPEN" and ev.footer_scope == "source_wide"
    d = decide(ev, QTY, COST)
    assert d["t.quantity"].state == NV and d["t.cost"].state == NV


def test_ambiguous_trailing_rows_keep_the_strict_verdict(tmp_path):
    rows = products(div0=())
    stray = ["?", None, None, 5, None, None, None, None, None, None]        # a trailing row no rule recognizes
    p = book(tmp_path / "amb.xlsx", rows, footer=("sum", "label", "subtotal"), extra=[stray])
    ev = pipeline_ev(p)
    assert ev.footer_scope == "source_wide"
    assert decide(ev, QTY)["t.quantity"].state != S


@pytest.mark.parametrize("bad", [dict(duplicate_records=1), dict(missing_required_values=2),
                                 dict(inspection_failures=("x",)), dict(selection_explicit=False),
                                 dict(external_references=1), dict(mapping_approved=None)])
def test_other_source_integrity_failures_still_block_the_quantity_metric(cost_open, bad):
    assert decide(pipeline_ev(cost_open, **bad), QTY)["t.quantity"].state != S


def test_a_used_join_is_still_judged(cost_open):
    uses = _metric("t.qty_join", (ACC.Requirement("stock", ("Stok_Kodu", "TOPLAM STOK MİKTAR "),
                                                  ("sales_product_in_stock",)),))
    ev = pipeline_ev(cost_open, joins={"sales_product_in_stock": {"unmatched": 3, "ambiguous": 0,
                                                                  "validated": False}})
    assert decide(ev, uses)["t.qty_join"].state == INV


# ---------------------------------------------------------------------------------------------
# 6 — missing or inconsistent per-field footer evidence fails closed
# ---------------------------------------------------------------------------------------------
def test_missing_per_field_footer_evidence_cannot_verify(cost_open):
    ev = pipeline_ev(cost_open)
    f = {k: dict(v) for k, v in ev.fields.items()}
    del f["TOPLAM STOK MİKTAR "]["footer_totals"]
    d = decide(dataclasses.replace(ev, fields=f), QTY)["t.quantity"]
    assert d.state == NV and any("rapor toplamı kanıtı yok" in r for r in d.reasons_tr)
    f2 = {k: {**v, "footer_totals": "SOMETHING"} for k, v in ev.fields.items()}
    assert decide(dataclasses.replace(ev, fields=f2), QTY)["t.quantity"].state == NV
    assert decide(dataclasses.replace(ev, fields=None), QTY)["t.quantity"].state == NV
    assert decide(dataclasses.replace(ev, footer_scope=None), QTY)["t.quantity"].state == NV   # strict verdict


def test_an_internally_inconsistent_totals_record_fails_closed(cost_open, cost_fail):
    for ev in (dataclasses.replace(pipeline_ev(cost_open), totals_consistent=True),
               dataclasses.replace(pipeline_ev(cost_fail), totals_consistent=True),
               dataclasses.replace(pipeline_ev(cost_fail), totals_consistent=None)):
        assert not ev._field_level_totals(QTY.scope("stock"))           # no per-field relief for a corrupt record
        for k, d in decide(ev, QTY, COST, STRICT).items():
            assert d.state == INV, k
    bogus = dataclasses.replace(pipeline_ev(cost_open), footer_totals="MAYBE")
    assert decide(bogus, QTY)["t.quantity"].state == INV


def test_a_metric_reading_no_field_of_the_source_keeps_the_strict_verdict(cost_open):
    empty = ACC.Requirement("stock", ())
    ev = pipeline_ev(cost_open)
    assert not ev._field_level_totals(empty)


# ---------------------------------------------------------------------------------------------
# 7 — ambiguous identity / altered template stays INVALID
# ---------------------------------------------------------------------------------------------
def test_duplicated_identity_outside_a_template_stays_invalid(tmp_path):
    p = tbook(tmp_path / "dup.xlsx")                                  # A and J both 'Stok_Kodu'; no template
    ev = pipeline_ev(p)
    assert ev.layout_supported is False and ev.footer_scope == "source_wide"
    for k, d in decide(ev, QTY, COST, STRICT).items():
        assert d.state == INV, k


def test_altered_approved_template_stays_invalid(tmp_path, approved):
    p = tbook(tmp_path / "moved.xlsx", head=[THEAD[0], THEAD[2], THEAD[1]] + THEAD[3:])
    ev = pipeline_ev(p)
    assert ev.layout_supported is False
    assert decide(ev, QTY)["t.quantity"].state == INV
    ok = pipeline_ev(tbook(tmp_path / "ok.xlsx"))                    # the approved layout itself is accepted
    assert ok.layout_supported is True and ok.fields["Stok_Kodu"]["duplicate_header"] is False


# ---------------------------------------------------------------------------------------------
# 9 — production
# ---------------------------------------------------------------------------------------------
def test_no_stock_metric_becomes_verified(cost_open, clean):
    REL.assert_release_state()                    # D-073: one certified metric (the Cari list), flag True
    for ev in (pipeline_ev(cost_open), pipeline_ev(clean)):
        g = ACC.Gate(ACC.REGISTER, ACC.DEFINITIONS, ACC.EvidenceSnapshot("g", {"stock": ev}), TODAY, "g")
        assert [k for k in ACC.REGISTER if g.decide(k).state == S] == []
