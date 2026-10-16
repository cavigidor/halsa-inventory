"""M6-2 order activity/source normalization tests.

All workbooks are synthetic and generated at runtime. Customer codes, names, SKUs and event stems are
invented (partner-like stems such as ÖRNEKBAYİ are fictional). Tests 01–55 follow the M6-2 acceptance list.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from services.importers import Confidence, IdentifierRole, MatchStatus, MasterIndex, record_key
from services.importers import sales_activity as SA
from services.importers.sales_activity import (
    SALES_LINES_SPEC,
    LabelError,
    SourceSelectionError,
    SourceType,
    canonical_customer,
    find_sales_workbook,
    import_order_sources,
    parse_label,
)
from tests.fixtures.xlsx_synth import E, F, write_xlsx

ROOT = Path(__file__).resolve().parents[1]
D = dt.date
SALES_NAME = "2020-5 ekim satışlar.xlsx"
HEADERS = ["sip_evrakno_seri", "sip_evrakno_sira", "Musterikod", "Musteri_ismi", "Pro_kodu", "ProjeIsmi",
           "sto_kod", "StokIsmi", "Srmmerk", "Tarih", "miktar", "Nettutar", "Temsilci", "Açık / Kapalı", "Sektör"]
NAMES = {"100.01.0001": "Kurgu Kırtasiye Ltd", "100.01.0002": "Hayali Ofis AŞ", "100.01.0003": "Uydurma Kaşe",
         "100.01.0004": "Örnek Bayi Ticaret"}
PARTNER_STEMS = ("ÖRNEKBAYİ", "DENEMEKIRTASİYE")


def L(seri, sira, label, date, pro="100.01.0001", mus=None, sku="P-1", net=1234.5):
    mus = pro if mus is None else mus
    return [seri, sira, mus, NAMES.get(mus, "Kurgu Hesap"), pro, NAMES.get(pro, "Kurgu Proje"), sku,
            f"Ürün {sku}", label, date, 2, net,
            F('IFNA(XLOOKUP(C2,liste!A:A,liste!G:G),"")', "Kurgu Temsilci"),
            F('IFNA(XLOOKUP(C2,liste!A:A,liste!F:F),"")', "AÇIK"), F('IFNA(XLOOKUP(C2,liste!A:A,liste!D:D),"")', "Kırtasiye")]


def BASE_LINES():
    return [
        L("AA", 1001, "GENEL", D(2026, 1, 5), sku="P-1"),
        L("AA", 1001, "GENEL", D(2026, 1, 5), sku="P-2"),
        L("AA", 1002, "2026ANKARAFUAR", D(2026, 1, 20), pro="100.01.0002", sku="P-1"),
        L("AA", 1002, "2026ANKARAFUAR", D(2026, 1, 20), pro="100.01.0002", sku="P-2"),
        L("AA", 1002, "2026ANKARAFUAR", D(2026, 1, 20), pro="100.01.0002", sku="P-3"),
        L("B1A", 1003, "2025DENEMEKIRTASİYEETKİNLİĞİ", D(2024, 12, 15), pro="100.01.0003", sku="P-4"),  # +1
        L("AA", 1004, "2025ÖRNEKBAYİ ETKİNLİK", D(2025, 3, 1), pro="100.01.0004", sku="P-1"),
        L("AA", 1005, "2024VANSAHAÇALIŞMASI", D(2024, 6, 1), sku="P-5"),
        L("AA", 1006, "2024VANSAHA", D(2024, 7, 1), sku="P-5"),
        L("AA", 1007, "2026ANKARAFUAR2", D(2026, 2, 2), pro="100.01.0002", sku="P-1"),
        L("AA", 1008, "2023İZMİRFUAR", D(2024, 1, 10), pro="100.01.0003", sku="P-2"),            # -1
        L("ABC", 7, "GENEL", D(2026, 9, 30), pro="", mus="100.01.0004", sku="P-6"),              # D-003 fallback
    ]


def MASTER_ROWS(codes=("100.01.0001", "100.01.0002", "100.01.0003", "100.01.0004")):
    return [["KODU", "ÜNVANI 1", "MUH.KODU 1", "SEKTÖR", "ANA CARİ", "CARİ FİRMA", "TEMSİLCİ"]] + [
        [c, NAMES.get(c, "Kurgu"), c, "Kırtasiye", None, "AÇIK", "Kurgu Temsilci"] for c in codes]


def book(tmp_path, lines=None, master=None, name=SALES_NAME, headers=HEADERS):
    rows = [headers] + (BASE_LINES() if lines is None else lines)
    return write_xlsx(tmp_path / name, {"2020-2026 satışlar": rows, "liste": master or MASTER_ROWS()})


def run(tmp_path, lines=None, **kw):
    return import_order_sources(book(tmp_path, lines, **kw))


def orders_by_number(res):
    return {o.number: o for o in res.orders}


# =============================================================================================
# Parser (1–17)
# =============================================================================================
def test_01_genel():
    p = parse_label("GENEL")
    assert (p.source_type, p.edition_year, p.stem, p.sequence, p.event_id) == (SourceType.GENERAL, None, None, None, None)
    assert not p.is_event


def test_02_fuar():
    p = parse_label("2026ANKARAFUAR")
    assert (p.source_type, p.edition_year, p.stem, p.sequence) == (SourceType.FUAR, 2026, "ANKARA", None)


def test_03_etkinlik():
    p = parse_label("2025DENEMEKIRTASİYEETKİNLİK")
    assert (p.source_type, p.edition_year, p.stem) == (SourceType.ETKINLIK, 2025, "DENEMEKIRTASİYE")


def test_04_etkinligi_variant():
    p = parse_label("2025DENEMEKIRTASİYEETKİNLİĞİ")
    assert (p.source_type, p.stem) == (SourceType.ETKINLIK, "DENEMEKIRTASİYE")


def test_05_saha():
    p = parse_label("2024VANSAHA")
    assert (p.source_type, p.edition_year, p.stem) == (SourceType.SAHA, 2024, "VAN")


def test_06_sahacalismasi_variant():
    p = parse_label("2024VANSAHAÇALIŞMASI")
    assert (p.source_type, p.stem) == (SourceType.SAHA, "VAN")


def test_07_numeric_sequence_suffix():
    assert parse_label("2026ANKARAFUAR2").sequence == 2
    assert parse_label("2026ANKARAFUAR12").sequence == 12
    assert parse_label("2026ANKARAFUAR2").stem == "ANKARA"
    with pytest.raises(LabelError):
        parse_label("2026ANKARAFUAR02")   # leading zero is not a sequence


def test_08_turkish_characters_in_stem():
    for label, stem in (("2024DİYARBAKIRFUAR", "DİYARBAKIR"), ("2023İZMİRFUAR", "İZMİR"),
                        ("2025ESKİŞEHİRFUAR", "ESKİŞEHİR"), ("2025ÇANAKKALEĞÜFUAR", "ÇANAKKALEĞÜ")):
        assert parse_label(label).stem == stem
    # A decomposed (NFD) label still parses; the raw value is kept as written.
    import unicodedata
    nfd = unicodedata.normalize("NFD", "2025ÖRNEKBAYİETKİNLİĞİ")
    p = parse_label(nfd)
    assert p.source_type is SourceType.ETKINLIK and p.raw == nfd and p.stem == "ÖRNEKBAYİ"


def test_09_interior_whitespace_preserved_outer_whitespace_fails():
    p = parse_label("2025ÖRNEKBAYİ ETKİNLİK")
    assert p.stem == "ÖRNEKBAYİ " and p.raw == "2025ÖRNEKBAYİ ETKİNLİK"
    assert parse_label("2025KURGU OFİS FUAR").stem == "KURGU OFİS "
    for bad in (" 2026ANKARAFUAR", "2026ANKARAFUAR ", "GENEL ", "\tGENEL"):
        with pytest.raises(LabelError) as exc:
            parse_label(bad)
        assert exc.value.code == "label_whitespace"


def test_10_future_edition_year():
    assert parse_label("2031ANKARAFUAR").edition_year == 2031
    assert parse_label("2099ANKARAFUAR").edition_year == 2099
    assert SA.MIN_EDITION_YEAR == 2000 and SA.MAX_EDITION_YEAR == 2099
    with pytest.raises(LabelError) as exc:
        parse_label("1899ANKARAFUAR")
    assert exc.value.code == "label_year_out_of_bounds"


def test_11_missing_year_fails():
    for bad in ("ANKARAFUAR", "26ANKARAFUAR", "202ANKARAFUAR", "20245ANKARAFUAR"):
        with pytest.raises(LabelError) as exc:
            parse_label(bad)
        assert exc.value.code == "label_no_year", bad


def test_12_missing_stem_fails():
    for bad in ("2026FUAR", "2026 FUAR", "2026ETKİNLİĞİ", "2026SAHA2"):
        with pytest.raises(LabelError) as exc:
            parse_label(bad)
        assert exc.value.code == "label_no_stem", bad


def test_13_unknown_type_fails():
    for bad in ("2026ANKARAKONGRE", "2026ANKARAfuar", "2026ANKARAFUARI", "2026ANKARAETKINLIK", "2026ANKARA"):
        with pytest.raises(LabelError) as exc:
            parse_label(bad)
        assert exc.value.code == "label_unknown_type", bad


def test_14_malformed_label_fails():
    for bad in ("", "genel", "Genel", "-", "2026", None, 2026, 12.5):
        with pytest.raises(LabelError):
            parse_label(bad)


def test_15_raw_label_preserved():
    for raw in ("2026ANKARAFUAR2", "2024VANSAHAÇALIŞMASI", "GENEL"):
        assert parse_label(raw).raw == raw


def test_16_type_variants_normalize():
    assert {parse_label(x).source_type for x in ("2025XETKİNLİK", "2025XETKİNLİĞİ")} == {SourceType.ETKINLIK}
    assert {parse_label(x).source_type for x in ("2025XSAHA", "2025XSAHAÇALIŞMASI")} == {SourceType.SAHA}
    assert [t.value for t in SourceType] == ["general", "fuar", "etkinlik", "saha"]
    # A stem that itself ends like a type keeps the longest right-hand token as the type.
    assert parse_label("2025SAHAFUAR").stem == "SAHA" and parse_label("2025SAHAFUAR").source_type is SourceType.FUAR


def test_17_different_stems_never_collapse():
    labels = ("2026ANKARAFUAR", "2026ANKARA FUAR", "2026ankaraFUAR", "2026KONYAFUAR", "2026KONYAYAZFUAR",
              "2025ANKARAFUAR", "2026ANKARAFUAR2", "2026ANKARAETKİNLİK")
    ids = [parse_label(x).event_id for x in labels]
    assert len(set(ids)) == len(labels)
    # Variants of the type token are the same event; only the spelling of the type differs.
    assert parse_label("2025XETKİNLİK").event_id == parse_label("2025XETKİNLİĞİ").event_id


# =============================================================================================
# Order grain (18–24)
# =============================================================================================
def test_18_multiple_lines_one_order(tmp_path):
    res = run(tmp_path)
    assert res.ok, res.report.to_json()
    o = orders_by_number(res)[1002]
    assert len(o.line_keys) == 3 and o.source_type is SourceType.FUAR
    assert res.counts.lines == 12 and res.counts.orders == 9
    assert res.counts.orders_by_type["fuar"] == 3  # 3 orders, not 5 lines


def test_19_one_label_across_lines_passes(tmp_path):
    res = run(tmp_path)
    assert res.counts.label_conflict_orders == 0
    assert all(len({res.lines[i].get("source_label") for i in range(len(res.lines))
                    if res.lines[i].key in o.line_keys}) == 1 for o in res.orders)


def test_20_conflicting_labels_in_one_order_fail(tmp_path):
    lines = BASE_LINES()
    lines[1][8] = "2026ANKARAFUAR"   # order 1001 now has GENEL + FUAR
    res = run(tmp_path, lines)
    assert not res.ok and res.failed_step == "attribute" and res.orders == [] and res.lines == []
    issue = next(i for i in res.report.issues if i.code == "source_label_conflict")
    assert issue.severity.value == "error" and issue.count == 1


def test_21_stable_order_key(tmp_path):
    a, b = run(tmp_path), run(tmp_path)
    assert [o.order_key for o in a.orders] == [o.order_key for o in b.orders]
    o = orders_by_number(a)[1002]
    assert o.order_key == record_key("sales_order", ["AA", 1002])
    code = "from services.importers import record_key; print(record_key('sales_order', ['AA', 1002]))"
    outs = {subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           env={"PYTHONHASHSEED": s, "PATH": "/usr/bin:/bin"}).stdout for s in ("1", "77")}
    assert outs == {o.order_key + "\n"}


def test_22_stable_line_keys(tmp_path):
    a, b = run(tmp_path), run(tmp_path)
    assert [r.key for r in a.lines] == [r.key for r in b.lines]
    assert a.lines[0].key == record_key("sales_activity", ["AA", 1001, "P-1"], 1)


def test_23_repeated_document_sku_lines_get_distinct_keys(tmp_path):
    lines = BASE_LINES() + [L("AA", 1001, "GENEL", D(2026, 1, 5), sku="P-1")]  # repeats AA-1001 × P-1
    res = run(tmp_path, lines)
    o = orders_by_number(res)[1001]
    assert len(o.line_keys) == 3 and len(set(o.line_keys)) == 3
    assert o.line_keys[2] == record_key("sales_activity", ["AA", 1001, "P-1"], 2)


def test_24_order_key_independent_of_source_metadata(tmp_path):
    before = orders_by_number(run(tmp_path))[1001].order_key
    lines = BASE_LINES()
    lines[0][8] = lines[1][8] = "2026KONYAFUAR"
    after_res = run(tmp_path, lines)
    after = orders_by_number(after_res)[1001]
    assert after.order_key == before and after.source_type is SourceType.FUAR


# =============================================================================================
# Edition year vs order year (25–29)
# =============================================================================================
def test_25_edition_year_equals_order_year(tmp_path):
    o = orders_by_number(run(tmp_path))[1002]
    assert (o.edition_year, o.order_date.year, o.edition_year_offset) == (2026, 2026, 0)


def test_26_edition_year_one_ahead_accepted(tmp_path):
    o = orders_by_number(run(tmp_path))[1003]
    assert (o.edition_year, o.order_date, o.edition_year_offset) == (2025, D(2024, 12, 15), 1)


def test_27_edition_year_one_behind_accepted(tmp_path):
    o = orders_by_number(run(tmp_path))[1008]
    assert (o.edition_year, o.order_date, o.edition_year_offset) == (2023, D(2024, 1, 10), -1)


def test_28_year_mismatch_is_info_only_and_never_corrected(tmp_path):
    res = run(tmp_path)
    assert res.ok
    issue = next(i for i in res.report.issues if i.code == "edition_year_differs")
    assert issue.severity.value == "info" and issue.count == 2
    assert res.counts.edition_year_differs == 2 and res.counts.edition_year_offsets == {"+1": 1, "-1": 1}
    o = orders_by_number(res)[1003]
    assert o.edition_year == 2025 and o.order_date.year == 2024  # both kept as recorded


def test_29_business_date_from_tarih_not_edition_year(tmp_path):
    lines = BASE_LINES() + [L("AA", 1999, "2031ANKARAFUAR", D(2026, 3, 3))]
    res = run(tmp_path, lines)
    assert res.ok
    assert res.counts.business_date == D(2026, 9, 30)   # max(Tarih)
    assert res.lines_result.business_date.origin.value == "content"
    assert res.counts.edition_year_max == 2031


# =============================================================================================
# Identity (30–37)
# =============================================================================================
def test_30_pro_kodu_is_order_customer_role():
    f = SALES_LINES_SPEC.field("order_customer")
    assert f.header.name == "Pro_kodu" and f.role is IdentifierRole.ORDER_CUSTOMER


def test_31_musterikod_is_invoice_account_role():
    f = SALES_LINES_SPEC.field("invoice_account")
    assert f.header.name == "Musterikod" and f.role is IdentifierRole.INVOICE_ACCOUNT


def test_32_roles_never_equated(tmp_path):
    lines = BASE_LINES() + [L("AA", 2001, "GENEL", D(2026, 4, 1), pro="100.01.0001", mus="100.01.0002")]
    res = run(tmp_path, lines)
    o = orders_by_number(res)[2001]
    assert (o.order_customer, o.invoice_account, o.customer, o.customer_fallback) == \
        ("100.01.0001", "100.01.0002", "100.01.0001", False)
    roles = {m.field: m.role for m in res.match_reports}
    assert roles["order_customer"] is IdentifierRole.ORDER_CUSTOMER
    assert roles["invoice_account"] is IdentifierRole.INVOICE_ACCOUNT


def test_33_canonical_fallback_exactly_d003(tmp_path):
    assert canonical_customer("100.01.0001", "100.01.0002") == ("100.01.0001", False)
    assert canonical_customer(None, "100.01.0002") == ("100.01.0002", True)
    assert canonical_customer("   ", "100.01.0002") == ("100.01.0002", True)   # empty after strip, like M5
    assert canonical_customer("100.01.0001 ", "100.01.0002") == ("100.01.0001 ", False)  # used as written
    assert canonical_customer(None, None) == (None, False)
    o = orders_by_number(run(tmp_path))[7]
    assert (o.order_customer, o.customer, o.customer_fallback) == (None, "100.01.0004", True)


def test_34_exact_master_match(tmp_path):
    res = run(tmp_path)
    assert all(o.customer_link.status is MatchStatus.MATCHED for o in res.orders)
    order_report = next(m for m in res.match_reports if m.field == "customer")
    assert order_report.counts[MatchStatus.MATCHED] == 9 and order_report.master == "sales_customer_master"


def test_35_unmatched_customer_only_in_aggregate(tmp_path):
    lines = BASE_LINES() + [L("AA", 3001, "GENEL", D(2026, 5, 5), pro="999.99.0001", mus="100.01.0001")]
    res = run(tmp_path, lines)
    assert res.ok
    assert orders_by_number(res)[3001].customer_link.status is MatchStatus.UNMATCHED
    order_report = next(m for m in res.match_reports if m.field == "customer")
    assert order_report.counts[MatchStatus.UNMATCHED] == 1
    assert "999.99.0001" not in json.dumps(res.summary(), ensure_ascii=False)


def test_36_duplicate_master_fail_safe(tmp_path):
    master = MASTER_ROWS() + [["100.01.0002", "Kopya", "100.01.0002", None, None, "AÇIK", None]]
    res = run(tmp_path, master=master)
    assert res.ok
    for n in (1002, 1007):
        assert orders_by_number(res)[n].customer_link.status is MatchStatus.DUPLICATE_MASTER
        assert orders_by_number(res)[n].customer_link.master_row is None
    assert "master_duplicate_keys" in res.report.codes()


def test_37_no_fuzzy_matching(tmp_path):
    near = ["100.01.0001 ", "100.1.0001", "100010001", "100.01.0O01"]
    lines = BASE_LINES() + [L("AA", 4000 + i, "GENEL", D(2026, 6, 1), pro=c, mus="100.01.0001")
                            for i, c in enumerate(near)]
    res = run(tmp_path, lines)
    assert all(orders_by_number(res)[4000 + i].customer_link.status is MatchStatus.UNMATCHED for i in range(4))
    src = (ROOT / "services/importers/sales_activity.py").read_text().lower()
    for banned in ("difflib", "levenshtein", "fuzz", "casefold", ".lower(", ".upper("):
        assert banned not in src, banned


# =============================================================================================
# Privacy (38–43)
# =============================================================================================
def _blob(res) -> str:
    return json.dumps(res.summary(), ensure_ascii=False, default=str) + res.report.to_json()


def test_38_summary_has_no_raw_labels(tmp_path):
    res = run(tmp_path)
    blob = _blob(res)
    for o in res.orders:
        if o.is_event:
            assert o.raw_label not in blob
    assert next(c for c in res.verification.checks if c.name == "summary_has_no_labels").ok


def test_39_summary_has_no_event_stems(tmp_path):
    res = run(tmp_path)
    blob = _blob(res)
    for stem in ("ANKARA", "VAN", "İZMİR", *PARTNER_STEMS):
        assert stem not in blob, stem


def test_40_summary_has_no_customer_codes(tmp_path):
    blob = _blob(run(tmp_path))
    assert "100.01" not in blob and "P-1" not in blob


def test_41_summary_has_no_customer_names(tmp_path):
    blob = _blob(run(tmp_path))
    for name in (*NAMES.values(), "Kurgu Temsilci", "Kırtasiye"):
        assert name not in blob, name


def test_42_absolute_path_absent(tmp_path):
    deep = tmp_path / "Users" / "owner" / "Gizli"
    deep.mkdir(parents=True)
    res = import_order_sources(book(deep))
    blob = _blob(res)
    assert res.file == SALES_NAME and str(tmp_path) not in blob and "Gizli" not in blob


def test_43_partner_like_names_do_not_leak_in_errors(tmp_path):
    lines = BASE_LINES() + [L("AA", 5001, "2026ÖRNEKBAYİKONGRE", D(2026, 1, 1)),
                            L("AA", 5002, "DENEMEKIRTASİYEFUAR", D(2026, 1, 1))]
    res = run(tmp_path, lines)
    assert not res.ok and res.failed_step == "attribute"
    issue = next(i for i in res.report.issues if i.code == "source_label_unparsed")
    assert issue.count == 2 and res.counts.unparsed_by_code == {"label_no_year": 1, "label_unknown_type": 1}
    blob = _blob(res)
    for leaked in (*PARTNER_STEMS, "KONGRE", "2026ÖRNEKBAYİ"):
        assert leaked not in blob, leaked


# =============================================================================================
# Source counts (44–49), at order grain
# =============================================================================================
def test_44_general_and_event_order_counts(tmp_path):
    c = run(tmp_path).counts
    assert (c.orders, c.general_orders, c.event_orders) == (9, 2, 7)


def test_45_type_counts(tmp_path):
    c = run(tmp_path).counts
    assert c.orders_by_type == {"general": 2, "fuar": 3, "etkinlik": 2, "saha": 2}
    assert sum(c.orders_by_type.values()) == c.orders
    assert next(ch for ch in run(tmp_path).verification.checks if ch.name == "type_counts_reconcile").ok


def test_46_distinct_label_count(tmp_path):
    assert run(tmp_path).counts.distinct_labels == 8  # GENEL + 7 event labels


def test_47_distinct_event_count(tmp_path):
    c = run(tmp_path).counts
    # ANKARA (2026 FUAR, 2026 FUAR seq 2), DENEMEKIRTASİYE, "ÖRNEKBAYİ ", VAN (SAHA ×2 → one event), İZMİR
    assert c.distinct_event_stems == 5
    assert c.distinct_events == 6  # 2026 ANKARA, 2026 ANKARA #2, 2025 DENEME…, 2025 ÖRNEK…, 2024 VAN SAHA, 2023 İZMİR
    assert c.orders_with_sequence == 1
    assert (c.edition_year_min, c.edition_year_max, c.edition_years) == (2023, 2026, 4)


def test_48_parse_failure_count(tmp_path):
    lines = BASE_LINES() + [L("AA", 6001, "2026ANKARAKONGRE", D(2026, 1, 1)),
                            L("AA", 6002, "2026ANKARAKONGRE", D(2026, 1, 1)),
                            L("AA", 6003, "2026FUAR", D(2026, 1, 1))]
    res = run(tmp_path, lines)
    assert (res.counts.unparsed_orders, res.counts.unparsed_labels) == (3, 2)
    assert res.counts.unparsed_by_code == {"label_no_stem": 1, "label_unknown_type": 2}
    assert not res.ok and res.orders == []


def test_49_edition_year_mismatch_count(tmp_path):
    lines = BASE_LINES() + [L("AA", 7001, "2020ANKARAFUAR", D(2026, 1, 1))]
    c = run(tmp_path, lines).counts
    assert c.edition_year_differs == 3 and c.edition_year_offsets == {"+1": 1, "-1": 1, "other": 1}


# =============================================================================================
# Framework use (50–55)
# =============================================================================================
def test_50_uses_run_import_and_source_spec(tmp_path, monkeypatch):
    calls = []
    real = SA.run_import

    def spy(path, spec, **kw):
        calls.append(spec.key)
        return real(path, spec, **kw)

    monkeypatch.setattr(SA, "run_import", spy)
    res = import_order_sources(book(tmp_path))
    assert res.ok and calls == ["sales_customer_master", "sales_activity"]
    SALES_LINES_SPEC.check()
    SA.CUSTOMER_MASTER_SPEC.check()
    assert [f.header.name for f in SALES_LINES_SPEC.fields] == [
        "sip_evrakno_seri", "sip_evrakno_sira", "Pro_kodu", "Musterikod", "sto_kod", "Srmmerk", "Tarih"]


def test_51_excel_errors_and_layout_handled_by_framework(tmp_path):
    lines = BASE_LINES()
    lines[2][8] = E("#N/A")
    res = run(tmp_path, lines)
    assert not res.ok and res.failed_step == "validate" and "excel_error" in res.report.codes()
    lines = BASE_LINES()
    lines[3][8] = None                              # blank source label → non-nullable
    res = run(tmp_path, lines)
    assert not res.ok and "null_values" in res.report.codes()
    lines = BASE_LINES()
    lines[0][1] = "1001"                             # order number as text → malformed identity
    res = run(tmp_path, lines)
    assert not res.ok and "type_mismatch" in res.report.codes()
    lines = BASE_LINES()
    lines[0][8] = F('"GENEL"', "GENEL")              # a formula in a raw ERP column
    assert "formula_forbidden" in run(tmp_path, lines).report.codes()
    headers = [h if h != "Srmmerk" else "Srmmerk " for h in HEADERS]
    res = run(tmp_path, headers=headers)             # missing Srmmerk (exact header)
    assert not res.ok and "header_missing" in res.report.codes()
    # Non-imported columns do not matter: the lookups are formulas, amounts are not read.
    assert run(tmp_path).ok


def test_52_confidence_follows_d023(tmp_path):
    res = run(tmp_path)
    assert all(o.source_confidence is Confidence.TRUSTED for o in res.orders)
    lc = res.lines_result.field_confidence
    assert set(lc) == {"series", "number", "order_customer", "invoice_account", "sku", "source_label", "order_date"}
    assert all(v is Confidence.TRUSTED for v in lc.values())
    assert not {"temsilci", "sektor", "kapali", "nettutar"} & set(lc)  # DQ-5 / revenue columns not imported


def test_53_no_parallel_reader():
    src = (ROOT / "services/importers/sales_activity.py").read_text()
    for banned in ("openpyxl", "load_workbook", "pandas", "read_excel", "zipfile", "WorkbookPair", "iter_rows"):
        assert banned not in src, banned


def test_54_no_ai_imports_or_calls():
    src = (ROOT / "services/importers/sales_activity.py").read_text()
    for banned in ("openai", "anthropic", "services.ai", "services.agent", "httpx", "requests", "urllib"):
        assert banned not in src, banned
    code = ("import sys, services.importers.sales_activity;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_55_no_persistence_or_live_swap(tmp_path):
    p = book(tmp_path)
    before = sorted(x.name for x in tmp_path.iterdir())
    data = p.read_bytes()
    assert import_order_sources(p).ok
    assert sorted(x.name for x in tmp_path.iterdir()) == before and p.read_bytes() == data
    src = (ROOT / "services/importers/sales_activity.py").read_text()
    for banned in ("write_text", "write_bytes", ".save(", "sqlite", "replace_context", "os.replace", "open("):
        assert banned not in src, banned
    for live in ("app.py", "dashboard_builder.py", "services/refresh.py", "services/context.py",
                 "services/action_sources.py", "services/actions.py", "services/entity.py"):
        text = (ROOT / live).read_text()
        assert "importers" not in text and "sales_activity" not in text, live


# =============================================================================================
# Record-level policies and selection
# =============================================================================================
def test_date_and_customer_conflicts_are_record_level(tmp_path):
    lines = BASE_LINES()
    lines[1][9] = D(2026, 1, 6)                                   # order 1001: two dates
    lines[4][4] = "100.01.0001"                                   # order 1002: two order customers
    res = run(tmp_path, lines)
    assert res.ok
    o1, o2 = orders_by_number(res)[1001], orders_by_number(res)[1002]
    assert (o1.order_date, o1.order_date_status, o1.source_type) == (None, "conflict", SourceType.GENERAL)
    assert (o2.order_customer, o2.customer, o2.customer_status, o2.customer_link.status) == \
        (None, None, "conflict", MatchStatus.NULL)
    assert o2.source_type is SourceType.FUAR and o2.edition_year == 2026   # attribution intact
    codes = {i.code: i.severity.value for i in res.report.issues}
    assert codes["order_date_conflict"] == "warning" and codes["order_customer_conflict"] == "warning"
    assert res.counts.order_date_unusable == 1 and res.counts.customer_conflict_orders == 1


def test_find_sales_workbook_is_deterministic(tmp_path):
    book(tmp_path)
    (tmp_path / "STOK 5 Ekim 2026.xlsx").write_bytes(b"x")
    assert find_sales_workbook(tmp_path).name == SALES_NAME
    book(tmp_path, name="2020-6 ekim satışlar.xlsx")
    with pytest.raises(SourceSelectionError) as exc:
        find_sales_workbook(tmp_path)
    assert "2" in str(exc.value) and "satış" not in str(exc.value)
    empty = tmp_path / "bos"
    empty.mkdir()
    with pytest.raises(SourceSelectionError):
        find_sales_workbook(empty)
