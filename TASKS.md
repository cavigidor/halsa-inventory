# TASKS.md — active backlog

Rules: one *Current* task at a time. Each task must be executable in one bounded session.
Milestone 5 must not be started until it is moved to *Current*.

## Current
- [ ] **Merge PR #3 (design, docs only).** The M5 design was approved on 2026-10-09 (D-015 to D-020).
  The M6/M7 roadmap and the M5 extensibility rules (D-021, D-022) were added on 2026-10-10. M5-1 starts
  only after this merge.

## Next (M5 slices, design §11; one bounded checkpoint each)
- [ ] **M5-1 Backend prerequisites (no UI).** Includes the D-022 extensibility seams: generic action
  contract with `source`, an action-source registry, and `GET /api/agent/categories`. `GET /` serves `dashboard.html`. `GET /api/agent/actions`
  never calls the provider and returns cached AI text (update the M2 test that expects enrichment on
  GET). `POST /api/agent/actions/enrich` takes ≤5 open action ids and makes one provider call, with an
  `ai_cache` table keyed by action_id + prompt version + model. A deterministic Turkish `reason` per
  action. The `X-StockAgent: 1` guard on every POST (403 without it). A warm-up thread and
  `context_ready` in `/api/health`. Tests per design §11.
- [ ] **M5-2 Read-only tab.** Cards (reason, facts, AI block, evidence chips), filters, top-5 / next-5 /
  per-card AI controls, and every §10 state except refresh. A separate `_ai_panel()`; the existing
  dashboard table code is untouched.
- [ ] **M5-3 Action state.** Done / defer / dismiss with a 5-second undo.
- [ ] **M5-4 Drafts and the customer drawer.** WhatsApp first, then e-posta; channel × purpose
  (collection, offer, winback, sales_followup, fair_invite); D-018 tone; copy-only; KAPALI refusals.
- [ ] **M5-5 Controlled monthly refresh.** validate → build → verify → swap (D-019), endpoint and CLI,
  a refresh button and report; tests prove nothing is swapped on failure.
- [ ] **M5-6 Verification and polish.** A Playwright smoke test in CI (mock provider), the mobile layout,
  and the owner's live check.
- [ ] **DQ-1: Fix dashboard margin missing-cost handling.**
  `m_margin` uses `fillna(0)` on `Net_Tutar_Maliyet_Dusulmus`, so a missing cost counts as zero
  profit and margin is understated. Exclude rows without cost from both revenue and profit in
  the margin calculation (or show coverage %). The test
  `tests/test_domain_rules.py::test_dashboard_margin_does_not_treat_missing_cost_as_zero` is a
  strict xfail: remove the xfail mark when fixed. **Needs owner approval**, because it changes a
  number the dashboard currently shows.
- [ ] **DQ-2: Exclude KAPALI customers from the dashboard "Stok Eritme" call list.**
  The AI context was fixed in M3 (D-009). The dashboard's `m_overstock_move` buyer lists still
  include KAPALI customers. On the 5 Oct 2026 data, 2 of the top 20 overstock products were affected.
  **Needs owner approval.**

## Later
- [ ] **Multi-user / remote deployment (future milestone, out of scope for M5, D-016):**
  authenticated access, father/admin role, salesperson roles, private deployment, secure business-data
  handling, backups, possibly per-salesperson action queues.
- [ ] **Scheduled AI generation (future option, D-017):** only after the usage, cost and value of
  on-demand AI comments are observed.
- [ ] **M6 — Additional data sources / sales intelligence foundation (planned, D-021; after M5).**
  See `docs/roadmap_m6_m7.md`. Sequence: M6-0 source inventory and owner Q&A (local header inspection,
  docs only) → M6-1 importer framework (validation report, fail-closed, entity-resolution match report,
  synthetic fixtures) → M6-2 hedef raporu → M6-3 detailed collections (+ `TAHSİLAT ÖNCELİKLİ` after
  threshold sign-off) → M6-4 Trodat sales → M6-5 owner sources (gider/gelir, kredi takip, bütçe only with
  cell-level validation).
- [ ] **M7 — Sales operating system (planned, D-021; after M6).** Daily sales queue ("Bugün kimi
  aramalıyım?"), customer 360, cross-sell, January Fair Command Center, light CRM / follow-up, new-buyer
  discovery (separate from Geri Kazanım; no external scraping in M5/M6). Python selects and ranks; AI only
  explains. The existing Geri Kazanım functionality is not rebuilt.
- [ ] **Q-2 (optional): AI judgment quality.** In the accepted Q-1 run a risk still mentioned missing
  margin data despite the out-of-scope warning, and one risk was vague. Compare one live run with
  `AI_REASONING_EFFORT=medium` (or a larger model via `AI_MODEL`) against `low`, and decide the default
  on quality vs latency and cost. A deterministic option: drop risk items that mention margin or
  profitability ("marj", "kârlılık"), since margin is out of scope (D-014 style tidy).
- [ ] Milestone 4: live macro provider (TCMB/TÜİK CPI, FX) behind the `macro` interface. Today
  inflation comes from `MANUAL_INFLATION_RATE` or "unavailable".
- [ ] Restore or rewrite `arama_listesi.py` (call-tracker CSV export). It is referenced in older
  notes but was not in the Milestone 1/2 deliveries and is not on the Mac.
- [ ] Replace deprecated `@app.on_event("startup")` with a FastAPI lifespan handler.
- [ ] Consider removing the legacy Anthropic adapter or bringing it to parity with OpenAI
  (native structured output, error categories).

## Blocked / needs a decision
- **M6/M7 owner questions (do not block M5):** 12 questions in `docs/roadmap_m6_m7.md` §4. They cover
  the meaning, maintainer, update frequency and reliability of `Haftalık Kaşe Adedi`; the meaning of
  `Risk` and `Kredi` in the hedef raporu; the salespeople and new-hire assignment (roles only in the repo);
  whether visits and their outcomes are recorded; historical fair lists; whether fair orders are
  identifiable in the ERP; and customer + SKU detail in the sales export.
- **Lapsed window 2020 vs 2021.** The spec says the analysis window is 2020–2025, but the code
  (`dashboard_builder.QUAL_YEARS`) uses 2021–2025. M3 did not change it, and
  `test_lapsed_thresholds_are_the_configured_ones` pins the current value. Owner decides; if
  2020 should count, change `QUAL_YEARS` and that test together.

## Completed
- [x] 2026-10-10 — M6/M7 roadmap documented (`docs/roadmap_m6_m7.md`, D-021, D-022). Nothing implemented.
- [x] 2026-10-09 — PR #2 (Q-1) merged into `main` (`a34ccac`); CI green on `main`.
- [x] 2026-10-09 — M5-P1: design note `docs/m5_action_center.md` written, reviewed and approved by the owner
  (decisions D-015 to D-020).
- [x] 2026-10-09 — Q-1: AI summary quality. Prompt and schema guidance, deterministic tidy (D-014),
  numeric-only `missing`, and quality measurement. Live run 4 accepted (see STATUS.md).
- [x] 2026-10-09 — M3-C3: PR #1 merged into `main` (`60dffdb`). Milestone 3 complete.
- [x] 2026-10-09 — M3-C2: live OpenAI smoke test PASSED (gpt-5-mini, low effort; attempt 2 after one
  guard-triggered retry; 12 facts referenced, every value from Python). Details in STATUS.md.
- [x] 2026-10-09 — M3-C1: both branches pushed and verified with `git ls-remote`; CI run
  `37972839805` on `claude/milestone-3-live-ai` @ `dfa57de` succeeded.
- [x] 2026-10-09 — Repository bootstrap: Milestone 1+2 code imported into git, `.gitignore`
  hardened, fixture names sanitized (commit `d37db1f` on `main`).
- [x] 2026-10-09 — M3: OpenAI Responses API provider, centralized factory, `ai_settings()`,
  evidence packets, fact references, numeric guard with one bounded retry, categorized failures,
  AI call metadata in SQLite, prompt rules, synthetic fixtures, CI workflow, repository-memory docs.
- [x] Milestone 2 — FastAPI backend and provider-agnostic AI layer.
- [x] Milestone 1 — deterministic foundation.
