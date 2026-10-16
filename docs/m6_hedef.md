# M6-3 Hedef raporu importer

`services/importers/hedef.py` turns one explicitly chosen Hedef workbook into a validated, normalized,
in-memory account model with field-level confidence. It uses the M6-1 framework (`SourceSpec` +
`run_import`; header detection through the framework's reader) and adds no reader of its own. It is staged
foundation only: no actions, scoring, penetration, credit logic, territory analytics, API, UI, persistence
or M5 refresh dependency. Decision: D-037.

## File of record: not chosen

The owner has not decided which Hedef workbook is maintained. Several exist with the same layouts (for
example the Feb 2026 `hedef raporu.xlsx`, a June 2026 file and dated snapshots).

- `import_hedef(path, ...)` takes an **explicit path**.
- `find_hedef_candidates(folder)` only lists Hedef-looking files with their detected schema. Basenames
  are listed in name order, with no modification time, and nothing is selected.

## Supported schemas (exact)

| Variant | Columns | Yearly metrics present | Extra |
|---|---|---|---|
| `current_2026` | 45 | stamp total/direct/distributor 2021–2026; revenue net/direct/distributor 2022–2026 | — |
| `legacy_snapshot` | 56 | stamp total 2020–2025, direct/distributor 2021–2025; revenue net/direct/distributor 2020–2025 | 10 placeholder/target columns |

**Header detection:** a workbook must contain **every** header of exactly one variant, matched exactly on
header row 1 of the first sheet. Otherwise it fails closed (`unsupported_schema`), including when it
matches both variants or neither. Renamed columns are never guessed. Extra columns are ignored, and the
normalized output does not change.

The variant describes structure only. It says nothing about which file is authoritative.

The customer name column (`cari_unvan1`) is never read.

## Grain and identity

- **Grain:** one row is one account (`cari_kod`). A missing or blank code is fatal, and so is a duplicate
  code (`duplicate_account_code`). Duplicates are never separated by occurrence.
- **Identity:** `cari_kod` is the canonical / order-customer concept (D-003, role `order_customer`), not
  an invoice account. It is matched exactly against `liste.KODU` of an explicitly supplied sales workbook
  (`master_from_sales_workbook`). Without a master, accounts are staged with `master_link = None` and
  `customer_resolution = "not_supplied"`, never presented as resolved.
- **Codes are never trimmed.** Codes with surrounding whitespace are kept exactly and counted
  (`codes_with_surrounding_whitespace`, info). The whitespace policy is an open decision.
- **Record key:** `record_key("hedef_account", [cari_kod])`. It is independent of the variant, the file
  name, the row number and the name, so the same account has the same key across snapshots.

## Normalized account (`HedefAccount`)

Each value is a framework `NormalizedValue`, carrying the value, its confidence and its provenance.

| Field | Source header | Confidence |
|---|---|---|
| `customer_code` | `cari_kod` | `trusted` |
| `city` / `district` | `İl` / `İlce` | `unverified` |
| `sector_code` | `Sektör Kodu` | `unverified` |
| `hedef_rep` | `Temsilci` | `unverified` |
| `customer_rep` | `Müşteri Temsilcisi` (a different field, never merged) | `unverified` |
| `discount_tier` | `isk1` | `unverified` |
| `visit_count` | `Ziyaret Sayısı` | `incomplete` |
| `historical_weekly_stamp_capacity` | `Haftalık Kaşe Adedi` | **`stale`, always** |
| `historical_risk_limit` | `Risk` | **`stale`, always** |
| `historical_unsecured_credit_limit` | `Kredi` | **`stale`, always** |
| `yearly[metric][year]` | `<Y> Kaşe Adedi`, `<Y> Direkt Satış Kaşe Adedi`, `<Y> Dağıtıcı Üzerinden Kaşe Adedi`, `<Y> Net Ciro`, `DİREKT SATIŞ TUTARI <Y>`, `DAĞITICI ÜZERİNDEN SATIŞ TUTARI <Y>` | `unverified` |

The `yearly` metrics are named `stamp_qty`, `stamp_qty_direct`, `stamp_qty_distributor`,
`reported_net_revenue`, `reported_direct_revenue` and `reported_distributor_revenue`. Only the years the
variant really has are present; missing years are absent, never synthesized.

**Values:**
- A blank cell stays missing, never zero. A recorded zero stays zero.
- An Excel error or a type mismatch makes that cell `invalid`. These are warnings at cell level; the
  account and the field stay.
- Only `cari_kod` problems are fatal.

**Legacy placeholder/target columns:**
- The columns are `2015–2017_*` and `2020/2021 Hedef *`.
- They are required headers for schema identity and imported as `invalid`.
- They are never part of `HedefAccount`.

**Confidence is field-level, never one status for the workbook.** Parsing never upgrades it, and
source verification fails if a stale field ever leaves the importer as anything but `stale` (or
`invalid`).

## Stale and incomplete fields: restrictions

- **`historical_weekly_stamp_capacity`** is a historical reference only. No current capacity, market
  share, penetration, potential, expected volume or ranking may be computed from it.
- **`historical_risk_limit` / `historical_unsecured_credit_limit`** are historical references only. They
  are never current exposure or credit authority: no blocking, collection priority, risk labels, AI
  warnings or actions. Current credit decisions need current receivables and approved rules.
- **`visit_count`** is incomplete activity metadata. Zero may mean "not logged". It is never used to
  compare salespeople, score customers, infer "not contacted", or create follow-ups.

## Snapshot date

The workbook has no internal date. The rules, in order:

1. A declared `snapshot_date` (origin `declared`).
2. Otherwise, only when the caller opts in, a filename date. `DATED_SNAPSHOT_FILENAME` matches
   `DD-MM-YYYY …` (origin `filename`), and the import fails closed if the name doesn't carry it.
3. Otherwise the date is unknown (`snapshot_date_unknown`, warning).

Never the modification time, today's date, or the latest year column.

## Reconciliation with sales (optional, technical)

`reconcile_with_sales(result, sales_path, year=Y)` is a **technical identity/reconciliation check against
existing sales `Nettutar`**, not a revenue policy.

- **Hedef side:** accounts whose `<Y> Net Ciro` is non-zero.
- **Sales side:** the existing `Nettutar` summed by the **D-003 canonical customer** (`Pro_kodu`, or
  `Musterikod` only when `Pro_kodu` is empty).
- **Match rule:** exact within 0.01 TL. A tolerance of 0.5–5 TL changes the real result by one account,
  so no wider tolerance is used.
- **Output:** comparable accounts, exact matches, mismatches, missing sales side, missing Hedef side and
  the exact-match rate.
- **Diagnostic:** the same comparison by invoice account is reported separately and labelled; it never
  replaces the canonical key.
- **Coverage:** distinct sales customers present in Hedef.
- Hedef values are never modified, and downstream analytics must not consume these numbers.

## Assignment drift (optional, technical)

`assignment_drift(result, sales_path)` compares Hedef `Temsilci` with the master's current `TEMSİLCİ` for
the same code. It reports comparable, same, different, missing on either side and not resolvable, plus
whether `Temsilci` equals `Müşteri Temsilcisi`.

- `same` is exact text.
- The real Hedef file pads representative names with a trailing space, so a second, labelled count
  ignores surrounding whitespace. The values themselves are never changed.
- Neither field is chosen as the salesperson of record. There are no names and no performance metrics.

## Safe summary and privacy

`HedefResult.summary()` contains counts and structure only:

- schema, accounts, unique accounts, duplicates, whitespace codes;
- resolution status and match counts;
- snapshot date and its origin;
- field fill counts and field confidence;
- stale and visit coverage;
- the years available per metric;
- assignment completeness;
- the optional reconciliation and assignment aggregates;
- the validation report and verification results.

It never contains codes, names, representatives, cities, Risk/Kredi/capacity values, revenues or paths. A
verification check (`summary_has_no_identities`) fails the import if any code, representative or location
text appears in it.

## Deferred

- The Hedef file of record.
- The salesperson of record.
- The customer-code whitespace policy.
- Any use of Hedef in analytics, actions, Customer 360 or territory views (M7).
- Refresh integration and persistence: no Hedef file is required by `POST /api/context/refresh`.
