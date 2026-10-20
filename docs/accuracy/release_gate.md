# M8-ACCURACY — Release gate and audit report

_Status: 2026-10-18. Checkpoint **M8-ACCURACY — Factual integrity and fail-closed display audit**, owner-approved to
precede M8-CP1 authentication and all production deployment work. Branch `claude/m8-accuracy-fail-closed` from
`main` @ `7f3202d`._

**Rule:** NO VERIFIED EVIDENCE = NO BUSINESS CONCLUSION. It is better to show no number than an unsupported number.
Synthetic CI success, a passing importer or a deterministic formula is **not** verification.

## 1. Classification of every audited output (do not conflate)

### A. Proven correct and displayable
**None.** No analytical output meets every VERIFIED criterion today: no business definition behind them is
owner-approved except customer identity (D-003), no stock / Cari report date is read, and no real-data reconciliation
could be performed in this environment. The register cannot certify VERIFIED without those (`validate_register`).

What the father-facing dashboard still shows is **not** a business conclusion:
- data status: source file names, data date (max sales date), product row count of the stock export, owner-source
  load status, the known-issues list and its data-quality counts (e.g. DQ-4 products without coverage);
- user-entered operational records: CRM interactions and follow-ups (coverage "Eksik kayıt olabilir"), fair campaigns
  with their members, stages, dates and notes, action-state changes;
- the customer directory lookup: **code and recorded name only** (last-order date and KAPALI status withheld);
- the controlled refresh (status only; unverified totals redacted).

### B. Calculated as designed, but not business-verified (withheld)
Deterministic, regression-tested on synthetic data, no defect found in the calculation itself; blocked by definitions,
dates, completeness or reconciliation:
- `stock.reorder`, `stock.overstock` — DQ-4 rule, report date, valuation basis (lists are incomplete by design today).
- `customers.momentum`, `customers.order_size` — revenue basis; missing amounts summed as 0 (becomes C if any amount
  is missing in the real export); KAPALI from DQ-5 lookups.
- `receivables.collections`, `receivables.risk` — bucket "overdue" meaning, report date, revenue basis, rep source.
- `actions.real_decline` — manual inflation value.
- `opportunities.cross_sell`, `opportunities.territories` — thresholds, active window, Hedef file of record.
- `customer360.profile` — revenue basis, code whitespace, rep source, Hedef.
- `fairs.history` — taxonomy, revenue basis.
- `crm.active_customers` — active-customer window.
- `owner.kredi` — DURUM meaning, snapshot date, file of record.
- `scenario.receivables` — depends on unverified receivables and manual assumptions.

### C. Known incorrect or invalid (withheld)
- `finance.margin` — **DQ-1** missing cost counted as zero profit.
- `stock.clearance_buyers` — **DQ-2** KAPALI customers in call lists.
- `customers.winback` — **DQ-6 (new)** overdue looked up by the wrong identity; shows 0 for dealer-invoiced debtors.
- `owner.gider_gelir` — contradictory source totals (annual vs monthly; ÖZET vs expense table).
- `owner.butce` — `#REF!` / `#DIV/0!` cells and an external-workbook dependency.
- Composite outputs that depend on these: `actions.queue`, `sales_queue`, `fairs.segments`, `context.full`,
  `agent.customer_facts`, `agent.product_facts`, `ai.drafts`, `owner.operations`.

### D. Unavailable due to missing data
- `collections.detailed` — `Evrak Detaylı Tahsilat Raporu` not supplied (M6-4 blocked).
- `trodat.sales` — no model → SKU mapping, units unconfirmed.
- Fair conversion, attendance, cost attribution, ROI — no source exists.
- Current Hedef capacity / Risk / Kredi — only stale values exist.
- Real-data reconciliation evidence for every metric — owner-local only, not yet produced.
- Declared report dates for stock and Cari exports — not captured (planned in CP3 upload).

Details per output: `metric_register.md`. Evidence and resolution per problem: `open_verification_items.md`.

## 2. How an output becomes VERIFIED (the only way to show it)
1. Owner decision for every business definition it uses, recorded as `D-xxx` in `DECISIONS.md`; set
   `Definition(approved=True, decision_ref="D-xxx")`.
2. Fix every blocker in code (with tests), and remove the blocker from the register.
3. Real-data reconciliation on the owner's machine, privacy-safe (counts / identities pass, no values committed),
   recorded and referenced in `reconciliation_ref`.
4. Named regression tests in `regression_tests`; the displayed label must say exactly what the number is.
5. Runtime evidence must still pass at every build (report date declared and fresh, no errors, no missing required
   values, no duplicates, joins validated, totals reconciled, mappings approved). Otherwise it is withheld again
   automatically.
Each promotion is one reviewed PR. There is no flag, environment variable or parameter that shows a withheld output.

## 3. AI policy (father-facing release)
- `AI_PROVIDER` defaults to `disabled`; no automatic AI call exists (AI text was always on explicit click only).
- In production every AI route (`enrich`, `analyze`, `draft-message`) is withheld before the provider is reached,
  regardless of configuration. Model text is never evidence and never a source of numbers, classifications, forecasts,
  margins, loan figures, CRM activity, intentions or missing records. The AI infrastructure is kept unchanged.

## 4. Production release gate (in addition to `docs/m8_father_first_launch.md`)
A father-facing release may go live only when **all** hold:
- [x] Fail-closed gate enforced in the backend for the HTML and every API route (this checkpoint).
- [x] Every route classified; unclassified routes withheld; tests enforce it.
- [x] Legacy dashboards without the policy marker are refused by `GET /`.
- [x] AI interpretations off and unreachable in production.
- [x] Directory responses carry only code and recorded name (derived fields withheld).
- [x] Evidence inspection fails closed; unknown evidence never passes.
- [ ] **Generation-bound evidence shared by dashboard, API, owner views and refresh** (M8-ACCURACY-2, mandatory).
- [ ] Each analysis the owner wants on day one is promoted to VERIFIED by §2 (none yet).
- [ ] Owner has reviewed which tabs show only notices and accepts that state for launch, or delays launch.
- [ ] Declared report dates for every uploaded source (CP3/CP4).
- [ ] Authentication (CP1) and the rest of the M8 plan.

## 5. This checkpoint's acceptance
| Criterion | Result |
|---|---|
| States VERIFIED / NOT_VERIFIED / INVALID defined and enforced | **pass** (`services/accuracy.py`) |
| Full metric inventory with evidence | **pass at module level**; field-level detail for Customer 360, queue scoring and fair arithmetic left as an explicit unfinished area (`metric_register.md` §3) |
| Known problems investigated (DQ-1/2/4/5, Kredi, Gider/Gelir, Bütçe, Hedef, cross-sell, fairs, CRM, detailed collections, identity, revenue) | **pass** (real files unavailable; evidence from code, tests and M6 docs) |
| Fail-closed display in backend / shared response layer | **pass** |
| No zero / guess substituted for withheld values (nulls or omission only) | **pass** |
| Algorithms preserved | **pass** (no algorithm changed; harness-run regression suite unchanged) |
| AI policy | **pass** |
| Synthetic regression tests for every Part F case | **pass** |
| Real-data verification | **not possible here** — recorded as unavailable, never claimed |

**Overall:** the checkpoint passes as a *bounded safety checkpoint*; the audit is **not complete** (see the unfinished
areas) and **no metric is verified**.

## 6. Next recommended checkpoint — M8-ACCURACY-2 "First verifiable core metrics"
Bounded, one PR. Work that needs no owner answer can start at once; promotions wait for the answers.

**Mandatory acceptance criteria (from the PR #27 review; no metric may be promoted before all hold):**
- A. One immutable, validated `EvidenceSnapshot` per published source generation, produced from the exact bytes /
  frames used, shared by the dashboard HTML, every API route, the Yönetim owner-source views and the refresh report.
- B. The snapshot is installed atomically with the generation it describes and invalidated on refresh, owner reload,
  failed refresh (rollback keeps the previous generation *and* its snapshot) and restart; evidence of an older
  generation is never carried into a new one (`Gate.bound_to` already drops it; extend to the API).
- C. Tests for verified → unverified and unverified → verified transitions, mixed-source generations (core vs owner),
  rollback, restart, and dashboard/API agreement for the same metric.
- D. Formula-aware evidence for the core exports (errors vs missing caches vs blanks) and recorded join validation.
- E. Only then set `GENERATION_BOUND_API_EVIDENCE = True` (validate_register refuses any VERIFIED entry before it).
- F. Production-gate tests prove every permitted field and route independently of the algorithm harness; the
  harness-run suite is regression coverage only, never evidence that a path is displayable.

Further work:
1. Declared business date per core source (stock, Cari) carried into `SourceEvidence` (stepping stone for CP3).
2. Formula-aware evidence for the core exports via the M6 framework (error cells vs missing caches vs blanks).
3. Sales join validation (order-source ↔ master ↔ invoice account; whitespace codes reported, not repaired).
4. An owner-local, privacy-safe reconciliation script that records counts-only evidence for `reconciliation_ref`.
5. With owner answers to Q3, Q7–Q11, Q15: fix DQ-6, DQ-2, DQ-1 and the zero substitution for amounts per the approved
   rules, then promote the first candidates (likely `receivables.collections`, `stock.reorder` / `stock.overstock`
   with the DQ-4 rule) — each only if §2 is fully met.
M8-CP1 (authentication) does not display numbers and can follow this PR; the release gate above still applies.

## 7. Correction pass after the independent review of PR #27 (2026-10-18)
**Fixed in this PR**
1. `GET /api/customer360/search` no longer returns the derived last-order date or the KAPALI status; code and
   recorded display name stay. The same fields are withheld in CRM follow-up / customer responses and fair member
   rows (new register entries `customer.last_order_date`, `customer.master_status`; backend `redact_directory`).
2. `_excel_errors` returned **0** whenever inspection raised — and it raised for every numeric-only column, so those
   columns were reported as inspected and clean. It now inspects all columns and returns `None` on failure; failures
   are recorded as `inspection_failures` (INVALID). A crashing collector records `collector:<Error>`.
3. `SourceEvidence` had optimistic defaults (available / layout True, counts 0, 45-day age on the source). Every field
   now starts unknown; collectors set only what they inspected and no longer assert joins, period completeness or
   mappings for the core exports.
4. Freshness moved from the source to the metric: operational vs historical (§ metric_register "How a decision is
   made"). Old complete historical periods are not withheld for age; recent sources are not approved for being recent.
5. Evidence is bound to a content-hash generation (`EvidenceSnapshot`, `Gate.bound_to`); evidence from another
   generation never certifies. The dashboard embeds `stockagent-generation`.

**Deferred to M8-ACCURACY-2 (mandatory, §6 A–F):** sharing the generation-bound snapshot with the API, owner views and
refresh, with transition / rollback / restart tests. Until then the API has no evidence, every source-bound metric
stays withheld everywhere, and the production register cannot certify any metric VERIFIED.

**Residual, documented:** KAPALI safeguards still act server-side and their refusal messages name the reason (they
restrict an action; they do not present an analysis). Counts of records in the directory, CRM and campaigns, the
data date (max sales date), file names and data-quality counts (e.g. DQ-4) remain as system information.

## 8. M8-ACCURACY-2 — shared generation evidence (2026-10-19)
### 8.1 What was built
- **Source-generation manifest** (`services/generation.py`, schema `halsa-generation/1`). For every core source
  (stock, sales, aging, and the master `liste` sheet of the sales workbook) and every owner source (Kredi,
  Gider/Gelir, Bütçe): file basename, **SHA-256 of the original workbook bytes**, size, selection origin, declared
  dates, observed dates, validation counts and `SourceEvidence`. `core_id` = digest of the policy version, the selection
  origin, every core record's **full content** (incl. validation results and evidence) and the join report;
  `generation_id` = digest of `core_id` and the owner records' full content (§10). No values, names or paths.
- **Frozen reads:** each source is read once (`FrozenSource`); the hash, the formula-aware inspection, the pandas
  frames, the dashboard and the context all use those bytes. Owner Gider/Gelir and Bütçe are re-hashed after parsing
  (`SourceChanged` if the file changed mid-read).
- **One manifest everywhere:** the dashboard HTML (`stockagent-generation` = `core_id`), the API gate
  (`app.accuracy_gate` binds to the fingerprints actually served), the Yönetim views (owner records replaced on
  reload), the refresh (installs context + manifest together, restores dashboard + manifest on failure) and restart
  (a saved manifest is only a candidate; the evaluation is re-derived from the original bytes, §10). Each request pins
  the views it serves and its gate (§10). Customer 360 checks the generation before and after its build.
- **Formula-aware core validation** (`services/source_validation.py`): a streaming sheet-XML scan distinguishes literal
  values, zeros, blanks, cached formula results, formulas without cached results, Excel errors, external references
  and invalid dates per column; ambiguous and missing headers; unsupported layouts. It never raises.
- **Join validation:** counts only; nothing is merged or repaired (D-003 preserved; DQ-5, DQ-6 open).
- **Dates:** declared in `data/sources.json` (§9), checked against the source (future dates, a sales period that
  contradicts the observed transactions, a report date before the latest transaction, aging buckets newer than the
  snapshot). The latest transaction date, the declared report date and the file time are separate fields; the file
  time is information only.
- **Kredi revised layout + schedule views + owner-local reconciliation command** (`docs/m6_kredi.md`).
- **Status route:** `GET /api/accuracy/status` (system information; fingerprints, dates, states, codes; no values).

### 8.2 Acceptance criteria §6 A–F
| | Result |
|---|---|
| A. One validated snapshot per published generation, from the exact bytes, shared by dashboard / API / owner / refresh | **pass** (synthetic) |
| B. Atomic install; invalidated on refresh, owner reload, failure (rollback keeps the previous one) and restart | **pass** (synthetic) |
| C. Transition, mixed-generation, rollback, restart and dashboard/API agreement tests | **pass** (`tests/test_m8_accuracy2.py`) |
| D. Formula-aware core evidence and recorded join validation | **pass** (synthetic) |
| E. `GENERATION_BOUND_API_EVIDENCE = True` | **not done — deliberately.** Requires §8.3 on the real files |
| F. Production-gate tests independent of the harness | **pass** for the new paths |

### 8.3 Owner real-data acceptance (owner-local; required before E)
1. Write `data/sources.json` (§9) naming the stock, sales and aging files and their declared business dates.
2. `python -m scripts.reconcile_sources --core data --evidence-out` → `GENEL SONUÇ` must not be FAIL; review each OPEN.
3. `python -m scripts.reconcile_sources --kredi "data/kredi takip(1).xlsx" --today <YYYY-MM-DD> --evidence-out` →
   not FAIL; each OPEN answered by the owner / accountant (§8.4). `--show-values` only on the local console.
   Evidence files go to the git-ignored `data/reconciliation/` (an explicit path inside the repository is refused
   unless it is under `data/`). Exit codes: 0 PASS, 1 FAIL, 2 unreadable / usage, 3 OPEN.
4. Run the refresh in the app; check `GET /api/accuracy/status`: the `generation_id` is set, every source is `bound`.
5. Restart the app; after the warm-up, `restore_pending` is false, `restore_reason` is null and the same `core_id` is
   published (re-derived from the bytes). `evidence_mismatch` or `source_changed` means: refresh again, nothing binds.
6. Only then, in a separate reviewed PR: set the flag, record the evidence (counts only) as `reconciliation_ref`, and
   promote individual metrics per §2.

### 8.4 Still not verified (nothing was promoted)
Kredi: DURUM / Kapalı meaning; the 11 positive-remaining installments outside the saved filter; the negative
residual; the file of record; the snapshot date; payment-record reconciliation. Core: DQ-1, DQ-2, DQ-4, DQ-5, DQ-6;
no approved definitions besides D-003; the 45-day age limit is provisional. Hedef is not in the manifest. Owner
sources have no approval mechanism. Field-level audits for Customer 360, queue scoring and fair arithmetic remain.

## 9. `data/sources.json` (private; lives in `data/`, never committed)
```json
{
  "schema": "halsa-source-selection/1",
  "core": {
    "stock": {"file": "<stock export>.xlsx", "snapshot_date": "YYYY-MM-DD", "period_complete": true,
              "approval": {"sha256": "<64 hex of this exact file>", "decision_ref": "D-xxx",
                           "totals_consistent": true, "mapping_approved": true}},
    "sales": {"file": "<sales export>.xlsx", "report_date": "YYYY-MM-DD", "period_start": "YYYY-MM-DD",
              "period_end": "YYYY-MM-DD", "period_complete": true},
    "aging": {"file": "<aging export>.xlsx", "snapshot_date": "YYYY-MM-DD"}
  }
}
```
- `file` is a basename inside the data folder; paths, traversal and unknown keys are rejected (the refresh fails).
- `stock` and `sales` are required. Without the file, the newest files are used and recorded as `mtime_fallback`;
  nothing can verify then.
- Dates are business dates declared by the owner. They are never read from file timestamps.
- `approval` applies **only** to the file with that SHA-256; any other revision gets `fingerprint_mismatch` and the
  approval is ignored. An approval sets only `totals_consistent` / `mapping_approved`; it cannot promote a metric.

## 10. Corrections after the independent review of PR #28 (2026-10-20)
### 10.1 Request-level generation consistency (review issue 1)
**Defect.** The accuracy middleware decided before the endpoint read its data; the endpoint then read the context, the
owner views and the Customer 360 snapshot again. A refresh or owner reload in between could authorize with generation
A's evidence and return generation B's numbers (reproduced by
`test_negative_control_without_pinning_the_race_is_real`). The refresh also replaced the dashboard, the manifest and the
context in separate steps.

**Fix.**
- `services/request_pin.py`: one `RequestView` per HTTP request (set by the middleware). The context
  (`ContextRepository.current/context`), the owner entries (`OwnerRepository.snapshot`) and the Customer 360 snapshot are
  each read **once** per request and then frozen. The gate is built **once** per request (`app.accuracy_gate`), from
  the published manifest and the fingerprints of the views that request pinned. The endpoint returns those same
  objects, so the evidence that authorized a response always belongs to the data in it. A refresh during the request
  serves later requests; the running request keeps its consistent previous generation. A view that was absent when the
  gate was built can still be loaded later in the request, but never gains evidence there (fails closed).
- `PUBLISH_LOCK`: the refresh publishes manifest + dashboard + context (and rolls all three back) as one step; an owner
  reload publishes its view and its manifest record as one step; a request captures its gate inputs and `GET /` reads
  the dashboard under the same lock.
- Indices of a pinned older context are never cached for newer requests; Customer 360 maintenance uses the live
  context, so a request pinned to A cannot discard B's snapshot.
- Responses whose gate was evaluated carry `X-Halsa-Generation` (the authorizing `generation_id`; no values).
- Another process (a restart) can still observe the two files mid-swap; restore refuses that pair
  (`dashboard_generation_mismatch`, tested with a simulated crash between the replacements).

**Guarantee (in-process):** a VERIFIED response is always built from the exact context / owner view whose fingerprints
bound its evidence. Not covered: consistency **across** responses (a dashboard page and a later API call may belong to
different generations, each consistent on its own; the header lets a client detect it); deployments with several
worker processes (each process has its own store and publish lock — the single-process uvicorn launch is assumed).

### 10.2 Persisted evidence integrity (review issue 2)
**Defect.** `core_id` / `generation_id` covered only the source identity (key, SHA-256, selection, declarations).
`validation`, `evidence`, `observed` and `joins` could be edited in `*.generation.json` and still pass `from_dict` and
the restart checks.

**Fix.**
- The ids now digest every record's full content (validation results and evidence included) and the join report.
  `from_dict` rejects a file whose content does not reproduce its ids. This is an **unkeyed checksum**: it catches
  accidental corruption, **not** deliberate edits (anyone who can edit the file can recompute it).
- Protection against deliberate edits is re-derivation: at restart a saved manifest is only a **pending candidate**
  (intact, the served dashboard was built from it, every core file has its recorded fingerprint). It provides no
  evidence. The first context build re-derives every validation result and all evidence from the original workbook
  bytes and the current `data/sources.json` (`generation.rederive_core`), and installs the **re-derived** manifest only
  if it reproduces the candidate's `core_id` exactly; otherwise `evidence_mismatch`, no evidence. Nothing from the
  saved evaluation is ever used as evidence.
- Not protected: the inputs themselves (workbooks, `data/sources.json`) and the served dashboard HTML — anyone with
  write access to the server folder can change what is computed or shown. That is an access-control question (M8-CP1 /
  deployment), not a manifest-integrity one.
- Cost: the restart re-runs the formula-aware inspection (about 20 s at real-data scale, in the warm-up thread).

### 10.3 Other corrections
- `*.generation.json`, its temp files and `kanit_*.json` are now git-ignored (the manifest is written beside
  `dashboard.html` in the repository root and was not ignored).
- `scripts/reconcile_sources.py`: explicit `GENEL SONUÇ: PASS / OPEN / FAIL`, `overall` in the evidence file, exit
  codes 0 / 3 / 1 (2 = unreadable / usage); evidence defaults to `data/reconciliation/` and is refused elsewhere inside
  the repository.

### 10.4 Still blocking ACCURACY-2 acceptance (unchanged by this pass)
The owner's real-data run (§8.3) has not happened; `GENERATION_BOUND_API_EVIDENCE` stays False; no metric is
VERIFIED; Kredi questions (§8.4) open; Hedef not generation-bound; no owner-source approvals; the 45-day limit is
provisional; DQ-1 … DQ-6 unchanged.
