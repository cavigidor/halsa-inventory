"""D-081 — detailed collections importer, Gider/Gelir row isolation (see test_m6_gider_gelir), the private owner /
financial report and the general report's Hedef / Trodat sections. Synthetic data only (invented codes and amounts).

Proves: the collections report date needs the file name and column J to agree; the red marker is the workbook's own
rule and only with an order date; arithmetic deviations are reported, rows never repaired or merged; the owner report
reads only explicitly named sources (Bütçe without renaming), keeps unknown as unknown and never labels an incomplete
allocation total complete; the general report never reads or contains owner / financial data and never exports Hedef
credit fields; both files work offline.
"""
import ast
import datetime as dt
import json
import os
import re
from pathlib import Path

import pytest

from services.importers.detailed_collections import import_detailed_collections as IMPORT
from tests.fixtures import collections_synth as CS

ROOT = Path(__file__).resolve().parents[1]


def _data(path):
    t = Path(path).read_text(encoding="utf-8")
    return json.loads(re.search(r"const DATA=(\{.*?\});\n", t, re.S).group(1).replace("<\\/", "</")), t


# ---------------------------------------------------------------------------------------------
# 1. detailed collections importer
# ---------------------------------------------------------------------------------------------
def test_collections_import_meaning_and_counts(tmp_path):
    r = IMPORT(CS.write(str(tmp_path)))
    c = r.counts
    assert r.ok and c.report_date == CS.REPORT and c.report_date_origin == "filename+header"
    assert (c.documents, c.accounts, c.repeated_document_numbers) == (7, 4, 1)
    assert c.source_red_rule_found and (c.source_red_rows, c.source_red_accounts) == (4, 2)
    assert c.days_to_due_mismatch == c.days_to_order_due_mismatch == 0
    assert c.rows_without_order_date == 1 and c.partially_open_documents == 3 and c.non_tl_rows == 1
    assert c.certified is False
    by = {(d.document_no, d.source_row): d for d in r.documents}
    nod = next(d for d in r.documents if d.order_date is None)
    assert nod.days_to_order_due is None and nod.order_due_date is None and not nod.source_red  # unknown, not "0"
    assert nod.days_to_order_due_source == 0
    both = [d for (n, _), d in by.items() if n == "HALSA-10006"]
    assert len(both) == 2 and len({d.record_key for d in both}) == 2         # kept separately, never merged
    eur = next(d for d in r.documents if d.currency == "EUR")
    assert eur.open_amount == 100.0 and eur.open_amount_tl == 3600.0
    s = json.dumps(r.summary(), ensure_ascii=False)
    assert "120.90" not in s and "HALSA-" not in s                         # safe summary: counts only


def test_collections_report_date_must_agree(tmp_path):
    bad = IMPORT(CS.write(str(tmp_path / "a"), j_date="06.11.2026"))
    assert not bad.ok and "report_date_conflict" in bad.report.codes()
    no_hdr = IMPORT(CS.write(str(tmp_path / "b"), j_date="bugün"))
    assert not no_hdr.ok and "report_date_header_missing" in bad.report.codes() + no_hdr.report.codes()
    hdr_only = IMPORT(CS.write(str(tmp_path / "c"), name="Evrak Detaylı Tahsilat Raporu.xlsx"))
    assert hdr_only.ok and hdr_only.counts.report_date_origin == "header"
    assert "report_date_from_header_only" in hdr_only.report.codes()


def test_red_marker_only_from_the_workbook_rule_and_arithmetic_is_checked(tmp_path):
    r = IMPORT(CS.write(str(tmp_path / "a"), red_rule=False))
    assert r.ok and r.counts.source_red_rows == 0 and "source_red_rule_not_found" in r.report.codes()

    def tamper(rows):
        rows[0][9] += 3            # column J no longer due − report date
        rows[1][22] += 2           # W no longer order date + term − report date
        return rows
    t = IMPORT(CS.write(str(tmp_path / "b"), mutate=tamper))
    assert t.ok and t.counts.days_to_due_mismatch == 1 and t.counts.days_to_order_due_mismatch == 1
    assert {"days_to_due_mismatch", "days_to_order_due_mismatch"} <= set(t.report.codes())
    assert t.documents[0].days_to_due == t.documents[0].days_to_due_source - 3   # recomputed, source kept


def test_collections_never_reach_general_or_ai_code():
    """Only the owner report imports the collections importer."""
    hits = []
    for p in ROOT.rglob("*.py"):
        if any(part in (".venv", "node_modules", "tests") for part in p.parts):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^\s*(from\s+\S*detailed_collections\s+import|import\s+\S*detailed_collections)", t, re.M) \
                and p.name != "owner_report.py":
            hits.append(str(p.relative_to(ROOT)))
    assert hits == []
    tree = ast.parse((ROOT / "services" / "general_sources.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert not ({"kredi", "gider_gelir", "butce", "cari_policy", "owner_view", "owner_report"} & (names | mods))


# ---------------------------------------------------------------------------------------------
# 2. owner / financial report
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def owner(tmp_path_factory):
    from services.owner_report import generate
    from tests.fixtures import owner_synth as O, synthetic_erp as SE
    f = tmp_path_factory.mktemp("owner")
    SE.write_all(str(f / "core"))
    src = dict(cari=f / "core" / SE.AGING_FILE, collections=Path(CS.write(str(f / "c"))),
               kredi=O.write_kredi(f / "k", dt.date(2026, 11, 5)), gider_gelir=O.write_gider_gelir(f / "g"),
               butce=O.write_butce(f / "b", name="bütçe EKİM.xlsx"))       # real-style name: never renamed
    before = {k: v.read_bytes() for k, v in src.items()}
    res = generate(f / "Yonetim.html", butce_year=2026, as_of=dt.date(2026, 11, 5), today=dt.date(2026, 11, 7),
                   cari_date=dt.date(2026, 11, 5), **src)
    assert {k: v.read_bytes() for k, v in src.items()} == before
    return res, f / "Yonetim.html", src, f


def test_owner_report_sections_and_meaning(owner):
    res, path, src, _ = owner
    D, page = _data(path)
    assert list(D) == ["cari", "tahsilat_hesap", "tahsilat_evrak", "kredi_takvim", "kredi_taksit", "gg_ay", "gg_kol",
                       "butce", "scope"]
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert all("İnceleme için / doğrulanmadı" in m["review_html"] for k, m in D.items() if k != "scope")
    acc = {r["code"]: r for r in D["tahsilat_hesap"]["rows"]}
    assert acc["120.90.0001"]["red"] == 1 and acc["120.90.0004"]["open_tl"] == 3670.0   # EUR row counted in TL
    assert acc["120.90.0003"]["unknown_order_due"] == 1 and acc["120.90.0003"]["oldest_order_due"] is None
    docs = D["tahsilat_evrak"]["rows"]
    assert sum(r["red"] == "Kırmızı" for r in docs) == 4 and sum(r["red"] == "Bilinmiyor" for r in docs) == 1
    assert all("cari_match" in r for r in acc.values())                    # Cari comparison inside the owner file only
    assert "Halsa Yönetim Raporu" in page and "Yönetim / Finans — Özel" in page
    assert "TAHSİLAT ÖNCELİKLİ" not in page and "Riskli" not in page    # no priority, no risk list
    assert any(r["window"].startswith("Vade tarihi geçmiş") for r in D["kredi_takvim"]["rows"])
    assert {r["currency"] for r in D["kredi_takvim"]["rows"]} == {"TL"}
    scope = {r["source"]: r for r in D["scope"]["rows"]}
    assert all(r["state"].startswith("Okundu") for k, r in scope.items() if not k.startswith("Genel"))
    assert D["butce"]["rows"] and any(r["validity"] != "valid" and r["value"] is None for r in D["butce"]["rows"])


def test_owner_report_refuses_and_reports_missing(owner, tmp_path):
    from services.owner_report import OwnerReportError, generate
    res, path, src, f = owner
    with pytest.raises(OwnerReportError, match="zaten var"):
        generate(path, kredi=src["kredi"])
    with pytest.raises(OwnerReportError, match="butce-year"):
        generate(tmp_path / "x.html", butce=src["butce"])
    with pytest.raises(OwnerReportError, match="En az bir"):
        generate(tmp_path / "y.html")
    only = generate(tmp_path / "z.html", collections=src["collections"], today=dt.date(2026, 11, 7))
    D, _ = _data(tmp_path / "z.html")
    assert set(D) == {"tahsilat_hesap", "tahsilat_evrak", "scope"}
    assert "cari_balance" not in D["tahsilat_hesap"]["rows"][0]               # no Cari given → no comparison
    missing = [r for r in D["scope"]["rows"] if r["state"].startswith("Kaynak verilmedi")]
    assert {r["source"] for r in missing} == {"Cari yaşlandırma", "Kredi takip", "Gider / Gelir", "Bütçe"}
    assert only["sections"]["tahsilat_evrak"] == 7


def test_owner_report_never_labels_incomplete_allocation_complete(tmp_path):
    from services.owner_report import generate
    from tests import test_m6_gider_gelir as TG
    exps = [list(e) for e in TG.EXPENSES]
    exps[0][2] = (0.5, 0.3, 0.1, 0.05)
    (tmp_path / "g").mkdir()
    g = TG.book(tmp_path / "g", expenses=[tuple(e) for e in exps])
    generate(tmp_path / "o.html", gider_gelir=g, today=dt.date(2026, 11, 7))
    D, _ = _data(tmp_path / "o.html")
    assert all(r["allocated_complete"] is None and r["allocation_status"].startswith("EKSİK") for r in D["gg_kol"]["rows"])
    assert len(D["gg_haric"]["rows"]) == 1 and D["gg_haric"]["rows"][0]["ratio_sum"] == 0.95
    assert "GG-ORAN" in D["gg_ay"]["review_html"]


# ---------------------------------------------------------------------------------------------
# 3. general report: Hedef / Trodat, and no owner data
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def general(tmp_path_factory, owner):
    from services.father_report import generate
    from tests.test_v1_sales_review import case
    from tests.fixtures import c360_synth as C3
    from tests import test_m6_trodat as TT
    tmp = tmp_path_factory.mktemp("general")
    folder, *_ = case(tmp)
    h = tmp / "hedef.xlsx"
    C3.write_hedef(h)
    (tmp / "t").mkdir()
    t = TT.book(tmp / "t")
    res = generate(folder, tmp / "g.html", hedef=h, trodat=t)
    plain = generate(folder, tmp / "plain.html")
    return res, tmp / "g.html", plain, tmp / "plain.html"


def test_general_report_hedef_trodat_sections(general):
    res, path, plain, ppath = general
    D, page = _data(path)
    keys = list(D)
    assert keys[-3:] == ["hedef", "trodat", "report_scope"]
    hed = D["hedef"]
    assert hed["rows"] and not ({"risk", "kredi", "discount", "isk1"} & {c["k"] for c in hed["cols"]})
    assert all(not ({"risk", "kredi", "discount_tier"} & set(r)) for r in hed["rows"])
    assert "50000" not in json.dumps(hed["rows"])                          # the synthetic Risk value never exported
    tro = D["trodat"]
    assert tro["rows"] and "Halsa ürün koduyla eşleme yok" in tro["review_html"]
    scope = {r["section"]: r for r in D["report_scope"]["rows"]}
    assert scope["Hedef / Trodat ek analizleri"]["state"] == "Dahil: Hedef ve Trodat"
    P, _ = _data(ppath)
    assert "hedef" not in P and "trodat" not in P and plain.get("extra") is None


def test_general_report_contains_no_owner_or_financial_data(general, owner):
    from tests.fixtures import owner_synth as O
    _, path, _, _ = general
    page = Path(path).read_text(encoding="utf-8")
    for canary in (*O.SECRETS, "HALSA-1000", "Evrak Detaylı", "Tahsilat — ", "Kredi — ", "Gider/Gelir — ", "Bütçe — "):
        assert canary not in page, canary
    D, _ = _data(path)
    assert not ({"cari", "collections", "tahsilat_hesap", "kredi_takvim", "gg_ay", "butce"} & set(D))


# ---------------------------------------------------------------------------------------------
# 4. offline browser
# ---------------------------------------------------------------------------------------------
from tests.test_m5_browser import browser  # noqa: E402,F401


def _open(browser, path):
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    errors, requests = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(Path(path).as_uri())
    return page, errors, requests


def test_owner_and_general_reports_work_offline(browser, owner, general):
    page, errors, requests = _open(browser, owner[1])
    D, _ = _data(owner[1])
    for key, m in D.items():
        page.click(f'.tab[data-t="{key}"]')
        assert page.locator(f"#{key} tbody tr.main").count() == min(len(m["rows"]), m.get("page_size") or 10**9), key
    page.click('.tab[data-t="tahsilat_hesap"]')
    panel = page.locator("#tahsilat_hesap")
    panel.locator(".review-issues > summary").click()
    panel.locator('details[data-check="TAH-KIRMIZI"] > summary').click()
    assert "onaylı vade sonucu değil" in panel.inner_text()
    panel.locator(".search").fill("120.90.0004")
    assert panel.locator("tbody tr.main").count() == 1
    page.click('.tab[data-t="butce"]')
    assert "sayfa 1 /" in page.locator("#butce .count").inner_text()
    page.locator("#butce .next-page").click()
    assert "sayfa 2 /" in page.locator("#butce .count").inner_text()
    assert not errors and not any(u.startswith(("http:", "https:")) for u in requests)
    page.close()
    page, errors, requests = _open(browser, general[1])
    for key in ("hedef", "trodat"):
        page.click(f'.tab[data-t="{key}"]')
        assert page.locator(f"#{key} table").count() == 1
    assert not errors and not any(u.startswith(("http:", "https:")) for u in requests)
    page.close()
