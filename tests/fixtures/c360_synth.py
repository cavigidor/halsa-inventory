"""SYNTHETIC Customer 360 workbooks for M7-1 tests — NOT real company data.

Every code (910.xx.xxxx range), name, event label, salesperson and amount is invented. The sales workbook mimics
the structure of the real main sales report: a data sheet (sales-order lines, with `Srmmerk`, `Satici` and the
cached `Temsilci` / `Açık / Kapalı` / `Sektör` lookup columns) plus the `liste` customer-master sheet. The stock and
aging files reuse the M5 synthetic layouts.

Scenarios:
  910.00.0001  unique master match (Açık); two EXCLUSIVE invoice accounts (itself + 910.99.0001); orders 2023–2026;
               several products and groups; a zero-amount line and a missing-amount line; fuar / etkinlik / saha /
               general orders (one fuar edition year ≠ order year); two salespeople on orders.
  910.00.0002  shares invoice account 910.99.0002 with 910.00.0003 (shared account, overdue in aging).
  910.00.0003  shares invoice account 910.99.0002 with 910.00.0002.
  910.00.0004  DUPLICATE master code (two liste rows with different reps).
  910.00.0005  not in the master (unmatched).
  910.00.0006  master `Kapalı`; the cached sales columns say AÇIK / CACHED-REP / CACHED-SEKTOR (must not be used).
  (empty)      Pro_kodu empty → D-003 fallback to invoice account 910.99.0007 (name from Musteri_ismi).
  910.00.0008  hostile name and hostile event label (XSS strings).
  "910.00.0009 " code with a trailing space (kept exactly; the master has it without → unmatched).
  aging        910.00.0001 overdue (oldest bucket Aralık-2025), 910.99.0002 overdue (shared), 910.99.0001 present with
               zero balance, 910.88.0000 not linked to any customer, a TOPLAM row.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

from tests.fixtures import synthetic_erp as SE

AS_OF = dt.date(2026, 6, 30)
SALES_FILE = "Sentetik 2020-2026 satışlar.xlsx"
STOCK_FILE = "STOK Sentetik.xlsx"
AGING_FILE = "Cari Sentetik.xlsx"
HEDEF_FILE = "hedef raporu sentetik.xlsx"

XSS_NAME = '<img src=x onerror="window.__C360_XSS=1">Sentetik <b>Kalın</b> Ltd'
XSS_EVENT = '2025<script>window.__C360_XSS=1</script>FUAR'
LONG_NAME = "Sentetik " + "Çokuzunisimlimüşteri" * 6 + " Anonim Şirketi"

_n = {"order": 0}


def _order():
    _n["order"] += 1
    return _n["order"]


def _line(order, date, pro, proname, inv, invname, amount, *, sku="C360-001", skuname="Sentetik Kalem",
          qty=10, label="GENEL", satici="SATICI-A", kategori="Sentetik Kategori A", anagrup="SENT-GRUP-A",
          cached_rep="CACHED-REP", cached_closed="AÇIK", cached_sector="CACHED-SEKTOR", series="CST"):
    return {"sip_evrakno_seri": series, "sip_evrakno_sira": order, "Musterikod": inv, "Musteri_ismi": invname,
            "Pro_kodu": pro, "ProjeIsmi": proname, "sto_kod": sku, "StokIsmi": skuname, "Srmmerk": label,
            "Tarih": pd.Timestamp(date), "miktar": qty, "Nettutar": amount,
            "Net_Tutar_Maliyet_Dusulmus": (None if amount is None else round(amount * 0.3, 2)),
            "kategori": kategori, "anagrup": anagrup, "Satici": satici, "Temsilci": cached_rep,
            "Açık / Kapalı": cached_closed, "Sektör": cached_sector}


def sales_rows():
    _n["order"] = 0
    r = []
    c1 = ("910.00.0001", "Sentetik Bir Kırtasiye")
    # 910.00.0001 — invoice to itself and to 910.99.0001 (both exclusive to this customer)
    o = _order()
    r += [_line(o, "2023-02-10", *c1, "910.00.0001", "Sentetik Bir Kırtasiye", 12_000.5),
          _line(o, "2023-02-10", *c1, "910.00.0001", "Sentetik Bir Kırtasiye", 3_000, sku="C360-002",
                skuname="Sentetik Defter", kategori="Sentetik Kategori B", anagrup="SENT-GRUP-B")]
    o = _order()
    r.append(_line(o, "2024-04-05", *c1, "910.99.0001", "Sentetik Bayi Fatura", 8_000, label="2024ZQXKENTFUAR",
                   satici="SATICI-B"))
    o = _order()
    r.append(_line(o, "2025-01-20", *c1, "910.00.0001", "Sentetik Bir Kırtasiye", 0.0, sku="C360-003",
                   skuname="Sentetik Silgi", qty=5))                                   # recorded zero
    o = _order()
    r.append(_line(o, "2025-11-03", *c1, "910.00.0001", "Sentetik Bir Kırtasiye", None, sku="C360-003",
                   skuname="Sentetik Silgi", qty=2, label="2025ZQXSALONETKİNLİK"))     # missing amount
    o = _order()
    r.append(_line(o, "2025-12-15", *c1, "910.99.0001", "Sentetik Bayi Fatura", 4_500, label="2026ZQXKENTFUAR",
                   satici="SATICI-B"))                                                  # edition year 2026 ≠ 2025
    o = _order()
    r.append(_line(o, "2026-03-03", *c1, "910.00.0001", "Sentetik Bir Kırtasiye", 6_000, sku="C360-002",
                   skuname="Sentetik Defter", kategori="Sentetik Kategori B", anagrup="SENT-GRUP-B",
                   label="2026ZQXBOLGESAHA"))
    o = _order()
    r.append(_line(o, "2026-06-30", *c1, "910.00.0001", "Sentetik Bir Kırtasiye", 2_500))  # AS_OF
    # shared invoice account 910.99.0002
    for code, name, date, amt in (("910.00.0002", "Sentetik İki Ofis", "2025-05-05", 7_000),
                                  ("910.00.0003", "Sentetik Üç Okul", "2026-02-02", 9_000)):
        o = _order()
        r.append(_line(o, date, code, name, "910.99.0002", "Sentetik Ortak Bayi", amt))
    o = _order()
    r.append(_line(o, "2024-09-09", "910.00.0004", "Sentetik Dört Çift Kayıt", "910.00.0004",
                   "Sentetik Dört Çift Kayıt", 5_000))
    o = _order()
    r.append(_line(o, "2024-10-10", "910.00.0005", "Sentetik Beş Kayıtsız", "910.00.0005", "Sentetik Beş Kayıtsız",
                   4_000))
    o = _order()
    r.append(_line(o, "2023-06-06", "910.00.0006", "Sentetik Altı Kapalı", "910.00.0006", "Sentetik Altı Kapalı",
                   6_500))
    o = _order()
    r.append(_line(o, "2025-08-08", None, None, "910.99.0007", "Sentetik Yedi Fatura Müşterisi", 3_300))
    o = _order()
    r.append(_line(o, "2025-03-03", "910.00.0008", XSS_NAME, "910.00.0008", XSS_NAME, 1_100, label=XSS_EVENT))
    o = _order()
    r.append(_line(o, "2024-12-12", "910.00.0009 ", "Sentetik Dokuz Boşluklu", "910.00.0009",
                   "Sentetik Dokuz Boşluklu", 2_200))
    o = _order()
    r.append(_line(o, "2022-07-07", "910.00.0010", LONG_NAME, "910.00.0010", LONG_NAME, 1_000))
    return r


def master_rows():
    def row(code, name, sector, status, rep):
        return {"KODU": code, "ÜNVANI 1": name, "MUH.KODU 1": code, "SEKTÖR": sector, "ANA CARİ": None,
                "CARİ FİRMA": status, "TEMSİLCİ": rep}
    return [
        row("910.00.0001", "Sentetik Bir Kırtasiye Ana Kayıt", "S-KIRTASIYE", "Açık", "REP-A"),
        row("910.99.0001", "Sentetik Bayi Fatura", "S-BAYI", "Açık", "REP-A"),
        row("910.00.0002", "Sentetik İki Ofis", None, "Açık", "REP-B"),
        row("910.00.0003", "Sentetik Üç Okul", "S-OKUL", "Açık", None),
        row("910.99.0002", "Sentetik Ortak Bayi", "S-BAYI", "Açık", "REP-B"),
        row("910.00.0004", "Sentetik Dört Kayıt Bir", "S-A", "Açık", "REP-A"),
        row("910.00.0004", "Sentetik Dört Kayıt İki", "S-B", "Kapalı", "REP-C"),
        row("910.00.0006", "Sentetik Altı Kapalı", "S-KAPALI", "Kapalı", "REP-B"),
        row("910.99.0007", "Sentetik Yedi Fatura Müşterisi", None, "Açık", "REP-C"),
        row("910.00.0008", XSS_NAME, None, "Açık", "REP-A"),
        row("910.00.0009", "Sentetik Dokuz Boşluklu", None, "Açık", "REP-A"),
        row("910.00.0010", LONG_NAME, None, "Açık", None),
    ]


AGING_COLUMNS = ["Hesap kodu", "Hesap adı", "Temsilci kodu", "Kasım-2025 ve öncesi", "Aralık-2025",
                 "Ocak-2026", "Şubat-2026", "Mart-2026", "Nisan-2026", "Mayıs-2026", "Haziran-2026",
                 "Toplam Döviz Bakiye"]


def aging_frame():
    def row(code, name, rep, buckets=None):
        d = {c: 0 for c in AGING_COLUMNS[3:]}
        d.update(buckets or {})
        return {"Hesap kodu": code, "Hesap adı": name, "Temsilci kodu": rep, **d}
    rows = [
        {"Hesap kodu": "TOPLAM", "Hesap adı": "ara toplam", "Temsilci kodu": None,
         **{c: 999_999 for c in AGING_COLUMNS[3:]}},
        row("910.00.0001", "Sentetik Bir Kırtasiye", "T1", {"Aralık-2025": 1_250, "Mart-2026": 750}),
        row("910.99.0002", "Sentetik Ortak Bayi", "T2", {"Kasım-2025 ve öncesi": 4_000}),
        row("910.99.0001", "Sentetik Bayi Fatura", "T1"),
        row("910.88.0000", "Sentetik Bağlantısız Hesap", "T3", {"Ocak-2026": 999}),
    ]
    return pd.DataFrame(rows, columns=AGING_COLUMNS)


def write_sales(path, rows=None, master=True, master_rows_override=None):
    with pd.ExcelWriter(path) as w:
        pd.DataFrame(rows if rows is not None else sales_rows()).to_excel(w, sheet_name="2020-2026 satışlar",
                                                                           index=False)
        if master:
            mrows = master_rows_override if master_rows_override is not None else master_rows()
            pd.DataFrame(mrows).to_excel(w, sheet_name="liste", index=False)
    return path


def write_all(folder, *, sales_rows_override=None, master=True, aging=True, master_rows_override=None,
              sales_file=SALES_FILE):
    os.makedirs(folder, exist_ok=True)
    write_sales(os.path.join(folder, sales_file), sales_rows_override, master, master_rows_override)
    with pd.ExcelWriter(os.path.join(folder, STOCK_FILE)) as w:      # real file: header on row 4
        pd.DataFrame([["Sentetik stok raporu"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        SE.stock_frame().to_excel(w, index=False, startrow=3)
    if aging:
        aging_frame().to_excel(os.path.join(folder, AGING_FILE), index=False)
    return folder


def write_hedef(path):
    """A synthetic Hedef workbook in the verified current_2026 layout (every required header present)."""
    from services.importers import hedef as HD
    headers = [f.header.name for f in HD.SPECS[HD.SchemaVersion.CURRENT_2026].fields]
    def acc(code, rep, crep, city, weekly, risk, kredi, visits, stamp_2025=None, rev_2025=None):
        d = {h: None for h in headers}
        d.update({"cari_kod": code, "Temsilci": rep, "Müşteri Temsilcisi": crep, "Sektör Kodu": "SK-1",
                  "İlce": "Sentetik İlçe", "İl": city, "isk1": 0.15, "Haftalık Kaşe Adedi": weekly, "Risk": risk,
                  "Kredi": kredi, "Ziyaret Sayısı": visits, "2025 Kaşe Adedi": stamp_2025,
                  "2025 Net Ciro": rev_2025})
        return d
    rows = [acc("910.00.0001", "HEDEF-REP-X", "MUSTERI-REP-Y", "Sentetik İl", 120, 50_000, 10_000, 0, 340, 22_222.5),
            acc("910.00.0002", "HEDEF-REP-Z", None, "Sentetik İl 2", None, None, None, 3),
            acc("910.00.0009", "HEDEF-REP-X", None, "Sentetik İl", None, None, None, None)]
    pd.DataFrame(rows, columns=headers).to_excel(path, index=False)
    return path
