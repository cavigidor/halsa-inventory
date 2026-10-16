"""SYNTHETIC M7-3 opportunity workbooks — NOT real company data.

Invented 920.xx codes, names, product groups, cities and amounts. As of 2026-06-30; the 365-day cohort window is
(2025-06-30, 2026-06-30]. Customers O[1..60] are active (a small 2026 order per group they "buy"), so the
association counts are exact by construction:

  group A = "SENT-KAŞE"      bought recently by O1–O40                                → 40 buyers
  group B = "SENT-MÜREKKEP"  O1–O24 and O41–O44                                       → 28 buyers
  group X = HOSTILE label     O1–O22 and O45–O46                                       → 24 buyers
  group T = "SENT-TAMPON"    O41–O60                                                  → 20 buyers
  group D = "SENT-DEFTER"    O1–O12 and O51–O61                                       → 23 buyers
  cohort = O1–O61 = 61 active customers

  A→B: pair 24, conf 0.60, lift ≈ 1.31  eligible     A→X: pair 22, conf 0.55, lift ≈ 1.40  eligible
  A→D: pair 12, conf 0.30, lift ≈ 0.80  lift-rejected (confidence exactly at the guard)
  B→T / A→T: pair 4 / 0                 support-rejected
  B→X: pair 22, conf ≈ 0.79              (stronger evidence than A→X for O23, O24)
  D→T: pair 10, conf ≈ 0.43, lift ≈ 1.33 eligible (support exactly at the guard)

Special customers:
  O25  bought B in 2023 (full history) → B is NOT a gap; only X is a target
  O26  + an overdue invoice account → collections + cross-sell (collection first)
  O27  + strong 2022–2024 history and no 2026 order (its recent orders are 2025-09) → winback + cross-sell
  O28  + 2025 H1 order larger than its 2026 H1 orders → real decline + cross-sell
  O30  master KAPALI → cross-sell removed from Günlük Satış (still shown in Customer 360 with a note)
  O61  bought A only in 2025-01 (outside the window) and D recently → no A-based target
  O70  bought A only in 2024 → not in the cohort, no target
  O8   hostile customer name
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

from tests.fixtures import c360_synth as F

AS_OF = dt.date(2026, 6, 30)
SALES_FILE = "Sentetik fırsat satışlar.xlsx"
STOCK_FILE = "STOK Sentetik fırsat.xlsx"
AGING_FILE = "Cari Sentetik fırsat.xlsx"
HEDEF_FILE = "hedef raporu sentetik fırsat.xlsx"
A, B, T, D = "SENT-KAŞE", "SENT-MÜREKKEP", "SENT-TAMPON", "SENT-DEFTER"
X = '<img src=x onerror="window.__OPP_XSS=1">Sentetik <b>Grup</b>'
XSS_CITY = '<img src=x onerror="window.__OPP_XSS=2">Sentetik Şehir'
XSS_NAME = '<img src=x onerror="window.__OPP_XSS=3">Sentetik Müşteri'


def code(i):
    return f"920.10.{i:04d}"


def name(i):
    return XSS_NAME if i == 8 else f"Sentetik Fırsat {i:02d}"


O = {i: code(i) for i in range(1, 71)}


def groups_of(i):
    g = []
    if 1 <= i <= 40:
        g.append(A)
    if 1 <= i <= 24 or 41 <= i <= 44:
        g.append(B)
    if 1 <= i <= 22 or 45 <= i <= 46:
        g.append(X)
    if 41 <= i <= 60:
        g.append(T)
    if 1 <= i <= 12 or 51 <= i <= 61:
        g.append(D)
    return g


def sales_rows():
    F._n["order"] = 0
    rows = []

    def line(o, date, i, amount, grp, sku=None):
        rows.append(F._line(o, date, O[i], name(i), O[i], name(i), amount, sku=sku or f"SKU-{grp[-6:]}",
                            skuname=f"Sentetik ürün {grp[-6:]}", anagrup=grp, kategori="Sentetik Kategori"))

    for i in range(1, 62):
        o = F._order()
        date = "2025-09-15" if i == 27 else "2026-02-10"           # O27: active, but no 2026 order (winback)
        for grp in groups_of(i):
            line(o, date, i, 1_000, grp)
    # O25: B long ago (full-history purchase)
    line(F._order(), "2023-05-05", 25, 900, B)
    # O27: strong 2022–2024 history (lapsed-year rule) in group A
    for y, amt in ((2022, 60_000), (2023, 70_000), (2024, 80_000)):
        line(F._order(), f"{y}-04-04", 27, amt, A)
    # O28: real decline (2025 H1 much larger than 2026 H1)
    line(F._order(), "2025-03-03", 28, 150_000, A)
    line(F._order(), "2026-03-03", 28, 20_000, A)
    # O61: A only outside the window
    line(F._order(), "2025-01-20", 61, 1_000, A)
    # O70: A only in 2024 (inactive)
    line(F._order(), "2024-02-02", 70, 1_000, A)
    # a missing group label (coverage < 100 %)
    rows.append(F._line(F._order(), "2026-06-30", O[60], name(60), O[60], name(60), 500, sku="SKU-NOGRP",
                        skuname="Sentetik grupsuz", anagrup=None, kategori="Sentetik Kategori"))   # AS_OF
    return rows


def master_rows():
    def row(i):
        return {"KODU": O[i], "ÜNVANI 1": name(i), "MUH.KODU 1": O[i], "SEKTÖR": "S-F", "ANA CARİ": None,
                "CARİ FİRMA": "Kapalı" if i == 30 else "Açık", "TEMSİLCİ": "REP-F"}
    return [row(i) for i in range(1, 71)]


def stock_frame():
    rows = [("SKU-NOGRP", "Sentetik grupsuz", 1, 0.2, 1.0, 3.0, 100, "")]
    return pd.DataFrame(rows, columns=["Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %",
                                       "minseviye", "maxseviye", "Envanter değeri", "DURUM"])


def aging_frame():
    cols = F.AGING_COLUMNS
    d = {c: 0 for c in cols[3:]}
    d["Kasım-2025 ve öncesi"] = 300_000
    return pd.DataFrame([{"Hesap kodu": O[26], "Hesap adı": name(26), "Temsilci kodu": "T", **d}], columns=cols)


def write_all(folder, *, sales_file=SALES_FILE, extra_rows=None):
    os.makedirs(folder, exist_ok=True)
    F.write_sales(os.path.join(folder, sales_file), sales_rows() + list(extra_rows or []), True, master_rows())
    with pd.ExcelWriter(os.path.join(folder, STOCK_FILE)) as w:
        pd.DataFrame([["Sentetik stok"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        stock_frame().to_excel(w, index=False, startrow=3)
    aging_frame().to_excel(os.path.join(folder, AGING_FILE), index=False)
    return folder


#: Hedef geography (unverified) for a subset: two cities, one hostile; district / sector for the same customers.
HEDEF_ROWS = ([(i, "Sentetik İl Bir", "Sentetik İlçe 1", "SK-1") for i in range(26, 36)]
              + [(i, XSS_CITY, "Sentetik İlçe 2", "SK-2") for i in range(41, 47)]
              + [(i, "Sentetik İl Bir", "Sentetik İlçe 1", "SK-2") for i in range(1, 5)]
              + [(47, None, None, None)])


def write_hedef(path):
    """Synthetic Hedef workbook (current_2026 layout). Capacity / Risk / Kredi are filled with LARGE values for some
    accounts so a test can prove they never influence anything."""
    from services.importers import hedef as HD
    headers = [f.header.name for f in HD.SPECS[HD.SchemaVersion.CURRENT_2026].fields]
    rows = []
    for i, city, district, sector in HEDEF_ROWS:
        d = {h: None for h in headers}
        d.update({"cari_kod": O[i], "Temsilci": "HEDEF-REP-F", "Müşteri Temsilcisi": "MUSTERI-REP-F",
                  "Sektör Kodu": sector, "İlce": district, "İl": city, "isk1": 0.1,
                  "Haftalık Kaşe Adedi": 100_000 if i in (41, 42) else None,
                  "Risk": 9_000_000 if i == 41 else None, "Kredi": 8_000_000 if i == 42 else None,
                  "Ziyaret Sayısı": 1})
        rows.append(d)
    pd.DataFrame(rows, columns=headers).to_excel(path, index=False)
    return path
