"""SYNTHETIC Günlük Satış (M7-2) workbooks — NOT real company data.

Invented 910.xx codes, names, events and amounts. Built so that the M5 global top-15 is filled ENTIRELY by product
(overstock) actions (16 products, score 71 each) while every customer action scores below 71. A queue built from the
cut top-15 would therefore be empty; the M7-2 queue must use the uncut candidates.

Customers (all with Customer 360 identity in the `liste` sheet unless noted):
  Q1 910.10.0001  real decline + collections on TWO exclusive invoice accounts (910.10.0001 itself and 910.19.0001)
  Q2 910.10.0002  winback only
  Q3 910.10.0003  master KAPALI (cached sales column says AÇIK): winback (removed in M7) + collections (kept)
  Q4 910.10.0004  master KAPALI with winback only → not in the queue
  Q5 910.10.0005 / Q6 910.10.0006  share invoice account 910.29.0056, which has a collections action → not assigned
  Q7 910.10.0007  duplicate master code; winback (kept, warning, KAPALI unknown)
  Q8 910.10.0008  hostile name; winback
  Q9 910.10.0009  real decline only; buys the 16 overstock products (their historical buyer)
  unlinked aging account 910.88.0001 with a collections action (no sales link) → not assigned
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

from tests.fixtures import c360_synth as F

AS_OF = dt.date(2026, 6, 30)
SALES_FILE = "Sentetik kuyruk satışlar.xlsx"
STOCK_FILE = "STOK Sentetik kuyruk.xlsx"
AGING_FILE = "Cari Sentetik kuyruk.xlsx"
Q = {i: f"910.10.000{i}" for i in range(1, 10)}
SHARED, UNLINKED, Q1_SECOND = "910.29.0056", "910.88.0001", "910.19.0001"
XSS = '<img src=x onerror="window.__SQ_XSS=1">Sentetik <i>Tehlikeli</i> Ad'
OVERSTOCK = [f"OS-{i:02d}" for i in range(1, 17)]


def sales_rows():
    F._n["order"] = 0
    r = []

    def order(date, cust, name, inv, invname, amount, **kw):
        o = F._order()
        r.append(F._line(o, date, cust, name, inv, invname, amount, **kw))

    # Q1: decline (2025-03 vs 2026-03) + two exclusive invoice accounts
    order("2025-03-05", Q[1], "Sentetik Kuyruk Bir", Q[1], "Sentetik Kuyruk Bir", 100_000)
    order("2026-03-05", Q[1], "Sentetik Kuyruk Bir", Q[1], "Sentetik Kuyruk Bir", 40_000)
    order("2025-01-10", Q[1], "Sentetik Kuyruk Bir", Q1_SECOND, "Sentetik Kuyruk Bir Bayi", 30_000)
    # Q2 winback (strong 2022–2024, nothing in 2026)
    for y, a in ((2022, 50_000), (2023, 60_000), (2024, 70_000)):
        order(f"{y}-05-05", Q[2], "Sentetik Kuyruk İki", Q[2], "Sentetik Kuyruk İki", a)
    # Q3 KAPALI in master: winback history + a 2025 order (revenue for the collections ratio)
    for y, a in ((2023, 60_000), (2024, 70_000), (2025, 20_000)):
        order(f"{y}-04-04", Q[3], "Sentetik Kuyruk Üç", Q[3], "Sentetik Kuyruk Üç", a)
    # Q4 KAPALI in master: winback only
    for y, a in ((2022, 40_000), (2023, 45_000), (2024, 50_000)):
        order(f"{y}-06-06", Q[4], "Sentetik Kuyruk Dört", Q[4], "Sentetik Kuyruk Dört", a)
    # Q5 / Q6 share an invoice account (2025 + 2026 orders on it)
    order("2025-09-09", Q[5], "Sentetik Kuyruk Beş", SHARED, "Sentetik Ortak Fatura", 25_000)
    order("2026-02-02", Q[6], "Sentetik Kuyruk Altı", SHARED, "Sentetik Ortak Fatura", 15_000)
    # Q7 duplicate master: winback
    for y, a in ((2021, 30_000), (2022, 35_000), (2023, 40_000)):
        order(f"{y}-07-07", Q[7], "Sentetik Kuyruk Yedi", Q[7], "Sentetik Kuyruk Yedi", a)
    # Q8 hostile name: winback
    for y, a in ((2022, 20_000), (2023, 25_000), (2024, 30_000)):
        order(f"{y}-08-08", Q[8], XSS, Q[8], XSS, a)
    # Q9 decline; historical buyer of the 16 overstock products
    o = F._order()
    for sku in OVERSTOCK:
        r.append(F._line(o, "2025-02-10", Q[9], "Sentetik Kuyruk Dokuz", Q[9], "Sentetik Kuyruk Dokuz", 5_000,
                         sku=sku, skuname=f"Sentetik Fazla {sku}"))
    order("2026-02-10", Q[9], "Sentetik Kuyruk Dokuz", Q[9], "Sentetik Kuyruk Dokuz", 20_000)
    order("2026-06-30", Q[9], "Sentetik Kuyruk Dokuz", Q[9], "Sentetik Kuyruk Dokuz", 1_000)    # AS_OF
    return r


def master_rows():
    def row(code, name, status="Açık", rep="REP-Q"):
        return {"KODU": code, "ÜNVANI 1": name, "MUH.KODU 1": code, "SEKTÖR": "S-Q", "ANA CARİ": None,
                "CARİ FİRMA": status, "TEMSİLCİ": rep}
    rows = [row(Q[1], "Sentetik Kuyruk Bir"), row(Q1_SECOND, "Sentetik Kuyruk Bir Bayi"),
            row(Q[2], "Sentetik Kuyruk İki"), row(Q[3], "Sentetik Kuyruk Üç", "Kapalı"),
            row(Q[4], "Sentetik Kuyruk Dört", "Kapalı"), row(Q[5], "Sentetik Kuyruk Beş"),
            row(Q[6], "Sentetik Kuyruk Altı"), row(SHARED, "Sentetik Ortak Fatura"),
            row(Q[7], "Sentetik Kuyruk Yedi A"), row(Q[7], "Sentetik Kuyruk Yedi B", "Kapalı"),
            row(Q[8], XSS), row(Q[9], "Sentetik Kuyruk Dokuz")]
    return rows


def stock_frame():
    rows = [(sku, f"Sentetik Fazla {sku}", 1_000, 9.0, 1.0, 3.0, 600_000, "") for sku in OVERSTOCK]
    rows.append(("OS-LOW", "Sentetik Az", 1, 0.2, 1.0, 3.0, 100, ""))
    return pd.DataFrame(rows, columns=["Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %",
                                       "minseviye", "maxseviye", "Envanter değeri", "DURUM"])


def aging_frame():
    cols = F.AGING_COLUMNS

    def row(code, name, oldest):
        d = {c: 0 for c in cols[3:]}
        d["Kasım-2025 ve öncesi"] = oldest
        return {"Hesap kodu": code, "Hesap adı": name, "Temsilci kodu": "T", **d}
    return pd.DataFrame([row(Q[1], "Sentetik Kuyruk Bir", 200_000), row(Q1_SECOND, "Sentetik Kuyruk Bir Bayi", 250_000),
                         row(Q[3], "Sentetik Kuyruk Üç", 260_000), row(SHARED, "Sentetik Ortak Fatura", 240_000),
                         row(UNLINKED, "Sentetik Bağlantısız", 500_000)], columns=cols)


def write_all(folder, *, sales_file=SALES_FILE, extra_rows=None):
    os.makedirs(folder, exist_ok=True)
    rows = sales_rows() + list(extra_rows or [])
    F.write_sales(os.path.join(folder, sales_file), rows, True, master_rows())
    with pd.ExcelWriter(os.path.join(folder, STOCK_FILE)) as w:
        pd.DataFrame([["Sentetik stok"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        stock_frame().to_excel(w, index=False, startrow=3)
    aging_frame().to_excel(os.path.join(folder, AGING_FILE), index=False)
    return folder
