# M8-R4 + M8-R5A — independent review and integration check (2026-10-31)

_Independent review of PR #37 and PR #38 and the proof that they combine safely, first on the temporary
integration branch `integration/m8-r4-r5a` (draft PR #39, never to be merged), then for real in PR #38. No private
workbook used, no metric promoted, `GENERATION_BOUND_API_EVIDENCE` False, nothing deployed. Verified outcomes only._

**Current status (2026-10-31):**
- **PR #37 merged** (planner-approved, code only): squash commit **`52522b1`** on `main`; its tree is byte-identical to
  the reviewed head `c430e0e` (tree `1692ad9`).
- **PR #38 updated, not merged, still draft:** `main` (`52522b1`) merged into `codex/m8-r5a-retire-stock-thresholds`
  without rebase (`e2f0d69`; resulting tree identical to the reviewed integration merge `b55046b`), then the carried
  test correction (finding 1) and the two regression tests for findings 2 and 3 (§6). Findings 4 and 5 remain open.
- **PR #39** stays open as evidence until the planner approves its cleanup.

## 1. Starting state

| | |
|---|---|
| `main` | `ffa2c81` (M8-R3 merged) |
| PR #37 (M8-R4, D-069) | draft, branch `m8-r4-option-b-scoped-release`, head `c430e0e` = reported; based on `ffa2c81`; CI on `c430e0e`: PR run `41096032644` ✅, push run `41096021175` ✅ |
| PR #38 (M8-R5A, D-070) | draft, branch `codex/m8-r5a-retire-stock-thresholds`, head `ecce773` = reported; based on `ffa2c81`; CI on `ecce773`: PR run `41103973736` ✅, push run `41103911159` ✅ |
| Owner's Mac checkout | not accessed; the integration used a fresh cloud clone |

## 2. Integration

`origin/main` → `git merge --no-ff` PR #37 (`4105850`) → `git merge --no-ff` PR #38 (`b55046b`) → one integration-only
test adjustment. No rebase, no force-push, no change to either original PR branch.

| Overlapping file | Result |
|---|---|
| `DECISIONS.md` | textual conflict; D-069 and D-070 both kept, in order |
| `TASKS.md` | textual conflict; both checkpoint lines kept |
| `STATUS.md` | textual conflict; both checkpoint blocks kept (R4, then R5A) |
| `services/metric_diagnostics.py` | merged automatically: #37's per-metric `metric_source_dependencies_clear` and Kredi-only prerequisite, and #38's `STOCK-THRESHOLDS-RETIRED` category |
| `tests/test_m8_r3_metric_diagnostics.py` | two comment-only conflicts: both PRs carry the **identical** byte-preserving fixture fix for the XLSX ZIP-timestamp flake; #37's wording kept |

**Semantic interaction found and resolved (integration only):** #37's
`test_option_b_withheld_metric_dependency_blocks_readiness` hard-coded the withheld dependency `stock.overstock` as
`not_verified`; #38 deliberately makes it `invalid` (retirement blocker). The test now takes the expected state from
the production gate's own decision and asserts it is INVALID with `STOCK-THRESHOLDS-RETIRED` — exact, not relaxed. The
test's purpose (a withheld dependency blocks readiness) is unchanged. Neither PR is wrong on its own; whichever merges
second must carry this adjustment.

## 3. Tests (cloud, synthetic only, `AI_PROVIDER=disabled`, `REQUIRE_BROWSER_TESTS=1`, `REQUIRE_JS_TESTS=1`)

Local Chromium came from the pre-installed browser (Playwright 1.63.0 in the local venv; CI keeps the pinned 1.56.0).

| Suite | Result |
|---|---|
| M8-R4 focused (`test_m8_r3_metric_diagnostics.py` + the same-address browser-session test) | 31 passed |
| M8-R5A focused (`test_m8_r5a_stock_retirement.py`) | 25 passed |
| Generation / authorization / tamper / restart / request-pinning (`test_m8_accuracy*.py`, 7 files) | 275 passed, 1 xfailed |
| **Full suite** | **1885 passed, 7 skipped, 2 xfailed** (1,894 collected = `main` 1,861 + #37 8 + #38 25) |

Skips: private-data tests (no ERP exports in the cloud). Expected failures: DQ-1 missing-cost margin and DQ-6
invoice-account allocation (unchanged). Both earlier CI-flake fixes survive integration: the XLSX byte-preserving
fixture (both PRs) and the browser session-cache invalidation (`tests/browser_auth.py`, #37), whose same-address test
passed with real login.

## 4. Gate and generation checks (independent of the PRs' own tests)

- **Register:** 33 metrics; certified ceilings identical to `main` (28 NOT_VERIFIED, 5 INVALID, 0 VERIFIED);
  `validate_register()` passes; flag False.
- **Gate on one synthetic, fully declared generation:** `main` 19 not_verified / 14 invalid; integration 17 / 16. The
  only changes: `stock.reorder` and `stock.overstock` not_verified → invalid (D-070 blocker). Nothing became more
  displayable.
- **Policy-version invalidation (ad-hoc, not committed):** a page + manifest published exactly as the pre-D-070 build
  did (`m8-accuracy-1`) is refused live (503 `policy_marker_missing`, no data) and after a restart (`evidence_mismatch`,
  503 `no_accepted_generation`, nothing old served).
- **Product-count security tests (#38) — negative control:** forcing the "N ürün" header to render ungated makes
  `test_get_root_waits_for_publication_and_serves_one_consistent_generation` fail, so the header is genuinely gated
  and the tests do detect an ungated count; the other rewritten tests exercise server-side refusal (503 pages carry no
  data). Coverage of the header gate itself rests on that one test (see §5).

## 5. Findings

| # | PR | Severity | Finding | Recommendation |
|---|---|---|---|---|
| 1 | #37 × #38 | must fix on merge | the hard-coded dependency state above | **done in PR #38** (carried from `677d166`) |
| 2 | #38 | low | only one test detects an ungated product-count header | **done in PR #38**: §6 Test A |
| 3 | #38 | low | no committed test for old-policy page / manifest invalidation (verified ad hoc here) | **done in PR #38**: §6 Test B |
| 4 | #37 + #38 | low (presentation) | the diagnostics still list `stock.reorder` / `stock.overstock` as "first-release candidates", and tag `STOCK-THRESHOLDS-RETIRED` as a pending business answer although the father has answered | after both merge: drop the two retired metrics from `CANDIDATES` (or label them "retired, D-070") and give the retirement blocker its own category |
| 5 | #37 | cosmetic | `test_option_b_unrelated_kredi_does_not_gate_stock_or_collections` comments that the strict result is OPEN while its scenario is a strict FAIL (the helper is given "OPEN") | correct the comment |

No defect found in production gating, generation binding, source validation, Veri Kontrol detection or the unrelated
customer / financial calculations.

## 6. Regression tests added in PR #38 (`tests/test_m8_r5a_authorization_regressions.py`, 5 tests)

Real production register and gate, synthetic workbooks, every stock declaration made (only the register withholds).

| Test | What it proves |
|---|---|
| A · `test_unverified_product_count_is_never_exposed` | production register: no "N ürün" header, metric not in the page's `stockagent-displayed` marker, refresh report count null, `GET /` serves the page without it, `/api/context` withheld (409, no `products_scanned`) |
| A · `test_verified_synthetic_control_shows_exactly_the_count` | control: a synthetic VERIFIED register copy shows exactly the count, declared in the marker, under the current generation header; that copy is still refused as a production register |
| B · `test_old_policy_page_and_manifest_are_refused_live_and_after_restart` | a page + manifest written exactly as the pre-D-070 build (`m8-accuracy-1`, old-policy `core_id`) carrying the count: live 503 `policy_marker_missing`; restart 503 `no_accepted_generation`, `restore_reason = evidence_mismatch`, no generation; no old bytes, data or policy marker served |
| B · `test_regenerated_current_policy_page_follows_normal_authorization` | after a controlled refresh under the current policy the page is served normally (live and after restart, same `core_id`) |
| B · `test_regenerated_page_without_verification_still_hides_the_count` | the same regeneration under the production register still hides the count |

**Negative controls (run, not committed):** rendering the header without the gate fails 3 of the 5 (A, the control —
whose marker assertion catches a count shown without being declared — and the regenerated-unverified case); removing
`GET /`'s policy-marker check fails Test B's refusal test.


**Validation of the updated PR #38 (cloud, synthetic only, AI disabled, browser + JS required):** R4 focused 31 passed;
R5A focused + regressions 30 passed; generation / authorization (`test_m8_accuracy*.py` + access) 400 passed,
1 xfailed; **full suite 1890 passed, 7 skipped, 2 xfailed** (1,899 collected = 1,894 + 5). Register: 33 metrics,
28 NOT_VERIFIED / 5 INVALID / 0 VERIFIED, `validate_register()` passes, `GENERATION_BOUND_API_EVIDENCE` False,
policy `m8-r5a-stock-retirement`.
