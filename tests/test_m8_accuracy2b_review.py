"""M8-ACCURACY-2B correction pass (independent review of PR #29).

1. Footer reconciliation: `stock_footer_totals` is PASS only when EVERY checked footer cell reconciled; a
   contradiction is FAIL; anything partial (error-propagated, missing cache, unsupported, ambiguous) is OPEN with the
   matched / unreconciled counts visible. A partial reconciliation never makes `totals_consistent` True, and the
   evidence is kept per field so an unrelated column's errors never invalidate an independent field.
2. Owner-confirmed DinamikCRM stock template (D-056): physical column A = product code, physical column J = country
   of origin (both labelled `Stok_Kodu`). A duplicated identity header is accepted only when the whole header row is
   exactly an approved template; any moved / renamed / added / removed column is refused. Column identity only.

Synthetic workbooks only; the real template is represented by a header digest, never by its labels or data.
REAL production gate (`production_gate`). No AI, no network.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import dashboard_builder as db
from services import accuracy as ACC
from services import generation as GEN
from services import source_validation as SV
from services.importers.excel import column_index
from tests.fixtures.xlsx_synth import E, F, write_xlsx
from tests.test_m8_accuracy2 import SE, TODAY, stock_rows  # noqa: F401
from tests.test_m8_accuracy2b import COLI, HEAD, agg_row, analyze, book, products

pytestmark = pytest.mark.production_gate
S, NV, INV = ACC.State.VERIFIED, ACC.State.NOT_VERIFIED, ACC.State.INVALID
ROOT = Path(__file__).resolve().parents[1]


# =============================================================================================
# 1 — footer reconciliation status
# =============================================================================================
def _errrow(rows, f, l, n):
    out = agg_row(rows, f, l, n)
    out[COLI["H"]] = F(f"(SUM(H{f}:H{l}))", E("#REF!"))      # Excel itself caches the propagated error
    return out


def _error_book(path):
    rows = products()
    rows[2][COLI["H"]] = E("#REF!")                          # an error cell inside the inventory-value column
    return book(path, rows, footer=(_errrow,))


def test_error_propagated_footer_cells_make_the_footer_status_open_not_pass(tmp_path):
    a = analyze(_error_book(tmp_path / "s.xlsx"))
    assert a.footer_checks == {"checked": 4, "matched": 3, "mismatched": 0, "error_propagated": 1,
                               "missing_cache": 0, "unsupported": 0}
    assert a.footer_status() == "OPEN"
    assert a.footer_by_column["H"]["error_propagated"] == 1                       # the column is identified …
    assert all(a.footer_by_column[c]["matched"] == 1 for c in ("D", "F", "G"))     # … the others stay matched
    assert a.summary()["footer_status"] == "OPEN"


@pytest.mark.parametrize("kw,expected", [
    (dict(), "PASS"),
    (dict(cache=False), "OPEN"),                                                    # missing cache
    (dict(tamper="D"), "FAIL"),                                                     # contradictory totals
])
def test_footer_status_is_pass_only_when_every_checked_cell_matched(tmp_path, kw, expected):
    assert analyze(book(tmp_path / "s.xlsx", products(), **kw)).footer_status() == expected


def test_unsupported_and_unchecked_footers_are_open(tmp_path):
    rows = products()
    data = [["t"], ["t"], [None], list(HEAD)] + [list(r) for r in rows] + \
        [agg_row(rows, 5, 9, 10, "9")]
    p = write_xlsx(tmp_path / "h.xlsx", {"Sheet1": data}, hidden_rows={"Sheet1": [6]})
    assert analyze(p).footer_status() == "OPEN"                                     # SUBTOTAL over hidden rows
    assert analyze(book(tmp_path / "n.xlsx", products(), footer=())).footer_status() == "OPEN"   # nothing checked


def _manifest(p, decl=None):
    frozen = {"stock": GEN.FrozenSource.read("stock", str(p))}
    decl = decl or {}
    if decl.get("approve_totals"):
        decl = {"stock": {"approval": {"sha256": frozen["stock"].sha256, "totals_consistent": True}}}
    return GEN.build_manifest(frozen, {"stock": db.read_stock(str(p))}, decl, "explicit", TODAY)


def test_partial_footer_totals_never_make_totals_consistent_true(tmp_path):
    full = _manifest(book(tmp_path / "ok.xlsx", products()), {"approve_totals": True})
    assert full.record("stock").evidence.totals_consistent is True                 # approval + every footer matched
    part = _manifest(_error_book(tmp_path / "part.xlsx"), {"approve_totals": True})
    assert part.record("stock").evidence.totals_consistent is None                 # capped: partial ≠ verified
    bad = _manifest(book(tmp_path / "bad.xlsx", products(), tamper="D"), {"approve_totals": True})
    assert bad.record("stock").evidence.totals_consistent is False
    none = _manifest(book(tmp_path / "none.xlsx", products(), footer=()), {"approve_totals": True})
    assert none.record("stock").evidence.totals_consistent is True                 # no footer: declaration decides
    assert _manifest(book(tmp_path / "nd.xlsx", products(), footer=())).record("stock").evidence \
        .totals_consistent is None                                                  # … and nothing is assumed


def test_footer_evidence_is_field_level_and_does_not_spread(tmp_path):
    ins = SV.inspect_core("stock", _error_book(tmp_path / "s.xlsx").read_bytes())
    f = ins.fields()
    assert f["Envanter değeri"]["footer_totals"] == "OPEN"
    assert f["TOPLAM STOK MİKTAR "]["footer_totals"] == "PASS" and f["minseviye"]["footer_totals"] == "PASS"
    assert f["Stok_Kodu"]["footer_totals"] == "not_aggregated"                     # never aggregated
    m = _manifest(tmp_path / "s.xlsx")
    ev = m.record("stock").evidence
    reorder = ACC.REGISTER["stock.reorder"]                                         # does NOT read Envanter değeri
    overstock = ACC.REGISTER["stock.overstock"]                                     # reads it
    rp = [t for _, t in ev.problems(reorder.scope("stock"))]
    op = [t for _, t in ev.problems(overstock.scope("stock"))]
    assert not any("Envanter değeri" in t for t in rp)
    assert any("rapor toplamı doğrulanamadı" in t and "Envanter değeri" in t for t in op)


def test_reconcile_reports_partial_footers_as_open_with_counts(tmp_path):
    d = SE.write_all(str(tmp_path / "core"))
    _error_book(Path(d) / SE.STOCK_FILE)
    r = subprocess.run([sys.executable, "-m", "scripts.reconcile_sources", "--core", d, "--today", TODAY.isoformat()],
                       cwd=ROOT, capture_output=True, text=True, env={**os.environ, "AI_PROVIDER": "disabled"})
    out = r.stdout
    assert "[OPEN] Rapor toplam satırları" in out and "[PASS] Rapor toplam satırları" not in out
    assert "footer_matched=3" in out and "footer_error_propagated=1" in out
    assert "toplamı doğrulanamayan sütun: H" in out
    for secret in ("Sentetik Ürün", "SP-10", "Envanter değeri'"):
        assert secret not in out, secret


# =============================================================================================
# 2 — owner-confirmed stock template (A = product code, J = country of origin)
# =============================================================================================
THEAD = ["Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye",
         "Envanter değeri", "DURUM", "Yardımcı", "Stok_Kodu"]                    # J = 10th column
TFIELDS = {"Stok_Kodu": "A", "Stok_Adı": "B", "TOPLAM STOK MİKTAR ": "C", "STOK Kontrol %": "D",
           "minseviye": "E", "maxseviye": "F", "Envanter değeri": "G", "DURUM": "H"}


def trows(n=5):
    # J (country of origin) carries the SAME value on every row and differs from A: if J were ever read as the
    # product code, the distinct count would collapse to 1
    return [[f"SP-{100 + i}", f"Sentetik Ürün {i}", 10 + i, 1.5 + i, 1.0, 3.0, 100.0 * (i + 1), "", None, "XX"]
            for i in range(n)]


def tbook(path, head=THEAD, rows=None):
    data = [["Sentetik stok raporu"], ["(test)"], [None], list(head)] + [list(r) for r in (rows or trows())]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return write_xlsx(Path(path), {"Sheet1": data})


@pytest.fixture
def approved(monkeypatch):
    cols, sha = SV.header_signature({i + 1: lab for i, lab in enumerate(THEAD)})
    monkeypatch.setitem(SV.STOCK_TEMPLATES, "synthetic_v1", {
        "title_tr": "sentetik", "header_row": 4, "columns": cols, "header_sha256": sha,
        "identity": "A", "country_of_origin": "J", "fields": dict(TFIELDS)})
    return sha


def test_approved_template_reads_column_a_and_never_column_j(tmp_path, approved):
    p = tbook(tmp_path / "s.xlsx")
    a = analyze(p)
    assert a.ok and a.template["status"] == "approved_template" and a.template["template"] == "synthetic_v1"
    assert a.template["identity_column"] == "A" and a.template["country_of_origin_column"] == "J"
    assert a.code_rows == 5 and a.distinct_codes == 5 and a.duplicate_code_occurrences == 0
    ins = SV.inspect_core("stock", p.read_bytes())
    assert ins.layout_supported is True and ins.fields()["Stok_Kodu"]["duplicate_header"] is False
    assert db.stock_row_counts(db.read_stock(str(p)))["distinct_codes"] == 5     # pandas reads A as well


def test_approved_template_does_not_hide_a_duplicated_product_code_or_resolve_other_duplicates(tmp_path, approved):
    rows = trows() + [["SP-100", "Sentetik Ürün 0 (ikinci)", 7, 2.0, 1.0, 3.0, 50.0, "", None, "XX"]]
    a = analyze(tbook(tmp_path / "s.xlsx", rows=rows))
    assert a.ok and a.code_rows == 6 and a.duplicate_code_occurrences == 1          # reported, never merged/deleted
    assert "STOCK-REPORT-SCOPE" in {b.code for b in ACC.REGISTER["stock.product_counts"].blockers}


@pytest.mark.parametrize("why,head", [
    ("reordered", [THEAD[0], THEAD[2], THEAD[1]] + THEAD[3:]),
    ("renamed", THEAD[:4] + ["minimum seviye"] + THEAD[5:]),
    ("added", THEAD + ["Yeni sütun"]),
    ("removed", THEAD[:8] + THEAD[9:]),
    ("identity_moved", ["Özel"] + THEAD[1:9] + ["Stok_Kodu", "Stok_Kodu"]),
])
def test_altered_or_reordered_layouts_are_refused(tmp_path, approved, why, head):
    rows = [r[:len(head)] + [None] * (len(head) - len(r)) for r in trows()]
    p = tbook(tmp_path / f"{why}.xlsx", head=head, rows=rows)
    a = analyze(p)
    assert not a.ok and a.failure == "identity_header_ambiguous", why
    assert a.template["status"] == "unapproved_duplicate_identity" and a.template["header_sha256"] != approved
    ins = SV.inspect_core("stock", p.read_bytes())
    assert ins.layout_supported is False and ins.fields()["Stok_Kodu"]["duplicate_header"] is True


def test_refused_layout_withholds_every_stock_metric(tmp_path, approved):
    p = tbook(tmp_path / "s.xlsx", head=THEAD + ["Yeni sütun"], rows=[r + [None] for r in trows()])
    ev = _manifest(p).record("stock").evidence
    assert ev.layout_supported is False
    for k in ("stock.reorder", "stock.overstock", "stock.product_counts"):
        m = ACC.REGISTER[k]
        assert (INV, "Kaynak dosyanın düzeni desteklenmiyor.") in ev.problems(m.scope("stock")), k


def test_duplicate_identity_without_any_approved_template_is_refused(tmp_path):
    a = analyze(tbook(tmp_path / "s.xlsx"))                                           # synthetic template NOT registered
    assert not a.ok and a.template["status"] == "unapproved_duplicate_identity"
    assert a.template["problems"] == ["duplicate_identity_header_outside_an_approved_template"]


def test_a_single_identity_header_is_unchanged(tmp_path):
    a = analyze(book(tmp_path / "s.xlsx", products()))
    assert a.ok and a.template["status"] == "single_identity_header" and a.template["identity_column"] == "B"
    assert a.template["country_of_origin_column"] is None


def test_header_signature_ignores_trailing_empty_cells_only():
    base = {i + 1: lab for i, lab in enumerate(THEAD)}
    assert SV.header_signature(base) == SV.header_signature({**base, 11: "", 12: ""})
    assert SV.header_signature(base) != SV.header_signature({**base, 3: ""})        # a blank inside counts
    assert SV.header_signature(base) != SV.header_signature({**base, 2: "Stok_Adi"})


def test_the_real_template_is_pinned_by_digest_and_positions_only():
    t = SV.STOCK_TEMPLATES["dinamikcrm_stok_v1"]
    assert t["identity"] == "A" and t["country_of_origin"] == "J" and t["columns"] == 89
    assert len(t["header_sha256"]) == 64 and set(t["header_sha256"]) <= set("0123456789abcdef")
    assert t["fields"]["Stok_Kodu"] == "A" and "J" not in t["fields"].values()     # J is never a pipeline field
    assert len(set(t["fields"].values())) == len(t["fields"])
    assert all(column_index(v) <= t["columns"] for v in t["fields"].values())
    assert set(t["fields"]) == set(SV.CORE["stock"]["required"])                   # every pipeline field is pinned


def test_stock_code_authority_is_no_longer_a_static_blocker_and_nothing_is_verified():
    for k in ("stock.reorder", "stock.overstock", "stock.product_counts"):
        codes = {b.code for b in ACC.REGISTER[k].blockers}
        assert "STOCK-CODE-AUTHORITY" not in codes and "STOCK-REPORT-SCOPE" in codes, k
        assert "STOCK-DATE" in codes and ACC.B_REAL.code in codes, k
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    assert all(m.certified is not S for m in ACC.REGISTER.values())
