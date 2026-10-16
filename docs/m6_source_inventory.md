# M6-0 — Source inventory and technical verification

_Status: complete (2026-10-13). Canonical technical inventory for M6. Docs only: no importer, no dashboard
module, no action, no AI._

**Method.** The real workbooks were opened **read-only** on the owner's Mac (inside the linked VM) with
pandas/openpyxl, by one-off scripts kept outside Git. The scripts printed **metadata only**: sheet names, header
rows, column names, types, fill rates, cardinalities, identities, match rates and error counts. They printed no
customer, person or amount values, and category labels matching a customer or person name were masked. Nothing
was sent to an AI provider. All 22 inspected files were hash-checked before and after (content hash, size and
mtime unchanged). The originals were not moved, renamed, repaired or saved.

**Privacy.** This document contains no customer, partner, salesperson, staff or bank names, no IDs and no
customer-level amounts. Where a header or label carries such names it is described generically (for example
"partner-named fair label").

---

## 1. Source map

| Role | Located file (pattern) | Status | Audience (D-021) | Proposed confidence (D-023) |
|---|---|---|---|---|
| A. Main sales 2020–2026 | `data/2020-<d> <month> satışlar.xlsx` (sheet `2020-2026 satışlar` + sheet `liste`) | **located** (current M5 source) | sales / shared | `trusted` (grain and identifiers verified below), with the formula-column caveat in §2.6 |
| A'. Customer master | sheet `liste` inside the sales workbook | **located** | sales / shared | `trusted` for code/status; `unverified` for SEKTÖR/TEMSİLCİ currency |
| B. Hedef raporu | `Downloads/hedef raporu.xlsx` (Feb 2026); dated snapshots `31-12-2025 Hedef raporu.xlsx`, `Hedef raporu Kasım 2025.xlsx` | **located** | sales / shared | mixed, see §5 (`stale` for Haftalık Kaşe / Risk / Kredi) |
| C. Evrak Detaylı Tahsilat Raporu | — | **not located during M6-0** | sales / shared | — |
| C'. Account aging (existing M5 source) | `data/Cari <date>.xlsx`; older `<date> alacak raporu.xlsx` | located | sales / shared | `trusted` as an account-level snapshot |
| D. Trodat sales report | `trodat sales report Halsa <MONTH YYYY> .xlsx` (latest found: November 2025) | **located (one month only)** | sales / shared | `unverified` (units, period, SKU mapping) |
| E. Gider / gelir | `Downloads/2026_gider_gelir_Eylül.xlsx` (+ older yearly versions) | **located** | **owner / Yönetim** | `unverified` until the business-line mapping and template issues (§8) are settled; error cells `invalid` |
| F. Kredi takip | no `kredi takip.xlsx`; the equivalent ERP export `Downloads/kredi sözleşmeleri.xlsx` (Feb 2026); older `kredi borçları.xlsx` | **located (different name)** | **owner / Yönetim** | `trusted` structure (identities hold 100%); a snapshot, so freshness must be checked |
| G. Bütçe | no `bütçe eylül.xlsx`; `Downloads/BUTCE_2026 Ocak.xlsx` and `BUTCE_2026 Taslak.xlsx` | **located (different names)** | **owner / Yönetim** | `invalid` (formula/reference errors, external dependency) |
| H. Fair summaries | `Downloads/2026 fuarlar özet.xlsx`; `2026 Antalya Fuar _ANALİZ_Sipariş Raporu.xlsx` | located | sales / shared (summary) | `unverified` |
| H'. Historical fair invitation / attendance lists | — | **Historical fair lists not located during M6-0.** | — | — |
| I. CRM export (visits/calls with customer IDs) | — | **not located during M6-0** | — | `incomplete` by owner statement (D-024) |
| I'. Call / lead log | `Dijital Pazarlama Raporu_2025.xlsx` | located | owner / management (person-level) | `incomplete`; not joinable to customers |
| (existing) Stock | `data/STOK <date>.xlsx` | located | sales / shared | `trusted` except the coverage column (§12, DQ-4) |

Searched only the two connected folders (Projects, Downloads; depth ≤ 5; spreadsheet extensions). Unrelated
spreadsheets (coursework, other projects) and payroll files were not opened. `kayit_detay.xls` was checked: it is
an 11-row, 2-column key/value sheet, not a CRM export. `rfs.xlsx` and `ffs.xlsx` are unrelated measurement data.
Older duplicates of the sales report (`2021-2026 satışlar.xlsx`, `31-12-2025 ürün ve müşteri bazlı satış
raporu.xlsx`, `müşteri bazlı ürün satış raporu …`) share the same column layout (the older ones lack the three
lookup columns) and are superseded by the current export.

---

## 2. A — Main sales report 2020–2026 (trusted core)

### 2.1 Structure
- Workbook: 2 sheets. `2020-2026 satışlar` (data) and `liste` (customer master, §3). No external links.
- Header row 1; **182,121 data rows**; 36 columns. Dates 2020-08-27 … 2026-10-05 (2020 is partial, from late
  August); no time component. Rows per year: about 26–37k per full year.
- Columns (exact names): `sip_evrakno_seri`, `sip_evrakno_sira`, `Musterikod`, `Musteri_ismi`, `Pro_kodu`,
  `ProjeIsmi`, `sto_kod`, `barkod`, `StokIsmi`, `Srmmerk`, `Tarih`, `miktar`, `Teslim`, `Kalan`, `Vazgecilen`,
  `Kalan_tutar`, `Birim_fiyat`, `brut_tutar`, `Bedelsiz_Haric_İskonto`, `BedelsizTutar`, `Nettutar`,
  `DevirMaliyeti_birim_fiyat_2022/2023`, `DevirMaliyeti_2022…2026`, `Net_Tutar_Maliyet_Dusulmus`, `kategori`,
  `anagrup`, `altgrup`, `Satici`, `Temsilci`, `Açık / Kapalı`, `Sektör`. The `sip_*` / `Srmmerk` / `Pro_kodu`
  names are typical of the ERP's sales-order table.

### 2.2 Row grain — **one row = one sales-order line (order document × SKU)**
Evidence (deterministic checks on all rows):
- Document key `sip_evrakno_seri` + `sip_evrakno_sira`: 38,215 documents, 27 distinct series, median 2 lines,
  p95 18 lines; 61.9 % of documents have more than one line.
- Within a document: one date (99.98 %), one invoice account (99.98 %), one order-source customer (99.94 %), one
  `Srmmerk` (100 %), one `Satici` (99.93 %).
- `document × sto_kod` is unique for 99.97 % of rows (0.03 % repeat a SKU in the same document; 0.02 % even with
  the same unit price). Exact full-row duplicates: 0.004 % (a handful of rows).
- Quantity identity `miktar = Teslim + Kalan + Vazgecilen` holds for **100 %** of rows. `Teslim = miktar` for
  99.82 %; `Kalan > 0` for 0.08 %; `Vazgecilen > 0` for 0.10 %. So `miktar` is the **ordered** quantity, with
  delivered / remaining / cancelled parts.
- Amount identities: `brut_tutar = miktar × Birim_fiyat` (100 %); `Nettutar ≤ brut_tutar` (100 %); `Nettutar =
  brut_tutar` for 34.6 % (the rest carry discounts); `Kalan_tutar = Kalan × Birim_fiyat` (99.87 %).
  `BedelsizTutar ≠ 0` on 24.6 % of rows (free goods); `Nettutar = 0` on 10.0 % of rows; no negative amounts.

**Conclusion:** sales-order lines with delivery status, not invoice lines. There is no invoice number and no
line-number column.

**Remaining ambiguity:**
1. Invoice linkage and VAT treatment cannot be verified from this file (`Nettutar` is net of discount; VAT
   inclusion is `unverified`).
2. `Nettutar` is based on the ordered quantity, so cancelled or undelivered quantities (about 0.1 % of rows)
   are inside it. Ordered vs delivered revenue basis is an owner/accounting decision (§16).
3. The few repeated `document × SKU` lines need a stable synthetic line key in M6-1, because there is no native
   line number.

SKU-level analysis is technically supported at order-line grain (`sto_kod` 100 % filled, 1:1 with `StokIsmi`).

### 2.3 Customer identifiers (D-003 confirmed, with one caveat)
| Field | Meaning | Fill | Distinct | Notes |
|---|---|---|---|---|
| `Pro_kodu` / `ProjeIsmi` | order-source customer (ERP "project" dimension) | 99.93 % | 6,747 | 1 name per code (100 %). Code shape `999.99.9999` for 6,747; a few malformed (4 short codes, 1 alphabetic placeholder, 1 cancelled-project placeholder, 1 with a trailing `*`) |
| `Musterikod` / `Musteri_ismi` | invoice account | 100 % | 5,485 | 1 name per code (100 %) |
| canonical (D-003: `Pro_kodu`, else `Musterikod`) | StockAgent customer | — | 6,754 | |

- `Pro_kodu = Musterikod` on 77.6 % of rows. **10.6 % of order-source codes are invoiced through more than one
  invoice account** (dealer/distributor invoicing). So the two IDs are distinct concepts and must never be
  treated as equivalent.
- **Caveat (DQ-5):** `Temsilci`, `Açık / Kapalı` and `Sektör` are **Excel formulas on every row** (546,363 formula
  cells): `IFNA(XLOOKUP(<Musterikod>, liste!KODU, liste!<col>), "…")`. They are attributes of the **invoice
  account**, looked up from the **current** customer master, not historical values. pandas reads Excel's cached
  results. StockAgent's `cust_attrs` applies them to the order-source customer (first/mean over that customer's
  rows), so KAPALI status, rep and sector of a `Pro_kodu` customer are inherited from its invoice accounts. M6-1
  should take these attributes from the master explicitly, keyed by a documented identity rule.

### 2.4 Product identifiers
- `sto_kod`: 100 % filled, 2,712 distinct, 1:1 with `StokIsmi`; `barkod` 92.6 % filled.
- vs the current stock export `Stok_Kodu`: 90.6 % of distinct sold SKUs exist in stock (98.9 % of rows); 74.2 % of
  stock codes were ever sold in 2020–2026. Unmatched sold SKUs are discontinued/removed codes: an M6-1 match-report
  item, not a fuzzy-matching need.
- Hierarchy: `kategori` 13 distinct (Giyotinler, Kalemler, Kazıma ve kesim plakaları, Kaşe üretim malzemeleri,
  Kaşeler, Lazerler, Makineler, Mürekkepler, Nümeratörler, Ofis ürünleri, Okul-Boyalar, Yapışkanlı Not kağıtları,
  and 1 more label withheld because it equals a customer name); `anagrup` 86; `altgrup` 144.

### 2.5 Activity / source field — **`Srmmerk`**
- 100 % filled, text, **99 distinct labels**, **one label per order document (100 %)**. So attribution is at
  order (document) level, and every line of the document inherits it.
- Label grammar: `<YYYY><EVENT><TYPE>[n]`, plus the default `GENEL` (no event).
  - TYPE: `FUAR` (88 labels), `ETKİNLİK` / `ETKİNLİĞİ` (6), `SAHA` / `SAHAÇALIŞMASI` (4, field campaigns), `GENEL` (1).
  - Label years 2020–2026; 38 distinct event stems, 23 of which recur across years; 2 labels carry a numeric
    suffix (a second event of the same stem in the same year).
  - **55 city/venue labels**, with stems ADANA, ADIYAMAN, ANKARA, ANTALYA, ANTEP, BURSA, DİYARBAKIR, ESKİŞEHİR,
    İZMİR, KAYSERİ, KONYA, KONYAYAZ, MARDİN, MERSİN, SİVAS, TÜYAP, URFA, VAN.
  - **43 partner-named labels**: events hosted with a dealer/partner company; the stems are company names, so they
    are not listed here.
- Spelling: no normalization collisions (case, spacing and Turkish letters are consistent). One spelling variant
  of the type token exists (`ETKİNLİK` vs `ETKİNLİĞİ`).
- Shares: 8.0 % of order documents carry an event label; 92.0 % are `GENEL`.
- **The label year is the event edition, not always the order year:** equal to the order year for 95.8 % of
  event documents; one year ahead for 127 documents (orders dated in the year before the edition), one year behind
  for 1.

### 2.6 Fair attribution — technical feasibility (D-025)
1. **Tagging:** yes. Event-tagged orders are identifiable deterministically (`Srmmerk ≠ GENEL`).
2. **Grain:** order document. Lines (SKU, quantity, value) and the customer come with it.
3. **Normalization:** yes, by rule. Parse the edition year, the event stem, the type (FUAR / ETKİNLİK / SAHA) and
   an optional sequence number. Owner input is needed only for taxonomy (§16).
4. **Consistency across years:** stems recur (23 of 38) with stable spelling; partner-named events must stay
   distinct from city fairs.
5. **Joins:** fair → order document → customer (100 % of event documents have a customer ID) → SKU (`sto_kod`) →
   stock/product.
6. **Follow-on sales:** technically measurable (customer IDs and order dates on every row). The window and the
   definition of "conversion" are owner decisions.
7. **Ambiguity:** whether ETKİNLİK/SAHA count as "fair"; edition year vs order date; no link to
   invitation/attendance (those lists were not located), so non-buying attendees are invisible.

### 2.7 Other fields
- `Satici`: order-level seller field, 24 distinct (11 active in 2026), consistent within a document (99.93 %).
- `Temsilci` (lookup from the master): 21 distinct (16 in 2026). Two different salesperson concepts.
- `Sektör` 77 distinct, 99.1 % filled (lookup). `Açık / Kapalı`: 2 values (open / KAPALI) (lookup).
- Cost columns (`DevirMaliyeti_*`, `Net_Tutar_Maliyet_Dusulmus`): partially filled; DQ-1 still applies (missing
  cost ≠ zero).

### 2.8 Data date / cadence
Snapshot with full history. The business date is `max(Tarih)` (already used by M5-5); the filename carries a date
label. Replaced monthly. Stale input is detected by comparing `max(Tarih)` with the live date (M5-5 rule).

**Not suitable for decision logic:** the cached lookup columns as historical facts; `Nettutar` as delivered
revenue without the ordered/delivered decision; cost-based margin where cost is missing.

---

## 3. A' — Customer master (`liste` sheet in the sales workbook)
- 18,725 rows; columns `KODU`, `ÜNVANI 1` (name), `MUH.KODU 1`, `SEKTÖR`, `ANA CARİ`, `CARİ FİRMA`, `TEMSİLCİ`.
- `KODU`: 99.95 % unique (about 10 duplicate codes). `MUH.KODU 1 = KODU` for 99.81 %. `ANA CARİ` is effectively
  empty (no parent-account hierarchy available). `CARİ FİRMA`: 2 values, the open/closed (KAPALI) flag. `SEKTÖR`
  70 % filled. `TEMSİLCİ` 94.6 % filled, 27 distinct.
- Coverage: 100 % of sales `Musterikod` and 99.56 % of `Pro_kodu` exist in `KODU`. So **one code space serves both
  invoice accounts and order-source customers**, and `liste` is the natural attribute source for M6-1.

## 4. Customer-identifier map (no values)
| Source | Native ID | Meaning | Proposed canonical mapping | Verification |
|---|---|---|---|---|
| Sales | `Pro_kodu` | order-source customer | canonical customer (D-003) | 99.93 % filled; 99.56 % in `liste.KODU` |
| Sales | `Musterikod` | invoice account | invoice account (separate concept) | 100 % in `liste.KODU`; differs from `Pro_kodu` on 22.4 % of rows |
| liste | `KODU` (= `MUH.KODU 1` 99.8 %) | account master code | key for both concepts | about 10 duplicates must be reported |
| Hedef | `cari_kod` | account code, one row per account | exact match to `KODU` | 100 % unique; 99.96 % in `liste`; revenue aggregated by order-source customer (§5) |
| Cari aging | `Hesap kodu` | receivable (invoice) account | exact match to `Musterikod`/`KODU` | 100 % unique per file; account level only |
| Trodat | none | — | no customer join possible | — |
| Fair order report (Antalya 2026) | none (product level) | — | — | — |
| Fair summary 2026 | event columns | per-event summary | — | not joinable to customers |
| Call log 2025 | none | per-staff daily counts | — | not joinable |
| Kredi / Gider-gelir / Bütçe | none (company-level) | — | — | — |

7 Hedef account codes have an 11-digit numeric shape. Importers must treat all codes as opaque strings, never
reformat them, and never display them outside the local app.

## 5. B — Hedef raporu
- Current file `hedef raporu.xlsx` (Feb 2026): 1 sheet, header row 1, 18,314 rows, 45 columns, **no formulas, no
  errors**. Older snapshots (Nov 2025, Dec 2025) have 56 columns: 2015–2017 columns and 2020–2021 target columns
  instead of the 2026 columns.
- **Grain:** one row per account (`cari_kod` unique 100 %), effectively the whole customer master with yearly
  aggregates.
- Columns: `cari_kod`, `cari_unvan1` (name), `Temsilci` (94 %, 23 distinct), `Müşteri Temsilcisi` (92.5 %,
  19 distinct; never equal to `Temsilci`, so a second role or representation), `Sektör Kodu` (70 %), `İl` (98 %,
  86), `İlce` (97 %), `isk1` (72 % filled, 5 distinct discount tiers), `Haftalık Kaşe Adedi`, yearly `<YYYY> Kaşe
  Adedi` + direct/distributor splits (2021–2026), yearly `<YYYY> Net Ciro` + `DİREKT SATIŞ TUTARI <YYYY>` +
  `DAĞITICI ÜZERİNDEN SATIŞ TUTARI <YYYY>` (2022–2026), `Risk`, `Kredi`, `Ziyaret Sayısı`.
- **Stale fields (status fixed by D-023; structure only verified here):**
  - `Haftalık Kaşe Adedi`: numeric, 64 % filled but **non-zero on only 9 %**, 50 distinct values (coarse estimates).
    **No freshness/date field accompanies it.** → `stale` (`historical_weekly_stamp_capacity`).
  - `Risk`: numeric, 99.6 % filled and non-zero (100 distinct). `Kredi`: numeric, 99.6 % (71 distinct). Populated
    almost everywhere, consistent with defaults, with no date. → `stale`, reference only. No current credit decision
    and no penetration calculation may use them.
  - Older snapshot columns `2015–2017_*` and `2020/2021 Hedef *` have 1–2 distinct values (placeholders) →
    `invalid` for analytics.
- **Identity check:** for the 2,232 accounts with 2025 revenue, Hedef `2025 Net Ciro` equals the sales 2025
  `Nettutar` sum **by order-source customer (`Pro_kodu`) for 89.4 %**, but by invoice account for only 68.2 %. So
  Hedef revenue follows D-003. The 10.6 % residual needs a reconciliation report in M6-3 (snapshot timing,
  ordered vs delivered, distributor splits).
- Assignment drift: Hedef `Temsilci` equals `liste.TEMSİLCİ` for the same code on 73.1 % of accounts.
- Coverage: 95 % of sales customers appear in Hedef.
- Data date: none inside; filename/snapshot only.
- Uses: territory/portfolio (İl/İlçe/sector), historical stamp volumes (direct vs distributor), visit counts
  (`Ziyaret Sayısı` non-zero 3.7 %, `incomplete`).

## 6. C — Collections
- **`Evrak Detaylı Tahsilat Raporu`: not located during M6-0.** Its grain, document IDs, due dates and overdue days
  cannot be verified yet.
- Existing account aging (`Cari <date>.xlsx`, the M5 source): 1 sheet, header row 1, 824 rows, **one row per
  account** (`Hesap kodu` unique), columns `Hesap kodu`, `Hesap adı`, `Temsilci kodu`, monthly buckets
  (`<Month-YYYY> ve öncesi`, then one column per month) and `Toplam Döviz Bakiye`. **No document, due-date or
  document-type fields.** The bucket headers carry month labels (a usable internal date signal). The older `<date>
  alacak raporu.xlsx` has the same layout.
- Deterministic inputs available today for a future `TAHSİLAT ÖNCELİKLİ` rule: overdue amount by aging bucket per
  account, the oldest non-zero bucket, account rep code, 2025–2026 revenue (sales), still ordering in 2026 (sales),
  KAPALI flag (`liste`). Missing until the detailed report arrives: document-level due dates, overdue days,
  document type, currency per document. Thresholds remain an owner decision.

## 7. D — Trodat sales report
- File (latest found Nov 2025): 1 sheet `Halsa_Turkey`, 66 rows × 17 columns, 46 merged ranges, 81 formulas (totals).
- Layout: a **matrix, not a table**. Column A: Trodat model numbers (shapes like `4910`, `4911`, `4910P4`), with a
  variant/pack suffix in column B (`P4`, `displayx8`, `displayx24`). Seven `TRODAT SALES` sections by product family
  (Printy, Professional, Daters, Numberers, Stamp pads, Other, Total). Columns Jan … Dec + Total; values in all
  12 month columns. 606 numeric cells, 89.9 % integers (units likely; `unverified`).
- **Row grain:** Trodat model (× variant) × month, for Halsa as a whole. **No customer identifiers, no Halsa SKU
  codes:** 0 of 50 model labels match `sto_kod`, stock codes or product names exactly. **A model → SKU mapping table
  is required** (M6-1 entity resolution + owner/technical input). The year comes from the filename only.
- Supports: model/family monthly trends and top/declining models (after units are confirmed). Customer × SKU
  analysis and cross-sell must come from the main sales report (order lines), not from this file. Fair product
  planning: only via the mapping.

## 8. E — Gider / gelir (owner / Yönetim)
- `2026_gider_gelir_Eylül.xlsx`: 4 sheets, no external links.
  - `ÖZET 2026`: header row 9; 41 P&L line labels (sales, cost, gross profit, expense, tax, net); columns `TOPLAM
    2021…2025`, `Bütçe 2026`, `TOPLAM 2026`, business lines **`KIRTASIYE`, `TRODAT`, `TROTEC`, `IMALAT`**, months
    `OCAK…ARALIK`, `TOPLAM`. 210 formulas; 3 `#DIV/0!` in 1 row (ratio row for empty months).
  - `Sheet1`: a pivot copy (no formulas).
  - `2026 GİDERLER TABLOSU`: header row 1; 178 expense rows; `GIDER YERİ` (26 coded cost centers), `MASRAF İSMİ`
    (177 distinct expense names, not reproduced here); yearly totals 2021–2026, `2026 BÜTÇE`; allocation ratios
    `Orantı/ kırtasiye|Trodat|Trotec|İmalat` that **sum to 1 on 100 % of rows**; monthly totals and per-line monthly
    allocations. 9,545 formulas, no errors. **The monthly headers say `2025 <Ay>` inside the 2026 file** (template
    carry-over), so the period must come from the filename or the ÖZET sheet, never from these headers.
  - `2026 GELİRLER TABLOSU`: 15 rows in 3 repeating blocks (sales / cost / gross profit) × months; 122 formulas;
    15 `#DIV/0!` in 5 rows (margin % of future months).
- Period grain: month (2026) plus yearly totals (2021–2025). Mostly formulas over hard-coded inputs (567 numbers in
  ÖZET, 2,650 in the expense table). Cash vs accrual: not discernible → `unverified`.
- **Business lines:** the source distinguishes four lines (KIRTASIYE, TRODAT, TROTEC, IMALAT). Mapping them to the
  owner's three views (kaşe/stamp, kırtasiye/stationery, other/import) is an owner decision (§16).
- Only legitimate recorded transactions are in scope (D-026).

## 9. F — Kredi takip (company financing; owner / Yönetim)
- `kredi sözleşmeleri.xlsx` (Feb 2026), the ERP loan-contract export: 1 sheet, header row 1, 189 rows, 23 columns,
  no formulas.
- **Row grain: loan installment** (`SÖZLEŞME KODU` × `TAKSİT NO` unique 99.47 %; 1 repeated pair to report). 26
  contracts, 5 lenders, currency TL only, type "Tüketici kredisi modeli" (amortizing), `DURUM` Aktif/Kapalı.
- Fields: `KREDİ TUTARI`, `TAKSİT SAYISI`, `TAKSİT NO`, `VADE TARİHİ` (2026-01 … 2027-07 in this export), `TAKSİT
  TUTARI` = `TAKSİT ANAPARA + TAKSİT FAİZ + TAKSİT BSMV + TAKSİT KKDF` (**100 %**), `KALAN ANAPARA` (99.5 %),
  `ÖDENEN TAKSİT TUTARI + KALAN TAKSİT TUTARI = TAKSİT TUTARI` (**100 %**), payment slip no/date (date filled on
  paid rows only).
- Snapshot date: no explicit field; the latest payment-slip date is the best internal signal. The older `kredi
  borçları.xlsx` (Dec 2025, includes `Rotatif` loans) satisfied these identities on only 89–92 % of rows: revolving
  loans need their own rules, and an importer must check the identities rather than assume them.
- Supports (later): next payment, 30/60/90-day obligations, remaining principal, interest and tax visibility.

## 10. G — Bütçe (owner / Yönetim) — `invalid` until validated
- `BUTCE_2026 Ocak.xlsx` (latest; `Taslak` is the earlier draft with the same layout): sheet `2026`, 141 × 134,
  4,839 formulas, 1,718 hard-coded numbers, **175 merged ranges**, header block in rows 5–6: `<YYYY> GERÇEK` /
  `<YYYY> Forecast` (2018–2025) × currency triplets (`YTL`, `$ kur`, `€ kur`) plus 2026 month columns. Column A:
  14 distinct labels repeated over 99 rows (block structure). A small `Sayfa1` (11 rows) is unrelated scratch.
- **Errors:** `#REF!` 67 + `#DIV/0!` 3 in 66 rows / 8 columns. 61 of them are in a single "Other" column, and
  the rest are scattered (Finance / Other / Total, one `2025 GERÇEK` cell, currency-rate cells). `Taslak`: 64
  `#REF!` in 2 columns.
- **External dependency:** 1 external link (2 formulas) to `2026_gider_gelir_TASLAK.xlsx`, so cached values depend
  on a separate workbook.
- Classification: rows/columns without errors or external references → technically valid (still `unverified`
  semantics); the "Other" column and error cells → `invalid`; the 2 external-reference cells → external
  dependency; the merged header area → unclear without a cell map.
- A Bütçe importer must: read cached values and formulas side by side; reject `#REF!` / `#DIV/0!` / any Excel
  error per cell; flag external references and missing cached values; work from an explicit cell/range map (labels
  repeat); report coverage and never fill gaps.

## 11. H — Fair-related files and historical fair lists
- `2026 fuarlar özet.xlsx`: 1 sheet, 86 × 31. **Per-event summary**: columns are 2026 events (city fairs,
  partner-hosted events, field campaigns; several headers are company or person names → never reproduced). 37 label
  rows / 13 distinct metric labels, 215 formulas, 10 `#DIV/0!` in 1 row. Not joinable to customers; events align
  with the 2026 `Srmmerk` labels by name (manual, not exact).
- `2026 Antalya Fuar _ANALİZ_Sipariş Raporu.xlsx`: 1 sheet, header row 11, about 590 product rows (`grup`, `KODU`,
  `İSMİ`, order quantity, remaining stock, gross/net order amount, current unit cost, list price, profit). **Product
  level, no customer column.** `KODU` matches sales `sto_kod` 100 %. 2,973 formulas, 590 of which reference an
  external workbook (costs), 1 external link, 29 `#DIV/0!`. The same information is derivable from sales lines with
  `Srmmerk = 2026ANTALYAFUAR`.
- **Historical fair lists not located during M6-0.** No invitation, attendance or invited-vs-ordered file was found
  in the connected folders. This is a source gap, not a blocker: M6-2 order-source attribution works from
  `Srmmerk` alone.

## 12. Existing stock export — data-quality finding (DQ-4)
- `STOK <date>.xlsx`: header row 4, 3,314 products, 89 columns; `Stok_Kodu` and `Ek iskonto %` each appear twice
  (pandas suffixes the second). 16,724 formulas; **2,477 error cells: 1,299 in `Birim Maliyet Rapor 2` and 1,178
  `#DIV/0!` in `STOK Kontrol %`** (coverage).
- M5 reads `STOK Kontrol %`. `m_stock` coerces errors to NaN and requires coverage to be present, so **1,178 products
  (36 %) are silently excluded from Azalan / Fazla Stok** (likely products without sales in the coverage window).
  Logged as DQ-4 (owner/technical decision); not changed in M6-0.

## 13. I — CRM / calls
- **No CRM export with customer IDs was located.** The owner's statement stands: CRM coverage is incomplete
  (D-024).
- `Dijital Pazarlama Raporu_2025.xlsx`: 13 sheets (12 months + total). Per day (`gün`) rows; column groups for
  channels (`Whats App`, `Web`) and **up to 5 staff members** (names in headers → not reproduced); metrics `Tel.
  Adet` (calls), `Açılan kayıt` (opened records), `Satış TL`. Layout varies between months (header row 2 or 3; group
  membership changes). No customer ID, no outcome, no next action.
- Usable for: channel-level lead/call volume context (WhatsApp/Web) only. **Not usable** for customer timeline,
  last contact or next follow-up, and **never** for person-level comparison (D-024).
- Required CRM fields for M7 (customer ID, salesperson, date/time, type, outcome, notes, next action/follow-up,
  contact status) remain to be supplied by an export.

## 14. Data date / cadence
| Source | Internal date | Filename date | Snapshot? | Rows span periods? | Cadence | Stale-input detection |
|---|---|---|---|---|---|---|
| Sales | `max(Tarih)` | yes | yes (full history) | yes (2020–) | monthly | `max(Tarih)` ≥ live date |
| liste | none | via sales file | yes | no | with sales | sales date |
| Hedef | none | sometimes | yes | yearly columns | irregular (Nov/Dec 2025, Feb 2026 seen) | latest year column with data + file date; must be declared on import |
| Cari aging | bucket headers (`<Month-YYYY>`) | yes | yes | buckets | monthly | last bucket month |
| Trodat | none | month/year | yes | 12 months | monthly | filename month; must be declared |
| Gider/gelir | none (headers unreliable) | month | yes | 2021–2026 | monthly | filename month; ÖZET month columns with data |
| Kredi | latest payment-slip date | no | yes | installments past + future | on demand | latest slip date |
| Bütçe | none | month/draft | yes | 2018–2026 | yearly + revisions | declared |
| Fair summaries | none | year | yes | per event | per fair season | declared |

Never infer recency from file modification time (as in M5-5).

## 15. Data-confidence inventory (D-023)
| Field / source | Status |
|---|---|
| Sales order lines (IDs, dates, quantities, `Nettutar`, `sto_kod`) | `trusted` (verified) |
| `Srmmerk` activity/source | `trusted` (verified: complete, document-consistent, parseable) |
| Sales `Temsilci` / `Açık / Kapalı` / `Sektör` (cached lookups) | `unverified` as history; use the `liste` master explicitly (DQ-5) |
| Sales cost columns / margin | `incomplete` (DQ-1) |
| liste `KODU`, `CARİ FİRMA` (open/closed) | `trusted` |
| liste `SEKTÖR`, `TEMSİLCİ` | `unverified` (70 % / 95 % filled; current assignment only) |
| Hedef yearly Kaşe Adedi / Net Ciro | `unverified` until reconciled with sales (89 % match) |
| Hedef `Haftalık Kaşe Adedi`, `Risk`, `Kredi` | `stale` |
| Hedef `Ziyaret Sayısı` | `incomplete` |
| Hedef old placeholder columns (2015–2017, 2020/21 targets) | `invalid` |
| Cari aging buckets | `trusted` (account snapshot) |
| Trodat matrix | `unverified` (units, mapping) |
| Gider/gelir values | `unverified`; `#DIV/0!` cells `invalid` |
| Kredi installments | `trusted` structure, after identity checks per import |
| Bütçe | `invalid` (error cells, external dependency); remaining cells `unverified` |
| Fair summary files | `unverified` |
| Call log | `incomplete`, not joinable |
| Stock `STOK Kontrol %` | `invalid` where `#DIV/0!` (DQ-4) |

## 16. Technically resolved vs owner decisions

**Technically resolved in M6-0:**
- Sales row grain (sales-order line).
- Customer identifiers and their relation.
- `liste` as the master.
- `Srmmerk` as the activity/source field, its grammar and its document-level consistency.
- SKU identifiers and stock coverage.
- Hedef grain, identity and stale-field structure.
- Kredi installment grain and identities.
- Gider/gelir sheet structure and allocation ratios.
- Bütçe error inventory and external dependency.
- Trodat matrix layout and lack of SKU match.
- Formula/cached-value behavior.
- Stock coverage errors.

**Owner decisions needed before later M6/M7:**
1. **Missing sources:** please provide the `Evrak Detaylı Tahsilat Raporu` export, a CRM export (if one exists),
   and the location of historical fair invitation/attendance lists. None was found in the connected folders.
2. **Fair taxonomy:** do `ETKİNLİK` and `SAHA` (field campaign) labels count as fair attribution, or as separate
   source types? What counts as a fair conversion (the order on the fair document only, or follow-on orders within
   a window)?
3. **Business-line mapping:** how gider/gelir's KIRTASIYE / TRODAT / TROTEC / IMALAT map to kaşe/stamp,
   kırtasiye/stationery and other/import.
4. **Revenue basis:** ordered net (`Nettutar`, which includes about 0.1 % cancelled or undelivered quantity) or
   delivered quantity × net price, for analytics.
5. **Salesperson of record for portfolio views:** order-level `Satici`, account `Temsilci` (master), or Hedef
   `Müşteri Temsilcisi`.
6. **Trodat:** who supplies or maintains the model → Halsa SKU mapping; whether reports newer than November 2025
   exist; whether the values are units.
7. **Hedef going forward:** which file is the maintained one (the Feb 2026 `hedef raporu.xlsx` vs dated snapshots)
   and how often it is refreshed.
8. **Bütçe:** which workbook is authoritative (`Ocak` vs `Taslak`), and whether its link to
   `2026_gider_gelir_TASLAK.xlsx` is intended.
9. **DQ-4:** how products with `#DIV/0!` coverage should be treated in stock lists.
10. **`TAHSİLAT ÖNCELİKLİ` thresholds** (already pending; needs the detailed collections report).

## 17. Requirements discovered for M6-1 (importer framework)
1. Per-source config: file pattern, sheet name or index, **header row** (1, 4, 9, 11 seen), required/optional
   columns with exact names, including trailing spaces, newlines inside headers and duplicate header names.
2. **Formula awareness:** read cached values and formulas together; detect formula columns; fail or warn when a
   formula cell has no cached value; record that values come from Excel's last calculation (XLOOKUP/IFNA in sales,
   external references in Bütçe and the fair report).
3. **Excel error detection per cell** (`#REF!`, `#DIV/0!`, `#N/A`, `#VALUE!`, `#NAME?`, `#NUM!`, `#NULL!`), mapped to
   `invalid` field status with counts. Never coerce silently to NaN (cf. DQ-4).
4. **External-link detection** (workbook relationships plus `[`-references) → dependency warning.
5. **Matrix/merged-cell sources** (Trodat, Bütçe, gider/gelir ÖZET, call log): an explicit cell/range map instead
   of a tabular reader; label-based section detection; reject layout drift.
6. **Snapshot business date** per source: from content where possible (sales `Tarih`, aging bucket headers, kredi
   slip date), otherwise declared at import; never from mtime; header year labels can be wrong (gider/gelir).
7. **Opaque string IDs:** keep leading zeros and shapes (`999.99.9999`, 11-digit codes); report malformed and
   placeholder codes (cancelled-project, `*`-suffixed, alphabetic) instead of joining them.
8. **Exact-match entity resolution with a match report** (matched / unmatched / ambiguous / null / duplicate),
   using `liste.KODU` as the master key for both invoice accounts and order-source customers. Duplicate master codes
   are reported. No fuzzy matching (Trodat models need an explicit mapping table, not fuzzy names).
9. **Identity rules declared per source:** order-source vs invoice account (10.6 % of order-source customers have
   several invoice accounts); attributes (KAPALI, rep, sector) taken from the master with a documented key (DQ-5).
10. **Synthetic line key** for the sales report (no native line number; 0.03 % repeated `document × SKU`).
11. **Identity checks as validation rules** where the source has them (sales quantity/amount identities, kredi
    installment identities); a failure is reported per row.
12. **Field-level confidence status** (D-023) declared per importer; `stale` / `invalid` fields cannot feed
    decisions.
13. **Audience tagging** per source (sales / shared vs owner / Yönetim); owner sources never reach salesperson
    views or packets.
14. **Fail-closed** with the M5-5 discipline: validate → build staging → verify → swap; reports that contain shapes
    only, never values.
15. **Synthetic fixtures** per source that reproduce the quirks above (header offsets, duplicate headers, formula
    cells with cached values, error cells, merged matrices, malformed codes).

## 18. M6 sequence — does it still make sense?
Keep the order **M6-1 → M6-2 → M6-3**: the framework first; `Srmmerk` is verified and ready (M6-2); Hedef is clean,
has no formulas and joins exactly (M6-3).

Two dependencies change *when* later slices can run, not their order:
- **M6-4 (detailed collections) is blocked on the missing export.** If it is still missing when reached, run the
  owner-source kredi importer (from M6-6) first. It is technically the cleanest source: installment grain,
  identities 100 %.
- **M6-5 (Trodat)** needs the model → SKU mapping decision and a current report before it is useful.

No reordering is made in this checkpoint.
