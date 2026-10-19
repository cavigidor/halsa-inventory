"""SYNTHETIC revised "kredi takip" workbook (M8-ACCURACY-2). Invented contracts, lenders and amounts only.

Mirrors the STRUCTURE the owner described for the private filtered workbook, never its content:
- row 1: summary formulas linked to the SUBTOTAL row (e.g. `=M<last>`), plus a visible-row count;
- row 2: the 23 ERP headers (same as the original export);
- rows 3..k: installments;
- row k+1: the ERP control-total row (first cell = installment count, amount columns = full-source sums);
- row k+2: Excel `SUBTOTAL(...)` formulas whose cached values are the totals of the VISIBLE rows;
- a saved AutoFilter on column L (VADE TARİHİ) that hides every row not selected by `visible`.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Callable

from tests import test_m6_kredi as TK
from tests.fixtures.xlsx_synth import F, MISSING, col_letter, write_xlsx

D = dt.date
NAME = "kredi takip.xlsx"
HEADERS = TK.HEADERS
SUM_HEADERS = ("KREDİ TUTARI", "TAKSİT TUTARI", "TAKSİT ANAPARA", "TAKSİT FAİZ", "TAKSİT BSMV", "TAKSİT KKDF",
               "ÖDENEN TAKSİT TUTARI", "KALAN TAKSİT TUTARI")
COL = {h: i + 1 for i, h in enumerate(HEADERS)}
L_COLID = COL["VADE TARİHİ"] - 1          # AutoFilter colId is 0-based


def rows_2026(today: dt.date):
    """Invented installments: paid, past-due unpaid, due today, future 2026 months, 2027, a negative residual on a
    Kapalı row, and a Kapalı row with a positive remaining amount."""
    i = TK.inst
    c1, c2 = TK.CONTRACTS
    out = [
        i(c1, 1, D(2026, 1, 10), paid=True),                                          # paid
        i(c1, 2, D(2026, 2, 10)),                                                     # past due, unpaid
        i(c1, 3, today),                                                              # due today
        i(c1, 4, D(2026, 11, 10)),
        i(c1, 5, D(2026, 12, 10), paid_amount=1000.0),                                # partly paid
        i(c1, 6, D(2027, 1, 10)),
        i(c2, 10, D(2026, 11, 20), lender=1, count=0, amount=250000.0),
        i(c2, 11, D(2026, 12, 20), lender=1, count=0, amount=250000.0, status="Kapalı"),   # Kapalı, positive remaining
        i(c2, 12, D(2026, 3, 20), lender=1, count=0, amount=250000.0, status="Kapalı",
          paid_amount=4120.11, remaining_amount=-9.0),                               # negative residual (synthetic)
    ]
    return out


def default_visible(row: dict) -> bool:
    """The saved view: November and December 2026 only (like a user-chosen date filter)."""
    d = row["VADE TARİHİ"]
    return d.year == 2026 and d.month in (11, 12)


def book(folder: Path, rows=None, *, today: dt.date = D(2026, 10, 19), visible: Callable[[dict], bool] = default_visible,
         name: str = NAME, stale_subtotal: bool = False, missing_subtotal_cache: bool = False,
         control_row: bool = True, subtotal_row: bool = True, row1: str = "linked", extra_row1_formula: str | None = None,
         headers=HEADERS) -> Path:
    rows = rows_2026(today) if rows is None else rows
    first, k = 3, 2 + len(rows)
    ctrl, last = k + 1, k + 2
    hidden = [first + n for n, r in enumerate(rows) if not visible(r)]
    vis = [r for r in rows if visible(r)]
    data = [[None] * len(headers), list(headers)] + [[r.get(h) for h in headers] for r in rows]
    if control_row:
        footer = TK.footer(rows)
        data.append([footer.get(h) for h in headers])
    if subtotal_row:
        sub = [None] * len(headers)
        a = f"A{first}:A{k}"
        sub[0] = F(f"SUBTOTAL(3,{a})", len(vis) + (1 if stale_subtotal else 0))
        for h in SUM_HEADERS:
            c = COL[h]
            letter = col_letter(c)
            total = round(sum(r[h] for r in vis), 2)
            if stale_subtotal and h == "TAKSİT TUTARI":
                total += 500.0
            sub[c - 1] = F(f"SUBTOTAL(9,{letter}{first}:{letter}{k})", MISSING if missing_subtotal_cache else total)
        data.append(sub)
        sub_row = len(data)
        top = data[0]
        if row1 == "linked":
            for h in ("TAKSİT TUTARI", "TAKSİT ANAPARA", "TAKSİT FAİZ", "TAKSİT BSMV", "TAKSİT KKDF"):
                c = COL[h]
                cached = sub[c - 1].cached
                top[c - 1] = F(f"{col_letter(c)}{sub_row}", cached)
        elif row1 == "literal":
            top[0] = "Toplam"                                              # not a formula: unsupported layout
        if extra_row1_formula:
            top[1] = F(extra_row1_formula, 1)
    folder.mkdir(parents=True, exist_ok=True)
    return write_xlsx(folder / name, {"kredi takip": data}, hidden_rows={"kredi takip": hidden},
                      auto_filter={"kredi takip": (f"A2:{col_letter(len(headers))}{ctrl if control_row else k}",
                                                   L_COLID)})
