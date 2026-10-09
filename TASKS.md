# TASKS.md — active backlog

Rules: one *Current* task at a time. Each task must be executable in one bounded session.
Milestone 5 must not be started until it is moved to *Current*.

## Current
- [ ] **M5-P1 review (owner):** read `docs/m5_action_center.md` and answer its §11 open questions
  (who uses it and where, AI trigger, number of actions with AI text, draft channels, data refresh).
  After approval, move M5-1 to Current and record the accepted decisions in DECISIONS.md.

## Next
- [ ] **M5-1 Backend prerequisites** (design §7 items 1–5): `GET /` serves `dashboard.html`;
  `GET /api/agent/actions?ai=0|1` (default 0, no paid call on load); `POST /api/agent/actions/enrich`
  (batches of 5, `ai_cache` keyed by action_id + prompt version + model); `POST /api/context/refresh`;
  warm-up thread with `context_ready` in `/api/health`; required `X-StockAgent: 1` header on every
  POST. Tests per design §9. No UI.
- [ ] **M5-2 Read-only AI tab** (§4, §6, §8). **M5-3** action state with undo. **M5-4** drafts and the
  customer drawer. **M5-5** Playwright smoke in CI, mobile layout, owner live check.
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
- [ ] **Q-2 (optional): AI judgment quality.** In the accepted Q-1 run a risk still mentioned missing
  margin data despite the out-of-scope warning, and one risk was vague. Compare one live run with
  `AI_REASONING_EFFORT=medium` (or a larger model via `AI_MODEL`) against `low`, and decide the default
  on quality vs latency and cost. A deterministic option: drop risk items that mention margin or
  profitability ("marj", "kârlılık"), since margin is out of scope (D-014 style tidy).
- [ ] Milestone 4: live macro provider (TCMB/TÜİK CPI, FX) behind the `macro` interface. Today
  inflation comes from `MANUAL_INFLATION_RATE` or "unavailable".
- [ ] Milestone 5: implement the "✨ AI Aksiyon Merkezi" tab, after M5-P1 is approved.
- [ ] Restore or rewrite `arama_listesi.py` (call-tracker CSV export). It is referenced in older
  notes but was not in the Milestone 1/2 deliveries and is not on the Mac.
- [ ] Replace deprecated `@app.on_event("startup")` with a FastAPI lifespan handler.
- [ ] Consider removing the legacy Anthropic adapter or bringing it to parity with OpenAI
  (native structured output, error categories).

## Blocked / needs a decision
- **Lapsed window 2020 vs 2021.** The spec says the analysis window is 2020–2025, but the code
  (`dashboard_builder.QUAL_YEARS`) uses 2021–2025. M3 did not change it, and
  `test_lapsed_thresholds_are_the_configured_ones` pins the current value. Owner decides; if
  2020 should count, change `QUAL_YEARS` and that test together.

## Completed
- [x] 2026-10-09 — PR #2 (Q-1) merged into `main` (`a34ccac`); CI green on `main`.
- [x] 2026-10-09 — M5-P1: design note `docs/m5_action_center.md` written (awaiting owner review).
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
