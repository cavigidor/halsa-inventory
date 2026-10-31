# M8-R4 isolated engineering checkpoint — 2026-10-30

PR: https://github.com/cavigidor/halsa-inventory/pull/37 (draft; do not merge or deploy).
Starting head: `410ee070da3178985ee20148ec090d2abf7bd1a8`, identical locally and on GitHub.
Independent clone: `~/Documents/Halsa-Codex-R4`, branch `m8-r4-option-b-scoped-release`.
The destination did not exist before cloning. It has its own `.git`, not a linked worktree.
The Claude-held canonical checkout and related folders were not accessed or changed. No ignored/private files
were copied. All workbooks used here were generated from synthetic fixtures in temporary test directories.

## CI diagnosis and correction

- PR run [41000795041](https://github.com/cavigidor/halsa-inventory/actions/runs/41000795041), job 123884463841:
  **1 failed, 1854 passed, 7 skipped, 2 xfailed**. The Stock synthetic-certification test still required
  `kredi_reconciliation_not_fail`, contradicting D-069. This is an outdated assertion, not a calculation regression.
  The original focused tests reproduced it locally: **1 failed, 33 passed**.
- Push run [41000789349](https://github.com/cavigidor/halsa-inventory/actions/runs/41000789349), job 123884445686:
  **2 failed, 1853 passed, 7 skipped, 2 xfailed**. Same assertion, plus a 30-second owner-interface timeout at the
  **first** `/yukleme` navigation in `test_second_upload_from_the_dashboard_tab_carries_the_other_reports`.
  No upload or second publication had occurred when it failed.
- Browser test infrastructure defect: `tests/browser_auth.py` cached sessions by base URL forever. Independent test
  servers use fresh SQLite databases, while the OS can reuse a port. `register(base)` retained the old server's
  cookie; `login(base)` then skipped the real login. A deterministic two-server, same-address regression failed
  before the fix (**1 failed**): the replacement server returned `authenticated: false`, `role: null` for the
  cached cookie. A disposable-process replay of the old helper also reproduced the original owner-selector timeout
  while the replacement UI reported `viewer`. The historic CI log contains no cookie/port trace, so it
  cannot establish that port reuse was the trigger in that particular run; ordinary focused rerun did not flake.
- Fix: invalidate the helper's cached session when each server registers. Keep real password login, owner sessions,
  CSRF, viewer rejection and all UI assertions. No timeout increase, retries, skips or production auth changes.
- Update the stale assertion to require the remaining flag/app blockers and positively assert clean Stock source
  readiness and absence of unrelated Kredi. Add tests for fully declared Stock despite strict Sales FAIL, unused
  Stock field errors versus used field errors, a withheld metric dependency, and Kredi's own prerequisite.
- First pushed checkpoint `226cecb`: PR run
  [41093877358](https://github.com/cavigidor/halsa-inventory/actions/runs/41093877358) passed **1860 / 7 skipped /
  2 xfailed**, but push run [41093869371](https://github.com/cavigidor/halsa-inventory/actions/runs/41093869371)
  found another pre-existing R3 fixture flake: **1 failed, 1859 passed, 7 skipped, 2 xfailed**.
  `test_unbound_source_with_matching_generation_id_is_not_complete` assumed that independently rewritten Sales/Cari
  workbooks had identical bytes. A delayed synthetic replay proved all hashes differ: ZIP timestamps and
  `docProps/core.xml` change even when worksheet cells do not. The production generation guard correctly refused
  those different bytes; this is a fixture defect, not a binding regression. The two tests modeling Stock-only
  replacement now preserve the other synthetic files byte-for-byte and explicitly assert that only Stock's hash
  changed. Exact unbound-source assertions and all mixed-generation rejection checks remain intact.

## Independent code review: actual production effect

The PR changes the documented release policy and the **read-only** diagnostic prerequisite report. Its only
application module change is `services/metric_diagnostics.py`, called by the reconciliation CLI. Neither the API
nor dashboard imports it. Production `accuracy.Gate` already evaluates each metric's declared sources, requirements,
time rules and dependencies (D-055/D-059); this PR does not change those decisions or make more results visible.
Unaudited metrics still receive strict source-wide checks. Only four candidate metrics have audited field scopes.

`metric_source_dependencies_clear` is one prerequisite, not approval of a business definition or permission to
display a result. The certified ceiling, register blockers, definition approvals, real-data reconciliation,
refresh/restart acceptance and `GENERATION_BOUND_API_EVIDENCE=False` still prevent certification. The tested
synthetic VERIFIED register is a test-only copy, not a production promotion.

No defect requiring a production-gate or financial-calculation change was established in this bounded review.
Source-wide duplicate identity still blocks all metrics reading Stock. Row/SKU isolation is **not implemented**;
the wider goal of displaying unaffected rows is not complete and must not be claimed by this PR.

## Acceptance assessment

| Requirement | Result | Evidence / limit |
|---|---|---|
| Excel-derived deterministic financial arithmetic preserved | PASS | No calculation, source mutation, AI grounding or aggregation changes |
| Own sources, fields, joins, dates and metric dependencies evaluated | PASS (bounded scope) | Reuses production Gate; four audited candidates, other metrics remain strict |
| Unrelated source failures do not add a metric release prerequisite | PASS | Positive fully declared Stock case with strict Sales FAIL; existing production gate unchanged |
| Strict reconciliation and Veri Kontrol preserved | PASS (synthetic) | Exact strict output/results/exit-code regressions; VKM implementation unchanged; private counts not rerun |
| Invalid, unknown, missing or mixed-generation required inputs block | PASS | Own-field errors, declarations, dependencies and binding regression coverage |
| Kredi independent of Stock/Cari, required for its own metric | PASS | Unrelated prerequisite absent; missing Kredi never treated as reconciled |
| No premature production VERIFIED result | PASS | Flag/register unchanged; certification still refused |
| Row/SKU isolation within a source with duplicate identity | PENDING | Conservative source-wide integrity blocker retained; no silent exclusion or sum |
| New production display behavior from PR #37 | PENDING | Diagnostic/policy change only; no production metric released |
| Private source reconciliation, refresh/restart acceptance, planner independent acceptance | PENDING | Outside this synthetic isolated checkpoint; no private workbook needed here |

## M8-R5 boundary — recorded, not implemented

Father's definitions conflict with existing withheld business interpretations: Cari monthly buckets establish invoice
age, not overdue status; customer-specific agreements are needed for overdue. Account 320 is supplier; negative
customer balances are Halsa liabilities. Old min/max levels cannot support active purchase/overstock recommendations;
trace `STOK Kontrol %` before using it. No-sales is not a discontinued indicator. One inspected duplicate has a stale
carry-forward row, but this is not a universal deduplication rule. Negative component balances are signed source
observations, not values to normalize or infer as available inventory.

Do not change these calculations, labels, register definitions or source records in R4. Keep their current blockers.
The separate agreement-based overdue report and Stock filename/sheet-date discrepancy remain unresolved. Preserve
the existing M8-R5 task; no new milestone is started.

## Verification and handoff

Completed local test counts and the first CI results are recorded in `STATUS.md`; final-head rerun results are
recorded in PR #37 and the checkpoint handoff. Local full-suite execution requires both
`REQUIRE_BROWSER_TESTS=1` and `REQUIRE_JS_TESTS=1`, with AI disabled. Private-data skips and expected failures must
be reported separately; passing algorithm-harness tests are not production certification evidence.

Claude must continue from GitHub PR #37 after comparing its current head. This clone is intentionally independent
of the live VM checkout; folder consolidation stays deferred. Do not pull, reset, move or create linked worktrees
from the canonical checkout while the VM holds it. No merge, deployment, evidence-flag change or business metric
promotion occurred. Planner review remains required; stop at this checkpoint.
