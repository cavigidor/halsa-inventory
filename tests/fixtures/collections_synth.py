"""SYNTHETIC 'Evrak Detaylı Tahsilat Raporu' (invented codes, names and amounts only), in the verified layout.

Report date 05.11.2026. Accounts:
  120.90.0001  two documents: one red (order term passed), one invoice-due passed but order term not passed
  120.90.0002  one partially paid document (open < total); order term differs from the invoice term
  120.90.0003  one document without an order (order date blank, export writes W = 0) — order due unknown
  120.90.0004  one EUR document (TL column differs from Meblağ); one repeated document number (two lines)
"""
import datetime as dt
import os

import openpyxl
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill

HEADERS = ["Hesap kodu", "Hesap adı", "Grup kodu", "Temsilci kodu", "Evrak no", "Belge no", "Evrak tarihi",
           "Vade tarih", "Vade gün", "{d} için vade", "Meblağ", "Türü", "Evrak tutarı", "Satır Bakiye", "Orj. döviz",
           "Ana döviz TL bakiye", "Temsilci kodu", "Temsilci adı", "Proje kodu", "Sipariş No", "Sipariş Tarihi",
           "Sipariş Vadesi", "Sipariş ile Vade Tarihi  Gün Farkı"]
REPORT = dt.date(2026, 11, 5)
FILE = "05.11.2026 Evrak Detaylı Tahsilat Raporu.xlsx"


def _row(acct, docno, doc_date, term, open_amt, total, order_date, order_term, *, currency="TL", tl=None,
         legal="HLS0000000000001", project=None, order_no="SN-1001", name="Sentetik Hesap"):
    due = doc_date + dt.timedelta(days=term)
    w = ((order_date + dt.timedelta(days=order_term)) - REPORT).days if order_date else 0
    return [acct, name, "GRUP-A", "TEMSILCI-A", docno, legal, dt.datetime.combine(doc_date, dt.time()),
            dt.datetime.combine(due, dt.time()), term, (due - REPORT).days, open_amt, "Genel", total, None, currency,
            open_amt if tl is None else tl, "TEMSILCI-A", "Temsilci A ", project or acct, order_no if order_date else "",
            dt.datetime.combine(order_date, dt.time()) if order_date else None, order_term, w]


def rows():
    d = dt.date
    r = [
        _row("120.90.0001", "HALSA-10001", d(2026, 8, 1), 30, 1000.0, 1000.0, d(2026, 7, 30), 30),     # red
        _row("120.90.0001", "HALSA-10002", d(2026, 9, 20), 30, 500.5, 500.5, d(2026, 9, 18), 90,
             order_no="SN-1002"),                                                                        # due passed, order not
        _row("120.90.0002", "HALSA-10003", d(2026, 10, 1), 60, 300.0, 800.0, d(2026, 9, 30), 45,
             order_no="SN-1003"),                                                                        # partial
        _row("120.90.0003", "HALSA-10004", d(2026, 10, 25), 0, 250.0, 250.0, None, 0, legal=""),         # no order
        _row("120.90.0004", "HALSA-10005", d(2026, 6, 1), 120, 100.0, 100.0, d(2026, 5, 30), 120,
             currency="EUR", tl=3600.0, order_no="SN-1005"),
        _row("120.90.0004", "HALSA-10006", d(2026, 10, 2), 30, 40.0, 70.0, d(2026, 10, 1), 30, order_no="SN-1006"),
        _row("120.90.0004", "HALSA-10006", d(2026, 10, 2), 30, 30.0, 90.0, d(2026, 10, 1), 30, order_no="SN-1006"),
    ]
    by = {}
    for x in r:                                   # running balance per account, in file order
        by[x[0]] = by.get(x[0], 0) + x[10]
        x[13] = round(by[x[0]], 2)
    return r


def write(folder, name=FILE, *, j_date="05.11.2026", red_rule=True, mutate=None):
    os.makedirs(folder, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = name[:31].replace(".xlsx", "")
    ws.append([h.format(d=j_date) for h in HEADERS])
    data = rows()
    if mutate:
        data = mutate(data)
    for r in data:
        ws.append(r)
    if red_rule:
        ws.conditional_formatting.add(f"A2:A{len(data) + 400}", FormulaRule(
            formula=['AND($A2<>"",$W2<0)'], font=Font(bold=True, color="FFFFFFFF"),
            fill=PatternFill(start_color="FFC00000", end_color="FFC00000", fill_type="solid")))
    desc = wb.create_sheet("Kolon Açıklamaları")
    desc.append(["Sütun", "Başlık", "Açıklama"])
    desc.append(["A", "Hesap kodu", "Müşterinin cari hesap numarasıdır."])
    path = os.path.join(folder, name)
    wb.save(path)
    return path
