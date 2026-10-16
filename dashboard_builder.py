"""İnteraktif pano oluşturucu — sayfaları hesaplar ve tek bir HTML dosyası üretir."""
import os, glob, json, html, datetime as dt, warnings
import pandas as pd, numpy as np
warnings.filterwarnings('ignore')

STOCK_HEADER_ROW=3; DISCONTINUED='ÜRETİMDEN KALKTI'
YEAR_THR=10000; MIN_TOTAL=30000; QUAL_YEARS=[2021,2022,2023,2024,2025]; LAPSED_YEAR=2026; T3_YEARS=[2024,2025]
SC={'code':'Stok_Kodu','name':'Stok_Adı','stock':'TOPLAM STOK MİKTAR ','cov':'STOK Kontrol %',
    'min':'minseviye','max':'maxseviye','inv':'Envanter değeri','status':'DURUM'}
AYLAR=['','Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık']

def _latest(folder,pred):
    m=[f for f in glob.glob(os.path.join(folder,'*.xlsx')) if pred(os.path.basename(f))]
    return max(m,key=os.path.getmtime) if m else None
def is_stock(n): return 'stok' in n.lower()
def is_aging(n):
    x=n.lower(); return 'cari' in x or 'yas' in x or 'yaş' in x
def is_sales(n):
    x=n.lower()
    if is_stock(n) or is_aging(n): return False
    return 'sat' in x or 'bazl' in x
def select_sources(folder):
    """The current M5 source set, picked once (M5-5): {'stock','sales','aging'} paths (aging may be None)."""
    return {'stock':_latest(folder,is_stock),'sales':_latest(folder,is_sales),'aging':_latest(folder,is_aging)}
def read_stock(path): return pd.read_excel(path,header=STOCK_HEADER_ROW)
def read_sales_raw(path): return pd.read_excel(path)
def read_aging_raw(path): return pd.read_excel(path)
def num(s): return pd.to_numeric(s,errors='coerce')
def _n(x,d=0):
    try:
        v=float(x); return round(v,d) if d else int(round(v))
    except: return 0
def _money(x):
    try: return '₺{:,.0f}'.format(float(x))
    except: return '—'

# ---------- sayfalar ----------
def m_stock(path,df=None):
    df=read_stock(path) if df is None else df.copy()
    for k in ['stock','cov','min','max','inv']: df[SC[k]]=num(df[SC[k]])
    band=df[SC['max']]>0; hc=df[SC['cov']].notna(); ins=df[SC['stock']].fillna(0)!=0
    disc=df[SC['status']].astype(str).str.contains(DISCONTINUED,na=False)
    low=df[band&hc&(df[SC['cov']]<df[SC['min']])&~disc].copy()
    low['_u']=low[SC['cov']]/low[SC['min']].replace(0,np.nan); low=low.sort_values('_u',na_position='first')
    over=df[band&hc&(df[SC['cov']]>df[SC['max']])&ins].copy()
    over['_d']=disc[over.index]; over=over.sort_values(SC['inv'],ascending=False)
    low_rows=[{'code':str(r[SC['code']]),'name':str(r[SC['name']]),'stock':_n(r[SC['stock']]),
               'cov':_n(r[SC['cov']],2),'min':_n(r[SC['min']],2),'crit':bool(r[SC['cov']]<0)} for _,r in low.iterrows()]
    over_rows=[{'code':str(r[SC['code']]),'name':str(r[SC['name']]),'stock':_n(r[SC['stock']]),
                'cov':_n(r[SC['cov']],2),'inv':_n(r[SC['inv']]),'disc':bool(r['_d'])} for _,r in over.iterrows()]
    return low_rows,over_rows,int(len(df))

def load_sales(path,df=None):
    df=read_sales_raw(path) if df is None else df.copy()
    df['_date']=pd.to_datetime(df['Tarih'],errors='coerce'); df['_yr']=df['_date'].dt.year
    df['Nettutar']=num(df['Nettutar']).fillna(0); df['miktar']=num(df['miktar']).fillna(0)
    df['_prof']=num(df['Net_Tutar_Maliyet_Dusulmus']).fillna(0) if 'Net_Tutar_Maliyet_Dusulmus' in df.columns else 0.0
    df['_ord']=df['sip_evrakno_seri'].astype(str)+'|'+df['sip_evrakno_sira'].astype(str)
    _pk=df['Pro_kodu'].astype(str).str.strip()
    m=df['Pro_kodu'].notna()&(_pk!='')&(_pk.str.lower()!='nan')
    df['_cust']=df['Pro_kodu'].where(m,df['Musterikod']).astype(str)          # siparişi aldığımız taraf (E/F)
    df['_cname']=df['ProjeIsmi'].where(m,df['Musteri_ismi']).astype(str).str.strip()
    df['_inv']=df['Musterikod'].astype(str)                                    # fatura hesabı (C)
    df['_invname']=df['Musteri_ismi'].astype(str)
    # Baba'nın eklediği kolonlar: AH Temsilci, AJ Sektör, AI Açık/Kapalı
    df['_rep']=df['Temsilci'].where(df['Temsilci'].notna(),None) if 'Temsilci' in df.columns else None
    df['_sector']=df['Sektör'].where(df['Sektör'].notna(),None) if 'Sektör' in df.columns else None
    if 'Açık / Kapalı' in df.columns:
        df['_closed']=df['Açık / Kapalı'].astype(str).str.upper().str.strip().eq('KAPALI')
    else:
        df['_closed']=False
    return df

def cust_attrs(df):
    """Her müşteri (_cust) için temsilci, sektör ve kapalı-durumu."""
    g=df.groupby('_cust')
    rep=g['_rep'].first(); sec=g['_sector'].first(); closed=g['_closed'].mean()
    out={}
    for c in rep.index:
        out[str(c)]={'rep':('' if pd.isna(rep[c]) else str(rep[c])),
                     'sector':('' if pd.isna(sec[c]) else str(sec[c])),
                     'closed':bool(closed.get(c,0)>=0.5)}
    return out

def inv_closed(df):
    g=df.groupby('_inv')['_closed'].mean()
    return {str(c) for c,v in g.items() if v>=0.5}

def _cust_detail(sub):
    yr=sub.groupby(['_cust','_yr']).agg(spend=('Nettutar','sum'),orders=('_ord','nunique'))
    pr=sub.groupby(['_cust','StokIsmi']).agg(spend=('Nettutar','sum'),qty=('miktar','sum'))
    d={}
    for (c,y),r in yr.iterrows():
        if pd.isna(y): continue
        d.setdefault(c,{'years':[],'products':[]})['years'].append(
            {'y':int(y),'spend':_n(r['spend']),'orders':int(r['orders']),'avg':_n(r['spend']/r['orders']) if r['orders'] else 0})
    for c,g in pr.groupby(level=0):
        top=g.sort_values('spend',ascending=False).head(6)
        d.setdefault(c,{'years':[],'products':[]})['products']=[{'name':str(i[1]),'spend':_n(r['spend']),'qty':_n(r['qty'])} for i,r in top.iterrows()]
    for c in d: d[c]['years']=sorted(d[c]['years'],key=lambda x:x['y'])
    return d

def m_lapsed(df,owed,attr):
    piv=df.pivot_table(index='_cust',columns='_yr',values='Nettutar',aggfunc='sum',fill_value=0)
    for y in QUAL_YEARS+[LAPSED_YEAR]:
        if y not in piv: piv[y]=0
    names=df.groupby('_cust')['_cname'].first()
    nq=sum((piv[y]>YEAR_THR).astype(int) for y in QUAL_YEARS); tot=piv[QUAL_YEARS].sum(axis=1)
    base=(piv[LAPSED_YEAR]<=0)&(tot>=MIN_TOTAL)
    rows=[]
    for c in piv.index[base]:
        a=attr.get(str(c),{})
        if a.get('closed'): continue
        n=nq[c]; tier=1 if n>=3 else 2 if n==2 else 0
        if n==1:
            qs=[y for y in QUAL_YEARS if piv.loc[c,y]>YEAR_THR]; tier=3 if qs and qs[0] in T3_YEARS else 0
        if not tier: continue
        ya=[y for y in QUAL_YEARS if piv.loc[c,y]>0]
        rows.append({'tier':tier,'code':str(c),'name':str(names.get(c,'')),'rep':a.get('rep',''),'sector':a.get('sector',''),
                     'spend':_n(tot[c]),'last':int(max(ya)) if ya else None,'years':int(n),'overdue':_n(owed.get(str(c),0))})
    rows.sort(key=lambda r:(r['tier'],-r['spend']))
    det=_cust_detail(df[df['_cust'].isin({r['code'] for r in rows})])
    for r in rows: r['detail']=det.get(r['code'],{'years':[],'products':[]})
    return rows

def m_momentum(df,attr):
    asof=df['_date'].max()
    if pd.isna(asof): return []
    doy=asof.dayofyear
    def per(yr): return df[(df['_yr']==yr)&(df['_date'].dt.dayofyear<=doy)].groupby('_cust')['Nettutar'].sum()
    p25,p26=per(2025),per(2026); names=df.groupby('_cust')['_cname'].first()
    rows=[]
    for c in set(p25.index)|set(p26.index):
        a=attr.get(str(c),{})
        if a.get('closed'): continue
        x=float(p25.get(c,0)); b=float(p26.get(c,0))
        if x<MIN_TOTAL or b<=0: continue
        rows.append({'code':c,'name':str(names.get(c,'')),'rep':a.get('rep',''),'sector':a.get('sector',''),
                     'y2025':_n(x),'y2026':_n(b),'delta':_n(b-x),'pct':round((b-x)/x*100,1)})
    rows.sort(key=lambda r:r['pct'])
    det=_cust_detail(df[df['_cust'].isin({r['code'] for r in rows})])
    for r in rows: r['detail']=det.get(r['code'],{'years':[],'products':[]})
    return rows

def m_margin(df,attr):
    rec=df[df['_yr'].isin([2025,2026])]
    g=rec.groupby('_cust').agg(rev=('Nettutar','sum'),prof=('_prof','sum'))
    names=rec.groupby('_cust')['_cname'].first(); rows=[]
    for c,r in g.iterrows():
        a=attr.get(str(c),{})
        if a.get('closed') or r['rev']<50000: continue
        rows.append({'code':c,'name':str(names.get(c,'')),'rep':a.get('rep',''),'rev':_n(r['rev']),'prof':_n(r['prof']),
                     'marg':round(r['prof']/r['rev']*100,1) if r['rev'] else 0})
    rows.sort(key=lambda r:-r['rev']); return rows

def m_ordersize(df,attr):
    rec=df[df['_yr'].isin([2024,2025,2026])]
    g=rec.groupby('_cust').agg(rev=('Nettutar','sum'),orders=('_ord','nunique'))
    g2=df.groupby(['_cust','_yr']).agg(rev=('Nettutar','sum'),orders=('_ord','nunique'))
    g2['aov']=g2['rev']/g2['orders'].replace(0,np.nan); aov=g2['aov'].unstack()
    names=df.groupby('_cust')['_cname'].first(); rows=[]
    for c,r in g.iterrows():
        a=attr.get(str(c),{})
        if a.get('closed') or r['orders']<3 or r['rev']<30000: continue
        a25=aov.loc[c,2025] if (2025 in aov.columns and c in aov.index) else np.nan
        a26=aov.loc[c,2026] if (2026 in aov.columns and c in aov.index) else np.nan
        chg=round((a26-a25)/a25*100,1) if (a25==a25 and a26==a26 and a25>0) else None
        rows.append({'code':c,'name':str(names.get(c,'')),'rep':a.get('rep',''),'orders':int(r['orders']),'rev':_n(r['rev']),
                     'aov':_n(r['rev']/r['orders']) if r['orders'] else 0,'chg':chg})
    rows.sort(key=lambda r:-r['rev'])
    det=_cust_detail(df[df['_cust'].isin({r['code'] for r in rows})])
    for r in rows: r['detail']=det.get(r['code'],{'years':[],'products':[]})
    return rows

def m_overstock_move(df,over_rows):
    ov={r['code']:r for r in over_rows}
    sub=df[df['sto_kod'].astype(str).isin(set(ov))&df['_yr'].isin([2024,2025,2026])]
    g=sub.groupby([sub['sto_kod'].astype(str),'_cust']).agg(qty=('miktar','sum'),last=('_yr','max'),spend=('Nettutar','sum'))
    names=df.groupby('_cust')['_cname'].first(); buyers={}
    for (code,cust),r in g.iterrows():
        buyers.setdefault(code,[]).append({'name':str(names.get(cust,'')),'qty':_n(r['qty']),'last':int(r['last']),'spend':_n(r['spend'])})
    rows=[]
    for code,bl in buyers.items():
        o=ov[code]; bl=sorted(bl,key=lambda x:-x['qty'])
        rows.append({'code':code,'name':o['name'],'stock':o['stock'],'inv':o['inv'],'buyers':len(bl),'detail':{'buyers':bl[:12]}})
    rows.sort(key=lambda r:-r['inv']); return rows

def m_risk(df,owed_map,closed_inv,attr):
    rec=df[df['_yr'].isin([2025,2026])]; g=rec.groupby('_inv')['Nettutar'].sum()
    act26=set(df[df['_yr']==2026]['_inv']); names=df.groupby('_inv')['_invname'].first()
    reps=df.groupby('_inv')['_rep'].first(); rows=[]
    for code,ov in owed_map.items():
        if ov<=0: continue
        rev=float(g.get(code,0)); rp=reps.get(code)
        rows.append({'code':code,'name':str(names.get(code,'') or code),'rep':('' if pd.isna(rp) else str(rp)),
                     'overdue':_n(ov),'rev':_n(rev),'active':('Evet' if code in act26 else 'Hayır'),
                     'ratio':(int(round(ov/rev*100)) if rev>0 else None),'closed':bool(code in closed_inv)})
    rows.sort(key=lambda r:-r['overdue']); return rows


def m_collections(path,df=None):
    df=read_aging_raw(path) if df is None else df.copy()
    df.columns=[str(c).strip() for c in df.columns]
    key,name,rep='Hesap kodu','Hesap adı','Temsilci kodu'
    months=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık']
    bkts=[c for c in df.columns if 'öncesi' in c or any(m in c for m in months)]  # aging columns, oldest→newest
    # eski format desteği: tek 'Vadesi geçen bakiye' sütunu varsa onu kullan
    single=next((c for c in df.columns if 'vadesi geçen' in c.lower()),None)
    for b in bkts: df[b]=num(df[b]).fillna(0)
    df=df[df[key].astype(str).str.match(r'^\d',na=False)]
    df['_bal']=num(df[single]).fillna(0) if single else (df[bkts].sum(axis=1) if bkts else 0)
    owed=df[df['_bal']>0].copy()
    def oldest(r):
        for b in bkts:
            if r[b]>0: return b
        return '—'
    rows=[{'name':str(r[name]),'rep':('' if pd.isna(r[rep]) else str(r[rep])),
           'overdue':_n(r['_bal']),'oldest':oldest(r),'code':str(r[key])}
          for _,r in owed.sort_values('_bal',ascending=False).iterrows()]
    owed_map={str(r[key]):float(r['_bal']) for _,r in owed.iterrows()}
    return rows, owed_map

# ---------- oluştur ----------
def build(data_folder,out_path,today=None,ai_panel=True,sources=None,frames=None,stats_out=None,c360_panel=True):
    """sources/frames (M5-5): a frozen source selection and the already-read raw frames, so a controlled
    refresh builds from exactly what it validated. stats_out (dict): filled with build totals for verification."""
    today=today or dt.date.today()
    src=sources or select_sources(data_folder); fr=frames or {}
    sp,ep,ap=src.get('stock'),src.get('sales'),src.get('aging')
    low,over,total=m_stock(sp,fr.get('stock'))
    coll,owed=([],{})
    if ap: coll,owed=m_collections(ap,fr.get('aging'))
    sdf=load_sales(ep,fr.get('sales')) if ep else None
    attr=cust_attrs(sdf) if sdf is not None else {}
    cinv=inv_closed(sdf) if sdf is not None else set()
    lap=m_lapsed(sdf,owed,attr) if sdf is not None else []
    momentum=m_momentum(sdf,attr) if sdf is not None else []
    margin=m_margin(sdf,attr) if sdf is not None else []
    ordersize=m_ordersize(sdf,attr) if sdf is not None else []
    osmove=m_overstock_move(sdf,over) if sdf is not None else []
    risk=m_risk(sdf,owed,cinv,attr) if (sdf is not None and owed) else []
    tier_counts={t:sum(1 for r in lap if r['tier']==t) for t in (1,2,3)}
    over_val=sum(r['inv'] for r in over); overdue_total=sum(r['overdue'] for r in coll)
    lap_owe=sum(1 for r in lap if r['overdue']>0); has_aging=bool(ap)

    wb_cols=[{'k':'tier','l':'Kademe'},{'k':'name','l':'Müşteri','sub':'code'},{'k':'rep','l':'Temsilci'},
             {'k':'sector','l':'Sektör'},{'k':'spend','l':'Toplam Harcama','money':1},
             {'k':'last','l':'Son Yıl'},{'k':'years','l':'Güçlü Yıl','n':1}]
    if has_aging: wb_cols.append({'k':'overdue','l':'Vadesi Geçen','money':1})
    modules={
      'reorder':{'title':'Azalan Stok','rows':low,
        'cols':[{'k':'code','l':'Kod'},{'k':'name','l':'Ürün'},{'k':'stock','l':'Stok','n':1},
                {'k':'cov','l':'Kapsama','n':1},{'k':'min','l':'Min','n':1}],
        'note':'Kapsama minseviye altında, en acil önce. Negatif kapsama = fazla satılmış.'},
      'overstock':{'title':'Fazla Stok','rows':over,
        'cols':[{'k':'code','l':'Kod'},{'k':'name','l':'Ürün'},{'k':'stock','l':'Stok','n':1},
                {'k':'cov','l':'Kapsama','n':1},{'k':'inv','l':'Envanter Değeri','money':1}],
        'note':'Kapsama maxseviye üstünde, en çok bağlı sermaye önce.'},
      'osmove':{'title':'Stok Eritme','rows':osmove,'expand':True,
        'cols':[{'k':'code','l':'Kod'},{'k':'name','l':'Ürün'},{'k':'stock','l':'Stok','n':1},
                {'k':'inv','l':'Envanter Değeri','money':1},{'k':'buyers','l':'Alıcı Sayısı','n':1}],
        'note':'Fazla stoktaki ürünler + bunları daha önce alan müşteriler. Aramak için ürüne tıklayın — hazır çağrı listesi.'},
      'winback':{'title':'Geri Kazanım','rows':lap,'cols':wb_cols,'expand':True,'filter':'rep',
        'note':('Geçmişte çok alışveriş yaptı, 2026\'da hiç almadı. Sarı = hâlâ borcu var, kovalamadan önce tahsil edin. Detay için müşteriye tıklayın. Kapalı firmalar çıkarıldı.'
                if has_aging else 'Geçmişte çok alışveriş yaptı, 2026\'da hiç almadı. Önce Kademe 1, sonra harcamaya göre sıralı. Detay için müşteriye tıklayın. Kapalı firmalar çıkarıldı.')},
      'momentum':{'title':'Büyüme / Küçülme','rows':momentum,'expand':True,'filter':'rep',
        'cols':[{'k':'name','l':'Müşteri','sub':'code'},{'k':'rep','l':'Temsilci'},{'k':'sector','l':'Sektör'},
                {'k':'y2025','l':'2025 (Oca–bugün)','money':1},{'k':'y2026','l':'2026 (Oca–bugün)','money':1},
                {'k':'delta','l':'Fark','money':1,'sign':1},{'k':'pct','l':'Değişim %','n':1,'sign':1}],
        'note':'Hâlâ aktif ama aynı dönem 2025\'e göre harcaması değişen müşteriler. Küçülenler üstte. "2025/2026 (Oca–bugün)" = yıl başından bugüne aynı dönem. Detay için tıklayın. Kapalı firmalar çıkarıldı.'},
      'ordersize':{'title':'Sipariş Büyüklüğü','rows':ordersize,'expand':True,'filter':'rep',
        'cols':[{'k':'name','l':'Müşteri','sub':'code'},{'k':'rep','l':'Temsilci'},{'k':'orders','l':'Sipariş (24–26)','n':1},
                {'k':'rev','l':'Toplam Ciro (24–26)','money':1},{'k':'aov','l':'Ort. Sipariş Tutarı','money':1},
                {'k':'chg','l':'Ort. Tutar Değişim % (26 vs 25)','n':1,'sign':1}],
        'note':'"Toplam Ciro (24–26)" 3 yılın toplamıdır; "Ort. Sipariş Tutarı" = toplam ÷ sipariş sayısı (düşüş değil, ortalama). Yıllık kırılım için satıra tıklayın.'},
      'margin':{'title':'Kâr Marjı','rows':margin,'filter':'rep',
        'cols':[{'k':'name','l':'Müşteri','sub':'code'},{'k':'rep','l':'Temsilci'},{'k':'rev','l':'Ciro','money':1},
                {'k':'prof','l':'Kâr','money':1},{'k':'marg','l':'Marj %','n':1}],
        'note':'Marj % = Kâr ÷ Ciro. Ciro = Nettutar (U sütunu), Kâr = Net_Tutar_Maliyet_Dusulmus (AC sütunu), 2025–2026. Maliyet boş olan satırlar marjı bozabilir.'},
    }
    if risk:
        modules['risk']={'title':'Riskli Müşteri','rows':risk,'filter':'rep',
          'cols':[{'k':'name','l':'Müşteri','sub':'code'},{'k':'rep','l':'Temsilci'},{'k':'overdue','l':'Vadesi Geçen','money':1},
                  {'k':'rev','l':'Ciro 25–26','money':1},{'k':'active','l':'2026 Sipariş'},{'k':'ratio','l':'Borç/Ciro %','n':1}],
          'note':'Borcu olan müşteriler; ciroyla birlikte. "2026 Sipariş = Evet" ama borçlu = hâlâ alıp ödemiyor. Kapalı firmalar KAPALI etiketiyle gösterilir (borç tahsil edilmeli).'}
    if has_aging:
        modules['collections']={'title':'Tahsilat','rows':coll,
          'cols':[{'k':'name','l':'Müşteri'},{'k':'rep','l':'Temsilci'},{'k':'overdue','l':'Vadesi Geçen','money':1},
                  {'k':'oldest','l':'En Eski Borç'}],
          'note':'Vadesi geçen alacaklar, en büyük önce. Listenin başındakileri kovalayın.'}
    stats=[('min altı',f"{len(low):,}"),('maks üstü',f"{len(over):,}"),
           ('fazla stok değeri',_money(over_val)),('kayıp müşteri',f"{len(lap):,}")]
    if has_aging:
        stats+=[('vadesi geçen toplam',_money(overdue_total)),('borçlu kayıp müşteri',f"{lap_owe}")]
    later=(['Tahsilat & alacak yaşlandırma'] if not has_aging else [])+[
           'Hedef vs gerçekleşen (müşteri / temsilci / bölge)','Temsilci verimliliği (ziyaret / km başına satış)',
           'Gerçek stok hareketinden sipariş','Bütçe vs gerçekleşen — giderler',
           'Kredi / nakit akışı takvimi','Fiyat değişim takibi','Dijital pazarlama hunisi',
           'Çapraz satış (5 ürün → 5 firma)']
    d_tr=f"{today.day} {AYLAR[today.month]} {today.year}"
    meta={'date':d_tr,'stock':os.path.basename(sp) if sp else '','sales':os.path.basename(ep) if ep else '',
          'aging':os.path.basename(ap) if ap else '','tiers':tier_counts,'total':total,'has_aging':has_aging,
          'asof':_asof_iso(sdf)}
    with open(out_path,'w',encoding='utf-8') as f: f.write(_html(modules,stats,meta,later,ai_panel=ai_panel,c360_panel=c360_panel))
    if stats_out is not None:
        stats_out.update({'asof':meta['asof'],'overdue_total':overdue_total,'overstock_value':over_val,
                          'tabs':list(modules.keys()),'products':total,'lapsed':len(lap),'low':len(low),
                          'over':len(over),'coll':len(coll),'sources':{k:(os.path.basename(v) if v else None) for k,v in src.items()}})
    return out_path,{'low':len(low),'over':len(over),'lapsed':len(lap),'coll':len(coll),
                     'momentum':len(momentum),'margin':len(margin),'ordersize':len(ordersize),
                     'osmove':len(osmove),'risk':len(risk)}

# ---------------- M5-2: AI Aksiyon Merkezi tab (read-only) ----------------
# One tab button + one panel, added next to the existing dashboard without touching its tables.
# The panel is server-driven: it shows the deterministic action queue from the local FastAPI app
# (GET /api/agent/categories, GET /api/agent/actions) and asks for AI text only when the user clicks
# (POST /api/agent/actions/enrich, at most 5 ids, header X-StockAgent: 1). No business logic in JS:
# order, numbers, reasons and categories all come from Python. Model text is set via textContent only.
def _asof_iso(sdf):
    """Latest sales date (ISO), the same rule as services/context.py. Used only by the AI tab to warn
    when the dashboard file and the server were built from different data."""
    if sdf is None: return ''
    a=pd.to_datetime(sdf['_date'],errors='coerce').max()
    return a.date().isoformat() if pd.notna(a) else ''

_AI_CSS=r"""
#ai .ai-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 10px}
#ai .ai-chip{font-size:12px;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:3px 10px;color:var(--muted)}
#ai .ai-chip.on{color:#1a7f4b;border-color:#bfe3cf;background:#f0faf4}
#ai .ai-chip.down{color:#b42318;border-color:#f3c4c4;background:#fdf3f3}
#ai .ai-date{font-size:12px;color:var(--muted);margin-left:auto}
#ai .ai-btn{border:1px solid var(--lap);background:var(--lap);color:#fff;font:inherit;font-weight:600;font-size:13px;padding:7px 12px;border-radius:8px;cursor:pointer}
#ai .ai-btn:disabled{opacity:.55;cursor:wait}
#ai .ai-one{background:var(--card);color:var(--lap);padding:4px 10px;font-size:12px}
#ai .ai-refresh{background:var(--card);color:var(--ink);border-color:var(--line)}#ai .ai-refresh:hover{border-color:#c3c8cf}
#ai .ai-rstat{font-size:12.5px;margin:0 0 10px;padding:8px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card)}
#ai .ai-rstat.run{color:var(--muted)}#ai .ai-rstat.ok{color:#1a7f4b;border-color:#bfe3cf;background:#f0faf4}
#ai .ai-rstat.err{color:#b42318;border-color:#f3c4c4;background:#fdf3f3;font-weight:600}
#ai .ai-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:8px 0 12px}
#ai .ai-state.off,#ai .ai-state.err{border-color:#f3c4c4;background:#fdf3f3}
#ai .ai-state.wait,#ai .ai-state.empty{color:var(--muted)}
#ai .ai-status{font-size:12px;margin:0 0 10px;color:var(--muted)}
#ai .ai-status.err{color:#b42318;font-weight:600}#ai .ai-status.ok{color:#1a7f4b}
#ai .ai-warn{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:9px 12px;margin:0 0 10px;font-size:13px;font-weight:600}
#ai .ai-filters{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
#ai .ai-f{--c:var(--ink);border:1px solid var(--line);background:var(--card);color:var(--muted);font:inherit;font-size:12px;font-weight:600;padding:5px 11px;border-radius:999px;cursor:pointer}
#ai .ai-f:hover{color:var(--ink)}#ai .ai-f.on{background:var(--c);border-color:var(--c);color:#fff}
#ai .aic{background:var(--card);border:1px solid var(--line);border-left:4px solid #9aa3ad;border-radius:10px;padding:11px 14px;margin:0 0 10px}
#ai .aic.pri-critical{border-left-color:var(--crit)}#ai .aic.pri-high{border-left-color:var(--low)}#ai .aic.pri-medium{border-left-color:var(--gold)}
#ai .aic-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:11px}
#ai .aic-pri{font-weight:800;letter-spacing:.06em;color:#6b7480}
#ai .pri-critical .aic-pri{color:var(--crit)}#ai .pri-high .aic-pri{color:var(--low)}#ai .pri-medium .aic-pri{color:var(--gold)}
#ai .aic-score{font-family:"SF Mono",Menlo,Consolas,monospace;color:var(--muted)}
#ai .aic-cat{color:#fff;background:#6b7480;border-radius:4px;padding:1px 7px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;font-size:10px}
#ai .aic-conf{color:var(--muted);margin-left:auto}
#ai .aic-title{font-size:15px;font-weight:700;margin:6px 0 3px;letter-spacing:-.01em}
#ai .aic-reason{margin:0 0 6px}
#ai .aic-facts{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:3px 16px;font-size:12.5px;font-variant-numeric:tabular-nums}
#ai .aic-facts li::before{content:'•';color:var(--muted);margin-right:5px}
#ai .aic-ai{margin:9px 0 2px;border:1px dashed #c4b5fd;background:#faf7ff;border-radius:8px;padding:8px 11px}
#ai .aic-ai-h{font-size:11px;font-weight:700;color:var(--lap);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
#ai .aic-cached{font-weight:500;text-transform:none;letter-spacing:0;color:var(--muted);margin-left:8px}
#ai .aic-int,#ai .aic-rec{margin:3px 0}
#ai .aic-ev{display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin-top:5px;font-size:11px}
#ai .aic-ev-l{color:var(--muted);font-weight:700}
#ai .aic-chip{background:#fff;border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-variant-numeric:tabular-nums}
#ai .aic-err{font-size:12px;color:#b42318;margin:7px 0 0}
#ai [hidden]{display:none!important}
#ai .aic-acts{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:9px}
#ai .aic-acts .ai-one{margin-left:auto}
#ai .ai-sbtn{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:12px;font-weight:600;padding:4px 10px;border-radius:8px;cursor:pointer}
#ai .ai-sbtn:hover{border-color:#c3c8cf}#ai .ai-sbtn:disabled{opacity:.5;cursor:wait}
#ai .aic-done{color:#1a7f4b}
#ai .aic-deferf{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:7px;font-size:12px;color:var(--muted)}
#ai .aic-deferf input{border:1px solid var(--line);border-radius:6px;padding:3px 6px;font:inherit;color:var(--ink)}
#ai .aic-serr{font-size:12px;color:#b42318;font-weight:600;margin:7px 0 0}
#ai .ai-undo{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:20;display:flex;gap:14px;align-items:center;
background:var(--ink);color:#fff;border-radius:10px;padding:9px 10px 9px 16px;font-size:13px;box-shadow:0 6px 24px rgba(0,0,0,.18);max-width:calc(100% - 32px)}
#ai .ai-undo-b{background:transparent;border:1px solid rgba(255,255,255,.55);color:#fff;font:inherit;font-weight:700;padding:4px 11px;border-radius:7px;cursor:pointer}
#ai .ai-undo-t.err{color:#fecaca}
#ai .ai-more{text-align:center;margin:4px 0 8px}
#ai .aic-draftp{margin-top:9px;border:1px solid var(--line);border-radius:8px;padding:9px 11px;background:#fbfbfc}
#ai .aic-drow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
#ai .aic-drow label{display:flex;gap:5px;align-items:center}
#ai .aic-drow select,#ai .aic-drow input{border:1px solid var(--line);border-radius:6px;padding:4px 6px;font:inherit;font-size:12px;color:var(--ink);background:var(--card)}
#ai .aic-drow .ai-din{flex:1;min-width:160px}
#ai .aic-drow .ai-btn{padding:5px 11px;font-size:12px}
#ai .aic-dstat,#ai .aic-dnote{font-size:12px;color:var(--muted);margin:7px 0 0}
#ai .aic-dres{margin-top:9px}
#ai .aic-dhead{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
#ai .ai-dmsg{width:100%;min-height:96px;border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit;font-size:13.5px;line-height:1.5;color:var(--ink);background:#fff;resize:vertical}
#ai .aic-dfoot{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:6px;font-size:12px;color:var(--muted)}
#ai .ai-dcopied{color:#1a7f4b;font-weight:600}#ai .ai-dcopied.fail{color:#b42318}
#ai .ai-drawer{position:fixed;top:0;right:0;bottom:0;width:380px;max-width:100%;z-index:30;background:var(--card);border-left:1px solid var(--line);box-shadow:-8px 0 30px rgba(0,0,0,.12);display:flex;flex-direction:column}
#ai .ai-dr-h{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;padding:16px 18px 12px;border-bottom:1px solid var(--line)}
#ai .ai-dr-name{font-size:16px;font-weight:800;letter-spacing:-.01em}
#ai .ai-dr-sub{font-size:12px;color:var(--muted);font-family:"SF Mono",Menlo,Consolas,monospace;margin-top:2px}
#ai .ai-dr-tag{font-size:10px;font-weight:700;letter-spacing:.04em;color:#fff;background:var(--crit);border-radius:4px;padding:1px 6px;margin-left:6px;vertical-align:2px}
#ai .ai-dr-x{border:1px solid var(--line);background:var(--card);border-radius:8px;font:inherit;padding:3px 9px;cursor:pointer}
#ai .ai-dr-body{padding:10px 18px;overflow:auto;flex:1}
#ai .ai-dr-row{display:flex;justify-content:space-between;gap:14px;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px}
#ai .ai-dr-row .l{color:var(--muted)}#ai .ai-dr-row .v{text-align:right;font-variant-numeric:tabular-nums}
#ai .ai-dr-msg{color:var(--muted);font-size:13px}#ai .ai-dr-msg.err{color:#b42318;font-weight:600}
#ai .ai-dr-note{font-size:11px;color:var(--muted);padding:10px 18px;margin:0;border-top:1px solid var(--line)}
#ai .ai-none{color:var(--muted);font-style:italic}
/* M5-6 polish: long unbroken text never widens the page; comfortable tap targets and a full-width
   undo bar / drawer on phones */
#ai .aic,#ai .ai-state,#ai .ai-status,#ai .ai-rstat,#ai .ai-warn,#ai .ai-dr-body,#ai .ai-dr-name,#ai .ai-undo-t{overflow-wrap:anywhere}
#ai .aic-chip{max-width:100%}
#ai .ai-undo-b{white-space:nowrap;flex-shrink:0}
@media(max-width:600px){#ai .ai-date{margin-left:0;width:100%}#ai .aic-conf{margin-left:0}
#ai .ai-btn,#ai .ai-sbtn,#ai .ai-f,#ai .ai-dr-x{min-height:36px}
#ai .aic-acts .ai-one{margin-left:0}
#ai .aic-drow .ai-din{flex-basis:100%;min-width:0}
#ai .ai-undo{left:12px;right:12px;transform:none;max-width:none;justify-content:space-between}
#ai .ai-drawer{width:100%;border-left:0}}
"""

_AI_BODY="""
<h2 class="sec">✨ AI Aksiyon Merkezi</h2>
<p class="note">Bugün ne yapmalı, neden ve hangi rakamlara dayanıyor. Sıralama, sayılar ve gerekçeler Python'dandır; AI yorumu yalnızca siz istediğinizde oluşturulur ve öneridir.</p>
<div class="ai-bar"><span class="ai-chip ai-srv" hidden></span><span class="ai-chip ai-aist" hidden></span>
<button class="ai-btn ai-gen" type="button" hidden>✨ AI yorumlarını oluştur</button>
<button class="ai-btn ai-refresh" type="button" hidden title="data klasöründeki güncel dosyalardan doğrula → oluştur → kontrol et → değiştir">↻ Verileri yenile</button><span class="ai-date"></span></div>
<div class="ai-warn" hidden></div>
<div class="ai-rstat" role="status" aria-live="polite" hidden></div>
<div class="ai-state" role="status" hidden></div>
<div class="ai-status" role="status" aria-live="polite" hidden></div>
<div class="ai-filters" hidden></div>
<div class="ai-list"></div>
<div class="ai-more"><button class="ai-btn ai-next" type="button" hidden>Sonraki 5 için AI yorumu oluştur</button></div>
<div class="ai-undo" role="status" aria-live="polite" hidden></div>
<aside class="ai-drawer" role="dialog" aria-label="Müşteri bilgileri" hidden><div class="ai-dr-h"><div><div class="ai-dr-name"></div><div class="ai-dr-sub"></div></div>
<button class="ai-dr-x" type="button" aria-label="Kapat">✕</button></div><div class="ai-dr-body"></div>
<p class="ai-dr-note">Python verileri ve kayıtlı notlar; AI yorumu yok.</p></aside>
<p class="note">Sıralama ve sayılar Python'dandır; AI metinleri öneridir ve rakam üretmez.</p>
"""

_AI_JS=r"""
(function(){
'use strict';
const root=document.getElementById('ai'), tabBtn=document.querySelector('.tab[data-t="ai"]');
if(!root||!tabBtn) return;
const ASOF=root.getAttribute('data-asof')||'';
const MAX_IDS=5;                                   // server contract: at most 5 ids and one provider call per request
const POLL_MS=window.__AI_POLL_MS||3000, POLL_MAX_MS=120000;
const POST_HEADERS={'Content-Type':'application/json','X-StockAgent':'1'};   // local CSRF guard (D-020)
const UNDO_MS=window.__AI_UNDO_MS||5000, DEFER_DAYS=7;   // design §7: 5-second "Geri al", "Ertele" defaults to +7 days
const MSG={
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  warming:'Veriler hazırlanıyor…',
  ctxError:'Veriler yüklenemedi: ',
  loadFail:'Aksiyonlar yüklenemedi',
  disabled:'AI kapalı (.env: AI_PROVIDER=openai)',
  running:'Oluşturuluyor… (≈10–20 sn)',
  empty:'Bugün için açık aksiyon yok.',
  noneInFilter:'Bu kategoride açık aksiyon yok.',
  mismatch:'Pano ve sunucu farklı veri tarihleri kullanıyor; veriyi yenileyin.',
  other:'AI yorumu üretilemedi',
  stateFail:'Aksiyon durumu kaydedilemedi.',
  badDate:'Geçerli bir ileri tarih seçin.',
  complete:'Aksiyon tamamlandı.',
  dismiss:'Aksiyon gizlendi.',
  drafting:'Taslak hazırlanıyor…',
  draftFail:'Taslak oluşturulamadı.',
  leak:'Taslak iç bilgi içerdiği için gösterilmiyor.',
  copied:'Kopyalandı',
  copyFail:'Kopyalanamadı; metni seçip elle kopyalayın.',
  draftNote:'Yalnızca taslak: inceleyin, kopyalayın ve kendiniz gönderin.',
  custLoading:'Yükleniyor…',
  custFail:'Müşteri bilgileri yüklenemedi.',
  custEmpty:'Gösterilecek bilgi yok.',
  refreshing:'Veriler yenileniyor…',
  refreshKept:'Önceki veriler kullanılmaya devam ediyor.',
  refreshNoServer:'Sunucuya ulaşılamadı'
};
// Controlled refresh (M5-5, D-019): after success the page reloads to get the new static dashboard.
const RELOAD_MS=window.__AI_RELOAD_MS||1500, RELOAD_WARN_MS=window.__AI_RELOAD_WARN_MS||6000;
// Draft contract (D-030): mirrors schemas.Channel / schemas.Purpose. Not categories: those come from the server.
const CHANNELS=[['whatsapp','WhatsApp'],['email','E-posta']];
const PURPOSES=[['collection','Tahsilat'],['offer','Teklif'],['winback','Geri kazanım'],['sales_followup','Satış takibi'],['fair_invite','Fuar daveti']];
const DEFAULT_CHANNEL='whatsapp', FALLBACK_PURPOSE='sales_followup';   // D-018: WhatsApp first
const GUARD='AI metni güvenlik kontrolünden geçmedi, gösterilmiyor', NET="OpenAI'ye ulaşılamadı";
const ERR={quota:'OpenAI kredisi yetersiz',rate_limit:'Çok fazla istek, biraz sonra deneyin',timeout:NET,connection:NET,
  auth:'API anahtarı geçersiz',numeric_guard:GUARD,fact_reference:GUARD};
const PRI={critical:'KRİTİK',high:'YÜKSEK',medium:'ORTA',low:'DÜŞÜK'};
const CONF={high:'yüksek',medium:'orta',low:'düşük'};
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const st={started:false,aiOn:false,model:null,cats:[],catByKey:{},order:[],byId:{},filter:'',busy:false,
          tried:new Set(),errors:{},cardEl:{},
          gone:new Set(),mut:new Set(),stateErr:{},undo:null,
          draft:{},cust:new Map(),custPending:new Map(),drawer:null};   // cust: drawer data, this page session only (never stored)   // gone: left the open queue after a server-confirmed change

const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls;
  if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function line(sel,text,kind){const n=$(sel); n.textContent=text||''; n.className=sel.slice(1)+(kind?' '+kind:''); show(n,!!text);}
const setState=(t,k)=>line('.ai-state',t,k), setStatus=(t,k)=>line('.ai-status',t,k);
function safeColor(c){return /^#[0-9a-fA-F]{3,8}$/.test(String(c||''))?c:null;}
function fmtDate(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'';}
function fmtScore(s){const n=Number(s); return Number.isFinite(n)?n.toLocaleString('tr-TR',{maximumFractionDigits:1}):'—';}
function hasAI(a){return !!(a&&(a.interpretation||a.recommendation));}
async function getJSON(url){const r=await fetch(url,{headers:{'Accept':'application/json'}});
  if(!r.ok){const e=new Error('HTTP '+r.status); e.status=r.status; throw e;} return r.json();}

function srvChip(ok){const c=$('.ai-srv'); c.textContent=ok?'● Sunucu bağlı':'○ Sunucu yok'; c.className='ai-chip ai-srv '+(ok?'on':'down'); show(c,true);}
function aiChip(){const c=$('.ai-aist'); show(c,true);
  if(st.aiOn){c.textContent='AI: açık'+(st.model?' · '+st.model:''); c.className='ai-chip ai-aist on';}
  else{c.textContent=MSG.disabled; c.className='ai-chip ai-aist';}}
function offline(){srvChip(false); setState(MSG.offline,'off'); show($('.ai-refresh'),false);}

function start(){
  if(st.started) return; st.started=true;
  if(location.protocol==='file:'){offline(); return;}   // file:// has origin null; the API is same-origin only
  pollHealth(0);
}
async function pollHealth(elapsed){
  let h;
  try{h=await getJSON('/api/health');}catch(e){offline(); return;}
  srvChip(true); show($('.ai-refresh'),true);   // refresh needs the server (never offered offline or from file://)
  if(!h.context_ready){
    if(h.context_error){setState(MSG.ctxError+h.context_error,'err'); return;}
    setState(MSG.warming,'wait');
    // Poll health only (never AI). After ~2 min, try loading once; the server finishes the build then.
    if(elapsed<POLL_MAX_MS){setTimeout(()=>pollHealth(elapsed+POLL_MS),POLL_MS); return;}
  }
  loadAll(h);
}
function checkDate(srv){
  $('.ai-date').textContent=(srv||ASOF)?'Veri: '+fmtDate(srv||ASOF):'';
  const w=$('.ai-warn');
  if(ASOF&&srv&&ASOF!==srv){w.textContent=MSG.mismatch+' (pano: '+fmtDate(ASOF)+' · sunucu: '+fmtDate(srv)+')'; show(w,true);}
  else show(w,false);
}
async function loadAll(h){
  st.aiOn=!!h.ai_available; st.model=h.ai_model||null; checkDate(h.as_of_data_date);
  let cats,res;
  try{[cats,res]=await Promise.all([getJSON('/api/agent/categories'),getJSON('/api/agent/actions')]);}   // provider-free GETs
  catch(e){if(!e.status){offline(); return;} setState(MSG.loadFail+' (HTTP '+e.status+').','err'); return;}
  setState('');
  st.aiOn=!!res.ai_available;
  st.cats=Array.isArray(cats)?cats:[]; st.catByKey={}; st.cats.forEach(c=>{st.catByKey[c.key]=c;});
  const acts=Array.isArray(res.actions)?res.actions:[];
  st.order=acts.map(a=>a.action_id); st.byId={}; acts.forEach(a=>{st.byId[a.action_id]=a;});   // Python order, never re-sorted
  aiChip(); renderFilters(); renderList();
}

function openIds(){return st.order.filter(id=>!st.gone.has(id));}   // Python order, minus server-confirmed state changes
function visible(){return openIds().filter(id=>!st.filter||st.byId[id].category===st.filter);}
function renderFilters(){
  const box=$('.ai-filters'); box.replaceChildren();
  const open=openIds(), counts={}; open.forEach(id=>{const k=st.byId[id].category; counts[k]=(counts[k]||0)+1;});
  const chip=(key,label,color,n)=>{const b=el('button','ai-f'+(st.filter===key?' on':''),label+' ('+n+')');
    b.type='button'; b.dataset.k=key; if(color) b.style.setProperty('--c',color);
    b.addEventListener('click',()=>{st.filter=key; renderFilters(); renderList();}); return b;};
  box.append(chip('','Tümü',null,open.length));
  st.cats.forEach(c=>box.append(chip(c.key,c.label_tr,safeColor(c.color),counts[c.key]||0)));   // server-described (D-022)
  Object.keys(counts).filter(k=>!st.catByKey[k]).forEach(k=>box.append(chip(k,k,null,counts[k])));
  show(box,open.length>0);
}
function renderList(){
  const list=$('.ai-list'); list.replaceChildren(); st.cardEl={};
  if(!openIds().length){setState(MSG.empty,'empty'); updateButtons(); return;}
  if($('.ai-state').classList.contains('empty')) setState('');
  const ids=visible();
  if(!ids.length) list.append(el('p','ai-none',MSG.noneInFilter));
  ids.forEach(id=>{const c=card(st.byId[id]); st.cardEl[id]=c; list.append(c);});
  updateButtons();
}
function card(a){
  const pri=PRI[a.priority]?a.priority:'low';
  const c=el('article','aic pri-'+pri); c.dataset.id=a.action_id;
  const top=el('div','aic-top');
  top.append(el('span','aic-pri',PRI[a.priority]||a.priority||''), el('span','aic-score','skor '+fmtScore(a.score)));
  const cat=st.catByKey[a.category], badge=el('span','aic-cat',cat?cat.label_tr:a.category), col=cat&&safeColor(cat.color);
  if(col) badge.style.background=col;
  top.append(badge);
  if(a.confidence) top.append(el('span','aic-conf','güven: '+(CONF[a.confidence]||a.confidence)));
  c.append(top, el('h3','aic-title',a.title));
  if(a.reason){const r=el('p','aic-reason'); r.append(el('b',null,'Neden: '),document.createTextNode(a.reason)); c.append(r);}
  if(Array.isArray(a.facts)&&a.facts.length){const f=el('ul','aic-facts'); f.setAttribute('aria-label','Python rakamları');
    a.facts.forEach(x=>f.append(el('li',null,x))); c.append(f);}
  if(hasAI(a)) c.append(aiBlock(a));
  else if(st.errors[a.action_id]) c.append(el('p','aic-err',st.errors[a.action_id]));
  const id=a.action_id, row=el('div','aic-acts');
  const sb=(cls,label,fn)=>{const b=el('button','ai-sbtn '+cls,label); b.type='button'; b.disabled=st.mut.has(id);
    b.addEventListener('click',fn); return b;};
  row.append(sb('aic-done','✓ Yapıldı',()=>mutate(id,'complete')), sb('aic-defer','⏸ Ertele',()=>toggleDefer(c,id)),
             sb('aic-hide','✕ Gizle',()=>mutate(id,'dismiss')));
  if(a.entity_type==='customer'){   // drafts and the drawer need a deterministic customer target (never a product)
    const nb=(cls,label,fn)=>{const b=el('button','ai-sbtn '+cls,label); b.type='button'; b.addEventListener('click',fn); return b;};
    row.append(nb('aic-draft','✉ Taslak',()=>toggleDraft(id)), nb('aic-cust','🔍 Müşteri',()=>openCustomer(String(a.entity_id))));
  }
  if(st.aiOn&&!hasAI(a)){const b=el('button','ai-btn ai-one','✨ Bu aksiyon için AI yorumu');
    b.type='button'; b.disabled=st.busy; b.addEventListener('click',()=>enrich([id])); row.append(b);}
  c.append(row);
  if(st.draft[id]&&st.draft[id].open) c.append(draftPanel(a));
  if(st.stateErr[id]) c.append(el('p','aic-serr',st.stateErr[id]));
  return c;
}
function aiBlock(a){   // advisory area, visually separate from the Python facts above
  const b=el('div','aic-ai'), h=el('div','aic-ai-h','✨ AI yorumu (öneridir)');
  if(a.ai_cached) h.append(el('span','aic-cached','önbellekten'));
  b.append(h);
  if(a.interpretation) b.append(el('p','aic-int',a.interpretation));
  if(a.recommendation){const p=el('p','aic-rec'); p.append(el('b',null,'Öneri: '),document.createTextNode(a.recommendation)); b.append(p);}
  if(Array.isArray(a.evidence)&&a.evidence.length){const ev=el('div','aic-ev'); ev.append(el('span','aic-ev-l','Dayanak:'));
    a.evidence.forEach(e=>ev.append(el('span','aic-chip',(e.label||e.fact_id)+': '+(e.display_value==null?'—':e.display_value))));
    b.append(ev);}
  return b;
}
function pending(){return visible().filter(id=>!hasAI(st.byId[id])&&!st.tried.has(id));}
function updateButtons(){
  const left=pending().length, started=st.order.some(id=>hasAI(st.byId[id])||st.tried.has(id));
  const gen=$('.ai-gen'), nx=$('.ai-next');
  show(gen,st.aiOn&&left>0); show(nx,st.aiOn&&left>0&&started);
  gen.disabled=nx.disabled=st.busy;
  root.querySelectorAll('.ai-one').forEach(b=>{b.disabled=st.busy;});
}
function replaceCard(id){const old=st.cardEl[id]; if(!old||!old.parentNode) return;
  const n=card(st.byId[id]); old.replaceWith(n); st.cardEl[id]=n;}

// ---- controlled refresh (M5-5): one guarded POST; the server validates, builds, verifies, swaps ----
function reloadPage(){(window.__aiReload||(()=>window.location.reload()))();}
async function refreshData(){
  const b=$('.ai-refresh'); if(b.disabled) return;
  b.disabled=true; line('.ai-rstat',MSG.refreshing,'run');   // the current dashboard stays as it is meanwhile
  let rep=null;
  try{const r=await fetch('/api/context/refresh',{method:'POST',headers:POST_HEADERS,body:'{}'});
      try{rep=await r.json();}catch(e){rep=null;}}
  catch(e){rep=null;}
  if(rep&&rep.ok===true){
    const w=Array.isArray(rep.warnings)?rep.warnings:[];
    line('.ai-rstat','Veriler yenilendi. Yeni veri tarihi: '+(fmtDate(rep.as_of_data_date)||'—')+'.'
         +(w.length?' Uyarılar: '+w.join(' · '):'')+' Sayfa yeniden yükleniyor…','ok');
    setTimeout(reloadPage,w.length?RELOAD_WARN_MS:RELOAD_MS);   // the button stays disabled until the new page loads
    return;
  }
  const step=(rep&&(rep.step_tr||rep.step))||'bağlantı';
  const msg=String((rep&&rep.message)||MSG.refreshNoServer).replace(/[.\s]+$/,'');
  line('.ai-rstat','Yenileme başarısız ('+step+'): '+msg+'. '+MSG.refreshKept,'err');
  b.disabled=false;
}

// ---- drafts (M5-4, D-018/D-030): channel × purpose, explicit click only, copy-only (no send anywhere) ----
function defaultPurpose(a){const c=st.catByKey[a.category], p=c&&c.draft_purpose;
  return PURPOSES.some(x=>x[0]===p)?p:FALLBACK_PURPOSE;}   // server-described per category; never chosen by AI
function toggleDraft(id){
  const d=st.draft[id];
  if(!d) st.draft[id]={open:true,channel:DEFAULT_CHANNEL,purpose:defaultPurpose(st.byId[id]),instr:'',busy:false,res:null,err:null,text:'',copy:null};
  else d.open=!d.open;
  refreshDraft(id);
}
function refreshDraft(id){
  const c=st.cardEl[id]; if(!c) return;
  const old=c.querySelector('.aic-draftp'), d=st.draft[id];
  if(!d||!d.open){if(old) old.remove(); return;}
  const n=draftPanel(st.byId[id]);
  if(old) old.replaceWith(n); else c.querySelector('.aic-acts').after(n);
}
function selectOf(cls,label,opts,val,onChange){
  const w=el('label'), s=el('select',cls);
  opts.forEach(([k,l])=>{const o=el('option',null,l); o.value=k; s.append(o);});
  s.value=val; s.addEventListener('change',()=>onChange(s.value)); w.append(document.createTextNode(label+' '),s); return w;
}
function draftPanel(a){
  const id=a.action_id, d=st.draft[id], box=el('div','aic-draftp'), row=el('div','aic-drow');
  const ins=el('input','ai-din'); ins.type='text'; ins.maxLength=300; ins.placeholder='Ek not (isteğe bağlı, ör. ton)'; ins.value=d.instr;
  ins.setAttribute('aria-label','Ek not'); ins.addEventListener('input',()=>{d.instr=ins.value;});
  const go=el('button','ai-btn ai-dgo','Taslak oluştur'); go.type='button'; go.disabled=d.busy||!st.aiOn;
  go.addEventListener('click',()=>makeDraft(id));
  const x=el('button','ai-sbtn ai-dx','Kapat'); x.type='button'; x.addEventListener('click',()=>{d.open=false; refreshDraft(id);});
  row.append(selectOf('ai-dch','Kanal',CHANNELS,d.channel,v=>{d.channel=v;}),
             selectOf('ai-dpu','Amaç',PURPOSES,d.purpose,v=>{d.purpose=v;}), ins, go, x);
  box.append(row);
  if(!st.aiOn) box.append(el('p','aic-dnote',MSG.disabled));
  if(d.busy) box.append(el('p','aic-dstat',MSG.drafting));
  if(d.err) box.append(el('p','aic-serr ai-derr',d.err));
  if(d.res){
    const r=el('div','aic-dres'), lab=(list,k)=>(list.find(x=>x[0]===k)||[k,k])[1];
    r.append(el('div','aic-dhead',lab(CHANNELS,d.res.channel)+' · '+lab(PURPOSES,d.res.purpose)));
    const ta=el('textarea','ai-dmsg'); ta.value=d.text; ta.setAttribute('aria-label','Taslak metni');   // plain text, never HTML
    ta.addEventListener('input',()=>{d.text=ta.value;});
    r.append(ta);
    if(Array.isArray(d.res.evidence)&&d.res.evidence.length){const ev=el('div','aic-ev'); ev.append(el('span','aic-ev-l','Dayanak:'));
      d.res.evidence.forEach(e=>ev.append(el('span','aic-chip',(e.label||e.fact_id)+': '+(e.display_value==null?'—':e.display_value))));
      r.append(ev);}
    (d.res.warnings||[]).forEach(w=>r.append(el('p','aic-dnote',w)));
    const f=el('div','aic-dfoot'), cp=el('button','ai-sbtn ai-dcopy','Kopyala'); cp.type='button';
    cp.addEventListener('click',()=>copyDraft(id));
    f.append(cp);
    if(d.copy) f.append(el('span','ai-dcopied'+(d.copy==='fail'?' fail':''),d.copy==='ok'?MSG.copied:MSG.copyFail));
    f.append(el('span',null,MSG.draftNote));
    r.append(f); box.append(r);
  }
  return box;
}
async function makeDraft(id){
  const d=st.draft[id], a=st.byId[id]; if(!d||d.busy||!st.aiOn) return;
  d.busy=true; d.err=null; d.res=null; d.copy=null; refreshDraft(id);
  const body={customer_code:String(a.entity_id),channel:d.channel,purpose:d.purpose};
  if(d.instr.trim()) body.additional_instruction=d.instr.trim().slice(0,300);
  const res=await post('/api/agent/draft-message',body);
  d.busy=false;
  if(!res) d.err=MSG.draftFail;
  else if(!res.available){
    const cat=res.ai_error_category;
    d.err=cat==='kapali_customer'?(res.reason||MSG.draftFail)
         :(cat==='disabled'||cat==='missing_key')?MSG.disabled
         :cat==='internal_leak'?MSG.leak:(ERR[cat]||MSG.draftFail);
  } else {d.res=res; d.text=String(res.message||'');}
  refreshDraft(id);
}
async function copyDraft(id){
  const d=st.draft[id]; let ok=false;
  try{if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(d.text); ok=true;}}catch(e){ok=false;}
  d.copy=ok?'ok':'fail'; refreshDraft(id);
  if(!ok){const ta=st.cardEl[id]&&st.cardEl[id].querySelector('.ai-dmsg'); if(ta){ta.focus(); ta.select();}}   // ready for manual copy
}

// ---- customer drawer (M5-4): deterministic GET only, lazy, cached in memory for this page session ----
function drawerHead(name,sub,closed){
  const n=$('.ai-dr-name'); n.textContent=name||''; if(closed) n.append(el('span','ai-dr-tag','KAPALI'));
  $('.ai-dr-sub').textContent=sub||'';
}
function drawerMsg(code,text,kind){drawerHead(code,'',false); $('.ai-dr-body').replaceChildren(el('p','ai-dr-msg'+(kind?' '+kind:''),text));}
function renderDrawer(code,data){
  const f=data.facts||{};
  drawerHead(f.name||code,code+(f.rep?' · '+f.rep:''),f.closed===true);
  const body=$('.ai-dr-body'); body.replaceChildren();
  const rows=Array.isArray(data.display_facts)?data.display_facts:[];
  if(!rows.length) body.append(el('p','ai-dr-msg',MSG.custEmpty));
  rows.forEach(r=>{const d=el('div','ai-dr-row'); d.append(el('span','l',r.label),el('span','v',r.display_value)); body.append(d);});
}
async function openCustomer(code){
  if(!st.drawer) st.drawerOpener=document.activeElement;   // keyboard focus returns here on close
  st.drawer=code; show($('.ai-drawer'),true); $('.ai-dr-x').focus();
  if(st.cust.has(code)){renderDrawer(code,st.cust.get(code)); return;}   // same customer again: no request
  drawerMsg(code,MSG.custLoading);
  let p=st.custPending.get(code);
  if(!p){p=getJSON('/api/agent/customer/'+encodeURIComponent(code)).catch(()=>null); st.custPending.set(code,p);}
  const data=await p; st.custPending.delete(code);
  if(data) st.cust.set(code,data);     // failures are not cached, so a later click retries
  if(st.drawer!==code) return;         // another customer was opened meanwhile
  if(data) renderDrawer(code,data); else drawerMsg(code,MSG.custFail,'err');
}
function closeDrawer(){st.drawer=null; show($('.ai-drawer'),false);
  const o=st.drawerOpener; st.drawerOpener=null; if(o&&o.isConnected&&o.focus) o.focus();}

// ---- action state (M5-3): done / defer / dismiss, server-confirmed, with a 5-second undo ----
function isoLocal(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function inDays(n){const d=new Date(); d.setDate(d.getDate()+n); return isoLocal(d);}
function toggleDefer(c,id){
  const open=c.querySelector('.aic-deferf'); if(open){open.remove(); return;}
  const f=el('div','aic-deferf'), inp=el('input');
  inp.type='date'; inp.min=inDays(1); inp.value=inDays(DEFER_DAYS); inp.setAttribute('aria-label','Erteleme tarihi');
  const ok=el('button','ai-sbtn aic-defer-ok','Ertele'), no=el('button','ai-sbtn aic-defer-no','Vazgeç'), msg=el('span','aic-serr');
  ok.type=no.type='button'; show(msg,false);
  ok.addEventListener('click',()=>{const v=inp.value;
    if(!/^\d{4}-\d{2}-\d{2}$/.test(v)||v<inp.min){msg.textContent=MSG.badDate; show(msg,true); return;}
    mutate(id,'defer',v);});
  no.addEventListener('click',()=>f.remove());
  f.append(el('span',null,'Ne zamana?'),inp,ok,no,msg);
  c.querySelector('.aic-acts').after(f);
}
function cardBusy(id,on){const c=st.cardEl[id]; if(c) c.querySelectorAll('.ai-sbtn').forEach(b=>{b.disabled=on;});}
async function post(path,body){
  try{const r=await fetch(path,{method:'POST',headers:POST_HEADERS,body:JSON.stringify(body||{})}); return r.ok?await r.json():null;}
  catch(e){return null;}
}
async function mutate(id,kind,until){
  if(st.mut.has(id)||st.gone.has(id)) return;
  st.mut.add(id); delete st.stateErr[id]; cardBusy(id,true);
  const res=await post('/api/actions/'+encodeURIComponent(id)+'/'+kind,kind==='defer'?{until:until}:{});
  st.mut.delete(id);
  if(!res){st.stateErr[id]=MSG.stateFail; replaceCard(id); return;}   // not confirmed: the card stays, with the error
  st.gone.add(id);
  const n=st.cardEl[id]; if(n) n.remove(); delete st.cardEl[id];
  renderFilters();
  if(!visible().length) renderList(); else updateButtons();
  offerUndo(id,kind==='defer'?'Aksiyon '+fmtDate(res.defer_until||until)+' tarihine ertelendi.':MSG[kind]);
}
function clearUndo(){if(st.undo) clearTimeout(st.undo.timer); st.undo=null; const t=$('.ai-undo'); t.replaceChildren(); show(t,false);}
function offerUndo(id,text){
  clearUndo();   // one undo at a time; an earlier change simply stays saved
  const t=$('.ai-undo'), b=el('button','ai-undo-b','Geri al');
  b.type='button'; b.addEventListener('click',()=>undo(id));
  t.append(el('span','ai-undo-t',text),b); show(t,true);
  st.undo={id:id,timer:setTimeout(clearUndo,UNDO_MS)};   // when it expires nothing happens: the saved state stays
}
async function undo(id){
  const t=$('.ai-undo'), b=t.querySelector('.ai-undo-b'); if(b) b.disabled=true;
  if(st.undo) clearTimeout(st.undo.timer);
  const res=await post('/api/actions/'+encodeURIComponent(id)+'/reopen',{});
  if(!res){t.replaceChildren(el('span','ai-undo-t err',MSG.stateFail)); st.undo={id:null,timer:setTimeout(clearUndo,UNDO_MS)}; return;}
  st.gone.delete(id); clearUndo(); renderFilters(); renderList();   // back in its Python position, with any AI text it had
}

// Top 5 / next 5: the next actions without AI text, in Python order within the current filter.
function enrichNext(){const ids=pending().slice(0,MAX_IDS); if(ids.length) enrich(ids);}
async function enrich(ids){
  if(st.busy||!st.aiOn||!ids.length) return;
  ids=ids.slice(0,MAX_IDS);
  st.busy=true; setStatus(MSG.running,'run'); updateButtons();
  let data=null;
  try{const r=await fetch('/api/agent/actions/enrich',{method:'POST',headers:POST_HEADERS,body:JSON.stringify({action_ids:ids})});
      if(r.ok) data=await r.json();}
  catch(e){data=null;}
  st.busy=false;
  const wasOn=st.aiOn;
  if(data){
    (data.actions||[]).forEach(a=>{if(st.byId[a.action_id]) st.byId[a.action_id]=a;});
    if(data.ai_available===false) st.aiOn=false;
    const msg=data.ai_available===false?MSG.disabled:(ERR[data.ai_error_category]||MSG.other);
    const failed=data.failed||[], ok=(data.enriched||[]).concat(data.cached||[]);
    failed.forEach(id=>{st.errors[id]=msg; st.tried.add(id);});
    ok.forEach(id=>{delete st.errors[id];});
    if(failed.length) setStatus(msg,'err'); else setStatus('AI yorumları hazır.','ok');
  }else{
    ids.forEach(id=>{st.errors[id]=MSG.other; st.tried.add(id);}); setStatus(MSG.other,'err');
  }
  if(wasOn!==st.aiOn){aiChip(); renderList();} else {ids.forEach(replaceCard); updateButtons();}
}

tabBtn.addEventListener('click',start);   // lazy: nothing is fetched until the tab is opened
$('.ai-gen').addEventListener('click',enrichNext);
$('.ai-dr-x').addEventListener('click',closeDrawer);
$('.ai-refresh').addEventListener('click',refreshData);
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&st.drawer) closeDrawer();});
$('.ai-next').addEventListener('click',enrichNext);
})();
"""

def _ai_panel(meta):
    """(tab button, panel block) for the AI Aksiyon Merkezi. The block is wrapped in markers so tests
    can strip it and prove the rest of the dashboard is byte-identical to the build without it."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="ai">✨ AI Aksiyon Merkezi</button>'
    block=('\n<!--AI-TAB-START-->\n<style>'+_AI_CSS+'</style>\n'
           f'<div id="ai" class="panel" data-asof="{asof}">'+_AI_BODY+'</div>\n'
           '<script>'+_AI_JS+'</script>\n<!--AI-TAB-END-->')
    return btn,block


# ---------------- M7-1: Müşteri 360 tab (deterministic, read-only, server-driven) ----------------
# One tab button + one panel, wrapped in markers like the AI tab so tests can prove the rest of the dashboard is
# byte-identical without it. Everything comes from the local FastAPI app (GET /api/customer360/status|search|<code>);
# nothing is fetched until the tab is opened, no AI endpoint is ever called, and all workbook text is set with
# textContent / safe DOM APIs only (never innerHTML).
_C360_CSS=r"""
#c360 .c3-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 10px}
#c360 .c3-chip{font-size:12px;border:1px solid var(--line);background:var(--card);border-radius:999px;padding:3px 10px;color:var(--muted)}
#c360 .c3-date{font-size:12px;color:var(--muted);margin-left:auto}
#c360 .c3-q{flex:1;min-width:0;width:100%;border:1px solid var(--line);border-radius:8px;padding:10px 12px;font:inherit;font-size:15px}
#c360 .c3-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0 12px}
#c360 .c3-state.off,#c360 .c3-state.err{border-color:#f3c4c4;background:#fdf3f3;color:#b42318;font-weight:600}
#c360 .c3-state.wait{color:var(--muted)}
#c360 .c3-warnbar{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:9px 12px;margin:0 0 10px;font-size:13px;font-weight:600}
#c360 .c3-hint{font-size:12px;color:var(--muted);margin:6px 0}
#c360 .c3-hits{display:flex;flex-direction:column;gap:6px;margin:6px 0 14px}
#c360 .c3-hit{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;text-align:left;border:1px solid var(--line);background:var(--card);border-radius:8px;padding:9px 12px;font:inherit;cursor:pointer;color:var(--ink);width:100%}
#c360 .c3-hit:hover,#c360 .c3-hit.on{border-color:var(--ink)}
#c360 .c3-hit-n{font-weight:700;flex:1 1 220px;min-width:0}
#c360 .c3-hit-c,#c360 .c3-code{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--muted)}
#c360 .c3-hit-d{font-size:12px;color:var(--muted)}
#c360 .c3-kapali{font-size:10px;font-weight:800;letter-spacing:.04em;color:#fff;background:var(--crit);border-radius:4px;padding:1px 6px;margin-left:6px;vertical-align:2px}
#c360 .c3-head{margin:4px 0 8px}
#c360 .c3-name{font-size:20px;font-weight:800;letter-spacing:-.01em;margin:0}
#c360 .c3-sub{font-size:12px;color:var(--muted)}
#c360 .c3-warns{list-style:none;padding:0;margin:0 0 12px;display:flex;flex-direction:column;gap:6px}
#c360 .c3-warns li{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:7px 11px;font-size:13px}
#c360 .c3-srcs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 14px}
#c360 .c3-src{font-size:12px;border:1px solid var(--line);background:var(--card);border-radius:8px;padding:5px 9px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
#c360 .c3-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:12px}
#c360 .c3-card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
#c360 .c3-card h4{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin:0 0 8px}
#c360 .c3-kv{display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
#c360 .c3-kv .l{color:var(--muted);flex:0 1 auto;overflow-wrap:normal}#c360 .c3-kv .v{text-align:right;font-variant-numeric:tabular-nums;min-width:0}
#c360 .c3-note{font-size:12px;color:var(--muted);margin:6px 0}
#c360 .c3-scroll{overflow-x:auto;max-width:100%;margin:6px 0}
#c360 table.c3-t{width:100%;border-collapse:collapse;font-size:12.5px;border:0;border-radius:0}
#c360 table.c3-t th{cursor:default;padding:6px 8px;font-size:10.5px}
#c360 table.c3-t td{padding:6px 8px;font-variant-numeric:tabular-nums}
#c360 table.c3-t td.r,#c360 table.c3-t th.r{text-align:right;white-space:nowrap}
#c360 table.c3-t td.nw{white-space:nowrap}
#c360 .c3-badge{display:inline-block;font-size:10px;font-weight:700;border-radius:4px;padding:1px 6px;letter-spacing:.02em;white-space:nowrap;border:1px solid transparent}
#c360 .c3-trusted{color:#1a7f4b;background:#f0faf4;border-color:#bfe3cf}
#c360 .c3-unverified{color:#475467;background:#f2f4f7;border-color:#d0d5dd}
#c360 .c3-incomplete{color:#92660a;background:#fef6e7;border-color:#f5d9a8}
#c360 .c3-stale{color:#b45309;background:#fff4ed;border-color:#f9cfb0}
#c360 .c3-invalid,#c360 .c3-unavailable{color:#b42318;background:#fdf3f3;border-color:#f3c4c4}
#c360 .c3-shared{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:6px;padding:5px 8px;font-size:12px;margin-top:4px}
#c360 .c3-types{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px}
#c360 .c3-type{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:3px 10px}
#c360 .c3-list{list-style:none;padding:0;margin:0}
#c360 .c3-list li{padding:5px 0;border-bottom:1px solid var(--line);font-size:13px}
#c360 [hidden]{display:none!important}
#c360 .c3-hit,#c360 .c3-name,#c360 .c3-warns li,#c360 .c3-state,#c360 .c3-kv .v,#c360 .c3-note{overflow-wrap:anywhere}
#c360 table.c3-t td:not(.r):not(.nw){overflow-wrap:break-word;min-width:5.5em}
@media(max-width:600px){#c360 .c3-date{margin-left:0;width:100%}#c360 .c3-grid{grid-template-columns:1fr}
#c360 .c3-hit{min-height:44px}#c360 .c3-name{font-size:18px}}
"""

_C360_BODY="""
<h2 class="sec">Müşteri 360</h2>
<p class="note">Bir müşteriyi kod veya adla arayın: satış geçmişi, ana kayıt, fatura hesapları ve cari, Hedef, fuar/etkinlik geçmişi ve aksiyonlar tek görünümde. Tüm bilgiler Python ile hesaplanır; yapay zekâ kullanılmaz.</p>
<div class="c3-bar"><span class="c3-chip c3-srv" hidden></span><span class="c3-date"></span></div>
<div class="c3-warnbar" hidden></div>
<div class="c3-state" role="status" hidden></div>
<input class="c3-q" type="search" placeholder="Müşteri kodu veya adı (en az 2 karakter)" aria-label="Müşteri ara" autocomplete="off" disabled>
<p class="c3-hint" role="status" aria-live="polite" hidden></p>
<div class="c3-hits"></div>
<div class="c3-detail" aria-live="polite"></div>
"""

_C360_JS=r"""
(function(){
'use strict';
const root=document.getElementById('c360'), tabBtn=document.querySelector('.tab[data-t="c360"]');
if(!root||!tabBtn) return;
const ASOF=root.getAttribute('data-asof')||'';
const POLL_MS=window.__C360_POLL_MS||2000, POLL_MAX=window.__C360_POLL_MAX||450;
const DEBOUNCE_MS=window.__C360_DEBOUNCE_MS||250, LIMIT=20;
const MSG={
  file:'Müşteri 360 için panoyu http://127.0.0.1:8000/ üzerinden açın.',
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  building:'Müşteri 360 hazırlanıyor… (ilk açılışta birkaç dakika sürebilir)',
  slow:'Müşteri 360 hâlâ hazırlanıyor; biraz sonra sekmeyi yeniden açın.',
  min:'En az 2 karakter yazın.',
  none:'Eşleşen müşteri yok.',
  more:'Daha fazla sonuç var; aramayı daraltın.',
  loading:'Yükleniyor…',
  fail:'Müşteri bilgileri yüklenemedi.',
  searchFail:'Arama yapılamadı.',
  notFound:'Müşteri bulunamadı.',
  mismatch:'Pano ve Müşteri 360 farklı veri tarihleri kullanıyor; verileri yenileyin.'
};
const CONF={trusted:'Güvenilir',unverified:'Doğrulanmadı',incomplete:'Eksik kayıt olabilir',stale:'Eski veri',invalid:'Geçersiz',unavailable:'Yok'};
const TYPES=[['general','Genel'],['fuar','Fuar'],['etkinlik','Etkinlik'],['saha','Saha']];
const STATUS={open:'açık',completed:'tamamlandı',deferred:'ertelendi',ignored:'gizlendi'};
const MASTER={matched:'eşleşti',unmatched:'eşleşmedi',duplicate:'çift kayıt',unavailable:'okunamadı'};
const SRC={available:'var',ok:'var',matched:'eşleşti',unmatched:'eşleşmedi',duplicate:'çift kayıt',unavailable:'yok',
  not_supplied:'sağlanmadı',not_configured:'yapılandırılmadı',failed:'okunamadı'};
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const st={started:false,ready:false,seq:0,dseq:0,timer:null,code:null};
const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function state(t,k){const n=$('.c3-state'); n.textContent=t||''; n.className='c3-state'+(k?' '+k:''); show(n,!!t);}
function hint(t){const n=$('.c3-hint'); n.textContent=t||''; show(n,!!t);}
function fmtDate(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'—';}
function fmtMoney(v){return (v==null||!Number.isFinite(Number(v)))?'—':'₺'+Math.round(Number(v)).toLocaleString('tr-TR');}
function fmtNum(v){return (v==null||!Number.isFinite(Number(v)))?'—':Number(v).toLocaleString('tr-TR',{maximumFractionDigits:2});}
function fmtPct(v){return (v==null||!Number.isFinite(Number(v)))?'—':'%'+(Number(v)*100).toLocaleString('tr-TR',{maximumFractionDigits:1});}
function badge(conf){const c=CONF[conf]?conf:'unavailable'; const b=el('span','c3-badge c3-'+c,CONF[c]); b.dataset.conf=c; return b;}
async function getJSON(url){
  const r=await fetch(url,{headers:{'Accept':'application/json'}}); let body=null;
  try{body=await r.json();}catch(e){body=null;}
  if(!r.ok){const e=new Error('HTTP '+r.status); e.status=r.status; e.body=body; throw e;} return body;}
function kv(label,value,conf){const d=el('div','c3-kv'), v=el('span','v'); v.append(document.createTextNode(value==null||value===''?'—':String(value)));
  if(conf){v.append(document.createTextNode(' ')); v.append(badge(conf));} d.append(el('span','l',label),v); return d;}
function table(cols,rows){   // cols: [[label, right-aligned?]], rows: arrays of strings or Nodes
  const w=el('div','c3-scroll'), t=el('table','c3-t'), th=el('thead'), tr=el('tr');
  cols.forEach(([l,r])=>tr.append(el('th',r?'r':null,l))); th.append(tr); t.append(th);
  const tb=el('tbody');
  rows.forEach(row=>{const x=el('tr'); row.forEach((c,i)=>{const td=el('td',cols[i][1]?'r':null);
    if(cols[i][2]) td.classList.add('nw');
    if(c instanceof Node) td.append(c); else td.textContent=c==null?'—':String(c); x.append(td);}); tb.append(x);});
  if(!rows.length){const x=el('tr'), td=el('td',null,'Kayıt yok.'); td.colSpan=cols.length; x.append(td); tb.append(x);}
  t.append(tb); w.append(t); return w;}
function card(title){const c=el('section','c3-card'); c.append(el('h4',null,title)); return c;}

function start(){
  if(st.started) return; st.started=true;
  if(location.protocol==='file:'){state(MSG.file,'off'); return;}   // the API is same-origin only
  pollStatus(0);
}
async function pollStatus(n){
  let s;
  try{s=await getJSON('/api/customer360/status');}catch(e){state(MSG.offline,'off'); return;}
  const srv=$('.c3-srv'); srv.textContent='● Sunucu bağlı'; show(srv,true);
  if(s.state==='ready'){ready(s); return;}
  if(s.state==='error'){state(s.message||MSG.fail,'err'); return;}
  state(n<POLL_MAX?MSG.building:MSG.slow,'wait');
  if(n<POLL_MAX) setTimeout(()=>pollStatus(n+1),POLL_MS);
}
function ready(s){
  st.ready=true; state('');
  $('.c3-date').textContent=s.as_of_data_date?'Veri: '+fmtDate(s.as_of_data_date):'';
  const w=$('.c3-warnbar');
  if(ASOF&&s.as_of_data_date&&ASOF!==s.as_of_data_date){w.textContent=MSG.mismatch+' (pano: '+fmtDate(ASOF)+' · sunucu: '+fmtDate(s.as_of_data_date)+')'; show(w,true);}
  else show(w,false);
  const q=$('.c3-q'); q.disabled=false; q.focus();
}
function onInput(){clearTimeout(st.timer); st.timer=setTimeout(()=>search($('.c3-q').value),DEBOUNCE_MS);}
async function search(q){
  const box=$('.c3-hits'); const my=++st.seq;
  if(String(q).trim().length<2){box.replaceChildren(); hint(String(q).trim()?MSG.min:''); return;}
  let res;
  try{res=await getJSON('/api/customer360/search?q='+encodeURIComponent(q)+'&limit='+LIMIT);}
  catch(e){if(my===st.seq){box.replaceChildren(); hint(MSG.searchFail);} return;}
  if(my!==st.seq) return;   // an older response arriving late is ignored
  box.replaceChildren();
  const hits=Array.isArray(res.results)?res.results:[];
  hint(!hits.length?MSG.none:(res.truncated?MSG.more:''));
  hits.forEach(h=>{const b=el('button','c3-hit'+(h.customer_code===st.code?' on':'')); b.type='button'; b.dataset.code=h.customer_code;
    const n=el('span','c3-hit-n',h.display_name); if(h.closed===true) n.append(el('span','c3-kapali','KAPALI'));
    b.append(n,el('span','c3-hit-c',h.customer_code),el('span','c3-hit-d','son sipariş: '+fmtDate(h.last_order_date)));
    b.addEventListener('click',()=>openCustomer(h.customer_code)); box.append(b);});
}
async function openCustomer(code){
  st.code=code; const my=++st.dseq;
  root.querySelectorAll('.c3-hit').forEach(b=>b.classList.toggle('on',b.dataset.code===code));
  const d=$('.c3-detail'); d.replaceChildren(el('p','c3-note',MSG.loading));
  let data;
  try{data=await getJSON('/api/customer360/'+encodeURIComponent(code));}
  catch(e){if(my===st.dseq) d.replaceChildren(el('p','c3-state err',e.status===404?MSG.notFound:MSG.fail)); return;}
  if(my!==st.dseq) return;   // another customer was selected meanwhile
  renderDetail(data);
}

function renderDetail(x){
  const d=$('.c3-detail'); d.replaceChildren();
  const id=x.identity||{}, m=x.master||{};
  const head=el('div','c3-head'), nm=el('h3','c3-name',id.display_name||x.customer_code);
  if(m.closed===true) nm.append(el('span','c3-kapali','KAPALI'));
  head.append(nm, el('div','c3-sub c3-code',x.customer_code));
  d.append(head);
  const ws=Array.isArray(x.warnings)?x.warnings:[];
  if(ws.length){const ul=el('ul','c3-warns'); ws.forEach(w=>{const li=el('li',null,w.message); li.dataset.code=w.code; ul.append(li);}); d.append(ul);}
  const srcs=el('div','c3-srcs');
  (x.sources||[]).forEach(s=>{const c=el('span','c3-src'); c.dataset.key=s.key; c.append(el('b',null,s.label),el('span',null,SRC[s.status]||s.status),badge(s.confidence));
    if(s.as_of) c.append(el('span',null,fmtDate(s.as_of))); srcs.append(c);});
  d.append(srcs);
  const g=el('div','c3-grid'); d.append(g);
  g.append(identityCard(x), salesCard(x.sales||{}), productsCard(x.products||{}), receivablesCard(x.receivables||{}),
           hedefCard(x.hedef||{}), eventsCard(x.events||{}), activityCard(x.activity||{}));
}
function identityCard(x){
  const c=card('Kimlik'), id=x.identity||{}, m=x.master||{};
  c.dataset.sec='identity';
  c.append(kv('Müşteri kodu',x.customer_code,'trusted'));
  const src={master:'ana kayıt adı',sales_project:'satış raporu (ProjeIsmi)',invoice_account:'fatura hesabı adı (D-003 yedeği)',code:'kod'};
  c.append(kv('Görünen ad kaynağı',src[id.display_name_source]||id.display_name_source));
  c.append(kv('Ana kayıt',MASTER[m.status]||m.status));
  c.append(kv('Ana kayıt adı',m.name&&m.name.value,m.name&&m.name.confidence));
  c.append(kv('Cari firma (açık/kapalı)',m.status_raw&&m.status_raw.value,m.status_raw&&m.status_raw.confidence));
  c.append(kv('Sektör',m.sector&&m.sector.value,m.sector&&m.sector.confidence));
  c.append(el('p','c3-note','Satış temsilcisi kavramları ayrı gösterilir; hiçbiri "kayıtlı satıcı" olarak seçilmez.'));
  (x.salespeople||[]).forEach(p=>c.append(kv(p.label,(p.values||[]).join(', '),p.confidence)));
  return c;
}
function salesCard(s){
  const c=card('Satış geçmişi'); c.dataset.sec='sales';
  c.append(kv('İlk sipariş',fmtDate(s.first_order_date),s.confidence), kv('Son sipariş',fmtDate(s.last_order_date),s.confidence),
           kv('Sipariş sayısı (belge)',fmtNum(s.orders)), kv('Aktif yıllar',(s.active_years||[]).join(', ')),
           kv(s.current_year+' siparişi',s.has_current_year_orders?'var':'yok'),
           kv('Son '+s.recent_window_days+' günde sipariş',fmtNum(s.recent_orders)),
           kv('Toplam '+(s.amount_label||''),fmtMoney(s.total_net_order_amount)));
  const rows=(s.years||[]).map(y=>[String(y.year),fmtMoney(y.net_order_amount),fmtNum(y.quantity),fmtNum(y.orders)]);
  c.append(table([['Yıl',0,1],['Nettutar',1],['Miktar',1],['Sipariş',1]],rows));
  c.append(el('p','c3-note',s.amount_note||''));
  if(s.zero_amount_lines) c.append(el('p','c3-note',s.zero_amount_lines+' satırda tutar kayıtlı olarak 0.'));
  if(s.missing_amount_lines) c.append(el('p','c3-note',s.missing_amount_lines+' satırda tutar boş (sıfır sayılmadı).'));
  return c;
}
function productsCard(p){
  const c=card('Ürünler / kategoriler'); c.dataset.sec='products';
  const rows=(p.top||[]).map(r=>{const n=el('span'); n.append(el('div',null,r.name||r.sku),el('div','c3-code',r.sku)); 
    return [n,fmtMoney(r.net_order_amount),fmtNum(r.quantity),fmtNum(r.orders),fmtDate(r.last_order_date)];});
  c.append(el('p','c3-note','İlk '+p.limit+' ürün, '+(p.amount_label||'tutar')+' sırasıyla.'));
  c.append(table([['Ürün'],['Tutar',1],['Miktar',1],['Sipariş',1],['Son',1]],rows));
  const mix=(title,list)=>{c.append(el('p','c3-note',title));
    c.append(table([['Etiket'],['Tutar',1],['Pay',1],['Satır',1]],(list||[]).map(r=>[r.label,fmtMoney(r.net_order_amount),fmtPct(r.share),fmtNum(r.lines)])));};
  mix('Kategori (kategori)',p.categories); mix('Ana grup (anagrup)',p.groups);
  return c;
}
function receivablesCard(r){
  const c=card('Cari / alacak'); c.dataset.sec='receivables';
  c.append(el('p','c3-note','Sipariş müşterisi (Pro_kodu) ile fatura hesabı (Musterikod) ayrı kavramlardır. Alacak fatura hesabı düzeyindedir.'));
  (r.accounts||[]).forEach(a=>{
    const box=el('div','c3-acc'); box.dataset.account=a.account_code;
    box.append(kv('Fatura hesabı',a.account_code+(a.account_name?' · '+a.account_name:'')));
    box.append(kv('Bu hesaptan sipariş',fmtNum(a.orders)+' · '+fmtMoney(a.net_order_amount)));
    if(!a.aging||!a.aging.available) box.append(kv('Vadesi geçen bakiye','cari dosyası yok','unavailable'));
    else if(!a.aging.present_in_file) box.append(kv('Vadesi geçen bakiye','cari dosyasında hesap yok',a.aging.confidence));
    else{box.append(kv('Vadesi geçen bakiye (hesap)',fmtMoney(a.aging.overdue),a.aging.confidence));
      box.append(kv('En eski dolu yaş dilimi',a.aging.oldest_bucket||'—'));}
    if(a.shared){const s=el('div','c3-shared',a.warning); s.dataset.shared='1'; box.append(s);}
    c.append(box);
  });
  (r.limitations||[]).forEach(t=>c.append(el('p','c3-note',t)));
  return c;
}
function hedefCard(h){
  const c=card('Hedef bilgileri'); c.dataset.sec='hedef'; c.dataset.status=h.status||'';
  const text={not_configured:'Hedef dosyası yapılandırılmadı (HEDEF_PATH). Diğer bölümler normal çalışır.',
    failed:'Hedef dosyası yapılandırılmış ama okunamadı; diğer bölümler etkilenmedi.',
    unmatched:'Bu müşteri yapılandırılan Hedef dosyasında birebir kodla bulunamadı.'};
  if(h.status!=='matched'){c.append(el('p','c3-note c3-hedef-state',text[h.status]||h.status)); return c;}
  c.append(kv('Dosya',(h.file||'—')+(h.snapshot_date?' · '+fmtDate(h.snapshot_date):' · anlık görüntü tarihi bilinmiyor')));
  (h.fields||[]).forEach(f=>{const row=kv(f.label,(typeof f.value==='number')?fmtNum(f.value):f.value,f.confidence); row.dataset.key=f.key; c.append(row);
    if(f.note&&(f.confidence==='stale'||f.confidence==='incomplete')) c.append(el('p','c3-note',f.note));});
  c.append(table([['Yıl',0,1],['Kaşe adedi',1],['Kaynakta net ciro',1]],(h.years||[]).map(y=>{
    const a=el('span'); a.append(document.createTextNode(fmtNum(y.stamp_qty.value)+' '),badge(y.stamp_qty.confidence));
    const b=el('span'); b.append(document.createTextNode(fmtMoney(y.reported_net_revenue.value)+' '),badge(y.reported_net_revenue.confidence));
    return [String(y.year),a,b];})));
  (h.notes||[]).forEach(t=>c.append(el('p','c3-note',t)));
  return c;
}
function eventsCard(e){
  const c=card('Fuar / etkinlik geçmişi'); c.dataset.sec='events';
  if(e.status!=='ok'){c.append(el('p','c3-note','Sipariş kaynağı verisi okunamadı.')); return c;}
  const ty=el('div','c3-types'); const ob=e.orders_by_type||{}, de=e.distinct_events_by_type||{};
  TYPES.forEach(([k,l])=>{const t=el('span','c3-type',l+': '+fmtNum(ob[k]||0)+' sipariş'+(k!=='general'?' · '+fmtNum(de[k]||0)+' etkinlik':'')); t.dataset.type=k; ty.append(t);});
  c.append(ty, badge(e.confidence));
  c.append(table([['Tarih',0,1],['Tür'],['Dönem yılı',1],['Etkinlik']],(e.recent||[]).map(r=>[fmtDate(r.order_date),
    (TYPES.find(t=>t[0]===r.source_type)||[0,r.source_type])[1],r.edition_year==null?'—':String(r.edition_year),r.event_name||'—'])));
  c.append(el('p','c3-note',e.note||''));
  return c;
}
function activityCard(a){
  const c=card('Aksiyonlar ve geçmiş'); c.dataset.sec='activity';
  const row=x=>[x.title||x.action_id,x.category_label||x.category||'—',STATUS[x.status]||x.status||'—',
    x.defer_until?fmtDate(x.defer_until):(x.completed_at?fmtDate(x.completed_at):'')];
  c.append(el('p','c3-note','Güncel aksiyonlar (Python kuyruğu)'));
  c.append(table([['Aksiyon'],['Kategori'],['Durum'],['Tarih']],(a.current_actions||[]).map(row)));
  c.append(el('p','c3-note','Kayıtlı geçmiş aksiyonlar'));
  c.append(table([['Aksiyon'],['Kategori'],['Durum'],['Tarih']],(a.stored_actions||[]).map(row)));
  c.append(el('p','c3-note','Takip notları (salt okunur)'));
  const ul=el('ul','c3-list'); (a.memory||[]).forEach(m=>ul.append(el('li',null,fmtDate(m.ts)+' · '+(m.kind||'')+' · '+(m.detail||''))));
  if(!(a.memory||[]).length) ul.append(el('li','c3-note','Kayıt yok.'));
  c.append(ul);
  return c;
}

tabBtn.addEventListener('click',start);   // lazy: nothing is fetched until the tab is opened
$('.c3-q').addEventListener('input',onInput);
})();
"""

def _c360_panel(meta):
    """(tab button, panel block) for Müşteri 360, wrapped in markers like the AI tab."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="c360">Müşteri 360</button>'
    block=('\n<!--C360-TAB-START-->\n<style>'+_C360_CSS+'</style>\n'
           f'<div id="c360" class="panel" data-asof="{asof}">'+_C360_BODY+'</div>\n'
           '<script>'+_C360_JS+'</script>\n<!--C360-TAB-END-->')
    return btn,block


def _html(modules,stats,meta,later,ai_panel=True,c360_panel=True):
    blob=json.dumps(modules,ensure_ascii=False).replace('</','<\\/')
    tabs=['overview']+list(modules.keys())
    tabbtns=''.join(f'<button class="tab" data-t="{t}">{("Genel Bakış" if t=="overview" else modules[t]["title"])}'
                    f'{"" if t=="overview" else f" <span class=cnt>{len(modules[t]["rows"])}</span>"}</button>' for t in tabs)
    statcards=''.join(f'<div class="stat"><div class="n">{v}</div><div class="l">{l}</div></div>' for l,v in stats)
    laterlist=''.join(f'<li>{html.escape(x)}</li>' for x in later)
    src=f"Stok: {meta['stock']} · Satış: {meta['sales']}"+(f" · Alacaklar: {meta['aging']}" if meta['has_aging'] else '')
    ai_btn,ai_block=_ai_panel(meta) if ai_panel else ('','')
    c3_btn,c3_block=_c360_panel(meta) if c360_panel else ('','')
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Şirket Panosu — {meta['date']}</title>
<style>
:root{{--ink:#16191d;--muted:#6b7480;--line:#e6e8ec;--paper:#fbfbf9;--card:#fff;
--low:#c2410c;--crit:#9f1239;--over:#0e7490;--gold:#92660a;--lap:#6d28d9;--warn:#b45309;--warn-bg:#fef6e7;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);
font-family:-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45}}
.wrap{{max-width:1080px;margin:0 auto;padding:26px 22px 70px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:18px}}
.eyebrow{{font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:var(--muted);font-weight:700}}
h1{{font-size:26px;font-weight:800;letter-spacing:-.02em;margin:5px 0 2px}}
.sub{{color:var(--muted);font-size:12px}}
.tabs{{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:20px}}
.tab{{border:1px solid var(--line);background:var(--card);color:var(--muted);font:inherit;font-weight:600;
padding:8px 14px;border-radius:8px;cursor:pointer}}
.tab:hover{{color:var(--ink)}}.tab.on{{background:var(--ink);color:#fff;border-color:var(--ink)}}
.tab .cnt{{font-size:11px;opacity:.7;margin-left:3px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:8px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.stat .n{{font-size:22px;font-weight:800;font-variant-numeric:tabular-nums}}
.stat .l{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-top:2px}}
.panel{{display:none}}.panel.on{{display:block}}
.toolbar{{display:flex;gap:10px;align-items:center;margin:6px 0 12px;flex-wrap:wrap}}
.search{{flex:1;min-width:180px;border:1px solid var(--line);border-radius:8px;padding:9px 12px;font:inherit}}
.count{{font-size:12px;color:var(--muted)}}.note{{font-size:12px;color:var(--muted);margin:0 0 12px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th{{text-align:left;font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:700;
padding:10px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap;user-select:none}}
th.r,td.r{{text-align:right}}th:hover{{color:var(--ink)}}th .ar{{opacity:.4;font-size:9px}}
td{{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
tbody tr.main:hover{{background:#f6f7f8}}
.mono{{font-family:"SF Mono",Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}}
.money{{text-align:right;font-family:"SF Mono",Menlo,Consolas,monospace}}
.tier{{font-family:"SF Mono",Menlo,monospace;font-size:11px;font-weight:700;padding:2px 7px;border-radius:4px;color:#fff}}
.t1{{background:#6d28d9}}.t2{{background:#8b5cf6}}.t3{{background:#a78bfa}}
.crit{{color:var(--crit);font-weight:700}}.val{{color:var(--gold);font-weight:600}}
.owe{{background:var(--warn-bg)}}.owe td.money.od{{color:var(--warn);font-weight:700}}
td.neg{{color:#b42318;font-weight:600}}td.pos{{color:#1a7f4b;font-weight:600}}
.subc{{font-family:"SF Mono",Menlo,monospace;font-size:11px;color:var(--muted);margin-top:2px}}
.repsel{{border:1px solid var(--line);border-radius:8px;padding:9px 12px;font:inherit;background:var(--card);cursor:pointer}}
.tag{{font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:var(--low);background:#fff4ed;padding:1px 6px;border-radius:3px;margin-left:6px}}
.empty{{text-align:center;color:var(--muted);font-style:italic;padding:22px}}
.sec{{font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;margin:0 0 6px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
.later{{columns:2;gap:24px;margin:8px 0 0;padding-left:18px;font-size:13px;color:var(--ink)}}
.later li{{margin:5px 0;break-inside:avoid}}
code{{background:#eef0f2;padding:1px 5px;border-radius:4px;font-size:12px}}
tr.exp td:first-child::before{{content:'▸';color:var(--muted);margin-right:7px;display:inline-block;transition:.15s}}
tr.exp.open td:first-child::before{{transform:rotate(90deg)}}
tr.exp{{cursor:pointer}}
tr.det{{display:none}}tr.det.open{{display:table-row}}
tr.det>td{{background:#faf9fd;padding:0}}
.detail{{display:flex;gap:26px;flex-wrap:wrap;padding:14px 16px 16px 30px}}
table.sub{{width:auto;border:1px solid var(--line);min-width:320px}}
table.sub th{{cursor:default;padding:7px 12px}}table.sub td{{padding:6px 12px;font-variant-numeric:tabular-nums}}
.prods{{flex:1;min-width:280px}}.prods-h{{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:700;margin-bottom:6px}}
.prow{{display:flex;justify-content:space-between;gap:14px;padding:4px 0;border-bottom:1px solid var(--line)}}
.pn{{font-size:13px}}.pv{{font-size:12px;color:var(--muted);white-space:nowrap;font-family:"SF Mono",Menlo,monospace}}
</style></head><body><div class="wrap">
<header><div class="eyebrow">Şirket Panosu</div>
<h1>Şirket Panosu — {meta['date']}</h1>
<div class="sub">{src} · {meta['total']:,} ürün</div></header>
<div class="tabs">{tabbtns}{c3_btn}{ai_btn}</div>
<div id="overview" class="panel on">
<h2 class="sec">Aktif Analizler</h2>
<p class="note">İlk istediğiniz ikisi — <b>stok uyarıları</b> (azalan + fazla) ve <b>kayıp müşteriler</b> (geri kazanım) — çalışıyor. Gezinmek, aramak ve sıralamak için yukarıdaki sekmelere tıklayın. Her şey doğrudan tablolarınızdan hesaplanır: filtreleme, müşteri ve stok kodu üzerinden birleştirme ve aritmetik — hiçbir şey tahmin değil.</p>
<div class="stats">{statcards}</div>
<p class="note" style="margin-top:14px">Geri kazanım dağılımı — Kademe 1: {meta['tiers'][1]} · Kademe 2: {meta['tiers'][2]} · Kademe 3: {meta['tiers'][3]}.</p>
<h2 class="sec" style="margin-top:34px">Sonrası İçin</h2>
<p class="note">Hazır olduğunuzda açabileceğimiz, tamamen gerçeğe dayalı diğer analizler — her biri yalnızca ilgili sayfanın <code>data</code> klasöründe olmasını ister, sonra kendi sekmesi olur. Yapay zekâ yok, bunlarla aynı.</p>
<ul class="later">{laterlist}</ul>
</div>
<div id="panels"></div>{c3_block}{ai_block}
<script>
const DATA={blob};
const fmtMoney=v=>'₺'+Math.round(v).toLocaleString('tr-TR');
const fmtNum=(v,d)=>Number(v).toLocaleString('tr-TR',{{minimumFractionDigits:d||0,maximumFractionDigits:d||0}});
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
const state={{}};
function detailHTML(d){{
  if(!d) return '<div class="detail"><span style="color:#888">Detay yok.</span></div>';
  if(d.buyers){{
    let bh='<div class="prods" style="min-width:420px"><div class="prods-h">Bu ürünü alan müşteriler (çağrı listesi)</div>';
    d.buyers.forEach(b=>{{bh+=`<div class="prow"><span class="pn">${{esc(b.name)}}</span><span class="pv">${{fmtNum(b.qty)}} adet · son ${{b.last}}</span></div>`;}});
    return `<div class="detail">${{bh}}</div></div>`;
  }}
  const hasY=d.years&&d.years.length, hasP=d.products&&d.products.length;
  if(!hasY&&!hasP) return '<div class="detail"><span style="color:#888">Detay yok.</span></div>';
  let y='';
  if(hasY){{y='<table class="sub"><thead><tr><th>Yıl</th><th class=r>Harcama</th><th class=r>Sipariş</th><th class=r>Sip. Ort.</th></tr></thead><tbody>';
    d.years.forEach(r=>{{y+=`<tr><td>${{r.y}}</td><td class="r money">${{fmtMoney(r.spend)}}</td><td class=r>${{r.orders}}</td><td class="r money">${{fmtMoney(r.avg)}}</td></tr>`;}});
    y+='</tbody></table>';}}
  let p='';
  if(hasP){{p='<div class="prods"><div class="prods-h">Öne Çıkan Ürünler</div>';
    d.products.forEach(pr=>{{p+=`<div class="prow"><span class="pn">${{esc(pr.name)}}</span><span class="pv">${{fmtMoney(pr.spend)}} · ${{fmtNum(pr.qty)}} adet</span></div>`;}});
    p+='</div>';}}
  return `<div class="detail">${{y}}${{p}}</div>`;
}}
function makePanel(id){{
  const m=DATA[id]; const p=document.createElement('div'); p.className='panel'; p.id=id;
  let filterHTML='';
  if(m.filter){{
    const vals=[...new Set(m.rows.map(r=>r[m.filter]).filter(x=>x))].sort((a,b)=>String(a).localeCompare(String(b),'tr'));
    filterHTML=`<select class="repsel"><option value="">Tüm temsilciler</option>${{vals.map(v=>`<option>${{esc(v)}}</option>`).join('')}}</select>`;
  }}
  p.innerHTML=`<div class="toolbar"><input class="search" placeholder="${{m.title.toLowerCase()}} ara…">${{filterHTML}}
    <span class="count"></span></div><p class="note">${{m.note}}</p><div class="tbl"></div>`;
  state[id]={{sort:null,dir:1,q:'',fval:''}};
  p.querySelector('.search').addEventListener('input',e=>{{state[id].q=e.target.value.toLowerCase();draw(id);}});
  const sel=p.querySelector('.repsel'); if(sel) sel.addEventListener('change',e=>{{state[id].fval=e.target.value;draw(id);}});
  document.getElementById('panels').appendChild(p); draw(id);
}}
function draw(id){{
  const m=DATA[id], s=state[id]; let rows=m.rows.slice();
  if(s.fval) rows=rows.filter(r=>String(r[m.filter]??'')===s.fval);
  if(s.q) rows=rows.filter(r=>m.cols.some(c=>String(r[c.k]??'').toLowerCase().includes(s.q)));
  if(s.sort){{const c=s.sort; rows.sort((a,b)=>{{let x=a[c],y=b[c];
    if(typeof x==='number'||typeof y==='number'){{x=x??-Infinity;y=y??-Infinity;return (x-y)*s.dir;}}
    return String(x??'').localeCompare(String(y??''),'tr')*s.dir;}});}}
  const isnum=c=>c.n!=null||c.money;
  let h='<table><thead><tr>'+m.cols.map(c=>`<th class="${{isnum(c)?'r':''}}" data-k="${{c.k}}">${{c.l}} <span class=ar>${{s.sort===c.k?(s.dir>0?'▲':'▼'):'↕'}}</span></th>`).join('')+'</tr></thead><tbody>';
  if(!rows.length) h+=`<tr><td class="empty" colspan="${{m.cols.length}}">Gösterilecek kayıt yok.</td></tr>`;
  rows.forEach((r,i)=>{{
    const owe=id==='winback'&&r.overdue>0;
    const cls='main '+(m.expand?'exp':'')+(owe?' owe':'');
    h+=`<tr class="${{cls}}" data-i="${{i}}">`+m.cols.map(c=>{{
      let v=r[c.k];
      if(c.k==='tier') return `<td><span class="tier t${{v}}">K${{v}}</span></td>`;
      if(c.money) return `<td class="money ${{c.k==='overdue'?'od':(c.k==='inv'?'val':'')}} ${{(c.sign&&v<0)?'neg':(c.sign&&v>0?'pos':'')}}">${{v?fmtMoney(v):(v===0?'₺0':'—')}}</td>`;
      if(c.n!=null) return `<td class="r mono ${{(id==='reorder'&&r.crit&&(c.k==='cov'||c.k==='stock'))?'crit':''}} ${{(c.sign&&v<0)?'neg':(c.sign&&v>0?'pos':'')}}">${{v==null?'—':fmtNum(v,String(v).includes('.')?2:0)}}</td>`;
      if(c.k==='code') return `<td class="mono" style="color:var(--muted);font-size:12px">${{v}}</td>`;
      if(c.k==='last') return `<td class="mono r">${{v??'—'}}</td>`;
      if(c.k==='name'){{
        let tags=(id==='overstock'&&r.disc)?' <span class="tag">üretimden kalktı</span>':'';
        if(id==='risk'&&r.closed) tags+=' <span class="tag">KAPALI</span>';
        let sub=(c.sub&&r[c.sub])?`<div class="subc">${{esc(r[c.sub])}}</div>`:'';
        return `<td><div class="prod">${{esc(v)}}${{tags}}</div>${{sub}}</td>`;
      }}
      return `<td>${{esc(v)}}</td>`;
    }}).join('')+'</tr>';
    if(m.expand) h+=`<tr class="det" data-d="${{i}}"><td colspan="${{m.cols.length}}">${{detailHTML(r.detail)}}</td></tr>`;
  }});
  h+='</tbody></table>';
  const p=document.getElementById(id); p.querySelector('.tbl').innerHTML=h;
  p.querySelector('.count').textContent=rows.length+' satır';
  p.querySelectorAll('th').forEach(th=>th.addEventListener('click',()=>{{
    const k=th.dataset.k, col=DATA[id].cols.find(c=>c.k===k);
    if(state[id].sort===k) state[id].dir*=-1; else {{state[id].sort=k;state[id].dir=(col.money||col.n!=null)?-1:1;}}
    draw(id);}}));
  if(m.expand) p.querySelectorAll('tr.exp').forEach(tr=>tr.addEventListener('click',()=>{{
    const d=tr.nextElementSibling; tr.classList.toggle('open'); d.classList.toggle('open');}}));
}}
Object.keys(DATA).forEach(makePanel);
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{{
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); document.getElementById(b.dataset.t).classList.add('on');}}));
document.querySelector('.tab').classList.add('on');
</script></div></body></html>"""

if __name__=='__main__':
    import sys
    out,counts=build(sys.argv[1] if len(sys.argv)>1 else '.', 'dashboard.html')
    print('built',out,counts)
