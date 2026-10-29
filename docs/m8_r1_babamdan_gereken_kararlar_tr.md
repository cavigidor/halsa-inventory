# Halsa — Veri Doğrulama: Babamdan Gereken Kararlar

_29 Ekim 2026 · 5 Ekim 2026 tarihli Stok, Satış ve Cari raporlarına göre hazırlandı._

Baba merhaba,

Panelde ilk **doğru ve güvenilir** rakamları gösterebilmek için 5 Ekim raporlarını satır satır kontrol ettik.
Programdan kaynaklanan sorunları biz düzelteceğiz (en altta, E bölümü). Aşağıdaki sorular ise şirketin işleyişini
bilen birinin cevaplaması gereken konular.

- Cevabı bilmediğin yerde **"bilmiyorum, şuna soralım"** demen yeterli. Tahmin etmene gerek yok; yanlış bir cevap,
  boş bir cevaptan daha zararlı.
- Her sorunun yanında kimin cevaplayabileceğini yazdık: **sen**, **muhasebe**, **satış ekibi** ya da **raporu
  DinamikCRM'den çıkaran kişi**.
- Bu belgede müşteri, ürün kodu veya tutar yok. Kontrol için gereken kodları ve sipariş numaralarını sana ayrıca ve
  özel olarak veririm.
- Cevaplar gelene kadar panel bu konulardaki rakamları **göstermemeye** devam eder.

**İlk hedef:** önce iki ekranı doğrulamak — **Tahsilat (cari bakiyeler)** ve **Azalan / Fazla Stok** listeleri.
Bu iki ekran için **ilk sorulması gereken** iş soruları A, B ve C1'dir. C2–D, satış ve müşteri ekranları için sonraki
adımdır.

**Önemli:** A, B ve C1'in cevapları bir ekranı açmak için **tek başına yetmez**. Cevaplardan sonra da sırasıyla şunlar
gerekir: bizim yapacağımız teknik düzeltmeler ve bunların ayrı ayrı test edilmesi, rakamların bağımsız olarak
yeniden kontrol edilmesi, kararların yazılı olarak kaydedilmesi ve panelin genel yayın kontrolünden geçmesi. Bu
adımların hepsi tamamlanmadan panel bir rakamı "doğrulanmış" olarak göstermez.

---

## A. Raporların tarihi — *raporu DinamikCRM'den çıkaran kişiye*

Bir rakamı doğru kabul etmek için **hangi güne ait olduğunu** bilmemiz gerekiyor. Dosya adındaki tarih ya da
dosyanın kaydedildiği saat bunu kanıtlamıyor.

**A1. Stok raporu hangi anın stoğunu gösteriyor?**
Raporun içinde tarih yok. Dosya 6 Ekim sabahı Excel'de kaydedilmiş.
- ☐ 5 Ekim gün sonu ☐ 6 Ekim sabahı ☐ Başka: ______ ☐ Bilmiyorum
- *Ek soru:* Stok raporu **bütün** ürün kartlarını mı listeliyor, yoksa yalnızca aktif olanları mı? (Satış raporunda
  geçen 256 ürün stok raporunda yok; bunların hiçbiri 2026'da satılmamış.)

**A2. Satış raporunda 5 Ekim günü tam mı?**
Satış raporundaki son gün 5 Ekim. Rapor gün bitmeden alındıysa o günün satışları eksik kalır; bunu rapordan
anlayamıyoruz. Panelin "5 Ekim'e kadar" diyebilmesi için bilmemiz gerekiyor.
- ☐ Evet, 5 Ekim tamamdır ☐ Hayır, gün içinde alındı ☐ Bilmiyorum
- *Ek soru:* Satış raporu 27 Ağustos 2020'de başlıyor. Daha eski kayıt sistemde yok mu?
  ☐ Yok, sistem o tarihte başladı ☐ Var, rapora alınmadı ☐ Bilmiyorum

**A3. Cari raporundaki bakiyeler hangi tarih itibarıyla?** *(+ muhasebe)*
Son ay sütunu "Eylül-2026"; Ekim sütunu yok.
- ☐ 30 Eylül itibarıyla ☐ 5 Ekim itibarıyla ☐ Başka: ______ ☐ Bilmiyorum
- Ay sütunları neye göre? ☐ Faturanın kesildiği ay ☐ Vadenin dolduğu ay ☐ Bilmiyorum

---

## B. Stok kararları — *sen*

**B1. Kapsamı hesaplanamayan ürünler ne olsun?** *(önceki 9. sorunun yeni bilgilerle hali)*
1.178 üründe "STOK Kontrol %" `#DIV/0!` gösteriyor. Sebebi: bu ürünlerin 2024–2026 arasında **hiç çıkışı yok**,
bu yüzden oran hesaplanamıyor. Bunların 472'sinde stok var; 263'ü "ÜRETİMDEN KALKTI" işaretli. **Bugün bu ürünler
Azalan / Fazla Stok listelerinde hiç görünmüyor**; listeler bu yüzden eksik.
- ☐ a) Stoğu olanlar ayrı bir **"kapsam hesaplanamadı — hareketsiz stok"** listesinde gösterilsin; stoğu sıfır
  olanlar gösterilmesin.
- ☐ b) (a) gibi, ama "ÜRETİMDEN KALKTI" olanlar ayrı bir grupta gösterilsin.
- ☐ c) Bu ürünler gösterilmesin; yalnızca "şu kadar ürün hesaplanamadı" notu kalsın.
- ☐ d) Başka bir kural: ______

**B2. "STOK Kontrol %" neyi ölçüyor?** *(sen ya da raporu kuran kişi)*
Rapordaki formül:
(siparis_dusulmus_miktar + sipariş + Yoldaki Ürünler) ÷ ((2024 + 2025 + 2026 Çıkış Miktarı) ÷ 6).
Azalan Stok listesi bu oranı **minseviye** ile, Fazla Stok listesi **maxseviye** ile karşılaştırıyor. İkisi aynı
ölçüde değilse listeler yanlış olur.
- Bu oran neyi gösteriyor? (ör. "kaç aylık stok", "kaç dönemlik stok") ______ ☐ Bilmiyorum
- minseviye / maxseviye aynı ölçüyle mi girildi? ☐ Evet ☐ Hayır ☐ Bilmiyorum

**B3. Aynı stok kodu iki satırda.** *(depoya / satış ekibine baktır, kararı sen ver)*
1 ürün raporda iki ayrı satırda: adı aynı, ama stok miktarı, stok değeri ve maliyeti farklı. Ürün 2026'da da
satılıyor.
- ☐ a) İki ayrı depo / parti — **toplanmalı**
- ☐ b) Biri hatalı — DinamikCRM'de düzeltilecek
- ☐ c) İki satır ayrı ayrı gösterilsin
- ☐ d) Bilmiyorum, depo kontrol etsin
- *Ek:* 3 üründe stok miktarı **eksi (–)** görünüyor. ☐ Gerçek (fazla satış / girilmemiş alış) ☐ Kayıt hatası
  ☐ Bilmiyorum

**B4. "Envanter değeri" hangi maliyetle hesaplanıyor?** *(muhasebe — yalnızca "fazla stok değeri" için gerekli)*
- ☐ Son alış fiyatı ☐ Ortalama maliyet ☐ Güncel maliyet ☐ Başka: ______ ☐ Bilmiyorum

---

## C. Cari ve müşteri kararları

**C1. Tahsilat listesinde hangi hesaplar olsun?** *(muhasebe bilgi versin, kararı sen ver)*
Cari raporunda 120 ile başlayan 796, 320 ile başlayan 28 hesap var. Tek Düzen Hesap Planı'nda 320 "Satıcılar"
(tedarikçi) hesabıdır; Halsa'da da böyle mi, muhasebe teyit etsin. Ayrıca 824 hesabın 402'sinin bakiyesi eksi.
- 320'li hesaplar: ☐ Tahsilatta hiç gösterilmesin ☐ Ayrı bir bölümde gösterilsin ☐ Tahsilatta kalsın
- Eksi bakiye ne demek? ☐ Müşteri bize alacaklı (avans / fazla ödeme) ☐ Başka: ______ ☐ Bilmiyorum
- Eksi bakiyeler nasıl gösterilsin? ☐ Ayrı "alacaklı hesaplar" listesi ☐ Toplamdan düşülsün ☐ Gösterilmesin
- Hangi aylar **"vadesi geçmiş"** sayılır? ☐ Tablodaki bütün aylar ☐ Rapor tarihinden önceki aylar ☐ Muhasebe vadeye
  göre söylesin

**C2. Müşteri ana listesinde aynı koda iki kayıt (7 müşteri).** *(muhasebe)*
Bu 7 müşterinin iki kaydı **açık / kapalı** durumunda ve **temsilcide** birbirini tutmuyor; 3'ü 2026'da sipariş vermiş.
Hangisinin doğru olduğunu bilmeden bu müşterileri "açık" ya da "kapalı" gösteremeyiz.
- Muhasebe hangi kaydın güncel olduğunu söyleyip DinamikCRM'de düzeltebilir mi? ☐ Evet ☐ Hayır
- Düzeltilene kadar bu müşterilerin durumu panelde **"bilinmiyor"** kalır.

**C3. Müşteri ana listesinde olmayan 30 sipariş kodu.** *(satış ekibi)*
Satış raporunda "siparişi veren" olarak geçen 30 kod ana listede yok. Bu satırların üçte ikisi fuar / etkinlik
siparişi; çoğu 2021–2022'de, yalnızca 1'i 2026'da. Faturaları bilinen müşterilere kesilmiş.
- ☐ a) Eski / silinmiş müşteri kartları — geçmişte "ana kartta yok" diye kalsın
- ☐ b) Bunlar müşteri değil, fuar / proje kodu — hangi müşteriye sayılacağını satış ekibi listelesin
- ☐ c) Satış ekibi tek tek baksın

**C4. Bayi üzerinden faturalanan müşteriler.** *(sen — önceki 10. soru)*
712 müşteride siparişi veren müşteri ile faturanın kesildiği hesap farklı (bayi faturası). Müşterinin **temsilcisi**
ve **açık / kapalı** durumu nereden alınsın?
- ☐ a) Siparişi veren müşterinin kendi ana kartından
- ☐ b) Faturanın kesildiği hesabın kartından
- ☐ c) İkisi ayrı ayrı gösterilsin
- ☐ d) Başka: ______
- *Ek:* Bazı kodlar yalnızca başındaki / sonundaki **boşluk** yüzünden farklı görünüyor (1 fatura hesabı bu yüzden
  eşleşmiyor). Boşluk dışında aynı olan kodlar aynı müşteri sayılsın mı? ☐ Evet ☐ Hayır ☐ Bilmiyorum

---

## D. Satış — *sonraki adım için*

**D1. Tekrar eden 4 satış satırı (2022, 3 sipariş).** *(satış ekibi)*
Bu satırların her alanı birebir aynı. DinamikCRM'de bu siparişlere bakınca ürün **iki kez mi** yazılı?
(Sipariş numaralarını ayrıca veririm.)
- ☐ Evet, iki kez yazılı — ikisi de sayılır ☐ Siparişte bir kez var — biri sayılmaz ☐ Bilmiyorum

**D2. Hatırlatma — "ciro" tanımı** *(önceki 3. soru, hâlâ bekliyor)*
Sipariş tutarı mı, faturalanan mı, teslim edilen mi; iptal ve iadeler nasıl düşülecek? Satış rakamlarının hiçbiri bu
cevap olmadan gösterilemez. Tahsilat ve stok ekranları için gerekli değil.

---

## E. Bizim çözeceğimiz teknik konular — *senden karar gerekmiyor*

- Cari raporunun sonundaki boş satırı program "tutarı boş hesap" sanıyor. Düzelteceğiz (hesaplarda boş tutar yok).
- Müşteri ana listesi sayfası henüz diğer sayfalar kadar ayrıntılı denetlenmiyor; eklenecek.
- Satış ekranlarının, bayi faturası yapısı yüzünden gereksiz yere "hatalı" görünmemesi için her ekranın hangi bilgiyi
  kullandığını ayrı ayrı tanımlayacağız.
- Stok raporundaki "Birim Maliyet" sütunundaki `#DIV/0!` hataları stoğu sıfır olan ürünlerden geliyor; stok
  listelerini etkilemiyor.

Bu düzeltmelerin hiçbiri bir rakamı kendiliğinden "doğru" yapmaz. Her ekran, senin cevapların ve son kontrolden sonra
ayrı ayrı açılacak.
