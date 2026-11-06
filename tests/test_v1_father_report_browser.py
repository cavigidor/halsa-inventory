"""The real exported single-file report works offline with no app/session or HTTP calls."""
import json
from pathlib import Path
import pandas as pd
import pytest
from services.father_report import generate
from tests.test_v1_sales_review import case
from tests.test_m5_browser import browser  # noqa: F401

pytestmark=pytest.mark.production_gate


def test_offline_report_tables_filters_warnings_and_no_server_controls(browser,tmp_path):
    folder,*_=case(tmp_path)
    out=tmp_path/'father.html';generate(folder,out)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    errors,requests=[],[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('request',lambda request:requests.append(request.url))
    page.goto(out.as_uri())
    for tab in ('stock_review','momentum','ordersize','winback','margin','purchases_yearly','purchases_products','sales_source_issues','fairs_review','report_scope'):
        page.click(f'.tab[data-t="{tab}"]')
        assert page.locator(f'#{tab} table').count()==1,tab
    assert page.locator('.tab[data-t="own"],.tab[data-t="crm"],.tab[data-t="fair"],.tab[data-t="ai"],.tab[data-t="up"]').count()==0
    page.click('.tab[data-t="margin"]')
    panel=page.locator('#margin')
    panel.locator('.review-issues > summary').click()
    panel.locator('details[data-check="DQ-1"] > summary').click()
    assert 'Eksik maliyet satırı' in panel.inner_text() or 'eksik maliyet satırı' in panel.inner_text()
    assert 'ERP kontrol sorusu' in panel.inner_text()
    assert '—' in panel.locator('tbody').inner_text()
    page.click('.tab[data-t="momentum"]')
    panel=page.locator('#momentum')
    panel.locator('.repsel').select_option('TEMSILCI-A')
    assert panel.locator('tbody tr').count()==1
    panel.locator('.search').fill('SYN-NO-MATCH')
    assert 'Gösterilecek kayıt yok' in panel.locator('tbody').inner_text()
    panel.locator('.search').fill('')
    panel.locator('th[data-k="pct"]').click()
    assert panel.locator('tbody tr').count()==1
    assert not errors,errors
    assert not any('/api/' in url or url.startswith(('http:','https:')) for url in requests)
    page.close()


def test_large_report_pagination_searches_all_rows_and_resets_page(browser,tmp_path):
    def many(raw):
        copies=[]
        for i in range(15):
            frame=raw.copy()
            frame['Pro_kodu']=frame['Pro_kodu'].fillna('SYN-FALLBACK').astype(str)+f'-SYN-{i:03}'
            frame['ProjeIsmi']='Sentetik Sayfalama '+str(i)
            frame['sip_evrakno_seri']='SYN'+str(i)
            copies.append(frame)
        return pd.concat(copies,ignore_index=True)
    folder,*_=case(tmp_path,many)
    out=tmp_path/'large.html';generate(folder,out)
    page=browser.new_page();page.goto(out.as_uri())
    page.click('.tab[data-t="purchases_yearly"]')
    panel=page.locator('#purchases_yearly')
    assert panel.locator('tbody tr').count()==100
    panel.locator('.next-page').click()
    assert 'sayfa 2 /' in panel.locator('.count').inner_text()
    panel.locator('.search').fill('SYN-014')
    assert 'sayfa 1 /' in panel.locator('.count').inner_text()
    assert 'SYN-014' in panel.locator('tbody').inner_text()
    assert panel.locator('.prev-page').is_disabled()
    page.close()


def test_source_filename_and_cell_text_cannot_create_html_or_requests(browser,tmp_path):
    from services import generation as GEN
    folder,*_=case(tmp_path)
    selection_path=Path(folder)/GEN.SELECTION_FILE
    selection=json.loads(selection_path.read_text())
    old=Path(folder)/selection['core']['sales']['file']
    hostile="<img src=x onerror='window.__FILE_XSS=1'> satışlar.xlsx"
    old.rename(old.with_name(hostile))
    selection['core']['sales']['file']=hostile
    selection_path.write_text(json.dumps(selection))
    out=tmp_path/'escaped.html';generate(folder,out)
    page=browser.new_page();requests=[]
    page.on('request',lambda request:requests.append(request.url))
    page.goto(out.as_uri())
    assert page.locator('img').count()==0
    assert page.evaluate('window.__FILE_XSS') is None
    assert len(requests)==1 and requests[0].endswith('escaped.html')
    assert hostile in page.locator('header').inner_text()
    page.close()
