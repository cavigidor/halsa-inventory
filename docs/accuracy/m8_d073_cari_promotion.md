# M8 — First VERIFIED metric, `receivables.collections`: controlled promotion (D-073, prepared)

Branch `claude/m8-cari-verified-promotion` from `main` @ `5e3656da88732815b3d88db6c2fc159168c8d467`. Draft PR; not
merged, not deployed. **The freshness policy is a proposal pending the owner's explicit approval** — so in production
the metric stays withheld: 33 metrics, 0 VERIFIED, `GENERATION_BOUND_API_EVIDENCE` False. PR #39 untouched. Counts,
check codes and SHA-256 / generation prefixes only; private evidence stays on the owner's Mac
(`~/Desktop/ME/Projects/_halsa_m8_real_acceptance/`).

## 1. Proposed freshness policy (owner approval PENDING)
45 days for current Cari account balances, counted from the Cari report date (D-072 D1: the date in the original file
name; the upload date only when the name has none). The 5 Oct 2026 export is eligible through **19 Nov 2026** and
stale (withheld, never zero) from **20 Nov 2026**. A newly uploaded, correctly verified monthly report uses its own
file-name date. Point for the owner to confirm with the approval: whether a report whose name carries no date (upload
date, D-072 D1) is also acceptable for display, or only file-name-dated reports.

Implementation: a metric-specific definition `cari_balance_freshness` (`max_age_days = 45`, **unapproved**,
question_ref D-073). `validate_register` requires the metric's `max_source_age_days` to equal the policy limit and at
most one freshness policy per metric. The 32 other metrics keep the general `operational_freshness` (unapproved) and
their limits unchanged — tested.

## 2. Release prerequisites (D-069 Option B)
| Requirement of `receivables.collections` | Status | Evidence |
|---|---|---|
| D-072 account classes (120 / 320) | met | definition `cari_account_class` approved (D-072); real: 796 × 120, 28 × 320, 0 unclassified |
| D-072 file-name date + provenance | met | real: 2026-10-05, `date_origin = filename`, `date_source` = original name; stored copy content-addressed |
| Exact source generation and fingerprint | met | real: Cari `672fac2f5bf6`, core `371248e21e96c6f6`; all four core records bound |
| Approved field-level mapping | met | `cari_balance_fields_v2` approved; USD column present and not read; 11 buckets |
| Independent workbook balance reproduction | met | 824 / 824 accounts, 0 mismatches, 0 unknown; rendered page balances = openpyxl balances |
| No source-integrity problem in its dependencies | met | scoped problems 0; strict aging FAIL is the sales join (not read by this metric) — kept and shown |
| Refresh, restart, current-generation binding | met | real upload → publish → restart: same `core_id`; `GET /` 200, page generation = API generation |
| Freshness | **pending owner approval** | rule implemented; boundary proven on real data (19 Nov eligible, 20 Nov withheld) |
| Register: blockers, approval refs, regression tests, reconciliation ref | met (prepared) | REAL-DATA removed; D-069 / D-071 / D-072 / D-073; tests named; ref below |
| Certification + `GENERATION_BOUND_API_EVIDENCE` | **activation step** | three changes after the owner's sign-off (§3) |

The seven strict reconciliation failures (and 20 OPEN) remain recorded and shown in the CLI and Veri Kontrol (17 core
issues); none lies in this metric's own source / field / join / temporal dependencies. No further DinamikCRM data is
requested (D-072 D3).

## 3. Changes in this PR
| Area | Change | Active now? |
|---|---|---|
| `services/accuracy.py` · `Definition.max_age_days` | freshness definitions can fix their age limit | yes (no effect on any decision) |
| `cari_balance_freshness` (45 days) | new, **unapproved**; `FRESHNESS_DEFINITIONS`; `_with_freshness` skips a metric with its own policy | yes, unapproved → withholds |
| `receivables.collections` | blockers `()` (REAL-DATA removed), definitions `cari_account_class` + `cari_balance_freshness`, `max_source_age_days = 45`, `approval_refs` D-069 / D-071 / D-072 / D-073, `regression_tests`, `reconciliation_ref = CARI_RECONCILIATION_REF`; certified stays NOT_VERIFIED | yes (gate: only `DEF:cari_balance_freshness`) |
| `validate_register` | one freshness policy per metric; limit = policy; refuses a `PENDING…` reconciliation ref | yes |
| `REFRESH_COUNT_METRIC["overdue_total"]` | → withdrawn `receivables.risk` (was the factual list): certifying the list can never un-redact an overdue field | yes |
| **Activation** (`tests/fixtures/d073_activation.py`) | approve `cari_balance_freshness` (D-073); certify VERIFIED; `GENERATION_BOUND_API_EVIDENCE = True` | **NO — after owner sign-off** |
| `docs/m8_cp1_access.md` §6 | the Cari list row (public by link once active; owner sign-off before hosting) | documentation |

Reconciliation reference (exact string, `accuracy.CARI_RECONCILIATION_REF`): "D-073 owner-local real-data acceptance
2026-11-02 UTC (docs/accuracy/m8_d073_cari_promotion.md §4): Cari export sha256 672fac2f5bf6, core_id
371248e21e96c6f6; report date 2026-10-05 from the original file name; mapping cari_balance_fields_v2 approved (USD
column present, not read); 824/824 balances reproduced independently, 0 mismatches, 0 unknown; 796 x 120 / 28 x 320 /
0 unclassified; scoped source problems 0 (D-069 Option B); upload, publish and restart through the real Upload Center:
same core_id, page generation = API generation".

## 4. Owner-local real-data acceptance (Mac, 2026-11-02 UTC) — PASS
Byte-identical copies of the 5 Oct exports (Stok `63602ae3e8bf`, Satış `be1d895e426b`, Cari `672fac2f5bf6`; unchanged
before / after; the stored upload copies carry the same prefixes). Exact working tree of this branch, Python 3.12, AI
disabled, in-process app over the real owner login + CSRF (throwaway credential, deleted afterwards). The TEST-ONLY
activation was applied in the acceptance process only (never written).

| Check | Result |
|---|---|
| Upload under original names → validate (56 s) → publish (55 s) | ready, 0 errors → published; `sources.json` aging: 2026-10-05, `filename`, original name as source, stored name without a date |
| Status after publication | core `371248e21e96c6f6`, generation `29deff56b8897eb6`; aging / sales / liste / stock bound; 33 metrics: 1 verified (`receivables.collections`, no codes), 4 not_verified, 28 invalid |
| `GET /` after publication | 200; displayed = `receivables.collections` only; page core = status core; `X-Halsa-Generation` = status generation |
| Restart (new process) | same core `371248e21e96c6f6`; `restore_reason` none; dashboard re-published (generation `485fdde76000abdd`); `GET /` 200, header = status generation, displayed = the Cari list only |
| Evidence | date 2026-10-05 (filename), complete, totals True, mapping True; reproduction 824 / 824, 0 mismatches, 0 unknown; mapping `cari_balance_fields_v2` approved, USD column present, 11 buckets; scoped problems 0; strict source-wide problems kept (1 on aging: the sales join) |
| Production gate as committed | 1 Nov / 19 Nov: NOT_VERIFIED `DEF:cari_balance_freshness`; 20 Nov: + `SRC:aging` (stale) |
| TEST-ONLY activation | production `validate_register` passes; displayable 1 Nov / 19 Nov = `receivables.collections` only; 20 Nov = none (`SRC:aging`) |
| Rendered page (activated) | 824 rows; page balances = independent openpyxl balances; labels 796 Müşteri (120) / 28 Satıcı (320); signs 402 credit / 386 debit / 36 zero; date note "dosya adındaki tarih"; USD note; 0 overdue strings; no other module has rows |
| Strict reconciliation CLI + metric diagnostics | FAIL, 7 technical / 20 OPEN (exit 1, unchanged); collections unmet prerequisites = gate verified, register certified, definitions approved, app binding (not evaluated by the CLI; shown above), evidence flag — i.e. exactly the activation set; `metric_source_dependencies_clear`, blockers, approval refs, tests and reconciliation ref met |
| Veri Kontrol | 17 core issues (unchanged) |

## 5. Tests (synthetic; real production gate)
`tests/test_m8_d073_cari_promotion.py` (18): register prepared but inert; production gate names only the pending
policy; activation = exactly three changes; production validation accepts the full activation and refuses every
partial one (unapproved policy, 46-day limit, pending ref, a blocker, no freshness policy, flag False, any other
metric certified); 5 Oct report eligible 5 Oct / 1 Nov / 19 Nov, stale 20 Nov / 1 Mar, future date INVALID; unrelated
metrics' freshness verdicts unchanged; activated gate shows only the Cari list (32 withheld) with exact signed
balances, 120 / 320 labels, sign wording, date provenance, USD note, no overdue wording, every other module withheld;
USD values / USD column absent change nothing; the overdue refresh count stays redacted; missing / other-generation /
unbound evidence, a one-kuruş pipeline mismatch, a duplicated month column and an unknown date all block. Real app:
upload → publish → page and API on one generation, business APIs still 409; 46 days later the same page is refused
(503 `not_authorized`), refresh rebuilds it withheld (no balance); a new monthly upload re-binds with its own date;
the old page put back is refused; a failed publication keeps the current generation; restart re-derives the same
core and re-authorizes; a changed stored source is never served; viewer / financial viewer / owner permissions
unchanged (viewer sees the list, cannot write; financial sections still withheld; owner can upload).
Updated: D-072 gate expectations (`DEF:cari_balance_freshness` only), R3 diagnostics, R5B1 register test.
Full suite (browser + JS required, AI disabled): **1996 passed / 7 skipped / 1 xfailed** (`main` 1978 + 18); focused
(D-073, D-072, R5B1, R3, domain rules, accuracy, access, finance) 416 passed / 1 skipped / 1 xfailed.

## 6. Not done here (separate checkpoints)
Owner approval and the activation commit; owner sign-off on the disclosure row before hosting; Stok / Satış file-name
dates; R5B2; hosting; folder consolidation. PR #39 untouched.
