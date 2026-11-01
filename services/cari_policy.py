"""M8-R5B1 / D-071: what the Cari aging export establishes, and what it does not. Not an overdue calculator.

The export's monthly buckets show how long ago invoices were issued (invoice age), never when payment was due.
Overdue status depends on each customer's payment agreement; the ERP report that shows it has not been supplied.
So nothing here derives overdue debt, due dates, collection urgency, customer risk or repayment warnings.

Facts kept: the signed balance exactly as the export carries it (blank buckets make it unknown, never zero), the
oldest month bucket holding a non-zero amount, and one confirmed account class: codes starting with 320 are
suppliers. No other prefix is classified (120 is not assumed to be a confirmed customer account).
"""
import pandas as pd

OVERDUE_RETIRED_CODE = "CARI-OVERDUE-UNSUPPORTED"
OVERDUE_RETIRED_REASON = ("Cari yaşlandırma dilimleri faturanın kesildiği aydan bu yana geçen süreyi gösterir; vade "
                          "tarihini göstermez. Vadesi geçen borç, tahsilat önceliği, risk sınıflaması ve ödeme uyarısı "
                          "bu rapordan çıkarılmaz. Vade müşteri sözleşmesine bağlıdır; bunu gösteren rapor henüz "
                          "sağlanmadı.")
BALANCE_NOTE = ("Bakiye kaynaktaki işaretiyle gösterilir. Pozitif: hesap sahibi Halsa'ya borçlu. Negatif: Halsa hesap "
                "sahibine borçlu (sıfır borç veya hata değildir). Hiçbiri vadesi geçmiş alacak anlamına gelmez.")
SUPPLIER_NOTE = "320 ile başlayan hesaplar tedarikçi hesaplarıdır; müşteri alacağı olarak gösterilmez."
UNCONFIRMED_NOTE = "Diğer hesapların (ör. 120) müşteri hesabı olduğu ayrıca doğrulanmadı."
OVERDUE_UNAVAILABLE = "Vadesi geçen bakiye (müşteri ödeme koşulları raporu sağlanmadı)"

SUPPLIER_PREFIX = "320"
CLASS_SUPPLIER, CLASS_UNCONFIRMED = "supplier", "unconfirmed"
CLASS_LABELS = {CLASS_SUPPLIER: "Tedarikçi (320)", CLASS_UNCONFIRMED: "Sınıflandırılmadı"}
DIRECTION_LABELS = {"debit": "Hesap sahibi Halsa'ya borçlu", "credit": "Halsa hesap sahibine borçlu",
                    "zero": "Bakiye sıfır", None: "Bakiye bilinmiyor (boş tutar hücresi)"}
CUSTOMER_DIRECTION_LABELS = {**DIRECTION_LABELS, "debit": "Müşteri Halsa'ya borçlu",
                             "credit": "Halsa müşteriye borçlu"}


def account_class(code) -> str:
    """Only the owner-confirmed rule (320 = supplier). The code is read as recorded, never trimmed (D-042)."""
    return CLASS_SUPPLIER if str(code).startswith(SUPPLIER_PREFIX) else CLASS_UNCONFIRMED


def balance_direction(balance) -> str | None:
    if balance is None:
        return None
    return "debit" if balance > 0 else "credit" if balance < 0 else "zero"


def signed_balances(frame: pd.DataFrame, buckets: list[str], single: str | None) -> pd.Series:
    """Signed balance per row: the sum of the numeric bucket values, NaN (unknown) when any bucket is blank or
    non-numeric. A legacy single-balance column is read as recorded (NaN when blank). Values are never changed."""
    if single:
        return pd.to_numeric(frame[single], errors="coerce")
    if not buckets:
        return pd.Series(float("nan"), index=frame.index)
    vals = frame[buckets].apply(lambda s: pd.to_numeric(s, errors="coerce"))
    return vals.sum(axis=1, min_count=len(buckets))


def oldest_nonzero_bucket(row, buckets: list[str]) -> str | None:
    """Oldest month bucket (columns are ordered oldest → newest) holding a non-zero amount of either sign."""
    for b in buckets:
        v = pd.to_numeric(row[b], errors="coerce")
        if pd.notna(v) and v != 0:
            return b
    return None
