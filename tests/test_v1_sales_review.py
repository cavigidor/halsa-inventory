"""V1 review visibility is not certification; strict missing-input handling and offline privacy."""
import dataclasses
import json
from pathlib import Path

import pandas as pd
import pytest

import app as APP
import dashboard_builder as DB
from services import accuracy as ACC, generation as GEN, sales_review as SALES, stock_review as STOCK
from services.father_report import generate
from tests.fixtures import synthetic_erp as SE
from tests.test_m8_accuracy import _data_blob
from tests.test_m8_accuracy2 import clean_folder, selection

pytestmark=pytest.mark.production_gate


def case(tmp_path,transform=None):
    folder=clean_folder(tmp_path)
    path=Path(folder)/SE.SALES_FILE
    raw=pd.read_excel(path)
    raw['Srmmerk']=pd.to_datetime(raw['Tarih']).dt.year.astype(str)+'SENTFUARAFUAR'
    if transform: raw=transform(raw)
    raw.to_excel(path,index=False)
    selection(folder)
    paths,decl,origin=GEN.select_core(folder)
    frozen=GEN.freeze(paths);frames=GEN.frames_from_frozen(frozen)
    manifest=GEN.build_manifest(frozen,frames,decl,origin)
    gate=ACC.current_gate().bound_to(manifest.core_snapshot())
    modules=SALES.modules(manifest,paths['sales'],gate,raw=frames['sales'])
    return folder,paths,frames,manifest,gate,modules


def by_code(module): return {r['code']:r for r in module['rows']}


def test_existing_calculations_are_preliminary_and_never_certified(tmp_path):
    folder,paths,frames,m,gate,mods=case(tmp_path)
    raw=frames['sales'];before=raw.copy(deep=True)
    sdf=DB.load_sales(None,raw);attrs=DB.cust_attrs(sdf)
    assert mods['momentum']['rows'][0]['y2025']==DB.m_momentum(sdf,attrs)[0]['y2025']
    assert mods['ordersize']['rows'][0]['aov']==DB.m_ordersize(sdf,attrs)[0]['aov']
    assert {r['code']:r['tier'] for r in mods['winback']['rows']}=={r['code']:r['tier'] for r in DB.m_lapsed(sdf,{},attrs)}
    profit=by_code(mods['margin'])['900.00.0006']
    assert profit['missing_cost']==2 and profit['prof'] is None and profit['marg'] is None
    assert profit['known_profit']==20000  # three known costs in 2025/26; two missing remain unknown
    assert 'Maliyet eksik' in profit['input_status']
    assert all('detail' not in row for key in ('momentum','ordersize','winback','margin') for row in mods[key]['rows'])
    assert '900.00.0007' not in by_code(mods['winback'])  # original KAPALI filter preserved
    assert any(r['code']=='900.00.0007' for r in mods['purchases_yearly']['rows'])  # descriptive history includes it
    for mod in mods.values():
        if 'review' not in mod:continue
        review=mod['review']
        assert review['status']=='PRELIMINARY' and not gate.decide(review['metric']).displayable
        assert review['sha256']==m.record('sales').sha256
        assert all(w['question'] and w['evidence'] and w['resolution'] for w in review['warnings'])
    assert [k for k,v in ACC.REGISTER.items() if v.certified==ACC.State.VERIFIED]==['receivables.collections']
    pd.testing.assert_frame_equal(raw,before)


def test_missing_amount_document_and_date_never_become_zero_or_complete_total(tmp_path):
    def change(raw):
        ix=raw.index[raw.Pro_kodu.eq('900.00.0006')][0]
        raw.loc[ix,'Nettutar']=None
        raw.loc[ix,'sip_evrakno_sira']=None
        raw.loc[ix,'Tarih']=None
        return raw
    *_,mods=case(tmp_path,change)
    size=by_code(mods['ordersize'])['900.00.0006']
    assert all(size[k] is None for k in ('orders','rev','aov','chg'))
    growth=by_code(mods['momentum'])['900.00.0006']
    assert all(growth[k] is None for k in ('y2025','y2026','delta','pct'))
    annual=[r for r in mods['purchases_yearly']['rows'] if r['code']=='900.00.0006']
    assert annual and all(r['amount'] is None and r['average'] is None and r['orders'] is None for r in annual)
    evidence=[r for r in mods['sales_source_issues']['rows'] if 'Tarih eksik' in r['issues']]
    assert len(evidence)==1 and evidence[0]['date'] is None and evidence[0]['amount'] is None
    assert evidence[0]['excel_row']>=2


def test_zero_and_negative_source_values_and_net_nonpositive_winback_meaning(tmp_path):
    def change(raw):
        row=raw.iloc[0].copy()
        row['Tarih']=pd.Timestamp('2026-01-10');row['Nettutar']=-100
        row['Net_Tutar_Maliyet_Dusulmus']=-25;row['sip_evrakno_sira']=9876
        raw=pd.concat([raw,pd.DataFrame([row])],ignore_index=True)
        row=row.copy();row['Nettutar']=0;row['sip_evrakno_sira']=9877
        return pd.concat([raw,pd.DataFrame([row])],ignore_index=True)
    *_,mods=case(tmp_path,change)
    r=by_code(mods['winback'])['900.00.0001']
    assert r['orders_2026']==2 and r['last_order'].startswith('2026-01-10')
    assert 'hiç sipariş yok sonucu değildir' in mods['winback']['note']
    yearly=next(r for r in mods['purchases_yearly']['rows'] if r['code']=='900.00.0001' and r['year']==2026)
    assert yearly['amount']==-100 and yearly['orders']==2
    assert yearly['average']==-50


def test_missing_optional_cost_or_fair_columns_do_not_block_other_sections(tmp_path):
    def change(raw):return raw.drop(columns=['Net_Tutar_Maliyet_Dusulmus','Srmmerk'])
    folder,paths,frames,m,gate,mods=case(tmp_path,change)
    assert mods['momentum']['rows'] and mods['purchases_yearly']['rows']
    assert all(r['prof'] is None and r['marg'] is None for r in mods['margin']['rows'])
    assert mods['fairs_review']['withheld']['verification']=='UNAVAILABLE'
    assert 'Srmmerk' in mods['fairs_review']['withheld']['reasons'][0]


def test_duplicate_records_are_kept_and_explained(tmp_path):
    def change(raw):return pd.concat([raw,raw.iloc[[0]]],ignore_index=True)
    *_,mods=case(tmp_path,change)
    yearly=next(r for r in mods['purchases_yearly']['rows'] if r['code']=='900.00.0001' and r['year']==2021)
    assert yearly['lines']==2 and yearly['amount']==30000
    dup=[r for r in mods['sales_source_issues']['rows'] if 'tekrarı' in r['issues']]
    assert len(dup)==2 and dup[0]['excel_row']!=dup[1]['excel_row']


def test_sales_source_changed_or_missing_refuses_cached_review(tmp_path,monkeypatch):
    folder,paths,frames,m,gate,mods=case(tmp_path)
    out=tmp_path/'dashboard.html'
    DB.build(folder,out,sources=paths,frames=frames,manifest=m)
    body=out.read_bytes();m=dataclasses.replace(m,dashboard_sha256=GEN.sha256_file(out))
    monkeypatch.setattr(APP,'DATA_FOLDER',folder)
    assert APP._dashboard_authorized(body,m)[0]
    Path(paths['sales']).write_bytes(b'changed')
    assert APP._dashboard_authorized(body,m)[1]=='review_source_unavailable'
    assert SALES.modules(m,paths['sales'],gate,raw=frames['sales'])=={}
    Path(paths['sales']).unlink()
    assert APP._dashboard_authorized(body,m)[1]=='review_source_unavailable'


def test_general_report_ignores_optional_cari_and_never_reads_its_records(tmp_path,monkeypatch):
    folder,paths,frames,m,gate,mods=case(tmp_path)
    selection_file=Path(folder)/GEN.SELECTION_FILE
    sel=json.loads(selection_file.read_text());sel['core']['aging']={'file':'MISSING-OPTIONAL-CARI.xlsx'}
    selection_file.write_text(json.dumps(sel))
    before={k:GEN.sha256_file(v) for k,v in paths.items() if v}
    out=tmp_path/'private.html'
    result=generate(folder,out)
    data=_data_blob(out.read_text())
    assert data['momentum']['rows'] and data['ordersize']['rows'] and data['winback']['rows']
    assert 'collections' not in data and all(not v.get('finance') for v in data.values())
    assert not any(k in result['sources'] for k in ('aging','liste','kredi','gider_gelir','butce'))
    assert all(GEN.sha256_file(paths[k])==sha for k,sha in before.items())
    assert out.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):generate(folder,out)
    assert set(result['sections'])==set(data)
    for forbidden in ('data-t="own"','data-t="crm"','data-t="fair"','data-t="ai"','data-t="up"','Sonrası İçin'):
        assert (f'<button class="tab" {forbidden}>' if forbidden.startswith('data-t') else forbidden) not in out.read_text()


def test_no_explicit_selection_or_input_overwrite_is_allowed(tmp_path):
    folder=clean_folder(tmp_path)
    with pytest.raises(OSError):generate(folder,tmp_path/'report.html')
    selection(folder)
    with pytest.raises(ValueError):generate(folder,Path(folder)/SE.STOCK_FILE)
    out=tmp_path/'existing.html';out.write_text('keep')
    with pytest.raises(FileExistsError):generate(folder,out)
    assert out.read_text()=='keep'


def test_missing_document_only_does_not_withhold_independent_order_amount(tmp_path):
    def change(raw):
        ix=raw.index[raw.Pro_kodu.eq('900.00.0006')][0]
        raw.loc[ix,'sip_evrakno_sira']=None
        return raw
    *_,mods=case(tmp_path,change)
    row=by_code(mods['ordersize'])['900.00.0006']
    assert row['rev']==170000
    assert row['orders'] is None and row['aov'] is None and row['chg'] is None


def test_cari_only_changes_cannot_leak_or_change_sales_review_values(tmp_path):
    import openpyxl
    folder,paths,frames,m,gate,mods=case(tmp_path)
    book=openpyxl.load_workbook(paths['aging']);sheet=book.worksheets[0]
    headers=[c.value for c in sheet[1]]
    name_col=headers.index('Hesap adı')+1
    sheet.cell(2,1,'999.99.9999')
    sheet.cell(2,name_col,'CARI-ONLY-SYN-PRIVATE-NAME')
    # Unique synthetic balance; never present in Stock or Sales, so byte scanning is a valid canary.
    bucket=next(i+1 for i,v in enumerate(headers) if 've öncesi' in str(v))
    sheet.cell(2,bucket,9234567.89123)
    book.save(paths['aging']);book.close()
    frozen=GEN.freeze(paths);changed_frames=GEN.frames_from_frozen(frozen)
    _,decl,origin=GEN.select_core(folder)
    import datetime as dt
    decl['aging']={**decl['aging'],'snapshot_date':dt.date.today(),'period_complete':True}
    changed=GEN.build_manifest(frozen,changed_frames,decl,origin)
    assert ACC.current_gate().bound_to(changed.core_snapshot()).decide('receivables.collections').displayable
    again=SALES.modules(changed,paths['sales'],ACC.current_gate().bound_to(changed.core_snapshot()),raw=changed_frames['sales'])
    for key in mods:
        assert mods[key]['rows']==again[key]['rows']
    output=tmp_path/'with-cari.html'
    DB.build(folder,output,sources=paths,frames=changed_frames,manifest=changed)
    text=output.read_text()
    assert all(canary not in text for canary in ('999.99.9999','CARI-ONLY-SYN-PRIVATE-NAME','9234567.89123'))
    assert _data_blob(text)['collections']['rows']==[]
