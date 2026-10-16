"""M6-1 importer framework tests.

Every workbook is synthetic and generated at runtime (tests/fixtures/xlsx_synth.py); codes,
names and amounts are invented. Tests 01–42 follow the numbered M6-1 acceptance list; the rest
cover business dates, read-only safety, isolation from M5 and the absence of AI.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from services.importers import (
    Anchor,
    AttributeSource,
    Audience,
    CellKind,
    Confidence,
    DateOrigin,
    DateRule,
    FieldSpec,
    FieldType,
    Header,
    IdentifierRole,
    MasterIndex,
    MatchStatus,
    MatrixLayout,
    MatrixRange,
    MatrixSection,
    RowRule,
    Severity,
    SourceSpec,
    SpecError,
    TableLayout,
    VerifySpec,
    downgrade,
    keys_with_occurrence,
    record_key,
    run_import,
)
from services.importers.excel import EXCEL_ERRORS, OTHER_ERROR, WorkbookPair
from tests.fixtures.xlsx_synth import MISSING, E, F, write_xlsx

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "services" / "importers"

T = FieldType
ID = FieldType.OPAQUE_ID


def _wb(tmp_path: Path, rows, name: str = "kaynak_2026.xlsx", sheet: str = "Veri", **kw) -> Path:
    return write_xlsx(tmp_path / name, {sheet: rows}, **kw)


def _spec(fields, header_row: int = 1, **kw) -> SourceSpec:
    base = dict(key="synthetic", audience=Audience.SHARED, filename_patterns=("kaynak_*.xlsx",), sheet="Veri",
                layout=TableLayout(header_row), fields=tuple(fields))
    base.update(kw)
    return SourceSpec(**base)


def _master(tmp_path: Path, codes, alt=None, name: str = "musteri_master.xlsx") -> MasterIndex:
    rows = [["KODU", "ESKI KOD"]] + [[c, (alt[i] if alt else None)] for i, c in enumerate(codes)]
    p = write_xlsx(tmp_path / name, {"liste": rows})
    spec = SourceSpec(key="customer_master", audience=Audience.SHARED, filename_patterns=("musteri_*.xlsx",),
                      sheet="liste", layout=TableLayout(1),
                      fields=(FieldSpec("KODU", ID, header="KODU"),
                              FieldSpec("ESKI_KOD", ID, header="ESKI KOD", required=False)))
    res = run_import(p, spec)
    assert res.ok, res.report.to_json()
    return MasterIndex.from_result(res, ["KODU", "ESKI_KOD"] if alt else ["KODU"])


def _codes(result, severity=None):
    return result.report.codes(severity)


# =============================================================================================
# Core models (1–10)
# =============================================================================================
def test_01_confidence_states_serialize_exactly():
    assert [c.value for c in Confidence] == ["trusted", "incomplete", "stale", "unverified", "invalid"]
    for c in Confidence:
        assert json.loads(json.dumps({"c": c.value}))["c"] == c.value
        assert Confidence(c.value) is c
    # downgrade only: parsing can never move a value to a better state
    assert downgrade(Confidence.STALE, Confidence.TRUSTED) is Confidence.STALE
    assert downgrade(Confidence.TRUSTED, Confidence.INVALID) is Confidence.INVALID
    assert not Confidence.STALE.usable_for_decisions and not Confidence.INVALID.usable_for_decisions
    assert Confidence.INCOMPLETE.usable_for_decisions and Confidence.TRUSTED.usable_for_decisions


def test_02_audience_classification(tmp_path):
    assert [a.value for a in Audience] == ["shared", "owner"]
    p = _wb(tmp_path, [["Kod"], ["K-1"]])
    for aud in Audience:
        res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod")], audience=aud))
        assert res.ok and res.audience is aud
        assert res.report.audience is aud
        assert res.summary()["audience"] == aud.value


@pytest.mark.parametrize("header_row", [1, 4, 9, 11])
def test_03_header_rows_are_configuration(tmp_path, header_row):
    filler = [["Rapor başlığı"]] + [[None]] * (header_row - 2) if header_row > 1 else []
    rows = filler + [["Kod", "Adet"], ["K-1", 3], ["K-2", 5]]
    p = _wb(tmp_path, rows)
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"), FieldSpec("adet", T.INTEGER, header="Adet")],
                              header_row=header_row))
    assert res.ok, res.report.to_json()
    assert [(r.get("kod"), r.get("adet")) for r in res.records] == [("K-1", 3), ("K-2", 5)]
    assert [r.row for r in res.records] == [header_row + 1, header_row + 2]


def test_04_exact_raw_header_matching(tmp_path):
    p = _wb(tmp_path, [["Kod", "Adet"], ["K-1", 1]])
    assert run_import(p, _spec([FieldSpec("kod", ID, header="Kod")])).ok
    for wrong in ("kod", "KOD", " Kod", "Kod "):
        res = run_import(p, _spec([FieldSpec("kod", ID, header=wrong)]))
        assert not res.ok and "header_missing" in _codes(res, Severity.ERROR)


def test_05_trailing_space_headers(tmp_path):
    p = _wb(tmp_path, [["Tutar ", "Kod"], [12.5, "K-1"]])
    ok = run_import(p, _spec([FieldSpec("tutar", T.DECIMAL, header="Tutar ")]))
    assert ok.ok and ok.records[0].get("tutar") == 12.5
    bad = run_import(p, _spec([FieldSpec("tutar", T.DECIMAL, header="Tutar")]))
    assert not bad.ok and "header_missing" in _codes(bad)


def test_06_newline_headers(tmp_path):
    p = _wb(tmp_path, [["Satış\nTutarı", "Kod"], [7.0, "K-1"]])
    ok = run_import(p, _spec([FieldSpec("satis", T.DECIMAL, header="Satış\nTutarı")]))
    assert ok.ok and ok.records[0].get("satis") == 7.0
    for wrong in ("Satış Tutarı", "Satış\r\nTutarı", "SatışTutarı"):
        assert not run_import(p, _spec([FieldSpec("satis", T.DECIMAL, header=wrong)])).ok


def test_07_duplicate_header_occurrences(tmp_path):
    p = _wb(tmp_path, [["Kod", "Tutar", "Ara", "Tutar"], ["K-1", 10.0, "x", 99.0]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"),
                               FieldSpec("tutar_1", T.DECIMAL, header=Header("Tutar", 1)),
                               FieldSpec("tutar_2", T.DECIMAL, header=Header("Tutar", 2))]))
    assert res.ok
    assert res.records[0].get("tutar_1") == 10.0 and res.records[0].get("tutar_2") == 99.0
    dup = [i for i in res.report.issues if i.code == "duplicate_header"]
    assert len(dup) == 1 and dup[0].region == "B,D" and dup[0].count == 2
    assert "Tutar" not in dup[0].message  # positions only, the raw header is not echoed
    # No invented suffixes: pandas-style "Tutar.1" does not exist in the workbook.
    assert not run_import(p, _spec([FieldSpec("t", T.DECIMAL, header="Tutar.1")])).ok
    # Selecting a duplicated header without an occurrence is ambiguous → error.
    amb = run_import(p, _spec([FieldSpec("t", T.DECIMAL, header="Tutar")]))
    assert not amb.ok and "ambiguous_header" in _codes(amb, Severity.ERROR)
    # Occurrence beyond what exists → missing.
    assert "header_missing" in _codes(run_import(p, _spec([FieldSpec("t", T.DECIMAL, header=Header("Tutar", 3))])))


def test_08_missing_required_field_fails(tmp_path):
    p = _wb(tmp_path, [["Kod"], ["K-1"]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"), FieldSpec("adet", T.INTEGER, header="Adet")]))
    assert not res.ok and res.status == "failed" and res.failed_step == "validate"
    assert res.records == []
    issue = next(i for i in res.report.issues if i.code == "header_missing")
    assert issue.severity == Severity.ERROR and issue.field == "adet"


def test_09_missing_optional_field_warns(tmp_path):
    p = _wb(tmp_path, [["Kod"], ["K-1"]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"),
                               FieldSpec("sektor", T.STRING, header="Sektör", required=False)]))
    assert res.ok
    assert "header_missing_optional" in _codes(res, Severity.WARNING)
    assert res.field_confidence["sektor"] is Confidence.INVALID
    assert "sektor" not in res.records[0].values


def test_10_unexpected_layout_fails_without_searching(tmp_path):
    # Header actually on row 2; the spec says row 1. The framework must not go looking for it.
    p = _wb(tmp_path, [["Rapor"], ["Kod", "Adet"], ["K-1", 1]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod")], header_row=1))
    assert not res.ok and "header_missing" in _codes(res)
    assert run_import(p, _spec([FieldSpec("kod", ID, header="Kod")], header_row=2)).ok
    # Wrong sheet → fails too.
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod")], header_row=2, sheet="Baska"))
    assert not res.ok and "sheet_missing" in _codes(res)
    # Sheet by index works.
    assert run_import(p, _spec([FieldSpec("kod", ID, header="Kod")], header_row=2, sheet=0)).ok


# =============================================================================================
# Excel safety (11–20)
# =============================================================================================
def _formula_book(tmp_path, **kw):
    rows = [["Kod", "Adet", "Toplam"],
            ["K-1", 2, F("B2*3", 6)],
            ["K-2", 4, 12],
            ["K-3", 5, F("B4*3", 15)]]
    return _wb(tmp_path, rows, **kw)


def test_11_formula_cells_detected(tmp_path):
    p = _formula_book(tmp_path)
    with WorkbookPair(p) as wb:
        rows = dict(wb.sheet("Veri").iter_rows())
    assert rows[2][2].kind is CellKind.FORMULA_VALUE
    assert rows[3][2].kind is CellKind.LITERAL
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"), FieldSpec("toplam", T.INTEGER, header="Toplam")]))
    stats = {f.field: f for f in res.report.fields}
    assert stats["toplam"].formula_count == 2 and stats["kod"].formula_count == 0
    info = next(i for i in res.report.issues if i.code == "formula_cached_values")
    assert info.severity == Severity.INFO and info.count == 2


def test_12_cached_formula_value_distinguished_from_literal(tmp_path):
    p = _formula_book(tmp_path)
    res = run_import(p, _spec([FieldSpec("toplam", T.INTEGER, header="Toplam")]))
    a, b, c = (r.values["toplam"] for r in res.records)
    assert (a.value, a.kind, a.from_last_excel_calculation) == (6, CellKind.FORMULA_VALUE, True)
    assert (b.value, b.kind, b.from_last_excel_calculation) == (12, CellKind.LITERAL, False)
    assert c.kind is CellKind.FORMULA_VALUE
    # Formula policy: forbid → error, warn → warning.
    assert "formula_forbidden" in _codes(run_import(p, _spec([FieldSpec("toplam", T.INTEGER, header="Toplam",
                                                                       formulas="forbid")])), Severity.ERROR)
    assert "formula_present" in _codes(run_import(p, _spec([FieldSpec("toplam", T.INTEGER, header="Toplam",
                                                                     formulas="warn")])), Severity.WARNING)


def test_13_missing_cached_formula_result_reported(tmp_path):
    p = _wb(tmp_path, [["Kod", "Toplam"], ["K-1", F("B9*2", MISSING)], ["K-2", 4]])
    fields = [FieldSpec("kod", ID, header="Kod"), FieldSpec("toplam", T.INTEGER, header="Toplam")]
    res = run_import(p, _spec(fields))  # default: error
    assert not res.ok and "formula_missing_cache" in _codes(res, Severity.ERROR)
    res = run_import(p, _spec(fields, missing_cache=Severity.WARNING))
    assert res.ok and "formula_missing_cache" in _codes(res, Severity.WARNING)
    nv = res.records[0].values["toplam"]
    assert nv.kind is CellKind.FORMULA_MISSING_CACHE and nv.confidence is Confidence.INVALID
    assert nv.error == "missing_cache" and not nv.is_null
    stats = next(f for f in res.report.fields if f.field == "toplam")
    assert stats.missing_cache_count == 1
    # Per-field override wins over the spec default.
    over = [fields[0], FieldSpec("toplam", T.INTEGER, header="Toplam", missing_cache=Severity.WARNING)]
    assert run_import(p, _spec(over)).ok


def test_14_ref_error_detected(tmp_path):
    p = _wb(tmp_path, [["Kod", "Tutar"], ["K-1", E("#REF!")], ["K-2", 3.0]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"), FieldSpec("tutar", T.DECIMAL, header="Tutar")]))
    assert not res.ok
    issue = next(i for i in res.report.issues if i.code == "excel_error")
    assert issue.severity == Severity.ERROR and issue.field == "tutar" and issue.region == "B"
    assert issue.count == 1 and "#REF!" in issue.message
    stats = next(f for f in res.report.fields if f.field == "tutar")
    assert stats.errors_by_type == {"#REF!": 1} and stats.error_count == 1


def test_15_div0_from_formula_detected(tmp_path):
    p = _wb(tmp_path, [["Kod", "Oran"], ["K-1", F("1/0", E("#DIV/0!"))], ["K-2", F("1/2", 0.5)]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"),
                               FieldSpec("oran", T.DECIMAL, header="Oran", tolerate_invalid_cells=True)]))
    assert res.ok
    nv = res.records[0].values["oran"]
    assert nv.kind is CellKind.FORMULA_ERROR and nv.error == "#DIV/0!" and nv.confidence is Confidence.INVALID
    assert res.records[1].values["oran"].value == 0.5
    stats = next(f for f in res.report.fields if f.field == "oran")
    assert stats.errors_by_type == {"#DIV/0!": 1} and stats.formula_count == 2


def test_16_all_supported_error_codes_classified(tmp_path):
    codes = sorted(EXCEL_ERRORS)
    assert set(codes) == {"#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!"}
    rows = [["Hata"]] + [[E(c)] for c in codes] + [[E("#SPILL!")], ["#N/A"]]  # last one is literal *text*
    p = _wb(tmp_path, rows)
    res = run_import(p, _spec([FieldSpec("hata", T.STRING, header="Hata", tolerate_invalid_cells=True)]))
    got = [r.values["hata"] for r in res.records]
    assert [nv.error for nv in got[: len(codes)]] == codes
    assert all(nv.kind is CellKind.ERROR and nv.confidence is Confidence.INVALID for nv in got[: len(codes) + 1])
    assert got[len(codes)].error == OTHER_ERROR
    # A text cell that merely reads "#N/A" is a literal string, not an Excel error.
    assert got[-1].kind is CellKind.LITERAL and got[-1].value == "#N/A" and got[-1].error is None
    stats = next(f for f in res.report.fields if f.field == "hata")
    assert stats.error_count == len(codes) + 1
    assert stats.errors_by_type == {**{c: 1 for c in codes}, OTHER_ERROR: 1}


def test_17_external_workbook_reference_detected(tmp_path):
    target = "file:///C:/Users/Someone/Gizli Klasor/Diger Kitap.xlsx"
    p = _wb(tmp_path, [["Kod", "Bagli"], ["K-1", F("[1]S!A1", 5)], ["K-2", F("Tablo1[Sutun]", 2)]],
            external_links=[target])
    fields = [FieldSpec("kod", ID, header="Kod"), FieldSpec("bagli", T.INTEGER, header="Bagli")]
    res = run_import(p, _spec(fields))
    assert res.ok  # default severity: warning
    link = next(i for i in res.report.issues if i.code == "external_link")
    assert link.severity == Severity.WARNING and link.count == 1
    assert res.report.external_links.count == 1
    assert res.report.external_links.names == ["Diger Kitap.xlsx"]
    assert res.report.external_links.formula_refs == 1  # the table reference is not an external ref
    blob = res.report.to_json()
    assert "Users" not in blob and "Someone" not in blob and "C:" not in blob and "Gizli" not in blob
    # A source can make an external dependency an error (e.g. a future Bütçe spec).
    strict = run_import(p, _spec(fields, external_links=Severity.ERROR))
    assert not strict.ok and "external_link" in _codes(strict, Severity.ERROR)


def _matrix_rows(label_row: int = 3, extra_col: bool = False, dup_label: bool = False, rename_col: bool = False):
    rows = [["Synthetic Model Raporu", None, None, None],      # title (a merged area in real files)
            ["Dönem", dt.date(2026, 9, 1), None, None]]
    while len(rows) < label_row - 1:
        rows.append([None])
    header = ["Model", "Ocak", "Şubat" if not rename_col else "Subat", "Mart"]
    if extra_col:
        header.append("Nisan")
    rows.append(header)
    rows += [["M-100", 3, 4, 5], ["M-200", 0, E("#N/A"), 2], ["M-300", 1, 1, 1]]
    if dup_label:
        rows.append(["Model", None, None, None])
    rows.append([None])
    rows.append(["Toplam", F("SUM(B4:B6)", 4), None, None])
    return rows


def _matrix_spec(**kw) -> SourceSpec:
    layout = MatrixLayout(
        bounds="A1:F20",
        anchors=(Anchor("donem", "Dönem", expected="A2", search="A1:A5"),
                 Anchor("model", "Model", expected="A3", search="A1:A12")),
        sections=(MatrixSection("aylik", anchor="model", column_labels=("Ocak", "Şubat", "Mart"),
                                type=T.INTEGER, tolerate_invalid_cells=True),),
        ranges=(MatrixRange("ilk_satir", "B4:D4", type=T.INTEGER),),
    )
    base = dict(key="synthetic_matrix", audience=Audience.SHARED, filename_patterns=("matris_*.xlsx",),
                sheet="Rapor", layout=layout,
                fields=(FieldSpec("donem", T.DATE, anchor="donem", offset=(0, 1)),
                        FieldSpec("baslik", T.STRING, cell="A1")))
    base.update(kw)
    return SourceSpec(**base)


def test_18_matrix_read_through_explicit_cell_map(tmp_path):
    p = write_xlsx(tmp_path / "matris_2026.xlsx", {"Rapor": _matrix_rows()})
    res = run_import(p, _matrix_spec())
    assert res.ok, res.report.to_json()
    cells = next(r for r in res.records if "block" not in r.values)
    assert cells.get("donem") == dt.date(2026, 9, 1) and cells.get("baslik") == "Synthetic Model Raporu"
    sec = [r for r in res.records if r.get("block") == "aylik"]
    assert len(sec) == 9
    grid = {(r.get("row_label"), r.get("column_label")): r.values["value"] for r in sec}
    assert grid[("M-100", "Ocak")].value == 3 and grid[("M-300", "Mart")].value == 1
    na = grid[("M-200", "Şubat")]
    assert na.error == "#N/A" and na.confidence is Confidence.INVALID  # cell-level, section stays usable
    rng = [r for r in res.records if r.get("block") == "ilk_satir"]
    assert [r.get("column_label") for r in rng] == ["B4", "C4", "D4"] and [r.get("value") for r in rng] == [3, 4, 5]
    # Section stopped at the first empty row label; the "Toplam" row below the gap is not read.
    assert "Toplam" not in {r.get("row_label") for r in sec}
    assert len({r.key for r in res.records}) == len(res.records)


def test_19_matrix_label_drift_fails(tmp_path):
    def run(rows):
        return run_import(write_xlsx(tmp_path / "matris_2026.xlsx", {"Rapor": rows}), _matrix_spec())

    moved = run(_matrix_rows(label_row=5))          # label one row lower
    assert not moved.ok
    issue = next(i for i in moved.report.issues if i.code == "anchor_moved")
    assert issue.field == "model" and issue.region == "A3->A5"
    missing_rows = _matrix_rows()
    missing_rows[2][0] = "Ürün"                     # label renamed → missing
    assert "anchor_missing" in _codes(run(missing_rows), Severity.ERROR)
    assert "anchor_ambiguous" in _codes(run(_matrix_rows(dup_label=True)), Severity.ERROR)
    assert "section_label_mismatch" in _codes(run(_matrix_rows(rename_col=True)), Severity.ERROR)
    assert "section_label_mismatch" in _codes(run(_matrix_rows(extra_col=True)), Severity.ERROR)
    for res in (moved,):
        assert res.records == [] and res.failed_step == "validate"


def test_20_excel_errors_never_become_plain_missing_values(tmp_path):
    p = _wb(tmp_path, [["Kod", "Oran"], ["K-1", E("#DIV/0!")], ["K-2", None], ["K-3", 0.25]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"),
                               FieldSpec("oran", T.DECIMAL, header="Oran", tolerate_invalid_cells=True)]))
    err, blank, ok = (r.values["oran"] for r in res.records)
    assert err.value is None and err.error == "#DIV/0!" and err.confidence is Confidence.INVALID
    assert err.is_invalid and not err.is_null
    assert blank.is_null and blank.error is None and blank.confidence is not Confidence.INVALID
    assert ok.value == 0.25
    stats = next(f for f in res.report.fields if f.field == "oran")
    assert (stats.filled, stats.null, stats.error_count) == (2, 1, 1)


# =============================================================================================
# Identifiers and entity resolution (21–30)
# =============================================================================================
def _id_import(tmp_path, values, **field_kw):
    p = _wb(tmp_path, [["Kod"]] + [[v] for v in values])
    return run_import(p, _spec([FieldSpec("kod", ID, header="Kod", **field_kw)]))


def test_21_leading_zero_id_preserved(tmp_path):
    res = _id_import(tmp_path, ["007", "000123", "0"])
    assert [r.get("kod") for r in res.records] == ["007", "000123", "0"]


def test_22_punctuated_id_preserved(tmp_path):
    vals = ["120.01.0042", "A-12/7", "  K 1  ", "X_9*"]
    res = _id_import(tmp_path, vals)
    assert [r.get("kod") for r in res.records] == vals  # no trimming unless declared
    trimmed = _id_import(tmp_path, vals, trim=True)
    assert trimmed.records[2].get("kod") == "K 1"


def test_23_long_numeric_looking_id_preserved(tmp_path):
    res = _id_import(tmp_path, ["12345678901234567890", "00000000000000000001"])
    assert [r.get("kod") for r in res.records] == ["12345678901234567890", "00000000000000000001"]
    stats = res.report.fields[0]
    assert stats.shapes == {"99999999999999999999": 2} and stats.length_max == 20
    # A float cell has already lost the code → invalid, never converted.
    bad = _id_import(tmp_path, [1.2345678901234568e19, "K-1"], tolerate_invalid_cells=True)
    assert bad.records[0].values["kod"].error == "id_float" and bad.records[0].get("kod") is None
    # An integer cell is accepted as text but flagged (leading zeros unverifiable) and never trusted.
    num = run_import(_wb(tmp_path, [["Kod"], [42]]),
                     _spec([FieldSpec("kod", ID, header="Kod")], default_confidence=Confidence.TRUSTED))
    assert num.records[0].get("kod") == "42" and "id_numeric_cell" in _codes(num, Severity.WARNING)
    assert num.records[0].values["kod"].confidence is Confidence.UNVERIFIED


def test_24_null_id_classified(tmp_path, ):
    master = _master(tmp_path, ["C-001"])
    p = _wb(tmp_path, [["Kod", "Adet"], [None, 1], ["", 2], ["C-001", 3]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod", role=IdentifierRole.ORDER_CUSTOMER,
                                         master="customer_master"),
                               FieldSpec("adet", T.INTEGER, header="Adet")]), masters={"customer_master": master})
    assert res.ok
    assert [r.links["kod"].status for r in res.records] == [MatchStatus.NULL, MatchStatus.NULL, MatchStatus.MATCHED]
    assert all(r.values["kod"].is_null for r in res.records[:2])
    mr = res.match_reports[0]
    assert (mr.total, mr.non_null, mr.counts[MatchStatus.NULL]) == (3, 1, 2)


def test_25_malformed_and_placeholder_ids_follow_source_rule(tmp_path):
    vals = ["120.01.0042", "12.1.42", "IPTAL-PROJE", "120.01.0043*"]
    kw = dict(pattern=r"\d{3}\.\d{2}\.\d{4}", placeholders=(r"IPTAL.*", r".+\*"))
    strict = _id_import(tmp_path, vals, **kw)
    assert not strict.ok and "id_malformed" in _codes(strict, Severity.ERROR)
    tolerant = _id_import(tmp_path, vals, tolerate_invalid_cells=True, **kw)
    assert tolerant.ok
    errs = [r.values["kod"].error for r in tolerant.records]
    assert errs == [None, "id_malformed", "id_placeholder", "id_placeholder"]
    ph = next(i for i in tolerant.report.issues if i.code == "id_placeholder")
    assert ph.severity == Severity.WARNING and ph.count == 2
    # Placeholder severity is per spec too.
    hard = _id_import(tmp_path, ["120.01.0042", "IPTAL-PROJE"], on_placeholder=Severity.ERROR, **kw)
    assert not hard.ok
    # Unusual shapes are not rejected globally: without a pattern every string is a valid code.
    assert _id_import(tmp_path, vals).ok


def _er_import(tmp_path, codes, master, role=IdentifierRole.ORDER_CUSTOMER, field="kod"):
    p = _wb(tmp_path, [["Kod", "Sira"]] + [[c, i] for i, c in enumerate(codes)], name="kaynak_er.xlsx")
    spec = _spec([FieldSpec(field, ID, header="Kod", role=role, master="customer_master"),
                  FieldSpec("sira", T.INTEGER, header="Sira")])
    return run_import(p, spec, masters={"customer_master": master})


def test_26_exact_match(tmp_path):
    master = _master(tmp_path, ["C-001", "C-002", "007"])
    res = _er_import(tmp_path, ["C-002", "007", "C-001"], master)
    assert [r.links["kod"].status for r in res.records] == [MatchStatus.MATCHED] * 3
    assert [r.links["kod"].master_row for r in res.records] == [1, 2, 0]
    assert res.match_reports[0].rates["matched"] == 1.0


def test_27_unmatched(tmp_path):
    master = _master(tmp_path, ["C-001"])
    res = _er_import(tmp_path, ["C-999", "C-001"], master)
    assert [r.links["kod"].status for r in res.records] == [MatchStatus.UNMATCHED, MatchStatus.MATCHED]
    assert res.records[0].links["kod"].master_row is None
    assert res.match_reports[0].counts[MatchStatus.UNMATCHED] == 1


def test_28_duplicate_master_never_matches(tmp_path):
    master = _master(tmp_path, ["C-001", "C-002", "C-001"])
    assert master.duplicate_keys == 1
    res = _er_import(tmp_path, ["C-001", "C-002"], master)
    assert res.ok
    first = res.records[0].links["kod"]
    assert first.status is MatchStatus.DUPLICATE_MASTER and first.master_row is None
    assert res.records[1].links["kod"].status is MatchStatus.MATCHED
    assert "master_duplicate_keys" in _codes(res, Severity.WARNING)
    assert res.match_reports[0].master_duplicate_keys == 1
    # Ambiguous: the value hits different master records through different key fields.
    alt_master = _master(tmp_path, ["C-001", "C-002"], alt=["C-002", None], name="musteri_alt.xlsx")
    amb = _er_import(tmp_path, ["C-002"], alt_master)
    assert amb.records[0].links["kod"].status is MatchStatus.AMBIGUOUS


def test_29_no_fuzzy_matching(tmp_path):
    master = _master(tmp_path, ["C-001", "ACME KIRTASIYE"])
    near = ["c-001", "C-001 ", " C-001", "C001", "C-0O1", "C-001.0", "ACME KIRTASİYE", "Acme Kirtasiye"]
    res = _er_import(tmp_path, near, master)
    assert all(r.links["kod"].status is MatchStatus.UNMATCHED for r in res.records)
    src = "\n".join(p.read_text() for p in PKG.glob("*.py")).lower()
    for banned in ("difflib", "levenshtein", "rapidfuzz", "fuzzywuzzy", "jellyfish", "sentence_transformers",
                   "import numpy", "openai", "anthropic"):
        assert banned not in src, banned
    resolver = (PKG / "entity_resolution.py").read_text()
    for banned in (".lower(", ".upper(", ".casefold(", ".strip(", "normalize("):
        assert banned not in resolver, banned


def test_30_match_report_is_aggregate_only(tmp_path):
    secret_codes = ["ZQX-771-SECRET", "ZQX-772-SECRET", "ZQX-773-SECRET"]
    master = _master(tmp_path, secret_codes[:2])
    res = _er_import(tmp_path, secret_codes + [None], master)
    mr = res.match_reports[0]
    dumped = mr.model_dump(mode="json")
    assert set(dumped) == {"field", "role", "master", "total", "non_null", "counts", "rates", "master_size",
                           "master_duplicate_keys", "master_invalid_keys"}
    assert dumped["counts"] == {"matched": 2, "unmatched": 1, "ambiguous": 0, "null": 1, "duplicate_master": 0}
    assert dumped["rates"]["matched"] == round(2 / 3, 4) and dumped["rates"]["null"] == 0.25
    blob = json.dumps(res.summary(), ensure_ascii=False)
    assert "ZQX" not in blob and "SECRET" not in blob


# =============================================================================================
# Fail closed (31–37)
# =============================================================================================
def test_31_validation_error_prevents_normalized_success(tmp_path):
    p = _wb(tmp_path, [["Kod", "Adet"], ["K-1", E("#VALUE!")], ["K-2", 2]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"), FieldSpec("adet", T.INTEGER, header="Adet")]))
    assert (res.ok, res.status, res.failed_step) == (False, "failed", "validate")
    assert res.records == [] and res.match_reports == []
    assert not res.report.valid and res.summary()["record_count"] == 0


def test_32_warnings_proceed_only_where_allowed(tmp_path):
    p = _wb(tmp_path, [["Kod"], ["K-1"]])
    fields = [FieldSpec("kod", ID, header="Kod"), FieldSpec("opt", T.STRING, header="Yok", required=False)]
    res = run_import(p, _spec(fields))
    assert res.ok and res.report.warnings and res.records
    res = run_import(p, _spec(fields, allow_warnings=False))
    assert not res.ok and "warnings_not_allowed" in _codes(res, Severity.ERROR) and res.records == []


def test_33_invalid_required_field_fails(tmp_path):
    p = _wb(tmp_path, [["Kod", "Tutar"], ["K-1", "on iki"], ["K-2", 3.0]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"), FieldSpec("tutar", T.DECIMAL, header="Tutar")]))
    assert not res.ok
    issue = next(i for i in res.report.issues if i.code == "type_mismatch")
    assert issue.severity == Severity.ERROR and issue.field == "tutar" and issue.count == 1


def test_34_invalid_optional_field_is_unusable_but_import_proceeds(tmp_path):
    p = _wb(tmp_path, [["Kod", "Kapsam"], ["K-1", E("#DIV/0!")], ["K-2", 0.4], ["K-3", 0.9]])
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"),
                               FieldSpec("kapsam", T.DECIMAL, header="Kapsam", required=False)]))
    assert res.ok
    assert "field_unusable" in _codes(res, Severity.WARNING) and "excel_error" in _codes(res, Severity.WARNING)
    assert res.field_confidence["kapsam"] is Confidence.INVALID
    assert all(r.values["kapsam"].confidence is Confidence.INVALID and r.get("kapsam") is None for r in res.records)
    assert res.field_confidence["kod"] is Confidence.UNVERIFIED
    # Cell-level tolerance: only the error cell is invalid; the field stays usable.
    cell = run_import(p, _spec([FieldSpec("kod", ID, header="Kod"),
                                FieldSpec("kapsam", T.DECIMAL, header="Kapsam", required=False,
                                          tolerate_invalid_cells=True)]))
    assert cell.field_confidence["kapsam"] is Confidence.UNVERIFIED
    assert [r.get("kapsam") for r in cell.records] == [None, 0.4, 0.9]


def test_35_verification_failure_hides_staged_records(tmp_path):
    p = _wb(tmp_path, [["Kod", "Not"], ["K-1", None], ["K-2", None], ["K-3", "x"]])
    fields = [FieldSpec("kod", ID, header="Kod"), FieldSpec("not_", T.STRING, header="Not")]
    res = run_import(p, _spec(fields, verify=VerifySpec(min_rows=10)))
    assert (res.ok, res.failed_step) == (False, "verify")
    assert res.records == [] and not res.verification.ok
    assert [c.name for c in res.verification.checks if not c.ok] == ["min_rows"]
    assert "verification_failed" in _codes(res, Severity.ERROR)
    res = run_import(p, _spec(fields, verify=VerifySpec(max_null_rate={"not_": 0.5})))
    assert not res.ok and not next(c for c in res.verification.checks if c.name == "max_null_rate:not_").ok
    assert run_import(p, _spec(fields, verify=VerifySpec(min_rows=3, max_null_rate={"not_": 0.7}))).ok


def test_36_report_uses_sanitized_basename(tmp_path):
    deep = tmp_path / "Users" / "owner" / "Desktop" / "Gizli"
    deep.mkdir(parents=True)
    p = _wb(deep, [["Kod"], ["K-1"]])
    for res in (run_import(p, _spec([FieldSpec("kod", ID, header="Kod")])),
                run_import(p, _spec([FieldSpec("kod", ID, header="Yok")]))):
        assert res.report.file == "kaynak_2026.xlsx" == res.file
        blob = json.dumps(res.summary(), ensure_ascii=False)
        assert str(tmp_path) not in blob and "Desktop" not in blob and "Gizli" not in blob and "/" not in res.file


def test_37_serialized_report_contains_no_source_values(tmp_path):
    rows = [["Kod", "Unvan", "Tutar", "Adet", "Oran", "Tarih"],
            ["SNTL-0001", "Örnekköy Kırtasiye Sentinel AŞ", 987654.321, 4321, F("C2/D2", 228.5719), dt.date(2026, 9, 30)],
            ["SNTL-0002", "Denemeli Sentinel Ticaret", 123456.789, 1234, E("#DIV/0!"), dt.date(2026, 9, 1)],
            ["SNTL-0002", "Denemeli Sentinel Ticaret", "bozuk-değer", 77, 1.5, None]]
    p = _wb(tmp_path, rows)
    fields = [FieldSpec("kod", ID, header="Kod"), FieldSpec("unvan", T.STRING, header="Unvan"),
              FieldSpec("tutar", T.DECIMAL, header="Tutar", tolerate_invalid_cells=True),
              FieldSpec("adet", T.INTEGER, header="Adet"),
              FieldSpec("oran", T.DECIMAL, header="Oran", required=False),
              FieldSpec("tarih", T.DATE, header="Tarih")]
    rules = (RowRule("pozitif", ("tutar",), lambda v: v["tutar"] < 0),)
    res = run_import(p, _spec(fields, identity_fields=("kod",), rules=rules,
                              business_date=DateRule.column_max("tarih")))
    blob = json.dumps(res.summary(), ensure_ascii=False) + res.report.to_json()
    for leaked in ("SNTL", "Sentinel", "Örnekköy", "Denemeli", "bozuk", "987654", "123456", "4321", "1234",
                   "228.57", "1.5", "77"):
        assert leaked not in blob, leaked
    assert "2026-09-30" in blob  # date max is allowed (source freshness)


# =============================================================================================
# Rules and record keys (38–42)
# =============================================================================================
def _qty_book(tmp_path, bad_rows=()):
    rows = [["Belge", "Stok", "Miktar", "Teslim", "Kalan", "Vazgecilen"]]
    for i in range(6):
        miktar = 10 + i
        rows.append([f"S-{i // 2}", f"P-{i}", miktar + (1 if i in bad_rows else 0), miktar - 3, 2, 1])
    return _wb(tmp_path, rows)


def _qty_spec(severity=Severity.ERROR):
    fields = [FieldSpec("belge", ID, header="Belge"), FieldSpec("stok", ID, header="Stok")] + [
        FieldSpec(n.lower(), T.DECIMAL, header=n) for n in ("Miktar", "Teslim", "Kalan", "Vazgecilen")]
    rule = RowRule.sum_identity("miktar_kimligi", "miktar", ("teslim", "kalan", "vazgecilen"), severity=severity)
    return _spec(fields, identity_fields=("belge", "stok"), rules=(rule,))


def test_38_arithmetic_identity_rule_passes(tmp_path):
    res = run_import(_qty_book(tmp_path), _qty_spec())
    assert res.ok
    rr = res.rule_results[0]
    assert (rr.checked, rr.failed, rr.skipped, rr.row_indexes) == (6, 0, 0, [])
    assert "rule_failed" not in _codes(res)


def test_39_rule_fails_deterministically(tmp_path):
    runs = [run_import(_qty_book(tmp_path, bad_rows=(1, 4)), _qty_spec()) for _ in range(2)]
    for res in runs:
        assert not res.ok and res.failed_step == "validate"
        assert res.rule_results[0].failed == 2 and res.rule_results[0].row_indexes == [1, 4]
    assert runs[0].report.to_json() == runs[1].report.to_json()
    warn = run_import(_qty_book(tmp_path, bad_rows=(1, 4)), _qty_spec(Severity.WARNING))
    assert warn.ok and "rule_failed" in _codes(warn, Severity.WARNING)


def test_40_rule_failure_count_reported_without_values(tmp_path):
    res = run_import(_qty_book(tmp_path, bad_rows=(2,)), _qty_spec())
    issue = next(i for i in res.report.issues if i.code == "rule_failed")
    assert issue.count == 1 and issue.field == "rule:miktar_kimligi"
    assert issue.message == "Rule failed on 1 of 6 checked row(s)."
    summary = res.summary()
    assert summary["rules"] == [{"name": "miktar_kimligi", "checked": 6, "failed": 1, "skipped": 0, "exceptions": 0}]
    blob = json.dumps(summary)
    assert "row_indexes" not in blob and "P-2" not in blob and "S-1" not in blob
    # A rule that raises is counted, never crashes or leaks a traceback.
    boom = RowRule("patlayan", ("miktar",), lambda v: 1 / 0)
    res = run_import(_qty_book(tmp_path), _spec(_qty_spec().fields, rules=(boom,)))
    assert res.rule_results[0].exceptions == 6 and "ZeroDivisionError" not in res.report.to_json()


def test_41_record_keys_stable_across_runs(tmp_path):
    a = run_import(_qty_book(tmp_path), _qty_spec())
    b = run_import(_qty_book(tmp_path), _qty_spec())
    assert [r.key for r in a.records] == [r.key for r in b.records]
    assert all(r.key.startswith("synthetic:") for r in a.records)
    # Known value: guards against accidental changes to the key encoding.
    assert record_key("src", ["S-1", "P-1"], 1) == record_key("src", ("S-1", "P-1"), 1)
    expected = hashlib.sha256("s:src\x1fs:S-1\x1fs:P-1\x1fo:1".encode()).hexdigest()[:20]
    assert record_key("src", ["S-1", "P-1"], 1) == f"src:{expected}"
    # No Python hash randomization: identical keys from processes with different hash seeds.
    code = ("from services.importers import record_key;"
            "print(record_key('src', ['S-1', 'P-1', None, 5], 2))")
    outs = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}).stdout for seed in ("1", "2", "999")}
    assert len(outs) == 1 and outs.pop().strip().startswith("src:")
    assert record_key("s", ["1"]) != record_key("s", [1]) != record_key("s", [None]) != record_key("s", [""])


def test_42_duplicate_logical_keys_get_distinct_deterministic_keys(tmp_path):
    rows = [["Belge", "Stok", "Adet"], ["S-1", "P-1", 1], ["S-1", "P-2", 2], ["S-1", "P-1", 3], ["S-1", "P-1", 4]]
    p = _wb(tmp_path, rows)
    spec = _spec([FieldSpec("belge", ID, header="Belge"), FieldSpec("stok", ID, header="Stok"),
                  FieldSpec("adet", T.INTEGER, header="Adet")], identity_fields=("belge", "stok"))
    a, b = run_import(p, spec), run_import(p, spec)
    keys = [r.key for r in a.records]
    assert len(set(keys)) == 4 and keys == [r.key for r in b.records]
    assert keys[0] == record_key("synthetic", ["S-1", "P-1"], 1)
    assert keys[2] == record_key("synthetic", ["S-1", "P-1"], 2)
    assert keys[3] == record_key("synthetic", ["S-1", "P-1"], 3)
    assert keys_with_occurrence("x", [("a",), ("a",)]) == [record_key("x", ["a"], 1), record_key("x", ["a"], 2)]
    assert next(c for c in a.verification.checks if c.name == "keys_unique").ok


# =============================================================================================
# Business date, confidence vs validity, safety and isolation
# =============================================================================================
def test_business_date_strategies_and_provenance(tmp_path):
    rows = [["Kod", "Tarih", "Hesap"],
            ["K-1", dt.date(2026, 9, 30), F("B2", dt.date(2026, 9, 30))],
            ["K-2", dt.date(2026, 10, 5), F("B3", dt.date(2026, 10, 5))]]
    p = _wb(tmp_path, rows)
    base = [FieldSpec("kod", ID, header="Kod"), FieldSpec("tarih", T.DATE, header="Tarih"),
            FieldSpec("hesap", T.DATE, header="Hesap")]
    res = run_import(p, _spec(base, business_date=DateRule.column_max("tarih")))
    assert (res.business_date.value, res.business_date.origin) == (dt.date(2026, 10, 5), DateOrigin.CONTENT)
    res = run_import(p, _spec(base, business_date=DateRule.column_max("hesap")))
    assert res.business_date.origin is DateOrigin.CACHED_FORMULA
    res = run_import(p, _spec(base, business_date=DateRule.snapshot_cell("B2")))
    assert (res.business_date.value, res.business_date.origin) == (dt.date(2026, 9, 30), DateOrigin.LITERAL)
    res = run_import(p, _spec(base, business_date=DateRule.snapshot_cell("C3")))
    assert res.business_date.origin is DateOrigin.CACHED_FORMULA
    assert not run_import(p, _spec(base, business_date=DateRule.snapshot_cell("A2"))).ok  # not a date
    # Declared: required at import time.
    missing = run_import(p, _spec(base, business_date=DateRule.declared()))
    assert not missing.ok and "business_date_missing" in _codes(missing)
    decl = run_import(p, _spec(base, business_date=DateRule.declared()), declared_date=dt.date(2026, 10, 1))
    assert (decl.business_date.value, decl.business_date.origin) == (dt.date(2026, 10, 1), DateOrigin.DECLARED)
    # Filename only when the spec opts in.
    fn = run_import(p, _spec(base, business_date=DateRule.from_filename(r"_(?P<year>\d{4})(?P<month>\d{2})?")))
    assert not fn.ok  # "kaynak_2026" has no month
    p2 = _wb(tmp_path, rows, name="kaynak_202609.xlsx")
    fn = run_import(p2, _spec(base, business_date=DateRule.from_filename(r"_(?P<year>\d{4})(?P<month>\d{2})")))
    assert (fn.business_date.value, fn.business_date.origin, fn.business_date.granularity) == \
        (dt.date(2026, 9, 1), DateOrigin.FILENAME, "month")
    # Never the file modification time.
    no_rule = run_import(p, _spec(base))
    os.utime(p, (0, 0))
    again = run_import(p, _spec(base))
    assert no_rule.business_date.value is None and again.business_date.value is None
    assert "business_date_none" in _codes(again, Severity.INFO)


def test_business_date_from_bucket_headers(tmp_path):
    rows = [["Cari", "Ağustos-2026", "Eylül-2026", "EKİM-2026"], ["C-1", 1.0, 2.0, 3.0]]
    p = _wb(tmp_path, rows)
    spec = _spec([FieldSpec("cari", ID, header="Cari")], business_date=DateRule.bucket_headers("B1:D1"))
    res = run_import(p, spec)
    assert res.ok and res.business_date.value == dt.date(2026, 10, 1)
    assert (res.business_date.origin, res.business_date.granularity) == (DateOrigin.CONTENT, "month")
    drift = _wb(tmp_path, [["Cari", "Ağustos-2026", "Toplam", "Ekim-2026"], ["C-1", 1, 2, 3]], name="kaynak_x.xlsx")
    assert "business_date_invalid" in _codes(run_import(drift, spec), Severity.ERROR)


def test_parse_success_never_upgrades_confidence(tmp_path):
    p = _wb(tmp_path, [["Kod", "Haftalik", "Ziyaret"], ["K-1", 12, 3], ["K-2", 9, None]])
    spec = _spec([FieldSpec("kod", ID, header="Kod", confidence=Confidence.TRUSTED),
                  FieldSpec("haftalik", T.INTEGER, header="Haftalik", confidence=Confidence.STALE),
                  FieldSpec("ziyaret", T.INTEGER, header="Ziyaret", confidence=Confidence.INCOMPLETE)])
    res = run_import(p, spec)
    assert res.ok
    assert all(r.values["haftalik"].confidence is Confidence.STALE for r in res.records)
    assert res.records[0].values["haftalik"].value == 12  # perfectly parseable, still stale
    assert res.records[1].values["ziyaret"].confidence is Confidence.INCOMPLETE
    assert res.field_confidence == {"kod": Confidence.TRUSTED, "haftalik": Confidence.STALE,
                                    "ziyaret": Confidence.INCOMPLETE}
    assert res.confidence is Confidence.STALE
    assert next(c for c in res.verification.checks if c.name == "confidence_never_upgraded").ok


def test_identifier_roles_and_attribute_provenance(tmp_path):
    master = _master(tmp_path, ["C-001", "C-002"])
    p = _wb(tmp_path, [["Pro", "Musteri"], ["C-001", "C-002"], ["C-002", "C-404"]])
    attrs = (AttributeSource("kapali", master="customer_master", master_field="CARİ FİRMA", master_key="KODU",
                             via_role=IdentifierRole.ORDER_CUSTOMER, note="DQ-5: from the master, not cached lookups"),)
    spec = _spec([FieldSpec("pro", ID, header="Pro", role=IdentifierRole.ORDER_CUSTOMER, master="customer_master"),
                  FieldSpec("musteri", ID, header="Musteri", role=IdentifierRole.INVOICE_ACCOUNT,
                            master="customer_master")], attribute_sources=attrs)
    res = run_import(p, spec, masters={"customer_master": master})
    roles = {m.field: (m.role, m.counts[MatchStatus.MATCHED]) for m in res.match_reports}
    assert roles == {"pro": (IdentifierRole.ORDER_CUSTOMER, 2), "musteri": (IdentifierRole.INVOICE_ACCOUNT, 1)}
    assert res.summary()["attribute_sources"][0]["via_role"] == "order_customer"
    # Without a master the identifiers are not resolved (reported, not guessed).
    res = run_import(p, spec)
    assert res.ok and res.match_reports == [] and "er_master_not_supplied" in _codes(res, Severity.INFO)
    with pytest.raises(ValueError):
        MasterIndex.from_result(run_import(p, _spec([FieldSpec("x", ID, header="Yok")])), ["x"])


def test_spec_errors_and_filename_rules(tmp_path):
    with pytest.raises(SpecError):
        _spec([FieldSpec("a", ID, header="A"), FieldSpec("a", ID, header="B")]).check()
    with pytest.raises(SpecError):
        _spec([FieldSpec("a", ID, header="A")], identity_fields=("nope",)).check()
    with pytest.raises(SpecError):
        _spec([FieldSpec("a", T.STRING, header="A", pattern=r"\d+")]).check()
    with pytest.raises(SpecError):
        _spec([FieldSpec("a", ID, header="A", master="m")]).check()  # master without a role
    p = _wb(tmp_path, [["Kod"], ["K-1"]], name="baska_dosya.xlsx")
    res = run_import(p, _spec([FieldSpec("kod", ID, header="Kod")]))
    assert not res.ok and "filename_mismatch" in _codes(res)
    # macOS stores names decomposed (NFD); patterns are compared after NFC normalization.
    nfd = unicodedata.normalize("NFD", "kaynak_ŞUBAT.xlsx")
    p = _wb(tmp_path, [["Kod"], ["K-1"]], name=nfd)
    assert run_import(p, _spec([FieldSpec("kod", ID, header="Kod")], filename_patterns=("kaynak_ŞUBAT.xlsx",))).ok
    bad = tmp_path / "kaynak_bozuk.xlsx"
    bad.write_bytes(b"not a workbook")
    assert "unreadable_workbook" in _codes(run_import(bad, _spec([FieldSpec("kod", ID, header="Kod")])))


def test_workbook_is_never_modified(tmp_path):
    p = _wb(tmp_path, [["Kod", "Oran"], ["K-1", F("1/0", E("#DIV/0!"))], ["K-2", F("B9", MISSING)]],
            external_links=["file:///C:/x/Other.xlsx"])
    before = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    run_import(p, _spec([FieldSpec("kod", ID, header="Kod"), FieldSpec("oran", T.DECIMAL, header="Oran")]))
    assert (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) == before
    assert sorted(x.name for x in tmp_path.iterdir() if x.suffix != ".db") == ["kaynak_2026.xlsx"]  # no side files
    src = "\n".join(f.read_text() for f in PKG.glob("*.py"))
    assert ".save(" not in src and "write_bytes" not in src and "open(" not in src.replace("zipfile.ZipFile(", "")


def test_framework_imports_no_ai_and_m5_does_not_import_framework():
    src = "\n".join(f.read_text() for f in PKG.glob("*.py"))
    for banned in ("import openai", "from openai", "anthropic", "services.ai", "services.agent", "requests",
                   "httpx", "urllib.request"):
        assert banned not in src, banned
    code = ("import sys, services.importers;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
    # M5 stays on its own sources: nothing in the live path imports the M6 framework yet.
    for live in ("app.py", "dashboard_builder.py", "services/refresh.py", "services/context.py",
                 "services/action_sources.py"):
        assert "importers" not in (ROOT / live).read_text(), live
