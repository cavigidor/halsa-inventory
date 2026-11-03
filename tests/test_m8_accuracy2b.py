"""M8-ACCURACY-2B — precise validation and safe diagnostics (after the first owner-local real-data acceptance).

1. Stock export report-control (footer) rows are recognized deterministically, reconciled, and never counted as
   products; anything that does not satisfy the rules fails closed as a row with missing identity.
2. Product counts: parsed rows / footer rows / product-code rows / distinct codes / duplicate occurrences / rows with
   missing or ambiguous identity — nothing deduplicated, nothing hardcoded; the count stays withheld.
3. Metric dependency scoping: a metric is judged by the fields and joins it actually uses; an undeclared metric keeps
   the strict source-wide judgement; a missing dependency check is NOT_VERIFIED.
4. Restart publication: GET / waits (bounded) for an in-progress valid re-publication; genuine failures stay 503.
5. Office lock files ('~$…') are never sources.

REAL production gate (`production_gate`). Synthetic workbooks only; a metric is certified only on synthetic register
copies inside tests. No AI, no network.
"""
import dataclasses
import datetime as dt
import json
import os
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

import app as APP
import dashboard_builder as db
from services import accuracy as ACC
from tests import release_state as REL
from services import generation as GEN
from services import refresh as RF
from services import source_validation as SV
from tests.fixtures.xlsx_synth import E, F, MISSING, write_xlsx
from tests.test_m8_accuracy2 import (SE, TODAY, promoted, refresh, selection, world, write_stock,  # noqa: F401
                                     stock_rows)
from tests.test_m8_accuracy2_review2 import close, data_of, live_client, displayed_stock_count, restart  # noqa: F401

pytestmark = pytest.mark.production_gate
S, NV, INV = ACC.State.VERIFIED, ACC.State.NOT_VERIFIED, ACC.State.INVALID
DIV0 = E("#DIV/0!")

# synthetic stock export (structure like the real one: label column, identity, quantities, coverage, helper column)
HEAD = ["Özel Grup2", "Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye",
        "Envanter değeri", "DURUM", "Yardımcı"]
AGG_COLS = ("D", "F", "G", "H")               # quantity, min, max, inventory value (coverage is never aggregated)
COLI = {c: i for i, c in enumerate("ABCDEFGHIJ")}


def products(n=5, *, div0=(1,), dup=False):
    rows = []
    for i in range(n):
        cov = DIV0 if i in div0 else 1.5 + i
        rows.append(["G1", f"SP-{100 + i}", f"Sentetik Ürün {i}", 10 + i, F("D5/1", cov) if cov is DIV0 else cov,
                     1.0, 3.0, 100.0 * (i + 1), "", None])
    if dup:
        rows.append(["G1", "SP-100", "Sentetik Ürün 0 (ikinci)", 7, 2.0, 1.0, 3.0, 50.0, "", None])
    return rows


def _num(v):
    return v if isinstance(v, (int, float)) else None


def agg_row(rows, first, last, r, fn="SUM", *, label="TOPLAM", tamper=None, cache=True, paren=True):
    out = [label, None, None, None, None, None, None, None, None, F(f'IF(B{r}<>"",B{r}*1,"")', "")]
    for c in AGG_COLS:
        total = sum(_num(x[COLI[c]]) or 0 for x in rows)
        if tamper == c:
            total += 999
        body = f"SUM({c}{first}:{c}{last})" if fn == "SUM" else f"SUBTOTAL({fn},{c}{first}:{c}{last})"
        out[COLI[c]] = F(f"({body})" if paren else body, total if cache else MISSING)
    return out


def label_row(r, text="—", label="TOPLAM"):
    out = [label, None, None, None, None, None, None, None, None, F(f'IF(B{r}<>"",B{r}*1,"")', "")]
    for c in AGG_COLS:
        out[COLI[c]] = text
    return out


def book(path, prod_rows, footer=("sum", "label", "subtotal"), *, extra=None, head=HEAD, **kw):
    """Title rows 1–3, header row 4, products from row 5, then the requested trailing rows."""
    first, last = 5, 4 + len(prod_rows)
    data = [["Sentetik stok raporu"], ["(test)"], [None], list(head)] + [list(r) for r in prod_rows]
    r = last
    for kind in footer:
        r += 1
        if kind == "sum":
            data.append(agg_row(prod_rows, first, last, r, "SUM", **kw))
        elif kind == "subtotal":
            data.append(agg_row(prod_rows, first, last, r, "9", **kw))
        elif kind == "label":
            data.append(label_row(r))
        else:
            data.append(kind(prod_rows, first, last, r))
    for row in extra or ():
        data.append(row)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return write_xlsx(Path(path), {"Sheet1": data})


def analyze(p):
    return SV.analyze_stock_rows(Path(p).read_bytes())


# =============================================================================================
# Task 1 — footer recognition
# =============================================================================================
def test_supported_footer_block_is_recognized_and_reconciled(tmp_path):
    p = book(tmp_path / "s.xlsx", products())
    a = analyze(p)
    assert a.ok and a.footer_kinds == {"aggregate": 2, "label": 1} and len(a.footer_rows) == 3
    assert a.ambiguous_rows == () and a.missing_identity_rows == 0
    assert a.footer_checks == {"checked": 8, "matched": 8, "mismatched": 0, "error_propagated": 0,
                               "missing_cache": 0, "unsupported": 0}
    ins = SV.inspect_core("stock", p.read_bytes())
    assert ins.missing_required_values == 0 and ins.data_rows == 5           # footers are not data rows
    assert ins.columns["Stok_Kodu"]["empty"] == 0
    assert ins.summary()["rows"]["counts"]["footer_rows"] == 3


def test_footer_formula_without_parentheses_and_109_are_supported(tmp_path):
    p = book(tmp_path / "s.xlsx", products(), footer=("sum", "subtotal"), paren=False)
    a = analyze(p)
    assert len(a.footer_rows) == 2 and a.footer_checks["matched"] == 8
    rows = products()
    p2 = book(tmp_path / "s2.xlsx", rows, footer=(lambda r, f, l, n: agg_row(r, f, l, n, "109"),))
    assert analyze(p2).footer_kinds == {"aggregate": 1, "label": 0}


def test_genuine_empty_rows_are_ignored_but_blank_code_data_rows_fail(tmp_path):
    rows = products()
    rows.insert(2, [None] * 10)                                              # a genuinely empty row
    rows.insert(3, ["G1", None, "Kodsuz satır", 4, 2.0, 1.0, 3.0, 9.0, "", None])   # data without identity
    a = analyze(book(tmp_path / "s.xlsx", rows))
    assert a.code_rows == 5 and a.missing_identity_rows == 1 and len(a.footer_rows) == 3
    ins = SV.inspect_core("stock", (tmp_path / "s.xlsx").read_bytes())
    assert ins.missing_required_values == 1                                  # still a missing identity (INVALID)


@pytest.mark.parametrize("make, why", [
    (lambda r, f, l, n: ["TOPLAM", None, None, 5, None, None, None, None, None, None], "numeric literal"),
    (lambda r, f, l, n: [None, None, "Sentetik Ürün X", 5, 2.0, 1, 3, 9, "", None], "product-like data, no code"),
    (lambda r, f, l, n: ["TOPLAM"] + [None] * 9, "text only, no aggregate"),
    (lambda r, f, l, n: ["TOPLAM", None, None, F(f"SUM(D{f}:D{l - 1})", 1), None, None, None, None, None, None],
     "range misses the last product"),
    (lambda r, f, l, n: ["TOPLAM", None, None, F(f"SUM(F{f}:F{l})", 1), None, None, None, None, None, None],
     "aggregates another column"),
    (lambda r, f, l, n: ["TOPLAM", None, None, F(f"SUBTOTAL(2,D{f}:D{l})", 5), None, None, None, None, None, None],
     "SUBTOTAL count function"),
    (lambda r, f, l, n: ["TOPLAM", None, None, F(f"SUM(D{f}:D{l})+1", 1), None, None, None, None, None, None],
     "arithmetic around the aggregate"),
    (lambda r, f, l, n: ["TOPLAM", None, None, F(f"SUM(D{f}:D{l})", 60), None, None, None, None, None,
                         F("D3*2", 4)], "formula referencing another row"),
])
def test_misleading_footer_like_rows_fail_closed(tmp_path, make, why):
    a = analyze(book(tmp_path / "s.xlsx", products(), footer=(make,)))
    assert a.footer_rows == () and len(a.ambiguous_rows) == 1, why
    assert a.missing_identity_rows == 1
    assert SV.inspect_core("stock", (tmp_path / "s.xlsx").read_bytes()).missing_required_values == 1


def test_label_row_needs_an_adjacent_aggregate_row(tmp_path):
    a = analyze(book(tmp_path / "s.xlsx", products(), footer=("label",)))
    assert a.footer_rows == () and len(a.ambiguous_rows) == 1
    mixed = lambda r, f, l, n: dict(enumerate(label_row(n))) and [  # noqa: E731
        "TOPLAM", None, None, "—", None, "başka", "—", "—", None, F(f'IF(B{n}<>"",B{n}*1,"")', "")]
    b = analyze(book(tmp_path / "s2.xlsx", products(), footer=("sum", mixed)))
    assert b.footer_kinds == {"aggregate": 1, "label": 0} and len(b.ambiguous_rows) == 1   # two different texts


def test_a_blank_code_row_before_the_last_product_is_never_a_footer(tmp_path):
    rows = products(3)
    data_first, data_last = 5, 7
    footer_like = agg_row(rows, data_first, data_last, 6)
    rows.insert(1, footer_like)                                    # SUM-like row between products
    a = analyze(book(tmp_path / "s.xlsx", rows, footer=()))
    assert a.footer_rows == () and a.missing_identity_rows == 1


def test_contradictory_footer_totals_make_the_source_invalid(tmp_path):
    p = book(tmp_path / "s.xlsx", products(), tamper="H")
    a = analyze(p)
    assert len(a.footer_rows) == 3 and a.footer_checks["mismatched"] == 2      # SUM and SUBTOTAL of column H
    frozen = {"stock": GEN.FrozenSource.read("stock", str(p))}
    m = GEN.build_manifest(frozen, {"stock": db.read_stock(str(p))}, {}, "explicit", TODAY)
    ev = m.record("stock").evidence
    assert ev.totals_consistent is False and (INV, "Kaynaktaki toplamlar birbiriyle çelişiyor.") in ev.problems()


def test_missing_cache_and_error_propagation_are_not_reported_as_matches(tmp_path):
    a = analyze(book(tmp_path / "s.xlsx", products(), footer=("sum",), cache=False))
    assert a.footer_checks["missing_cache"] == 4 and a.footer_checks["matched"] == 0
    rows = products()
    rows[2][COLI["H"]] = E("#REF!")                                  # an error inside an aggregated range
    def errrow(r, f, l, n):
        out = agg_row(r, f, l, n)
        out[COLI["H"]] = F(f"(SUM(H{f}:H{l}))", E("#REF!"))
        return out
    b = analyze(book(tmp_path / "s2.xlsx", rows, footer=(errrow,)))
    assert b.footer_checks["error_propagated"] == 1 and b.footer_checks["mismatched"] == 0


def test_numeric_total_cached_although_the_range_has_an_error_is_contradictory(tmp_path):
    rows = products()
    rows[2][COLI["H"]] = E("#REF!")
    a = analyze(book(tmp_path / "s.xlsx", rows, footer=("sum",)))
    assert a.footer_checks["mismatched"] == 1


def test_footer_analysis_never_modifies_the_workbook(tmp_path):
    p = book(tmp_path / "s.xlsx", products())
    before = p.read_bytes()
    analyze(p)
    SV.inspect_core("stock", before)
    db.m_stock(str(p))
    assert p.read_bytes() == before


def test_hidden_rows_make_subtotal_reconciliation_unsupported(tmp_path):
    rows = products()
    data = [["t"], ["t"], [None], list(HEAD)] + [list(r) for r in rows] + [agg_row(rows, 5, 9, 10, "9")]
    p = write_xlsx(tmp_path / "h.xlsx", {"Sheet1": data}, hidden_rows={"Sheet1": [6]})
    a = analyze(p)
    assert a.footer_checks["unsupported"] == 4 and a.footer_checks["matched"] == 0


# =============================================================================================
# Task 2 — counting definitions
# =============================================================================================
def test_row_counts_separate_footers_codes_duplicates_and_missing_identity(tmp_path):
    rows = products(6, dup=True)
    rows.insert(4, ["G1", None, "Kodsuz", 1, 2.0, 1.0, 3.0, 9.0, "", None])
    p = book(tmp_path / "s.xlsx", rows)
    a = analyze(p)
    assert a.counts() == {"parsed_rows": 8 + 3, "footer_rows": 3, "code_rows": 7, "distinct_codes": 6,
                          "duplicate_code_occurrences": 1, "missing_identity_rows": 1}
    assert set(SV.StockRowAnalysis.LABELS_TR) == set(a.counts())
    frame = db.stock_row_counts(db.read_stock(str(p)))
    assert frame == {"parsed_rows": 11, "code_rows": 7, "distinct_codes": 6, "duplicate_code_occurrences": 1,
                     "rows_without_code": 4}
    low, over, total = db.m_stock(str(p))
    assert total == 7                                                   # product-code rows, not deduplicated


def test_product_count_is_not_hardcoded_and_footers_never_count(tmp_path):
    for n in (2, 9):
        p = book(tmp_path / f"s{n}.xlsx", products(n, div0=()))
        assert db.m_stock(str(p))[2] == n and analyze(p).code_rows == n


def test_identity_header_ambiguity_is_refused_not_resolved(tmp_path):
    """Correction pass (D-056): a duplicated identity header outside an approved template is refused — never 'the
    first column'. (The approved DinamikCRM template is covered in tests/test_m8_accuracy2b_review.py.)"""
    head = HEAD[:9] + ["Stok_Kodu"]                                      # a second, different 'Stok_Kodu' column
    rows = [r[:9] + [f"ALT-{i}"] for i, r in enumerate(products())]
    p = book(tmp_path / "s.xlsx", rows, footer=(), head=head)
    a = analyze(p)
    assert not a.ok and a.failure == "identity_header_ambiguous" and a.identity_header_columns == 2
    assert a.code_rows == 0 and a.template["status"] == "unapproved_duplicate_identity"
    ins = SV.inspect_core("stock", p.read_bytes())
    assert ins.layout_supported is False and ins.fields()["Stok_Kodu"]["duplicate_header"] is True


def test_coverage_keeps_undefined_apart_from_missing_and_numbers(tmp_path):
    rows = products(5, div0=(1, 3))
    rows[3][COLI["D"]] = 0                                               # undefined coverage, no stock
    rows[4][COLI["E"]] = None                                            # blank coverage
    rows[0][COLI["D"]] = None                                            # missing stock quantity
    a = analyze(book(tmp_path / "s.xlsx", rows))
    assert a.coverage == {"undefined_div0": 2, "other_error": 0, "blank": 1, "numeric": 2,
                          "undefined_with_stock": 1, "stock_quantity_missing": 1}
    low, over, _ = db.m_stock(str(tmp_path / "s.xlsx"))
    codes = {r["code"] for r in low + over}
    assert "SP-101" not in codes and "SP-103" not in codes              # no zero / infinity / classification invented


def test_refresh_report_and_dashboard_header_withhold_the_product_count(world, monkeypatch):
    book(Path(world.folder) / SE.STOCK_FILE, [list(r) for r in stock_rows()], footer=("sum", "label", "subtotal"))
    rep = refresh(world.folder, world.dash, install=world.install)
    assert rep["ok"] and rep["counts"]["products"] is None              # stock.product_counts is not verified
    html = open(world.dash, encoding="utf-8").read()
    assert " ürün</div>" not in html
    rec = world.store.current().record("stock")
    assert rec.validation["rows"]["counts"]["footer_rows"] == 3 and rec.evidence.missing_required_values == 0
    assert rec.evidence.duplicate_records == 0                          # footers never count as duplicates
    # with a synthetic certification the header shows exactly the product-code rows
    reg = dict(ACC.REGISTER)
    reg["stock.product_counts"] = dataclasses.replace(reg["stock.product_counts"], blockers=(), certified=S,
                                                      approval_refs=("D-999",), regression_tests=("t",),
                                                      reconciliation_ref="synthetic")
    monkeypatch.setattr(ACC, "REGISTER", reg)
    out = str(Path(world.dash).with_name("h.html"))
    db.build(world.folder, out, today=TODAY, gate=ACC.AlgorithmHarnessGate(today=TODAY)
             if hasattr(ACC, "AlgorithmHarnessGate") else _allow_gate())
    n = len(stock_rows())
    assert f" · {n:,} ürün</div>" in open(out, encoding="utf-8").read()


def _allow_gate():
    from tests.accuracy_harness import AlgorithmHarnessGate
    return AlgorithmHarnessGate(today=TODAY)


def test_dq4_count_excludes_report_total_rows(tmp_path):
    d = SE.write_all(str(tmp_path / "d"))
    rows = [list(r) for r in stock_rows()]
    book(Path(d) / SE.STOCK_FILE, rows)
    from services import context as CTX
    ctx = CTX.build_context(d, inflation_rate=0.4)
    cov_missing = sum(1 for r in rows if not isinstance(r[COLI["E"]], (int, float)))
    assert ctx.data_quality["stock_coverage_unavailable_products"] == cov_missing


# =============================================================================================
# Task 3 — dependency scoping
# =============================================================================================
def _fields(**bad):
    base = {h: {"error_cells": 0, "missing_cached_formulas": 0, "blank_cells": 0, "invalid_dates": 0,
                "external_refs": 0, "duplicate_header": False, "amount": h in SV.CORE["stock"]["amounts"]}
            for h in SV.CORE["stock"]["required"]}
    for h, d in bad.items():
        base[h] = {**base[h], **d}
    return base


def _ev(**kw):
    base = dict(available=True, layout_supported=True, report_date=TODAY - dt.timedelta(days=2),
                report_date_origin="declared", period_complete=True, error_cells=0, missing_cached_formulas=0,
                missing_required_values=0, duplicate_records=0, join_unmatched=0, join_ambiguous=0,
                joins_validated=True, totals_consistent=True, mapping_approved=True, blank_amount_cells=0,
                invalid_dates=0, ambiguous_headers=0, external_references=0, date_contradictions=0,
                selection_explicit=True, fields=_fields(),
                joins={j: {"unmatched": 0, "ambiguous": 0, "validated": True} for j in SV.JOINS})
    base.update(kw)
    return ACC.SourceEvidence("stock", **base)


def _metric(key, requires=(), sources=("stock",)):
    return ACC.Metric(key, key, ("test",), sources, certified=S, approval_refs=("D-999",), regression_tests=("t",),
                      reconciliation_ref="synthetic", requires=requires)


def _gate(metrics, ev):
    reg = {m.key: m for m in metrics}
    ACC.validate_register(reg, production=False)
    return ACC.Gate(reg, ACC.DEFINITIONS, ACC.EvidenceSnapshot("g", {"stock": ev}), TODAY, "g")


COVERAGE_BROKEN = _ev(error_cells=1178, fields=_fields(**{"STOK Kontrol %": {"error_cells": 1178}}))


def test_an_invalid_coverage_field_does_not_contaminate_an_independent_quantity_metric():
    qty = _metric("t.quantity", (ACC.Requirement("stock", ("Stok_Kodu", "TOPLAM STOK MİKTAR ")),))
    g = _gate([qty], COVERAGE_BROKEN)
    assert g.decide("t.quantity").state == S


def test_a_metric_that_uses_the_invalid_field_stays_withheld():
    cov = _metric("t.coverage", (ACC.Requirement("stock", ("Stok_Kodu", "STOK Kontrol %")),))
    d = _gate([cov], COVERAGE_BROKEN).decide("t.coverage")
    assert d.state == INV and any("STOK Kontrol %" in r for r in d.reasons_tr)


def test_an_undeclared_metric_keeps_the_strict_source_wide_judgement():
    strict = _metric("t.strict")
    assert _gate([strict], COVERAGE_BROKEN).decide("t.strict").state == INV


def test_joins_only_judge_metrics_that_use_them():
    broken = _ev(joins_validated=False, join_unmatched=256,
                 joins={**{j: {"unmatched": 0, "ambiguous": 0, "validated": True} for j in SV.JOINS},
                        "sales_product_in_stock": {"unmatched": 256, "ambiguous": 1, "validated": False}})
    no_join = _metric("t.stock_only", (ACC.Requirement("stock", ("Stok_Kodu",)),))
    uses = _metric("t.uses_join", (ACC.Requirement("stock", ("Stok_Kodu",), ("sales_product_in_stock",)),))
    other = _metric("t.other_join", (ACC.Requirement("stock", ("Stok_Kodu",), ("order_customer_in_master",)),))
    g = _gate([no_join, uses, other], broken)
    assert g.decide("t.stock_only").state == S
    assert g.decide("t.uses_join").state == INV
    assert g.decide("t.other_join").state == S
    assert _gate([_metric("t.strict")], broken).decide("t.strict").state == INV


def test_missing_dependency_evidence_is_not_verified():
    m = _metric("t.m", (ACC.Requirement("stock", ("Stok_Kodu", "TOPLAM STOK MİKTAR "), ("sales_product_in_stock",)),))
    assert _gate([m], _ev(fields=None)).decide("t.m").state == NV
    no_field = _ev(fields={k: v for k, v in _fields().items() if k != "TOPLAM STOK MİKTAR "})
    assert _gate([m], no_field).decide("t.m").state == NV
    assert _gate([m], _ev(joins={})).decide("t.m").state == NV
    unknown = _ev(joins={"sales_product_in_stock": {"unmatched": None, "ambiguous": None, "validated": None}})
    assert _gate([m], unknown).decide("t.m").state == NV


def test_source_integrity_still_applies_to_scoped_metrics():
    m = _metric("t.m", (ACC.Requirement("stock", ("Stok_Kodu",)),))
    for bad in (dict(missing_required_values=3), dict(duplicate_records=1), dict(selection_explicit=False),
                dict(inspection_failures=("x",)), dict(totals_consistent=False), dict(external_references=1),
                dict(layout_supported=False)):
        assert _gate([m], _ev(**bad)).decide("t.m").state != S, bad


def test_a_duplicated_header_on_a_used_field_withholds():
    m = _metric("t.m", (ACC.Requirement("stock", ("Stok_Kodu", "TOPLAM STOK MİKTAR ")),))
    ev = _ev(ambiguous_headers=1, fields=_fields(**{"Stok_Kodu": {"duplicate_header": True}}))
    assert _gate([m], ev).decide("t.m").state == NV
    other = _metric("t.o", (ACC.Requirement("stock", ("TOPLAM STOK MİKTAR ",)),))
    assert _gate([other], ev).decide("t.o").state == S                 # a field it does not use


def test_blank_amount_cells_only_count_in_used_amount_fields():
    m = _metric("t.m", (ACC.Requirement("stock", ("Stok_Kodu", "DURUM")),))
    ev = _ev(blank_amount_cells=6, fields=_fields(DURUM={"blank_cells": 2651},
                                                  **{"Envanter değeri": {"blank_cells": 6}}))
    assert _gate([m], ev).decide("t.m").state == S                     # DURUM blanks are not amount blanks
    m2 = _metric("t.m2", (ACC.Requirement("stock", ("Stok_Kodu", "Envanter değeri")),))
    assert _gate([m2], ev).decide("t.m2").state == NV


@pytest.mark.parametrize("req, msg", [
    ((ACC.Requirement("stock", ("Nope",)),), "unknown or empty field"),
    ((ACC.Requirement("stock", ()),), "unknown or empty field"),
    ((ACC.Requirement("stock", ("Stok_Kodu",), ("made_up_join",)),), "unknown joins"),
    ((ACC.Requirement("sales", ("Tarih",)),), "exactly once"),
    ((ACC.Requirement("stock", ("Stok_Kodu",)), ACC.Requirement("stock", ("Stok_Adı",))), "exactly once"),
])
def test_requirement_declarations_are_validated(req, msg):
    with pytest.raises(ACC.RegisterError, match=msg):
        ACC.validate_register({"t.m": _metric("t.m", req)}, production=False)


def test_real_declarations_follow_the_traced_code():
    reg = ACC.REGISTER
    assert "STOK Kontrol %" in reg["stock.reorder"].scope("stock").fields      # reorder USES coverage → withheld
    assert reg["stock.reorder"].scope("stock").joins == ()
    assert reg["receivables.collections"].scope("aging").fields == ("Hesap kodu", "Hesap adı", "Temsilci kodu",
                                                                     "bucket:*")
    assert reg["stock.product_counts"].scope("stock").fields == ("Stok_Kodu",)
    undeclared = [k for k, m in reg.items() if m.sources and not m.requires]
    assert "customers.winback" in undeclared and "finance.margin" in undeclared  # strict until audited


def test_real_coverage_errors_still_withhold_reorder_but_not_by_the_join(tmp_path):
    d = SE.write_all(str(tmp_path / "d"))
    rows = [list(r) for r in stock_rows()]
    rows[0][3] = F("C5/1", DIV0)
    write_stock(d, rows)
    src = db.select_sources(d)
    frozen = GEN.freeze(src)
    m = GEN.build_manifest(frozen, GEN.frames_from_frozen(frozen), {}, "explicit", TODAY)
    g = ACC.current_gate(today=TODAY, snapshot=m.core_snapshot(), expected_generation=m.generation_id)
    reorder = g.decide("stock.reorder")
    assert reorder.state == INV and any("STOK Kontrol %" in r for r in reorder.reasons_tr)
    assert not any("eşleşme" in r.lower() for r in reorder.reasons_tr)         # no join judges a stock-only list
    coll = g.decide("receivables.collections")
    assert coll.state != S and not any("eşleşme" in r.lower() for r in coll.reasons_tr)


def test_only_the_d073_metric_is_certified_and_the_flag_is_on():
    ACC.validate_register()
    REL.assert_release_state()                    # D-073: one certified metric, flag True


# =============================================================================================
# Task 4 — restart publication synchronization
# =============================================================================================
def _slow_build(monkeypatch, seconds, fail=False):
    real = db.build
    started = threading.Event()

    def slow(*a, **k):
        started.set()
        time.sleep(seconds)
        if fail:
            raise OSError("synthetic re-publication failure")
        return real(*a, **k)
    monkeypatch.setattr(db, "build", slow)
    return started


def _warm_up_in_background():
    t = threading.Thread(target=lambda: APP.app.state.repo.context(), daemon=True)
    t.start()
    return t


def _until(pred, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def published_restart(world, monkeypatch):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    monkeypatch.setattr(APP, "DASHBOARD_PATH", world.dash)
    return world


def test_slow_successful_republication_is_waited_for(published_restart, monkeypatch):
    w = published_restart
    c = restart(w, monkeypatch)
    try:
        started = _slow_build(monkeypatch, 1.5)
        t = _warm_up_in_background()
        assert started.wait(60)                                        # re-publication in progress
        store = APP.app.state.generations
        assert store.dashboard_pending == store.current().core_id and store.current().dashboard_sha256 is None
        t0 = time.monotonic()
        r = c.get("/")
        assert r.status_code == 200 and time.monotonic() - t0 >= 0.5    # waited instead of a needless 503
        assert GEN.sha256_bytes(r.content) == store.current().dashboard_sha256
        t.join(30)
    finally:
        close(c)


def test_failed_republication_stays_503_without_waiting_the_full_bound(published_restart, monkeypatch):
    w = published_restart
    c = restart(w, monkeypatch)
    try:
        started = _slow_build(monkeypatch, 0.3, fail=True)
        monkeypatch.setattr(APP, "REPUBLISH_WAIT_SECONDS", 30.0)
        t = _warm_up_in_background()
        assert started.wait(60)
        t0 = time.monotonic()
        r = c.get("/")
        assert r.status_code == 503 and r.headers["X-Halsa-Dashboard"] == "bytes_mismatch"
        assert time.monotonic() - t0 < 15
        assert APP.app.state.generations.dashboard_pending is None
        t.join(30)
    finally:
        close(c)


def test_republication_wait_is_bounded(published_restart, monkeypatch):
    w = published_restart
    c = restart(w, monkeypatch)
    try:
        started = _slow_build(monkeypatch, 3.0)
        monkeypatch.setattr(APP, "REPUBLISH_WAIT_SECONDS", 0.3)
        t = _warm_up_in_background()
        assert started.wait(60)
        t0 = time.monotonic()
        r = c.get("/")
        assert r.status_code == 503 and r.headers["X-Halsa-Dashboard"] == "publication_pending"
        assert time.monotonic() - t0 < 2.5 and data_of(r.text) is None
        t.join(30)
        assert c.get("/").status_code == 200                           # once published, it is served
    finally:
        close(c)


def test_concurrent_refresh_supersedes_an_in_progress_republication(published_restart, monkeypatch):
    w = published_restart
    c = restart(w, monkeypatch)
    try:
        real = db.build
        gate_open, started = threading.Event(), threading.Event()

        def slow(*a, **k):
            if str(a[1]).endswith(".restore.new"):                     # only the restart re-publication waits
                started.set()
                gate_open.wait(30)
            return real(*a, **k)
        monkeypatch.setattr(db, "build", slow)
        t = _warm_up_in_background()
        assert started.wait(60)
        write_stock(w.folder, stock_rows([["SP-777", "Sentetik Yeni", 5, 0.2, 1.0, 3.0, 50, ""]]))
        rep = RF.run_refresh(w.folder, w.dash, inflation_rate=0.40, today=TODAY,
                             install=lambda s: APP._install_generation(APP.app.state.repo, s))
        assert rep["ok"]
        refreshed = Path(w.dash).read_bytes()
        gate_open.set()
        t.join(30)
        r = c.get("/")
        store = APP.app.state.generations
        assert r.status_code == 200 and r.content == refreshed == Path(w.dash).read_bytes()
        assert store.current().dashboard_sha256 == GEN.sha256_bytes(refreshed)
        assert GEN.read_manifest(GEN.manifest_path(w.dash)).core_id == store.current().core_id
    finally:
        close(c)


def test_store_wait_is_bounded_and_released_by_every_outcome():
    st = GEN.GenerationStore()
    rec = GEN.SourceRecord("stock", "core", "stock_snapshot", "current_state_snapshot", "s.xlsx", "a" * 64, 1,
                           "explicit")
    cand = GEN.Manifest("t", (rec,))
    st._pending = cand
    m = st.accept_rederived(cand, cand)
    assert st.dashboard_pending == m.core_id
    t0 = time.monotonic()
    assert st.wait_dashboard(0.2) is False and time.monotonic() - t0 < 1.5
    threading.Timer(0.2, lambda: st.set_dashboard(m.core_id, "b" * 64)).start()
    assert st.wait_dashboard(5) is True and st.current().dashboard_sha256 == "b" * 64
    st._pending = cand
    st.accept_rederived(cand, cand)
    threading.Timer(0.2, lambda: st.dashboard_failed(m.core_id)).start()
    assert st.wait_dashboard(5) is True and st.dashboard_pending is None
    st._pending = cand
    st.accept_rederived(cand, cand)
    threading.Timer(0.2, lambda: st.install(GEN.Manifest("u", (dataclasses.replace(rec, sha256="c" * 64),)))).start()
    assert st.wait_dashboard(5) is True


# =============================================================================================
# Office lock files are never sources
# =============================================================================================
def test_excel_lock_files_are_never_selected(tmp_path):
    d = SE.write_all(str(tmp_path / "d"))
    lock = Path(d) / ("~$" + SE.STOCK_FILE)
    lock.write_bytes(b"\x0cowner-lock-file-bytes")
    os.utime(lock, (time.time() + 60, time.time() + 60))               # newest by modification time
    assert os.path.basename(db.select_sources(d)["stock"]) == SE.STOCK_FILE
    (Path(d) / ("._" + SE.SALES_FILE)).write_bytes(b"appledouble")
    assert os.path.basename(db.select_sources(d)["sales"]) == SE.SALES_FILE
    with open(os.path.join(d, GEN.SELECTION_FILE), "w") as fh:
        json.dump({"schema": GEN.SELECTION_SCHEMA, "core": {"stock": {"file": lock.name},
                                                            "sales": {"file": SE.SALES_FILE}}}, fh)
    with pytest.raises(GEN.SelectionError):
        GEN.load_selection(d)
    from services.importers import kredi as K
    (Path(d) / "~$kredi takip.xlsx").write_bytes(b"x")
    assert all(not c["file"].startswith("~$") for c in K.find_kredi_candidates(d))


def test_reconcile_core_reports_stock_rows_privately(tmp_path):
    import subprocess
    import sys
    d = SE.write_all(str(tmp_path / "core"))
    book(Path(d) / SE.STOCK_FILE, [list(r) for r in stock_rows()])
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-m", "scripts.reconcile_sources", "--core", d, "--today", TODAY.isoformat()],
                       cwd=root, capture_output=True, text=True, env={**os.environ, "AI_PROVIDER": "disabled"})
    out = r.stdout
    assert "Tanınan rapor toplam satırı: 3" in out and f"Ürün kodlu satır: {len(stock_rows())}" in out
    assert "[PASS] Rapor toplam satırları" in out and "tanımsız (#DIV/0!)=" in out
    for secret in ("Sentetik Kalem", "SP-900", "TOPLAM", "2000"):
        assert secret not in out, secret


def _share(path, groups):
    """Rewrite cells as Excel shared formulas: groups = [(si, [cell refs]), …]; the first ref is the master, the
    others keep only `<f t="shared" si=…/>` (as Excel saves a run of identical formulas)."""
    import re
    import zipfile
    src = Path(path).read_bytes()
    zin = zipfile.ZipFile(__import__("io").BytesIO(src))
    xml = zin.read("xl/worksheets/sheet1.xml").decode()
    for si, refs in groups:
        span = f"{refs[0]}:{refs[-1]}"
        for k, ref in enumerate(refs):
            def sub(m, k=k):
                return (f'{m.group(1)}<f t="shared" ref="{span}" si="{si}">{m.group(2)}</f>' if k == 0
                        else f'{m.group(1)}<f t="shared" si="{si}"/>')
            xml, n = re.subn(rf'(<c r="{ref}"[^>]*>)<f>(.*?)</f>', sub, xml)
            assert n == 1, ref
    out = __import__("io").BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, xml.encode() if item.filename == "xl/worksheets/sheet1.xml" else zin.read(item))
    Path(path).write_bytes(out.getvalue())
    return path


def test_shared_formulas_in_footer_rows_are_expanded_and_recognized(tmp_path):
    """Real exports store runs of identical formulas as shared formulas (found in the 2B private rerun)."""
    p = book(tmp_path / "s.xlsx", products())                     # products rows 5–9, footers 10–12
    _share(p, [(0, ["D10", "F10", "G10", "H10"]), (1, ["D12", "F12", "G12", "H12"]),
               (2, ["J10", "J11", "J12"])])
    a = analyze(p)
    assert a.footer_kinds == {"aggregate": 2, "label": 1} and a.ambiguous_rows == ()
    assert a.footer_checks["matched"] == 8 and a.footer_checks["mismatched"] == 0
    assert SV.inspect_core("stock", p.read_bytes()).missing_required_values == 0


def test_a_shared_formula_without_its_master_fails_closed(tmp_path):
    p = book(tmp_path / "s.xlsx", products(), footer=("sum",))
    _share(p, [(0, ["D10", "F10", "G10", "H10"])])
    xml_fix = Path(p).read_bytes()
    import io
    import re
    import zipfile
    zin = zipfile.ZipFile(io.BytesIO(xml_fix))
    xml = zin.read("xl/worksheets/sheet1.xml").decode()
    xml = re.sub(r'<f t="shared" ref="[^"]*" si="0">.*?</f>', '<f t="shared" si="9"/>', xml)   # master removed
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, xml.encode() if item.filename == "xl/worksheets/sheet1.xml" else zin.read(item))
    Path(p).write_bytes(out.getvalue())
    a = analyze(p)
    assert a.footer_rows == () and len(a.ambiguous_rows) == 1
