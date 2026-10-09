# TASKS.md — active backlog

Rules: one *Current* task at a time. Each task must be executable in one bounded session.
Milestone 5 must not be started until it is moved to *Current*.

## Current — Milestone 3 (close-out)
- [ ] **M3-C1: Push the checkpoint to GitHub and verify CI.**
  Push `main` and `claude/milestone-3-live-ai`, then check that the `tests` workflow is green
  on GitHub Actions. The cloud session's push was refused (403, Claude GitHub App not installed
  on the repo), so push from the Mac or install the app first.
  Done when `git ls-remote origin` shows both branches at the local SHAs and CI passes.
- [ ] **M3-C2: Run the single live provider smoke test.**
  On the Mac, put `OPENAI_API_KEY` in `.env` and run
  `AI_PROVIDER=openai python scripts/live_smoke_test.py`. It sends one synthetic packet and makes
  at most 2 requests. Record provider, model, result, latency and tokens in STATUS.md.
  If the default model `gpt-5-mini` is not available on the account, set `AI_MODEL`
  (the default lives only in `config.DEFAULT_MODELS`).
  Done when the script prints `RESULT: OK` and the outcome is recorded.
- [ ] **M3-C3: Merge `claude/milestone-3-live-ai` into `main`** after C1 and C2, via a PR on GitHub.

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
- [x] 2026-10-09 — Repository bootstrap: Milestone 1+2 code imported into git, `.gitignore`
  hardened, fixture names sanitized (commit `d37db1f` on `main`).
- [x] 2026-10-09 — M3: OpenAI Responses API provider, centralized factory, `ai_settings()`,
  evidence packets, fact references, numeric guard with one bounded retry, categorized failures,
  AI call metadata in SQLite, prompt rules, synthetic fixtures, CI workflow, repository-memory docs.
- [x] Milestone 2 — FastAPI backend and provider-agnostic AI layer.
- [x] Milestone 1 — deterministic foundation.
