"""İnteraktif pano oluşturucu — sayfaları hesaplar ve tek bir HTML dosyası üretir."""
import os, glob, json, html, datetime as dt, warnings
import pandas as pd, numpy as np
from services.stock_policy import RETIREMENT_REASON
from services import cari_policy as CARI
warnings.filterwarnings('ignore')

STOCK_HEADER_ROW=3
YEAR_THR=10000; MIN_TOTAL=30000; QUAL_YEARS=[2021,2022,2023,2024,2025]; LAPSED_YEAR=2026; T3_YEARS=[2024,2025]
SC={'code':'Stok_Kodu','name':'Stok_Adı','stock':'TOPLAM STOK MİKTAR ','cov':'STOK Kontrol %',
    'min':'minseviye','max':'maxseviye','inv':'Envanter değeri','status':'DURUM'}
AYLAR=['','Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık']

def is_temp_file(n):
    """Office owner/lock files ('~$…', created while a workbook is open in Excel) and macOS resource forks ('._…')
    are never sources (M8-ACCURACY-2B: an open stock workbook made the fallback pick its 165-byte lock file)."""
    return n.startswith('~$') or n.startswith('._') or n.startswith('.~lock')
def _latest(folder,pred):
    m=[f for f in glob.glob(os.path.join(folder,'*.xlsx')) if not is_temp_file(os.path.basename(f)) and pred(os.path.basename(f))]
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
    """D-070: no current purchasing policy exists. Preserve raw rows; do not classify from obsolete fields.
    Empty recommendation lists mean withdrawn, not zero products needing attention (totals are unavailable).
    Signed quantities, blanks, duplicates and Excel formulas are left exactly as read; no physical-stock inference.
    """
    df=read_stock(path) if df is None else df
    return [],[],stock_row_counts(df)['code_rows']

def stock_row_counts(df):
    """M8-ACCURACY-2B: stock row diagnostics from the frame as read (counts only, nothing dropped or deduplicated).
    The product count is the number of rows that carry a product code. Identity column = physical column A, the first
    'Stok_Kodu' (the one pandas reads); the owner confirmed A = product code and J = country of origin (D-056), and the
    gate accepts the duplicated header only for the exact approved template (source_validation.STOCK_TEMPLATES) —
    any other duplicated-identity layout is refused before anything is shown. Rows without a code — e.g. the export's trailing
    report-total rows — are never counted as products. Footer recognition itself is formula-aware and lives in
    services/source_validation.analyze_stock_rows."""
    code=df[SC['code']] if SC['code'] in df.columns else pd.Series([],dtype=object)
    txt=code.astype(str).str.strip(); has=code.notna()&(txt!='')&(txt.str.lower()!='nan')
    codes=txt[has]
    return {'parsed_rows':int(len(df)),'code_rows':int(has.sum()),'distinct_codes':int(codes.nunique()),
            'duplicate_code_occurrences':int(has.sum()-codes.nunique()),'rows_without_code':int((~has).sum())}

def load_sales(path,df=None,copy=True):
    # copy=False (M7-6, build_context only): the caller read this frame itself and hands over ownership, exactly
    # like the default read below (which also modifies its own frame in place).
    df=read_sales_raw(path) if df is None else (df.copy() if copy else df)
    df['_date']=pd.to_datetime(df['Tarih'],errors='coerce'); df['_yr']=df['_date'].dt.year
    _nt=num(df['Nettutar']); df['_amt_missing']=_nt.isna()   # M7-4: remember missing amounts (M5 still sums 0)
    df['Nettutar']=_nt.fillna(0); df['miktar']=num(df['miktar']).fillna(0)
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
    # D-071: `owed` is accepted for call compatibility and NOT read. Invoice-age balances are not overdue debt, and the
    # old lookup joined an invoice-account map by the order-source code (DQ-6); win-back rows carry no debt claim.
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
                     'spend':_n(tot[c]),'last':int(max(ya)) if ya else None,'years':int(n)})
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
    rows.sort(key=lambda r:(r['pct'],str(r['code'])))   # DQ-3 (M7-6): equal pct in code order, never hash-seed order
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
    """D-070: no clearance recommendation, including from a caller's old threshold-derived rows."""
    return []

def m_risk(df,owed_map,closed_inv,attr):
    """D-071: no customer-risk classification from invoice-age balances (no due dates, no agreements). Withdrawn."""
    return []


def m_collections(path,df=None):
    """D-071: factual Cari balances, not overdue receivables. One row per account (numeric code, as before): the signed
    balance as exported (None when a bucket is blank — unknown, never zero), the oldest month bucket holding a non-zero
    amount (invoice age, not a due date) and the account class (320 = supplier; nothing else is classified).
    Returns (rows, balances) with balances = {account code: signed balance or None}; nothing reads it as overdue."""
    df=read_aging_raw(path) if df is None else df.copy()
    df.columns=[str(c).strip() for c in df.columns]
    key,name,rep='Hesap kodu','Hesap adı','Temsilci kodu'
    months=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık']
    bkts=[c for c in df.columns if 'öncesi' in c or any(m in c for m in months)]  # aging columns, oldest→newest
    # eski format desteği: tek bakiye sütunu (başlığı ne derse desin vade kanıtı sayılmaz, D-071)
    single=next((c for c in df.columns if 'vadesi geçen' in c.lower()),None)
    df=df[df[key].astype(str).str.match(r'^\d',na=False)]
    bal=CARI.signed_balances(df,bkts,None if bkts else single)
    rows=[]
    for i,(_,r) in enumerate(df.iterrows()):
        b=bal.iloc[i]; b=None if pd.isna(b) else float(b)
        cls=CARI.account_class(r[key]); dirn=CARI.balance_direction(b)
        rows.append({'code':str(r[key]),'name':str(r[name]),'rep':('' if pd.isna(r[rep]) else str(r[rep])),
                     # exact signed amount (kuruş kept; never _n(), which rounds to whole lira)
                     'balance':b,'oldest':(CARI.oldest_nonzero_bucket(r,bkts) or '—'),
                     'account_class':cls,'class_label':CARI.CLASS_LABELS[cls],
                     # account-level list: neutral sign names only (customer meaning is not established here)
                     'direction':dirn,'direction_label':CARI.direction_label(dirn,cls,customer_account=False)})
    rows.sort(key=lambda r:r['code'])          # neutral order: no ranking, no implied priority
    return rows,{r['code']:r['balance'] for r in rows}

# ---------- oluştur ----------
def build(data_folder,out_path,today=None,ai_panel=True,sources=None,frames=None,stats_out=None,c360_panel=True,sq_panel=True,opp_panel=True,fair_panel=True,crm_panel=True,own_panel=True,gate=None,manifest=None,vkm_panel=True,up_panel=True):
    """sources/frames (M5-5): a frozen source selection and the already-read raw frames, so a controlled
    refresh builds from exactly what it validated. stats_out (dict): filled with build totals for verification.
    gate (M8-ACCURACY): the factual-integrity gate; default = the production gate (services/accuracy.py). Every
    module, stat card and server-driven panel whose metric is not VERIFIED is WITHHELD from the HTML: its rows are
    never embedded, only a neutral notice. The computations themselves are unchanged.
    manifest (M8-ACCURACY-2): the source generation whose frozen bytes `frames` were parsed from (the controlled
    refresh passes both from one frozen read). Evidence is taken ONLY from it; without a manifest there is no evidence
    and every source-bound output is withheld."""
    from services import accuracy as ACC
    today=today or dt.date.today()
    src=sources or select_sources(data_folder); fr=dict(frames or {})
    sp,ep,ap=src.get('stock'),src.get('sales'),src.get('aging')
    # Read each raw frame once here (as the callees would) so the accuracy evidence sees the values as read.
    if fr.get('stock') is None: fr['stock']=read_stock(sp)
    if ep and fr.get('sales') is None: fr['sales']=read_sales_raw(ep)
    if ap and fr.get('aging') is None: fr['aging']=read_aging_raw(ap)
    # Evidence only from the generation these frames came from (services/generation.Manifest); evidence held for any
    # other generation is dropped, never reused (services/accuracy.Gate.bound_to).
    snapshot=manifest.core_snapshot() if manifest is not None else ACC.EvidenceSnapshot(None,{})
    core_gen=manifest.core_id if manifest is not None else ''
    gate=(gate or ACC.current_gate(today=today)).bound_to(snapshot)
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
    over_val=None; overdue_total=None      # D-071: no overdue total exists (invoice age is not a due date)
    has_aging=bool(ap)

    wb_cols=[{'k':'tier','l':'Kademe'},{'k':'name','l':'Müşteri','sub':'code'},{'k':'rep','l':'Temsilci'},
             {'k':'sector','l':'Sektör'},{'k':'spend','l':'Toplam Harcama','money':1},
             {'k':'last','l':'Son Yıl'},{'k':'years','l':'Güçlü Yıl','n':1}]
    modules={
      'reorder':{'title':'Azalan Stok','rows':low,
        'cols':[{'k':'code','l':'Kod'},{'k':'name','l':'Ürün'},{'k':'stock','l':'Stok','n':1},
                {'k':'cov','l':'Kapsama','n':1},{'k':'min','l':'Min','n':1}],
        'note':RETIREMENT_REASON},
      'overstock':{'title':'Fazla Stok','rows':over,
        'cols':[{'k':'code','l':'Kod'},{'k':'name','l':'Ürün'},{'k':'stock','l':'Stok','n':1},
                {'k':'cov','l':'Kapsama','n':1},{'k':'inv','l':'Envanter Değeri','money':1}],
        'note':RETIREMENT_REASON},
      'osmove':{'title':'Stok Eritme','rows':osmove,'expand':True,
        'cols':[{'k':'code','l':'Kod'},{'k':'name','l':'Ürün'},{'k':'stock','l':'Stok','n':1},
                {'k':'inv','l':'Envanter Değeri','money':1},{'k':'buyers','l':'Alıcı Sayısı','n':1}],
        'note':RETIREMENT_REASON},
      'winback':{'title':'Geri Kazanım','rows':lap,'cols':wb_cols,'expand':True,'filter':'rep',
        'note':'Geçmişte çok alışveriş yaptı, 2026\'da hiç almadı. Önce Kademe 1, sonra harcamaya göre sıralı. Detay için müşteriye tıklayın. Kapalı firmalar çıkarıldı.'},
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
    if has_aging:
        modules['risk']={'title':'Riskli Müşteri','rows':risk,'cols':[{'k':'name','l':'Müşteri','sub':'code'}],
          'note':CARI.OVERDUE_RETIRED_REASON}
        modules['collections']={'title':'Cari Bakiye (fatura yaşı)','rows':coll,'filter':'rep',
          'cols':[{'k':'name','l':'Hesap','sub':'code'},{'k':'rep','l':'Temsilci'},{'k':'class_label','l':'Hesap Türü'},
                  {'k':'balance','l':'Bakiye (işaretli)','money':1,'exact':1,'sign':1},{'k':'direction_label','l':'Bakiye İşareti'},
                  {'k':'oldest','l':'En Eski Fatura Ayı'}],
          'note':' '.join([CARI.OVERDUE_RETIRED_REASON,CARI.BALANCE_NOTE,CARI.SUPPLIER_NOTE,CARI.UNCONFIRMED_NOTE])}
    stats=[('min altı','—'),('maks üstü','—'),
           ('fazla stok değeri',_money(over_val)),('kayıp müşteri',f"{len(lap):,}")]
    # ---- M8-ACCURACY: withhold every output whose metric is not VERIFIED (rows never reach the HTML) ----
    decisions={}
    def _dec(key):
        if key not in decisions: decisions[key]=gate.decide(key)
        return decisions[key]
    for k in list(modules):
        d=_dec(ACC.MODULE_METRIC.get(k,'unregistered:'+k))
        if not d.displayable:
            modules[k]={'title':modules[k]['title'],'rows':[],'cols':[],'withheld':d.notice()}
    withheld_stats=[l for l,_ in stats if not _dec(ACC.STAT_METRIC.get(l,'unregistered:'+l)).displayable]
    stats=[(l,v) for l,v in stats if l not in withheld_stats]
    show_tiers=_dec('customers.winback').displayable
    panel_notice={p:_dec(k).notice() for p,k in ACC.PANEL_METRIC.items() if not _dec(k).displayable}
    later=(['Tahsilat & alacak yaşlandırma'] if not has_aging else [])+[
           'Hedef vs gerçekleşen (müşteri / temsilci / bölge)','Temsilci verimliliği (ziyaret / km başına satış)',
           'Gerçek stok hareketinden sipariş','Bütçe vs gerçekleşen — giderler',
           'Kredi / nakit akışı takvimi','Fiyat değişim takibi','Dijital pazarlama hunisi',
           'Çapraz satış (5 ürün → 5 firma)']
    d_tr=f"{today.day} {AYLAR[today.month]} {today.year}"
    meta={'date':d_tr,'stock':os.path.basename(sp) if sp else '','sales':os.path.basename(ep) if ep else '',
          'aging':os.path.basename(ap) if ap else '','tiers':tier_counts,'total':total,'has_aging':has_aging,
          'asof':_asof_iso(sdf),'show_tiers':show_tiers,'withheld_stats':withheld_stats,
          'withheld_panels':panel_notice,'accuracy_policy':ACC.POLICY_VERSION,'generation':core_gen,
          'total_shown':_dec('stock.product_counts').displayable,
          # M8-ACCURACY-2: every metric whose output this HTML embeds; GET / re-authorizes exactly these keys
          'displayed':sorted(k for k,d in decisions.items() if d.displayable)}
    with open(out_path,'w',encoding='utf-8') as f: f.write(_html(modules,stats,meta,later,ai_panel=ai_panel,c360_panel=c360_panel,sq_panel=sq_panel,opp_panel=opp_panel,fair_panel=fair_panel,crm_panel=crm_panel,own_panel=own_panel,vkm_panel=vkm_panel,up_panel=up_panel))
    if stats_out is not None:
        # Internal build totals for the refresh's context ↔ dashboard reconciliation (never displayed as such).
        stats_out.update({'asof':meta['asof'],'overdue_total':overdue_total,'overstock_value':over_val,
                          'tabs':list(modules.keys()),'products':total,'lapsed':len(lap),'low':None,
                          'over':None,'coll':len(coll),'sources':{k:(os.path.basename(v) if v else None) for k,v in src.items()},
                          'accuracy':{k:d.state.value for k,d in decisions.items()},'generation':core_gen or None})
    return out_path,{'low':None,'over':None,'lapsed':len(lap),'coll':len(coll),
                     'momentum':len(momentum),'margin':len(margin),'ordersize':len(ordersize),
                     'osmove':None,'risk':len(risk)}

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
const WITHHELD=root.getAttribute('data-withheld')==='1';   // M8-ACCURACY: queue withheld server-side; never fetched
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
  if(WITHHELD){checkDate(h.as_of_data_date); return;}   // M8-ACCURACY: status + controlled refresh only
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

def _notice_html(n):
    """M8-ACCURACY: the neutral withheld notice (escaped; message + at most a few short reasons; no numbers)."""
    why=''.join(f'<li>{html.escape(str(r))}</li>' for r in (n.get('reasons') or []))
    return (f'<div class="aw-box" role="note" data-metric="{html.escape(str(n.get("metric","")),quote=True)}" '
            f'data-verification="{html.escape(str(n.get("verification","")),quote=True)}">'
            f'<p class="aw-msg">{html.escape(str(n.get("message","")))}</p>'
            +(f'<ul class="aw-why">{why}</ul>' if why else '')+'</div>')


def _withheld_panel(pid,label,marker,n,meta):
    """M8-ACCURACY: a server-driven panel whose metric is not VERIFIED is replaced by its notice. No script is
    embedded, so the page never requests the withheld API; the tab and the panel id stay for navigation."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn=f'<button class="tab" data-t="{pid}">{label}</button>'
    block=(f'\n<!--{marker}-TAB-START-->\n'
           f'<div id="{pid}" class="panel" data-asof="{asof}" data-withheld="1">'
           f'<h2 class="sec">{label}</h2>'+_notice_html(n)+'</div>\n'
           f'<!--{marker}-TAB-END-->')
    return btn,block


def _ai_panel(meta):
    """(tab button, panel block) for the AI Aksiyon Merkezi. The block is wrapped in markers so tests
    can strip it and prove the rest of the dashboard is byte-identical to the build without it.
    M8-ACCURACY: when the action queue is withheld the panel keeps only the server status and the controlled
    refresh (operational); the script never requests the actions or any AI text (data-withheld)."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    n=(meta.get('withheld_panels') or {}).get('ai')
    btn='<button class="tab" data-t="ai">✨ AI Aksiyon Merkezi</button>'
    body=_AI_BODY if n is None else _AI_BODY.replace('<div class="ai-list"></div>',_notice_html(n)+'<div class="ai-list"></div>',1)
    attr='' if n is None else ' data-withheld="1"'
    block=('\n<!--AI-TAB-START-->\n<style>'+_AI_CSS+'</style>\n'
           f'<div id="ai" class="panel" data-asof="{asof}"{attr}>'+body+'</div>\n'
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
#c360 .c3-opp{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:0 0 8px}
#c360 .c3-opp-h{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 4px}
#c360 .c3-opp-tag{font-size:10px;font-weight:800;letter-spacing:.04em;color:#047857;border:1px solid #a7d7c5;background:#ecfdf5;border-radius:4px;padding:1px 6px}
#c360 .c3-opp-t{overflow-wrap:anywhere;min-width:0}
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
#c360 .c3-crmacts{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
#c360 .c3-crmgo{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:12px;font-weight:600;padding:6px 11px;border-radius:8px;cursor:pointer;min-height:34px}
#c360 [data-sec="crm"] .v,#c360 [data-sec="crm"] li,#c360 .c3-crmcov{overflow-wrap:anywhere}
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
function fmtMoneyExact(v){return (v==null||!Number.isFinite(Number(v)))?'—':'₺'+Number(v).toLocaleString('tr-TR',{minimumFractionDigits:2,maximumFractionDigits:6});}
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
  g.append(identityCard(x), salesCard(x.sales||{}), productsCard(x.products||{}), opportunitiesCard(x.opportunities||{}),
           receivablesCard(x.receivables||{}),
           hedefCard(x.hedef||{}), eventsCard(x.events||{}), crmCard(x.crm, x.customer_code), activityCard(x.activity||{}));
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
function opportunitiesCard(o){   // M7-3: deterministic cross-sell at the anagrup (group) level; never a SKU or money
  const c=card('Fırsatlar'); c.dataset.sec='opportunities'; c.dataset.status=o.status||'';
  c.append(el('p','c3-note',(o.label||'Ürün kapsam fırsatı')+' · '+(o.dimension_label||'Ana grup')+' · son '+(o.recent_window_days||365)+' gün'));
  if(o.status==='unavailable'){c.append(el('p','c3-note c3-opp-state','Fırsat modeli şu anda kullanılamıyor.')); return c;}
  if(o.status!=='available'){c.append(el('p','c3-note c3-opp-state','Bu müşteri için çapraz satış adayı yok.'));}
  (o.items||[]).forEach(it=>{
    const box=el('div','c3-opp'); box.dataset.target=it.target_group;
    const h=el('div','c3-opp-h'); h.append(el('span','c3-opp-tag','Çapraz satış adayı'), el('b','c3-opp-t',it.target_group));
    box.append(h);
    box.append(kv('En güçlü kaynak grup',it.source_group));
    box.append(kv('Birlikte görülme oranı',fmtPct(it.confidence)+' ('+fmtNum(it.pair_buyers)+' / '+fmtNum(it.source_buyers)+' aktif müşteri)'));
    box.append(kv('Lift',fmtNum(it.lift)));
    if((it.supporting_sources||[]).length) box.append(kv('Diğer kaynak gruplar',it.supporting_sources.join(', ')));
    const nv=el('p','c3-note c3-opp-never','Bu müşteri bu gruptan satış geçmişinde hiç alım yapmadı.'); nv.dataset.never='1'; box.append(nv);
    c.append(box);
  });
  (o.notes||[]).forEach(t=>c.append(el('p','c3-note',t)));
  return c;
}
function receivablesCard(r){
  const c=card('Cari hesap'); c.dataset.sec='receivables';
  c.append(el('p','c3-note','Sipariş müşterisi (Pro_kodu) ile fatura hesabı (Musterikod) ayrı kavramlardır. Cari bakiye fatura hesabı düzeyindedir.'));
  (r.accounts||[]).forEach(a=>{
    const box=el('div','c3-acc'); box.dataset.account=a.account_code;
    box.append(kv('Fatura hesabı',a.account_code+(a.account_name?' · '+a.account_name:'')));
    box.append(kv('Bu hesaptan sipariş',fmtNum(a.orders)+' · '+fmtMoney(a.net_order_amount)));
    if(!a.aging||!a.aging.available) box.append(kv('Cari bakiye','cari dosyası yok','unavailable'));
    else if(!a.aging.present_in_file) box.append(kv('Cari bakiye','cari dosyasında hesap yok',a.aging.confidence));
    else{const b=a.aging.balance; box.dataset.cls=a.aging.account_class||'';
      box.append(kv('Hesap türü',a.aging.class_label||'—'));
      box.append(kv('Cari bakiye (işaretli)',b==null?'bilinmiyor':fmtMoneyExact(b),b==null?'unavailable':a.aging.confidence));
      box.append(kv('Bakiye yönü',a.aging.direction_label||'—'));
      box.append(kv('En eski fatura ayı (vade değil)',a.aging.oldest_bucket||'—'));
      box.append(kv('Vadesi geçen bakiye','bilinmiyor — ödeme koşulları raporu yok','unavailable'));}
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
function fairCampaigns(c,list){   // M7-4: read-only fair-campaign membership (edited in Fuar Merkezi)
  if(!list||!list.length) return;
  const box=el('div','c3-fair'); box.dataset.fair='1';
  box.append(el('p','c3-note','Fuar kampanyaları (Fuar Merkezi\'nde düzenlenir)'));
  c.append(box, table([['Kampanya'],['Durum'],['Aşama'],['Sonraki adım',0,1],['Not'],['ERP']],list.map(x=>[x.name+' · '+x.edition_year,
    ({planned:'Planlandı',active:'Aktif',archived:'Arşiv'})[x.status]||x.status,x.stage_label,fmtDate(x.next_action_date),x.has_note?'var':'—',
    x.erp_evidence?(x.erp_evidence+' ('+fmtNum(x.erp_fuar_orders)+')'):(x.linked_event?'bağlı fuarda sipariş yok':'ERP fuarı bağlı değil')])));
}
function eventsCard(e){
  const c=card('Fuar / etkinlik geçmişi'); c.dataset.sec='events';
  fairCampaigns(c,e.campaigns);
  if(e.status!=='ok'){c.append(el('p','c3-note','Sipariş kaynağı verisi okunamadı.')); return c;}
  const ty=el('div','c3-types'); const ob=e.orders_by_type||{}, de=e.distinct_events_by_type||{};
  TYPES.forEach(([k,l])=>{const t=el('span','c3-type',l+': '+fmtNum(ob[k]||0)+' sipariş'+(k!=='general'?' · '+fmtNum(de[k]||0)+' etkinlik':'')); t.dataset.type=k; ty.append(t);});
  c.append(ty, badge(e.confidence));
  c.append(table([['Tarih',0,1],['Tür'],['Dönem yılı',1],['Etkinlik']],(e.recent||[]).map(r=>[fmtDate(r.order_date),
    (TYPES.find(t=>t[0]===r.source_type)||[0,r.source_type])[1],r.edition_year==null?'—':String(r.edition_year),r.event_name||'—'])));
  c.append(el('p','c3-note',e.note||''));
  return c;
}
function crmCard(m,code){   // M7-5: read-only CRM summary; explicit writes happen in the Takip / CRM tab
  const c=card('CRM / Takip'); c.dataset.sec='crm';
  const go=(label,cls)=>{const b=el('button','c3-crmgo '+cls,label); b.type='button';
    b.addEventListener('click',()=>{if(window.StockAgentCRM&&typeof window.StockAgentCRM.open==='function') window.StockAgentCRM.open(code);}); return b;};
  if(!m){c.append(el('p','c3-note','CRM bilgisi şu anda kullanılamıyor.')); return c;}
  const w=el('p','c3-note c3-crmcov',(m.coverage_label||'Eksik kayıt olabilir')+': '+(m.coverage_note||'')); w.dataset.cov=m.coverage_status||''; c.append(w);
  (m.warnings||[]).forEach(x=>c.append(el('p','c3-note',x.message)));
  const li=m.last_interaction, fu=m.followup;
  c.append(kv('Son yapılandırılmış temas',li?fmtDate(li.occurred_on)+' · '+(li.channel_label||''):'kayıt yok (bilinmiyor)'));
  c.append(kv('Son sonuç',li&&li.outcome?li.outcome:'—'));
  c.append(kv('Sonraki iş',fu&&fu.next_action?fu.next_action:'—'));
  c.append(kv('Sonraki takip',fu?fmtDate(fu.due_date)+' · '+(fu.purpose_label||''):'—'));
  if(fu&&fu.owner_label) c.append(kv('Sorumlu etiketi (elle)',fu.owner_label));
  const ul=el('ul','c3-list'); (m.interactions||[]).slice(0,5).forEach(i=>ul.append(el('li',null,fmtDate(i.occurred_on)+' · '+(i.channel_label||'')+(i.outcome?' · '+i.outcome:''))));
  if((m.interactions||[]).length) c.append(el('p','c3-note','Son yapılandırılmış etkileşimler'),ul);
  if(m.legacy&&m.legacy.count) c.append(el('p','c3-note','Eski (yapılandırılmamış) notlar: '+fmtNum(m.legacy.count)+' — "Aksiyonlar ve geçmiş" kartında.'));
  const row=el('div','c3-crmacts'); row.append(go('Etkileşim kaydet','c3-crmlog'),go('Takip planla','c3-crmplan')); c.append(row);
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
// M7-2 deep link: Günlük Satış opens this tab with one exact customer (deterministic GET, no AI)
window.StockAgentC360={open:function(code){start(); if(code) openCustomer(String(code));}};
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


# ---------------- M7-2: Günlük Satış tab (deterministic customer-centric queue, server-driven) ----------------
# One tab button + one panel in markers (like the AI and Müşteri 360 tabs). The order, grouping, scores, primary
# reason and KAPALI rules all come from Python (GET /api/sales-queue); the page never re-sorts. Reason state uses the
# existing /api/actions/* endpoints (one state with the AI Aksiyon Merkezi). A message draft is generated only after
# an explicit click (POST /api/agent/draft-message, copy-only). All text is set with textContent.
_SQ_CSS=r"""
#sq .sq-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0 10px}
#sq .sq-date{font-size:12px;color:var(--muted);margin-left:auto}
#sq .sq-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0 12px}
#sq .sq-state.off,#sq .sq-state.err{border-color:#f3c4c4;background:#fdf3f3;color:#b42318;font-weight:600}
#sq .sq-state.wait,#sq .sq-state.empty{color:var(--muted)}
#sq .sq-filters{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
#sq .sq-f{border:1px solid var(--line);background:var(--card);color:var(--muted);font:inherit;font-size:12px;font-weight:600;padding:5px 11px;border-radius:999px;cursor:pointer}
#sq .sq-f.on{background:var(--ink);border-color:var(--ink);color:#fff}
#sq .sq-card{background:var(--card);border:1px solid var(--line);border-left:4px solid #9aa3ad;border-radius:10px;padding:11px 14px;margin:0 0 10px;min-width:0}
#sq .sq-card.pri-critical{border-left-color:var(--crit)}#sq .sq-card.pri-high{border-left-color:var(--low)}#sq .sq-card.pri-medium{border-left-color:var(--gold)}
#sq .sq-top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:11px}
#sq .sq-pri{font-weight:800;letter-spacing:.06em;color:#6b7480}
#sq .pri-critical .sq-pri{color:var(--crit)}#sq .pri-high .sq-pri{color:var(--low)}#sq .pri-medium .sq-pri{color:var(--gold)}
#sq .sq-score{font-family:"SF Mono",Menlo,Consolas,monospace;color:var(--muted);white-space:nowrap}
#sq .sq-kapali{font-size:10px;font-weight:800;letter-spacing:.04em;color:#fff;background:var(--crit);border-radius:4px;padding:1px 6px}
#sq .sq-badge{font-size:10px;font-weight:700;border-radius:4px;padding:1px 6px;color:#92660a;background:#fef6e7;border:1px solid #f5d9a8}
#sq .sq-name{font-size:16px;font-weight:800;letter-spacing:-.01em;margin:6px 0 1px}
#sq .sq-sub{font-size:12px;color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}
#sq .sq-code{font-family:"SF Mono",Menlo,Consolas,monospace}
#sq .sq-cf{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:7px 10px;margin:8px 0 0;font-size:13px;font-weight:600}
#sq .sq-primary{margin:8px 0 4px;font-size:13.5px}
#sq .sq-chips{display:flex;gap:5px;flex-wrap:wrap;margin:6px 0 0}
#sq .sq-chip{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--muted)}
#sq .sq-acts{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:9px}
#sq .sq-btn{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:12px;font-weight:600;padding:5px 10px;border-radius:8px;cursor:pointer}
#sq .sq-btn:hover{border-color:#c3c8cf}#sq .sq-btn:disabled{opacity:.5;cursor:wait}
#sq .sq-btn.pri{background:var(--ink);border-color:var(--ink);color:#fff}
#sq .sq-reasons{margin:8px 0 0;border-top:1px solid var(--line)}
#sq .sq-r{padding:8px 0;border-bottom:1px solid var(--line)}
#sq .sq-r-h{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px}
#sq .sq-r-cat{font-weight:700}
#sq .sq-r-meta{color:var(--muted)}
#sq .sq-r-txt{font-size:13px;margin:3px 0}
#sq .sq-r-facts{font-size:12px;color:var(--muted);margin:2px 0 4px}
#sq .sq-deferf{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px;font-size:12px;color:var(--muted)}
#sq .sq-deferf input{border:1px solid var(--line);border-radius:6px;padding:3px 6px;font:inherit;color:var(--ink)}
#sq .sq-err{font-size:12px;color:#b42318;font-weight:600;margin:6px 0 0}
#sq .sq-draft{margin-top:9px;border:1px solid var(--line);border-radius:8px;padding:9px 11px;background:#fbfbfc}
#sq .sq-drow{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:12px;color:var(--muted)}
#sq .sq-drow select{border:1px solid var(--line);border-radius:6px;padding:4px 6px;font:inherit;font-size:12px;color:var(--ink);background:var(--card)}
#sq .sq-dmsg{width:100%;min-height:96px;border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit;font-size:13.5px;margin-top:8px}
#sq .sq-note{font-size:12px;color:var(--muted);margin:6px 0}
#sq .sq-undo{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:20;display:flex;gap:14px;align-items:center;background:var(--ink);color:#fff;border-radius:10px;padding:9px 10px 9px 16px;font-size:13px;max-width:calc(100% - 32px)}
#sq .sq-undo-b{background:transparent;border:1px solid rgba(255,255,255,.55);color:#fff;font:inherit;font-weight:700;padding:4px 11px;border-radius:7px;cursor:pointer;white-space:nowrap}
#sq [hidden]{display:none!important}
#sq .sq-name,#sq .sq-primary,#sq .sq-r-txt,#sq .sq-r-facts,#sq .sq-cf,#sq .sq-state,#sq .sq-sub{overflow-wrap:anywhere}
@media(max-width:600px){#sq .sq-date{margin-left:0;width:100%}#sq .sq-btn,#sq .sq-f{min-height:36px}
#sq .sq-undo{left:12px;right:12px;transform:none;max-width:none;justify-content:space-between}}
"""

_SQ_BODY="""
<h2 class="sec">Bugün kimi aramalıyım?</h2>
<p class="note">Her müşteri bir kez görünür; birden fazla gerekçesi varsa birlikte listelenir. Sıralama, skorlar ve gerekçeler Python'dandır (mevcut aksiyon kuralları). Tahsilat varsa önce tahsilat konuşulur. Yapay zekâ yalnızca "Taslak oluştur"a tıkladığınızda kullanılır.</p>
<div class="sq-bar"><span class="sq-date"></span></div>
<div class="sq-state" role="status" hidden></div>
<div class="sq-filters" hidden></div>
<div class="sq-list"></div>
<p class="sq-note sq-more" hidden></p>
<div class="sq-undo" role="status" aria-live="polite" hidden></div>
"""

# M8-CP1 (D-060): viewing needs no login; owner actions need the owner session. One shared script, loaded before the
# panels: it adds the session's CSRF token to same-origin state-changing requests (the panels' own fetch calls are
# unchanged), shows "Görüntüleme modu" / "Yönetici" in the header, and tells a viewer who tries an owner action that it
# needs the owner login. It reads no business data, stores nothing in the browser and never sees the session cookie
# (HttpOnly). Does nothing when the file is opened directly (file://).
_ACCESS_CSS = """
.acc-bar{margin:10px 0 0;font-size:13px;color:var(--muted);display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center}
.acc-bar a{color:#1f5fbf;font-weight:600}
.acc-bar button{font:inherit;font-weight:600;border:1px solid var(--line);background:var(--card);border-radius:8px;
 padding:5px 12px;cursor:pointer;min-height:32px}
.acc-msg{position:sticky;top:0;z-index:50;margin:10px 0;padding:12px 14px;border-radius:10px;background:var(--warn-bg);
 border:1px solid #f1d39b;color:var(--ink);font-weight:600;font-size:14px}
.acc-msg a{color:#1f5fbf;margin-left:6px}
.acc-bar[hidden],.acc-msg[hidden]{display:none}
"""
_ACCESS_JS = r"""
(function(){
if(location.protocol==='file:'||typeof window.fetch!=='function') return;
var nativeFetch=window.fetch.bind(window), sess=null, pending=null, msgTimer=null;
var TXT={viewer:'Görüntüleme modu — değişiklik yapmak için ',login:'Yönetici girişi',owner:'Yönetici girişi açık',
  finance:'Finans görüntüleme açık (salt okunur)',
  logout:'Çıkış yap',need:'Bu işlem yalnızca yönetici girişiyle yapılabilir. Görüntüleme için giriş gerekmez.',
  ownerOnly:'Bu işlem yalnızca yönetici girişiyle yapılabilir. Finans görüntüleme şifresi yalnızca finans bölümlerini salt okunur açar.',
  off:'Yönetici girişi bu sunucuda yapılandırılmadı; değişiklik yapılamaz.',
  csrf:'Oturum doğrulanamadı; sayfayı yenileyip tekrar deneyin.',viewonly:'Görüntüleme modu'};
function $(id){return document.getElementById(id);}
function el(t,txt){var e=document.createElement(t); if(txt!=null) e.textContent=txt; return e;}
function session(force){
  if(force){sess=null; pending=null;}
  if(sess) return Promise.resolve(sess);
  if(!pending) pending=nativeFetch('/api/auth/session',{headers:{'Accept':'application/json'},credentials:'same-origin'})
    .then(function(r){return r&&r.ok?r.json():{authenticated:false};}).catch(function(){return {authenticated:false};})
    .then(function(s){sess=s||{authenticated:false}; pending=null; paint(); return sess;});
  return pending;}
function urlOf(i){return typeof i==='string'?i:(i&&i.url)||String(i);}
function sameOrigin(u){try{return new URL(u,location.href).origin===location.origin;}catch(e){return false;}}
function withToken(init,tok){var h=init.headers;
  if(h&&typeof Headers!=='undefined'&&h instanceof Headers){h=new Headers(h); h.set('X-CSRF-Token',tok);}
  else{h=Object.assign({},h||{},{'X-CSRF-Token':tok});}
  return Object.assign({},init,{headers:h,credentials:'same-origin'});}
function watch(r){
  if(r&&(r.status===401||r.status===403)&&typeof r.clone==='function'){
    try{r.clone().json().then(function(b){var c=b&&b.detail&&b.detail.error_code;
      if(c==='auth_required'||c==='owner_required'||c==='admin_not_configured'||c==='csrf_failed'||c==='origin_rejected'){session(true); notify(c);}
    }).catch(function(){});}catch(e){}}
  return r;}
window.fetch=function(input,init){
  init=init||{}; var m=String(init.method||(input&&input.method)||'GET').toUpperCase(), u=urlOf(input);
  if(m==='GET'||m==='HEAD'||!sameOrigin(u)) return nativeFetch(input,init).then(watch);
  return session().then(function(s){return nativeFetch(input,s&&s.csrf_token?withToken(init,s.csrf_token):init);}).then(watch);
};
function notify(code){var box=$('halsa-access-msg'); if(!box) return;
  box.replaceChildren(el('span',code==='admin_not_configured'?TXT.off:(code==='auth_required'?TXT.need:
    (code==='owner_required'?TXT.ownerOnly:TXT.csrf))));
  if(code==='auth_required'||code==='owner_required'){var a=el('a',TXT.login); a.href='/giris'; box.append(a);}
  box.hidden=false; clearTimeout(msgTimer); msgTimer=setTimeout(function(){box.hidden=true;},12000);}
function paint(){var bar=$('halsa-access'); if(!bar||!sess) return; bar.replaceChildren(); bar.hidden=false;
  var role=sess.role||(sess.authenticated?'owner':null);
  bar.dataset.mode=role||'viewer';
  if(role){bar.append(el('span',role==='owner'?'🔐 '+TXT.owner:'🔓 '+TXT.finance)); var b=el('button',TXT.logout); b.type='button';
    b.addEventListener('click',function(){b.disabled=true; window.fetch('/api/auth/logout',{method:'POST',
      headers:{'Content-Type':'application/json','X-StockAgent':'1'},body:'{}'}).then(function(){location.reload();},
      function(){b.disabled=false;});}); bar.append(b);
    if(role==='finance'&&sess.configured){var o=el('a',TXT.login); o.href='/giris'; bar.append(o);}}
  else if(sess.configured){bar.append(el('span','👁 '+TXT.viewer)); var a=el('a',TXT.login); a.href='/giris'; bar.append(a);}
  else{bar.append(el('span','👁 '+TXT.viewonly));}}
window.StockAgentAccess={session:session};
// D-061 direct link to the financial section: https://<site>/#yonetim opens the Yönetim tab (password form if needed)
function deepLink(){if(location.hash==='#yonetim'){var t=document.querySelector('.tab[data-t="own"]'); if(t) t.click();}}
function ready(){session(); deepLink();}
if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',ready); else ready();
window.addEventListener('hashchange',deepLink);
})();
"""


_SQ_JS=r"""
(function(){
'use strict';
const root=document.getElementById('sq'), tabBtn=document.querySelector('.tab[data-t="sq"]');
if(!root||!tabBtn) return;
const POLL_MS=window.__SQ_POLL_MS||2000, POLL_MAX=window.__SQ_POLL_MAX||450, UNDO_MS=window.__SQ_UNDO_MS||5000, DEFER_DAYS=7;
const POST_HEADERS={'Content-Type':'application/json','X-StockAgent':'1'};   // local CSRF guard (D-020)
const MSG={
  file:'Günlük Satış için panoyu http://127.0.0.1:8000/ üzerinden açın.',
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  building:'Müşteri verisi hazırlanıyor…',
  slow:'Müşteri verisi hâlâ hazırlanıyor; biraz sonra sekmeyi yeniden açın.',
  fail:'Günlük Satış yüklenemedi',
  empty:'Bugün için açık satış görevi yok.',
  noneInFilter:'Bu filtrede müşteri yok.',
  stateFail:'Durum kaydedilemedi.',
  badDate:'Geçerli bir ileri tarih seçin.',
  complete:'Gerekçe tamamlandı.',
  dismiss:'Gerekçe gizlendi.',
  drafting:'Taslak hazırlanıyor…',
  draftFail:'Taslak oluşturulamadı.',
  aiOff:'AI kapalı (.env: AI_PROVIDER=openai); taslak oluşturulamaz.',
  copied:'Kopyalandı', copyFail:'Kopyalanamadı; metni seçip elle kopyalayın.',
  draftNote:'Yalnızca taslak: inceleyin, kopyalayın ve kendiniz gönderin.'
};
const PRI={critical:'KRİTİK',high:'YÜKSEK',medium:'ORTA',low:'DÜŞÜK'};
const CONF={high:'yüksek',medium:'orta',low:'düşük'};
const STATUS={open:'açık',deferred:'ertelendi',completed:'tamamlandı',ignored:'gizlendi'};
const CHANNELS=[['whatsapp','WhatsApp'],['email','E-posta']];
const PURPOSES={collection:'Tahsilat',offer:'Teklif',winback:'Geri kazanım',sales_followup:'Satış takibi',fair_invite:'Fuar daveti'};
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const st={started:false,filter:'all',data:null,busy:new Set(),err:{},open:new Set(),draft:{},undo:null};
const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function state(t,k){const n=$('.sq-state'); n.textContent=t||''; n.className='sq-state'+(k?' '+k:''); show(n,!!t);}
function fmtDate(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'—';}
function fmtScore(s){const n=Number(s); return Number.isFinite(n)?n.toLocaleString('tr-TR',{maximumFractionDigits:1}):'—';}
function btn(cls,label,fn){const b=el('button','sq-btn'+(cls?' '+cls:''),label); b.type='button'; b.addEventListener('click',fn); return b;}
async function post(path,body){
  try{const r=await fetch(path,{method:'POST',headers:POST_HEADERS,body:JSON.stringify(body||{})}); return r.ok?await r.json():null;}
  catch(e){return null;}
}

function start(){
  if(st.started) return; st.started=true;
  if(location.protocol==='file:'){state(MSG.file,'off'); return;}   // the API is same-origin only; no request
  load(0);
}
async function load(n){
  let r, body=null; const my=st.lseq=(st.lseq||0)+1;   // M7-6: a late response of an older filter never renders
  try{r=await fetch('/api/sales-queue?filter='+encodeURIComponent(st.filter)+'&limit=20',{headers:{'Accept':'application/json'}});
      try{body=await r.json();}catch(e){body=null;}}
  catch(e){if(my===st.lseq) state(MSG.offline,'off'); return;}
  if(my!==st.lseq) return;
  if(r.status===503){
    const d=(body&&body.detail)||{};
    if(d.state==='building'){state(n<POLL_MAX?MSG.building:MSG.slow,'wait'); if(n<POLL_MAX) setTimeout(()=>load(n+1),POLL_MS); return;}
    state(d.message||MSG.fail,'err'); return;
  }
  if(!r.ok||!body){state(MSG.fail+' (HTTP '+r.status+').','err'); return;}
  st.data=body; render();
}
function render(){
  const d=st.data; state('');
  $('.sq-date').textContent=d.as_of_data_date?'Veri: '+fmtDate(d.as_of_data_date):'';
  const fb=$('.sq-filters'); fb.replaceChildren();
  (d.filters||[]).forEach(f=>{const b=el('button','sq-f'+(st.filter===f.key?' on':''),f.label+' ('+((d.filter_counts||{})[f.key]||0)+')');
    b.type='button'; b.dataset.k=f.key; b.addEventListener('click',()=>{st.filter=f.key; load(0);}); fb.append(b);});
  show(fb,(d.filter_counts||{}).all>0);
  const list=$('.sq-list'); list.replaceChildren();
  const items=Array.isArray(d.items)?d.items:[];
  if(!items.length) state((d.filter_counts||{}).all?MSG.noneInFilter:MSG.empty,'empty');
  items.forEach(it=>list.append(card(it)));   // Python order, never re-sorted
  const more=$('.sq-more'); more.textContent=d.truncated?('İlk '+d.returned+' müşteri gösteriliyor ('+d.visible_customers+' içinden).'):''; show(more,!!d.truncated);
}
function card(it){
  const pri=PRI[it.priority]?it.priority:'low', c=el('article','sq-card pri-'+pri); c.dataset.code=it.customer_code;
  const top=el('div','sq-top'); top.append(el('span','sq-pri',PRI[it.priority]||it.priority), el('span','sq-score','skor '+fmtScore(it.queue_score)));
  if(it.closed===true) top.append(el('span','sq-kapali','KAPALI'));
  (it.warnings||[]).filter(w=>w.code!=='collection_account_scope').forEach(w=>{const b=el('span','sq-badge',w.code==='master_duplicate'?'çift ana kayıt':'ana kayıt yok'); b.title=w.message; top.append(b);});
  c.append(top, el('h3','sq-name',it.display_name));
  const sub=el('div','sq-sub'); sub.append(el('span','sq-code',it.customer_code), el('span',null,'son sipariş: '+fmtDate(it.last_order_date)));
  c.append(sub);
  const reasons=Array.isArray(it.reasons)?it.reasons:[], primary=reasons.find(r=>r.action_id===it.primary_action_id)||reasons[0];
  if(it.collection_first&&primary){const cf=el('div','sq-cf','Önce tahsilat: fatura hesabı '+(primary.account_code||'—')+' için tahsilat gerekçesi önce konuşulmalı; diğer satış konuları sonra.'); cf.dataset.cf='1'; c.append(cf);}
  if(primary){const p=el('p','sq-primary'); p.append(el('b',null,primary.category_label+': '),document.createTextNode(primary.reason||'')); c.append(p);}
  const chips=el('div','sq-chips'), counts={};
  reasons.forEach(r=>{counts[r.category_label]=(counts[r.category_label]||0)+1;});
  Object.keys(counts).forEach(k=>chips.append(el('span','sq-chip',k+(counts[k]>1?' ×'+counts[k]:''))));
  c.append(chips);
  const acts=el('div','sq-acts'), code=it.customer_code;
  acts.append(btn('sq-toggle','Gerekçeler ('+reasons.length+')',()=>{st.open.has(code)?st.open.delete(code):st.open.add(code); replace(code);}),
              btn('sq-c360','Müşteri 360',()=>openC360(code)));
  if(!it.draft||it.draft.available!==false)   // M7-5: no draft when no reason has a fitting purpose (CRM service / other)
    acts.append(btn('sq-drafto','Mesaj taslağı',()=>{const d=st.draft[code]; if(d) d.open=!d.open; else st.draft[code]={open:true,purpose:it.draft.default_purpose,channel:'whatsapp'}; replace(code);}));
  c.append(acts);
  if(st.open.has(code)){const box=el('div','sq-reasons'); reasons.forEach(r=>box.append(reasonRow(r,code))); c.append(box);}
  if(st.draft[code]&&st.draft[code].open) c.append(draftPanel(it));
  if(st.err[code]) c.append(el('p','sq-err',st.err[code]));
  return c;
}
function replace(code){const it=(st.data.items||[]).find(x=>x.customer_code===code), old=root.querySelector('.sq-card[data-code="'+CSS.escape(code)+'"]');
  if(it&&old) old.replaceWith(card(it));}
function reasonRow(r,code){
  const row=el('div','sq-r'); row.dataset.id=r.action_id; row.dataset.cat=r.category;
  const h=el('div','sq-r-h'); h.append(el('span','sq-r-cat',r.category_label),
    el('span','sq-r-meta',(r.scope==='invoice_account'?'fatura hesabı '+(r.account_code||''):'müşteri')+' · skor '+fmtScore(r.score)+' · güven: '+(CONF[r.confidence]||r.confidence||'—')+(r.status&&r.status!=='open'?' · '+(STATUS[r.status]||r.status):'')));
  row.append(h, el('p','sq-r-txt',r.reason||''));
  if(Array.isArray(r.facts)&&r.facts.length) row.append(el('p','sq-r-facts',r.facts.join(' · ')));
  const a=el('div','sq-acts'), busy=st.busy.has(r.action_id);
  const b1=btn('sq-done','✓ Yapıldı',()=>mutate(r.action_id,'complete',null,code)), b2=btn('sq-defer','⏸ Ertele',()=>toggleDefer(row,r.action_id,code)),
        b3=btn('sq-hide','✕ Gizle',()=>mutate(r.action_id,'dismiss',null,code));
  [b1,b2,b3].forEach(b=>{b.disabled=busy;}); a.append(b1,b2,b3); row.append(a);
  return row;
}
function isoLocal(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function inDays(n){const d=new Date(); d.setDate(d.getDate()+n); return isoLocal(d);}
function toggleDefer(row,id,code){
  const open=row.querySelector('.sq-deferf'); if(open){open.remove(); return;}
  const f=el('div','sq-deferf'), inp=el('input'), msg=el('span','sq-err');
  inp.type='date'; inp.min=inDays(1); inp.value=inDays(DEFER_DAYS); inp.setAttribute('aria-label','Erteleme tarihi'); show(msg,false);
  f.append(el('span',null,'Ne zamana?'),inp,btn('sq-defer-ok','Ertele',()=>{const v=inp.value;
    if(!/^\d{4}-\d{2}-\d{2}$/.test(v)||v<inp.min){msg.textContent=MSG.badDate; show(msg,true); return;} mutate(id,'defer',v,code);}),
    btn('sq-defer-no','Vazgeç',()=>f.remove()),msg);
  row.append(f);
}
async function mutate(id,kind,until,code){
  if(st.busy.has(id)) return; st.busy.add(id); delete st.err[code];
  const res=await post('/api/actions/'+encodeURIComponent(id)+'/'+kind,kind==='defer'?{until:until}:{});
  st.busy.delete(id);
  if(!res){st.err[code]=MSG.stateFail; replace(code); return;}   // not confirmed: nothing changes but the error
  offerUndo(id,kind==='defer'?'Gerekçe '+fmtDate(res.defer_until||until)+' tarihine ertelendi.':MSG[kind]);
  load(0);   // the server recomputes visible reasons, primary reason, queue score and order
}
function clearUndo(){if(st.undo) clearTimeout(st.undo.timer); st.undo=null; const t=$('.sq-undo'); t.replaceChildren(); show(t,false);}
function offerUndo(id,text){
  clearUndo(); const t=$('.sq-undo');
  t.append(el('span','sq-undo-t',text),btn('sq-undo-b','Geri al',async()=>{clearUndo(); const r=await post('/api/actions/'+encodeURIComponent(id)+'/reopen',{});
    if(!r) state(MSG.stateFail,'err'); load(0);}));
  t.querySelector('.sq-undo-b').className='sq-undo-b'; show(t,true);
  st.undo={id:id,timer:setTimeout(clearUndo,UNDO_MS)};
}
function openC360(code){   // the existing Müşteri 360 tab, loaded with this exact customer
  const t=document.querySelector('.tab[data-t="c360"]'); if(t) t.click();
  if(window.StockAgentC360&&typeof window.StockAgentC360.open==='function') window.StockAgentC360.open(code);
}
function draftPanel(it){
  const code=it.customer_code, d=st.draft[code], box=el('div','sq-draft'), row=el('div','sq-drow');
  const sel=(opts,val,fn)=>{const s=el('select'); opts.forEach(([k,l])=>{const o=el('option',null,l); o.value=k; s.append(o);}); s.value=val; s.addEventListener('change',()=>fn(s.value)); return s;};
  const purposes=(it.draft.allowed_purposes||['collection']).map(p=>[p,PURPOSES[p]||p]);
  const go=btn('pri sq-dgo','Taslak oluştur',()=>makeDraft(it)); go.disabled=!!d.busy;
  row.append(el('span',null,'Amaç'),sel(purposes,d.purpose,v=>{d.purpose=v;}),el('span',null,'Kanal'),sel(CHANNELS,d.channel,v=>{d.channel=v;}),go);
  box.append(row);
  if(it.closed===true) box.append(el('p','sq-note','KAPALI müşteri: yalnızca tahsilat taslağı.'));
  if(d.busy) box.append(el('p','sq-note',MSG.drafting));
  if(d.err) box.append(el('p','sq-err',d.err));
  if(d.text!=null){const ta=el('textarea','sq-dmsg'); ta.value=d.text; ta.setAttribute('aria-label','Taslak metni'); ta.addEventListener('input',()=>{d.text=ta.value;});
    box.append(ta); const f=el('div','sq-drow'); f.append(btn('sq-dcopy','Kopyala',()=>copy(code)));
    if(d.copy) f.append(el('span',null,d.copy==='ok'?MSG.copied:MSG.copyFail)); f.append(el('span',null,MSG.draftNote)); box.append(f);}
  return box;
}
async function makeDraft(it){
  const code=it.customer_code, d=st.draft[code]; if(d.busy) return;
  d.busy=true; d.err=null; d.text=null; d.copy=null; replace(code);
  const target=(d.purpose==='collection'&&it.draft.collection_target)?it.draft.collection_target:it.draft.sales_target;
  const res=await post('/api/agent/draft-message',{customer_code:target,channel:d.channel,purpose:d.purpose});
  d.busy=false;
  if(!res) d.err=MSG.draftFail;
  else if(!res.available) d.err=(res.ai_error_category==='disabled'||res.ai_error_category==='missing_key')?MSG.aiOff:(res.reason||MSG.draftFail);
  else d.text=String(res.message||'');
  replace(code);
}
async function copy(code){const d=st.draft[code]; let ok=false;
  try{if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(d.text); ok=true;}}catch(e){ok=false;}
  d.copy=ok?'ok':'fail'; replace(code);}

tabBtn.addEventListener('click',start);   // lazy: nothing is fetched until the tab is opened
// M7-3 deep link: Fırsatlar opens this tab on one server-side filter (e.g. cross_sell). Deterministic GET, no AI.
window.StockAgentSQ={show:function(f){const was=st.started; st.filter=String(f||'all'); tabBtn.click(); if(was&&location.protocol!=='file:') load(0);}};
})();
"""

def _sq_panel(meta):
    """(tab button, panel block) for Günlük Satış, wrapped in markers like the other server-driven tabs."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="sq">Günlük Satış</button>'
    block=('\n<!--SQ-TAB-START-->\n<style>'+_SQ_CSS+'</style>\n'
           f'<div id="sq" class="panel" data-asof="{asof}">'+_SQ_BODY+'</div>\n'
           '<script>'+_SQ_JS+'</script>\n<!--SQ-TAB-END-->')
    return btn,block


# ---------------- M7-3: Fırsatlar tab (read-only opportunity intelligence, server-driven) ----------------
# Cross-sell model summary, strongest group relationships and descriptive (unverified) Hedef territory counts, all from
# GET /api/opportunities/*. No customer list here (that is Günlük Satış); no AI; all text via textContent.
_OPP_CSS=r"""
#opp .op-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0 12px}
#opp .op-state.off,#opp .op-state.err{border-color:#f3c4c4;background:#fdf3f3;color:#b42318;font-weight:600}
#opp .op-sec{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 12px;min-width:0}
#opp .op-sec h3{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin:0 0 8px}
#opp .op-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));gap:8px;margin:0 0 10px}
#opp .op-tile{border:1px solid var(--line);border-radius:8px;padding:8px 10px;min-width:0}
#opp .op-tile .n{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
#opp .op-tile .l{font-size:12px;color:var(--muted)}
#opp .op-th,#opp .op-note,#opp .op-cov{font-size:12px;color:var(--muted);margin:6px 0}
#opp .op-btn{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:12px;font-weight:600;padding:6px 11px;border-radius:8px;cursor:pointer}
#opp .op-btn.pri{background:var(--ink);border-color:var(--ink);color:#fff}
#opp .op-btn.on{background:var(--ink);border-color:var(--ink);color:#fff}
#opp .op-dims{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 8px}
#opp .op-badge{font-size:10px;font-weight:700;border-radius:4px;padding:1px 6px;color:#92660a;background:#fef6e7;border:1px solid #f5d9a8}
#opp .op-warn{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:7px 10px;font-size:13px;margin:0 0 8px}
#opp .op-scroll{overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}
#opp table.op-t{border-collapse:collapse;width:100%;font-size:13px}
#opp table.op-t th,#opp table.op-t td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
#opp table.op-t td.r,#opp table.op-t th.r{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
#opp table.op-t td.t{min-width:140px;overflow-wrap:break-word}
#opp [hidden]{display:none!important}
@media(max-width:600px){#opp .op-btn{min-height:36px}}
"""

_OPP_BODY="""
<h2 class="sec">Fırsatlar</h2>
<p class="note">Salt okunur fırsat görünümü. Çapraz satış, satış raporundaki ana grup (anagrup) düzeyinde ve yalnızca güvenilir satış geçmişinden hesaplanır; belirli ürün, stok veya ciro tahmini içermez. Yapay zekâ kullanılmaz.</p>
<div class="op-state" role="status" hidden></div>
<section class="op-sec" data-sec="model" hidden><h3>Çapraz Satış Modeli</h3><div class="op-tiles"></div><p class="op-th"></p>
<button type="button" class="op-btn pri op-go">Günlük Satış'ta Çapraz Satış adaylarını göster</button><div class="op-notes"></div></section>
<section class="op-sec" data-sec="pairs" hidden><h3>En güçlü ürün-grubu ilişkileri</h3><p class="op-note op-pnote"></p><div class="op-scroll op-pairs"></div></section>
<section class="op-sec" data-sec="territory" hidden><h3>Bölge / sektör görünümü <span class="op-badge">Doğrulanmadı</span></h3>
<div class="op-dims"></div><p class="op-warn op-tnote"></p><p class="op-note op-tstate" hidden></p><div class="op-scroll op-terr"></div><p class="op-cov"></p></section>
"""

_OPP_JS=r"""
(function(){
'use strict';
const root=document.getElementById('opp'), tabBtn=document.querySelector('.tab[data-t="opp"]');
if(!root||!tabBtn) return;
const POLL_MS=window.__OPP_POLL_MS||2000, POLL_MAX=window.__OPP_POLL_MAX||450;
const MSG={file:'Fırsatlar için panoyu http://127.0.0.1:8000/ üzerinden açın.',
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  fail:'Fırsatlar yüklenemedi', building:'Müşteri verisi hazırlanıyor…', slow:'Müşteri verisi hâlâ hazırlanıyor; biraz sonra sekmeyi yeniden açın.',
  model_off:'Çapraz satış modeli bu veriyle kurulamadı.'};
const DIMS=[['city','İl'],['district','İlçe'],['sector','Sektör kodu']];
const st={started:false,dim:'city',seq:0};
const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function state(t,k){const n=$('.op-state'); n.textContent=t||''; n.className='op-state'+(k?' '+k:''); show(n,!!t);}
function num(v){return (v==null||!Number.isFinite(Number(v)))?'—':Number(v).toLocaleString('tr-TR',{maximumFractionDigits:2});}
function pct(v){return (v==null||!Number.isFinite(Number(v)))?'—':'%'+(Number(v)*100).toLocaleString('tr-TR',{maximumFractionDigits:1});}
async function getJSON(url){const r=await fetch(url,{headers:{'Accept':'application/json'}}); let b=null; try{b=await r.json();}catch(e){b=null;}
  if(!r.ok){const e=new Error('HTTP '+r.status); e.status=r.status; e.body=b; throw e;} return b;}
function table(cols,rows){   // cols: [label, cls]
  const t=el('table','op-t'), h=el('tr'); cols.forEach(([l,c])=>h.append(el('th',c||null,l)));
  const th=el('thead'); th.append(h); t.append(th); const tb=el('tbody');
  rows.forEach(r=>{const tr=el('tr'); r.forEach((v,i)=>tr.append(el('td',cols[i][1]||null,v))); tb.append(tr);}); t.append(tb); return t;}
function start(){
  if(st.started) return; st.started=true;
  if(location.protocol==='file:'){state(MSG.file,'off'); return;}   // same-origin API only; no request
  loadSummary(); loadTerritory(0);
}
async function loadSummary(){
  let s;
  try{s=await getJSON('/api/opportunities/summary?limit=20');}
  catch(e){state(e.status?((e.body&&e.body.detail&&e.body.detail.message)||(MSG.fail+' (HTTP '+e.status+').')):MSG.offline, e.status?'err':'off'); return;}
  renderSummary(s);
}
function renderSummary(s){
  const sec=$('[data-sec="model"]'), tiles=$('.op-tiles'); tiles.replaceChildren(); show(sec,true);
  if(s.status!=='ok'){tiles.append(el('p','op-note',MSG.model_off)); return;}
  const tile=(n,l,k)=>{const d=el('div','op-tile'); d.dataset.k=k; d.append(el('div','n',num(n)),el('div','l',l)); tiles.append(d);};
  const th=s.thresholds||{};
  tile(s.active_customers,'Aktif müşteri (son '+th.recent_days+' gün)','active'); tile(s.groups_in_model,'Modeldeki ana grup','groups');
  tile(s.eligible_pairs,'Uygun grup ilişkisi','pairs'); tile(s.customers_with_opportunities,'Fırsatı olan müşteri','customers');
  tile(s.opportunity_targets,'Toplam fırsat (hedef grup)','targets'); tile(s.queue_reservoir,'Günlük Satış havuzundaki çapraz satış gerekçesi','reservoir');
  const bp=s.targets_by_priority||{};
  $('.op-th').textContent='Eşikler (model kalitesi, finansal hedef değil): kaynak alıcı ≥ '+th.min_source_buyers+' · hedef alıcı ≥ '+th.min_target_buyers+
    ' · birlikte alan ≥ '+th.min_pair_buyers+' · oran ≥ '+pct(th.min_confidence)+' · lift ≥ '+num(th.min_lift)+' · müşteri başına en fazla '+th.max_targets_per_customer+
    ' hedef grup · skor = oran × 100, en fazla '+th.score_cap+' (orta: '+num(bp.medium)+', düşük: '+num(bp.low)+'). Sayılar KAPALI ve aksiyon durumu uygulanmadan öncedir.';
  const notes=$('.op-notes'); notes.replaceChildren(); (s.notes||[]).forEach(t=>notes.append(el('p','op-note',t)));
  const ps=$('[data-sec="pairs"]'); show(ps,true);
  $('.op-pnote').textContent='İlk '+s.pair_limit+' ilişki, oran ve lift sırasıyla'+(s.pairs_truncated?' (toplam '+s.eligible_pairs+').':'.')+' Müşteri bilgisi içermez.';
  const box=$('.op-pairs'); box.replaceChildren();
  box.append(table([['Kaynak grup','t'],['Hedef grup','t'],['Kaynak alıcı','r'],['Birlikte alan','r'],['Oran','r'],['Lift','r']],
    (s.pairs||[]).map(p=>[p.source_group,p.target_group,num(p.source_buyers),num(p.pair_buyers),pct(p.confidence),num(p.lift)])));
}
function dims(){
  const d=$('.op-dims'); d.replaceChildren();
  DIMS.forEach(([k,l])=>{const b=el('button','op-btn op-dim'+(st.dim===k?' on':''),l); b.type='button'; b.dataset.k=k;
    b.addEventListener('click',()=>{if(st.dim===k) return; st.dim=k; dims(); loadTerritory(0);}); d.append(b);});
}
async function loadTerritory(n){
  const sec=$('[data-sec="territory"]'); show(sec,true); dims(); const my=++st.seq, ts=$('.op-tstate');
  let t;
  try{t=await getJSON('/api/opportunities/territories?dimension='+encodeURIComponent(st.dim)+'&limit=50');}
  catch(e){if(my!==st.seq) return;
    const d=(e.body&&e.body.detail)||{};
    if(e.status===503&&d.state==='building'){ts.textContent=n<POLL_MAX?MSG.building:MSG.slow; show(ts,true);
      if(n<POLL_MAX) setTimeout(()=>{if(my===st.seq) loadTerritory(n+1);},POLL_MS); return;}
    ts.textContent=e.status?(d.message||MSG.fail):MSG.offline; show(ts,true); return;}
  if(my!==st.seq) return;
  renderTerritory(t);
}
function renderTerritory(t){
  const ts=$('.op-tstate'), box=$('.op-terr'), cov=$('.op-cov'); box.replaceChildren(); cov.textContent='';
  $('.op-tnote').textContent=t.note||''; const sec=$('[data-sec="territory"]'); sec.dataset.status=t.status;
  if(t.status!=='ok'){ts.textContent=t.message||''; show(ts,true); return;}
  show(ts,false);
  box.append(table([[t.dimension_label+' (Hedef)','t'],['Eşleşen müşteri','r'],['Son 365 günde aktif','r'],['Bu yıl sipariş veren','r'],
    ['Çapraz satış adayı müşteri','r'],['Fırsat (hedef grup)','r']],
    (t.rows||[]).map(r=>[r.label,num(r.matched_customers),num(r.active_customers),num(r.current_year_customers),num(r.cross_sell_customers),num(r.cross_sell_targets)])));
  const c=t.coverage||{};
  cov.textContent='Hedef dosyası: '+(t.hedef_file||'—')+' · Hedef hesabı: '+num(c.hedef_accounts)+' · satış müşterisiyle eşleşen: '+num(c.matched_customers)+
    ' · bu alanı boş: '+num(c.missing_value_customers)+(t.truncated?' · ilk '+t.limit+' satır gösteriliyor (toplam '+t.total_rows+').':'');
}
$('.op-go').addEventListener('click',()=>{if(window.StockAgentSQ&&typeof window.StockAgentSQ.show==='function') window.StockAgentSQ.show('cross_sell');});
tabBtn.addEventListener('click',start);   // lazy: nothing is fetched until the tab is opened
})();
"""

def _opp_panel(meta):
    """(tab button, panel block) for Fırsatlar, wrapped in markers like the other server-driven tabs."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="opp">Fırsatlar</button>'
    block=('\n<!--OPP-TAB-START-->\n<style>'+_OPP_CSS+'</style>\n'
           f'<div id="opp" class="panel" data-asof="{asof}">'+_OPP_BODY+'</div>\n'
           '<script>'+_OPP_JS+'</script>\n<!--OPP-TAB-END-->')
    return btn,block


# ---------------- M7-4: Fuar Merkezi tab (manual fair campaigns + trusted historical FUAR facts) ----------------
# Campaigns, funnel stages, notes and next-action dates are local user state (SQLite, /api/fairs/*); historical
# editions are FUAR-tagged ERP orders only. Nothing is inferred or auto-enrolled; no AI; text via textContent only.
_FAIR_CSS=r"""
#fair .fm-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0 12px}
#fair .fm-state.off,#fair .fm-state.err{border-color:#f3c4c4;background:#fdf3f3;color:#b42318;font-weight:600}
#fair .fm-sec{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 12px;min-width:0}
#fair .fm-sec h3{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin:0 0 8px}
#fair .fm-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 8px}
#fair .fm-btn{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:12px;font-weight:600;padding:6px 11px;border-radius:8px;cursor:pointer}
#fair .fm-btn.pri,#fair .fm-btn.on{background:var(--ink);border-color:var(--ink);color:#fff}
#fair .fm-btn:disabled{opacity:.5;cursor:not-allowed}
#fair select,#fair input[type=text],#fair input[type=date],#fair input[type=number],#fair textarea{border:1px solid var(--line);border-radius:6px;padding:5px 7px;font:inherit;font-size:13px;color:var(--ink);background:var(--card);max-width:100%}
#fair textarea{width:100%;min-height:54px}
#fair .fm-add-q{flex:1 1 320px;min-width:0}#fair .fm-f-q,#fair .fm-c-q,#fair .fm-h-q{flex:1 1 180px;min-width:0}
#fair .fm-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:8px;margin:8px 0;border:1px dashed var(--line);border-radius:8px;padding:10px}
#fair .fm-form label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted)}
#fair .fm-note,#fair .fm-muted{font-size:12px;color:var(--muted);margin:4px 0}
#fair .fm-err{font-size:12px;color:#b42318;font-weight:600;margin:4px 0}
#fair .fm-head h4{font-size:17px;font-weight:800;margin:4px 0;overflow-wrap:anywhere}
#fair .fm-funnel{display:flex;gap:6px;flex-wrap:wrap}
#fair .fm-stage{border:1px solid var(--line);border-radius:8px;padding:6px 9px;background:var(--card);font:inherit;font-size:12px;cursor:pointer;text-align:left;color:var(--ink)}
#fair .fm-stage b{display:block;font-size:16px;font-variant-numeric:tabular-nums}
#fair .fm-stage.on{border-color:var(--ink);box-shadow:inset 0 0 0 1px var(--ink)}
#fair .fm-stage.term{color:var(--muted)}
#fair .fm-card{border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:0 0 8px;min-width:0}
#fair .fm-card.due{border-left:4px solid var(--gold)}
#fair .fm-name{font-weight:800;overflow-wrap:anywhere}
#fair .fm-code{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--muted)}
#fair .fm-chip{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted)}
#fair .fm-kapali{font-size:10px;font-weight:800;color:#fff;background:var(--crit);border-radius:4px;padding:1px 6px}
#fair .fm-warn{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:6px 9px;font-size:12px;margin:6px 0}
#fair .fm-erp{background:#eef6ff;border:1px solid #c7ddf7;color:#1d4ed8;border-radius:8px;padding:6px 9px;font-size:12px;margin:6px 0}
#fair .fm-scroll{overflow-x:auto;max-width:100%}
#fair table.fm-t{border-collapse:collapse;width:100%;font-size:13px}
#fair table.fm-t th,#fair table.fm-t td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
#fair table.fm-t td.r,#fair table.fm-t th.r{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
#fair table.fm-t td.t{min-width:140px;overflow-wrap:break-word}
#fair tr.fm-click{cursor:pointer}#fair tr.fm-click:hover td{background:#f7f8fa}
#fair .fm-hits{display:flex;flex-direction:column;gap:4px;margin:4px 0 8px}
#fair .fm-hit{display:flex;gap:8px;align-items:center;flex-wrap:wrap;border:1px solid var(--line);border-radius:8px;padding:6px 9px}
#fair .fm-toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:20;background:var(--ink);color:#fff;border-radius:10px;padding:9px 14px;font-size:13px;max-width:calc(100% - 32px)}
#fair [hidden]{display:none!important}
#fair h4,#fair .fm-name,#fair .fm-code,#fair .fm-note,#fair .fm-muted,#fair .fm-warn,#fair .fm-erp,#fair .fm-err,#fair .fm-hit{overflow-wrap:anywhere;min-width:0}
@media(max-width:600px){#fair .fm-btn,#fair .fm-stage{min-height:36px}#fair .fm-toast{left:12px;right:12px;transform:none;max-width:none}}
"""

_FAIR_BODY="""
<h2 class="sec">Fuar Merkezi</h2>
<p class="note">Yaklaşan fuar için elle yönetilen davet / takip listesi ve ERP'de FUAR etiketli geçmiş siparişler. Aşamalar sizin girdiğiniz durumdur; satış verisinden çıkarılmaz ve hiçbir müşteri otomatik eklenmez. Yapay zekâ yalnızca Günlük Satış'ta "Taslak oluştur"a tıkladığınızda kullanılır.</p>
<div class="fm-state" role="status" hidden></div>
<section class="fm-sec" data-sec="campaign" hidden><h3>Fuar kampanyası</h3>
<div class="fm-row"><select class="fm-camp-sel" aria-label="Kampanya"></select><button type="button" class="fm-btn pri fm-new">Yeni fuar oluştur</button></div>
<div class="fm-form" hidden>
<label>Fuar adı<input type="text" class="f-name" maxlength="120"></label>
<label>Dönem yılı<input type="number" class="f-year" min="2000" max="2099"></label>
<label>Başlangıç tarihi<input type="date" class="f-start"></label>
<label>Bitiş tarihi (isteğe bağlı)<input type="date" class="f-end"></label>
<label>ERP fuar kökü (isteğe bağlı, birebir)<input type="text" class="f-stem" maxlength="120"></label>
<label>Sıra no (isteğe bağlı)<input type="number" class="f-seq" min="1" max="999"></label>
<div class="fm-row"><button type="button" class="fm-btn pri f-save">Kaydet</button><button type="button" class="fm-btn f-cancel">Vazgeç</button></div>
<p class="fm-err f-err" hidden></p>
<p class="fm-note">ERP bağlantısı yalnızca dönem yılı + kök + sıra birebir eşleşirse kurulur; ada benzerlik kullanılmaz.</p>
</div>
<p class="fm-muted fm-empty" hidden>Henüz fuar kampanyası yok. "Yeni fuar oluştur" ile başlayın.</p>
<div class="fm-head"></div></section>
<section class="fm-sec" data-sec="funnel" hidden><h3>Huni</h3><div class="fm-funnel"></div><p class="fm-note fm-fnote"></p></section>
<section class="fm-sec" data-sec="customers" hidden><h3>Kampanya müşterileri</h3>
<div class="fm-row"><input type="text" class="fm-add-q" placeholder="Müşteri ekle: ad veya kod (Müşteri 360 araması)" aria-label="Müşteri ara"></div>
<div class="fm-hits"></div>
<div class="fm-row"><select class="fm-f-stage" aria-label="Aşama"></select><select class="fm-f-seg" aria-label="Segment"></select>
<label class="fm-muted"><input type="checkbox" class="fm-f-due"> Yalnızca zamanı gelenler</label><input type="text" class="fm-f-q" placeholder="Listede ara" aria-label="Listede ara"></div>
<p class="fm-note fm-cnote"></p><div class="fm-clist"></div></section>
<section class="fm-sec" data-sec="candidates" hidden><h3>Aday keşfi (salt okunur)</h3>
<div class="fm-row"><select class="fm-c-seg" aria-label="Aday segmenti"></select><label class="fm-muted"><input type="checkbox" class="fm-c-ff"> Önce geçmiş fuar müşterileri</label>
<input type="text" class="fm-c-q" placeholder="Ad veya kod" aria-label="Aday ara"></div>
<p class="fm-note fm-c-count"></p><div class="fm-c-list"></div>
<div class="fm-row"><button type="button" class="fm-btn pri fm-c-add" disabled>Seçilenleri kampanyaya ekle</button></div><p class="fm-note fm-c-res"></p></section>
<section class="fm-sec" data-sec="history" hidden><h3>Geçmiş fuarlar (ERP, yalnızca FUAR etiketli siparişler)</h3>
<div class="fm-row"><select class="fm-h-year" aria-label="Yıl"></select><input type="text" class="fm-h-q" placeholder="Fuar etiketi ara" aria-label="Fuar ara"></div>
<div class="fm-h-notes"></div><div class="fm-scroll fm-h-list"></div><div class="fm-h-detail"></div></section>
<div class="fm-toast" role="status" aria-live="polite" hidden></div>
"""

_FAIR_JS=r"""
(function(){
'use strict';
const root=document.getElementById('fair'), tabBtn=document.querySelector('.tab[data-t="fair"]');
if(!root||!tabBtn) return;
const POLL_MS=window.__FAIR_POLL_MS||2000, POLL_MAX=window.__FAIR_POLL_MAX||450, BULK_MAX=200;
const H={'Content-Type':'application/json','X-StockAgent':'1'};   // local CSRF guard (D-020)
const MSG={file:'Fuar Merkezi için panoyu http://127.0.0.1:8000/ üzerinden açın.',
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  building:'Müşteri verisi hazırlanıyor…', fail:'Yüklenemedi', erp:"ERP'de bu fuara atfedilmiş sipariş bulundu.",
  noCamp:'Önce bir kampanya seçin veya oluşturun.', archived:'Arşivlenmiş kampanya: yalnızca görüntüleme.'};
const STATUS={planned:'Planlandı',active:'Aktif',archived:'Arşiv'};
const SEG={historical_fuar:'Geçmiş fuar müşterisi',active:'Son 365 günde aktif',winback:'Geri Kazanım',real_decline:'Reel Satış Düşüşü',cross_sell:'Çapraz Satış',collections:'Tahsilat hassasiyeti'};
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const st={started:false,camps:[],stages:[],sel:null,amountLabel:'Net Sipariş Tutarı (Nettutar)',editing:false,armArchive:false,
  f:{stage:'',seg:'',due:false,q:''},c:{seg:'historical_fuar',ff:false,q:'',picked:new Set()},h:{year:'',q:'',sel:null,picked:new Set()},seqs:{},timers:{}};
const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function state(t,k){const n=$('.fm-state'); n.textContent=t||''; n.className='fm-state'+(k?' '+k:''); show(n,!!t);}
function fmtDate(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'—';}
function num(v){return (v==null||!Number.isFinite(Number(v)))?'—':Number(v).toLocaleString('tr-TR',{maximumFractionDigits:2});}
function money(v){return (v==null||!Number.isFinite(Number(v)))?'—':'₺'+Number(v).toLocaleString('tr-TR',{maximumFractionDigits:0});}
function btn(cls,label,fn){const b=el('button','fm-btn'+(cls?' '+cls:''),label); b.type='button'; b.addEventListener('click',fn); return b;}
function toast(t){const n=$('.fm-toast'); n.textContent=t; show(n,true); clearTimeout(st.timers.toast); st.timers.toast=setTimeout(()=>show(n,false),5000);}
async function api(path,opt){
  let r; try{r=await fetch(path,opt||{headers:{'Accept':'application/json'}});}catch(e){const x=new Error('offline'); x.offline=true; throw x;}
  let b=null; try{b=await r.json();}catch(e){b=null;}
  if(!r.ok){const x=new Error('HTTP '+r.status); x.status=r.status; x.body=b; throw x;} return b;}
function errText(e){if(e.offline) return MSG.offline; const d=e.body&&e.body.detail;
  if(d&&typeof d==='object') return d.message||(MSG.fail+' (HTTP '+e.status+')');
  if(Array.isArray(d)) return 'Geçersiz giriş.'; return (typeof d==='string'&&d)||(MSG.fail+' (HTTP '+e.status+')');}
function table(cols,rows,onRow){const w=el('div','fm-scroll'), t=el('table','fm-t'), h=el('tr'); cols.forEach(([l,c])=>h.append(el('th',c||null,l)));
  const th=el('thead'); th.append(h); t.append(th); const tb=el('tbody');
  rows.forEach((r,i)=>{const tr=el('tr'); r.forEach((v,j)=>{const td=el('td',cols[j][1]||null); if(v instanceof Node) td.append(v); else td.textContent=v==null?'—':String(v); tr.append(td);});
    if(onRow){tr.className='fm-click'; tr.addEventListener('click',ev=>{if(ev.target.closest('button,input')) return; onRow(i);});} tb.append(tr);});
  t.append(tb); w.append(t); return w;}
function camp(){return st.camps.find(c=>c.campaign_id===st.sel)||null;}
function openC360(code){const t=document.querySelector('.tab[data-t="c360"]'); if(t) t.click();
  if(window.StockAgentC360&&typeof window.StockAgentC360.open==='function') window.StockAgentC360.open(code);}

function start(){
  if(st.started) return; st.started=true;
  if(location.protocol==='file:'){state(MSG.file,'off'); return;}   // same-origin API only; no request
  loadCampaigns(); loadHistory();
}
// ---------------- campaigns ----------------
async function loadCampaigns(keep){
  let r; try{r=await api('/api/fairs/campaigns');}catch(e){state(errText(e),e.offline?'off':'err'); return;}
  st.camps=r.items||[]; st.stages=r.stages||[]; st.amountLabel=r.amount_label||st.amountLabel;
  if(!keep||!camp()){const open=st.camps.find(c=>c.status!=='archived'); st.sel=(camp()&&keep)?st.sel:(open?open.campaign_id:(st.camps[0]||{}).campaign_id||null);}
  renderCampaign(); renderFunnel(); fillStageFilter(); loadCandidates(0); await loadCustomers(0);   // resolves once the list is drawn
}
function renderCampaign(){
  show($('[data-sec="campaign"]'),true);
  const sel=$('.fm-camp-sel'); sel.replaceChildren();
  st.camps.forEach(c=>{const o=el('option',null,c.name+' · '+c.edition_year+(c.status==='archived'?' (arşiv)':'')); o.value=String(c.campaign_id); sel.append(o);});
  show(sel,st.camps.length>0); if(st.sel) sel.value=String(st.sel);
  show($('.fm-empty'),!st.camps.length);
  const head=$('.fm-head'); head.replaceChildren(); const c=camp(); if(!c) return;
  const box=el('div','fm-headbox'); box.dataset.status=c.status;
  box.append(el('h4',null,c.name));
  const r1=el('div','fm-row'); r1.append(el('span','fm-chip',STATUS[c.status]||c.status), el('span','fm-muted','Dönem: '+c.edition_year+' · Fuar tarihi: '+fmtDate(c.start_date)+(c.end_date?' – '+fmtDate(c.end_date):'')));
  box.append(r1);
  const le=c.linked_event||{}, lp=el('p','fm-note fm-link');
  if(!le.configured) lp.textContent='ERP fuar kimliği tanımlı değil (isteğe bağlı).';
  else if(le.withheld) lp.textContent='Bağlı ERP fuarı: '+(le.message||'')+((le.reasons||[]).length?' '+le.reasons.join(' '):'');
  else if(!le.found) lp.textContent='Tanımlı ERP fuar kimliği satış verisinde henüz yok.';
  else lp.textContent='Bağlı ERP fuarı: '+(le.label||'')+' · '+num(le.orders)+' sipariş · '+num(le.customers)+' müşteri · '+st.amountLabel+': '+money(le.net_order_amount);
  box.append(lp);
  const acts=el('div','fm-row');
  if(c.status!=='archived'){
    acts.append(btn('fm-edit','Düzenle',()=>openForm(c)));
    acts.append(btn('fm-status',c.status==='active'?'Planlandı yap':'Aktif yap',()=>saveCampaign(c.campaign_id,{status:c.status==='active'?'planned':'active'})));
    acts.append(btn('fm-archive',st.armArchive?'Arşivlemeyi onayla':'Arşivle',()=>{if(!st.armArchive){st.armArchive=true; renderCampaign(); return;} st.armArchive=false; archive(c.campaign_id);}));
  }else acts.append(el('span','fm-muted',MSG.archived));
  box.append(acts); head.append(box);
}
function openForm(c){
  st.editing=c?c.campaign_id:'new'; const f=$('.fm-form'); show(f,true); show($('.f-err'),false);
  $('.f-name').value=c?c.name:''; $('.f-year').value=c?c.edition_year:''; $('.f-start').value=c?c.start_date:''; $('.f-end').value=c&&c.end_date?c.end_date:'';
  $('.f-stem').value=c&&c.event_stem?c.event_stem:''; $('.f-seq').value=c&&c.event_sequence?c.event_sequence:''; $('.f-name').focus();
}
function formBody(){
  const b={name:$('.f-name').value, edition_year:Number($('.f-year').value), start_date:$('.f-start').value||null};
  const end=$('.f-end').value, stem=$('.f-stem').value, seq=$('.f-seq').value;
  if(end) b.end_date=end; if(stem.trim()) b.event_stem=stem; if(seq) b.event_sequence=Number(seq); return b;
}
async function submitForm(){
  const e=$('.f-err'), b=formBody();
  if(!b.name.trim()||!b.edition_year||!b.start_date){e.textContent='Ad, dönem yılı ve başlangıç tarihi gerekli.'; show(e,true); return;}
  if(b.end_date&&b.end_date<b.start_date){e.textContent='Bitiş tarihi başlangıçtan önce olamaz.'; show(e,true); return;}
  try{
    let r;
    if(st.editing==='new') r=await api('/api/fairs/campaigns',{method:'POST',headers:H,body:JSON.stringify(b)});
    else{const c=camp(); if(!b.event_stem&&c&&c.event_stem){b.clear_event_link=true;} if(!b.end_date&&c&&c.end_date) b.clear_end_date=true;
      r=await api('/api/fairs/campaigns/'+st.editing,{method:'PUT',headers:H,body:JSON.stringify(b)});}
    st.sel=r.campaign_id; st.editing=false; show($('.fm-form'),false); toast('Kampanya kaydedildi.'); loadCampaigns(true);
  }catch(x){e.textContent=errText(x); show(e,true);}
}
async function saveCampaign(id,b){try{await api('/api/fairs/campaigns/'+id,{method:'PUT',headers:H,body:JSON.stringify(b)}); loadCampaigns(true);}catch(x){toast(errText(x));}}
async function archive(id){try{await api('/api/fairs/campaigns/'+id+'/archive',{method:'POST',headers:H,body:'{}'}); toast('Kampanya arşivlendi.'); loadCampaigns(true);}catch(x){toast(errText(x));}}
// ---------------- funnel ----------------
function renderFunnel(){
  const c=camp(), sec=$('[data-sec="funnel"]'); show(sec,!!c); if(!c) return;
  const f=$('.fm-funnel'); f.replaceChildren(); const sc=c.stage_counts||{};
  st.stages.forEach(s=>{const b=el('button','fm-stage'+(s.terminal?' term':'')+(st.f.stage===s.key?' on':'')); b.type='button'; b.dataset.k=s.key;
    b.append(el('b',null,num(sc[s.key]||0)),document.createTextNode(s.label));
    b.addEventListener('click',()=>{st.f.stage=st.f.stage===s.key?'':s.key; $('.fm-f-stage').value=st.f.stage; renderFunnel(); loadCustomers(0);}); f.append(b);});
  $('.fm-fnote').textContent='Toplam '+num(c.total_customers)+' müşteri · zamanı gelen / gecikmiş görev: '+num(c.due_tasks)+'. Sayılar yalnızca girilen aşamalardan; satış verisinden çıkarılmaz.';
}
function fillStageFilter(){
  const s=$('.fm-f-stage'); s.replaceChildren(); const a=el('option',null,'Tüm aşamalar'); a.value=''; s.append(a);
  st.stages.forEach(x=>{const o=el('option',null,x.label); o.value=x.key; s.append(o);}); s.value=st.f.stage;
  const g=$('.fm-f-seg'); g.replaceChildren(); const b=el('option',null,'Tüm segmentler'); b.value=''; g.append(b);
  Object.keys(SEG).forEach(k=>{const o=el('option',null,SEG[k]); o.value=k; g.append(o);}); g.value=st.f.seg;
}
// ---------------- campaign customers ----------------
function waitMsg(box,n,fn,key){box.replaceChildren(el('p','fm-muted',n<POLL_MAX?MSG.building:'Müşteri verisi hâlâ hazırlanıyor.'));
  if(n<POLL_MAX){clearTimeout(st.timers[key]); st.timers[key]=setTimeout(()=>fn(n+1),POLL_MS);}}
async function loadCustomers(n){
  const c=camp(), sec=$('[data-sec="customers"]'); show(sec,!!c); if(!c) return;
  const box=$('.fm-clist'), my=st.seqs.cust=(st.seqs.cust||0)+1;
  const qs='stage='+encodeURIComponent(st.f.stage)+'&segment='+encodeURIComponent(st.f.seg)+'&due='+(st.f.due?'true':'false')+'&q='+encodeURIComponent(st.f.q)+'&limit=200';
  let r; try{r=await api('/api/fairs/campaigns/'+c.campaign_id+'/customers?'+qs);}
  catch(e){if(my!==st.seqs.cust) return; if(e.status===503&&e.body&&e.body.detail&&e.body.detail.state==='building'){waitMsg(box,n,loadCustomers,'cust'); return;}
    box.replaceChildren(el('p','fm-err',errText(e))); return;}
  if(my!==st.seqs.cust) return;
  $('.fm-cnote').textContent=(r.note||'')+' '+num(r.total)+' müşteri'+(r.truncated?' (ilk '+r.limit+')':'')+'.';
  box.replaceChildren(); (r.items||[]).forEach(it=>box.append(custCard(it,c)));
  if(!(r.items||[]).length) box.append(el('p','fm-muted','Bu filtrede müşteri yok.'));
  show($('.fm-add-q'),c.status!=='archived');
}
function custCard(it,c){
  const k=el('div','fm-card'+(it.due?' due':'')); k.dataset.code=it.customer_code; k.dataset.stage=it.stage;
  const top=el('div','fm-row'); top.append(el('span','fm-name',it.display_name), el('span','fm-code',it.customer_code));
  if(it.closed===true) top.append(el('span','fm-kapali','KAPALI'));
  (it.segments||[]).forEach(s=>top.append(el('span','fm-chip',SEG[s]||s)));
  k.append(top);
  (it.warnings||[]).forEach(w=>{const x=el('p','fm-warn',w.message); x.dataset.w=w.code; k.append(x);});
  if(it.collection_first){const x=el('p','fm-warn fm-cf','Tahsilat önce: tahsilat gerekçesi varsa önce o konuşulmalı.'); k.append(x);}
  if(it.erp_fuar_orders){const x=el('div','fm-erp'); x.dataset.erp='1'; x.append(document.createTextNode(MSG.erp+' ('+num(it.erp_fuar_orders)+' sipariş) '));
    if(it.stage!=='order'&&c.status!=='archived') x.append(btn('fm-mark','Aşamayı Sipariş olarak işaretle',()=>saveCust(c,it.customer_code,{stage:'order'})));
    k.append(x);}
  if(it.erp_mismatch==='stage_order_without_erp') k.append(el('p','fm-warn fm-mm','Aşama "Sipariş", ancak bağlı ERP fuarında bu müşteriye atfedilmiş sipariş yok. İkisi de gösteriliyor.'));
  const ro=c.status==='archived';
  const row=el('div','fm-row'), sel=el('select','fm-stage-sel'); sel.setAttribute('aria-label','Aşama');
  st.stages.forEach(s=>{const o=el('option',null,s.label); o.value=s.key; sel.append(o);}); sel.value=it.stage; sel.disabled=ro;
  const d=el('input','fm-next'); d.type='date'; d.value=it.next_action_date||''; d.setAttribute('aria-label','Sonraki adım tarihi'); d.disabled=ro;
  row.append(el('span','fm-muted','Aşama'),sel,el('span','fm-muted','Sonraki adım'),d);
  if(it.due) row.append(el('span','fm-chip fm-due','zamanı geldi'));
  k.append(row);
  const note=el('textarea','fm-notein'); note.maxLength=500; note.value=it.note||''; note.placeholder='Kısa yerel not (en fazla 500 karakter; yapay zekâya gönderilmez)'; note.disabled=ro; note.setAttribute('aria-label','Not');
  k.append(note);
  const acts=el('div','fm-row');
  if(!ro) acts.append(btn('pri fm-save','Kaydet',()=>{const b={stage:sel.value};
    if(d.value) b.next_action_date=d.value; else if(it.next_action_date) b.clear_next_action=true;
    if(note.value.trim()) b.note=note.value; else if(it.note) b.clear_note=true; saveCust(c,it.customer_code,b,k);}));
  acts.append(btn('fm-c360','Müşteri 360',()=>openC360(it.customer_code)));
  k.append(acts);
  const err=el('p','fm-err fm-cerr'); show(err,false); k.append(err);
  return k;
}
async function saveCust(c,code,b,card){
  try{await api('/api/fairs/campaigns/'+c.campaign_id+'/customers',{method:'PUT',headers:H,body:JSON.stringify({customer_code:code,...b})});
    toast('Kaydedildi.'); loadCampaigns(true);}
  catch(x){if(card){const e=card.querySelector('.fm-cerr'); e.textContent=errText(x); show(e,true);} else toast(errText(x));}
}
async function addCodes(codes,where){
  const c=camp(); if(!c||c.status==='archived'){toast(MSG.noCamp); return null;}
  try{const r=await api('/api/fairs/campaigns/'+c.campaign_id+'/customers',{method:'POST',headers:H,body:JSON.stringify({customer_codes:codes.slice(0,BULK_MAX)})});
    const n=r.counts||{}, t='Eklendi: '+num(n.added)+' · zaten listede: '+num(n.already_present)+' · KAPALI engellendi: '+num(n.blocked_kapali)+' · geçersiz: '+num(n.invalid);
    await loadCampaigns(true); toast(t); if(where) where.textContent=t; return r;}   // toast after the list is redrawn
  catch(x){toast(errText(x)); return null;}
}
async function searchAdd(q){
  const box=$('.fm-hits'), my=st.seqs.search=(st.seqs.search||0)+1;
  if(String(q).trim().length<2){box.replaceChildren(); return;}
  let r; try{r=await api('/api/customer360/search?q='+encodeURIComponent(q)+'&limit=8');}catch(e){if(my===st.seqs.search) box.replaceChildren(el('p','fm-err',errText(e))); return;}
  if(my!==st.seqs.search) return; box.replaceChildren();
  (r.results||[]).forEach(h=>{const d=el('div','fm-hit'); d.dataset.code=h.customer_code; d.append(el('span','fm-name',h.display_name),el('span','fm-code',h.customer_code));
    if(h.closed===true) d.append(el('span','fm-kapali','KAPALI'));
    d.append(btn('fm-addone','Ekle',()=>addCodes([h.customer_code]))); box.append(d);});
  if(!(r.results||[]).length) box.append(el('p','fm-muted','Sonuç yok.'));
}
// ---------------- candidates ----------------
async function loadCandidates(n){
  const sec=$('[data-sec="candidates"]'); show(sec,true);
  const seg=$('.fm-c-seg'); if(!seg.options.length){Object.keys(SEG).forEach(k=>{const o=el('option',null,SEG[k]); o.value=k; seg.append(o);}); seg.value=st.c.seg;}
  const c=camp(), box=$('.fm-c-list'), my=st.seqs.cand=(st.seqs.cand||0)+1;
  const qs='segment='+encodeURIComponent(st.c.seg)+'&q='+encodeURIComponent(st.c.q)+'&fuar_first='+(st.c.ff?'true':'false')+'&limit=50'+(c?'&campaign_id='+c.campaign_id:'');
  let r; try{r=await api('/api/fairs/candidates?'+qs);}
  catch(e){if(my!==st.seqs.cand) return; if(e.status===503&&e.body&&e.body.detail&&e.body.detail.state==='building'){waitMsg(box,n,loadCandidates,'cand'); return;}
    box.replaceChildren(el('p','fm-err',errText(e))); return;}
  if(my!==st.seqs.cand) return;
  st.c.picked.clear();
  $('.fm-c-count').textContent='Bu segmentte '+num(r.total)+' müşteri var'+(r.truncated?' (ilk '+r.limit+' gösteriliyor)':'')+'. KAPALI olduğu için gösterilmeyen: '+num(r.kapali_excluded)+'. '+(r.note||'');
  const rows=(r.items||[]).map(it=>{const cb=el('input'); cb.type='checkbox'; cb.className='fm-pick'; cb.dataset.code=it.customer_code; cb.setAttribute('aria-label','Seç: '+it.customer_code); cb.disabled=!c||c.status==='archived'||it.in_campaign;
    cb.addEventListener('change',()=>{cb.checked?st.c.picked.add(it.customer_code):st.c.picked.delete(it.customer_code); $('.fm-c-add').disabled=!st.c.picked.size;});
    const nm=el('span'); nm.append(el('div','fm-name',it.display_name),el('div','fm-code',it.customer_code));
    const tags=el('span'); (it.segments||[]).forEach(s=>tags.append(el('span','fm-chip',SEG[s]||s),document.createTextNode(' ')));
    if(it.collection_first) tags.append(el('span','fm-chip fm-cf','Tahsilat önce'));
    return [cb,nm,tags,it.queue_score==null?'—':num(it.queue_score),fmtDate(it.last_order_date),it.in_campaign?'listede':''];});
  box.replaceChildren(table([['',''],['Müşteri','t'],['Segmentler','t'],['Günlük Satış skoru','r'],['Son sipariş','r'],['Kampanya','']],rows));
  $('.fm-c-add').disabled=true; if(!c) $('.fm-c-res').textContent=MSG.noCamp;
}
// ---------------- history ----------------
async function loadHistory(){
  const sec=$('[data-sec="history"]'); show(sec,true);
  let r; try{r=await api('/api/fairs/history?limit=200&q='+encodeURIComponent(st.h.q)+(st.h.year?'&year='+st.h.year:''));}
  catch(e){$('.fm-h-list').replaceChildren(el('p','fm-err',errText(e))); return;}
  const ys=$('.fm-h-year'); if(!ys.options.length){const a=el('option',null,'Tüm yıllar'); a.value=''; ys.append(a); (r.years||[]).forEach(y=>{const o=el('option',null,String(y)); o.value=String(y); ys.append(o);});}
  const notes=$('.fm-h-notes'); notes.replaceChildren(); (r.notes||[]).concat([r.amount_note||'']).forEach(t=>notes.append(el('p','fm-note',t)));
  const items=r.items||[];
  $('.fm-h-list').replaceChildren(items.length?table([['Yıl','r'],['Fuar etiketi','t'],['Müşteri','r'],['Sipariş','r'],[r.amount_label,'r'],['Ürün','r']],
    items.map(x=>[String(x.edition_year),x.label,num(x.customers),num(x.orders),money(x.net_order_amount),num(x.products)]),i=>loadEdition(items[i].event_id)):el('p','fm-muted','FUAR etiketli sipariş yok.'));
  $('.fm-h-list').querySelectorAll('tbody tr').forEach((tr,i)=>{tr.dataset.event=items[i].event_id;});
}
async function loadEdition(id,n){
  st.h.sel=id; const box=$('.fm-h-detail'), c=camp(), my=st.seqs.ed=(st.seqs.ed||0)+1;
  let r; try{r=await api('/api/fairs/history/'+encodeURIComponent(id)+'?customer_limit=200'+(c?'&campaign_id='+c.campaign_id:''));}
  catch(e){if(my!==st.seqs.ed) return; if(e.status===503&&e.body&&e.body.detail&&e.body.detail.state==='building'){waitMsg(box,n||0,k=>loadEdition(id,k),'ed'); return;}
    box.replaceChildren(el('p','fm-err',errText(e))); return;}
  if(my!==st.seqs.ed) return;
  st.h.picked.clear(); box.replaceChildren(); box.dataset.event=id;
  box.append(el('h4',null,r.label+' · '+r.edition_year+(r.sequence?' · sıra '+r.sequence:'')));
  box.append(el('p','fm-note','Tarih aralığı: '+fmtDate(r.first_order_date)+' – '+fmtDate(r.last_order_date)+' · '+num(r.orders)+' sipariş belgesi · '+num(r.lines)+' satır · '+num(r.customers)+' müşteri · '+num(r.products)+' ürün · '+r.amount_label+': '+money(r.net_order_amount)));
  box.append(el('p','fm-note fm-dq','Veri kalitesi: tarihi kullanılamayan sipariş '+num(r.date_unusable_orders)+' (tarih aralığına alınmadı) · müşterisi tutarsız sipariş '+num(r.customer_conflict_orders)+' (müşteri listesine alınmadı) · tutarı boş satır '+num(r.missing_amount_lines)+'.'));
  const tops=(title,list)=>{box.append(el('p','fm-muted',title)); box.append(table([['Etiket','t'],[r.amount_label,'r'],['Miktar','r'],['Sipariş','r']],(list||[]).map(x=>[x.name?x.name+' ('+x.label+')':x.label,money(x.net_order_amount),num(x.quantity),num(x.orders)])));};
  tops('En çok ürünler',r.top_products); tops('Ana gruplar (anagrup)',r.top_groups); tops('Kategoriler',r.top_categories);
  box.append(el('p','fm-muted','Fuara atfedilmiş müşteriler — '+r.customer_sort+'. Bu müşterilerin bu fuara atfedilmiş siparişi var; katılım veya davet kaydı değildir.'));
  const rows=(r.customer_rows||[]).map(x=>{const cb=el('input'); cb.type='checkbox'; cb.className='fm-hpick'; cb.dataset.code=x.customer_code; cb.setAttribute('aria-label','Seç: '+x.customer_code);
    cb.disabled=!c||c.status==='archived'||x.in_campaign||x.closed===true;
    cb.addEventListener('change',()=>{cb.checked?st.h.picked.add(x.customer_code):st.h.picked.delete(x.customer_code); addB.disabled=!st.h.picked.size;});
    const nm=el('span'); nm.append(el('div','fm-name',x.display_name),el('div','fm-code',x.customer_code)); if(x.closed===true) nm.append(el('span','fm-kapali','KAPALI'));
    const b=btn('fm-c360','Müşteri 360',()=>openC360(x.customer_code));
    return [cb,nm,num(x.orders),money(x.net_order_amount),num(x.quantity),fmtDate(x.first_order_date)+' – '+fmtDate(x.last_order_date),b];});
  box.append(table([['',''],['Müşteri','t'],['Sipariş','r'],[r.amount_label,'r'],['Miktar','r'],['Tarih','r'],['','']],rows));
  const res=el('p','fm-note fm-hres');
  const addB=btn('pri fm-hadd','Seçilenleri kampanyaya ekle',async()=>{if(!st.h.picked.size) return; await addCodes([...st.h.picked],res); loadEdition(id);});
  addB.disabled=true; box.append(addB,res); if(!c) res.textContent=MSG.noCamp;
}
// ---------------- wiring ----------------
$('.fm-new').addEventListener('click',()=>openForm(null));
$('.f-save').addEventListener('click',submitForm);
$('.f-cancel').addEventListener('click',()=>{st.editing=false; show($('.fm-form'),false);});
$('.fm-camp-sel').addEventListener('change',e=>{st.sel=Number(e.target.value); st.armArchive=false; renderCampaign(); renderFunnel(); loadCustomers(0); loadCandidates(0); if(st.h.sel) loadEdition(st.h.sel);});
$('.fm-f-stage').addEventListener('change',e=>{st.f.stage=e.target.value; renderFunnel(); loadCustomers(0);});
$('.fm-f-seg').addEventListener('change',e=>{st.f.seg=e.target.value; loadCustomers(0);});
$('.fm-f-due').addEventListener('change',e=>{st.f.due=e.target.checked; loadCustomers(0);});
$('.fm-f-q').addEventListener('input',e=>{clearTimeout(st.timers.fq); st.timers.fq=setTimeout(()=>{st.f.q=e.target.value; loadCustomers(0);},250);});
$('.fm-add-q').addEventListener('input',e=>{clearTimeout(st.timers.aq); st.timers.aq=setTimeout(()=>searchAdd(e.target.value),250);});
$('.fm-c-seg').addEventListener('change',e=>{st.c.seg=e.target.value; loadCandidates(0);});
$('.fm-c-ff').addEventListener('change',e=>{st.c.ff=e.target.checked; loadCandidates(0);});
$('.fm-c-q').addEventListener('input',e=>{clearTimeout(st.timers.cq); st.timers.cq=setTimeout(()=>{st.c.q=e.target.value; loadCandidates(0);},250);});
$('.fm-c-add').addEventListener('click',async()=>{if(!st.c.picked.size) return; await addCodes([...st.c.picked],$('.fm-c-res')); loadCandidates(0);});
$('.fm-h-year').addEventListener('change',e=>{st.h.year=e.target.value; loadHistory();});
$('.fm-h-q').addEventListener('input',e=>{clearTimeout(st.timers.hq); st.timers.hq=setTimeout(()=>{st.h.q=e.target.value; loadHistory();},250);});
tabBtn.addEventListener('click',start);   // lazy: nothing is fetched until the tab is opened
})();
"""

def _fair_panel(meta):
    """(tab button, panel block) for Fuar Merkezi, wrapped in markers like the other server-driven tabs."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="fair">Fuar Merkezi</button>'
    block=('\n<!--FAIR-TAB-START-->\n<style>'+_FAIR_CSS+'</style>\n'
           f'<div id="fair" class="panel" data-asof="{asof}">'+_FAIR_BODY+'</div>\n'
           '<script>'+_FAIR_JS+'</script>\n<!--FAIR-TAB-END-->')
    return btn,block


# ---------------- M7-5: Takip / CRM tab (lightweight, explicit customer follow-up) ----------------
# Local CRM state via /api/crm/* (append-only interactions + one current follow-up per customer). Coverage is always
# shown as incomplete (D-024); no person/owner aggregation anywhere; nothing is inferred; no AI; textContent only.
_CRM_CSS=r"""
#crm .cr-cov{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:10px;padding:10px 13px;margin:6px 0 12px;font-size:13px}
#crm .cr-cov b{display:inline-block;margin-right:6px}
#crm .cr-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0 12px}
#crm .cr-state.off,#crm .cr-state.err{border-color:#f3c4c4;background:#fdf3f3;color:#b42318;font-weight:600}
#crm .cr-sec{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 12px;min-width:0}
#crm .cr-sec h3{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin:0 0 8px}
#crm .cr-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,140px),1fr));gap:8px}
#crm .cr-tile{border:1px solid var(--line);border-radius:8px;padding:7px 9px;min-width:0}
#crm .cr-tile .n{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
#crm .cr-tile .l{font-size:12px;color:var(--muted)}
#crm .cr-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 8px}
#crm .cr-btn{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:12px;font-weight:600;padding:6px 11px;border-radius:8px;cursor:pointer}
#crm .cr-btn.pri{background:var(--ink);border-color:var(--ink);color:#fff}
#crm .cr-btn:disabled{opacity:.5;cursor:not-allowed}
#crm select,#crm input[type=text],#crm input[type=date],#crm textarea{border:1px solid var(--line);border-radius:6px;padding:5px 7px;font:inherit;font-size:13px;color:var(--ink);background:var(--card);max-width:100%;min-width:0}
#crm textarea{width:100%;min-height:54px}
#crm .cr-q{flex:1 1 320px}
#crm .cr-card{border:1px solid var(--line);border-radius:10px;padding:9px 12px;margin:0 0 8px;min-width:0}
#crm .cr-card.overdue{border-left:4px solid var(--crit)}#crm .cr-card.today{border-left:4px solid var(--gold)}
#crm .cr-name{font-weight:800;overflow-wrap:anywhere}
#crm .cr-code{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12px;color:var(--muted)}
#crm .cr-chip{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted);white-space:nowrap}
#crm .cr-chip.overdue{color:#b42318;border-color:#f3c4c4}#crm .cr-chip.today{color:var(--warn);border-color:#f5d9a8}
#crm .cr-kapali{font-size:10px;font-weight:800;color:#fff;background:var(--crit);border-radius:4px;padding:1px 6px}
#crm .cr-warn{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:6px 9px;font-size:12px;margin:6px 0}
#crm .cr-note,#crm .cr-muted{font-size:12px;color:var(--muted);margin:4px 0}
#crm .cr-err{font-size:12px;color:#b42318;font-weight:600;margin:4px 0}
#crm .cr-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:8px;border:1px dashed var(--line);border-radius:8px;padding:10px;margin:8px 0}
#crm .cr-form label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted);min-width:0}
#crm .cr-form .wide{grid-column:1/-1}
#crm .cr-tl{list-style:none;padding:0;margin:0}
#crm .cr-tl li{border-bottom:1px solid var(--line);padding:7px 0;font-size:13px}
#crm .cr-hits{display:flex;flex-direction:column;gap:4px;margin:4px 0 8px}
#crm .cr-hit{display:flex;gap:8px;align-items:center;flex-wrap:wrap;border:1px solid var(--line);border-radius:8px;padding:6px 9px;background:var(--card);font:inherit;text-align:left;cursor:pointer;color:var(--ink)}
#crm .cr-toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:20;background:var(--ink);color:#fff;border-radius:10px;padding:9px 14px;font-size:13px;max-width:calc(100% - 32px)}
#crm [hidden]{display:none!important}
#crm h4,#crm .cr-note,#crm .cr-muted,#crm .cr-warn,#crm .cr-err,#crm .cr-tl li,#crm .cr-card,#crm .cr-cov{overflow-wrap:anywhere}
@media(max-width:600px){#crm .cr-btn{min-height:36px}#crm .cr-toast{left:12px;right:12px;transform:none;max-width:none}}
"""

_CRM_BODY="""
<h2 class="sec">Takip / CRM</h2>
<div class="cr-cov" role="note"><b>Eksik kayıt olabilir.</b><span class="cr-covtext">CRM kayıtları henüz tüm müşteri temaslarını kapsamıyor. Bu veriler çalışan performansı karşılaştırmak için kullanılmaz.</span></div>
<p class="note">Bir müşteriyle en son ne konuşuldu, sonuç ne, sıradaki iş ne ve ne zaman tekrar takip edilecek? Kayıtlar ve takip tarihleri yalnızca sizin girdiğiniz bilgilerdir; sistem sonraki tarih veya iş önermez. Yapay zekâ kullanılmaz.</p>
<div class="cr-state" role="status" hidden></div>
<section class="cr-sec" data-sec="summary" hidden><h3>Kapsam ve iş yükü (yalnızca sayılar)</h3><div class="cr-tiles"></div><p class="cr-note cr-unknown"></p></section>
<section class="cr-sec" data-sec="due" hidden><h3>Zamanı gelen / gecikmiş takipler</h3><div class="cr-due"></div></section>
<section class="cr-sec" data-sec="upcoming" hidden><h3>Yaklaşan takipler (7 gün)</h3><div class="cr-up"></div></section>
<section class="cr-sec" data-sec="customer" hidden><h3>Müşteri ara / kayıt</h3>
<div class="cr-row"><input type="text" class="cr-q" placeholder="Ad veya kod (Müşteri 360 araması)" aria-label="Müşteri ara"></div>
<div class="cr-hits"></div><div class="cr-panel"></div></section>
<section class="cr-sec" data-sec="recent" hidden><h3>Son etkileşimler</h3><p class="cr-note">Notlar burada gösterilmez; müşteriyi açınca görünür.</p><div class="cr-recent"></div></section>
<div class="cr-toast" role="status" aria-live="polite" hidden></div>
"""

_CRM_JS=r"""
(function(){
'use strict';
const root=document.getElementById('crm'), tabBtn=document.querySelector('.tab[data-t="crm"]');
if(!root||!tabBtn) return;
const POLL_MS=window.__CRM_POLL_MS||2000, POLL_MAX=window.__CRM_POLL_MAX||450;
const H={'Content-Type':'application/json','X-StockAgent':'1'};   // local CSRF guard (D-020)
const MSG={file:'Takip / CRM için panoyu http://127.0.0.1:8000/ üzerinden açın.',
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  building:'Müşteri verisi hazırlanıyor…', fail:'Yüklenemedi', none:'Henüz yapılandırılmış CRM kaydı yok.'};
const CH=[['phone','Telefon'],['whatsapp','WhatsApp'],['email','E-posta'],['visit','Ziyaret'],['other','Diğer']];
const PU=[['sales_followup','Satış takibi'],['collection','Tahsilat'],['service','Hizmet / destek'],['other','Diğer']];
const DUE={overdue:'Gecikmiş',today:'Bugün',upcoming:'Yaklaşan',later:'İleri tarih'};
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const st={started:false,code:null,seq:{},timers:{}};
const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function state(t,k){const n=$('.cr-state'); n.textContent=t||''; n.className='cr-state'+(k?' '+k:''); show(n,!!t);}
function fmtDate(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'—';}
function num(v){return (v==null||!Number.isFinite(Number(v)))?'—':Number(v).toLocaleString('tr-TR');}
function localToday(){const d=new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function btn(cls,label,fn){const b=el('button','cr-btn'+(cls?' '+cls:''),label); b.type='button'; b.addEventListener('click',fn); return b;}
function toast(t){const n=$('.cr-toast'); n.textContent=t; show(n,true); clearTimeout(st.timers.t); st.timers.t=setTimeout(()=>show(n,false),5000);}
function sel(opts,val,cls,empty){const s=el('select',cls); if(empty!=null){const o=el('option',null,empty); o.value=''; s.append(o);}
  opts.forEach(([k,l])=>{const o=el('option',null,l); o.value=k; s.append(o);}); s.value=val||''; if(s.selectedIndex<0&&s.options.length) s.selectedIndex=0; return s;}
function input(type,cls,max){const i=el('input',cls); i.type=type; if(max) i.maxLength=max; return i;}
async function api(path,opt){
  let r; try{r=await fetch(path,opt||{headers:{'Accept':'application/json'}});}catch(e){const x=new Error('offline'); x.offline=true; throw x;}
  let b=null; try{b=await r.json();}catch(e){b=null;}
  if(!r.ok){const x=new Error('HTTP '+r.status); x.status=r.status; x.body=b; throw x;} return b;}
function errText(e){if(e.offline) return MSG.offline; const d=e.body&&e.body.detail;
  if(d&&typeof d==='object'&&!Array.isArray(d)) return d.message||(MSG.fail+' (HTTP '+e.status+')');
  if(Array.isArray(d)) return 'Geçersiz giriş (alan uzunluğu veya tarih).'; return (typeof d==='string'&&d)||(MSG.fail+' (HTTP '+e.status+')');}
function building(e){return e.status===503&&e.body&&e.body.detail&&e.body.detail.state==='building';}
function openC360(code){const t=document.querySelector('.tab[data-t="c360"]'); if(t) t.click();
  if(window.StockAgentC360&&typeof window.StockAgentC360.open==='function') window.StockAgentC360.open(code);}

function start(){
  if(st.started) return; st.started=true;
  if(location.protocol==='file:'){state(MSG.file,'off'); return;}   // same-origin API only; no request
  refresh(0);
}
async function refresh(n){
  let s; try{s=await api('/api/crm/summary');}
  catch(e){if(building(e)){state(n<POLL_MAX?MSG.building:'Müşteri verisi hâlâ hazırlanıyor.','wait'); if(n<POLL_MAX) setTimeout(()=>refresh(n+1),POLL_MS); return;}
    state(errText(e),e.offline?'off':'err'); return;}
  state(''); renderSummary(s); loadList('due','.cr-due','[data-sec="due"]'); loadList('upcoming','.cr-up','[data-sec="upcoming"]'); loadRecent();
  show($('[data-sec="customer"]'),true);
  if(st.code&&st.loading!==st.code) loadCustomer(st.code);   // never re-render (and wipe) a form already open
}
function renderSummary(s){
  show($('[data-sec="summary"]'),true); const t=$('.cr-tiles'); t.replaceChildren();
  [['total_customers','Müşteri 360 müşterisi'],['customers_with_structured_contact','Yapılandırılmış kaydı olan müşteri'],
   ['active_customers_with_structured_contact','Aktif müşterilerden kaydı olan'],['current_followups','Güncel takip'],
   ['overdue','Gecikmiş'],['due_today','Bugün'],['upcoming_7d','7 gün içinde']].forEach(([k,l])=>{
    if(s[k]==null&&(s.withheld||[]).length) return;   // M8-ACCURACY: withheld classification, no tile
    const d=el('div','cr-tile'); d.dataset.k=k; d.append(el('div','n',num(s[k])),el('div','l',l)); t.append(d);});
  $('.cr-unknown').textContent=(s.unknown_note||'')+((s.withheld||[]).length?' Aktif müşteri sayıları doğrulanmadığı için gösterilmiyor.':'')+' Bu sayılar sistem kapsamını ve iş yükünü gösterir; kişi bazında sayım yapılmaz.'+(s.orphan_customer_records?' Güncel veride bulunmayan '+num(s.orphan_customer_records)+' müşteri kodunun CRM kaydı saklanıyor (gösterilmez).':'');
}
async function loadList(mode,boxSel,secSel){
  show($(secSel),true); const box=$(boxSel);
  let r; try{r=await api('/api/crm/followups?mode='+mode+'&days=7&limit=100');}catch(e){box.replaceChildren(el('p','cr-err',errText(e))); return;}
  box.replaceChildren(); (r.items||[]).forEach(it=>box.append(fuCard(it)));
  if(!(r.items||[]).length) box.append(el('p','cr-muted',mode==='due'?'Zamanı gelen takip yok.':'Yaklaşan takip yok.'));
  if(r.truncated) box.append(el('p','cr-muted','İlk '+r.limit+' gösteriliyor (toplam '+r.total+').'));
}
function fuCard(it){
  const c=el('div','cr-card '+(it.due_state||'')); c.dataset.code=it.customer_code; c.dataset.due=it.due_state||'';
  const top=el('div','cr-row'); top.append(el('span','cr-name',it.display_name),el('span','cr-code',it.customer_code));
  if(it.closed===true) top.append(el('span','cr-kapali','KAPALI'));
  top.append(el('span','cr-chip '+(it.due_state||''),(DUE[it.due_state]||'')+' · '+fmtDate(it.due_date)), el('span','cr-chip',it.purpose_label));
  c.append(top);
  if(it.next_action) c.append(el('p','cr-note cr-next','Sonraki iş: '+it.next_action));
  if(it.owner_label) c.append(el('p','cr-muted cr-owner','Sorumlu (elle girilen etiket): '+it.owner_label));
  if(it.suppressed_kapali) c.append(el('p','cr-warn','Müşteri KAPALI: satış takibi Günlük Satış\'a alınmaz (kayıt silinmedi).'));
  (it.warnings||[]).forEach(w=>c.append(el('p','cr-warn',w.message)));
  const a=el('div','cr-row'); a.append(btn('cr-open','Aç / kaydet',()=>openCustomer(it.customer_code)),btn('cr-c360','Müşteri 360',()=>openC360(it.customer_code)));
  c.append(a); return c;
}
async function loadRecent(){
  show($('[data-sec="recent"]'),true); const box=$('.cr-recent');
  let r; try{r=await api('/api/crm/recent?limit=50');}catch(e){box.replaceChildren(el('p','cr-err',errText(e))); return;}
  const ul=el('ul','cr-tl');
  (r.items||[]).forEach(x=>{const li=el('li'); li.dataset.code=x.customer_code;
    li.append(el('b',null,fmtDate(x.occurred_on)+' · '),document.createTextNode(x.display_name+' ('+x.customer_code+') · '+(x.channel_label||'')+(x.outcome?' · '+x.outcome:'')+(x.has_next_action?' · sonraki iş var':'')+' '));
    li.append(btn('cr-open','Aç',()=>openCustomer(x.customer_code))); ul.append(li);});
  box.replaceChildren(ul); if(!(r.items||[]).length) box.replaceChildren(el('p','cr-muted',MSG.none+' Kayıt olmaması temas olmadığı anlamına gelmez.'));
}
async function search(q){
  const box=$('.cr-hits'), my=st.seq.s=(st.seq.s||0)+1;
  if(String(q).trim().length<2){box.replaceChildren(); return;}
  let r; try{r=await api('/api/customer360/search?q='+encodeURIComponent(q)+'&limit=8');}catch(e){if(my===st.seq.s) box.replaceChildren(el('p','cr-err',errText(e))); return;}
  if(my!==st.seq.s) return; box.replaceChildren();
  (r.results||[]).forEach(h=>{const b=el('button','cr-hit'); b.type='button'; b.dataset.code=h.customer_code;
    b.append(el('span','cr-name',h.display_name),el('span','cr-code',h.customer_code)); if(h.closed===true) b.append(el('span','cr-kapali','KAPALI'));
    b.addEventListener('click',()=>{box.replaceChildren(); openCustomer(h.customer_code);}); box.append(b);});
  if(!(r.results||[]).length) box.append(el('p','cr-muted','Sonuç yok.'));
}
function openCustomer(code){st.code=code; show($('[data-sec="customer"]'),true); loadCustomer(code); $('[data-sec="customer"]').scrollIntoView({block:'start'});}
async function loadCustomer(code){
  const p=$('.cr-panel'), my=st.seq.c=(st.seq.c||0)+1; st.loading=code;
  let x; try{x=await api('/api/crm/customers/'+encodeURIComponent(code));}catch(e){if(my===st.seq.c) p.replaceChildren(el('p','cr-err',errText(e))); return;}
  if(my!==st.seq.c) return; renderCustomer(x);
}
function renderCustomer(x){
  const p=$('.cr-panel'); p.replaceChildren(); p.dataset.code=x.customer_code;
  let name=x.customer_code; const head=el('div','cr-row cr-head'); const h=el('h4',null,'');
  api('/api/customer360/search?q='+encodeURIComponent(x.customer_code)+'&limit=1').then(r=>{const hit=(r.results||[]).find(z=>z.customer_code===x.customer_code); if(hit){h.textContent=hit.display_name;}}).catch(()=>{});
  h.textContent=name; head.append(h,el('span','cr-code',x.customer_code)); if(x.closed===true) head.append(el('span','cr-kapali','KAPALI'));
  head.append(btn('cr-c360','Müşteri 360',()=>openC360(x.customer_code))); p.append(head);
  p.append(el('p','cr-muted',(x.coverage_label||'')+': '+(x.coverage_note||'')));
  (x.warnings||[]).forEach(w=>{const n=el('p','cr-warn',w.message); n.dataset.w=w.code; p.append(n);});
  // current follow-up
  const fu=x.followup, fb=el('div','cr-card cr-fu'); fb.dataset.state=fu?(fu.due_state||''):'none';
  if(fu){fb.append(el('div','cr-name','Güncel takip: '+fmtDate(fu.due_date)+' · '+fu.purpose_label+' · '+(DUE[fu.due_state]||'')));
    if(fu.next_action) fb.append(el('p','cr-note cr-next','Sonraki iş: '+fu.next_action));
    if(fu.owner_label) fb.append(el('p','cr-muted cr-owner','Sorumlu (elle girilen etiket): '+fu.owner_label));
    fb.append(btn('cr-clear','Takibi temizle',()=>putFollowup(x.customer_code,{clear:true})));}
  else fb.append(el('p','cr-muted','Güncel takip yok. Tarih yalnızca siz girerseniz oluşur.'));
  p.append(fb);
  // interaction form
  const f=el('div','cr-form cr-iform'); f.append(el('div','wide cr-name','Etkileşim kaydet'));
  const dt=input('date','ci-date'); dt.value=localToday(); dt.max=localToday();
  const ch=sel(CH,'phone','ci-channel'), pu=sel(PU,'','ci-purpose','(seçilmedi)');
  const oc=input('text','ci-outcome',120), na=input('text','ci-next',160), ow=input('text','ci-owner',80);
  const nt=el('textarea','ci-note'); nt.maxLength=500;
  oc.placeholder='Örn. Görüşüldü'; na.placeholder='Örn. Yeni fiyat listesini gönder'; ow.placeholder='İsteğe bağlı'; nt.placeholder='Kısa yerel not (en fazla 500; yapay zekâya gönderilmez)';
  const lab=(t,n,cls)=>{const l=el('label',cls||null,t); l.append(n); return l;};
  f.append(lab('Temas tarihi',dt),lab('Kanal',ch),lab('Amaç (isteğe bağlı)',pu),lab('Sonuç (en fazla 120)',oc,'wide'),lab('Sonraki iş (en fazla 160)',na,'wide'),
           lab('Sorumlu etiketi (isteğe bağlı, en fazla 80)',ow),lab('Not',nt,'wide'));
  const setFu=input('checkbox','ci-setfu'); const fuDate=input('date','ci-fudate'), fuPu=sel(PU.filter(([k])=>(x.allowed_followup_purposes||[]).includes(k)),'','ci-fupurpose');
  fuDate.setAttribute('aria-label','Sonraki takip tarihi'); fuPu.setAttribute('aria-label','Sonraki takip amacı');
  const fr=el('div','wide cr-row'); const l2=el('label','cr-muted'); l2.append(setFu,document.createTextNode(' Aynı kayıtla sonraki takibi de planla (tarih sizin seçiminiz)')); fr.append(l2,fuDate,fuPu); f.append(fr);
  const ierr=el('p','cr-err wide ci-err'); show(ierr,false);
  f.append(btn('pri ci-save wide','Etkileşimi kaydet',async()=>{
    const b={occurred_on:dt.value,channel:ch.value}; if(pu.value) b.purpose=pu.value; if(oc.value.trim()) b.outcome=oc.value; if(na.value.trim()) b.next_action=na.value;
    if(ow.value.trim()) b.owner_label=ow.value; if(nt.value.trim()) b.note=nt.value;
    if(setFu.checked){if(!fuDate.value){ierr.textContent='Takip tarihi seçin.'; show(ierr,true); return;}
      b.followup={purpose:fuPu.value,due_date:fuDate.value}; if(na.value.trim()) b.followup.next_action=na.value; if(ow.value.trim()) b.followup.owner_label=ow.value;}
    try{const r=await api('/api/crm/customers/'+encodeURIComponent(x.customer_code)+'/interactions',{method:'POST',headers:H,body:JSON.stringify(b)});
      toast('Etkileşim kaydedildi.'); renderCustomer(r); refreshLists();}catch(e){ierr.textContent=errText(e); show(ierr,true);}}), ierr);
  p.append(f);
  // follow-up form
  const g=el('div','cr-form cr-fform'); g.append(el('div','wide cr-name','Takip planla'));
  const gd=input('date','cf-date'), gp=sel(PU.filter(([k])=>(x.allowed_followup_purposes||[]).includes(k)),fu?fu.purpose:'','cf-purpose');
  const gn=input('text','cf-next',160), go=input('text','cf-owner',80);
  if(fu){gd.value=fu.due_date; gn.value=fu.next_action||''; go.value=fu.owner_label||'';}
  g.append(lab('Takip tarihi',gd),lab('Amaç',gp),lab('Sonraki iş (en fazla 160)',gn,'wide'),lab('Sorumlu etiketi (isteğe bağlı)',go));
  if(x.closed===true) g.append(el('p','cr-note wide','KAPALI müşteri: yalnızca tahsilat veya hizmet takibi planlanabilir.'));
  const ferr=el('p','cr-err wide cf-err'); show(ferr,false);
  g.append(btn('pri cf-save wide','Takibi kaydet',()=>{if(!gd.value){ferr.textContent='Takip tarihi seçin.'; show(ferr,true); return;}
    const b={purpose:gp.value,due_date:gd.value}; if(gn.value.trim()) b.next_action=gn.value; if(go.value.trim()) b.owner_label=go.value; putFollowup(x.customer_code,b,ferr);}), ferr);
  p.append(g);
  // timeline
  p.append(el('div','cr-name','Yapılandırılmış etkileşimler ('+num(x.structured_interactions)+')'));
  const ul=el('ul','cr-tl cr-timeline');
  (x.interactions||[]).forEach(i=>{const li=el('li'); li.dataset.id=i.id;
    li.append(el('b',null,fmtDate(i.occurred_on)+' · '+(i.channel_label||'')+(i.purpose_label?' · '+i.purpose_label:'')));
    if(i.outcome) li.append(el('div',null,'Sonuç: '+i.outcome)); if(i.next_action) li.append(el('div',null,'Sonraki iş: '+i.next_action));
    if(i.next_followup) li.append(el('div','cr-muted','Planlanan takip: '+fmtDate(i.next_followup)));
    if(i.owner_label) li.append(el('div','cr-muted','Sorumlu etiketi: '+i.owner_label)); if(i.note) li.append(el('div','cr-muted cr-noteview','Not: '+i.note));
    ul.append(li);});
  if(!(x.interactions||[]).length) ul.append(el('li','cr-muted',MSG.none+' '+(x.unknown_note||'')));
  p.append(ul);
  const lg=x.legacy||{}; if(lg.count){p.append(el('div','cr-name',(lg.label||'Eski notlar')+' ('+num(lg.count)+')'));
    const ul2=el('ul','cr-tl cr-legacy'); (lg.items||[]).forEach(m=>ul2.append(el('li',null,fmtDate(m.ts)+' · '+(m.kind||'')+' · '+(m.detail||'')))); p.append(ul2);}
}
async function putFollowup(code,b,errEl){
  try{const r=await api('/api/crm/customers/'+encodeURIComponent(code)+'/followup',{method:'PUT',headers:H,body:JSON.stringify(b)});
    toast(b.clear?'Takip temizlendi.':'Takip kaydedildi.'); renderCustomer(r); refreshLists();}
  catch(e){if(errEl){errEl.textContent=errText(e); show(errEl,true);} else toast(errText(e));}
}
async function refreshLists(){try{renderSummary(await api('/api/crm/summary'));}catch(e){} loadList('due','.cr-due','[data-sec="due"]'); loadList('upcoming','.cr-up','[data-sec="upcoming"]'); loadRecent();}
$('.cr-q').addEventListener('input',e=>{clearTimeout(st.timers.q); st.timers.q=setTimeout(()=>search(e.target.value),250);});
// Customer 360 / other tabs: open this tab on one customer (explicit writes stay here)
window.StockAgentCRM={open:function(code){st.code=String(code); const was=st.started; tabBtn.click();
  if(was&&location.protocol!=='file:') loadCustomer(st.code);}};   // first open: start() -> refresh() loads it once
tabBtn.addEventListener('click',start);   // lazy: nothing is fetched until the tab is opened
})();
"""

def _crm_panel(meta):
    """(tab button, panel block) for Takip / CRM, wrapped in markers like the other server-driven tabs."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="crm">Takip / CRM</button>'
    block=('\n<!--CRM-TAB-START-->\n<style>'+_CRM_CSS+'</style>\n'
           f'<div id="crm" class="panel" data-asof="{asof}">'+_CRM_BODY+'</div>\n'
           '<script>'+_CRM_JS+'</script>\n<!--CRM-TAB-END-->')
    return btn,block


_OWN_CSS=r"""
#own .ow-aud{background:#eef2ff;border:1px solid #c7d2fe;color:#3730a3;border-radius:10px;padding:10px 13px;margin:6px 0 12px;font-size:13px;overflow-wrap:anywhere}
#own .ow-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:8px 0 12px;overflow-wrap:anywhere}
#own .ow-state.off,#own .ow-state.err{border-color:#f3c4c4;background:#fdf3f3;color:#b42318;font-weight:600}
#own .ow-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 12px}
#own .ow-btn{border:1px solid var(--ink);background:var(--ink);color:#fff;font:inherit;font-size:12px;font-weight:600;padding:7px 12px;border-radius:8px;cursor:pointer}
#own .ow-btn:disabled{opacity:.5;cursor:not-allowed}
#own .ow-btn:focus-visible{outline:2px solid #2563eb;outline-offset:2px}
#own .ow-sec{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 12px;min-width:0}
#own .ow-sec h3{font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;margin:0 0 8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#own .ow-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,150px),1fr));gap:8px}
#own .ow-tile{border:1px solid var(--line);border-radius:8px;padding:7px 9px;min-width:0}
#own .ow-tile .n{font-size:18px;font-weight:800;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
#own .ow-tile .l{font-size:12px;color:var(--muted)}
#own .ow-chip{font-size:11px;font-weight:700;border:1px solid var(--line);border-radius:999px;padding:1px 8px;color:var(--muted);white-space:nowrap;letter-spacing:0;text-transform:none}
#own .ow-chip.ready{color:#1a7f4b;border-color:#bfe3cf}#own .ow-chip.failed{color:#b42318;border-color:#f3c4c4}
#own .ow-chip.loading{color:var(--warn);border-color:#f5d9a8}
#own .ow-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:4px 14px;margin:6px 0}
#own .ow-kv div{font-size:13px;min-width:0;overflow-wrap:anywhere}#own .ow-kv b{color:var(--muted);font-weight:600;margin-right:4px}
#own .ow-note,#own .ow-muted{font-size:12px;color:var(--muted);margin:4px 0;overflow-wrap:anywhere}
#own .ow-warn{background:var(--warn-bg);border:1px solid #f5d9a8;color:var(--warn);border-radius:8px;padding:6px 9px;font-size:12px;margin:6px 0;overflow-wrap:anywhere}
#own .ow-strong{background:#fdf3f3;border:1px solid #f3c4c4;color:#b42318;border-radius:8px;padding:7px 10px;font-size:12px;font-weight:600;margin:6px 0;overflow-wrap:anywhere}
#own .ow-err{font-size:12px;color:#b42318;font-weight:600;margin:4px 0;overflow-wrap:anywhere}
#own .ow-scroll{overflow-x:auto;max-width:100%;margin:6px 0}
#own table{width:100%;border-collapse:collapse;font-size:12px}
#own th{cursor:default;white-space:nowrap}#own td{font-variant-numeric:tabular-nums}
#own td.r,#own th.r{text-align:right}
#own .ow-list{list-style:none;padding:0;margin:0}
#own .ow-list li{border-bottom:1px solid var(--line);padding:7px 0;font-size:13px;overflow-wrap:anywhere}
#own .ow-list li b{display:block}
#own .ow-issue[data-status="blocked"] b::after{content:" · bloke";color:#b42318;font-weight:700}
#own [hidden]{display:none!important}
#own .ow-login{border:1px solid var(--line);background:var(--card);border-radius:12px;padding:16px;max-width:520px;margin:10px 0 16px}
#own .ow-login label{display:block;font-weight:700;margin:8px 0 6px}
#own .ow-login-row{display:flex;gap:8px;flex-wrap:wrap}
#own .ow-pw{flex:1 1 220px;font-size:16px;padding:10px;border:1px solid #b8bfcc;border-radius:8px;min-height:44px}
#own .ow-unlock{min-height:44px;padding:0 20px;font-size:15px}
#own .ow-loginmsg{min-height:1.4em;font-weight:600;margin:8px 0 4px}#own .ow-loginmsg.err{color:#b42318}
@media(max-width:600px){#own .ow-btn{min-height:36px}#own th,#own td{padding:6px 5px}#own th{white-space:normal}}
"""

_OWN_BODY="""
<h2 class="sec">Yönetim</h2>
<div class="ow-aud" role="note">Yönetim bilgisidir ve erişim kontrollüdür: finans bölümleri finans görüntüleme şifresiyle salt okunur, tam görünüm yönetici girişiyle açılır.</div>
<p class="note">Salt okunur. Sayılar Python ile hesaplanır; yapay zekâ kullanılmaz. Sahip kaynakları (Kredi, Gider/Gelir, Bütçe) yalnızca açıkça yapılandırılan dosyalardan okunur ve hiçbir dosya otomatik seçilmez.</p>
<div class="ow-state" role="status" hidden></div>
<form class="ow-login" hidden autocomplete="on" novalidate><p class="ow-login-t">Bu bölüm şirket finanslarını (Kredi, Gider / Gelir, Bütçe) içerir. Görüntülemek için <b>finans görüntüleme şifresini</b> girin. Şifre yalnızca okumaya izin verir; hiçbir değişiklik yapılamaz.</p>
<label for="ow-pw">Finans görüntüleme şifresi</label><div class="ow-login-row"><input id="ow-pw" class="ow-pw" name="password" type="password" autocomplete="current-password" maxlength="256">
<button type="submit" class="ow-btn ow-unlock">Aç</button></div><p class="ow-loginmsg" role="status" aria-live="polite"></p>
<p class="ow-muted">Yönetici misiniz? <a href="/giris">Yönetici girişi</a></p></form>
<div class="ow-bar" hidden><button type="button" class="ow-btn ow-reload">Yönetim verilerini yeniden oku</button><span class="ow-muted ow-reloadmsg" role="status" aria-live="polite"></span></div>
<section class="ow-sec" data-sec="ops" hidden><h3>A. Operasyon Özeti</h3><div class="ow-tiles"></div><div class="ow-opsnotes"></div></section>
<section class="ow-sec" data-sec="kredi" hidden><h3>B. Finansman / Kredi <span class="ow-chip"></span></h3><div class="ow-body"></div></section>
<section class="ow-sec" data-sec="gg" hidden><h3>C. Gider / Gelir <span class="ow-chip"></span></h3><div class="ow-body"></div></section>
<section class="ow-sec" data-sec="butce" hidden><h3>D. Bütçe Sağlığı <span class="ow-chip"></span></h3><div class="ow-body"></div></section>
<section class="ow-sec" data-sec="system" hidden><h3>E. Veri ve Sistem Durumu</h3><div class="ow-body"></div>
<h3 style="margin-top:12px">Açık kararlar / veri eksikleri</h3><ul class="ow-list ow-issues"></ul></section>
"""

_OWN_JS=r"""
(function(){
'use strict';
const root=document.getElementById('own'), tabBtn=document.querySelector('.tab[data-t="own"]');
if(!root||!tabBtn) return;
const POLL_MS=window.__OWN_POLL_MS||1500, POLL_MAX=window.__OWN_POLL_MAX||400;
const H={'Content-Type':'application/json','X-StockAgent':'1'};   // local CSRF guard (D-020)
const MSG={file:'Yönetim için panoyu http://127.0.0.1:8000/ üzerinden açın.',
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  fail:'Yüklenemedi',
  auth:'Yönetim bölümü (şirket finansları ve sistem durumu) yalnızca yönetici girişiyle görüntülenir. Sayfanın üstündeki “Yönetici girişi” bağlantısını kullanın.'};
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const st={started:false,seq:0,shown:0,timer:null,polls:0,reloading:false};
const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function state(t,k){const n=$('.ow-state'); n.textContent=t||''; n.className='ow-state'+(k?' '+k:''); show(n,!!t);}
function fmtDate(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'—';}
function num(v){return (v==null||!Number.isFinite(Number(v)))?'—':Number(v).toLocaleString('tr-TR');}
function money(v,cur){if(v==null||!Number.isFinite(Number(v))) return '—';
  return Number(v).toLocaleString('tr-TR',{minimumFractionDigits:2,maximumFractionDigits:2})+(cur?' '+cur:'');}
function pct(v){return (v==null||!Number.isFinite(Number(v)))?'—':'%'+(Number(v)*100).toLocaleString('tr-TR',{maximumFractionDigits:1});}
function kv(pairs){const g=el('div','ow-kv'); pairs.forEach(([k,v])=>{const d=el('div'); d.append(el('b',null,k+':'),document.createTextNode(v==null||v===''?'—':String(v))); g.append(d);}); return g;}
function table(head,rows,cls){const w=el('div','ow-scroll'); const t=el('table',cls||null); const th=el('thead'), tr=el('tr');
  head.forEach(([label,r])=>{const c=el('th',r?'r':null,label); c.scope='col'; tr.append(c);}); th.append(tr); t.append(th);
  const tb=el('tbody'); rows.forEach(cells=>{const r=el('tr'); cells.forEach(([v,right],i)=>{const c=el(i===0?'th':'td',right?'r':null,v); if(i===0) c.scope='row'; r.append(c);}); tb.append(r);});
  t.append(tb); w.append(t); return w;}
function warnList(ws,box){(ws||[]).forEach(w=>{const p=el('p','ow-warn',w.message); p.dataset.code=w.code; box.append(p);});}
// M8-ACCURACY: a withheld section carries a notice and no data (enforced by the server)
function withheld(w,box){const d=el('div','aw-box'); d.setAttribute('role','note'); d.dataset.metric=w.metric||''; d.dataset.verification=w.verification||'';
  d.append(el('p','aw-msg',w.message)); if((w.reasons||[]).length){const u=el('ul','aw-why'); w.reasons.forEach(r=>u.append(el('li',null,r))); d.append(u);} box.append(d);}
async function api(path,opt){
  let r; try{r=await fetch(path,opt||{headers:{'Accept':'application/json'}});}catch(e){const x=new Error('offline'); x.offline=true; throw x;}
  let b=null; try{b=await r.json();}catch(e){b=null;}
  if(!r.ok){const x=new Error('HTTP '+r.status); x.status=r.status; x.body=b; throw x;} return b;}
function errText(e){if(e.offline) return MSG.offline; if(e.status===401) return MSG.auth;   // M8-CP1: owner-only tab
  return MSG.fail+(e.status?' (HTTP '+e.status+')':'')+'.';}

function start(){
  if(st.started) return; st.started=true;
  if(location.protocol==='file:'){state(MSG.file,'off'); return;}   // same-origin API only; no request
  load();
}
// M8-CP1 (D-061): the owner gets the full Yönetim; everyone else asks for the financial projection, which the server
// refuses (401) without a financial-viewer or owner session → the password form. Permissions are enforced by the
// server; this only chooses which read to ask for.
async function role(force){try{const a=window.StockAgentAccess; const s=a?await a.session(force):null; return s&&s.role||null;}catch(e){return null;}}
async function load(force){
  const my=++st.seq; clearTimeout(st.timer);
  const r=await role(force), url=r==='owner'?'/api/owner/overview':'/api/finance/overview';
  let d; try{d=await api(url);}catch(e){if(my!==st.seq) return;
    const c=e.body&&e.body.detail&&e.body.detail.error_code;
    if(c==='finance_auth_required'||c==='finance_not_configured'){hideData(); loginForm(c); return;}
    if((c==='auth_required'||c==='owner_required')&&!force){load(true); return;}   // the session changed meanwhile
    state(errText(e),e.offline?'off':'err'); return;}
  if(my!==st.seq) return;                // a newer request was issued: never render an older response
  st.shown=my; state(''); show($('.ow-login'),false); render(d);
  const fin=d.audience==='finance';
  const busy=(!fin&&(d.reload.inflight||d.operations.state!=='ready'))||['kredi','gider_gelir','butce'].some(k=>d[k].state==='loading'||d[k].reloading);
  if(busy&&st.polls<POLL_MAX){st.polls++; st.timer=setTimeout(load,POLL_MS);} else {st.polls=0; setReloading(false);}
}
function setReloading(on){st.reloading=on; const b=$('.ow-reload'); b.disabled=on; b.setAttribute('aria-busy',on?'true':'false');
  $('.ow-reloadmsg').textContent=on?'Sahip kaynakları yeniden okunuyor…':'';}
async function reload(){
  if(st.reloading) return; setReloading(true);
  try{await api('/api/owner/reload',{method:'POST',headers:H,body:'{}'});}
  catch(e){setReloading(false); $('.ow-reloadmsg').textContent=errText(e); return;}
  st.polls=0; load();
}
function hideData(){['.ow-bar','[data-sec="ops"]','[data-sec="kredi"]','[data-sec="gg"]','[data-sec="butce"]','[data-sec="system"]']
  .forEach(q=>show($(q),false)); state('');}
const LOGIN_TXT={invalid_credentials:'Şifre hatalı.',login_throttled:'Çok fazla hatalı deneme. Lütfen biraz sonra tekrar deneyin.',
  https_required:'Şifre yalnızca güvenli (HTTPS) bağlantıyla girilebilir.',
  finance_not_configured:'Finans görüntüleme şifresi bu sunucuda tanımlanmadı. Bölüm yalnızca yönetici girişiyle açılır.',
  origin_rejected:'Lütfen sayfayı yenileyin.'};
function loginForm(code){const f=$('.ow-login'); show(f,true); const m=$('.ow-loginmsg');
  const off=code==='finance_not_configured'; $('.ow-pw').disabled=off; $('.ow-unlock').disabled=off;
  m.textContent=off?LOGIN_TXT.finance_not_configured:''; m.className='ow-loginmsg'+(off?' err':'');
  root.dataset.access='locked'; if(!off) $('.ow-pw').focus();}
async function unlock(ev){ev.preventDefault(); const pw=$('.ow-pw'), b=$('.ow-unlock'), m=$('.ow-loginmsg');
  if(!pw.value){m.textContent='Şifreyi girin.'; m.className='ow-loginmsg err'; return;}
  b.disabled=true; m.textContent='Kontrol ediliyor…'; m.className='ow-loginmsg';
  try{await api('/api/auth/finance-login',{method:'POST',headers:H,body:JSON.stringify({password:pw.value})});}
  catch(e){pw.value=''; b.disabled=false; const c=e.body&&e.body.detail&&e.body.detail.error_code;
    m.textContent=LOGIN_TXT[c]||(e.offline?MSG.offline:'Giriş yapılamadı.'); m.className='ow-loginmsg err'; pw.focus(); return;}
  pw.value=''; b.disabled=false; m.textContent=''; show($('.ow-login'),false); st.polls=0; load(true);}
function render(d){
  const fin=d.audience==='finance';      // D-061: financial viewer — financial sections only, read-only
  root.dataset.today=d.today; root.dataset.access=fin?'finance':'owner'; show($('.ow-bar'),!fin);
  $('.ow-aud').textContent=d.audience_note;
  if(fin){show($('[data-sec="ops"]'),false); show($('[data-sec="system"]'),false);}
  else renderOps(d.operations);
  renderKredi(d.kredi); renderGG(d.gider_gelir); renderButce(d.butce);
  if(!fin){renderSystem(d.system,d.known_issues); if(!d.reload.inflight&&!st.reloading) setReloading(false);}
}
function head(sec,src){const chip=$(sec+' .ow-chip'); chip.textContent=src.state_label+(src.reloading?' · yenileniyor':''); chip.className='ow-chip '+src.state;
  const s=$(sec); s.dataset.state=src.state; s.dataset.reloading=src.reloading?'1':'0'; show(s,true);
  const b=$(sec+' .ow-body'); b.replaceChildren();
  if('file' in src) b.append(kv([['Kaynak',src.file||'yapılandırılmadı']]));   // the financial projection has no file name
  if(src.state==='not_configured') b.append(el('p','ow-muted','Yapılandırılmadı. Göstermek için ortam değişkeninde dosya yolunu açıkça verin; dosya otomatik seçilmez.'));
  if(src.state==='loading') b.append(el('p','ow-muted','Yükleniyor…'));
  if(src.state==='failed'){const p=el('p','ow-err',(src.message||'Okunamadı.')+(src.failed_step?' (adım: '+src.failed_step+')':'')); p.dataset.code=src.error_code||''; b.append(p);
    if((src.issue_codes||[]).length) b.append(el('p','ow-muted','Doğrulama kodları: '+src.issue_codes.join(', ')));}
  return b;}
function renderOps(o){
  const s=$('[data-sec="ops"]'); show(s,true); s.dataset.state=o.state; const t=$('.ow-tiles'); t.replaceChildren();
  [['customer360_customers','Müşteri 360 müşterisi'],['daily_sales_customers','Günlük Satış müşterisi (açık)'],['collection_first_customers','Önce tahsilat'],
   ['crm_overdue','CRM takibi gecikmiş'],['crm_due_today','CRM takibi bugün'],['fair_tasks_due','Zamanı gelen fuar görevi'],
   ['active_fair_campaigns','Aktif fuar kampanyası'],['cross_sell_customers','Çapraz satış adayı müşteri']].forEach(([k,l])=>{
    if(o.withheld&&o[k]==null&&k!=='customer360_customers') return;   // M8-ACCURACY: withheld counts are not shown
    const d=el('div','ow-tile'); d.dataset.k=k; d.append(el('div','n',num(o[k])),el('div','l',l)); t.append(d);});
  const cov=el('div','ow-tile'); cov.dataset.k='crm_coverage'; cov.append(el('div','n',o.crm_coverage_label),el('div','l','CRM kapsamı ('+num(o.crm_structured_interactions)+' yapılandırılmış etkileşim; kayıt olmaması temas olmadığı anlamına gelmez)')); t.append(cov);
  const n=$('.ow-opsnotes'); n.replaceChildren();
  if(o.withheld) withheld(o.withheld,n);
  if(o.state!=='ready') n.append(el('p','ow-muted','Müşteri verisi hazırlanıyor…'));
  if(o.cross_sell_share!=null) n.append(el('p','ow-muted','Çapraz satış: aktif '+num(o.cross_sell_active_customers)+' müşterinin '+pct(o.cross_sell_share)+' kadarında en az bir hedef var.'));
  if(o.cross_sell_note){const p=el('p','ow-warn ow-xs',o.cross_sell_note); n.append(p);}
  n.append(el('p','ow-muted','Kişi veya temsilci bazında sayım ya da sıralama yapılmaz.'));
}
function renderKredi(k){
  const b=head('[data-sec="kredi"]',k); b.append(el('p','ow-note',k.purpose));
  if(k.withheld&&k.state!=='not_configured'){withheld(k.withheld,b); return;}
  const x=k.data; if(!x) return;
  b.append(kv([['Sözleşme',num(x.contracts)],['Taksit',num(x.installments)],['Banka sayısı',num(x.lenders)],['Para birimleri',(x.currencies||[]).join(', ')],
    ['Kaynak anlık tarihi',x.snapshot_date?fmtDate(x.snapshot_date)+' (beyan edilen)':'bilinmiyor'],['Son kayıtlı ödeme tarihi',fmtDate(x.latest_payment_date)],['Bugün',fmtDate(k.today)]]));
  if(x.snapshot_note) b.append(el('p','ow-strong ow-snap',x.snapshot_note));
  b.append(el('p','ow-warn',k.schedule_note),el('p','ow-muted',k.horizon_note));
  (x.schedule||[]).forEach(c=>{
    const box=el('div','ow-cur'); box.dataset.currency=c.currency;
    box.append(el('p','ow-note','Para birimi: '+c.currency+' · sonraki ödenmemiş taksit: '+fmtDate(c.next_due_date)));
    const rows=(c.horizons||[]).map(h=>[[h.days+' gün içinde'],[num(h.installments),true],[money(h.amount,c.currency),true]]);
    rows.push([['90 gün sonrası'],[num(c.after_90.installments),true],[money(c.after_90.amount,c.currency),true]]);
    rows.push([['Vadesi geçmiş (ayrı)'],[num(c.past_due.installments),true],[money(c.past_due.amount,c.currency),true]]);
    rows.push([['Toplam kayıtlı kalan'],[num(c.total_remaining.installments),true],[money(c.total_remaining.amount,c.currency),true]]);
    box.append(table([['Vade ufku'],['Taksit',true],['Kalan taksit tutarı',true]],rows,'ow-sched')); b.append(box);});
  b.append(el('p','ow-muted',k.overdue_note),el('p','ow-muted',k.principal_note)); warnList(x.warnings,b);
}
function renderGG(g){
  const b=head('[data-sec="gg"]',g); const x=g.data;
  if(g.withheld&&g.state!=='not_configured'){withheld(g.withheld,b); return;}
  b.append(el('p','ow-strong ow-basis',g.basis_note));
  if(!x) return;
  b.append(kv([['Rapor yılı',x.report_year],['Revizyon etiketi',x.revision_label],['Kaynak anlık tarihi','bilinmiyor'],
    ['Kaynak verisi olan aylar',(x.months||[]).map(m=>AY[m.month-1]).join(', ')||'—']]));
  b.append(el('p','ow-muted',g.mapping_note));
  b.append(table([['Ay'],['Raporlanan satış',true],['Raporlanan maliyet',true],['Raporlanan brüt kâr',true],['Raporlanan gider',true]],
    (x.months||[]).map(m=>[[AY[m.month-1]],[money(m.reported_sales),true],[money(m.reported_cost),true],[money(m.reported_gross_profit),true],[money(m.reported_expense),true]]),'ow-months'));
  b.append(el('p','ow-note','Kaynaktaki iş kolları (ayrı; eşleme yok)'));
  b.append(table([['İş kolu'],['Raporlanan satış',true],['Raporlanan maliyet',true],['Raporlanan brüt kâr',true],['Dağıtılmış gider',true]],
    (x.lines||[]).map(l=>[[l.label],[money(l.reported_sales),true],[money(l.reported_cost),true],[money(l.reported_gross_profit),true],[money(l.allocated_expense),true]]),'ow-lines'));
  if((x.budget||[]).length){b.append(el('p','ow-note','Kaynakta raporlanan bütçe sütunu ('+x.report_year+'; yalnızca kaynak değeri, karşılaştırma yapılmaz)'));
    b.append(table([['Satır'],['Kaynak bütçe değeri',true]],x.budget.map(r=>[[r.label],[money(r.value),true]]),'ow-budget'));}
  b.append(el('p','ow-muted',g.reconciliation_note)); warnList(x.warnings,b);
}
function renderButce(u){
  const b=head('[data-sec="butce"]',u); const x=u.data;
  if(u.withheld&&u.state!=='not_configured'){withheld(u.withheld,b); return;}
  if(!x) return;
  b.append(el('p',x.source_status==='complete'?'ow-note':'ow-strong ow-bwarn',u.warning));
  b.append(kv([['Planlama yılı',x.planning_year],['Revizyon etiketi',x.revision_label],['Kaynak anlık tarihi',x.snapshot_date?fmtDate(x.snapshot_date)+' (beyan edilen)':'bilinmiyor'],
    ['Kaynak durumu',x.source_status==='partially_usable'?'kısmen kullanılabilir':x.source_status==='complete'?'tam':x.source_status],
    ['Aday hücre',num(x.candidate_cells)],['Kullanılabilir (doğrulanmamış)',num(x.usable_unverified)],['Geçersiz hücre',num(x.invalid_cells)],
    ['Kapsam',x.coverage_pct==null?'—':'%'+Number(x.coverage_pct).toLocaleString('tr-TR')],['#REF!',num(x.ref_errors)],['#DIV/0!',num(x.div0_errors)],
    ['Diğer Excel hataları',num(x.other_errors)],['Önbelleksiz formül',num(x.missing_cached_formulas)],['Dış bağlantı',num(x.external_links)],
    ['Dış bağımlı hücre',num(x.external_dependent_cells)],['Dosya kaydı','karara bağlanmadı']]));
  b.append(el('p','ow-muted','Bütçe-vs-gerçek, fark analizi veya Gider/Gelir ile karşılaştırma yapılmaz; formüller onarılmaz.'));
}
function renderSystem(s,issues){
  const sec=$('[data-sec="system"]'); show(sec,true); const b=$('[data-sec="system"] .ow-body'); b.replaceChildren();
  const c=s.core||{};
  b.append(el('p','ow-note','Ana kaynaklar'),kv([['Bağlam',c.context_state==='ready'?'hazır':c.context_state==='error'?'hata':'hazırlanıyor'],['Veri tarihi',fmtDate(c.as_of_data_date)],
    ['Satış',c.sales_file],['Stok',c.stock_file],['Cari yaşlandırma',c.aging_file]]));
  b.append(el('p','ow-note','İsteğe bağlı satış kaynağı'),kv([['Hedef',s.hedef.configured?(s.hedef.file+' · '+s.hedef.status):'yapılandırılmadı']]));
  b.append(el('p','ow-note','Sahip kaynakları'));
  const ul=el('ul','ow-list ow-srcs'); (s.owner_sources||[]).forEach(o=>{const li=el('li'); li.dataset.key=o.key; li.dataset.state=o.state;
    li.append(el('b',null,o.label),document.createTextNode((o.file||'yapılandırılmadı')+' · '+o.state_label+(o.message?' · '+o.message:''))); ul.append(li);}); b.append(ul);
  const t=s.stores||{};
  b.append(el('p','ow-note','Operasyonel kayıtlar'),kv([['Müşteri 360',(t.customer360_state==='ready'?'hazır':t.customer360_state)+(t.customer360_customers!=null?' · '+num(t.customer360_customers)+' müşteri':'')],
    ['CRM kapsamı',t.crm_coverage_label],['Fuar kampanyası',num(t.fair_campaigns)],['Yerel SQLite',t.sqlite_available?'erişilebilir':'erişilemiyor']]));
  b.append(el('p','ow-note','Bloke kaynaklar'));
  const bl=el('ul','ow-list ow-blocked'); (s.blocked||[]).forEach(x=>{const li=el('li'); li.dataset.key=x.key; li.append(el('b',null,x.label),document.createTextNode(x.status_label)); bl.append(li);}); b.append(bl);
  const il=$('.ow-issues'); il.replaceChildren();
  (issues||[]).forEach(i=>{const li=el('li','ow-issue'); li.dataset.code=i.code; li.dataset.status=i.status; li.append(el('b',null,i.title),document.createTextNode(i.detail));
    if(i.count!=null) li.append(el('div','ow-muted',num(i.count)+' '+(i.count_label||''))); il.append(li);});
}
$('.ow-reload').addEventListener('click',reload);
$('.ow-login').addEventListener('submit',unlock);
tabBtn.addEventListener('click',start);   // lazy: nothing is fetched (and no owner file is read) until the tab is opened
})();
"""

# ---------------------------------------------------------------------------------------------
# M8-VKM-1 Veri Kontrol Merkezi (D-062): server-driven tab. Everyone sees the allow-listed counts summary
# (GET /api/vkm/summary); the owner sees the issue list, details, history and the review controls (owner_read /
# owner_write; the server enforces every permission). All text goes through textContent (never innerHTML).
# ---------------------------------------------------------------------------------------------
_VKM_CSS=r"""
#vkm .vk-intro{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:6px 0 12px;font-size:13px}
#vkm .vk-intro p{margin:4px 0}
#vkm .vk-notapproval{background:#eef2ff;border:1px solid #c7d2fe;color:#3730a3;border-radius:8px;padding:8px 11px;font-weight:700;font-size:13px;margin:6px 0 0}
#vkm .vk-state{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 14px;margin:8px 0 12px;overflow-wrap:anywhere}
#vkm .vk-state.err{border-color:#f3c4c4;background:#fdf3f3;color:#b42318;font-weight:600}
#vkm .vk-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,120px),1fr));gap:8px;margin:0 0 10px}
#vkm .vk-tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 11px;min-width:0}
#vkm .vk-tile .n{font-size:20px;font-weight:800;font-variant-numeric:tabular-nums}
#vkm .vk-tile .l{font-size:12px;color:var(--muted)}
#vkm .vk-muted{font-size:12px;color:var(--muted);margin:4px 0;overflow-wrap:anywhere}
#vkm .vk-types{list-style:none;padding:0;margin:4px 0 12px;display:flex;flex-wrap:wrap;gap:6px}
#vkm .vk-types li{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;background:var(--card)}
#vkm .vk-viewer{background:var(--warn-bg);border:1px solid #f5d9a8;border-radius:10px;padding:10px 13px;font-size:13px;margin:6px 0 12px}
#vkm .vk-viewer a{color:#1f5fbf;font-weight:600}
#vkm .vk-srcs{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
#vkm .vk-src{border:1px solid var(--line);border-radius:8px;padding:5px 9px;font-size:12px;background:var(--card)}
#vkm .vk-src[data-state="missing"]{border-color:#f3c4c4;background:#fdf3f3;color:#b42318}
#vkm .vk-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:8px;margin:0 0 8px;align-items:end}
#vkm .vk-filters label{display:flex;flex-direction:column;gap:3px;font-size:12px;font-weight:700;color:var(--muted)}
#vkm select,#vkm textarea{font:inherit;font-size:14px;border:1px solid #b8bfcc;border-radius:8px;padding:7px 9px;background:var(--card);color:var(--ink);min-height:38px;max-width:100%}
#vkm textarea{width:100%;min-height:76px;resize:vertical}
#vkm .vk-btn{border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit;font-size:13px;font-weight:600;padding:8px 12px;border-radius:8px;cursor:pointer;min-height:38px}
#vkm .vk-btn.pri{background:var(--ink);color:#fff;border-color:var(--ink)}
#vkm .vk-btn:disabled{opacity:.5;cursor:not-allowed}
#vkm .vk-btn:focus-visible,#vkm .vk-item:focus-visible{outline:2px solid #2563eb;outline-offset:2px}
#vkm .vk-list{list-style:none;padding:0;margin:0}
#vkm .vk-item{display:block;width:100%;text-align:left;font:inherit;color:inherit;background:var(--card);border:1px solid var(--line);border-left-width:5px;border-radius:10px;padding:10px 12px;margin:0 0 8px;cursor:pointer}
#vkm .vk-item[data-severity="kritik"]{border-left-color:#b42318}#vkm .vk-item[data-severity="yuksek"]{border-left-color:#c2410c}
#vkm .vk-item[data-severity="orta"]{border-left-color:#b45309}#vkm .vk-item[data-severity="dusuk"]{border-left-color:#94a3b8}
#vkm .vk-item[data-status="cozuldu_kaynakta"]{opacity:.75}
#vkm .vk-t{font-weight:700;font-size:14px;display:block;margin:3px 0 2px;overflow-wrap:anywhere}
#vkm .vk-meta{font-size:12px;color:var(--muted);display:block;overflow-wrap:anywhere}
#vkm .vk-chip{display:inline-block;font-size:11px;font-weight:700;border:1px solid var(--line);border-radius:999px;padding:1px 8px;margin:0 4px 2px 0;white-space:nowrap}
#vkm .vk-chip.sev-kritik{color:#b42318;border-color:#f3c4c4}#vkm .vk-chip.sev-yuksek{color:#c2410c;border-color:#fed7aa}
#vkm .vk-chip.sev-orta{color:#b45309;border-color:#f5d9a8}#vkm .vk-chip.sev-dusuk{color:#475569}
#vkm .vk-chip.st-cozuldu_kaynakta{color:#1a7f4b;border-color:#bfe3cf}#vkm .vk-chip.st-yeniden_acildi{color:#b42318;border-color:#f3c4c4}
#vkm .vk-chip.pr-source_missing,#vkm .vk-chip.pr-not_checked{color:#b45309;border-color:#f5d9a8}
#vkm .vk-sync{background:#fdf3f3;border:1px solid #f3c4c4;color:#b42318;border-radius:10px;padding:10px 13px;margin:6px 0 12px;font-weight:600;font-size:13px;overflow-wrap:anywhere}
#vkm .vk-sync p{margin:3px 0}#vkm .vk-sync .vk-btn{margin-top:6px}
#vkm .vk-detail{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 14px}
#vkm .vk-detail h3{font-size:16px;margin:6px 0;overflow-wrap:anywhere}
#vkm .vk-detail h4{font-size:12px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;margin:12px 0 4px;color:var(--muted)}
#vkm .vk-detail p{margin:4px 0;font-size:13px;overflow-wrap:anywhere}
#vkm .vk-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:4px 14px;font-size:13px}
#vkm .vk-kv b{color:var(--muted);font-weight:600;margin-right:4px}
#vkm .vk-acts{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
#vkm .vk-msg{min-height:1.2em;font-weight:600;font-size:13px;margin:6px 0}#vkm .vk-msg.err{color:#b42318}#vkm .vk-msg.ok{color:#1a7f4b}
#vkm .vk-hist{list-style:none;padding:0;margin:0}
#vkm .vk-hist li{border-bottom:1px solid var(--line);padding:6px 0;font-size:13px;overflow-wrap:anywhere}
#vkm .vk-hist .vk-note{white-space:pre-wrap;background:#f6f7f8;border-radius:6px;padding:5px 8px;margin:4px 0 0}
#vkm [hidden]{display:none!important}
@media(max-width:600px){#vkm .vk-acts .vk-btn{flex:1 1 100%}}
"""

_VKM_BODY="""
<h2 class="sec">Veri Kontrol Merkezi</h2>
<div class="vk-intro" role="note"><p>Bu bölüm, aylık DinamikCRM Excel raporlarında sistemin kural tabanlı (yapay zekâ kullanmayan) denetimlerle bulduğu veri sorunlarını listeler. Her sorun her yeni yüklemede yeniden denetlenir: devam ediyorsa açık kalır, kaynakta düzelirse “kaynakta çözüldü” olur, geri gelirse yeniden açılır. Kaynak dosyalarda hiçbir satır değiştirilmez veya silinmez.</p>
<p class="vk-notapproval">İnceleme bir onay değildir; metrikler yalnızca doğrulanmış kanıtla gösterilir.</p></div>
<div class="vk-state" role="status" hidden></div>
<div class="vk-sync" role="alert" hidden><p class="vk-sync-msg"></p><p class="vk-sync-src"></p><button type="button" class="vk-btn pri vk-resync" hidden>Denetimi yeniden çalıştır</button></div>
<section class="vk-sum" hidden><div class="vk-tiles"></div><p class="vk-muted vk-last"></p><ul class="vk-types"></ul></section>
<p class="vk-viewer" hidden>Sorunların yeri, ayrıntıları, geçmişi ve notlar yalnızca yönetici girişiyle görüntülenir; inceleme kararlarını yalnızca yönetici verebilir. <a href="/giris">Yönetici girişi</a></p>
<section class="vk-owner" hidden>
<p class="vk-muted">Son yükleme gözlemi (hangi dosyaların bu listeye dayandığı):</p><div class="vk-srcs"></div>
<form class="vk-filters" onsubmit="return false"><label>Durum<select class="vk-f" data-f="status"></select></label><label>Önem<select class="vk-f" data-f="severity"></select></label>
<label>Sorun türü<select class="vk-f" data-f="type"></select></label><label>Kaynak<select class="vk-f" data-f="source"></select></label>
<label>Bu yüklemede<select class="vk-f" data-f="presence"></select></label><button type="button" class="vk-btn vk-clear">Filtreleri temizle</button></form>
<section class="vk-detail" hidden aria-live="polite"></section>
<p class="vk-muted vk-count" role="status"></p><ul class="vk-list"></ul>
</section>
"""

_VKM_JS=r"""
(function(){
'use strict';
const root=document.getElementById('vkm'), tabBtn=document.querySelector('.tab[data-t="vkm"]');
if(!root||!tabBtn) return;
const H={'Content-Type':'application/json','X-StockAgent':'1'};   // local guard (D-020); the CSRF token is added by the access script
const MSG={file:'Veri Kontrol Merkezi için panoyu http://127.0.0.1:8000/ üzerinden açın.',
  offline:'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  fail:'Veri Kontrol Merkezi yüklenemedi', none:'Henüz gözlenmiş bir veri yüklemesi yok. Veriler yenilendiğinde denetim sonuçları burada görünür.',
  empty:'Bu filtrelerle eşleşen konu yok.', noOpen:'Açık veri sorunu yok.', auth:'Oturum sona erdi; yeniden yönetici girişi yapın.',
  stale:'Veriler bu sayfa açıldıktan sonra yeniden gözlendi; liste yenilendi. Lütfen konuyu kontrol edip tekrar deneyin.',
  saved:'Kaydedildi.', noteNeeded:'Not boş olamaz.', synced:'Denetim sonuçları güncellendi.',
  blocked:'Denetim sonuçları yayımlanan verilerle güncel olmadığı için şu anda inceleme kararı verilemez.'};
const SEV=[['kritik','Kritik'],['yuksek','Yüksek'],['orta','Orta'],['dusuk','Düşük']];
const AY=['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık'];
const st={started:false,seq:0,role:null,gen:null,cat:null,filters:{status:'open'},open:null,busy:false};
const $=s=>root.querySelector(s);
function el(tag,cls,text){const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text); return e;}
function show(n,on){n.hidden=!on;}
function state(t,k){const n=$('.vk-state'); n.textContent=t||''; n.className='vk-state'+(k?' '+k:''); show(n,!!t);}
function num(v){return (v==null||!Number.isFinite(Number(v)))?'—':Number(v).toLocaleString('tr-TR');}
function fmtDay(iso){const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso||'')); return m?(+m[3])+' '+AY[+m[2]-1]+' '+m[1]:'—';}
function fmtWhen(iso){if(!iso) return '—'; const d=new Date(iso); if(isNaN(d)) return fmtDay(iso);
  return d.getDate()+' '+AY[d.getMonth()]+' '+d.getFullYear()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
async function api(path,opt){
  let r; try{r=await fetch(path,opt||{headers:{'Accept':'application/json'}});}catch(e){const x=new Error('offline'); x.offline=true; throw x;}
  let b=null; try{b=await r.json();}catch(e){b=null;}
  if(!r.ok){const x=new Error('HTTP '+r.status); x.status=r.status; x.body=b; x.code=b&&b.detail&&b.detail.error_code; throw x;} return b;}
function errText(e){if(e.offline) return MSG.offline; if(e.status===401) return MSG.auth;
  const m=e.body&&e.body.detail&&e.body.detail.message; return m||(MSG.fail+(e.status?' (HTTP '+e.status+')':'')+'.');}
async function role(force){try{const a=window.StockAgentAccess; const s=a?await a.session(force):null; return s&&s.role||null;}catch(e){return null;}}

function start(){if(st.started) return; st.started=true;
  if(location.protocol==='file:'){state(MSG.file,'err'); return;}
  load();}
async function load(force){
  const my=++st.seq; st.role=await role(force);
  if(st.role==='owner'){try{const d=await api(listUrl()); if(my!==st.seq) return; renderOwner(d); return;}
    catch(e){if(my!==st.seq) return; if(e.status!==401&&e.status!==403){state(errText(e),'err'); return;} st.role=null;}}
  try{const s=await api('/api/vkm/summary'); if(my!==st.seq) return; state(''); renderSummary(s); show($('.vk-owner'),false); show($('.vk-viewer'),true); root.dataset.access='viewer';}
  catch(e){if(my===st.seq) state(errText(e),'err');}
}
function listUrl(){const q=new URLSearchParams(); Object.entries(st.filters).forEach(([k,v])=>{if(v) q.set(k,v);}); return '/api/vkm/issues?'+q.toString();}
function tiles(c,last){const box=$('.vk-tiles'); box.replaceChildren();
  const t=(k,n,l)=>{const d=el('div','vk-tile'); d.dataset.k=k; d.append(el('div','n',num(n)),el('div','l',l)); box.append(d);};
  t('open',c.open_total,'Açık konu'); SEV.forEach(([k,l])=>t(k,(c.by_severity||{})[k],l+' (açık)'));
  t('resolved',(c.by_status||{}).cozuldu_kaynakta,'Kaynakta çözüldü');
  $('.vk-last').textContent=last?('Son gözlem: '+last):''; show($('.vk-sum'),true);}
function renderSync(sy,owner){const box=$('.vk-sync'); const st0=sy&&(sy.state||sy);
  root.dataset.sync=st0||'';
  if(!st0||st0==='in_sync'){show(box,false); return;}
  $('.vk-sync-msg').textContent=(sy&&sy.message)||'';
  const src=(sy&&sy.sources||[]).map(x=>x.label); $('.vk-sync-src').textContent=src.length?('Güncel olmayan kaynaklar: '+src.join(', ')):'';
  show($('.vk-resync'),!!owner&&st0==='out_of_sync'); show(box,true);}
function renderSummary(s){
  renderSync({state:s.sync,message:s.sync_message},false);
  if(!s.observed){state(MSG.none); show($('.vk-sum'),false); return;}
  tiles(s,fmtDay(s.last_observed_on));
  const ul=$('.vk-types'); ul.replaceChildren(); (s.by_type||[]).forEach(x=>{const li=el('li',null,x.label+': '+num(x.open)); li.dataset.type=x.key; ul.append(li);});
  if(!s.open_total) state(MSG.noOpen); }
function options(sel,items,cur,all){sel.replaceChildren(); if(all!==null){const o=el('option',null,all||'Tümü'); o.value=''; sel.append(o);}
  items.forEach(([k,l])=>{const o=el('option',null,l); o.value=k; sel.append(o);}); sel.value=cur||'';}
function renderFilters(cat){
  const f=st.filters, get=k=>root.querySelector('.vk-f[data-f="'+k+'"]');
  options(get('status'),[['open','Açık olanlar (çözülmemiş)'],['all','Tümü (geçmiş dahil)']].concat(cat.statuses.map(x=>[x.key,x.label])),f.status||'open',null);
  options(get('severity'),cat.severities.map(x=>[x.key,x.label]),f.severity,'Tüm önem düzeyleri');
  options(get('type'),cat.types.map(x=>[x.key,x.label]),f.type,'Tüm sorun türleri');
  options(get('source'),cat.sources.map(x=>[x.key,x.label]),f.source,'Tüm kaynaklar');
  options(get('presence'),cat.presence.map(x=>[x.key,x.label]),f.presence,'Hepsi');}
function renderOwner(d){
  root.dataset.access='owner'; st.gen=d.vkm_generation; root.dataset.gen=d.vkm_generation; st.cat=d.catalogue;
  show($('.vk-viewer'),false); show($('.vk-owner'),true); state(''); renderSync(d.sync,true);
  if(!(d.sources||[]).length){state(MSG.none);}
  tiles(d.counts,(d.sources||[]).length?fmtWhen((d.sources||[]).map(s=>s.observed_at).sort().pop()):'');
  const ty=$('.vk-types'); ty.replaceChildren(); (d.counts.by_type||[]).filter(x=>x.open).forEach(x=>{const li=el('li',null,x.label+': '+num(x.open)); li.dataset.type=x.key; ty.append(li);});
  const srcs=$('.vk-srcs'); srcs.replaceChildren();
  (d.sources||[]).forEach(s=>{const b=el('span','vk-src',s.label+' · '+s.state_label+' · '+fmtWhen(s.observed_at)+(s.period_end?' · rapor tarihi '+fmtDay(s.period_end):'')); b.dataset.key=s.key; b.dataset.state=s.state;
    if(s.fingerprint) b.title='Dosya parmak izi '+s.fingerprint+' · yükleme '+(s.generation||''); srcs.append(b);});
  renderFilters(d.catalogue);
  const ul=$('.vk-list'); ul.replaceChildren();
  d.issues.forEach(i=>ul.append(item(i)));
  $('.vk-count').textContent=d.issues.length?(num(d.issues.length)+' konu gösteriliyor.'):(st.filters.status==='open'&&!Object.keys(st.filters).some(k=>k!=='status'&&st.filters[k])?MSG.noOpen:MSG.empty);
}
function chip(cls,text){return el('span','vk-chip '+cls,text);}
function item(i){
  const li=el('li'); const b=el('button','vk-item'); b.type='button';
  b.dataset.id=i.issue_id; b.dataset.severity=i.severity; b.dataset.status=i.status; b.dataset.presence=i.presence; b.dataset.type=i.issue_type;
  const top=el('span'); top.append(chip('sev-'+i.severity,i.severity_label),chip('st-'+i.status,i.status_label),chip('pr-'+i.presence,i.presence_label));
  if(i.notes) top.append(chip('vk-notes',i.notes+' not'));
  b.append(top,el('span','vk-t',i.title),el('span','vk-meta',i.source_label+' · '+i.location+' · '+i.type_label),
    el('span','vk-meta','İlk görülme: '+fmtWhen(i.first_seen_at)+' · Son görülme: '+fmtWhen(i.last_seen_at)));
  b.addEventListener('click',()=>openIssue(i.issue_id)); li.append(b); return li;}
async function openIssue(id){
  st.open=id; const box=$('.vk-detail'); show(box,true); box.replaceChildren(el('p','vk-muted','Yükleniyor…'));
  try{const d=await api('/api/vkm/issues/'+encodeURIComponent(id)); if(st.open!==id) return; st.gen=d.vkm_generation; root.dataset.gen=d.vkm_generation; renderDetail(d);}
  catch(e){if(st.open===id) box.replaceChildren(el('p','vk-msg err',errText(e)));}}
function kv(pairs){const g=el('div','vk-kv'); pairs.forEach(([k,v])=>{const d=el('div'); d.append(el('b',null,k+':'),document.createTextNode(v==null||v===''?'—':String(v))); g.append(d);}); return g;}
function renderDetail(d){
  const i=d.issue, box=$('.vk-detail'); box.replaceChildren(); box.dataset.id=i.issue_id; box.dataset.status=i.status;
  const back=el('button','vk-btn vk-back','← Listeye dön'); back.type='button'; back.addEventListener('click',()=>{st.open=null; show(box,false); box.replaceChildren(); const t=root.querySelector('.vk-item[data-id="'+i.issue_id+'"]'); if(t) t.focus();});
  const h=el('h3','vk-dt',i.title); h.tabIndex=-1;
  const chips=el('p'); chips.append(chip('sev-'+i.severity,i.severity_label),chip('st-'+i.status,i.status_label),chip('pr-'+i.presence,i.presence_label));
  box.append(back,h,chips,el('p','vk-notapproval',d.notice));
  box.append(el('h4',null,'Ne anlama geliyor?'),el('p','vk-explain',i.explanation));
  box.append(el('h4',null,'Ne yapmalı?'),el('p','vk-action',i.action));
  box.append(el('h4',null,'Nerede?'),kv([['Kaynak',i.source_label],['Konum',i.location],['Sorun türü',i.type_label],['Konu numarası',i.issue_id]]));
  if((i.labels||[]).length) box.append(el('p','vk-muted vk-labels','Ayrıntı: '+i.labels.join(', ')));
  if((i.counts||[]).length){box.append(el('h4',null,'Bu yüklemedeki sayılar')); box.append(kv(i.counts.map(c=>[c.label,num(c.value)])));}
  box.append(el('h4',null,'Ne zaman?'),kv([['İlk görülme',fmtWhen(i.first_seen_at)],['Son görülme',fmtWhen(i.last_seen_at)],
    ['Bu yüklemede',i.presence_label],['Beyan edilen rapor tarihi',i.period_end?fmtDay(i.period_end):'beyan edilmedi'],
    ['İlk / son yükleme',(i.first_seen_generation||'—')+' / '+(i.last_seen_generation||'—')]]));
  box.append(el('h4',null,'Etkilenen analizler'));
  if((i.affected_metrics||[]).length){const u=el('ul','vk-types vk-affected'); i.affected_metrics.forEach(m=>{const li=el('li',null,m.title); li.dataset.metric=m.key; u.append(li);}); box.append(u);
    box.append(el('p','vk-muted','Bu analizler doğrulanmış kanıt olmadan gösterilmez; incelemek onları göstermez.'));}
  else box.append(el('p','vk-muted','Kayıtlı bir analiz bu konuya doğrudan bağlı değil.'));
  // owner decision (server-enforced; the reviewer is the logged-in owner, never a form field)
  box.append(el('h4',null,'İnceleme kararı ve not'));
  const lab=el('label','vk-muted','Not (isteğe bağlı, en fazla 500 karakter; yapay zekâya gönderilmez)'); const ta=el('textarea','vk-notein'); ta.maxLength=500; lab.append(ta); box.append(lab);
  const acts=el('div','vk-acts'), msg=el('p','vk-msg'); msg.setAttribute('role','status');
  (i.allowed_actions||[]).forEach(a=>{const b=el('button','vk-btn pri vk-act',a.label); b.type='button'; b.dataset.action=a.key; b.addEventListener('click',()=>decide(i.issue_id,a.key,ta,msg)); acts.append(b);});
  const nb=el('button','vk-btn vk-addnote','Yalnızca not ekle'); nb.type='button'; nb.addEventListener('click',()=>addNote(i.issue_id,ta,msg)); acts.append(nb);
  box.append(acts,msg);
  if(i.decisions_blocked){ta.disabled=true; nb.disabled=true; nb.hidden=true; msg.textContent=MSG.blocked; msg.className='vk-msg err vk-blocked';}
  else if(!(i.allowed_actions||[]).length) box.append(el('p','vk-muted','Bu konu kaynakta çözüldü; yalnızca not eklenebilir. Sorun yeniden görülürse otomatik olarak yeniden açılır.'));
  box.append(el('h4',null,'Geçmiş'));
  const ul=el('ul','vk-hist'); (d.history||[]).forEach(e=>{const li=el('li'); li.dataset.event=e.event;
    li.append(el('b',null,fmtWhen(e.at)+' · '+e.event_label));
    const meta=[e.actor_label]; if(e.status_after_label) meta.push('durum: '+e.status_after_label); if(e.reason_label) meta.push(e.reason_label);
    if((e.counts||[]).length) meta.push(e.counts.map(c=>c.label+' '+num(c.value)).join(', '));
    li.append(el('div','vk-muted',meta.join(' · '))); if(e.note) li.append(el('div','vk-note',e.note)); ul.append(li);});
  box.append(ul); h.focus();
}
async function post(path,body,msg){
  if(st.busy) return null; st.busy=true; root.querySelectorAll('.vk-detail .vk-btn').forEach(b=>b.disabled=true); msg.textContent='Kaydediliyor…'; msg.className='vk-msg';
  try{return await api(path,{method:'POST',headers:H,body:JSON.stringify(body)});}
  catch(e){if(e.code==='stale_generation'||e.code==='out_of_sync'){const id=st.open; await load(); if(id) await openIssue(id); const m=root.querySelector('.vk-detail .vk-msg'); if(m){m.textContent=e.code==='out_of_sync'?MSG.blocked:MSG.stale; m.className='vk-msg err';} return null;}
    msg.textContent=errText(e); msg.className='vk-msg err'; return null;}
  finally{st.busy=false; root.querySelectorAll('.vk-detail .vk-btn').forEach(b=>b.disabled=false);}}
async function decide(id,action,ta,msg){const body={action:action,vkm_generation:st.gen}; if(ta.value.trim()) body.note=ta.value;
  const d=await post('/api/vkm/issues/'+encodeURIComponent(id)+'/review',body,msg); if(d) after(d);}
async function addNote(id,ta,msg){if(!ta.value.trim()){msg.textContent=MSG.noteNeeded; msg.className='vk-msg err'; ta.focus(); return;}
  const d=await post('/api/vkm/issues/'+encodeURIComponent(id)+'/notes',{note:ta.value,vkm_generation:st.gen},msg); if(d) after(d);}
async function after(d){st.gen=d.vkm_generation; renderDetail(d); const m=root.querySelector('.vk-detail .vk-msg'); if(m){m.textContent=MSG.saved; m.className='vk-msg ok';}
  try{const l=await api(listUrl()); renderOwner(l);}catch(e){}}
root.querySelectorAll('.vk-f').forEach(s=>s.addEventListener('change',()=>{st.filters[s.dataset.f]=s.value||undefined; if(!st.filters.status) st.filters.status='open'; load();}));
$('.vk-clear').addEventListener('click',()=>{st.filters={status:'open'}; load();});
$('.vk-resync').addEventListener('click',async()=>{const b=$('.vk-resync'); if(b.disabled) return; b.disabled=true; const m=$('.vk-sync-src');
  try{const r=await api('/api/vkm/sync',{method:'POST',headers:H,body:'{}'}); const id=st.open; await load(); if(id) await openIssue(id);
    if(r&&r.sync&&r.sync.state==='in_sync') state(MSG.synced);}
  catch(e){m.textContent=errText(e);} finally{b.disabled=false;}});
tabBtn.addEventListener('click',start);   // lazy: nothing is fetched until the tab is opened
})();
"""


def _vkm_panel(meta):
    """(tab button, panel block) for Veri Kontrol Merkezi, wrapped in markers like the other server-driven tabs."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="vkm">Veri Kontrol</button>'
    block=('\n<!--VKM-TAB-START-->\n<style>'+_VKM_CSS+'</style>\n'
           f'<div id="vkm" class="panel" data-asof="{asof}">'+_VKM_BODY+'</div>\n'
           '<script>'+_VKM_JS+'</script>\n<!--VKM-TAB-END-->')
    return btn,block


def _own_panel(meta):
    """(tab button, panel block) for Yönetim, wrapped in markers like the other server-driven tabs."""
    asof=html.escape(str(meta.get('asof') or ''),quote=True)
    btn='<button class="tab" data-t="own">Yönetim</button>'
    block=('\n<!--OWN-TAB-START-->\n<style>'+_OWN_CSS+'</style>\n'
           f'<div id="own" class="panel" data-asof="{asof}">'+_OWN_BODY+'</div>\n'
           '<script>'+_OWN_JS+'</script>\n<!--OWN-TAB-END-->')
    return btn,block


def _html(modules,stats,meta,later,ai_panel=True,c360_panel=True,sq_panel=True,opp_panel=True,fair_panel=True,crm_panel=True,own_panel=True,vkm_panel=True,up_panel=True):
    blob=json.dumps(modules,ensure_ascii=False).replace('</','<\\/')
    tabs=['overview']+list(modules.keys())
    tabbtns=''.join(f'<button class="tab" data-t="{t}">{("Genel Bakış" if t=="overview" else modules[t]["title"])}'
                    f'{"" if (t=="overview" or modules[t].get("withheld")) else f" <span class=cnt>{len(modules[t]["rows"])}</span>"}</button>' for t in tabs)
    statcards=''.join(f'<div class="stat"><div class="n">{v}</div><div class="l">{l}</div></div>' for l,v in stats)
    # M8-ACCURACY: withheld stat cards are left out entirely (no number); one neutral line names them.
    ws=meta.get('withheld_stats') or []
    statnote=(f'<p class="note aw-stats">{html.escape("Bu özet göstergeler henüz doğrulanmadığı için gösterilmiyor: "+", ".join(ws)+".")}</p>'
              if ws else '')
    tiers=(f'<p class="note" style="margin-top:14px">Geri kazanım dağılımı — Kademe 1: {meta["tiers"][1]} · Kademe 2: {meta["tiers"][2]} · Kademe 3: {meta["tiers"][3]}.</p>'
           if meta.get('show_tiers',True) else '')
    acc=html.escape(str(meta.get('accuracy_policy') or ''),quote=True)
    gen=html.escape(str(meta.get('generation') or ''),quote=True)
    shown=html.escape(','.join(meta.get('displayed') or []),quote=True)
    laterlist=''.join(f'<li>{html.escape(x)}</li>' for x in later)
    src=f"Stok: {meta['stock']} · Satış: {meta['sales']}"+(f" · Alacaklar: {meta['aging']}" if meta['has_aging'] else '')
    ai_btn,ai_block=_ai_panel(meta) if ai_panel else ('','')
    wp=meta.get('withheld_panels') or {}
    c3_btn,c3_block=(_withheld_panel('c360','Müşteri 360','C360',wp['c360'],meta) if 'c360' in wp else _c360_panel(meta)) if c360_panel else ('','')
    sq_btn,sq_block=(_withheld_panel('sq','Günlük Satış','SQ',wp['sq'],meta) if 'sq' in wp else _sq_panel(meta)) if sq_panel else ('','')
    op_btn,op_block=(_withheld_panel('opp','Fırsatlar','OPP',wp['opp'],meta) if 'opp' in wp else _opp_panel(meta)) if opp_panel else ('','')
    fm_btn,fm_block=_fair_panel(meta) if fair_panel else ('','')
    cr_btn,cr_block=_crm_panel(meta) if crm_panel else ('','')
    ow_btn,ow_block=_own_panel(meta) if own_panel else ('','')
    vk_btn,vk_block=_vkm_panel(meta) if vkm_panel else ('','')   # M8-VKM-1: after the analysis tabs it explains
    from services import upload_ui as _UPUI
    up_btn,up_block=_UPUI.panel() if up_panel else ('','')        # M8-CP3: owner-only Dosya Yükleme (hidden for viewers)
    return f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="stockagent-accuracy" content="{acc}"><meta name="stockagent-generation" content="{gen}"><meta name="stockagent-displayed" content="{shown}"><title>Şirket Panosu — {meta['date']}</title>
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
.tab:focus-visible{{outline:2px solid #2563eb;outline-offset:2px}}
@media(max-width:600px){{.tabs{{gap:6px}}.tab{{padding:7px 10px;min-height:36px;white-space:nowrap}}}}
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
.aw-box{{border:1px solid var(--line);background:#f4f5f7;border-radius:10px;padding:14px 16px;margin:8px 0 14px}}
.aw-msg{{font-weight:700;margin:0 0 6px;font-size:15px}}.aw-why{{margin:0;padding-left:18px;color:var(--muted);font-size:13px}}
.aw-why li{{margin:3px 0}}
{_ACCESS_CSS}</style></head><body><div class="wrap">
<header><div class="eyebrow">Şirket Panosu</div>
<h1>Şirket Panosu — {meta['date']}</h1>
<div class="sub">{src}{f" · {meta['total']:,} ürün" if meta.get('total_shown') else ''}</div>
<div class="acc-bar" id="halsa-access" hidden></div></header>
<div class="acc-msg" id="halsa-access-msg" role="alert" hidden></div>
<script>{_ACCESS_JS}</script>
<div class="tabs">{tabbtns}{up_btn}{vk_btn}{sq_btn}{c3_btn}{op_btn}{fm_btn}{cr_btn}{ow_btn}{ai_btn}</div>
<div id="overview" class="panel on">
<h2 class="sec">Aktif Analizler</h2>
<p class="note">Bu panoda yalnızca kaynağı, tarihi, tanımı ve hesabı doğrulanmış sonuçlar gösterilir. Doğrulanmamış bir analizin yerinde rakam değil, neyin eksik olduğunu söyleyen kısa bir not görürsünüz. Rakamlar yapay zekâ tarafından üretilmez.</p>
<div class="stats">{statcards}</div>{statnote}
{tiers}
<h2 class="sec" style="margin-top:34px">Sonrası İçin</h2>
<p class="note">İleride eklenebilecek analizler. Her biri ancak kaynağı sağlanıp doğrulandıktan sonra gösterilir.</p>
<ul class="later">{laterlist}</ul>
</div>
<div id="panels"></div>{up_block}{vk_block}{sq_block}{c3_block}{op_block}{fm_block}{cr_block}{ow_block}{ai_block}
<script>
const DATA={blob};
const fmtMoney=v=>'₺'+Math.round(v).toLocaleString('tr-TR');
const fmtMoneyExact=v=>'₺'+Number(v).toLocaleString('tr-TR',{{minimumFractionDigits:2,maximumFractionDigits:6}});  // D-071 Cari balance: kuruş kept
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
  if(m.withheld){{   // M8-ACCURACY: withheld server-side; the build embedded no rows for this module
    const w=m.withheld; p.dataset.withheld='1';
    p.innerHTML=`<h2 class="sec">${{esc(m.title)}}</h2><div class="aw-box" role="note"><p class="aw-msg">${{esc(w.message)}}</p>`
      +((w.reasons||[]).length?`<ul class="aw-why">${{w.reasons.map(r=>`<li>${{esc(r)}}</li>`).join('')}}</ul>`:'')+`</div>`;
    const bx=p.querySelector('.aw-box'); bx.dataset.metric=String(w.metric||''); bx.dataset.verification=String(w.verification||'');
    document.getElementById('panels').appendChild(p); return;
  }}
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
    const cls='main '+(m.expand?'exp':'');
    h+=`<tr class="${{cls}}" data-i="${{i}}">`+m.cols.map(c=>{{
      let v=r[c.k];
      if(c.k==='tier') return `<td><span class="tier t${{v}}">K${{v}}</span></td>`;
      if(c.money&&c.exact) return `<td class="money ${{(c.sign&&v<0)?'neg':(c.sign&&v>0?'pos':'')}}">${{v==null?'—':fmtMoneyExact(v)}}</td>`;
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
  document.querySelectorAll('.tab').forEach(x=>{{x.classList.remove('on'); x.removeAttribute('aria-current');}});
  document.querySelectorAll('.panel').forEach(x=>x.classList.remove('on'));
  b.classList.add('on'); b.setAttribute('aria-current','page'); document.getElementById(b.dataset.t).classList.add('on');}}));
document.querySelector('.tab').classList.add('on'); document.querySelector('.tab').setAttribute('aria-current','page');
</script></div></body></html>"""

if __name__=='__main__':
    import sys
    out,counts=build(sys.argv[1] if len(sys.argv)>1 else '.', 'dashboard.html')
    print('built',out,counts)
