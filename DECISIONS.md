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
