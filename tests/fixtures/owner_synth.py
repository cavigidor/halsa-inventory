"""M7-6: synthetic owner workbooks (Kredi, Gider/Gelir, Bütçe) for the Yönetim tests.

Built with the M6 importer tests' own synthetic builders (invented contracts, lenders, expense names and amounts), so
the owner view is exercised against exactly the layouts those importers verify. No real data.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests import test_m6_butce as TB
from tests import test_m6_gider_gelir as TG
from tests import test_m6_kredi as TK

D = dt.date
KREDI_FILE = TK.NAME                      # "kredi sözleşmeleri.xlsx"
GG_FILE = TG.NAME                         # "2026_gider_gelir_Eylül.xlsx"
BUTCE_FILE = TB.NAME                      # "BUTCE_2026 Synth.xlsx"
#: invented identifiers that must never appear in any API payload / log (privacy checks)
SECRETS = (*TK.LENDER_NAMES, *TK.CONTRACTS, *TG.SECRET_CC, *TG.SECRET_NAMES, TB.SECRET_TEXT, *TB.GROUP_TITLES.values())


def kredi_rows(today: dt.date, *, currency2: str | None = None):
    """Installments around `today`: paid, past-due, partly paid (+10 d), +45 d, +80 d, +200 d; optional 2nd currency."""
    i = TK.inst
    c1, c2 = TK.CONTRACTS
    rows = [
        i(c1, 1, today - dt.timedelta(days=60), paid=True),                       # paid: never an obligation
        i(c1, 2, today - dt.timedelta(days=5)),                                    # past due (separate bucket)
        i(c1, 3, today + dt.timedelta(days=10), paid_amount=1000.0),               # partly paid: remaining only
        i(c1, 4, today + dt.timedelta(days=45)),
        i(c1, 5, today + dt.timedelta(days=80)),
        i(c1, 6, today + dt.timedelta(days=200)),
    ]
    cur = currency2 or "TL"
    rows += [i(c2, 10, today + dt.timedelta(days=20), lender=1, count=0, amount=250000.0, currency=cur),
             i(c2, 11, today + dt.timedelta(days=100), lender=1, count=0, amount=250000.0, currency=cur)]
    return rows


def write_kredi(folder: Path, today: dt.date, *, currency2: str | None = None, name: str = KREDI_FILE) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return TK.book(folder, kredi_rows(today, currency2=currency2), name=name)


def write_gider_gelir(folder: Path, name: str = GG_FILE, **kw) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return TG.book(folder, name=name, **kw)


def write_butce(folder: Path, name: str = BUTCE_FILE, **kw) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return TB.build(folder, name=name, **kw)


def write_broken(folder: Path, name: str) -> Path:
    """A file that is not a workbook at all (failure isolation)."""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(b"not an xlsx")
    return p
