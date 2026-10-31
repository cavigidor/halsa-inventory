# M8-R5A — retire obsolete Stock thresholds (2026-10-30)

Isolated clone: `~/Documents/Halsa-Codex-R5A`; new branch `codex/m8-r5a-retire-stock-thresholds`.
Starting GitHub `main`: `ffa2c81fca12dcd8043d6de226b90984dcdaa300` (fresh fetch, clean tree).
PR #37 and the Claude-held checkout were not modified. No private sources, ignored files or credentials copied.
This is Stock only. Cari aging, supplier/customer balances and agreement-based overdue work are deferred.

## Business authority and boundary

D-070 records the owner's relayed father answers: old min/max settings survived only for export compatibility;
they are not current purchasing targets. There is no separate discontinued indicator. Signed ERP inventory can
include component removals/sales; it is not a measurement of physically available stock. A stale duplicate row in
one inspected workbook does not authorize universal row deletion, summing or sign normalization.

No new purchasing model is justified by these facts. R5A withdraws unsupported recommendations rather than
substituting forecasts, thresholds or descriptive business metrics with unreviewed definitions.

## Dependency trace and old/new behavior

| Path | Previous behavior | R5A behavior |
|---|---|---|
| `dashboard_builder.m_stock` | Compared cached `STOK Kontrol %` with min/max; classified/ranked reorder/overstock; interpreted `DURUM`; rounded quantities into recommendation rows | No recommendations or discontinued/critical classification; counts original code-bearing rows only; never coerces or modifies provided Stock frame |
| `m_overstock_move` | Collapsed overstock rows by product code and attached historical buyers | Returns no clearance recommendation, including for supplied legacy candidates; no duplicate aggregation |
| Dashboard tabs/statistics/build report | Low/max counts, inventory value of threshold-selected products, urgency/clearance instructions | Existing tabs remain behind the accuracy gate; fixed retirement reason replaces active instructions; unsupported totals are unavailable (`None` internally/API, dash in unverified algorithm rendering), not zero |
| `services.context` / refresh | Threshold-derived candidates, buyer scoring and working-capital totals | Empty stock recommendation collections; low-stock count and overstock value `null`; refresh reconciles unavailable values explicitly; customer/financial calculations unchanged |
| Action producer / AI evidence | Overstock action reason said stock exceeded maximum; selected stock data reached AI | Producer refuses overstock candidates; stale overstock actions are omitted from evidence while other action references retain their numbers; stock-only enrichment makes no call |
| Product index / product AI | Product cards existed only for threshold-selected rows; product analysis could propose clearance | No catalog is invented from retired context rows; no threshold-derived product card. Product AI refuses before a provider call. Existing production routes remain gated; no replacement route |
| Product evidence helper | `low_stock`/`overstock`, coverage, minimum and excess-stock value treated as facts | Status unknown; obsolete fields omitted. A supplied signed ERP balance is explicitly labeled as not available physical stock; historical buyers are not labeled as current candidates |
| `accuracy.REGISTER` | Existing ceilings, source problems, definitions and dependencies withheld results | Same certified ceilings/approvals; additional `STOCK-THRESHOLDS-RETIRED` INVALID blocker on reorder/overstock; dependent clearance/product/combined views stay withheld |
| Metric requirements | Combined queue/context required obsolete stock outputs | Queue retains its actual customer-metric dependencies; context requires product count instead of retired recommendations. Product card has an explicit retirement blocker. Legacy field/join source checks stay conservative; no accuracy check removed |
| Source scanner, template, import schema, reconciliation | Reads and checks raw fields/formulas, identities, duplicate codes and footer results | Unchanged; obsolete columns remain in the accepted export schema. Strict findings are not erased |
| Veri Kontrol / owner known issues | Source coverage failures described as a pending list-policy decision | Same issue identity/detection/counts/history; explanatory stock text now says old fields are not decision inputs |
| `weekly_agent.py` | Legacy separate formulas/renderers, but CLI already unconditionally disabled by D-049 | Still disabled; no email or weekly process executed. Dormant helpers are not a production path and are not re-enabled |

`STOK Kontrol %` is a cached source value, not a formula recomputed by this app. Its workbook formula meaning is
not established by retiring min/max; it is therefore excluded from recommendations/AI rather than reinterpreted.
It remains inspectable as source evidence, including `#DIV/0!`, blank and missing-cache findings.

The source fields `TOPLAM STOK MİKTAR `, `minseviye`, `maxseviye`, `STOK Kontrol %`, `DURUM` and inventory values
remain untouched. Product-count diagnostics still count duplicate code-bearing rows; they are not unique physical
inventory. No raw Stock catalog or balance metric is newly displayed or certified.

## Generation and display consequences

`POLICY_VERSION` changes to `m8-r5a-stock-retirement` because served HTML must reflect the current policy. Existing
HTML/persisted manifests from the old policy cannot be trusted as current evidence. The existing fail-closed
restart path requires a controlled refresh/republication from the same source bytes; no migration or source rewrite.

Production calculation paths change for Stock: the obsolete Python comparisons, ranking and clearance aggregation
are removed. Original Excel formulas are not edited. Product AI is withdrawn. Existing stock display paths now
carry the explicit blocker; no new output is exposed, no route reclassified and no authorization bypass added.
`GENERATION_BOUND_API_EVIDENCE=False`; the 33 registered certified states are unchanged; zero metrics VERIFIED.
The real gate may become stricter (NOT_VERIFIED → INVALID) due to the retirement blocker; that is not certification.

## Synthetic verification

`tests/test_m8_r5a_stock_retirement.py` covers blank/zero/positive/negative balances, contradictory positive/negative/
missing old settings, no-sales products, duplicates with differing signed balances, source bytes and cached formulas,
unchanged unrelated computations, stale candidate/AI refusal, unavailable totals and real production-gate API refusal.
Existing algorithm/API tests now assert withdrawal rather than require the obsolete recommendations. Grounding
type/reference tests still exercise the real validator directly; customer AI and authentication coverage remain.
Generation/security fixtures use the existing product-count metric as independently rendered evidence; tamper,
restart, rollback, source changes, negative controls and concurrent request pinning are still exercised. Test-only
product producers/DOM records stress queue cuts and generic product-card controls without restoring stock actions.

An inherited R3 fixture flake reproduced during focused validation: rewriting logically identical Excel files does
not preserve their ZIP/core timestamps or SHA-256. Stock-only replacement tests now copy the unchanged synthetic
Sales/Cari bytes and assert that only Stock's hash differs. Exact generation-rejection checks are retained. This is
an independent test-fixture correction on main; no Option B code or other changes from unmerged PR #37 are included.

Actual focused/full counts are recorded in `STATUS.md`; final-head CI results and commit are in the draft PR and
checkpoint handoff. Browser and JS tests are required, AI disabled. Seven private-data tests may skip because no
private workbooks are present; the existing strict expected failures are not stock-retirement acceptance.

## Acceptance and remaining work

| Criterion | Assessment |
|---|---|
| Obsolete settings cannot produce active reorder/overstock/clearance or stock AI recommendations | PASS — synthetic and full local suite; final CI in PR |
| Blank, zero and signed quantities remain source values; no physical availability/discontinued inference | PASS — source/packet tests |
| No duplicate row deletion, combining or normalization | PASS — byte/frame/duplicate diagnostics tests |
| Original formulas/schema, strict source checks and Veri Kontrol retained | PASS — formula, gate and VKM suites |
| Unrelated customer/financial computations preserved | PASS — comparisons and full suite |
| No metric promotion or evidence-flag change | PASS — all 33 ceilings match main; register/API checks |
| Planner independent acceptance and private owner-local acceptance | PENDING; this is a synthetic isolated checkpoint |

Dedicated R5A suite: **25 passed**. Full local suite with browser/JS required and AI disabled:
**1877 passed, 7 private-data skips, 2 existing xfailed (448.90s)**. Private production acceptance remains separate
R4/launch work; no private workbook is needed to reproduce this R5A engineering checkpoint.

Do not merge/deploy or start R5B. Claude should compare the current draft-PR head before resuming. Keep the
canonical VM-held directories and PR #37 untouched. A future stock model, source-specific duplicate resolution,
physical availability and the unresolved stock snapshot date require separate evidence and review.

## Modified files

- Runtime/policy: `dashboard_builder.py`; `services/stock_policy.py`, `accuracy.py`, `action_sources.py`, `agent.py`,
  `context.py`, `entity.py`, `evidence.py`, `metric_diagnostics.py`, `owner_view.py`, `refresh.py`, `vkm.py`.
- Documentation: `DECISIONS.md`, `PROJECT.md`, `STATUS.md`, `TASKS.md`, `docs/accuracy/metric_register.md`,
  `docs/accuracy/m8_r5a_stock_retirement.md`, `docs/m8_cp1_access.md`.
- Tests: `tests/js/ai_panel.test.mjs`; `tests/test_api.py`, `test_domain_rules.py`, `test_failure_isolation.py`,
  `test_grounding.py`, `test_local_real_data.py`, `test_m5_backend.py`, `test_m5_refresh.py`, `test_m7_sales_queue.py`,
  `test_m8_accuracy.py`, `test_m8_accuracy2.py`, `test_m8_accuracy2_review.py`, `test_m8_accuracy2_review2.py`,
  `test_m8_accuracy2_review3.py`, `test_m8_accuracy2b.py`, `test_m8_r3_metric_diagnostics.py`,
  `test_m8_r5a_stock_retirement.py`.
