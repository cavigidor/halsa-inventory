# M5 design note — "✨ AI Aksiyon Merkezi" tab

Status: **approved by the owner 2026-10-09** (decisions recorded as D-015 to D-020 in DECISIONS.md).
This is a design document only; no M5 code has been written yet. Implementation follows the bounded
slices in §11.

Invariants (AGENTS.md) still apply: Python owns every number, AI text is advisory, and nothing is ever
sent to a customer automatically.

---

## 1. Principle: the deterministic queue is the real product (D-015)

The AI Aksiyon Merkezi must be **fully useful with AI completely disabled**.

For every action, the deterministic Python layer provides **immediately**:

| Field | Source |
|---|---|
| action (title) | `services/context.py` `build_actions` |
| category (tahsilat / stok eritme / geri kazanım / reel düşüş) | Python |
| priority band and score (0–100) | `services/scoring.py` (transparent formula, components in `drivers`) |
| facts | Python-formatted strings |
| customer / product / entity | Python (order-source identity, D-003) |
| **reason the action exists** | **new in M5-1:** a deterministic Turkish sentence per category built from the same thresholds (e.g. "Vadesi geçen borç tahsilat öncelik eşiğini aştı (puan ≥ 50)") |

AI adds **only**: interpretation, explanation, a suggested approach, and an optional draft.

AI **does not** decide which customer or action deserves attention, **does not** determine or change
priority or ranking, and **does not** create financial facts. Python determines and ranks **all**
actions; AI explains selected ones.

## 2. Users and deployment scope (D-016)

- **End state:** the owner's father uses it, and later the sales team (2 people now, 2 more expected)
  may use parts of it from their own computers.
- **M5 scope: local-only on the Mac.** The backend binds to `127.0.0.1`. It is not exposed to the LAN
  or internet, and there is no login or authentication. The security model in §6 applies.
- **M5 must not block a later secure deployment.** Concretely:
  - The UI talks to the backend **only through the HTTP API** (no reading local files from the page),
    so the same UI can later be served by a deployed backend.
  - Host and port come from configuration, not hard-coded in business code.
  - Action state lives behind `services/store.py` functions, so a later `user_id` / `assigned_to`
    column (per-salesperson queues) is an additive schema change. M5 does not add it.
  - The POST header guard (§6) is a seam that real auth and CSRF tokens can replace later.
  - No responses or UI copy assume "the user is the owner" beyond labels.
- **Future milestone, explicitly out of scope for M5: Multi-user / remote deployment.**
  Authenticated access; a father/admin role; salesperson roles; private deployment; secure handling of
  business data; backups; and possibly per-salesperson action queues.

## 3. Current state this design builds on

| Piece | Today |
|---|---|
| `dashboard.html` | One self-contained static file (about 2.2 MB, data embedded) built by `python dashboard_builder.py data` and opened from disk. Tabs come from a generic table renderer. |
| Backend | `uvicorn app:app` (FastAPI): `/api/health`, `/api/context`, `/api/agent/actions`, `/api/agent/customer/{code}`, `/api/agent/product/{code}`, `POST /api/agent/draft-message`, `POST /api/actions/{id}/complete|defer|dismiss`, `/api/ai/calls`. |
| Actions payload | `ActionOut`: `action_id` (category + entity + hash of facts), `category`, `entity_type`, `entity_id`, `title`, `facts[]`, `drivers`, `score`, `priority`, `confidence`, `status`, plus AI fields `interpretation`, `recommendation`, `evidence[]`. |
| AI cost and latency | About 8–16 s and about 2.7k input / 1.3k output tokens per call (measured 2026-10-09). **Today `GET /api/agent/actions` calls the AI on every request** for up to 15 actions; M5-1 changes this. |
| First context build | Reads the 40 MB sales export. It takes tens of seconds the first time; there is no warm-up today. |

## 4. AI on demand: top 5 by default (D-017)

- **No paid call happens automatically.** Opening or refreshing the tab never calls the provider.
  There is no scheduled or morning generation for now; the owner first wants to observe usage, cost
  and value. Scheduled generation stays a future option (TASKS *Later*).
- `GET /api/agent/actions` returns **all** open deterministic actions, ranked by Python, plus any
  **cached** AI text. It never calls the provider. (This is a behavior change; the M2 test that expects
  enrichment on GET is updated in M5-1.)
- **"✨ AI yorumlarını oluştur"** enriches the **top 5 open actions by Python score that have no cached
  AI text**. That is one provider call.
- Then **"Sonraki 5 için AI yorumu oluştur"** enriches the next 5. There is also a per-card
  **"✨ Bu aksiyon için AI yorumu"** control for a single action.
- API: `POST /api/agent/actions/enrich` with body `{"action_ids": [...]}`. The server enforces
  **at most 5 ids per request** and **one provider call per request**. Every id must be a currently open
  deterministic action, otherwise the request fails with 422. The server never re-orders or filters by
  AI output.
- **Cache:** an SQLite table `ai_cache` keyed by `action_id` + prompt version + model. Because
  `action_id` contains a hash of the facts, the cache goes stale automatically when the data changes.
  Reloading costs nothing.

## 5. Same-origin serving; the file still works offline

- `GET /` serves the generated `dashboard.html` (gitignored, at the repo root). The owner opens
  **http://127.0.0.1:8000/**. Same origin means no CORS; a `file://` page has origin `null`, and allowing
  that would let any local file call the API.
- Double-clicking `dashboard.html` still works for every existing tab. The AI tab then shows an
  offline card with start instructions.

## 6. Security model for M5 (local-only)

- Run with `uvicorn app:app --host 127.0.0.1` (documented). No LAN or internet exposure.
- Every `POST` requires the header `X-StockAgent: 1`, or it gets 403. Browsers cannot send a custom
  header cross-origin without a CORS preflight, and none is allowed. This blocks drive-by
  "complete/dismiss" calls and paid-AI abuse from other websites. Paid AI is only reachable by POST.
- All model text is rendered escaped (the existing `esc()` / `textContent`), never as HTML.

## 7. Cards and layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ✨ AI Aksiyon Merkezi                                  Veri: 5 Ekim 2026  │
│ [● Sunucu bağlı] [AI: açık · gpt-5-mini]   [✨ AI yorumlarını oluştur]   │
│ Filtre: (Tümü) (Tahsilat) (Stok eritme) (Geri kazanım) (Reel düşüş)      │
├──────────────────────────────────────────────────────────────────────────┤
│ KRİTİK · skor 88   TAHSİLAT — <müşteri>                                  │
│ Neden: <deterministic reason sentence>                                   │
│ • Vadesi geçen: ₺…   • En eski borç: …   • 2026 sipariş: Aktif           │
│ ┌ ✨ AI yorumu (öneridir) ───────────────────────────────────────────┐   │
│ │ <yorum> · Öneri: <öneri>                                           │   │
│ │ Dayanak: [Vadesi geçen: ₺…] [En eski borç: …]                      │   │
│ └────────────────────────────────────────────────────────────────────┘   │
│ [✓ Yapıldı] [⏸ Ertele ▾] [✕ Gizle] [✉ Taslak] [🔍 Müşteri] [✨ Bu aksiyon]│
├──────────────────────────────────────────────────────────────────────────┤
│ …                                       [Sonraki 5 için AI yorumu oluştur]│
└──────────────────────────────────────────────────────────────────────────┘
Footer: "Sıralama ve sayılar Python'dandır; AI metinleri öneridir. Bugün: N AI çağrısı."
```

- Cards are always ordered by Python score. The AI block is visibly marked and absent when there is
  no AI text. The evidence chips show Python `label: display_value`.
- **Ertele ▾** opens a date picker (default +7 days). **✓ / ✕** hide the card, with a 5-second "Geri al".
- **🔍 Müşteri** opens a drawer with `/api/agent/customer/{code}`. It is loaded on demand and cached.

## 8. Message drafts: WhatsApp first (D-018)

- **Channels:** 1. **WhatsApp** (the first workflow), 2. **E-posta**.
- **Flow:** draft → display → **Kopyala** → a human sends it manually. There is no automatic sending
  under any circumstances, and no "send" button.
- **Tone:** professional, concise, natural Turkish B2B. "Sayın …" is **not** mandatory.
  The default greeting is context-dependent, e.g. "Merhaba [isim/firma],". A formal e-mail may use
  "Sayın …" when appropriate. WhatsApp should feel professional but human, not like a legal letter.
  No internal analysis, scores or rankings appear in customer-facing text.
- **Purposes:** tahsilat, teklif, geri kazanım, satış takibi, fuar daveti.
  The API separates **channel** (`whatsapp|email`) from **purpose**
  (`collection|offer|winback|sales_followup|fair_invite`). Today's `message_type` values are mapped for
  backward compatibility. KAPALI customers: only `collection` is allowed (Python refuses the others
  without calling the model, D-009).
- Numbers in drafts still go through fact references and the numeric guard. Scope stays minimal: one
  prompt section per purpose, with no template library or CRM features.

## 9. Controlled monthly refresh (D-019)

Desired experience: put the new approved exports in `data/` → press **one** refresh action → clear
success or failure → never a partial update.

Pipeline (`POST /api/context/refresh`, plus a CLI equivalent `python -m services.refresh` that works
without the server). Only one refresh may run at a time (a lock).

1. **Validate.** Detect the stock, sales and aging files (and report which were selected, since
   `_latest` picks the newest). Check required columns, the stock header row and aging bucket headers;
   check for non-empty rows and parseable dates; and check that the new data date is **not older** than
   the current one. Produce a validation report. **Stop on any error; nothing changes.**
2. **Build into staging.** Build the context and `dashboard.html.new` (temporary paths). The live
   dashboard and the in-memory context are untouched.
3. **Verify.** Reconcile the context totals with dashboard_builder (overdue and overstock, as the tests
   do). Check that the staged dashboard is non-empty and contains the expected tabs, and that row counts
   are plausible against the previous build (large drops are reported).
4. **Swap.** Keep `dashboard.prev.html`, then atomically `os.replace(dashboard.html.new → dashboard.html)`
   and replace the in-memory context under a lock. The visible data date updates.
5. **Report.** Return `ok`, the selected files, `as_of_data_date`, counts and warnings. On failure,
   report the step that failed; the previous dashboard and context remain in service.

The AI cache needs no manual flush, because changed facts mean new `action_id`s. The dashboard/backend
data-date mismatch warning (§10) stays as a safety net.

## 10. States the tab handles (Turkish copy)

| State | Detection | Shown |
|---|---|---|
| Backend offline / opened from file | `fetch('/api/health')` fails or `file://` | "Sunucu çalışmıyor. Terminalde `uvicorn app:app --host 127.0.0.1` çalıştırıp http://127.0.0.1:8000 adresini açın. Diğer sekmeler normal çalışır." |
| Context warming up | `context_ready: false` | "Veriler hazırlanıyor…" (polls every 3 s, up to 2 min) |
| AI disabled | `ai_available: false` | Deterministic cards only. "AI kapalı (.env: AI_PROVIDER=openai)". The AI buttons are hidden. |
| AI running | pending POST | "Oluşturuluyor… (≈10–20 sn)". Cards stay usable. |
| AI failed | `ai_error_category` | quota → "OpenAI kredisi yetersiz"; rate_limit → "Çok fazla istek, biraz sonra deneyin"; timeout/connection → "OpenAI'ye ulaşılamadı"; auth → "API anahtarı geçersiz"; numeric_guard/fact_reference → "AI metni güvenlik kontrolünden geçmedi, gösterilmiyor"; other → "AI yorumu üretilemedi". Facts stay visible. |
| No open actions | empty list | "Bugün için açık aksiyon yok." |
| Data date mismatch | the dashboard `meta.date` vs `/api/context.as_of_data_date` | Yellow note: "Pano ve sunucu farklı veri tarihleri kullanıyor; veriyi yenileyin." |
| Refresh running / failed | refresh response | Progress, then a success summary or "Yenileme başarısız (<adım>): … Önceki veriler kullanılmaya devam ediyor." |

## 11. Implementation slices (each one bounded session and checkpoint)

1. **M5-1 Backend prerequisites (no UI).** `GET /` serves the dashboard; `GET /api/agent/actions`
   never calls the provider and returns cached AI text; `POST /api/agent/actions/enrich`
   (≤5 ids, one call, `ai_cache`); a deterministic `reason` per action; the `X-StockAgent` guard on all
   POSTs; warm-up with `context_ready`. Tests: no provider call on GET (spy), the 5-id limit, a cache hit
   with no second call, facts change → cache miss, 403 without the header, `/` served.
2. **M5-2 Read-only tab.** Cards, the reason, facts, the AI block, evidence chips, filters, the top-5 /
   next-5 / per-card AI controls, and every §10 state (except refresh). The dashboard gets one tab
   button and one panel built by a separate `_ai_panel()` string; the existing table code is untouched.
3. **M5-3 Action state.** Done / defer / dismiss with undo; persisted via `services/store.py`.
4. **M5-4 Drafts and the customer drawer.** Channel × purpose API (WhatsApp first), tone rules in the
   prompt, copy-only UI, KAPALI refusals, and the cached customer drawer.
5. **M5-5 Controlled monthly refresh.** §9 pipeline (validate → build → verify → swap), the CLI and
   endpoint, the refresh button and report, and the mismatch warning. Tests use synthetic files,
   including invalid ones (missing column, older date), and prove nothing is swapped on failure.
6. **M5-6 Verification and polish.** A Playwright smoke test in CI (mock provider: offline,
   AI-disabled and mock-AI states, escaping of a `<script>` string, an action round trip), the mobile
   layout, and the owner's live check on the Mac.

## 12. Testing approach

- Backend: pytest with synthetic fixtures and a spy or mock provider. No key and no paid calls in CI.
- HTML: the built dashboard contains the AI tab and panel, and the existing tabs and row counts are
  unchanged.
- Browser: Playwright (CI installs Chromium via `playwright install --with-deps chromium`), mock provider only.
- One live check by the owner on the Mac per AI-facing slice, as in M3 and Q-1.

## 13. Out of scope (explicitly not started)

- **Multi-user / remote deployment** (§2): a future milestone.
- **Scheduled or morning AI generation** (§4): a future option after usage, cost and value are observed.
- **Sales-growth modules**, being explored separately: high-potential / low-penetration accounts,
  cross-sell, a salesperson daily work queue, customer 360, January fair planning and tracking,
  lightweight sales follow-up / CRM, new-buyer discovery, and salesperson portfolio assignment.
- **Geri Kazanım** already exists (dashboard tab + win-back actions) and is **not** rebuilt.
