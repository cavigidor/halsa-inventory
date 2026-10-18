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
- the customer directory lookup (code, name, master KAPALI flag, last order date as recorded);
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
1. Declared business date per core source (stock, Cari) carried into `SourceEvidence` (stepping stone for CP3).
2. Formula-aware evidence for the core exports via the M6 framework (error cells vs missing caches vs blanks).
3. Sales join validation (order-source ↔ master ↔ invoice account; whitespace codes reported, not repaired).
4. An owner-local, privacy-safe reconciliation script that records counts-only evidence for `reconciliation_ref`.
5. With owner answers to Q3, Q7–Q11, Q15: fix DQ-6, DQ-2, DQ-1 and the zero substitution for amounts per the approved
   rules, then promote the first candidates (likely `receivables.collections`, `stock.reorder` / `stock.overstock`
   with the DQ-4 rule) — each only if §2 is fully met.
M8-CP1 (authentication) does not display numbers and can follow this PR; the release gate above still applies.
