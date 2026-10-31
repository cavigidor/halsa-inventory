# M8-R3 — Strict vs dependency-scoped accuracy diagnostics

**Historical R3 report.** D-069 in PR #37 supersedes the global strict/Kredi release prerequisite and the undecided
Option A/B discussion below. See [the M8-R4 checkpoint](m8_r4_isolated_checkpoint.md) and `release_gate.md` §8.3
for current scope and limits. R3's recorded private observations are historical, not a new acceptance run.

_2026-10-29. Read-only diagnostic and evidence for a future release-gate decision. It decides nothing: no check,
register entry, definition, approval, release-gate rule, displayed value or `GENERATION_BOUND_API_EVIDENCE` (False) is
changed, and no metric is promoted. Counts, codes, fixed reason texts and schema labels only._

## 1. What the three judgements mean (confirmed in code)

| Judgement | Where | What it reads | Used for |
|---|---|---|---|
| **Strict, source-wide** | `scripts/reconcile_sources.reconcile_core()` → `SourceEvidence.problems()` without a scope | every problem of each core source: integrity, every field, every join, totals, mapping, dates | `GENEL SONUÇ` PASS / OPEN / FAIL and the exit code; `release_gate.md` §8.3 step 2 requires it not to be FAIL before the flag may change |
| **Per metric (dependency-scoped)** | `accuracy.Gate.decide()` → `SourceEvidence.problems(scope)` = `_scoped_problems()` for metrics that declare `requires` (D-055, D-059), plus `temporal_problems()`, register blockers, unapproved definitions and dependencies | source integrity always; field-level problems only for the fields the metric reads; joins only those it uses | the state that decides whether a metric is shown (VERIFIED only); evidence bound to one generation (`Manifest.bound_snapshot`) |
| **Release prerequisites** | `release_gate.md` §2 (per metric) and §8.3 (before the flag), `validate_register()` | the register entry (certified, blockers, approved definitions, approval refs, regression tests, reconciliation ref), the strict result, Kredi, the app publication / restart checks, the flag | whether a metric could be certified at all |

A metric can only be shown when all three agree. Today none do.

## 2. The diagnostic (`--metric-diagnostics`)

```bash
python -m scripts.reconcile_sources --core data --metric-diagnostics [--evidence-out]
```

- **Opt-in, read-only.** Without the flag the command is byte-for-byte the same. With it, the strict checks, counts,
  `GENEL SONUÇ`, the evidence `results` and the exit code are unchanged (tests compare them); a section follows the
  overall result, and `--evidence-out` adds a `metric_diagnostics` key. Nothing else is written.
- **One generation.** It reuses the manifest the command just built from the frozen bytes and binds it to an
  independent SHA-256 of those same bytes, through `accuracy.current_gate` — exactly as the API binds evidence. Evidence
  of another generation, unbound bytes or a missing record are reported as unavailable (`EVIDENCE-GENERATION` /
  `NO-EVIDENCE`) and never pass. The summary reports two separate facts: whether the **generation ID matches**, and
  whether the **core evidence is complete and bound** (aging, liste, sales, stock all present with evidence and bound
  to the bytes read); a matching ID with an unbound or missing source is shown as incomplete, with the sources named.
- **Per candidate** (`receivables.collections`, `stock.reorder`, `stock.overstock`, `stock.product_counts`): the
  strict status of each source it reads; the gate's state and codes; the parts of that decision (certified ceiling,
  open blockers, unapproved definitions, source problems as the gate judges them for this metric, time rules,
  dependencies) — the breakdown must recompose to the gate's state; every strict problem of those sources that does
  not appear in the metric's judgement, labelled **"kaynakta HÂLÂ VAR (çözülmedi)"**; and each release prerequisite
  as met / not met / not evaluated by this command (never counted as met).
- **Category of each blocker:** business answer · authoritative date · fingerprint-bound owner declaration
  (selection, totals, mapping) · technical or source fix · independent real-data reconciliation · release
  prerequisite · evidence unavailable. Where two apply, the first must happen first (e.g. DQ-4: business answer, then
  an implemented, tested rule).
- Code: `services/metric_diagnostics.py`; tests: `tests/test_m8_r3_metric_diagnostics.py`.

## 3. Owner-local acceptance on the 5 October exports (2026-10-29)

`main` @ `cc4a50a` + this branch's patch, fresh clone in Claude's isolated workspace on the owner's Mac (Python 3.12,
pandas 3.1.0, openpyxl 3.1.5), byte-identical read-only copies (Stok `63602ae3e8bf`, Satış `be1d895e426b`, Cari
`672fac2f5bf6`), explicit selection without declarations or approvals, `STOCKAGENT_NO_DOTENV=1`,
`AI_PROVIDER=disabled`, no server, no SQLite. Originals, the owner's checkout (`c2a3df5`) and `.env` unchanged
(fingerprints, sizes, mtimes); the data folder gained no file. A sweep of the diagnostic output (console + JSON)
against all 60,810 identity strings of the three workbooks found none.

| | Expected from M8-R2 | Actual now |
|---|---|---|
| Strict result | FAIL · 7 failures · 23 OPEN | **FAIL · 7 · 23** (exit 1), identical with and without `--metric-diagnostics` |
| Veri Kontrol (detection incl. `kredi / missing_source`) | 20 | **20** (14 high, 6 medium) |
| Gate (33 metrics) | 0 VERIFIED | **0 VERIFIED** — 28 invalid, 5 not_verified |
| Cari data rows / phantom blank cells | 824 / 0 | **824 / 0** |
| Generation | — | generation ID matches; core evidence complete and bound (aging, liste, sales, stock); none unbound or missing |
| Strict per source | — | aging FAIL, liste FAIL, sales FAIL, stock FAIL |

## 4. Blocker matrix for the candidate metrics (real files)

Gate state: **Tahsilat** `not_verified` · **Azalan Stok** `invalid` · **Fazla Stok** `invalid` · **Stok ürün
sayıları** `invalid`. Certification permitted: **no** for all four.

| Blocker (as the gate / release rules state it) | Tahsilat | Azalan | Fazla | Ürün sayıları | Needs |
|---|---|---|---|---|---|
| Report date unknown (`Kaynak raporun tarihi bilinmiyor`; `AGING-DATE` / `STOCK-DATE`) | ✗ | ✗ | ✗ | ✗ | **authoritative date** (exporter) declared at upload |
| Period completeness not established | ✗ | ✗ | ✗ | ✗ | **authoritative date / period** declaration |
| Totals not reconciled (`totals_consistent` unknown) | ✗ | ✗ | ✗ | ✗ | **owner declaration** bound to the file, after the accountant confirms control totals |
| Mapping approval unknown | ✗ | ✗ | ✗ | ✗ | **owner declaration** bound to the file |
| Duplicate product code (source integrity, INVALID) | — | ✗ | ✗ | ✗ | **business answer** (B3), then a **source or tested technical fix** |
| `#DIV/0!` in the coverage field read (INVALID) | — | ✗ | ✗ | — | **business answer** (B1/B2, DQ-4), then an **implemented, tested** evidence rule |
| `DQ-4` blocker + `stock_coverage_rule` definition | — | ✗ | ✗ | — | **business answer** → D-xxx, then **technical fix** |
| `STOCK-REPORT-SCOPE` (what the export lists; the duplicate) | — | ✗ | ✗ | ✗ | **business answer** (A1, B3), then **technical fix** if needed |
| `INV-VALUE` + `inventory_valuation` definition | — | — | ✗ | — | **business answer** (B4, accountant) |
| `OVERDUE-DEF` + `overdue_definition` definition | ✗ | — | — | — | **business answer** (A3, C1: buckets, overdue, 120 / 320, negative balances) |
| `operational_freshness` definition (45-day limit provisional) | ✗ | ✗ | ✗ | ✗ | **business answer** (owner policy) |
| `REAL-DATA` blocker / no `reconciliation_ref` | ✗ | ✗ | ✗ | ✗ | **independent reconciliation evidence** |
| Register: certified NOT_VERIFIED, no approval refs, no regression tests recorded | ✗ | ✗ | ✗ | ✗ | **release prerequisite** — one reviewed promotion PR per metric |
| Strict core reconciliation FAIL (§8.3 step 2) | ✗ | ✗ | ✗ | ✗ | **release prerequisite** — see §5 |
| Kredi reconciliation (§8.3 step 3) — not run, no file of record | ✗ | ✗ | ✗ | ✗ | **release prerequisite** + the Kredi workbook of record |
| App refresh / restart binding (§8.3 steps 4–5) — not evaluated by the CLI | ✗ | ✗ | ✗ | ✗ | **release prerequisite** (owner-local app run) |
| `GENERATION_BOUND_API_EVIDENCE` False | ✗ | ✗ | ✗ | ✗ | **release prerequisite** (§8.3 step 6, separate PR) |
| Strict-only, still in the source (not in the metric's own judgement) | Cari join (F7) | stock join (F3); source-wide Excel errors (F1); duplicated `Stok_Kodu` header (resolved for scoped metrics by D-056) | same as Azalan | same as Azalan | resolved only as in `m8_r1_issue_actions.md` §C1 — **never** by scoping |

`✗` = blocks this metric today · `—` = not part of this metric's judgement.

## 5. The two release-gate options (explained, **not selected or implemented**)

The open conflict (`m8_r1_issue_actions.md` §D): the candidates are judged per metric, while §8.3 requires the strict,
all-source result not to be FAIL before the flag may change. The diagnostic shows both side by side on the same bytes.

**Option A — keep §8.3 as written (non-FAIL overall reconciliation).**
- Every strict failure F1–F7 must disappear from the strict result: through corrected exports (stock duplicate, sales
  duplicates, master duplicates), or separately reviewed check changes where the data are legitimate (F5 dealer
  invoicing is structural; F1 and F7 need an approved rule and a changed check; F3 needs a full product list or a
  changed check).
- What it guarantees, precisely: the strongest **global detected-failure** prerequisite available — before the flag
  may change, none of the implemented strict checks reports an INVALID problem in any core source. It does **not**
  prove that no contradiction exists. A non-FAIL result can still be OPEN: business questions, undeclared dates,
  totals and mappings, and evidence that was never inspected or is unknown (for example the nine `liste` checks the
  formula-aware scan does not run, R1 T2) — each of those still needs its own review. And no check can detect what
  the source does not show.
- Cost: no first-release metric can be certified until all core sources are clean or re-specified, including sources
  the candidate metrics never read (sales, customer master).

**Option B — a separately approved, independently tested per-metric release prerequisite.**
- §8.3 step 2 would be revised, by its own owner-authorized decision (D-xxx) and its own reviewed PR, so that the flag
  may change when the metric being promoted has no FAIL in its own dependencies — while the strict result is still
  computed, printed and stored unchanged and is never relabelled PASS.
- Minimum safeguards any such revision would need: every remaining strict FAIL explained, open in Veri Kontrol and
  owner-reviewed; none of them in the scoped dependencies of a promoted metric; every unscoped or affected metric stays
  withheld, proven by tests; all other release prerequisites (dates, declarations, approved definitions, regression
  tests, real-data reconciliation, Kredi step, app checks) unchanged.
- Cost: a weaker global guarantee — a metric could be shown while unrelated core sources still FAIL; it requires an
  explicit owner decision to accept that, and the safeguards above must be implemented and independently reviewed
  before use.

Neither option changes any business rule or makes a candidate certifiable on its own: under either, every row of §4
other than the strict-FAIL row still blocks all four metrics. Neither can certify business facts the exports do not
show or that are not documented — those rest on the owner's / accountant's recorded answers and the independent
real-data reconciliation.
