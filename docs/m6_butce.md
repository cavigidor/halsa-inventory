# M6-6 Bütçe importer (owner / Yönetim)

`services/importers/butce.py` imports the Bütçe planning workbook into in-memory, **owner-only**, cell-level
facts plus a validation and coverage map. It answers four questions:
- which budget cells are structurally valid and usable;
- which are technically invalid;
- which depend on another workbook;
- what coverage the workbook actually provides.

It uses the M6-1 framework: one explicit matrix spec (`butce_spec`) read by `run_import`, orchestrated by
`import_butce`. There is no reader of its own. Nothing is recalculated, repaired, filled or persisted.
Decision: D-041.

What it never adds: budget-vs-actual, variance, profit, FX conversion, reconciliation with Gider/Gelir, a
dashboard, an API, actions, AI, or a refresh dependency.

## Audience and file selection

- **Audience:** `owner`. A verification check fails the import if the spec or the result is not owner. Nothing from
  this source reaches salesperson workflows, M5, actions, Customer 360, the dashboard, the API or AI evidence.
- **Explicit path only:** `import_butce(path, planning_year=None, snapshot_date=None)`.
- **`find_butce_candidates(folder)`:** lists each candidate's name, schema compatibility, status, Excel-error counts,
  external formula count and coverage. It never selects one. No mtime, no name order, no error-count preference.
  The file of record is an owner decision.

## Real files (read-only inspection, 2026-10-16)

| | `BUTCE_2026 Ocak.xlsx` | `BUTCE_2026 Taslak.xlsx` |
|---|---|---|
| Sheets | `2026`, `Sayfa1` | same |
| Used range | A1:ED141 (141 × 134) | same |
| Merged ranges | 175 | 175 (identical) |
| Labels | — | identical to Ocak (0 differences) |
| Cell-kind differences | — | 14 cells |
| Formula cells (cached) | 4,769 + 2 external | 4,773 + 2 external |
| Numeric literals | 1,718 | 1,710 |
| `#REF!` | 67 (incl. the 2 external cells) | 64 |
| `#DIV/0!` | 3 | 0 |
| External link | 1 → `2026_gider_gelir_TASLAK.xlsx` | same link; the 2 linked cells hold cached values |
| Missing caches | 0 | 0 |

Both use the same verified schema and both import. The differences: Ocak has January 2026 actuals that the draft
lacks, the draft has no `#DIV/0!`, and in Ocak the linked cells' cached values are `#REF!`. None of this makes
either file authoritative.

### Corrections to the M6-0 notes
- **Repeated labels:** the "repeated labels in column A" are the month rows (January … December, Total), repeated in
  each of **seven stacked sales blocks**. They are not a single table.
- **`$ kur` / `€ kur` are exchange-rate columns, not converted amounts.**
  - All 410 numeric values in block 1's rate columns lie between 0 and 200.
  - The Total row averages them instead of summing them.
  - Their order differs between scenarios: Forecast = (amount, `$ kur`, `€ kur`); GERCEK = (amount, `€ kur`, `$ kur`).
- **The "Other" column with most `#REF!`:** it is column BG. Row 5 labels BG `Other` above block 1, which explains
  the M6-0 note. Below block 1, BG is the **`Kümülatif sapma` (cumulative deviation)** column of the
  deviation/returns panel next to blocks 2–7. That accounts for 61 `#REF!`.
- **"An error affecting 2025 actual data":** the two `#DIV/0!` in the 2025 GERCEK columns sit in a derived
  growth-vs-last-year row (row 135), not in the 2025 actual monthly amounts.
- **The external dependency:** two formulas in row 1 (above the 2026 expense-forecast group) reference
  `ÖZET 2026` in the linked workbook, plus one formula adds them. The monthly 2026 expense-forecast cells reference
  row 2, not row 1 (inspection fact).

## Supported layout (`butce_2026_v1`)

**File name:** `BUTCE_<YYYY> <label>.xlsx`. **Sheet:** `<YYYY>`. **Bounds:** A1:ED141. Only planning year 2026 is
verified.

### Sales blocks
Title rows 4, 23, 42, 61, 80, 99 and 118, 19 rows apart. The seven blocks are:

| Block | Structural id | AL/AR/AX title |
|---|---|---|
| 1 | `total_sales` | `TOTAL SALES` |
| 2 | `total_sales_without_trotec` | `TOTAL SALES WITHOUT TROTEC` |
| 3 | `group_list_1` | a list of product/brand names (not committed; checked for consistency only) |
| 4 | `group_list_2` | a list of product/brand names (not committed; checked for consistency only) |
| 5 | `trodat` | `TRODAT` |
| 6 | `imalat` | `İMALAT` |
| 7 | `trotec` | `TROTEC` |

Each block has these rows (t = title row):
- **Title row t:** merged B:Y, Z:AK, AL:AQ, AR:AW, AX:BC. B and Z read `TOTAL SALES` in every block. AL, AR and AX
  carry the block title.
- **Segment row t+1:** 18 segments at B, E, H, … BA: `2018 Forecast`, `2018  GERCEK` (two spaces), … `2026  GERCEK`.
- **Unit row t+2:** `Months`, then three columns per segment: an amount column labelled `YTL` or `TL` (per block and
  scenario, kept verbatim) and the two rate columns.
- **Rows t+3 … t+14:** January … December. Row t+15 is `Total`. Rows t+16 … t+18 are derived (growth ratios,
  `geçen sene TL/USD`, `NET`).

### Expense area (BF4:ED19)
- **19 groups:** `2016 expenses`, then `<y> expens forecast` and `<y> expenses` for 2018–2026. Each group has
  Finance / Other / Total (`YTL`) by month (rows 7–18), a Total row (19), and a separator column.
- **Row 2:** unlabelled annual inputs above the groups.
- **Row 1:** the linked-workbook cells.

### BÜTÇE ÖZET panels (CK24:ED31)
Five panels at CL (2021), DB (2023), DJ (2024), DR (2025) and DZ (2026).
- **Panel year:** taken from the expense-group header in the same column (`<y> expenses`), and verified.
- **Columns:** `senaryo 1` and `senaryo 2`.
- **Lines:** `satış`, `satış maliyeti`, `kar`, `Giderler`, then `Kar` (2021, 2023) or `EBIT` (2024–2026).
- **Target label:** the 2024–2026 panels also carry a `TOPLAM SATIŞ HEDEFİ` label.

### Excluded or validated only
- **`Sayfa1`:** a small "Ocak – Ekim Arası USD" scratch summary by group. It is excluded and not read.
- **Validated and counted, not normalized:**
  - the duplicate header in rows 1–2;
  - derived rows;
  - rate copies in blocks 2–7;
  - Total-row rate columns (mixed averages and growth ratios);
  - rate growth (BD);
  - expense derived rows;
  - deviation/returns panels;
  - panel side cells (margins, unlabelled values);
  - notes (rows 137–141).

### Fail closed (structural)
The import fails, with no facts, when any of these hold:
- the planning sheet is missing;
- an anchor is missing or moved (each block's `TOTAL SALES`, `Months`, `January`, `Total`; the first and last
  expense groups; each panel title);
- any title, segment, unit, month or expense header cell differs from the template, or a cell expected blank holds
  a value (a value inside a merged header span counts);
- a group-list title is missing, inconsistent across AL/AR/AX, repeats another block's title, or matches the other
  group-list block;
- a panel label or panel year differs;
- the filename year and the declared year conflict, or the year is unknown or unsupported;
- the verification fails.

**Merged headers:** only the top-left cell holds the label. Blank cells inside a merge are expected blanks, not
missing headers.

## Normalized facts (`BudgetFact`)

| Region | Grain | Count (2026 template) |
|---|---|---|
| `sales_month` | block × segment (year, scenario) × month, amount column | 1,512 positions |
| `sales_total` | block × segment, Total row, amount column | 126 |
| `rate_month` | block 1 × segment × rate column (`$ kur` / `€ kur`) × month | 432 |
| `expense_month` | expense group (year, scenario) × component × month | 684 |
| `expense_total` | expense group × component, Total row | 57 |
| `expense_annual_input` | row 2 above an expense group (unlabelled) | 57 |
| `expense_linked_input` | row 1 above an expense group (linked-workbook row) | 57 |
| `budget_summary` | panel year × scenario × line | 50 |

That is 2,975 candidate value cells. Blank cells produce no fact: missing is never zero, and a recorded zero stays
zero.

Each fact carries:
- `record_key`;
- `region`, `block`, `year`, `scenario`, `month`;
- `value_type` (`amount` | `currency_rate`) and the verbatim `source_label` (`YTL`, `TL`, `$ kur`, `€ kur`);
- `component` (`finance` / `other` / `total`), `line_label` (internal);
- `value` (`NormalizedValue`: value, confidence, cell kind, error code, `external_ref`);
- `validity`, `external_dependency`, `title_conflict`;
- `source_cell` (internal provenance, never in the key);
- `cached_external_value` (internal only);
- `provenance` (`literal` | `cached_formula_result` | `excel_error` | `formula_missing_cache`).

### Scenarios (source-native)
- `Forecast` / `expens forecast` → `forecast`.
- `GERCEK` (gerçek = actual) → `actual`.
- `<y> expenses` → `unqualified`: the label carries no scenario word. It is probably actual, but that is not
  asserted.
- `senaryo 1` / `senaryo 2` → `senaryo_1` / `senaryo_2`.
- Forecast is never renamed to approved budget or plan.

### Repeated labels and block identity
Identity is structural: block (fixed position, verified title) + segment (year, scenario) + month, or group +
component + month, or panel + scenario + line. A month label alone is never identity.
- **Historical columns:** in blocks 2–7, the columns for 2018–2023 sit under the historical `TOTAL SALES` title
  cells (B, Z). Those facts carry `title_conflict=True`: 936 facts in the real files. The workbook's own formulas
  roll the group blocks up into blocks 2 and 1 for those columns too, but the semantics stay unverified.
- **Duplicate top header:** rows 1–2 repeat the header above block 1. In the real files it disagrees with block 1's
  header in 8 cells (4 forecast segments × 2 rate labels). Identity uses each block's own header rows; the
  conflicts are counted (info).

### Keys
`record_key("butce", [region, block, year, scenario, month, column/line identity])`. The column/line identity is the
rate label, the expense component or the panel line. Keys are the same across revisions (Ocak / Taslak). They
never use the file name, the revision label, the row number or Python `hash()`.

## Validity vs confidence (D-023)

| Cell | `validity` | Confidence | Usable |
|---|---|---|---|
| Numeric literal or cached formula result | `valid` | `unverified` (never `trusted`) | yes |
| Excel error (`#REF!`, `#DIV/0!`, `#N/A`, …) | `excel_error` | `invalid`, value `None`, code kept | no |
| Formula without a cached result | `missing_cache` | `invalid` | no |
| Non-numeric value in a value position | `type_mismatch` | `invalid` | no |
| Formula referencing another workbook | `external_dependency` (or `excel_error` if cached as an error) | `invalid` | no |

- **Invalid cells:** isolated per cell, never turned into zero or blank, never filled from neighbors, other periods,
  Gider/Gelir or a re-implemented formula. Neighboring cells are unaffected.
- **Formulas:** cached values from Excel's last calculation are used as-is (provenance `cached_formula_result`).
  Python never recalculates.
- **External dependency policy:**
  - The linked workbook is never opened, followed or resolved.
  - Each cell whose formula references another workbook is unusable.
  - Its cached value, if any, is kept separately in `cached_external_value`, internal only and never in a summary.
  - In the linked-input row, a group that holds any external reference is dependent as a whole. The formula that
    adds the linked cells is dependent too.
  - Unrelated cells stay usable, and the dependency is reported prominently: a warning, counts and regions.
- **Currency:**
  - `YTL`, `TL`, `$ kur` and `€ kur` are kept verbatim. The importer does not assert that `YTL` means today's TRY or
    which currency pair a rate is.
  - No conversion, no FX fetch, no TCMB.
  - Invalid rate cells stay invalid and are never used.

## Coverage and source status

**Per region:** `coverage_by_region` (normalized regions) and `validated_regions` (validated-only) each give these
counts:
- cells, blank, valid;
- literal, cached formula;
- Excel errors by type, missing caches, external references, type mismatches;
- usable, invalid.

The regions together cover all 18,894 cells exactly; this is verified.

**Totals:**
- candidate value cells, blank candidates;
- usable `unverified` facts, invalid facts by validity;
- invalid cells in validated regions;
- external formula cells, dependent facts and regions;
- `coverage_rate = usable / non-blank candidate cells`.

**`source_status`:**
- `complete`: no invalid cell anywhere and no external dependency.
- `partially_usable`: usable facts alongside invalid cells or dependencies.
- `unusable`: no usable fact.

The source is never authoritative (`authoritative = false`, `file_of_record = "unresolved"`). Status is separate from
D-023, so no new confidence state was added.

## Validation arithmetic (warnings or info only; values kept as recorded)

| Check | Severity | Ocak | Taslak |
|---|---|---|---|
| Sales Total row = Σ months | warning | 3 of 126 differ | 3 of 126 |
| Block roll-up (1 = 2 + 7; 2 = 3 + 4 + 5 + 6), per month | warning | 2 of 410 differ | 2 of 408 |
| Blocks 2–7 rate copies = block 1 | info | 8 of 2,460 differ | 8 of 2,460 |
| Expense Total row = Σ months | warning | 12 of 57 differ (some totals reference one month) | 6 of 57 |
| Expense Total = Finance + Other | warning | 0 of 236 | 0 of 235 |

## Real-file results (aggregates only)

| | Ocak | Taslak |
|---|---|---|
| Result | **staged**, `partially_usable` | **staged**, `partially_usable` |
| Candidate value cells | 2,975 | 2,975 |
| Blank candidates | 182 | 189 |
| Usable `unverified` facts | 2,790 | 2,783 |
| Invalid facts | 3 (the linked row: `#REF!`, external) | 3 (the linked row: external dependency, cached values kept aside) |
| Coverage rate | 0.9989 | 0.9989 |
| Invalid cells in validated regions | 67 | 64 |
| … deviation panels (BG, cumulative deviation) | 61 `#REF!` | 61 `#REF!` |
| … Total-row rate columns (growth formulas) | 3 `#REF!` + 1 `#DIV/0!` | 3 `#REF!` |
| … derived rows | 2 `#DIV/0!` (row 135, 2025 GERCEK growth) | 0 |
| Facts by region | sales month 1,457; sales total 126; rates 410; expense month 662; expense total 57; annual inputs 28; linked inputs 3; panels 50 | 1,452; 126; 410; 660; 57; 28; 3; 50 |
| Leak check | 0 leaks | 0 leaks |

**Read-only proof:** both originals have the same SHA-256 and mtime before and after. Nothing was saved or
recalculated, and the scratch copies on the Mac were deleted.

## Snapshot date and revision

- **Planning year:** declared, otherwise from the filename. A conflict fails. The sheet must be named after the year,
  so a `2027` filename against a `2026` sheet fails.
- **Revision label:** the filename label (`Ocak`, `Taslak`) is stored as `revision_label` and never parsed as a date.
- **Snapshot date:** `None` unless the owner declares one (`snapshot_date_origin = "declared"`). Never mtime or today.

## Privacy

**The safe summary contains structure and counts only:**
- schema, planning year and origin, revision label, snapshot status;
- sheets excluded, bounds, block ids, segment and group counts;
- cell-kind counts, errors by type, external counts, coverage by region, check counts;
- status.

**It never contains:**
- amounts, rates, totals or panel values;
- external cached values;
- the linked workbook's name (`names_withheld`);
- the group-list titles;
- absolute paths.

A verification check fails the import if any fact value (≥ 1,000, or non-integer ≥ 1), cached external value,
group-list title, link name or the file's directory appears in it.

## Future relationship with Gider/Gelir (not implemented)

Bütçe's linked cells point at `2026_gider_gelir_TASLAK.xlsx` (`ÖZET 2026`). Reconciling Bütçe with the Gider/Gelir
importer, or filling Bütçe gaps from it, is deliberately not done. Both files of record are unresolved, the link
targets a draft, the management mapping is open and the accounting basis is unverified.

## Deferred
- Budget vs actual, variance, forecast accuracy, profit, margin, FX-adjusted values.
- Reconciliation with Gider/Gelir or sales.
- The file of record (Ocak vs Taslak) and whether the dependency on the Gider/Gelir draft is intended.
- Templates for other planning years.
- Any UI, API, alert, action, AI use or refresh integration.
