"""M6-6 Bütçe importer tests (owner / Yönetim).

A synthetic workbook reproduces the verified 2026 template: sheet `2026` (A1:ED141) with seven stacked sales
blocks (segments 2018–2026 × Forecast/GERCEK, amount + two rate columns), the expense area (19 groups ×
Finance/Other/Total), the five BÜTÇE ÖZET panels, derived rows, deviation panels, a notes area, and a `Sayfa1`
scratch sheet. All titles, labels in the two group-list blocks and every number are invented.
Tests 01–63 follow the acceptance list; extra tests cover audience, region map, titles and candidates.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.importers import Audience, CellKind, Confidence, NormalizedValue, record_key
from services.importers import butce as B
from services.importers.butce import (
    BLOCKS,
    PANELS,
    BudgetBlock,
    BudgetRegion,
    CellValidity,
    Scenario,
    SourceStatus,
    ValueType,
    expense_groups,
    fact_key,
    find_butce_candidates,
    import_butce,
    parse_filename,
    segment_units,
    segments,
)
from tests.fixtures.xlsx_synth import MISSING, E, F, col_letter, write_xlsx

ROOT = Path(__file__).resolve().parents[1]
YEAR = 2026
NAME = "BUTCE_2026 Synth.xlsx"
GROUP_TITLES = {3: "ZQX Alfa / ZQX Beta", 4: "ZQX Gama / ZQX Delta"}
LINK = "file:///C:/Users/someone/Gizli Klasor/2026_gider_gelir_TASLAK.xlsx"
SECRET_TEXT = "ZQX-GIZLI-METIN"


def ci(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n


def at(col: int, row: int) -> str:
    return f"{col_letter(col)}{row}"


# ---------------------------------------------------------------------------------------------
# Synthetic workbook
# ---------------------------------------------------------------------------------------------
def amount(k: int, i: int, m: int) -> float:
    """Invented literal amount for blocks 3–7 (≥ 1000, never an integer)."""
    return 1000 + 111.25 * k + 17.5 * i + 3.125 * m


def rate(i: int, m: int) -> float:
    """Invented rate (≥ 1, never an integer)."""
    return 2.5 + 0.25 * i + 0.0625 * m


def expense(j: int, c: int, m: int) -> float:
    return 2000.75 + 13 * j + 1.5 * m + 500 * c


def panel_value(p: int, k: int, r: int) -> float:
    return 50000.5 + 1000 * p + 100 * k + 10 * r


def grid(clean: bool = False, ext_cached=E("#REF!"), values: bool = True) -> dict[str, object]:
    g: dict[str, object] = {}
    segs = segments(YEAR)
    blank_m = lambda b, s, m: s.year == YEAR and s.scenario is Scenario.ACTUAL and m > 1  # noqa: E731
    # Top duplicate header (rows 1–2).
    g["A2"] = "Months"
    for s in segs:
        g[at(s.col, 1)] = s.label
        for k, lab in enumerate(segment_units(BLOCKS[0], s)):
            g[at(s.col + k, 2)] = lab
    vals: dict[tuple[int, int, int], float] = {}
    for b in BLOCKS:
        t, k = b.title_row, b.index
        g[f"B{t}"] = g[f"Z{t}"] = "TOTAL SALES"
        title = b.title or GROUP_TITLES[k]
        for c in ("AL", "AR", "AX"):
            g[f"{c}{t}"] = title
        g[f"A{t + 2}"] = "Months"
        for i, m in enumerate(B.MONTHS_EN):
            g[f"A{t + 3 + i}"] = m
        g[f"A{t + 15}"] = "Total"
        for s in segs:
            g[at(s.col, t + 1)] = s.label
            for c, lab in enumerate(segment_units(b, s)):
                g[at(s.col + c, t + 2)] = lab
    if values:
        for s in segs:
            for m in range(1, 13):
                for k in (3, 4, 5, 6, 7):
                    vals[(k, s.index, m)] = None if blank_m(None, s, m) else amount(k, s.index, m)
                parts = [vals[(k, s.index, m)] for k in (3, 4, 5, 6)]
                vals[(2, s.index, m)] = sum(p or 0.0 for p in parts)
                vals[(1, s.index, m)] = vals[(2, s.index, m)] + (vals[(7, s.index, m)] or 0.0)
        for b in BLOCKS:
            t, k = b.title_row, b.index
            for s in segs:
                tot = 0.0
                for m in range(1, 13):
                    v = vals[(k, s.index, m)]
                    coord = at(s.col, t + 2 + m)
                    if v is None:
                        continue
                    tot += v
                    if k == 1:
                        g[coord] = F(f"{at(s.col, 25 + m)}+{at(s.col, 120 + m)}", v)
                    elif k == 2:
                        g[coord] = F(f"{at(s.col, 44 + m)}+{at(s.col, 63 + m)}", v)
                    else:
                        g[coord] = v
                    for c in (1, 2):   # rates: literal in block 1, references to block 1 elsewhere
                        if blank_m(b, s, m):
                            continue
                        rv = rate(s.index, m) * (1.1 if c == 2 else 1.0)
                        if k == 1:
                            g[at(s.col + c, t + 2 + m)] = (F(f"{at(s.col + 1, 6 + m)}*1.1", rv)
                                                          if c == 2 and s.scenario is Scenario.FORECAST else rv)
                        else:
                            g[at(s.col + c, t + 2 + m)] = F(at(s.col + c, 6 + m), rv)
                for m in range(2, 13):  # ACTUAL 2026 later months: blank rates in block 1, zero copies elsewhere
                    if blank_m(b, s, m) and k != 1:
                        for c in (1, 2):
                            g[at(s.col + c, t + 2 + m)] = F(at(s.col + c, 6 + m), 0)
                g[at(s.col, t + 15)] = F(f"SUM({at(s.col, t + 3)}:{at(s.col, t + 14)})", tot)
                g[at(s.col + 1, t + 15)] = F(f"AVERAGE({at(s.col + 1, t + 3)}:{at(s.col + 1, t + 14)})", rate(s.index, 6))
            # Derived rows (validated only).
            g[f"B{t + 16}"] = F(f"B{t + 15}*1", 1.0)
            g[f"D{t + 17}"] = "geçen sene TL"
            g[f"E{t + 17}"] = F(f"E{t + 15}/B{t + 15}-1", 0.04)
    # Expense area.
    groups = expense_groups(YEAR)
    for j, (gy, sc, label) in enumerate(groups):
        gc = B.EXPENSE_FIRST_COL + 4 * j
        g[at(gc, 4)] = label
        for c, comp in enumerate(B.EXPENSE_COMPONENTS):
            g[at(gc + c, 5)] = comp
            g[at(gc + c, 6)] = "YTL"
        if not values:
            continue
        sums = [0.0, 0.0, 0.0]
        for m in range(1, 13):
            r = 6 + m
            if gy == YEAR and sc is Scenario.UNQUALIFIED and m > 1:
                g[at(gc + 2, r)] = F(f"{at(gc, r)}+{at(gc + 1, r)}", 0)
                continue
            fin, oth = expense(j, 0, m), expense(j, 1, m)
            g[at(gc, r)] = fin
            g[at(gc + 1, r)] = oth
            g[at(gc + 2, r)] = F(f"{at(gc, r)}+{at(gc + 1, r)}", fin + oth)
            sums[0] += fin
            sums[1] += oth
            sums[2] += fin + oth
        for c in range(3):
            g[at(gc + c, 19)] = F(f"SUM({at(gc + c, 7)}:{at(gc + c, 18)})", sums[c])
        g[at(gc, 20)] = F(f"{at(gc, 19)}/2", 1.5)
        if sc is Scenario.FORECAST:
            for c in range(3):
                g[at(gc + c, 2)] = 24000.5 + 100 * j + 10 * c
    g["BF21"] = "Toplam iade"
    g["BD5"], g["BD6"] = "USD", "Kur artışı"
    g["BD7"] = F("BC7/AW7-1", 0.02)
    if not clean:
        dv = B.EXPENSE_FIRST_COL + 4 * 17
        g[at(dv, 1)] = F("'[1]S'!$G$44+'[1]S'!$G$50", ext_cached)
        g[at(dv + 1, 1)] = F(f"'[1]S'!$G$51-{at(dv, 1)}", ext_cached if isinstance(ext_cached, E) else ext_cached + 1)
        g[at(dv + 2, 1)] = F(f"{at(dv + 1, 1)}+{at(dv, 1)}", ext_cached if isinstance(ext_cached, E) else 2 * ext_cached + 1)
    # Deviation / returns panels right of blocks 2–7 (validated only).
    for b in BLOCKS[1:]:
        t = b.title_row
        g[f"BF{t}"], g[f"BH{t}"] = "değişim", "return"
        g[f"BF{t + 1}"], g[f"BG{t + 1}"], g[f"BH{t + 1}"] = "Aylık", "Kümülatif", "iade"
        g[f"BF{t + 3}"] = F(f"AI{t + 3}/AF{t + 3}-1", 0.05)
        if not clean:
            g[f"BG{t + 4}"] = F("#REF!/#REF!-1", E("#REF!"))
    # BÜTÇE ÖZET panels.
    for p, (col, py, last, has_target) in enumerate(PANELS):
        c = ci(col)
        if has_target:
            g[at(c, 24)] = B.PANEL_TARGET_LABEL
            g[at(c + 2, 24)] = 1234567.5
        g[at(c, 25)] = B.PANEL_TITLE
        g[at(c + 1, 26)], g[at(c + 2, 26)] = "senaryo 1", "senaryo 2"
        g[at(c + 4, 26)] = 0.3
        for r, lab in enumerate((*B.PANEL_LINES, last)):
            g[at(c, 27 + r)] = lab
            if values:
                for k in (1, 2):
                    v = panel_value(p, k, r)
                    g[at(c + k, 27 + r)] = v if r == 0 else F(f"{at(c + k, 27)}*0.5", v)
    g["D137"], g["E137"] = "Fark  +", 3.5
    if not clean:
        g["C38"] = F("B38/#REF!-1", E("#REF!"))
        g["AU135"] = F("AU133/AO134-1", E("#DIV/0!"))
    return g


def rows_of(g: dict[str, object]) -> list[list[object]]:
    out = [[None] * 134 for _ in range(141)]
    for coord, v in g.items():
        col = "".join(ch for ch in coord if ch.isalpha())
        row = int("".join(ch for ch in coord if ch.isdigit()))
        out[row - 1][ci(col) - 1] = v
    return out


MERGED = (["B1:D1", "E1:G1", "B4:Y4", "Z4:AK4", "AL4:AQ4", "AR4:AW4", "AX4:BC4", "BF4:BH4", "B5:D5", "E5:G5",
           "B42:Y42", "Z42:AK42", "AL42:AQ42", "Q43:S43", "DK25:DL25"])


def build(tmp: Path, name: str = NAME, overrides: dict | None = None, *, clean: bool = False, ext_cached=E("#REF!"),
          sheet: str = "2026", scratch: bool = True, extra_sheets: dict | None = None, values: bool = True,
          link: str | None = LINK) -> Path:
    g = grid(clean=clean, ext_cached=ext_cached, values=values)
    for coord, v in (overrides or {}).items():
        if v is None:
            g.pop(coord, None)
        else:
            g[coord] = v
    sheets: dict[str, list] = {sheet: rows_of(g)}
    if scratch:
        sheets["Sayfa1"] = [[None, "Ocak - Ekim Arası USD"], [], [GROUP_TITLES[3], 98765.4321], ["TOPLAM", 87654.321]]
    sheets.update(extra_sheets or {})
    p = tmp / name
    write_xlsx(p, sheets, external_links=() if (clean or link is None) else (link,), merged={sheet: MERGED})
    return p


def run(tmp: Path, *, planning_year=None, snapshot_date=None, **kw) -> B.BudgetResult:
    return import_butce(build(tmp, **kw), planning_year=planning_year, snapshot_date=snapshot_date)


def by_cell(res: B.BudgetResult) -> dict[str, B.BudgetFact]:
    return {f.source_cell: f for f in res.facts}


def blob(res: B.BudgetResult) -> str:
    return json.dumps(res.summary(), ensure_ascii=False, default=str)


def codes(res: B.BudgetResult, severity=None) -> list[str]:
    out = list(res.report.codes(severity))
    if res.component is not None:
        out += res.component.report.codes(severity)
    return out


@pytest.fixture(scope="module")
def default(tmp_path_factory) -> B.BudgetResult:
    return run(tmp_path_factory.mktemp("default"))


@pytest.fixture(scope="module")
def clean(tmp_path_factory) -> B.BudgetResult:
    return run(tmp_path_factory.mktemp("clean"), clean=True)


# =============================================================================================
# Layout (01–07)
# =============================================================================================
def test_01_valid_supported_matrix(default):
    assert default.ok and default.failed_step is None
    c = default.counts
    assert c.schema_version == "butce_2026_v1" and c.sheet == "2026" and c.bounds == "A1:ED141"
    assert c.cells_in_bounds == 141 * 134 and c.candidate_value_cells == 2975
    assert c.blocks == [b.value for b in BudgetBlock] and c.segments == 18 and c.expense_groups == 19
    assert c.summary_panels == 5
    assert set(c.facts_by_region) == {r.value for r in BudgetRegion}
    assert c.facts_by_region["sales_month"] == 7 * 18 * 12 - 55 and c.facts_by_region["budget_summary"] == 50
    assert c.facts_by_region["rate_month"] == 18 * 2 * 12 - 22


def test_02_missing_main_sheet_fails(tmp_path):
    res = run(tmp_path, sheet="Sheet1")
    assert not res.ok and res.facts == [] and "sheet_missing" in codes(res)


def test_03_moved_merged_header_fails(tmp_path):
    # The block-3 title (merged B42:Y42) moved one column right.
    res = run(tmp_path, overrides={"B42": None, "C42": "TOTAL SALES"})
    assert not res.ok and res.facts == [] and "anchor_missing" in codes(res)
    # A segment header (merged Q43:S43) moved inside its merge.
    res = run(tmp_path, overrides={"Q43": None, "R43": "2020  GERCEK"})
    assert not res.ok and "header_mismatch" in codes(res)


def test_03b_merged_header_interpretation(default, tmp_path):
    # Blank cells inside a merge (C5, D5, C4 … Y4) are not missing headers: the default book has merges and passes.
    assert default.ok
    # A value inside a merged span is unexpected and fails closed.
    res = run(tmp_path, overrides={"C5": "x"})
    assert not res.ok and "header_mismatch" in codes(res)


def test_04_missing_expected_block_fails(tmp_path):
    t = 99
    ov = {f"B{t}": None, f"A{t + 2}": None, f"A{t + 3}": None, f"A{t + 15}": None}
    res = run(tmp_path, overrides=ov)
    assert not res.ok and res.facts == [] and "anchor_missing" in codes(res)


def test_05_repeated_label_block_identity(default):
    jan = [f for f in default.facts if f.region is BudgetRegion.SALES_MONTH and f.month == 1 and f.year == 2018
           and f.scenario is Scenario.FORECAST]
    assert len(jan) == 7 and len({f.block for f in jan}) == 7 and len({f.record_key for f in jan}) == 7


def test_06_unsupported_matrix_drift_fails(tmp_path):
    assert "header_mismatch" in codes(run(tmp_path, overrides={"B5": "2018 Budget"}))
    assert "header_mismatch" in codes(run(tmp_path, overrides={"C25": "USD"}))          # unit drift
    assert "header_mismatch" in codes(run(tmp_path, overrides={"BI4": "extra"}))         # expense separator column
    assert "header_mismatch" in codes(run(tmp_path, overrides={"BJ5": "Finans"}))       # expense component drift
    assert "row_label_mismatch" in codes(run(tmp_path, overrides={"A50": "Haziran"}))
    assert "panel_label_mismatch" in codes(run(tmp_path, overrides={"DZ31": "Net"}))
    res = run(tmp_path, overrides={"BB4": "TOTAL SALES"})                                 # stray title cell
    assert not res.ok and "header_mismatch" in codes(res)


def test_07_scratch_sheet_excluded(default, tmp_path):
    assert default.counts.sheets_excluded == ["Sayfa1"] and default.counts.unexpected_sheets == 0
    assert all(f.value.value != 98765.4321 for f in default.facts) and "98765" not in blob(default)
    res = run(tmp_path, extra_sheets={"Notlar": [["x"]]})
    assert res.ok and res.counts.unexpected_sheets == 1 and "unexpected_sheets" in codes(res, B.Severity.WARNING)


# =============================================================================================
# Errors (08–14)
# =============================================================================================
def test_08_ref_becomes_invalid(tmp_path):
    res = run(tmp_path, overrides={"B45": E("#REF!")})
    f = by_cell(res)["B45"]
    assert res.ok and f.validity is CellValidity.EXCEL_ERROR and f.value.error == "#REF!" and not f.usable
    assert f.value.confidence is Confidence.INVALID


def test_09_div0_becomes_invalid(tmp_path):
    res = run(tmp_path, overrides={"E46": F("E45/0", E("#DIV/0!"))})
    f = by_cell(res)["E46"]
    assert res.ok and f.validity is CellValidity.EXCEL_ERROR and f.value.error == "#DIV/0!"
    assert f.value.kind is CellKind.FORMULA_ERROR


def test_10_other_error_becomes_invalid(tmp_path):
    res = run(tmp_path, overrides={"H47": E("#N/A"), "BF8": E("#VALUE!"), "EA28": F("X", E("#NAME?"))})
    cells = by_cell(res)
    assert {cells[c].value.error for c in ("H47", "BF8", "EA28")} == {"#N/A", "#VALUE!", "#NAME?"}
    assert not any(cells[c].usable for c in ("H47", "BF8", "EA28"))
    assert cells["EA28"].region is BudgetRegion.BUDGET_SUMMARY


def test_11_error_never_zero(tmp_path):
    f = by_cell(run(tmp_path, overrides={"B45": E("#REF!")}))["B45"]
    assert f.value.value is None and f.value.value != 0


def test_12_error_never_ordinary_blank(tmp_path):
    res = run(tmp_path, overrides={"B45": E("#REF!")})
    f = by_cell(res)["B45"]
    assert not f.value.is_null and f.value.is_invalid
    cov = res.counts.coverage_by_region["sales_month"]
    assert cov.invalid == 1 and cov.blank == 55


def test_13_invalid_does_not_corrupt_neighbours(tmp_path):
    res = run(tmp_path, overrides={"B45": E("#REF!")})
    cells = by_cell(res)
    assert cells["B46"].usable and cells["B46"].value.value == amount(3, 0, 2)
    assert cells["E45"].usable and cells["E45"].value.value == amount(3, 1, 1)
    assert cells["C7"].usable and cells["C7"].value.value == rate(0, 1)


def test_14_safe_summary_counts_error_types(tmp_path):
    res = run(tmp_path, overrides={"B45": E("#REF!"), "E46": E("#DIV/0!"), "H47": E("#N/A")})
    c = res.counts
    assert c.excel_errors["#N/A"] == 1 and c.other_errors == 1
    assert c.ref_errors == c.excel_errors["#REF!"] and c.div0_errors == c.excel_errors["#DIV/0!"]
    assert c.coverage_by_region["sales_month"].excel_errors == {"#REF!": 1, "#DIV/0!": 1, "#N/A": 1}


# =============================================================================================
# Formulas (15–19)
# =============================================================================================
def test_15_cached_formula_with_provenance(default):
    f = by_cell(default)["B7"]   # block 1 = block 2 + block 7 (formula)
    assert f.value.kind is CellKind.FORMULA_VALUE and f.provenance == "cached_formula_result"
    assert f.value.from_last_excel_calculation


def test_16_missing_cache_invalid(tmp_path):
    res = run(tmp_path, overrides={"B45": F("B64*2", MISSING)})
    f = by_cell(res)["B45"]
    assert res.ok and f.validity is CellValidity.MISSING_CACHE and f.value.value is None
    assert res.counts.formula_missing_cache == 1 and res.counts.coverage_by_region["sales_month"].missing_cache == 1
    assert "formula_missing_cache" in codes(res, B.Severity.WARNING)


def test_17_formula_text_never_a_value(default):
    assert all(not isinstance(f.value.value, str) for f in default.facts)
    assert all(isinstance(f.value.value, float) for f in default.facts if f.usable)


def test_18_never_recalculated(tmp_path):
    res = run(tmp_path, overrides={"B45": F("1+1", 4321.5)})
    assert by_cell(res)["B45"].value.value == 4321.5
    src = (ROOT / "services/importers/butce.py").read_text()
    assert "eval(" not in src and "import formulas" not in src and "pycel" not in src


def test_19_literal_vs_formula_provenance(default):
    cells = by_cell(default)
    assert cells["B45"].provenance == "literal" and cells["B45"].value.kind is CellKind.LITERAL
    assert cells["B26"].provenance == "cached_formula_result"
    assert {f.provenance for f in default.facts} >= {"literal", "cached_formula_result", "excel_error"}


# =============================================================================================
# External links (20–24)
# =============================================================================================
def test_20_external_dependency_detected(default):
    c = default.counts
    assert c.external_links == 1 and c.external_formula_cells == 2 and c.external_dependent_facts == 3
    assert c.external_dependency_regions == ["expense_linked_input"]
    assert "external_dependency" in codes(default, B.Severity.WARNING)
    dep = [f for f in default.facts if f.external_dependency]
    assert {(f.year, f.scenario, f.component) for f in dep} == {(2026, Scenario.FORECAST, x) for x in
                                                                 ("finance", "other", "total")}


def test_21_linked_workbook_never_opened(tmp_path, monkeypatch):
    from services.importers import excel as X
    opened: list[str] = []
    real = X.openpyxl.load_workbook

    def spy(path, *a, **kw):
        opened.append(str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(X.openpyxl, "load_workbook", spy)
    (tmp_path / "2026_gider_gelir_TASLAK.xlsx").write_bytes(b"not a workbook")
    p = build(tmp_path)
    assert import_butce(p).ok
    assert opened and set(opened) == {str(p)}


def test_22_external_cell_unusable_cached_kept_aside(tmp_path):
    res = run(tmp_path, ext_cached=777.5)
    dep = [f for f in res.facts if f.external_dependency]
    assert len(dep) == 3 and all(f.validity is CellValidity.EXTERNAL_DEPENDENCY and not f.usable for f in dep)
    assert all(f.value.value is None and f.value.error == "external_dependency" for f in dep)
    first = next(f for f in dep if f.component == "finance")
    assert first.cached_external_value == 777.5
    # The total cell has no external reference itself but is computed from the linked cells.
    total = next(f for f in dep if f.component == "total")
    assert not total.value.external_ref and total.cached_external_value == 2 * 777.5 + 1


def test_23_unrelated_cells_remain_available(tmp_path):
    res = run(tmp_path, ext_cached=777.5)
    assert res.ok and res.counts.usable_unverified == res.counts.candidate_value_cells - res.counts.blank_candidates - 3
    assert by_cell(res)["DV7"].usable   # 2026 expense-forecast months below the linked row are not dependent


def test_24_summary_reports_dependency_without_values_or_path(tmp_path):
    res = run(tmp_path, ext_cached=777.5)
    b = blob(res)
    assert res.counts.external_formula_cells == 2 and res.counts.external_links == 1
    for leak in ("777.5", "778.5", "1556.0", "Gizli Klasor", "someone", "2026_gider_gelir_TASLAK"):
        assert leak not in b, leak
    assert '"names_withheld": true' in b


# =============================================================================================
# Partial usability (25–29)
# =============================================================================================
def test_25_valid_cells_stage_with_invalid_cells(default):
    assert default.ok and default.counts.usable_unverified > 2700 and default.counts.invalid_facts == 3
    assert default.counts.invalid_cells_in_validated_regions == 6 + 2   # BG deviation ×6, C38, AU135


def test_26_structural_error_fails_whole_import(tmp_path):
    res = run(tmp_path, overrides={"A2": "Months", "A6": "Aylar"})
    assert not res.ok and res.facts == [] and res.failed_step == "component"


def test_27_source_status(default, clean, tmp_path):
    assert default.counts.source_status == SourceStatus.PARTIALLY_USABLE.value and default.counts.contains_invalid_cells
    assert clean.counts.source_status == SourceStatus.COMPLETE.value and not clean.counts.contains_invalid_cells
    empty = run(tmp_path, values=False)
    assert empty.ok and empty.counts.source_status == SourceStatus.UNUSABLE.value
    for r in (default, clean):
        assert r.counts.authoritative is False and r.counts.file_of_record == "unresolved"


def test_28_coverage_calculation(tmp_path):
    res = run(tmp_path, clean=True, overrides={"B45": E("#REF!"), "B46": F("x", MISSING), "B47": None, "B48": "abc"})
    c = res.counts
    non_blank = c.candidate_value_cells - c.blank_candidates
    # blanks: sales 55, rates 22, expense months 22, annual inputs 30, linked row 57 (clean), plus B47
    assert c.invalid_facts == 3 and c.blank_candidates == 55 + 22 + 22 + 30 + 57 + 1
    assert c.usable_unverified == non_blank - 3
    assert c.coverage_rate == round(c.usable_unverified / non_blank, 4)
    assert c.invalid_by_validity == {"excel_error": 1, "missing_cache": 1, "type_mismatch": 1}


def test_29_no_invalid_cell_usable(default, tmp_path):
    res = run(tmp_path, overrides={"B45": E("#REF!"), "B46": "abc", "B47": F("x", MISSING)}, ext_cached=5.5)
    for r in (default, res):
        assert all(f.usable == (f.validity is CellValidity.VALID) for f in r.facts)
        assert all(f.value.value is None for f in r.facts if not f.usable)
        assert all(f.value.confidence is Confidence.UNVERIFIED for f in r.usable_facts)


# =============================================================================================
# Periods (30–37)
# =============================================================================================
def test_30_planning_year(tmp_path, default):
    assert default.counts.planning_year == 2026 and default.counts.planning_year_origin == "filename"
    assert run(tmp_path, planning_year=2026).counts.planning_year_origin == "declared"
    res = run(tmp_path, planning_year=2025)
    assert not res.ok and "planning_year_conflict" in codes(res)
    res = run(tmp_path, name="BUTCE_2027 Synth.xlsx")
    assert not res.ok and "unsupported_template" in codes(res)
    res = run(tmp_path, sheet="2025")
    assert not res.ok and "sheet_missing" in codes(res)
    res = run(tmp_path, name="butce taslak.xlsx")
    assert not res.ok and "planning_year_unknown" in codes(res)


def test_31_historical_year_identity(default):
    years = {f.year for f in default.facts if f.region is BudgetRegion.SALES_MONTH}
    assert years == set(range(2018, 2027))
    assert default.counts.historical_years == list(range(2018, 2026))
    assert {f.year for f in default.facts if f.region is BudgetRegion.EXPENSE_MONTH} == {2016, *range(2018, 2027)}


def test_32_scenarios_preserved(default):
    sales = {f.scenario for f in default.facts if f.region is BudgetRegion.SALES_MONTH}
    assert sales == {Scenario.FORECAST, Scenario.ACTUAL}
    exp = {(f.year, f.scenario) for f in default.facts if f.region is BudgetRegion.EXPENSE_MONTH}
    assert (2016, Scenario.UNQUALIFIED) in exp and (2016, Scenario.FORECAST) not in exp
    assert {f.scenario for f in default.facts if f.region is BudgetRegion.BUDGET_SUMMARY} == {Scenario.SENARYO_1,
                                                                                          Scenario.SENARYO_2}
    assert "budget_plan" not in {s.value for s in Scenario} and "approved" not in json.dumps([s.value for s in Scenario])


def test_33_month_mapping(default):
    cells = by_cell(default)
    assert cells["B7"].month == 1 and cells["B18"].month == 12 and cells["B19"].month is None
    assert cells["B19"].region is BudgetRegion.SALES_TOTAL
    assert cells["BF7"].month == 1 and cells["BF18"].month == 12 and cells["BF19"].region is BudgetRegion.EXPENSE_TOTAL


def test_34_blank_month_remains_missing(default):
    # 2026 GERCEK (BA) months 2–12 are blank in blocks 3–7: no fact, counted as blank.
    cells = by_cell(default)
    assert "BA46" not in cells and "BA45" in cells
    assert default.counts.coverage_by_region["sales_month"].blank == 55


def test_35_zero_remains_zero(default):
    f = by_cell(default)["BA8"]   # block 1, 2026 GERCEK February: a recorded zero (formula result)
    assert f.usable and f.value.value == 0.0


def test_36_revision_label_not_snapshot_date(default, tmp_path):
    assert default.counts.revision_label == "Synth" and default.counts.snapshot_date is None
    assert default.counts.snapshot_date_origin is None
    import datetime as dt
    res = run(tmp_path, snapshot_date=dt.date(2026, 1, 31))
    assert res.counts.snapshot_date == dt.date(2026, 1, 31) and res.counts.snapshot_date_origin == "declared"


def test_37_no_mtime_or_today_fallback(tmp_path):
    p = build(tmp_path)
    os.utime(p, (946684800, 946684800))
    res = import_butce(p)
    assert res.counts.snapshot_date is None and res.counts.planning_year == 2026
    src = (ROOT / "services/importers/butce.py").read_text()
    for banned in ("st_mtime", "getmtime", "date.today", "datetime.now", "utcnow"):
        assert banned not in src, banned


# =============================================================================================
# Currency / rate columns (38–41)
# =============================================================================================
def test_38_rate_subcolumn_identity(default):
    cells = by_cell(default)
    assert cells["C7"].source_label == "$ kur" and cells["D7"].source_label == "€ kur"     # 2018 Forecast
    assert cells["F7"].source_label == "€ kur" and cells["G7"].source_label == "$ kur"     # 2018 GERCEK (order flips)
    assert {f.value_type for f in default.facts if f.region is BudgetRegion.RATE_MONTH} == {ValueType.CURRENCY_RATE}
    rate_keys = {f.record_key for f in default.facts if f.region is BudgetRegion.RATE_MONTH}
    assert len(rate_keys) == 410


def test_39_no_fx_conversion(default):
    cells = by_cell(default)
    assert cells["B45"].value.value == amount(3, 0, 1)        # no division by a rate
    assert all(not hasattr(f, attr) for f in default.facts[:5] for attr in ("converted", "usd_value", "eur_value"))
    src = (ROOT / "services/importers/butce.py").read_text()
    assert "tcmb" not in src.lower() and "http" not in src.lower().replace("https://", "")


def test_40_invalid_rate_cell_stays_invalid(tmp_path):
    res = run(tmp_path, overrides={"C7": E("#REF!")})
    cells = by_cell(res)
    assert cells["C7"].validity is CellValidity.EXCEL_ERROR and cells["C7"].region is BudgetRegion.RATE_MONTH
    assert cells["B7"].usable and cells["D7"].usable   # neither the amount nor the formula-linked € cell is altered


def test_41_label_semantics_not_upgraded(default):
    cells = by_cell(default)
    assert cells["B45"].source_label == "TL" and cells["E45"].source_label == "YTL"   # kept verbatim, never "TRY"
    assert {f.source_label for f in default.facts if f.region is BudgetRegion.SALES_MONTH} == {"YTL", "TL"}
    assert {f.source_label for f in default.facts if f.region is BudgetRegion.RATE_MONTH} == {"$ kur", "€ kur"}
    assert all(not hasattr(f, "currency") for f in default.facts[:5])


# =============================================================================================
# Repeated labels / keys (42–46)
# =============================================================================================
def test_42_same_label_two_blocks_distinct_keys(default):
    cells = by_cell(default)
    assert cells["B7"].record_key != cells["B83"].record_key   # both "January", 2018 Forecast; blocks 1 and 5


def test_43_same_fact_same_key_across_revisions(tmp_path):
    a = run(tmp_path)
    b = run(tmp_path, name="BUTCE_2026 Taslak.xlsx", overrides={"B45": 1111.5})
    assert {f.record_key for f in a.facts} == {f.record_key for f in b.facts}
    assert by_cell(a)["B45"].record_key == by_cell(b)["B45"].record_key


def test_44_file_name_does_not_determine_identity(tmp_path):
    a, b = run(tmp_path), run(tmp_path, name="BUTCE_2026 Ocak.xlsx")
    assert [f.record_key for f in a.facts] == [f.record_key for f in b.facts]
    assert all("Synth" not in f.record_key and "Ocak" not in f.record_key for f in a.facts)


def test_45_row_number_alone_not_identity(default):
    cells = by_cell(default)
    assert cells["B7"].record_key == record_key("butce", ["sales_month", "total_sales", 2018, "forecast", 1, None])
    assert cells["BF7"].record_key == record_key("butce", ["expense_month", None, 2016, "unqualified", 1, "finance"])
    row7 = [f.record_key for f in default.facts if f.source_cell.endswith("7") and f.source_cell[-2].isalpha()]
    assert len(row7) == len(set(row7)) > 50


def test_46_no_python_hash():
    src = (ROOT / "services/importers/butce.py").read_text()
    assert " hash(" not in src and "(hash(" not in src
    code = ("from services.importers.butce import _positions, fact_key;"
            "p = _positions(2026); print(sorted(fact_key(v) for v in p.values())[:3])")
    outs = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONHASHSEED": s, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}).stdout
            for s in ("3", "333")}
    assert len(outs) == 1 and "butce:" in next(iter(outs))


# =============================================================================================
# Privacy (47–52)
# =============================================================================================
def _assert_no_numbers(b: str, numbers) -> None:
    for n in numbers:
        assert repr(float(n)) not in b, n


def test_47_summary_has_no_amounts(default):
    _assert_no_numbers(blob(default), [amount(3, 0, 1), amount(7, 5, 6), expense(0, 0, 1), 24000.5])


def test_48_no_rate_values(default):
    _assert_no_numbers(blob(default), [rate(0, 1), rate(3, 7), rate(0, 1) * 1.1])


def test_49_no_budget_totals(default):
    f = by_cell(default)
    _assert_no_numbers(blob(default), [f["B19"].value.value, f["BF19"].value.value, panel_value(4, 1, 0),
                                       panel_value(4, 2, 4), 1234567.5])


def test_50_no_absolute_paths(tmp_path):
    deep = tmp_path / "Users" / "owner" / "Gizli"
    deep.mkdir(parents=True)
    res = import_butce(build(deep))
    b = blob(res)
    assert res.ok and res.file == NAME and str(tmp_path) not in b and "Gizli" not in b


def test_51_no_leak_through_errors(tmp_path):
    cases = ({"B45": SECRET_TEXT}, {"B42": None}, {"AL42": "ZQX Başka"}, {"B45": E("#REF!")}, {"C7": F("x", MISSING)})
    for ov in cases:
        res = run(tmp_path, overrides=ov)
        b = blob(res)
        assert SECRET_TEXT not in b and "ZQX" not in b and repr(amount(3, 0, 2)) not in b, ov
    assert all(GROUP_TITLES[k] not in blob(run(tmp_path)) for k in (3, 4))


def test_52_external_cached_values_do_not_leak(tmp_path):
    res = run(tmp_path, ext_cached=4242.125)
    assert "4242.125" not in blob(res) and "8485.25" not in blob(res)


def test_privacy_verification_catches_a_leak(tmp_path, monkeypatch):
    real = B.BudgetResult.summary

    def leaky(self):
        d = real(self)
        d["oops"] = repr(amount(3, 0, 1))
        return d

    monkeypatch.setattr(B.BudgetResult, "summary", leaky)
    res = run(tmp_path)
    assert not res.ok and res.failed_step == "verify" and res.facts == []


# =============================================================================================
# Isolation (53–63)
# =============================================================================================
LIVE = ("app.py", "dashboard_builder.py", "services/refresh.py", "services/context.py", "services/action_sources.py",
        "services/actions.py", "services/entity.py", "services/evidence.py", "services/agent.py")


def test_53_m5_unchanged():
    for live in LIVE:
        text = (ROOT / live).read_text()
        assert "importers" not in text and "butce" not in text and "import_butce" not in text, live


def test_54_to_58_other_importers_unchanged():
    for mod in ("sales_activity", "hedef", "kredi", "trodat", "gider_gelir"):
        assert "butce" not in (ROOT / f"services/importers/{mod}.py").read_text(), mod
    src = (ROOT / "services/importers/butce.py").read_text()
    for mod in ("sales_activity", "hedef", "kredi", "trodat", ".gider_gelir", "import_gider_gelir"):
        assert f"from .{mod.lstrip('.')} import" not in src and f"import {mod}" not in src, mod
    # The framework additions are backward compatible.
    assert NormalizedValue(1.0, Confidence.UNVERIFIED, CellKind.LITERAL).external_ref is False


def test_59_no_dashboard_or_api_exposure():
    assert "butce" not in (ROOT / "app.py").read_text().lower()
    assert "import_butce" not in (ROOT / "dashboard_builder.py").read_text()
    assert "butce" not in (ROOT / "services/evidence.py").read_text().lower()


def test_60_no_actions():
    for f in ("services/actions.py", "services/action_sources.py"):
        assert "butce" not in (ROOT / f).read_text().lower()


def test_61_no_ai():
    src = (ROOT / "services/importers/butce.py").read_text()
    for banned in ("openai", "anthropic", "services.ai", "services.agent", "httpx", "requests"):
        assert banned not in src, banned
    code = ("import sys, services.importers.butce;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip() == "[]"


def test_62_no_refresh_dependency(tmp_path):
    assert "butce" not in (ROOT / "services/refresh.py").read_text().lower()
    p = build(tmp_path)
    before = (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir()))
    assert import_butce(p).ok
    assert (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir())) == before
    src = (ROOT / "services/importers/butce.py").read_text()
    for banned in ("write_text", "write_bytes", ".save(", "sqlite", "open(", "pandas"):
        assert banned not in src, banned


def test_63_detailed_collections_not_started():
    names = {p.stem for p in (ROOT / "services/importers").glob("*.py")}
    assert not any(n.startswith(("collection", "tahsilat", "detailed")) for n in names)
    src = (ROOT / "services/importers/butce.py").read_text().lower()
    assert "tahsilat" not in src and "collections" not in src.replace("from collections import", "")


# =============================================================================================
# Audience, region map, titles, candidates, checks
# =============================================================================================
def test_audience_owner_and_verified(default, tmp_path, monkeypatch):
    assert default.audience is Audience.OWNER and default.component.audience is Audience.OWNER
    assert B.butce_spec(2026).audience is Audience.OWNER
    real = B.butce_spec
    monkeypatch.setattr(B, "butce_spec", lambda y: __import__("dataclasses").replace(real(y), audience=Audience.SHARED))
    res = run(tmp_path)
    assert not res.ok and res.failed_step == "verify"


def test_region_map_complete_and_non_overlapping():
    ranges = B.butce_ranges(2026)
    seen: set[tuple[int, int]] = set()
    from openpyxl.utils.cell import range_boundaries
    for r in ranges:
        c0, r0, c1, r1 = range_boundaries(r.range)
        cells = {(rr, cc) for rr in range(r0, r1 + 1) for cc in range(c0, c1 + 1)}
        assert not (cells & seen), r.name
        seen |= cells
    assert len(seen) == 141 * 134
    positions = B._positions(2026)
    assert len(positions) == 2975


def test_region_coverage_partition(default):
    c = default.counts
    assert sum(r.cells for r in c.coverage_by_region.values()) + sum(r.cells for r in c.validated_regions.values()) \
        == c.cells_in_bounds
    assert c.validated_regions["deviation_and_side_panels"].excel_errors == {"#REF!": 6}
    assert c.validated_regions["sales_total_rate_columns"].excel_errors == {"#REF!": 1}
    assert c.validated_regions["sales_derived_rows"].excel_errors == {"#DIV/0!": 1}


def test_group_list_titles_verified_not_committed(tmp_path):
    assert run(tmp_path, overrides={"AR61": "ZQX Farklı"}).failed_step == "layout"   # AL/AR/AX disagree
    res = run(tmp_path, overrides={c: GROUP_TITLES[3] for c in ("AL61", "AR61", "AX61")})
    assert not res.ok and "block_title_mismatch" in codes(res)                       # same as block 3
    res = run(tmp_path, overrides={c: "TRODAT" for c in ("AL42", "AR42", "AX42")})
    assert not res.ok and "block_title_mismatch" in codes(res)                       # repeats a known title
    assert [b.title for b in BLOCKS if b.block in (BudgetBlock.GROUP_LIST_1, BudgetBlock.GROUP_LIST_2)] == [None, None]
    assert " / " not in "".join(b.title or "" for b in BLOCKS)


def test_title_conflict_flagged(default):
    cells = by_cell(default)
    assert cells["B45"].title_conflict and not cells["AL45"].title_conflict and not cells["B7"].title_conflict
    assert default.counts.title_conflict_facts > 0 and "historical_title_conflict" in codes(default)


def test_top_header_conflicts_counted(tmp_path, default):
    assert default.counts.top_header_conflicts == 0
    res = run(tmp_path, overrides={"AG2": "€ kur", "AH2": "$ kur"})
    assert res.ok and res.counts.top_header_conflicts == 2 and "top_header_conflicts" in codes(res)


def test_source_checks_warn_never_repair(tmp_path, clean):
    assert all(v["failed"] == 0 for v in clean.counts.checks.values())
    res = run(tmp_path, clean=True, overrides={"B19": F("SUM(B7:B18)", 1.5), "BH8": F("BF8+BG8", 7.25)})
    assert res.ok and res.counts.checks["sales_total_vs_months"]["failed"] == 1
    assert res.counts.checks["expense_total_component"]["failed"] == 1
    assert by_cell(res)["B19"].value.value == 1.5 and by_cell(res)["BH8"].value.value == 7.25
    assert "check_sales_total_vs_months" in codes(res, B.Severity.WARNING)


def test_candidates_listed_never_selected(tmp_path):
    build(tmp_path, name="BUTCE_2026 Ocak.xlsx")
    build(tmp_path, name="BUTCE_2026 Taslak.xlsx", overrides={"B45": E("#REF!")})
    (tmp_path / "notes.xlsx").write_bytes(b"x")
    out = find_butce_candidates(tmp_path)
    assert [c["file"] for c in out] == ["BUTCE_2026 Ocak.xlsx", "BUTCE_2026 Taslak.xlsx"]
    assert all(c["schema_compatible"] for c in out)
    assert all(set(c) == {"file", "planning_year", "revision_label", "schema_compatible", "failed_step",
                          "source_status", "excel_errors", "external_formula_cells", "coverage_rate"} for c in out)
    src = (ROOT / "services/importers/butce.py").read_text()
    assert "selected" not in src and "file_of_record = " not in src.replace('file_of_record: str = "unresolved"', "")


def test_parse_filename():
    assert parse_filename("BUTCE_2026 Ocak.xlsx") == (2026, "Ocak")
    assert parse_filename("BUTCE_2026 Taslak.xlsx") == (2026, "Taslak")
    assert parse_filename("2026_gider_gelir_TASLAK.xlsx") is None
