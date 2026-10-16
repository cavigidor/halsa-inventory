# M6-2 order activity/source normalization

`services/importers/sales_activity.py` turns the `Srmmerk` field of the main 2020–2026 sales report
into one deterministic source attribution per order document. It is the first source module on the
M6-1 framework (`docs/m6_importer_framework.md`): it declares `SourceSpec`s and calls `run_import`, with
no reader of its own. It is staged foundation only. It has no API, UI, actions, persistence or M5
integration. Decision: D-036.

## Raw field and grain

`Srmmerk` is the ERP sales order's activity/source label. It is 100 % filled, with one label per order
document (M6-0). The report is at sales-order **line** grain (order document × SKU). Attribution is at
**order-document** grain: lines are grouped by `sip_evrakno_seri` + `sip_evrakno_sira`. One order with ten
SKU lines is one attributed order, and its lines stay linked (`line_keys`) for later product analysis.

## Imported columns (SourceSpec `sales_activity`)

| Field | Exact header | Type | Policy |
|---|---|---|---|
| `series` | `sip_evrakno_seri` | opaque id | required, non-null |
| `number` | `sip_evrakno_sira` | integer | required, non-null |
| `order_customer` | `Pro_kodu` | opaque id | role `order_customer`, resolved against `liste.KODU` |
| `invoice_account` | `Musterikod` | opaque id | role `invoice_account`, resolved against `liste.KODU` |
| `sku` | `sto_kod` | opaque id | role `product` |
| `source_label` | `Srmmerk` | string | required, non-null |
| `order_date` | `Tarih` | date | business date = `max(Tarih)` |

Spec-wide settings:

- **Sheet:** index 0. The data sheet's name carries the year range and changes yearly. Header row 1.
- **Confidence:** all seven fields are `trusted`.
- **Formulas:** forbidden in these raw ERP columns.
- **Line identity:** `(series, number, sku)` plus the occurrence number.
- **Customer master (`sales_customer_master`):** the `liste` sheet of the same workbook, header `KODU`.

**Not imported, on purpose:**

- Amounts: the revenue basis is an owner decision.
- `Temsilci`, `Açık / Kapalı`, `Sektör`: invoice-account lookups (DQ-5).
- Names.

**Workbook selection:** `find_sales_workbook(folder)` requires exactly one `.xlsx` whose name matches
`*satış*.xlsx` (or the capitalized variants). It never picks by modification time. M5's selector is
unchanged.

## Parser grammar

```
GENEL                                   → general
<YYYY><stem><TYPE>[<sequence>]          → event
  YYYY      four digits, 2000–2099 (broad structural bound, not the years present today)
  stem      everything between the year and the type token; non-empty, kept exactly (opaque event
            identifier; may contain digits; one real stem starts with one)
  TYPE      matched from the right, longest token first:
            SAHAÇALIŞMASI → saha · ETKİNLİĞİ → etkinlik · ETKİNLİK → etkinlik · FUAR → fuar · SAHA → saha
  sequence  optional positive integer without a leading zero (a second edition of the stem that year)
```

- **What the parser does not do:** no case folding, no trimming, no transliteration, no name or city
  dictionaries, no fuzzy matching.
- **Unicode:** the label is NFC-normalized before matching, so a decomposed `İ`/`Ç` still matches the
  type token. The raw label is kept as written.
- **Whitespace:** inside the stem it is preserved. Leading or trailing whitespace on the label is a
  parse error.
- **Unparseable labels:** anything that doesn't fit is a parse error (`label_no_year`, `label_no_stem`,
  `label_unknown_type`, `label_whitespace`, `label_year_out_of_bounds`, `label_not_text`). An unknown
  label is never classed as general, fair or "other": the import fails closed, and the importer must be
  updated deliberately.

## Canonical source types

The types are `general`, `fuar`, `etkinlik` and `saha`. Only the spelling of the type token is
canonicalized (`ETKİNLİK`/`ETKİNLİĞİ`, `SAHA`/`SAHAÇALIŞMASI`). There is no `is_fair` flag.
`OrderSource.is_fuar` is the strict fact "the type token is FUAR". Whether ETKİNLİK or SAHA count as fairs
is an owner decision, configured later.

**Event identity:** `event_id = record_key("sales_event", [edition year, exact stem, canonical type,
sequence])`. The same stem in different years, different types, or different sequences gives different
events. Different stems are never merged.

## Normalized model (`OrderSource`, in memory)

- **Order:**
  - `order_key`: `record_key("sales_order", [series, number])`. Never the label, so a corrected
    attribution doesn't create a different physical order.
  - `series`, `number`.
- **Attribution:**
  - `source_type`, `edition_year`, `event_sequence`, `event_id`.
  - `raw_label` and `event_stem` are internal only.
  - `source_confidence` is `trusted`.
- **Date:** `order_date`, with `order_date_status` (ok | conflict | missing).
- **Customer:**
  - `order_customer` (`Pro_kodu`) and `invoice_account` (`Musterikod`), each kept only when consistent
    across the order's lines.
  - `customer` is the canonical D-003 customer: `Pro_kodu`, or `Musterikod` only when `Pro_kodu` is empty
    or whitespace. `customer_fallback` records which one was used.
  - `customer_status` (ok | conflict | missing).
  - `customer_link`: the exact-match `Resolution` against `liste.KODU`.
- **Lines:** `line_keys` are the M6-1 line keys (SHA-256 + occurrence; repeated document × SKU lines get
  distinct keys).

## Edition year vs order date

The edition year comes from `Srmmerk`; the order date comes from `Tarih`. They are independent facts and
neither is corrected from the other. Orders dated the year before the edition are normal. A mismatch is
counted as info (`edition_year_differs`, with offsets `+1` / `-1` / `other`), never rejected. The business
date is `max(Tarih)`, never an edition year, the file modification time or today's date.

## Fatal vs record-level

**Source-fatal:** `ok=False`, no orders or lines exposed.

- Any M6-1 framework error: a missing or renamed header, a blank `Srmmerk`, a missing or malformed order
  identity, Excel errors or formulas in these columns.
- Any unparseable label (`source_label_unparsed`).
- More than one label within an order (`source_label_conflict`).
- A failed source verification.

**Record-level:** the order is kept, the affected field is unusable, and the count is reported.

- Conflicting or missing dates: `order_date` is None (warning).
- Different customers across an order's lines: `customer` is None and the link is `null` (warning).
- More than one invoice account: info.
- D-003 fallback: info.
- Unmatched or duplicate-master customers: these appear only in the match report.
- Edition year ≠ order year: info, valid data.

## Safe summary and privacy

`OrderSourceResult.summary()` is safe to print, log or return from a future API. It contains:

- **Counts at order grain:** lines, orders, general and event orders, orders by type, distinct labels,
  distinct event stems, distinct events, orders with a sequence, parsed and unparsed orders and labels
  (unparsed by error code), label-conflict orders.
- **Edition years:** minimum, maximum and count, plus mismatch counts and offsets.
- **Record-level counts:** orders with an unusable date, customer fallback and conflicts.
- **The rest:** business date, the validation report, the framework and source verification results,
  and the match reports (line level for both roles, plus order level for the canonical customer).

It never contains labels, event stems (which can be partner company names), customer codes or names,
amounts, or paths. A source verification check (`summary_has_no_labels`) fails the import if any event
label or stem shows up in the summary.

## Intentionally not decided here

- Whether ETKİNLİK and SAHA count as fairs.
- What counts as a fair conversion, and any follow-on window.
- Revenue basis (ordered `Nettutar` vs delivered) and any event revenue, profit or ROI.
- Salesperson of record.
- Acquisition-source taxonomy: `Srmmerk` is order activity, not Google, referral and similar sources.
- Classifying stems (city fair vs partner event).
- DQ-5. M5 `cust_attrs` is unchanged.

## How M7 can consume it

Call `import_order_sources(find_sales_workbook(folder))` and use only `ok=True` results. Join paths:

- event → orders: group by `event_id`;
- order → customer: `customer`, with `customer_link.status == matched`;
- order → lines → SKU: `line_keys` into `lines`, then `sku`.

Apply the owner's taxonomy as configuration on top of `source_type` (for example "fair = {fuar}" or
"{fuar, etkinlik}"). Presentation of event names stays local to the app, never in logs or AI packets.
Refresh integration happens when a consuming feature is introduced.
