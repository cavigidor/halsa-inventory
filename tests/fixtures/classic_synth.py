"""SYNTHETIC inputs for the Klasik Rapor tests (invented codes and names only).

The standard synthetic ERP exports (tests/fixtures/synthetic_erp.py) plus:
- eight active customers with exactly the same 2025 → 2026 change (-50.0 %), so "Büyüme / Küçülme" has a tie group whose
  order the ORIGINAL builder leaves to Python's randomized set iteration;
- the sales file stored under a decomposed (NFD) Unicode name, as macOS stores many file names, which the original
  report shows in its header exactly as stored;
- a customer / representative MASTER list named like a Cari file, which must never be read as the aging report.
"""
import os
import unicodedata

import pandas as pd

from tests.fixtures import synthetic_erp as F

SALES_NFC = "Sentetik satışlar.xlsx"
SALES_FILE = unicodedata.normalize("NFD", SALES_NFC)           # 's' + U+0327, as stored by macOS
STOCK_FILE = F.STOCK_FILE
AGING_FILE = F.AGING_FILE
MASTER_FILE = "cari temsilci listesi.xlsx"
DATE = "2026-07-01"


def tied_rows():
    rows = []
    for i in range(8):
        code, name = f"900.00.07{i:02d}", f"Sentetik Esit {i}"
        rows.append(F._row("2025-03-10", code, name, code, name, 40_000, rep=f"TEMSILCI-{'AB'[i % 2]}"))
        rows.append(F._row("2026-03-10", code, name, code, name, 20_000, rep=f"TEMSILCI-{'AB'[i % 2]}"))
    return rows


def write_all(folder):
    os.makedirs(folder, exist_ok=True)
    F.write_all(folder)
    rows = F.sales_rows() + tied_rows()
    os.remove(os.path.join(folder, F.SALES_FILE))
    pd.DataFrame(rows).to_excel(os.path.join(folder, SALES_FILE), index=False)
    pd.DataFrame([{"Cari Kodu": "900.00.0001", "Cari Adı": "Sentetik T1 Kirtasiye", "Temsilci": "TEMSILCI-A",
                   "Sektör": "S1", "Açık / Kapalı": "AÇIK"}]).to_excel(os.path.join(folder, MASTER_FILE), index=False)
    return folder
