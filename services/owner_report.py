"""Private offline OWNER / FINANCIAL report (D-081): Cari, detailed collections, Kredi, Gider/Gelir, Bütçe.

`python -m services.owner_report --output NEW.html [--cari F] [--cari-date D] [--collections F] [--kredi F]
[--kredi-date D] [--gider-gelir F] [--gider-gelir-year Y] [--butce F] --butce-year Y [--as-of D]`

A separate file from the general report (which never reads any of these sources): account-level balances, receivable
documents and owner finances stay out of the general HTML entirely (D-075 / D-081). Every input is named explicitly
for its role (the owner's explicit source-type selection, like the Upload Center: file names are not used to
recognize a role), read once, fingerprinted, and checked again before the file is written. Each section reuses its
existing importer and view; nothing is recalculated beyond declared, labelled arithmetic. All sections are
preliminary review ("İnceleme için / doğrulanmadı"); no metric is certified, no action, ranking, AI, server or
database. A missing source is reported as missing, never as zero. New output only, mode 600, atomic exclusive create.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import os
import tempfile
from collections import defaultdict
from pathlib import Path

ROLES = ("cari", "collections", "kredi", "gider_gelir", "butce")
LABELS = {"cari": "Cari yaşlandırma", "collections": "Evrak Detaylı Tahsilat", "kredi": "Kredi takip",
          "gider_gelir": "Gider / Gelir", "butce": "Bütçe"}
REVIEW_VERIFICATION = "Çevrimdışı ön inceleme — sertifika yok"
MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


class OwnerReportError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _w(code, title, issue, evidence, question, resolution, severity="orta"):
    return dict(code=code, title=title, issue=issue, evidence=evidence, question=question, resolution=resolution,
                severity=severity)


def _review(metric, source, sha, formula, warnings, *, report_date=None, origin="beyan edilmedi", generation="",
            observed=(None, None)):
    from services import sales_review as SALES
    r = dict(metric=metric, status="PRELIMINARY", verification=REVIEW_VERIFICATION, source=source, sha256=sha,
             generation=generation, report_date=report_date, date_origin=origin, observed_start=observed[0],
             observed_end=observed[1], formula=formula, warnings=warnings)
    return SALES.warning_html(r)


def _money(k, l, exact=True, sign=False):
    c = {"k": k, "l": l, "money": 1}
    if exact:
        c["exact"] = 1
    if sign:
        c["sign"] = 1
    return c


def _n(k, l):
    return {"k": k, "l": l, "n": 1}


def _num(nv):
    v = getattr(nv, "value", nv)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _iso(v):
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


# ---------------------------------------------------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------------------------------------------------
def cari_section(src, coll, generation, declared_date):
    import pandas as pd
    import dashboard_builder as DB
    from services import cari_policy as CARI
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(src["data"]), read_only=True)
    try:
        headers = [c for c in next(wb.worksheets[0].iter_rows(max_row=1, values_only=True))]
    finally:
        wb.close()
    if CARI.template_status(headers) != "approved":
        raise OwnerReportError("Cari yaşlandırma dosyası onaylı düzende değil (Hesap kodu / Hesap adı / Temsilci kodu ve "
                               "ay sütunları gerekir); müşteri / temsilci listesi kullanılamaz.")
    rows, balances = DB.m_collections(None, pd.read_excel(io.BytesIO(src["data"])))
    buckets = CARI.calculation_buckets([str(h).strip() for h in headers if h is not None])
    open_by = defaultdict(float)
    in_coll = set()
    if coll is not None:
        for d in coll.documents:
            open_by[d.account_code] += d.open_amount_tl
            in_coll.add(d.account_code)
    equal = 0
    for r in rows:
        r["in_collections"] = "Evet" if r["code"] in in_coll else ("Hayır" if coll is not None else None)
        if r["code"] in in_coll:
            r["collections_open_tl"] = round(open_by[r["code"]], 2)
            same = r["balance"] is not None and abs(r["balance"] - r["collections_open_tl"]) <= 0.05
            r["matches_collections"] = "Aynı" if same else "Farklı"
            equal += same
    pos120 = sum(1 for r in rows if r["account_class"] == CARI.CLASS_CUSTOMER and (r["balance"] or 0) > 0)
    cov = sum(1 for r in rows if r["account_class"] == CARI.CLASS_CUSTOMER and (r["balance"] or 0) > 0
              and r["code"] in in_coll)
    date_dates = CARI.filename_dates(src["name"])
    if declared_date:
        rep, origin = declared_date.isoformat(), "beyan edildi"
    elif len(date_dates) == 1:
        rep, origin = next(iter(date_dates)).isoformat(), "dosya adı"
    else:
        rep, origin = None, "dosya adında tarih yok — beyan edilmedi"
    warnings = [
        _w("CARI-TARIH", "Cari raporunun tarihi", "Rapor tarihi dosya adından okunur (D-072); bu dosyanın adında tarih "
           "yok ve tarih beyan edilmedi." if rep is None else f"Rapor tarihi: {rep} ({origin}).",
           f"En yeni ay sütunu: {buckets[-1] if buckets else '—'}. "
           + (f"Detaylı tahsilat raporundaki {len(in_coll)} hesabın {equal} tanesinde açık evrak toplamı Cari bakiyesiyle "
              "kuruşuna kadar aynı (aynı gün alındığına işaret eder, kanıt değildir)." if coll is not None else ""),
           "Bu Cari yaşlandırma raporu hangi gün alındı?", "Tarihi `--cari-date` ile beyan edin.",
           "yuksek" if rep is None else "dusuk"),
        _w("CARI-YAS", "Ay dilimleri fatura yaşıdır, vade değil (D-071)", CARI.OVERDUE_RETIRED_REASON,
           f"Hesap: {len(rows)}; dilim sütunu: {len(buckets)}.", "—", "Vade sonucu yalnız anlaşmaya dayalı raporla incelenir "
           "(Tahsilat bölümleri).", "dusuk"),
        _w("CARI-ISARET", "İşaret, 320 ve döviz", " ".join([CARI.BALANCE_NOTE, CARI.SUPPLIER_NOTE, CARI.FX_NOTE]),
           f"Müşteri (120): {sum(r['account_class'] == CARI.CLASS_CUSTOMER for r in rows)}; Satıcı (320): "
           f"{sum(r['account_class'] == CARI.CLASS_SUPPLIER for r in rows)}; bakiye bilinmeyen: "
           f"{sum(r['balance'] is None for r in rows)}.", "—", "Bilgi amaçlıdır.", "dusuk"),
    ]
    if coll is not None:
        warnings.append(_w("CARI-KAPSAM", "Detaylı tahsilat raporu her hesabı kapsamıyor",
                           "Tahsilat raporunda bulunmayan bir hesabın açık evrağı olmadığı sonucu çıkarılmaz.",
                           f"Pozitif bakiyeli 120 hesap: {pos120}; tahsilat raporunda olan: {cov}.",
                           "Tahsilat raporu hangi filtreyle alındı (temsilci / grup / tutar)?",
                           "Filtreyi teyit edin.", "orta"))
    cols = [{"k": "name", "l": "Hesap", "sub": "code"}, {"k": "rep", "l": "Temsilci"},
            {"k": "class_label", "l": "Hesap Türü"}, _money("balance", "Bakiye (işaretli)", sign=True),
            {"k": "direction_label", "l": "Bakiye İşareti"}, {"k": "oldest", "l": "En Eski Fatura Ayı"}]
    if coll is not None:
        cols += [{"k": "in_collections", "l": "Tahsilat raporunda"},
                 _money("collections_open_tl", "Tahsilat açık TL"), {"k": "matches_collections", "l": "Cari ile"}]
    formula = ("Hesap başına kaynaktaki ay dilimlerinin işaretli toplamı (kuruşuyla); en eski sıfırdan farklı dilim = "
               "fatura yaşı. Sıralama hesap koduna göre (öncelik yok, toplam yok).")
    return {"title": "Cari Bakiye (fatura yaşı)", "rows": rows, "cols": cols, "filter": "rep", "page_size": 100,
            "note": formula, "review_html": _review("finance.cari", src["name"], src["sha"], formula, warnings,
                                                    report_date=rep, origin=origin, generation=generation)}, {
        "accounts": len(rows), "report_date": rep, "equal": equal, "pos120": pos120, "covered": cov}


def collections_sections(src, res, cari_bal, generation):
    c = res.counts
    rep = c.report_date.isoformat()
    by = defaultdict(list)
    for d in res.documents:
        by[d.account_code].append(d)
    acc_rows = []
    for code in sorted(by):
        ds = by[code]
        red = [d for d in ds if d.source_red]
        past = [d for d in ds if d.invoice_due_passed]
        row = {"code": code, "name": ds[0].account_name, "rep": ds[0].document_rep or ds[0].card_rep,
               "documents": len(ds), "open_tl": round(sum(d.open_amount_tl for d in ds), 2),
               "invoice_due_passed": len(past), "invoice_due_passed_tl": round(sum(d.open_amount_tl for d in past), 2),
               "red": len(red), "red_tl": round(sum(d.open_amount_tl for d in red), 2),
               "oldest_due": min(d.due_date for d in ds).isoformat(),
               "oldest_order_due": (min(d.order_due_date for d in ds if d.order_due_date).isoformat()
                                    if any(d.order_due_date for d in ds) else None),
               "unknown_order_due": sum(d.order_due_date is None for d in ds)}
        if cari_bal is not None:
            b = cari_bal.get(code)
            row["cari_balance"] = b
            row["cari_match"] = ("Cari'de yok" if code not in cari_bal else "Bilinmiyor" if b is None else
                                 "Aynı" if abs(b - row["open_tl"]) <= 0.05 else "Farklı")
        acc_rows.append(row)
    doc_rows = [{"excel_row": d.source_row, "code": d.account_code, "name": d.account_name,
                 "rep": d.document_rep or d.card_rep, "project": d.project_code, "document_no": d.document_no,
                 "legal_document_no": d.legal_document_no, "document_date": d.document_date.isoformat(),
                 "due_date": d.due_date.isoformat(), "term_days": d.term_days, "days_to_due": d.days_to_due,
                 "open_tl": d.open_amount_tl, "document_total": d.document_total, "currency": d.currency,
                 "order_no": d.order_no, "order_date": _iso(d.order_date), "order_term_days": d.order_term_days,
                 "order_due_date": _iso(d.order_due_date), "days_to_order_due": d.days_to_order_due,
                 "red": "Kırmızı" if d.source_red else ("Bilinmiyor" if d.order_due_date is None else ""),
                 "status": "Kısmen açık" if d.partially_open else "Tamamı açık"} for d in res.documents]
    iss = res.issues_by_account

    def codes(k, n=30):
        xs = iss.get(k, [])
        return ", ".join(xs[:n]) + (f" … (+{len(xs) - n})" if len(xs) > n else "")
    eq = sum(1 for r in acc_rows if r.get("cari_match") == "Aynı")
    warnings = [
        _w("TAH-KIRMIZI", "Kırmızı = sipariş vadesi geçmiş (kaynağın kendi kuralı); onaylı vade sonucu değil",
           "Kaynak, 'Sipariş Tarihi + Sipariş Vadesi' rapor tarihinden önceyse hesap kodunu kırmızı gösterir "
           f"(koşullu biçim: {c.source_red_rule or 'bulunamadı'}). Sipariş vadesi siparişe girilen ödeme anlaşmasıdır; "
           "bağlayıcı anlaşma olduğu babanız tarafından teyit edilmedi. Tahsilat önceliği, risk veya aksiyon üretilmez; "
           "genel toplam verilmez.",
           f"Kırmızı satır: {c.source_red_rows}; hesap: {c.source_red_accounts}. Fatura vadesi geçmiş satır (J<0): "
           f"{c.invoice_due_passed_rows}. Sipariş vadesi fatura vadesinden farklı satır: "
           f"{c.order_term_differs_from_invoice_term}. J ve W sütunları yeniden hesapla birebir: J farkı "
           f"{c.days_to_due_mismatch}, W farkı {c.days_to_order_due_mismatch}.",
           "Kırmızı işaret (sipariş vadesi) mi, fatura vadesi mi esas alınmalı? Siparişe girilmeyen özel anlaşmalar var mı?",
           "Esas alınacak vadeyi belirtin; o zamana kadar tutarlar ön incelemedir.", "yuksek"),
        _w("TAH-SIPARISSIZ", "Siparişi olmayan evrak: sipariş vadesi bilinmiyor",
           "Sipariş tarihi boş satırlarda dışa aktarım W = 0 yazar; bu 'bugün vadeli' değil, bilinmiyor demektir.",
           f"Satır: {c.rows_without_order_date}; hesaplar: {codes('no_order_date')}.",
           "Bu evraklar hangi anlaşmaya göre vadelenir?", "ERP'de evrağın siparişini kontrol edin."),
        _w("TAH-TEKRAR", "Aynı evrak numarası birden çok satırda",
           "Satırlar ayrı tutulur, birleştirilmez veya silinmez.",
           f"Evrak numarası: {c.repeated_document_numbers}; hesaplar: {codes('repeated_document_no')}.",
           "Aynı evrakın iki satırı farklı belgeler mi (taksit / kısmi fatura)?", "ERP evrak ekranında kontrol edin."),
        _w("TAH-BAKIYE", "'Satır Bakiye' hesap içi birikimli toplamla uyuşmuyor",
           "Bilgi sütunudur; hesap toplamları 'Ana döviz TL bakiye' sütunundan hesaplanır.",
           f"Hesap: {c.running_balance_accounts_inconsistent}; {codes('running_balance')}.",
           "Satır bakiyesi rapor dışı hareketleri de içeriyor mu?", "Bilgi amaçlıdır.", "dusuk"),
        _w("TAH-DOVIZ-BELGE", "Döviz ve belge numarası",
           "TL toplamlar 'Ana döviz TL bakiye' sütunuyla yapılır; 'Meblağ' dövizli satırda orijinal dövizdir.",
           f"Döviz satırı: {c.non_tl_rows} ({', '.join(c.currencies)}); 'Belge no' boş satır: "
           f"{c.blank_legal_document_numbers}.", "Belge numarası boş evraklar henüz e-faturalanmadı mı?",
           "Bilgi amaçlıdır.", "dusuk"),
        _w("TAH-KIMLIK", "Hesap kodu fatura hesabıdır; Proje kodu siparişi veren müşteridir",
           "Satış panolarındaki müşteri kimliği (Pro_kodu) ile bu rapordaki 'Proje kodu' aynı alandır; 'Hesap kodu' "
           "Cari / fatura hesabıdır. Birleştirme yalnız birebir kodla yapılır.",
           f"Evrak: {c.documents}; hesap: {c.accounts}."
           + (f" Cari'deki bakiyeyle aynı hesap: {eq} / {len(acc_rows)}." if cari_bal is not None else ""),
           "Bir fatura hesabı birden çok müşteriye fatura kesiyorsa tahsilat kimden istenir?", "Bilgi amaçlıdır.", "dusuk"),
    ]
    formula_acc = ("Hesap başına: açık TL = Σ 'Ana döviz TL bakiye'; fatura vadesi geçmiş = J < 0; kırmızı = kaynak kuralı "
                   "W < 0 (yalnız sipariş tarihi varken). Sıralama hesap koduna göre; genel toplam yok.")
    formula_doc = ("Her satır bir açık evrak satırıdır. Vadeye kalan gün = vade tarihi − rapor tarihi; sipariş vadesine "
                   "kalan gün = sipariş tarihi + sipariş vadesi − rapor tarihi (siparişsiz satırda bilinmiyor).")
    review_acc = _review("collections.detailed", src["name"], src["sha"], formula_acc, warnings, report_date=rep,
                         origin=c.report_date_origin, generation=generation)
    review_doc = _review("collections.detailed", src["name"], src["sha"], formula_doc, warnings, report_date=rep,
                         origin=c.report_date_origin, generation=generation)
    acc_cols = [{"k": "name", "l": "Hesap", "sub": "code"}, {"k": "rep", "l": "Temsilci"}, _n("documents", "Açık evrak"),
                _money("open_tl", "Açık TL"), _n("invoice_due_passed", "Fatura vadesi geçmiş"),
                _money("invoice_due_passed_tl", "Fatura vadesi geçmiş TL"), _n("red", "Kırmızı (sipariş vadesi)"),
                _money("red_tl", "Kırmızı TL"), {"k": "oldest_due", "l": "En eski fatura vadesi"},
                {"k": "oldest_order_due", "l": "En eski sipariş vadesi"}]
    if cari_bal is not None:
        acc_cols += [_money("cari_balance", "Cari bakiye", sign=True), {"k": "cari_match", "l": "Cari ile"}]
    doc_cols = [_n("excel_row", "Excel satırı"), {"k": "name", "l": "Hesap", "sub": "code"}, {"k": "rep", "l": "Temsilci"},
                {"k": "project", "l": "Proje (müşteri) kodu"}, {"k": "document_no", "l": "Evrak no"},
                {"k": "document_date", "l": "Evrak tarihi"}, {"k": "due_date", "l": "Vade tarihi"},
                _n("days_to_due", "Vadeye gün"), _money("open_tl", "Açık TL"), _money("document_total", "Evrak tutarı"),
                {"k": "currency", "l": "Döviz"}, {"k": "order_no", "l": "Sipariş no"},
                {"k": "order_due_date", "l": "Sipariş vadesi"}, _n("days_to_order_due", "Sipariş vadesine gün"),
                {"k": "red", "l": "Kaynak işareti"}, {"k": "status", "l": "Durum"}]
    return ({"title": "Tahsilat — Hesap Özeti", "rows": acc_rows, "cols": acc_cols, "filter": "rep", "page_size": 100,
             "note": formula_acc, "review_html": review_acc},
            {"title": "Tahsilat — Evraklar", "rows": doc_rows, "cols": doc_cols, "filter": "rep", "page_size": 100,
             "note": formula_doc, "review_html": review_doc},
            {"accounts": len(acc_rows), "documents": len(doc_rows), "red_rows": c.source_red_rows,
             "red_accounts": c.source_red_accounts, "cari_equal": eq if cari_bal is not None else None})


def kredi_sections(src, res, generation, as_of):
    from services import owner_view as OV
    v = OV.build_kredi_view(res)
    sched = OV.kredi_schedule(v, as_of)
    srows = []
    for s in sched:
        cur = s["currency"]
        for h in s["horizons"]:
            srows.append({"currency": cur, "window": f"{as_of.isoformat()} → +{h['days']} gün", "installments":
                          h["installments"], "amount": h["amount"]})
        srows.append({"currency": cur, "window": "+90 gün sonrası", "installments": s["after_90"]["installments"],
                      "amount": s["after_90"]["amount"]})
        srows.append({"currency": cur, "window": "Vade tarihi geçmiş, kalan tutarı olan",
                      "installments": s["past_due"]["installments"], "amount": s["past_due"]["amount"]})
        srows.append({"currency": cur, "window": "Toplam kalan (aynı para birimi)",
                      "installments": s["total_remaining"]["installments"], "amount": s["total_remaining"]["amount"]})
    con = {k.contract_key: k for k in res.contracts}
    irows = []
    for i in res.installments:
        rem = _num(i.remaining_amount)
        if rem is None or rem == 0:
            continue
        k = con[i.contract_key]
        irows.append({"excel_row": i.source_row, "contract": k.contract_code, "lender": k.lender_code,
                      "currency": k.currency, "installment": i.installment_number, "due_date": _iso(i.due_date.value),
                      "installment_total": _num(i.installment_total), "remaining": rem,
                      "remaining_principal": _num(i.remaining_principal), "status": i.status.value,
                      "payment_date": _iso(i.payment_date.value)})
    irows.sort(key=lambda r: (r["currency"], r["due_date"] or "", r["contract"], r["installment"]))
    c = res.counts
    warnings = [_w(w["code"], w["message"].split(".")[0], w["message"], f"Sayı: {w['count']}.",
                   "ERP'de bu taksitleri kontrol edin.", "Kaynak değeri korunur.") for w in v.warnings]
    warnings.insert(0, _w("KREDI-TARIH", "Kredi dosyasının anlık tarihi bilinmiyor",
                          "Takvim pencereleri rapor için seçilen tarihten itibaren sayılır; kaynak tarihi değildir.",
                          f"Pencere başlangıcı: {as_of.isoformat()}; son kayıtlı ödeme fişi: {v.latest_payment_date or '—'}; "
                          f"anlık tarih: {v.snapshot_date or 'beyan edilmedi'}.",
                          "Bu dosya hangi gün alındı?", "Tarihi `--kredi-date` ile beyan edin.",
                          "orta" if not v.snapshot_date else "dusuk"))
    if c.negative_remaining_rows:
        warnings.append(_w("KREDI-NEGATIF", "Negatif kalan taksit tutarı",
                           "Kaynaktaki gibi tutulur; sıfırlanmaz, düzeltilmez.", f"Satır: {c.negative_remaining_rows}.",
                           "Bu taksit fazla mı ödendi, yoksa kayıt hatası mı?", "ERP'de kontrol edin."))
    formula = ("Ödenmemiş yükümlülük = 'KALAN TAKSİT TUTARI' > 0 (ödeme alanları doğrulanmıştır; DURUM doğrulanmamış bir "
               "alandır ve yükümlülüğü kaldırmaz). Para birimleri asla toplanmaz; döviz kuru kullanılmaz.")
    head = dict(source=src["name"], sha=src["sha"], formula=formula, warnings=warnings,
                report_date=v.snapshot_date, origin=v.snapshot_origin or "beyan edilmedi", generation=generation)
    return ({"title": "Kredi — Ödeme Takvimi", "rows": srows,
             "cols": [{"k": "currency", "l": "Para birimi"}, {"k": "window", "l": "Pencere"},
                      _n("installments", "Taksit"), _money("amount", "Kalan tutar")], "note": formula,
             "review_html": _review("owner.kredi", head["source"], head["sha"], formula, warnings,
                                    report_date=head["report_date"], origin=head["origin"], generation=generation)},
            {"title": "Kredi — Açık Taksitler", "rows": irows, "page_size": 100,
             "cols": [_n("excel_row", "Excel satırı"), {"k": "contract", "l": "Sözleşme"}, {"k": "lender", "l": "Banka kodu"},
                      {"k": "currency", "l": "Para birimi"}, _n("installment", "Taksit no"), {"k": "due_date", "l": "Vade"},
                      _money("installment_total", "Taksit tutarı"), _money("remaining", "Kalan", sign=True),
                      _money("remaining_principal", "Kalan anapara"), {"k": "status", "l": "DURUM (doğrulanmadı)"},
                      {"k": "payment_date", "l": "Ödeme fişi tarihi"}],
             "note": "Kalan tutarı sıfırdan farklı her taksit; kaynak satırıyla.",
             "review_html": _review("owner.kredi", head["source"], head["sha"], formula, warnings,
                                    report_date=head["report_date"], origin=head["origin"], generation=generation)},
            {"contracts": v.contracts, "installments": v.installments, "open_installments": len(irows)})


def gider_gelir_sections(src, res, generation):
    from services import owner_view as OV
    v = OV.build_gider_gelir_view(res)
    months = [{"month": MONTHS[m["month"] - 1], **{k: m[k] for k in ("reported_sales", "reported_cost",
                                                                      "reported_gross_profit", "reported_expense")}}
              for m in v.months]
    lines = []
    for l, p in zip(v.lines, v.allocation_partial):
        lines.append({"label": l["label"], "reported_sales": l["reported_sales"], "reported_cost": l["reported_cost"],
                      "reported_gross_profit": l["reported_gross_profit"],
                      "allocated_complete": l["allocated_expense"],
                      "allocated_consistent_rows": p["allocated_expense_consistent_rows"],
                      "allocation_status": "Tam" if p["complete"] else
                      f"EKSİK — {len(v.allocation_excluded)} tutarsız satır hariç"})
    warnings = [_w(w["code"], w["message"].split(".")[0][:90], w["message"], f"Sayı: {w['count']}." if w["count"] else "—",
                   "Kaynak çalışma kitabında kontrol edin.", "Kaynak değeri korunur; tahmin edilmez.")
                for w in v.warnings]
    if v.allocation_excluded:
        rows_txt = ", ".join(f"{x['excel_row']} (oran toplamı {x['ratio_sum']}, fark {x['difference']})"
                             for x in v.allocation_excluded)
        warnings.insert(0, _w("GG-ORAN", "İş kolu oranları 1'e eşit olmayan gider satırları",
                              "Bu satırların aylık dağıtımları ay toplamına eşit değil. Satırın gider toplamı korunur; iş "
                              "kolu dağıtımları iş kolu toplamlarına katılmaz, bu yüzden iş kolu dağıtılmış gideri 'EKSİK' "
                              "olarak gösterilir (tam toplam gibi gösterilmez).", f"Excel satırları: {rows_txt}.",
                              "Bu satırların doğru oranları nedir (ör. %70 / %30)?",
                              "Kaynakta oranları düzeltin ve raporu yeniden oluşturun.", "yuksek"))
    year = v.report_year
    formula = (f"Kaynaktaki değerler ({year}); yeniden hesaplanmaz. Ay gideri = gider tablosundaki satırların o ay toplamı. "
               "İş kolu dağıtılmış gider = tutarlı satırların aylık dağıtımları. Muhasebe esası ve yönetim eşlemesi "
               "doğrulanmadı.")
    rv = _review("owner.gider_gelir", src["name"], src["sha"], formula, warnings, report_date=None,
                 origin=f"rapor yılı {year} ({v.report_year_origin})", generation=generation)
    out = [{"title": "Gider/Gelir — Aylar", "rows": months, "note": formula, "review_html": rv,
            "cols": [{"k": "month", "l": "Ay"}, _money("reported_sales", "Satış"), _money("reported_cost", "Satış maliyeti"),
                     _money("reported_gross_profit", "Brüt kâr", sign=True), _money("reported_expense", "Gider", sign=True)]},
           {"title": "Gider/Gelir — İş Kolları", "rows": lines, "note": formula, "review_html": rv,
            "cols": [{"k": "label", "l": "İş kolu (kaynak)"}, _money("reported_sales", "Satış"),
                     _money("reported_cost", "Satış maliyeti"), _money("reported_gross_profit", "Brüt kâr", sign=True),
                     _money("allocated_complete", "Dağıtılmış gider (tam)", sign=True),
                     _money("allocated_consistent_rows", "Tutarlı satırların dağıtımı (kısmi)", sign=True),
                     {"k": "allocation_status", "l": "Dağıtım durumu"}]}]
    if v.allocation_excluded:
        out.append({"title": "Gider/Gelir — Tutarsız Satırlar", "rows": v.allocation_excluded, "note": formula,
                    "review_html": rv,
                    "cols": [_n("excel_row", "Excel satırı"), {"k": "cost_center", "l": "Gider yeri"},
                             {"k": "expense_name", "l": "Masraf"}, _n("ratio_sum", "Oran toplamı"),
                             _money("reported_expense_total", "Aylık giderler toplamı", sign=True),
                             _money("allocations_total", "Dağıtımlar toplamı", sign=True),
                             _money("difference", "Fark", sign=True)]})
    return out, {"months": len(months), "excluded_rows": [x["excel_row"] for x in v.allocation_excluded]}


def butce_section(src, res, generation):
    from services import owner_view as OV
    h = OV.build_butce_view(res).health
    rows = []
    for f in res.facts:
        rows.append({"region": f.region.value, "block": f.block.value if f.block else None, "year": f.year,
                     "scenario": f.scenario.value, "month": MONTHS[f.month - 1] if f.month else None,
                     "label": f.source_label, "component": f.component, "line": f.line_label,
                     "value": _num(f.value) if f.usable else None, "validity": f.validity.value,
                     "flags": "; ".join(x for x, on in (("dış bağlantı", f.external_dependency),
                                                       ("başlık çelişkisi", f.title_conflict)) if on),
                     "cell": f.source_cell})
    warnings = [
        _w("BUTCE-YETKI", "Bütçe yetkili kaynak değildir", "Kayıt dosyası kararı açık; değerler kaynaktaki gibidir.",
           f"Durum: {h['source_status']}; kapsam: %{h['coverage_pct']}; kullanılabilir hücre: {h['usable_unverified']}; "
           f"geçersiz: {h['invalid_cells']} (#REF!: {h['ref_errors']}).", "Hangi bütçe dosyası esas alınır?",
           "Geçersiz hücreler gösterilmez (—).", "orta"),
        _w("BUTCE-DIS", "Başka çalışma kitabına bağlantı",
           "Bağlı dosya açılmaz; bağlı hücrelerin değeri kullanılmaz.",
           f"Dış formül hücresi: {h['external_formula_cells']}; etkilenen değer: {h['external_dependent_cells']}.",
           "Bağlı 'gider gelir TASLAK' dosyası hâlâ gerekli mi?", "Bağlantıyı kaynakta değere çevirin.", "dusuk"),
        _w("BUTCE-YIL", "Planlama yılı beyan edildi", "Dosya adında yıl yok; yıl rapor oluşturulurken beyan edildi.",
           f"Planlama yılı: {h['planning_year']} ({h['planning_year_origin']}).", "—", "Bilgi amaçlıdır.", "dusuk"),
    ]
    formula = "Hücre düzeyinde kaynak değerleri; Excel'in son hesapladığı değerler, yeniden hesaplanmaz. Geçersiz hücre = —."
    return {"title": "Bütçe — Hücreler", "rows": rows, "page_size": 100, "note": formula,
            "review_html": _review("owner.butce", src["name"], src["sha"], formula, warnings,
                                   origin=f"planlama yılı {h['planning_year']}", generation=generation),
            "cols": [{"k": "region", "l": "Bölge"}, {"k": "block", "l": "Blok"}, _n("year", "Yıl"),
                     {"k": "scenario", "l": "Senaryo"}, {"k": "month", "l": "Ay"}, {"k": "label", "l": "Kaynak etiketi"},
                     {"k": "component", "l": "Bileşen"}, {"k": "line", "l": "Satır"}, _money("value", "Değer", sign=True),
                     {"k": "validity", "l": "Geçerlilik"}, {"k": "flags", "l": "Uyarı"}, {"k": "cell", "l": "Hücre"}]}, h


# ---------------------------------------------------------------------------------------------------------------------
def _read(role, path):
    p = Path(path)
    if not p.is_file() or p.suffix.lower() != ".xlsx":
        raise OwnerReportError(f"{LABELS[role]} dosyası bulunamadı veya .xlsx değil.")
    data = p.read_bytes()
    return {"role": role, "path": str(p), "name": p.name, "data": data, "sha": _sha(data)}


def generate(output, *, cari=None, cari_date=None, collections=None, kredi=None, kredi_date=None, gider_gelir=None,
             gider_gelir_year=None, butce=None, butce_year=None, as_of=None, today=None):
    import dashboard_builder as DB
    from services.importers.base import explicit_source_type
    from services.importers import butce as BU, gider_gelir as GG, kredi as KR
    from services.importers.detailed_collections import import_detailed_collections
    today = today or dt.date.today()
    as_of = as_of or today
    target = Path(output).absolute()
    if target.suffix.lower() != ".html":
        raise OwnerReportError("Çıktı yeni bir .html dosyası olmalı.")
    if target.exists() or target.is_symlink():
        raise OwnerReportError("Çıktı dosyası zaten var; yeni bir ad seçin.")
    given = {k: v for k, v in dict(cari=cari, collections=collections, kredi=kredi, gider_gelir=gider_gelir,
                                   butce=butce).items() if v}
    if not given:
        raise OwnerReportError("En az bir kaynak dosya seçilmeli.")
    if butce and not butce_year:
        raise OwnerReportError("Bütçe dosya adında yıl yok: planlama yılını --butce-year ile beyan edin.")
    srcs = {role: _read(role, p) for role, p in given.items()}
    if any(Path(s["path"]).resolve() == target.resolve() for s in srcs.values()):
        raise OwnerReportError("Çıktı bir girdi dosyası olamaz.")
    generation = _sha("".join(f"{r}:{srcs[r]['sha']}" for r in sorted(srcs)).encode())
    modules, counts, scope = {}, {}, []
    with explicit_source_type():                       # roles are chosen explicitly; file names are not used
        coll = None
        if "collections" in srcs:
            s = srcs["collections"]
            coll = import_detailed_collections(s["path"], content=s["data"])
            if not coll.ok:
                raise OwnerReportError("Detaylı tahsilat raporu doğrulanamadı: " +
                                       ", ".join(i.code for i in coll.report.issues if i.severity.value == "error"))
        cari_bal = None
        if "cari" in srcs:
            modules["cari"], counts["cari"] = cari_section(srcs["cari"], coll, generation, cari_date)
            cari_bal = {r["code"]: r["balance"] for r in modules["cari"]["rows"]}
        if coll is not None:
            a, d, counts["collections"] = collections_sections(srcs["collections"], coll, cari_bal, generation)
            modules["tahsilat_hesap"], modules["tahsilat_evrak"] = a, d
        if "kredi" in srcs:
            s = srcs["kredi"]
            res = KR.import_kredi(s["path"], snapshot_date=kredi_date, content=s["data"])
            if not res.ok:
                raise OwnerReportError("Kredi dosyası doğrulanamadı.")
            modules["kredi_takvim"], modules["kredi_taksit"], counts["kredi"] = kredi_sections(s, res, generation, as_of)
        if "gider_gelir" in srcs:
            s = srcs["gider_gelir"]
            year = gider_gelir_year or (GG.parse_filename(s["name"]) or (None,))[0]
            if not year:
                raise OwnerReportError("Gider/Gelir rapor yılını --gider-gelir-year ile beyan edin.")
            res = GG.import_gider_gelir(s["path"], report_year=year)
            if not res.ok:
                raise OwnerReportError("Gider/Gelir doğrulanamadı: " +
                                       ", ".join(sorted(set(i.code for i in res.report.issues if i.severity.value == "error"))))
            secs, counts["gider_gelir"] = gider_gelir_sections(s, res, generation)
            for key, m in zip(("gg_ay", "gg_kol", "gg_haric"), secs):
                modules[key] = m
        if "butce" in srcs:
            s = srcs["butce"]
            res = BU.import_butce(s["path"], planning_year=butce_year)
            if not res.ok:
                raise OwnerReportError("Bütçe doğrulanamadı.")
            modules["butce"], counts["butce"] = butce_section(s, res, generation)
    for role in ROLES:
        scope.append({"source": LABELS[role], "file": srcs[role]["name"] if role in srcs else None,
                      "sha": srcs[role]["sha"][:16] if role in srcs else None,
                      "state": "Okundu (ön inceleme)" if role in srcs else "Kaynak verilmedi — bölüm yok (sıfır değil)"})
    scope.append({"source": "Genel rapor (Stok / Satış / B2B / Hedef / Trodat)", "file": None, "sha": None,
                  "state": "Ayrı dosya; bu raporda yok. Genel rapor bu kaynakların hiçbirini okumaz."})
    modules["scope"] = {"title": "Kaynaklar / Kapsam", "rows": scope, "note": "Her kaynak açıkça seçilir; SHA-256 ilk 16 hane.",
                        "cols": [{"k": "source", "l": "Kaynak"}, {"k": "file", "l": "Dosya"}, {"k": "sha", "l": "SHA-256"},
                                 {"k": "state", "l": "Durum"}]}
    stats = []
    if "cari" in counts:
        stats.append(("cari hesap", f"{counts['cari']['accounts']:,}"))
    if "collections" in counts:
        stats += [("açık evrak", f"{counts['collections']['documents']:,}"),
                  ("kırmızı işaretli hesap", f"{counts['collections']['red_accounts']:,}")]
    if "kredi" in counts:
        stats.append(("açık kredi taksiti", f"{counts['kredi']['open_installments']:,}"))
    meta = {"date": f"{today.day} {DB.AYLAR[today.month]} {today.year}", "stock": "", "sales": "", "aging": "",
            "has_aging": False, "tiers": {}, "total": None, "total_shown": False, "show_tiers": False,
            "withheld_stats": [], "withheld_panels": {}, "accuracy_policy": "owner-offline-review", "generation": generation,
            "asof": "", "displayed": [], "reviewed": sorted({k for k in counts}),
            "eyebrow": "Yönetim / Finans — Özel", "title": "Halsa Yönetim Raporu",
            "subline": " · ".join(f"{LABELS[r]}: {srcs[r]['name']}" for r in ROLES if r in srcs),
            "intro": ("Bu özel rapor yalnız yetkili yönetim içindir: Cari bakiyeler, açık evraklar, kredi, gider/gelir ve "
                      "bütçe. Tüm bölümler ön incelemedir; sarı kutulardaki uyarıları ve ERP sorularını okuyun. Kırmızı "
                      "işaret kaynağın kendi kuralıdır, onaylı gecikme sonucu değildir. Bilinmeyen değerler — olarak "
                      "kalır; toplamlar para birimleri arasında yapılmaz.")}
    body = DB._html(modules, stats, meta, [], ai_panel=False, c360_panel=False, sq_panel=False, opp_panel=False,
                    fair_panel=False, crm_panel=False, own_panel=False, vkm_panel=False, up_panel=False)
    for s in srcs.values():
        if _sha(Path(s["path"]).read_bytes()) != s["sha"]:
            raise OwnerReportError("Kaynak okunurken değişti; rapor yazılmadı.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, staged = tempfile.mkstemp(prefix=".owner-report-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(staged, 0o600)
        os.link(staged, target)               # exclusive: never replaces an existing file
    finally:
        os.unlink(staged)
    return {"output": str(target), "sha256": _sha(target.read_bytes()), "generation": generation,
            "sections": {k: len(m["rows"]) for k, m in modules.items()}, "counts": counts,
            "sources": {r: s["sha"] for r, s in srcs.items()}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Özel çevrimdışı Halsa yönetim / finans raporu.")
    ap.add_argument("--output", required=True)
    ap.add_argument("--cari")
    ap.add_argument("--cari-date", type=dt.date.fromisoformat)
    ap.add_argument("--collections")
    ap.add_argument("--kredi")
    ap.add_argument("--kredi-date", type=dt.date.fromisoformat)
    ap.add_argument("--gider-gelir")
    ap.add_argument("--gider-gelir-year", type=int)
    ap.add_argument("--butce")
    ap.add_argument("--butce-year", type=int)
    ap.add_argument("--as-of", type=dt.date.fromisoformat, help="Kredi takvim pencerelerinin başlangıcı (varsayılan bugün)")
    a = ap.parse_args(argv)
    try:
        res = generate(a.output, cari=a.cari, cari_date=a.cari_date, collections=a.collections, kredi=a.kredi,
                       kredi_date=a.kredi_date, gider_gelir=a.gider_gelir, gider_gelir_year=a.gider_gelir_year,
                       butce=a.butce, butce_year=a.butce_year, as_of=a.as_of)
    except (OwnerReportError, OSError) as e:
        ap.exit(2, f"Yönetim raporu oluşturulamadı: {e}\n")
    print("Özel yönetim raporu oluşturuldu (yalnız yetkili yönetim; genel raporla paylaşmayın).")
    print("Bölümler: " + ", ".join(f"{k} {n}" for k, n in res["sections"].items()))
    print("SHA-256 " + res["sha256"][:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
