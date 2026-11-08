"""General V1 report: Hedef and Trodat sections (D-081), offline only, opt-in (`father_report --hedef / --trodat`).

Shared-audience sources only. Nothing from Cari, collections, Kredi, Gider/Gelir or Bütçe is read here. From Hedef the
stale credit fields (`Risk`, `Kredi`) and the discount tier are NOT exported to the general file; capacity and visit
counts are labelled stale / incomplete. Trodat stays at its native model level: no Halsa SKU mapping, reported
quantities with unverified meaning, blanks unknown (never zero). Both sections are preliminary review surfaces.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _w(code, title, issue, evidence, question, resolution, severity="orta"):
    return dict(code=code, title=title, issue=issue, evidence=evidence, question=question, resolution=resolution,
                severity=severity)


def _review(metric, source, sha, formula, warnings, generation, origin="beyan edilmedi"):
    from services import sales_review as SALES
    return SALES.warning_html(dict(metric=metric, status="PRELIMINARY", verification="Kayıtlı ölçüt değil — ön inceleme",
                                   source=source, sha256=sha, generation=generation, report_date=None,
                                   date_origin=origin, observed_start=None, observed_end=None, formula=formula,
                                   warnings=warnings))


def _num(nv):
    v = getattr(nv, "value", nv)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def hedef_section(path, base, generation):
    """`base`: the sales frame prepared by the dashboard's own load_sales (D-003 `_cust`, `_yr`, `Nettutar`)."""
    from services.importers import hedef as HD
    from services.importers.base import explicit_source_type
    with explicit_source_type():
        res = HD.import_hedef(path)
    if not res.ok:
        raise ValueError("Hedef dosyası doğrulanamadı.")
    years = sorted({y for a in res.accounts for y, nv in a.yearly.get(HD.REPORTED_NET_REVENUE, {}).items()
                    if nv.value is not None})
    show = years[-3:]
    stamp_year = max((y for a in res.accounts for y, nv in a.yearly.get(HD.STAMP_QTY, {}).items()
                      if nv.value is not None), default=None)
    sales_by = defaultdict(float)
    sales_custs = set(base["_cust"].astype(str))
    for (c, y), v in base.groupby(["_cust", "_yr"])["Nettutar"].sum().items():
        sales_by[(str(c), int(y))] = float(v)
    rec_year = show[-1] if show else None
    comparable = equal = 0
    rows = []
    for a in res.accounts:
        row = {"code": a.customer_code, "city": a.city.value, "district": a.district.value, "sector": a.sector_code.value,
               "rep": a.hedef_rep.value, "customer_rep": a.customer_rep.value, "visits": _num(a.visit_count),
               "weekly_capacity": _num(a.historical_weekly_stamp_capacity),
               "in_sales": "Evet" if a.customer_code in sales_custs else "Hayır"}
        for y in show:
            row[f"rev_{y}"] = _num(a.yearly.get(HD.REPORTED_NET_REVENUE, {}).get(y))
        if stamp_year:
            row["stamp_qty"] = _num(a.yearly.get(HD.STAMP_QTY, {}).get(stamp_year))
        if rec_year and row.get(f"rev_{rec_year}") is not None and a.customer_code in sales_custs:
            comparable += 1
            equal += abs(row[f"rev_{rec_year}"] - sales_by.get((a.customer_code, rec_year), 0.0)) <= 1.0
        rows.append(row)
    c = res.counts
    warnings = [
        _w("HEDEF-YETKI", "Hedef yetkili kaynak değildir", "Hedef kayıt dosyası kararı açık; hesaplar olduğu gibi gösterilir.",
           f"Hesap: {c.accounts}; satış dosyasında müşteri olarak geçen: {sum(r['in_sales'] == 'Evet' for r in rows)}; "
           f"başında/sonunda boşluk olan kod: {c.codes_with_surrounding_whitespace}.",
           "Hedef listesi hangi tarihte ve kim tarafından güncellendi?", "Anlık tarihi beyan edin.", "orta"),
        _w("HEDEF-ESKI", "Haftalık kaşe adedi eski, ziyaret sayısı eksik",
           "Haftalık kaşe adedi geçmiş bir tahmindir; potansiyel veya pazar payı hesaplanmaz. Ziyaret sayısında 0 'kayıt "
           "yok' anlamına gelebilir.", f"Haftalık kaşe dolu: {c.weekly_capacity_filled}; ziyaret > 0: {c.visit_count_nonzero}.",
           "Haftalık kaşe adedi en son ne zaman güncellendi?", "Bilgi amaçlıdır.", "dusuk"),
        _w("HEDEF-FINANS", "Risk / Kredi limitleri bu genel rapora alınmadı",
           "Eski kredi alanları ve iskonto oranı genel rapora konmaz; güncel kredi kararı değildir.",
           f"Risk dolu: {c.risk_filled}; Kredi dolu: {c.kredi_filled} (rapora alınmadı).", "—", "Bilgi amaçlıdır.", "dusuk"),
    ]
    if rec_year:
        warnings.append(_w("HEDEF-CIRO", f"{rec_year} Net Ciro satış dosyasıyla karşılaştırıldı (teknik)",
                           "Aynı müşteri kodunda Hedef 'Net Ciro' ile satış dosyası Nettutar toplamı (±1 TL). Gelir "
                           "tanımı değildir; farklar hangi raporun neyi saydığını gösterir.",
                           f"Karşılaştırılabilir hesap: {comparable}; aynı: {equal}; farklı: {comparable - equal}.",
                           "Hedef 'Net Ciro' hangi rapordan, hangi filtreyle alınıyor?", "Farkları ERP'de kontrol edin."))
    formula = (f"Hedef dosyasındaki değerler aynen; son {len(show)} yılın raporlanan Net Ciro'su"
               + (f" ve {stamp_year} kaşe adedi" if stamp_year else "") + ". 'Satışta' = kod satış dosyasında siparişi veren "
               "müşteri olarak birebir geçiyor.")
    cols = [{"k": "code", "l": "Kod"}, {"k": "city", "l": "İl"}, {"k": "district", "l": "İlçe"},
            {"k": "sector", "l": "Sektör"}, {"k": "rep", "l": "Hedef temsilcisi"},
            {"k": "customer_rep", "l": "Müşteri temsilcisi"}]
    cols += [{"k": f"rev_{y}", "l": f"{y} Net Ciro (Hedef)", "money": 1, "exact": 1} for y in show]
    if stamp_year:
        cols.append({"k": "stamp_qty", "l": f"{stamp_year} Kaşe Adedi", "n": 1})
    cols += [{"k": "weekly_capacity", "l": "Haftalık kaşe (eski)", "n": 1}, {"k": "visits", "l": "Ziyaret (eksik olabilir)", "n": 1},
             {"k": "in_sales", "l": "Satışta"}]
    sha = _sha(path)
    return {"title": "Hedef / Bölge", "rows": rows, "cols": cols, "filter": "rep", "page_size": 100, "note": formula,
            "review_html": _review("hedef.accounts", Path(path).name, sha, formula, warnings, generation)}, {
        "accounts": len(rows), "years": show, "comparable": comparable, "equal": equal, "sha": sha}


def trodat_section(path, generation, report_year=None):
    from services.importers import trodat as TR
    from services.importers.base import explicit_source_type
    year = report_year or (TR.parse_report_filename(Path(path).name) or (None,))[0]
    if year is None:
        raise ValueError("Trodat rapor yılı dosya adından okunamadı; --trodat-year ile beyan edin.")
    with explicit_source_type():
        res = TR.import_trodat(path, report_year=year)
    if not res.ok:
        raise ValueError("Trodat raporu doğrulanamadı.")
    months = sorted({r.month for r in res.records if r.reported_quantity.value is not None})
    by = defaultdict(dict)
    for r in res.records:
        by[r.line_key][r.month] = _num(r.reported_quantity)
    rows = []
    for ln in res.lines:
        vals = by.get(ln.line_key, {})
        row = {"excel_row": ln.source_row, "section": ln.section, "family": ln.family.value if ln.family else None,
               "kind": "Model" if ln.line_kind == "model" else "Aile satırı", "model": ln.model_code,
               "variant": ln.variant, "label": ln.line_label,
               "controls": "Doğrulandı" if ln.control_verified else "Kaynak kontrolleri tutarsız"}
        for m in months:
            row[f"m{m}"] = vals.get(m)
        known = [vals.get(m) for m in months]
        row["total"] = sum(known) if known and all(v is not None for v in known) else None
        rows.append(row)
    c = res.counts
    warnings = [
        _w("TRODAT-BIRIM", "Raporlanan miktarın anlamı doğrulanmadı",
           "Değerler adet gibi görünüyor; teşhir paketi / set olup olmadığı teyit edilmedi. Birimler toplanmaz.",
           f"Model satırı: {c.model_lines}; aile satırı: {c.family_lines}; boş hücre: {c.blank_cells} (bilinmiyor, sıfır değil).",
           "Trodat raporundaki sayılar adet mi, paket mi?", "Teyit edin.", "orta"),
        _w("TRODAT-ESLEME", "Halsa ürün koduyla eşleme yok",
           "Trodat model kodları Halsa stok kodlarına bağlanmadı; stok veya satışla birleştirilmez.",
           f"Eşleme: {c.halsa_sku_mapping}.", "Model → Halsa stok kodu eşleme tablosunu kim tutar?", "Bilgi amaçlıdır.",
           "dusuk"),
        _w("TRODAT-DONEM", "Dönem dosya adından", f"Rapor yılı {year} ({'beyan' if report_year else 'dosya adı'}); "
           f"dosya adındaki ay etiketi: {c.filename_month_label}.", "Değer içeren aylar: "
           + ", ".join(MONTHS[m - 1] for m in months) + ".", "—", "Bilgi amaçlıdır.", "dusuk"),
    ]
    formula = "Trodat raporundaki model × ay değerleri aynen. Toplam yalnız tüm aylar doluysa gösterilir."
    cols = [{"k": "excel_row", "l": "Excel satırı", "n": 1}, {"k": "family", "l": "Aile"}, {"k": "kind", "l": "Satır"},
            {"k": "model", "l": "Model"}, {"k": "variant", "l": "Varyant"}, {"k": "label", "l": "Etiket"}]
    cols += [{"k": f"m{m}", "l": MONTHS[m - 1], "n": 1} for m in months]
    cols += [{"k": "total", "l": "Toplam (aylar)", "n": 1}, {"k": "controls", "l": "Kaynak kontrolü"}]
    sha = _sha(path)
    return {"title": "Trodat Model Satışları", "rows": rows, "cols": cols, "note": formula,
            "review_html": _review("trodat.models", Path(path).name, sha, formula, warnings, generation,
                                   origin=f"rapor yılı {year}")}, {"lines": len(rows), "months": months, "sha": sha}
