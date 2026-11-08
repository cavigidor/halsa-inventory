"""Father's B2B tracking (D-080) — synthetic data only.

Proves: the B2B channel is physical column AG ('B2B' exactly), registration is physical column B (exact codes, no
trimming / fuzzy matching), ambiguous matches are never counted as registered, a missing registration list is
'unknown' (A/B not produced), amounts keep missing / negative / duplicate lines honestly, orders are distinct documents
and mixed documents are flagged; the Klasik B2B variant leaves every original calculation, tab and the frozen builder
untouched while adding the tab, column and five-column detail; the V1 general report shows the same B2B figures.
"""
import ast
import datetime as dt
import json
import os
import re
import sys

import pandas as pd
import pytest

import dashboard_builder as DB
import halsa_b2b as HB
from tests.fixtures import b2b_synth as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASSIC = os.path.join(ROOT, "classic")
sys.path.insert(0, CLASSIC)
import klasik_b2b as KB  # noqa: E402

DAY = dt.date.fromisoformat(S.DATE)


def _data(path):
    t = open(path, encoding="utf-8").read()
    m = re.search(r"const DATA=(\{.*?\});\n", t, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


def _b2b(d):
    """B2B Takip rows: Klasik ships them as b2b_rows (no full draw at start-up), V1 as rows."""
    m = d["b2b"]
    return m["b2b_rows"] if "b2b_rows" in m else m["rows"]


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return S.write_all(str(tmp_path_factory.mktemp("b2b_src")))


def _paths(src):
    return (os.path.join(src, S.STOCK_FILE), os.path.join(src, S.SALES_FILE),
            os.path.join(src, "kayit", S.REG_FILE))


@pytest.fixture(scope="module")
def core(src):
    """The shared calculation on the synthetic export (load_sales / cust_attrs: the same rules in both dashboards)."""
    _, sales, reg = _paths(src)
    raw = pd.read_excel(sales)
    base = DB.load_sales(None, raw)
    return HB.analyse(raw, base, DB.cust_attrs(base), HB.read_registration(open(reg, "rb").read()),
                      HB.sheet_header(sales))


@pytest.fixture(scope="module")
def classic(src, tmp_path_factory):
    stock, sales, reg = _paths(src)
    d = tmp_path_factory.mktemp("klasik_b2b")
    res = KB.generate_b2b(stock, sales, str(d / "k.html"), registration=reg, day=DAY)
    G = KB.G
    orig = G.generate(stock, sales, str(d / "orig.html"), day=DAY)
    return res, str(d / "k.html"), orig, str(d / "orig.html")


# ---------------------------------------------------------------------------------------------
# 1. the shared rules
# ---------------------------------------------------------------------------------------------
def test_channel_is_physical_column_ag_exactly_b2b(core):
    c = core["counts"]
    assert core["channel"]["header"] == "Satici" and c["channel_ok"]
    assert c["b2b_lines"] == 7 and c["unclear_channel_lines"] == 1 and c["blank_channel_lines"] == 1


def test_channel_column_is_refused_when_the_header_row_differs():
    raw = pd.DataFrame(S.sales_rows(), columns=S.HEADERS)
    hdr = list(S.HEADERS)
    hdr[32] = "Baska"
    ch, info = HB.channel(raw, hdr)
    assert ch is None and not info["ok"]
    ch, info = HB.channel(raw, S.HEADERS[:20])
    assert ch is None


def test_registration_is_column_b_exact_with_duplicates_and_numeric_cells(core, src):
    reg = HB.read_registration(open(_paths(src)[2], "rb").read())
    assert reg["status"] == "ok" and reg["header"] == "Kod No" and reg["rows"] == 9 and reg["numeric_cells"] == 1
    assert reg["duplicates"] == {"910.00.0001": 2} and "090.00.0001" in reg["unique"]   # leading zero kept
    assert reg["missing_code_rows"] == [11] and reg["names"]["910.00.0099"] == "Sentetik Kayit Firma 7"
    assert not any("@" in str(v) for v in reg["names"].values())                       # e-mails never read
    st = core["status"]
    assert st["910.00.0001"] == st["090.00.0001"] == "confirmed"
    assert st["910.00.0006 "] == "ws"                         # trailing space: never trimmed into a match
    assert st["910.00.0007"] == "inv"                         # only its invoice account is registered
    assert st["910.00.0003"] == "not_found"
    c = core["counts"]
    assert c["registration_missing_code_rows"] == 1 and c["registration_duplicate_codes"] == 1
    assert "B2B-KAYIT-KODSUZ" in {w["code"] for w in core["warnings"]}


@pytest.mark.parametrize("headers,rows,state", [
    (["Id", "Cari Kodu", "x"], [[1, "910.00.0001", "y"]], "wrong_layout"),       # another column B header
    (S.REG_HEADERS, [], "empty"),                                                # header only
    (S.REG_HEADERS, [[1, None, "a", "b@ornek.invalid", "-", "F", "Kullanıcı"]], "empty"),
])
def test_wrong_layout_or_empty_registration_is_never_zero_registrations(src, tmp_path, headers, rows, state):
    path = tmp_path / "reg.xlsx"
    pd.DataFrame(rows, columns=headers).to_excel(path, index=False)
    reg = HB.read_registration(path.read_bytes())
    assert reg["status"] == state
    _, sales, _ = _paths(src)
    base = DB.load_sales(sales)
    res = HB.analyse(pd.read_excel(sales), base, DB.cust_attrs(base), reg, HB.sheet_header(sales))
    assert res["views"]["A"] == res["views"]["B"] == res["views"]["V"] == []
    assert set(res["status"].values()) == {"unknown"} and HB.momentum_flag(res, "910.00.0003") == "—"
    assert HB.read_registration(b"not a workbook")["status"] == "unreadable"


def test_ambiguous_matches_are_separate_and_never_counted_as_registered(core):
    v = {r["code"]: r for r in core["views"]["V"]}
    assert v["910.00.0006 "]["kind"].endswith("boşluk farkı") and v["910.00.0007"]["kind"].endswith("fatura hesabı kayıtlı")
    assert v["910.00.0006"]["kind"].startswith("Kayıt kodu yalnız boşluk") and v["910.00.0006"]["linked"] == "910.00.0006 "
    assert v["910.99.0001"]["kind"].startswith("Kayıt kodu yalnız fatura") and v["910.99.0001"]["linked"] == "910.00.0007"
    assert v["910.99.0001"]["name"] == "Sentetik Kayit Firma 5"                 # name from 'Kullanıcı Cari'
    assert HB.momentum_flag(core, "910.00.0007") == "Belirsiz" and HB.momentum_flag(core, "910.00.0003") == ""
    assert HB.momentum_flag(core, "910.00.0001") == "B2B"
    assert core["counts"]["status_confirmed"] == 4          # 0001, 0002, 0005, 090.00.0001
    codes = {w["code"]: w for w in core["warnings"]}
    assert "910.00.0007" in codes["B2B-FATURA"]["codes"] and "910.00.0006 " in codes["B2B-BOSLUK"]["codes"]


def test_the_call_lists_and_the_verification_list(core):
    v = {k: {r["code"]: r for r in rows} for k, rows in core["views"].items()}
    assert set(v["A"]) == {"910.00.0003", "910.00.0010"}                         # definite gaps only
    assert all(r["reg"] == "Listede bulunamadı" for r in v["A"].values())
    assert v["V"]["910.00.0004"]["kind"] == "B2B'den alıyor, kodu kayıt listesinde yok"
    assert v["V"]["910.00.0009"]["kind"].startswith("Kanal yazımı belirsiz")
    assert set(v["V"]) == {"910.00.0004", "910.00.0006 ", "910.00.0007", "910.00.0009", "910.00.0006", "910.99.0001"}
    assert "910.00.0008" not in v["A"] and core["counts"]["closed_excluded"]["A"] == 1     # KAPALI
    assert v["B"]["910.00.0002"]["sub"] == "Diğer kanallardan alıyor"
    assert v["B"]["910.00.0099"]["sub"].startswith("Satış verisinde eşleşme yok")
    assert v["B"]["910.00.0099"]["name"] == "Sentetik Kayit Firma 7" and v["B"]["910.00.0099"]["excel_row"] == 9
    assert v["B"]["12345"]["sub"].startswith("Satış verisinde eşleşme yok")
    assert set(v["B"]) == {"910.00.0002", "910.00.0099", "12345"}
    assert set(v["C"]) == {"910.00.0004", "910.00.0005"}
    c5 = v["C"]["910.00.0005"]
    assert c5["last_b2b"] == "2023-05-10" and c5["b2b_total"] == 9000 and c5["b2b_orders"] == 1
    assert c5["recent_other"] == 4000 and c5["recent_other_orders"] == 1
    kinds = core["counts"]["view_V_kinds"]
    assert sum(kinds.values()) == core["counts"]["view_V"] == 6


def test_unverified_channel_never_produces_b_or_c(src):
    _, sales, reg = _paths(src)
    raw = pd.read_excel(sales)
    base = DB.load_sales(None, raw)
    hdr = list(S.HEADERS)
    hdr[32] = "Baska"
    res = HB.analyse(raw, base, DB.cust_attrs(base), HB.read_registration(open(reg, "rb").read()), hdr)
    assert res["views"]["B"] == [] and res["views"]["C"] == []
    assert "B2B-KANAL-YOK" in {w["code"] for w in res["warnings"]}
    assert HB.module(res)["b2b_channel_unavailable"] is True
    assert all(r["b2b_total"] is None for r in res["per"].values())


def test_window_counts_back_from_the_latest_transaction_not_today(core, src):
    assert core["counts"]["asof"] == "2026-06-30" and core["counts"]["window_start"] == "2025-06-30"
    _, sales, reg = _paths(src)
    base = DB.load_sales(sales)
    short = HB.analyse(pd.read_excel(sales), base, DB.cust_attrs(base), HB.read_registration(open(reg, "rb").read()),
                       HB.sheet_header(sales), review_days=30)
    assert "910.00.0001" in {r["code"] for r in short["views"]["C"]}           # last B2B 2026-03-01 → old for 30 days


def test_amounts_documents_missing_negative_mixed_and_identifier_collisions(core):
    y = core["yearly"]["910.00.0001"]
    assert y[2025]["b2b"] is None and y[2025]["b2b_orders"] == 2              # missing amount → unknown, never 0
    assert y[2026]["b2b"] == 7000 and y[2026]["b2b_orders"] == 2              # 8000 − 1000; distinct documents
    assert y[2026]["b2b_mixed"] == 1 and core["counts"]["mixed_documents"] == 1
    c = core["counts"]
    assert c["doc_ids_multi_customer"] == 1 and c["doc_ids_multi_customer_b2b"] == 1
    assert core["yearly"]["910.00.0010"][2026]["b2b_mixed"] == 0              # the collision is not "mixed" for it
    assert c["b2b_amount_negative"] == 1 and c["b2b_amount_missing"] == 1
    a = {r["code"]: r for r in core["views"]["A"]}
    assert a["910.00.0003"]["total"] == 59000                                 # identical lines both kept
    assert core["yearly"]["910.00.0009"][2025]["b2b"] is None                 # unclear channel → unknown
    assert HB.year_fields(core, "910.00.0002", 2025) == {"b2b": 0, "b2b_orders": 0, "b2b_mixed": 0}
    assert c["b2b_buyers_not_confirmed"] == 1 and c["b2b_lines_not_confirmed"] == 1   # 910.00.0004 only


def test_missing_registration_is_unknown_not_unregistered(src):
    _, sales, _ = _paths(src)
    base = DB.load_sales(sales)
    res = HB.analyse(pd.read_excel(sales), base, DB.cust_attrs(base), HB.read_registration(None), HB.sheet_header(sales))
    assert res["views"]["A"] == [] and res["views"]["B"] == [] and res["views"]["V"] == []
    assert {r["code"] for r in res["views"]["C"]} == {"910.00.0004", "910.00.0005"}
    assert set(res["status"].values()) == {"unknown"} and HB.momentum_flag(res, "910.00.0001") == "—"
    w = res["warnings"][0]
    assert w["code"] == "B2B-KAYIT-YOK" and "b2b_kullanici_rapor.xlsx" in w["title"] and "GELMEZ" in w["issue"]
    assert HB.module(res)["b2b_unavailable"] is True


def test_the_shared_module_imports_nothing_of_either_application():
    for f in ("halsa_b2b.py", os.path.join("classic", "klasik_b2b.py")):
        tree = ast.parse(open(os.path.join(ROOT, f), encoding="utf-8").read())
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        allowed = {"__future__", "collections", "datetime", "hashlib", "io", "json", "math", "pandas", "openpyxl", "argparse", "os",
                   "shutil", "subprocess", "sys", "tempfile", "generate"}
        assert mods <= allowed, (f, mods - allowed)
    assert "import original_builder" in KB.CHILD and "spec_from_file_location('halsa_b2b'" in KB.CHILD
    assert not any(f"import {m}" in KB.CHILD for m in ("services", "config", "app", "schemas", "dashboard_builder"))


# ---------------------------------------------------------------------------------------------
# 2. Klasik B2B variant
# ---------------------------------------------------------------------------------------------
def test_frozen_builder_and_original_generator_are_untouched(classic):
    G = KB.G
    assert G._sha(G.BUILDER) == G.BUILDER_SHA256
    res, path, orig, opath = classic
    assert set(orig["counts"]) == set(res["counts"]) and orig["counts"] == res["counts"]
    t = open(opath, encoding="utf-8").read()
    assert "B2B Takip" not in t and "b2b" not in t                             # the original report has no B2B at all


def test_variant_changes_only_the_three_requested_things(classic):
    res, path, orig, opath = classic
    new, old = _data(path), _data(opath)
    assert list(new) == [*list(old)[:list(old).index("margin") + 1], "b2b", *list(old)[list(old).index("margin") + 1:]]
    for k in old:
        o, n = old[k], new[k]
        cols = [c for c in n["cols"] if c["k"] != "b2b"]
        assert cols == o["cols"] and n["note"] == o["note"] and n["title"] == o["title"], k
        assert len(n["rows"]) == len(o["rows"]), k
        for ro, rn in zip(o["rows"], n["rows"]):
            rn = {x: v for x, v in rn.items() if x != "b2b"}
            if "detail" in ro and "years" in ro["detail"]:
                assert rn["detail"]["products"] == ro["detail"]["products"]     # Öne Çıkan Ürünler unchanged
                for yo, yn in zip(ro["detail"]["years"], rn["detail"]["years"]):
                    assert {x: v for x, v in yn.items() if not x.startswith("b2b")} == yo
                rn = {x: v for x, v in rn.items() if x != "detail"}
                ro = {x: v for x, v in ro.items() if x != "detail"}
            assert rn == ro, k
    mc = [c["k"] for c in new["momentum"]["cols"]]
    assert mc[mc.index("rep") + 1] == "b2b" and mc[mc.index("b2b") + 1] == "sector"
    assert [r["b2b"] for r in new["momentum"]["rows"]] == ["B2B", "B2B", ""]
    page, opage = open(path, encoding="utf-8").read(), open(opath, encoding="utf-8").read()
    head = opage[:opage.index("</style>")]
    assert page.startswith(head)                                               # the original styles come first, unchanged
    assert opage[opage.index("const fmtMoney"):opage.index(HB.ANCHOR)] in page  # the original script is kept verbatim


def test_variant_refuses_missing_registration_path_and_reports_missing_list(src, tmp_path):
    stock, sales, _ = _paths(src)
    with pytest.raises(Exception, match="bulunamadı"):
        KB.generate_b2b(stock, sales, str(tmp_path / "x.html"), registration=str(tmp_path / "yok.xlsx"), day=DAY)
    res = KB.generate_b2b(stock, sales, str(tmp_path / "m.html"), day=DAY)
    assert res["b2b"]["registration_status"] == "missing" and res["b2b"]["view_A"] == 0
    b = _data(tmp_path / "m.html")["b2b"]
    assert b["b2b_unavailable"] and b["b2b_reg_file"] == "b2b_kullanici_rapor.xlsx"
    assert oct(os.stat(tmp_path / "m.html").st_mode & 0o777) == "0o600"


def test_inputs_are_never_changed(src, tmp_path):
    G = KB.G
    stock, sales, reg = _paths(src)
    before = [G._sha(p) for p in (stock, sales, reg)]
    KB.generate_b2b(stock, sales, str(tmp_path / "k.html"), registration=reg, day=DAY)
    assert [G._sha(p) for p in (stock, sales, reg)] == before
    with pytest.raises(Exception, match="zaten var"):
        KB.generate_b2b(stock, sales, str(tmp_path / "k.html"), registration=reg, day=DAY)


def test_inject_requires_the_exact_template_ending():
    with pytest.raises(ValueError):
        HB.inject("<html><style></style><script></script></html>")


# ---------------------------------------------------------------------------------------------
# 3. V1 general report: same figures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def modern(src, tmp_path_factory):
    from services.father_report import generate
    from tests.test_v1_sales_review import case
    tmp = tmp_path_factory.mktemp("v1_b2b")
    folder, *_ = case(tmp, lambda raw: pd.DataFrame(S.sales_rows(), columns=S.HEADERS))
    out = tmp / "g.html"
    res = generate(folder, out, b2b=True, b2b_registration=_paths(src)[2])
    plain = generate(folder, tmp / "plain.html")
    return res, out, plain, tmp / "plain.html"


def test_v1_report_shows_the_same_b2b_classification_as_klasik(classic, modern):
    k, m = _data(classic[1]), _data(modern[1])
    strip = lambda rows: [{x: r[x] for x in r if x != "detail"} for r in rows]
    assert k["b2b"]["rows"] == [] and "b2b_rows" not in m["b2b"]
    assert strip(_b2b(k)) == strip(_b2b(m)) and len(_b2b(k)) == 14
    assert k["b2b"]["izahat"] == m["b2b"]["izahat"] and k["b2b"]["cols"] == m["b2b"]["cols"]
    assert {r["code"]: r["b2b"] for r in k["momentum"]["rows"]} == {r["code"]: r["b2b"] for r in m["momentum"]["rows"]}
    ky = {(r["code"], y["y"]): (y["b2b"], y["b2b_orders"], y["b2b_mixed"])
          for r in _b2b(k) for y in r.get("detail", {}).get("years", [])}
    my = {(r["code"], r["year"]): (r["b2b_amount"], r["b2b_orders"], r["b2b_mixed"])
          for r in m["purchases_yearly"]["rows"]}
    assert ky and all(my[key] == val for key, val in ky.items())
    assert not any(r.get("detail") for r in _b2b(k) if r["side"] == "kayit")   # no sales → no detail
    assert list(m)[list(m).index("margin") + 1] == "b2b"
    assert classic[0]["b2b"]["view_A"] == modern[0]["b2b"]["view_A"]
    assert classic[0]["b2b"]["unified"] == modern[0]["b2b"]["unified"]


def test_v1_report_without_b2b_is_unchanged_and_keeps_its_safeguards(modern):
    res, out, plain, ppath = modern
    assert plain["b2b"] is None and "b2b" not in plain["sections"]
    p = _data(ppath)
    assert "b2b" not in p and all(c["k"] != "b2b" for c in p["momentum"]["cols"])
    m = _data(out)
    assert m["b2b"]["review_html"].count("İnceleme için / doğrulanmadı") == 1
    assert all("Cari" not in str(r) or r["section"] for r in m["report_scope"]["rows"])
    assert m["report_scope"]["rows"][-1]["section"] == "B2B Takip"
    assert list(m)[-1] == "report_scope"


# ---------------------------------------------------------------------------------------------
# 4. browser (offline)
# ---------------------------------------------------------------------------------------------
from tests.test_m5_browser import browser  # noqa: E402,F401


def _open(browser, path):
    page = browser.new_page(viewport={"width": 1280, "height": 1000})
    errors, requests = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(f"file://{path}")
    return page, errors, requests


def _cells(panel, col):
    heads = [h.get_attribute("data-k") for h in panel.locator("thead th").all()]
    ix = heads.index(col) + 1
    return [t.inner_text().strip() for t in panel.locator(f"tbody tr.main td:nth-child({ix})").all()]


@pytest.mark.parametrize("which", ["classic", "modern"])
def test_b2b_takip_single_table_izahat_filter_search_sort_and_detail_in_the_browser(browser, classic, modern, which):
    path = classic[1] if which == "classic" else str(modern[1])
    page, errors, requests = _open(browser, path)
    tabs = [t.get_attribute("data-t") for t in page.locator(".tab").all()]
    assert tabs[tabs.index("margin") + 1] == "b2b"
    page.click('.tab[data-t="b2b"]')
    panel = page.locator("#b2b")
    assert panel.locator(".b2b-view").count() == 0                              # no A / B / C / V buttons
    heads = [h.get_attribute("data-k") for h in panel.locator("thead th").all()]
    assert heads[-2:] == ["b2b_reg", "izahat"]                                  # İzahat is the rightmost column
    assert panel.locator("thead th").last.inner_text().upper().startswith("İZAHAT")       # headers are upper-cased by CSS
    iz = panel.locator("select.b2b-izahat")
    assert iz.input_value() == "" and iz.locator("option").first.inner_text() == "İzahat: Tümü (14)"
    assert panel.locator("tbody tr.main").count() == 14                         # default: every row
    assert page.locator(".tab[data-t='b2b'] .cnt").inner_text() == "14"
    assert set(_cells(panel, "b2b_reg")) == {"VAR", "Listede yok", "Kontrol"}
    assert panel.locator("tbody .b2b-flag").count() == 6 and panel.locator("tbody .b2b-amb").count() == 6
    assert "bulunamadı" not in panel.locator("table").inner_text()
    assert "YOK" not in _cells(panel, "b2b_reg")                                # no declaration → never YOK
    panel.locator(".search").fill("910.00.0010")                               # search across all customers
    assert panel.locator("tbody tr.main").count() == 1
    panel.locator(".search").fill("bayi")                                      # İzahat text is searchable
    assert panel.locator("tbody tr.main").count() == 1 and "910.00.0007" in panel.inner_text()
    panel.locator(".search").fill("")
    iz.select_option("listede_yok")
    assert sorted(_cells(panel, "code")) == ["910.00.0003", "910.00.0010"]
    iz.select_option("b2b_kayitsiz")                                           # C ∩ Doğrulanacak: one row
    assert _cells(panel, "code") == ["910.00.0004"] and "eskiden B2B'den alıyordu" in _cells(panel, "izahat")[0]
    iz.select_option("kayit_satissiz")                                         # registration-only rows stay separate
    assert sorted(_cells(panel, "code")) == ["12345", "910.00.0099"] and set(_cells(panel, "rep")) == {""}
    iz.select_option("")
    panel.locator(".repsel").first.select_option("TEMSILCI-B")
    assert sorted(_cells(panel, "code")) == ["910.00.0002", "910.00.0004"]
    panel.locator(".repsel").first.select_option("")
    panel.locator('th[data-k="b2b_total"]').click()
    top = max((r for r in _b2b(_data(path)) if r["b2b_total"] is not None), key=lambda r: r["b2b_total"])
    assert top["code"] in panel.locator("tbody tr.main").first.inner_text()    # largest B2B total first
    if which == "classic":                                                      # detail built on first open
        assert panel.locator("[data-lazy]").count() == 10                      # customer rows only
        panel.locator("tr.exp").first.click()
        det = panel.locator("tr.det.open")
        assert [h.inner_text().strip().upper() for h in det.locator("table.sub th").all()] == \
            ["YIL", "HARCAMA", "SİPARİŞ", "B2B ALIMI", "SİPARİŞ (B2B)"]
        assert det.locator(".prods-h").count() == 1 and panel.locator("[data-lazy]").count() == 9
    page.click('.tab[data-t="momentum"]')
    assert page.locator("#momentum .b2b-flag").count() == 2
    if which == "classic":
        page.locator("#momentum tr.exp").first.click()
        det = page.locator("#momentum tr.det.open")
        assert [h.inner_text().strip().upper() for h in det.locator("table.sub th").all()] == \
            ["YIL", "HARCAMA", "SİPARİŞ", "B2B ALIMI", "SİPARİŞ (B2B)"]
        assert det.locator(".prods-h").inner_text().upper() == "ÖNE ÇIKAN ÜRÜNLER"
        assert det.locator(".prow").count() == 2 and "*" in det.inner_text()
        page.click('.tab[data-t="winback"]')                                    # other original tabs still work
    else:
        page.click('.tab[data-t="purchases_yearly"]')
        heads = [h.get_attribute("data-k") for h in page.locator("#purchases_yearly th").all()]
        assert heads[heads.index("amount") + 1:heads.index("amount") + 3] == ["b2b_amount", "b2b_orders"]
    assert not errors, errors
    assert not any(u.startswith(("http:", "https:")) for u in requests)
    page.close()


def test_missing_registration_banner_in_the_browser(browser, src, tmp_path):
    stock, sales, _ = _paths(src)
    KB.generate_b2b(stock, sales, str(tmp_path / "m.html"), day=DAY)
    page, errors, _ = _open(browser, str(tmp_path / "m.html"))
    page.click('.tab[data-t="b2b"]')
    assert "anlamına gelmez" in page.locator("#b2b .b2b-miss").inner_text()
    page.click('.tab[data-t="momentum"]')
    assert set(t.inner_text() for t in page.locator("#momentum tbody tr.main td:nth-child(3)").all()) == {"—"}
    assert not errors
    page.close()


# ---------------------------------------------------------------------------------------------
# 5. one table with İzahat (father's feedback, 2026-11-08) — presentation only, classification unchanged
# ---------------------------------------------------------------------------------------------
KONTROL = {"bayi", "b2b_kayitsiz", "bosluk", "kanal", "kayit_bagli"}


def _expected_keys(res):
    keys = set()
    for k, rows in res["views"].items():
        for r in rows:
            reg_side = r["code"] not in res["per"] or (k == "V" and r.get("excel_row"))
            keys.add(("kayit" if reg_side else "musteri", r["code"]))
    return keys


def test_unified_table_lists_every_classified_row_once_and_every_open_customer(core):
    rows = HB.unified_rows(core)
    keys = [(r["side"], r["code"]) for r in rows]
    assert len(keys) == len(set(keys)) == 14                                    # no customer twice
    assert _expected_keys(core) <= set(keys)                                    # nothing of A / B / C / V dropped
    extra = set(keys) - _expected_keys(core)                                    # registered, in no call list
    assert extra == {("musteri", "910.00.0001"), ("musteri", "090.00.0001")}
    assert all(core["per"][c]["status"] == "confirmed" and core["per"][c]["recent_b2b"] for _, c in extra)
    open_customers = {c for c, p in core["per"].items() if not p["closed"]}
    assert {c for s, c in keys if s == "musteri"} == open_customers               # Tümü = every open customer
    assert ("musteri", "910.00.0008") not in keys                               # KAPALI rule kept
    assert ("musteri", "910.00.0006 ") in keys and ("kayit", "910.00.0006") in keys   # never merged across codes
    r4 = next(r for r in rows if r["code"] == "910.00.0004")
    assert r4["prev"] == "VC" and r4["izahat_key"] == "b2b_kayitsiz" and "eskiden B2B'den alıyordu" in r4["izahat"]
    letters = {}
    for k, vr in core["views"].items():                                         # classification letters preserved
        for r in vr:
            reg_side = r["code"] not in core["per"] or (k == "V" and r.get("excel_row"))
            letters.setdefault(("kayit" if reg_side else "musteri", r["code"]), set()).add(k)
    assert all(set(r["prev"]) == letters.get((r["side"], r["code"]), set()) for r in rows)


def test_unified_amounts_are_the_classification_values_and_b2b_kaydi_is_never_inferred(core):
    rows = HB.unified_rows(core)
    for r in rows:
        p = core["per"].get(r["code"]) if r["side"] == "musteri" else None
        if p:
            assert (r["recent"], r["total"], r["last"], r["last_b2b"], r["b2b_total"]) == \
                (p["recent"], p["total"], p["last"], p["last_b2b"], p["b2b_total"])
        else:
            assert r["recent"] is r["total"] is r["b2b_total"] is None and r["rep"] == ""
        assert r["izahat"] and r["izahat_key"] in HB.IZAHAT
    st = core["status"]
    assert {r["code"] for r in rows if r["b2b_reg"] == "VAR"} == \
        {c for c, s in st.items() if s == "confirmed" and not core["per"][c]["closed"]} | {"12345", "910.00.0099"}
    assert {r["code"] for r in rows if r["b2b_reg"] == "Kontrol"} == {r["code"] for r in rows if r["izahat_key"] in KONTROL}
    assert {r["code"] for r in rows if r["b2b_reg"] == "Listede yok"} == {r["code"] for r in core["views"]["A"]}
    assert "YOK" not in {r["b2b_reg"] for r in rows}                            # no declaration → never YOK
    assert all("bulunamadı" not in r["izahat"] for r in rows)
    total = sum(p["b2b_total"] or 0 for p in core["per"].values() if not p["closed"])
    assert sum(r["b2b_total"] or 0 for r in rows) == total                      # B2B totals unchanged


def test_list_complete_declaration_only_turns_unmatched_codes_into_yok(core, src):
    plain, decl = HB.unified_rows(core), HB.unified_rows(core, registration_complete=True)
    assert [(r["side"], r["code"]) for r in plain] == [(r["side"], r["code"]) for r in decl]
    for a, b in zip(plain, decl):
        if a["izahat_key"] == "listede_yok":
            assert (a["b2b_reg"], b["b2b_reg"]) == ("Listede yok", "YOK") and b["izahat"].startswith("B2B kaydı yok")
        else:
            assert a == b                                                       # Kontrol / VAR rows untouched
    _, sales, _ = _paths(src)
    raw = pd.read_excel(sales)
    base = DB.load_sales(None, raw)
    missing = HB.analyse(raw, base, DB.cust_attrs(base), HB.read_registration(None), HB.sheet_header(sales))
    m = HB.module(missing, registration_complete=True)                         # a declaration without a list is void
    assert {r["b2b_reg"] for r in m["rows"]} == {"—"} and not m["b2b_registration_complete"]
    assert {r["code"] for r in m["rows"]} == {r["code"] for r in missing["views"]["C"]}
    assert all(r["izahat_key"] == "liste_kullanilamadi" for r in m["rows"])


def test_declaration_needs_the_registration_list(src, tmp_path):
    stock, sales, _ = _paths(src)
    with pytest.raises(KB.G.ClassicError):
        KB.generate_b2b(stock, sales, str(tmp_path / "x.html"), day=DAY, registration_complete=True)
    from services.father_report import generate
    with pytest.raises(ValueError):
        generate(str(tmp_path), tmp_path / "y.html", b2b=True, b2b_list_complete=True)
    assert not (tmp_path / "x.html").exists() and not (tmp_path / "y.html").exists()


def test_declared_preview_shows_yok_in_the_browser(browser, src, tmp_path):
    stock, sales, reg = _paths(src)
    res = KB.generate_b2b(stock, sales, str(tmp_path / "d.html"), registration=reg, day=DAY, registration_complete=True)
    assert res["b2b"]["registration_complete"] and res["b2b"]["unified"]["b2b_kaydi"]["YOK"] == 2
    page, errors, _ = _open(browser, str(tmp_path / "d.html"))
    page.click('.tab[data-t="b2b"]')
    panel = page.locator("#b2b")
    assert _cells(panel, "b2b_reg").count("YOK") == 2 and "Listede yok" not in _cells(panel, "b2b_reg")
    assert "beyan edildi" in panel.locator("p.note").inner_text()
    assert "B2B kaydı yok" in panel.locator("select.b2b-izahat").inner_text()
    assert not errors
    page.close()


# ---------------------------------------------------------------------------------------------
# 6. registration identity is kept apart from sales-channel uncertainty (PR #53 review)
# ---------------------------------------------------------------------------------------------
UNCLEAR_REGISTERED = ("910.00.0011", "910.00.0012")      # exact Kod No; one ambiguous AG spelling each


@pytest.fixture(scope="module")
def unclear_reports(tmp_path_factory):
    from services.father_report import generate
    from tests.test_v1_sales_review import case
    src2 = S.write_all(str(tmp_path_factory.mktemp("b2b_src_unclear")), registered_unclear=True)
    stock, sales, reg = _paths(src2)
    d = tmp_path_factory.mktemp("unclear_out")
    k = KB.generate_b2b(stock, sales, str(d / "k.html"), registration=reg, day=DAY)
    folder, *_ = case(d / "v1", lambda raw: pd.DataFrame(S.sales_rows(registered_unclear=True), columns=S.HEADERS))
    g = generate(folder, d / "g.html", b2b=True, b2b_registration=reg)
    raw = pd.read_excel(sales)
    base = DB.load_sales(None, raw)
    res = HB.analyse(raw, base, DB.cust_attrs(base), HB.read_registration(open(reg, "rb").read()), HB.sheet_header(sales))
    return res, (k, str(d / "k.html")), (g, str(d / "g.html"))


def test_exact_registration_stays_var_when_the_channel_is_ambiguous(unclear_reports, core):
    res, (k, kp), (g, gp) = unclear_reports
    assert all(res["status"][c] == "confirmed" and res["per"][c]["unclear"] for c in UNCLEAR_REGISTERED)
    assert res["per"]["910.00.0012"]["has_b2b"] and res["per"]["910.00.0012"]["recent_b2b"]
    for path in (kp, gp):                                                       # Classic and V1 outputs
        rows = {r["code"]: r for r in _b2b(_data(path))}
        for c in UNCLEAR_REGISTERED:
            r = rows[c]
            assert r["b2b_reg"] == "VAR" and r["izahat_key"] == "kanal_var"
            assert r["izahat"].startswith("B2B kaydı var") and "kanal" in r["izahat"] and "Kontrol" not in r["izahat"]
            assert r["b2b_total"] is None                                       # B2B amount stays unknown, not 0
        n9 = rows["910.00.0009"]                                                # no list match + ambiguous channel
        assert n9["b2b_reg"] == "Kontrol" and n9["izahat_key"] == "kanal" and "kodu listede yok" in n9["izahat"]
        assert "YOK" not in {r["b2b_reg"] for r in rows.values()}
    # everything else identical to the base scenario: classification, counts, amounts, warnings
    assert {k: [r["code"] for r in v] for k, v in res["views"].items()} == \
        {k: [r["code"] for r in v] for k, v in core["views"].items()}
    base_rows = {(r["side"], r["code"]): r for r in HB.unified_rows(core)}
    new_rows = {(r["side"], r["code"]): r for r in HB.unified_rows(res)}
    assert set(new_rows) - set(base_rows) == {("musteri", c) for c in UNCLEAR_REGISTERED}
    changed = [k for k in base_rows if base_rows[k] != new_rows[k]]
    assert changed == []
    assert [w["code"] for w in res["warnings"]] == [w["code"] for w in core["warnings"]]
    assert res["counts"]["unclear_channel_lines"] == core["counts"]["unclear_channel_lines"] + 2
    assert k["b2b"]["unified"] == g["b2b"]["unified"]
    assert k["b2b"]["unified"]["b2b_kaydi"] == {"Kontrol": 6, "Listede yok": 2, "VAR": 8}


def test_ambiguous_channel_var_in_the_browser(browser, unclear_reports):
    _, (_, kp), (_, gp) = unclear_reports
    for path in (kp, gp):
        page, errors, _ = _open(browser, path)
        page.click('.tab[data-t="b2b"]')
        panel = page.locator("#b2b")
        panel.locator("select.b2b-izahat").select_option("kanal_var")
        assert sorted(_cells(panel, "code")) == list(UNCLEAR_REGISTERED)
        assert _cells(panel, "b2b_reg") == ["VAR", "VAR"] and panel.locator("tbody .b2b-flag").count() == 2
        panel.locator("select.b2b-izahat").select_option("kanal")
        assert _cells(panel, "code") == ["910.00.0009"] and _cells(panel, "b2b_reg") == ["Kontrol"]
        assert not errors
        page.close()


def test_klasik_b2b_paging_searches_filters_and_sorts_the_complete_table(browser, unclear_reports, tmp_path):
    """Klasik's frozen renderer has no paging, so B2B Takip is paged by the shared script; every search, filter and
    sort still runs over all rows. The V1 report keeps its own paging (no extra pager)."""
    _, (_, kp), (_, gp) = unclear_reports
    html = open(kp, encoding="utf-8").read()
    assert html.count('"b2b_page_size": 200') + html.count('"b2b_page_size":200') == 1
    small = tmp_path / "k5.html"
    small.write_text(html.replace('"b2b_page_size": 200', '"b2b_page_size": 5').replace('"b2b_page_size":200',
                                                                                      '"b2b_page_size":5'), encoding="utf-8")
    rows = _b2b(_data(kp))
    for r in rows:
        r["code"] = r["code"].strip()                                           # cell text is trimmed by the browser
    assert len(rows) == 16
    page, errors, _ = _open(browser, str(small))
    page.click('.tab[data-t="b2b"]')
    panel = page.locator("#b2b")
    count = lambda: panel.locator(".count").inner_text()
    assert panel.locator("tbody tr.main").count() == 5 and count() == "16 satır · sayfa 1 / 4"
    assert [c for c in _cells(panel, "code")] == [r["code"] for r in rows[:5]]          # default order kept
    seen = list(_cells(panel, "code"))
    for _ in range(3):
        panel.locator(".b2b-next").click()
        seen += _cells(panel, "code")
    assert seen == [r["code"] for r in rows] and panel.locator(".b2b-next").is_disabled()   # every row, once
    assert count() == "16 satır · sayfa 4 / 4"
    last = rows[-1]["code"]
    panel.locator(".b2b-prev").click(); panel.locator(".b2b-prev").click(); panel.locator(".b2b-prev").click()
    panel.locator(".search").fill(last)                                         # a code on the last page, from page 1
    assert _cells(panel, "code") == [last] and count() == "1 satır" and not panel.locator(".b2b-pager").is_visible()
    panel.locator(".search").fill("")
    assert count() == "16 satır · sayfa 1 / 4"
    panel.locator('th[data-k="recent"]').click()                               # sort spans every page
    money = [r["recent"] for r in rows if r["recent"] is not None]
    first = panel.locator("tbody tr.main").first.inner_text()
    top = max(rows, key=lambda r: r["recent"] if r["recent"] is not None else -1e18)
    assert top["code"] in first and "▼" in panel.locator('th[data-k="recent"]').inner_text()
    order = []
    for i in range(4):
        order += _cells(panel, "code")
        if i < 3:
            panel.locator(".b2b-next").click()
    want = [r["code"] for r in sorted(rows, key=lambda r: -(r["recent"] if r["recent"] is not None else -1e18))]
    assert [order.index(c) for c in want if c in order][:len(money)] == list(range(len(money)))
    panel.locator("select.b2b-izahat").select_option("listede_yok")            # filter resets to page 1
    assert sorted(_cells(panel, "code")) == ["910.00.0003", "910.00.0010"] and count() == "2 satır"
    panel.locator("select.b2b-izahat").select_option("")
    panel.locator(".repsel").first.select_option("TEMSILCI-B")                 # representative filter spans all rows
    assert sorted(_cells(panel, "code")) == sorted(r["code"] for r in rows if r["rep"] == "TEMSILCI-B")
    panel.locator(".repsel").first.select_option("")
    panel.locator(".b2b-next").click()                                         # detail opens on a later page
    exp = panel.locator("tr.exp", has=page.locator("td.mono", has_text="910.00.0")).first
    exp.click()
    assert panel.locator("tr.det.open table.sub").count() == 1
    page.click('.tab[data-t="winback"]'); page.click('.tab[data-t="b2b"]')
    assert panel.locator("tbody tr.main").count() == 5 and not errors
    page.close()
    page, errors, _ = _open(browser, gp)                                        # V1: native pager only
    page.click('.tab[data-t="b2b"]')
    assert page.locator("#b2b .b2b-pager").count() == 0 and page.locator("#b2b .pager").count() == 1 and not errors
    page.close()
