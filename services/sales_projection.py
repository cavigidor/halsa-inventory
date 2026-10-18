"""M7-6 — the Customer 360 projection of the core sales frame, shared so the sales workbook is read only once.

Before M7-6 the Customer 360 build read the sales workbook a second time (`pd.read_excel`) right after
`build_context` had read the very same file. Now `build_context` computes this compact projection from the frame it
already holds (before `load_sales` modifies that frame) and hands it to the first Customer 360 build of that exact
context object. The projection function is unchanged (moved here from `services/customer360.py`), so the snapshot is
identical. No amounts are changed, nothing is trimmed (D-003), missing values stay missing.

The handoff is private runtime state: never a context dict item, never serialized, never pickled with data, never in
evidence packets or logs, and released after one use.
"""
from __future__ import annotations

import os
import threading

import pandas as pd

SALES_REQUIRED = ["Tarih", "Nettutar", "miktar", "sip_evrakno_seri", "sip_evrakno_sira", "Pro_kodu", "ProjeIsmi",
                  "Musterikod", "Musteri_ismi", "sto_kod", "StokIsmi"]


def prepare_sales(raw: pd.DataFrame) -> pd.DataFrame:
    """D-003 identity exactly as M5 (`dashboard_builder.load_sales`): Pro_kodu as written; Musterikod only when
    Pro_kodu is empty. No trimming of codes, no case changes. Amounts keep missing values (never zero)."""
    df = pd.DataFrame(index=raw.index)
    df["_date"] = pd.to_datetime(raw["Tarih"], errors="coerce")
    df["_yr"] = df["_date"].dt.year
    pk = raw["Pro_kodu"].astype(str).str.strip()
    has_pro = raw["Pro_kodu"].notna() & (pk != "") & (pk.str.lower() != "nan")
    df["_fallback"] = ~has_pro
    df["_cust"] = raw["Pro_kodu"].where(has_pro, raw["Musterikod"]).astype(str)
    df["_name_raw"] = raw["ProjeIsmi"].where(has_pro, raw["Musteri_ismi"])
    df["_inv"] = raw["Musterikod"].astype(str)
    df["_invname_raw"] = raw["Musteri_ismi"]
    df["_ord"] = raw["sip_evrakno_seri"].astype(str) + "|" + raw["sip_evrakno_sira"].astype(str)
    df["_amt"] = pd.to_numeric(raw["Nettutar"], errors="coerce")
    df["_amt_missing"] = df["_amt"].isna()
    df["_qty"] = pd.to_numeric(raw["miktar"], errors="coerce")
    df["_sku"] = raw["sto_kod"].astype(str)
    df["_skuname"] = raw["StokIsmi"].copy()
    df["_kategori"] = raw["kategori"].copy() if "kategori" in raw.columns else None
    df["_anagrup"] = raw["anagrup"].copy() if "anagrup" in raw.columns else None
    if "Satici" in raw.columns:
        df["_satici"] = raw["Satici"].where(raw["Satici"].notna() & (raw["Satici"].astype(str).str.strip() != ""))
    return df


class SalesFrameHandoff:
    """The projection of the sales frame `build_context` already read, for ONE Customer 360 build of that context.

    `take(basename)` returns the frame once (then releases it) when the basename matches the sales file the
    Customer 360 build resolved; otherwise None and the build reads the file itself exactly as before."""

    __slots__ = ("basename", "_frame", "_lock")

    def __init__(self, basename: str, frame: pd.DataFrame | None) -> None:
        self.basename = basename
        self._frame = frame
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self._frame is not None

    def take(self, basename: str) -> pd.DataFrame | None:
        with self._lock:
            if self._frame is None or basename != self.basename:
                return None
            frame, self._frame = self._frame, None
            return frame

    def __repr__(self) -> str:          # never prints rows
        return f"SalesFrameHandoff(available={self.available})"

    def __reduce__(self):               # never pickled with data
        return (SalesFrameHandoff, (self.basename, None))


def prepare_handoff(sales_path: str, raw: pd.DataFrame | None) -> SalesFrameHandoff | None:
    """Called by build_context with the pristine raw frame (before load_sales modifies it). None when the frame
    lacks a required column: Customer 360 then reads the file itself and reports the same error as before."""
    if raw is None or any(c not in raw.columns for c in SALES_REQUIRED):
        return None
    return SalesFrameHandoff(os.path.basename(sales_path), prepare_sales(raw))
