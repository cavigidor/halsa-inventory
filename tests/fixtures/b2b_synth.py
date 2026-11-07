"""SYNTHETIC inputs for the B2B tracking tests (D-080). Invented codes, names and amounts only.

The sales workbook has the physical 36-column layout of the real export (A … AJ), so that column AG is the
seller / channel column ``Satici``. The registration workbook has customer codes in physical column B.

Scenarios (latest transaction 2026-06-30 → provisional 365-day window starts after 2025-06-30):
  910.00.0001  registered (twice), recent B2B, one MIXED order document, one missing-amount B2B line (2025), one
               negative B2B line (2026); also in Büyüme / Küçülme
  910.00.0002  registered, only other channels                                   → B (diğer kanal)
  910.00.0003  not registered, recent other-channel purchases, a duplicated line   → A
  910.00.0004  not registered, B2B only in 2024                                    → A (B2B'den alım var) and C
  910.00.0005  registered, B2B only in 2023, other channel in 2026                 → C
  910.00.0006␠ sales code with a trailing space; registration without             → ambiguous (boşluk)
  910.00.0007  invoiced to 910.99.0001, which is registered                         → ambiguous (fatura hesabı)
  910.00.0008  KAPALI, not registered                                              → excluded from the lists
  910.00.0009  one channel cell written 'B2B web'                                  → B2B figures unknown
  090.00.0001  leading zero, registered as text                                    → confirmed
  910.00.0099  registration only                                                   → B (satış eşleşmesi yok)
  12345        a numeric registration cell
"""
import os

import pandas as pd

from tests.fixtures import synthetic_erp as F

HEADERS = ["sip_evrakno_seri", "sip_evrakno_sira", "Musterikod", "Musteri_ismi", "Pro_kodu", "ProjeIsmi", "sto_kod",
           "barkod", "StokIsmi", "Srmmerk", "Tarih", "miktar", "Teslim", "Kalan", "Vazgecilen", "Kalan_tutar",
           "Birim_fiyat", "brut_tutar", "Bedelsiz_Haric_İskonto", "BedelsizTutar", "Nettutar",
           "DevirMaliyeti_birim_fiyat_2022", "DevirMaliyeti_birim_fiyat_2023", "DevirMaliyeti_2022",
           "DevirMaliyeti_2023", "DevirMaliyeti_2024", "DevirMaliyeti_2025", "DevirMaliyeti_2026",
           "Net_Tutar_Maliyet_Dusulmus", "kategori", "anagrup", "altgrup", "Satici", "Temsilci", "Açık / Kapalı",
           "Sektör"]
assert HEADERS[32] == "Satici" and len(HEADERS) == 36

SALES_FILE = "Sentetik B2B satislar.xlsx"
STOCK_FILE = F.STOCK_FILE
REG_FILE = "b2b_kullanici_rapor.xlsx"
DATE = "2026-07-01"


def _line(doc, date, pro, name, amount, channel="SATICI-X", inv=None, rep="TEMSILCI-A", closed="AÇIK",
          sku="SP-900", skuname="Sentetik Kalem", qty=5, sector="S1"):
    r = {h: None for h in HEADERS}
    r.update({"sip_evrakno_seri": "SNB", "sip_evrakno_sira": doc, "Musterikod": inv or pro.strip(),
              "Musteri_ismi": name, "Pro_kodu": pro, "ProjeIsmi": name, "sto_kod": sku, "StokIsmi": skuname,
              "Srmmerk": "GENEL", "Tarih": pd.Timestamp(date), "miktar": qty, "Teslim": qty, "Kalan": 0,
              "Vazgecilen": 0, "Nettutar": amount, "Net_Tutar_Maliyet_Dusulmus": None if amount is None else amount * .25,
              "anagrup": "SENTETIK-GRUP", "Satici": channel, "Temsilci": rep, "Açık / Kapalı": closed, "Sektör": sector})
    return r


def sales_rows():
    L = []
    n = iter(range(1000, 9999))
    c1 = "910.00.0001"
    L.append(_line(next(n), "2025-03-01", c1, "Sentetik Bir", 40_000))
    L.append(_line(next(n), "2025-04-01", c1, "Sentetik Bir", 5_000, channel="B2B"))
    L.append(_line(next(n), "2025-05-01", c1, "Sentetik Bir", None, channel="B2B"))        # missing amount
    mixed = next(n)
    L.append(_line(mixed, "2026-02-01", c1, "Sentetik Bir", 8_000, channel="B2B"))
    L.append(_line(mixed, "2026-02-01", c1, "Sentetik Bir", 2_000, sku="SP-002", skuname="Sentetik Defter"))
    L.append(_line(next(n), "2026-03-01", c1, "Sentetik Bir", -1_000, channel="B2B"))     # return
    c2 = "910.00.0002"
    L.append(_line(next(n), "2025-02-01", c2, "Sentetik Iki", 40_000, rep="TEMSILCI-B"))
    L.append(_line(next(n), "2026-02-01", c2, "Sentetik Iki", 10_000, rep="TEMSILCI-B"))
    c3 = "910.00.0003"
    L.append(_line(next(n), "2025-01-15", c3, "Sentetik Uc", 35_000))
    dup = _line(next(n), "2026-01-15", c3, "Sentetik Uc", 12_000)
    L += [dup, dict(dup)]                                                                  # identical lines kept
    c4 = "910.00.0004"
    L.append(_line(next(n), "2024-05-10", c4, "Sentetik Dort", 7_000, channel="B2B", rep="TEMSILCI-B"))
    L.append(_line(next(n), "2024-06-10", c4, "Sentetik Dort", 3_000, rep="TEMSILCI-B"))
    c5 = "910.00.0005"
    L.append(_line(next(n), "2023-05-10", c5, "Sentetik Bes", 9_000, channel="B2B"))
    L.append(_line(next(n), "2026-04-10", c5, "Sentetik Bes", 4_000))
    L.append(_line(next(n), "2026-05-10", "910.00.0006 ", "Sentetik Alti", 6_000))
    L.append(_line(next(n), "2026-05-11", "910.00.0007", "Sentetik Yedi", 6_500, inv="910.99.0001"))
    L.append(_line(next(n), "2026-05-12", "910.00.0008", "Sentetik Kapali", 9_999, closed="KAPALI"))
    L.append(_line(next(n), "2025-08-12", "910.00.0009", "Sentetik Dokuz", 1_500, channel="B2B web"))
    L.append(_line(next(n), "2026-06-30", "090.00.0001", "Sentetik Sifir", 2_500, channel="B2B"))
    L.append(_line(next(n), "2026-06-20", "910.00.0010", "Sentetik Bos Kanal", 700, channel=None))
    return L


def registration_rows():
    return [("A", "910.00.0001", "Kullanici 1"), ("A", "910.00.0001", "Kullanici 2"), ("A", "910.00.0002", "K"),
            ("A", "910.00.0005", "K"), ("A", "910.00.0006", "K"), ("A", "910.99.0001", "K"), ("A", "090.00.0001", "K"),
            ("A", "910.00.0099", "K"), ("A", 12345, "K")]


def write_all(folder, registration=True):
    os.makedirs(folder, exist_ok=True)
    F.write_all(folder)
    for x in (F.SALES_FILE, F.AGING_FILE):
        os.remove(os.path.join(folder, x))
    pd.DataFrame(sales_rows(), columns=HEADERS).to_excel(os.path.join(folder, SALES_FILE), index=False)
    if registration:
        reg_dir = os.path.join(folder, "kayit")
        os.makedirs(reg_dir, exist_ok=True)
        pd.DataFrame(registration_rows(), columns=["Firma", "Cari Kodu", "Kullanici"]).to_excel(
            os.path.join(reg_dir, REG_FILE), index=False)
    return folder
