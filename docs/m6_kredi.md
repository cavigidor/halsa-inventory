# Kredi takip importer (owner / Yönetim)

`services/importers/kredi.py` imports Halsa's **own bank-loan installments** from the ERP loan-contract export
into a validated, in-memory model. It uses the M6-1 framework (`SourceSpec` + `run_import`) and adds no reader
of its own. Decision: D-038.

This is company financing. It is **not** customer credit, Hedef `Risk` / `Kredi`, receivables or collections.

## Why now

This slice was pulled forward from M6-6 while M6-4 is blocked:

- **M6-4 (detailed collections)** has no `Evrak Detaylı Tahsilat Raporu` export. It is not approximated from
  the account-level Cari aging, and `TAHSİLAT ÖNCELİKLİ` is not implemented.
- **Trodat** still needs a model → SKU mapping.
- **Gider/gelir** still needs the business-line mapping.

Kredi depends on none of these. The roadmap numbering is unchanged (`docs/roadmap_m6_m7.md`).

## Audience

`Audience.OWNER` (D-023 §13). Nothing here may reach salesperson queues, actions, evidence packets,
Customer 360 or AI. No roles exist yet; the classification travels with the data. A source verification check
fails the import if the spec is ever not `owner`.

## Supported source

The ERP loan-contract export, `kredi sözleşmeleri.xlsx`:

- one sheet, header row 1, 23 exact headers, no formulas;
- one row per **loan-contract installment**;
- **only amortizing loans** (`KREDİ TİPİ = Tüketici kredisi modeli`).

The module checks content, not file names, and accepts only an **explicit path**. `find_kredi_candidates`
lists files and whether their headers fit. It never selects one and never uses modification time.

| | Supported? |
|---|---|
| `kredi sözleşmeleri.xlsx` (Feb 2026) | **Yes.** 188 installments, 25 contracts. |
| A June 2026 export with the same headers, found in an old transfer folder | **Passes** (amortizing only, identities hold). Which file is authoritative is not decided here. |
| `kredi borçları.xlsx` (Dec 2025) | **No. Fails closed.** Same headers, but it contains `Rotatif` (revolving) loans and many repeated contract × installment pairs whose due dates and amounts differ. Both identities fail on 37 / 51 of 489 rows. |

**`Rotatif` (revolving) loans** are a known type that this importer deliberately does not support. Amortizing
installment identities don't describe them, so they need their own rule set. An unknown loan type also fails
closed.

## Revised "kredi takip" layout (M8-ACCURACY-2, D-051)

On 2026-10-19 the owner described a private, hand-maintained revision of the export (`kredi takip(1).xlsx`). It
was **not opened in the cloud** (confidential; never sent to an AI provider). Support was built against synthetic
workbooks that copy its *structure* only (`tests/fixtures/kredi_takip_synth.py`). The owner verifies the real file
locally with `scripts/reconcile_sources.py` (below).

| | `erp_export` (original) | `filtered_takip` (revised) |
|---|---|---|
| Row 1 | the 23 headers | summary formulas only: `SUBTOTAL(…)` or a reference to the SUBTOTAL row |
| Headers | row 1 | row 2 (same 23 headers) |
| Installments | rows 2…n | rows 3…n |
| ERP control-total row | optional last row | required, after the installments |
| SUBTOTAL row | none | required, the last row (totals of the **visible** rows only) |
| Saved filter | none | AutoFilter on `VADE TARİHİ`; hidden rows |

**Layout detection** (`probe_layout`) is deterministic and fails closed:
- both row 1 and row 2 look like headers → `layout_ambiguous`;
- row 2 has the headers but row 1 is not all summary formulas, or the last row is not all `SUBTOTAL` →
  `layout_unsupported`; a row-1 formula of any other kind → `unsupported_summary_formula`;
- no headers in row 1 or row 2 → the strict original checks (`header_missing`).

**Rules for the revised layout:**
- Every installment row is imported and validated, **including the rows the saved filter hides**. Hiding is a view:
  each row carries `hidden_in_source`, and it is never read as "paid", "excluded" or "settled".
- The control-total row is checked against **all** installment rows (`control_totals_mismatch` is an error; a
  missing control row is `control_total_row_missing`). Neither the control row nor the SUBTOTAL row is ever counted
  as an installment.
- The SUBTOTAL row is checked separately against the **visible** rows (Excel `SUBTOTAL` functions 9/109, 2/102 and
  3/103 are emulated). A mismatch is a warning (`filtered_subtotal_mismatch`); a missing cached value is
  `filtered_subtotal_unverified`. Filtered totals never replace full-source totals.
- Contract × installment-number uniqueness, the arithmetic invariants and missing-cache / error-cell checks are the
  same as for the original layout.
- Exceptions are reported, never repaired: a negative `KALAN TAKSİT TUTARI` (`negative_remaining_amount`), any other
  negative amount (`negative_amount`), and `DURUM = Kapalı` with a positive remaining amount
  (`status_closed_with_remaining`, info). Kapalı is **not** read as paid; a negative residual is **not** zeroed.
- The summary adds layout, header row, saved-filter flag, filtered column names, hidden / visible counts, SUBTOTAL
  and row-1 check counts, and the exception counts — still no codes, names or amounts.

**Schedule views** (`services/kredi_schedule.py`, not displayed): year / month / lender / due-date breakdowns of
principal, interest, BSMV, KKDF, installment total, paid and remaining amount. The currency is always the first group
key, so currencies are never merged. A missing value makes its group total unknown (`None`) and is counted. Categories:
A all installments; B a user-defined filter (`select`; the saved filter is ignored unless `visible_only` is asked for
explicitly); C future with remaining > 0; D past due with remaining > 0; E due today with remaining > 0; F invalid or
contradictory (negative / unknown amounts, Kapalı with a positive remaining amount). A date-range total is a selection
total, not the company's complete debt, and a schedule is not a cash-flow forecast.

**Owner-local reconciliation** (read-only; never writes the workbook):

```
python -m scripts.reconcile_sources --kredi "data/kredi takip(1).xlsx" --today 2026-10-19 \
    [--snapshot-date YYYY-MM-DD] [--expect-sha256 <hash>] [--show-values] [--evidence-out [PATH]]
```

It prints the SHA-256 of the file and PASS / FAIL / OPEN checks: layout; full import (hidden rows included); saved
filter visible / hidden counts; control totals vs all rows; SUBTOTAL vs visible rows; installment and paid + remaining
identities; hidden rows that still record a positive remaining amount (past due / due today / future); negative
remaining; Kapalı with positive remaining; past-due and due-today positive remaining; snapshot date (OPEN unless
declared — the file time is shown as information only); currencies; visible rows per month (counts). Amounts appear
**only** with `--show-values`, on the local console. `--evidence-out` writes fingerprints, counts and results only;
without a path to the git-ignored `data/reconciliation/`, and an explicit path inside the repository must be under
`data/`. The last line is `GENEL SONUÇ: PASS / OPEN / FAIL`.
Exit codes: 0 = PASS, 1 = FAIL (a technical check failed), 2 = unreadable input / usage, 3 = OPEN (no technical failure,
business questions remain). A PASS is technical consistency, never financial approval.

**Still open for the revised workbook** (the owner's description, recorded without values): 11 installments with a
positive remaining amount in 2026 are outside the saved filter (10 due before 2026-10-19, 1 due on it); one Kapalı row
records a negative remaining amount; DURUM / Kapalı meaning; why those rows were excluded; which workbook is the file
of record; the business snapshot date; reconciliation against bank / payment records. The Kredi module stays withheld.

## Fields (SourceSpec `kredi_installments`)

| Normalized | Exact header | Confidence | Policy |
|---|---|---|---|
| `contract_code` | `SÖZLEŞME KODU` | trusted | required |
| `lender_code` | `BANKA KODU` | trusted | contract-level; lender names (`BANKA ADI`) are not imported |
| `loan_type` | `KREDİ TİPİ` | trusted | `amortizing`; `Rotatif` → unsupported; unknown → fail |
| `currency` | `DÖVİZ CİNSİ` | trusted | source code kept as written (TL today); no FX, not hard-coded |
| `closing_date` | `KAPANIŞ TARİHİ` | unverified | invalid cells are cell-level |
| `status` | `DURUM` | unverified | `Aktif`→`active`, `Kapalı`→`closed`, unknown → fail; never inferred from dates or balances |
| `original_amount` | `KREDİ TUTARI` | trusted | contract-level |
| `installment_count` | `TAKSİT SAYISI` | trusted | contract-level; `0` = not recorded |
| `installment_number` | `TAKSİT NO` | trusted | ≥ 1; ≤ count when the count is recorded |
| `due_date` | `VADE TARİHİ` | trusted | a true date; future dates are normal; never derived |
| `installment_total`, `principal`, `interest`, `bsmv`, `kkdf` | `TAKSİT TUTARI`, `TAKSİT ANAPARA`, `TAKSİT FAİZ`, `TAKSİT BSMV`, `TAKSİT KKDF` | trusted | arithmetic rule 1 |
| `remaining_principal` | `KALAN ANAPARA` | trusted | separate fact; blank stays missing, never zero, never derived |
| `payment_slip_series`, `payment_slip_number` | `ÖDEME FİŞİ SERİ NO`, `ÖDEME FİŞİ SIRA NO` | unverified | the number is `0` on every unpaid row in the real export (kept as written) |
| `payment_date` | `ÖDEME FİŞ TARİHİ` | trusted | blank on unpaid rows; never invented |
| `paid_amount`, `remaining_amount` | `ÖDENEN TAKSİT TUTARI`, `KALAN TAKSİT TUTARI` | trusted | arithmetic rule 2 |

Not imported: `AÇIKLAMA` (free text) and `BANKA ADI` (data minimization).

- **Formulas** are forbidden in all these columns.
- **Excel errors and type errors** in money, identity or date fields are fatal. In the informational fields
  (closing date, slip reference) they are cell-level `invalid`.

## Grain, keys and the "repeated pair"

- **Contract key:** `record_key("kredi_contract", [contract code])`.
- **Installment key:** `record_key("kredi_installment", [contract code, installment number])`.

Neither key uses lender, row, file name or Python `hash()`, so they are stable across unchanged snapshots.

**The repeated pair M6-0 reported was the export's control-total row.** The export ends with one total row:
its first cell is the number of installment rows, and its amount columns are the column sums. M6-0 counted it
as a 189th installment, which gave 26 "contracts", one pair without an installment number, and the "99.5 %"
remaining-principal fill.

The importer now handles it explicitly:

- **Detection:** the last row, with no installment number, lender, due date or type, and a first cell equal to
  the count of rows above.
- **Verification:** the column sums must match within 0.05 TL, otherwise the import fails
  (`control_totals_mismatch`).
- **Exclusion:** the row is never an installment and is skipped by both arithmetic rules.
- **Report side effects:** two framework warnings come from this row, a numeric code (`id_numeric_cell`) and an
  incomplete identity (`identity_incomplete`).

Among the real installment rows, contract × installment is **unique**. There is no native discriminator
anyway (the old Dec 2025 file shows what repeats look like: different due dates and amounts). So a repeated
pair is **fatal** (`duplicate_installment`). It is never separated by occurrence.

**Contract-level consistency:** lender, type, currency, original amount and installment count must agree
across a contract's rows, otherwise the import fails. Status is not checked here: in the June export it varies
between installments of one contract, so it is treated as row-level.

**Installment numbering:** exports start at the next due installment (2 of 25 contracts start at 1), so
continuity is not required. One real contract records `TAKSİT SAYISI = 0`. This is reported as "not recorded"
(info), and the upper-bound check is skipped for it.

## Arithmetic invariants (fatal)

Both identities are checked to ±0.01 TL. Values are rounded to 1e-6 before comparing, so float noise never
decides the boundary.

1. `TAKSİT TUTARI = TAKSİT ANAPARA + TAKSİT FAİZ + TAKSİT BSMV + TAKSİT KKDF`
2. `ÖDENEN TAKSİT TUTARI + KALAN TAKSİT TUTARI = TAKSİT TUTARI`

A failure is **source-fatal**, because a wrong installment or payment total must never reach future cash
planning. Nothing is repaired or recomputed. The report gives aggregate counts only.

## Dates

- **`latest_recorded_payment_date`** is the latest valid payment-slip date. It is an activity date, **not**
  the date the workbook was produced.
- **`snapshot_date`** is unknown unless declared (`import_kredi(path, snapshot_date=...)`, origin
  `declared`). Never the modification time, today's date or a due date.
- **Future due dates** are the point of the source and are accepted.

**Payment slips:**

- A blank slip on an unpaid row is valid.
- A paid amount without a slip date gives a warning (`paid_without_payment_date`).
- A slip date without a paid amount gives a warning (`payment_date_without_paid_amount`).

## Safe summary and privacy

`KrediResult.summary()` contains:

- rows read; whether the control-total row is present and matches;
- contracts, installments, duplicate identities;
- status counts, distinct lenders, currency codes, loan types;
- the due-date range and the count of installments due after the latest payment;
- the latest recorded payment date and the snapshot date with its origin;
- fill counts, rule checked/failed/skipped, invalid rows, contracts without a recorded installment count, and
  payment-slip anomalies;
- the validation report and the verification results.

It contains **no** contract codes, lender codes or names, amounts, totals, slip numbers or paths. A
verification check fails the import if a contract or lender code appears in it.

## Deferred

- Cash-planning metrics: next 30/60/90 days, monthly debt service, outstanding principal, interest burden,
  lender concentration.
- An Owner / Yönetim tab or API.
- FX.
- Revolving loans.
- Which Kredi export is authoritative (Feb 2026 vs June 2026 vs the owner's revised `kredi takip` workbook).
- Combination with receivables or other owner sources.

D-026 applies: only legitimate, recorded company financing is modeled.
