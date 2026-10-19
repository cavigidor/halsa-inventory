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
