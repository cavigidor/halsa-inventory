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
  // Fake action-state store (mirrors services/store.is_hidden), shared across "reloads" via opts.server.
  const server = opts.server || { status: {} };
  const today = isoLocal(new Date());
  const hidden = (s) => !!s && (s.status === 'completed' || s.status === 'ignored' || (s.status === 'deferred' && s.until > today));
  const STATUS = { complete: 'completed', dismiss: 'ignored', defer: 'deferred', reopen: 'open' };
  const stateFn = opts.stateFn || ((kind, id, body) => {
    const until = kind === 'defer' ? body.until : null;
    server.status[id] = { status: STATUS[kind], until };
    return { json: { ok: true, action_id: id, status: STATUS[kind], defer_until: until } };
  });
  const customers = opts.customers || clone(FIX.customers);
  const customerFn = opts.customerFn || ((code) => (customers[code] ? { json: customers[code] } : { status: 404, json: { detail: 'yok' } }));
  const draftFn = opts.draftFn || ((b) => ({ json: { ...clone(FIX.draft_sample), channel: b.channel, purpose: b.purpose } }));
  const copied = [], reloads = [];
  const refreshFn = opts.refreshFn || (() => ({ json: { ok: true, step: 'done', step_tr: 'tamam', message: 'Veriler yenilendi.',
    as_of_data_date: '2026-07-15', previous_as_of_data_date: FIX.health.as_of_data_date, warnings: [], errors: [],
    sources: { stock: 'STOK Sentetik.xlsx', sales: 'Sentetik satislar.xlsx', aging: 'Cari Sentetik.xlsx' }, counts: {} } }));
  const routes = {
    '/api/health': opts.healthFn || (() => ({ json: health })),
    '/api/agent/categories': () => ({ json: cats }),
    '/api/agent/actions': () => {
      const open = actions.actions.filter((a) => !hidden(server.status[a.action_id]));
      return { json: { ...actions, count: open.length, actions: open } };
    },
    '/api/agent/actions/enrich': opts.enrich || okEnrich(actions.actions),
  };
  // M8-CP1: the shared access script asks the server once who is logged in (owner by default here, like the real
  // owner session); that request is answered like the real server and not counted among the panel's calls.
  const session = opts.session || { configured: true, authenticated: true, csrf_token: 'test-csrf-token' };
  const mkResp = (status, json) => ({ ok: status >= 200 && status < 300, status, json: async () => json,
    clone() { return mkResp(status, json); } });
  async function fakeFetch(url, init = {}) {
    const method = (init.method || 'GET').toUpperCase();
    const path = new URL(url, 'http://127.0.0.1:8000/').pathname;
    const body = init.body ? JSON.parse(init.body) : null;
    if (path === '/api/auth/session' && method === 'GET') return mkResp(200, session);
    calls.push({ path, method, headers: { ...(init.headers || {}) }, body });
    if (opts.offline) throw new TypeError('Failed to fetch');
    const cm = /^\/api\/agent\/customer\/([^/]+)$/.exec(path);
    if (cm && method === 'GET') {
      const r = await customerFn(decodeURIComponent(cm[1]));
      if (r instanceof Error) throw r;
      const status = r.status || 200;
      return mkResp(status, r.json);
    }
    if (path === '/api/context/refresh' && method === 'POST') {
      const r = await refreshFn(body);
      if (r instanceof Error) throw r;
      const status = r.status || 200;
      return mkResp(status, r.json);
    }
    if (path === '/api/agent/draft-message' && method === 'POST') {
      const r = await draftFn(body);
      if (r instanceof Error) throw r;
      const status = r.status || 200;
      return mkResp(status, r.json);
    }
    const m = /^\/api\/actions\/([^/]+)\/(complete|defer|dismiss|reopen)$/.exec(path);
    if (m && method === 'POST') {
      const r = await stateFn(m[2], decodeURIComponent(m[1]), body || {});
      if (r instanceof Error) throw r;
      const status = r.status || 200;
      return mkResp(status, r.json);
    }
    const h = routes[path];
    if (!h) return mkResp(404, {});
    const r = await h(body, calls);
    const status = r.status || 200;
    return mkResp(status, r.json);
  }
  const dom = new JSDOM(HTML, {
    url: opts.url || 'http://127.0.0.1:8000/', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = fakeFetch; w.__AI_POLL_MS = 5; if (opts.undoMs) w.__AI_UNDO_MS = opts.undoMs;
      w.__aiReload = () => reloads.push(Date.now()); w.__AI_RELOAD_MS = 30; w.__AI_RELOAD_WARN_MS = 60;
      const writeText = opts.clipboardFail ? async () => { throw new Error('denied'); } : async (t) => { copied.push(t); };
      if (!opts.noClipboard) Object.defineProperty(w.navigator, 'clipboard', { value: { writeText }, configurable: true });
    },
  });
  const doc = dom.window.document;
  const q = (s) => doc.querySelector('#ai ' + s), qa = (s) => [...doc.querySelectorAll('#ai ' + s)];
  return { dom, win: dom.window, doc, calls, q, qa, actions, server, copied, reloads,
    refreshPosts: () => calls.filter((c) => c.path === '/api/context/refresh'),
    gets: (prefix) => calls.filter((c) => c.method === 'GET' && c.path.startsWith(prefix)),
    draftPosts: () => calls.filter((c) => c.path === '/api/agent/draft-message'),
    open: () => doc.querySelector('.tab[data-t="ai"]').click(),
    posts: () => calls.filter((c) => c.method !== 'GET'),
    statePosts: () => calls.filter((c) => c.method === 'POST' && c.path.startsWith('/api/actions/'))
      .map((c) => ({ ...c, path: decodeURIComponent(c.path) })),
    enrichPosts: () => calls.filter((c) => c.path === '/api/agent/actions/enrich') };
}
function isoLocal(d) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}
function inDays(n) { const d = new Date(); d.setDate(d.getDate() + n); return isoLocal(d); }
const AY = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
const trDate = (iso) => { const [y, m, d] = iso.split('-'); return `${+d} ${AY[+m - 1]} ${y}`; };
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
  assert.equal(p1.headers['X-CSRF-Token'], 'test-csrf-token', 'M8-CP1: the owner session CSRF token is attached');
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

// ---------------- M5-3: done / defer / dismiss with undo ----------------
const ids = (t) => t.qa('.aic').map((c) => c.dataset.id);
const card = (t, id) => t.qa('.aic').find((c) => c.dataset.id === id);
const STATE_FAIL = 'Aksiyon durumu kaydedilemedi.';

test('✓ Yapıldı: POST complete with the header, card leaves the queue, order kept, undo offered', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const before = ids(t), nodes = t.qa('.aic'), target = before[1];
  card(t, target).querySelector('.aic-done').click();
  await until(() => !card(t, target));
  const p = t.statePosts();
  assert.equal(p.length, 1);
  assert.equal(p[0].path, `/api/actions/${target}/complete`);
  assert.equal(p[0].headers['X-StockAgent'], '1');
  assert.deepEqual(ids(t), before.filter((x) => x !== target), 'Python order of the rest is unchanged');
  t.qa('.aic').forEach((c) => assert.ok(nodes.includes(c), 'other cards are not rebuilt'));
  assert.equal(t.q('.ai-f').textContent, `Tümü (${before.length - 1})`);
  const u = t.q('.ai-undo');
  assert.ok(visible(u));
  assert.equal(u.querySelector('.ai-undo-t').textContent, 'Aksiyon tamamlandı.');
  assert.equal(u.querySelector('.ai-undo-b').textContent, 'Geri al');
  assert.equal(t.enrichPosts().length, 0);
});

test('✕ Gizle: POST dismiss with the header and the undo message', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const target = ids(t).at(-1);
  card(t, target).querySelector('.aic-hide').click();
  await until(() => !card(t, target));
  assert.deepEqual(t.statePosts().map((p) => [p.path, p.headers['X-StockAgent']]), [[`/api/actions/${target}/dismiss`, '1']]);
  assert.equal(t.q('.ai-undo-t').textContent, 'Aksiyon gizlendi.');
  assert.equal(t.enrichPosts().length, 0);
});

test('⏸ Ertele: date defaults to +7 days, sends the chosen date, rejects past dates', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const target = ids(t)[0];
  card(t, target).querySelector('.aic-defer').click();
  const inp = card(t, target).querySelector('.aic-deferf input[type="date"]');
  assert.equal(inp.value, inDays(7));
  assert.equal(inp.min, inDays(1));
  inp.value = inDays(-2);
  card(t, target).querySelector('.aic-defer-ok').click();
  await sleep(20);
  assert.equal(t.statePosts().length, 0, 'a past date is not sent');
  assert.equal(card(t, target).querySelector('.aic-deferf .aic-serr').textContent, 'Geçerli bir ileri tarih seçin.');
  card(t, target).querySelector('.aic-defer-no').click();
  assert.equal(card(t, target).querySelector('.aic-deferf'), null, 'Vazgeç closes the picker');
  card(t, target).querySelector('.aic-defer').click();
  const chosen = inDays(12);
  card(t, target).querySelector('.aic-deferf input').value = chosen;
  card(t, target).querySelector('.aic-defer-ok').click();
  await until(() => !card(t, target));
  const p = t.statePosts();
  assert.equal(p.length, 1);
  assert.equal(p[0].path, `/api/actions/${target}/defer`);
  assert.equal(p[0].headers['X-StockAgent'], '1');
  assert.deepEqual(p[0].body, { until: chosen });
  assert.equal(t.q('.ai-undo-t').textContent, `Aksiyon ${trDate(chosen)} tarihine ertelendi.`);
});

test('Geri al: POST reopen, the card returns to its Python position with its cached AI, no AI request', async () => {
  const actions = clone(FIX.actions);
  actions.actions[2] = { ...aiText(actions.actions[2], 'C'), ai_cached: true };
  const t = setup({ actions });
  t.open(); await until(cardsReady(t));
  const before = ids(t), target = before[2];
  card(t, target).querySelector('.aic-done').click();
  await until(() => !card(t, target));
  t.q('.ai-undo-b').click();
  await until(() => !!card(t, target));
  assert.deepEqual(t.statePosts().map((p) => p.path), [`/api/actions/${target}/complete`, `/api/actions/${target}/reopen`]);
  assert.equal(t.statePosts()[1].headers['X-StockAgent'], '1');
  assert.deepEqual(ids(t), before, 'same order, no duplicate');
  assert.equal(card(t, target).querySelector('.aic-int').textContent, 'Yorum C');
  assert.equal(card(t, target).querySelector('.aic-cached').textContent, 'önbellekten');
  assert.ok(!visible(t.q('.ai-undo')));
  assert.equal(t.q('.ai-f').textContent, `Tümü (${before.length})`);
  assert.equal(t.enrichPosts().length, 0);
});

test('undo window: about 5 s by default; when it expires nothing more happens', async () => {
  const t = setup({ undoMs: 40 });
  t.open(); await until(cardsReady(t));
  const target = ids(t)[0];
  card(t, target).querySelector('.aic-hide').click();
  await until(() => visible(t.q('.ai-undo')));
  await until(() => !visible(t.q('.ai-undo')), 1000);
  await sleep(30);
  assert.deepEqual(t.statePosts().map((p) => p.path), [`/api/actions/${target}/dismiss`]);
  assert.ok(!card(t, target), 'the saved state stays');
  assert.equal(t.server.status[target].status, 'ignored');
  assert.match(HTML, /UNDO_MS=window\.__AI_UNDO_MS\|\|5000/, 'default undo window is 5 s');
});

test('failed state change: card stays with its facts and an error; no undo offered', async () => {
  for (const fail of [() => ({ status: 500, json: { detail: 'x' } }), () => new TypeError('Failed to fetch'),
                      () => ({ status: 404, json: { detail: 'yok' } })]) {
    const t = setup({ stateFn: fail });
    t.open(); await until(cardsReady(t));
    const before = ids(t), target = before[0];
    card(t, target).querySelector('.aic-done').click();
    await until(() => card(t, target)?.querySelector('.aic-serr'));
    const c = card(t, target);
    assert.equal(c.querySelector('.aic-serr').textContent, STATE_FAIL);
    assert.ok(c.querySelector('.aic-facts li') && c.querySelector('.aic-reason'));
    assert.ok([...c.querySelectorAll('.ai-sbtn')].every((b) => !b.disabled), 'buttons usable again');
    assert.deepEqual(ids(t), before);
    assert.ok(!visible(t.q('.ai-undo')));
    t.dom.window.close();
  }
});

test('failed undo: the error is shown and the card stays hidden (server state unchanged)', async () => {
  const server = { status: {} };
  const t = setup({ server, stateFn: (kind, id) => {
    if (kind === 'reopen') return { status: 500, json: {} };
    server.status[id] = { status: 'completed' };
    return { json: { ok: true, action_id: id, status: 'completed' } };
  } });
  t.open(); await until(cardsReady(t));
  const target = ids(t)[0];
  card(t, target).querySelector('.aic-done').click();
  await until(() => visible(t.q('.ai-undo')));
  t.q('.ai-undo-b').click();
  await until(() => t.q('.ai-undo-t')?.textContent === STATE_FAIL);
  assert.ok(!card(t, target));
  assert.equal(t.q('.ai-undo-b'), null);
});

test('a pending change disables the card buttons; a double click sends one request', async () => {
  let release;
  const t = setup({ stateFn: (kind, id) => new Promise((r) => { release = () => r({ json: { ok: true, action_id: id, status: 'completed' } }); }) });
  t.open(); await until(cardsReady(t));
  const target = ids(t)[0];
  const b = card(t, target).querySelector('.aic-done');
  b.click(); b.click();
  await until(() => t.statePosts().length === 1);
  assert.ok([...card(t, target).querySelectorAll('.ai-sbtn')].every((x) => x.disabled));
  assert.equal(t.qa('.aic').length, FIX.actions.actions.length, 'other cards stay usable');
  release();
  await until(() => !card(t, target));
  assert.equal(t.statePosts().length, 1);
});

test('filters stay correct after state changes (server categories, no reranking)', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const counts = {};
  FIX.actions.actions.forEach((a) => { counts[a.category] = (counts[a.category] || 0) + 1; });
  const [catKey, n] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0];
  const chip = () => t.qa('.ai-f').find((b) => b.dataset.k === catKey);
  chip().click();
  const inCat = FIX.actions.actions.filter((a) => a.category === catKey).map((a) => a.action_id);
  assert.deepEqual(ids(t), inCat);
  card(t, inCat[0]).querySelector('.aic-hide').click();
  await until(() => !card(t, inCat[0]));
  assert.ok(chip().textContent.endsWith(`(${n - 1})`) && chip().classList.contains('on'));
  assert.deepEqual(ids(t), inCat.slice(1));
  for (const id of inCat.slice(1)) {
    card(t, id).querySelector('.aic-done').click();
    await until(() => !card(t, id));
  }
  assert.equal(t.q('.ai-none').textContent, 'Bu kategoride açık aksiyon yok.');
  assert.ok(chip().textContent.endsWith('(0)'));
  t.qa('.ai-f')[0].click();
  assert.equal(t.qa('.aic').length, FIX.actions.actions.length - n);
});

test('removing the last open action shows "Bugün için açık aksiyon yok."; undo brings it back', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const all = ids(t);
  for (const id of all) {
    card(t, id).querySelector('.aic-done').click();
    await until(() => !card(t, id));
  }
  assert.equal(t.q('.ai-state').textContent, MSG.empty);
  assert.ok(visible(t.q('.ai-state')) && !visible(t.q('.ai-filters')) && !visible(t.q('.ai-gen')));
  t.q('.ai-undo-b').click();
  await until(() => t.qa('.aic').length === 1);
  assert.deepEqual(ids(t), [all.at(-1)]);
  assert.ok(!visible(t.q('.ai-state')) && visible(t.q('.ai-filters')));
});

test('state survives a reload; an undone action is open again after reload', async () => {
  const server = { status: {} };
  const t = setup({ server });
  t.open(); await until(cardsReady(t));
  const [a, b, c, d] = ids(t);
  card(t, a).querySelector('.aic-done').click(); await until(() => !card(t, a));
  card(t, b).querySelector('.aic-hide').click(); await until(() => !card(t, b));
  card(t, c).querySelector('.aic-defer').click();
  card(t, c).querySelector('.aic-defer-ok').click(); await until(() => !card(t, c));
  card(t, d).querySelector('.aic-done').click(); await until(() => !card(t, d));
  t.q('.ai-undo-b').click(); await until(() => !!card(t, d));
  t.dom.window.close();
  const r = setup({ server });               // "reload": a fresh page against the same server state
  r.open(); await until(cardsReady(r));
  const now = ids(r);
  assert.ok(!now.includes(a) && !now.includes(b) && !now.includes(c), 'completed, dismissed, deferred stay hidden');
  assert.ok(now.includes(d), 'the undone action is open');
  assert.deepEqual(now, FIX.actions.actions.map((x) => x.action_id).filter((x) => ![a, b, c].includes(x)));
  assert.equal(r.posts().length, 0);
});

// ---------------- M5-4: drafts (channel × purpose, copy-only) and the customer drawer ----------------
const custCards = (t) => t.qa('.aic').filter((c) => t.actions.actions.find((a) => a.action_id === c.dataset.id).entity_type === 'customer');
const firstCust = (t) => custCards(t)[0];
const actionOf = (t, c) => t.actions.actions.find((a) => a.action_id === c.dataset.id);
const setSelect = (sel, v) => { sel.value = v; sel.dispatchEvent(new sel.ownerDocument.defaultView.Event('change')); };

test('✉ Taslak and 🔍 Müşteri appear on customer cards only, never on product cards', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  for (const c of t.qa('.aic')) {
    const isCust = actionOf(t, c).entity_type === 'customer';
    assert.equal(!!c.querySelector('.aic-draft'), isCust);
    assert.equal(!!c.querySelector('.aic-cust'), isCust);
  }
  assert.ok(t.qa('.aic').some((c) => actionOf(t, c).entity_type === 'product'), 'synthetic queue has a product action');
  assert.equal(firstCust(t).querySelector('.aic-draft').textContent, '✉ Taslak');
  assert.equal(firstCust(t).querySelector('.aic-cust').textContent, '🔍 Müşteri');
});

test('opening the draft panel makes no request; WhatsApp and the server category purpose are the defaults', async () => {
  const cats = clone(FIX.categories);
  cats.push({ key: 'zz_fair', label_tr: 'Fuar (test)', color: '#123456', entity_type: 'customer', source: 'x', draft_purpose: 'fair_invite' });
  cats.push({ key: 'zz_new', label_tr: 'Yeni (test)', color: '#654321', entity_type: 'customer', source: 'y', draft_purpose: null });
  const actions = clone(FIX.actions);
  const base = actions.actions.find((a) => a.entity_type === 'customer');
  actions.actions.push({ ...clone(base), action_id: 'zz_fair:900.00.0001:aaaa', category: 'zz_fair', score: 1 },
                       { ...clone(base), action_id: 'zz_new:900.00.0001:bbbb', category: 'zz_new', score: 0.5 });
  const t = setup({ categories: cats, actions });
  t.open(); await until(cardsReady(t));
  const before = t.calls.length;
  const expected = { winback: 'winback', real_decline: 'sales_followup', collections: 'collection', zz_fair: 'fair_invite', zz_new: 'sales_followup' };
  for (const c of custCards(t)) {
    c.querySelector('.aic-draft').click();
    const p = card(t, c.dataset.id).querySelector('.aic-draftp');
    assert.equal(p.querySelector('.ai-dch').value, 'whatsapp');
    assert.equal(p.querySelector('.ai-dpu').value, expected[actionOf(t, c).category], actionOf(t, c).category);
  }
  await sleep(20);
  assert.equal(t.calls.length, before, 'opening drafts sends nothing');
  const opts = [...t.q('.aic-draftp .ai-dpu').options].map((o) => o.value);
  assert.deepEqual(opts, ['collection', 'offer', 'winback', 'sales_followup', 'fair_invite']);
  assert.deepEqual([...t.q('.aic-draftp .ai-dch').options].map((o) => o.value), ['whatsapp', 'email']);
});

test('Taslak oluştur sends the canonical channel × purpose request with the header; result is plain text and copyable', async () => {
  let release;
  const bad = '<img src=x onerror="window.__pwned=1">Merhaba,\nnasılsınız?';
  const t = setup({ draftFn: (b) => new Promise((r) => { release = () => r({ json: { ...clone(FIX.draft_sample),
    channel: b.channel, purpose: b.purpose, message: bad, evidence: [{ fact_id: 'F001', label: 'Müşteri', display_value: '<b>x</b>' }] } }); }) });
  t.open(); await until(cardsReady(t));
  const c = firstCust(t), id = c.dataset.id, a = actionOf(t, c);
  c.querySelector('.aic-draft').click();
  const p = () => card(t, id).querySelector('.aic-draftp');
  setSelect(p().querySelector('.ai-dch'), 'email');
  setSelect(p().querySelector('.ai-dpu'), 'offer');
  const ins = p().querySelector('.ai-din'); ins.value = '  kısa ve sıcak  '; ins.dispatchEvent(new t.win.Event('input'));
  p().querySelector('.ai-dgo').click();
  await until(() => t.draftPosts().length === 1);
  const req = t.draftPosts()[0];
  assert.deepEqual(req.body, { customer_code: String(a.entity_id), channel: 'email', purpose: 'offer', additional_instruction: 'kısa ve sıcak' });
  assert.equal(req.headers['X-StockAgent'], '1');
  assert.ok(!('message_type' in req.body));
  // generating: status shown, the rest of the tab usable
  assert.equal(p().querySelector('.aic-dstat').textContent, 'Taslak hazırlanıyor…');
  assert.ok(p().querySelector('.ai-dgo').disabled);
  const other = t.qa('.aic').find((x) => x.dataset.id !== id);
  assert.ok(![...other.querySelectorAll('.ai-sbtn')].some((b) => b.disabled));
  release();
  await until(() => p().querySelector('.ai-dmsg'));
  assert.equal(p().querySelector('.ai-dmsg').value, bad, 'shown verbatim as text');
  assert.equal(t.doc.getElementById('ai').querySelectorAll('img, .aic-draftp b').length, 0);
  assert.equal(t.win.__pwned, undefined);
  assert.equal(p().querySelector('.aic-dhead').textContent, 'E-posta · Teklif');
  assert.equal(p().querySelector('.aic-chip').textContent, 'Müşteri: <b>x</b>');
  p().querySelector('.ai-dcopy').click();
  await until(() => p().querySelector('.ai-dcopied'));
  assert.deepEqual(t.copied, [bad]);
  assert.equal(p().querySelector('.ai-dcopied').textContent, 'Kopyalandı');
  const ta = p().querySelector('.ai-dmsg'); ta.value = 'Düzenlenmiş metin'; ta.dispatchEvent(new t.win.Event('input'));
  p().querySelector('.ai-dcopy').click();
  await until(() => t.copied.length === 2);
  assert.equal(t.copied[1], 'Düzenlenmiş metin', 'Kopyala copies the text as displayed');
  assert.equal(t.enrichPosts().length, 0);
  assert.equal(t.statePosts().length, 0, 'drafting never changes action state');
});

test('copy-only: no send control or messaging link anywhere in the tab', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  firstCust(t).querySelector('.aic-draft').click();
  firstCust(t).querySelector('.ai-dgo').click();
  await until(() => t.q('.ai-dmsg'));
  const panel = t.doc.getElementById('ai');
  for (const b of panel.querySelectorAll('button, a, [role="button"]')) assert.doesNotMatch(b.textContent, /gönder|send|whatsapp'?a aç/i);
  assert.equal(panel.querySelectorAll('a[href]').length, 0);
  assert.doesNotMatch(HTML, /wa\.me|mailto:|web\.whatsapp/);
  assert.match(t.q('.aic-dfoot').textContent, /kendiniz gönderin/);
});

test('clipboard failure leaves the draft visible with a manual-copy hint', async () => {
  for (const o of [{ clipboardFail: true }, { noClipboard: true }]) {
    const t = setup(o);
    t.open(); await until(cardsReady(t));
    firstCust(t).querySelector('.aic-draft').click();
    firstCust(t).querySelector('.ai-dgo').click();
    await until(() => t.q('.ai-dmsg'));
    t.q('.ai-dcopy').click();
    await until(() => t.q('.ai-dcopied.fail'));
    assert.equal(t.q('.ai-dcopied').textContent, 'Kopyalanamadı; metni seçip elle kopyalayın.');
    assert.equal(t.q('.ai-dmsg').value, FIX.draft_sample.message);
    t.dom.window.close();
  }
});

test('KAPALI refusal from the backend is shown as text; the card stays usable', async () => {
  const t = setup({ draftFn: () => ({ json: clone(FIX.kapali_refusal) }) });
  t.open(); await until(cardsReady(t));
  const c = firstCust(t), id = c.dataset.id;
  c.querySelector('.aic-draft').click();
  setSelect(card(t, id).querySelector('.ai-dpu'), 'offer');
  card(t, id).querySelector('.ai-dgo').click();
  await until(() => card(t, id).querySelector('.ai-derr'));
  assert.equal(card(t, id).querySelector('.ai-derr').textContent, 'KAPALI müşteri: yalnızca tahsilat mesajı hazırlanabilir.');
  assert.equal(card(t, id).querySelector('.ai-dmsg'), null);
  assert.ok(card(t, id).querySelector('.aic-facts li'));
  card(t, id).querySelector('.aic-done').click();
  await until(() => !card(t, id));
});

test('draft failures map to clear messages and never break the card', async () => {
  const cases = [[{ json: { available: false, ai_error_category: 'quota', reason: 'x' } }, 'OpenAI kredisi yetersiz'],
                 [{ json: { available: false, ai_error_category: 'internal_leak' } }, 'Taslak iç bilgi içerdiği için gösterilmiyor.'],
                 [{ json: { available: false, ai_error_category: 'numeric_guard' } }, 'AI metni güvenlik kontrolünden geçmedi, gösterilmiyor'],
                 [{ json: { available: false, ai_error_category: 'disabled' } }, 'AI kapalı (.env: AI_PROVIDER=openai)'],
                 [{ status: 500, json: {} }, 'Taslak oluşturulamadı.'], [new TypeError('Failed to fetch'), 'Taslak oluşturulamadı.']];
  for (const [resp, msg] of cases) {
    const t = setup({ draftFn: () => resp });
    t.open(); await until(cardsReady(t));
    const id = firstCust(t).dataset.id;
    firstCust(t).querySelector('.aic-draft').click();
    card(t, id).querySelector('.ai-dgo').click();
    await until(() => card(t, id).querySelector('.ai-derr'));
    assert.equal(card(t, id).querySelector('.ai-derr').textContent, msg);
    assert.ok(!card(t, id).querySelector('.ai-dgo').disabled, 'can try again');
    assert.ok(card(t, id).querySelector('.aic-reason') && card(t, id).querySelector('.ai-one'));
    t.dom.window.close();
  }
});

test('AI disabled: drafts report unavailable; the drawer and state controls still work', async () => {
  const t = setup({ health: { ...clone(FIX.health), ai_available: false }, actions: { ...clone(FIX.actions), ai_available: false } });
  t.open(); await until(cardsReady(t));
  const c = firstCust(t);
  c.querySelector('.aic-draft').click();
  assert.ok(c.querySelector('.ai-dgo').disabled);
  assert.equal(c.querySelector('.aic-dnote').textContent, 'AI kapalı (.env: AI_PROVIDER=openai)');
  c.querySelector('.aic-cust').click();
  await until(() => t.qa('.ai-dr-row').length > 0);
  assert.equal(t.draftPosts().length, 0);
});

test('the draft panel survives an AI comment on the same card (state kept, no extra request)', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const id = firstCust(t).dataset.id;
  firstCust(t).querySelector('.aic-draft').click();
  setSelect(card(t, id).querySelector('.ai-dch'), 'email');
  card(t, id).querySelector('.ai-one').click();
  await until(() => card(t, id).querySelector('.aic-ai'));
  assert.equal(card(t, id).querySelector('.aic-draftp .ai-dch').value, 'email');
  assert.equal(t.draftPosts().length, 0);
});

test('customer drawer: first open GETs deterministic data, reopening the same customer uses the session cache', async () => {
  const t = setup();
  t.open(); await until(cardsReady(t));
  const c = firstCust(t), code = String(actionOf(t, c).entity_id), data = FIX.customers[code];
  c.querySelector('.aic-cust').click();
  await until(() => t.qa('.ai-dr-row').length > 0);
  assert.ok(visible(t.q('.ai-drawer')));
  assert.deepEqual(t.gets('/api/agent/customer/').map((x) => decodeURIComponent(x.path)), [`/api/agent/customer/${code}`]);
  assert.equal(t.q('.ai-dr-name').textContent, data.facts.name);
  assert.match(t.q('.ai-dr-sub').textContent, new RegExp('^' + code.replace(/\./g, '\\.')));
  assert.deepEqual(t.qa('.ai-dr-row').map((r) => [r.querySelector('.l').textContent, r.querySelector('.v').textContent]),
                   data.display_facts.map((f) => [f.label, f.display_value]));
  t.q('.ai-dr-x').click();
  assert.ok(!visible(t.q('.ai-drawer')));
  c.querySelector('.aic-cust').click();
  await until(() => visible(t.q('.ai-drawer')) && t.qa('.ai-dr-row').length > 0);
  assert.equal(t.gets('/api/agent/customer/').length, 1, 'cached for this page session');
  t.doc.dispatchEvent(new t.win.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.ok(!visible(t.q('.ai-drawer')), 'Escape closes the drawer');
  const other = custCards(t).find((x) => String(actionOf(t, x).entity_id) !== code);
  other.querySelector('.aic-cust').click();
  await until(() => t.gets('/api/agent/customer/').length === 2);
  assert.equal(t.posts().length, 0, 'the drawer never POSTs (no AI, no state change)');
  assert.equal(t.enrichPosts().length + t.draftPosts().length + t.statePosts().length, 0);
});

test('drawer shows KAPALI from the server facts and escapes all text', async () => {
  const actions = clone(FIX.actions);
  const base = actions.actions.find((a) => a.entity_type === 'customer');
  actions.actions.unshift({ ...clone(base), action_id: 'collections:900.00.0007:cccc', category: 'collections', entity_id: '900.00.0007', score: 99 });
  const customers = clone(FIX.customers);
  customers['900.00.0007'].display_facts.push({ label: '<img src=x onerror="window.__pwned=1">', display_value: '<script>x</script>' });
  const t = setup({ actions, customers });
  t.open(); await until(cardsReady(t));
  card(t, 'collections:900.00.0007:cccc').querySelector('.aic-cust').click();
  await until(() => t.qa('.ai-dr-row').length > 0);
  assert.equal(t.q('.ai-dr-tag').textContent, 'KAPALI');
  assert.equal(t.q('.ai-drawer').querySelectorAll('img, script').length, 0);
  assert.equal(t.win.__pwned, undefined);
  assert.equal(t.qa('.ai-dr-row').at(-1).querySelector('.v').textContent, '<script>x</script>');
});

test('drawer failure shows a compact message, keeps the tab working, and is not cached', async () => {
  let n = 0;
  const t = setup({ customerFn: (code) => (++n === 1 ? { status: 404, json: { detail: 'yok' } } : { json: FIX.customers[code] }) });
  t.open(); await until(cardsReady(t));
  const c = firstCust(t);
  c.querySelector('.aic-cust').click();
  await until(() => t.q('.ai-dr-msg.err'));
  assert.equal(t.q('.ai-dr-msg').textContent, 'Müşteri bilgileri yüklenemedi.');
  assert.equal(t.qa('.aic').length, FIX.actions.actions.length);
  c.querySelector('.aic-done').click();
  await until(() => !card(t, c.dataset.id));
  t.q('.ai-undo-b').click();
  await until(() => !!card(t, c.dataset.id));
  card(t, c.dataset.id).querySelector('.aic-cust').click();
  await until(() => t.qa('.ai-dr-row').length > 0);
  assert.equal(t.gets('/api/agent/customer/').length, 2, 'a failure is retried on the next open');
  const t2 = setup({ customerFn: () => new TypeError('Failed to fetch') });
  t2.open(); await until(cardsReady(t2));
  firstCust(t2).querySelector('.aic-cust').click();
  await until(() => t2.q('.ai-dr-msg.err'));
});

// ---------------- M5-5: controlled refresh ----------------
const REFRESH_KEPT = 'Önceki veriler kullanılmaya devam ediyor.';

test('↻ Verileri yenile: one guarded POST, pending state keeps the dashboard, success reloads the page', async () => {
  let release;
  const t = setup({ refreshFn: () => new Promise((r) => { release = () => r({ json: { ok: true, step: 'done', step_tr: 'tamam',
    message: 'Veriler yenilendi.', as_of_data_date: '2026-07-15', warnings: [], errors: [], sources: {}, counts: {} } }); }) });
  t.open(); await until(cardsReady(t));
  const b = t.q('.ai-refresh');
  assert.ok(visible(b));
  assert.equal(b.textContent, '↻ Verileri yenile');
  const cards = t.qa('.aic').length, tableRows = t.doc.querySelectorAll('#reorder tbody tr').length;
  b.click(); b.click();
  await until(() => t.refreshPosts().length === 1);
  assert.equal(t.refreshPosts()[0].headers['X-StockAgent'], '1');
  assert.ok(b.disabled);
  assert.equal(t.q('.ai-rstat').textContent, 'Veriler yenileniyor…');
  assert.equal(t.qa('.aic').length, cards, 'cards stay visible while pending');
  assert.equal(t.doc.querySelectorAll('#reorder tbody tr').length, tableRows, 'static tables untouched');
  release();
  await until(() => t.q('.ai-rstat').classList.contains('ok'));
  assert.match(t.q('.ai-rstat').textContent, /^Veriler yenilendi\. Yeni veri tarihi: 15 Temmuz 2026\./);
  assert.equal(t.reloads.length, 0, 'short pause so the message is seen');
  await until(() => t.reloads.length === 1, 1000);
  assert.ok(b.disabled, 'stays disabled until the new page loads');
  assert.equal(t.refreshPosts().length, 1);
  assert.equal(t.enrichPosts().length + t.draftPosts().length + t.statePosts().length, 0);
});

test('refresh success with warnings shows them and still reloads', async () => {
  const t = setup({ refreshFn: () => ({ json: { ok: true, step: 'done', step_tr: 'tamam', message: 'ok', as_of_data_date: '2026-07-15',
    warnings: ['Aksiyon belirgin düştü: 15 → 3.'], errors: [], sources: {}, counts: {} } }) });
  t.open(); await until(cardsReady(t));
  t.q('.ai-refresh').click();
  await until(() => t.q('.ai-rstat').classList.contains('ok'));
  assert.match(t.q('.ai-rstat').textContent, /Uyarılar: Aksiyon belirgin düştü/);
  await until(() => t.reloads.length === 1, 1000);
});

test('refresh failure: no reload, failing step and "old data in use" shown, button usable again', async () => {
  const cases = [
    [{ json: { ok: false, step: 'validate', step_tr: 'doğrulama', message: 'Yeni veri tarihi (2026-09-30) mevcut veri tarihinden (2026-10-05) eski.', warnings: [], errors: [] } },
     'Yenileme başarısız (doğrulama): Yeni veri tarihi (2026-09-30) mevcut veri tarihinden (2026-10-05) eski. ' + REFRESH_KEPT],
    [{ json: { ok: false, step: 'verify', step_tr: 'kontrol', message: 'Bağlam ve pano tutmuyor: vadesi geçen toplam.' } },
     'Yenileme başarısız (kontrol): Bağlam ve pano tutmuyor: vadesi geçen toplam. ' + REFRESH_KEPT],
    [{ status: 409, json: { ok: false, step: 'busy', step_tr: 'kilit', message: 'Başka bir yenileme sürüyor; bitmesini bekleyin.' } },
     'Yenileme başarısız (kilit): Başka bir yenileme sürüyor; bitmesini bekleyin. ' + REFRESH_KEPT],
    [{ status: 500, json: null }, 'Yenileme başarısız (bağlantı): Sunucuya ulaşılamadı. ' + REFRESH_KEPT],
    [new TypeError('Failed to fetch'), 'Yenileme başarısız (bağlantı): Sunucuya ulaşılamadı. ' + REFRESH_KEPT],
  ];
  for (const [resp, text] of cases) {
    const t = setup({ refreshFn: () => resp });
    t.open(); await until(cardsReady(t));
    const ids0 = t.qa('.aic').map((c) => c.dataset.id);
    t.q('.ai-refresh').click();
    await until(() => t.q('.ai-rstat').classList.contains('err'));
    assert.equal(t.q('.ai-rstat').textContent, text);
    assert.ok(!t.q('.ai-refresh').disabled);
    await sleep(90);
    assert.equal(t.reloads.length, 0, 'never reloads after a failure');
    assert.deepEqual(t.qa('.aic').map((c) => c.dataset.id), ids0, 'cards unchanged');
    assert.equal(t.statePosts().length + t.enrichPosts().length, 0);
    t.dom.window.close();
  }
});

test('refresh button is never offered offline or from file://, and the mismatch warning still works', async () => {
  const off = setup({ offline: true });
  off.open(); await until(() => off.q('.ai-state').textContent.startsWith('Sunucu çalışmıyor'));
  assert.ok(!visible(off.q('.ai-refresh')));
  const file = setup({ url: 'file:///Users/someone/dashboard.html' });
  file.open(); await until(() => file.q('.ai-state').textContent.startsWith('Sunucu çalışmıyor'));
  assert.ok(!visible(file.q('.ai-refresh')));
  assert.equal(file.calls.length + off.refreshPosts().length, 0);
  const mm = setup({ health: { ...clone(FIX.health), as_of_data_date: '2026-07-31' } });
  mm.open(); await until(cardsReady(mm));
  assert.ok(visible(mm.q('.ai-warn')) && visible(mm.q('.ai-refresh')), 'mismatch warning with the refresh action next to it');
  assert.equal(mm.refreshPosts().length, 0, 'never refreshes automatically');
});

test('refresh is available while the server reports a warm-up error', async () => {
  const t = setup({ health: { ...clone(FIX.health), context_ready: false, context_error: 'FileNotFoundError: Stok ve satış dosyaları gerekli.' } });
  t.open();
  await until(() => t.q('.ai-state').textContent.startsWith('Veriler yüklenemedi'));
  assert.ok(visible(t.q('.ai-refresh')));
});

// M8-CP1 (D-060): viewing needs no login; owner actions need the owner session
test('owner mode: header says so, GETs carry no CSRF token, state changes carry the session token', async () => {
  const t = setup();
  await until(() => visible(t.doc.getElementById('halsa-access')));
  const bar = t.doc.getElementById('halsa-access');
  assert.equal(bar.dataset.mode, 'owner');
  assert.match(bar.textContent, /Yönetici girişi açık/);
  assert.ok(bar.querySelector('button'), 'logout button');
  t.open(); await until(cardsReady(t));
  assert.ok(t.calls.filter((c) => c.method === 'GET').every((c) => !('X-CSRF-Token' in c.headers)));
  const target = ids(t)[0];
  card(t, target).querySelector('.aic-done').click();
  await until(() => t.statePosts().length === 1);
  assert.equal(t.statePosts()[0].headers['X-CSRF-Token'], 'test-csrf-token');
  assert.equal(t.statePosts()[0].headers['X-StockAgent'], '1', 'the existing local guard header is kept');
});

test('viewer mode: everything reads without login; an owner action is refused with a clear message', async () => {
  const refused = () => ({ status: 401, json: { detail: { error_code: 'auth_required',
    message: 'Bu işlem yalnızca yönetici girişiyle yapılabilir. Görüntüleme için giriş gerekmez.' } } });
  const t = setup({ session: { configured: true, authenticated: false, csrf_token: null }, stateFn: refused });
  await until(() => visible(t.doc.getElementById('halsa-access')));
  const bar = t.doc.getElementById('halsa-access');
  assert.equal(bar.dataset.mode, 'viewer');
  assert.match(bar.textContent, /Görüntüleme modu/);
  assert.equal(bar.querySelector('a').getAttribute('href'), '/giris');
  t.open(); await until(cardsReady(t));
  const before = ids(t), target = before[0];
  card(t, target).querySelector('.aic-done').click();
  await until(() => visible(t.doc.getElementById('halsa-access-msg')));
  assert.ok(!('X-CSRF-Token' in t.statePosts()[0].headers), 'a viewer has no token to send');
  const msg = t.doc.getElementById('halsa-access-msg');
  assert.match(msg.textContent, /yalnızca yönetici girişiyle/);
  assert.equal(msg.querySelector('a').getAttribute('href'), '/giris');
  await until(() => card(t, target)?.querySelector('.aic-serr'));
  assert.deepEqual(ids(t), before, 'nothing changed: the card stays in the queue');
});

test('financial viewer mode (D-061): header says read-only finance, offers logout and the owner login', async () => {
  const t = setup({ session: { configured: true, finance_configured: true, authenticated: true, role: 'finance',
    csrf_token: 'fin-csrf' } });
  await until(() => visible(t.doc.getElementById('halsa-access')));
  const bar = t.doc.getElementById('halsa-access');
  assert.equal(bar.dataset.mode, 'finance');
  assert.match(bar.textContent, /Finans görüntüleme açık \(salt okunur\)/);
  assert.ok(bar.querySelector('button'), 'logout button');
  assert.equal(bar.querySelector('a').getAttribute('href'), '/giris');
});

test('file:// mode: the access script does nothing and asks nothing', async () => {
  const t = setup({ url: 'file:///Users/someone/dashboard.html' });
  await sleep(30);
  assert.ok(!visible(t.doc.getElementById('halsa-access')));
  assert.equal(t.calls.length, 0);
});
}
