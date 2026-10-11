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
