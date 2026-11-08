"""V1 general report: father's B2B tracking (D-080), offline only.

Applies the shared `halsa_b2b` calculation — the same one the Klasik B2B variant uses — to the V1 report's modules:
the "B2B" registration column in Büyüme / Küçülme (after the representative), B2B Alımı / Sipariş (B2B) in the yearly
customer table, and a single "B2B Takip" table (İzahat last) right after the profitability section. Existing producers, thresholds,
masks and warnings are unchanged; nothing here is a registered metric, an API, an action or AI input.
"""
from __future__ import annotations

import halsa_b2b as HB
from services import sales_review as SALES


def apply(modules: dict, raw, header, registration: dict, *, source: str, sha256: str, generation: str,
          review_days: int = HB.REVIEW_DAYS, registration_complete: bool = False):
    import dashboard_builder as DB
    base = DB.load_sales(None, raw)
    result = HB.analyse(raw, base, DB.cust_attrs(base), registration, header, review_days=review_days)
    if 'momentum' in modules and modules['momentum'].get('rows') is not None:
        HB.add_flag_column(modules['momentum'], result, after='rep')
    yearly = modules.get('purchases_yearly')
    if yearly and yearly.get('cols'):
        ix = next(i for i, c in enumerate(yearly['cols']) if c['k'] == 'amount') + 1
        yearly['cols'][ix:ix] = [dict(k='b2b_amount', l='B2B Alımı', money=1, exact=1),
                                 dict(k='b2b_orders', l='Sipariş (B2B)', n=1),
                                 dict(k='b2b_mixed', l='Karışık kanal belgesi', n=1)]
        for r in yearly['rows']:
            f = HB.year_fields(result, r.get('code'), r['year']) if r.get('code') is not None else \
                {'b2b': None, 'b2b_orders': None, 'b2b_mixed': None}
            r['b2b_amount'], r['b2b_orders'], r['b2b_mixed'] = f['b2b'], f['b2b_orders'], f['b2b_mixed']
        yearly['note'] = (yearly.get('note') or '') + (' B2B Alımı = AG sütunu "B2B" olan satırların Nettutar toplamı; '
                                                       'Sipariş (B2B) = farklı sipariş belgesi (fatura değil).')
    c = result['counts']
    review = dict(metric='b2b.tracking', status='PRELIMINARY', verification='Kayıtlı ölçüt değil — ön inceleme',
                  source=f"{source} · B2B kayıt: {result['registration']['file']}"
                         + ('' if c['registration_status'] == 'ok' else ' (YOK)'),
                  sha256=sha256 + (f" · kayıt {result['registration']['sha256']}" if result['registration']['sha256'] else ''),
                  generation=generation, report_date=None, date_origin='beyan edilmedi',
                  observed_start=None, observed_end=c['asof'],
                  formula=(f"Tek B2B Takip tablosu, İzahat son sütunda: B2B satışı = satış dosyası AG sütunu tam "
                           f"'B2B'; B2B Kaydı VAR = {HB.REGISTRATION_FILE} B sütununda sipariş müşterisi koduyla birebir; "
                           "belirsiz eşleşme = Kontrol"
                           + ("; listede olmayan = YOK (liste tamlığı beyan edildi)" if registration_complete
                              else "; listede olmayan = 'Listede yok' (liste tamlığı teyit edilmedi)")
                           + f". 'Son zamanlarda' = son {c['review_days']} gün (geçici)."),
                  warnings=[{k: w[k] for k in ('code', 'title', 'issue', 'evidence', 'question', 'resolution',
                                               'severity')} for w in result['warnings']])
    mod = HB.module(result, review_html=SALES.warning_html(review), registration_complete=registration_complete)
    result['counts']['unified'] = HB.unified_counts(mod['rows'])
    mod['page_size'] = 100
    return HB.insert_after(modules, 'margin', 'b2b', mod), result
