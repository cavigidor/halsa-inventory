"""M8-VKM-1 — Veri Kontrol Merkezi: deterministic data-quality issue detection (docs/m8_veri_kontrol_merkezi.md).

This module is PURE: it turns one accepted source generation's records (`services/generation.SourceRecord`: the
formula-aware validation counts, the `SourceEvidence` and the join report that the existing pipeline already produced
from the frozen workbook bytes) into a list of `Issue`s. It never opens a workbook, never reads a value, never infers a
relationship, a date or a business definition, and never touches the accuracy gate. Persistence, carry-over and review
live in `services/vkm_store.py`.

Privacy (design §1 rule 3): an issue carries a check code, a source key, a schema-level locator (a header label that is
already in the code, a physical column letter or a named check), COUNTS and schema labels. No customer / product code,
name, amount, file name, path or fingerprint is part of an issue, its ID, its title or its text.

Stable IDs (design §3.1): ``VKM-`` + the first 16 hex of SHA-256(issue_type | source_key | locator). The source bytes and
the generation are deliberately NOT part of it, so the same problem in next month's upload keeps its ID.

Review is not certification (design §1 rule 1): nothing here — or in the store — sets any evidence field, changes a gate
decision, a manifest or `GENERATION_BOUND_API_EVIDENCE`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from services import accuracy as ACC
from services import source_validation as SV

DETECTOR_VERSION = "vkm-1.2"   # 1.1: detector scopes, provenance-bound observations; 1.2: per-column footer scopes
NOT_APPROVAL_TR = "İnceleme bir onay değildir; metrikler yalnızca doğrulanmış kanıtla gösterilir."

# ---------------------------------------------------------------------------------------------
# vocabulary (design §2 / §3)
# ---------------------------------------------------------------------------------------------
SEVERITIES = ("kritik", "yuksek", "orta", "dusuk")
SEVERITY_TR = {"kritik": "Kritik", "yuksek": "Yüksek", "orta": "Orta", "dusuk": "Düşük"}

TYPES: dict[str, dict[str, str]] = {
    "confirmed_error": {"label": "Kesin hata", "severity": "kritik"},
    "suspected_duplicate": {"label": "Olası yinelenen kayıt", "severity": "yuksek"},
    "unresolved_mapping": {"label": "Çözülmemiş kaynak eşlemesi", "severity": "yuksek"},
    "unknown_report_date": {"label": "Rapor tarihi bilinmiyor / beyan edilmedi", "severity": "yuksek"},
    "invalid_formula": {"label": "Geçersiz / hesaplanmamış formül", "severity": "yuksek"},
    "undefined_stock_coverage": {"label": "Tanımsız stok kapsamı (#DIV/0!)", "severity": "orta"},
    "unmatched_account": {"label": "Eşleşmeyen müşteri / ürün hesabı", "severity": "orta"},
    "partial_reconciliation": {"label": "Kısmi mutabakat", "severity": "orta"},
    "layout_refused": {"label": "Rapor düzeni onaylı şablonla uyuşmuyor", "severity": "kritik"},
    "missing_source": {"label": "Beklenen dosya yok", "severity": "yuksek"},
    "other_deterministic": {"label": "Diğer veri kalitesi istisnası", "severity": "orta"},
}

STATUSES = ("acik", "incelendi", "kabul_edildi", "cozuldu_kaynakta", "yeniden_acildi")
STATUS_TR = {"acik": "Açık", "incelendi": "İncelendi", "kabul_edildi": "Kabul edildi (bilinen durum)",
             "cozuldu_kaynakta": "Kaynakta çözüldü", "yeniden_acildi": "Yeniden açıldı"}
OPEN_STATUSES = ("acik", "yeniden_acildi", "incelendi", "kabul_edildi")   # still present or unknown: not resolved

PRESENCE = ("present", "absent", "not_checked", "source_missing")
PRESENCE_TR = {"present": "Bu yüklemede var", "absent": "Bu yüklemede yok (kaynakta çözüldü)",
               "not_checked": "Bilinmiyor — bu yüklemede denetlenemedi",
               "source_missing": "Bilinmiyor — kaynak dosya yüklenmedi"}

EVENTS = ("detected", "still_present", "not_present", "reviewed", "acknowledged", "note_added", "reopened",
          "source_missing", "not_checked")
EVENT_TR = {"detected": "İlk kez görüldü", "still_present": "Yeni yüklemede yine görüldü",
            "not_present": "Yeni yüklemede görülmedi — kaynakta çözüldü",
            "reviewed": "İncelendi olarak işaretlendi", "acknowledged": "Kabul edildi (bilinen durum)",
            "note_added": "Not eklendi", "reopened": "Yeniden açıldı",
            "source_missing": "Kaynak dosya yüklenmedi — durum bilinmiyor, çözülmüş sayılmadı",
            "not_checked": "Dosya yüklendi ama bu denetim yapılamadı — durum bilinmiyor, çözülmüş sayılmadı"}
REASON_TR = {"returned": "Sorun yeni yüklemede yeniden görüldü", "counts_increased": "Etkilenen sayı arttı",
             "owner": "Yönetici yeniden açtı", "source_returned": "Kaynak dosya yeniden yüklendi",
             "check_restored": "Denetim yeniden yapılabildi"}

SOURCE_TR = {"stock": "Stok raporu", "sales": "Satış raporu", "aging": "Cari yaşlandırma raporu",
             "liste": "Müşteri ana listesi (satış dosyasındaki 'liste' sayfası)", "kredi": "Kredi takip dosyası",
             "gider_gelir": "Gider / Gelir dosyası", "butce": "Bütçe dosyası"}
CORE_SOURCES = ("stock", "sales", "aging", "liste")
OWNER_SOURCES = ("kredi", "gider_gelir", "butce")
# D-062: the sources whose absence is itself an issue. The three core exports feed the dashboard; Kredi is expected
# by owner requirement (M8-VKM-1: "missing ERP exports, including Kredi"). Gider/Gelir and Bütçe stay optional
# (D-047): their absence is an issue only once they are configured or have been observed before.
EXPECTED_ALWAYS = ("stock", "sales", "aging", "kredi")
OWNER_ENV_HINT = {"kredi": "KREDI_PATH", "gider_gelir": "GIDER_GELIR_PATH", "butce": "BUTCE_PATH"}

# which sources a named relationship (services/source_validation.JOINS) connects
JOIN_SOURCES = {"sales_product_in_stock": ("sales", "stock"), "order_customer_in_master": ("sales", "liste"),
                "invoice_account_in_master": ("sales", "liste"), "order_customer_invoice_account": ("sales",),
                "aging_account_in_sales_invoices": ("aging", "sales")}

COUNT_TR = {"cells": "Hücre", "rows": "Satır", "occurrences": "Yinelenen tekrar", "code_rows": "Ürün kodlu satır",
            "distinct_codes": "Farklı ürün kodu", "with_stock": "Stoğu olan ürün", "accounts": "Hesap",
            "codes": "Kod", "customers": "Müşteri", "products": "Ürün", "checks": "Denetim",
            "contradictions": "Çelişki", "missing_headers": "Eksik başlık", "links": "Bağlantı / dış başvuru",
            "mismatched": "Çelişen toplam hücresi", "error_propagated": "Hata yüzünden doğrulanamayan toplam",
            "unsupported": "Filtreli toplam (doğrulanamadı)", "missing_cache": "Hesaplanmamış toplam",
            "ambiguous_rows": "Tanınmayan alt satır", "order_customers": "Birden fazla fatura hesabı olan müşteri",
            "invoice_accounts": "Birden fazla müşteriye bağlı fatura hesabı", "installments": "Taksit",
            "problems": "Şablon uyuşmazlığı"}


# ---------------------------------------------------------------------------------------------
# check catalogue: fixed Turkish text (no values). {field} / {column} / {source} are SCHEMA labels only.
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Check:
    issue_type: str
    severity: str
    title: str
    explain: str
    action: str


_ACT_REEXPORT = "Raporu DinamikCRM'den her zamanki şablonla yeniden alın ve verileri yenileyin."
CHECKS: dict[str, Check] = {
    # --- source integrity ---
    "inspection_failed": Check(
        "other_deterministic", "kritik", "Dosya denetimi tamamlanamadı",
        "Sistem bu dosyanın içini baştan sona denetleyemedi. Denetlenemeyen bir dosyadaki sonuçlar doğrulanmış "
        "sayılmaz.",
        "Dosyanın Excel'de sorunsuz açıldığını kontrol edin. " + _ACT_REEXPORT),
    "import_failed": Check(
        "other_deterministic", "kritik", "Dosya içe aktarılamadı",
        "Dosya okunurken doğrulama adımlarından biri başarısız oldu; bu dosyadan hiçbir sonuç kullanılmıyor.",
        "Doğru dosyanın seçildiğini ve Excel'de açıldığını kontrol edin; gerekirse raporu yeniden alın."),
    "layout_unsupported": Check(
        "layout_refused", "kritik", "Rapor düzeni tanınmadı",
        "Dosyanın sütun başlıkları onaylı rapor şablonuyla uyuşmuyor (eksik, taşınmış veya yeniden adlandırılmış "
        "sütun). Sistem hangi sütunun ne olduğunu tahmin etmez; bu dosyadaki analizler geçersiz sayılır.",
        _ACT_REEXPORT + " Şablon bilerek değiştirildiyse yeni düzenin ayrıca onaylanması gerekir."),
    "duplicate_header": Check(
        "layout_refused", "kritik", "Aynı başlık birden fazla sütunda: {field}",
        "Gerekli bir sütun başlığı dosyada birden fazla kez geçiyor; hangi sütunun kullanılacağı belirsiz. Sistem "
        "ilk sütunu seçerek tahmin yapmaz.",
        "Excel'de başlık satırını kontrol edin; rapor şablonunun değişip değişmediğine bakın."),
    "selection_not_explicit": Check(
        "other_deterministic", "yuksek", "Dosya açıkça seçilmedi",
        "Bu yüklemede kaynak dosya sources.json ile açıkça seçilmedi; sistem en son değiştirilen dosyayı kullandı. "
        "Bu seçim doğrulama kanıtı sayılmaz.",
        "Kullanılacak dosyayı ve rapor tarihini data/sources.json içinde açıkça belirtin, sonra verileri yenileyin."),
    "report_date_missing": Check(
        "unknown_report_date", "yuksek", "Rapor tarihi beyan edilmedi",
        "Bu raporun hangi tarihin durumunu gösterdiği bilinmiyor. Tarih, dosyanın değiştirilme zamanından veya en "
        "son işlem tarihinden tahmin edilmez.",
        "Raporun tarihini açıkça beyan edin (ana raporlar için data/sources.json, sahip dosyaları için ilgili tarih "
        "ayarı)."),
    "period_unknown": Check(
        "unknown_report_date", "yuksek", "Satış döneminin tamamlığı beyan edilmedi",
        "Satış raporunun hangi dönemi eksiksiz kapsadığı beyan edilmedi. Dönemin tam olduğu, ilk ve son işlem "
        "tarihinden çıkarılmaz.",
        "data/sources.json içinde dönem başlangıcını, bitişini ve dönemin tamam olup olmadığını belirtin."),
    "period_incomplete": Check(
        "unknown_report_date", "orta", "Satış dönemi eksik olarak beyan edildi",
        "Satış raporunun dönemi eksik olarak beyan edildi; dönem toplamları tam kabul edilmez.",
        "Dönem tamamlandığında raporu yeniden alıp dönemi tamam olarak beyan edin."),
    "date_contradiction": Check(
        "confirmed_error", "kritik", "Beyan edilen tarih dosyayla çelişiyor",
        "Beyan edilen rapor tarihi veya dönem, dosyadaki işlem tarihleriyle ya da bugünün tarihiyle uyuşmuyor (ör. "
        "beyan edilen dönemden sonra işlem var veya tarih gelecekte).",
        "Beyan edilen tarihleri ve doğru dosyanın yüklendiğini kontrol edin."),
    "external_links": Check(
        "invalid_formula", "yuksek", "Dosya başka Excel dosyalarına bağlı",
        "Dosyada başka çalışma kitaplarına bağlantı veya dış başvurulu formül var; bu değerler o dosyalar "
        "değiştiğinde değişebilir.",
        "Excel'de Veri → Bağlantıları Düzenle ile bağlantıları kontrol edin; raporu değer olarak yeniden alın."),
    "joins_not_checked": Check(
        "other_deterministic", "kritik", "Eşleşme denetimi çalışmadı",
        "Müşteri ve ürün eşleşmeleri bu yüklemede denetlenemedi; eşleşmeye dayanan analizler doğrulanmış sayılmaz.",
        _ACT_REEXPORT),
    "mapping_not_approved": Check(
        "unresolved_mapping", "yuksek", "Kaynak eşlemesi onaylanmadı (beyan)",
        "Bu dosya revizyonu için kaynak eşlemesi açıkça onaylanmadı olarak beyan edildi.",
        "Eşleme sorusunu karara bağlayıp onayı bu dosya revizyonu için kaydedin."),
    "mapping_unknown": Check(
        "unresolved_mapping", "yuksek", "Kaynak eşlemesi onaylanmadı",
        "Bu dosya revizyonu için DinamikCRM sütunlarının hangi iş tanımına karşılık geldiğine dair kayıtlı bir onay "
        "yok. Sistem eşlemeyi tahmin etmez; onaysız eşlemeye dayanan analizler doğrulanmış sayılmaz.",
        "Sütun eşlemesini (ör. hangi sütunun net satış, hangisinin müşteri kodu olduğu) sahip olarak karara bağlayın; "
        "onay, bu dosya revizyonu için data/sources.json içinde bir karar numarasıyla kaydedilir."),
    "declared_totals_inconsistent": Check(
        "confirmed_error", "kritik", "Toplamların tutarsız olduğu beyan edildi",
        "Bu dosya revizyonu için kontrol toplamlarının tutmadığı açıkça beyan edildi.",
        "Toplam farkının nedenini DinamikCRM'de inceleyin; düzeltilmiş raporu yeniden alın."),
    # --- field level (core) ---
    "field_excel_errors": Check(
        "confirmed_error", "kritik", "Excel hata değeri: {field}",
        "Bu sütunda #REF!, #VALUE!, #N/A gibi Excel hata değerleri var. Hatalı hücreler sıfır veya boş sayılmaz.",
        "Excel'de {column} sütununu filtreleyip hata değerlerini bulun; kaynağı DinamikCRM'de düzeltip raporu "
        "yeniden alın."),
    "field_missing_cache": Check(
        "invalid_formula", "yuksek", "Hesaplanmamış formül: {field}",
        "Bu sütunda sonucu kaydedilmemiş formüller var; dosya formüller hesaplanmadan kaydedilmiş olabilir.",
        "Dosyayı Excel'de açıp tüm formülleri hesaplatın (F9), kaydedip yeniden yükleyin."),
    "field_invalid_dates": Check(
        "confirmed_error", "kritik", "Geçersiz tarih: {field}",
        "Bu tarih sütununda tarih olarak okunamayan hücreler var.",
        "Excel'de {column} sütununu tarih olarak biçimlendirip okunamayan hücreleri kontrol edin."),
    "required_blank": Check(
        "other_deterministic", "yuksek", "Zorunlu alanda boş hücre: {field}",
        "Bu sütun her satırda dolu olmalı; boş hücreler var. Boş değer sıfır veya bilinmeyen bir değerle "
        "doldurulmaz.",
        "Excel'de {column} sütununda boşları filtreleyip ilgili kayıtları DinamikCRM'de kontrol edin."),
    "amount_blank": Check(
        "other_deterministic", "orta", "Tutar sütununda boş hücre: {field}",
        "Tutar sütununda boş hücreler var. Boş hücrenin sıfır mı yoksa eksik veri mi olduğu onaylanmadığı için sıfır "
        "sayılmaz.",
        "Boş hücrenin anlamı (sıfır mı, eksik mi) için sahip kararı gerekir; veride değişiklik gerekmez."),
    # --- stock rows / footers / coverage ---
    "stock_duplicate_code": Check(
        "suspected_duplicate", "yuksek", "Aynı ürün kodu birden fazla satırda",
        "Stok raporunda aynı ürün kodu birden fazla satırda geçiyor. Bu gerçek bir tekrar olabilir ya da raporun "
        "kapsamıyla ilgili olabilir; hiçbir satır otomatik olarak silinmez veya birleştirilmez.",
        "Excel'de {column} sütununu seçip Koşullu Biçimlendirme → Yinelenen Değerler ile tekrar eden kodları "
        "işaretleyin; 'Yinelenenleri Kaldır' kullanmayın. Ürün kartlarını DinamikCRM'de kontrol edin."),
    "stock_missing_identity": Check(
        "other_deterministic", "yuksek", "Ürün kodu olmayan satır",
        "Stok raporunda ürün kodu boş olan ve rapor toplam satırı olarak tanınmayan satırlar var.",
        "Excel'de ürün kodu sütununda boşları filtreleyin; bu satırların ne olduğunu kontrol edin."),
    "footer_mismatch": Check(
        "confirmed_error", "kritik", "Rapor toplamı satırlarla çelişiyor (sütun {column})",
        "Raporun altındaki toplam hücresi, kapsadığı satırların toplamıyla uyuşmuyor.",
        "Excel'de {column} sütununun alt toplam formülünü ve kapsadığı satırları kontrol edin; raporu yeniden alın."),
    "footer_partial": Check(
        "partial_reconciliation", "orta", "Rapor toplamı doğrulanamadı (sütun {column})",
        "Bu sütunun alt toplamı, sütunda Excel hata değeri olduğu veya filtreli toplam (SUBTOTAL) kullanıldığı için "
        "mutabık kılınamadı. Kısmi mutabakat doğrulama sayılmaz.",
        "Excel'de {column} sütunundaki hata değerlerini ve gizli satırları kontrol edin."),
    "footer_missing_cache": Check(
        "invalid_formula", "yuksek", "Rapor toplamında hesaplanmamış formül (sütun {column})",
        "Alt toplam formülünün kaydedilmiş bir sonucu yok; toplam doğrulanamıyor.",
        "Dosyayı Excel'de açıp formülleri hesaplatın (F9), kaydedip yeniden yükleyin."),
    "footer_ambiguous": Check(
        "partial_reconciliation", "orta", "Tanınmayan rapor alt satırı",
        "Ürün satırlarından sonra, rapor toplam satırı olarak tanınamayan satırlar var; bunlar ürün kodu eksik "
        "satır sayılır ve toplam mutabakatı tamamlanmış kabul edilmez.",
        "Excel'de raporun son satırlarını kontrol edin; şablon dışı bir satır eklenmiş olabilir."),
    "stock_coverage_undefined": Check(
        "undefined_stock_coverage", "orta", "Stok kapsamı hesaplanamıyor (#DIV/0!)",
        "Bu ürünlerde stok kontrol yüzdesi #DIV/0! (sıfıra bölme) gösteriyor. Kapsam sıfır veya sonsuz sayılmaz; "
        "bu ürünler için kural sahip kararı bekliyor (DQ-4).",
        "Kural (ör. ayrı bir 'kapsam yok' listesi) için sahip kararı gerekir. DinamikCRM'de bu ürünlerin kontrol "
        "yüzdesi formülünün girdilerini inceleyebilirsiniz."),
    "stock_coverage_error": Check(
        "confirmed_error", "kritik", "Stok kapsamında Excel hatası (#DIV/0! dışı)",
        "Stok kontrol yüzdesi sütununda sıfıra bölme dışında Excel hata değerleri var.",
        "Excel'de {column} sütununu filtreleyip hata değerlerini kontrol edin; raporu yeniden alın."),
    # --- duplicates (other sources) ---
    "sales_duplicate_rows": Check(
        "suspected_duplicate", "yuksek", "Birebir aynı satış satırı",
        "Satış raporunda bütün sütunları birebir aynı olan satırlar var. Gerçek bir tekrar mı (ör. aynı satırın iki "
        "kez aktarılması) yoksa geçerli ayrı satırlar mı olduğu bilinmiyor; hiçbir satır otomatik olarak silinmez.",
        "Excel'de 'Yinelenenleri Kaldır' KULLANMAYIN. Satırları Koşullu Biçimlendirme ile işaretleyip sipariş "
        "belgelerini DinamikCRM'de kontrol edin."),
    "aging_duplicate_accounts": Check(
        "suspected_duplicate", "yuksek", "Aynı cari hesap kodu birden fazla satırda",
        "Cari yaşlandırma raporunda aynı hesap kodu birden fazla satırda geçiyor; hiçbir satır birleştirilmez.",
        "Excel'de hesap kodu sütununda Koşullu Biçimlendirme → Yinelenen Değerler ile tekrarları işaretleyin."),
    "master_duplicate_codes": Check(
        "suspected_duplicate", "yuksek", "Müşteri ana listesinde yinelenen kod",
        "Müşteri ana listesinde ('liste' sayfası) aynı müşteri kodu birden fazla satırda geçiyor; hangi satırın esas "
        "olduğu bilinmiyor.",
        "Satış dosyasındaki 'liste' sayfasında KODU sütununda tekrarları işaretleyip DinamikCRM'de kontrol edin."),
    # --- relationships (joins; counts only, nothing matched by a new rule) ---
    "join_sales_product_not_in_stock": Check(
        "unmatched_account", "orta", "Satılan ürün stok raporunda yok",
        "Satış raporundaki bazı ürün kodları stok raporunda bulunmuyor. Kodlar tam eşleşmeyle aranır. Kaynak kanıtı "
        "olmadan hiçbir ürün 'üretimden kalktı' sayılmaz.",
        "Stok raporunun tüm ürünleri kapsayıp kapsamadığını (rapor filtresi) DinamikCRM'de kontrol edin."),
    "join_order_customer_not_in_master": Check(
        "unmatched_account", "orta", "Sipariş müşterisi ana listede yok",
        "Satışlardaki bazı sipariş müşterisi kodları (Pro_kodu) müşteri ana listesinde bulunamadı. Kodlar tam "
        "eşleşmeyle aranır; benzer kodlar birleştirilmez.",
        "Müşteri ana listesinin ('liste' sayfası) tüm müşterileri içerdiğini kontrol edin."),
    "join_invoice_account_not_in_master": Check(
        "unmatched_account", "orta", "Fatura hesabı ana listede yok",
        "Satışlardaki bazı fatura hesabı kodları (Musterikod) müşteri ana listesinde bulunamadı.",
        "Müşteri ana listesinin ('liste' sayfası) tüm fatura hesaplarını içerdiğini kontrol edin."),
    "join_aging_account_not_in_sales": Check(
        "unmatched_account", "orta", "Cari hesap satışlarda fatura hesabı olarak geçmiyor",
        "Cari yaşlandırmadaki bazı hesap kodları satış raporunda fatura hesabı (Musterikod) olarak hiç geçmiyor. "
        "Bunlar satış dışı hesaplar olabilir; hangi hesapların tahsilata dahil olacağı sahip kararı bekliyor.",
        "Bu hesapların türünü muhasebe kayıtlarından kontrol edin; kural için sahip kararı gerekir."),
    "dq5_multiple_invoice_accounts": Check(
        "unresolved_mapping", "yuksek", "Bir müşterinin birden fazla fatura hesabı var (DQ-5)",
        "Bazı sipariş müşterileri (Pro_kodu) birden fazla fatura hesabıyla (Musterikod) faturalanmış veya bir fatura "
        "hesabı birden fazla müşteriye bağlı. Temsilci, KAPALI ve sektör bilgisinin hangi hesaptan alınacağı karara "
        "bağlanmadı.",
        "Kural için sahip kararı gerekir (DQ-5); veride değişiklik gerekmez."),
    "dq6_aging_order_code_only": Check(
        "unresolved_mapping", "yuksek", "Cari hesap yalnızca sipariş müşterisi koduyla eşleşiyor (DQ-6)",
        "Bazı cari hesap kodları satışlarda fatura hesabı olarak değil, yalnızca sipariş müşterisi kodu olarak "
        "geçiyor. Borcun hangi müşteriye ait sayılacağı karara bağlanmadı.",
        "Kural için sahip kararı gerekir (DQ-6); veride değişiklik gerekmez."),
    "code_whitespace": Check(
        "unresolved_mapping", "yuksek", "Kodlarda baş / son boşluk",
        "Bazı müşteri veya ürün kodlarının başında ya da sonunda boşluk var; bazıları ancak boşluk kırpılınca "
        "eşleşiyor. Kodlar kırpılmadan, tam haliyle karşılaştırılır.",
        "Boşluk politikası için sahip kararı gerekir; kaynak kartlarda boşlukları DinamikCRM'de düzeltebilirsiniz."),
    "products_multiple_names": Check(
        "unresolved_mapping", "yuksek", "Aynı ürün kodu satışta farklı adlarla",
        "Satış raporunda aynı ürün kodu farklı ürün adlarıyla geçiyor; ürün kartı zamanla değişmiş olabilir.",
        "DinamikCRM'de ürün kartlarının ad geçmişini kontrol edin."),
    # --- owner sources (Kredi / Gider-Gelir / Bütçe) ---
    "excel_errors": Check(
        "confirmed_error", "kritik", "Dosyada Excel hata değerleri",
        "Dosyada #REF!, #DIV/0! gibi Excel hata değerleri var. Hatalı hücreler onarılmaz ve sıfır sayılmaz.",
        "Excel'de Giriş → Bul ve Seç → Özel Git → Formüller → Hatalar ile hatalı hücreleri bulun ve dosyada düzeltin."),
    "missing_cache": Check(
        "invalid_formula", "yuksek", "Hesaplanmamış formüller",
        "Dosyada sonucu kaydedilmemiş formüller var.",
        "Dosyayı Excel'de açıp formülleri hesaplatın (F9), kaydedip yeniden okuyun."),
    "owner_required_blank": Check(
        "other_deterministic", "yuksek", "Zorunlu alanlarda eksik değer",
        "Dosyada dolu olması gereken alanlarda boş hücreler var. Boş değer tahmin edilmez.",
        "Boş hücreleri dosyada kontrol edin."),
    "owner_duplicates": Check(
        "suspected_duplicate", "yuksek", "Yinelenen kayıt",
        "Dosyada birebir yinelenen kayıtlar var (ör. aynı taksit iki kez). Hiçbir kayıt otomatik silinmez.",
        "Kayıtları dosyada Koşullu Biçimlendirme ile işaretleyip kontrol edin."),
    "totals_contradiction": Check(
        "confirmed_error", "kritik", "Dosyadaki toplamlar birbiriyle çelişiyor",
        "Dosyanın kendi kontrol / özet toplamları satırların toplamıyla uyuşmuyor.",
        "Dosyadaki toplam satırlarını ve özet tablolarını kontrol edin."),
    "kredi_negative_remaining": Check(
        "other_deterministic", "orta", "Negatif kalan taksit tutarı",
        "Bazı taksit satırlarında kalan tutar eksi. Ne anlama geldiği netleşmedi; değer düzeltilmez.",
        "İlgili taksitleri banka ödeme kayıtlarıyla karşılaştırın."),
    "kredi_closed_with_remaining": Check(
        "unresolved_mapping", "yuksek", "DURUM 'Kapalı' ama kalan tutar var",
        "DURUM sütunu 'Kapalı' gösteren ama kalan tutarı sıfır olmayan taksitler var. 'Kapalı'nın ödendi anlamına "
        "gelip gelmediği netleşmedi; sistem DURUM'u ödeme bilgisi olarak yorumlamaz.",
        "DURUM'un anlamını netleştirin (sahip kararı); taksitleri banka ödeme kayıtlarıyla karşılaştırın."),
    "kredi_hidden_rows": Check(
        "other_deterministic", "dusuk", "Excel filtresiyle gizlenmiş taksit satırları",
        "Dosyada kayıtlı bir filtre bazı taksit satırlarını gizliyor. Gizli satırlar da okunur ve sayılır; filtre bir "
        "ödeme bilgisi değildir.",
        "Bilgi amaçlıdır; gizli satırların neden filtrelendiğini kontrol edebilirsiniz."),
    # --- missing source ---
    "missing_source": Check(
        "missing_source", "yuksek", "Beklenen dosya yok: {source}",
        "Bu kaynağın dosyası bu yüklemede yok veya okunamadı. Dosyanın olmaması, önceki sorunlarının çözüldüğü "
        "anlamına gelmez; o sorunlar 'kaynak dosya yüklenmedi' olarak işaretlenir ve açık kalır.",
        "{hint}"),
}
MISSING_HINT_CORE = "Dosyayı veri klasörüne koyup data/sources.json ile seçin ve verileri yenileyin."
MISSING_HINT_OWNER = ("Dosya yolunu sunucu ayarında ({env}) açıkça belirtin; ardından Yönetim sekmesinden sahip "
                      "verilerini yeniden okuyun.")
MISSING_HINT_LISTE = "Satış dosyasında 'liste' sayfasının (KODU sütunuyla) bulunduğunu kontrol edin."


# ---------------------------------------------------------------------------------------------
# issues
# ---------------------------------------------------------------------------------------------
def issue_id(issue_type: str, source_key: str, locator: str) -> str:
    """Stable, privacy-safe ID (design §3.1): no source bytes, no generation, no value."""
    return "VKM-" + hashlib.sha256(f"{issue_type}|{source_key}|{locator}".encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Issue:
    check: str
    source_key: str
    locator: str                                   # schema-level only (header label, column letter, check name)
    counts: Mapping[str, int] = field(default_factory=dict)
    field_name: str | None = None                  # header label as the pipeline reads it (schema, not data)
    column: str | None = None                      # physical column letter when the layout fixes it
    join: str | None = None                        # services/source_validation.JOINS id
    labels: tuple[str, ...] = ()                   # schema labels / problem codes only (owner detail)

    @property
    def spec(self) -> Check:
        return CHECKS[self.check]

    @property
    def issue_type(self) -> str:
        return self.spec.issue_type

    @property
    def severity(self) -> str:
        return self.spec.severity

    @property
    def issue_id(self) -> str:
        return issue_id(self.issue_type, self.source_key, self.locator)

    @property
    def scope(self) -> str:
        return scope_of(self.check, self.field_name, self.column)

    def affected_metrics(self) -> tuple[str, ...]:
        return affected_metrics(self.source_key, field_name=self.field_name, join=self.join,
                                column_only=self.column is not None and self.field_name is None)


def _texts(check: str, *, source_key: str, field_name: str | None, column: str | None) -> dict[str, str]:
    c = CHECKS.get(check)
    if c is None:
        return {"title": "Bilinmeyen denetim", "explain": "", "action": ""}
    fld = (field_name or "").strip() or "—"
    col = column or (f"'{fld}'" if field_name else "ilgili")
    if source_key == "liste":
        hint = MISSING_HINT_LISTE
    elif source_key in OWNER_ENV_HINT:
        hint = MISSING_HINT_OWNER.format(env=OWNER_ENV_HINT[source_key])
    else:
        hint = MISSING_HINT_CORE
    fmt = {"field": fld, "column": col, "source": SOURCE_TR.get(source_key, source_key), "hint": hint}
    return {k: getattr(c, k).format(**fmt) for k in ("title", "explain", "action")}


def texts(check: str, source_key: str, field_name: str | None = None, column: str | None = None) -> dict[str, str]:
    """Turkish title / explanation / action for a stored issue (fixed catalogue text, schema labels only)."""
    return _texts(check, source_key=source_key, field_name=field_name, column=column)


# ---------------------------------------------------------------------------------------------
# affected metrics (from the in-code register; read only)
# ---------------------------------------------------------------------------------------------
def affected_metrics(source_key: str, *, field_name: str | None = None, join: str | None = None,
                     column_only: bool = False) -> tuple[str, ...]:
    """Register keys whose declared dependencies include the issue's locator, plus every output that depends on them
    (the gate propagates: a combined output is never better than what it depends on). A field-level issue reaches only
    metrics that read that field (or declare no scope = strict); a column that no declared field maps to reaches only
    the strict (unscoped) metrics; a relationship issue reaches metrics that use that relationship (or no scope)."""
    reg = ACC.REGISTER
    srcs = JOIN_SOURCES.get(join, (source_key,)) if join else (source_key,)
    direct: set[str] = set()
    for key, m in reg.items():
        for s in srcs:
            if s not in m.sources:
                continue
            scope = m.scope(s)
            if scope is None:
                direct.add(key)
            elif join:
                if join in scope.joins:
                    direct.add(key)
            elif column_only:
                continue
            elif field_name is not None:
                if field_name in scope.fields:
                    direct.add(key)
            else:
                direct.add(key)
    out = set(direct)
    changed = True
    while changed:
        changed = False
        for key, m in reg.items():
            if key not in out and any(d in out for d in m.depends_on):
                out.add(key)
                changed = True
    return tuple(sorted(out))


def metric_titles(keys: Iterable[str]) -> list[dict[str, str]]:
    return [{"key": k, "title": ACC.REGISTER[k].title_tr} for k in keys if k in ACC.REGISTER]


# ---------------------------------------------------------------------------------------------
# detection (deterministic; reads only what the generation already recorded)
# ---------------------------------------------------------------------------------------------
def _n(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _stock_columns(validation: Mapping[str, Any]) -> dict[str, str]:
    """header → physical column letter, ONLY when the owner-approved template fixes the layout (D-056)."""
    t = ((validation.get("rows") or {}).get("template") or {})
    spec = SV.STOCK_TEMPLATES.get(t.get("template") or "") if t.get("status") == "approved_template" else None
    return dict((spec or {}).get("fields") or {})


def _integrity(key: str, ev: ACC.SourceEvidence, validation: Mapping[str, Any], role: str) -> list[Issue]:
    out: list[Issue] = []
    if ev.inspection_failures:
        out.append(Issue("inspection_failed", key, "integrity:inspection",
                         {"checks": len(ev.inspection_failures)}))
    if ev.layout_supported is False:
        tmpl = ((validation.get("rows") or {}).get("template") or {})
        labels = tuple(str(x) for x in (validation.get("missing_headers") or ())) + \
            tuple(str(x) for x in (tmpl.get("problems") or ()))
        if role == "owner" and validation.get("failed_step"):
            labels += (f"adım: {validation.get('failed_step')}",)
        cnt = {"missing_headers": len(validation.get("missing_headers") or ())} if role == "core" else {}
        if tmpl.get("problems"):
            cnt["problems"] = len(tmpl.get("problems") or ())
        out.append(Issue("layout_unsupported", key, "integrity:layout", cnt, labels=labels))
    if role == "core" and key != "liste" and ev.selection_explicit is not True:
        out.append(Issue("selection_not_explicit", key, "dates:selection"))
    if key != "liste" and ev.report_date is None:
        out.append(Issue("report_date_missing", key, "dates:report_date"))
    if key == "sales":
        if ev.period_complete is None:
            out.append(Issue("period_unknown", key, "dates:period"))
        elif ev.period_complete is False:
            out.append(Issue("period_incomplete", key, "dates:period_incomplete"))
    if _n(ev.date_contradictions):
        out.append(Issue("date_contradiction", key, "dates:contradiction", {"contradictions": _n(ev.date_contradictions)}))
    if _n(ev.external_references):
        out.append(Issue("external_links", key, "integrity:external_links", {"links": _n(ev.external_references)}))
    # D-062: an unresolved DinamikCRM source mapping is an issue whether it was declared NOT approved or simply has no
    # recorded approval for this exact source revision (the gate's "Kaynak eşlemesinin onayı bilinmiyor"). Both are
    # the same problem in two states, so they share one locator (one stable ID). Owner workbooks have no mapping
    # approval path (their records always carry None), so only the core exports are judged here.
    if ev.mapping_approved is False:
        out.append(Issue("mapping_not_approved", key, "mapping:approval"))
    elif ev.mapping_approved is None and role == "core" and key != "liste":
        out.append(Issue("mapping_unknown", key, "mapping:approval"))
    if role == "core" and ev.declared_totals_consistent is False:
        out.append(Issue("declared_totals_inconsistent", key, "totals:declared"))
    return out


def _core_fields(key: str, ev: ACC.SourceEvidence, validation: Mapping[str, Any]) -> list[Issue]:
    out: list[Issue] = []
    spec = SV.CORE.get(key) or {}
    nonblank = set(spec.get("nonblank") or ())
    cols = _stock_columns(validation) if key == "stock" else {}
    for fname, d in sorted((ev.fields or {}).items()):
        col = cols.get(fname)
        coverage_field = key == "stock" and fname == "STOK Kontrol %"
        if _n(d.get("error_cells")) and not coverage_field:
            out.append(Issue("field_excel_errors", key, f"field:{fname}|errors", {"cells": _n(d.get("error_cells"))},
                             field_name=fname, column=col))
        if _n(d.get("missing_cached_formulas")):
            out.append(Issue("field_missing_cache", key, f"field:{fname}|missing_cache",
                             {"cells": _n(d.get("missing_cached_formulas"))}, field_name=fname, column=col))
        if _n(d.get("invalid_dates")):
            out.append(Issue("field_invalid_dates", key, f"field:{fname}|invalid_dates",
                             {"cells": _n(d.get("invalid_dates"))}, field_name=fname, column=col))
        blanks = _n(d.get("blank_cells"))
        if blanks and fname in nonblank and not (key == "stock" and fname == "Stok_Kodu"):
            out.append(Issue("required_blank", key, f"field:{fname}|blank", {"cells": blanks}, field_name=fname,
                             column=col))
        elif blanks and d.get("amount") and fname not in nonblank:
            out.append(Issue("amount_blank", key, f"field:{fname}|blank", {"cells": blanks}, field_name=fname,
                             column=col))
        if d.get("duplicate_header"):
            out.append(Issue("duplicate_header", key, f"field:{fname}|duplicate_header", field_name=fname))
    return out


def _stock_rows(ev: ACC.SourceEvidence, validation: Mapping[str, Any]) -> list[Issue]:
    rows = validation.get("rows") or {}
    if not rows or not rows.get("ok"):
        return []
    out: list[Issue] = []
    cnt = rows.get("counts") or {}
    tmpl = rows.get("template") or {}
    idcol = tmpl.get("identity_column")
    if _n(cnt.get("duplicate_code_occurrences")):
        out.append(Issue("stock_duplicate_code", "stock", "rows:product_code",
                         {"occurrences": _n(cnt.get("duplicate_code_occurrences")), "code_rows": _n(cnt.get("code_rows")),
                          "distinct_codes": _n(cnt.get("distinct_codes"))}, field_name="Stok_Kodu", column=idcol))
    if _n(cnt.get("missing_identity_rows")):
        out.append(Issue("stock_missing_identity", "stock", "rows:missing_identity",
                         {"rows": _n(cnt.get("missing_identity_rows"))}, field_name="Stok_Kodu", column=idcol))
    letters = {v: k for k, v in _stock_columns(validation).items()}
    for letter, d in sorted((rows.get("footer_by_column") or {}).items()):
        fname = letters.get(letter)
        if _n(d.get("mismatched")):
            out.append(Issue("footer_mismatch", "stock", f"footer:{letter}|mismatch", {"mismatched": _n(d.get("mismatched"))},
                             field_name=fname, column=letter))
        partial = {k: _n(d.get(k)) for k in ("error_propagated", "unsupported") if _n(d.get(k))}
        if partial:
            out.append(Issue("footer_partial", "stock", f"footer:{letter}|partial", partial, field_name=fname,
                             column=letter))
        if _n(d.get("missing_cache")):
            out.append(Issue("footer_missing_cache", "stock", f"footer:{letter}|missing_cache",
                             {"missing_cache": _n(d.get("missing_cache"))}, field_name=fname, column=letter))
    if _n(rows.get("ambiguous_rows")):
        out.append(Issue("footer_ambiguous", "stock", "footer:rows|ambiguous", {"ambiguous_rows": _n(rows.get("ambiguous_rows"))}))
    cov = rows.get("coverage") or {}
    covcol = _stock_columns(validation).get("STOK Kontrol %")
    if _n(cov.get("undefined_div0")):
        out.append(Issue("stock_coverage_undefined", "stock", "field:STOK Kontrol %|div0",
                         {"rows": _n(cov.get("undefined_div0")), "with_stock": _n(cov.get("undefined_with_stock"))},
                         field_name="STOK Kontrol %", column=covcol))
    if _n(cov.get("other_error")):
        out.append(Issue("stock_coverage_error", "stock", "field:STOK Kontrol %|errors",
                         {"rows": _n(cov.get("other_error"))}, field_name="STOK Kontrol %", column=covcol))
    return out


def _joins(joins: Mapping[str, Any]) -> list[Issue]:
    """Relationship counts from the generation's join report (services/source_validation.validate_joins)."""
    if not joins:
        return []
    if "failure" in joins:
        return [Issue("joins_not_checked", "sales", "joins:failure")]
    j = joins
    out: list[Issue] = []

    def add(check, src, loc, counts, join):
        counts = {k: v for k, v in counts.items() if v}
        if counts:
            out.append(Issue(check, src, loc, counts, join=join))
    add("join_sales_product_not_in_stock", "sales", "join:sales_product_in_stock|unmatched",
        {"products": _n(j.get("sold_products_not_in_stock"))}, "sales_product_in_stock")
    add("products_multiple_names", "sales", "join:sales_product_in_stock|multiple_names",
        {"products": _n(j.get("products_with_multiple_names"))}, "sales_product_in_stock")
    add("join_order_customer_not_in_master", "sales", "join:order_customer_in_master|unmatched",
        {"customers": _n(j.get("order_customers_unmatched_in_master"))}, "order_customer_in_master")
    add("join_invoice_account_not_in_master", "sales", "join:invoice_account_in_master|unmatched",
        {"accounts": _n(j.get("invoice_accounts_unmatched_in_master"))}, "invoice_account_in_master")
    add("code_whitespace", "sales", "join:code_whitespace",
        {"codes": _n(j.get("order_codes_with_whitespace")) + _n(j.get("invoice_codes_with_whitespace"))
         + _n(j.get("product_codes_with_whitespace")),
         "customers": _n(j.get("matches_only_after_trim")) + _n(j.get("order_codes_colliding_after_trim"))},
        "order_customer_in_master")
    add("dq5_multiple_invoice_accounts", "sales", "join:order_customer_invoice_account|dq5",
        {"order_customers": _n(j.get("order_customers_with_multiple_invoice_accounts")),
         "invoice_accounts": _n(j.get("invoice_accounts_with_multiple_order_customers"))},
        "order_customer_invoice_account")
    add("master_duplicate_codes", "liste", "rows:customer_code", {"codes": _n(j.get("master_duplicate_codes"))},
        "order_customer_in_master")
    add("join_aging_account_not_in_sales", "aging", "join:aging_account_in_sales_invoices|unmatched",
        {"accounts": _n(j.get("aging_accounts_not_in_sales_invoice_accounts"))}, "aging_account_in_sales_invoices")
    add("dq6_aging_order_code_only", "aging", "join:aging_account_in_sales_invoices|dq6",
        {"accounts": _n(j.get("aging_accounts_matching_order_code_only"))}, "aging_account_in_sales_invoices")
    return out


def detect_core(record, joins: Mapping[str, Any] | None = None) -> list[Issue]:
    """Issues of one core source record (stock / sales / aging / liste); joins are attributed to the source where the
    unmatched record lives (sales, aging, liste)."""
    return list(inspect_core(record, joins).issues)


def inspect_core(record, joins: Mapping[str, Any] | None = None) -> "Detection":
    """The issues of one core record AND the detector scopes that could actually be evaluated on it (review §1)."""
    key, ev, v = record.key, record.evidence, dict(record.validation or {})
    return _detection(_detect_core(record, joins), _checked_core(key, ev, v, joins))


def _detect_core(record, joins: Mapping[str, Any] | None = None) -> list[Issue]:
    key, ev, v = record.key, record.evidence, dict(record.validation or {})
    if ev is None:
        return [Issue("inspection_failed", key, "integrity:inspection", {"checks": 1})]
    out = _integrity(key, ev, v, "core")
    if key in ("stock", "sales", "aging"):
        out += _core_fields(key, ev, v)
    if key == "stock":
        out += _stock_rows(ev, v)
    if key == "sales" and _n(ev.duplicate_records):
        out.append(Issue("sales_duplicate_rows", "sales", "rows:identical_lines", {"rows": _n(ev.duplicate_records)}))
    if key == "aging" and _n(ev.duplicate_records):
        out.append(Issue("aging_duplicate_accounts", "aging", "rows:account_code", {"rows": _n(ev.duplicate_records)}))
    if joins is not None:
        out += [i for i in _joins(joins) if i.source_key == key]
    return _dedupe(out)


def detect_owner(record) -> list[Issue]:
    """Issues of one owner source record (Kredi / Gider-Gelir / Bütçe) from its importer counts."""
    return list(inspect_owner(record).issues)


def inspect_owner(record) -> "Detection":
    key, ev, v = record.key, record.evidence, dict(record.validation or {})
    return _detection(_detect_owner(record), _checked_owner(key, ev, v))


def _detect_owner(record) -> list[Issue]:
    key, ev, v = record.key, record.evidence, dict(record.validation or {})
    if ev is None:
        return [Issue("inspection_failed", key, "integrity:inspection", {"checks": 1})]
    out: list[Issue] = []
    if v.get("ok") is False and v.get("failed_step") != "layout":
        step = v.get("failed_step")
        out.append(Issue("import_failed", key, "integrity:import", labels=(f"adım: {step}",) if step else ()))
    out += [i for i in _integrity(key, ev, v, "owner") if i.check != "inspection_failed" or v.get("ok") is not False]
    if _n(ev.error_cells):
        out.append(Issue("excel_errors", key, "cells:excel_errors", {"cells": _n(ev.error_cells)}))
    if _n(ev.missing_cached_formulas):
        out.append(Issue("missing_cache", key, "cells:missing_cache", {"cells": _n(ev.missing_cached_formulas)}))
    if _n(ev.missing_required_values):
        out.append(Issue("owner_required_blank", key, "cells:required_blank", {"cells": _n(ev.missing_required_values)}))
    if _n(ev.duplicate_records):
        out.append(Issue("owner_duplicates", key, "rows:duplicates", {"rows": _n(ev.duplicate_records)}))
    if ev.totals_consistent is False:
        out.append(Issue("totals_contradiction", key, "totals:contradiction"))
    if key == "kredi":
        if _n(v.get("negative_remaining_rows")):
            out.append(Issue("kredi_negative_remaining", key, "rows:negative_remaining",
                             {"installments": _n(v.get("negative_remaining_rows"))}))
        if _n(v.get("closed_with_positive_remaining")):
            out.append(Issue("kredi_closed_with_remaining", key, "rows:closed_with_remaining",
                             {"installments": _n(v.get("closed_with_positive_remaining"))}))
        if _n(v.get("hidden_installment_rows")):
            out.append(Issue("kredi_hidden_rows", key, "rows:hidden_by_filter",
                             {"installments": _n(v.get("hidden_installment_rows"))}))
    return _dedupe(out)


# ---------------------------------------------------------------------------------------------
# detector scopes (review of PR #31, §1): UNKNOWN evidence is never a resolution
# ---------------------------------------------------------------------------------------------
# Every check belongs to one scope: the piece of recorded evidence it is evaluated on. An observation lists the scopes
# whose evidence was actually present and valid for that upload. A previously seen issue that is not in a PRESENT
# upload is "resolved in the source" only if its scope was evaluated; otherwise its presence is UNKNOWN
# (`not_checked`) and its status is kept. A missing count (None / absent key) is "not checked", never zero.
FIELD_CHECKS = frozenset({"field_excel_errors", "field_missing_cache", "field_invalid_dates", "required_blank",
                          "amount_blank", "duplicate_header"})
JOIN_SCOPE_KEYS: dict[str, tuple[str, ...]] = {
    "join_sales_product_not_in_stock": ("sold_products_not_in_stock",),
    "products_multiple_names": ("products_with_multiple_names",),
    "join_order_customer_not_in_master": ("order_customers_unmatched_in_master",),
    "join_invoice_account_not_in_master": ("invoice_accounts_unmatched_in_master",),
    "code_whitespace": ("order_codes_with_whitespace", "invoice_codes_with_whitespace",
                        "product_codes_with_whitespace", "matches_only_after_trim",
                        "order_codes_colliding_after_trim"),
    "dq5_multiple_invoice_accounts": ("order_customers_with_multiple_invoice_accounts",
                                      "invoice_accounts_with_multiple_order_customers"),
    "master_duplicate_codes": ("master_duplicate_codes",),
    "join_aging_account_not_in_sales": ("aging_accounts_not_in_sales_invoice_accounts",),
    "dq6_aging_order_code_only": ("aging_accounts_matching_order_code_only",),
}
_SCOPE = {
    "inspection_failed": "evidence", "import_failed": "evidence", "layout_unsupported": "layout",
    "selection_not_explicit": "selection", "report_date_missing": "report_date", "period_unknown": "period",
    "period_incomplete": "period", "date_contradiction": "dates", "external_links": "external",
    "mapping_not_approved": "mapping", "mapping_unknown": "mapping", "declared_totals_inconsistent": "declared_totals",
    "joins_not_checked": "joins",
    "stock_duplicate_code": "rows:counts", "stock_missing_identity": "rows:counts",
    "footer_ambiguous": "footer:ambiguous_rows", "stock_coverage_undefined": "rows:coverage",
    "stock_coverage_error": "rows:coverage",
    "sales_duplicate_rows": "duplicates", "aging_duplicate_accounts": "duplicates", "owner_duplicates": "duplicates",
    "excel_errors": "cells:errors", "missing_cache": "cells:missing_cache", "owner_required_blank": "cells:required",
    "totals_contradiction": "totals", "kredi_negative_remaining": "kredi:negative_remaining_rows",
    "kredi_closed_with_remaining": "kredi:closed_with_positive_remaining",
    "kredi_hidden_rows": "kredi:hidden_installment_rows", "missing_source": "source",
}


# Footer reconciliation is judged PER PHYSICAL COLUMN and per check (review of PR #31 at 847c055, finding A): a
# footer problem in column H can only be cleared by an upload in which column H's footer cells were reconciled again,
# and only when EVERY one of them matched. A footer that checks only column D, an empty `footer_by_column`, or an
# H footer that is now non-comparable (error-propagated / SUBTOTAL / uncached) leaves the H issue UNKNOWN. Each
# footer check has its own scope, so observing one footer problem in H never "checks" another one in H.
FOOTER_COLUMN_CHECKS = frozenset({"footer_mismatch", "footer_partial", "footer_missing_cache"})


def scope_of(check: str, field_name: str | None = None, column: str | None = None) -> str:
    """The detector scope of a check (stored issues are judged by this, so it must stay stable)."""
    if check in FOOTER_COLUMN_CHECKS:
        return f"footer:{column}:{check}"
    if check in FIELD_CHECKS:
        return f"field:{field_name}"
    if check in JOIN_SCOPE_KEYS:
        return f"join:{check}"
    return _SCOPE.get(check, f"check:{check}")


@dataclass(frozen=True)
class Detection:
    issues: tuple[Issue, ...]
    checked: frozenset[str]


def _detection(issues: list[Issue], checked: set[str]) -> Detection:
    # an observed issue proves its own scope was evaluated
    return Detection(tuple(issues), frozenset(checked | {i.scope for i in issues}))


def _known(*values) -> bool:
    return all(v is not None for v in values)


def _checked_common(key: str, ev, role: str) -> set[str]:
    out = {"source", "evidence"}
    if ev is None:
        return out
    out |= {"report_date", "mapping"}
    if role == "core":
        out |= {"selection", "declared_totals"}
    if key == "sales":
        out.add("period")
    if _known(ev.layout_supported):
        out.add("layout")
    if _known(ev.date_contradictions):
        out.add("dates")
    if _known(ev.external_references):
        out.add("external")
    if _known(ev.duplicate_records):
        out.add("duplicates")
    return out


def _checked_joins(joins: Mapping[str, Any] | None) -> set[str]:
    if not joins:
        return set()
    out = {"joins"}                                     # whether the relationship check ran at all is itself known
    if "failure" in joins:
        return out                                      # every relationship count is UNKNOWN
    for check, keys in JOIN_SCOPE_KEYS.items():
        if all(joins.get(k) is not None for k in keys):
            out.add(f"join:{check}")
    return out


def _checked_core(key: str, ev, v: Mapping[str, Any], joins: Mapping[str, Any] | None) -> set[str]:
    out = _checked_common(key, ev, "core")
    if ev is None:
        return out
    for fname, d in (ev.fields or {}).items():
        if isinstance(d, Mapping):
            out.add(f"field:{fname}")
    rows = v.get("rows") or {}
    if key == "stock" and rows.get("ok"):
        if isinstance(rows.get("counts"), Mapping):
            out.add("rows:counts")
        out |= _checked_footer(rows)
        if isinstance(rows.get("coverage"), Mapping):
            out.add("rows:coverage")
    return out | _checked_joins(joins)


def _checked_footer(rows: Mapping[str, Any]) -> set[str]:
    """Footer scopes that were genuinely evaluated: a column whose footer cells were ALL reconciled and matched
    clears every footer check of that column; anything less (no footer cells, some cells non-comparable or
    mismatched) clears nothing. Unrecognized trailing rows are counted for every analysed report."""
    out: set[str] = set()
    for letter, d in (rows.get("footer_by_column") or {}).items():
        if not isinstance(d, Mapping):
            continue
        checked, matched = d.get("checked"), d.get("matched")
        if isinstance(checked, int) and checked > 0 and matched == checked:
            out |= {f"footer:{letter}:{c}" for c in FOOTER_COLUMN_CHECKS}
    if isinstance(rows.get("ambiguous_rows"), int):
        out.add("footer:ambiguous_rows")
    return out


def _checked_owner(key: str, ev, v: Mapping[str, Any]) -> set[str]:
    out = _checked_common(key, ev, "owner")
    if ev is None:
        return out
    for scope, value in (("cells:errors", ev.error_cells), ("cells:missing_cache", ev.missing_cached_formulas),
                         ("cells:required", ev.missing_required_values), ("totals", ev.totals_consistent)):
        if _known(value):
            out.add(scope)
    if key == "kredi":
        for k in ("negative_remaining_rows", "closed_with_positive_remaining", "hidden_installment_rows"):
            if _known(v.get(k)):
                out.add(f"kredi:{k}")
    return out


def missing_issue(key: str) -> Issue:
    return Issue("missing_source", key, "source:file")


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: dict[str, Issue] = {}
    for i in issues:
        seen.setdefault(i.issue_id, i)
    return list(seen.values())


__all__ = ["Issue", "Check", "CHECKS", "TYPES", "SEVERITIES", "SEVERITY_TR", "STATUSES", "STATUS_TR", "PRESENCE",
           "PRESENCE_TR", "EVENTS", "EVENT_TR", "REASON_TR", "SOURCE_TR", "COUNT_TR", "CORE_SOURCES", "OWNER_SOURCES",
           "EXPECTED_ALWAYS", "DETECTOR_VERSION", "NOT_APPROVAL_TR", "issue_id", "detect_core", "detect_owner",
           "inspect_core", "inspect_owner", "Detection", "scope_of", "FIELD_CHECKS", "JOIN_SCOPE_KEYS",
           "FOOTER_COLUMN_CHECKS",
           "missing_issue", "affected_metrics", "metric_titles", "texts", "OPEN_STATUSES"]
