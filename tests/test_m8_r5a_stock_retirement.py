"""D-070: synthetic-only stock retirement, source preservation and real-gate acceptance."""
import copy
import hashlib
from pathlib import Path

import pandas as pd
import pytest

import dashboard_builder as DB
from services import accuracy as ACC, action_sources as AS, context as CTX, evidence as EV
from services import vkm as VKM, refresh as RF
from services.agent import BusinessActionAgent
from services.ai.mock_provider import MockProvider
from services.entity import ContextRepository
from services.stock_policy import RETIREMENT_CODE
from tests.fixtures import synthetic_erp as SE
from tests.fixtures.xlsx_synth import F, write_xlsx
from tests.test_m8_accuracy import client  # noqa: F401 — real API gate, real synthetic source context
from tests.test_m8_r3_metric_diagnostics import manifest_of

pytestmark = pytest.mark.production_gate


def test_reading_stock_keeps_cached_formulas_and_signed_values_in_original_file(tmp_path):
    import openpyxl
    headers = list(SE.stock_frame().columns)
    rows = [['Sentetik'], [], [], headers,
            ['SYN-FORMULA', 'Sentetik Formül', F('0-3.25', -3.25), F('1/2', .5), 1, 2, 100, '']]
    path = write_xlsx(tmp_path / 'synthetic.xlsx', {'Stock': rows})
    before = path.read_bytes()
    assert DB.m_stock(path) == ([], [], 1)
    workbook = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        assert workbook['Stock']['C5'].value == '=0-3.25'
        assert workbook['Stock']['D5'].value == '=1/2'
    finally:
        workbook.close()
    assert DB.read_stock(path)[DB.SC['stock']].tolist() == [-3.25]
    assert path.read_bytes() == before


@pytest.mark.parametrize("balance", [None, 0, 7.5, -3.25])
@pytest.mark.parametrize("coverage,minimum,maximum", [(None, None, None), (-8, 1, 2), (50, 1, 2), (1, 900, 1000)])
def test_no_threshold_or_signed_balance_can_create_a_recommendation(balance, coverage, minimum, maximum):
    frame = SE.stock_frame().iloc[:1].copy()
    frame[DB.SC['stock']] = balance
    frame[DB.SC['cov']] = coverage
    frame[DB.SC['min']] = minimum
    frame[DB.SC['max']] = maximum
    frame[DB.SC['status']] = 'ÜRETİMDEN KALKTI'  # untrusted export text cannot create a classification
    before = frame.copy(deep=True)
    assert DB.m_stock(None, frame) == ([], [], 1)
    pd.testing.assert_frame_equal(frame, before)


def test_duplicate_and_negative_source_rows_are_not_rewritten_or_combined(tmp_path):
    frame = SE.stock_frame().iloc[:2].copy()
    frame[DB.SC['code']] = ['SYN-DUP', 'SYN-DUP']
    frame[DB.SC['stock']] = [-3.25, 12.5]
    folder = SE.write_all(str(tmp_path / 'source'))
    path = Path(folder) / SE.STOCK_FILE
    with pd.ExcelWriter(path) as writer:
        frame.to_excel(writer, startrow=3, index=False)
    before = path.read_bytes()
    raw = DB.read_stock(path)
    assert DB.m_stock(path) == ([], [], 2)
    assert path.read_bytes() == before
    assert raw[DB.SC['stock']].tolist() == [-3.25, 12.5]
    assert DB.stock_row_counts(raw)['duplicate_code_occurrences'] == 1
    manifest, _ = manifest_of(folder)
    record = manifest.record('stock')
    assert record.sha256 == hashlib.sha256(before).hexdigest()
    assert record.evidence.duplicate_records == 1
    assert any(state == ACC.State.INVALID for state, _ in record.evidence.problems())
    assert any(issue.check == 'stock_duplicate_code' for issue in VKM.inspect_core(record).issues)


def test_stock_changes_do_not_change_unrelated_calculations_or_input_frames(synthetic_dir):
    sources = DB.select_sources(synthetic_dir)
    frames = {'stock': DB.read_stock(sources['stock']), 'sales': DB.read_sales_raw(sources['sales']),
              'aging': DB.read_aging_raw(sources['aging'])}
    first = CTX.build_context(synthetic_dir, inflation_rate=.4, sources=sources, frames=frames)
    changed = {k: v.copy(deep=True) for k, v in frames.items()}
    changed['stock'][DB.SC['min']] = 99999
    changed['stock'][DB.SC['max']] = -1
    changed['stock'][DB.SC['stock']] = [-5.5] * len(changed['stock'])
    before = {k: v.copy(deep=True) for k, v in changed.items()}
    second = CTX.build_context(synthetic_dir, inflation_rate=.4, sources=sources, frames=changed)
    for key in ('collections', 'winback', 'shrinking', 'risk', 'actions'):
        assert second[key] == first[key], key
    for key in changed:
        pd.testing.assert_frame_equal(changed[key], before[key])
    for ctx in (first, second):
        assert ctx['low_stock'] == ctx['overstock'] == []
        wc = ctx['company']['working_capital']
        assert wc['low_stock_count'] is None and wc['overstock_value'] is None
        assert not any(a['category'] == 'overstock' for a in ctx['actions'])


def test_stale_clearance_candidates_and_stock_ai_are_refused_without_provider_calls():
    legacy = {'code': 'SYN-OLD', 'name': 'Sentetik Eski', 'stock': -3.25, 'cov': -1,
              'min': 5, 'inv': 99999, 'buyers': 2, 'top_buyers': []}
    before = copy.deepcopy(legacy)
    assert DB.m_overstock_move(pd.DataFrame(), [legacy]) == []
    assert AS.produce_overstock({'over_cand': [legacy]}) == []
    assert ContextRepository(context={'low_stock': [legacy], 'overstock': [legacy]}).product('SYN-OLD') is None
    class NeverCall(MockProvider):
        def generate_structured(self, *args, **kwargs):
            pytest.fail('retired stock analysis reached AI')
    facts = {**legacy, 'product_code': 'SYN-OLD', 'coverage': -1, 'min_level': 5, 'status': 'overstock'}
    data, result = BusinessActionAgent(NeverCall()).analyze_product(facts)
    assert data is None and result['category'] == 'stock_thresholds_retired'
    result = BusinessActionAgent(NeverCall()).enrich_actions([{'category': 'overstock', 'facts': ['Eski hedef']}])
    assert result['by_ref'] == {} and result['category'] == 'stock_thresholds_retired'
    payload = EV.product_packet(facts).to_payload()
    assert {'name': 'stock_status', 'value': 'unknown'} in payload['classifications']
    labels = {f['label'] for f in payload['facts']}
    assert not labels.intersection({'Kapsama', 'Min seviye', 'Fazla stok (envanter) değeri'})
    balance = next(f for f in payload['facts'] if f['label'].startswith('İşaretli ERP bakiyesi'))
    assert balance['display_value'] == '-3,25'
    assert legacy == before


def test_no_sales_is_not_discontinued_and_no_recommendation_total_is_zero(synthetic_dir, tmp_path):
    stock = DB.read_stock(DB.select_sources(synthetic_dir)['stock'])
    stock.loc[len(stock)] = ['SYN-NO-SALES', 'Sentetik Satışsız', 10, 999, 1, 2, 500, '']
    before = stock.copy(deep=True)
    assert DB.m_stock(None, stock) == ([], [], len(stock))
    pd.testing.assert_frame_equal(stock, before)
    stats = {}
    DB.build(synthetic_dir, str(tmp_path / 'page.html'), stats_out=stats)
    assert stats['low'] is stats['over'] is stats['overstock_value'] is None
    text = (tmp_path / 'page.html').read_text()
    assert 'Kapsama minseviye altında' not in text and 'Negatif kapsama = fazla satılmış' not in text
    assert not any(m.certified == ACC.State.VERIFIED for m in ACC.REGISTER.values())
    assert ACC.GENERATION_BOUND_API_EVIDENCE is False
    for key in ('stock.reorder', 'stock.overstock'):
        decision = ACC.current_gate().decide(key)
        assert not decision.displayable and RETIREMENT_CODE in decision.codes
    assert not ACC.current_gate().decide('stock.clearance_buyers').displayable


def test_real_api_withholds_stock_and_ai_without_promoting_other_metrics(client):  # noqa: F811
    for path in ('/api/context', '/api/agent/product/SYN-OLD'):
        response = client.get(path)
        assert response.status_code == 409
        assert 'SYN-OLD' not in response.text
    response = client.post('/api/agent/product/SYN-OLD/analyze')
    assert response.status_code == 409


def test_mixed_ai_evidence_keeps_unrelated_action_references(synthetic_ctx):
    customer = next(a for a in synthetic_ctx['actions'] if a['entity_type'] == 'customer')
    packet = EV.actions_packet([{'category': 'overstock', 'facts': ['Eski hedef: 999']}, customer])
    assert [item['action_ref'] for item in packet.items] == ['A02']
    assert packet.items[0]['category'] == customer['category']
    assert not any(f['display_value'] == '999' for f in packet.facts)


def test_refresh_does_not_accept_zero_in_place_of_withdrawn_total(synthetic_dir, tmp_path):
    ctx = CTX.build_context(synthetic_dir, inflation_rate=.4)
    stats, path = {}, tmp_path / 'page.html'
    DB.build(synthetic_dir, str(path), stats_out=stats)
    date = ctx['as_of_data_date']
    assert RF.verify(ctx, stats, str(path), date)['ok']
    bad = RF.verify(ctx, {**stats, 'overstock_value': 0}, str(path), date)
    assert not bad['ok'] and any('fazla stok değeri' in e for e in bad['errors'])


def test_combined_metric_requirements_follow_remaining_calculations():
    assert ACC.REGISTER['actions.queue'].depends_on == (
        'receivables.collections', 'customers.winback', 'actions.real_decline')
    assert ACC.REGISTER['context.full'].depends_on == (
        'stock.product_counts', 'customers.winback', 'receivables.collections', 'receivables.risk', 'actions.queue')
    assert RETIREMENT_CODE in ACC.current_gate().decide('agent.product_facts').codes
    assert all(not ACC.current_gate().decide(key).displayable for key in ('actions.queue', 'context.full'))
