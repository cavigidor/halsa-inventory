"""Synthetic source review: real production server, Chromium, filters, escaping and diagnostics."""
import pytest

pytestmark = pytest.mark.production_gate

from tests.test_m8_accuracy_browser import production_server
from tests.test_m5_browser import browser, open_page  # noqa: F401


def test_review_is_visible_searchable_expandable_and_makes_no_analysis_call(browser, tmp_path):
    with production_server(tmp_path) as srv:
        page = open_page(browser)
        errors, requested = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requested.append(request.url))
        assert page.goto(srv['base'] + '/').status == 200
        page.click('.tab[data-t="stock_review"]')
        panel = page.locator('#stock_review')
        assert panel.locator('.review-box').count() == 1
        assert 'İnceleme için / doğrulanmadı' in panel.inner_text()
        assert panel.locator('tbody tr').count() > 0
        panel.locator('details[data-check="D-070"] summary').click()
        assert 'ERP kontrol sorusu' in panel.inner_text()
        assert panel.locator('details[data-check="D-070"]').get_attribute('open') is not None
        panel.locator('.search').fill('SP-002')
        assert panel.locator('tbody tr').count() == 1
        assert 'SP-002' in panel.locator('tbody').inner_text()
        panel.locator('.search').fill('SYNTHETIC-NO-MATCH')
        assert 'Gösterilecek kayıt yok' in panel.locator('tbody').inner_text()
        panel.locator('.search').fill('')
        panel.locator('th[data-k="excel_row"]').click()
        assert panel.locator('tbody tr').count() > 1
        # Retired action surfaces and analytical APIs stay withheld.
        for tab in ('reorder', 'overstock', 'osmove', 'risk'):
            page.click(f'.tab[data-t="{tab}"]')
            assert page.locator(f'#{tab} table').count() == 0
        assert not any('/api/agent/actions' in url or '/api/opportunities/' in url for url in requested)
        assert not errors, errors
        page.close()


def test_stock_preview_opens_from_file_without_a_server(browser, tmp_path):
    import dashboard_builder as DB
    from tests.test_v1_stock_review import case
    folder, paths, frames, manifest, gate = case(tmp_path)
    out = tmp_path / 'dashboard.html'
    DB.build(folder, out, sources=paths, frames=frames, manifest=manifest)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors, network = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: network.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
    page.goto(out.as_uri())
    page.click('.tab[data-t="stock_review"]')
    panel = page.locator('#stock_review')
    assert panel.locator('tbody tr').count() == 4
    panel.locator('details[data-check="D-070"] summary').click()
    assert 'kaynak kayıt: 2.' in panel.inner_text()
    panel.locator('.search').fill('#DIV/0!')
    assert panel.locator('tbody tr').count() == 2
    assert not errors and not network
    page.close()
