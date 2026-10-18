"""Read-only local preflight for explicitly configured M7 owner workbooks.

Usage:
    python -m scripts.owner_preflight
    python -m scripts.owner_preflight --json

This runs the existing validated importers on configured files only. It does NOT pick
a file of record, edit files, create a dashboard, touch SQLite, or call AI. Output is
restricted to file basenames, states, counts, and validation codes (not raw rows,
financial amounts, contract/lender names, paths, or cell contents).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as _config  # noqa: F401,E402  (loads the local .env when allowed)
from services import owner_view as OV  # noqa: E402


def inspect(configs: dict[str, OV.OwnerSourceConfig] | None = None) -> dict:
    """Read ONLY explicitly configured owner sources, returning safe aggregate status."""
    repo = OV.OwnerRepository(configs if configs is not None else OV.config_from_env(), background=False)
    repo.ensure_loaded()
    entries = repo.snapshot()
    sources = []
    for key in OV.SOURCES:
        entry = entries[key]
        head = OV.source_head(repo, key, entry)
        row = {
            "source": key,
            "configured": head["configured"],
            "file": head["file"],  # basename only
            "state": head["state"],
            "error_code": head["error_code"],
            "issue_codes": head["issue_codes"],
        }
        view = entry.get("view")
        if key == "kredi" and view is not None:
            row["contracts"] = view.contracts
            row["installments"] = view.installments
            row["snapshot_date"] = view.snapshot_date
            row["kapali_with_remaining_installments"] = view.closed_with_remaining
            row["durum_definition"] = "unverified"
        if key == "gider_gelir" and view is not None:
            row["report_year"] = view.report_year
            row["months_with_revenue"] = len(view.months_with_revenue)
            row["warning_counts"] = {
                w["code"]: w["count"] for w in view.warnings if w.get("count") is not None
            }
            row["accounting_basis"] = "unverified"
            row["management_mapping"] = "not_supplied"
        if key == "butce" and view is not None:
            h = view.health
            row["source_status"] = h["source_status"]
            row["invalid_cells"] = h["invalid_cells"]
            row["ref_errors"] = h["ref_errors"]
            row["div0_errors"] = h["div0_errors"]
            row["external_dependent_cells"] = h["external_dependent_cells"]
        sources.append(row)
    configured = sum(1 for s in sources if s["configured"])
    failed = sum(1 for s in sources if s["state"] == OV.FAILED)
    return {
        "technical_status": "failed" if failed else ("validated" if configured else "not_configured"),
        "configured_sources": configured,
        "failed_sources": failed,
        "business_approval": "not_implied",
        "kredi_durum_definition": "awaiting_owner_confirmation",
        "sources": sources,
        "limitations": [
            "Only owner workbooks are checked; core ERP exports, Hedef, dashboard, browser, and SQLite are not tested.",
            "Valid source structure does not establish the file of record, source freshness, or accounting approval.",
            "Kredi DURUM=Kapali is informational until clarified; remaining amounts are not live bank balances.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate configured owner files locally; no writes or AI.")
    parser.add_argument("--json", action="store_true", help="Print privacy-safe machine-readable summary.")
    args = parser.parse_args(argv)
    result = inspect()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Yönetim kaynak kontrolü (salt okunur; yapay zekâ kullanılmaz)")
        for row in result["sources"]:
            file_label = row["file"] or "(yapılandırılmadı)"
            details = ""
            if row["source"] == "kredi" and row["state"] == OV.READY:
                details = (f"; Kapalı + kalan taksit: {row['kapali_with_remaining_installments']}"
                           " (DURUM doğrulanmadı)")
            if row["error_code"]:
                details += f"; hata: {row['error_code']}"
            print(f"- {row['source']}: {row['state']} — {file_label}{details}")
        print(f"Teknik sonuç: {result['technical_status']}. Ticari/muhasebe onayı verilmiş değildir.")
        print("Kredi DURUM açıklaması bekleniyor. Bu test canlı banka bakiyesi doğrulaması değildir.")
    return 1 if result["failed_sources"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
