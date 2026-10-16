# M6-6 Gider/Gelir importer (owner / Yönetim)

`services/importers/gider_gelir.py` imports the useful accounting structure of the Gider/Gelir workbook into
validated, in-memory, **owner-only** models. It uses the M6-1 framework: three explicit specs (one table, two
matrices) read by `run_import`, orchestrated by `import_gider_gelir`. There is no reader of its own, nothing is
recalculated and nothing is persisted. Decision: D-040.

What it never adds: a management mapping, an owner dashboard, profitability analytics, fair economics, Bütçe, or
Detailed Collections.

## Audience and file selection

**Audience:** `owner` for every component. A verification check fails the import if any component is not owner.
Nothing from this source reaches salesperson views, actions, evidence packets, AI, the dashboard or the API.

**File selection:**
- **Explicit path only.** `find_gider_gelir_candidates(folder)` lists files with the year and revision label in
  their names and whether that year's template is verified. It never selects one; there is no file of record.

## Supported template (verified 2026 layout)

| Sheet | Used as | Layout |
|---|---|---|
| `<Y> GİDERLER TABLOSU` | **normalized** (expenses) | table, header row 1, 78 exact headers |
| `<Y> GELİRLER TABLOSU ` (trailing space) | **normalized** (sales / cost / gross profit) | matrix, A1:N35 |
| `ÖZET <Y>` | **normalized only where unique**, otherwise verification | matrix, A1:Y51 |
| `Sheet1` | **excluded** | a pasted copy of the ÖZET expense pivot; presentation only |

Only the 2026 template is verified, as `SUPPORTED_TEMPLATES = {2026: 2025}`.

**Other files found:**
- **2024 and 2025 files:** fail closed (`unsupported_template`). Their ÖZET has fewer historical columns, so a
  different layout.
- **2026 draft (`TASLAK`):** also fails closed. It lacks two accounting-entry rows (anchor drift).
- **2026 `mayıs` file:** passes.

### GİDERLER
- **One row per expense:** cost center (`GIDER YERİ`, exact text such as a 2-digit code + name) × expense name
  (`MASRAF İSMİ`). Rows run until the first row without a cost center.
  - That row is the **column-totals row**: it is verified against the expense rows and is fatal on mismatch.
  - Anything after it is a trailing note and is not imported.
- **Columns:**
  - budget `<Y>\nBÜTÇE`;
  - `<Y>\nTOPLAM` (verification only: it should equal the sum of the months);
  - historical `<Y-1>\nTOPLAM`, `<Y-2>\nTOPLAM`, `<Y-3>\nGERÇEK`, `<Y-4>\nGERÇEK`, `<Y-5> GERÇEK`;
  - allocation ratios `Orantı/ kırtasiye|Trodat|Trotec|İmalat`, plus their `TOPLAM`;
  - annual line allocations;
  - for each month, a total plus four line allocations.
- **Known source defect:** the monthly headers carry the template's previous year (`2025 Ocak` … in the 2026
  file). The exact template headers are verified, a warning `template_header_year_mismatch` is always recorded,
  only the **month** is read, and the year comes from the report-year rule. A header that changes (even a
  "corrected" one) is drift and fails.

### GELİRLER
- **Blocks:** four native-line blocks (`Kırtasiye`, `Trodat`, `Trotec`, `imalat` as written) plus a `TOPLAM` block.
  Each has a month header (`Ocak` … `Aralık`, `TOTAL`), then `Satış`, `Satış maliyeti`, `Brüt Kar` and a ratio row.
- **Normalized:** sales, cost and gross profit by line and month.
- **Controls only (fatal on mismatch):** the `TOPLAM` block must equal the sum of the four blocks, and each row's
  `TOTAL` must equal the sum of its months.
- **Ratio rows** (gross profit / cost) are not imported. Their `#DIV/0!` in empty months is expected, counted
  (`known_ratio_errors`) and never turned into 0 %.
- **No gross-profit identity:** `Brüt Kar` is **not** `Satış − Satış maliyeti` in many real months; it is entered
  separately. So no identity is enforced: differences are counted (info) and the source value is kept.

### ÖZET
- **Normalized (facts no other sheet has):**
  - historical yearly totals `TOPLAM <Y-5>…<Y-1>` for the labelled P&L lines (`Satış`, `Satış maliyeti`,
    `Brüt Kar`, `gider`, `NET KAR`, `KAR-ZARAR`) and the accounting-entry lines (`Kur farkı geliri/gideri`,
    `Amortisman`, `KK Gider`, `Reeskont Gideri/Geliri`);
  - the `Bütçe <Y>` column as `source_reported_budget` (not the separate Bütçe workbook);
  - report-year month, total and business-line values of the accounting-entry lines.
- **Verification only (never imported twice):**
  - The report-year P&L columns are formula references to GELİRLER. Equality is checked and fatal on mismatch.
  - The `gider` line equals the pasted expense pivot, which equals the GİDERLER totals by month, year, budget and
    line. Checked; a mismatch is a warning, because the pivot is pasted values.
  - The monthly identities `NET KAR = Brüt Kar − gider`, subtotal = Σ entries and `KAR-ZARAR = NET KAR + subtotal`
    are fatal on mismatch.
- **Not imported:** two unlabelled value rows (counted), the ratio row (`#DIV/0!` in empty months, isolated) and
  the share row.

## Report year and revision

1. A declared `report_year`. If it conflicts with the filename year, the import fails.
2. Otherwise the approved filename pattern `<YYYY>_gider_gelir_<label>.xlsx`.
3. Otherwise the import fails.

The year must match the workbook's own sheet names (`ÖZET <Y>`, `<Y> GİDERLER TABLOSU`, …) and ÖZET headers;
otherwise a component fails. It is never taken from the modification time, today's date or the broken expense
headers.

The filename label (`Eylül`, `mayıs`, `TASLAK`) is a revision label. `snapshot_date` stays unknown.

## Normalized models

**`ExpenseLine`** has these fields:
- `expense_key = record_key("gider_expense", [cost center, expense name])`;
- `cost_center` and `expense_name`, both internal only;
- the four `ratios`, `source_reported_budget`, historical yearly totals;
- `annual_total_matches_months`, `management_category = "not_mapped"`.

**`ExpenseMonth`** has these fields:
- `record_key = (expense, year, month)`;
- `reported_expense`;
- the four `allocations`, as the workbook's cached values (never recomputed);
- `allocations_match_ratios`.

**`RevenueRecord`** has these fields:
- `(native_line, metric ∈ reported_sales | reported_cost | reported_gross_profit, year, month)`;
- `value`, `management_category = "not_mapped"`.

**`SummaryRecord`** has these fields:
- `line_key` (the exact label) and `line_label` (internal);
- `period_kind ∈ historical_year_total | source_reported_budget | report_year_total | business_line_total | month`;
- `year`, `month`, `business_line`, `value`.

**Keys** never use the file name, the revision label, the row number or Python `hash()`.

## Native business lines

The lines are `kirtasiye`, `trodat`, `trotec` and `imalat`, kept separately with their exact source labels per
sheet.

There is **no management mapping** (kaşe / kırtasiye / other-import); that is an owner decision. The future
contract: native line → owner-defined management category, as a configuration layer. Its absence never fails the
import (`management_mapping = "not_supplied"`).

## Confidence

- **Structure is trusted:** periods, native lines, cost-center and expense-name identity.
- **All monetary values are `unverified`:** sales, cost, gross profit, expenses, allocations, historical totals and
  the source budget. Validation never upgrades them.
- **Accounting basis (cash vs accrual):** `unverified`. Metrics carry neutral names (`reported_*`).
- **Excel errors** in money cells and missing caches on required formulas are fatal. Ratio errors are isolated.

## Allocation validation (expense table)

| Check | Result |
|---|---|
| Four ratios present on every expense row | fatal if missing |
| Ratios sum to 1 within ±0.001 (the source stores rounded percentages; the real file deviates up to 0.001) | fatal on mismatch |
| Ratio outside 0–1 | **warning** (the real file uses one negative reallocation ratio deliberately) |
| Monthly allocation ≈ total × ratio (±0.01) | **warning** + per-month flag: the real file has manual overrides (23 cells in the Eylül file), kept as recorded |
| Annual line allocation = Σ monthly allocations | fatal on mismatch |
| `<Y>\nTOPLAM` = Σ months | **warning** + flag (1 real row differs) |
| Column-totals row = column sums | fatal on mismatch |

## Privacy

The safe summary contains structure and counts only:
- year, origin, revision label, snapshot status;
- sheets validated and excluded;
- record and line counts, native lines, historical years, months present;
- formula cells, Excel errors by component and field;
- allocation, annual and gross-profit check counts;
- cross-sheet check counts, unlabelled cells, trailing rows, external links;
- mapping and basis status;
- component reports and verification results.

It never contains expense names, cost centers, line labels, amounts or paths. A verification check fails the import
if a label or an amount (≥ 1,000) appears in it. External workbook links are **errors** for this owner source.

## Deferred

- The management mapping.
- Profitability, margin, variance, expense trends.
- Fair economics.
- Reconciliation with the sales export: the revenue basis is unresolved.
- Bütçe (separate checkpoint).
- Any UI, API or refresh integration.
- The 2024/2025 templates and the draft layout.
