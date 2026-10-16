# M6 importer framework (M6-1)

`services/importers/` is the shared foundation for every M6 Excel source. It turns a workbook into
**validated, normalized, in-memory staging records**, or into a failed result with a safe report. It
is deterministic: no AI, no fuzzy matching, no recalculation, and no writes. It is not yet wired into
the live M5 context, the refresh or the API. Each source plugs in when its own checkpoint introduces it.

Source facts that shaped it: `docs/m6_source_inventory.md` (§17). Decision: D-035.

## Pipeline

```
SourceSpec ─► workbook inspection ─► validation report ─► normalized staging records
           ─► entity-resolution report ─► verification ─► ImportResult (staged | failed)
```

`run_import(path, spec, *, declared_date=None, masters=None) -> ImportResult`

1. **Inspect.** Check the file name against the spec's patterns (Unicode NFC, because macOS stores
   NFD names). Only `.xlsx`/`.xlsm` is accepted. External links are read from the package
   relationships. The workbook is opened read-only twice: once as formulas, once as cached values.
2. **Read** the declared sheet with the declared layout: a table at a fixed header row, or a matrix
   from explicit cells, anchors, ranges and sections.
3. **Normalize** each cell into a `NormalizedValue` and collect structural stats per field.
4. **Validate**: field policies (Excel errors, types, identifiers, missing caches, formulas, nulls),
   row rules and the business date.
5. **Fail closed.** Any `error` gives `ok=False`, `records=[]` and a `failed_step`. Warnings
   proceed only if `spec.allow_warnings` is true.
6. **Resolve entities** (exact match) for identifiers that declare a `master`, when that
   `MasterIndex` is supplied.
7. **Verify** the staged output: row count, unique keys, errors marked invalid, confidence never
   upgraded, `min_rows`, `max_null_rate`, business date present. A failure hides the records.

Modules: `models.py` (types and reports), `spec.py` (SourceSpec), `excel.py` (formula-aware
reading, errors, links), `validation.py` (cell normalization, header resolution, stats),
`entity_resolution.py`, `dates.py` (business date), `keys.py` (record keys), `base.py` (pipeline).

## SourceSpec

```python
SourceSpec(
    key="hedef", audience=Audience.SHARED,
    filename_patterns=("hedef raporu*.xlsx",), sheet="Sheet1",          # name, or a 0-based index
    layout=TableLayout(header_row=9),                                     # or MatrixLayout(...)
    fields=(
        FieldSpec("cari", FieldType.OPAQUE_ID, header="cari_kod", role=IdentifierRole.ORDER_CUSTOMER,
                  master="customer_master", pattern=r"\d{3}\.\d{2}\.\d{4}"),
        FieldSpec("tutar_1", FieldType.DECIMAL, header=Header("Tutar", 1)),  # duplicated header
        FieldSpec("haftalik", FieldType.INTEGER, header="Haftalık Kaşe\nAdedi ",
                  required=False, confidence=Confidence.STALE),
    ),
    default_confidence=Confidence.UNVERIFIED,
    business_date=DateRule.declared(),
    identity_fields=("cari",),
    rules=(RowRule.sum_identity("taksit", "taksit", ("anapara", "faiz", "vergi")),),
    verify=VerifySpec(min_rows=1),
)
```

(Illustrative only. Real source specs come with their own checkpoints.)

- **Headers** are matched exactly at the declared row: trailing spaces, embedded newlines, case and
  Unicode form all count. There are no pandas-style `.1` suffixes. A duplicated header must be
  selected with `Header(name, occurrence)`; without an occurrence it is an `ambiguous_header` error.
  Duplicates are always reported by column letter (`duplicate_header`, info).
- **No header search.** If the declared row doesn't hold the required headers, the import fails
  (`header_missing`).
- **Types:** `string`, `opaque_id`, `integer`, `decimal`, `date`, `boolean`. Typing is strict: no
  locale number parsing and no guessing of serial dates. `any` (added in M6-6 Bütçe) keeps the raw cell value
  without conversion, for validated-only regions with mixed content; errors, missing caches and external
  references are still classified.
- **Policies per field:**
  - `required` / `nullable`;
  - `missing_cache` (error | warning; the default comes from the spec);
  - `formulas` (allow | warn | forbid);
  - `tolerate_invalid_cells`;
  - `trim`, `pattern`, `placeholders` and `on_placeholder` for identifiers.
- **Matrix layout:** `MatrixLayout(bounds, anchors, ranges, sections)` reads only inside `bounds`.
  - An `Anchor(label, expected, search)` must appear exactly once, at `expected`. A moved label
    (`anchor_moved`), a missing one (`anchor_missing`) or a repeated one (`anchor_ambiguous`) is an
    error.
  - Fields point to a `cell`, or to an `anchor` plus an `offset`.
  - A `MatrixSection` checks its column labels exactly, including an unexpected extra column. It
    reads labeled rows until the first empty label, or exactly `rows` rows. It yields long records
    `(block, row_label, column_label, value)`.
  - A `MatrixRange` yields one record per cell.
  - Merged areas need no special handling: the value lives in the declared top-left cell.

## Confidence vs validity

These are two separate concepts.

- **Business confidence** (D-023, `Confidence`): `trusted`, `incomplete`, `stale`, `unverified`,
  `invalid`. It is declared per spec or field, and every `NormalizedValue` carries it.
  `usable_for_decisions` is false for `stale` and `invalid`.
- **Technical validity** comes from reading: the `CellKind` and the `error` code.

Parsing success never upgrades confidence. A perfectly parseable stale field stays `stale`, and
verification checks that no value ends up better than declared. Technical problems only downgrade:
an Excel error, a missing cache, a type mismatch or a malformed/placeholder identifier makes that
value `invalid`, with `value=None` and an `error` code. A blank cell is `None` with no error, and
`is_null` tells the two apart. An identifier stored as an integer is never `trusted`, because
leading zeros can't be verified.

**Invalid fields:**
- A required field with invalid cells is an error, and the import fails.
- An optional field with invalid cells is a warning plus `field_unusable`: the whole field becomes
  `invalid` for this import, and the rest of the import proceeds.
- `tolerate_invalid_cells=True` makes invalid cells cell-level warnings only. Only those cells are
  invalid; the field keeps its declared confidence. This is the honest model for DQ-4-like fields.
- A missing optional header is a warning, and the field is `invalid`.

## Excel formulas, errors and links

- Each cell is classified from both views:
  - `literal`, `error`, `empty`;
  - `formula_value`: the cached result of Excel's last calculation; `NormalizedValue.from_last_excel_calculation` is true;
  - `formula_missing_cache`;
  - `formula_error`.
- Python never recalculates. A missing cache follows the declared severity, and the value is
  `invalid`.
- **Excel errors:** `#REF!`, `#DIV/0!`, `#N/A`, `#VALUE!`, `#NAME?`, `#NUM!` and `#NULL!` are
  recognized by cell type. Other error types count as `#OTHER`. A text cell that merely reads
  "#N/A" stays literal text. Errors are never NaN. They are reported per field and region, with
  their type, count and severity (`excel_error`), and nothing from neighboring cells.
- **External links:**
  - Detected from the `externalLink` workbook relationships, reported by count and sanitized
    basename (no directories, no scheme).
  - Also detected from formula text: `[1]Sheet!A1` and `[Book.xlsx]` references count, structured
    table references don't. Formula references are counted (`external_formula_ref`).
  - Linked files are never opened.
  - Severity comes from `spec.external_links` (warning by default; Gider/Gelir uses error).
  - Every `NormalizedValue` carries `external_ref` (added in M6-6 Bütçe), so a source can apply a cell-level policy.
    Bütçe keeps the import going and marks each dependent cell `invalid`.

## Identifiers and entity resolution

- `opaque_id` values stay strings. Leading zeros, punctuation and long codes are preserved, and
  whitespace is trimmed only with `trim=True`.
- A float cell is `id_float` (invalid), because the code is already lost. An integer cell is
  accepted as text with an `id_numeric_cell` warning.
- `pattern` (an exact regex) and `placeholders` (regexes) are per source. Nothing is rejected
  globally.
- `MasterIndex.build(name, records, key_fields)` (or `.from_result(result, ...)`, which only
  accepts a successful import). Matching is exact string equality: no case folding, no fuzzy
  matching, no edit distance, no embeddings, no AI.
- **Statuses:**

  | Status | Meaning |
  |---|---|
  | `matched` | exactly one master record |
  | `unmatched` | no master record |
  | `ambiguous` | the value hits different master records through different key fields |
  | `null` | empty or invalid identifier, never looked up |
  | `duplicate_master` | the master key is not unique, so the row is never matched to one of them |

- `MatchReport` holds field, role, master, total, non_null, the counts and rates per status,
  master size, duplicate keys and invalid keys. It contains no codes or names. Resolved master
  rows stay internal (`NormalizedRecord.links`).
- **Roles:** `order_customer`, `invoice_account`, `product`, `loan`, `other`. Order source and
  invoice account share the customer master's key space but are different concepts.
- **DQ-5 seam:** `AttributeSource(attribute, master, master_field, master_key, via_role)` declares
  where an entity attribute (KAPALI, rep, sector) must come from. M5 `cust_attrs` is unchanged;
  DQ-5 is still open.

## Business date and provenance

- `DateRule` strategies:
  - `column_max(field)`
  - `snapshot_cell("B2")`
  - `bucket_headers("D1:H1")`: Turkish and English month labels or date cells; every cell in the
    range must parse
  - `declared()`: `declared_date` is required at import time
  - `from_filename(regex)`: opt-in only, with `year`/`month`(/`day`) groups
  - `none`
- File modification time is never used. Header year labels are not trusted globally.
- `BusinessDate.origin` is one of:
  - `literal`
  - `cached_formula`
  - `content`
  - `declared`
  - `filename`

  `granularity` is `day` or `month`.

## Rules and record keys

- `RowRule(name, fields, check, severity)` runs on rows whose inputs are all valid and non-null.
  `RowRule.sum_identity(...)` covers identities like "ordered = delivered + remaining + cancelled".
- A failure gives an aggregate `rule_failed` issue. Failing row indexes stay internal
  (`RuleResult.row_indexes`) and are never serialized. A rule that raises is counted as an
  exception and never leaks a traceback.
- `record_key(source, parts, occurrence)` is `<source>:<sha256[:20]>` over type-tagged identity
  parts plus the 1-based occurrence. There is no Python `hash()`, so keys are stable across
  processes for unchanged, identically ordered source rows.
- `keys_with_occurrence()` gives repeated identities distinct keys. Example: document × SKU in the
  sales report has no native line number.

## Privacy and reporting

`ValidationReport` / `ImportResult.summary()` are safe to print, log or return from a future API.
They contain:

- the spec key, audience, file **basename**, sheet and layout;
- row counts, issues and per-field stats;
- the external-link summary;
- the business date.

**Issues** contain:

- severity: `error` fails closed; `warning` may proceed; `info` is structure only;
- code;
- source and sheet;
- the spec field name;
- a region: cell, column letters or range;
- a template message;
- a count.

**Per-field stats** include rows, filled, null, fill rate, distinct, duplicates, raw type counts,
formula/error/missing-cache/invalid counts, string length range, identifier shapes (`9` = digit,
`A` = letter; top 5) and date min/max. There is no numeric min/max and no sample values.

Never included: cell values, codes, names, amounts, raw headers of duplicated columns, absolute
paths or tracebacks. A test serializes a report built from sentinel values and asserts that none of
them appear.

## How a source importer plugs in (M6-2 onward)

1. Add a small module, e.g. `services/importers/sources/<source>.py`, that declares the `SourceSpec`
   (exact headers from the inventory, header row, types, confidence per field, identity fields,
   rules, business-date strategy, audience).
2. Build a `MasterIndex` from the customer-master import (`liste.KODU`) when identifiers need
   resolving.
3. Call `run_import`. Use only `ok=True` results. Downstream code reads `NormalizedValue.confidence`
   and ignores values that aren't `usable_for_decisions`.
4. Add synthetic runtime fixtures (`tests/fixtures/xlsx_synth.py` can write cached formulas, errors
   and external links) that reproduce the source's quirks.
5. Integrate with the monthly refresh only in the checkpoint that introduces the source. M5 refresh
   still reads only its three M5 exports.
