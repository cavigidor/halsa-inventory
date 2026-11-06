"""V1 read-only source review, independent of certification and action production.

Only the registered stock.product_counts surface is extended. No analytical API,
AI packet, recommendation or financial account record is released by this module.
"""
from __future__ import annotations

import html
import re
from collections import Counter

from services import generation as GEN
from services import source_validation as SV
from services import vkm as VKM

KEY = "stock.product_counts"
TITLE = "Stok Kaynak İncelemesi"
FIELDS = {"code": "Stok_Kodu", "name": "Stok_Adı", "quantity": "TOPLAM STOK MİKTAR ",
          "coverage": "STOK Kontrol %", "minimum": "minseviye", "maximum": "maxseviye",
          "inventory": "Envanter değeri", "status": "DURUM"}
COLS = [{"k": "excel_row", "l": "Excel satırı", "n": 1}, {"k": "code", "l": "Kod"},
        {"k": "name", "l": "Ürün"}, {"k": "quantity", "l": "Kayıtlı miktar"},
        {"k": "coverage", "l": "STOK Kontrol % (eski)"}, {"k": "minimum", "l": "Min (eski)"},
        {"k": "maximum", "l": "Maks (eski)"}, {"k": "inventory", "l": "Kayıtlı envanter değeri"},
        {"k": "status", "l": "Kayıtlı DURUM"}, {"k": "issues", "l": "Kontrol / kanıt"}]


def available(manifest):
    """A readable, inspected, unambiguous stock layout is required even for raw review.

    Known bad values / duplicates remain visible as evidence. Unknown column identity,
    a failed inspection or absent source never becomes an empty/zero result.
    """
    rec = manifest.record("stock") if manifest is not None else None
    ev = rec.evidence if rec else None
    rows = (rec.validation or {}).get("rows", {}) if rec else {}
    return bool(rec and ev and ev.available is True and ev.layout_supported is True
                and not ev.inspection_failures and rows.get("ok") is True
                and rows.get("template", {}).get("status") in ("approved_template", "single_identity_header"))


def markers(body):
    match = re.search(rb'<meta name="stockagent-review" content="([^"]*)">', body[:4096])
    return None if match is None else tuple(filter(None, match[1].decode("ascii").split(",")))


def authorize(body, manifest, data_folder):
    """Recheck the exact selected source bytes on every served review page (including cached HTML)."""
    keys = markers(body)
    if keys is None or any(k != KEY for k in keys):
        return False
    if not keys:
        return True
    if not available(manifest):
        return False
    try:
        paths, _, _ = GEN.select_core(data_folder)
        rec = manifest.record("stock")
        return GEN.sha256_file(paths["stock"]) == rec.sha256
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _value(cell, cached):
    if cell is None:
        return cached
    if cell.formula is not None and cell.kind == "blank":
        return "Hesaplanmamış formül (önbellek yok)"
    return cached


def module(manifest, source_path, gate):
    """Read original bytes, verify fingerprint, then reuse the formula-aware stock reader.

    No dataframe substitution (errors→NaN→0), deduplication, rounding or unit inference.
    Recognized footer rows stay out of the product table; unknown identities remain.
    """
    unavailable = {"title": TITLE, "rows": [], "cols": [], "withheld": {
        "metric": KEY, "verification": "UNAVAILABLE", "message": "Stok kaynak incelemesi kullanılamıyor.",
        "reasons": ["Aynı veri kuşağına ait okunabilir Stok Excel dosyası ve tanınan sütun düzeni gerekir. "
                    "Dosya Yükleme bölümünde Stok raporunu seçip doğrulayın ve yayımlayın."]}}
    if not available(manifest) or not source_path:
        return unavailable
    try:
        frozen = GEN.FrozenSource.read("stock", source_path)
        rec = manifest.record("stock")
        if frozen.sha256 != rec.sha256:
            return unavailable
        cells, _ = SV._read_cells(frozen.data)  # same formula/cache reader as the accuracy diagnostics
        analysis = SV.analyze_stock_rows(frozen.data)
        if not analysis.ok:
            return unavailable
        headers = {c: str(x.value) for c, x in cells.get(4, {}).items()}
        from services.importers.excel import column_index
        columns = {}
        for k, label in FIELDS.items():
            matches = [c for c, value in headers.items() if value == label]
            if k == "code":
                columns[k] = column_index(analysis.template["identity_column"])
            elif len(matches) == 1:
                columns[k] = matches[0]
            else:
                return unavailable
        # Never select a duplicate required header by guessing.
        assert all(headers[c] == FIELDS[k] for k, c in columns.items())
        # The diagnostic reader intentionally ignores whitespace-only text. Keep cached source
        # values from openpyxl too, so even a literal blank-looking product name is not changed.
        import openpyxl
        workbook = openpyxl.load_workbook(frozen.stream(), data_only=True, read_only=True)
        try:
            source_rows = list(workbook.worksheets[0].iter_rows(min_row=5, values_only=True))
        finally:
            workbook.close()
        rows = []
        for number, values in enumerate(source_rows, start=5):
            if not any(v is not None for v in values) or number in analysis.footer_rows:
                continue
            raw = cells.get(number, {})
            row = {k: _value(raw.get(c), values[c-1] if c <= len(values) else None)
                   for k, c in columns.items()}
            row["excel_row"] = number
            problems = []
            for k, c in columns.items():
                cell = raw.get(c)
                if cell and (cell.kind == "error" or str(cell.value) in SV.EXCEL_ERRORS):
                    problems.append(f"{FIELDS[k]}: {cell.value}")
                elif cell and cell.formula is not None and cell.kind == "blank":
                    problems.append(f"{FIELDS[k]}: formül önbelleği yok")
                elif k != "status" and (cell is None or cell.kind == "blank"):
                    problems.append(f"{FIELDS[k]}: boş (sıfır değil)")
            row["_problems"] = problems
            rows.append(row)
        codes = Counter(str(r["code"]).strip() for r in rows if r["code"] is not None)
        for row in rows:
            if row["code"] is not None and codes[str(row["code"]).strip()] > 1:
                row["_problems"].append("Yinelenen kod; satırlar birleştirilmedi")
            if isinstance(row["quantity"], (int, float)) and row["quantity"] < 0:
                row["_problems"].append("Negatif kaynak miktarı; işareti korundu")
            row["issues"] = "; ".join(row.pop("_problems"))
        decision = gate.decide(KEY)
        review = {"metric": KEY, "verification": decision.state.value, "status": "PRELIMINARY",
                  "source": rec.file, "sha256": rec.sha256, "generation": manifest.core_id,
                  "report_date": rec.evidence.report_date.isoformat() if rec.evidence.report_date else None,
                  "date_origin": rec.evidence.report_date_origin,
                  "counts": analysis.counts(), "warnings": []}
        for issue in VKM.detect_core(rec, manifest.joins):
            texts = VKM.texts(issue.check, "stock", issue.field_name, issue.column)
            counts = ", ".join(f"{VKM.COUNT_TR.get(k, k)}: {v}" for k, v in issue.counts.items())
            review["warnings"].append({"title": texts["title"], "issue": texts["explain"],
                "evidence": counts or "Denetim sonucu; etkilenen kayıt sayısı bilinmiyor.",
                "question": "Excel'de ilgili sütun ve satırları karşılaştırınca bu bulgu aynı mı?",
                "resolution": texts["action"], "code": issue.check, "severity": issue.severity})
        # Field errors may also include footer cells in diagnostics. Count actual displayed records separately.
        affected = [r for r in rows if any(str(r[k]) in SV.EXCEL_ERRORS for k in ("coverage",))]
        review["warnings"].insert(0, {
            "title": "Eski stok eşikleri ve STOK Kontrol % satın alma kararı vermez (D-070)",
            "issue": "Min / maks seviyeleri yıllar önce belirlenmiş; güncel hedef değildir. "
                     "STOK Kontrol % hatalıysa eski sınıflandırma da güvenilir olmaz. Yeniden sipariş, fazla stok "
                     "ve stok eritme sıralamaları emekliye ayrıldı.",
            "evidence": f"STOK Kontrol % hata kodu taşıyan kaynak kayıt: {len(affected)}. "
                        "Etkilenen satırlar tabloda hata koduyla gösterilir; aramada #DIV/0! gibi kodları kullanın. "
                        "Sayı yalnızca kayıt satırlarıdır; Veri Kontrol hücre sayımı toplam satırlarını da içerebilir.",
            "question": "Bu kayıtların ERP miktarları doğru mu? Güncel sipariş kararı için hangi birim, "
                        "talep dönemi ve güncel alt / üst seviye kullanılmalı?",
            "resolution": "Kaynak miktarlarını kontrol edin. Yeni bir iş kuralı onaylanıp ayrıca uygulanmadan "
                          "bu alanlardan satın alma veya stok eritme önerisi üretilmez.",
            "code": "D-070", "severity": "yuksek"})
        return {"title": TITLE, "rows": rows, "cols": COLS, "review": review,
                "note": "Kaynak kayıtlarıdır; satın alma önerisi değildir. Satırlar birleştirilmedi; negatifler "
                        "ve hata kodları korundu. — = boş / bilinmiyor, sıfır değildir. Birimler kaynakta nasılsa öyledir."}
    except Exception:
        return unavailable  # no source path, exception text, or partial data on a failed inspection


def warning_html(review):
    """Compact expandable Turkish evidence, escaped at the Python boundary."""
    e = lambda x: html.escape(str(x if x is not None else "Bilinmiyor"), quote=True)
    counts = review["counts"]
    out = [f'<section class="aw-box review-box" role="note" data-metric="{KEY}" data-verification="{e(review["verification"])}">',
           '<p class="aw-msg">İnceleme için / doğrulanmadı — kaynak kayıtları</p>',
           f'<p>Ürün kodlu satır: {counts["code_rows"]} · Farklı kod: {counts["distinct_codes"]} · '
           f'Yinelenen tekrar: {counts["duplicate_code_occurrences"]} · Kimliği eksik satır: {counts["missing_identity_rows"]}. '
           'Ürün kodlu satır sayısı, benzersiz ürün veya fiziksel stok sayısı değildir.</p>',
           '<details><summary>Kaynak ve doğrulama kaydı</summary>',
           f'<p>Dosya: {e(review["source"])} · Rapor tarihi: {e(review["report_date"])} '
           f'({e(review["date_origin"])})</p>',
           f'<p>Kuşak: <code>{e(review["generation"])}</code><br>SHA-256: <code>{e(review["sha256"])}</code></p>',
           f'<p>Doğrulama kaydı: {e(review["verification"])}. İnceleme görünürlüğü bu kaydı değiştirmez. '
           f'Tanınan toplam satırı (tablo dışında): {counts["footer_rows"]}. Sayım: kod taşıyan satırlar; '
           'aynı kodun tekrarları ayrı kalır.</p></details>']
    for w in review["warnings"]:
        out += [f'<details data-check="{e(w["code"])}"><summary>{e(w["title"])} · {e(w["severity"])}</summary>',
                f'<p><b>Sorun / etki:</b> {e(w["issue"])}</p><p><b>Kanıt:</b> {e(w["evidence"])}</p>',
                f'<p><b>ERP kontrol sorusu:</b> {e(w["question"])}</p>',
                f'<p><b>Çözüm için:</b> {e(w["resolution"])}</p></details>']
    out.append('<p>Diğer tanılar: Veri Kontrol sekmesi (ayrıntılar yönetici erişiminde).</p></section>')
    return "".join(out)
