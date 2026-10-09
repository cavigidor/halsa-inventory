# M5 design note — "✨ AI Aksiyon Merkezi" tab

Status: **proposal, awaiting owner review** (TASKS M5-P1). No UI code has been written.
Scope: how the AI action layer (Milestone 3) is shown inside the existing Turkish dashboard.
Invariants that still apply (AGENTS.md): Python owns every number, the AI text is advisory, and
nothing is sent to customers automatically.

---

## 1. What the tab is for

One screen where the owner sees **today's prioritized actions** and acts on them:
- the deterministic action list (collections, stock clearance, win-back, real decline), already ranked
  by Python (`services/context.py`, `scoring.py`);
- for each action, an **AI interpretation and recommendation** with its **evidence**: the Python facts
  the text relies on;
- buttons to mark an action **done / defer / dismiss**, and to create a **message draft** that is copied
  by hand and never sent.

It does **not** recalculate anything, show new analytics, or replace the existing tabs.

## 2. Current state (facts this design builds on)

| Piece | Today |
|---|---|
| `dashboard.html` | One self-contained static file (about 2.2 MB, data embedded) built by `python dashboard_builder.py data` and opened from disk (`file://`). Tabs come from a generic table renderer (`DATA[id].rows/cols`). |
| Backend | `uvicorn app:app` (FastAPI). Endpoints: `GET /api/health`, `GET /api/context`, `GET /api/agent/actions`, `GET /api/agent/customer/{code}`, `GET /api/agent/product/{code}`, `POST /api/agent/draft-message`, `POST /api/actions/{id}/complete|defer|dismiss`, `GET /api/ai/calls`. |
| Actions payload | `ActionOut`: `action_id, category, entity_type, entity_id, title, facts[], score, priority, confidence, status, interpretation, recommendation, evidence[{fact_id,label,display_value}]`, plus `ai_available, ai_reason, ai_error_category, ai_warnings`. |
| AI cost and latency | Measured on 2026-10-09: about 8–16 s and about 2.7k input / 1.3k output tokens for one customer analysis. **`GET /api/agent/actions` calls the AI on every request**, for up to 15 actions in one call. |
| First context build | Reads the 40 MB sales export. That takes tens of seconds the first time the backend is asked (no warm-up today). |

## 3. Key decisions (proposed)

### D1. Serve the dashboard from the backend (same origin); keep the file working offline
- Add `GET /` → `FileResponse("dashboard.html")` (the generated, gitignored file at the repo root).
  The owner opens **http://127.0.0.1:8000/** instead of double-clicking the file.
- Same origin means **no CORS**. A page opened from `file://` has origin `null`, and allowing `null`
  would let any local HTML file call the API.
- Double-clicking `dashboard.html` keeps working exactly as today. The AI tab then shows an
  "offline" card with how to start the backend (§6).
- *Alternative considered:* CORS for `null` / `file://`. Rejected for security and fragility.

### D2. AI never runs on page load; it runs on a button and is cached
- `GET /api/agent/actions` gains `?ai=0|1`, **default 0**. The tab's first load shows the deterministic
  actions instantly, with no paid call.
- New `POST /api/agent/actions/enrich` runs the AI for the visible open actions. It is triggered by the
  button **"✨ AI yorumlarını oluştur"**.
- **Cache** in SQLite (new table `ai_cache`). The key is
  `action_id` (which already includes a hash of the action's facts) + prompt version + model, so
  results are reused until the underlying facts change. A refresh of the page costs nothing.
- Enrich in **batches of 5 actions per call**. Fifteen actions in one call risks hitting the output
  limit (`incomplete`), and smaller batches fail independently.
- *Alternative considered:* auto-enrich on load. Rejected because every reload would be a 10–20 s paid
  call, the same cost would recur, and it would be slow.

### D3. Clear separation of fact vs AI text on every card
- **Facts** (Python): title, priority badge, the `facts[]` lines, and the evidence chips. These always show.
- **AI** (advisory): interpretation and recommendation in a visibly marked block
  ("✨ AI yorumu, öneridir"). They are hidden when the AI is off or failed.
- Evidence chips show `label: display_value` straight from Python. The chip values are the only
  numbers next to AI text that did not pass through placeholders.

### D4. Mutations are protected against cross-site requests
- Bind uvicorn to **127.0.0.1 only** (documented command: `uvicorn app:app --host 127.0.0.1`).
- Every `POST` must carry a custom header `X-StockAgent: 1`. A foreign website cannot send custom
  headers cross-origin without a CORS preflight, and we never allow one. This blocks drive-by
  "complete/dismiss" calls and paid-AI abuse.
- The paid AI call is a `POST` (D2), never a `GET`.

### D5. All model text is escaped
- AI text is untrusted input. It is rendered with the dashboard's existing `esc()` (or `textContent`)
  and never inserted as HTML.

### D6. Backend warm-up and data-date check
- At startup the backend builds the context in a background thread, so the first tab load is not a
  30–60 s wait. Until it's ready, `/api/health` reports `context_ready: false`.
- The tab compares the dashboard's build date (`meta.date`) with `/api/context.as_of_data_date`. If they
  differ, it shows a yellow note: "Pano ve sunucu farklı veri tarihleri kullanıyor; panoyu yeniden
  oluşturun."
- New `POST /api/context/refresh` re-reads the data folder after new exports are dropped in.

## 4. Layout (mobile-friendly; matches the existing visual style)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ✨ AI Aksiyon Merkezi                            Veri: 5 Ekim 2026        │
│ [● Sunucu bağlı] [AI: açık · gpt-5-mini]  [✨ AI yorumlarını oluştur]    │
│ Filtre: (Tümü) (Tahsilat) (Stok eritme) (Geri kazanım) (Reel düşüş)      │
├──────────────────────────────────────────────────────────────────────────┤
│ KRİTİK  TAHSİLAT — <müşteri>                              skor 88        │
│ • Vadesi geçen: ₺…   • En eski borç: …   • 2026 sipariş: Aktif           │
│ ┌ ✨ AI yorumu (öneridir) ───────────────────────────────────────────┐   │
│ │ <yorum>                                                            │   │
│ │ Öneri: <öneri>                                                     │   │
│ │ Dayanak: [Vadesi geçen: ₺…] [En eski borç: …]                      │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│ [✓ Yapıldı] [⏸ Ertele ▾] [✕ Gizle] [✉ Mesaj taslağı] [🔍 Müşteri analizi]│
├──────────────────────────────────────────────────────────────────────────┤
│ … next card …                                                            │
└──────────────────────────────────────────────────────────────────────────┘
Footer: "AI metinleri öneridir; sayılar Python tarafından hesaplanır. Bugün: N AI çağrısı."
```

- Cards are sorted by Python `score`. The filter chips use `category`.
- **Ertele ▾** opens a date input (default +7 days) and calls `/defer` with `until`.
- **Mesaj taslağı** opens an inline panel: channel select (WhatsApp / e-posta / tahsilat / teklif /
  geri kazanım) → `POST /api/agent/draft-message` → the text, a **Kopyala** button, and the note
  "Yalnızca taslak, otomatik gönderim yok." A KAPALI refusal shows its reason.
- **Müşteri analizi** opens a side drawer with `GET /api/agent/customer/{code}` (summary, risks,
  opportunities, action, evidence). It is loaded on demand and cached like D2.

## 5. Data flow

```
open http://127.0.0.1:8000/  ─►  GET /            (dashboard.html, same origin)
tab opened                   ─►  GET /api/health  (server, ai_available, model, context_ready)
                             ─►  GET /api/agent/actions?ai=0   (deterministic cards; cached AI text if present)
"✨ AI yorumlarını oluştur"   ─►  POST /api/agent/actions/enrich  (batches of 5, cached; X-StockAgent header)
✓ / ⏸ / ✕                    ─►  POST /api/actions/{id}/complete|defer|dismiss  (card hides; Geri al for 5 s)
✉                            ─►  POST /api/agent/draft-message
```

## 6. States the tab must handle (Turkish copy)

| State | Detection | Shown |
|---|---|---|
| Backend offline (file opened directly, server not running) | `fetch('/api/health')` fails or page is `file://` | "Sunucu çalışmıyor. Terminalde `uvicorn app:app --host 127.0.0.1` çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır." |
| Context warming up | `context_ready: false` | Spinner: "Veriler hazırlanıyor…" (polls every 3 s, up to 2 min) |
| AI disabled | `ai_available: false`, `ai_reason` | Deterministic cards only. Note: "AI kapalı (.env: AI_PROVIDER=openai)". Button hidden. |
| AI running | pending POST | Button: "Oluşturuluyor… (≈10–20 sn)". Cards stay usable. |
| AI failed | `ai_error_category` | Mapped message: quota → "OpenAI kredisi yetersiz"; rate_limit → "Çok fazla istek, biraz sonra deneyin"; timeout/connection → "OpenAI'ye ulaşılamadı"; auth → "API anahtarı geçersiz"; numeric_guard/fact_reference → "AI metni güvenlik kontrolünden geçmedi, gösterilmiyor"; other → "AI yorumu üretilemedi". Facts stay visible. |
| No open actions | empty list | "Bugün için açık aksiyon yok." |
| Data date mismatch | D6 | Yellow note to rebuild the dashboard. |

## 7. Backend changes M5 needs (summary)

1. `GET /` serves `dashboard.html`. Return 404 with a hint if it hasn't been built yet.
2. `GET /api/agent/actions?ai=0|1` (default 0), returning cached AI text when available.
3. `POST /api/agent/actions/enrich`: batches of 5, `ai_cache` table, per-batch error categories.
4. `POST /api/context/refresh`. Warm-up thread, and `context_ready` in `/api/health`.
5. A required `X-StockAgent: 1` header on every POST. Return 403 without it, plus a test.
6. Cache customer analysis results too (same key scheme), for the drawer.

None of these change a number, the domain rules, or the grounding pipeline.

## 8. Dashboard changes (kept isolated)

- `dashboard_builder.py` gets one new tab button (`data-t="ai"`) and one panel `<div id="ai">`. The
  panel's CSS and JS live in a separate string (`_ai_panel()`), so the existing generic table code is
  untouched.
- The panel JS uses only `fetch` and the existing `esc()`. No libraries, so the file still works offline.
- If the backend is unreachable, the panel shows the offline card. The rest of the dashboard is
  unaffected.

## 9. Testing plan

- **Backend (pytest, synthetic data):** the `ai=0` default makes no provider call (spy provider); enrich
  uses batching and a cache hit makes no second call; a facts change invalidates the cache; a POST
  without `X-StockAgent` returns 403; `GET /` serves the file; refresh rebuilds the context; warm-up flags.
- **HTML (pytest):** the built dashboard contains the AI tab and panel, and the existing tabs and row
  counts are unchanged (regression).
- **Browser smoke (Playwright; CI installs Chromium via `playwright install --with-deps chromium`):** open `/` with the mock
  provider; check the offline, AI-disabled and mock-AI states, escaping of a `<script>` string in AI
  text, and a done/defer/dismiss round trip. This runs in CI with the mock provider only, with no key.
- **One live check** by the owner on the Mac (real key), as in M3.

## 10. Proposed M5 task slices (each one bounded session)

1. **M5-1 Backend prerequisites:** §7 items 1–5, with tests. No UI.
2. **M5-2 Read-only tab:** cards, facts, AI block, evidence chips, filters, and every state in §6.
3. **M5-3 Action state:** done / defer / dismiss with undo; cards hide; persisted in SQLite (already exists).
4. **M5-4 Drafts and customer drawer:** draft panel with copy; the customer analysis drawer; §7 item 6.
5. **M5-5 Polish and verification:** Playwright smoke in CI, mobile layout, owner live check.

## 11. Open questions for the owner

1. **Who uses it, and where?** Only you on this Mac, or also your father on another computer? Another
   computer needs a different setup (network exposure and a login) and is out of scope for this plan.
2. **AI trigger:** is the "✨ AI yorumlarını oluştur" button OK, or should it also run automatically once
   each morning (a scheduled job) so the comments are ready when you open the tab?
3. **How many actions get AI text?** All open ones (up to 15, about 3 calls) or only the top 5 (1 call)?
4. **Drafts:** which channels matter most (WhatsApp, e-posta)? Should drafts address the customer
   formally ("Sayın …") by default?
5. **Data refresh:** is the monthly export → rebuild routine fine as a manual step, or should
   `POST /api/context/refresh` also rebuild `dashboard.html`?
