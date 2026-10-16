# M6-5 Trodat sales report importer

`services/importers/trodat.py` normalizes the Trodat sales report into **Trodat model × variant × month**
records. It uses the M6-1 framework: an explicit `MatrixLayout` with anchors and ranges, read by `run_import`.
There is no reader of its own and no heuristic scanning. Decision: D-039.

What stays out of it:
- Halsa SKU identity. The mapping is **not supplied** and never guessed.
- Customer attribution. The source has none.
- Any analytics, UI, API, actions, AI or refresh dependency.

## File of record

No maintained Trodat file or cadence is established, so import uses an **explicit path** only.
`find_trodat_candidates(folder)` lists Trodat-looking files with the year and month label in their names. It
never selects one: no modification time, no name order, no "latest-looking" choice.

## Supported layout

Verified on the Nov and Dec 2025 reports, which have identical layouts. The sheet is `Halsa_Turkey` and
nothing outside A1:P47 is read.

| Rows | Content | Used as |
|---|---|---|
| 1 | Month header in C–N (`Jan Feb March April May June July August Sep Oct Nov Dec`), `Total` in O | exact check |
| 2–27 | Section 1 model lines: code in A, optional variant in B, months in C–N, line total (formula) in O | detail |
| 28 / 29 | `Other Printy` / `Professional` lines | detail (family lines) |
| 30 | blank (inside section 1's SUM range) | must stay empty |
| 31 | `Total`: monthly `SUM(C2:C30)` …, annual `SUM(C31:N31)` | control (fatal) |
| 32–39 | Section 2 model lines, no title; rows 36–39 have display-pack variants (`displayx<N>`) | detail |
| 40 | `Total`: monthly sums with display packs × N; annual = sum of the line totals as written | control (warning only, see below) |
| 41 | ratios against the yearly series, plus `O31+O40` | not read |
| 42 | section title `Daters Numberers` | anchor |
| 43 | month header (`December` spelled out) | exact check |
| 44–45 / 46 / 47 | section 3 model lines / `Numberers` / `Stamp pads` | detail |
| 50–65 | yearly `<YYYY> TRODAT SALES` series (merged cells, decimal values: a different metric) | not read (outside bounds) |

**Anchors** (exact label at the declared cell; a moved, missing or repeated label fails):

| Label | Cell |
|---|---|
| `Other Printy` | A28 |
| `Professional` | A29 |
| `Total` | A31 |
| `Total` | A40 |
| `Daters Numberers` | A42 |
| `Numberers` | A46 |
| `Stamp pads` | A47 |

**Layout checks** — each of these fails the import:
- a month header differs, or anything appears in column P (an unexpected column);
- row 30 is not empty;
- a declared detail row is blank, or has values but no code.

M6-0 called this "seven TRODAT SALES sections". Inspection shows the seven `TRODAT SALES` rows are the yearly
series below the product matrix. They are not product families.

## Normalized model

**`TrodatLine`** has these fields:
- `line_key`;
- `line_kind`: `model` or `family_line`;
- `model_code`: the exact native code as an opaque string. Integer-looking codes stored as numbers become
  their decimal text, with a framework warning. Never coerced or trimmed.
- `variant`: column B as written, or None when blank. Never concatenated into the code.
- `line_label`, for family lines;
- `family`, `family_confidence`, `section`;
- `control_verified`, `source_row`;
- `halsa_sku`: always None.

**`TrodatModelMonth`** has these fields:
- `record_key`, `line_key`, `model_code`, `variant`, `family`, `report_year`, `month` (1–12);
- `reported_quantity`: a framework `NormalizedValue`.

Records are created only for non-blank month cells. A blank month is missing, never a zero record; a recorded
zero stays zero. Line totals and total rows never become records, and there is no 13th "Total" month.

**Families**, exactly as labelled, with no mapping to Halsa categories:

| Lines | Family | Confidence |
|---|---|---|
| `Other Printy` line | `printy` | trusted |
| `Professional` line | `professional` | trusted |
| `Numberers` line | `numberers` | trusted |
| `Stamp pads` line | `stamp_pads` | trusted |
| Section 3 model lines | `daters_numberers` (the section title) | trusted |
| Section 1 model lines | `printy` | **unverified** |
| Section 2 lines | none (`unlabelled`) | — |

Section 1's model lines carry no label; `printy` is inferred from the section's "Other Printy" residual line.
Section 2 carries no title in the workbook. No model × variant repeats across rows or sections; a repeat is
fatal (`duplicate_model`).

## Keys

- `line_key = record_key("trodat_model", [model_code, variant])`, or `record_key("trodat_line", [label])` for
  family lines.
- `record_key = record_key("trodat_model_month", [line_key, report_year, month])`.

Neither uses the row, file name, Python `hash()` or a Halsa SKU, so both are stable across exports for the same
identity, year and month.

## Year, month label and snapshot date

- **`report_year`:** the declared year (`import_trodat(path, report_year=...)`) wins. Otherwise the approved
  filename pattern `trodat sales report Halsa <MONTH> <YYYY> .xlsx` gives it (case-insensitive, the trailing
  space before `.xlsx` allowed, year 2000–2099). Otherwise the import fails (`report_year_unknown`). Never the
  modification time or the current year.
- **`filename_month_label`** (for example `NOVEMBER`) is a label only.
- **`snapshot_date`** stays unknown; a month word in the name is not a date.

## Reported value: semantics unverified

The value is named `reported_quantity` and always carries `unverified` confidence:
- The values look like whole quantities (100 % integer-like in both files), but their business meaning (units?)
  has not been confirmed.
- Totals reconciling does not upgrade it.
- Decimals are preserved, blanks stay missing, zero stays zero. No negative values exist, and no nonnegative
  rule is imposed.
- Display-pack lines are kept **as reported**: the number of displays, not expanded by the pack size.

## Total checks

All checks compare at ±0.01, rounded first, against the workbook's **cached** results. Formulas are never
recalculated, and a missing cache on a total is a framework error.

1. **Line totals (O):** each must equal the sum of its months, or, for a `displayx<N>` variant, that sum × N.
   The real workbook expands 3 of its 4 display lines. Anything else is fatal (`row_total_mismatch`).
2. **Section 1 total (row 31):** each month must equal the sum of lines 2–29, and the annual figure must equal
   the sum of the monthly totals. A mismatch is **fatal**.
3. **Section 2 total (row 40):** each month is checked against Σ value × pack size. The annual figure is
   checked against the sum of the line totals as written.
   - The real workbook's own section-2 controls are inconsistent. One line total is not pack-expanded while the
     others are, and one month's formula (two months in the December file) uses a multiplier that disagrees
     with its pack label.
   - Section 2 mismatches are therefore a **warning** (`section_total_inconsistent`), and section-2 lines carry
     `control_verified = False`. The detail cells themselves are literal values and are kept as reported.
4. **No grand total is used.** Row 41 mixes ratios against the yearly series with `O31+O40`, and section 3 has no
   total row.

## Halsa SKU mapping: deferred

0 Trodat model codes match Halsa `sto_kod` or stock codes (M6-0). Codes are never assigned, fuzzy-matched,
substring-matched, matched by name or matched by number similarity. The summary says
`halsa_sku_mapping = "not_supplied"`.

A future explicit mapping source should provide:
- Trodat model code, plus optional Trodat variant;
- one or more Halsa SKU codes;
- a mapping status;
- an effective date;
- provenance (who maintains it).

Who maintains it is an owner decision. Mapping is a downstream integration step and never blocks this import.

## Safe summary and privacy

**`TrodatResult.summary()` includes:**
- sheet, report year and its origin, filename month label, snapshot date;
- line counts: models, family lines, lines with variant, lines by section and family;
- record and cell counts: detail records, months with values, filled/blank/integer-like/decimal/invalid/formula
  cells;
- line-total checks, section-total checks;
- mapping status and the value-semantics note;
- the validation report and verification results.

**It never includes:** model codes, variants, quantities, workbook values or paths. A verification check fails
the import if a model code or variant appears in it. Model numbers are business data, so they stay in memory
only.

## Deferred

- Halsa SKU mapping and its maintainer.
- Confirming the unit meaning.
- The Trodat file of record and cadence.
- Trends, top or declining models, concentration.
- Cross-sell, fair product planning.
- The yearly `TRODAT SALES` series (a separate metric, if ever needed).
- Any UI, API or refresh integration.
