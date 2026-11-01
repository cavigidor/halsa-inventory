"""M8-R5B1 / D-071: what the Cari aging export establishes, and what it does not. Not an overdue calculator.

The export's monthly buckets show how long ago invoices were issued (invoice age), never when payment was due.
Overdue status depends on each customer's payment agreement; the ERP report that shows it has not been supplied.
So nothing here derives overdue debt, due dates, collection urgency, customer risk or repayment warnings.

Facts kept: the signed balance exactly as the export carries it (decimals kept; blank buckets make it unknown, never
zero), the oldest month bucket holding a non-zero amount, and the owner-confirmed account classes (D-072 D4): codes
starting with 120 are customers (Müşteri), 320 suppliers (Satıcı); every other prefix stays unclassified. A class is
never an overdue status, a due date or a customer-level allocation.

Sign wording: only the owner-confirmed customer meaning is stated (negative customer balance = Halsa owes the
customer), and only for a 120 customer account that is the account holder's own (the account-level list) or that is
uniquely associated with the one customer shown (Customer 360; a shared invoice account is never attributed).
Everywhere else — suppliers, unclassified accounts, shared accounts — and for every positive balance (its meaning is
not independently established), the sign is named neutrally (positive / negative / zero).

D-072 D2: `Toplam Döviz Bakiye` is a USD balance. It is a separate source field: never added to, compared with or
converted into the month buckets, and not read by the balance calculation.
D-072 D1: the report's as-of date is the calendar date in the ORIGINAL file name; with no parseable date there, the
upload date (provenance recorded). Never the newest bucket, a sheet title or a file timestamp.
D-072 D3: the export is the DinamikCRM report and authoritative; the code reproduces its balances independently from
the workbook records (there is no workbook-native total row in this export, and none is invented).
"""
import datetime as dt
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

import pandas as pd

OVERDUE_RETIRED_CODE = "CARI-OVERDUE-UNSUPPORTED"
OVERDUE_RETIRED_REASON = ("Cari yaşlandırma dilimleri faturanın kesildiği aydan bu yana geçen süreyi gösterir; vade "
                          "tarihini göstermez. Vadesi geçen borç, tahsilat önceliği, risk sınıflaması ve ödeme uyarısı "
                          "bu rapordan çıkarılmaz. Vade müşteri sözleşmesine bağlıdır; bunu gösteren rapor henüz "
                          "sağlanmadı.")
BALANCE_NOTE = ("Bakiye kaynaktaki işaretiyle ve kuruşuyla gösterilir; işaret değiştirilmez. Müşteri (120) hesabında "
                "negatif bakiye Halsa'nın müşteriye borcudur (sıfır borç veya hata değildir). Pozitif bakiyenin anlamı "
                "ve 320 satıcı hesaplarında işaretin anlamı doğrulanmadı. Hiçbir bakiye vadesi geçmiş alacak anlamına "
                "gelmez.")
SUPPLIER_NOTE = "320 ile başlayan hesaplar satıcı (tedarikçi) hesaplarıdır; müşteri alacağı olarak gösterilmez."
UNCONFIRMED_NOTE = ("120 ile başlayan hesaplar müşteri hesaplarıdır; diğer önekler sınıflandırılmadı. Hesap türü vade "
                    "veya ödeme durumu göstermez.")
FX_COLUMN = "Toplam Döviz Bakiye"
FX_NOTE = ("'Toplam Döviz Bakiye' sütunu USD cinsindendir; ay dilimleriyle toplanmaz, karşılaştırılmaz, TL'ye "
           "çevrilmez ve bu bakiyede kullanılmaz.")
OVERDUE_UNAVAILABLE = "Vadesi geçen bakiye (müşteri ödeme koşulları raporu sağlanmadı)"

CUSTOMER_PREFIX, SUPPLIER_PREFIX = "120", "320"
CLASS_CUSTOMER, CLASS_SUPPLIER, CLASS_UNCONFIRMED = "customer", "supplier", "unconfirmed"
CLASS_LABELS = {CLASS_CUSTOMER: "Müşteri (120)", CLASS_SUPPLIER: "Satıcı (320)", CLASS_UNCONFIRMED: "Sınıflandırılmadı"}
# Neutral sign names (no debtor / creditor claim): suppliers, unclassified and shared accounts, positive balances.
DIRECTION_LABELS = {"debit": "Pozitif bakiye", "credit": "Negatif bakiye", "zero": "Sıfır bakiye",
                    None: "Bakiye bilinmiyor (boş tutar hücresi)"}
# Customer meaning (D-071 rule D: negative only), for a 120 customer account that is the holder's own / uniquely
# associated (D-072 D4). The positive side stays neutral until its meaning is independently established.
CUSTOMER_DIRECTION_LABELS = {**DIRECTION_LABELS, "credit": "Halsa müşteriye borçlu"}


def account_class(code) -> str:
    """Owner-confirmed prefixes only (D-072 D4: 120 customer, 320 supplier). The code is read as recorded, never
    trimmed (D-042): a code with leading whitespace is not classified."""
    c = str(code)
    if c.startswith(CUSTOMER_PREFIX):
        return CLASS_CUSTOMER
    if c.startswith(SUPPLIER_PREFIX):
        return CLASS_SUPPLIER
    return CLASS_UNCONFIRMED


def direction_label(direction: str | None, account_class: str, customer_account: bool) -> str:
    """Customer wording only for a 120 customer account when `customer_account` holds (the account-level list: the
    holder's own account; Customer 360: the invoice account belongs uniquely to the customer shown). Neutral sign names
    otherwise — and always for a positive balance (CUSTOMER_DIRECTION_LABELS keeps it neutral)."""
    if customer_account and account_class == CLASS_CUSTOMER:
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


# ---------------------------------------------------------------------------------------------
# D-072 D1: the report date from the ORIGINAL file name (upload date only when the name has no date)
# ---------------------------------------------------------------------------------------------
_TR_MONTHS = ("ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim",
              "aralik")
DATE_ORIGINS = ("declared", "filename", "upload_date")
DATE_ORIGIN_TR = {"declared": "beyan edildi", "filename": "dosya adındaki tarih (D-072)",
                  "upload_date": "dosya adında tarih yok — yükleme tarihi (D-072)"}


class AmbiguousFilenameDate(ValueError):
    """The file name holds an impossible date or several different dates: the date is not guessed."""


def _fold(text: str) -> str:
    """Case- and accent-insensitive Turkish folding for month names: 'EKİM', 'Ekim', 'ekım', 'Şubat', 'subat' match."""
    t = str(text).replace("İ", "i").replace("I", "ı").lower()
    t = t.translate(str.maketrans("ışğüöç", "isguoc"))
    return "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))


_DAY_MONTH_YEAR = re.compile(r"(?<!\d)(\d{1,2})[ ._-]*(" + "|".join(_TR_MONTHS) + r")[ ._-]*((?:19|20)\d{2})(?!\d)")
_ISO = re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)")
_DOTTED = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./]((?:19|20)\d{2})(?!\d)")


def filename_dates(name: str | None) -> set[dt.date]:
    """Every calendar date written in a file name ("5 Ekim 2026", "2026-10-05", "05.10.2026"). Only the name is read —
    never the workbook, a sheet title or a file timestamp. A written but impossible date (31 Şubat) raises: a typo is
    never silently replaced by the upload date."""
    if not name:
        return set()
    base = _fold(str(name).replace("\\", "/").rsplit("/", 1)[-1])
    found = set()
    for m in _DAY_MONTH_YEAR.finditer(base):
        found.add((int(m.group(3)), _TR_MONTHS.index(m.group(2)) + 1, int(m.group(1))))
    for m in _ISO.finditer(base):
        found.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in _DOTTED.finditer(base):
        found.add((int(m.group(3)), int(m.group(2)), int(m.group(1))))
    out = set()
    for y, mo, d in found:
        try:
            out.add(dt.date(y, mo, d))
        except ValueError:
            raise AmbiguousFilenameDate("dosya adındaki tarih geçerli bir takvim tarihi değil")
    return out


def report_date(original_name: str | None, upload_date: dt.date) -> tuple[dt.date, str]:
    """(as-of date, origin) under D-072 D1: the one date in the original file name ("filename"); no date there → the
    upload date ("upload_date"). Two different dates in one name are ambiguous: refused, never guessed."""
    dates = filename_dates(original_name)
    if len(dates) > 1:
        raise AmbiguousFilenameDate("dosya adında birden fazla farklı tarih var")
    if dates:
        return next(iter(dates)), "filename"
    return upload_date, "upload_date"


# ---------------------------------------------------------------------------------------------
# D-072: approved Cari column template (column identity only) and independent reproduction (D3)
# ---------------------------------------------------------------------------------------------
TEMPLATE_ID = "dinamikcrm_cari_yaslandirma_v1"
_HEADER_MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
                  "Aralık")
_BUCKET = re.compile(r"^(" + "|".join(_HEADER_MONTHS) + r")-(\d{4})( ve öncesi)?$")


def template_status(headers: list) -> str:
    """'approved' when the header row is exactly the owner-approved Cari layout: Hesap kodu, Hesap adı, Temsilci kodu,
    one '<Ay>-<YYYY> ve öncesi' bucket, then consecutive monthly buckets, then 'Toplam Döviz Bakiye' (USD). Anything
    else is 'not_matched' (mapping stays unknown; nothing is refused or reinterpreted)."""
    h = [str(x).strip() if x is not None else "" for x in headers]
    while h and h[-1] == "":
        h.pop()
    if len(h) < 6 or h[:3] != ["Hesap kodu", "Hesap adı", "Temsilci kodu"] or h[-1] != FX_COLUMN:
        return "not_matched"
    months = []
    for i, label in enumerate(h[3:-1]):
        m = _BUCKET.match(label)
        if m is None or bool(m.group(3)) != (i == 0):
            return "not_matched"
        months.append(int(m.group(2)) * 12 + _HEADER_MONTHS.index(m.group(1)))
    if any(b - a != 1 for a, b in zip(months, months[1:])):
        return "not_matched"
    return "approved"


def independent_balances(content: bytes) -> tuple[list, dict]:
    """(header row, {account code: exact signed balance or None}) read with openpyxl from the workbook bytes — a second,
    independent reader of the same records (the pipeline reads them with pandas). Same account rule (code starts with
    a digit), same exact decimal sum of the month buckets; a blank or non-numeric bucket makes the balance unknown."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        it = wb.worksheets[0].iter_rows(values_only=True)
        head = list(next(it, ()) or ())
        labels = [str(x).strip() if x is not None else "" for x in head]
        if "Hesap kodu" not in labels:
            return head, {}
        k = labels.index("Hesap kodu")
        bix = [i for i, x in enumerate(labels) if _BUCKET.match(x)]
        out = {}
        for r in it:
            code = r[k] if k < len(r) else None
            if code is None or not re.match(r"^\d", str(code)):
                continue
            vals = [r[i] if i < len(r) else None for i in bix]
            if not bix or any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in vals):
                out[str(code)] = None
                continue
            out[str(code)] = float(sum((Decimal(repr(float(v))) for v in vals), Decimal(0))) + 0.0
        return head, out
    finally:
        wb.close()
