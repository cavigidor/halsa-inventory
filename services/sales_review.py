"""V1 descriptive review of existing sales calculations; never certifies or feeds action/AI APIs.

The old producers are unchanged. Their known zero substitutions are NOT released:
input-affected derived values are null and their source evidence remains visible.
"""
from __future__ import annotations
import copy
import datetime as dt
import html
import math
import os

import numpy as np
import pandas as pd
from services import generation as GEN, sales_projection as SP, vkm as VKM
from services.review_questions import question

SURFACES = {"momentum": "customers.momentum", "ordersize": "customers.order_size",
            "winback": "customers.winback", "margin": "finance.margin",
            "purchases_yearly": "customer360.profile", "purchases_products": "customer360.profile",
            "sales_source_issues": "customer360.profile", "fairs_review": "fairs.history"}
KEYS = frozenset(SURFACES.values())
COMMON = tuple(SP.SALES_REQUIRED)


def available(manifest):
    rec = manifest.record("sales") if manifest is not None else None
    ev = rec.evidence if rec else None
    return bool(ev and ev.available is True and ev.layout_supported is True and not ev.inspection_failures
                and not any((ev.fields or {}).get(field,{}).get("duplicate_header") for field in COMMON))


def clean(value):
    if isinstance(value, dict): return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [clean(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT: return None
    if isinstance(value, (dt.date, dt.datetime, pd.Timestamp)): return value.isoformat()
    if isinstance(value, np.generic): value = value.item()
    if isinstance(value, float) and not math.isfinite(value): return None
    return value


def missing(series):
    return series.isna() | series.astype(str).str.strip().str.lower().isin(("", "nan", "none", "<na>"))


def numeric(series):
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _warning(code, title, issue, evidence, ask, resolution, severity="yuksek"):
    return dict(code=code, title=title, issue=issue, evidence=evidence, question=ask,
                resolution=resolution, severity=severity)


def _rows_text(numbers):
    values = sorted(int(n) for n in numbers)
    return ", ".join(map(str, values[:20])) + (f" … ({len(values)} satır; tamamı kaynak kanıtı tablosunda)" if len(values)>20 else "")


def warning_html(review):
    e = lambda x: html.escape(str(x if x is not None else "Bilinmiyor"), quote=True)
    out = [f'<section class="aw-box review-box" role="note" data-metric="{e(review["metric"])}" data-verification="{e(review["verification"])}">',
           '<p class="aw-msg">İnceleme için / doğrulanmadı — hesaplanan kaynak analizi</p>',
           f'<p>{e(review["formula"])}</p>', '<details><summary>Kaynak, dönem ve doğrulama kaydı</summary>',
           f'<p>Dosya: {e(review["source"])} · Rapor tarihi: {e(review["report_date"])} ({e(review["date_origin"])})</p>',
           f'<p>Gözlenen işlemler: {e(review["observed_start"])} — {e(review["observed_end"])}. Bu aralık, rapor tarihi veya dönem tamamlığı değildir.</p>',
           f'<p>Kuşak: <code>{e(review["generation"])}</code><br>SHA-256: <code>{e(review["sha256"])}</code></p>',
           f'<p>Doğrulama kaydı: {e(review["verification"])}; inceleme görünürlüğü sertifikayı değiştirmez.</p></details>']
    out.append(f'<details class="review-issues"><summary>Doğrulama uyarıları ve ERP soruları ({len(review["warnings"])})</summary>')
    for w in review["warnings"]:
        out += [f'<details data-check="{e(w["code"])}"><summary>{e(w["title"])} · {e(w["severity"])}</summary>',
                f'<p><b>Sorun / etki:</b> {e(w["issue"])}</p><p><b>Kanıt:</b> {e(w["evidence"])}</p>',
                f'<p><b>ERP kontrol sorusu:</b> {e(w["question"])}</p><p><b>Çözüm için:</b> {e(w["resolution"])}</p></details>']
    out.append('</details></section>')
    return ''.join(out)


def modules(manifest, source_path, gate, *, raw=None, legacy=None):
    """Read one fingerprint-checked sales revision. No aging, owner files, SQLite or provider is read."""
    if not available(manifest) or not source_path: return {}
    frozen = GEN.FrozenSource.read("sales", source_path)
    rec = manifest.record("sales")
    if frozen.sha256 != rec.sha256: return {}
    import dashboard_builder as DB
    from services import customer360 as C360, fairs as FAIRS
    # Even when a caller supplied a frame, validate its columns. Production passes the frozen generation's raw frame.
    raw = DB.read_sales_raw(frozen.stream()) if raw is None else raw.copy()
    if any(list(raw.columns).count(c)!=1 for c in COMMON): return {}
    df = SP.prepare_sales(raw)
    df['_amt'] = numeric(raw['Nettutar']); df['_qty'] = numeric(raw['miktar'])
    df['_amt_missing'] = df['_amt'].isna()
    df['_row'] = np.arange(2, len(raw)+2)
    df['_doc_missing'] = missing(raw['sip_evrakno_seri']) | missing(raw['sip_evrakno_sira'])
    # Literal separators can collapse two distinct document pairs in the inherited producer; mark them unknown.
    df['_doc_missing'] |= raw['sip_evrakno_seri'].astype(str).str.contains('|',regex=False) | raw['sip_evrakno_sira'].astype(str).str.contains('|',regex=False)
    df['_identity_missing'] = missing(df['_cust'])
    df['_sku_missing'] = missing(raw['sto_kod'])
    df['_profit'] = numeric(raw['Net_Tutar_Maliyet_Dusulmus']) if 'Net_Tutar_Maliyet_Dusulmus' in raw else np.nan
    df['_profit_missing'] = df['_profit'].isna()
    df['_duplicate'] = raw.duplicated(keep=False)
    df['_qty_missing'] = df['_qty'].isna()
    df['_date_missing'] = df['_date'].isna()
    base = DB.load_sales(None, raw)
    attrs = DB.cust_attrs(base)
    legacy = legacy or {"momentum":DB.m_momentum(base,attrs), "ordersize":DB.m_ordersize(base,attrs),
                        "winback":DB.m_lapsed(base,{},attrs), "margin":DB.m_margin(base,attrs)}
    names = base.groupby('_cust')['_cname'].first().to_dict()
    latest = df['_date'].max()
    doy = int(latest.dayofyear) if pd.notna(latest) else None
    issues = []
    cost_window = df['_yr'].isin([2025,2026]) | df['_date_missing']
    masks = {"Nettutar eksik / sayı değil":df['_amt_missing'], "Tarih eksik / geçersiz":df['_date_missing'],
             "Belge seri/sıra eksik veya ayraç belirsiz":df['_doc_missing'], "Müşteri kimliği eksik":df['_identity_missing'],
             "Ürün kodu eksik":df['_sku_missing'], "Miktar eksik / sayı değil":df['_qty_missing'],
             "2025–2026 maliyet düşülmüş tutar eksik":df['_profit_missing'] & cost_window,
             "Aynı kaynak satırı tekrarı (ilkler dahil)":df['_duplicate']}
    flagged = pd.Series(False,index=df.index)
    for mask in masks.values(): flagged |= mask
    for ix in df.index[flagged]:
        r = df.loc[ix]
        issues.append(dict(excel_row=int(r['_row']), code=None if r['_identity_missing'] else r['_cust'],
             name=names.get(r['_cust']), date=clean(r['_date']), sku=None if r['_sku_missing'] else r['_sku'],
             amount=clean(r['_amt']), profit=clean(r['_profit']), quantity=clean(r['_qty']),
             issues='; '.join(label for label,mask in masks.items() if mask.loc[ix])))
    common_warnings = [
        _warning('SALES-BASIS','Nettutar, kayıtlı sipariş net tutarıdır',
            'İptal/teslim edilmemiş kısımlar, iadeler ve KDV esası burada yeniden sınıflandırılmadı. Nominal tutar değişimi, gerçek hacim veya tahsilat değildir.',
            f'Kaynak satır: {len(df)}; sıfır tutarlı: {int(df._amt.eq(0).sum())}; negatif tutarlı: {int(df._amt.lt(0).sum())}; eksik tutarlı: {int(df._amt_missing.sum())}.',
            'ERP raporunda Nettutar sipariş/teslim/fatura seçeneklerinden hangisi? Sıfır veya negatif tutarlı belge satırları bedelsiz ürün, iptal, iade ya da iskonto mu; KDV dahil mi?',
            'Rapor filtresini ve ilgili belge satırlarını kontrol edin; eksik tutarı 0 ile tamamlamayın. Bu rapor Nettutar toplamının anlamını değiştirmez.'),
        _warning('DQ-5','Müşteri ve temsilci/KAPALI alanları aynı kimlik olmayabilir',
            'Müşteri = Pro_kodu; yalnızca boşsa Musterikod. Mevcut karşılaştırma üreticileri temsilci/sektörde ilk değeri, KAPALI için satırların en az yarısını kullanır; fatura hesabından taşınan bilgi sipariş müşterisine ait olmayabilir.',
            f'Fatura hesabı geri dönüşü kullanılan satır: {int(df._fallback.sum())}; kimliği bilinmeyen satır: {int(df._identity_missing.sum())}. Kod yazımı ve tekrarlı satırlar değiştirilmedi.',
            'Karşılaştırılan müşterinin Pro_kodu ile Musterikod farklı mı? ERP Excelindeki temsilci ve Açık/Kapalı bilgisi siparişi veren müşteriye mi, fatura hesabına mı ait?',
            'Müşterinin esas temsilci/KAPALI kaynağını belirtin. Buradaki ön sonuç, mevcut üretici kuralını kullanır; CRM kaydı veya otomatik aksiyon oluşturmaz.')]
    for issue in VKM.detect_core(rec, manifest.joins):
        texts = VKM.texts(issue.check,'sales',issue.field_name,issue.column)
        evidence = ', '.join(f'{VKM.COUNT_TR.get(k,k)}: {v}' for k,v in issue.counts.items()) or 'Etkilenen sayı bu denetimde bilinmiyor.'
        common_warnings.append(_warning(issue.check,texts['title'],texts['explain'],evidence,question(issue),texts['action'],issue.severity))
    if int(flagged.sum()):
        common_warnings.append(_warning('SOURCE-ROWS','Eksik veya şüpheli girdiler kaynak satırlarıyla gösterilir',
            'Eksik tutar/tarih/belgeye bağlı türetilmiş değerler — olarak gösterilir. Bilinen kısım toplamı tam toplam değildir. Bazı adaylar eksik girdiler yüzünden mevcut eşik listesine hiç girmeyebilir.',
            f'Etkilenen satır (birden çok sorun tek sayılır): {int(flagged.sum())}. Örnek Excel satırları: {_rows_text(df.loc[flagged,"_row"])}.',
            'Kaynak Kanıtı tablosundaki Excel satırında hangi alan boş veya hatalı? Kaynak raporda kayıtlı gerçek değer var mı; yoksa bu hesap neden bilinmiyor?',
            'Kaynak değerini belge üzerinden tamamlayın veya bilinmiyor olarak bırakın. Rapor tüm kaynak kanıtı kayıtlarını aramayla gösterir.'))
    formulas = {
      'momentum':f'Özgün kural: 2025 ve 2026 gün numarası ≤ {doy}; 2025 toplamı ≥ {DB.MIN_TOTAL}, 2026 > 0. Değişim = (2026−2025)/2025. Tutarlar özgün üreticide tam TL, oran 1 ondalık yuvarlanır; enflasyon düzeltmesi yok.',
      'ordersize':'Özgün kural: 2024–2026; en az 3 belge ve 30.000 Nettutar. Ortalama = toplam / farklı (seri|sıra) belge sayısı; değişim 2026/2025 ortalamalarından. Sıfır tutarlı belgeler de sayılır.',
      'winback':f'Özgün tarama: nitelik yılları {DB.QUAL_YEARS}, toplam ≥ {DB.MIN_TOTAL}; {DB.LAPSED_YEAR} net toplamı ≤ 0. Güçlü yıl > {DB.YEAR_THR}; T1 ≥3, T2=2, T3 yalnız {DB.T3_YEARS}. Bu kuralın adaylarıdır; son yılda hiç sipariş yok sonucu değildir.',
      'margin':'Özgün karşılaştırma kapsamı: 2025–2026, Nettutar ≥ 50.000. Oran = Σ kaynak Net_Tutar_Maliyet_Dusulmus / Σ Nettutar. Eksik maliyette tam kâr ve oran bilinmiyor; yalnız kaydedilmiş kısım ayrıca gösterilir.',
      'purchases_yearly':'Customer 360 mevcut _agg: müşteri × işlem yılı; Nettutar toplamı, farklı seri|sıra ve satır sayısı. Eksik girdide tam toplam/ortalama bilinmiyor; kaydedilmiş kısım ayrı. KAPALI dahil bütün kaynak müşterileridir.',
      'purchases_products':f'Customer 360 mevcut _products: dosyadaki tüm tarihçe, müşteri başına bilinen Nettutar sırasıyla ilk {C360.TOP_PRODUCTS} ürün. Tam liste veya satın alma önerisi değildir; miktar ürün bazında ve kaynak birimiyle, birimler toplanmaz.',
      'sales_source_issues':'Kaynak Excel satırları; hata/eksik/tekrar değiştirilmez. Tutar ve maliyet düşülmüş tutar yalnız kaynakta sayısal olarak okunabiliyorsa gösterilir.',
      'fairs_review':'Mevcut Fuar geçmişi: yalnız FUAR etiketli, tek etiketli seri|sıra belgeleri. Net sipariş tutarı; katılım, sonradan dönüşüm veya ROI değildir.'}
    customer_cols=[{'k':'name','l':'Müşteri','sub':'code'},{'k':'rep','l':'Kaynak temsilci'}]
    money=lambda k,l:dict(k=k,l=l,money=1,exact=1)
    number=lambda k,l:dict(k=k,l=l,n=1)
    definitions={
      'momentum':('Büyüme / Küçülme',customer_cols+[money('y2025','2025 (kaynak kesiti)'),money('y2026','2026 (kaynak kesiti)'),money('delta','Fark'),number('pct','Değişim %')]),
      'ordersize':('Sipariş Büyüklüğü',customer_cols+[number('orders','Belge (2024–26)'),money('rev','Nettutar (2024–26)'),money('aov','Ort. belge tutarı'),number('chg','Ort. tutar değişimi %')]),
      'winback':('Eski Müşteri / Geri Kazanım İncelemesi',customer_cols+[number('tier','Kural kademesi'),money('spend','Nitelik dönemi Nettutar'),number('years','Güçlü yıl'),number('last','Son pozitif nitelik yılı'),number('orders_2026','2026 kaynak belge'),{'k':'last_order','l':'Gözlenen son işlem'}]),
      'margin':('Satış / Kârlılık Kanıtı',customer_cols+[money('rev','Nettutar (2025–26)'),money('known_profit','Kayıtlı maliyet düşülmüş kısım'),money('prof','Tam maliyet düşülmüş toplam'),number('marg','Kaynak oran %'),number('missing_cost','Eksik maliyet satırı')]),
      'purchases_yearly':('Yıllık Müşteri Alışverişi',[{'k':'name','l':'Müşteri','sub':'code'},number('year','Yıl'),number('orders','Belge'),number('lines','Satır'),money('known_amount','Kaydedilmiş Nettutar kısmı'),money('amount','Tam Nettutar'),money('average','Ort. belge'),number('missing_amount','Eksik tutar satırı')]),
      'purchases_products':('Müşteri / Ürün Alışverişi',[{'k':'name','l':'Müşteri','sub':'code'},{'k':'sku','l':'Ürün kodu'},{'k':'product','l':'Ürün'},number('orders','Belge'),money('known_amount','Kaydedilmiş Nettutar kısmı'),money('amount','Tam Nettutar'),dict(k='quantity',l='Kaynak miktar (birimi teyit)',n=1,raw=1),number('missing_amount','Eksik tutar satırı'),{'k':'last','l':'Son gözlenen işlem'}]),
      'sales_source_issues':('Satış Kaynak Kanıtı',[number('excel_row','Excel satırı'),{'k':'name','l':'Müşteri','sub':'code'},{'k':'date','l':'Tarih'},{'k':'sku','l':'Ürün'},money('amount','Kaynak Nettutar'),money('profit','Kaynak maliyet düşülmüş tutar'),{'k':'issues','l':'Eksik / kontrol'}]),
      'fairs_review':('Fuar Sipariş Geçmişi',[{'k':'label','l':'FUAR etiketi'},number('edition_year','Etiket yılı'),{'k':'first_order_date','l':'İlk işlem'},{'k':'last_order_date','l':'Son işlem'},number('orders','Belge'),number('customers','Atfedilen müşteri'),money('known_amount','Kaydedilmiş Nettutar kısmı'),money('net_order_amount','Tam Nettutar'),number('missing_amount_lines','Eksik tutar satırı'),number('customer_conflict_orders','Müşterisi belirsiz belge')])}
    result={}
    for key,(title,cols) in definitions.items():
        metric=SURFACES[key]
        review=dict(metric=metric,status='PRELIMINARY',verification=gate.decide(metric).state.value,
          source=rec.file,sha256=rec.sha256,generation=manifest.core_id,
          report_date=clean(rec.evidence.report_date),date_origin=rec.evidence.report_date_origin,
          observed_start=clean(rec.evidence.earliest_transaction_date),observed_end=clean(rec.evidence.latest_transaction_date),
          formula=formulas[key],warnings=copy.deepcopy(common_warnings))
        result[key]=dict(title=title,rows=[],cols=cols,review=review,note=formulas[key],page_size=100)
        if key in legacy: result[key]['filter']='rep'
    def dirty(window,fields):
        mask=df['_date_missing'].copy()
        for field in fields: mask |= window & df[field]
        mask |= window & df['_identity_missing']
        return set(df.loc[mask,'_cust'])
    windows={'momentum':df['_yr'].isin([2025,2026]) & (df['_date'].dt.dayofyear<=doy) if doy else pd.Series(False,index=df.index),
             'ordersize':df['_yr'].isin([2024,2025,2026]),
             'winback':df['_yr'].isin(DB.QUAL_YEARS+[DB.LAPSED_YEAR]),'margin':df['_yr'].isin([2025,2026])}
    masks_by_key={'momentum':('_amt_missing',),'ordersize':('_amt_missing','_doc_missing'),
                  'winback':('_amt_missing',),'margin':('_amt_missing',)}
    fields_by_key={'momentum':('y2025','y2026','delta','pct'), 'ordersize':('orders','rev','aov','chg'),
                   'winback':('tier','spend','years','last'), 'margin':('rev','prof','marg')}
    profg=df.loc[windows['margin']].groupby('_cust')
    profits=profg['_profit'].sum(min_count=1).to_dict()
    bad_cost=profg['_profit_missing'].sum().to_dict()
    last_dates=df.groupby('_cust')['_date'].max().to_dict()
    orders26=df.loc[df['_yr'].eq(2026)].groupby('_cust')['_ord'].nunique().to_dict()
    docs26=dirty(df['_yr'].eq(2026),('_doc_missing',))
    for key in windows:
        bad=dirty(windows[key],masks_by_key[key])
        amount_bad=dirty(windows[key],('_amt_missing',))
        doc_bad=dirty(windows[key],('_doc_missing',)) if key=='ordersize' else set()
        rows=[]
        for original in legacy[key]:
            row={k:v for k,v in original.items() if k!='detail'}
            code=str(row['code'])
            if key=='ordersize':
                if code in amount_bad:
                    row['rev']=row['aov']=row['chg']=None
                if code in doc_bad:
                    row['orders']=row['aov']=row['chg']=None
            elif code in bad:
                for field in fields_by_key[key]: row[field]=None
            if key=='winback':
                row['orders_2026']=None if code in docs26 else int(orders26.get(code,0))
                row['last_order']=clean(last_dates.get(code))
            if key=='margin':
                row['known_profit']=clean(profits.get(code))
                row['missing_cost']=int(bad_cost.get(code,0))
                if row['missing_cost'] or code in bad:
                    row['prof']=row['marg']=None
            if missing(pd.Series([code])).iloc[0]: row['code']=row['name']=None
            row['input_status']=('Maliyet eksik; kâr/oran bilinmiyor' if key=='margin' and row.get('missing_cost') else 'Girdi eksik; türetilmiş sonuç bilinmiyor' if code in bad else 'Ön inceleme / iş tanımı teyit edilmeli')
            rows.append(row)
        result[key]['cols'].append({'k':'input_status','l':'Kontrol durumu'})
        result[key]['rows']=rows
    missing_cost_count=int((df['_profit_missing'] & windows['margin']).sum())
    result['margin']['review']['warnings'].insert(0,_warning('DQ-1','Eksik maliyet, sıfır kâr değildir',
        'Eski üretici eksik maliyet düşülmüş tutarı sıfıra çevirir. Bu inceleme o müşterinin tam kârını/oranını göstermez; kaydedilmiş kısım, eksiklerin yerine geçmez.',
        f'2025–2026 eksik maliyet satırı: {missing_cost_count}; etkilenen müşteri: {int(df.loc[df._profit_missing & windows["margin"],"_cust"].nunique())}. Satırlar Satış Kaynak Kanıtında.',
        'Bu Excel satırlarında Net_Tutar_Maliyet_Dusulmus neden boş? ERP maliyet kaydı eksik mi, formül sonucu mu kaydedilmemiş? Kaydedilmiş değer hangi maliyet esasıyla oluşuyor?',
        'Eksik değerleri ERP kaydından doğrulayın. Tam maliyet kapsamı olmadan bu müşteri için tam kâr/oran bilinmiyor kalır; yeni muhasebe kuralı varsayılmaz.'))
    for key in ('momentum','ordersize','winback'):
        result[key]['review']['warnings'].insert(0,_warning('CALCULATION-SCOPE','Karşılaştırma kuralını kaynak filtreleriyle kontrol edin',
            formulas[key],f'Bu kurala göre listelenen kaynak müşteri: {len(result[key]["rows"])}. Kaynak döneminin tamamlığı: {"beyan edildi" if rec.evidence.period_complete is True else "bilinmiyor / eksik"}.',
            {'momentum':'ERP Excelde 2025 ve 2026 için belirtilen gün numarası sınırına kadar Nettutar toplamları aynı mı? Değişimin fiyat/kur ve iptal/iade etkisi nedir?',
             'ordersize':'ERPde aynı seri|sıra belgesi yıllar arasında tekrar kullanılıyor mu? Bedelsiz veya iptal belgelerinin de sayılması bu müşteri için ortalamayı nasıl etkiliyor?',
             'winback':'2026 net toplamı sıfır/eksi olan müşterinin o yıl belge satırı var mı? İade/iptal pozitif alışverişi sıfırlıyor mu? 2021–25 nitelik penceresi ve kademe eşikleri tarama amacınıza uygun mu?'}[key],
            'Belge ve tarih filtrelerini Excel üzerinde karşılaştırın. Mevcut eşikler/yıllar değiştirilmedi; bu tablo aksiyon veya kesin kayıp müşteri kararı değildir.'))
    usable=df.loc[~df['_identity_missing']].copy()
    annual=C360._agg(usable,['_cust','_yr'],missing_amount=('_amt_missing','sum'),missing_docs=('_doc_missing','sum')).reset_index()
    yearly=[]
    undated_customers=set(df.loc[df['_date_missing'],'_cust'])
    for r in annual.to_dict('records'):
        row=dict(code=r['_cust'],name=names.get(r['_cust']),year=int(r['_yr']),lines=int(r['lines']),
            orders=None if r['missing_docs'] or r['_cust'] in undated_customers else int(r['orders']),known_amount=clean(r['amount']),
            amount=None if r['missing_amount'] or r['_cust'] in undated_customers else clean(r['amount']),missing_amount=int(r['missing_amount']))
        row['average']=row['amount']/row['orders'] if row['amount'] is not None and row['orders'] else None
        yearly.append(row)
    result['purchases_yearly']['rows']=yearly
    product_groups=usable.groupby(['_cust','_sku'])
    product_bad_amount=product_groups['_amt_missing'].sum().to_dict()
    product_bad_qty=product_groups['_qty_missing'].sum().to_dict()
    product_bad_doc=product_groups['_doc_missing'].sum().to_dict()
    products=C360._products(usable).reset_index()
    pattern=[]
    for r in products.to_dict('records'):
        key=(r['_cust'],r['_sku']); badsku=bool(missing(pd.Series([r['_sku']])).iloc[0])
        pattern.append(dict(code=r['_cust'],name=names.get(r['_cust']),sku=None if badsku else r['_sku'],product=r.get('name'),
            orders=None if product_bad_doc.get(key) else int(r['orders']),known_amount=clean(r['amount']),
            amount=None if product_bad_amount.get(key) else clean(r['amount']),
            quantity=None if badsku or product_bad_qty.get(key) else clean(r['qty']),
            missing_amount=int(product_bad_amount.get(key,0)),last=clean(r.get('last'))))
    result['purchases_products']['rows']=pattern
    result['sales_source_issues']['rows']=issues
    history=FAIRS.build_fair_history(base)
    if history.status=='ok':
        rows=[]
        for edition in history.editions.values():
            row=edition.summary()
            row.pop('quantity',None); row.pop('products',None)
            row['known_amount']=row['net_order_amount']
            if row['missing_amount_lines']: row['net_order_amount']=None
            rows.append(row)
        result['fairs_review']['rows']=rows
        diag=history.diagnostics
        result['fairs_review']['review']['warnings'].insert(0,_warning('FUAR-SCOPE','Etiket, katılım veya dönüşüm kanıtı değildir',
            'Sadece tanınan FUAR etiketi; ETKİNLİK/SAHA/GENEL ayrı. Farklı etiketli tek belge dışarıda; eksik tutarlı tam toplam bilinmiyor. Fuar miktarları farklı ürün birimleri arasında toplanarak gösterilmez.',
            f'Tanınmayan farklı etiket: {diag.get("unparsed_labels")}; etiketi çelişen belge: {diag.get("label_conflict_orders")}; eksik tutarlı FUAR satırı: {diag.get("fuar_missing_amount_lines",0)}.',
            'ERP Excelde Srmmerk etiketi gerçekten bu fuara atfedilen siparişi mi gösteriyor? Aynı belgenin satırlarında farklı etiket var mı? Sonraki GENEL siparişleri fuara bağlayan kayıt bulunuyor mu?',
            'Belge etiketi/tarih/müşteri kayıtlarını karşılaştırın. Katılım, maliyet veya sonraki satış bağı yoksa dönüşüm/ROI hesaplanmaz.'))
    else:
        result['fairs_review']={'title':definitions['fairs_review'][0],'rows':[],'cols':[],'withheld':{
            'metric':SURFACES['fairs_review'],'verification':'UNAVAILABLE','message':'FUAR kaynak geçmişi kullanılamıyor.',
            'reasons':['Satış Excelinde Srmmerk etiket sütunu gerekir; diğer satış analizleri bu eksikten etkilenmez.']}}
    for module in result.values():
        if module.get('review'): module['review_html']=warning_html(module['review'])
    return clean(result)
