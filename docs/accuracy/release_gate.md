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
- [ ] Authentication (CP1) and the rest of the M8 plan. *(2026-10-22, D-060: CP1 implemented locally as viewing
  without login + one owner administration login — `docs/m8_cp1_access.md`; still needs PR review and merge.)*
- [ ] Owner's recorded sign-off on the public-by-link disclosure inventory (`docs/m8_cp1_access.md` §6) — every metric
  promoted to VERIFIED becomes visible to anyone with the link, not only to the owner (D-060).

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
   unless it is under `data/`; a path that is, or links to, any input or any existing file is refused). Exit codes:
   0 PASS, 1 FAIL, 2 unreadable / usage / refused evidence path, 3 OPEN, 4 evidence file could not be written.
4. Run the refresh in the app; check `GET /api/accuracy/status`: the `generation_id` is set, every source is `bound`.
5. Restart the app; after the warm-up, `restore_pending` is false, `restore_reason` is null and the same `core_id` is
   published (re-derived from the bytes). `evidence_mismatch` or `source_changed` means: refresh again, nothing binds.
   `GET /` must then return 200 with an `X-Halsa-Generation` header equal to the status `generation_id` (the page is
   re-built by the server after the restart, §12); a 503 with `X-Halsa-Dashboard` names why it is withheld.
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

## 11. Second review pass on PR #28 (2026-10-20, two reproduced findings)
### 11.1 Reconciliation output could overwrite an input
**Reproduced on `bc01577`:** `--evidence-out` equal to `--kredi` replaced the synthetic workbook with JSON (exit 3; the
file then started with `{`).
**Fix (`scripts/reconcile_sources.py`):** the destination is refused (exit 2) when it is, or resolves / links to, the
Kredi workbook, `sources.json`, a selected core workbook or any workbook / JSON file in the core folder (symlinks,
`..`, symlinked folders and hard links via device + inode), or when anything already exists at the path (including a
dangling symlink). The file is created with `O_CREAT | O_EXCL | O_NOFOLLOW` (mode 0600), so no existing file can be
truncated even if one appears after the check; a partly written file of ours is removed; a write failure is reported
as `KANIT DOSYASI YAZILAMADI` with exit 4. The private-destination rule (inside the repository only under `data/`)
is unchanged. Inputs are only ever opened read-only.

### 11.2 `GET /` served a dashboard whose generation had been rejected
**Reproduced on `bc01577`:** publish a synthetic VERIFIED stock metric, change the stock workbook, restart →
`restore_reason = source_changed`, the API withholds the metric, but `GET /` returned 200 with the 2 embedded rows.
**Fix:**
- The build embeds `stockagent-displayed`: every metric whose output the HTML contains. The refresh records the
  SHA-256 of the exact published HTML in the manifest (`dashboard_sha256`, covered by `generation_id`); restart refuses
  a dashboard whose bytes differ (`dashboard_bytes_mismatch`); a re-derived generation keeps the candidate's digest.
- `GET /` reads the bytes and the accepted generation together under `PUBLISH_LOCK`. Evidence binds only if the HTML's
  generation is the accepted (re-validated, not pending) one and its bytes are exactly the published bytes. The
  CURRENT gate (today's freshness, current register) must then authorize every displayed metric. Otherwise 503 with
  a neutral notice and `X-Halsa-Dashboard` = `evidence_unavailable` / `not_authorized` / `displayed_marker_missing`.
  No embedded data is returned.
- ~~A dashboard that displays nothing source-bound is served without evidence~~ — **superseded by §12**: no on-disk
  page is served without an exact match to the accepted generation, whatever its markers claim.
- HTTP tests (`tests/test_m8_accuracy2_review2.py`) cover:
  - source changed + restart (the reproduction);
  - pending revalidation, then a successful restore with identical bytes;
  - a corrupted manifest; a resealed manifest; `evidence_mismatch` after resealing the manifest and the dashboard;
  - edited HTML bytes, live and after restart;
  - failed publication / rollback; a crash between the file swaps;
  - authorization re-evaluated at serve time (stale stock);
  - `GET /` waits for an in-progress publication.

### 11.3 Readiness — three different things
| Level | State |
|---|---|
| **Technical foundation** (manifest, request pinning, publish lock, re-derived restart, dashboard authorization, fail-closed reconciliation) | Ready for review / merge as a foundation: both review rounds' findings are fixed and covered by synthetic production-gate tests. Every production metric is still withheld. |
| **Owner-local real-data acceptance** (§8.3) | **Not started.** The real ERP exports and `kredi takip(1).xlsx` have not been run through this code. Required before `GENERATION_BOUND_API_EVIDENCE` may become True. |
| **Individual metric promotion** (§2) | **None.** Each metric needs owner decisions (D-xxx), real-data reconciliation evidence and regression tests, one reviewed PR each, after the flag. Kredi additionally needs the DURUM / Kapalı meaning, the 11 excluded positive-remaining rows, the negative residual, the file of record, the snapshot date and payment reconciliation. |

## 12. Third review pass on PR #28 (2026-10-20): the page's own claims are never evidence
**Root cause.** `_dashboard_authorized()` computed whether the page was bound to the accepted generation, but refused
only when a metric listed in `stockagent-displayed` failed the gate. A page whose marker was EMPTY passed with no
accepted generation, with other bytes, or after editing — the marker is a claim of the HTML file, not evidence. An
edited page could inject values while declaring that it displays nothing. `_gated_dashboard()` also read the file
outside the publication lock.

**Correction (`app.py`, `services/entity.py`, `services/generation.py`).**
- The on-disk HTML is served only if ALL hold, evaluated on the bytes captured under `PUBLISH_LOCK` together with
  the accepted generation:
  1. an accepted, re-validated generation exists;
  2. the page's policy marker is current;
  3. its generation marker equals the accepted `core_id`;
  4. the SHA-256 of the bytes equals the digest **recorded by this process when it published them**;
  5. the displayed marker is present and the current gate authorizes every metric in it.
  An empty list is accepted only after steps 1–4. Everything else gets a server-generated neutral Turkish page
  (503, `X-Halsa-Dashboard` = reason: `no_accepted_generation`, `policy_marker_missing`, `generation_mismatch`,
  `bytes_mismatch`, `displayed_marker_missing`, `not_authorized`); the file is never returned. The pre-lock
  `_gated_dashboard()` read is gone.
- **Publication provenance after a restart.** The dashboard digest in the saved manifest cannot be re-derived from the
  workbooks, so an editor of both files could otherwise re-record it. After a restart the accepted, re-derived
  generation therefore starts with NO dashboard digest. The server re-builds the dashboard from the re-derived evidence
  and the same frozen bytes, and publishes page + manifest + digest as one locked step
  (`app._republish_dashboard`). Only those bytes are served. If the re-build fails, nothing is served. The pre-restart
  file is never served. `GET /` waits for a pending candidate's re-validation instead of serving anything meanwhile.
- A plain `python dashboard_builder.py data` build has no publication record and is not served; publish with
  `python -m services.refresh` (or the dashboard's refresh button). README and the launch checklist are updated.

**Tests (`tests/test_m8_accuracy2_review3.py`, 19, production gate, HTTP):**
1. a valid accepted empty dashboard (the real register) is served, live and after a restart;
2. an edited empty dashboard with injected data is refused; editing page and manifest together still serves only the
   server-rebuilt page;
3. an empty dashboard without an accepted generation (source changed, no store, CLI build, a candidate that fails
   re-validation) is not served;
4. an authorized synthetic metric is served, live and after a restart;
5. tampered markers (list altered or emptied, a duplicate marker, generation marker, policy marker, renamed marker)
   are refused;
6. refresh publishes new bytes, rollback keeps the previous, a failed re-publication after restart withholds, the
   re-published pair restores again;
7. no production metric is VERIFIED and the flag is off;
8. no AI provider is called.

**Readiness:** technical foundation — ready for final technical review; owner-local real-data acceptance — not started;
metric promotion — none (§11.3).

## 13. M8-ACCURACY-2B — precise validation and safe diagnostics (2026-10-21)
Follows the first owner-local real-data acceptance (2A, read-only on the owner's Mac; no values left the Mac).

**Bugs fixed (each found on the real exports, reproduced synthetically):**
1. *Report-total rows counted as data.* The stock export ends with report-control rows (a `SUM` row, a label row, a
   `SUBTOTAL(9)` row). They were counted as rows with a missing product code (FAIL), as duplicates of each other
   (`astype(str)` turned blank codes into `"nan"`), as products in the header / refresh count, and in the DQ-4 count.
2. *Shared formulas.* Excel stores runs of identical formulas once; the streaming reader saw empty formula text in the
   dependent cells. They are now expanded with Excel's relative-reference rule (a dependent without its master never
   matches a footer rule).
3. *Office lock files.* While a workbook is open in Excel, `~$<name>.xlsx` (165 bytes) is the newest matching file;
   the fallback selection picked it for stock. `~$…`, `._…` and `.~lock…` are never sources; `sources.json` refuses
   them.
4. *Source-wide contamination.* A cross-source join problem (e.g. products sold but absent from stock) made every
   metric on the source INVALID — also lists that never use that join. Metrics can now declare their dependencies
   (metric register "How a decision is made" 5).
5. *Restart re-publication 503.* GET / no longer answers a needless 503 while the warm-up thread re-publishes the
   dashboard of a re-validated generation: it waits, bounded (`DASHBOARD_REPUBLISH_WAIT_SECONDS`, default 60 s, max
   300 s); a timeout is 503 `publication_pending`; failures stay 503.

**Footer recognition (`source_validation.analyze_stock_rows`).** Only rows after the last product row can be footers;
a blank-code row between products is always a missing identity. A trailing row is recognized when identity and name
are blank and either (a) AGGREGATE: every aggregate cell is `SUM(X a:X b)` or `SUBTOTAL(9|109, X a:X b)` over its own
column X with exactly the product range, other formulas are row-local with a blank cached result, no numeric literal;
or (b) LABEL: adjacent to a recognized aggregate row, one identical text in the aggregated columns, otherwise only the
aggregate row's own text label. Everything else is AMBIGUOUS and stays a row with missing identity (fail closed).
Every aggregate cell is reconciled against the rows it covers: matched / mismatched (contradictory → the source's
totals are INVALID) / error_propagated (an error inside the range) / missing_cache / unsupported (SUBTOTAL on a sheet
with hidden rows). Recognized footers are excluded from the field scan and reported separately; nothing is dropped,
repaired or deduplicated; the workbook is never written.

**Counting definitions (Turkish labels in the reconciliation output):** Okunan satır (parsed rows) · Tanınan rapor
toplam satırı (recognized footers) · Ürün kodlu satır (product-code rows) · Farklı ürün kodu (distinct codes) ·
Yinelenen ürün kodu tekrarı (duplicate occurrences) · Kimliği eksik / belirsiz satır (missing or ambiguous identity).
The product count = product-code rows; it is the new withheld metric `stock.product_counts`.

**Real-data result of this branch (private rerun, counts only):** 3 footers recognized (2 aggregate, 1 label),
0 ambiguous; 94 aggregate cells: 92 matched, 2 error-propagated (an unused cost column with error cells), 0
contradictory; 3,311 product-code rows, 3,310 distinct codes, 1 duplicate occurrence, 0 missing identity; coverage
1,178 undefined (472 with stock), 2,133 numeric, 0 blank. Core technical failures 8 → 7 (overall still FAIL on genuine
items: coverage `#DIV/0!`, one duplicated code, sales duplicate rows, joins). With scoping, `stock.reorder` /
`stock.overstock` remain INVALID for their own fields (coverage errors, duplicated identity header, duplicate code);
`receivables.collections` is no longer INVALID through the unrelated aging↔sales join (NOT_VERIFIED: date,
selection, totals, mapping, blank buckets). No metric is VERIFIED.

**Not done here (documented):** dependency declarations for the remaining metrics (strict until audited); supplier
(`320`) accounts in collections (owner question, no rule); cross-response consistency; native macOS browser tests.

## 14. PR #29 correction pass — partial footers, owner-confirmed stock template (2026-10-21; D-056–D-058)
Follows the independent review of PR #29.

**Footer reconciliation (fixed).** `stock_footer_totals` reported PASS while 2 of 94 footer cells were
error-propagated. It is now **PASS only when every checked footer cell matched** (matched == checked, no ambiguous
row); a contradiction is **FAIL**; error-propagated, missing-cache or unsupported cells, ambiguous rows or nothing
checked are **OPEN**, with the matched and unreconciled counts and the affected column letters printed. A partial
reconciliation caps `totals_consistent` at None — no declaration can make it True; a contradiction makes it False.
Without any footer row the declaration decides, as before. The evidence is also kept **per field**
(`footer_totals`), so a metric is withheld by a partial / contradictory footer only when it reads that field.

**Owner-confirmed stock template (D-056).** Column A = product code, column J = country of origin (both labelled
`Stok_Kodu`). `STOCK_TEMPLATES["dinamikcrm_stok_v1"]` pins header row 4, 89 columns, the SHA-256 of the header labels
in order (measured owner-locally; no label or value committed), A / J and the physical column of every pipeline
field. One `Stok_Kodu` header → unchanged; a duplicated one → accepted only for the exact approved header row,
otherwise refused (`identity_header_ambiguous`, layout unsupported → every stock metric INVALID). Never "the first
column". Residual risk: swapped column contents under unchanged labels are not detectable from the header (no
content heuristic is used). The approval covers column identity only; `STOCK-CODE-AUTHORITY` is replaced by this
evidence, `STOCK-REPORT-SCOPE` (the duplicated code's row grain) stays.

**DinamikCRM conventions (D-057)** recorded as owner statements, not evidence: staff generate the recurring exports,
the owner uploads them manually, layouts stay fixed, the intended period runs from 1 January of the earliest selected
year to the selected reporting date. Dates are still declared per upload; completeness is never inferred; no SKU is
classified discontinued without source evidence.

**Real-data private rerun (counts and statuses only):** footers 3 recognized (2 aggregate, 1 label), 0 ambiguous;
94 checked, 92 matched, 2 error-propagated (one column, BA), 0 contradictory → **OPEN**; `totals_consistent` None.
Template: `approved_template` (`dinamikcrm_stok_v1`), identity A, country of origin J; the `Stok_Kodu`
duplicated-header reason no longer applies. Counts unchanged (3,314 parsed, 3,311 product-code rows, 3,310 distinct,
1 duplicate occurrence, 0 missing identity). Core technical failures 7, OPEN 29. `stock.reorder` INVALID (coverage
errors, duplicate code), `stock.overstock` INVALID, `stock.product_counts` INVALID (duplicate code),
`receivables.collections` NOT_VERIFIED. **No metric VERIFIED; `GENERATION_BOUND_API_EVIDENCE` False.**

**Veri Kontrol Merkezi** — designed in `docs/m8_veri_kontrol_merkezi.md`, separate checkpoint M8-VKM-1; review never
certifies a metric, never changes source data, never deletes a suspected duplicate.

## 15. Second review of PR #29 — column footers vs source-wide totals (2026-10-21; D-059)
**Defect.** A scoped metric still inherited the source-level `totals_consistent` verdict, which a partial or
contradictory footer in any column caps — so the real export's error-propagated cost column (BA) would also withhold a
metric that reads only quantities whose footers reconcile.

**Fix.** The evidence records the verdict's origin (`declared_totals_consistent`, `footer_totals`, `footer_scope`). A
metric with an audited scope is judged on the declaration (source-wide) plus each used field's own footer result
(PASS / `not_aggregated` pass, OPEN or missing → NOT_VERIFIED, FAIL → INVALID) — but only when the per-column
evidence can stand on its own: footer rows recognized, at least one cell checked and matched, no ambiguous trailing
row, a self-consistent record, and at least one used field. Otherwise the strict source-level verdict applies. The
source's overall reconciliation stays OPEN / FAIL; unscoped metrics, the other integrity checks and used joins are
unchanged. Regression tests: `tests/test_m8_accuracy2b_review2.py` (pipeline-built evidence judged by the real gate).

**Effect on the real export:** none on any production decision — every stock metric is still withheld by its own
problems (coverage errors, the duplicated code, dates, approvals). No metric VERIFIED; `GENERATION_BOUND_API_EVIDENCE`
False.
