"""M6-6 Gider/Gelir importer tests (owner / Yönetim).

A synthetic workbook reproduces the verified 2026 template: ÖZET (summary matrix with P&L, accounting entries
and a pasted expense pivot), GİDERLER (expense table with the carried-over `2025 <Ay>` headers), GELİRLER (four
native-line blocks + TOPLAM) and a presentation-only Sheet1. All labels and amounts are invented.
Tests 01–67 follow the acceptance list.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from services.importers import Audience, CellKind, Confidence, record_key
from services.importers import gider_gelir as G
from services.importers.gider_gelir import (
    LINES,
    NativeLine,
    PeriodKind,
    find_gider_gelir_candidates,
    import_gider_gelir,
    parse_filename,
)
from tests.fixtures.xlsx_synth import MISSING, E, F, col_letter, write_xlsx

ROOT = Path(__file__).resolve().parents[1]
YEAR = 2026
NAME = "2026_gider_gelir_Eylül.xlsx"
SECRET_CC = ("01ZQXFUAR", "02ZQXNAKLIYE", "25ZQXGELIR")
SECRET_NAMES = ("Gizli Kira Bedeli ZQX", "Kurgu Danışmanlık ZQX", "Hayali Prim ZQX", "Uydurma İade ZQX")


def ci(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + ord(ch) - 64
    return n


class Sheet:
    def __init__(self, rows=60, cols=80):
        self.rows = [[None] * cols for _ in range(rows)]

    def __setitem__(self, coord, value):
        letters = "".join(ch for ch in coord if ch.isalpha())
        self.rows[int(coord[len(letters):]) - 1][ci(letters) - 1] = value

    def __getitem__(self, coord):
        letters = "".join(ch for ch in coord if ch.isalpha())
        return self.rows[int(coord[len(letters):]) - 1][ci(letters) - 1]


def cached(v):
    return v.cached if isinstance(v, F) else v


EXPENSES = [  # cost center, name, ratios, monthly totals (Jan–Sep filled), budget
    (SECRET_CC[0], SECRET_NAMES[0], (0.4, 0.3, 0.2, 0.1), [1000.0 + 10 * m if m <= 9 else None for m in range(1, 13)], 15000.0),
    (SECRET_CC[0], SECRET_NAMES[1], (0.333, 0.333, 0.334, 0.0), [300.0 if m <= 9 else None for m in range(1, 13)], 4000.0),
    (SECRET_CC[1], SECRET_NAMES[2], (0.25, 0.25, 0.25, 0.25), [-50.0 if m == 3 else 200.0 if m <= 9 else None
                                                               for m in range(1, 13)], 2000.0),
    (SECRET_CC[2], SECRET_NAMES[3], (0.6, 0.3, 0.2, -0.1), [120.0 if m <= 9 else None for m in range(1, 13)], 0.0),
]
REVENUE = {  # line → (sales, cost) per month (Jan–Sep), gross profit entered as sales − cost
    0: (5000.0, 3000.0), 1: (4000.0, 2500.0), 2: (1500.0, 900.0), 3: (800.0, 500.0)}


def build(*, year=YEAR, expenses=None, header_year=None, alloc_override=False, gp_deviation=False,
          block_total_wrong=False, ozet_m3_wrong=False, pivot_stale=False, footer_wrong=False, money_error=False,
          money_missing_cache=False, drop_entry_label=False, ozet_header_drift=False, gelir_header_drift=False,
          annual_wrong=False, ratio_blank=False, extra_note=True, ozet_year=None):
    exps = expenses if expenses is not None else EXPENSES
    t = header_year if header_year is not None else 2025
    oy = ozet_year or year
    # ---------------- GELİRLER ----------------
    gl = Sheet(36, 16)
    months = [col_letter(2 + i) for i in range(12)]
    block_rows = (1, 8, 15, 22, 29)
    titles = ("Kırtasiye", "Trodat", "Trotec", "imalat", "TOPLAM")
    hdr = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
           "TOTAL"]
    if gelir_header_drift:
        hdr[2] = "MART"
    tot = {(i, m): 0.0 for i in range(3) for m in range(12)}
    for k, tr in enumerate(block_rows):
        gl[f"B{tr}"] = titles[k]
        for j, h in enumerate(hdr):
            gl[f"{col_letter(2 + j)}{tr + 1}"] = h
        for i, lab in enumerate(("Satış", "Satış maliyeti", "Brüt Kar")):
            gl[f"A{tr + 2 + i}"] = lab
        for m, col in enumerate(months):
            if k < 4:
                s, c = REVENUE[k] if m < 9 else (None, None)
                gp = None if s is None else s - c + (7.0 if gp_deviation and k == 0 and m == 0 else 0.0)
                for i, v in enumerate((s, c, gp)):
                    gl[f"{col}{tr + 2 + i}"] = v
                    if v is not None:
                        tot[(i, m)] += v
            else:
                for i in range(3):
                    v = tot[(i, m)] + (1.0 if block_total_wrong and i == 0 and m == 0 else 0.0)
                    gl[f"{col}{tr + 2 + i}"] = F(f"{col}{3 + i}+{col}{10 + i}+{col}{17 + i}+{col}{24 + i}", v)
            cost = cached(gl[f"{col}{tr + 3}"]) or 0
            gl[f"{col}{tr + 5}"] = F(f"{col}{tr + 4}/{col}{tr + 3}", cached(gl[f"{col}{tr + 4}"]) / cost if cost else E("#DIV/0!"))
        for i in range(3):
            r = tr + 2 + i
            gl[f"N{r}"] = F(f"SUM(B{r}:M{r})", sum((cached(gl[f"{c}{r}"]) or 0) for c in months))
        gl[f"N{tr + 5}"] = F(f"N{tr + 4}/N{tr + 3}", cached(gl[f"N{tr + 4}"]) / cached(gl[f"N{tr + 3}"]))
    gl["J7"] = "kurgu not"
    # ---------------- GİDERLER ----------------
    gd = Sheet(len(exps) + 8, 80)
    spec = G.expense_spec(2026)
    headers = [f.header.name for f in spec.fields]
    if header_year is not None:   # simulate a template whose monthly headers carry another year
        headers = [h.replace("2025\n", f"{t}\n").replace("2025 \n", f"{t} \n") for h in headers]
    if year != 2026:
        headers = [h.replace("2026\n", f"{year}\n") for h in headers]
    order = ["cost_center", "expense_name", "budget", "report_year_total"] + [f"historical_{y}" for y in range(2025, 2020, -1)] + \
        [f"ratio_{ln.value}" for ln in LINES] + ["ratio_total"] + [f"annual_{ln.value}" for ln in LINES]
    for m in range(1, 13):
        order += [f"m{m}_total"] + [f"m{m}_{ln.value}" for ln in LINES]
    col = {name: col_letter(i + 1) for i, name in enumerate(order)}
    name_to_header = {f.name: h for f, h in zip(spec.fields, headers)}
    for name in order:
        gd[f"{col[name]}1"] = name_to_header[name]
    col_sums: dict[str, float] = {}
    for i, (cc, nm, ratios, totals, budget) in enumerate(exps):
        r = 2 + i
        vals = {"cost_center": cc, "expense_name": nm, "budget": budget}
        for y in range(2021, 2026):
            vals[f"historical_{y}"] = 900.0 + y - 2021
        for k, ln in enumerate(LINES):
            vals[f"ratio_{ln.value}"] = None if (ratio_blank and i == 0 and k == 0) else ratios[k]
        vals["ratio_total"] = F(f"SUM(J{r}:M{r})", sum(x or 0 for x in ratios))
        annual = {ln: 0.0 for ln in LINES}
        for m, total in enumerate(totals, start=1):
            vals[f"m{m}_total"] = total
            for k, ln in enumerate(LINES):
                a = round((total or 0) * ratios[k], 6)
                if alloc_override and i == 0 and m == 2 and k == 1:
                    vals[f"m{m}_{ln.value}"] = a + 50.0           # manual entry in the source
                    annual[ln] += a + 50.0
                else:
                    vals[f"m{m}_{ln.value}"] = F(f"{col[f'm{m}_total']}{r}*${col[f'ratio_{ln.value}']}{r}", a)
                    annual[ln] += a
        yt = sum(x or 0 for x in totals) + (5.0 if annual_wrong and i == 0 else 0.0)
        vals["report_year_total"] = F("SUM()", yt)
        if money_missing_cache and i == 1:
            vals["report_year_total"] = F("SUM()", MISSING)
        for ln in LINES:
            vals[f"annual_{ln.value}"] = F("SUM()", annual[ln])
        if money_error and i == 2:
            vals["m4_total"] = E("#VALUE!")
        for name, v in vals.items():
            gd[f"{col[name]}{r}"] = v
            if name not in ("cost_center", "expense_name"):
                col_sums[name] = col_sums.get(name, 0.0) + (cached(v) if isinstance(cached(v), (int, float)) else 0.0)
    fr = 2 + len(exps)
    for name in order[2:]:
        if name.startswith("ratio"):
            continue
        gd[f"{col[name]}{fr}"] = F("SUM()", col_sums.get(name, 0.0) + (3.0 if footer_wrong and name == "m1_total" else 0))
    if extra_note:
        gd[f"B{fr + 1}"] = "kurgu dipnot"
        gd[f"C{fr + 1}"] = 1.5
    # ---------------- ÖZET ----------------
    oz = Sheet(52, 26)
    oh = G.ozet_headers(oy, False)
    oh9 = G.ozet_headers(oy, True)
    if ozet_header_drift:
        oh9 = (*oh9[:7], "KIRTASİYE", *oh9[8:])
    for j, h in enumerate(oh):
        oz[f"{col_letter(2 + j)}2"] = h
        oz[f"{col_letter(2 + j)}24"] = h
    for j, h in enumerate(oh9):
        oz[f"{col_letter(2 + j)}9"] = h
    for r, lab in {**G.OZET_PL_ROWS, **G.OZET_ENTRY_ROWS, 8: "Muhasebe kayıtları", 24: "Row Labels", 51: "Grand Total"}.items():
        if not (drop_entry_label and r == 14):
            oz[f"A{r}"] = lab
    ozm = [col_letter(13 + i) for i in range(12)]   # M..X
    line_cols = ["I", "J", "K", "L"]
    for i, r in enumerate((3, 4, 5)):
        for c in "BCDEF":
            oz[f"{c}{r}"] = 10000.0 + 100 * i + ord(c)
        oz[f"G{r}"] = 20000.0 + i
        for m, c in enumerate(ozm):
            v = cached(gl[f"{months[m]}{31 + i}"]) or 0.0
            oz[f"{c}{r}"] = F(f"'GELİRLER'!{months[m]}{31 + i}", v + (2.0 if ozet_m3_wrong and r == 3 and m == 0 else 0.0))
        for k, c in enumerate(line_cols):
            oz[f"{c}{r}"] = F("ref", cached(gl[f"N{3 + 7 * k + i}"]))
        oz[f"H{r}"] = F("ref", cached(gl[f"N{31 + i}"]))
        oz[f"Y{r}"] = F(f"SUM(M{r}:X{r})", sum(cached(oz[f"{c}{r}"]) for c in ozm))
    # pivot rows 25.. by cost center
    centers = sorted({e[0] for e in exps})
    pivot_month = {c: 0.0 for c in ozm}
    for k, cc in enumerate(centers):
        r = 25 + k
        oz[f"A{r}"] = cc
        rows = [e for e in exps if e[0] == cc]
        for m, c in enumerate(ozm):
            v = sum((e[3][m] or 0.0) for e in rows)
            oz[f"{c}{r}"] = v
            pivot_month[c] += v
    for m, c in enumerate(ozm):
        oz[f"{c}51"] = F(f"SUM({c}25:{c}50)", pivot_month[c] + (9.0 if pivot_stale and m == 0 else 0.0))
    oz["H51"] = F("SUM", sum(sum(x or 0 for x in e[3]) for e in exps) + (5.0 if annual_wrong else 0.0))
    oz["G51"] = F("SUM", sum(e[4] for e in exps))
    for k, c in enumerate(line_cols):
        oz[f"{c}51"] = F("SUM", sum(round((t_ or 0) * e[2][k], 6) for e in exps for t_ in e[3])
                         + (50.0 if alloc_override and k == 1 else 0.0))
    for c in ["G", "H", *line_cols, *ozm]:
        oz[f"{c}6"] = F(f"{c}51", cached(oz[f"{c}51"]))
    for c in "BCDEF":
        oz[f"{c}6"] = 5000.0 + ord(c)
    oz["Y6"] = F("SUM(M6:X6)", sum(cached(oz[f"{c}6"]) for c in ozm))
    for c in [col_letter(2 + j) for j in range(24)]:
        if cached(oz[f"{c}5"]) is not None or cached(oz[f"{c}6"]) is not None:
            oz[f"{c}7"] = F(f"{c}5-{c}6", (cached(oz[f"{c}5"]) or 0) - (cached(oz[f"{c}6"]) or 0))
    for r, base in zip(range(10, 16), (100.0, -40.0, 30.0, 10.0, 5.0, -5.0)):
        for c in "BCDEF":
            oz[f"{c}{r}"] = base * 10
        for m, c in enumerate(ozm[:9]):
            oz[f"{c}{r}"] = base + m
        oz[f"H{r}"] = F(f"Y{r}", sum(base + m for m in range(9)))
        oz[f"Y{r}"] = F(f"SUM(M{r}:X{r})", sum(base + m for m in range(9)))
        for k, c in enumerate(line_cols):
            oz[f"{c}{r}"] = F(f"$H{r}*{c}$22", cached(oz[f"H{r}"]) * (0.25))
    for c in [col_letter(2 + j) for j in range(24)]:
        s = sum((cached(oz[f"{c}{r}"]) or 0) for r in range(10, 18))
        oz[f"{c}18"] = F(f"SUM({c}10:{c}17)", s)
        oz[f"{c}21"] = F(f"{c}7+{c}18", (cached(oz[f"{c}7"]) or 0) + s)
    for c in [col_letter(2 + j) for j in range(24)]:
        sales = cached(oz[f"{c}3"]) or 0
        oz[f"{c}1"] = F(f"{c}5/{c}3", (cached(oz[f"{c}5"]) or 0) / sales if sales else E("#DIV/0!"))
    sheets = {f"ÖZET {oy}": oz.rows, "Sheet1": [["Row Labels", "TOPLAM 2021"], [SECRET_CC[0], 1.0]],
              f"{year} GİDERLER TABLOSU": gd.rows, f"{year} GELİRLER TABLOSU ": gl.rows}
    return sheets


def book(tmp_path, name=NAME, drop_sheet=None, external_links=(), **kw):
    sheets = build(**kw)
    if drop_sheet:
        sheets.pop(drop_sheet)
    return write_xlsx(tmp_path / name, sheets, external_links=external_links)


def run(tmp_path, report_year=None, name=NAME, **kw):
    return import_gider_gelir(book(tmp_path, name=name, **kw), report_year=report_year)


def codes(res, sev=None):
    out = res.report.codes(sev)
    for c in res.components.values():
        out += c.report.codes(sev)
    return out


def blob(res):
    return json.dumps(res.summary(), ensure_ascii=False, default=str)


# =============================================================================================
# Audience / source (1–6)
# =============================================================================================
def test_01_owner_audience(tmp_path):
    res = run(tmp_path)
    assert res.ok, blob(res)
    assert res.audience is Audience.OWNER and res.summary()["audience"] == "owner"
    assert all(s.audience is Audience.OWNER for s in (G.ozet_spec(YEAR), G.expense_spec(YEAR), G.revenue_spec(YEAR)))
    assert next(c for c in res.verification.checks if c.name == "audience_owner").ok


def test_02_explicit_path_import(tmp_path):
    p = book(tmp_path)
    assert import_gider_gelir(p).ok and import_gider_gelir(str(p)).ok


def test_03_no_mtime_selection(tmp_path):
    book(tmp_path)
    book(tmp_path, name="2026_gider_gelir_mayıs.xlsx")
    write_xlsx(tmp_path / "2024_gider_gelir_Eylül.xlsx", {"x": [["a"]]})
    os.utime(tmp_path / NAME, (1, 1))
    out = find_gider_gelir_candidates(tmp_path)
    assert [o["file"] for o in out] == sorted(o["file"] for o in out)
    assert {o["file"]: o["template_supported"] for o in out} == {
        "2024_gider_gelir_Eylül.xlsx": False, "2026_gider_gelir_mayıs.xlsx": True, NAME: True}
    src = (ROOT / "services/importers/gider_gelir.py").read_text()
    assert "getmtime" not in src and "st_mtime" not in src and "_latest(" not in src


def test_04_sheet_requirements(tmp_path):
    res = run(tmp_path)
    assert res.counts.sheets_validated == ["ÖZET 2026", "2026 GİDERLER TABLOSU", "2026 GELİRLER TABLOSU "]
    assert res.counts.sheets_excluded == ["Sheet1"]


def test_05_missing_required_sheet_fails(tmp_path):
    res = run(tmp_path, drop_sheet="2026 GELİRLER TABLOSU ")
    assert not res.ok and res.failed_step == "components" and "sheet_missing" in codes(res)
    assert res.revenue == [] and res.expenses == []    # no partial composite result


def test_06_external_link_fails(tmp_path):
    res = run(tmp_path, external_links=["file:///C:/Users/x/Baska.xlsx"])
    assert not res.ok and "external_link" in codes(res, G.Severity.ERROR)


# =============================================================================================
# Report year / month (7–14)
# =============================================================================================
def test_07_declared_year(tmp_path):
    res = run(tmp_path, name="gider_gelir_kopya.xlsx", report_year=2026)
    assert res.ok and (res.counts.report_year, res.counts.report_year_origin) == (2026, "declared")


def test_08_filename_year_with_workbook_anchor(tmp_path):
    res = run(tmp_path)
    assert (res.counts.report_year, res.counts.report_year_origin, res.counts.revision_label) == (2026, "filename", "Eylül")
    assert parse_filename("2026_gider_gelir_TASLAK.xlsx") == (2026, "TASLAK")
    assert parse_filename("gider gelir 2026.xlsx") is None


def test_09_year_signals_disagree_fail(tmp_path):
    res = run(tmp_path, report_year=2025)                    # declared vs filename
    assert not res.ok and "report_year_conflict" in codes(res)
    res = run(tmp_path, ozet_year=2025)                       # filename 2026 vs workbook sheet "ÖZET 2025"
    assert not res.ok and "sheet_missing" in codes(res)


def test_10_broken_header_year_not_used(tmp_path):
    res = run(tmp_path)
    assert res.ok and {m.year for m in res.expense_months} == {2026}
    assert res.counts.template_header_year == 2025


def test_11_template_year_warning(tmp_path):
    res = run(tmp_path)
    issue = next(i for i in res.report.issues if i.code == "template_header_year_mismatch")
    assert issue.severity.value == "warning" and "2025" in issue.message and "2026" in issue.message
    # A template whose headers changed (e.g. corrected to 2026) is header drift → fails closed.
    assert not run(tmp_path, header_year=2026).ok


def test_12_turkish_months_map(tmp_path):
    res = run(tmp_path)
    e0 = res.expenses[0].expense_key
    by_month = {m.month: m.reported_expense.value for m in res.expense_months if m.expense_key == e0}
    assert by_month[1] == 1010.0 and by_month[9] == 1090.0 and by_month[12] is None
    assert G.MONTHS_OZET[3] == "NISAN" and G.MONTHS_OZET[7] == "AGUSTOS"   # exact source spelling


def test_13_no_today_fallback(tmp_path):
    res = run(tmp_path, name="gider_gelir_kopya.xlsx")
    assert not res.ok and "report_year_unknown" in codes(res)
    src = (ROOT / "services/importers/gider_gelir.py").read_text()
    assert "today(" not in src and "now(" not in src


def test_14_filename_month_not_snapshot(tmp_path):
    res = run(tmp_path)
    assert res.counts.revision_label == "Eylül" and res.counts.snapshot_date is None


# =============================================================================================
# Business lines (15–19)
# =============================================================================================
def test_15_four_lines_separate(tmp_path):
    res = run(tmp_path)
    assert res.counts.native_lines == ["kirtasiye", "trodat", "trotec", "imalat"]
    assert {r.native_line for r in res.revenue} == set(LINES)
    assert set(res.expense_months[0].allocations) == set(LINES)


def test_16_no_management_mapping(tmp_path):
    res = run(tmp_path)
    assert all(e.management_category == "not_mapped" for e in res.expenses)
    assert all(r.management_category == "not_mapped" for r in res.revenue)
    src = (ROOT / "services/importers/gider_gelir.py").read_text().lower()
    for banned in ("stamp", "kaşe", "stationery", "import_category", "\"other\""):
        assert banned not in src.split('"""', 2)[2], banned


def test_17_trodat_not_trotec(tmp_path):
    assert NativeLine.TRODAT != NativeLine.TROTEC
    res = run(tmp_path)
    sales = {r.native_line: r.value.value for r in res.revenue if r.metric == "reported_sales" and r.month == 1}
    assert sales[NativeLine.TRODAT] == 4000.0 and sales[NativeLine.TROTEC] == 1500.0


def test_18_imalat_not_mapped_to_stamp():
    assert NativeLine.IMALAT.value == "imalat" and [ln.value for ln in NativeLine] == ["kirtasiye", "trodat", "trotec",
                                                                                      "imalat"]


def test_19_mapping_status_not_supplied(tmp_path):
    assert run(tmp_path).counts.management_mapping == "not_supplied"


# =============================================================================================
# Expense allocations (20–26)
# =============================================================================================
def test_20_ratios_sum_to_one_pass(tmp_path):
    res = run(tmp_path)
    assert (res.counts.ratio_rows_checked, res.counts.ratio_rows_failed) == (4, 0)   # incl. 0.333/0.333/0.334


def test_21_ratio_sum_mismatch_fails(tmp_path):
    exps = [list(e) for e in EXPENSES]
    exps[0][2] = (0.5, 0.3, 0.1, 0.05)
    res = run(tmp_path, expenses=[tuple(e) for e in exps])
    assert not res.ok and "allocation_ratio_sum" in codes(res, G.Severity.ERROR)


def test_22_ratio_outside_unit_interval_is_warning(tmp_path):
    res = run(tmp_path)        # one synthetic reallocation row (…, -0.1), like the real source
    assert res.ok and res.counts.ratios_outside_unit_interval == 1
    assert "allocation_ratio_outside_unit_interval" in codes(res, G.Severity.WARNING)


def test_23_blank_ratio_fails(tmp_path):
    res = run(tmp_path, ratio_blank=True)
    assert not res.ok and "allocation_ratio_missing" in codes(res, G.Severity.ERROR)


def test_24_monthly_allocations_reconcile(tmp_path):
    res = run(tmp_path)
    assert res.counts.allocation_cells_not_matching_ratio == 0
    assert all(m.allocations_match_ratios for m in res.expense_months)


def test_25_allocation_mismatch_caught(tmp_path):
    res = run(tmp_path, alloc_override=True)
    assert res.ok and res.counts.allocation_cells_not_matching_ratio == 1
    assert "allocation_not_total_times_ratio" in codes(res, G.Severity.WARNING)
    flagged = [m for m in res.expense_months if not m.allocations_match_ratios]
    assert len(flagged) == 1 and flagged[0].month == 2


def test_26_no_silent_correction(tmp_path):
    res = run(tmp_path, alloc_override=True)
    m = next(m for m in res.expense_months if not m.allocations_match_ratios)
    assert m.allocations[NativeLine.TRODAT].value == round(1020.0 * 0.3, 6) + 50.0   # kept as recorded


# =============================================================================================
# Formulas / errors (27–33)
# =============================================================================================
def test_27_cached_formula_with_provenance(tmp_path):
    res = run(tmp_path)
    m = res.expense_months[0]
    assert m.allocations[NativeLine.KIRTASIYE].kind is CellKind.FORMULA_VALUE
    assert m.reported_expense.kind is CellKind.LITERAL


def test_28_missing_required_cache_rejected(tmp_path):
    res = run(tmp_path, money_missing_cache=True)
    assert not res.ok and "formula_missing_cache" in codes(res, G.Severity.ERROR)


def test_29_money_error_fails(tmp_path):
    res = run(tmp_path, money_error=True)
    assert not res.ok and "excel_error" in codes(res, G.Severity.ERROR)


def test_30_known_ozet_ratio_error_isolated(tmp_path):
    res = run(tmp_path)
    assert res.ok and res.counts.excel_errors.get("summary.ratio_row.#DIV/0!") == 3
    assert not any(s.value.error for s in res.summary_records)


def test_31_known_revenue_margin_errors_isolated(tmp_path):
    res = run(tmp_path)
    assert sum(v for k, v in res.counts.excel_errors.items() if k.startswith("revenue.")) == 15
    assert "known_ratio_errors" in codes(res, G.Severity.INFO)


def test_32_amounts_usable_when_ratio_invalid(tmp_path):
    res = run(tmp_path)
    oct_sales = [r for r in res.revenue if r.month == 10]
    assert oct_sales == []              # blank future months are missing, not 0 and not errors
    assert all(r.value.error is None for r in res.revenue)


def test_33_never_recalculated():
    src = (ROOT / "services/importers/gider_gelir.py").read_text()
    for banned in ("load_workbook", "WorkbookPair", "import formulas", "pycel", "calculate(", "eval("):
        assert banned not in src, banned


# =============================================================================================
# Totals / cross-sheet (34–40)
# =============================================================================================
def test_34_expense_totals_row_reconciles(tmp_path):
    res = run(tmp_path)
    assert res.ok and "expense_totals_row_mismatch" not in codes(res)
    assert res.counts.trailing_rows == 1


def test_35_expense_totals_row_mismatch(tmp_path):
    res = run(tmp_path, footer_wrong=True)
    assert not res.ok and "expense_totals_row_mismatch" in codes(res, G.Severity.ERROR)


def test_36_line_allocation_totals(tmp_path):
    res = run(tmp_path)
    assert res.counts.cross_sheet_checks["summary_expense_vs_expense_table"]["failed"] == 0


def test_37_line_allocation_total_mismatch(tmp_path):
    res = run(tmp_path, pivot_stale=True)
    assert res.ok and res.counts.cross_sheet_checks["summary_expense_vs_expense_table"]["failed"] >= 1
    assert "summary_expense_vs_expense_table" in codes(res, G.Severity.WARNING)


def test_38_block_and_row_identities(tmp_path):
    res = run(tmp_path)
    assert res.counts.cross_sheet_checks["summary_vs_revenue"] == {"checked": 51, "failed": 0}
    assert res.counts.gross_profit_not_sales_minus_cost == 0


def test_39_identity_failures_caught(tmp_path):
    assert "revenue_block_total_mismatch" in codes(run(tmp_path, block_total_wrong=True), G.Severity.ERROR)
    assert "summary_vs_revenue_mismatch" in codes(run(tmp_path, ozet_m3_wrong=True), G.Severity.ERROR)
    gp = run(tmp_path, gp_deviation=True)       # gross profit is not an enforced identity (source enters it)
    assert gp.ok and gp.counts.gross_profit_not_sales_minus_cost == 1


def test_40_no_double_counting(tmp_path):
    res = run(tmp_path)
    kinds = {(s.line_label, s.period_kind) for s in res.summary_records}
    # ÖZET's report-year P&L columns duplicate GELİRLER and are never summary records.
    assert not any(lab in ("Satış", "Satış maliyeti", "Brüt Kar", "gider", "NET KAR", "KAR-ZARAR") and k in
                   (PeriodKind.MONTH, PeriodKind.REPORT_YEAR_TOTAL, PeriodKind.BUSINESS_LINE_TOTAL) for lab, k in kinds)
    assert not any(r.native_line is None for r in res.revenue)   # the TOPLAM block is a control only


# =============================================================================================
# Keys (41–45)
# =============================================================================================
def test_41_stable_summary_key(tmp_path):
    a, b = run(tmp_path), run(tmp_path)
    assert [s.record_key for s in a.summary_records] == [s.record_key for s in b.summary_records]
    s = next(s for s in a.summary_records if s.line_label == "Amortisman" and s.period_kind is PeriodKind.MONTH)
    assert s.line_key == record_key("gider_gelir_summary_line", ["Amortisman"])


def test_42_stable_expense_key(tmp_path):
    e = run(tmp_path).expenses[0]
    assert e.expense_key == record_key("gider_expense", [SECRET_CC[0], SECRET_NAMES[0]])


def test_43_period_changes_key(tmp_path):
    res = run(tmp_path)
    keys = {m.record_key for m in res.expense_months if m.expense_key == res.expenses[0].expense_key}
    assert len(keys) == 12


def test_44_filename_revision_does_not_change_identity(tmp_path):
    a = run(tmp_path)
    b = run(tmp_path, name="2026_gider_gelir_mayıs.xlsx")
    assert {e.expense_key for e in a.expenses} == {e.expense_key for e in b.expenses}
    assert {r.record_key for r in a.revenue} == {r.record_key for r in b.revenue}


def test_45_no_hash_or_row_identity():
    src = (ROOT / "services/importers/gider_gelir.py").read_text()
    assert " hash(" not in src
    assert 'record_key("gider_expense", [r.get("cost_center"), r.get("expense_name")])' in src
    code = "from services.importers import record_key; print(record_key('gider_expense', ['A', 'B']))"
    outs = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONHASHSEED": s, "PATH": "/usr/bin:/bin"}).stdout for s in ("5", "55")}
    assert len(outs) == 1


# =============================================================================================
# Confidence (46–50)
# =============================================================================================
def test_46_validation_does_not_upgrade(tmp_path):
    res = run(tmp_path)
    assert {r.value.confidence for r in res.revenue} == {Confidence.UNVERIFIED}
    assert {m.reported_expense.confidence for m in res.expense_months if m.reported_expense.value is not None} == {
        Confidence.UNVERIFIED}


def test_47_accounting_basis_unverified(tmp_path):
    assert run(tmp_path).counts.accounting_basis == "unverified"
    assert all("cash" not in r.metric and "accru" not in r.metric for r in run(tmp_path).revenue)


def test_48_declared_confidence_retained(tmp_path):
    res = run(tmp_path)
    hist = [s for s in res.summary_records if s.period_kind is PeriodKind.HISTORICAL_YEAR_TOTAL]
    assert hist and {s.value.confidence for s in hist} == {Confidence.UNVERIFIED}
    assert {s.period_kind for s in res.summary_records} == set(PeriodKind)


def test_49_invalid_cells_invalid(tmp_path):
    res = run(tmp_path, money_error=True)
    comp = res.components["expenses"]
    assert comp.records == [] and "excel_error" in comp.report.codes()


def test_50_identity_fields_trusted(tmp_path):
    fc = run(tmp_path).components["expenses"].field_confidence
    assert fc["cost_center"] is Confidence.TRUSTED and fc["expense_name"] is Confidence.TRUSTED
    assert fc["m1_total"] is Confidence.UNVERIFIED


# =============================================================================================
# Privacy (51–56)
# =============================================================================================
def test_51_no_amounts(tmp_path):
    b = blob(run(tmp_path))
    for amount in ("1010.0", "15000", "4000.0", "5000.0", "20000", "1090"):
        assert amount not in b, amount


def test_52_no_expense_names(tmp_path):
    b = blob(run(tmp_path))
    assert not any(n in b for n in SECRET_NAMES) and "ZQX" not in b


def test_53_no_cost_centers(tmp_path):
    assert not any(c in blob(run(tmp_path)) for c in SECRET_CC)


def test_54_no_pl_values(tmp_path):
    b = blob(run(tmp_path))
    assert "10166" not in b and "Amortisman" not in b and "Kur farkı" not in b


def test_55_no_absolute_path(tmp_path):
    deep = tmp_path / "Users" / "owner" / "Gizli"
    deep.mkdir(parents=True)
    res = import_gider_gelir(book(deep))
    b = blob(res)
    assert res.file == NAME and str(tmp_path) not in b and "Gizli" not in b


def test_56_no_leak_through_errors(tmp_path):
    for kw in ({"footer_wrong": True}, {"money_error": True}, {"ozet_m3_wrong": True}, {"alloc_override": True}):
        b = blob(run(tmp_path, **kw))
        assert "ZQX" not in b and "1010.0" not in b and "Gizli" not in b, kw


# =============================================================================================
# Isolation (57–67)
# =============================================================================================
LIVE = ("app.py", "dashboard_builder.py", "services/refresh.py", "services/context.py", "services/action_sources.py",
        "services/actions.py", "services/entity.py", "services/evidence.py", "services/agent.py")


def test_57_m5_does_not_import():
    for live in LIVE:
        text = (ROOT / live).read_text()
        assert "importers" not in text and "gider_gelir" not in text, live


def test_58_to_61_other_m6_modules_untouched():
    for mod in ("sales_activity", "hedef", "kredi", "trodat"):
        assert "gider_gelir" not in (ROOT / f"services/importers/{mod}.py").read_text(), mod


def test_62_no_api():
    assert "gider" not in (ROOT / "app.py").read_text().lower()


def test_63_no_dashboard():
    assert "import_gider_gelir" not in (ROOT / "dashboard_builder.py").read_text()


def test_64_no_actions():
    for f in ("services/actions.py", "services/action_sources.py"):
        assert "gider" not in (ROOT / f).read_text().lower()


def test_65_no_ai():
    src = (ROOT / "services/importers/gider_gelir.py").read_text()
    for banned in ("openai", "anthropic", "services.ai", "services.agent", "httpx", "requests"):
        assert banned not in src, banned
    code = ("import sys, services.importers.gider_gelir;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip() == "[]"


def test_66_no_refresh_dependency(tmp_path):
    assert "gider" not in (ROOT / "services/refresh.py").read_text().lower()
    p = book(tmp_path)
    before = (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir()))
    assert import_gider_gelir(p).ok
    assert (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir())) == before
    src = (ROOT / "services/importers/gider_gelir.py").read_text()
    for banned in ("write_text", "write_bytes", ".save(", "sqlite", "open(", "pandas"):
        assert banned not in src, banned


def test_67_butce_not_imported():
    src = (ROOT / "services/importers/gider_gelir.py").read_text()
    assert "BUTCE_" not in src and "bütçe eylül" not in src.lower()
    assert not (ROOT / "services/importers/butce.py").exists()


# =============================================================================================
# Layout drift
# =============================================================================================
def test_layout_drift_fails(tmp_path):
    assert "anchor_missing" in codes(run(tmp_path, drop_entry_label=True))
    assert "header_mismatch" in codes(run(tmp_path, ozet_header_drift=True))
    assert "header_mismatch" in codes(run(tmp_path, gelir_header_drift=True))
    res = run(tmp_path, name="2025_gider_gelir_Kasım.xlsx")
    assert not res.ok and "unsupported_template" in codes(res)


def test_annual_total_mismatch_is_warning(tmp_path):
    res = run(tmp_path, annual_wrong=True)
    assert "annual_total_not_sum_of_months" in codes(res, G.Severity.WARNING)
    assert not res.expenses[0].annual_total_matches_months if res.ok else True
