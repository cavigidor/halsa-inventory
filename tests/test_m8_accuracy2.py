"""M8-ACCURACY-2 — shared generation evidence, workbook fingerprints, formula-aware core validation, joins, dates.

REAL production gate (marker `production_gate`); never the algorithm-only harness. A metric is promoted to VERIFIED
ONLY inside a test, on a synthetic register copy, to prove the transitions; the production register still certifies
nothing. Synthetic workbooks only; no network, no key, no AI call.
"""
import contextlib
import dataclasses
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as db
from services import accuracy as ACC
from tests import release_state as REL
from services import generation as GEN
from services import refresh as RF
from services import source_validation as SV
from services import store as ST
from services.entity import ContextRepository
from services.importers.excel import WorkbookPair
from tests.fixtures import synthetic_erp as SE
from tests.fixtures.xlsx_synth import E, F, MISSING, write_xlsx

pytestmark = pytest.mark.production_gate
ROOT = Path(__file__).resolve().parents[1]
# The API gate judges operational freshness against the real date (as in production), so the synthetic stock snapshot
# is declared relative to it; nothing here reads a file timestamp.
TODAY = dt.date.today()
SNAP = TODAY - dt.timedelta(days=3)
S, NV, INV = ACC.State.VERIFIED, ACC.State.NOT_VERIFIED, ACC.State.INVALID
STOCK_HEADERS = ["Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %", "minseviye", "maxseviye",
                 "Envanter değeri", "DURUM"]


# ---------------------------------------------------------------------------------------------
# a CLEAN synthetic core set (every sold product exists in stock) + an explicit selection
# ---------------------------------------------------------------------------------------------
def stock_rows(extra=None):
    rows = [list(r) for r in SE.stock_frame().itertuples(index=False)]
    rows.append(["SP-900", "Sentetik Kalem", 40, 0.4, 1.0, 3.0, 2_000, ""])         # sold in the synthetic sales
    return rows + (extra or [])


def write_stock(folder, rows=None, *, name=SE.STOCK_FILE, extra_sheet=False):
    data = [["Sentetik stok raporu"], ["(test)"], [None], STOCK_HEADERS] + (rows or stock_rows())
    Path(folder).mkdir(parents=True, exist_ok=True)
    sheets = {"Sheet1": data}
    if extra_sheet:
        sheets["Not"] = [["revizyon"]]
    return write_xlsx(Path(folder) / name, sheets)


def clean_folder(tmp_path):
    d = SE.write_all(str(tmp_path / "data"))
    write_stock(d)
    return d


def selection(folder, *, stock_date=SNAP, complete=True, approve=True, extra=None):
    sha = GEN.sha256_file(os.path.join(folder, SE.STOCK_FILE))
    stock = {"file": SE.STOCK_FILE, "snapshot_date": stock_date.isoformat() if stock_date else None,
             "period_complete": complete}
    if approve:
        stock["approval"] = {"sha256": sha, "decision_ref": "D-999", "totals_consistent": True,
                             "mapping_approved": True}
    core = {"stock": stock, "sales": {"file": SE.SALES_FILE, "report_date": SE.AS_OF.isoformat()},
            "aging": {"file": SE.AGING_FILE, "snapshot_date": SE.AS_OF.isoformat()}}
    core.update(extra or {})
    with open(os.path.join(folder, GEN.SELECTION_FILE), "w", encoding="utf-8") as fh:
        json.dump({"schema": GEN.SELECTION_SCHEMA, "core": core}, fh)
    return sha


@pytest.fixture
def promoted(monkeypatch):
    """SYNTHETIC ceilings for source/generation tests; no production metric is promoted.
    Product count supplies an independent rendered value now that reorder calculation is retired.
    """
    reg = dict(ACC.REGISTER)
    reg["stock.reorder"] = dataclasses.replace(reg["stock.reorder"], blockers=(), definitions=(), certified=S,
                                               approval_refs=("D-999",), regression_tests=("tests/test_m8_accuracy2.py",),
                                               reconciliation_ref="synthetic")
    reg["stock.product_counts"] = dataclasses.replace(reg["stock.product_counts"], blockers=(), definitions=(),
        certified=S, approval_refs=("D-999",), regression_tests=("tests/test_m8_accuracy2.py",),
        reconciliation_ref="synthetic")
    ACC.validate_register(reg, production=False)
    with pytest.raises(ACC.RegisterError):
        ACC.validate_register(reg, production=True)             # the production rules still refuse it
    monkeypatch.setattr(ACC, "REGISTER", reg)
    return reg


def refresh(folder, dash, install=None, today=TODAY, **kw):
    return RF.run_refresh(folder, dash, inflation_rate=0.40, today=today, install=install, **kw)


def blob(path):
    html = open(path, encoding="utf-8").read()
    import re
    return json.loads(re.search(r"const DATA=(\{.*?\});\n", html, re.S).group(1).replace("<\\/", "</"))


class World:
    """A repo + generation store wired like the app, and a TestClient on the production app."""

    def __init__(self, folder, dash, tmp_path):
        self.folder, self.dash = folder, dash
        self.store = GEN.GenerationStore()
        self.repo = ContextRepository(data_folder=folder, inflation_rate=0.40, generation_store=self.store)

    def install(self, staged):
        prev = self.repo.current()
        self.repo.replace_context(staged)
        try:
            self.store.install(staged.generation_manifest)
        except Exception:
            if prev is not None:
                self.repo.replace_context(prev)
            raise

    @contextlib.contextmanager
    def client(self):
        APP.app.dependency_overrides[APP.get_repo] = lambda: self.repo
        with TestClient(APP.app, headers={"X-StockAgent": "1"}) as c:
            APP.app.state.generations = self.store          # after startup, which installs its own store
            yield c


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "s.db"))
    ST.init_db()
    folder = clean_folder(tmp_path)
    w = World(folder, str(tmp_path / "dashboard.html"), tmp_path)
    yield w
    APP.app.dependency_overrides.clear()
    APP.app.state.generations = None


def api_state(w, key="stock.reorder"):
    with w.client() as c:
        j = c.get("/api/accuracy/status").json()
    return {m["key"]: m for m in j["metrics"]}[key], j


# ---------------------------------------------------------------------------------------------
# 1 + 2 + 3 + 5: unverified → verified when all evidence passes; dashboard and API agree; core refresh
# ---------------------------------------------------------------------------------------------
def test_unverified_to_verified_and_dashboard_api_agree(world, promoted):
    selection(world.folder)
    rep = refresh(world.folder, world.dash, install=world.install)
    assert rep["ok"], rep
    m = world.store.current()
    assert m is not None and m.selection_origin == "explicit"
    assert GEN.dashboard_generation(world.dash) == m.core_id
    stock = m.record("stock")
    assert stock.sha256 == GEN.sha256_file(os.path.join(world.folder, SE.STOCK_FILE))
    assert stock.observed["approval_status"] == "applied"
    assert stock.evidence.problems() == []
    rows = blob(world.dash)["reorder"]
    assert rows["rows"] == [] and "withheld" not in rows
    assert f" · {len(stock_rows()):,} ürün</div>" in Path(world.dash).read_text()
    st, j = api_state(world)
    assert st["state"] == "verified", st
    assert j["generation_id"] == m.generation_id      # …and the API agrees
    for k in ("finance.margin", "customers.winback", "owner.kredi"):
        assert {x["key"]: x for x in j["metrics"]}[k]["state"] != "verified"
    assert next(s for s in j["sources"] if s["key"] == "stock")["bound"] is True


def test_verified_to_unverified_after_source_change(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    old = world.store.current()
    write_stock(world.folder, stock_rows([["SP-777", "Sentetik Yeni", 5, 0.2, 1.0, 3.0, 50, ""]]))
    assert refresh(world.folder, world.dash, install=world.install)["ok"]    # the approval named the OLD bytes
    new = world.store.current()
    assert new.core_id != old.core_id
    assert new.record("stock").observed["approval_status"] == "fingerprint_mismatch"
    assert blob(world.dash)["reorder"]["rows"] == []
    st, _ = api_state(world)
    assert st["state"] == "not_verified"


# ---------------------------------------------------------------------------------------------
# 6 + 7 + 14: partial failure, rollback and validation failure keep the previous generation and evidence
# ---------------------------------------------------------------------------------------------
def _snapshot_files(w):
    mp = GEN.manifest_path(w.dash)
    return open(w.dash, encoding="utf-8").read(), open(mp, encoding="utf-8").read()


def test_build_failure_keeps_previous_generation(world, promoted, monkeypatch):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    before, gen = _snapshot_files(world), world.store.current()
    write_stock(world.folder, stock_rows([["SP-778", "Sentetik", 1, 0.1, 1.0, 3.0, 5, ""]]))
    monkeypatch.setattr(db, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic")))
    rep = refresh(world.folder, world.dash, install=world.install)
    assert not rep["ok"] and rep["step"] == "build" and rep["previous_data_kept"]
    assert _snapshot_files(world) == before and world.store.current() is gen


def test_install_failure_rolls_back_dashboard_manifest_context_and_store(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    before, gen, ctx = _snapshot_files(world), world.store.current(), world.repo.current()
    write_stock(world.folder, stock_rows([["SP-779", "Sentetik", 1, 0.1, 1.0, 3.0, 5, ""]]))

    def failing(staged):
        world.repo.replace_context(staged)
        raise RuntimeError("synthetic install failure")
    rep = refresh(world.folder, world.dash, install=lambda s: (failing(s) if True else None))
    assert not rep["ok"] and rep["step"] == "swap"
    assert _snapshot_files(world) == before                     # dashboard AND manifest file restored together
    world.repo.replace_context(ctx)                              # (the app's installer puts the context back itself)
    assert world.store.current() is gen


def test_app_installer_restores_previous_context_when_the_store_fails(world, monkeypatch):
    APP.app.state.generations = world.store
    first = {"actions": [], "x": 1}
    world.repo.replace_context(first)
    monkeypatch.setattr(world.store, "install", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    from services.context import AgentContext
    staged = AgentContext({"actions": []})
    with pytest.raises(RuntimeError):
        APP._install_generation(world.repo, staged)
    assert world.repo.current() is first


def test_source_validation_failure_publishes_nothing(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    before, gen = _snapshot_files(world), world.store.current()
    data = [["t"], ["t"], [None], [h for h in STOCK_HEADERS if h != "minseviye"]] + [r[:4] + r[5:] for r in stock_rows()]
    write_xlsx(Path(world.folder) / SE.STOCK_FILE, {"Sheet1": data})
    rep = refresh(world.folder, world.dash, install=world.install)
    assert not rep["ok"] and rep["step"] == "validate"
    assert _snapshot_files(world) == before and world.store.current() is gen


def test_invalid_selection_file_fails_closed(world):
    with open(os.path.join(world.folder, GEN.SELECTION_FILE), "w") as fh:
        json.dump({"schema": GEN.SELECTION_SCHEMA, "core": {"stock": {"file": "../x.xlsx"}}}, fh)
    rep = refresh(world.folder, world.dash, install=world.install)
    assert not rep["ok"] and rep["step"] == "validate" and "Kaynak seçimi" in rep["message"]
    assert world.store.current() is None


def test_without_selection_file_mtime_choice_is_recorded_and_never_verifies(world, promoted):
    rep = refresh(world.folder, world.dash, install=world.install)
    assert rep["ok"] and any("açıkça seçilmedi" in w for w in rep["warnings"])
    m = world.store.current()
    assert m.selection_origin == "mtime_fallback" and m.record("stock").evidence.selection_explicit is False
    assert blob(world.dash)["reorder"]["rows"] == []


# ---------------------------------------------------------------------------------------------
# 8: restart restores only the exact generation
# ---------------------------------------------------------------------------------------------
def test_restart_restores_only_a_matching_generation(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    published = world.store.current()
    mp = GEN.manifest_path(world.dash)
    restarted = World(world.folder, world.dash, None)
    assert restarted.store.restore(mp, world.dash, world.folder).generation_id == published.generation_id
    ctx = restarted.repo.context()                                 # built from the recorded bytes
    assert ctx.generation["core_id"] == published.core_id
    st, _ = api_state(restarted)
    assert st["state"] == "verified"
    # a source edited after publication → nothing is restored, and the rebuilt context carries no generation
    write_stock(world.folder, stock_rows([["SP-780", "Sentetik", 1, 0.1, 1.0, 3.0, 5, ""]]))
    again = World(world.folder, world.dash, None)
    assert again.store.restore(mp, world.dash, world.folder) is None and again.store.restore_reason == "source_changed"
    assert getattr(again.repo.context(), "generation", None) is None
    st, j = api_state(again)
    assert st["state"] == "not_verified" and j["generation_id"] is None


def test_restart_refuses_a_dashboard_from_another_generation(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    db.build(world.folder, world.dash, today=TODAY)                 # a CLI rebuild without a manifest
    s = GEN.GenerationStore()
    assert s.restore(GEN.manifest_path(world.dash), world.dash, world.folder) is None
    assert s.restore_reason == "dashboard_generation_mismatch"


def test_edited_manifest_is_never_restored(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    mp = GEN.manifest_path(world.dash)
    d = json.load(open(mp, encoding="utf-8"))
    d["core"][0]["evidence"]["error_cells"] = 0
    d["core"][0]["sha256"] = "0" * 64                              # someone edits the evidence file
    json.dump(d, open(mp, "w", encoding="utf-8"))
    assert GEN.read_manifest(mp) is None


# ---------------------------------------------------------------------------------------------
# 9: mixed generations are never combined
# ---------------------------------------------------------------------------------------------
def test_mixed_generation_evidence_is_never_used(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    gen_a, ctx_a = world.store.current(), world.repo.current()
    write_stock(world.folder, stock_rows([["SP-781", "Sentetik", 1, 0.1, 1.0, 3.0, 5, ""]]))
    selection(world.folder)                                          # an approval for the NEW bytes
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    gen_b = world.store.current()
    world.repo.replace_context(ctx_a)                                # serve A's data while B's manifest is published
    st, j = api_state(world)
    assert st["state"] == "not_verified" and "EVIDENCE-GENERATION:stock" in st["codes"]
    assert next(s for s in j["sources"] if s["key"] == "stock")["bound"] is False
    world.store.install(gen_a)                                       # A's manifest with A's data binds again
    assert api_state(world)[0]["state"] == "verified"
    assert gen_a.generation_id != gen_b.generation_id


# ---------------------------------------------------------------------------------------------
# 4: owner workbook reload replaces the owner record; stale owner evidence never binds
# ---------------------------------------------------------------------------------------------
def test_owner_reload_rebinds_only_the_served_revision(tmp_path):
    from services import owner_view as OV
    from tests.fixtures import owner_synth as OS
    d = Path(tmp_path / "owner")
    p = OS.write_kredi(d, TODAY)
    store = GEN.GenerationStore()
    repo = OV.OwnerRepository({"kredi": OV.OwnerSourceConfig("kredi", str(p))}, background=False,
                              on_record=store.replace_owner)
    repo.ensure_loaded()
    rec1 = store.current().record("kredi")
    served = {k: e.get("fingerprint") for k, e in repo.snapshot().items()}
    assert rec1.sha256 == GEN.sha256_file(str(p)) == served["kredi"]
    assert "kredi" in store.current().bound_snapshot(served).evidence
    OS.write_kredi(d, TODAY + dt.timedelta(days=1))                  # a new revision of the workbook
    stale = store.current().bound_snapshot({"kredi": GEN.sha256_file(str(p))})
    assert "kredi" not in stale.evidence and "kredi" in stale.unbound   # before the reload: old evidence never binds
    repo.reload()
    rec2 = store.current().record("kredi")
    assert rec2.sha256 != rec1.sha256
    served = {k: e.get("fingerprint") for k, e in repo.snapshot().items()}
    assert store.current().bound_snapshot(served).evidence["kredi"] is rec2.evidence
    # a core refresh keeps the owner records of their own (still served) revision
    core = GEN.Manifest("now", (GEN.SourceRecord("stock", "core", "stock_snapshot", "current_state_snapshot", "s.xlsx",
                                                 "a" * 64, 1, "explicit"),))
    assert store.install(core).record("kredi") is rec2


def test_failed_owner_reload_withdraws_its_evidence(tmp_path):
    from services import owner_view as OV
    from tests.fixtures import owner_synth as OS
    p = OS.write_kredi(Path(tmp_path / "o"), TODAY)
    store = GEN.GenerationStore()
    repo = OV.OwnerRepository({"kredi": OV.OwnerSourceConfig("kredi", str(p))}, background=False,
                              on_record=store.replace_owner)
    repo.ensure_loaded()
    p.write_bytes(b"not a workbook")
    repo.reload()
    e = repo.snapshot()["kredi"]
    assert e["state"] == "failed" and e["fingerprint"] is None
    rec = store.current().record("kredi")
    assert rec.evidence.inspection_failures and "kredi" not in store.current().bound_snapshot(
        {"kredi": e["fingerprint"]}).evidence


# ---------------------------------------------------------------------------------------------
# 10 + 11 + 12 + 13: fingerprints follow the workbook bytes, not DataFrame strings
# ---------------------------------------------------------------------------------------------
def _old_frame_hash(frame):
    return pd.util.hash_pandas_object(frame.astype(str), index=True).values.tobytes()


def test_changed_formula_with_unchanged_displayed_value(tmp_path):
    a = write_stock(tmp_path / "a", name="STOK a.xlsx")
    rows = stock_rows()
    rows[0][4] = F("0.5*2", 1.0)                                    # same cached value 1.0, now a formula
    b = write_stock(tmp_path / "b", rows, name="STOK b.xlsx")
    fa, fb = db.read_stock(str(a)), db.read_stock(str(b))
    assert fa.equals(fb) and _old_frame_hash(fa) == _old_frame_hash(fb)      # pandas cannot see the difference…
    assert GEN.sha256_file(str(a)) != GEN.sha256_file(str(b))              # …the workbook fingerprint does
    ia, ib = SV.inspect_core("stock", a.read_bytes()), SV.inspect_core("stock", b.read_bytes())
    assert ia.columns["minseviye"]["formula_value"] == 0 and ib.columns["minseviye"]["formula_value"] == 1


def test_changed_cell_type_with_identical_string_representation(tmp_path):
    rows = stock_rows()
    rows[0][1] = 101                                                 # a number…
    a = write_stock(tmp_path / "a", rows, name="STOK a.xlsx")
    rows2 = stock_rows()
    rows2[0][1] = "101"                                              # …vs the same text
    b = write_stock(tmp_path / "b", rows2, name="STOK b.xlsx")
    fa, fb = db.read_stock(str(a)), db.read_stock(str(b))
    assert _old_frame_hash(fa) == _old_frame_hash(fb)
    assert GEN.sha256_file(str(a)) != GEN.sha256_file(str(b))
    assert SV.inspect_core("stock", b.read_bytes()).columns["Stok_Adı"]["text"] > \
        SV.inspect_core("stock", a.read_bytes()).columns["Stok_Adı"]["text"]


def test_missing_cached_formula_results_are_invalid(tmp_path):
    rows = stock_rows()
    rows[1][3] = F("C6/1", MISSING)                                  # coverage formula without a saved result
    rows[2][6] = F("[1]Fiyat!B2*C7", 5.0)                            # an external workbook reference
    p = write_stock(tmp_path, rows)
    ins = SV.inspect_core("stock", p.read_bytes())
    assert ins.missing_cached_formulas == 1 and ins.external_references == 1
    assert db.read_stock(str(p))["STOK Kontrol %"].isna().sum() == 1     # pandas: just a NaN
    frozen = {"stock": GEN.FrozenSource.read("stock", str(p))}
    m = GEN.build_manifest(frozen, {"stock": db.read_stock(str(p))}, {}, "explicit", TODAY)
    probs = dict((why, st) for st, why in m.record("stock").evidence.problems())
    assert probs["Kaynakta hesaplanmamış (önbelleksiz) formüller var."] == INV
    assert probs["Kaynak dış çalışma kitaplarına bağlı."] == INV


def test_changed_workbook_revision_is_a_new_generation(world, promoted):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    g1 = world.store.current()
    write_stock(world.folder, extra_sheet=True)                     # same stock rows; another workbook revision
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    g2 = world.store.current()
    assert g1.core_id != g2.core_id and g2.record("stock").observed["approval_status"] == "fingerprint_mismatch"


def test_excel_errors_zero_and_blank_are_distinguished(tmp_path):
    rows = stock_rows()
    rows[0][6] = 0                                                   # a valid recorded zero
    rows[1][6] = None                                                # a blank amount
    rows[2][3] = E("#DIV/0!")                                        # an Excel error (DQ-4 style)
    rows[3][3] = F("1/0", E("#DIV/0!"))
    p = write_stock(tmp_path, rows)
    ins = SV.inspect_core("stock", p.read_bytes())
    inv = ins.columns["Envanter değeri"]
    assert inv["literal_zero"] == 1 and inv["empty"] == 1
    assert ins.error_cells == 2 and ins.blank_amount_cells == 1 and ins.zero_amount_cells >= 1


def test_scanner_classifies_cells_exactly_like_the_framework(tmp_path):
    rows = [["Kod", "Tutar", "Tarih"], ["K1", 0, dt.date(2026, 1, 2)], ["K2", None, "2026-01-03"],
            ["K3", F("1+1", 2), "bozuk"], ["K4", F("B9", MISSING), dt.date(2026, 1, 4)],
            ["K5", F("1/0", E("#DIV/0!")), None], ["K6", E("#REF!"), 46000], ["K7", F("[1]S!A1", 3), "x"]]
    p = write_xlsx(tmp_path / "k.xlsx", {"S": rows})
    scan = SV.scan_sheet(p.read_bytes(), header_row=1, wanted=("Kod", "Tutar", "Tarih"), date_columns=("Tarih",))
    with WorkbookPair(p) as wb:
        cells = [c for _, row in wb.sheet(0).iter_rows(min_row=2) for c in row[1:2]]
    kinds = {k: sum(1 for c in cells if c.kind.value == k) for k in
             ("literal", "formula_value", "formula_missing_cache", "formula_error", "error")}
    t = scan.columns["Tutar"].counts(scan.data_rows)
    assert (t["literal"], t["formula_value"], t["formula_missing_cache"], t["formula_error"], t["error"]) == (
        kinds["literal"], kinds["formula_value"], kinds["formula_missing_cache"], kinds["formula_error"], kinds["error"])
    assert t["empty"] == 1 and t["literal_zero"] == 1 and t["external_ref"] == 1
    assert scan.columns["Tarih"].counts(scan.data_rows)["invalid_dates"] == 2   # "bozuk" and "x"
    assert sum(c.external_ref for c in cells) == 1


# ---------------------------------------------------------------------------------------------
# 15: reporting dates — declared, never inferred; contradictions invalid; transaction date ≠ report date
# ---------------------------------------------------------------------------------------------
def test_missing_reporting_date_withholds(world, promoted):
    selection(world.folder, stock_date=None)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    st, _ = api_state(world)
    assert st["state"] == "not_verified"
    m = world.store.current()
    assert m.record("stock").evidence.report_date is None
    assert m.record("sales").evidence.latest_transaction_date == SE.AS_OF
    assert m.record("sales").evidence.report_date == SE.AS_OF and m.record("sales").evidence.report_date_origin == "declared"


def test_stale_operational_source_and_incomplete_period_withhold(world, promoted):
    selection(world.folder, stock_date=TODAY - dt.timedelta(days=200))
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    assert api_state(world)[0]["state"] == "not_verified"
    selection(world.folder, complete=None)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    assert api_state(world)[0]["state"] == "not_verified"


def test_declared_dates_contradicting_the_source_are_invalid(tmp_path):
    d = SE.write_all(str(tmp_path / "d"))
    paths = db.select_sources(d)
    frozen = GEN.freeze(paths)
    frames = GEN.frames_from_frozen(frozen)
    decl = {"sales": {"report_date": dt.date(2026, 6, 1), "period_start": dt.date(2021, 1, 1),
                      "period_end": dt.date(2026, 6, 1), "period_complete": True},
            "aging": {"snapshot_date": dt.date(2026, 3, 31)},
            "stock": {"snapshot_date": TODAY + dt.timedelta(days=3)}}
    m = GEN.build_manifest(frozen, frames, decl, "explicit", TODAY)
    for k in ("sales", "aging", "stock"):
        ev = m.record(k).evidence
        assert ev.date_contradictions >= 1, k
        assert (INV, "Beyan edilen tarihler kaynakla çelişiyor.") in ev.problems(), k
    assert m.record("sales").evidence.period_complete is None          # a contradicted declaration is not accepted


def test_no_file_timestamp_is_used_as_a_business_date():
    src = "".join((ROOT / p).read_text() for p in ("services/generation.py", "services/source_validation.py",
                                                    "services/accuracy.py"))
    assert "getmtime" not in src
    m = GEN.FrozenSource.__dataclass_fields__
    assert "file_modified" in m                                            # recorded for information only…
    import inspect
    body = inspect.getsource(GEN._date_checks) + inspect.getsource(GEN.build_core_records)
    assert "file_modified" not in body.split("SourceRecord(")[0]           # …and never read as a date


# ---------------------------------------------------------------------------------------------
# joins (Task 4)
# ---------------------------------------------------------------------------------------------
def test_join_report_flags_ambiguity_and_never_merges(tmp_path):
    rows = SE.sales_rows()
    rows.append(SE._row("2025-05-05", "900.00.0006", "Sentetik Aktif", "900.99.0006", "Sentetik Bayi", 5_000))
    rows.append(SE._row("2025-05-06", "900.00.0003 ", "Sentetik T3 Okul", "900.00.0003", "Sentetik T3 Okul", 1_000))
    rows.append(SE._row("2025-05-07", "900.00.0002", "Sentetik T2 Ofis", "900.00.0002", "Sentetik T2 Ofis", 1_000,
                        stok="SP-002", stokname="Sentetik Defter Başka Ad"))
    sales = pd.DataFrame(rows)
    master = pd.DataFrame({"KODU": ["900.00.0001", "900.00.0002", "900.00.0002", "900.00.0003", "900.99.0001"]})
    aging = SE.aging_frame()
    aging.loc[len(aging)] = {**{c: 0 for c in SE.AGING_COLUMNS}, "Hesap kodu": "900.00.0001", "Hesap adı": "x",
                             "Temsilci kodu": "T"}
    rep = SV.validate_joins(sales, SE.stock_frame(), aging, master)
    assert rep["order_customers_with_multiple_invoice_accounts"] == 1
    assert rep["order_codes_with_whitespace"] == 1 and rep["order_codes_colliding_after_trim"] == 1
    assert rep["master_duplicate_codes"] == 1 and rep["matches_only_after_trim"] >= 1
    assert rep["aging_accounts_matching_order_code_only"] == 1                  # the DQ-6 shape
    assert rep["products_with_multiple_names"] == 1 and rep["sold_products_not_in_stock"] == 1
    ev = SV.join_evidence(rep)
    assert ev["sales"]["joins_validated"] is False and ev["aging"]["joins_validated"] is False
    # D-003 unchanged: the canonical identity is still Pro_kodu as written (no trimming, no merging)
    sdf = db.load_sales(None, sales)
    assert "900.00.0003 " in set(sdf["_cust"]) and "900.00.0003" in set(sdf["_cust"])


def test_dq5_and_dq6_remain_open():
    g = ACC.current_gate(today=TODAY)
    # D-071: win-back no longer reads Cari debt, so its DQ-6 blocker is gone (still INVALID by its ceiling); the DQ-6
    # identity question itself stays open as a known issue for any future customer-level use of Cari balances.
    assert "DQ-6" not in g.decide("customers.winback").codes and g.decide("customers.winback").state == INV
    issue = next(i for i in ACC.accuracy_known_issues() if i["code"] == "dq6_winback_overdue_identity")
    assert issue["status"] == "open"
    assert any("DQ-5" in c for c in g.decide("customers.momentum").codes)


# ---------------------------------------------------------------------------------------------
# promotion rules (Task 8) and AI (16)
# ---------------------------------------------------------------------------------------------
def test_only_the_d073_metric_is_certified_and_the_flag_is_set_after_acceptance():
    REL.assert_release_state()                    # D-073: one certified metric, flag True
    ACC.validate_register()
    assert [k for k, m in ACC.REGISTER.items() if m.certified == S] == list(REL.CERTIFIED)
    assert not ACC.DEFINITIONS["operational_freshness"].approved
    assert not ACC.DEFINITIONS["kredi_durum"].approved


def test_production_app_never_calls_ai_during_refresh_and_status(world):
    selection(world.folder)
    code = ("import sys, services.generation, services.source_validation, services.kredi_schedule, "
            "scripts.reconcile_sources, services.importers.kredi;"
            "print(sorted(m for m in sys.modules if m.startswith(('openai','anthropic','services.ai','services.agent'))))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    with world.client() as c:
        assert c.get("/api/accuracy/status").json()["ai_used"] is False
        assert c.get("/api/ai/calls").json()["calls"] == []


def test_manifest_and_status_carry_no_values_or_names(world):
    selection(world.folder)
    assert refresh(world.folder, world.dash, install=world.install)["ok"]
    text = open(GEN.manifest_path(world.dash), encoding="utf-8").read()
    with world.client() as c:
        text += json.dumps(c.get("/api/accuracy/status").json(), ensure_ascii=False)
    for secret in ("Sentetik T1", "Sentetik Kapali", "900.00.0001", "TEMSILCI-A", "250000", "987654", "Sentetik Kalem"):
        assert secret not in text, secret
    assert str(world.folder) not in text
