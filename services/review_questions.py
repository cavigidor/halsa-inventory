"""Specific, deterministic ERP questions for inherited VKM findings; no workflow or certification."""

def question(issue):
    field = issue.field_name or (f"{issue.column} sütunu" if issue.column else "kaynak kayıtları")
    check = issue.check
    if check == "stock_duplicate_code":
        return "Tablodaki yinelenen kodların Excel satırları bu ayın ERP raporunda da ayrı mı? Eski formülü taşımak için kalan aktarım satırı var mı; hangi kayıt güncel? Satırlar otomatik birleştirilmez."
    if check in ("stock_missing_identity", "footer_ambiguous"):
        return "Kod alanı boş gösterilen Excel satırları ürün kaydı mı, rapor toplamı mı? Aynı satırdaki ad ve miktarı ERP Excel ile karşılaştırıp satırın işlevini belirtin."
    if check.startswith("footer_"):
        return f"{field} için raporun alt toplam formülü hangi Excel satırlarını kapsıyor? Gizli/filtreli satırlar ve hata hücreleri dahil edildiğinde kaynak toplamı aynı mı?"
    if check.startswith("stock_coverage"):
        return "STOK Kontrol % hata kodu taşıyan satırlarda formülün çıkış miktarı/böleni sıfır mı? Aynı satırdaki kayıtlı stok miktarı ERP ile aynı mı? Tanımsız oran, sıfır stok demek değildir."
    if check in ("field_excel_errors", "field_missing_cache"):
        return f"{field} sütunundaki hata veya sonucu kaydedilmemiş formülün Excel girdileri neler? Kaynak dosyada hesaplanmış değer var mı; hata diğer bir dosyaya bağlantıdan mı geliyor?"
    if check in ("amount_blank", "required_blank"):
        return f"{field} hücreleri kaynak Excel'de gerçekten boş mu, yoksa görünmeyen bir hata/formül mü var? Boş kayıt için tutar bilinmiyor mu, yoksa kaynakta açıkça 0 mı yazıyor?"
    if check in ("field_invalid_dates", "date_contradiction"):
        return f"{field} ve orijinal dosya adındaki tarih, Excel'in rapor filtresiyle uyuşuyor mu? Metin/geçersiz tarih olan satırlar hangi döneme ait? Son işlem tarihi rapor tarihi yerine kullanılmaz."
    if check == "report_date_missing":
        return "Gönderilen orijinal Excel dosyasının adındaki rapor tarihi nedir? Onaylı dosya-adı kuralına göre bu tarih kaynak seçim kaydına aktarılmış mı?"
    if check.startswith("period_"):
        return "Satış Excel'indeki başlangıç/bitiş filtreleri hangi dönemi kapsıyor; son gün tamam mı? Dosyadaki en son işlem, dönemin eksiksiz olduğunu tek başına kanıtlamaz."
    if check == "selection_not_explicit":
        return "Kaynak bilgisinde yazan dosya bu ay için gönderdiğiniz orijinal ERP Excel'i mi? Aynı klasördeki eski raporun yanlış seçilmediğini dosya adıyla kontrol edin."
    if check in ("mapping_unknown", "mapping_not_approved", "duplicate_header", "layout_unsupported"):
        return f"{field} başlığı beklenen rapor sütununda mı; aynı başlık iki kez var mı? Stokta onaylı A=ürün kodu, J=ülke düzeni korunmuş mu; sütunlar kaymış mı?"
    if check.startswith("join_"):
        return "Eşleşmeyen/çoklu eşleşen kayıtta siparişi veren Pro_kodu ile fatura hesabı Musterikod farklı mı? Temsilci/KAPALI bilgisi siparişi veren müşteriye mi, fatura hesabına mı ait? Ürün kodu stok dosyasında aynı yazımla bulunuyor mu?"
    if check == "sales_duplicate_rows":
        return "Aynı belge/ürün/tarih/tutarı taşıyan satırlar ERP Excel'de gerçekten iki işlem mi, yoksa tekrar kopya mı? Rapor bunları silmeden ayrı sayar; belge satırlarını karşılaştırın."
    if check == "external_links":
        return f"{field} formüllerinin bağlı olduğu diğer Excel dosyası mevcut ve güncel mi? Bağlantı açıldığında gösterilen sonuç ile bu dosyanın kaydedilmiş sonucu aynı mı?"
    if check in ("inspection_failed", "joins_not_checked", "import_failed"):
        return f"{field} için seçilen Excel açılıyor mu ve beklenen sütunlar mevcut mu? Denetim tamamlanmadığı için hangi kayıtların okunamadığını kaynak rapor üzerinden kontrol edin."
    if "totals" in check:
        return f"{field} için kaynak toplam hücresi ile kapsadığı satırlar aynı mı? Dönem, filtre ve formül aralığı hangi kayıtları dışarıda bırakıyor?"
    return f"{field} için '{issue.spec.title}' bulgusuna neden olan kaynak hücreler hangileri? Sütun başlığı, Excel satırı ve rapor filtresini birlikte kontrol edin; eksik bilgiyi tahminle doldurmayın."
