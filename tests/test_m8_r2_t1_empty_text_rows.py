"""M8-R2 / T1 — a Cari row whose only cells are empty text is not an account row.

The real DinamikCRM Cari export ends with a row that has no account code and whose only written cells hold the empty
string. The streaming scanner counted it as a data row, so every month bucket looked blank once (11 false
"blank amount" cells on the real file). The correction is opt-in per source (`CORE[...]["skip_empty_text_rows"]`,
enabled for the Cari aging export only) and narrow:

- a row whose ONLY written cells are "" (shared, inline or `str`) is not a data row; it is counted in
  `empty_text_rows` and none of its cells is counted in any column;
- every other row stays a data row exactly as before: an account code, an amount, a numeric zero, a formula (also one
  whose cached result is ""), an error, a boolean, a date, whitespace-only or other text, or an unresolvable shared
  string;
- genuine blank amounts and missing account codes are still counted;
- stock (row analysis D-055, footer recognition) and sales are unchanged.

Synthetic workbooks only. No AI, no network.
"""
import datetime as dt
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import unescape

import pytest

from services import accuracy as ACC
from tests import release_state as REL
from services import generation as GEN
from services import refresh as RF
from services import source_validation as SV
from tests.fixtures import synthetic_erp as SE
from tests.fixtures.xlsx_synth import E, F, MISSING, write_xlsx
from tests.test_m8_accuracy2 import clean_folder
from tests.test_m8_accuracy2b import analyze, book, products

pytestmark = pytest.mark.production_gate

HEAD = ["Hesap kodu", "Hesap adı", "Temsilci kodu", "Ağustos-2026 ve öncesi", "Eylül-2026", "Toplam Döviz Bakiye"]
BUCKETS = 2
ACCOUNTS = [["900.00.0101", "Sentetik Hesap A", "T1", 100, 50, 0],
            ["900.00.0102", "Sentetik Hesap B", "T2", 0, 10, 0],
            ["320.00.0103", "Sentetik Tedarikçi", "T1", -5, 0, 0]]


def cari(path, *extra_rows, shared=False, accounts=None):
    p = write_xlsx(Path(path), {"Sheet1": [HEAD] + [list(r) for r in (accounts or ACCOUNTS)] + [list(r) for r in extra_rows]})
    return to_shared_strings(p) if shared else p


def to_shared_strings(path, *, bad_index_for=None):
    """Rewrite every literal inline-string cell as a shared-string cell (Excel's own form, as in the real export).
    `bad_index_for`: text whose cells get an index outside the shared-string table (unresolvable)."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    table: list[str] = []

    def sub(m):
        ref, text = m.group(1), unescape(m.group(2) or "")
        if bad_index_for is not None and text == bad_index_for:
            return f'<c r="{ref}" t="s"><v>99999</v></c>'
        if text not in table:
            table.append(text)
        return f'<c r="{ref}" t="s"><v>{table.index(text)}</v></c>'

    inline = re.compile(r'<c r="([A-Z]+\d+)" t="inlineStr"><is><t xml:space="preserve">(.*?)</t></is></c>', re.S)
    for name in [n for n in parts if n.startswith("xl/worksheets/")]:
        parts[name] = inline.sub(sub, parts[name].decode("utf-8")).encode("utf-8")
    from xml.sax.saxutils import escape
    sst = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
           f'count="{len(table)}" uniqueCount="{len(table)}">'
           + "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in table) + "</sst>")
    parts["xl/sharedStrings.xml"] = sst.encode("utf-8")
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        b"</Types>", b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-'
                     b'officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
    parts["xl/_rels/workbook.xml.rels"] = parts["xl/_rels/workbook.xml.rels"].replace(
        b"</Relationships>", b'<Relationship Id="rIdSst" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             b'relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in parts.items():
            z.writestr(n, data)
    return path


def inspect(p):
    return SV.inspect_core("aging", Path(p).read_bytes())


def comparable(ins):
    d = ins.summary()
    d.pop("empty_text_rows")
    return d, ins.fields()


# ---------------------------------------------------------------------------------------------
# 1. the phantom row (the real-export shape) is not an account row
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("shared", [False, True], ids=["inline_string", "shared_string"])
@pytest.mark.parametrize("trailing", [["", ""], [None, ""], [""], ["", None, "", "", "", ""]],
                         ids=["code_and_name_empty", "name_only_empty", "code_only_empty", "every_cell_empty"])
def test_trailing_empty_text_row_is_not_a_data_row(tmp_path, shared, trailing):
    clean = inspect(cari(tmp_path / "clean.xlsx", shared=shared))
    ins = inspect(cari(tmp_path / "cari.xlsx", trailing, shared=shared))
    assert ins.ok and ins.data_rows == len(ACCOUNTS) and ins.empty_text_rows == 1
    assert ins.blank_amount_cells == 0 and ins.missing_required_values == 0
    assert comparable(ins) == comparable(clean)               # the phantom row changes nothing else
    assert ins.summary()["empty_text_rows"] == 1               # … and is reported, never silent


def test_two_trailing_rows_like_the_real_export(tmp_path):
    # the real export: one row holding only "" and one fully empty row after it
    p = cari(tmp_path / "cari.xlsx", ["", ""], [], shared=True)
    ins = inspect(p)
    assert ins.data_rows == len(ACCOUNTS) and ins.empty_text_rows == 1 and ins.blank_amount_cells == 0


def test_negative_control_without_the_option_the_phantom_row_is_counted(tmp_path):
    # the pre-T1 behaviour, replayed with the option off: 1 extra data row and one blank cell per bucket
    p = cari(tmp_path / "cari.xlsx", ["", ""], shared=True)
    scan = SV.scan_sheet(p.read_bytes(), header_row=1, wanted=SV.CORE["aging"]["required"], strip_headers=True,
                         header_match=SV._is_bucket)
    assert scan.data_rows == len(ACCOUNTS) + 1 and scan.empty_text_rows == 0
    buckets = [s for h, s in scan.columns.items() if SV._is_bucket(h)]
    assert sum(s.empty(scan.data_rows) for s in buckets) == BUCKETS


# ---------------------------------------------------------------------------------------------
# 2. genuine problems are still counted
# ---------------------------------------------------------------------------------------------
def test_genuine_blank_amount_is_still_counted(tmp_path):
    accounts = [list(r) for r in ACCOUNTS]
    accounts[1][4] = None                                       # a real account with a blank bucket
    ins = inspect(cari(tmp_path / "cari.xlsx", ["", ""], accounts=accounts, shared=True))
    assert ins.data_rows == len(ACCOUNTS) and ins.blank_amount_cells == 1 and ins.empty_text_rows == 1


def test_amounts_without_account_code_stay_a_data_row_with_missing_identity(tmp_path):
    ins = inspect(cari(tmp_path / "cari.xlsx", [None, None, None, 0, 5, 0], shared=True))
    assert ins.data_rows == len(ACCOUNTS) + 1 and ins.empty_text_rows == 0
    assert ins.missing_required_values == 1                     # missing account code still counted


# ---------------------------------------------------------------------------------------------
# 3. every row carrying anything is preserved (one meaningful cell beside empty text is enough)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cell, col", [
    (0, 3), (0.0, 4), (-5, 3),                                  # numeric zero / amounts
    (F("1+1", 2), 3), (F('""', ""), 1), (F("A1", MISSING), 3),   # formulas, also one whose cached result is ""
    (E("#N/A"), 3), (F("1/0", E("#DIV/0!")), 4),                # errors
    (True, 2), (dt.date(2026, 9, 30), 2),                       # boolean, date
    (" ", 1), ("x", 1), ("TOPLAM", 0),                          # whitespace-only and other text
], ids=["zero_int", "zero_float", "negative", "formula", "formula_empty_result", "formula_missing_cache",
        "error", "formula_error", "bool", "date", "whitespace", "text", "label"])
@pytest.mark.parametrize("shared", [False, True], ids=["inline_string", "shared_string"])
def test_rows_with_any_meaningful_cell_are_preserved(tmp_path, cell, col, shared):
    row = ["", "", "", "", "", ""]                              # empty text everywhere …
    row[col] = cell                                             # … except one meaningful cell
    ins = inspect(cari(tmp_path / "cari.xlsx", row, shared=shared))
    assert ins.data_rows == len(ACCOUNTS) + 1 and ins.empty_text_rows == 0


def test_unresolvable_shared_string_is_not_treated_as_empty(tmp_path):
    p = to_shared_strings(cari(tmp_path / "cari.xlsx", ["", "BİLİNMEYEN"]), bad_index_for="BİLİNMEYEN")
    ins = inspect(p)
    assert ins.data_rows == len(ACCOUNTS) + 1 and ins.empty_text_rows == 0      # text unknown: fails closed


@pytest.mark.parametrize("shared", [False, True], ids=["inline_string", "shared_string"])
def test_real_account_row_with_empty_text_cells_is_unchanged(tmp_path, shared):
    accounts = [list(r) for r in ACCOUNTS]
    accounts[0][2] = ""                                         # empty representative on a real account
    before = SV.scan_sheet(cari(tmp_path / "a.xlsx", accounts=accounts, shared=shared).read_bytes(), header_row=1,
                           wanted=SV.CORE["aging"]["required"], strip_headers=True, header_match=SV._is_bucket)
    ins = inspect(tmp_path / "a.xlsx")
    assert ins.data_rows == before.data_rows == len(ACCOUNTS) and ins.empty_text_rows == 0
    assert ins.columns["Temsilci kodu"] == before.columns["Temsilci kodu"].counts(before.data_rows)


# ---------------------------------------------------------------------------------------------
# 4. scope: only the Cari source opts in; stock and sales are unchanged
# ---------------------------------------------------------------------------------------------
def test_only_the_cari_source_opts_in():
    assert {k for k, spec in SV.CORE.items() if spec.get("skip_empty_text_rows")} == {"aging"}


def test_stock_footer_recognition_and_counts_are_unchanged(tmp_path):
    p = book(tmp_path / "s.xlsx", products())
    a, ins = analyze(p), SV.inspect_core("stock", p.read_bytes())
    assert a.footer_kinds == {"aggregate": 2, "label": 1} and a.footer_checks["matched"] == 8
    assert ins.data_rows == 5 and ins.missing_required_values == 0 and ins.empty_text_rows == 0


def test_stock_behaviour_for_an_empty_text_row_is_unchanged(tmp_path):
    # stock does not opt in; its numbers are exactly those of `main` before T1 (pinned, not endorsed): the row
    # analysis (D-055) already skips literal empty / whitespace text cells, so it sees no extra row and the footer
    # stays recognized; the scan still counts the row as a data row (6), and — pre-existing, outside T1 — an
    # empty-text product code counts as present, so no missing value is reported
    empty = [""] * 10
    p = book(tmp_path / "s.xlsx", products(), extra=[empty])
    a, ins = analyze(p), SV.inspect_core("stock", p.read_bytes())
    assert a.parsed_rows == 8 and a.ambiguous_rows == () and a.footer_kinds == {"aggregate": 2, "label": 1}
    assert ins.data_rows == 6 and ins.missing_required_values == 0 and ins.empty_text_rows == 0


def test_sales_keeps_counting_an_empty_text_row(tmp_path):
    head = list(SV.CORE["sales"]["required"])
    line = ["2026-01-02", 10, 1, "A", 1, "P1", "Proje", "M1", "Müşteri", "SP-1", "Ürün", "G"]
    p = write_xlsx(tmp_path / "sales.xlsx", {"S": [head, line, [""] * len(head)]})
    ins = SV.inspect_core("sales", p.read_bytes())
    assert ins.data_rows == 2 and ins.empty_text_rows == 0


# ---------------------------------------------------------------------------------------------
# 5. end to end: generation evidence and the production gate see the same Cari with or without the phantom row
# ---------------------------------------------------------------------------------------------
def _manifest(folder):
    paths, decl, origin = GEN.select_core(folder)
    frozen = GEN.freeze(paths)
    v = RF.validate(paths, None, SE.AS_OF, frozen=frozen)
    assert v["ok"], v["errors"]
    return GEN.build_manifest(frozen, v["frames"], decl, origin, SE.AS_OF)


def _append_empty_text_row(path):
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    n = ws.max_row
    with zipfile.ZipFile(path) as z:
        parts = {name: z.read(name) for name in z.namelist()}
    sheet = next(name for name in parts if name.startswith("xl/worksheets/sheet"))
    row = f'<row r="{n + 1}"><c r="B{n + 1}" t="inlineStr"><is><t></t></is></c></row>'.encode()
    parts[sheet] = parts[sheet].replace(b"</sheetData>", row + b"</sheetData>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def test_generation_evidence_and_gate_ignore_the_phantom_row(tmp_path):
    a = clean_folder(tmp_path / "a")
    b = clean_folder(tmp_path / "b")
    _append_empty_text_row(Path(b) / SE.AGING_FILE)
    ra, rb = _manifest(a).record("aging"), _manifest(b).record("aging")
    assert rb.validation["empty_text_rows"] == 1 and ra.validation["empty_text_rows"] == 0
    assert rb.validation["data_rows"] == ra.validation["data_rows"]
    assert rb.validation["blank_amount_cells"] == ra.validation["blank_amount_cells"] == 0
    assert rb.evidence.blank_amount_cells == ra.evidence.blank_amount_cells == 0
    assert rb.evidence.fields == ra.evidence.fields
    gate = lambda m: ACC.Gate(ACC.REGISTER, ACC.DEFINITIONS, m.bound_snapshot({r.key: r.sha256 for r in m.core}),
                              SE.AS_OF, m.bound_snapshot({r.key: r.sha256 for r in m.core}).generation_id)
    ma, mb = _manifest(a), _manifest(b)
    da, db_ = gate(ma).decide("receivables.collections"), gate(mb).decide("receivables.collections")
    assert da.state == db_.state and da.reasons_tr == db_.reasons_tr and da.codes == db_.codes
    assert not any("tutar alanında boş hücre" in r for r in db_.reasons_tr)     # the T1 symptom is absent
    REL.assert_release_state()                    # D-073: one certified metric, flag True
