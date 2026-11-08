# Advanced V1 — the seven additional November sources (D-081)

Two private, offline, read-only reports. The **general** report (`services.father_report`) keeps Stock / Sales / B2B
and adds Hedef and Trodat; it never reads an owner or financial source. The **owner / financial** report
(`services.owner_report`, new) holds Cari, detailed collections, Kredi, Gider/Gelir and Bütçe. Every section is a
preliminary review surface ("İnceleme için / doğrulanmadı") with Turkish verification boxes; no metric is certified, no
action, priority, risk list, AI, server or database. The Classic and the earlier V1 + B2B reports are unchanged.

## Source → dashboard coverage

| Source (ERP Exports) | Role / flag | Section | State |
|---|---|---|---|
| `hedef.xlsx` | general `--hedef` | Hedef / Bölge | Values as recorded; Risk / Kredi limits and discount tier **not exported**; capacity labelled stale, visits possibly incomplete; not the file of record |
| `trodat sales report Halsa October 2026  .xlsx` | general `--trodat` | Trodat Model Satışları | Model × month as reported; year from the file name; units unverified; **no Halsa SKU mapping** |
| `yaşlandırma alacak.xlsx` | owner `--cari [--cari-date]` | Cari Bakiye (fatura yaşı) | D-071 / D-072 factual signed balances, invoice age (not overdue); file name has no date → undeclared unless `--cari-date` |
| `05.11.2026 Evrak Detaylı Tahsilat Raporu.xlsx` | owner `--collections` | Tahsilat — Hesap Özeti, Tahsilat — Evraklar | **New importer** (M6-4). Document facts reconciled; the red marker is the source's own rule, not a certified overdue result |
| `kredi takip.xlsx` | owner `--kredi [--kredi-date] [--as-of]` | Kredi — Ödeme Takvimi, Açık Taksitler | Existing importer and schedule; snapshot date undeclared; windows counted from `--as-of` |
| `2026_gider_gelir_EKİM_rev.xlsx` | owner `--gider-gelir` | Aylar, İş Kolları, Tutarsız Satırlar | Was refused; now three ratio rows are isolated (below) |
| `bütçe EKİM.xlsx` | owner `--butce --butce-year` | Bütçe — Hücreler | Was refused on its file name; now selected explicitly (no renaming); `partially_usable` |

Stock, Sales and the B2B registration list are not in that folder; the verified copies are reused (hashes below).

## Detailed collections (`services/importers/detailed_collections.py`)

Layout: one sheet of 23 exact headers (row 1), one open document line per row; a second sheet `Kolon Açıklamaları`
describes the columns. `Temsilci kodu` appears twice (D = customer card, Q = document) and is read by occurrence.

| Question | Finding (verified on the real file) |
|---|---|
| Invoice identity | `Evrak no` (`HALSA-…`, the sales document) + `Belge no` (legal/e-invoice no., blank on 23 rows). 4 `Evrak no` occur on two rows with different totals: kept as separate lines, never merged |
| Customer keys | `Hesap kodu` = the Cari / invoice account (149/149 present in the Cari export; account sums equal the Cari balance for 141). `Proje kodu` = the ordering customer, the dashboards' `Pro_kodu` (311/316 rows are a known order customer of that account). `Sipariş No` = the sales order `seri-sıra` (281/310) |
| Due dates | `Vade tarih` = `Evrak tarihi` + `Vade gün` (316/316). Column J (`05.11.2026 için vade`) = due date − report date (316/316) |
| Agreement information | `Sipariş Vadesi` is the payment term recorded on the sales order; W = order date + order term − report date (310/310 rows with an order). It differs from the invoice term on 17 rows. Six rows have no order: the export writes W = 0 there; the importer treats the order due date as **unknown** |
| Payment status | Only open items: `Meblağ` > 0 and ≤ `Evrak tutarı` (93 partly paid). `Ana döviz TL bakiye` = TL equivalent (1 EUR row). `Satır Bakiye` is a running balance (5 accounts deviate; informational) |
| Red indicator | One conditional format: `Hesap kodu` white-on-#C00000 when `AND($A2<>"",$W2<0)`. 155 rows on 102 accounts. Read from the workbook; a row is red only by that rule and only with an order date |
| Report date | File name `05.11.2026` and the J header must agree, else the import fails |
| Coverage | 149 of the 396 positive-balance customer (120) accounts in the Cari export: absence does not mean "no open invoices" |

Not done: overdue totals, collection priority (`TAHSİLAT ÖNCELİKLİ`), customer risk, ranking, actions or AI evidence.
Account rows are ordered by code; no grand total of red amounts.

## Gider/Gelir allocation rows

Previously one `allocation_ratio_sum` error rejected the whole workbook. Now the row is isolated (warning with the
Excel rows): its monthly expense totals stay in the month totals; its business-line allocations are kept as recorded,
listed separately and excluded from business-line totals. The web Yönetim view then shows the per-line allocated
expense as unknown (—, warning); the offline report shows the consistent-row sum labelled "EKSİK" next to an empty
"tam" column. Real file:

| Excel row | Ratio sum | Monthly totals | Sum of line allocations | Gap |
|---|---|---|---|---|
| 59 | 0.998366 | 470,723.13 | 469,953.99 | −769.14 |
| 82 | 1.002869 | 754,349.53 | 756,514.11 | +2,164.58 |
| 165 | 0.996694 | −27,591.94 | −27,500.73 | +91.21 |

Not rounding of a displayed percentage: in 10 of 12 months the allocations differ from the month total by total × (sum − 1).

## Source currency and units (PR #52 review)

The shared table renderer's `money` column always prints a lira sign and its `n` column rounds to two decimals. The
owner report therefore shows these values as text carrying the source unit (an opt-in `disp` column; the numeric value
still sorts and is unchanged; no conversion):

| Table | Value | Shown as |
|---|---|---|
| Tahsilat — Evraklar | `Evrak tutarı` (`Orj. döviz`) | original currency, e.g. `100,00 EUR`, `1.234,50 TL`; `Açık TL (TL karşılığı)` stays ₺ |
| Kredi — Ödeme Takvimi / Açık Taksitler | remaining, installment, principal | contract currency, e.g. `250.000,00 USD`; never summed across currencies |
| Bütçe — Hücreler | `ValueType.AMOUNT` | with the verbatim label `YTL` / `TL`; BÜTÇE ÖZET panel: no unit in the source, none shown |
| Bütçe — Hücreler | `ValueType.CURRENCY_RATE` | exchange rate, e.g. `2,5625 ($ kur)` — never an amount |

Every stored digit is shown (shortest round-trip decimal of the cached value). Cari balances, collections TL open
amounts, Gider/Gelir and the general report are unchanged (TL).

## Bütçe

The file-name pattern check is skipped by the existing explicit source-type mechanism (`explicit_source_type()`,
used by the Upload Center); the planning year must then be declared (`--butce-year`). Everything else is validated as
before. External link: one workbook (`2026_gider_gelir_TASLAK.xlsx` on the author's computer). Only side-panel header
cells DV1, DW1 use it (DX1 depends on them); their cached values are already `#REF!`, so nothing usable depends on the
link. The values next to them (row 2: 18,500,000 and 46,500,000) equal the supplied Gider/Gelir ÖZET budget column
(G44 + G50, and G51 minus that). Result: `partially_usable`, 99.9 % coverage, 2,870 usable cells, 3 invalid facts,
67 `#REF!` cells in validated-only regions; not authoritative, file of record unresolved.

## Other sources

- **Cari:** template approved; 838 accounts (788 `120`, 50 `320`), buckets up to `Kasım-2026`, USD column separate. No
  date in the file name; 141 of 149 accounts equal the 5 Nov collections totals (evidence of the same day, not proof).
- **Kredi:** 43 contracts, 412 installments, control totals match; 182 installments with a non-zero remaining amount,
  TL only; latest payment slip 2026-11-02; 1 negative remaining amount; snapshot date undeclared.
- **Hedef:** `current_2026` schema; 18,773 accounts, 0 duplicate codes, 7 codes with surrounding spaces; snapshot
  undeclared; 6,771 accounts are order customers in the sales file.
- **Trodat:** year 2026 from the file name (`October`); 36 model + 4 family lines, 400 values, 10 months.

## Generate (owner's Mac, private)

```sh
E="…/Halsa/ERP Exports"
STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled .venv/bin/python -m services.owner_report \
  --output reports/private/advanced-v1/Halsa_Kasim_2026_Yonetim_Finans.html \
  --cari "$E/yaşlandırma alacak.xlsx" --collections "$E/05.11.2026 Evrak Detaylı Tahsilat Raporu.xlsx" \
  --kredi "$E/kredi takip.xlsx" --gider-gelir "$E/2026_gider_gelir_EKİM_rev.xlsx" \
  --butce "$E/bütçe EKİM.xlsx" --butce-year 2026 --as-of 2026-11-05

STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled .venv/bin/python -m services.father_report --data <private Stock/Sales> \
  --output reports/private/advanced-v1/Halsa_Kasim_2026_Genel_Gelismis.html \
  --b2b --b2b-registration data/b2b_kullanici_rapor.xlsx --hedef "$E/hedef.xlsx" \
  --trodat "$E/trodat sales report Halsa October 2026  .xlsx"
```

Both refuse to overwrite. A source left out is shown as "Kaynak verilmedi" (never zero). `--cari-date` / `--kredi-date`
declare snapshot dates when known.

## November acceptance (counts only)

Run 2026-11-07 on the owner's Mac (isolated VM, Python 3.12.15, pandas 3.1.0, openpyxl 3.1.5, AI disabled) from this
branch's code (hash-checked copy). The seven ERP files and the Stock / Sales / B2B inputs were unchanged before and after
(ERP: `2df34df7…`, `ac44b17a…`, `5bb81091…`, `2831fa1f…`, `dc5eccd3…`, `e932111c…`, `ee948117…`; Stock `5cba8f55…`,
Sales `c23967fe…`, B2B list `dbad60b3…`).

| Section | Rows |
|---|---:|
| Cari Bakiye | 838 |
| Tahsilat — Hesap Özeti / Evraklar | 149 / 316 (red 155 rows, 102 accounts; invoice due passed 161) |
| Kredi — Açık Taksitler | 182 (TL) |
| Gider/Gelir — Aylar / İş Kolları / Tutarsız Satırlar | 10 / 4 / 3 |
| Bütçe — Hücreler | 2,873 |
| Hedef / Bölge | 18,773 (Net Ciro 2024–2026) |
| Trodat Model Satışları | 40 |
| B2B Takip (unchanged) | A 4,739 · B 1,142 · C 415 · Doğrulanacak 360 |

Independent openpyxl reconciliation (`verify_adv.py`, private; separate implementation): **101,668 checks, 0
discrepancies** — every Cari balance and oldest bucket; every collections document (open TL, days to due, order due
date, red / unknown, partly open) and account (counts, sums, red, oldest due, Cari match); Kredi open installments,
windows, past due and totals; Gider/Gelir month expense totals, isolated rows and partial line sums; every usable Bütçe
value against its cell; every Hedef Net Ciro, city and in-sales flag; every Trodat month value; no collections document
number, Kredi contract or owner section in the general file; all earlier general sections identical except review
metadata. Three checker mistakes were found and corrected during the run (date-like substrings, empty-string cities,
the sales file name's Unicode form); none was a report defect. Offline DOM (jsdom) on both real files: 50 + 76 checks,
0 JS errors, no external references. Real Safari / Chrome: **not performed** (no browser connected); Chromium ran on the
synthetic reports (`tests/test_advanced_v1_sources.py`).

| File (`reports/private/advanced-v1/`) | Bytes | SHA-256 |
|---|---:|---|
| `Halsa_Kasim_2026_Yonetim_Finans.html` | 1,264,812 | `565a8126edd499587ff632fb8de4b573b165278c9e4478dffdd4cf6bb887dc07` |
| `Halsa_Kasim_2026_Genel_Gelismis.html` | 22,033,122 | `40dbf854c46917830cdf040f96fb6c5ccf197955d3ebfe76053827cf796e7c04` |
| `Halsa_Kasim_2026_Yonetim_Finans_r2.html` (currency / unit correction; supersedes the first owner file for review) | 1,513,177 | `f9f2bad1fe8ce3714c879de3fdc42dc437439d3b31d5b52e318d1a3142b46ae3` |

The `_r2` owner file has the same sections, rows and source values as the first one; only the display fields of
Tahsilat — Evraklar, Kredi and Bütçe changed. Rendered in jsdom on every page and checked against the Excel sources read
independently (openpyxl): 12,941 checks, 0 failed (316 documents: 315 TL, 1 EUR; 2,873 Bütçe cells: 1,468 YTL and 927
TL amounts, 50 BÜTÇE ÖZET panel amounts without a unit, 214 `$ kur` and 214 `€ kur` rates; 182 Kredi installments, TL).
The general file was not regenerated: its data never uses the new column, and a synthetic old-vs-new comparison
differs only in three inert template lines.

## Complete, provisional, unsupported

- **Source-faithful and reconciled:** Cari balances, collections document facts and account sums, Kredi open
  installments and windows, Gider/Gelir month totals, Bütçe cell values, Hedef and Trodat values.
- **Provisional (meaning or date unconfirmed):** whether the red marker (order term) is the binding overdue rule; Cari
  and Kredi snapshot dates; Gider/Gelir business-line allocation (incomplete while three rows are inconsistent); Bütçe
  authority; Hedef authority and Net Ciro definition; Trodat units.
- **Unsupported here:** certified overdue amounts, collection priority, customer risk; budget-vs-actual comparison;
  management mapping of business lines; Trodat → Halsa SKU mapping; live-backend features (Customer 360, Günlük Satış,
  Fırsatlar, CRM, Upload Center). The web app does not read the collections report yet.

## Questions for the father

1. Is the order payment term (`Sipariş Vadesi`, the red marker) the binding agreement, or should the invoice term
   count? Are there agreements not recorded on orders?
2. Which filter produced the collections report (149 of 396 positive-balance customer accounts)?
3. On which day was the Cari aging report taken (no date in its name)?
4. Which term applies to the six documents without an order? Are the four document numbers on two rows separate bills?
5. Correct allocation ratios for Gider/Gelir rows 59, 82 and 165?
6. Which Bütçe file is the file of record; is the link to `2026_gider_gelir_TASLAK.xlsx` still needed?
7. On which day was `kredi takip.xlsx` taken; does `DURUM = Kapalı` mean paid?
8. Who maintains Hedef and when was it updated; what does its `Net Ciro` include?
9. Are Trodat values pieces or packs; who would maintain a model → Halsa stock code mapping?
