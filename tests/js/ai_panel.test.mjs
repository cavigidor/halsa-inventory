// M5-2: behaviour of the AI Aksiyon Merkezi tab in a DOM (jsdom), against a fake local server.
// Run through tests/test_m5_ui.py, which builds the dashboard from SYNTHETIC data and dumps real
// API responses from the FastAPI app (mock provider, no network) into AI_FIXTURES.
// Nothing here calls a real provider or the network.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const HTML_PATH = process.env.AI_DASH_HTML, FIX_PATH = process.env.AI_FIXTURES;
if (!HTML_PATH || !FIX_PATH) {
  test('fixtures missing (run via pytest tests/test_m5_ui.py)', { skip: true }, () => {});
} else {
const HTML = readFileSync(HTML_PATH, 'utf8');
const FIX = JSON.parse(readFileSync(FIX_PATH, 'utf8'));
const clone = (x) => JSON.parse(JSON.stringify(x));
const MSG = {
  offline: 'Sunucu çalışmıyor. Terminalde uvicorn app:app --host 127.0.0.1 çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır.',
  warming: 'Veriler hazırlanıyor…',
  disabled: 'AI kapalı (.env: AI_PROVIDER=openai)',
  empty: 'Bugün için açık aksiyon yok.',
  mismatch: 'Pano ve sunucu farklı veri tarihleri kullanıyor; veriyi yenileyin.',
};

function aiText(a, n) {
  return { ...a, interpretation: `Yorum ${n}`, recommendation: `Öneri ${n}`, ai_cached: false,
           evidence: [{ fact_id: 'F001', label: 'Python etiketi', display_value: a.facts[0] }] };
}
function okEnrich(base) {
  return (body) => {
    const ids = body.action_ids;
    return { json: { ai_available: true, ai_reason: null, ai_error_category: null, ai_warnings: [],
      provider_called: true, enriched: ids, cached: [], failed: [],
      actions: ids.map((id, i) => aiText(base.find((a) => a.action_id === id), i + 1)) } };
  };
}

function setup(opts = {}) {
  const health = opts.health || clone(FIX.health);
  const cats = opts.categories || clone(FIX.categories);
  const actions = opts.actions || clone(FIX.actions);
  const calls = [];
  const routes = {
    '/api/health': opts.healthFn || (() => ({ json: health })),
    '/api/agent/categories': () => ({ json: cats }),
    '/api/agent/actions': () => ({ json: actions }),
    '/api/agent/actions/enrich': opts.enrich || okEnrich(actions.actions),
  };
  async function fakeFetch(url, init = {}) {
    const method = (init.method || 'GET').toUpperCase();
    const path = new URL(url, 'http://127.0.0.1:8000/').pathname;
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ path, method, headers: { ...(init.headers || {}) }, body });
    if (opts.offline) throw new TypeError('Failed to fetch');
    const h = routes[path];
    if (!h) return { ok: false, status: 404, json: async () => ({}) };
    const r = await h(body, calls);
    const status = r.status || 200;
    return { ok: status >= 200 && status < 300, status, json: async () => r.json };
  }
  const dom = new JSDOM(HTML, {
    url: opts.url || 'http://127.0.0.1:8000/', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(w) { w.fetch = fakeFetch; w.__AI_POLL_MS = 5; },
  });
  const doc = dom.window.document;
  const q = (s) => doc.querySelector('#ai ' + s), qa = (s) => [...doc.querySelectorAll('#ai ' + s)];
  return { dom, win: dom.window, doc, calls, q, qa, actions,
    open: () => doc.querySelector('.tab[data-t="ai"]').click(),
    posts: () => calls.filter((c) => c.method !== 'GET') };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 3000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (fn()) return; await sleep(5); }
  throw new Error('condition not met in time');
}
const visible = (n) => n && !n.hidden;
const cardsReady = (t) => () => t.qa('.aic').length > 0;

// 1, 2, 3 — the tab and panel exist next to the untouched tabs; loading makes no AI call
test('tab is lazy and loading it never calls enrichment', async () => {
  const t = setup();
  assert.equal(t.doc.querySelectorAll('.tab[data-t="ai"]').length, 1);
  assert.ok(t.doc.getElementById('ai').classList.contains('panel'));
  await sleep(20);
  assert.equal(t.calls.length, 0, 'nothing is fetched before the tab is opened');
  t.open();
  await until(cardsReady(t));
  assert.ok(t.doc.getElementById('ai').classList.contains('on'));
  assert.deepEqual(t.calls.map((c) => c.method + ' ' + c.path).sort(),
    ['GET /api/agent/actions', 'GET /api/agent/categories', 'GET /api/health']);
  // Python order and Python content, unchanged
  assert.deepEqual(t.qa('.aic').map((c) => c.dataset.id), t.actions.actions.map((a) => a.action_id));
  const first = t.actions.actions[0], c0 = t.qa('.aic')[0];
  assert.equal(c0.querySelector('.aic-title').textContent, first.title);
  assert.equal(c0.querySelector('.aic-reason').textContent, 'Neden: ' + first.reason);
  assert.deepEqual([...c0.querySelectorAll('.aic-facts li')].map((l) => l.textContent), first.facts);
  // opening filters and switching tabs back and forth still makes no POST
  t.qa('.ai-f')[1].click(); t.qa('.ai-f')[0].click(); t.open();
  await sleep(30);
  assert.equal(t.posts().length, 0);
  assert.equal(t.calls.length, 3, 'the tab loads once');
});

// 5 — filters and badges come from GET /api/agent/categories, including a category the UI never saw
test('filters and badges are server-described (a new category needs no UI change)', async () => {
  const cats = clone(FIX.categories);
  cats.push({ key: 'zz_future', label_tr: 'Gelecek Kaynak', color: '#123456', entity_type: 'customer', source: 'future' });
  const actions = clone(FIX.actions);
  const extra = { ...clone(actions.actions[0]), action_id: 'zz_future:900.00.0099:abc', category: 'zz_future',
                  source: 'future', title: 'GELECEK — Sentetik Müşteri', score: 1, priority: 'low' };
  actions.actions.push(extra); actions.count += 1;
  const t = setup({ categories: cats, actions });
  t.open(); await until(cardsReady(t));
  const labels = t.qa('.ai-f').map((b) => b.textContent.replace(/ \(\d+\)$/, ''));
  assert.deepEqual(labels, ['Tümü', ...cats.map((c) => c.label_tr)]);
  const fut = t.qa('.ai-f').find((b) => b.dataset.k === 'zz_future');
  assert.equal(fut.textContent, 'Gelecek Kaynak (1)');
  fut.click();
  assert.deepEqual(t.qa('.aic').map((c) => c.dataset.id), [extra.action_id]);
  const badge = t.q('.aic .aic-cat');
  assert.equal(badge.textContent, 'Gelecek Kaynak');
  assert.match(badge.style.background, /rgb\(18, 52, 86\)|#123456/);
});

// 6, 7, 8 — explicit top 5 then next 5; header; ≤5 ids; running state does not block the cards
test('top 5, then next 5: one POST each, ≤5 ids in Python order, X-StockAgent header', async () => {
  let release;
  const base = clone(FIX.actions);
  const ok = okEnrich(base.actions);
  const t = setup({ actions: base, enrich: (body) => new Promise((r) => { release = () => r(ok(body)); }) });
  t.open(); await until(cardsReady(t));
  const ids = base.actions.map((a) => a.action_id);
  assert.ok(ids.length > 5, 'synthetic queue has more than 5 actions');
  assert.ok(visible(t.q('.ai-gen')));
  assert.ok(!visible(t.q('.ai-next')), '"Sonraki 5" appears only after a first batch');
  t.q('.ai-gen').click();
  await until(() => t.posts().length === 1);
  // running: status shown, buttons disabled, cards and filters still usable
  assert.match(t.q('.ai-status').textContent, /^Oluşturuluyor…/);
  assert.ok(t.q('.ai-gen').disabled);
  assert.equal(t.qa('.aic').length, ids.length);
  t.qa('.ai-f')[1].click(); t.qa('.ai-f')[0].click();
  assert.equal(t.qa('.aic').length, ids.length);
  const p1 = t.posts()[0];
  assert.equal(p1.path, '/api/agent/actions/enrich');
  assert.equal(p1.headers['X-StockAgent'], '1');
  assert.equal(p1.headers['Content-Type'], 'application/json');
  assert.deepEqual(p1.body.action_ids, ids.slice(0, 5));
  release();
  await until(() => t.qa('.aic-ai').length === 5);
  // updated in place: AI block on exactly the first five, facts still there
  t.qa('.aic').forEach((c, i) => {
    assert.equal(!!c.querySelector('.aic-ai'), i < 5);
    assert.ok(c.querySelector('.aic-facts li'));
  });
  assert.equal(t.q('.aic .aic-int').textContent, 'Yorum 1');
  assert.equal(t.q('.aic .aic-ai-h').firstChild.textContent, '✨ AI yorumu (öneridir)');
  assert.ok(visible(t.q('.ai-next')));
  t.q('.ai-next').click();
  await until(() => t.posts().length === 2);
  assert.deepEqual(t.posts()[1].body.action_ids, ids.slice(5, 10));
  release();
  await until(() => t.qa('.aic-ai').length === Math.min(ids.length, 10));
  for (const p of t.posts()) {
    assert.ok(p.body.action_ids.length >= 1 && p.body.action_ids.length <= 5);
    assert.equal(p.headers['X-StockAgent'], '1');
  }
  if (ids.length <= 10) assert.ok(!visible(t.q('.ai-gen')) && !visible(t.q('.ai-next')));
});

test('per-action control sends exactly that one id and leaves other cards untouched', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const before = t.qa('.aic');
  before[2].querySelector('.ai-one').click();
  await until(() => t.qa('.aic-ai').length === 1);
  assert.deepEqual(t.posts().map((p) => p.body.action_ids), [[before[2].dataset.id]]);
  assert.equal(t.posts()[0].headers['X-StockAgent'], '1');
  const after = t.qa('.aic');
  after.forEach((c, i) => { if (i !== 2) assert.equal(c, before[i], 'unrelated card is the same node'); });
  assert.ok(after[2].querySelector('.aic-ai') && !after[2].querySelector('.ai-one'));
});

// 9 — AI disabled: deterministic cards, the note, no AI controls
test('AI disabled: actions still shown, note shown, AI controls hidden', async () => {
  const health = { ...clone(FIX.health), ai_available: false, ai_reason: 'AI_PROVIDER=disabled' };
  const actions = { ...clone(FIX.actions), ai_available: false };
  const t = setup({ health, actions });
  t.open(); await until(cardsReady(t));
  assert.equal(t.qa('.aic').length, actions.actions.length);
  assert.equal(t.q('.ai-aist').textContent, MSG.disabled);
  assert.ok(!visible(t.q('.ai-gen')) && !visible(t.q('.ai-next')));
  assert.equal(t.qa('.ai-one').length, 0);
  assert.equal(t.posts().length, 0);
});

// 10 — cached AI renders without a call and is never regenerated automatically
test('cached AI text renders without any POST; top 5 skips cached actions', async () => {
  const actions = clone(FIX.actions);
  actions.actions[0] = { ...aiText(actions.actions[0], 'C'), ai_cached: true, ai_generated_at: '2026-10-10T09:00:00' };
  const t = setup({ actions });
  t.open(); await until(cardsReady(t));
  const c0 = t.qa('.aic')[0];
  assert.equal(c0.querySelector('.aic-int').textContent, 'Yorum C');
  assert.equal(c0.querySelector('.aic-rec').textContent, 'Öneri: Öneri C');
  assert.equal(c0.querySelector('.aic-cached').textContent, 'önbellekten');
  assert.equal(c0.querySelector('.aic-chip').textContent, 'Python etiketi: ' + actions.actions[0].facts[0]);
  assert.equal(c0.querySelector('.ai-one'), null);
  await sleep(30);
  assert.equal(t.posts().length, 0);
  t.q('.ai-gen').click();
  await until(() => t.posts().length === 1);
  assert.deepEqual(t.posts()[0].body.action_ids, actions.actions.slice(1, 6).map((a) => a.action_id));
});

test('a real enrich response from the backend (mock provider) renders as an AI block', async () => {
  const sample = clone(FIX.enrich_sample);
  const t = setup({ enrich: () => ({ json: sample }) });
  t.open(); await until(cardsReady(t));
  t.q('.ai-gen').click();
  await until(() => t.qa('.aic-ai').length === sample.enriched.length);
  const a = sample.actions[0], c = t.qa('.aic')[0];
  if (a.interpretation) assert.equal(c.querySelector('.aic-int').textContent, a.interpretation);
  assert.equal(c.querySelectorAll('.aic-chip').length, a.evidence.length);
});

// 11 — escaping: model text, Python text and server labels are inert text
test('AI and server text is escaped (rendered as text, never as HTML)', async () => {
  const bad = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
  const cats = clone(FIX.categories); cats[0].label_tr = bad; cats[0].color = 'red;background:url(x)';
  const actions = clone(FIX.actions);
  const a0 = actions.actions[0];
  Object.assign(a0, { title: bad, reason: bad, facts: [bad], category: cats[0].key,
    interpretation: bad, recommendation: bad, evidence: [{ fact_id: 'F001', label: bad, display_value: bad }] });
  const t = setup({ categories: cats, actions });
  t.open(); await until(cardsReady(t));
  await sleep(20);
  const panel = t.doc.getElementById('ai');
  assert.equal(panel.querySelectorAll('img').length, 0);
  assert.equal(panel.querySelectorAll('.ai-list script, .ai-filters script').length, 0);
  assert.equal(t.win.__pwned, undefined);
  const c0 = t.qa('.aic')[0];
  assert.equal(c0.querySelector('.aic-title').textContent, bad);
  assert.equal(c0.querySelector('.aic-int').textContent, bad);
  assert.equal(c0.querySelector('.aic-cat').style.background, '', 'invalid server colour is ignored');
  assert.ok(t.qa('.ai-f').some((b) => b.textContent.startsWith(bad)));
});

// 12 — no open actions
test('empty queue shows the empty state and no AI controls', async () => {
  const t = setup({ actions: { ...clone(FIX.actions), count: 0, actions: [] } });
  t.open();
  await until(() => visible(t.q('.ai-state')) && t.q('.ai-state').textContent === MSG.empty);
  assert.equal(t.qa('.aic').length, 0);
  assert.ok(!visible(t.q('.ai-gen')) && !visible(t.q('.ai-filters')));
});

// 13 — context warming: polls /api/health only, then loads
test('warming: shows "Veriler hazırlanıyor…", polls health, then loads without AI', async () => {
  let n = 0;
  const ready = clone(FIX.health);
  const t = setup({ healthFn: () => ({ json: ++n < 3 ? { ...ready, context_ready: false, as_of_data_date: null } : ready }) });
  t.open();
  await until(() => t.q('.ai-state').textContent === MSG.warming);
  assert.ok(!t.calls.some((c) => c.path === '/api/agent/actions'), 'actions are not requested while warming');
  await until(cardsReady(t));
  assert.ok(n >= 3);
  assert.ok(!visible(t.q('.ai-state')));
  assert.equal(t.posts().length, 0);
});

test('warm-up failure shows the server error as text', async () => {
  const t = setup({ health: { ...clone(FIX.health), context_ready: false, context_error: 'FileNotFoundError: <b>x</b>' } });
  t.open();
  await until(() => visible(t.q('.ai-state')) && t.q('.ai-state').textContent.startsWith('Veriler yüklenemedi: '));
  assert.ok(t.q('.ai-state').textContent.endsWith('<b>x</b>'));
  assert.equal(t.q('.ai-state b'), null);
});

// 14 — data-date mismatch
test('data-date mismatch shows the yellow warning; matching dates do not', async () => {
  const t = setup({ health: { ...clone(FIX.health), as_of_data_date: '2026-07-31' } });
  t.open(); await until(cardsReady(t));
  assert.ok(visible(t.q('.ai-warn')));
  assert.ok(t.q('.ai-warn').textContent.startsWith(MSG.mismatch));
  const same = setup();
  same.open(); await until(cardsReady(same));
  assert.ok(!visible(same.q('.ai-warn')));
  assert.match(same.q('.ai-date').textContent, /^Veri: \d{1,2} \S+ \d{4}$/);
});

test('backend offline: offline guidance, other tabs keep working', async () => {
  const t = setup({ offline: true });
  t.open();
  await until(() => t.q('.ai-state').textContent === MSG.offline);
  assert.equal(t.qa('.aic').length, 0);
  const other = t.doc.querySelector('.tab:not([data-t="ai"]):not([data-t="overview"])');
  other.click();
  const panel = t.doc.getElementById(other.dataset.t);
  assert.ok(panel.classList.contains('on') && panel.querySelector('table'));
  assert.ok(!t.doc.getElementById('ai').classList.contains('on'));
});

test('opened from file://: offline guidance without any request', async () => {
  const t = setup({ url: 'file:///Users/someone/dashboard.html' });
  t.open();
  await until(() => t.q('.ai-state').textContent === MSG.offline);
  assert.equal(t.calls.length, 0);
});

test('AI error categories map to the approved Turkish copy and never hide the facts', async () => {
  const cases = { quota: 'OpenAI kredisi yetersiz', rate_limit: 'Çok fazla istek, biraz sonra deneyin',
    timeout: "OpenAI'ye ulaşılamadı", connection: "OpenAI'ye ulaşılamadı", auth: 'API anahtarı geçersiz',
    numeric_guard: 'AI metni güvenlik kontrolünden geçmedi, gösterilmiyor',
    fact_reference: 'AI metni güvenlik kontrolünden geçmedi, gösterilmiyor', malformed: 'AI yorumu üretilemedi' };
  for (const [cat, msg] of Object.entries(cases)) {
    const base = clone(FIX.actions);
    const t = setup({ actions: base, enrich: (body) => ({ json: { ai_available: true, ai_error_category: cat,
      provider_called: true, enriched: [], cached: [], failed: body.action_ids,
      actions: base.actions.filter((a) => body.action_ids.includes(a.action_id)) } }) });
    t.open(); await until(cardsReady(t));
    t.q('.ai-gen').click();
    await until(() => t.qa('.aic-err').length === 5);
    assert.equal(t.q('.ai-status').textContent, msg, cat);
    t.qa('.aic').slice(0, 5).forEach((c) => {
      assert.equal(c.querySelector('.aic-err').textContent, msg);
      assert.ok(c.querySelector('.aic-facts li') && c.querySelector('.aic-reason'), 'facts stay visible');
      assert.ok(c.querySelector('.ai-one'), 'per-card retry stays available');
    });
    t.dom.window.close();
  }
  const t = setup({ enrich: () => ({ status: 500, json: { detail: 'x' } }) });
  t.open(); await until(cardsReady(t));
  t.q('.ai-gen').click();
  await until(() => t.q('.ai-status').textContent === 'AI yorumu üretilemedi');
  assert.equal(t.qa('.aic').length, FIX.actions.actions.length);
});

test('enrich reporting AI unavailable switches the tab to the disabled state', async () => {
  const base = clone(FIX.actions);
  const t = setup({ actions: base, enrich: (body) => ({ json: { ai_available: false, ai_error_category: 'disabled',
    enriched: [], cached: [], failed: body.action_ids,
    actions: base.actions.filter((a) => body.action_ids.includes(a.action_id)) } }) });
  t.open(); await until(cardsReady(t));
  t.q('.ai-gen').click();
  await until(() => t.q('.ai-aist').textContent === MSG.disabled);
  assert.equal(t.qa('.ai-one').length, 0);
  assert.ok(!visible(t.q('.ai-gen')));
  assert.equal(t.qa('.aic').length, base.actions.length);
});
}
