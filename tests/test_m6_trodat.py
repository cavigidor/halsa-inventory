"""M6-5 Trodat sales report importer tests.

Synthetic workbooks reproduce the verified matrix layout (three sections, display-pack variants, family lines,
formula totals with cached values). Model codes and quantities are invented. Tests 01–52 follow the acceptance
list.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from services.importers import CellKind, Confidence, record_key
from services.importers import trodat as T
from services.importers.trodat import SourceFamily, find_trodat_candidates, import_trodat, parse_report_filename
from tests.fixtures.xlsx_synth import E, F, write_xlsx

ROOT = Path(__file__).resolve().parents[1]
NAME = "trodat sales report Halsa NOVEMBER 2031 .xlsx"
MONTHS = ["Jan", "Feb", "March", "April", "May", "June", "July", "August", "Sep", "Oct", "Nov", "Dec", "Total"]
COLS = "CDEFGHIJKLMN"


def val(row, month):
    return (row * 7 + month * 3) % 41 + 1


def build_rows(*, blank_dec=False, overrides=None, s2_inconsistent=False, header1=None, header3=None,
               extra_col=False, move_total=False, drop_label=None, dup_title=False, display_expanded=True):
    overrides = overrides or {}
    rows = [[None] * 16 for _ in range(66)]

    def put(r, c, v):
        rows[r - 1][c] = v

    for i, m in enumerate(header1 or MONTHS):
        put(1, 2 + i, m)
    if extra_col:
        put(1, 15, "Total 2")

    def detail(r, code, variant, mult_total=1):
        put(r, 0, code)
        put(r, 1, variant)
        vals = []
        for i in range(12):
            v = overrides.get((r, i + 1), None if (blank_dec and i == 11) else val(r, i + 1))
            put(r, 2 + i, v)
            vals.append(v if isinstance(v, (int, float)) else (v.cached if isinstance(v, F) else 0))
        put(r, 14, F(f"SUM(C{r}:N{r})*{mult_total}" if mult_total > 1 else f"SUM(C{r}:N{r})", sum(vals) * mult_total))
        return vals

    s1 = {}
    for r in range(2, 8):
        s1[r] = detail(r, f"99{r:02d}P4", "P4")
    for r in range(8, 28):
        s1[r] = detail(r, 9000 + r, None)
    s1[28] = detail(28, "Other Printy" if drop_label != 28 else "Diğer", None)
    s1[29] = detail(29, "Professional", None)
    total_row = 32 if move_total else 31
    put(total_row, 0, "Total")
    month_tot = [sum(s1[r][i] for r in s1) for i in range(12)]
    for i, c in enumerate(COLS):
        put(total_row, 2 + i, F(f"SUM({c}2:{c}30)", month_tot[i]))
    put(total_row, 14, F(f"SUM(C{total_row}:N{total_row})", sum(month_tot)))

    s2, mults = {}, {}
    if not move_total:
        for r, code, variant, m in ((32, "9100-0001", None, 1), (33, "9100-0002", None, 1), (34, 9134, None, 1),
                                    (35, 9135, None, 1), (36, 9136, "displayx24", 24), (37, 9137, "displayx24", 24),
                                    (38, 9138, "displayx8", 8), (39, 9139, "displayx8", 8)):
            expand = display_expanded and r in (36, 38, 39)   # like the real file: row 37 stays plain
            s2[r] = detail(r, code, variant, m if expand else 1)
            mults[r] = (m, m if expand else 1)
        put(40, 0, "Total")
        for i, c in enumerate(COLS):
            expect = sum(s2[r][i] * mults[r][0] for r in s2)
            if s2_inconsistent and c == "G":
                expect += s2[39][i]     # the "×9 instead of ×8" kind of formula slip
            put(40, 2 + i, F(f"{c}32+{c}33+{c}35+{c}34+({c}36*24)+({c}37*24)+({c}38*8)+({c}39*8)", expect))
        put(40, 14, F("SUM(O32:O39)", sum(sum(s2[r]) * mults[r][1] for r in s2)))
        for i in range(8):
            put(41, 2 + i, F(f"{COLS[i]}53/{COLS[i]}31", 0.37))

    put(42, 0, "Daters Numberers")
    if dup_title:
        put(45, 0, "Daters Numberers")
    for i, m in enumerate(header3 or (MONTHS[:11] + ["December", "Total"])):
        put(43, 2 + i, m)
    detail(44, 9244, None)
    if not dup_title:
        detail(45, "9200-0001", None)
    detail(46, "Numberers", None)
    detail(47, "Stamp pads" if drop_label != 47 else None, None)
    put(50, 0, "2031 TRODAT SALES")
    for i in range(12):
        put(50, 2 + i, 1234.56 + i)
    return rows


def book(tmp_path, name=NAME, sheet="Halsa_Turkey", **kw):
    return write_xlsx(tmp_path / name, {sheet: build_rows(**kw)})


def run(tmp_path, *, report_year=None, name=NAME, **kw):
    return import_trodat(book(tmp_path, name=name, **kw), report_year=report_year)


def codes(res, sev=None):
    return res.report.codes(sev)


def blob(res):
    return json.dumps(res.summary(), ensure_ascii=False, default=str) + res.report.to_json()


def line(res, code=None, label=None):
    return next(ln for ln in res.lines if (code is not None and ln.model_code == code) or
                (label is not None and ln.line_label == label))


# =============================================================================================
# Matrix structure (1–9)
# =============================================================================================
def test_01_valid_matrix_imports(tmp_path):
    res = run(tmp_path)
    assert res.ok, res.report.to_json()
    c = res.counts
    assert (c.model_lines, c.family_lines, c.lines_with_variant) == (36, 4, 10)
    assert c.lines_by_section == {"section_1": 28, "section_2": 8, "section_3": 4}
    assert c.detail_records == 40 * 12 and c.months_with_values == list(range(1, 13))


def test_02_missing_sheet_fails(tmp_path):
    res = run(tmp_path, sheet="Sheet1")
    assert not res.ok and "sheet_missing" in codes(res)


def test_03_moved_anchor_fails(tmp_path):
    res = run(tmp_path, move_total=True)
    assert not res.ok and "anchor_moved" in codes(res)


def test_04_missing_family_label_fails(tmp_path):
    for drop in (28, 47):
        res = run(tmp_path, drop_label=drop)
        assert not res.ok and "anchor_missing" in codes(res), drop


def test_05_month_header_drift_fails(tmp_path):
    h = MONTHS.copy()
    h[2] = "Mar"
    res = run(tmp_path, header1=h)
    assert not res.ok and "month_header_mismatch" in codes(res)
    h3 = MONTHS[:11] + ["Dec", "Total"]     # section 3 spells December out
    assert "month_header_mismatch" in codes(run(tmp_path, header3=h3))


def test_06_unexpected_extra_column_fails(tmp_path):
    res = run(tmp_path, extra_col=True)
    assert not res.ok and "unexpected_column" in codes(res)


def test_07_duplicate_section_label_fails(tmp_path):
    res = run(tmp_path, dup_title=True)
    assert not res.ok and "anchor_ambiguous" in codes(res)


def test_08_merged_style_layout_read_through_anchors(tmp_path):
    res = run(tmp_path)
    # The yearly series below row 47 (merged in the real file) is outside the declared bounds and never read.
    assert all(r.month in range(1, 13) for r in res.records)
    assert not any("TRODAT SALES" in (ln.line_label or "") for ln in res.lines)
    assert {a.name for a in T.TRODAT_SPEC.layout.anchors} >= {"section_1_total", "section_2_total", "section_3_title"}


def test_09_unsupported_layout_fails(tmp_path):
    rows = [["Model", "Var", *MONTHS]] + [[f"M{i}", None, *range(12), 66] for i in range(10)]
    p = write_xlsx(tmp_path / NAME, {"Halsa_Turkey": rows})
    res = import_trodat(p)
    assert not res.ok and res.records == []


# =============================================================================================
# Model identity (10–18)
# =============================================================================================
def test_10_numeric_model_stays_string(tmp_path):
    ln = line(run(tmp_path), code="9010")
    assert ln.model_code == "9010" and isinstance(ln.model_code, str)


def test_11_alphanumeric_model_exact(tmp_path):
    res = run(tmp_path)
    assert line(res, code="9902P4").variant == "P4"
    assert line(res, code="9100-0001").model_code == "9100-0001"


def test_12_variant_preserved(tmp_path):
    ln = line(run(tmp_path), code="9136")
    assert ln.variant == "displayx24" and ln.model_code == "9136"   # not concatenated


def test_13_blank_variant_missing(tmp_path):
    assert line(run(tmp_path), code="9010").variant is None


def test_14_stable_model_key(tmp_path):
    a, b = run(tmp_path), run(tmp_path)
    assert [ln.line_key for ln in a.lines] == [ln.line_key for ln in b.lines]
    assert line(a, code="9136").line_key == record_key("trodat_model", ["9136", "displayx24"])
    assert line(a, label="Other Printy").line_key == record_key("trodat_line", ["Other Printy"])


def test_15_stable_month_key(tmp_path):
    res = run(tmp_path)
    ln = line(res, code="9010")
    rec = next(r for r in res.records if r.line_key == ln.line_key and r.month == 5)
    assert rec.record_key == record_key("trodat_model_month", [ln.line_key, 2031, 5])
    code = f"from services.importers import record_key; print(record_key('trodat_model_month', ['{ln.line_key}', 2031, 5]))"
    outs = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONHASHSEED": s, "PATH": "/usr/bin:/bin"}).stdout for s in ("4", "44")}
    assert outs == {rec.record_key + "\n"}


def test_16_keys_ignore_filename_and_row(tmp_path):
    a = run(tmp_path)
    b = import_trodat(book(tmp_path, name="trodat sales report Halsa december 2031 .xlsx"))
    assert {r.record_key for r in a.records} == {r.record_key for r in b.records}
    src = (ROOT / "services/importers/trodat.py").read_text()
    assert "record_key(\"trodat_model\", [code_nv.value, variant])" in src and "source_row]" not in src


def test_17_halsa_sku_never_guessed(tmp_path):
    res = run(tmp_path)
    assert all(ln.halsa_sku is None for ln in res.lines) and res.counts.halsa_sku_mapping == "not_supplied"
    assert not hasattr(res.records[0], "sto_kod")


def test_18_no_fuzzy_matching():
    src = (ROOT / "services/importers/trodat.py").read_text().lower()
    for banned in ("difflib", "levenshtein", "rapidfuzz", "sto_kod", "stok_kodu", "model_code.startswith", "in sku", "casefold"):
        assert banned not in src, banned


# =============================================================================================
# Months / year (19–25)
# =============================================================================================
def test_19_months_map_to_1_12(tmp_path):
    res = run(tmp_path)
    ln = line(res, code="9010")
    by_month = {r.month: r.reported_quantity.value for r in res.records if r.line_key == ln.line_key}
    assert by_month == {m: float(val(ln.source_row, m)) for m in range(1, 13)}


def test_20_year_from_approved_filename_only(tmp_path):
    assert parse_report_filename("trodat sales report Halsa NOVEMBER 2025 .xlsx") == (2025, "NOVEMBER")
    assert parse_report_filename("trodat sales report Halsa december 2025 .xlsx") == (2025, "december")
    for bad in ("trodat 2025.xlsx", "Trodat satış 2025 kasım.xlsx", "trodat sales report Halsa 2025.xlsx"):
        assert parse_report_filename(bad) is None, bad
    res = run(tmp_path, name="trodat_2031_kopya.xlsx")
    assert not res.ok and "report_year_unknown" in codes(res)


def test_21_declared_year(tmp_path):
    res = run(tmp_path, name="trodat_kopya.xlsx", report_year=2030)
    assert res.ok and (res.counts.report_year, res.counts.report_year_origin) == (2030, "declared")
    assert all(r.report_year == 2030 for r in res.records)


def test_22_no_mtime_fallback(tmp_path):
    p = book(tmp_path, name="trodat_kopya.xlsx")
    os.utime(p, (1_900_000_000, 1_900_000_000))
    assert not import_trodat(p).ok
    src = (ROOT / "services/importers/trodat.py").read_text()
    assert "getmtime" not in src and "st_mtime" not in src


def test_23_no_current_year_fallback(tmp_path):
    res = run(tmp_path, name="trodat_kopya.xlsx")
    assert res.counts.report_year is None
    src = (ROOT / "services/importers/trodat.py").read_text()
    assert "today(" not in src and "now(" not in src and ".year" not in src.replace("report_year", "")


def test_24_unknown_year_requires_explicit_handling(tmp_path):
    res = run(tmp_path, name="trodat_kopya.xlsx")
    assert not res.ok and res.records == [] and res.failed_step == "year"


def test_25_filename_month_not_snapshot(tmp_path):
    res = run(tmp_path)
    assert res.counts.filename_month_label == "NOVEMBER" and res.counts.snapshot_date is None


# =============================================================================================
# Values (26–32)
# =============================================================================================
def test_26_decimal_preserved(tmp_path):
    res = run(tmp_path, overrides={(10, 3): 2.5})
    rec = next(r for r in res.records if r.model_code == "9010" and r.month == 3)
    assert rec.reported_quantity.value == 2.5 and res.counts.decimal_cells == 1


def test_27_whole_numbers_not_upgraded(tmp_path):
    res = run(tmp_path)
    assert res.counts.integer_like_cells == res.counts.filled_cells
    assert all(r.reported_quantity.confidence is Confidence.UNVERIFIED for r in res.records)
    assert not any("units" in f for f in T.TrodatModelMonth.__slots__)


def test_28_blank_stays_missing(tmp_path):
    res = run(tmp_path, blank_dec=True)
    assert res.ok and 12 not in res.counts.months_with_values
    assert not any(r.month == 12 for r in res.records) and res.counts.blank_cells == 40


def test_29_zero_stays_zero(tmp_path):
    res = run(tmp_path, overrides={(10, 4): 0})
    rec = next(r for r in res.records if r.model_code == "9010" and r.month == 4)
    assert rec.reported_quantity.value == 0.0 and not rec.reported_quantity.is_null


def test_30_value_confidence_unverified(tmp_path):
    assert {r.reported_quantity.confidence for r in run(tmp_path).records} == {Confidence.UNVERIFIED}


def test_31_excel_error_is_invalid(tmp_path):
    res = run(tmp_path, overrides={(10, 6): E("#N/A")})
    assert not res.ok and "excel_error" in codes(res)


def test_32_formula_in_values_handled_by_framework(tmp_path):
    res = run(tmp_path, overrides={(10, 6): F("C10*2", val(10, 6))})
    assert res.ok
    rec = next(r for r in res.records if r.model_code == "9010" and r.month == 6)
    assert rec.reported_quantity.kind is CellKind.FORMULA_VALUE
    assert "formula_cached_values" in codes(res)


# =============================================================================================
# Totals (33–40)
# =============================================================================================
def test_33_row_total_reconciles(tmp_path):
    rt = run(tmp_path).counts.row_total_checks
    assert rt["mismatched"] == 0 and rt["checked"] == 40 and rt["pack_expanded"] == 3


def test_34_row_total_mismatch_caught(tmp_path):
    rows = build_rows()
    rows[9][14] = F("SUM(C10:N10)", 1)          # cached total disagrees with the months
    res = import_trodat(write_xlsx(tmp_path / NAME, {"Halsa_Turkey": rows}))
    assert not res.ok and "row_total_mismatch" in codes(res)


def test_35_section_total_reconciles(tmp_path):
    st = run(tmp_path).counts.section_total_checks
    assert st["section_1"] == {"months_checked": 12, "month_mismatches": 0, "annual_mismatches": 0}
    assert st["section_2"]["month_mismatches"] == 0


def test_36_section_total_mismatch_caught(tmp_path):
    rows = build_rows()
    rows[30][4] = F("SUM(E2:E30)", 1)            # section 1 month total wrong → fatal
    res = import_trodat(write_xlsx(tmp_path / NAME, {"Halsa_Turkey": rows}))
    assert not res.ok and "section_total_mismatch" in codes(res)
    # Section 2's workbook controls are known to be inconsistent: warning + lines flagged, not fatal.
    res = run(tmp_path, s2_inconsistent=True)
    assert res.ok and "section_total_inconsistent" in codes(res, T.Severity.WARNING)
    assert res.counts.section_total_checks["section_2"]["month_mismatches"] == 1
    assert all(not ln.control_verified for ln in res.lines if ln.section == "section_2")


def test_37_no_workbook_wide_grand_total_used():
    # Row 41 mixes ratios against a yearly series with O31+O40; rows 50+ are a different metric. Neither is a
    # grand total of the detail lines, so neither is read.
    bounds = T.TRODAT_SPEC.layout.bounds
    assert bounds == "A1:P47"
    assert not any("41" in r.range for r in T.TRODAT_SPEC.layout.ranges)


def test_38_annual_section_total_checked(tmp_path):
    rows = build_rows()
    rows[30][14] = F("SUM(C31:N31)", 5)
    res = import_trodat(write_xlsx(tmp_path / NAME, {"Halsa_Turkey": rows}))
    assert not res.ok and res.counts.section_total_checks["section_1"]["annual_mismatches"] == 1


def test_39_aggregate_rows_excluded(tmp_path):
    res = run(tmp_path)
    assert "Total" not in {ln.line_label for ln in res.lines} and "Total" not in {ln.model_code for ln in res.lines}
    assert not any(ln.source_row in (31, 40, 41, 42, 43) for ln in res.lines)


def test_40_totals_never_double_counted(tmp_path):
    res = run(tmp_path)
    detail_sum = sum(r.reported_quantity.value for r in res.records if r.month == 1 and r.family is not None
                     and r.line_key in {ln.line_key for ln in res.lines if ln.section == "section_1"})
    assert detail_sum == sum(val(r, 1) for r in range(2, 30))
    assert len(res.records) == 40 * 12   # no 13th "Total" month, no total-row records


# =============================================================================================
# Privacy (41–45)
# =============================================================================================
def test_41_no_model_codes_in_summary(tmp_path):
    b = blob(run(tmp_path))
    for code in ("9010", "9902P4", "9100-0001", "9136"):
        assert code not in b, code


def test_42_no_variants_in_summary(tmp_path):
    b = blob(run(tmp_path))
    assert "displayx24" not in b and '"P4"' not in b


def test_43_no_quantities_in_summary(tmp_path):
    res = run(tmp_path, overrides={(10, 3): 98765})
    rows = build_rows(overrides={(10, 3): 98765})
    assert "98765" not in blob(res) and rows   # values never serialized


def test_44_errors_do_not_echo_values(tmp_path):
    rows = build_rows()
    rows[9][14] = F("SUM(C10:N10)", 77777)
    res = import_trodat(write_xlsx(tmp_path / NAME, {"Halsa_Turkey": rows}))
    b = blob(res)
    assert not res.ok and "77777" not in b and "9010" not in b


def test_45_no_absolute_path(tmp_path):
    deep = tmp_path / "Users" / "owner" / "Gizli"
    deep.mkdir(parents=True)
    res = import_trodat(book(deep))
    assert res.file == NAME and str(tmp_path) not in blob(res) and "Gizli" not in blob(res)


# =============================================================================================
# Isolation (46–52)
# =============================================================================================
LIVE = ("app.py", "dashboard_builder.py", "services/refresh.py", "services/context.py", "services/action_sources.py",
        "services/actions.py", "services/entity.py", "services/evidence.py", "services/agent.py")


def test_46_no_m5_live_import():
    for live in LIVE:
        text = (ROOT / live).read_text()
        assert "importers" not in text and "import_trodat" not in text, live


def test_47_no_api_exposure():
    assert "trodat" not in (ROOT / "app.py").read_text().lower()


def test_48_no_dashboard_exposure():
    text = (ROOT / "dashboard_builder.py").read_text()
    assert "import_trodat" not in text and "TrodatModelMonth" not in text


def test_49_no_action_source():
    for f in ("services/actions.py", "services/action_sources.py"):
        assert "trodat" not in (ROOT / f).read_text().lower()


def test_50_no_ai():
    src = (ROOT / "services/importers/trodat.py").read_text()
    for banned in ("openai", "anthropic", "services.ai", "services.agent", "httpx", "requests"):
        assert banned not in src, banned
    code = ("import sys, services.importers.trodat;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_51_no_refresh_dependency(tmp_path):
    assert "trodat" not in (ROOT / "services/refresh.py").read_text().lower()
    p = book(tmp_path)
    before = (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir()))
    assert import_trodat(p).ok
    assert (p.read_bytes(), sorted(x.name for x in tmp_path.iterdir())) == before
    src = (ROOT / "services/importers/trodat.py").read_text()
    for banned in ("write_text", "write_bytes", ".save(", "sqlite", "open(", "openpyxl", "load_workbook", "pandas"):
        assert banned not in src, banned


def test_52_no_mapping_fabricated():
    src = (ROOT / "services/importers/trodat.py").read_text()
    assert "halsa_sku: None = None" in src and "halsa_sku =" not in src.replace("halsa_sku_mapping", "")


# =============================================================================================
# Candidates and families
# =============================================================================================
def test_candidates_listed_never_selected(tmp_path):
    book(tmp_path)
    book(tmp_path, name="trodat sales report Halsa december 2031 .xlsx")
    book(tmp_path, name="Trodat kopya.xlsx")
    out = find_trodat_candidates(tmp_path)
    assert out == [{"file": "Trodat kopya.xlsx", "report_year": None, "filename_month_label": None},
                   {"file": "trodat sales report Halsa NOVEMBER 2031 .xlsx", "report_year": 2031,
                    "filename_month_label": "NOVEMBER"},
                   {"file": "trodat sales report Halsa december 2031 .xlsx", "report_year": 2031,
                    "filename_month_label": "december"}]


def test_families_from_exact_labels(tmp_path):
    res = run(tmp_path)
    assert line(res, label="Other Printy").family is SourceFamily.PRINTY
    assert line(res, label="Professional").family is SourceFamily.PROFESSIONAL
    assert line(res, label="Stamp pads").family is SourceFamily.STAMP_PADS
    assert line(res, code="9010").family is SourceFamily.PRINTY
    assert line(res, code="9010").family_confidence is Confidence.UNVERIFIED     # inferred, not labelled
    assert line(res, code="9244").family is SourceFamily.DATERS_NUMBERERS
    assert line(res, code="9136").family is None                                  # unlabelled section 2
    assert res.counts.lines_by_family["unlabelled"] == 8
