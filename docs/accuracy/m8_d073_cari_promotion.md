# M8 — First VERIFIED metric, `receivables.collections` (D-073, owner-approved 60-day Cari freshness)

Branch `claude/m8-cari-verified-promotion` from `main` @ `5e3656da88732815b3d88db6c2fc159168c8d467`, draft PR #43.
Not merged, not deployed, no hosting. PR #39 untouched. Counts, check codes and SHA-256 / generation prefixes only;
private evidence stays on the owner's Mac (`~/Desktop/ME/Projects/_halsa_m8_real_acceptance/`).

## 1. Owner decision D-073 (2026-11-01)
- **60 days** for current Cari account balances (supersedes the 45-day proposal of the first draft), counted from the
  Cari report date of D-072 D1. The 5 Oct 2026 export: eligible through **4 Dec 2026** (day 60), withheld from
  **5 Dec 2026** (day 61) — unavailable, never zero. A new, independently validated monthly report uses its own date.
- **No-date fallback (D-072 D1, already approved):** a name without a parseable date → the upload calendar date, with
  provenance recorded and shown; the same 60-day rule; generation-bound. Impossible / ambiguous name dates stay
  refused (422), never guessed.
- **Scope:** this metric only (`cari_balance_freshness`, approved, D-073, `max_age_days = 60`; metric
  `max_source_age_days = 60`). The global `operational_freshness` (unapproved, 45 days) and the other 32 metrics'
  limits are unchanged.

## 2. Release prerequisites (D-069 Option B)
| Requirement of `receivables.collections` | Status | Evidence |
|---|---|---|
| D-072 account classes (120 / 320) | met | `cari_account_class` approved (D-072); real: 796 × 120, 28 × 320, 0 unclassified |
| D-072 file-name date + provenance (fallback: upload date) | met | real: 2026-10-05, `date_origin = filename`, `date_source` = original name |
| Exact source generation and fingerprint | met | real: Cari `672fac2f5bf6`, Stok `63602ae3e8bf`, Satış `be1d895e426b`; all four core records bound |
| Field-level mapping | met | `cari_balance_fields_v2` approved; USD column present, not read; 11 buckets |
| Independent workbook balance reproduction | met | 824 / 824 accounts, 0 mismatches, 0 unknown; page balances = openpyxl balances |
| No source-integrity problem in its own dependencies | met | scoped problems 0; the aging strict FAIL is the sales join (not read by this metric) — kept and shown |
| Refresh, restart, current-generation binding | met | real upload → publish → restart: same `core_id`; `GET /` 200, page generation = API generation |
| Freshness | met | D-073 approved (60 days); boundary proven on real data (4 Dec shown, 5 Dec withheld) |
| Register: blockers, approval refs, tests, reconciliation ref | met | REAL-DATA removed; D-069 / D-071 / D-072 / D-073; tests named; ref below |
| Certification + `GENERATION_BOUND_API_EVIDENCE` | **applied** | certified VERIFIED; flag True after the owner-local §8.3 acceptance (§4) |

The seven strict reconciliation failures and 20 OPEN remain recorded and shown (CLI exit 1; Veri Kontrol 17 core
issues); none lies in this metric's own source / field / join / temporal dependencies.

## 3. Changes
| Area | Change |
|---|---|
| `services/accuracy.py` · definitions | `Definition.max_age_days`; `cari_balance_freshness` approved (D-073, 60 days); `FRESHNESS_DEFINITIONS`; `_with_freshness` leaves a metric with its own policy alone |
| `receivables.collections` | blockers `()`, definitions `cari_account_class` + `cari_balance_freshness`, `max_source_age_days = 60`, `approval_refs` D-069 / D-071 / D-072 / D-073, `regression_tests`, `reconciliation_ref = CARI_RECONCILIATION_REF`, **certified VERIFIED** |
| `GENERATION_BOUND_API_EVIDENCE` | **True** (it permits a certification; every metric is still judged by the gate) |
| `POLICY_VERSION` | `m8-cari-d073`: pages / manifests of `m8-cari-d072` (built while the list was always withheld) are refused live and after a restart until the controlled refresh / restart re-publication rebuilds them (tested with the other predecessors) |
| `validate_register` | one freshness policy per metric; limit = policy; refuses a `PENDING…` reconciliation ref |
| `REFRESH_COUNT_METRIC["overdue_total"]` | → withdrawn `receivables.risk`: certifying the list never un-redacts an overdue field |
| Upload Center preview | the collections tab label "Tahsilat" → "Cari Bakiye (fatura yaşı)" (D-071 name; owner-only preview) |
| Tests | `tests/release_state.py` (one certified metric, flag True) replaces the old "nothing VERIFIED / flag False" assertions; the test-only activation fixture is removed |
| Access docs | legacy local access vs the required protected launch; the Cari list flagged for the access checkpoint (§6) |

Reconciliation reference (unchanged, `accuracy.CARI_RECONCILIATION_REF`): "D-073 owner-local real-data acceptance
2026-11-02 UTC (docs/accuracy/m8_d073_cari_promotion.md §4): Cari export sha256 672fac2f5bf6, core_id
371248e21e96c6f6; report date 2026-10-05 from the original file name; mapping cari_balance_fields_v2 approved (USD
column present, not read); 824/824 balances reproduced independently, 0 mismatches, 0 unknown; 796 x 120 / 28 x 320 /
0 unclassified; scoped source problems 0 (D-069 Option B); upload, publish and restart through the real Upload Center:
same core_id, page generation = API generation". It names the first acceptance run (policy `m8-cari-d072`); the final
run below repeats every check under `m8-cari-d073` (its `core_id` differs only because the policy version is part of
the digest).

## 4. Owner-local real-data acceptance (Mac) — PASS
Originals (`StockAgent/data/`, read only) and the acceptance copies are byte-identical: Stok `63602ae3e8bf`
(1,657,601 B), Satış `be1d895e426b` (40,293,157 B), Cari `672fac2f5bf6` (59,035 B); the stored upload copies carry the
same prefixes. Python 3.12, AI disabled, in-process app over the real owner login + CSRF (throwaway credential,
deleted). Two runs:

| Check | Run 1 — 2026-11-02 02:48 UTC (prepared code, test-only activation in process) | Run 2 — 2026-11-02 04:02 UTC (**final code, production register as committed**) |
|---|---|---|
| Upload (original names) → validate → publish | ready, 0 errors (56 s) → published (55 s) | ready, 0 errors (59 s) → published (55 s) |
| `sources.json` aging | 2026-10-05 · `filename` · original name · stored name without date | same |
| Status after publication | core `371248e21e96c6f6`, generation `29deff56b889`; 4 core sources bound | core `01d305886628f6c6`, generation `6c6163206e11`; 4 core sources bound; policy `m8-cari-d073`; flag True |
| Metric states | 1 verified (this one), 4 not_verified, 28 invalid | 1 verified (this one, no codes), 4 not_verified, 28 invalid |
| `GET /` | 200; displayed = this metric only; page core = status core; header = status generation | same |
| Restart (new process) | same core; dashboard re-published (generation `485fdde76000`); `GET /` 200, header = status | same core `01d305886628f6c6`; `restore_reason` none; re-published generation `d62af9cc1b35`; `GET /` 200, header = status; displayed = this metric |
| Evidence | 2026-10-05 filename, complete, totals ✓, mapping ✓; 824 / 824, 0 mismatches, 0 unknown; scoped 0 | same |
| Gate by date | (45-day proposal) | 2 Nov (28 d) / 20 Nov (46 d) / **4 Dec (60 d): shown**; **5 Dec (61 d): withheld** (`SRC:aging`) |
| Rendered page | 824 rows = openpyxl balances; 796 Müşteri (120) / 28 Satıcı (320); 402 credit / 386 debit / 36 zero; date + USD notes; 0 overdue strings; no other module has rows | same |
| Strict CLI + metric diagnostics | FAIL 7 / 20 OPEN (exit 1) | FAIL 7 / 20 OPEN (exit 1); collections gate DOĞRULANDI; only unmet item: app binding (not evaluated by the CLI — proven by the app run) |
| Veri Kontrol | 17 core issues | 17 core issues |

## 5. Tests (synthetic; the production register and gate)
`tests/test_m8_d073_cari_promotion.py` (22): register certifies only this metric (60 days, D-073, flag True, policy
`m8-cari-d073`, the 32 others unchanged); activation = exactly three changes from the prepared state; production
validation refuses every incomplete state (unapproved policy, 45 / 59 / 61-day limits, pending ref, a blocker, no
policy, any other metric certified, flag off); 5 Oct report shown 5 Oct / 20 Nov / 4 Dec, withheld 5 Dec / 1 Mar;
upload-date fallback shown at day 0 / 60, withheld at 61, generation-bound; future date INVALID; unrelated verdicts
unchanged; only this metric displayable (32 withheld) with exact signed balances, 120 / 320 labels, sign wording,
date provenance, USD note, no overdue wording; USD values / column absent change nothing; overdue refresh count stays
redacted; missing / other-generation / unbound / one-kuruş mismatch / duplicated month / unknown date block. Real
app: page and API on one generation, business APIs still 409; day 60 served, day 61 refused (503) and rebuilt
withheld; no-date name → upload date, provenance shown, same 60 / 61 days, impossible / ambiguous names refused; new
monthly upload re-binds; the old page put back is refused; failed publication keeps the current generation; restart
re-authorizes; a changed stored source is never served; viewer / financial viewer / owner permissions unchanged.
Predecessor policy `m8-cari-d072` added to the refused-page tests. 18 other test files assert the new release state
through `tests/release_state.py`.

## 6. Access — separate launch checkpoint (not in this PR)
Owner launch requirement: one password to enter the website, a separate password for the sensitive financial
sections, owner-only administration / Excel uploads / edits. The implemented D-061 model (public viewer without a
password) is the legacy local arrangement, not the approved hosting arrangement, and no approval of public-by-link
disclosure of Cari account names and balances. The account-level Cari list is flagged for that checkpoint
(`docs/m8_cp1_access.md` §6). No hosting or external deployment is authorized.

## 7. Not done here
Access / launch checkpoint; Stok / Satış file-name dates; R5B2; hosting; folder consolidation. PR #39 untouched.
