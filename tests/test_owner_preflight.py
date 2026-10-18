"""Synthetic-only checks for the local owner-file preflight (never sends data or invokes AI)."""
from __future__ import annotations

import datetime as dt
import json

from scripts import owner_preflight as PRE
from services import owner_view as OV
from tests.fixtures import owner_synth as FIX


def cfg(kredi=None, gider_gelir=None, butce=None):
    return {
        "kredi": OV.OwnerSourceConfig("kredi", str(kredi) if kredi else None),
        "gider_gelir": OV.OwnerSourceConfig("gider_gelir", str(gider_gelir) if gider_gelir else None),
        "butce": OV.OwnerSourceConfig("butce", str(butce) if butce else None),
    }


def test_nothing_configured_is_not_misrepresented_as_ready():
    report = PRE.inspect(cfg())
    assert report["technical_status"] == "not_configured"
    assert report["configured_sources"] == 0 and report["failed_sources"] == 0
    assert all(s["state"] == OV.NOT_CONFIGURED for s in report["sources"])
    assert report["business_approval"] == "not_implied"
    assert report["kredi_durum_definition"] == "awaiting_owner_confirmation"


def test_valid_synthetic_owner_sources_have_only_safe_aggregates(tmp_path):
    k = FIX.write_kredi(tmp_path, dt.date(2026, 3, 1))
    g = FIX.write_gider_gelir(tmp_path)
    b = FIX.write_butce(tmp_path)
    report = PRE.inspect(cfg(kredi=k, gider_gelir=g, butce=b))
    assert report["technical_status"] == "validated"
    assert report["configured_sources"] == 3 and report["failed_sources"] == 0
    sources = {x["source"]: x for x in report["sources"]}
    assert all(x["state"] == OV.READY for x in sources.values())
    assert sources["kredi"]["contracts"] > 0
    assert sources["kredi"]["durum_definition"] == "unverified"
    assert sources["gider_gelir"]["accounting_basis"] == "unverified"
    assert sources["butce"]["invalid_cells"] >= 0
    raw = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in raw  # basename only; no private folder disclosed
    for secret in FIX.SECRETS:
        assert secret not in raw
    assert "remaining_amount" not in raw and "lender_code" not in raw


def test_missing_source_fails_without_blocking_other_sources(tmp_path):
    g = FIX.write_gider_gelir(tmp_path)
    report = PRE.inspect(cfg(kredi=tmp_path / "kayip.xlsx", gider_gelir=g))
    sources = {x["source"]: x for x in report["sources"]}
    assert report["technical_status"] == "failed" and report["failed_sources"] == 1
    assert sources["kredi"]["error_code"] == "file_missing"
    assert sources["gider_gelir"]["state"] == OV.READY
    assert sources["butce"]["state"] == OV.NOT_CONFIGURED


def test_cli_json_exit_code_and_no_path_echo(monkeypatch, tmp_path, capsys):
    # The CLI reads the process environment. This points to a non-existent private file;
    # its full path must never be printed.
    monkeypatch.setenv("KREDI_PATH", str(tmp_path / "kayip.xlsx"))
    monkeypatch.delenv("GIDER_GELIR_PATH", raising=False)
    monkeypatch.delenv("BUTCE_PATH", raising=False)
    assert PRE.main(["--json"]) == 1
    raw = capsys.readouterr().out
    result = json.loads(raw)
    assert result["technical_status"] == "failed"
    assert result["sources"][0]["file"] == "kayip.xlsx"
    assert str(tmp_path) not in raw
