"""SYNTHETIC ERP exports for tests — NOT real company data.

Every customer, code, product and amount below is invented. Codes use the 900.xx.xxxx
range (not a real account range) and names start with "Sentetik". The files mimic only
the *structure* of the real exports (column names, header row offset, aging-bucket
headers) so the deterministic pipeline can be tested from a fresh checkout and in CI.

Scenarios encoded (see tests/test_domain_rules.py):
  900.00.0001  T1 lapsed: 3 strong years 2021-2023, no 2026; INVOICED to a different party
               (Musterikod 900.99.0001) -> identity must follow Pro_kodu/ProjeIsmi.
  900.00.0002  T2 lapsed: strong 2024 + 2025; small overdue in aging file.
  900.00.0003  T3 lapsed: single strong year 2025.
  900.00.0004  NOT lapsed-tiered: single strong year 2023 (T3 only allows 2024/2025).
  900.00.0005  NOT lapsed: total below 30.000.
  900.00.0006  Active, shrinking: Jan-Jun 2025 vs Jan-Jun 2026; some rows have NO cost value.
  900.00.0007  KAPALI: would be T1, must be excluded from opportunities but kept in risk/collections.
  900.00.0008  Empty Pro_kodu -> falls back to invoice code (Musterikod).
  2020 rows    Present for 900.00.0004 to check the configured qualification window.
"""
import datetime as dt
import os

import pandas as pd

AS_OF = dt.date(2026, 6, 30)

SALES_FILE = "Sentetik satislar.xlsx"
STOCK_FILE = "STOK Sentetik.xlsx"
AGING_FILE = "Cari Sentetik.xlsx"

_order = {"n": 0}


def _row(date, pro, proname, inv, invname, amount, stok="SP-900", stokname="Sentetik Kalem",
         rep="TEMSILCI-A", sector="S1", closed="AÇIK", cost_net="same", qty=10, anagrup="SENTETIK-GRUP"):
    _order["n"] += 1
    if cost_net == "same":
        net_after_cost = round(amount * 0.25, 2)   # 25% synthetic profit
    else:
        net_after_cost = cost_net                  # explicit value or None (missing cost)
    return {
        "sip_evrakno_seri": "SNT", "sip_evrakno_sira": _order["n"],
        "Musterikod": inv, "Musteri_ismi": invname, "Pro_kodu": pro, "ProjeIsmi": proname,
        "sto_kod": stok, "StokIsmi": stokname, "Tarih": pd.Timestamp(date), "miktar": qty,
        "Nettutar": float(amount), "Net_Tutar_Maliyet_Dusulmus": net_after_cost,
        "anagrup": anagrup, "Temsilci": rep, "Açık / Kapalı": closed, "Sektör": sector,
    }


def sales_rows():
    _order["n"] = 0
    r = []
    # T1, invoiced through a different party (identity check)
    for y, amt in ((2021, 15_000), (2022, 20_000), (2023, 12_000)):
        r.append(_row(f"{y}-03-10", "900.00.0001", "Sentetik T1 Kirtasiye",
                      "900.99.0001", "Sentetik Fatura Merkezi", amt))
    # T2
    r.append(_row("2024-05-10", "900.00.0002", "Sentetik T2 Ofis", "900.00.0002", "Sentetik T2 Ofis", 25_000,
                  rep="TEMSILCI-B"))
    r.append(_row("2025-02-10", "900.00.0002", "Sentetik T2 Ofis", "900.00.0002", "Sentetik T2 Ofis", 15_000,
                  rep="TEMSILCI-B", stok="SP-002", stokname="Sentetik Defter"))
    # T3 (2025 only)
    r.append(_row("2025-07-01", "900.00.0003", "Sentetik T3 Okul", "900.00.0003", "Sentetik T3 Okul", 35_000))
    # strong only in 2023 -> no tier; plus a 2020 row outside the configured window
    r.append(_row("2020-04-01", "900.00.0004", "Sentetik Eski Musteri", "900.00.0004", "Sentetik Eski Musteri", 50_000))
    r.append(_row("2023-04-01", "900.00.0004", "Sentetik Eski Musteri", "900.00.0004", "Sentetik Eski Musteri", 40_000))
    # below 30k total
    r.append(_row("2022-01-15", "900.00.0005", "Sentetik Kucuk", "900.00.0005", "Sentetik Kucuk", 12_000))
    r.append(_row("2023-01-15", "900.00.0005", "Sentetik Kucuk", "900.00.0005", "Sentetik Kucuk", 11_000))
    # active + shrinking; two rows with MISSING cost (None) for margin data-quality
    r.append(_row("2024-03-01", "900.00.0006", "Sentetik Aktif", "900.00.0006", "Sentetik Aktif", 50_000,
                  stok="SP-002", stokname="Sentetik Defter"))
    r.append(_row("2025-03-01", "900.00.0006", "Sentetik Aktif", "900.00.0006", "Sentetik Aktif", 40_000,
                  stok="SP-002", stokname="Sentetik Defter"))
    r.append(_row("2025-05-01", "900.00.0006", "Sentetik Aktif", "900.00.0006", "Sentetik Aktif", 20_000,
                  cost_net=None))
    r.append(_row("2025-09-01", "900.00.0006", "Sentetik Aktif", "900.00.0006", "Sentetik Aktif", 30_000))
    r.append(_row("2026-03-01", "900.00.0006", "Sentetik Aktif", "900.00.0006", "Sentetik Aktif", 20_000,
                  cost_net=None, stok="SP-002", stokname="Sentetik Defter"))
    r.append(_row("2026-06-30", "900.00.0006", "Sentetik Aktif", "900.00.0006", "Sentetik Aktif", 10_000))
    # KAPALI — would be T1
    for y in (2021, 2022, 2023):
        r.append(_row(f"{y}-06-01", "900.00.0007", "Sentetik Kapali Ltd", "900.00.0007", "Sentetik Kapali Ltd",
                      20_000, closed="KAPALI", stok="SP-002", stokname="Sentetik Defter"))
    # empty Pro_kodu -> invoice code fallback; T1-like lapsed under the invoice identity
    for y in (2023, 2024, 2025):
        r.append(_row(f"{y}-08-01", None, None, "900.00.0008", "Sentetik Fatura Musterisi", 11_000))
    return r


def stock_frame():
    rows = [
        # code, name, stock, coverage, min, max, inventory value, status
        ("SP-001", "Sentetik Silgi", 5, 0.5, 1.0, 3.0, 1_000, ""),
        ("SP-002", "Sentetik Defter", 1_000, 5.0, 1.0, 3.0, 250_000, ""),
        ("SP-003", "Sentetik Pergel", 300, 6.0, 1.0, 3.0, 90_000, "ÜRETİMDEN KALKTI"),
        ("SP-004", "Sentetik Cetvel", 2, 0.2, 1.0, 3.0, 400, "ÜRETİMDEN KALKTI"),
        ("SP-005", "Sentetik Kalemtras", 50, 2.0, 1.0, 3.0, 5_000, ""),
    ]
    return pd.DataFrame(rows, columns=["Stok_Kodu", "Stok_Adı", "TOPLAM STOK MİKTAR ", "STOK Kontrol %",
                                       "minseviye", "maxseviye", "Envanter değeri", "DURUM"])


AGING_COLUMNS = ["Hesap kodu", "Hesap adı", "Temsilci kodu", "Kasım-2025 ve öncesi", "Aralık-2025",
                 "Ocak-2026", "Şubat-2026", "Mart-2026", "Nisan-2026", "Mayıs-2026", "Haziran-2026",
                 "Toplam Döviz Bakiye"]


def aging_frame():
    def row(code, name, rep, buckets=None):
        d = {c: 0 for c in AGING_COLUMNS[3:]}
        d.update(buckets or {})
        return {"Hesap kodu": code, "Hesap adı": name, "Temsilci kodu": rep, **d}
    rows = [
        {"Hesap kodu": "TOPLAM", "Hesap adı": "ara toplam satırı", "Temsilci kodu": None,
         **{c: 999_999 for c in AGING_COLUMNS[3:]}},                                   # non-account row
        row("900.00.0007", "Sentetik Kapali Ltd", "TEMSILCI-A",
            {"Kasım-2025 ve öncesi": 50_000, "Mart-2026": 10_000, "Toplam Döviz Bakiye": 7_777}),
        row("900.00.0002", "Sentetik T2 Ofis", "TEMSILCI-B", {"Mayıs-2026": 8_000}),
        row("900.00.0006", "Sentetik Aktif", "TEMSILCI-A", {"Ocak-2026": 30_000, "Haziran-2026": 5_000}),
        row("900.00.0005", "Sentetik Kucuk", "TEMSILCI-A"),                             # zero balance
    ]
    return pd.DataFrame(rows, columns=AGING_COLUMNS)


def write_all(folder):
    """Write the three synthetic .xlsx files into folder (e.g. a pytest tmp dir)."""
    os.makedirs(folder, exist_ok=True)
    pd.DataFrame(sales_rows()).to_excel(os.path.join(folder, SALES_FILE), index=False)
    with pd.ExcelWriter(os.path.join(folder, STOCK_FILE)) as w:      # real file: header on row 4
        pd.DataFrame([["Sentetik stok raporu"], ["(test)"], [""]]).to_excel(w, index=False, header=False)
        stock_frame().to_excel(w, index=False, startrow=3)
    aging_frame().to_excel(os.path.join(folder, AGING_FILE), index=False)
    return folder
