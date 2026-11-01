# M8 — First verified Cari metric: owner decisions D-072 (2026-11-01)

Branch `claude/m8-cari-first-metric` from `main` @ `0604fe85d4c5b387b1e639163e7133064843149f`. Draft PR; not merged,
not deployed; no metric promoted; `GENERATION_BOUND_API_EVIDENCE` False; 33 metrics, 0 VERIFIED. PR #39 untouched.
Counts, check codes and SHA-256 prefixes only; private evidence stays on the owner's Mac
(`~/Desktop/ME/Projects/_halsa_m8_real_acceptance/`).

## Owner decisions (D-072)
D1 report date = the date in the original file name (upload date if none, with provenance); D2 `Toplam Döviz Bakiye`
is USD, kept apart; D3 the exports are the DinamikCRM reports and authoritative — no external control total; D4
120 = Müşteri, 320 = Satıcı, other prefixes unclassified.

## Changes
| Area | Change |
|---|---|
| `services/cari_policy.py` | 120 / 320 classes and labels; customer wording only for a 120 account (account list) or a uniquely associated 120 account (Customer 360), negative balances only; USD note; file-name date rule (Turkish month names in any case, ISO, dd.mm.yyyy; impossible or several dates → refused); field-level mapping check `cari_balance_fields_v2` (fields the metric reads only; USD column optional, never read) with the shared `calculation_buckets` rule; independent openpyxl reproduction of every balance |
| `services/upload_center.py` | Cari slot: date from the original (browser) file name at assignment, else the file's upload date; typed date must agree (`cari_date_conflict`), ambiguous names refused (`cari_date_ambiguous`); provenance (`date_origin`, `date_source`, `period_complete`) carried to preview, `sources.json`, carried-over reports; declaration field set aligned with re-derivation (restart-safe) |
| `services/generation.py` | selection accepts / validates `date_origin` + `date_source`; evidence `report_date_origin` from it; Cari evidence: reproduction match → totals established, mismatch → INVALID; field mapping approved and identical to the columns the calculation reads → mapping established; explicit `false` still wins |
| `services/accuracy.py` | `cari_account_class` approved (D-072); `receivables.collections` blockers AGING-DATE, CARI-ACCOUNT-CLASS, CARI-FX removed (date is evidence; class decided; USD column not read) — REAL-DATA and the unapproved `operational_freshness` remain; `POLICY_VERSION` `m8-cari-d072` |
| Dashboard / Customer 360 / schema | class labels, 120 negative wording, USD note, "Cari rapor tarihi: … (dosya adındaki tarih / yükleme tarihi)" on the Cari screen; `account_class` may be `customer` |
| `scripts/reconcile_sources.py` | the report-date line names its provenance |

**Report-date rule — scope.** Owner business rule: **confirmed** for the exports generally (the date in the original
file name). Cari implementation: **this PR**. Stok / Satış implementation: **deferred to a separate checkpoint, not
awaiting owner confirmation**; until then their dates stay declared-or-unknown in code (D-051, D-065). No single Satış
report date is inferred from a historical range file name; an ambiguous name stays refused / unknown.

## FX audit (D2)
`receivables.collections` reads `Hesap kodu`, `Hesap adı`, `Temsilci kodu` and the month buckets (`Requirement`);
`m_collections` sums the buckets only. `Toplam Döviz Bakiye` is not read, so it cannot affect the metric: the
`CARI-FX` blocker is withdrawn. The column is preserved in the source untouched; the mapping check neither requires
nor reads it (present or absent, any values: same mapping, balances and gate).

## Real-data evidence (owner's Mac, isolated, AI disabled, loopback)
Originals unchanged (Stok `63602ae3e8bf`, Satış `be1d895e426b`, Cari `672fac2f5bf6`). The real files were uploaded
through the existing Upload Center API under their original names (owner login, CSRF), validated (58 s), published
(62 s) and restarted.

| Check | Result |
|---|---|
| Cari date | 2026-10-05, `date_origin = filename`, `date_source` = the original name; stored copy content-addressed (no date in its name); Stok / Satış unknown |
| `sources.json` | aging = file, snapshot_date, period_complete, date_origin, date_source; stock / sales = file only |
| Restart | same `core_id`; after the restart's dashboard re-publication the served `generation_id` equals the published one; same page bytes across restarts |
| Independent reproduction | 824 / 824 accounts, 0 mismatches, 0 unknown balances; template approved |
| Classes | 796 Müşteri (120), 28 Satıcı (320), 0 unclassified |
| Strict reconciliation | FAIL, 7 technical / 20 OPEN (was 23: Cari date, totals, mapping now established); aging keeps its join FAIL (45 accounts not in sales, 1 DQ-6) |
| Veri Kontrol | 17 core issues (was 19: Cari `mapping_unknown`, `report_date_missing` resolved); the two Cari join flags remain visible |
| Production gate, `receivables.collections` | NOT_VERIFIED — `REAL-DATA`, `DEF:operational_freshness` only; `metric_source_dependencies_clear` met |
| All metrics | 33: 28 invalid, 5 not_verified, 0 VERIFIED; flag False; page displays nothing |
| Test-only promoted register copy (never written; production `validate_register` refuses it) | only `receivables.collections` displayable; page renders 824 rows, the dated provenance note, the USD note; 383 "Halsa müşteriye borçlu" (120 negatives), 19 neutral 320 negatives; no overdue wording; withheld again from 2026-11-20 (46 days > provisional 45) |

## Status of each prerequisite
| Prerequisite | Status |
|---|---|
| Report date and provenance | met (D1, evidence) |
| Period completeness | met (D1 + D3 snapshot; contradictions still checked) |
| Totals | met (D3 independent reproduction; no native total exists) |
| Mapping | met (field-level check of the fields read; USD column independent) |
| Account classification | met (D4, definition approved) |
| FX | not applicable (field not read) |
| Field / join evidence for this metric | met (scoped; unrelated strict FAILs stay visible) |
| Refresh / restart binding | met (real upload flow) |
| `operational_freshness` definition | **open** — 45-day limit is provisional; an owner / planner decision (code rule `accuracy._with_freshness`) |
| REAL-DATA → reconciliation reference, certification, approval refs, regression tests | **promotion step** (separate PR) |
| `GENERATION_BOUND_API_EVIDENCE` | **promotion step** under its own release gate (`validate_register` refuses VERIFIED while False) |

No outside DinamikCRM information is needed.

## Tests
`tests/test_m8_d072_cari_first_metric.py` (33; 30 fail on `main`): file-name dates, refusals, upload persistence,
carry-over, restart, USD separation, 120 / 320 wording incl. shared accounts, reproduction / template evidence incl.
tampering and explicit `false`, re-derivation identity, production gate, test-only promotion. Policy predecessor tests
now cover `m8-r5b1-cari-invoice-age`. Updated for D-072: R5B1, Customer 360, R3 diagnostics, CP3 upload tests.
Full suite (browser + JS required, AI disabled): **1962 passed / 7 skipped / 1 xfailed**; focused 389 passed / 1 xfailed.

**Final-review corrections** (49 tests in the file; 16 of the new ones fail on the previous head `f76e698`):
- USD column removed → mapping still approved, totals established, scoped judgement empty; USD values changed (incl.
  a blank) → no effect; an unused extra column → no effect.
- Required account column missing / duplicated / corrupted (`Hesap kodu`, `Hesap adı`, `Temsilci kodu`) and month
  column missing (gap) / duplicated / corrupted / out of order / second or missing '… ve öncesi', or a legacy
  'vadesi geçen' column → `not_matched` with its reason, mapping not established, metric not verifiable; duplicated
  headers are still flagged by the source scan (`ambiguous_headers`).
- Identified columns ≠ the columns the calculation reads (frame missing one all-zero bucket, balances still equal) →
  `calculation_columns_differ`, mapping unknown.
- A one-kuruş amount change with the USD column absent → reproduction mismatch → INVALID; explicit `false` still wins.
- Real Cari export (owner's Mac, read-only, SHA-256 prefix `672fac2f5bf6` unchanged): `mapping_check` approved with the
  USD column (11 buckets, 824 accounts) and approved with it removed in memory.

Full suite after the corrections (browser + JS required, AI disabled): **1978 passed / 7 skipped / 1 xfailed**.

## Acceptance
| Criterion | Result |
|---|---|
| Owner decisions recorded (D-072) and implemented | PASS |
| File-name date, upload-date fallback, provenance, refresh / restart consistency | PASS (synthetic + real upload) |
| USD kept apart; FX blocker scoped out; mapping independent of the USD column | PASS |
| 120 / 320 classes; shared accounts never attributed | PASS |
| Independent reproduction on real data | PASS (824 / 0 mismatches) |
| Unrelated strict / Veri Kontrol findings preserved and visible | PASS |
| D-071 withdrawals unchanged | PASS |
| No promotion, flag False | PASS |
| Ready for a separate controlled VERIFIED promotion | **YES, after** the freshness decision; the promotion PR then applies the register items and the flag under its gate |
