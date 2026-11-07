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
    assert reg["header"] == "Cari Kodu" and reg["rows"] == 9 and reg["numeric_cells"] == 1
    assert reg["duplicates"] == {"910.00.0001": 2} and "090.00.0001" in reg["unique"]   # leading zero kept
    st = core["status"]
    assert st["910.00.0001"] == st["090.00.0001"] == "confirmed"
    assert st["910.00.0006 "] == "ws"                         # trailing space: never trimmed into a match
    assert st["910.00.0007"] == "inv"                         # only its invoice account is registered
    assert st["910.00.0003"] == "not_found"


def test_ambiguous_matches_are_never_counted_as_registered(core):
    a = {r["code"]: r for r in core["views"]["A"]}
    assert a["910.00.0006 "]["reg"].startswith("Belirsiz") and a["910.00.0007"]["reg"].startswith("Belirsiz")
    assert HB.momentum_flag(core, "910.00.0007") == "Belirsiz" and HB.momentum_flag(core, "910.00.0003") == ""
    assert HB.momentum_flag(core, "910.00.0001") == "B2B"
    assert core["counts"]["status_confirmed"] == 4          # 0001, 0002, 0005, 090.00.0001
    codes = {w["code"]: w for w in core["warnings"]}
    assert "910.00.0007" in codes["B2B-FATURA"]["codes"] and "910.00.0006 " in codes["B2B-BOSLUK"]["codes"]


def test_the_three_call_lists(core):
    v = {k: {r["code"]: r for r in rows} for k, rows in core["views"].items()}
    assert set(v["A"]) == {"910.00.0003", "910.00.0004", "910.00.0006 ", "910.00.0007", "910.00.0009", "910.00.0010"}
    assert v["A"]["910.00.0004"]["b2b_seen"] == "Evet"                       # buys via B2B but not in the list
    assert v["A"]["910.00.0009"]["b2b_seen"] == "Belirsiz kanal"
    assert "910.00.0008" not in v["A"] and core["counts"]["closed_excluded_A"] == 1     # KAPALI
    assert v["B"]["910.00.0002"]["sub"] == "Diğer kanallardan alıyor"
    assert v["B"]["910.00.0099"]["sub"] == "Satış verisinde eşleşme yok" and v["B"]["910.00.0099"]["name"] == ""
    assert v["B"]["12345"]["sub"] == "Satış verisinde eşleşme yok"
    assert v["B"]["910.99.0001"]["sub"].startswith("Belirsiz") and v["B"]["910.00.0006"]["sub"].startswith("Belirsiz")
    assert "910.00.0005" not in v["B"] and "910.00.0001" not in v["B"]         # they did buy via B2B
    assert set(v["C"]) == {"910.00.0004", "910.00.0005"}
    c5 = v["C"]["910.00.0005"]
    assert c5["last_b2b"] == "2023-05-10" and c5["b2b_total"] == 9000 and c5["b2b_orders"] == 1
    assert c5["recent_other"] == 4000 and c5["recent_other_orders"] == 1


def test_window_counts_back_from_the_latest_transaction_not_today(core, src):
    assert core["counts"]["asof"] == "2026-06-30" and core["counts"]["window_start"] == "2025-06-30"
    _, sales, reg = _paths(src)
    base = DB.load_sales(sales)
    short = HB.analyse(pd.read_excel(sales), base, DB.cust_attrs(base), HB.read_registration(open(reg, "rb").read()),
                       HB.sheet_header(sales), review_days=30)
    assert "910.00.0001" in {r["code"] for r in short["views"]["C"]}           # last B2B 2026-03-01 → old for 30 days


def test_amounts_documents_missing_negative_and_mixed(core):
    y = core["yearly"]["910.00.0001"]
    assert y[2025]["b2b"] is None and y[2025]["b2b_orders"] == 2              # missing amount → unknown, never 0
    assert y[2026]["b2b"] == 7000 and y[2026]["b2b_orders"] == 2              # 8000 − 1000; distinct documents
    assert y[2026]["b2b_mixed"] == 1 and core["counts"]["mixed_documents"] == 1
    assert core["counts"]["b2b_amount_negative"] == 1 and core["counts"]["b2b_amount_missing"] == 1
    a = {r["code"]: r for r in core["views"]["A"]}
    assert a["910.00.0003"]["total"] == 59000                                 # identical lines both kept
    assert core["yearly"]["910.00.0009"][2025]["b2b"] is None                 # unclear channel → unknown
    assert HB.year_fields(core, "910.00.0002", 2025) == {"b2b": 0, "b2b_orders": 0, "b2b_mixed": 0}


def test_missing_registration_is_unknown_not_unregistered(src):
    _, sales, _ = _paths(src)
    base = DB.load_sales(sales)
    res = HB.analyse(pd.read_excel(sales), base, DB.cust_attrs(base), HB.read_registration(None), HB.sheet_header(sales))
    assert res["views"]["A"] == [] and res["views"]["B"] == []
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
        allowed = {"__future__", "datetime", "hashlib", "io", "json", "math", "pandas", "openpyxl", "argparse", "os",
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
    for v in ("A", "B", "C"):
        strip = lambda rows: [{x: r[x] for x in r if x != "detail"} for r in rows]
        assert strip(k["b2b"]["views"][v]["rows"]) == strip(m["b2b"]["views"][v]["rows"]), v
    assert {r["code"]: r["b2b"] for r in k["momentum"]["rows"]} == {r["code"]: r["b2b"] for r in m["momentum"]["rows"]}
    ky = {(r["code"], y["y"]): (y["b2b"], y["b2b_orders"], y["b2b_mixed"])
          for r in k["b2b"]["views"]["A"]["rows"] + k["b2b"]["views"]["C"]["rows"] for y in r.get("detail", {}).get("years", [])}
    my = {(r["code"], r["year"]): (r["b2b_amount"], r["b2b_orders"], r["b2b_mixed"])
          for r in m["purchases_yearly"]["rows"]}
    assert ky and all(my[key] == val for key, val in ky.items())
    assert list(m)[list(m).index("margin") + 1] == "b2b"
    assert classic[0]["b2b"]["view_A"] == modern[0]["b2b"]["view_A"]


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


@pytest.mark.parametrize("which", ["classic", "modern"])
def test_b2b_takip_views_search_filter_sort_and_detail_in_the_browser(browser, classic, modern, which):
    path = classic[1] if which == "classic" else str(modern[1])
    page, errors, requests = _open(browser, path)
    tabs = [t.get_attribute("data-t") for t in page.locator(".tab").all()]
    assert tabs[tabs.index("margin") + 1] == "b2b"
    page.click('.tab[data-t="b2b"]')
    panel = page.locator("#b2b")
    assert panel.locator(".b2b-view").count() == 3
    assert panel.locator("tbody tr.main").count() == 6
    panel.locator(".search").fill("910.00.0004")                               # code search
    assert panel.locator("tbody tr.main").count() == 1
    panel.locator(".search").fill("")
    panel.locator(".repsel").select_option("TEMSILCI-B")
    assert panel.locator("tbody tr.main").count() == 1
    panel.locator(".repsel").select_option("")
    panel.locator('.b2b-view[data-v="B"]').click()
    assert panel.locator("tbody tr.main").count() == 5 and "Satış verisinde eşleşme yok" in panel.inner_text()
    panel.locator('.b2b-view[data-v="C"]').click()
    assert panel.locator("tbody tr.main").count() == 2
    panel.locator('th[data-k="b2b_total"]').click()
    assert "910.00.0005" in panel.locator("tbody tr.main").first.inner_text()   # largest B2B total first
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
