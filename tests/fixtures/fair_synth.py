"""SYNTHETIC M7-4 Fuar Merkezi workbooks — NOT real company data.

Invented 930.xx codes, names, event labels and amounts; as of 2026-06-30.

FUAR editions (label type FUAR only):
  E25  "2025SENTFUARAFUAR"   P1 (one order, TWO lines 6,000 + 4,000), P2 (2,000), P10 KAPALI (800), P12 hostile (900)
  E26  "2026SENTFUARAFUAR"   P1 (3,000), P5/P6 customer-conflict order (500 + 500), P7 date-conflict order (two dates),
                             P8 missing-amount line
  E26b "2026SENTFUARAFUAR2"  P3 (1,500)                         (same stem, sequence 2 → a separate edition)
  EX   hostile stem FUAR     P4 (700)
Not FUAR (excluded): ETKİNLİK (P2), SAHA (P2), GENEL (many), and — with label_conflict=True — P9's order whose lines
carry FUAR and GENEL labels (M6-2 rejects such a file; the FUAR model excludes the order and counts it).
P1 has a later GENEL order (never attributed to the fair).

Segments:
  P13 winback (strong 2022–2024, a 2025-09 order, none in 2026) · P14 real decline · P15 collections (overdue account)
  P16 inactive (last order 2023) · P17–P20 recent GENEL buyers · P11 duplicate master code · P10 master KAPALI
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

from tests.fixtures import c360_synth as F

AS_OF = dt.date(2026, 6, 30)
SALES_FILE = "Sentetik fuar satışlar.xlsx"
STOCK_FILE = "STOK Sentetik fuar.xlsx"
AGING_FILE = "Cari Sentetik fuar.xlsx"
E25, E26, E26B = "2025SENTFUARAFUAR", "2026SENTFUARAFUAR", "2026SENTFUARAFUAR2"
EX = '2026<img src=x onerror="window.__FAIR_XSS=1">SentetikFUAR'
ETK, SAHA = "2025SENTSALONETKİNLİK", "2025SENTBOLGESAHA"
XSS_NAME = '<img src=x onerror="window.__FAIR_XSS=2">Sentetik Fuar Müşterisi'
P = {i: f"930.10.{i:04d}" for i in range(1, 21)}


def name(i):
    return XSS_NAME if i == 12 else f"Sentetik Fuar {i:02d}"


def sales_rows(label_conflict=False):
    F._n["order"] = 0
    rows = []

    def line(o, date, i, amount, label="GENEL", sku="FS-001", qty=10, grp="SENT-GRUP-F", cust=None):
        c = cust or i
        rows.append(F._line(o, date, P[c], name(c), P[c], name(c), amount, sku=sku, skuname=f"Sentetik ürün {sku}",
                            qty=qty, label=label, anagrup=grp, kategori="Sentetik Kategori F"))

    o = F._order(); line(o, "2025-01-20", 1, 6_000, E25, "FS-001", 10); line(o, "2025-01-20", 1, 4_000, E25, "FS-002", 5)
    line(F._order(), "2025-03-01", 1, 9_999, "GENEL")                                       # follow-on GENEL order
    line(F._order(), "2026-01-18", 1, 3_000, E26)
    line(F._order(), "2025-01-21", 2, 2_000, E25)
    line(F._order(), "2025-05-05", 2, 1_111, ETK)
    line(F._order(), "2025-06-06", 2, 1_222, SAHA)
    line(F._order(), "2026-01-25", 3, 1_500, E26B, "FS-003")
    line(F._order(), "2026-02-02", 4, 700, EX)
    o = F._order(); line(o, "2026-01-19", 5, 500, E26); line(o, "2026-01-19", 6, 500, E26, "FS-002")   # conflict
    o = F._order(); line(o, "2026-01-18", 7, 400, E26); line(o, "2026-01-20", 7, 600, E26, "FS-002")   # date conflict
    line(F._order(), "2026-01-21", 8, None, E26, qty=3)                                     # missing amount
    if label_conflict:   # M6-2 treats this as source-fatal; the FUAR model excludes the order (counted)
        o = F._order(); line(o, "2026-01-22", 9, 300, E26); line(o, "2026-01-22", 9, 300, "GENEL", "FS-002")
    else:
        line(F._order(), "2026-01-22", 9, 300, "GENEL")
    line(F._order(), "2025-01-23", 10, 800, E25)
    line(F._order(), "2026-03-03", 11, 1_000)
    line(F._order(), "2025-01-24", 12, 900, E25)
    for y, amt in ((2022, 60_000), (2023, 70_000), (2024, 80_000)):
        line(F._order(), f"{y}-04-04", 13, amt)
    line(F._order(), "2025-09-09", 13, 1_000)
    line(F._order(), "2025-03-03", 14, 150_000)
    line(F._order(), "2026-03-03", 14, 20_000)
    line(F._order(), "2026-02-02", 15, 30_000)
    line(F._order(), "2023-02-02", 16, 1_000)
    for i in range(17, 21):
        line(F._order(), "2026-05-05", i, 1_000)
    line(F._order(), "2026-06-30", 20, 100)                                                 # AS_OF
    return rows


def master_rows():
    def row(i, status="Açık", nm=None):
        return {"KODU": P[i], "ÜNVANI 1": nm or name(i), "MUH.KODU 1": P[i], "SEKTÖR": "S-F", "ANA CARİ": None,
                "CARİ FİRMA": status, "TEMSİLCİ": "REP-F"}
    rows = [row(i) for i in range(1, 21) if i not in (10, 11)]
    rows += [row(10, "Kapalı"), row(11, "Açık", "Sentetik Fuar 11 A"), row(11, "Kapalı", "Sentetik Fuar 11 B")]
    return rows


def stock_frame():
    rows = [("FS-001", "Sentetik ürün", 1, 0.2, 1.0, 3.0, 100, "")]
    return pd.DataFrame(rows, columns=["Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %",
                                       "minseviye", "maxseviye", "Envanter değeri", "DURUM"])


def aging_frame():
    cols = F.AGING_COLUMNS
    d = {c: 0 for c in cols[3:]}
    d["Kasım-2025 ve öncesi"] = 300_000
    return pd.DataFrame([{"Hesap kodu": P[15], "Hesap adı": name(15), "Temsilci kodu": "T", **d}], columns=cols)


def write_all(folder, *, sales_file=SALES_FILE, extra_rows=None, label_conflict=False):
    os.makedirs(folder, exist_ok=True)
    F.write_sales(os.path.join(folder, sales_file), sales_rows(label_conflict) + list(extra_rows or []), True,
                  master_rows())
    with pd.ExcelWriter(os.path.join(folder, STOCK_FILE)) as w:
        pd.DataFrame([["Sentetik stok"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        stock_frame().to_excel(w, index=False, startrow=3)
    aging_frame().to_excel(os.path.join(folder, AGING_FILE), index=False)
    return folder
