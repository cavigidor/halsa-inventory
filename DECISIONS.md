# DECISIONS.md — architecture decision log (append-only)

Format: ID · date · decision · rationale · alternatives · consequences.
Don't rewrite earlier entries. If one is superseded, add a new entry that says so.

### D-001 · 2026 (M1) · Python owns all financial calculations
- **Decision:** every financial figure (totals, growth, ratios, scores, aging, margins) is computed
  deterministically in Python/pandas.
- **Rationale:** the numbers drive collections and purchasing decisions, so they must be
  reproducible and auditable.
- **Consequences:** formulas live in `dashboard_builder.py`, `services/scoring.py`, `growth.py` and
  `scenarios.py`, each with a component breakdown.

### D-002 · 2026 (M1, enforced M3) · AI can never be the source of a numerical truth
- **Decision:** the model references facts as `[[FACT:Fxxx]]`, and Python substitutes the display
  value. A server-side numeric guard rejects model-written numbers and number words, allowing at
  most one stricter retry before rejecting the answer.
- **Rationale:** a prompt saying "don't calculate" is not a control. Architecture is.
- **Alternatives:** prompt-only rules (rejected); post-hoc comparison of numbers against facts
  (rejected because a copied number is still model-authored).
- **Consequences:** prose has no digits except years that appear in the packet and names or codes
  in the allow-list. Some harmless phrasing gets rejected. The guard catches common Turkish and
  English number words but not every spelled-out small number (known limitation).

### D-003 · 2026 (dashboard) · Customer identity uses order-source fields
- **Decision:** `_cust = Pro_kodu` (name `ProjeIsmi`), falling back to `Musterikod` only when
  `Pro_kodu` is empty.
- **Rationale:** about 22% of rows are invoiced to a different party than the one that placed the order.
- **Consequences:** the AI never decides identity. Risk uses the invoice code because receivables
  are booked against it.

### D-004 · 2026 · Real ERP data stays local and private
- **Decision:** Excel exports, SQLite state, generated contexts and dashboards, and `.env` never
  go to git. Tests use synthetic fixtures.
- **Consequences:** `.gitignore` covers them. Real-data tests are `local_data` and skip themselves when the data is absent.

### D-005 · 2026-10-09 · GitHub is the canonical source of truth for code and project state
- **Decision:** `cavigidor/halsa-inventory` holds the code, docs and repository memory
  (`AGENTS.md`, `PROJECT.md`, `STATUS.md`, `TASKS.md`, `DECISIONS.md`). The Mac folder is a
  working checkout plus private data.
- **Rationale:** sessions must be resumable without any AI's chat history.
- **Consequences:** every session ends with a committed and pushed checkpoint.

### D-006 · 2026-10-09 · The AI receives minimized deterministic evidence packets
- **Decision:** `services/evidence.py` builds a small, task-specific packet (fact ids, labels,
  Python display values, classifications, flags, missing, warnings). `validate_payload` rejects
  DataFrames, bytes, file paths, raw ERP column names and oversized packets before any provider call.
- **Consequences:** packets on the 5 Oct 2026 real data are 2.7 KB median and 12 KB max (limit 24 KB).

### D-007 · 2026-10-09 · Provider layer stays vendor-agnostic
- **Decision:** `AIProvider.generate_structured(system_prompt, payload, response_model) -> AIResult`
  is unchanged in shape and extended with `error_category` and `meta`. Selection happens only in
  `services/ai/factory.py`, and default model names live only in `config.DEFAULT_MODELS`.
  Grounding and retry logic sit in the provider-independent agent.
- **Alternatives:** provider-specific logic in the agent (rejected).
- **Consequences:** a new vendor means one adapter plus one registry entry.

### D-008 · 2026-10-09 · OpenAI via the Responses API with structured outputs
- **Decision:** the official `openai` SDK, `client.responses.parse(text_format=<Pydantic>)`, a finite
  timeout, SDK retries for transient errors only (0–3, default 1), a bounded `max_output_tokens`,
  and `store=False`. No Assistants API, browser automation, cookies or scraping.
- **Consequences:** AI output schemas are strict (all fields required, no extra fields).

### D-009 · 2026-10-09 · KAPALI customers removed from AI overstock buyer candidates
- **Decision:** `services/context.py` `score_buyers` skips customers whose `cust_attrs` mark them
  closed. Python also strips "opportunities" from AI analysis of a KAPALI customer and refuses
  win-back and sales-offer drafts for them without calling the model.
- **Rationale:** this enforces the existing invariant, which the M2 context did not apply to buyer
  candidates (2 of 20 overstock products affected on real data).
- **Consequences:** the dashboard's own "Stok Eritme" list is unchanged (TASKS DQ-2).

### D-010 · 2026-10-09 · The AI Action Center (M5) is built only after the live backend is proven
- **Decision:** no dashboard UI for AI until the live provider smoke test passes and the M3
  checkpoint is pushed with CI green.

### D-011 · 2026-10-09 · Python 3.12+ is required
- **Decision:** `dashboard_builder.py` uses PEP 701 nested-quote f-strings, so CI and the docs
  pin 3.12.

### D-012 · 2026-10-09 · OpenAI calls use `responses.create()` + strict schema; default reasoning effort "low"
- **Decision:** replace `responses.parse()` with `responses.create(text={"format": <strict json_schema
  from the Pydantic model via the SDK helper>})`. The provider checks `status`, `incomplete_details`
  and refusals and records usage (including `reasoning_tokens`) before validating the final-answer
  text with Pydantic. The default model (`gpt-5-mini`) is sent `reasoning.effort=low` unless
  `AI_REASONING_EFFORT` is set (`none` disables it), and the default `AI_MAX_OUTPUT_TOKENS` is 4000.
- **Rationale:** the first live call with credit failed as an opaque `ValidationError` after 20 s.
  `parse()` raises before the response can be inspected, so truncation by hidden reasoning tokens
  could not be told apart from bad output.
- **Consequences:** failures now name the cause (`incomplete` + a hint, or `malformed` with the
  pydantic error types and output length, never the content). Structured Outputs are still strict,
  and the D-008 constraints are unchanged.

### D-013 · 2026-10-09 · Numeric-guard allow-list: Python labels and list markers
- **Decision:** the model may repeat Python-authored flag names and classification values verbatim,
  and years appearing inside them count as packet years. Single-digit list markers (`1)`, `2.`, `(3)`)
  at the start of the text or after a line or sentence break are treated as layout. User-typed
  instructions are never allow-listed, and every other digit or number word is still rejected.
- **Rationale:** the first complete live call was rejected for exactly these three non-financial
  patterns. Rejecting them made the AI layer unusable without improving safety.
- **Consequences:** the guard is still strict about amounts, percentages, counts and ratios (tests
  cover numbers inside enumerated sentences). The prompt also asks for no numbering and no raw
  flag names.

### D-014 · 2026-10-09 · Deterministic prose tidy-up after grounding (writing quality)
- **Decision:** after `grounding.check` and before `resolve`, `services/ai/quality.tidy` normalizes the
  model's prose. Risks, opportunities and recommendations are plain text, so inline `[[FACT:..]]` refs
  move to `evidence_fact_ids` (the UI shows them as evidence). In other fields, citation-style refs
  (parenthetical groups, after ';' or ',' at a clause end, after a finished Turkish verb, a text fact
  already written out) and technical labels in parentheses are removed.
- **Rationale:** two prompt iterations did not stop `gpt-5-mini` from gluing fact refs onto sentence
  ends. A deterministic fix is reliable and testable, as the numeric guard is.
- **Safety:** tidy only removes placeholders and labels. It cannot add or alter a number, and its
  output is re-validated against the schema. Grounding still runs first on the raw output.
- **Consequences:** some numbers appear only as evidence chips rather than in the sentence. Quality
  is logged for both the raw and the tidied output, so prompt drift stays visible.

### D-015 · 2026-10-09 · The deterministic action queue is the product; AI only explains
- **Decision (owner):** the AI Aksiyon Merkezi must be fully useful with AI disabled. Python provides
  every action's title, category, priority, score, facts, entity and a deterministic "reason"
  immediately. AI adds interpretation, explanation, a suggested approach and optional drafts only. It
  never decides which action matters, never ranks or prioritizes, and never creates financial facts.
- **Consequences:** the actions endpoint works without a provider. The UI orders cards by Python score
  only. A deterministic `reason` field is added in M5-1.

### D-016 · 2026-10-09 · M5 is local-only; multi-user / remote deployment is a separate future milestone
- **Decision (owner):** M5 runs on the Mac only (bound to 127.0.0.1, no LAN or internet, no login).
  The end state includes the owner's father and the sales team, so a later **Multi-user / remote
  deployment** milestone will cover authenticated access, admin and salesperson roles, private
  deployment, secure data handling, backups and per-salesperson queues. It is out of scope for M5.
- **Consequences:** M5 must not block it. The UI uses only the HTTP API; host and port are config;
  action state stays behind `services/store.py` (so `user_id`/`assigned_to` can be added later); and the
  POST header guard is a seam for real auth and CSRF tokens.

### D-017 · 2026-10-09 · AI runs only on explicit request; top 5 by default; cached
- **Decision (owner):** there are no automatic or scheduled paid calls for now; usage, cost and value
  are observed first. Loading the tab shows all deterministic actions with no provider call.
  "✨ AI yorumlarını oluştur" enriches the top 5 open actions by Python score. "Sonraki 5…" and a
  per-action control enrich more.
- **Consequences:** `GET /api/agent/actions` never calls the provider (a behavior change from M2).
  `POST /api/agent/actions/enrich` accepts ≤5 open action ids and makes one call. An `ai_cache` table is
  keyed by action_id (which includes a facts hash) + prompt version + model.

### D-018 · 2026-10-09 · Message drafts: WhatsApp first, channel × purpose, copy-only
- **Decision (owner):** the channels are WhatsApp (first) and e-posta. Drafts are displayed and copied,
  and a human sends them. There is never automatic sending. The tone is professional, concise and
  natural Turkish B2B. The default greeting is context-dependent ("Merhaba [isim/firma],"); "Sayın …" is
  optional for formal e-mail. The purposes are tahsilat, teklif, geri kazanım, satış takibi and fuar
  daveti, with minimal scope.
- **Consequences:** the API splits `channel` from `purpose` (backward-compatible mapping of
  `message_type`). KAPALI customers get only collection drafts (Python refusal, D-009).

### D-019 · 2026-10-09 · Controlled monthly refresh: validate → build → verify → swap
- **Decision (owner):** one explicit refresh after the monthly exports are replaced. It validates the
  files, builds the context and dashboard in staging, verifies reconciliation and plausibility, then
  atomically swaps the dashboard and the in-memory context. On any failure nothing changes, and the
  report says which step failed.
- **Consequences:** `POST /api/context/refresh` and `python -m services.refresh` share one pipeline,
  with one refresh at a time; `dashboard.prev.html` is kept. The dashboard/backend data-date mismatch
  warning stays as a safety net.

### D-020 · 2026-10-09 · Dashboard served same-origin by FastAPI; POST header guard; escaped AI text
- **Decision:** `GET /` serves `dashboard.html`, so no CORS is needed (allowing a `null` origin was
  rejected). Every POST requires `X-StockAgent: 1`, and paid AI is POST-only. AI text is always
  rendered escaped. Double-clicking the file still works, and the AI tab then shows an offline card.

### D-021 · 2026-10-10 · Roadmap M6/M7 and the source-pipeline rule for new Excel reports
- **Decision (owner):** six additional monthly reports (hedef raporu, kredi takip, the detailed
  collections report, bütçe, the Trodat sales report, gider/gelir) become part of StockAgent in **M6
  (Additional Data Sources / Sales Intelligence Foundation)**, followed by **M7 (Sales Operating
  System)**. None is ingested in M5.
- **Rule:** every source goes through Excel → source-specific validator/importer → normalized
  deterministic model → entity resolution → analytics → actions → dashboard/API → optional AI
  explanation. There are no spreadsheet-specific features in dashboard code. Only stable, useful fields
  are imported, never whole sheets. Validation is fail-closed (formula errors such as those in bütçe
  surface as validation errors). Sales identity stays `Pro_kodu`/`ProjeIsmi`, and other identifiers
  join only through an explicit entity-resolution step with a match report. Real files stay local and
  gitignored; GitHub holds importers, schemas, validation rules, synthetic fixtures, tests and docs.
- **Consequences:** `docs/roadmap_m6_m7.md` holds the sources, intended uses, the M6/M7 module list and
  12 open owner questions (for example the meaning of `Haftalık Kaşe Adedi`), which are recorded and not
  guessed. Owner-only sources (gider/gelir, kredi takip, bütçe) feed an Owner / Yönetim view, not
  salesperson screens. Personal names (for example salespeople) are not written into the repo, only roles.

### D-022 · 2026-10-10 · M5 action contract is generic so M6/M7 sources plug in without redesign
- **Decision:** M5-1 introduces a generic action contract (with a `source` field), an action-source
  registry (`produce(context) -> list[action]`) and server-described categories
  (`GET /api/agent/categories`). The UI builds filters and badges from the server list. Ranking stays
  global and deterministic via `services/scoring.py`. Evidence builders are per entity type.
- **Rationale:** M6/M7 will add underpenetration, collection-first, cross-sell, follow-up and fair
  actions. Adding one must not require changing the agent, the enrich endpoint, the cache or the tab.
- **Consequences:** a small amount of extra structure in M5-1. Today's four categories become the first
  registered sources. Details are in `docs/m5_action_center.md` §14.

### D-023 · 2026-10-10 · Data confidence: not all source fields are equally trustworthy
- **Decision (owner answers):** normalized fields and sources carry deterministic data-quality status
  where it matters: `trusted`, `incomplete`, `stale`, `unverified` or `invalid`. Python and business rules
  assign it; the AI never does. A non-trusted field is used only as its status allows. Statuses travel
  into evidence packets as warnings. **The AI must never upgrade a stale or low-confidence field into a
  current fact.**
- **Current classification:**
  - `trusted` after technical validation: the 2020–2026 sales history and its order activity/source field.
  - `incomplete`: CRM activity and call/visit outcomes.
  - `stale`: `Haftalık Kaşe Adedi`, a historical capacity estimate about 30 years old, unmaintained for
    about 4–5 years. It is kept and never treated as current capacity, and current penetration is never
    calculated against it as if authoritative.
  - `stale`: `Risk`/`Kredi`, historical exposure and unsecured-credit limits, unmaintained for about 4–5
    years. They are reference only and never drive credit, collection, selling-restriction or AI
    decisions. Current risk logic uses current receivables and collections data and owner-approved rules.
  - `invalid`: bütçe cells with formula errors, until validation passes.
- **Consequences:** each M6 importer declares statuses for the fields it exposes. This is not a generic
  framework. The 12 owner questions in D-021 are now resolved or reduced to M6-0 technical verification
  (`docs/roadmap_m6_m7.md` §7).

### D-024 · 2026-10-10 · Sales organization and CRM coverage
- **Decision (owner answers):** there are two primary salespeople, and their names are never in the
  repo. The planned hires are **office-based sales support / inside-sales assistants**, not field
  salespeople: no new territories and no performance comparisons for them. CRM logging is inconsistent
  between roles, so CRM counts are **not** comparable performance metrics.
- **Consequences:** M7 includes a CRM coverage / data-completeness requirement. CRM-derived metrics
  carry a coverage warning, and Python never ranks salesperson performance on raw activity counts without
  approved completeness logic. "Sales Support / Inside Sales Support" is a future concept, likely a
  follow-up queue, and is not designed in detail yet.

### D-025 · 2026-10-10 · Fair and acquisition attribution start from existing trusted data
- **Decision (owner answers):** the 2020–2026 sales report's activity/source field reliably records
  fair names and other order sources, so fair-generated orders are already identifiable. M6 validates
  and normalizes this field (M6-2) before any new attribution system is built. The January Fair Command
  Center (M7) starts from existing attribution, and recovered invitation/attendance lists come later. All
  attribution is deterministic Python. Customer-acquisition sources (Google/inbound, fair, referral,
  distributor list, historical reactivation, other) are future tracking context. Geri Kazanım and Yeni
  Potansiyel Müşteri stay separate. No web scraping.
- **Consequences:** the exact grain and values of the field are verified in M6-0. No SKU-level claims
  are made until then.

### D-026 · 2026-10-10 · Legitimate recorded transactions only
- **Decision:** StockAgent models only legitimate, recorded company transactions and validated
  accounting data. Any financing, shareholder or interest scenario uses recorded values and
  owner/accountant-approved accounting treatment.

### D-027 · 2026-10-10 · M5-1 implementation notes (assumptions within the approved design)
- **Action registry:** `services/actions.py` holds the contract, registry and server-described categories.
  `services/action_sources.py` registers the four built-in sources (collections_aging, overstock_buyers,
  winback_lapsed, real_growth). They reproduce the M1 `build_actions` output exactly; this was verified
  identical on synthetic data and on the real 5 Oct 2026 exports (15/15 actions, with and without
  inflation). Ties keep registration order.
- **`reason`:** deterministic Turkish templates per category, extended with Python facts (for example
  still ordering, or overdue debt blocking win-back). It is never written by the AI.
- **"One provider call" per enrich request** means one grounded request for the uncached actions. The
  M3 numeric guard may still make its single stricter retry (D-002). Already-cached actions are not sent.
- **Cache key:** `action_id` (category + entity + facts hash) + `prompt_version` (hash of the system
  prompt + `AI_PIPELINE_VERSION`) + `provider:model`. Only successful, grounded results are cached.
  Cached text is **not** served while AI is unavailable. The cache lives in the local SQLite state DB.
- **Validation:** enrich accepts 1–5 **unique** ids that are currently **open**. Anything else is 422
  before any provider call. The response lists `enriched`, `cached` and `failed` ids, and returns actions
  in Python order.
- **Guard:** `X-StockAgent: 1` is required on **every** mutating request, including the deterministic
  `/api/scenario` POST, as the design says.
- **Warm-up:** a background thread builds the context at startup (`WARMUP_CONTEXT=false` disables it).
  `ContextRepository` builds under a lock, and `/api/health` reports `context_ready` / `context_error`.

### D-028 · 2026-10-11 · M5-2 implementation notes (assumptions within the approved design)
- **Placement:** the AI tab is appended after the existing tabs so their order is unchanged. The panel is
  one marked block from `_ai_panel()`; `build(..., ai_panel=False)` reproduces the previous HTML exactly,
  which the tests use to prove the existing dashboard did not change.
- **Lazy, server-driven panel:** nothing is fetched until the tab is opened. The browser never sorts,
  scores or filters by anything but the server's category key; it only counts cards per category chip.
- **"Top 5" and "Sonraki 5"** both take the next ≤5 actions that have no AI text and have not failed in this
  page session, in Python order, within the current category filter. A failed action keeps its per-card
  retry button. One click is one POST; buttons are disabled while it runs (no parallel paid requests).
- **Data-date mismatch:** the design compared `meta.date` (the build date) with the server's data date; the
  build date is not a data date, so the dashboard now embeds the latest sales date (`data-asof`, same rule
  as `services/context.py`) and `/api/health` exposes `as_of_data_date` once the context is ready, so the
  check needs no extra large request.
- **Warming:** after the 2-minute polling window the tab makes one ordinary actions request instead of
  giving up (the server finishes or reports the build); it never triggers AI.
- **DOM tests:** jsdom under Node (`tests/js/`, pinned lockfile), driven by `tests/test_m5_ui.py` with real
  API responses from the synthetic app and a mock provider. Skipped locally without node/jsdom; CI sets
  `REQUIRE_JS_TESTS=1` so they must run. The full Playwright smoke test stays in M5-6.

### D-029 · 2026-10-11 · M5-3 action-state contract (done / defer / dismiss / undo)
- **Undo endpoint:** `POST /api/actions/{action_id}/reopen` (guarded by `X-StockAgent: 1` like every POST) calls the
  existing `set_status(id, "open")`. That clears `completed_at`, `defer_until` and `notes` and appends a
  `status:open` history event. The action row, its history and the AI cache are kept; nothing is deleted or
  recreated. It returns `ActionStatusResponse`.
- **Server-confirmed mutations:** all four state endpoints return 404 for an `action_id` that was never generated
  (previously a silent no-op that still wrote history). The UI removes a card only after a 2xx and keeps it,
  with an error, otherwise. There is no optimistic deletion.
- **Defer date:** `until` must be an ISO date strictly after today (422 otherwise); an empty `until` defaults to
  today + 7 (design §7). A deferred action without a date would never hide under `is_hidden`, so it is no longer
  stored. The response includes the stored `defer_until`. Expiry stays the existing `is_hidden` comparison; no
  scheduler.
- **Undo UI:** one 5-second "Geri al" bar at a time; a newer change replaces it and the earlier change simply stays
  saved. The client keeps the removed card's data in memory, so undo restores its Python position and any cached
  AI text without a refetch or an AI call.
- **Unchanged store semantics:** the same facts give the same `action_id`; `upsert_actions` never reopens a
  completed or dismissed action; changed facts make a new action.

### D-030 · 2026-10-11 · Message drafts: channel × purpose contract (M5-4)
- **Contract:** `POST /api/agent/draft-message` takes `channel` (`whatsapp`|`email`) and `purpose` (`collection`|
  `offer`|`winback`|`sales_followup`|`fair_invite`) as Pydantic literals, plus `customer_code`, optional
  `product_code` and `additional_instruction` (≤300 chars). Unknown fields are rejected. The response echoes
  `channel` and `purpose`.
- **Legacy:** the mixed `message_type` is accepted only for old callers and mapped in one place
  (`services/drafts.LEGACY_MESSAGE_TYPES`; generic whatsapp/email → `sales_followup`). It cannot be combined
  with channel/purpose. The UI never sends it.
- **KAPALI:** only `collection` drafts; any other purpose is refused in Python before the provider
  (`kapali_customer`). This supersedes the older rule that blocked only winback and sales_offer. KAPALI status comes
  from the full classification in the context (`closed_customers`), not only from the top-N risk list.
- **Prompt layer:** channel and purpose rules are appended to the system prompt for the `message_draft` task only,
  so `prompt_version` (the action AI-cache key) does not change. The draft packet is purpose-scoped and
  customer-facing (no scores, tiers, ratios, growth analysis, flags or KAPALI wording).
- **Customer-facing check:** after grounding and fact substitution, a deterministic check rejects internal wording
  (AI/system, score, ranking, tier, internal category labels, fact ids, technical flags) as `internal_leak`, with the
  same single stricter retry as the numeric guard. `additional_instruction` stays untrusted (never allow-listed).
- **Defaults and copy-only:** categories describe a default `draft_purpose`, so the UI hard-codes no category;
  the default channel is WhatsApp. Drafts are shown as editable plain text with "Kopyala". There is no send
  capability, and no draft cache.

### D-031 · 2026-10-11 · Paid AI off GET; drawer data for every action customer (M5-4)
- **GETs are deterministic:** `GET /api/agent/customer/{code}` and `GET /api/agent/product/{code}` never call the
  provider. They keep their response shape with the AI fields empty, and the customer GET adds Python-formatted
  `display_facts` for the drawer. The AI analyses moved to `POST /api/agent/customer/{code}/analyze` and
  `POST /api/agent/product/{code}/analyze` (guarded like every POST; no UI in M5-4). This completes D-020's
  "paid AI is POST-only".
- **Action customers outside top-N:** actions are produced from the full lists, but the context lists are top-N, so
  a real action's customer could be missing from the index (6/15 on the 5 Oct 2026 data). The context now adds
  `action_customers` (already-computed rows of those customers per source). The top-N lists, actions and
  calculations are unchanged.
- **Drawer:** loaded only on click, cached in a browser `Map` for the page session (never in browser storage or on
  disk), failures not cached, no AI.
- **Legacy script:** `weekly_agent.py` (unused, standalone SMTP weekly report to the owner) is not part of the app
  and is not imported by it. The copy-only guarantee covers the app surface (routes, services, dashboard).

### D-032 · 2026-10-11 · Controlled refresh implementation (M5-5)
- **One pipeline:** `services/refresh.run_refresh` (validate → build staging → verify → atomic swap → report). The API
  passes the live context and `ContextRepository.replace_context` as the installer; the CLI passes none and only
  swaps the on-disk dashboard (no IPC into a running server, so the button is preferred while the app runs).
- **Frozen source snapshot:** sources are selected once (`dashboard_builder.select_sources`, still newest-by-name
  rule) and each workbook is read once. The validated raw frames are passed to `build_context(..., sources, frames)`
  and `dashboard_builder.build(..., sources, frames)`, which copy them. Content, not only paths, is frozen. Defaults
  are unchanged when no snapshot is passed.
- **Business date from content:** the sales `Tarih` maximum, never file times and never `today()`. It must be ≥ the
  live date (the newer of the server context and the live dashboard's `data-asof`).
- **Cross-process lock:** non-blocking `flock` on `<dashboard>.refresh.lock` (released by the kernel if a process
  dies; the file is removed on clean exit with an inode check against a racing opener). Busy → HTTP 409 / CLI exit 3.
  POSIX only (the Mac and CI).
- **Swap order:** copy the live dashboard to a temp prev, `os.replace` the staged file in, install the context, then
  rename the temp to `dashboard.prev.html`. If installing fails, the temp prev is moved back, so the dashboard and the
  context never knowingly disagree.
- **Report:** `RefreshReport` (`ok`, `step` busy|validate|build|verify|swap|done, `step_tr`, `message`, filenames,
  previous/candidate/current data date, counts, warnings, errors, checks, `previous_data_kept`). Controlled failures
  are HTTP 200 with `ok: false`; no stack traces or absolute paths. `/api/health` reports the data folder's name only.
- **Plausibility:** drops above `REFRESH_MAX_DROP` (default 0.5) against the live context are warnings, not errors.
  The CLI has no live context to compare and says so.

### D-033 · 2026-10-13 · Browser smoke tests: Python Playwright against the real app (M5-6)
- **Decision:** the real-browser layer uses Python Playwright (`playwright==1.56.0` in `requirements-dev.txt`) inside
  pytest (`tests/test_m5_browser.py`), not Node `@playwright/test`. The server, fixtures and the rest of the suite are
  Python, so a test can write synthetic workbooks, build the dashboard with the refresh pipeline, start `uvicorn` with
  an isolated environment and assert on API state in one place. One Playwright framework only. The jsdom suite stays
  for fast, fine-grained DOM tests.
- **Isolation:** one server process per test (free port, temp data folder, dashboard and SQLite DB, disabled or mock
  provider, no `.env`), readiness by polling `/api/health`, process always stopped. Hostile AI text is tested by
  intercepting responses at the browser boundary; the grounding pipeline is not weakened for tests.
- **CI:** `python -m playwright install --with-deps chromium` and `REQUIRE_BROWSER_TESTS=1` (the tests fail instead
  of skipping). Locally they skip without Playwright/Chromium, like the jsdom tests without Node.

### D-034 · 2026-10-13 · M6-0 technical facts that shape the M6 architecture
Verified locally on the real exports (details and evidence in `docs/m6_source_inventory.md`):
- **Sales grain:** the 2020–2026 sales report is at **sales-order line** grain (order document `sip_evrakno_seri` +
  `sip_evrakno_sira` × `sto_kod`). Ordered = delivered + remaining + cancelled on 100 % of rows. There is no invoice
  number and no native line number, so M6 uses a synthetic line key.
- **Activity/source field:** `Srmmerk` (one label per order document, 100 % filled). Fair and order-source
  attribution (D-025) is order-level and normalized by rule (edition year, event stem, type FUAR / ETKİNLİK /
  SAHA, optional sequence; `GENEL` = no event).
- **Identity:** D-003 is confirmed (Hedef revenue aggregates by `Pro_kodu`). `Pro_kodu` (order source) and
  `Musterikod` (invoice account) are distinct concepts that share one code space, the customer master sheet
  `liste` (`KODU`). Entity resolution keys on `KODU` with exact matching and match reports; customer attributes
  (KAPALI, rep, sector) come from the master with a documented key, not from the sales file's cached lookups
  (DQ-5).
- **Workbook realism:** importers must handle formula cells with cached values, per-cell Excel errors, external
  links, matrix/merged layouts and wrong header years explicitly. Each is a validation outcome, never a silent
  coercion (DQ-4 shows the cost of silent coercion).
- **Actual source files:** "kredi takip" = the ERP export `kredi sözleşmeleri.xlsx` (installment grain);
  "bütçe" = `BUTCE_2026 <revision>.xlsx` (`invalid` until cell-level validation). The detailed collections report,
  a CRM export and historical fair invitation/attendance lists were not located.

### D-035 · 2026-10-16 · M6 importer framework: declarative specs, two-view Excel reading, fail-closed staging (M6-1)
- **Decision:** every M6 source is a declarative `SourceSpec` run through one pipeline, `services/importers.run_import`:
  inspect → read → normalize → validate → fail closed → exact-match entity resolution → verify → in-memory
  `ImportResult`. Source modules declare specs. They do not write their own readers. Details:
  `docs/m6_importer_framework.md`.
- **Layout is configuration, never discovered:**
  - Headers are matched exactly (spaces, newlines, case, Unicode form) at a declared row.
  - Duplicated headers are selected by `(name, occurrence)`; without an occurrence they are an error.
  - Matrices are read only through explicit cells, anchors (which must be exactly once at the declared cell),
    ranges and sections with exact column labels.
  - Drift is an error.
- **Excel reading:**
  - Read-only, in two views (formulas + cached values), never recalculated, never saved.
  - Excel errors, missing formula caches, type mismatches and malformed/placeholder IDs become `invalid` values
    with a code, never None/NaN.
  - External links are detected and never opened.
- **Validity vs confidence:** technical validity and D-023 business confidence are separate. Confidence is declared
  per field and can only be downgraded.
  - A required field with invalid cells fails the import.
  - An optional one is marked unusable (warning).
  - `tolerate_invalid_cells` makes invalidity cell-level.
- **Identifiers and matching:**
  - Identifiers are opaque strings with per-source pattern/placeholder rules.
  - Entity resolution is exact string equality only. Duplicate master keys are never matched.
  - Roles: `order_customer`, `invoice_account`, `product`, `loan`, `other`.
  - Attribute provenance is declared via `AttributeSource` (the DQ-5 seam; M5 is unchanged).
- **Reports:** reports and summaries carry basenames, positions, codes and counts only (date min/max allowed). Row
  indexes and resolved master rows stay internal.
- **Keys:** record keys are SHA-256 over type-tagged identity parts + occurrence.
- **Business date:** never mtime. Filename dates only by opt-in.
- **Not yet:** persistence, refresh integration and API exposure come with each source's own checkpoint.

### D-036 · 2026-10-16 · Order activity/source attribution: separate types, order-document grain (M6-2)
- **Decision:** `Srmmerk` is normalized at **order-document** grain (`sip_evrakno_seri` + `sip_evrakno_sira`) into one of
  four canonical source types: `general` (`GENEL`), `fuar`, `etkinlik` and `saha`.
  - `FUAR`, `ETKİNLİK` and `SAHA` stay separate. Only the spelling of the type token is canonicalized
    (`ETKİNLİĞİ` → `etkinlik`, `SAHAÇALIŞMASI` → `saha`).
  - There is no `is_fair` flag. Business grouping (which types count as fairs) and any conversion window are owner
    policy, applied later as configuration.
- **Grammar:** structural only, `<YYYY><stem><TYPE>[sequence]`.
  - The year is the first four digits.
  - The type is matched from the right, longest token first.
  - The stem is opaque and exact.
  - Unknown labels, or more than one label in an order, fail the import closed. They are never classed as general,
    fair or other.
- **Event and order identity:**
  - Edition year (from the label) and order date (`Tarih`) are independent facts; neither corrects the other.
  - Event identity is year + exact stem + type + sequence. Different stems are never merged.
  - The order key is derived from series + number only, never from the label.
- **Privacy:** labels and stems can be partner company names. They live only in in-memory records, never in summaries,
  reports, logs or committed docs.
- **Scope:** no amounts are imported (revenue basis is open), and the DQ-5 lookup columns are not imported.

### D-037 · 2026-10-16 · Hedef: explicit file, exact schema variants, field-level confidence (M6-3)
- **No automatic file of record:** until the owner decides which Hedef workbook is maintained, Hedef is imported only
  from an explicit path. Candidate listing never selects (no mtime, name order or "newest" logic).
  - Several files share the layouts: the Feb 2026 file, a June 2026 file and dated snapshots.
- **Schemas:** exact variants (`current_2026`, `legacy_snapshot`). A workbook must carry every header of exactly one
  variant, otherwise the import fails closed.
  - The variant is structure only, never authority.
  - Legacy placeholder/target columns are `invalid` and excluded from the model.
- **Identity:** `cari_kod` is the canonical / order-customer concept (D-003), matched exactly against `liste.KODU`.
  - Never trimmed.
  - Duplicates are fatal.
  - Keyed by code alone, so the key is stable across snapshots.
- **Confidence:** field-level, never one status per workbook.
  - `Haftalık Kaşe Adedi`, `Risk` and `Kredi` are always `stale`, whatever their parse result. They are historical
    references that never feed capacity, penetration, credit, collection, scoring or actions.
  - `Ziyaret Sayısı` is `incomplete`. Zero may mean "not logged"; it is never a performance or contact signal.
- **Sales reconciliation** is a technical identity check against the existing `Nettutar`, keyed by the D-003
  customer. It is not a revenue policy and never overwrites Hedef values.
- **Assignment fields:** `Temsilci` and `Müşteri Temsilcisi` stay separate and unranked. The salesperson of record
  stays open.
- **Snapshot date:** declared or opt-in filename only; otherwise unknown.

### D-038 · 2026-10-16 · Kredi takip pulled forward; installment grain, amortizing-only, fatal arithmetic
- **Sequence exception:** M6-4 (detailed collections) is blocked by the missing `Evrak Detaylı Tahsilat Raporu`, so the
  Kredi takip importer (part of M6-6) was implemented first.
  - It must not be approximated from the account-level Cari aging.
  - Trodat and gider/gelir wait on owner mappings.
  - Roadmap numbering is unchanged, and M6-4 stays open and blocked.
- **Source:** the ERP loan-contract export (`kredi sözleşmeleri.xlsx`) is company financing at **installment grain**
  (`SÖZLEŞME KODU` × `TAKSİT NO`, unique; a repeat is fatal).
  - The audience is **owner**. It is never customer credit, Hedef `Risk` / `Kredi`, or collections.
  - The trailing control-total row is verified against the column sums and excluded. It was the source of M6-0's
    "repeated pair".
- **Loan types:** only amortizing loans are supported. **Revolving (`Rotatif`) loans need their own rule set** and
  fail closed, as do unknown types and statuses.
- **Arithmetic:** the identities "components = installment" and "paid + remaining = installment" are source-fatal at
  ±0.01 TL, because a wrong payment total must never reach cash planning.
- **Dates:** the latest payment-slip date is an activity date, never the snapshot date.
- **Not yet:** no cash-planning metrics, UI or FX in this slice.

### D-039 · 2026-10-16 · Trodat: native model × variant × month, independent of Halsa SKUs (M6-5)
- **Identity:** M6-5 normalizes Trodat's native model × variant × month data on its own.
  - Model codes are opaque strings, and variants are separate fields.
  - Families come from exact workbook labels only; an inferred family is `unverified`.
  - Keys are built from code + variant (+ year + month), never from the row or the file.
- **Halsa SKU mapping is a later, explicit data/configuration layer:** Trodat model (+ variant) → one or more Halsa
  SKUs, with status, effective date and provenance. It is never guessed (no fuzzy, substring, name or number-similarity
  matching), and its absence never blocks the import. Who maintains it is an owner decision.
- **Values:** the reported value is `reported_quantity` with `unverified` semantics until the owner confirms its
  meaning. Reconciling totals does not upgrade it.
- **Layout:** read through an explicit matrix spec (exact anchors and headers; drift fails closed).
  - Workbook totals are verified against cached results.
  - Section-1 totals are fatal on mismatch.
  - Section-2 totals, which are inconsistent in the real workbook, are a warning, and those lines are flagged.
  - The yearly "TRODAT SALES" rows are a different metric and are not part of the model.
- **Year:** declared or taken from the approved filename pattern. The filename month is a label, never a snapshot
  date.

### D-040 · 2026-10-16 · Gider/Gelir: native business lines, unique facts once, template year never the period (M6-6)
- **Native lines:** Gider/Gelir keeps KIRTASIYE / TRODAT / TROTEC / IMALAT as native dimensions.
  - The owner's management-category mapping (kaşe / kırtasiye / other-import) is a later configuration layer. Its
    absence never fails the import.
  - The accounting basis (cash vs accrual) is `unverified`, and amounts carry neutral `reported_*` names.
- **The period year** comes from the declared year or the filename, and must agree with the workbook's own sheet names.
  The template year text in the expense sheet's monthly headers (`2025 <Ay>` in the 2026 file) is never used as the
  business period; only the month is read, with a warning.
- **Each financial fact is normalized once,** from the sheet that owns it:
  - expenses from GİDERLER;
  - sales, cost and gross profit by line from GELİRLER;
  - historical totals, the source budget and the accounting entries from ÖZET.
- **Duplicates are controls:** ÖZET report-year P&L, the pasted pivot and Sheet1 are verification-only or excluded.
- **Cached values are kept as recorded,** never recomputed.
  - Allocation overrides, ratios outside 0–1 and gross profit ≠ sales − cost (all present in the real source) are
    flagged, not corrected.
  - Missing or non-summing ratios, broken totals, Excel errors in money cells and external links are fatal.

### D-041 · 2026-10-16 · Bütçe: partially usable owner planning data at cell level; no repairs; authority unresolved (M6-6)
- **Cell-level validity:** Bütçe is imported as partially usable owner planning data, cell by cell.
  - Technically valid cells stay `unverified`; they are never `trusted`.
  - These cells are `invalid` and excluded from usable coverage: Excel errors, missing caches, non-numeric values in
    value positions, and cells that depend on another workbook (including the row-1 cells computed from them).
  - Invalid cells are isolated: they never fail the whole import and never contaminate unrelated cells.
  - Only structural drift fails the import: anchors, headers, titles, panel labels, year.
- **No repair, no fill:** the importer never repairs or recalculates formulas, never opens or resolves the linked
  workbook, and never fills a broken cell. The forbidden sources are neighbors, other periods, Gider/Gelir and sales.
  An external cell's cached value is kept aside internally only.
- **Structural identity:** fixed block position with a verified title, segment (year × Forecast/GERCEK), month,
  expense group × component, panel × scenario × line. Month labels alone are never identity. The two group-list
  block titles are verified for consistency but not committed.
- **Currency labels stay verbatim:** `$ kur` / `€ kur` are rate columns; `YTL` / `TL` are amount labels. There is
  no conversion and no FX source.
- **Source status** (`complete | partially_usable | unusable`) is reported separately from D-023. It is never
  `authoritative`.
- **File of record:** Ocak vs Taslak is unresolved; candidates are listed, never selected. Reconciliation with
  Gider/Gelir is deferred.
- **Framework additions** (backward compatible): `FieldType.ANY` for validated-only mixed regions and
  `NormalizedValue.external_ref` for cell-level external-dependency policies.

### D-042 · 2026-10-16 · Customer 360: a separate full-customer deterministic repository; account-grain receivables; explicit sources; no AI (M7-1)
- **Separate repository:** Customer 360 is its own deterministic repository (`services/customer360.py`), not the
  minimized agent context.
  - It indexes every canonical customer of the live sales workbook.
  - The agent context and `/api/context` stay top-N and unchanged.
- **Exact live sources:** it is built from the live context's exact sources: the sales basename inside `DATA_FOLDER`,
  that workbook's `liste`, and the context's aging file.
  - The sales workbook's max date must equal the context's `as_of_data_date`.
  - The cache is keyed by the source signature. A changed signature drops the snapshot immediately (never served
    stale) and rebuilds; mismatches are refused, never mixed. No mtime.
- **Identity:** D-003 exactly; codes are never trimmed or fuzzy-matched.
- **Master attributes** (KAPALI, sector, rep, name) come from `liste` by exact canonical code. The cached sales lookup
  columns are not used (the DQ-5-safe pattern for new M7 features; M5 `cust_attrs` is unchanged). A duplicate master
  code is never resolved to one row.
- **Receivables stay at invoice-account grain.** Shared invoice accounts are flagged, and their balances are never
  allocated to one order customer. Cari aging is not Detailed Collections (M6-4 stays blocked).
- **Sources with unresolved file-of-record decisions** enter only through explicit configuration. Hedef is used only
  via `HEDEF_PATH` / `HEDEF_SNAPSHOT_DATE`, with field-level D-023 status visible (stale capacity/Risk/Kredi,
  incomplete visits). Owner-only sources and Trodat are not used.
- **Labels and scope:** amounts are labelled "Net Sipariş Tutarı (Nettutar)" (revenue basis open). No margin, no
  scores or rankings, no cross-sell, no CRM writes.
- **No AI:** Customer 360 is read-only and never calls AI. Its new data (Hedef values, event names, master
  attributes) is not sent to any AI provider in M7-1; that needs a separate review.

### D-043 · 2026-10-16 · Günlük Satış: a customer-centric queue over the existing reasons, from private uncut candidates; M5 unchanged (M7-2)
- **Reasons:** only existing, tested customer categories: `collections`, `winback`, `real_decline`.
  - No new rule, score or threshold: no cross-sell, potential, territory, fair, CRM follow-up, new-buyer,
    positive-growth or salesperson logic.
  - Product actions stay in M5. Later M7 sources join by adding their category to `QUEUE_CATEGORIES` after their own
    approval.
- **Candidate seam:** `actions.produce_candidates` returns every validated action, sorted stably, before any cut.
  - M5 `ctx["actions"]` is that list cut to `MAX_DAILY_ACTIONS`: the same ids, scores, facts, order and AI cache
    keys (regression-tested against the old implementation).
  - The queue never reads the cut top 15, so products cannot take sales slots.
- **Private by construction:** the customer candidates (≤ `MAX_SALES_QUEUE_CANDIDATES`) and their rows are attributes
  of `AgentContext` (a dict subclass), not dict items.
  - They are never serialized to `/api/context`, `agent_context.json`, evidence packets or logs.
  - They swap atomically with the context.
  - The entity index uses them only as a fallback for codes M5 does not know.
- **One card per canonical customer** (Customer 360 identity; the M5 "Temsilci:" fact is not shown).
  - `queue_score` = maximum visible reason score; secondary reasons add nothing; priority = `config.band`.
  - A visible collections reason makes the card collection-first and its primary topic. This sets the sequence only,
    with no score bonus.
  - Order: score ↓, collection-first, code ↑.
- **Collections** attach only when the invoice account maps to exactly one customer. Shared accounts are never
  assigned or divided, and unlinked accounts are not attached. Both are counted and stay in M5.
- **KAPALI:** only the trusted unique master KAPALI removes sales reasons; collections stay and drafts are limited to
  collection. A duplicate master invents no status (warning only).
- **State** is per reason, in the shared SQLite table with the M5 ids (`is_hidden` semantics). The queue upserts the
  reasons it returns, so complete / defer / dismiss / reopen work outside the top 15.
- **Consistency:** the queue is served only when the live context signature equals the Customer 360 snapshot
  signature (503 `building` otherwise). It never reads Excel and never calls AI; a message draft is generated only on
  an explicit click through the existing copy-only endpoint.
- **Bounded pool:** reasons hidden by state are not backfilled from below the 200-candidate pool (documented limit).

### D-044 · 2026-10-16 · Cross-sell at anagrup level; capped confidence score; descriptive territory; per-category queue reservoir (M7-3)
1. **Group, not SKU:** cross-sell operates on the native Halsa `anagrup` of the trusted sales file. It recommends a
   product GROUP and never a SKU, so no stock, margin or Trodat mapping is involved.
   - If the column is missing the model is unavailable. It never switches to another dimension.
2. **Recent behaviour, full-history gap:**
   - The association uses customers with an order in the 365 days to the sales data date (never `today()`) and their
     in-window purchases.
   - Directed pairs need support, confidence and lift above `config.CROSS_SELL_*`. These are model-quality guards and
     are not tuned to reach a count.
   - A target must be a group the customer has never bought in the whole trusted history, reached from an eligible
     source bought inside the window.
   - One opportunity per target group (strongest source by confidence, lift, support, label); at most three per
     customer.
3. **Score = association confidence, capped at 69:** `min(69, round(confidence × 100, 1))`.
   - Support and lift only gate eligibility.
   - Cross-sell is never `high` or `critical`, so it cannot outrank collections or a major decline.
   - Action confidence is `medium`: trusted facts, inferred recommendation.
   - "Confidence" is the co-occurrence share among active source buyers, not a purchase probability.
4. **Hedef stale data produces no score:** no potential, penetration, capacity, Risk or Kredi value is used by
   cross-sell, territory, the queue or Customer 360 opportunities. Capacity stays visible only as stale history in the
   Hedef card.
5. **Territory is descriptive and unverified:**
   - Counts per Hedef İl / İlçe / Sektör kodu: matched, active, current-year, cross-sell customers, targets.
   - Labelled unverified, with an explicit "not a performance ranking" statement.
   - No salesperson field is aggregated, and no action or score comes from a territory.
6. **Action surfaces and a per-category reservoir:**
   - Categories declare `action_center` / `sales_queue`. Cross-sell is sales-queue only, so the M5 top 15 and
     `/api/agent/categories` are unchanged.
   - The private Günlük Satış reservoir keeps the best N (200) candidates per category, which removes starvation
     without re-weighting.
   - The final page has no quotas.
   - The conversation sequence becomes collections → winback → the rest by score (extends D-043; scores unchanged).

### D-045 · 2026-10-16 · Fuar Merkezi: FUAR-only history; curated campaigns; manual funnel separate from ERP; due fair tasks (M7-4)
1. **Historical fair truth = explicit FUAR attribution only:**
   - orders whose `Srmmerk` label type is FUAR (M6-2 grammar and `event_id`), at order-document grain;
   - ETKİNLİK and SAHA are excluded and there is no `is_fair` grouping (taxonomy open);
   - no conversion window, so later GENEL orders are never attributed;
   - no ROI (costs are not attributed to editions);
   - amounts are labelled "Net Sipariş Tutarı (Nettutar)"; missing amounts are counted, never zero;
   - date-conflict orders stay out of date ranges, and customer-conflict orders stay out of customer lists.
2. **Historical invitations and attendance are unknown** until their source is recovered. A FUAR-tagged order means
   "FUAR-attributed customer", never attendee, visitor or invitee. The historical funnel starts empty.
3. **Upcoming fair lists are explicitly curated.**
   - Campaigns are created by the user.
   - Members are added by exact Customer 360 code (bulk add ≤ 200).
   - Read-only segments (historical FUAR, active, winback, real decline, cross-sell, collections) never enroll anyone.
   - Trusted master KAPALI is blocked; an unknown master status is never turned into KAPALI.
4. **Funnel state is local, user-maintained operational state** (SQLite `fair_campaigns` / `fair_customers` /
   `fair_history`), distinct from ERP attributed orders.
   - A linked edition (exact year + stem + sequence, never fuzzy) shows ERP evidence and offers an explicit "mark as
     Sipariş".
   - A disagreement is shown, never synchronized.
   - Campaign state survives source refreshes.
5. **Due fair tasks enter Günlük Satış** from an explicit `next_action_date` (today or earlier) in a planned/active
   campaign at a non-terminal stage.
   - Category `fair_followup`, sales-queue surface only.
   - Fixed operational score `FAIR_TASK_SCORE = 55` (no financial prioritization); confidence `high` for the task's
     existence.
   - Id facts = campaign, stage, date.
   - Sequence: collections → winback → fair task → rest.
   - Queue action state (complete / defer / dismiss) never changes the funnel stage, and stage changes never complete
     the task.
6. **Campaigns are reusable and multi-edition** (planned / active / archived; archive instead of delete), not
   hard-coded to one January fair. No salesperson assignment, CRM inference, AI or new-buyer discovery.

### D-046 · 2026-10-17 · Lightweight CRM: append-only interactions, one explicit current follow-up, incomplete coverage, no person metrics (M7-5)
1. **Two concepts, existing storage:**
   - append-only interaction history in the existing `memory` table (fixed kind `crm_contact` + nullable columns
     `occurred_on`, `channel`, `purpose`, `outcome`, `next_action`, `owner_label`; safe `ALTER ADD COLUMN`
     migration); old rows are never edited, a correction is a new entry; older memory rows stay readable as legacy
     history and are not counted as coverage;
   - one current follow-up per canonical customer in `crm_followups` (replace or clear explicitly), never derived from
     old `next_followup` values.
2. **Closed vocabularies and limits:** channel phone / whatsapp / email / visit / other; purpose sales_followup /
   collection / service / other; outcome ≤ 120, next action ≤ 160, note ≤ 500, owner label ≤ 80; contact dates may be
   backdated, never in the future; exact Customer 360 codes only (D-003). Validation errors never echo the text.
3. **Coverage is always `incomplete` ("Eksik kayıt olabilir")** until the owner states that logging is standardized
   (D-024). Missing records mean unknown activity, never "not contacted"; there is no "uncontacted customer" task.
4. **No salesperson / person metrics:** the owner label is manual, non-authoritative free text; it is never
   aggregated, ranked, filtered on or used as a task fact. The summary is counts only.
5. **Nothing is inferred:** no next date or next action from an outcome. Only an explicit current follow-up with
   `due_date ≤ today` creates a Günlük Satış task.
6. **Günlük Satış task `crm_followup` ("Takip Zamanı"):** request-time from SQLite, sales-queue surface only (never the
   M5 action center or the build-time reservoir); fixed `CRM_FOLLOWUP_SCORE = 60`, confidence high; id facts = purpose,
   due date, next action (no note, no owner label); sequence collections → crm_followup → winback → fair_followup →
   rest by score; collection / service follow-ups are `operational`; drafts: collection → collection, sales_followup →
   sales follow-up, service / other → none. Queue action state never erases the follow-up.
7. **KAPALI:** history may always be recorded; a follow-up for a trusted-KAPALI customer only for collection or
   service; duplicate masters never invent KAPALI; a customer that later becomes KAPALI has its sales follow-up
   suppressed from the queue while the state is kept.
8. **Separation:** no CRM content is ever sent to AI (`get_memory` excludes `crm_contact` rows); fair funnel state and
   CRM state stay separate; `Dijital Pazarlama Raporu_2025.xlsx` is not imported; orphan CRM rows are counted, never
   reassigned or deleted; CRM state survives refreshes; no Excel read per request.

### D-047 · 2026-10-17 · Yönetim owner view; owner sources explicit and optional; scheduled-obligation Kredi; source-native Gider/Gelir; Bütçe health; DQ-3; M7 complete (M7-6)
1. **Owner sources stay explicit-path, optional inputs** (`KREDI_PATH`, `GIDER_GELIR_PATH`, `BUTCE_PATH` + optional
   declared dates / years), imported only through `import_kredi` / `import_gider_gelir` / `import_butce`. Nothing is
   discovered or ordered by mtime; configuring a file is not a file-of-record decision. They are never required by the
   core refresh, the dashboard or any M7 workflow. Lazy load, process-lifetime compact cache, explicit guarded reload;
   per-source isolation. Core refresh and owner reload are separate workflows.
2. **Yönetim is owner-audience data in local single-user mode.** The tab says so and is not access-controlled; real
   role enforcement waits for an authenticated multi-user deployment (D-016).
3. **Kredi 30/60/90 is a scheduled-obligation view, not a cash-flow forecast:** unpaid `remaining_amount` by due date,
   cumulative windows from the local date, past-due shown separately, per currency (no FX), freshness shown separately
   (unknown snapshot stated), no remaining-principal total (not additive). The unverified `DURUM` field never drops an
   unpaid installment (in the real export most `Kapalı` contracts still carry unpaid installments); it is only counted.
4. **Gider/Gelir stays source-native and unverified** until the accounting basis and the management mapping are
   approved: the four native lines separate, totals only where M6 verifies them, no M5 margin, no profitability.
5. **Bütçe exposes validity / coverage**, not variance: no budget-vs-actual and no reconciliation with Gider/Gelir.
6. **M7-6 does not fix DQ-1 / DQ-2 / DQ-4 / DQ-5** (each changes a reported number or list and needs owner
   approval); they are shown in the known-issues panel.
7. **DQ-3 is fixed:** `m_momentum` sorts by `(pct, code)`; ordering only, no value or membership change.
8. **Owner data never enters AI** (evidence, actions, drafts, automatic comments) and never logs.
9. **Performance:** Customer 360 consumes a private, one-use projection of the sales frame the context already read
   (no second sales read); the M6-2 order-source import stays because it is the only fail-closed validation of those
   columns.
10. **M7 is complete after M7-6** even though M6-4 (Detailed Collections) remains blocked by a missing export.

### D-048 · 2026-10-18 · Father-first, owner-maintained web launch is the next delivery goal (M8 plan)

- **Owner direction:** The owner is in the USA and handles all technical maintenance and monthly uploads of
  reports prepared by Halsa staff. His father in Türkiye (70) must use a secure bookmarked web link without
  installing software, running commands, importing files or maintaining the service. Feature scope from the father's
  original voice-message analysis is preserved, not reduced merely to simplify hosting.
- **Release boundary:** first authenticated deployment serves ONLY the owner and his father, with all existing
  valid M1–M7 functionality. Employees get separate, restricted access only in a later explicit checkpoint.
  Hosting, permissions, backups and unified monthly publication are necessary for production acceptance.
- **User experience:** one owner-controlled monthly upload validates/updates ALL relevant core and optional
  reports and resulting cached screens in a consistent generation; previously recorded CRM, fair and action state
  survives refresh, failure and reboot. A failed batch cannot replace the last good dashboard.
- **What is NOT decided:** no provider/regulatory assessment, credentials, file-of-record, financial accounting
  definition, DQ-1/2/4/5 treatment, commercial targets, or Kredi `DURUM` interpretation is implied.
  Kredi DURUM awaits a specific follow-up on 2026-10-19. M6-4 still needs its source. The original audio/voice
  transcript is not bundled in the repository and must be explicitly checked if verbatim coverage is claimed.
- **Execution:** `docs/m8_father_first_launch.md` is the planning and acceptance baseline;
  `docs/m8_owner_questions_tr.md` is the owner-decision questionnaire. Implementation consists of bounded
  checkpoint PRs, with synthetic CI and a final remote real-data acceptance. An implementation plan does not
  constitute a live deployment.

### D-049 · 2026-10-18 · Owner directive: no verified evidence = no business conclusion; fail-closed accuracy gate (M8-ACCURACY)
- **Owner directive (highest priority; supersedes earlier decisions that kept potentially misleading figures visible
  behind a warning, e.g. parts of D-047):** only results supported by valid source data, a known report date, an
  explicitly defined deterministic calculation, passing reconciliation, validated joins, an approved business
  definition, regression tests and an accurate label may be shown as business facts. Accuracy outranks features,
  appearance, performance and completion. M8-ACCURACY precedes M8-CP1; later checkpoints use Claude Code.
- **Decision:**
  - three internal states `VERIFIED` / `NOT_VERIFIED` / `INVALID` in `services/accuracy.py`;
  - an in-code register is the ceiling; `VERIFIED` only with D-xxx approvals for every definition, no open blocker,
    named regression tests and real-data reconciliation evidence (`validate_register`, import time);
  - runtime source evidence only downgrades (unknown = not passed); dependencies propagate; errors fail closed;
  - enforcement in the backend: dashboard build embeds no withheld rows and drops withheld panel scripts; an HTTP
    middleware withholds metric-bound routes (409 `accuracy_withheld`) and any unclassified route; mixed endpoints
    redact field by field; `GET /` refuses dashboards built without the gate; refresh reports and warnings never carry
    unverified totals; the legacy `weekly_agent.py` refuses to run;
  - neutral notice "Bu analiz henüz doğrulanmadığı için gösterilmiyor." + short reasons; never zero, an estimate or
    another field in place of a withheld value;
  - AI interpretations / drafts withheld in production regardless of `AI_PROVIDER` (default stays `disabled`);
    model text is never evidence.
- **State at this checkpoint:** no output is VERIFIED (no approved definitions except D-003, no report dates for
  stock / Cari, no real-data reconciliation in this environment). DQ-1, DQ-2, the new **DQ-6** (win-back overdue joined
  by the wrong identity), Gider/Gelir contradictory totals and Bütçe errors are INVALID. Operational records (CRM,
  fair campaigns, action state), system status, known issues and the customer directory lookup stay visible.
- **Tests:** the pre-existing suite runs against a TEST-ONLY harness (`tests/accuracy_harness.py`) so the preserved
  algorithms keep their regression coverage; `production_gate` tests run the real gate. Production code has no
  switch that disables the gate.
- **HTTP status choice:** 409 for a withheld resource (it exists, but its verification state conflicts with display);
  401/403 stay free for M8-CP1 authentication.
- **Consequences:** the father-facing dashboard shows notices instead of every analysis until owner answers and
  real-data reconciliation allow promotions, one reviewed PR each (`docs/accuracy/release_gate.md` §2).

### D-050 · 2026-10-18 · M8-ACCURACY correction pass after the independent review of PR #27
- **Directory attributes are analyses, not reference data.** `GET /api/customer360/search`, CRM follow-up / customer
  responses and fair member rows keep the customer code and recorded display name only; the derived last-order date
  (`customer.last_order_date`) and the open / KAPALI status (`customer.master_status`) are withheld in the backend
  (`ACC.redact_directory`). Conservative KAPALI safeguards keep restricting actions server-side.
- **Evidence fails closed.** Every `SourceEvidence` field starts unknown; unknown never passes; inspection errors are
  recorded as failures (INVALID) — `_excel_errors` previously returned 0 on error (and errored on every numeric column).
  Collectors no longer assert joins, period completeness or mappings they did not check.
- **Time per metric:** `Metric.temporal` = operational (needs a fresh source, `max_source_age_days`) or historical (needs
  a dated, complete period; no age limit). Both need a known report date and verified completeness. Age only withholds.
- **Generation-bound evidence:** immutable `EvidenceSnapshot` keyed by a content hash of the exact frames; a gate uses
  evidence only for the generation it decides for (`Gate.bound_to` drops evidence of other generations). The API gets
  no snapshot yet, so every source-bound metric stays withheld there. `validate_register(production=True)` refuses any
  VERIFIED certification while `GENERATION_BOUND_API_EVIDENCE` is False. Sharing the published generation's snapshot
  with the API, owner views and refresh (with transition / rollback / restart tests) is a **mandatory M8-ACCURACY-2
  acceptance criterion** (`docs/accuracy/release_gate.md` §6 A–F).
- **Unchanged:** no financial calculation or business rule; Kredi DURUM unresolved; DQ-1/2/4/5/6 unresolved.

### D-051 · 2026-10-19 · One source-generation manifest; declared source dates; revised Kredi layout (M8-ACCURACY-2)
- **One generation, one manifest.** Every published core generation and every loaded owner source is described by a
  manifest of SHA-256 fingerprints of the **original workbook bytes** plus declared dates, validation counts and
  `SourceEvidence` (`services/generation.py`). Dashboard, API, Yönetim and refresh use it; evidence binds per source
  only when the served fingerprint equals the recorded one. Mixed generations never combine.
  A failed refresh keeps the previous dashboard, manifest, context and evidence. A restart restores only an exact match.
- **Fingerprints follow bytes, not frames.** A formula, cell type or workbook revision change gives a new generation
  even when pandas reads identical values (the PR #27 frame hash could not see these).
- **Dates are declared, never inferred.** `data/sources.json` (private) selects the core files explicitly and declares
  snapshot / report dates and period completeness. File timestamps are information only. Without the file the
  modification-time choice is recorded as `mtime_fallback` and cannot verify. The 45-day operational limit is
  provisional (`operational_freshness`, unapproved).
- **Fingerprint-bound approvals.** A core source may carry an approval (D-xxx, `totals_consistent`,
  `mapping_approved`) bound to one SHA-256. It is ignored for any other revision and cannot promote a metric by itself.
- **Revised Kredi layout.** The owner's filtered `kredi takip` workbook is supported as layout `filtered_takip`
  (two-row header, ERP control row, SUBTOTAL row, saved date filter). All rows are imported, hidden ones included.
  Full-source totals are checked against the control row; filtered totals are checked separately against the
  SUBTOTAL row. Negative remaining amounts and Kapalı rows with remaining amounts are reported, never repaired.
  The saved filter is a view, never a payment fact. The real workbook was not opened in the cloud; the owner runs
  `scripts/reconcile_sources.py` locally.
- **No promotion.** `GENERATION_BOUND_API_EVIDENCE` stays False until the owner's real-data acceptance
  (`docs/accuracy/release_gate.md` §8.3). No metric is VERIFIED. The Kredi module stays withheld. DURUM semantics,
  D-003, DQ-5 and DQ-6 are unchanged.

### D-052 · 2026-10-20 · Request-pinned generations; the saved evaluation is never trusted (review of PR #28)
- **One request, one view.** Inside an HTTP request the context, the owner-source entries and the Customer 360 snapshot
  are read once and frozen, and the accuracy gate is built once from the published manifest and those views'
  fingerprints. Endpoints return the pinned objects, so the evidence that authorizes a response belongs to its data.
  A publication during a request affects later requests only. Publication (dashboard + manifest + context, or owner
  view + owner record) and the capture of a request's gate inputs share one lock.
- **Ids cover the evaluation.** `core_id` / `generation_id` digest full record content (validation results, evidence,
  joins), not only source identity. This unkeyed checksum detects corruption; it is not presented as tamper protection.
- **Restart re-derives.** A persisted manifest is only a candidate; evidence is re-derived from the original bytes and
  the current selection file and accepted only if it reproduces the recorded `core_id`. Otherwise no evidence.
- **Reconciliation output** stays private: evidence defaults to `data/reconciliation/`; the manifest beside the
  dashboard is git-ignored. Overall result PASS / OPEN / FAIL with distinct exit codes.
- **Unchanged:** `GENERATION_BOUND_API_EVIDENCE` False; no metric VERIFIED; DQ-1 … DQ-6, D-003, Kredi DURUM open.

### D-053 · 2026-10-20 · Dashboard bytes are authorized like an API response; evidence output never touches an input
- **`GET /` is gated.** The HTML lists the metrics it embeds. The manifest records the SHA-256 of the exact published HTML.
  `GET /` captures the bytes and the accepted generation under the publish lock and binds evidence only when both match
  the accepted, re-validated generation. The current gate must authorize every embedded metric; otherwise 503, no
  data. A page that embeds no source-bound metric needs no evidence; a page without the marker is refused.
- **Reconciliation never writes over inputs.** A destination that is, or aliases, an input, or that already exists,
  is refused. Evidence files are created exclusively (no truncation possible), and write failures are reported (exit 4).
- **Readiness is reported on three levels:** technical foundation / owner-local real-data acceptance / individual
  metric promotion (`docs/accuracy/release_gate.md` §11.3).
- **Unchanged:** `GENERATION_BOUND_API_EVIDENCE` False; no metric VERIFIED; Kredi questions, DQ-1 … DQ-6, D-003 open.

### D-054 · 2026-10-20 · GET / serves only bytes this server published for the accepted generation
- A dashboard file's own markers (policy, generation, displayed metrics — including an empty list) are claims, never
  evidence. The on-disk HTML is served only when an accepted, re-validated generation exists, the markers match it,
  the bytes equal the digest this process recorded when publishing them, and the current gate authorizes every
  displayed metric. Otherwise a server-generated neutral page (503); the file is never returned.
- After a restart the re-derived generation gets no dashboard digest from the saved manifest. The server re-builds the
  dashboard from the re-derived evidence and publishes page + manifest + digest as one locked step.
- A plain `dashboard_builder.py` build is not served; publication goes through the refresh.
- **Unchanged:** `GENERATION_BOUND_API_EVIDENCE` False; no metric VERIFIED; Kredi DURUM, D-003, DQ-1 … DQ-6 open.

### D-055 · 2026-10-21 · Report-control rows, dependency-scoped judgement, lock files, bounded re-publication wait (M8-ACCURACY-2B)
- **Stock report-control rows** are recognized only by deterministic structure (aggregate over the own column and
  exactly the product range, or an adjacent label row) and reconciled cell by cell; anything else stays a row with
  missing identity. They are never products, never duplicates and never silently dropped.
- **Product count** = rows carrying a product code (not deduplicated), withheld as `stock.product_counts` until code
  authority (two `Stok_Kodu` columns), report scope and source dates are confirmed.
- **Scoped judgement:** a metric may declare the fields and joins it reads; it is then judged on those, source
  integrity always applies, missing dependency evidence is NOT_VERIFIED, undeclared metrics stay strict. No
  relationship is approved because it is common; D-003, DQ-5, DQ-6 unchanged.
- **Lock files** (`~$`, `._`, `.~lock`) are never sources.
- **Restart:** GET / waits (bounded, default 60 s) for an in-progress valid re-publication; never serves old bytes.
- **Unchanged:** no metric VERIFIED; `GENERATION_BOUND_API_EVIDENCE` False; no financial calculation changed except
  that report-total rows no longer count as products (and in the DQ-4 count).

### D-056 · 2026-10-21 · Owner-confirmed DinamikCRM stock layout: column A = product code, column J = country of origin (column identity only)
- **Owner statement (2026-10-21, review of PR #29):** in the DinamikCRM stock export, physical column **A** is the
  actual product / stock code and physical column **J** is the country of origin. Both are labelled `Stok_Kodu`.
- **Implementation:** `services/source_validation.STOCK_TEMPLATES["dinamikcrm_stok_v1"]` pins the approved layout by
  header row 4, column count (89), the SHA-256 of every header label in physical order (no label or data is
  committed; measured owner-locally), identity column A, country-of-origin column J, and the physical column of every
  pipeline field (Stok_Kodu A, Stok_Adı B, TOPLAM STOK MİKTAR W, STOK Kontrol % AI, Envanter değeri AV, minseviye BN,
  maxseviye BO, DURUM CB). `check_stock_template` decides the identity column:
  - exactly one `Stok_Kodu` header → unambiguous, unchanged behaviour (read by name);
  - more than one → accepted only when the whole header row is exactly an approved template; then A is the product
    code, J is recorded as country of origin and never read, and the duplicated header counts as resolved;
  - any other duplicated-identity layout (moved, renamed, added, removed or reordered column) → refused:
    `identity_header_ambiguous`, `layout_supported = False`, every stock metric INVALID. Never "the first column".
- **Residual risk (documented, not hidden):** a header-only signature cannot tell the two identically-labelled
  columns apart if their CONTENTS were swapped with the labels unchanged. No content heuristic is used to "detect"
  that (it would be an inference); it stays an item for the Veri Kontrol Merkezi and owner review.
- **Scope of the approval:** column identity ONLY. It does not establish freshness, completeness, report date,
  quantities, totals, financial accuracy or product-level correctness. The duplicated product code (row grain) stays
  open (`STOCK-REPORT-SCOPE`); nothing is merged or deleted. `STOCK-CODE-AUTHORITY` is no longer a static blocker —
  the template evidence decides. No metric VERIFIED; `GENERATION_BOUND_API_EVIDENCE` stays False.

### D-057 · 2026-10-21 · DinamikCRM report conventions (owner statement; not evidence)
- Halsa staff generate all recurring ERP reports in DinamikCRM; the owner uploads the Excel files manually. No
  DinamikCRM integration exists or is required.
- Recurring exports keep the established layout (D-056 enforces this for the stock export; any layout change is
  refused until a new template is approved).
- The intended reporting period runs from 1 January of the earliest selected year to the selected / latest
  reporting date.
- **These conventions are NOT proof** of completeness, of the snapshot / report date, or of any figure. Dates still
  have to be declared per upload (D-051 rules unchanged: no date from file timestamps or mtime), and period
  completeness is never inferred from the earliest / latest transaction dates.
- Historical SKUs absent from today's stock **may** be discontinued, but no SKU is classified as discontinued without
  source evidence.
- **Still open:** the duplicated stock-code row grain; the four 2022 duplicate sales rows; 120 / 320 account treatment
  in collections; Kredi `DURUM`; unknown source dates. Kredi `DURUM`, revenue definitions, DQ-1–DQ-6 and D-003
  unchanged.

### D-058 · 2026-10-21 · Partial footer reconciliation is OPEN, never PASS; review never certifies (PR #29 correction pass)
- `stock_footer_totals` is **PASS only when every checked footer cell reconciled** (matched == checked, no ambiguous
  row); any contradictory total is **FAIL**; error-propagated, missing-cache or unsupported cells, ambiguous rows or
  nothing checked are **OPEN**, with matched / unreconciled counts and the affected column letters shown.
- A partial reconciliation caps `totals_consistent` at "not verified" (None) — no declaration can turn it into True;
  a contradiction makes it False (INVALID).
- Footer evidence is also **field level** (`footer_totals` per field): a metric is NOT_VERIFIED only when a field it
  reads has a partial footer, INVALID when a field it reads contradicts; an unrelated column's formula errors never
  invalidate an independent field.
- Veri Kontrol Merkezi (design, `docs/m8_veri_kontrol_merkezi.md`): reviewing or acknowledging a data issue records a
  human decision; it **never** certifies a metric, never changes source data and never deletes a suspected duplicate.

### D-059 · 2026-10-21 · Column-footer problems are judged per field; source-wide totals stay strict (2nd review of PR #29)
- **Defect:** D-058 kept footer evidence per field, but a scoped metric still inherited the source-level
  `totals_consistent` verdict, which a partial / contradictory footer in ANY column caps at None / False — so an
  unrelated cost-column footer withheld a quantity-only metric.
- **Fix:** `SourceEvidence` now records where that verdict comes from: `declared_totals_consistent` (the
  fingerprint-bound declaration alone), `footer_totals` (whole-footer PASS / OPEN / FAIL) and `footer_scope`
  (`per_field` only when the rows are recognized, at least one footer cell was checked AND matched, and there is no
  ambiguous trailing row; otherwise `source_wide`; None without footer rows). `totals_consistent` and the strict
  judgement are unchanged.
- **Scoped judgement** replaces the source-level totals verdict only when the per-column evidence can stand on its own
  (`per_field`), the record is internally consistent (the strict verdict equals the declaration capped by the footer
  status) and the metric reads at least one field of the source. It then applies: the declaration source-wide (False →
  INVALID, unknown → NOT_VERIFIED) and, per used field, its own footer result — PASS or `not_aggregated` (no footer
  totals that column) pass; OPEN → NOT_VERIFIED; FAIL → INVALID; missing / unknown → NOT_VERIFIED. In every other case
  the strict source-level verdict applies (fail closed).
- **Unchanged:** the source's overall reconciliation stays OPEN / FAIL and is reported; every other source-integrity
  check and every used join still applies; unscoped metrics keep the strict judgement; a record whose totals verdict
  is more optimistic than its own footer reconciliation is INVALID. No metric VERIFIED; `GENERATION_BOUND_API_EVIDENCE`
  False. D-056–D-058, Kredi, revenue, DQ-1–DQ-6, duplicate-record and supplier-account rules unchanged.

### D-060 · 2026-10-22 · Simplified access: viewing without login, one owner administration login (M8-CP1)
- **Owner direction (2026-10-22):** Halsa Analysis is a browser-based web application, not a native app. Anyone with
  the dashboard link views the permitted read-only content without logging in; the owner authenticates before any
  administrative or data-changing operation (report upload / refresh, CRM and follow-up edits, fair and action
  changes, data-review decisions, verification approvals, business rules, system settings, paid AI). No separate
  father and owner accounts, no multi-user system.
- **Supersedes:** D-048's "owner + father, each with their own login" and the M8-CP1 scope in
  `docs/m8_father_first_launch.md` (two seeded owner/father accounts, "unauthenticated read/write uniformly denied",
  login/recovery path). D-016 (multi-user later) is unchanged. D-048's other points (father-first, owner-maintained
  monthly upload, no deployment before acceptance) stand.
- **Decision:**
  - one register, `services/access.py`, gives every route one level — `viewer`, `owner_read`, `owner_write` or
    `auth`; one middleware enforces it before the accuracy gate; unlisted routes are refused for everyone; a
    state-changing route can never be viewer level; owner-only query parameters (`/api/customer360/status?retry`);
  - one owner password, stored only as an scrypt hash in the private `.env` (`HALSA_OWNER_PASSWORD_HASH`), provisioned
    with `python -m services.owner_auth hash-password`; no hash → administration disabled (fail closed);
  - server-side sessions (SQLite keeps only the token's SHA-256), HttpOnly / SameSite=Strict / Secure-over-HTTPS
    cookie, absolute 12 h and idle 120 min limits (bounded env), logout, log-out-everywhere, CLI revocation, revocation
    on password change, rotation on login;
  - CSRF token = HMAC of the session token (same-origin page reads it from `/api/auth/session`) + Origin check on
    every owner write; `X-StockAgent` stays as a legacy local guard and is **not** authentication;
  - login throttling per client and global (SQLite, pseudonymous client keys); password accepted only over HTTPS or
    on the loopback interface; standard security headers; `/docs`, `/redoc`, `/openapi.json` disabled;
  - owner-only reads: Yönetim (`/api/owner/*`, owner finances and system state), `/api/accuracy/status`,
    `/api/context` (bulk export), `/api/ai/calls`. Viewer-visible operational records: CRM, follow-ups, fair
    campaigns, customer directory (code + recorded name);
  - GETs audited for side effects (`docs/m8_cp1_access.md` §2.5): the action-queue / sales-queue reads keep their
    insert-only materialization of the published queue (never a human decision); everything else that starts work is
    owner-only.
- **Accuracy unchanged:** authentication never changes a gate decision; the owner sees exactly the same withheld
  notices as a viewer. No metric VERIFIED; `GENERATION_BOUND_API_EVIDENCE` False; no financial calculation, revenue
  definition, Kredi `DURUM`, DQ-1–DQ-6, DinamikCRM mapping, source date or completeness rule changed.
- **Disclosure:** an unauthenticated internet URL is not private because it is hard to guess. Today a viewer can read
  customer codes and names, CRM outcomes / notes / owner labels and fair-member notes; every metric that is later
  VERIFIED becomes viewer-visible too. The full inventory is `docs/m8_cp1_access.md` §6. **No public hosting without
  the owner's explicit, recorded sign-off on that inventory** (and the CP8 hosting / KVKK review).
- **Alternatives rejected:** separate father login (owner does not want it); a shared "viewer password" (a link plus
  a shared secret is still effectively public and adds friction); OAuth / e-mail login (external services, recovery
  complexity); treating `X-StockAgent` as identity (any client can send it).
- **Veri Kontrol Merkezi (D-058, M8-VKM-1):** issue visibility may be viewer-level where it carries no private value;
  review decisions, notes and status changes are owner-write with the session's identity as reviewer. Review never
  certifies a metric.
- **Tests:** `tests/test_m8_cp1_access.py` (real sessions, `production_auth`), `tests/test_m8_cp1_browser.py` (real
  login in Chromium), jsdom access-mode tests. Pre-existing in-process feature tests run as the owner through the
  TEST-ONLY `tests/auth_harness.py` (same pattern as D-049's accuracy harness); production code has no bypass.

### D-061 · 2026-10-22 · Three access levels: public viewer, financial viewer (password, read-only), owner (M8-CP1 revision)
- **Owner approval (2026-10-22):** Halsa Analysis stays ONE browser web application with three levels:
  1. **Public viewer, no password** — the normal dashboard sections. The owner **explicitly approves** that customer
     names, CRM notes, follow-ups and fair-campaign notes are visible to anyone with the link (answers D-060's
     disclosure question 1). The accuracy gate applies unchanged.
  2. **Financial viewer, one shared financial password** — read-only Yönetim financial sections (Kredi, Gider/Gelir,
     Bütçe and future approved financial reports) for the owner's father and other explicitly trusted financial
     viewers. No usernames or accounts. Never any owner permission.
  3. **Owner administrator, separate password** — unchanged from D-060: all viewing incl. full Yönetim, and every
     data-changing or administrative operation. The owner session also opens the financial view.
- **Supersedes in D-060:** "Yönetim is owner-only" (now: the financial sections are financial-viewer readable through
  a projection; the full Yönetim stays owner-only), and disclosure question 1 (approved). Everything else in D-060
  stands: owner login, CSRF, throttling, transport rule, route register, no deployment without sign-off.
- **Decision:**
  - new access level `finance_read` (GET only; a mutating route can never be a read level) for exactly one route,
    `GET /api/finance/overview`: a server-side projection of the owner overview with ONLY the three financial
    sections, built and gate-redacted exactly as for the owner, then stripped of source diagnostics (file name, error
    code, failed step, issue codes, load time, reload flag) and validated against an explicit allow-list schema
    (`schemas.FinanceOverview`, `extra="forbid"`). `/api/owner/overview`, `/api/owner/status`, `/api/accuracy/status`,
    `/api/context`, `/api/ai/calls` and every write stay owner-only; a financial session gets 403 `owner_required`;
  - `services/owner_auth.py` gains roles: separate hashes (`HALSA_OWNER_PASSWORD_HASH`, `HALSA_FINANCE_PASSWORD_HASH`),
    sessions carry their role server-side, a role-bound credential id (rotating one password revokes only that role's
    sessions), per-role session caps (owner 5, finance 10) and per-role throttling (guessing the financial password
    never locks the owner out). The role always comes from the login ENDPOINT (`/api/auth/login` vs
    `/api/auth/finance-login`), never from the request. The provisioning command refuses a financial password equal to
    the owner's (and vice versa). An owner who submits the financial password is never downgraded;
  - revocation: logout (either role), owner "log out everywhere" (all roles), owner `POST
    /api/auth/finance-sessions/revoke` (financial sessions only, owner stays logged in), `revoke-sessions --finance`,
    financial-password rotation;
  - same cookie, CSRF, HTTPS-or-loopback password transport, expiry (12 h / 120 min) and headers as the owner;
  - UI: opening Yönetim without a session shows a Turkish password form inside the tab ("Finans görüntüleme şifresi");
    `/#yonetim` is a direct link to it; a financial viewer sees only the financial sections (no operations, system
    state, known issues, file names or reload control); the header says "Finans görüntüleme açık (salt okunur)".
- **Also corrected:** the Yönetim audience note still said "bu sekme erişim kontrollü DEĞİLDİR" after D-060 — false
  since PR #30's first commit; now states the access control (`access_controlled: true`).
- **Unchanged:** no metric VERIFIED; `GENERATION_BOUND_API_EVIDENCE` False; every financial section is still withheld
  for the financial viewer AND the owner; no financial calculation, reporting definition, Kredi `DURUM`, DinamikCRM
  mapping, DQ-1–DQ-6 or source date changed. No deployment. Veri Kontrol Merkezi stays M8-VKM-1 (its decisions are
  owner-write; a financial session can never make them).
- **Residual risk:** a shared password is shared — anyone who learns it reads the financial sections (once verified)
  until it is rotated; the financial view may lazily load the configured owner workbooks on first open (read-only,
  same as the owner view; never a reload). Public hosting still needs the owner's recorded sign-off on the remaining
  §6 questions (network gate / hosting region / KVKK).
- **Tests:** `tests/test_m8_cp1_finance.py` (37, real sessions), 2 new browser tests (financial password flow incl.
  `/#yonetim`; owner full view), 1 jsdom header test; the D-060 tests updated for the new session fields.

### D-062 · 2026-10-24 · Veri Kontrol Merkezi implemented: deterministic issues, append-only history, owner review (M8-VKM-1)
- **Scope:** implements the D-058 design (`docs/m8_veri_kontrol_merkezi.md`) under the D-060 / D-061 access model. No
  change to the access architecture, the accuracy gate, a metric definition, Kredi `DURUM`, revenue definitions,
  DQ-1–DQ-6, source dates or DinamikCRM mappings.
- **Detection (`services/vkm.py`, pure):** runs only on an ACCEPTED generation, from what that generation already
  recorded (formula-aware validation counts, `SourceEvidence`, join report). A fixed catalogue of named checks maps each
  recorded fact to exactly one issue type of design §2 (incl. stock duplicate codes, identical sales lines, undefined
  stock coverage / DQ-4, Excel errors, uncached formulas, footer mismatch / partial reconciliation, refused layouts and
  duplicate headers, unmatched products / customers / invoice / Cari accounts, DQ-5 and DQ-6 counts, undeclared report
  dates and periods, mtime-fallback selection, unknown or refused mapping approval, Kredi negative remaining /
  "Kapalı" with remaining / filter-hidden rows, missing exports). No AI, no inference, no new business relationship.
- **Stable, privacy-safe IDs:** `VKM-` + first 16 hex of SHA-256(`issue_type|source_key|locator`); the locator is a
  schema label, column letter or check name — never a value, file name, fingerprint or generation.
- **Persistence (`services/vkm_store.py`):** the existing private SQLite. `dq_issue` (current state, a trigger refuses
  DELETE), `dq_issue_event` and `dq_observation` (append-only, triggers refuse UPDATE / DELETE), `dq_source_state`.
  Carry-over per design §3.2: detected → `acik`; still present → decision kept, grown counts reopen a reviewed /
  acknowledged issue; absent from an uploaded source → `cozuldu_kaynakta`; returns → `yeniden_acildi`; source not
  uploaded → `source_missing` event, status unchanged, plus one `missing_source` issue (never resolves anything). The
  three core exports and Kredi are always expected; Gider/Gelir and Bütçe once configured or previously seen. The same
  observation again (repeated refresh, restart re-derivation) writes nothing. Source records are never deleted,
  deduplicated or modified.
- **Hook:** `GenerationStore.add_observer`; notified after install / re-derivation / owner-record replacement, outside
  the store lock; an observer failure never affects publication; a superseded generation is not written.
- **Access:** `GET /api/vkm/summary` viewer (allow-list `VkmPublicSummary`: counts by severity / status / type, last
  observation date, fixed notice); `GET /api/vkm/issues[/{id}]` owner_read; `POST …/review` and `…/notes` owner_write
  (session + CSRF + Origin). The reviewer is the session's role, never a request field. A review or note against an
  older observation token (`vkm_generation`) is refused (409). Notes ≤ 500 characters, control characters removed,
  rendered as text, never logged, never sent to an AI provider. Disclosure inventory updated (`docs/m8_cp1_access.md`
  §6).
- **Review is not certification:** reviewing / acknowledging / annotating changes only the issue's review status and
  appends history. No evidence field, gate decision, manifest, approval reference or `GENERATION_BOUND_API_EVIDENCE`
  changes (property test over every register metric under the production gate). No metric VERIFIED.
- **Not detectable from the current pipeline (left to owner decisions, unchanged):** DQ-1 (cost columns are not
  inspected by the core validator), DQ-2 (a list rule, not a data fact), the 120/320 supplier-account split.
- **Tests:** `tests/test_m8_vkm.py` (unit, workbook-level, store, HTTP with real sessions, publication hooks) and
  `tests/test_m8_vkm_browser.py` (real Chromium).

### D-063 · 2026-10-24 · Veri Kontrol Merkezi state integrity: unknown ≠ resolved, generation-bound observations, visible out-of-sync (M8-VKM-1 review)
- **Context:** the independent review of PR #31 at `b699e89` found three state-integrity gaps (not in the metric gate):
  an uninspectable present source resolved earlier issues; observation identity ignored accepted-generation and
  declared-period changes; a failed observation after publication was invisible and still allowed reviews.
- **Decision:**
  1. Detector scopes (`services/vkm.py`): each check has a scope; each observation records the scopes actually
     evaluated (a missing count is "not checked", never zero). An issue absent from a present upload is resolved only
     when its scope was evaluated; otherwise presence `not_checked`, status kept, one append-only `not_checked` event.
  2. Observation digest = detector version + state + fingerprint + provenance (core: `core_id`; owner: record content
     digest) + declared period + evaluated scopes + issues/counts. Exact repeat refreshes and restarts are idempotent.
  3. Sync: reads compare the store with the served generation's expected observations; `out_of_sync` /
     `no_generation` is shown to every audience and every decision is refused (409 `out_of_sync`). New owner_write
     route `POST /api/vkm/sync` (CSRF; idempotent; superseded-generation guard). Publication stays independent of the
     recorder. A core observation never overwrites an owner record carried by the served generation.
- **Unchanged:** append-only history and no-delete triggers; the `source_missing` carry-over rule; access model;
  `GENERATION_BOUND_API_EVIDENCE` False; no metric VERIFIED; no financial calculation, mapping, DQ rule, Kredi
  `DURUM`, source date, authentication or source byte changed.
- **Tests:** `tests/test_m8_vkm_integrity.py` (regressions with negative controls replaying the old rule, the old
  digest and the store-only review check), +1 browser scenario (out-of-sync banner, blocked decisions, owner resync).

### D-064 · 2026-10-24 · Veri Kontrol Merkezi: per-column footer scopes and race-free owner decisions (M8-VKM-1 re-review)
- **Context:** the independent re-review of PR #31 at `847c055` confirmed D-063 and found two narrower defects:
  (A) `rows:footer` was granted for any `footer_by_column` mapping — including `{}` when no footer cell was
  reconciled — and covered every column, so a column-H footer problem could be "resolved in the source" by an upload
  with no footer or with a footer for column D only; (B) a review / note computed the served generation's expectation
  BEFORE taking the issue-store lock, so a decision based on generation A could commit after generation B was
  installed while B's observer was still waiting for that lock.
- **Decision:**
  - (A) Footer scopes are per physical column and per check (`footer:<letter>:footer_mismatch | footer_partial |
    footer_missing_cache`). A column's footer scopes count as evaluated only when that column's footer cells were
    reconciled again and ALL matched; no footer cells, a footer for other columns only, or a non-comparable
    (error-propagated / SUBTOTAL / uncached) or mismatched footer leave earlier issues of that column `not_checked`.
    Unrecognized trailing rows have their own scope (`footer:ambiguous_rows`). `DETECTOR_VERSION` `vkm-1.2`.
  - (B) Owner decisions and the owner resync take `request_pin.PUBLISH_LOCK` first, then the issue-store lock, and
    read the LIVE served generation under that lock in the same step as the commit (the expectation is passed as a
    callable). Lock order everywhere: `PUBLISH_LOCK → vkm_store._LOCK → GenerationStore._lock`; nothing acquires
    `PUBLISH_LOCK` while holding either of the others. Restart re-derivation installs outside `PUBLISH_LOCK` but only
    moves "nothing served" to the already-published `core_id`, during which decisions are refused (`no_generation`).
    The publication observer waits at most `OBSERVER_LOCK_TIMEOUT` (2 s) for the lock and `OBSERVER_DB_TIMEOUT` (2 s)
    for SQLite; on timeout the observation is abandoned (recorded failure → out of sync, decisions refused, resync),
    and publication proceeds.
- **Unchanged:** D-062 / D-063 behaviour (detector scopes, provenance-bound observations, out-of-sync handling),
  append-only history and no-delete triggers, `source_missing` carry-over, privacy controls, the three access levels,
  `GENERATION_BOUND_API_EVIDENCE` False, no metric VERIFIED; no financial calculation, mapping, DQ rule, Kredi
  `DURUM`, source date, authentication or source byte changed.
- **Tests:** `tests/test_m8_vkm_review2.py` — H mismatch then no footer / D-only footer / non-comparable H footer →
  UNKNOWN (with negative controls replaying the old `rows:footer` rule), rechecked matching H → resolved, per-column
  scope unit test; a barrier-controlled A→B race (B installed, observer held) refusing the decision and note with a
  negative control computing the expectation before the lock; an HTTP decision that waits for an in-flight
  publication and is then refused as stale; a busy issue store not blocking publication; interleaved publications and
  decisions without deadlock.

### D-065 · 2026-10-25 · M8-CP3 Excel Upload Center: owner-only upload, declared report details, background validation, explicit generation-bound publication
- **Context:** Step 1 (real Excel compatibility on the owner's Mac) was accepted 2026-10-25: the private Stock, Sales and
  Cari exports load without manual changes; supported owner workbooks were tested; 0 of 33 metrics VERIFIED. The owner
  requires the website to take the monthly Excel exports (exported by the father from DinamikCRM) with **no direct
  DinamikCRM integration** of any kind.
- **Decision:**
  - Owner-only tab **Dosya Yükleme** and fixed page `/yukleme`: select files → declare report type and details →
    background validation job (status polling) → preview → explicit **Değişiklikleri Yayınla**.
  - The report type is chosen by the owner per file, never from the file name; dates / years are declared or unknown,
    never inferred. Uploaded workbooks are stored privately under content-addressed names, so no reader can infer
    anything from a name; the importer framework's file-name pattern check is skipped only for owner-typed uploads
    (`explicit_source_type()`, recorded as INFO). Gider/Gelir and Bütçe need their year declared to be read.
  - Upload safety: size limit while streaming, .xlsx container check, ZIP bomb / part-name / encryption / DTD rules.
  - Validation reuses the controlled refresh in stage-only mode (`PublishHooks`), the M6 owner loaders without
    publication and a read-only VKM delta; the preview changes nothing live.
  - Publication reuses the generation-bound refresh with the batch as explicit selection and the previewed `core_id`
    as the required identity; selection files (`data/sources.json`, new `data/owner_sources.json`) are written inside
    the swap step and restored on failure; uploaded owner workbooks are installed in the same publish-lock step.
    Only the newest batch is publishable; a preview is stale once the served core generation or a selection file
    changed; an identical set publishes nothing.
  - Core reports missing from a batch are carried over only from an explicit live selection.
  - Hedef / Trodat are not offered (not in the generation manifest).
- **Unchanged:** `GENERATION_BOUND_API_EVIDENCE` False; no metric VERIFIED; no approval, mapping, source authority,
  business definition, reporting date or Kredi `DURUM` meaning inferred; CRM / fair / action / VKM history untouched;
  access model (owner_read / owner_write with session + CSRF + Origin; viewers and financial viewers refused).
- **Docs / tests:** `docs/m8_cp3_upload_center.md`; `tests/test_m8_cp3_upload_files.py`,
  `tests/test_m8_cp3_upload_center.py`, `tests/test_m8_cp3_browser.py` (real Chromium, production app and gate).

### D-066 · 2026-10-25 · M8-CP3 review fix: an Upload Center publication is all-or-nothing
- **Context:** independent review of PR #32 at `40fdef1` found two failure-atomicity gaps: (1) if `data/sources.json`
  was written but writing `data/owner_sources.json` (or staging the owner configuration) failed, the earlier selection
  file stayed rewritten; (2) a failing owner-workbook installation after the core dashboard had already changed was
  reported as a successful publication (with a warning).
- **Decision:**
  - The commit step (`before_swap`, under `PUBLISH_LOCK`) snapshots the live context, the generation state, the owner
    repository state and the bytes (or absence) of both selection files, then writes the files and stages the owner
    configuration; any failure inside it restores everything it already changed before re-raising.
  - `refresh.PublishHooks.after_install` now **fails closed**: a failure restores the previous dashboard and manifest
    and runs the full refresh undo (context, generation, selections); the job ends as `publish_failed` with
    "önceki pano ve veriler geri yüklendi". A new `on_commit` hook runs only after every step succeeded.
  - `OwnerRepository.install_entries` no longer swallows a failing generation-record publication; the repository and
    `GenerationStore` have `snapshot_state` / `restore_state`, so a partial installation (one record published, the
    next failing) is undone exactly. `ContextRepository.restore_live` puts the previous context object back.
  - Generation observer notifications (the Veri Kontrol Merkezi path) raised by the publishing thread are **held**
    during the commit and delivered only on commit; on rollback they are dropped, so a failed publication writes no
    VKM observation or history. Holding is thread-scoped (other threads are notified at once).
  - Workbook copies placed in the data folder by a failed publication attempt are removed again.
  - The swap-step failure message now says whether the previous dashboard was restored.
  - Lock order unchanged (`PUBLISH_LOCK → vkm_store._LOCK → GenerationStore._lock`); notifications are still issued
    outside `GenerationStore._lock`. No change to the Upload Center UI or to any business calculation.
- **Unchanged:** `GENERATION_BOUND_API_EVIDENCE` False; no metric VERIFIED; CRM / fair / action / VKM review history
  untouched (also asserted after every injected failure).
- **Tests:** `tests/test_m8_cp3_atomicity.py` — failure injection at the first and second selection write, a torn
  owner-selection write, owner configuration staging, owner installation before any record is published and after a
  partial installation (mixed and owner-only batches); after each failure dashboard / manifest / selection bytes,
  data-folder and upload bytes, context object, generation and evidence binding, owner configs and views, finance
  states, all VKM tables and CRM / fair / action tables are unchanged, and a restart serves the same generation; a
  clean publication afterwards commits and is observed.
- **Still open before hosting (not part of this fix):** single-process serving (CP2) and upload retention / cleanup (CP5).

### D-067 · 2026-10-25 · M8-CP3 review fix: the final freshness check is atomic with the commit
- **Context:** the D-066 re-review of PR #32 at `f1d4e25` confirmed the rollback fixes and found one remaining merge
  blocker. A core-changing upload compared the preview's base (served `core_id` + selection-file hash) with the live
  state only before the long copy / owner import / build; `refresh._pipeline` then took `PUBLISH_LOCK` for the swap
  without comparing again. An independent publication B (e.g. the owner changes `sources.json` and runs the controlled
  refresh) finishing in that window would be overwritten by the older preview A. The identical-upload path likewise
  reported "Değişiklik yoktu" without rechecking what was served when the job ran.
- **Decision:**
  - `refresh.PublishHooks.precheck()`: runs FIRST under `PUBLISH_LOCK`, before `before_swap`, before any selection
    file, configuration, dashboard or manifest byte changes. A refusal ends the run as step `stale` with nothing
    changed (`previous_data_kept`); `had_live` / `had_manifest` are now observed under the lock, so a refusal or a
    restore can never touch another publisher's files.
  - The Upload Center's precheck compares the preview's base with the live base (same `_base()` as the earlier
    checks). A refused job ends with code `stale`, batch status `stale` and the existing stale message ("Hiçbir şey
    yayınlanmadı; dosyaları yeniden doğrulayın"), recorded as `publication_failed` (code `stale`) in the append-only
    history. The owner-only path uses the same check under the same lock.
  - The identical-upload path rechecks the base under `PUBLISH_LOCK` before reporting "unchanged"; if the served
    version changed it is refused as stale.
  - Cleanup of a failed attempt never removes a data-folder file that a live selection file names.
  - Lock order unchanged (`PUBLISH_LOCK → vkm_store._LOCK → GenerationStore._lock`); no new lock edge. D-066 rollback
    unchanged; single-process scope unchanged.
- **Unchanged:** `GENERATION_BOUND_API_EVIDENCE` False; no metric VERIFIED; upload permissions; CRM / fair / action /
  VKM review history.
- **Tests:** `tests/test_m8_cp3_stale_race.py` (8, event-gated, no timing): core-only and mixed core + owner batches
  overtaken by an independent controlled refresh B before the refresh lock and by a selection change inside it are
  refused, and B's dashboard / manifest / selection / data bytes, context, generation, owner views, finance states, all
  VKM tables and CRM / fair / action tables stay exactly as B published them, also across a restart; an identical
  upload overtaken by B is stale, not "unchanged"; a fresh preview afterwards publishes; the hook runs under the lock
  and a refusal changes nothing. Six of these fail on `f1d4e25` (the obsolete preview publishes over B / reports
  "unchanged").

### D-068 · 2026-10-29 · A Cari row holding only empty text is not an account row (M8-R2 / T1)
- **Context:** M8-R1 (PR #34) found that the real DinamikCRM Cari export ends with a row without an account code whose
  only written cells hold the empty string. The formula-aware scanner counted every row with any written cell as a data
  row, so the Cari showed 825 data rows for 824 accounts and 11 "blank amount" cells (one per month bucket), an OPEN
  reconciliation item and the Veri Kontrol issue `aging / amount_blank` — none of them a real blank amount. The
  owner's confirmation that the row is report furniture could not change that evidence (PR #34 review).
- **Decision:** `source_validation.scan_sheet` gets an opt-in `skip_empty_text_rows`, set per source in `CORE`; only the
  Cari aging source opts in. With it, a row whose ONLY written cells are literal empty text ("" as a shared, inline or
  `str` value) is not a data row: it is counted in `empty_text_rows` (in the inspection summary and therefore in the
  generation manifest; never silent) and none of its cells is counted in any column.
- **Preserved:** a row with any other written cell stays a data row exactly as before, its empty-text cells included —
  an account code, an amount, a numeric zero, a formula (also one whose cached result is ""), a missing formula cache,
  an Excel error, a boolean, a date, whitespace-only or any other text, or an unresolvable shared string (fails closed).
  Genuine blank amounts and missing account codes are still counted. No code is trimmed, no matching rule changes,
  no value is substituted.
- **Not changed:** stock (its row analysis and footer recognition, D-055 – D-059, decide identity-less rows; the scan
  keeps counting every written row) and sales (no evidence of the pattern). Pre-existing and outside T1: an
  empty-text cell inside a real row still counts as a present value in its column (for stock and Cari alike) —
  recorded as an observation, not changed here.
- **Effect on the 5 Oct exports (owner-local, isolated, counts only):** Cari 824 data rows, 0 blank bucket cells, 1
  empty-text row; reconciliation 7 FAIL / 23 OPEN (was 24); Veri Kontrol 20 (was 21); gate unchanged (28 invalid,
  5 not_verified); `receivables.collections` still NOT_VERIFIED for its other reasons. Stock and sales evidence
  identical.
- **One-time consequence:** the inspection summary gained the `empty_text_rows` key, so the same workbook bytes produce a
  different `core_id` than before. A manifest saved by an earlier build is not reproduced at restart
  (`evidence_mismatch`, nothing binds) until the next controlled refresh / upload publication — the existing
  fail-closed path; no migration is attempted. The Veri Kontrol issue `aging / amount_blank` resolves in the source at
  the next observation of a Cari upload.
- **Unchanged:** release gate, `GENERATION_BOUND_API_EVIDENCE` False, no metric VERIFIED, no business rule, D-003 /
  D-042 identity rules, DQ-1 – DQ-6.
- **Tests:** `tests/test_m8_r2_t1_empty_text_rows.py` (inline and shared strings, the real two-row ending, a negative
  control with the option off, genuine blanks, missing codes, every preserved cell kind, unresolvable shared strings,
  stock and sales unchanged, generation evidence and gate identical with and without the phantom row).
