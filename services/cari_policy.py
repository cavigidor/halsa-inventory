"""M8-R5B1 / D-071: what the Cari aging export establishes, and what it does not. Not an overdue calculator.

The export's monthly buckets show how long ago invoices were issued (invoice age), never when payment was due.
Overdue status depends on each customer's payment agreement; the ERP report that shows it has not been supplied.
So nothing here derives overdue debt, due dates, collection urgency, customer risk or repayment warnings.

Facts kept: the signed balance exactly as the export carries it (decimals kept; blank buckets make it unknown, never
zero), the oldest month bucket holding a non-zero amount, and one confirmed account class: codes starting with 320 are
suppliers. No other prefix is classified (120 is not assumed to be a confirmed customer account).

Sign wording: only the owner-confirmed customer meaning is stated (negative customer balance = Halsa owes the
customer), and only where the invoice account is uniquely associated with that one customer and is not a 320 supplier
account (Customer 360). Everywhere else — shared invoice accounts, suppliers, unclassified accounts, the account-level
dashboard list — and for every positive balance (its meaning is not independently established), the sign is named
neutrally (positive / negative / zero).
"""
from decimal import Decimal, InvalidOperation

import pandas as pd

OVERDUE_RETIRED_CODE = "CARI-OVERDUE-UNSUPPORTED"
OVERDUE_RETIRED_REASON = ("Cari yaşlandırma dilimleri faturanın kesildiği aydan bu yana geçen süreyi gösterir; vade "
                          "tarihini göstermez. Vadesi geçen borç, tahsilat önceliği, risk sınıflaması ve ödeme uyarısı "
                          "bu rapordan çıkarılmaz. Vade müşteri sözleşmesine bağlıdır; bunu gösteren rapor henüz "
                          "sağlanmadı.")
BALANCE_NOTE = ("Bakiye kaynaktaki işaretiyle ve kuruşuyla gösterilir; işaret değiştirilmez. Müşteri hesabında negatif "
                "bakiye Halsa'nın müşteriye borcudur (sıfır borç veya hata değildir). 320 tedarikçi hesaplarında ve "
                "sınıflandırılmamış hesaplarda işaretin anlamı doğrulanmadı. Hiçbir bakiye vadesi geçmiş alacak "
                "anlamına gelmez.")
SUPPLIER_NOTE = "320 ile başlayan hesaplar tedarikçi hesaplarıdır; müşteri alacağı olarak gösterilmez."
UNCONFIRMED_NOTE = "Diğer hesapların (ör. 120) müşteri hesabı olduğu ayrıca doğrulanmadı."
OVERDUE_UNAVAILABLE = "Vadesi geçen bakiye (müşteri ödeme koşulları raporu sağlanmadı)"

SUPPLIER_PREFIX = "320"
CLASS_SUPPLIER, CLASS_UNCONFIRMED = "supplier", "unconfirmed"
CLASS_LABELS = {CLASS_SUPPLIER: "Tedarikçi (320)", CLASS_UNCONFIRMED: "Sınıflandırılmadı"}
# Neutral sign names (no debtor / creditor claim): suppliers, unclassified accounts, the account-level list.
DIRECTION_LABELS = {"debit": "Pozitif bakiye", "credit": "Negatif bakiye", "zero": "Sıfır bakiye",
                    None: "Bakiye bilinmiyor (boş tutar hücresi)"}
# Customer meaning (D-071 rule D: negative only), for a non-supplier invoice account uniquely associated with one
# customer (Customer 360). The positive side stays neutral until its meaning is independently established.
CUSTOMER_DIRECTION_LABELS = {**DIRECTION_LABELS, "credit": "Halsa müşteriye borçlu"}


def account_class(code) -> str:
    """Only the owner-confirmed rule (320 = supplier). The code is read as recorded, never trimmed (D-042)."""
    return CLASS_SUPPLIER if str(code).startswith(SUPPLIER_PREFIX) else CLASS_UNCONFIRMED


def direction_label(direction: str | None, account_class: str, customer_account: bool) -> str:
    """Customer wording only when `customer_account` (the invoice account belongs uniquely to this customer) and the
    account is not a 320 supplier account; neutral sign names otherwise."""
    if customer_account and account_class != CLASS_SUPPLIER:
        return CUSTOMER_DIRECTION_LABELS[direction]
    return DIRECTION_LABELS[direction]


def balance_direction(balance) -> str | None:
    if balance is None:
        return None
    return "debit" if balance > 0 else "credit" if balance < 0 else "zero"


def signed_balances(frame: pd.DataFrame, buckets: list[str], single: str | None) -> pd.Series:
    """Signed balance per row: the sum of the numeric bucket values, NaN (unknown) when any bucket is blank or
    non-numeric. A legacy single-balance column is read as recorded (NaN when blank). Values are never changed.
    The sum is taken in decimal on each value's shortest representation, so kuruş amounts add up exactly (no binary
    float residue such as 0.30000000000000004, and nothing is rounded to whole lira)."""
    if single:
        return pd.to_numeric(frame[single], errors="coerce")
    if not buckets or frame.empty:
        return pd.Series(float("nan"), index=frame.index, dtype=float)
    vals = frame[buckets].apply(lambda s: pd.to_numeric(s, errors="coerce"))

    def exact(row):
        if row.isna().any():
            return float("nan")
        try:
            total = sum((Decimal(repr(float(v))) for v in row), Decimal(0))
        except InvalidOperation:
            return float("nan")
        return float(total) + 0.0          # + 0.0 turns -0.0 into 0.0 (a genuine zero stays zero)
    return vals.apply(exact, axis=1)


def oldest_nonzero_bucket(row, buckets: list[str]) -> str | None:
    """Oldest month bucket (columns are ordered oldest → newest) holding a non-zero amount of either sign."""
    for b in buckets:
        v = pd.to_numeric(row[b], errors="coerce")
        if pd.notna(v) and v != 0:
            return b
    return None
