# TASKS.md — active backlog

Rules: one *Current* task at a time. Each task must be executable in one bounded session.
Milestone 5 must not be started until it is moved to *Current*.

## Current — Q-1: AI summary quality (branch `claude/q1-summary-quality`)
- [ ] **Q-1: AI summary quality.** In the first passing live run the customer summary listed facts
  ("₺61.000, 4, ₺70.230, 5 …") instead of interpreting them.
  Done so far: writing-quality rules in `prompts/business_agent.txt`; per-field guidance in the
  strict JSON schema (`schemas.py` descriptions); `services/ai/quality.py` measures fact runs,
  references per field, list length and jargon. It is measured and logged, never enforced, and never
  touches numbers. The smoke test prints a Q-1 verdict.
  Live run 1: metric OK but the text had tacked-on facts, duplicates and listed missing fields;
  fixed (packet relevance, sharper quality checks, prompt). Live run 2: the model repeated them, so a
  deterministic tidy step was added (D-014). Live run 3: tidy gaps + notes treated as to-dos; fixed.
  Remaining: live run 4. If the wording is acceptable, merge; remaining judgment issues are model
  capability (option: AI_REASONING_EFFORT=medium), not a reason to keep iterating.
  Done when it prints `RESULT: OK` and `quality (Q-1): OK`, then merge via PR.
  If quality still shows ISSUES, tune the prompt once more; do not loosen the numeric guard.

## Next
- [ ] **M5-P1 (preparation only): design note for "✨ AI Aksiyon Merkezi".**
  Write `docs/m5_action_center.md` covering how the static `dashboard.html` reaches the FastAPI
  endpoints (same-origin `uvicorn` serving the HTML, or CORS), which endpoints the tab uses
  (`/api/agent/actions`, complete/defer/dismiss, draft-message), how the evidence chips
  (`evidence[]`) are shown, and the offline and AI-disabled states. No UI code yet.
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
