"""M8-R3 — read-only strict vs dependency-scoped metric diagnostics (`reconcile_sources --metric-diagnostics`).

Established on synthetic data:
1. the strict result (checks, counts, overall, exit code, evidence results) is identical with or without diagnostics;
2. every diagnostic gate state is exactly the production gate's decision, and the breakdown recomposes to it;
3. a failure in a source a metric does not read is still reported (strict FAIL, "outside scope, still in source")
   without blocking that metric's source prerequisite (D-069) — never as resolved;
4. failures in a metric's own source / fields still block it;
5. missing dates, totals, mappings and definitions stay blocking; full declarations still leave definitions,
   blockers and release prerequisites open;
6. evidence from another generation, unbound or missing evidence is refused (reported unavailable);
7. nothing is promoted or written; the register, definitions and GENERATION_BOUND_API_EVIDENCE are unchanged;
8. no customer / product identity, transaction or amount appears in the diagnostic output.
Synthetic workbooks only. No AI, no network.
"""
import dataclasses
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from scripts import reconcile_sources as RS
from services import accuracy as ACC
from services import generation as GEN
from services import metric_diagnostics as MD
from services import refresh as RF
from tests.fixtures import synthetic_erp as SE
from tests.fixtures.xlsx_synth import E
from tests.test_m8_accuracy2 import TODAY, clean_folder, promoted, selection, stock_rows, write_stock  # noqa: F401

pytestmark = pytest.mark.production_gate
KEYS = MD.CANDIDATES


# ---------------------------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------------------------
def folder_clean(tmp_path, name="clean"):
    return clean_folder(tmp_path / name)


def folder_sales_duplicate(tmp_path):
    """A strict FAIL in sales only (an identical sales line) — a source none of the candidates reads."""
    d = clean_folder(tmp_path / "salesdup")
    rows = SE.sales_rows()
    pd.DataFrame(rows + [dict(rows[0])]).to_excel(os.path.join(d, SE.SALES_FILE), index=False)
    return d


def folder_aging_unmatched(tmp_path):
    """A strict Cari join FAIL (an account that is not a sales invoice account) — outside Tahsilat's declared scope."""
    d = clean_folder(tmp_path / "agingjoin")
    a = SE.aging_frame()
    extra = {c: 0 for c in SE.AGING_COLUMNS}
    extra.update({"Hesap kodu": "320.00.0999", "Hesap adı": "Sentetik Tedarikçi", "Temsilci kodu": "TEMSILCI-A"})
    pd.concat([a, pd.DataFrame([extra])], ignore_index=True).to_excel(os.path.join(d, SE.AGING_FILE), index=False)
    return d


def folder_stock_broken(tmp_path, *, unchanged_from=None):
    """The candidates' OWN source fails: a duplicated product code and an Excel error in the coverage field."""
    d = clean_folder(tmp_path / "stockbad")
    if unchanged_from is not None:
        # Fingerprints identify exact XLSX bytes, including ZIP/core-property timestamps.
        # Rewriting identical cells is not an unchanged export; retain the original synthetic bytes.
        for name in (SE.SALES_FILE, SE.AGING_FILE):
            (Path(d) / name).write_bytes((Path(unchanged_from) / name).read_bytes())
    rows = stock_rows()
    rows[0][3] = E("#DIV/0!")
    rows.append(list(rows[1]))
    write_stock(d, rows)
    return d


def manifest_of(folder, today=TODAY):
    paths, decl, origin = GEN.select_core(folder)
    frozen = GEN.freeze(paths)
    v = RF.validate(paths, None, today, frozen=frozen)
    assert v["ok"], v["errors"]
    m = GEN.build_manifest(frozen, v["frames"], decl, origin, today)
    served = {k: GEN.sha256_bytes(fs.data) for k, fs in frozen.items()}
    served["liste"] = served["sales"]
    return m, served


def run_cli(folder, *extra, capsys):
    code = RS.main(["--core", str(folder), "--today", TODAY.isoformat(), *extra])
    return code, capsys.readouterr().out


def diag_of(folder, today=TODAY):
    m, served = manifest_of(folder, today)
    return m, served, MD.diagnose(m, served, today, strict_overall="OPEN", strict_sources={})


# ---------------------------------------------------------------------------------------------
# 1. the strict result is unchanged
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("make", [folder_clean, folder_sales_duplicate, folder_aging_unmatched, folder_stock_broken])
def test_strict_result_and_exit_code_are_unchanged(tmp_path, capsys, make):
    d = make(tmp_path)
    code0, out0 = run_cli(d, "--evidence-out", str(tmp_path / "e0.json"), capsys=capsys)
    code1, out1 = run_cli(d, "--metric-diagnostics", "--evidence-out", str(tmp_path / "e1.json"), capsys=capsys)
    assert code0 == code1 and code0 in (1, 3)
    head0 = out0.split("Kanıt dosyası yazıldı")[0]
    head1 = out1.split("METRİK TANILAMA")[0]
    assert head1 == head0 + "\n"                               # every strict line, counts, GENEL SONUÇ (+ a blank line)
    e0, e1 = (json.loads((tmp_path / f).read_text(encoding="utf-8")) for f in ("e0.json", "e1.json"))
    assert "metric_diagnostics" not in e0 and e1["metric_diagnostics"]["available"] is True
    for k in ("results", "overall", "failed", "open", "core"):
        assert e0[k] == e1[k], k
    assert e1["metric_diagnostics"]["strict"]["overall"] == e1["overall"]


def test_without_core_the_option_changes_nothing(tmp_path, capsys):
    from tests.fixtures import kredi_takip_synth as KS
    p = KS.book(tmp_path)
    c0 = RS.main(["--kredi", str(p), "--today", TODAY.isoformat()])
    o0 = capsys.readouterr().out
    c1 = RS.main(["--kredi", str(p), "--today", TODAY.isoformat(), "--metric-diagnostics"])
    o1 = capsys.readouterr().out
    assert c0 == c1 and o1.startswith(o0) and "yalnızca --core ile" in o1


# ---------------------------------------------------------------------------------------------
# 2. diagnostic states are exactly the production gate's decisions
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("make", [folder_clean, folder_sales_duplicate, folder_aging_unmatched, folder_stock_broken])
def test_states_match_the_production_gate(tmp_path, make):
    m, served, d = diag_of(make(tmp_path))
    gate = ACC.current_gate(TODAY, m.bound_snapshot(served), m.generation_id)
    for k in KEYS:
        dm = d["metrics"][k]
        assert dm["gate_state"] == gate.decide(k).state.value and dm["gate_codes"] == list(gate.decide(k).codes)
        assert dm["breakdown_matches_gate"] is True
        assert dm["certification_permitted"] is False and dm["gate_state"] != "verified"
        cats = [c for s in dm["source_judgement"].values() for p in s["problems"] for c in p["categories"]]
        cats += [c for b in dm["open_blockers"] for c in b["categories"]]
        assert MD.UNCLASSIFIED not in cats


def test_a_gate_verified_synthetic_metric_is_still_not_certifiable(tmp_path, promoted):  # noqa: F811
    # a SYNTHETIC register copy certifies stock.reorder; with full declarations its gate decision may be VERIFIED —
    # the release prerequisites (flag off and app checks) still refuse certification under Option B
    d = folder_clean(tmp_path)
    selection(d)
    m, served, diag = diag_of(d)
    dm = diag["metrics"]["stock.reorder"]
    assert dm["gate_state"] == ACC.current_gate(TODAY, m.bound_snapshot(served), m.generation_id).decide(
        "stock.reorder").state.value
    assert dm["certification_permitted"] is False
    unmet = {p["code"] for p in dm["release_prerequisites"] if p["met"] is not True}
    assert {"generation_bound_api_evidence", "app_refresh_and_restart_binding"} <= unmet
    prereq = {p["code"]: p["met"] for p in dm["release_prerequisites"]}
    assert "kredi_reconciliation_not_fail" not in prereq
    assert prereq["metric_source_dependencies_clear"] is True
    assert "EVET" not in "\n".join(MD.render_tr(diag))


# ---------------------------------------------------------------------------------------------
# 3. an unrelated source's failure is never treated as resolved
# ---------------------------------------------------------------------------------------------
def test_unrelated_strict_failure_stays_visible_but_does_not_block_unaffected_metric(tmp_path, capsys):
    d = folder_sales_duplicate(tmp_path)
    code, out = run_cli(d, "--metric-diagnostics", capsys=capsys)
    assert code == 1 and "GENEL SONUÇ: FAIL" in out
    m, served = manifest_of(d)
    diag = MD.diagnose(m, served, TODAY, strict_overall="FAIL", strict_sources={"sales": "FAIL"})
    baseline = diag_of(folder_clean(tmp_path))[2]
    for k in KEYS:
        pre = {p["code"]: p["met"] for p in diag["metrics"][k]["release_prerequisites"]}
        base_pre = {p["code"]: p["met"] for p in baseline["metrics"][k]["release_prerequisites"]}
        # Both still have missing date/totals/owner declarations; unrelated sales FAIL adds no new metric blocker.
        assert pre["metric_source_dependencies_clear"] == base_pre["metric_source_dependencies_clear"]
        assert "strict_core_reconciliation_not_fail" not in pre
    assert "sales=FAIL" in "\n".join(MD.render_tr(diag))
    assert "sales=FAIL" in out.split("METRİK TANILAMA")[1]


def test_out_of_scope_problem_is_listed_as_still_in_the_source(tmp_path):
    clean = diag_of(folder_clean(tmp_path))[2]["metrics"]["receivables.collections"]
    m, served, diag = diag_of(folder_aging_unmatched(tmp_path))
    dm = diag["metrics"]["receivables.collections"]
    aging = dm["source_judgement"]["aging"]
    outside = [p["text"] for p in aging["strict_only_still_in_source"]]
    assert "Müşteri veya ürün eşleşmesi hatalı." in outside
    assert all(p["applies"] is False for p in aging["strict_only_still_in_source"])
    assert {p["why"] for p in aging["strict_only_still_in_source"]} == {"rejudged_for_used_fields_and_joins"}
    assert m.record("aging").evidence.join_unmatched                 # the source itself still carries it
    assert dm["gate_state"] == clean["gate_state"]                   # the scope does not read the join …
    assert "katı değerlendirmede, kaynakta HÂLÂ VAR (çözülmedi" in "\n".join(MD.render_tr(diag))   # … and it is shown as unresolved


# ---------------------------------------------------------------------------------------------
# 4. the metric's own source still blocks it
# ---------------------------------------------------------------------------------------------
def test_own_source_failures_block_the_stock_metrics(tmp_path):
    _, _, diag = diag_of(folder_stock_broken(tmp_path))
    for k in ("stock.reorder", "stock.overstock"):
        dm = diag["metrics"][k]
        texts = [p["text"] for p in dm["source_judgement"]["stock"]["problems"] if p["applies"]]
        assert dm["gate_state"] == "invalid"
        assert "Kaynakta yinelenen kayıtlar var." in texts
        assert "Kullanılan alanda hatalı Excel formülleri var: STOK Kontrol %." in texts
    pc = diag["metrics"]["stock.product_counts"]
    assert pc["gate_state"] == "invalid"                             # duplicate code = source integrity
    assert "Kaynakta yinelenen kayıtlar var." in [p["text"] for p in pc["source_judgement"]["stock"]["problems"]]


# ---------------------------------------------------------------------------------------------
# 5. dates, totals, mappings and definitions stay blocking
# ---------------------------------------------------------------------------------------------
def test_missing_dates_totals_mapping_and_definitions_block(tmp_path):
    _, _, diag = diag_of(folder_clean(tmp_path))
    for k in KEYS:
        dm = diag["metrics"][k]
        texts = {p["text"] for s in dm["source_judgement"].values() for p in s["problems"]}
        assert {"Kaynak raporun tarihi bilinmiyor.", "Kaynak toplamlarının mutabakatı yapılmadı.",
                "Kaynak eşlemesinin onayı bilinmiyor."} <= texts
        assert "operational_freshness" in {x["key"] for x in dm["unapproved_definitions"]}
        assert dm["gate_state"] != "verified" and dm["certification_permitted"] is False


def test_full_declarations_still_leave_definitions_blockers_and_release_open(tmp_path):
    d = folder_clean(tmp_path)
    selection(d)                                               # stock: date, period, totals and mapping declared
    _, _, diag = diag_of(d)
    dm = diag["metrics"]["stock.reorder"]
    texts = {p["text"] for p in dm["source_judgement"]["stock"]["problems"]}
    assert "Kaynak raporun tarihi bilinmiyor." not in texts and "Kaynak eşlemesinin onayı bilinmiyor." not in texts
    assert {b["code"] for b in dm["open_blockers"]} >= {"DQ-4", "STOCK-DATE", "STOCK-REPORT-SCOPE", "REAL-DATA"}
    assert {x["key"] for x in dm["unapproved_definitions"]} == {"stock_coverage_rule", "operational_freshness"}
    assert dm["gate_state"] == "not_verified" and dm["certification_permitted"] is False


# ---------------------------------------------------------------------------------------------
# 6. mixed-generation, unbound and missing evidence are refused
# ---------------------------------------------------------------------------------------------
def test_evidence_of_another_generation_is_refused(tmp_path, promoted):  # noqa: F811
    a = folder_clean(tmp_path, "a")
    selection(a)
    b = folder_stock_broken(tmp_path, unchanged_from=a)
    ma, sa = manifest_of(a)
    mb, sb = manifest_of(b)
    assert {k for k in sa if sa[k] != sb[k]} == {"stock"}
    # (a) only Stock was replaced; the other synthetic source files were preserved byte-for-byte.
    diag = MD.diagnose(ma, sb, TODAY, strict_overall="OPEN")
    assert diag["generation"]["unbound_sources"] == ["stock"]
    for k in ("stock.reorder", "stock.overstock", "stock.product_counts"):
        dm = diag["metrics"][k]
        s = dm["source_judgement"]["stock"]
        assert s["available"] is False and s["reason"] == "EVIDENCE-GENERATION"
        assert dm["gate_state"] != "verified" and dm["certification_permitted"] is False
        assert any(c.startswith("EVIDENCE-GENERATION") for c in dm["gate_codes"])
    # (b) a caller claiming another generation: nothing binds at all
    diag = MD.diagnose(ma, sa, TODAY, strict_overall="OPEN", expected_generation=mb.generation_id)
    for k in KEYS:
        dm = diag["metrics"][k]
        for s in dm["source_judgement"].values():
            assert s["available"] is False and s["reason"] == "EVIDENCE-GENERATION"
        assert dm["gate_state"] != "verified" and dm["certification_permitted"] is False
    g = MD.diagnose(ma, sa, TODAY, strict_overall="OPEN", expected_generation=mb.generation_id)["generation"]
    assert g["generation_id_matches"] is False and g["core_evidence_complete_and_bound"] is False
    assert g["bound_sources"] == [] and g["unbound_sources"] == ["aging", "liste", "sales", "stock"]


def test_missing_evidence_is_unavailable_never_passing(tmp_path, promoted):  # noqa: F811
    d = folder_clean(tmp_path)
    selection(d)
    m, served = manifest_of(d)
    core = tuple(dataclasses.replace(r, evidence=None) if r.key == "stock" else r for r in m.core)
    m2 = dataclasses.replace(m, core=core)
    diag = MD.diagnose(m2, served, TODAY, strict_overall="OPEN")
    for k in ("stock.reorder", "stock.overstock", "stock.product_counts"):
        s = diag["metrics"][k]["source_judgement"]["stock"]
        assert s["available"] is False and s["reason"] == "NO-EVIDENCE"
        assert diag["metrics"][k]["gate_state"] == "not_verified"
        assert MD.UNAVAILABLE in s["problems"][0]["categories"]


# ---------------------------------------------------------------------------------------------
# 6b. R3-1: a matching generation ID is never reported as a complete, fully bound source set
# ---------------------------------------------------------------------------------------------
def _summary_line(diag):
    return next(x for x in MD.render_tr(diag) if x.startswith("  Üretim:"))


def _gate_unchanged(m, served, diag):
    gate = ACC.current_gate(TODAY, m.bound_snapshot(served), m.generation_id)
    for k in KEYS:
        assert diag["metrics"][k]["gate_state"] == gate.decide(k).state.value
        assert diag["metrics"][k]["gate_codes"] == list(gate.decide(k).codes)


def test_clean_generation_is_complete_and_bound(tmp_path):
    m, served, diag = diag_of(folder_clean(tmp_path))
    g = diag["generation"]
    assert g["generation_id_matches"] is True and g["core_evidence_complete_and_bound"] is True
    assert g["bound_sources"] == ["aging", "liste", "sales", "stock"]
    assert g["unbound_sources"] == [] and g["missing_evidence_sources"] == []
    assert "eksiksiz ve bağlı: evet" in _summary_line(diag)


def test_unbound_source_with_matching_generation_id_is_not_complete(tmp_path, promoted):  # noqa: F811
    a = folder_clean(tmp_path, "a")
    selection(a)
    ma, sa = manifest_of(a)
    _, sb = manifest_of(folder_stock_broken(tmp_path, unchanged_from=a))
    assert {k for k in sa if sa[k] != sb[k]} == {"stock"}
    diag = MD.diagnose(ma, sb, TODAY, strict_overall="OPEN")
    g = diag["generation"]
    assert g["generation_id_matches"] is True                          # the ID alone still matches …
    assert g["core_evidence_complete_and_bound"] is False              # … but the source set is not complete
    assert g["unbound_sources"] == ["stock"] and "stock" not in g["bound_sources"]
    line = _summary_line(diag)
    assert "eksiksiz ve bağlı: HAYIR" in line and "BAĞLANAMAYAN: stock" in line
    assert "eksiksiz ve bağlı: evet" not in line and "tutarlı: evet" not in line
    _gate_unchanged(ma, sb, diag)                                      # per-metric fail-closed logic unchanged
    for k in ("stock.reorder", "stock.overstock", "stock.product_counts"):
        assert diag["metrics"][k]["source_judgement"]["stock"]["available"] is False
        assert diag["metrics"][k]["gate_state"] != "verified"


@pytest.mark.parametrize("how", ["evidence_none", "record_absent"])
def test_missing_source_evidence_with_matching_generation_id_is_not_complete(tmp_path, promoted, how):  # noqa: F811
    d = folder_clean(tmp_path)
    selection(d)
    m, served = manifest_of(d)
    core = (tuple(dataclasses.replace(r, evidence=None) if r.key == "stock" else r for r in m.core)
            if how == "evidence_none" else tuple(r for r in m.core if r.key != "stock"))
    m2 = dataclasses.replace(m, core=core)
    diag = MD.diagnose(m2, served, TODAY, strict_overall="OPEN")
    g = diag["generation"]
    assert g["generation_id_matches"] is True and g["core_evidence_complete_and_bound"] is False
    assert g["missing_evidence_sources"] == ["stock"] and g["unbound_sources"] == []
    line = _summary_line(diag)
    assert "eksiksiz ve bağlı: HAYIR" in line and "KANITI EKSİK: stock" in line
    assert "eksiksiz ve bağlı: evet" not in line
    _gate_unchanged(m2, served, diag)
    for k in ("stock.reorder", "stock.overstock", "stock.product_counts"):
        s = diag["metrics"][k]["source_judgement"]["stock"]
        assert s["available"] is False and s["reason"] == "NO-EVIDENCE"
        assert diag["metrics"][k]["gate_state"] != "verified"


# ---------------------------------------------------------------------------------------------
# 7. nothing is promoted or written
# ---------------------------------------------------------------------------------------------
def _snapshot_tree(folder):
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns, GEN.sha256_file(str(p)))
            for p in Path(folder).iterdir() if p.is_file()}


def test_nothing_is_promoted_or_written(tmp_path, capsys):
    d = folder_aging_unmatched(tmp_path)
    reg_before = dict(ACC.REGISTER)
    defs_before = dict(ACC.DEFINITIONS)
    tree_before = _snapshot_tree(d)
    run_cli(d, "--metric-diagnostics", capsys=capsys)
    assert _snapshot_tree(d) == tree_before and not list(Path(d).glob("*.json")) and not list(Path(d).glob("*.db"))
    assert not (Path(d) / "reconciliation").exists()
    assert ACC.REGISTER == reg_before and ACC.DEFINITIONS == defs_before
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    ACC.validate_register()
    assert all(m.certified != ACC.State.VERIFIED for m in ACC.REGISTER.values())


# ---------------------------------------------------------------------------------------------
# 8. privacy
# ---------------------------------------------------------------------------------------------
def test_no_identity_transaction_or_amount_leaks(tmp_path, capsys):
    d = folder_stock_broken(tmp_path)
    _, out = run_cli(d, "--metric-diagnostics", "--evidence-out", str(tmp_path / "e.json"), capsys=capsys)
    diag_text = out.split("METRİK TANILAMA")[1] + json.dumps(
        json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))["metric_diagnostics"], ensure_ascii=False)
    secrets = set()
    for r in SE.sales_rows():
        secrets |= {str(r[k]) for k in ("Musterikod", "Musteri_ismi", "Pro_kodu", "ProjeIsmi", "sto_kod", "StokIsmi")
                    if r[k]}
        secrets.add(str(int(r["Nettutar"])))
    for row in SE.stock_frame().itertuples(index=False):
        secrets |= {row[0], row[1], str(row[6])}
    for row in SE.aging_frame().itertuples(index=False):
        secrets |= {str(row[0]), str(row[1])}
    secrets |= {"TEMSILCI-A", "TEMSILCI-B", "SP-900", "Sentetik Kalem", "999999", "50000", str(tmp_path)}
    schema = " ".join(h for spec in __import__("services.source_validation", fromlist=["CORE"]).CORE.values()
                      for h in spec["required"])
    leaked = sorted(s for s in secrets if s and len(s) >= 4 and s in diag_text and s not in schema)
    assert leaked == []


# M8-R4 D-069: Option B never erases strict findings and never certifies an ambiguous own source.
def test_option_b_own_source_error_and_missing_declarations_still_block(tmp_path):
    _, _, diag = diag_of(folder_stock_broken(tmp_path))
    for key in ("stock.reorder", "stock.overstock", "stock.product_counts"):
        pre = {p["code"]: p["met"] for p in diag["metrics"][key]["release_prerequisites"]}
        assert pre["metric_source_dependencies_clear"] is False
        assert diag["metrics"][key]["certification_permitted"] is False
    _, _, clean = diag_of(folder_clean(tmp_path))
    for key in KEYS:
        pre = {p["code"]: p["met"] for p in clean["metrics"][key]["release_prerequisites"]}
        assert pre["metric_source_dependencies_clear"] is False  # no approved date / totals / mapping


def test_option_b_unrelated_kredi_does_not_gate_stock_or_collections(tmp_path):
    _, _, diag = diag_of(folder_sales_duplicate(tmp_path))
    for key in KEYS:
        codes = {p["code"] for p in diag["metrics"][key]["release_prerequisites"]}
        assert "kredi_reconciliation_not_fail" not in codes
        assert "strict_core_reconciliation_not_fail" not in codes
        assert diag["metrics"][key]["certification_permitted"] is False
    assert diag["strict"]["overall"] == "OPEN"  # passed to this read-only helper


def test_option_b_generation_mismatch_never_clears_prerequisite(tmp_path):
    folder = folder_clean(tmp_path)
    manifest, served = manifest_of(folder)
    diag = MD.diagnose(manifest, served, TODAY, strict_overall="FAIL",
                       expected_generation="wrong-generation")
    for key in KEYS:
        pre = {p["code"]: p["met"] for p in diag["metrics"][key]["release_prerequisites"]}
        assert pre["metric_source_dependencies_clear"] is False
        assert diag["metrics"][key]["certification_permitted"] is False


def test_option_b_clean_stock_is_ready_despite_unrelated_sales_fail(tmp_path, promoted):  # noqa: F811
    folder = folder_sales_duplicate(tmp_path)
    selection(folder)
    manifest, served = manifest_of(folder)
    diag = MD.diagnose(manifest, served, TODAY, strict_overall="FAIL", strict_sources={"sales": "FAIL"})
    metric = diag["metrics"]["stock.reorder"]
    prereq = {p["code"]: p["met"] for p in metric["release_prerequisites"]}
    assert prereq["metric_source_dependencies_clear"] is True
    assert metric["gate_state"] == "verified"  # synthetic register only; production register is never promoted
    assert metric["certification_permitted"] is False
    assert diag["strict"] == {"overall": "FAIL", "sources": {"sales": "FAIL"}}


def test_option_b_unused_stock_field_failure_does_not_block_used_fields(tmp_path):
    folder = folder_clean(tmp_path)
    rows = stock_rows()
    rows[0][6] = E("#REF!")  # inventory value is used by overstock, but not reorder or product counts
    write_stock(folder, rows)
    selection(folder)
    manifest, served = manifest_of(folder)
    diag = MD.diagnose(manifest, served, TODAY, strict_overall="FAIL", strict_sources={"stock": "FAIL"})
    for key in ("stock.reorder", "stock.product_counts"):
        metric = diag["metrics"][key]
        prereq = {p["code"]: p["met"] for p in metric["release_prerequisites"]}
        assert prereq["metric_source_dependencies_clear"] is True
        assert metric["source_judgement"]["stock"]["strict_only_still_in_source"]
    metric = diag["metrics"]["stock.overstock"]
    assert metric["gate_state"] == "invalid"
    assert next(p["met"] for p in metric["release_prerequisites"]
                if p["code"] == "metric_source_dependencies_clear") is False
    assert all(not m["certification_permitted"] for m in diag["metrics"].values())


def test_option_b_withheld_metric_dependency_blocks_readiness(tmp_path, promoted, monkeypatch):  # noqa: F811
    folder = folder_clean(tmp_path)
    selection(folder)
    reg = dict(promoted)
    reg["stock.reorder"] = dataclasses.replace(reg["stock.reorder"], depends_on=("stock.overstock",))
    monkeypatch.setattr(ACC, "REGISTER", reg)
    metric = diag_of(folder)[2]["metrics"]["stock.reorder"]
    assert metric["source_evidence_state_for_this_metric"] == "verified"
    assert metric["dependencies_withheld"] == [{"key": "stock.overstock", "state": "not_verified"}]
    assert next(p["met"] for p in metric["release_prerequisites"]
                if p["code"] == "metric_source_dependencies_clear") is False
    assert metric["certification_permitted"] is False


def test_option_b_kredi_metric_retains_its_own_reconciliation_prerequisite(tmp_path):
    manifest, served = manifest_of(folder_clean(tmp_path))
    metric = MD.diagnose(manifest, served, TODAY, strict_overall="FAIL", keys=("owner.kredi",))[
        "metrics"]["owner.kredi"]
    prereq = {p["code"]: p["met"] for p in metric["release_prerequisites"]}
    assert prereq["kredi_reconciliation_not_fail"] is None  # absent Kredi evidence is never a pass
    assert prereq["metric_source_dependencies_clear"] is False
    assert metric["certification_permitted"] is False
