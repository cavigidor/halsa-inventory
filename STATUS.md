# STATUS.md — current checkpoint

_Updated 2026-10-16 (M6-1 merged to `main` @ `c24488c`; M6-2 order-source normalization done on `claude/m6-2-order-source-normalization`, PR pending)._

## Git
- **`main`:** `c24488c` (PR #12, M6-1 importer framework). **This checkpoint:** branch
  `claude/m6-2-order-source-normalization` (PR pending); the commit that last touched this file is the checkpoint (`git log -1 -- STATUS.md`).
- **Owner's Mac folder:** still on `main` @ `a34ccac` (PR #2) with no uncommitted tracked changes; run `git pull`
  there to get M5 (see "Owner follow-ups" below).

## Milestone
**Milestone 5 — AI Aksiyon Merkezi: COMPLETE** (M5-1 to M5-6; final acceptance in the M5-6 section below).
Two owner follow-ups remain that the sandboxes cannot do (they cannot reach `api.openai.com` or open a browser on
the Mac): the visual check in the owner's own browser and one live WhatsApp draft tone check. Neither blocks M5.
**Milestone 6 — Additional data sources / sales intelligence foundation: in progress.** M6-0 (source inventory,
docs only) and M6-1 (importer framework, `services/importers/`) are merged; M6-2 (order activity/source normalization,
`services/importers/sales_activity.py`) is done (PR pending). M6-3 is not started.

## What works
- Deterministic dashboard (`python dashboard_builder.py data`). This file was unchanged by M3.
- Agent context, scoring, growth, scenarios and SQLite action state (M1).
- FastAPI backend. It starts with no key, and every deterministic endpoint works with AI disabled (M2).
- **M3:**
  - `services/ai/openai_provider.py`: Responses API, Pydantic structured output, finite timeout,
    bounded transient retries, `store=False`, every error categorized and none raised.
  - `services/ai/factory.py`: the only provider-selection point (`disabled|mock|openai|anthropic`).
  - `config.ai_settings()`: `AI_PROVIDER`, `AI_MODEL`, `AI_TIMEOUT_SECONDS`,
    `AI_MAX_OUTPUT_TOKENS`, `AI_MAX_RETRIES`, `AI_REASONING_EFFORT`. `.env` is auto-loaded.
  - `services/evidence.py`: evidence packets, Turkish display formatting, minimization validation.
  - `services/ai/grounding.py`: fact-id validation, placeholder resolution, numeric guard.
  - `services/agent.py`: grounded pipeline with one bounded stricter retry. KAPALI rules are enforced
    in Python. Provider exceptions are contained.
  - `services/store.py`: an `ai_calls` metadata table (no prompts or content), exposed at `GET /api/ai/calls`.
  - API responses now carry `evidence[]` (fact id, label, Python display value), `ai_error_category`
    and `ai_warnings`.
  - `services/context.py`: KAPALI customers removed from overstock buyer candidates (D-009).

## Tests (actual results, 2026-10-09)
| Where | Command | Result |
|---|---|---|
| Baseline before M3, Mac, real data | `python -m pytest tests/ -q` | **32 passed** |
| Baseline before M3, no data (CI-like) | same | 27 passed, 1 failed, 4 errors (needed real Excel) |
| After M3, cloud, no data | `python -m pytest -q` | **106 passed, 7 skipped (local_data), 1 xfailed** |
| After .env-loader fix, cloud, no data | `python -m pytest -q` | **107 passed, 7 skipped, 1 xfailed** |
| After quota category, cloud, no data | `python -m pytest -q` | **108 passed, 7 skipped, 1 xfailed** |
| After create()+diagnostics, cloud, no data | `python -m pytest -q` | **111 passed, 7 skipped, 1 xfailed** |
| After guard false-positive fixes, cloud, no data | `python -m pytest -q` | **121 passed, 7 skipped, 1 xfailed** |
| After M3, Mac VM, real 5 Oct 2026 data | `python -m pytest -q` | **113 passed, 1 xfailed** |

The xfail is the strict known issue DQ-1 (dashboard margin treats missing cost as zero).
CI: GitHub Actions run `37972839805` on `claude/milestone-3-live-ai` @ `dfa57de` — **success**
(every step green, including `python -m pytest -q`). The per-test counts could not be read from the
cloud session (the log download was forbidden); expected 106 passed / 7 skipped / 1 xfailed.
`main` @ `d37db1f` predates the workflow file, so it has no CI run yet.

## Live provider test
**PASSED 2026-10-09** (owner's Mac Terminal, `python scripts/live_smoke_test.py`, synthetic packet):
`provider=openai model=gpt-5-mini reasoning_effort=low max_output_tokens=3000`
- attempt 1: rejected by the numeric guard (`numeric_guard`, 12.1 s, 1920 in / 1227 out / 256 reasoning tokens)
- attempt 2 (the one bounded stricter retry): **success** (9.0 s, 2048 in / 917 out / 256 reasoning tokens)
- `RESULT: OK — schema valid, 12 fact(s) referenced, all values from Python: True`

Every number in the final summary (₺184.230, ₺0, yearly revenue and order counts) was inserted by
Python from `[[FACT:..]]` references. The model wrote no numbers itself.

Earlier attempts the same day, all handled as designed (categorized failure, nothing fabricated):
1. HTTP 429 `insufficient_quota`, because the API account had no credit. Now its own `quota` category.
2. `malformed` after 20 s. A truncated answer was hidden by `responses.parse()`; fixed in D-012.
3. The guard rejected both attempts on non-financial tokens; fixed in D-013.

Observation: the passing summary mostly lists facts ("₺61.000, 4, ₺70.230, 5 …") rather than
interpreting them. That is a prompt-quality item, not a safety issue (TASKS *Next*).
The cloud and Mac sandboxes still cannot reach `api.openai.com`, so live runs happen in the Mac Terminal.

## Provider configuration
Default `AI_PROVIDER=disabled`. Default `reasoning.effort=low` applies only to the default
OpenAI model. The OpenAI default model `gpt-5-mini` lives in
`config.DEFAULT_MODELS`. It has not been verified live; override it with `AI_MODEL`.

## Not implemented / known issues
- Milestone 4 (live macro data) is not started. Milestone 5 is complete (M5-1 to M5-6).
- The controlled refresh lock uses `flock`: supported on macOS and Linux (the Mac and CI), not on Windows (D-032).
- `weekly_agent.py` (older, unused, standalone weekly report e-mail to the owner via SMTP) still exists. The app
  never imports it and it never contacts customers; it is outside the copy-only app surface (D-031).
- DQ-3: tie order in "Büyüme / Küçülme" depends on the Python hash seed (rows, not values; TASKS *Next*).
- DQ-1: dashboard margin uses `fillna(0)` for missing cost (owner decision needed).
- DQ-2: the dashboard "Stok Eritme" list still includes KAPALI customers (owner decision needed).
- The lapsed qualification window is 2021–2025 in code but 2020–2025 in the spec (TASKS *Blocked*).
- `arama_listesi.py` is missing. It wasn't in the M1/M2 zips or on the Mac.
- The numeric guard catches digits and common magnitude or percent words. It doesn't catch every
  spelled-out small number (for example "iki müşteri").

## Local-only dependencies
- Python 3.12+ (`dashboard_builder.py` doesn't parse on 3.10).
- Real exports in `./data` (Mac): `2020-5 ekim satışlar.xlsx`, `STOK 5 Ekim 2026.xlsx`, `Cari 5 Ekim 2026.xlsx`.
- A pre-M3 backup of the Mac folder: `~/Desktop/ME/Projects/StockAgent_backup_2026-10-08_pre-M3`.

## Q-1 (done; merged in PR #2)
Branch `claude/q1-summary-quality` from `main` @ `60dffdb` (PR #1 merged; Milestone 3 complete).
Writing-quality prompt rules, schema field guidance and `services/ai/quality.py` measurement are
implemented.
Live run 1 on this branch: `RESULT: OK`, 1 attempt (16.4 s, 2479 in / 1354 out / 704 reasoning), and the
quality metric said OK. The summary now interprets, but on reading it still had defects the metric
missed: fact references tacked onto the end of items ("…; T1"), a name written and referenced twice,
"yok ₺0", and a recommendation listing "missing" fields that do not apply to a lapsed customer.
Fixes: the packet omits non-applicable collections fields (only genuinely empty ones are `missing`);
the quality check now detects tacked-on references, duplicated names and listed missing fields; the
prompt and schema guidance were tightened.
Live run 2: `RESULT: OK` (1 attempt, 7.8 s). The quality check now caught every defect (6 issues), but
the model repeated them despite explicit rules: it uses `[[FACT:..]]` like a citation footnote.
Fix (D-014): a deterministic tidy step after the safety check. Risks, opportunities and the
recommended action carry no inline facts (refs move to `evidence_fact_ids`). In the summary,
citation-style refs (parenthetical groups, after ';' or ',' at a clause end, after a finished verb,
duplicated names) and technical labels are removed. Tidy only removes, never adds a number. Quality
is measured both on the model output and on the tidied output. Standard suite: 134 passed, 7 skipped,
1 xfailed.
Live run 3 (two runs, both `RESULT: OK`; one used the guard retry): tidy fixed several defects, but
1) a year glued after another ref survived a single tidy pass; 2) a mid-sentence run of three amounts
was not handled; 3) the model kept turning informational notes into to-dos (an empty "Sektör" listed
as missing, and the margin warning).
Fixes: tidy runs until stable (max 3 passes) and handles ref sequences; a clause made of 3+ refs is
removed (ids kept as evidence) and the next sentence re-capitalized; `missing` now lists only numeric
facts (empty descriptive fields are omitted); the margin warning says margin is out of scope.
Standard suite: 137 passed, 7 skipped, 1 xfailed.
**Live run 4: ACCEPTED.** `RESULT: OK` on the first attempt (8.3 s, 2749 in / 1287 out / 832 reasoning tokens),
12 facts referenced, every value from Python. The model output had 2 quality issues; tidy applied 1 fix
and the final quality check was OK. Read by a human: the summary interprets rather than lists, the
opportunities are clean, and the action is concrete (who, what, when).
Residual model-judgment notes, not formatting: one risk still mentions missing margin data despite the
out-of-scope warning, a fact label is capitalized mid-sentence, and one risk is vague. Logged as an
optional follow-up (TASKS Later: Q-2); not a reason to keep iterating.

## M5-P1 (design approved)
`docs/m5_action_center.md` (branch `claude/m5-p1-design-note`, PR #3, docs only) has been updated with the
owner's decisions:
- The deterministic queue is the product, and AI only explains (D-015).
- M5 is local-only; multi-user / remote deployment is a future milestone, and M5 must not block it (D-016).
- AI runs only on request: top 5 by default, then "next 5" or per-action, with a cache (D-017).
- Drafts are WhatsApp first, channel × purpose, copy-only, natural B2B tone (D-018).
- The monthly refresh is controlled: validate → build → verify → swap (D-019).
- The dashboard is served same-origin, with a POST guard and escaped AI text (D-020).

There are six bounded implementation slices (M5-1 to M5-6). No code has changed in this checkpoint.

## Roadmap (documented 2026-10-10, not started)
Six additional monthly owner reports (hedef raporu, kredi takip, the detailed collections report,
bütçe, the Trodat sales report, gider/gelir) are planned for **M6** (validated importers → normalized
model → entity resolution) and **M7** (sales operating system). See `docs/roadmap_m6_m7.md` and
D-021 to D-026. None is ingested yet, and the real files stay local and gitignored.

The owner's answers were incorporated on 2026-10-10:
- Data-confidence statuses (D-023): `Haftalık Kaşe Adedi`, `Risk` and `Kredi` are stale historical
  fields; CRM coverage is incomplete; the 2020–2026 sales report and its activity/source (fair) field are
  trusted after verification.
- The new hires are office sales support, not field salespeople (D-024).
- Fair attribution starts from existing data (D-025).

Questions are resolved or reduced to M6-0 technical verification. M5-1 includes the extensibility seams
so M6/M7 actions plug in without redesign.

## M5-1 (done; merged in PR #4 @ `df4ba28`)
Branch `claude/m5-1-backend` from `main` @ `f9c42fb`. Backend only, no UI. Implementation notes are in D-027.
- **Action queue:** a generic action contract with `source`, an action-source registry
  (`services/actions.py`, `services/action_sources.py`), `GET /api/agent/categories`, and a deterministic
  Turkish `reason`. The refactor reproduces the M1 queue exactly: identical on synthetic data and on the
  real 5 Oct 2026 exports (15/15 actions, with and without inflation).
- **No AI on GET:** `GET /api/agent/actions` never calls the provider and attaches cached AI text when present.
- **Explicit enrich:** `POST /api/agent/actions/enrich` takes ≤5 unique open ids and makes one grounded
  call for the uncached ones, with an `ai_cache` keyed by action_id + prompt_version + provider:model.
  Changed facts, a changed prompt or a changed model mean a cache miss. Failures are not cached.
- **Guard:** `X-StockAgent: 1` is required on all mutating requests (403 otherwise).
- **Serving and warm-up:** `GET /` serves `dashboard.html` (404 with a build hint if missing); a warm-up
  thread builds the context, and `/api/health` reports `context_ready` / `context_error`.

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data | `python -m pytest -q` | **166 passed, 7 skipped, 1 xfailed** (29 new M5-1 tests) |
| Mac VM, real 5 Oct 2026 data (temp copy) | `python -m pytest -q` | **173 passed, 1 xfailed** |

## M5-2 (done; merged in PR #5 @ `0061a43`)
Branch `claude/m5-2-ai-tab` from `main` @ `df4ba28`. Notes in D-028.
- **Tab:** one tab button "✨ AI Aksiyon Merkezi" (appended after the existing tabs) and one panel built by
  `dashboard_builder._ai_panel()`, wrapped in `<!--AI-TAB-START/END-->` markers. `build(..., ai_panel=False)`
  gives the pre-M5-2 output byte for byte. No existing tab, table or data blob changed.
- **Cards:** priority (KRİTİK/YÜKSEK/ORTA/DÜŞÜK), score, server category badge, confidence, title,
  "Neden:" reason and the Python facts, in Python order. A separate dashed "✨ AI yorumu (öneridir)" block
  holds interpretation, "Öneri:" and the evidence chips (Python label: display value); "önbellekten" when cached.
- **Filters/badges:** from `GET /api/agent/categories` (no category is hard-coded in the UI).
- **AI controls:** "✨ AI yorumlarını oluştur" / "Sonraki 5 için AI yorumu oluştur" (the next ≤5 actions
  without AI text, in Python order within the current filter) and "✨ Bu aksiyon için AI yorumu". One POST
  per click with `X-StockAgent: 1`; buttons are disabled while a request runs; cards update in place.
- **No automatic AI:** the tab fetches nothing until opened, then only `/api/health`, categories and actions.
- **States:** offline / `file://`, warming (polls health every 3 s, up to 2 min, then one load attempt),
  warm-up error, AI disabled (controls hidden), running, the six error messages (facts stay visible, the
  per-card retry stays), empty queue, and the yellow data-date mismatch (dashboard `data-asof` vs the new
  `/api/health.as_of_data_date`, reported only when the context is ready, never building it).
- **Escaping:** the AI script uses `textContent` / `createElement` only (no `innerHTML`), and server colours
  are accepted only as hex.

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data (with node + jsdom) | `REQUIRE_JS_TESTS=1 python -m pytest -q` | **179 passed, 7 skipped, 1 xfailed** (13 new; one of them runs 16 jsdom tests) |

Mutation check: changing the header value, raising the id limit to 6, rendering text through `innerHTML`,
or loading on page open each makes the DOM tests fail.
Visual check: the real app on synthetic data with the mock provider in Chromium (desktop and 390 px):
cards, the AI block, filters and "Sonraki 5" render as designed; a reload shows cached text with no POST.

Real-data regression (Mac VM, 5 Oct 2026 exports, temp copies of `main` and the branch, nothing committed):
- Actions: 15 on `main`, 15 on the branch; content identical (category, entity, score, priority, title,
  reason, facts). Module row counts identical.
- Dashboard HTML: with a fixed `PYTHONHASHSEED`, the branch build with `ai_panel=False` is byte-identical to
  `main`, and the default build minus the AI block is too. Without a fixed seed, `main` itself differs
  between two runs (DQ-3).
- The dashboard `data-asof` equals the server `as_of_data_date` (2026-10-05).

## M5-3 (done; merged in PR #6 @ `c9e1a4d`)
Branch `claude/m5-3-action-state` from `main` @ `0061a43`. The existing SQLite action state is reused; notes in D-029.
- **Card controls:** "✓ Yapıldı" → `POST /api/actions/{id}/complete`; "✕ Gizle" → `.../dismiss`; "⏸ Ertele" opens
  a date picker (default today + 7, minimum tomorrow) → `.../defer` with `{"until": "YYYY-MM-DD"}`. All send
  `X-StockAgent: 1` and use the real `action_id`.
- **Server-confirmed:** the card leaves the queue only after a 2xx. Remaining cards keep Python order and are
  not rebuilt; category chip counts update; an emptied filter shows "Bu kategoride açık aksiyon yok." and an
  empty queue "Bugün için açık aksiyon yok.". On failure the card stays with "Aksiyon durumu kaydedilemedi."
  and its facts; a pending card's buttons are disabled (one request per click).
- **Undo:** a bottom bar "Aksiyon tamamlandı." / "Aksiyon gizlendi." / "Aksiyon <tarih> tarihine ertelendi." with
  "Geri al" for 5 seconds → `POST /api/actions/{id}/reopen`; the card returns at its Python position with any
  cached AI text. When the bar expires nothing else happens. A failed undo says so and the card stays hidden.
- **Backend:** new `reopen` endpoint (`set_status(id, "open")`, which clears `completed_at`, `defer_until` and notes
  and appends a `status:open` history event); all four state endpoints return 404 for an unknown `action_id`
  instead of a silent no-op; `defer` validates the date (future ISO date, default +7 days) and the response
  carries `defer_until`. No AI call, no AI-cache change, no schema change.

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data (with node + jsdom) | `REQUIRE_JS_TESTS=1 python -m pytest -q` | **198 passed, 7 skipped, 1 xfailed** (19 new backend tests; the jsdom run now has 27 tests, 11 new) |

Mutation check: dropping the header from state POSTs, treating a failed mutation as success, undo not restoring
the card, a +8 default, or undo calling the wrong endpoint each makes the DOM tests fail.
Browser check (Chromium, real app on synthetic data, temporary DB): complete, dismiss and defer three actions,
complete and undo a fourth, reload → the three stay hidden, the fourth is open, order kept, zero enrich calls;
the SQLite rows and history match.

Real-data regression (Mac VM, 5 Oct 2026 exports, temp copies, temporary `AGENT_DB`):
- Actions: 15 on `main`, 15 on the branch; content identical. M5-3 changes no business calculation.
- Through the API: 15 open → complete + dismiss + defer → 12 open (the three hidden) → reopen all three → the same
  15 in the same order; no AI calls recorded. The real `data/agent_state.db` was not created or touched.

## M5-4 (done; merged in PR #7 @ `4868265`)
Branch `claude/m5-4-drafts-customer-drawer` from `main` @ `c9e1a4d`. Notes in D-030 (drafts) and D-031 (GETs).
- **Draft contract:** `POST /api/agent/draft-message` takes `customer_code`, `channel` (`whatsapp`|`email`),
  `purpose` (`collection`|`offer`|`winback`|`sales_followup`|`fair_invite`), optional `product_code` and
  `additional_instruction` (≤300 chars). Unknown fields, other values, or mixing with the legacy field → 422.
  Legacy `message_type` is mapped once (`services/drafts.LEGACY_MESSAGE_TYPES`): collection → whatsapp/collection,
  sales_offer → whatsapp/offer, winback → whatsapp/winback, whatsapp → whatsapp/sales_followup,
  email → email/sales_followup. The response carries `channel` and `purpose`.
- **KAPALI:** only `collection` is allowed; every other purpose returns `available: false`,
  `ai_error_category: "kapali_customer"`, "KAPALI müşteri: yalnızca tahsilat mesajı hazırlanabilir." before any
  provider call (also with AI disabled). KAPALI status now comes from the full classification
  (`ctx["closed_customers"]`: order-source attribute or invoice party), not only the top-N risk list.
- **Draft generation:** a purpose-scoped, customer-facing evidence packet (name, rep; overdue and oldest debt only
  for collection; previous products, last purchase year for winback, optional product otherwise; no scores, tiers,
  ratios, growth analysis or KAPALI wording). Channel/purpose rules are a draft-only prompt layer, so the global
  prompt and the action-cache `prompt_version` are unchanged. Output goes through the numeric guard, fact
  substitution and a new deterministic customer-facing leak check (AI wording, scores, ranking, tiers, internal
  category labels, fact ids, technical flags), with the same single stricter retry. No draft cache.
- **Paid GET fixed:** `GET /api/agent/customer/{code}` and `GET /api/agent/product/{code}` never call the provider
  (same response shape, AI fields empty; customer adds `display_facts`). The AI analysis moved to guarded
  `POST .../analyze` (no UI).
- **Drawer data fix (found on real data):** 6 of the 15 real actions referenced customers outside the top-N
  `shrinking` list, so their lookups 404'd. The context now carries the already-computed rows of action customers
  outside each cut (`ctx["action_customers"]`); the top-N lists and the actions are unchanged.
- **UI:** customer cards only get "✉ Taslak" and "🔍 Müşteri" (product cards never; no customer is inferred from
  them). The draft panel opens without a request; defaults: WhatsApp and the category's server-described
  `draft_purpose` (collections → collection, winback → winback, real_decline → sales_followup, unknown →
  sales_followup). "Taslak oluştur" sends the canonical request with `X-StockAgent: 1`, shows "Taslak hazırlanıyor…",
  then the text in an editable textarea (plain text), evidence chips and "Kopyala" ("Kopyalandı", or a
  manual-copy hint). No send button or link. The drawer GETs once per customer per page session (in-memory
  `Map`, failures not cached), shows Python-formatted facts and notes, a KAPALI tag, and closes with ✕ or Escape.

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data (with node + jsdom) | `REQUIRE_JS_TESTS=1 python -m pytest -q` | **246 passed, 7 skipped, 1 xfailed** (48 new backend/structural tests; the jsdom run has 39 tests, 12 new) |

Mutation check: Taslak on product cards, an e-mail default, the legacy field in the UI request, no drawer cache,
ignoring the server purpose, or caching drawer failures each makes the DOM tests fail.
Browser check (Chromium, real app, synthetic data, mock provider): opening the draft panel sends nothing; one
POST per "Taslak oluştur"; reopening the drawer sends no second GET; desktop and 390 px layouts render.

Real-data regression (Mac VM, 5 Oct 2026 exports, temp copies, temporary `AGENT_DB`, mock/spy provider only, no
live call, nothing sent):
- Actions: 15 on `main`, 15 on the branch; content identical.
- Customer GETs for all 15 customer actions: 200, zero provider calls (before the drawer fix: 6 × 404).
- KAPALI offer draft refused with zero provider calls; one explicit mock draft = one provider call.
- No send routes; the real `data/agent_state.db` was not created or touched.

## M5-5 (done; merged in PR #9 @ `1f018c4`)
Branch `claude/m5-5-controlled-refresh` from `main` @ `4868265`. Notes in D-032.
- **One pipeline:** `services/refresh.run_refresh` (validate → build staging → verify → atomic swap → report), used by
  `POST /api/context/refresh` and `python -m services.refresh` (exit 0 ok, 1 failed, 3 busy). Never calls the AI.
- **Frozen sources:** `dashboard_builder.select_sources` picks stock/sales/aging once; validation reads each workbook
  once and the same raw frames feed `build_context(..., sources, frames)` and `dashboard_builder.build(..., sources,
  frames)`. A newer file appearing mid-refresh is ignored. Each read happens once (on the real data the full refresh
  takes about as long as one context build).
- **Validation:** stock (header row 4) needs the 8 columns `m_stock` reads and ≥1 product row; sales needs the 12
  columns the pipeline reads (Tarih, Nettutar, miktar, sip_evrakno_seri/sira, Pro_kodu, ProjeIsmi, Musterikod,
  Musteri_ismi, sto_kod, StokIsmi, anagrup), rows, and parseable dates (the data date is their maximum; no
  `today()` fallback). Optional columns (cost, Temsilci, Sektör, Açık / Kapalı) stay optional with a warning. Aging is
  optional; when selected it needs Hesap kodu / Hesap adı / Temsilci kodu, month buckets or a "Vadesi geçen" column,
  and ≥1 account row. Unreadable workbooks fail. The new date must not be older than the live date (the newer of
  the server context and the live dashboard's `data-asof`); first builds skip that check.
- **Verify:** same date in context, dashboard and validation; context ↔ dashboard reconciliation (overdue total,
  overstock value, lapsed, low stock, products) from the same build results; staged HTML structure (tabs, data,
  AI tab, `data-asof`). Plausibility: a count/total that drops by more than 50 % (`REFRESH_MAX_DROP`) vs the live
  context is a warning.
- **Swap:** copy live → `.prev.html.tmp`, `os.replace(dashboard.html.new → dashboard.html)`, install the context
  (`ContextRepository.replace_context`: lock, replace, drop indices, clear error), then keep `dashboard.prev.html`.
  If installing fails the previous dashboard is put back. Staging files and the lock file are always removed.
- **Lock:** non-blocking `flock` on `dashboard.html.refresh.lock`, shared by the server and the CLI; released by the
  kernel if a process dies; API → 409, CLI → exit 3.
- **UI:** "↻ Verileri yenile" in the AI tab toolbar (only when the server answers). Pending: disabled +
  "Veriler yenileniyor…", dashboard unchanged. Success: "Veriler yenilendi. Yeni veri tarihi: …" (+ warnings), then
  a page reload. Failure: "Yenileme başarısız (<adım>): <mesaj>. Önceki veriler kullanılmaya devam ediyor.", no reload,
  button usable again. The data-date mismatch warning stays.
- **Small fix:** `/api/health` `data_folder` now reports the folder name, not the absolute local path.

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data (with node + jsdom) | `REQUIRE_JS_TESTS=1 python -m pytest -q` | **295 passed, 7 skipped, 1 xfailed** (49 new refresh tests; the jsdom run has 44 tests, 5 new) |

Mutation check: skipping the older-date check, no flock, ignoring verify errors, no previous copy, or not dropping
the repository indices each fails the refresh tests; reloading after failure, not disabling the button, no header,
refreshing on load, or not re-enabling after failure each fails the DOM tests.
Browser check (Chromium, real app, synthetic data): newer exports → pending → success → reload, the data date moves
on and the mismatch warning is gone; an older replacement → "Yenileme başarısız (doğrulama) …", no reload, cards stay.

Real-data regression (Mac VM, 5 Oct 2026 exports via read-only links in a temp folder, temp dashboard/state DB, no AI):
- The real exports validate; staged actions 15 = `main` 15, identical; verify passes; the swap works (prev kept).
- An older synthetic sales file selected as newest → rejected at validation; an unreadable one → rejected; the temp
  dashboard and context unchanged in both. No staging leftovers, zero AI calls.
- The original exports are byte-identical afterwards; the real `data/agent_state.db` was not touched.

## M5-6 (done; merged in PR #10 @ `b3e4e4a`) — verification, browser smoke tests, polish
Branch `claude/m5-6-verification-polish` from `main` @ `1f018c4`. Harness decision in D-033. No business logic changed.
- **Playwright (Python, `tests/test_m5_browser.py`, pinned in `requirements-dev.txt`):** each test starts the real
  FastAPI app (free port, synthetic workbooks, temporary dashboard + SQLite, `AI_PROVIDER=disabled|mock`, no `.env`),
  polls `/api/health` for readiness and stops the process afterwards. CI installs Chromium
  (`python -m playwright install --with-deps chromium`) and sets `REQUIRE_BROWSER_TESTS=1`; locally the tests skip
  without Playwright/Chromium. The jsdom suite stays.
- **Scenarios:** (A) AI disabled: every existing tab renders, cards/filters/facts/reason/score, AI-off note, no AI
  controls, drawer, Ertele/Gizle/Geri al, no page errors; (B) mock AI: zero provider calls on load, tab, filters and
  drawer; explicit "AI yorumlarını oluştur" → separate "AI yorumu (öneridir)" block with evidence chips, facts
  unchanged; opening a draft → no call, "Taslak oluştur" → one call, "Kopyala" fills the clipboard, no send control,
  no `wa.me`/`mailto:`; hostile `<script>`/`onerror` text in intercepted AI and draft responses is shown as text,
  never executed (`window.__M5_XSS` unset, no new script); action state: Yapıldı → reload → hidden → reopen → reload
  → open, and Gizle → Geri al → reload → open; refresh: guarded POST, pending text, success, page reload,
  `dashboard.prev.html`; `file://`: static tabs work, offline message, no refresh button, zero requests; layout at
  390×844, 375×812 and 1280×900 with long unbroken AI text; accessible names, live regions, drawer dialog role.
- **Polish (AI tab CSS/JS only):** long unbroken text wraps (it widened the phone page to ~2900 px); 36 px tap targets,
  full-width undo bar ("Geri al" no longer wraps) and drawer on phones; the drawer moves focus to its close button and
  back to the opener. Desktop: checked, no change needed.
- **Mutation check:** removing the wrapping rule fails the layout tests; rendering AI text via `innerHTML` executes the
  injected handler in Chromium and fails the escaping test.

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data (node + jsdom + Chromium) | `REQUIRE_JS_TESTS=1 REQUIRE_BROWSER_TESTS=1 python -m pytest -q` | **305 passed, 7 skipped, 1 xfailed** (10 new browser tests; jsdom 44) |
| GitHub Actions | `tests.yml` with Chromium | **success** (first M5-6 commit `9aab2d9`) |

Real-data check (Mac VM, 5 Oct 2026 exports via read-only links, temp dashboard + state DB, mock provider so any
AI call would be counted; no browser exists or can be downloaded in the VM, so this is the server/API path):
- Actions 15 = 15 before M5-6, identical content. `python -m services.refresh` exit 0; the server starts and becomes
  ready, health shows 2026-10-05 and the folder name only; `/` serves the dashboard with the AI tab, refresh button
  and `data-asof` 2026-10-05.
- All 15 customer drawer GETs 200; complete → hidden → reopen → restored in order; API refresh ok (same date,
  previous kept, no warnings); zero AI calls from browsing, state changes and refresh; no send routes.
- The original exports are byte-identical; the real `data/agent_state.db` was not touched.

### Final M5 acceptance
| Area | Evidence |
|---|---|
| Deterministic core | Python computes and ranks every action (M5-1); 15/15 real actions unchanged through M5-6 |
| AI Aksiyon Merkezi | cards, reason, facts, server filters, top-5 / next-5 / per-card AI, cache, explicit calls only (M5-2; browser B) |
| Workflow | Yapıldı / Ertele / Gizle / Geri al with reload persistence (M5-3; browser round trip) |
| Customer | deterministic drawer, lazy + session cache (M5-4; browser A/B) |
| Messaging | WhatsApp/e-posta, channel × purpose, KAPALI → collection only, copy-only, no send (M5-4; browser B) |
| Refresh | validate → staging → verify → atomic swap, prev kept, failures keep old data, no AI (M5-5; browser refresh) |
| Safety | escaped AI text (browser XSS test), POST guard, local-only, real data out of Git, no automatic AI or messaging |
| Browser quality | Chromium smoke tests in CI; phone and desktop layouts verified |

### Owner follow-ups (do not block M5)
1. **Get M5 on the Mac and look at it:** in the project folder `git pull`, `pip install -r requirements.txt`,
   `python -m services.refresh`, `uvicorn app:app --host 127.0.0.1`, open http://127.0.0.1:8000/ → "✨ AI Aksiyon
   Merkezi". Check the date (5 Ekim 2026), 15 cards, a "🔍 Müşteri" drawer, one Yapıldı + Geri al, the "↻ Verileri
   yenile" button; then narrow the window (or the browser's phone view) and check the same.
2. **One live WhatsApp draft tone check (at most one paid call):** with `AI_PROVIDER=openai` in `.env`, open a
   Geri Kazanım or Reel Satış Düşüşü card → "✉ Taslak" → WhatsApp, "Satış takibi" (or "Geri kazanım") →
   "Taslak oluştur" once. Do not send it. Judge: natural Turkish, concise, professional, human, no internal analysis,
   no unsupported number, sendable after a light edit. If the tone needs work, add it to Q-2 (no prompt campaign).

## M6-0 (done; merged in PR #11 @ `979cd33`) — source inventory and technical verification
Branch `claude/m6-0-source-inventory` from `main` @ `b3e4e4a`. **Docs only:** `docs/m6_source_inventory.md` (canonical
inventory) and D-034. No importer, dashboard, action or AI code. The real workbooks were read only on the Mac, with
local metadata-only scripts outside Git; no workbook data reached any AI provider. All 22 inspected files were
hash-checked unchanged.
- **Located:** main sales (with a `liste` customer-master sheet), Hedef (Feb 2026 + two dated snapshots), account
  aging, one Trodat report (Nov 2025), gider/gelir 2026, the kredi ERP export (`kredi sözleşmeleri`, the "kredi
  takip" equivalent), Bütçe 2026 (`Ocak`, `Taslak`), two 2026 fair summaries, a 2025 call/lead log.
- **Not located:** `Evrak Detaylı Tahsilat Raporu`; a CRM export with customer IDs; historical fair
  invitation/attendance lists.
- **Key facts:**
  - Sales row grain is the sales-order line (order document × SKU; ordered = delivered + remaining + cancelled
    on 100 % of rows).
  - The activity/source field is `Srmmerk`: 99 labels, 100 % filled, one per order document, grammar
    `<YYYY><EVENT><FUAR|ETKİNLİK|SAHA>` or `GENEL`.
  - Fair attribution is deterministic at order level and joins to customer and SKU.
  - D-003 is confirmed. Hedef revenue matches sales by `Pro_kodu` for 89 % of accounts, against 68 % by invoice
    account.
  - Trodat model numbers match no Halsa SKU, so a mapping is needed.
  - Bütçe has 70 `#REF!`/`#DIV/0!` errors and an external link → `invalid`.
- **New data-quality items:**
  - DQ-4: 1,178 stock rows have `#DIV/0!` coverage and are silently excluded from stock lists.
  - DQ-5: the sales rep / KAPALI / sector columns are XLOOKUPs on the invoice account, applied to the order-source
    customer.
- **Owner decisions** listed in the inventory §16; **M6-1 requirements** in §17; the sequence is kept (§18).

## M6-1 (done; merged in PR #12 @ `c24488c`) — importer framework
Branch `claude/m6-1-importer-framework` from `main` @ `979cd33`. New package `services/importers/` (developer doc:
`docs/m6_importer_framework.md`, decision D-035). Framework only: no source-specific importer, no API, tab, action or
AI change, and nothing wired into the M5 context or refresh.
- **Pipeline:** `run_import(path, spec)`: inspect → read → normalize → validate → (fail closed) → exact-match entity
  resolution → verify → in-memory `ImportResult` (`staged` / `failed`, records empty on failure).
- **SourceSpec:**
  - Table layout at a fixed header row, or matrix layout (explicit cells, label anchors, ranges, sections).
  - Exact raw headers; duplicates selected by `(name, occurrence)`.
  - Types; per-field confidence (D-023) and audience (`shared` / `owner`).
  - Identity fields; row rules; business-date strategy; verification limits.
- **Excel:**
  - Formula view + cached-value view: literal / cached formula / missing cache / Excel error. Never recalculated.
  - The 7 error codes (+ `#OTHER`) become `invalid`, never NaN.
  - External links detected from relationships and formula text; never opened.
- **Identifiers and matching:**
  - Opaque string IDs (leading zeros, punctuation, long codes kept; float cells rejected).
  - Pattern/placeholder rules per source.
  - Statuses matched / unmatched / ambiguous / null / duplicate_master; roles; aggregate match reports.
  - `AttributeSource` seam for DQ-5.
- **Reports:** `ValidationReport` / `ImportResult.summary()` carry basename, counts, positions, codes and date
  min/max only. A sentinel test proves no row values, codes, amounts or paths leak.
- **Keys:** `record_key` = SHA-256 of type-tagged identity parts + occurrence (no `hash()`; stable across processes).

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data (node + jsdom + Chromium) | `AI_PROVIDER=disabled REQUIRE_JS_TESTS=1 REQUIRE_BROWSER_TESTS=1 python -m pytest -q` | **357 passed, 7 skipped, 1 xfailed** (52 new in `tests/test_m6_importers.py`; the other 305 unchanged) |

- **Fixtures:** all test workbooks are synthetic and generated at runtime by `tests/fixtures/xlsx_synth.py`, a
  low-level OOXML writer for cached formula values, error cells and external links. No binary fixture is committed.
- **Mutation checks:** each of these fails at least one test:
  - missing cache read as empty;
  - a duplicate master returning a match;
  - trimmed headers;
  - errors read as values;
  - Python `hash()` keys;
  - no anchor-moved check.
- **M5 regression:** no M5 file changed. The live path (`app.py`, `dashboard_builder.py`, `services/refresh.py`,
  `context.py`, `action_sources.py`) does not import the framework (tested). M5 refresh still reads only its three
  exports. Because M5 code is byte-identical to `main`, the 15-action real-data baseline is unchanged by
  construction.
- **Real-file smoke (read-only, Mac VM, 16 Oct 2026):**
  - Setup: a throwaway clone of this branch and a *copy* of the 5 Oct 2026 stock export in the VM's scratch
    space, run against a test spec that was not committed (`Stok_Kodu` occurrence 1, `STOK Kontrol %`,
    `TOPLAM STOK MİKTAR `).
  - Result: staged, 3,311 records. Both duplicated headers were reported by column letter.
  - Coverage: 1,178 `#DIV/0!` cells reported as cell-level `invalid` (matches DQ-4 exactly), 3,311 cached
    formula values.
  - Duplicates: one duplicated SKU got a distinct occurrence key. Without an occurrence the spec fails with
    `ambiguous_header`.
  - Leak check: 0 of 3,420 sampled real cell values and no absolute path appear in the serialized report.
  - Safety: the original was hash- and mtime-identical afterwards. The scratch copy was deleted and nothing
    was committed or sent to a provider.

## M6-2 (done; PR pending) — order activity/source normalization
Branch `claude/m6-2-order-source-normalization` from `main` @ `c24488c`. New source module
`services/importers/sales_activity.py` on the M6-1 framework (doc: `docs/m6_order_source.md`, decision D-036). No
generic framework change. No API, UI, action, persistence or M5 integration. No amounts and no DQ-5 lookup columns
are imported.

**What it contains:**
- SourceSpecs:
  - `sales_activity`: sheet index 0, header row 1, exact headers `sip_evrakno_seri`, `sip_evrakno_sira`,
    `Pro_kodu` (order_customer), `Musterikod` (invoice_account), `sto_kod` (product), `Srmmerk`, `Tarih`. All
    `trusted`; formulas forbidden; business date `max(Tarih)`.
  - `sales_customer_master`: `liste.KODU`.
- Parser: `GENEL` or `<YYYY><stem><TYPE>[seq]`, with the type matched from the right.
  - Types: `general`, `fuar`, `etkinlik` (ETKİNLİK/ETKİNLİĞİ) and `saha` (SAHA/SAHAÇALIŞMASI).
  - There is no `is_fair`.
  - An unknown grammar fails closed.
- Grain: orders are grouped by document. A conflicting label within an order is fatal. Conflicting dates or
  customers are record-level.
- Identity: canonical customer per D-003; exact master match with aggregate reports.
- Keys: `order_key` = `record_key("sales_order", [series, number])`; `event_id` = year + exact stem + type +
  sequence.

**Real-data finding:** one real label has a digit right after the four-digit year. The first draft of the parser
required the stem to start with a letter and failed closed on it (13 orders). That rule was my own addition, not
the approved grammar. Fixed: the year is exactly the first four digits, and everything up to the type token is the
opaque stem.

Tests:
| Where | Command | Result |
|---|---|---|
| Cloud, no data (node + jsdom + Chromium) | `AI_PROVIDER=disabled REQUIRE_JS_TESTS=1 REQUIRE_BROWSER_TESTS=1 python -m pytest -q` | **414 passed, 7 skipped, 1 xfailed** (57 new in `tests/test_m6_order_source.py`; the other 357 unchanged) |

Mutation checks: each of these fails at least one test:
- dropping the label-conflict check;
- mapping ETKİNLİĞİ → fuar;
- classifying unknown labels as general;
- putting the label into the order key;
- invoice-first identity.

**Real-data read-only check** (Mac VM, the 5 Oct 2026 sales export, opened read-only from a throwaway clone; only
aggregates printed; hash and mtime identical before and after):

| Check | M6-0 baseline | M6-2 |
|---|---|---|
| Sales lines / order documents | 182,121 / 38,215 | 182,121 / 38,215 |
| Labels, one per order | 99, 100 % | 99, 0 conflicts |
| Event orders | 8.0 % | 3,051 = 7.98 % (fuar 2,924 · etkinlik 57 · saha 70; general 35,164; sums to 38,215) |
| Stems / events | 38 stems | 38 stems, 98 event editions, 44 orders on a sequenced label |
| Unknown labels | — | 0 |
| Edition year = order year | 95.8 %; +1 for 127, −1 for 1 | 95.8 %; +1 for 127, −1 for 1 |
| `Pro_kodu` in `liste.KODU` (distinct) | 99.56 % | 6,717 of 6,747 = 99.56 % (6 of them on duplicated master keys) |
| `Musterikod` in `liste.KODU` (distinct) | 100 % | 5,484 of 5,485. The one miss has trailing whitespace and matches only after trimming. M6-0 compared trimmed codes; M6-2 matches exactly (no trim declared). |

Record-level, by order:
- 8 orders have an unusable date (conflict);
- 22 have conflicting customers, 4 of them event orders;
- 8 are invoiced to several accounts;
- 37 use the D-003 fallback.

Customer master: 18,722 codes with 7 duplicated keys. Order-level canonical customers: 38,047 matched, 66 unmatched,
80 on duplicated master keys, 22 null (conflict).

Leak check: 0 of 6,906 labels, stems and codes appear in the safe summary; no path. Run time 76 s, about 660 MB.

**M5 regression:** no M5 file changed. The live path does not import the new module (tested). M5 code is
byte-identical to `main`, so M5 output on unchanged inputs is unchanged by construction.

## Immediate next task
Review and merge the M6-2 PR. Owner decisions remain open (`docs/m6_source_inventory.md` §16), notably the fair
taxonomy (do ETKİNLİK / SAHA count as fairs?) and the conversion window. Start **M6-3** (Hedef) only when it is moved
to *Current*.
