"""Owner V1 directive: provisional source evidence is visible, certification and roles stay strict."""
import dataclasses
import json
from pathlib import Path

import pytest

import app as APP
import dashboard_builder as DB
from services import accuracy as ACC, generation as GEN, stock_review as REVIEW
from tests.fixtures import synthetic_erp as SE
from tests.fixtures.xlsx_synth import E, F, MISSING
from tests.test_m8_accuracy2 import clean_folder, selection, write_stock
from tests.test_m8_accuracy import _data_blob

pytestmark = pytest.mark.production_gate


def case(tmp_path):
    folder = clean_folder(tmp_path)
    path = write_stock(folder, [
        ['SYN-DUP', 'Sentetik <Ürün>', -3.125, E('#DIV/0!'), 1, 3, None, None],
        ['SYN-DUP', 'Sentetik İkinci', 0, F('1/0', E('#DIV/0!')), 1, 3, 0, None],
        ['SYN-CACHE', ' ', None, F('1+1', MISSING), 1, 3, 1.234567, None],
        [None, 'Sentetik Kimliksiz', 4, 0.4, 1, 3, 40, None]])
    selection(folder)
    paths, declarations, origin = GEN.select_core(folder)
    frozen = GEN.freeze(paths)
    frames = GEN.frames_from_frozen(frozen)
    manifest = GEN.build_manifest(frozen, frames, declarations, origin)
    gate = ACC.current_gate().bound_to(manifest.core_snapshot())
    return folder, paths, frames, manifest, gate


def test_preliminary_facts_keep_errors_blanks_signs_duplicates_and_exact_rows(tmp_path):
    folder, paths, frames, m, gate = case(tmp_path)
    module = REVIEW.module(m, paths['stock'], gate)
    rows, review = module['rows'], module['review']
    assert [r['excel_row'] for r in rows] == [5, 6, 7, 8]
    assert rows[0]['quantity'] == -3.125 and rows[1]['quantity'] == 0
    assert rows[2]['name'] == ' '
    assert rows[2]['quantity'] is None and rows[0]['inventory'] is None
    assert rows[1]['inventory'] == 0 and rows[2]['inventory'] == 1.234567
    assert rows[0]['coverage'] == rows[1]['coverage'] == '#DIV/0!'
    assert 'önbellek yok' in rows[2]['coverage']
    assert rows[0]['code'] == rows[1]['code'] and rows[3]['code'] is None
    assert 'Yinelenen kod' in rows[0]['issues'] and 'Negatif kaynak' in rows[0]['issues']
    assert review['counts']['code_rows'] == 3 and review['counts']['distinct_codes'] == 2
    assert review['counts']['missing_identity_rows'] == 1
    assert 'kaynak kayıt: 2.' in review['warnings'][0]['evidence']
    assert review['status'] == 'PRELIMINARY' and review['verification'] == gate.decide(REVIEW.KEY).state.value
    assert not gate.decide(REVIEW.KEY).displayable
    assert review['sha256'] == GEN.sha256_file(paths['stock']) and review['generation'] == m.core_id
    for w in review['warnings']:
        assert all(w[k] for k in ('issue', 'evidence', 'question', 'resolution'))
    output = tmp_path / 'dashboard.html'
    DB.build(folder, output, sources=paths, frames=frames, manifest=m)
    blob = _data_blob(output.read_text())
    assert blob['stock_review']['rows'] == rows
    for retired in ('reorder', 'overstock', 'osmove', 'risk'):
        assert blob[retired]['rows'] == []
    assert REVIEW.KEY in REVIEW.markers(output.read_bytes())
    assert REVIEW.KEY not in GEN.dashboard_markers(output.read_bytes())[1]


@pytest.mark.parametrize('change', ['missing', 'changed', 'layout', 'inspection', 'no_manifest'])
def test_unavailable_is_honest_and_never_returns_partial_records(tmp_path, change):
    folder, paths, frames, m, gate = case(tmp_path)
    if change == 'missing':
        Path(paths['stock']).unlink()
    elif change == 'changed':
        write_stock(folder, [['SYN-CHANGED', 'Başka veri', 10, 1, 1, 3, 1, None]])
    elif change in ('layout', 'inspection'):
        rec = m.record('stock')
        ev = dataclasses.replace(rec.evidence, **({'layout_supported': False} if change == 'layout'
                                                 else {'inspection_failures': ('test',)}))
        m = dataclasses.replace(m, core=tuple(dataclasses.replace(r, evidence=ev) if r.key == 'stock' else r for r in m.core))
    else:
        m = None
    result = REVIEW.module(m, paths['stock'], gate)
    assert result['rows'] == [] and result['withheld']['verification'] == 'UNAVAILABLE'
    assert 'SYN-DUP' not in json.dumps(result)


def test_served_review_rechecks_source_and_marker_on_every_request(tmp_path, monkeypatch):
    folder, paths, frames, m, gate = case(tmp_path)
    out = tmp_path / 'dashboard.html'
    DB.build(folder, out, sources=paths, frames=frames, manifest=m)
    m = dataclasses.replace(m, dashboard_sha256=GEN.sha256_file(out))
    monkeypatch.setattr(APP, 'DATA_FOLDER', folder)
    body = out.read_bytes()
    assert APP._dashboard_authorized(body, m)[0]
    # HTML integrity still protects the marker and raw rows.
    assert APP._dashboard_authorized(body.replace(b'SYN-DUP', b'SYN-NEW'), m)[1] == 'bytes_mismatch'
    assert not REVIEW.authorize(body.replace(REVIEW.KEY.encode(), b'context.full'), m, folder)
    write_stock(folder, [['SYN-NEW', 'Yeni kayıt', 2, 1, 1, 3, 2, None]])
    assert APP._dashboard_authorized(body, m)[1] == 'review_source_unavailable'
    Path(paths['stock']).unlink()
    assert APP._dashboard_authorized(body, m)[1] == 'review_source_unavailable'


def test_warning_html_escapes_source_labels_and_preserves_unknown_dates(tmp_path):
    folder, paths, frames, m, gate = case(tmp_path)
    rec = m.record('stock')
    ev = dataclasses.replace(rec.evidence, report_date=None, report_date_origin=None)
    m = dataclasses.replace(m, core=tuple(dataclasses.replace(r, file='<script>evil</script>.xlsx', evidence=ev)
                                         if r.key == 'stock' else r for r in m.core))
    review = REVIEW.module(m, paths['stock'], gate)['review']
    rendered = REVIEW.warning_html(review)
    assert '<script>evil' not in rendered and '&lt;script&gt;' in rendered
    assert 'Bilinmiyor' in rendered and 'ERP kontrol sorusu' in rendered
    assert 'Doğrulama kaydı: invalid' in rendered


def test_identical_source_generation_does_not_change_html_on_rebuild(tmp_path):
    folder, paths, frames, manifest, gate = case(tmp_path)
    out_a, out_b = tmp_path / 'a.html', tmp_path / 'b.html'
    later = dataclasses.replace(manifest, created_at='2099-01-01T00:00:00')
    assert manifest.core_id == later.core_id
    DB.build(folder, out_a, sources=paths, frames=frames, manifest=manifest)
    DB.build(folder, out_b, sources=paths, frames=frames, manifest=later)
    assert out_a.read_bytes() == out_b.read_bytes()
