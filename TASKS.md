# TASKS.md — active backlog

Rules: one *Current* task at a time. Each task must be executable in one bounded session.
Milestone 5 is complete. All available M6 sources are done (M6-0 to M6-3, Kredi, M6-5 Trodat, M6-6 Gider/Gelir, M6-6 Bütçe). M6-4 is BLOCKED (missing export). M7-1 to M7-6 are merged; **Milestone 7 is COMPLETE** (PR #24, squash merge `de7eb43`; D-047). M8 production-readiness planning is merged (PR #26); CP1 authentication is the only active implementation task.

## Current
- [ ] **M8-CP1 — Security-first owner/father authentication:** implement server-side authentication and authorization for ALL sensitive UI and API routes, safe account provisioning and revocable sessions, CSRF protection, login rate limits, and synthetic/browser tests. See `docs/m8_father_first_launch.md`; one isolated PR; no public deployment or financial rule changes. Kredi `DURUM` remains unverified until owner clarification.

## Next (M8 — production launch)
- [ ] **M8-CP1–CP8** as sequenced in `docs/m8_father_first_launch.md`. One implementation PR/checkpoint at a time, with tests and verified acceptance; revise count if approved feature-gap work expands.
- [ ] **Owner: answer `docs/m8_owner_questions_tr.md`**, identify real file-of-record and supply missing exports via a private channel; do not commit real customer data. The original voice-note transcript is not verified in the repo, so signoff on feature coverage is still required.
- [ ] **Owner: provision approved private cloud hosting, billing, encrypted backup and secure account setup**; verify privacy/transfer handling for Halsa data, plus father usability from Türkiye. Never deploy current unauthenticated app publicly.
- [ ] **Owner: monthly-report upload acceptance**, including complete source date/revision selection, all affected M7 views, preserved CRM/fair/action state and failure rollback. No local installation for father.
- [ ] **Later: Kredi DURUM semantics clarification**, one narrow follow-up after 2026-10-19 response. Do not interpret Kapalı as paid before then.

## Next (after M7: owner decisions and follow-ups; start each only when moved to *Current*)
- [ ] **Owner: cross-sell threshold review.** On real data the initial guards give a broad population (78 % of active
  customers have at least one target). Decide whether to tighten them (`config.CROSS_SELL_*`); nothing changes
  automatically.
- [ ] **Owner: create the January fair campaign** in Fuar Merkezi (name, edition year, start date; optionally the
  exact ERP stem once the orders exist). Curate the invite list explicitly; segments only filter.
- [ ] **Owner: fair decisions:** ETKİNLİK / SAHA taxonomy, post-fair conversion window, fair cost attribution (for any
  future ROI), recovery of historical invitation / attendance lists, and whether `FAIR_TASK_SCORE` (55) fits.
- [ ] **Owner: CRM decisions (D-046).** When (if ever) logging is standardized enough to drop "Eksik kayıt
  olabilir"; whether a CRM export exists to import as legacy history; whether `CRM_FOLLOWUP_SCORE` (60) fits. Owner
  labels stay free text until a salesperson-of-record decision.
- [ ] **Owner (optional): Yönetim sources.** Set `KREDI_PATH` (+ `KREDI_SNAPSHOT_DATE`), `GIDER_GELIR_PATH`
  (+ `GIDER_GELIR_REPORT_YEAR`) and / or `BUTCE_PATH` (+ `BUTCE_PLANNING_YEAR`, `BUTCE_SNAPSHOT_DATE`) to explicitly
  chosen files; that is not a file-of-record decision. Note: in the Feb 2026 Kredi export, `DURUM = Kapalı` appears on
  152 unpaid installments; confirm what `DURUM` means (D-047 keeps it informational).
- [ ] **Later (performance): single-pass framework reader.** The M6-2 order-source import (both openpyxl views of the
  sales sheet) is now the largest Customer 360 startup step. Reading each cell's formula and cached value in one XML
  pass would roughly halve it without weakening validation. Framework work; needs its own tests.
- [ ] **Future (not scheduled):** CRM legacy import only if a customer-level export appears (through the M6
  verification process first); new-buyer discovery / acquisition source; live macro provider; AI quality tuning;
  authentication / multi-user / remote deployment with owner/admin roles (Yönetim is not access-controlled today).
- [ ] **Owner (optional): Customer 360 Hedef.** To show Hedef context, set `HEDEF_PATH` (and optionally
  `HEDEF_SNAPSHOT_DATE`) to one explicitly chosen file. That is not a file-of-record decision.

## Next (owner decisions, open data-quality items, blocked work)
- [ ] **Owner: M5 check on the Mac** (`git pull`, refresh, open http://127.0.0.1:8000/, desktop + phone width).
  Steps in STATUS.md "Owner follow-ups".
- [ ] **Owner: one live WhatsApp draft tone check** (at most one paid call, never sent). If the tone needs work,
  record it under Q-2.
- [ ] **Owner: decisions and missing sources from M6-0** (`docs/m6_source_inventory.md` §16): provide the
  `Evrak Detaylı Tahsilat Raporu` export, a CRM export if one exists, and the historical fair invitation/attendance
  lists; decide the fair taxonomy (ETKİNLİK / SAHA), business-line mapping, revenue basis, salesperson of record,
  Trodat mapping, Hedef/Bütçe file of record.
- [ ] **BLOCKED — M6-4 Detailed collections.** Needs the `Evrak Detaylı Tahsilat Raporu` export. Do not approximate it
  from the account-level Cari aging. `TAHSİLAT ÖNCELİKLİ` only after threshold sign-off.
- [ ] **Owner: Kredi file of record.** The Feb 2026 `kredi sözleşmeleri.xlsx` and a June 2026 `kredi borçları 27 haziran
  2026.xlsx` (old transfer folder) both pass. The Dec 2025 `kredi borçları.xlsx` (revolving loans) is unsupported.
  Revolving (`Rotatif`) loans would need their own rule set if they matter.
- [ ] **Owner: Trodat → Halsa SKU mapping (D-039).** Who maintains an explicit mapping table (Trodat model + optional
  variant → one or more Halsa SKUs, with status, effective date and provenance)? Also confirm whether the Trodat values are
  units, and the Trodat file of record and cadence. M6-5 imports the native data without any mapping.
- [ ] **Owner: Bütçe decisions (D-041).** Choose the file of record (`BUTCE_2026 Ocak.xlsx` vs `BUTCE_2026 Taslak.xlsx`;
  both import). Decide whether the row-1 link to `2026_gider_gelir_TASLAK.xlsx` is intended and should be fixed in the
  workbook. Confirm what `<y> expenses` (no scenario word) means. Confirm the semantics of the 2018–2023 columns in
  the group blocks, titled `TOTAL SALES`. Repairing the `#REF!` cells (cumulative-deviation column, growth ratios)
  is a workbook fix for the owner; the importer will never repair them.
- [ ] **Owner: Gider/Gelir decisions (D-040).** Map the four native lines (KIRTASIYE / TRODAT / TROTEC / IMALAT) to the
  management views (kaşe / kırtasiye / other-import). Confirm the accounting basis (cash vs accrual), the file of record
  and cadence, and whether older-year templates (2024/2025) or the TASLAK layout must be supported.
- [ ] **Later (owner cash planning):** M7-6 shows the scheduled 30/60/90-day loan obligations (not a cash forecast).
  Still open: outstanding principal (needs a contract-level definition), interest / tax visibility, and any combination
  with receivables, suppliers, purchasing, tax and payroll.
- [ ] **Owner: Hedef file of record** (D-037). Candidates: the Feb 2026 `hedef raporu.xlsx`, a June 2026
  `hedef raporu 27 haziran.xlsx` found in an old transfer folder, and the dated snapshots. Also the refresh
  cadence. Until decided, Hedef is imported only from an explicit path.
- [ ] **Owner: fair taxonomy and conversion window** (M6-2 keeps `fuar`, `etkinlik` and `saha` separate; D-036). The
  grouping and any follow-on window are configuration for M7.
- [ ] **Identity whitespace policy (small, needs a decision):** 20 sales `Musterikod` and 19 `Pro_kodu` values carry a
  trailing space. Exact matching leaves one invoice account unmatched that matches after trimming. Decide whether
  customer-code fields declare `trim=True` (a spec change, not fuzzy matching). M6-3 found 5 Hedef codes with
  surrounding whitespace (kept exactly), and Hedef rep names padded with a trailing space.
- [ ] **DQ-4: Stock coverage `#DIV/0!` rows are silently excluded.** In the stock export, `STOK Kontrol %` is
  `#DIV/0!` for 1,178 of 3,314 products (5 Oct 2026 data); `m_stock` coerces errors to NaN and requires coverage,
  so these products never appear in Azalan / Fazla Stok. Decide the rule (for example a separate "no coverage"
  list); **needs owner approval** (changes dashboard lists).
- [ ] **DQ-5: Sales `Temsilci` / `Açık / Kapalı` / `Sektör` are invoice-account lookups.** They are Excel XLOOKUPs
  on `Musterikod` against the current `liste` sheet (cached values). `cust_attrs` applies them to the order-source
  customer (`Pro_kodu`), and 10.6 % of order-source customers have several invoice accounts. M6-1 added the seam
  (`AttributeSource`: attribute ← master field via a declared key and identifier role); M5 `cust_attrs` is
  unchanged and DQ-5 stays open. Changing M5 behavior **needs owner approval**.
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
  See `docs/roadmap_m6_m7.md`. Sequence: M6-0 source inventory and technical verification (local, docs
  only: headers and types, the 2020–2026 sales report row grain, join identifiers, activity/source values,
  CRM fields, fair-list locations, rule thresholds) → M6-1 importer framework (validation report,
  fail-closed, per-field data-confidence status D-023, entity-resolution match report) → M6-2 order
  activity/source normalization (fair attribution foundation, D-025) → M6-3 hedef raporu (stale
  `Haftalık Kaşe Adedi` / `Risk` / `Kredi`) → M6-4 detailed collections (+ `TAHSİLAT ÖNCELİKLİ` after
  threshold sign-off) → M6-5 Trodat sales → M6-6 owner sources (gider/gelir by business line, kredi
  takip, bütçe only with cell-level validation).
- [ ] **M7 — Sales operating system (planned, D-021; after M6).** Daily sales queue, customer 360,
  cross-sell, January Fair Command Center (built on existing trusted fair attribution), light CRM with a
  coverage/completeness requirement (no salesperson ranking from raw CRM counts, D-024), Sales Support /
  Inside Sales Support concept, new-buyer discovery and acquisition-source tracking (separate from Geri
  Kazanım; no web scraping). Python selects and ranks; AI only explains.
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
- **M6/M7 owner questions:** resolved on 2026-10-10 (see `docs/roadmap_m6_m7.md` §7, D-023 to D-025).
  Only technical verification remains, in M6-0. Nothing blocks M5.
- **Lapsed window 2020 vs 2021.** The spec says the analysis window is 2020–2025, but the code
  (`dashboard_builder.QUAL_YEARS`) uses 2021–2025. M3 did not change it, and
  `test_lapsed_thresholds_are_the_configured_ones` pins the current value. Owner decides; if
  2020 should count, change `QUAL_YEARS` and that test together.

## Completed
- [x] 2026-10-18 — M8-P0 docs-only launch plan, father questionnaire, D-048 and checkpoint backlog merged as PR #26 (`c34a504`). CI: 1058 passed, 7 skipped, 1 xfailed; no runtime changes or deployment. Next: CP1 authentication.
- [x] 2026-10-18 — PR #25 post-M7 local readiness squash-merged to `main` (`d67fb0b`): optional `.env.example` paths, privacy-safe `python -m scripts.owner_preflight`, synthetic tests, README and local launch checklist. CI: 1058 passed, 7 skipped, 1 expected failure. Mac/browser acceptance and real-file paths require local owner verification. Kredi `DURUM` stays unresolved.
- [x] 2026-10-18 — PR #24 (M7-6 Yönetim / integration) squash-merged into `main` (`de7eb43`); **Milestone 7 COMPLETE**. Kredi DURUM remains unverified and informational; owner clarification pending 2026-10-19. M6-4 still blocked by the missing export.
- [x] 2026-10-13 — M6-0 source inventory and technical verification (docs only): `docs/m6_source_inventory.md`,
  D-034. Sales grain, identifiers, `Srmmerk` activity/source field, fair-attribution feasibility, Hedef, Trodat,
  gider/gelir, kredi, Bütçe, fair files, CRM gap; DQ-4 and DQ-5 logged.
- [x] 2026-10-13 — PR #10 (M5-6) merged into `main` (`b3e4e4a`); **Milestone 5 complete**.
- [x] 2026-10-13 — **Milestone 5 — AI Aksiyon Merkezi: COMPLETE.** M5-6: Playwright smoke tests in CI (real
  FastAPI + Chromium, synthetic data, disabled/mock AI), hostile-text escaping at the browser boundary, action-state
  round trip across reloads, refresh smoke, `file://` mode, phone/desktop layout; small AI-tab responsive and focus
  polish. Harness decision in D-033.
- [x] 2026-10-13 — PR #9 (M5-5) merged into `main` (`1f018c4`).
- [x] 2026-10-11 — M5-5 controlled refresh: `services/refresh.py` (validate → build staging → verify → atomic
  swap → report) shared by `POST /api/context/refresh` and `python -m services.refresh`; frozen single-read source
  snapshot; cross-process flock; `ContextRepository.replace_context`; `dashboard.prev.html`; refresh button with
  pending/success-reload/failure states. Notes in D-032.
- [x] 2026-10-11 — PR #7 (M5-4) merged into `main` (`4868265`).
- [x] 2026-10-11 — M5-4 drafts and customer drawer: channel × purpose draft contract (legacy `message_type`
  mapped), KAPALI → collection only (Python, before any AI call), draft-only prompt layer (action cache key
  unchanged), customer-facing leak check, copy-only UI; deterministic customer/product GETs, AI analysis moved
  to guarded POST `/analyze`; lazy, session-cached drawer. Notes in D-030 and D-031.
- [x] 2026-10-11 — PR #6 (M5-3) merged into `main` (`c9e1a4d`).
- [x] 2026-10-11 — M5-3 action state: "✓ Yapıldı" / "⏸ Ertele" (+7 days default) / "✕ Gizle" on each card,
  server-confirmed, 5-second "Geri al" via the new `POST /api/actions/{id}/reopen`. Notes in D-029.
- [x] 2026-10-11 — PR #5 (M5-2) merged into `main` (`0061a43`).
- [x] 2026-10-11 — M5-2 read-only AI Aksiyon Merkezi tab (`_ai_panel()`, server-described filters,
  top-5 / next-5 / per-card AI, every §10 state except refresh, jsdom DOM tests in CI). Notes in D-028.
- [x] 2026-10-11 — PR #4 (M5-1) merged into `main` (`df4ba28`).
- [x] 2026-10-10 — M5-1 backend prerequisites: generic action contract + source registry +
  `GET /api/agent/categories`; deterministic `reason`; `GET /api/agent/actions` never calls the provider;
  `POST /api/agent/actions/enrich` (≤5 open ids, one call, `ai_cache`); `X-StockAgent` guard on all
  mutating requests; `GET /` serves the dashboard; warm-up with `context_ready`. Notes in D-027.
- [x] 2026-10-10 — PR #3 (design + roadmap, docs only) merged into `main` (`f9c42fb`).
- [x] 2026-10-10 — Owner answers incorporated into the roadmap (D-023 data confidence, D-024 sales
  organization and CRM coverage, D-025 fair/acquisition attribution, D-026 legitimate transactions only).
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
