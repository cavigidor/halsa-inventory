"""Klasik Rapor: the original dashboard (commit 71f5f2f), frozen, generated from explicitly chosen files.

Proves with SYNTHETIC data: the frozen builder is byte-identical to 71f5f2f; the generator's output is byte-identical
to running that original builder directly (same files, date and hash seed) and to the committed golden report; the
only run-to-run variation of the original (tie order in "Büyüme / Küçülme") is pinned; inputs are never changed; the
master list is never read as the Cari aging report; nothing of the new Halsa application participates; every original
tab and browser interaction works.
"""
import ast
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

import pytest

from tests.fixtures import classic_synth as CS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLASSIC = os.path.join(ROOT, "classic")
sys.path.insert(0, CLASSIC)
import generate as G  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "fixtures", "classic", "golden_synthetic.html")
DAY = dt.date.fromisoformat(CS.DATE)
ORIGINAL_SHA = "76afc196f984c3320e62e9a1244f57ba344054f53f009c49cb261fbc328e3646"    # git show 71f5f2f:dashboard_builder.py


def _sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def _data(path):
    t = open(path, encoding="utf-8").read()
    m = re.search(r"const DATA=(\{.*?\});\n", t, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    return CS.write_all(str(tmp_path_factory.mktemp("klasik_src")))


def _gen(src, out, **kw):
    return G.generate(os.path.join(src, CS.STOCK_FILE), os.path.join(src, CS.SALES_NFC), out,
                      cari=kw.pop("cari", os.path.join(src, CS.AGING_FILE)), day=kw.pop("day", DAY), **kw)


def _direct(src, out, seed, aging=True):
    """The ORIGINAL builder, run directly (no wrapper) on a folder holding exactly these files."""
    folder = os.path.join(os.path.dirname(out), f"direct-{seed}-{aging}")
    os.makedirs(folder, exist_ok=True)
    for n in (CS.STOCK_FILE, CS.SALES_FILE, *([CS.AGING_FILE] if aging else [])):
        shutil.copyfile(os.path.join(src, n), os.path.join(folder, n))
    code = (f"import sys,datetime as dt; sys.path.insert(0,{CLASSIC!r}); import original_builder as ob; "
            f"ob.build({folder!r},{out!r},today=dt.date({DAY.year},{DAY.month},{DAY.day}))")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": str(seed)}
    subprocess.run([sys.executable, "-s", "-P", "-c", code], check=True, env=env, cwd=folder)
    return out


# ---------------------------------------------------------------------------------------------
# 1. the frozen original and its isolation
# ---------------------------------------------------------------------------------------------
def test_the_frozen_builder_has_the_pinned_hash_of_commit_71f5f2f():
    assert _sha(os.path.join(CLASSIC, "original_builder.py")) == ORIGINAL_SHA == G.BUILDER_SHA256


def test_the_pinned_hash_is_the_one_in_git_history():
    """Needs the repository history (a full clone; CI's shallow checkout skips this one check)."""
    try:
        blob = subprocess.run(["git", "show", "71f5f2f:dashboard_builder.py"], cwd=ROOT, capture_output=True,
                              check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("history of 71f5f2f not available in this checkout (shallow clone)")
    assert hashlib.sha256(blob).hexdigest() == ORIGINAL_SHA


def test_the_classic_code_imports_nothing_of_the_new_halsa():
    allowed = {"os", "glob", "json", "html", "datetime", "warnings", "pandas", "numpy", "argparse", "hashlib",
               "shutil", "subprocess", "sys", "tempfile", "unicodedata", "__future__", "openpyxl", "original_builder"}
    for f in ("original_builder.py", "generate.py"):
        tree = ast.parse(open(os.path.join(CLASSIC, f), encoding="utf-8").read())
        mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert mods <= allowed, (f, mods - allowed)
    child = G.CHILD
    assert "import original_builder" in child and not any(f"import {m}" in child for m in G.FORBIDDEN_MODULES)


def test_the_new_dashboard_builder_is_a_different_file_and_never_used(src, tmp_path):
    new = os.path.join(ROOT, "dashboard_builder.py")
    assert _sha(new) != ORIGINAL_SHA                                   # the new dashboard is a separate program
    res = _gen(src, str(tmp_path / "k.html"))
    assert set(res["counts"]) == {"low", "over", "lapsed", "coll", "momentum", "margin", "ordersize", "osmove", "risk"}
    html = open(tmp_path / "k.html", encoding="utf-8").read()
    for marker in ("stockagent-generation", "stockagent-displayed", "withheld", "Doğrulan", "finans görüntüleme"):
        assert marker not in html                                      # no new-Halsa page feature at all


# ---------------------------------------------------------------------------------------------
# 2. equivalence
# ---------------------------------------------------------------------------------------------
def test_generator_output_equals_the_original_builder_run_directly(src, tmp_path):
    res = _gen(src, str(tmp_path / "klasik.html"))
    direct = _direct(src, str(tmp_path / "direct.html"), seed=G.HASH_SEED)
    assert open(res["out"], "rb").read() == open(direct, "rb").read()  # byte for byte
    assert res["sha256"] == _sha(direct)


def test_generator_output_equals_the_committed_golden_report(src, tmp_path):
    res = _gen(src, str(tmp_path / "klasik.html"))
    assert open(res["out"], "rb").read() == open(GOLDEN, "rb").read(), \
        "Klasik Rapor output changed: the frozen original or a library changed its output"
    d = _data(res["out"])
    assert list(d) == ["reorder", "overstock", "osmove", "winback", "momentum", "ordersize", "margin", "risk",
                       "collections"]
    assert res["counts"] == {"low": 1, "over": 2, "lapsed": 4, "coll": 3, "momentum": 9, "margin": 9, "ordersize": 1,
                             "osmove": 1, "risk": 3}


def test_same_inputs_and_date_give_the_same_bytes(src, tmp_path):
    a = _gen(src, str(tmp_path / "a.html"))
    b = _gen(src, str(tmp_path / "b.html"))
    assert a["sha256"] == b["sha256"]


def test_the_only_variation_of_the_original_is_the_order_of_equal_growth_rows(src, tmp_path):
    """The original builder iterates a Python set for "Büyüme / Küçülme": rows with the same change % come out in a
    random order per run. Everything else is identical; the generator pins one order (PYTHONHASHSEED=0)."""
    runs = [_data(_direct(src, str(tmp_path / f"seed{s}.html"), seed=s)) for s in (1, 2, 3, 4)]
    orders = {tuple(r["code"] for r in d["momentum"]["rows"]) for d in runs}
    assert len(orders) > 1                                             # the original itself varies
    for d in runs[1:]:
        assert {k: v for k, v in d.items() if k != "momentum"} == {k: v for k, v in runs[0].items() if k != "momentum"}
        assert [r["pct"] for r in d["momentum"]["rows"]] == [r["pct"] for r in runs[0]["momentum"]["rows"]]
        canon = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)   # noqa: E731
        assert canon(d["momentum"]["rows"]) == canon(runs[0]["momentum"]["rows"])


def test_without_a_cari_report_the_original_omits_tahsilat_and_riskli(src, tmp_path):
    res = _gen(src, str(tmp_path / "k.html"), cari=None)
    direct = _direct(src, str(tmp_path / "d.html"), seed=G.HASH_SEED, aging=False)
    assert open(res["out"], "rb").read() == open(direct, "rb").read()
    d = _data(res["out"])
    assert "collections" not in d and "risk" not in d and res["counts"]["coll"] == 0
    assert "Alacaklar:" not in open(res["out"], encoding="utf-8").read()


def test_the_header_shows_each_file_name_exactly_as_stored(src, tmp_path):
    assert CS.SALES_FILE != CS.SALES_NFC and os.path.exists(os.path.join(src, CS.SALES_FILE))
    res = _gen(src, str(tmp_path / "k.html"))                          # the owner picked the NFC spelling
    html = open(res["out"], encoding="utf-8").read()
    assert f"Satış: {CS.SALES_FILE}" in html and f"Satış: {CS.SALES_NFC}" not in html
    assert res["inputs"]["sales"] == CS.SALES_FILE


def test_inputs_are_never_changed_and_nothing_else_is_written(src, tmp_path):
    before = {n: _sha(os.path.join(src, n)) for n in os.listdir(src)}
    mt = {n: os.stat(os.path.join(src, n)).st_mtime_ns for n in os.listdir(src)}
    out_dir = tmp_path / "out"
    _gen(src, str(out_dir / "k.html"))
    assert {n: _sha(os.path.join(src, n)) for n in os.listdir(src)} == before
    assert {n: os.stat(os.path.join(src, n)).st_mtime_ns for n in os.listdir(src)} == mt
    assert os.listdir(out_dir) == ["k.html"]


# ---------------------------------------------------------------------------------------------
# 3. refusals (nothing generated)
# ---------------------------------------------------------------------------------------------
def test_the_master_list_is_never_read_as_the_cari_aging_report(src, tmp_path):
    out = tmp_path / "k.html"
    with pytest.raises(G.ClassicError, match="Cari yaşlandırma raporu değil"):
        _gen(src, str(out), cari=os.path.join(src, CS.MASTER_FILE))
    assert not out.exists()


def test_a_file_name_the_original_would_misread_is_refused(src, tmp_path):
    odd = tmp_path / "in"
    odd.mkdir()
    shutil.copyfile(os.path.join(src, CS.SALES_FILE), odd / "stok satışlar.xlsx")   # 'stok' → read as stock
    with pytest.raises(G.ClassicError, match="başka bir rapor gibi okunur"):
        G.generate(os.path.join(src, CS.STOCK_FILE), str(odd / "stok satışlar.xlsx"), str(tmp_path / "k.html"), day=DAY)
    shutil.copyfile(os.path.join(src, CS.SALES_FILE), odd / "veriler.xlsx")          # no 'sat' → never read
    with pytest.raises(G.ClassicError, match="başka bir rapor gibi okunur"):
        G.generate(os.path.join(src, CS.STOCK_FILE), str(odd / "veriler.xlsx"), str(tmp_path / "k.html"), day=DAY)
    assert not (tmp_path / "k.html").exists()


def test_missing_wrong_type_and_existing_output_are_refused(src, tmp_path):
    with pytest.raises(G.ClassicError, match="dosya yok"):
        G.generate(str(tmp_path / "yok stok.xlsx"), os.path.join(src, CS.SALES_FILE), str(tmp_path / "k.html"))
    csv = tmp_path / "stok.csv"
    csv.write_text("x")
    with pytest.raises(G.ClassicError, match="yalnızca .xlsx"):
        G.generate(str(csv), os.path.join(src, CS.SALES_FILE), str(tmp_path / "k.html"))
    (tmp_path / "k.html").write_text("eski")
    with pytest.raises(G.ClassicError, match="zaten var"):
        _gen(src, str(tmp_path / "k.html"))
    assert (tmp_path / "k.html").read_text() == "eski"
    _gen(src, str(tmp_path / "k.html"), overwrite=True)
    assert open(tmp_path / "k.html", "rb").read() == open(GOLDEN, "rb").read()


def test_a_changed_frozen_builder_refuses(src, tmp_path, monkeypatch):
    monkeypatch.setattr(G, "BUILDER_SHA256", "0" * 64)
    with pytest.raises(G.ClassicError, match="değişmiş"):
        _gen(src, str(tmp_path / "k.html"))


def test_cli_prints_counts_and_hash_only(src, tmp_path, capsys):
    rc = G._cli(["--stock", os.path.join(src, CS.STOCK_FILE), "--sales", os.path.join(src, CS.SALES_NFC),
                 "--cari", os.path.join(src, CS.AGING_FILE), "--out", str(tmp_path / "k.html"), "--date", CS.DATE])
    out = capsys.readouterr().out
    assert rc == 0 and "Klasik rapor hazır" in out and "Geri Kazanım 4" in out and "Sentetik" not in out
    rc = G._cli(["--stock", os.path.join(src, CS.STOCK_FILE), "--sales", os.path.join(src, CS.SALES_NFC),
                 "--cari", os.path.join(src, CS.MASTER_FILE), "--out", str(tmp_path / "x.html")])
    assert rc == 2 and "Klasik rapor oluşturulamadı" in capsys.readouterr().err


def test_mac_launcher_uses_explicit_selection_and_the_generator():
    sh = open(os.path.join(CLASSIC, "Klasik Rapor Oluştur.command"), encoding="utf-8").read()
    assert "choose file" in sh and '"$HERE/generate.py"' in sh and '-r "$HERE/requirements.txt"' in sh
    assert "--stock" in sh and "--sales" in sh and "--cari" in sh
    assert "dashboard_builder.py" not in sh and "services" not in sh


# ---------------------------------------------------------------------------------------------
# 4. the original page in a real browser: every tab and interaction
# ---------------------------------------------------------------------------------------------
from tests.test_m5_browser import browser  # noqa: E402,F401  (the suite's Chromium fixture; REQUIRE_BROWSER_TESTS)


def test_every_original_tab_and_interaction_works_in_the_browser(browser):
    b = browser
    if True:
        page = b.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file://" + GOLDEN)
        tabs = page.eval_on_selector_all(".tab", "els => els.map(e => e.dataset.t)")
        assert tabs == ["overview", "reorder", "overstock", "osmove", "winback", "momentum", "ordersize", "margin",
                        "risk", "collections"]
        assert page.is_visible("#overview") and page.query_selector_all("#overview .stat")
        for t in tabs[1:]:
            page.click(f'.tab[data-t="{t}"]')
            assert page.is_visible(f"#{t}") and not page.is_visible("#overview")
            n = len(_data(GOLDEN)[t]["rows"])
            assert page.inner_text(f"#{t} .count") == f"{n} satır"
        page.click('.tab[data-t="momentum"]')                           # search filters rows
        page.fill("#momentum .search", "esit 3")
        assert page.inner_text("#momentum .count") == "1 satır"
        page.fill("#momentum .search", "")
        first = page.eval_on_selector_all("#momentum tbody tr.exp", "trs => trs.map(t => t.innerText)")
        names = lambda: page.eval_on_selector_all("#momentum tbody tr.exp .prod", "els => els.map(e => e.innerText)")  # noqa: E731
        page.click('#momentum th[data-k="name"]')                       # sorting by a column, then reversed
        asc = names()
        page.click('#momentum th[data-k="name"]')
        desc = names()
        assert len(first) == len(asc) == 9 and sorted(asc) == sorted(desc) and asc == desc[::-1] and asc != desc
        assert set(page.inner_text('#momentum th[data-k="name"]')) & {"▲", "▼"}
        page.select_option("#momentum .repsel", "TEMSILCI-B")            # representative filter
        assert page.inner_text("#momentum .count") == "4 satır"
        page.select_option("#momentum .repsel", "")
        page.click('.tab[data-t="winback"]')                            # expanded detail rows
        row = page.query_selector("#winback tr.exp")
        row.click()
        assert "open" in (page.get_attribute("#winback tr.det", "class") or "")
        page.click('.tab[data-t="osmove"]')
        page.click("#osmove tr.exp")
        assert "Bu ürünü alan müşteriler" in page.text_content("#osmove tr.det")
        assert "open" in (page.get_attribute("#osmove tr.det", "class") or "")
        assert errors == []
        page.close()


def test_the_launcher_pins_the_libraries_the_equivalence_was_proven_with():
    req = open(os.path.join(CLASSIC, "requirements.txt"), encoding="utf-8").read().split()
    assert req == ["pandas==3.1.0", "numpy==2.5.4", "openpyxl==3.1.5"]


def test_the_new_halsa_never_imports_the_classic_generator():
    """Changes to the new dashboard can't silently change Klasik Rapor, and the reverse: no module outside classic/
    (and its tests) imports it."""
    hits = []
    for d, dirs, files in os.walk(ROOT):
        dirs[:] = [x for x in dirs if x not in (".git", ".venv", "node_modules", "classic", "__pycache__")]
        for f in files:
            if f.endswith(".py") and f not in ("test_classic_dashboard.py", "classic_synth.py"):
                t = open(os.path.join(d, f), encoding="utf-8", errors="ignore").read()
                if re.search(r"^\s*(from|import)\s+(classic|original_builder|generate)\b", t, re.M):
                    hits.append(os.path.relpath(os.path.join(d, f), ROOT))
    assert hits == []
