"""Generate one private, offline, read-only general report from explicit Stock/Sales inputs.

No Cari/owner workbook, database, AI, application session, publication or backend is used.
The output never overwrites a file; source files are read only, frozen, and checked again.
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
import json
from pathlib import Path
import tempfile

from services import accuracy as ACC, generation as GEN, paths as PATHS
from services import sales_review as SALES, stock_review as STOCK


def generate(data_folder, output, *, today=None):
    import dashboard_builder as DB
    today=today or dt.date.today()
    data_folder=str(Path(data_folder).resolve())
    target=Path(output).absolute()
    if target.suffix.lower()!='.html': raise ValueError('Çıktı yeni bir .html dosyası olmalı.')
    if target.exists() or target.is_symlink(): raise FileExistsError('Çıktı dosyası zaten var; yeni bir ad seçin.')
    # Require explicit monthly input selection. Never pick an optional older Cari by mtime.
    with open(Path(data_folder)/GEN.SELECTION_FILE,encoding='utf-8') as stream:
        selection=json.load(stream)
    if selection.get('schema')!=GEN.SELECTION_SCHEMA or not isinstance(selection.get('core'),dict):
        raise ValueError('Stok ve Satış için geçerli sources.json gerekir.')
    paths,declarations,origin={}, {}, 'explicit'
    for key in ('stock','sales'):
        entry=selection['core'].get(key,{})
        name=entry.get('file') if isinstance(entry,dict) else None
        if not isinstance(name,str) or not name or name in ('.','..') or '/' in name or '\\' in name:
            raise ValueError('Stok ve Satış dosyaları açıkça seçilmeli; yalnız dosya adı kullanılmalı.')
        paths[key]=str(Path(data_folder)/name)
        if not Path(paths[key]).is_file(): raise ValueError('Seçilen ana kaynak bulunamadı.')
        declarations[key]=GEN._declaration(key,entry)  # existing date/provenance validation, no new inference

    PATHS.check({'DATA_FOLDER':data_folder,'DASHBOARD_PATH':str(target)})
    frozen=GEN.freeze(paths)
    frames=GEN.frames_from_frozen(frozen)
    manifest=GEN.build_manifest(frozen,frames,{k:declarations.get(k,{}) for k in paths},origin,today=today)
    gate=ACC.current_gate(today=today).bound_to(manifest.core_snapshot())
    modules={'stock_review':STOCK.module(manifest,paths['stock'],gate)}
    if modules['stock_review'].get('review'):
        modules['stock_review']['review_html']=STOCK.warning_html(modules['stock_review']['review'],offline=True)
    modules.update(SALES.modules(manifest,paths['sales'],gate,raw=frames['sales']))
    if not any(m.get('review') for m in modules.values()): raise ValueError('Seçilen kaynaklardan inceleme üretilemedi.')
    # One honest scope section, rather than server-only/CRM/action tabs that cannot work offline.
    modules['report_scope']={'title':'Veri Kapsamı / Eksikler','rows':[
        {'section':'Cari hesap bakiyeleri','state':'Genel raporda yok','need':'Bu genel rapora Cari kaynağı okunmaz ve hesap bakiyesi eklenmez. Güncel Kasım Cari yaşlandırma raporu bu teslimde seçilmedi; eski dosya/temsilci listesi yerine kullanılmaz.'},
        {'section':'Vade / risk / tahsilat önceliği','state':'Hesaplanmaz','need':'Fatura yaşı vade değildir; anlaşmaya dayalı ayrı rapor gerekir. Emekliye ayrılmış aksiyonlar üretilmez.'},
        {'section':'Stok sipariş / fazla stok / eritme önerileri','state':'Emekliye ayrıldı (D-070)','need':'Eski min/maks ve STOK Kontrol % güncel satın alma politikası değildir. Ham stok kanıtı ayrı gösterilir.'},
        {'section':'Hedef / Trodat ek analizleri','state':'Kaynak seçilmedi','need':'İlgili asıl raporlar bu Stock/Sales tesliminin girdileri değildir; eksikleri mevcut analizleri engellemez.'},
        {'section':'Kredi / Gider-Gelir / Bütçe','state':'Finansal teslim kapsamı dışında','need':'Genel rapora bu kaynaklar okunmaz. Yetkilendirilmiş ayrı özel finansal teslim burada oluşturulmaz.'}],
        'cols':[{'k':'section','l':'Bölüm'},{'k':'state','l':'Durum'},{'k':'need','l':'Kapsam / gerekli kaynak'}],
        'note':'Bu dosya çevrimdışı, salt okunur özel rapordur; şirket içi yetkili alıcıyla paylaşın. Web uygulamasının şifreleri dosyada uygulanamaz.'}
    keys=sorted({m['review']['metric'] for m in modules.values() if m.get('review')})
    meta={'date':f'{today.day} {DB.AYLAR[today.month]} {today.year}', 'stock':frozen['stock'].file,
          'sales':frozen['sales'].file,'aging':'','has_aging':False,'tiers':{},'total':None,'total_shown':False,
          'show_tiers':False,'withheld_stats':[],'withheld_panels':{},'accuracy_policy':ACC.POLICY_VERSION,
          'generation':manifest.core_id,'asof':'','displayed':[],'reviewed':keys,
          'intro':'Bu özel rapor, seçilen Stok ve Satış Excel dosyalarından hesaplanan ön inceleme sonuçlarını içerir. Dönem, belge, müşteri kimliği ve eksik maliyet uyarılarını kaynak raporla kontrol edin. Bilinmeyen değerler — olarak kalır. Cari hesap bakiyeleri genel rapora dahil değildir.'}
    body=DB._html(modules,[],meta,[],ai_panel=False,c360_panel=False,sq_panel=False,opp_panel=False,
                  fair_panel=False,crm_panel=False,own_panel=False,vkm_panel=False,up_panel=False)
    if any(GEN.sha256_file(paths[k])!=source.sha256 for k,source in frozen.items()):
        raise ValueError('Kaynak değişti; karışık kuşak raporu yazılmadı.')
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,staged=tempfile.mkstemp(prefix='.father-report-',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        os.link(staged,target)  # exclusive + atomic: never replace input, report, or symlink created meanwhile
        PATHS.fsync_dir(str(target.parent))
    finally:
        os.unlink(staged)
    return {'generation':manifest.core_id,'sections':{k:len(m['rows']) for k,m in modules.items()},
            'sources':{k:s.sha256 for k,s in frozen.items()},'output':str(target)}


def main():
    parser=argparse.ArgumentParser(description='Özel çevrimdışı Halsa genel inceleme raporu; Cari bakiyesi içermez.')
    parser.add_argument('--data',required=True,help='Açık kaynak seçimi sources.json bulunan özel veri klasörü')
    parser.add_argument('--output',required=True,help='Henüz var olmayan özel .html çıktı dosyası')
    args=parser.parse_args()
    try:
        result=generate(args.data,args.output)
    except (ValueError,OSError,KeyError):
        parser.exit(2,'Rapor oluşturulamadı. Kaynak seçimini/sütunlarını ve yeni çıktı adını kontrol edin; dosyalar değiştirilmedi.\n')
    print('Özel çevrimdışı rapor oluşturuldu. Cari hesap bakiyesi içermez; yalnız yetkili alıcıyla özel paylaşın.')
    print('İnceleme bölümü: '+str(len(result['sections'])))


if __name__=='__main__': main()
