# M8-R1 — Business data reconciliation of the 5 October 2026 exports (evidence and diagnosis)

_2026-10-29. Evidence and diagnosis checkpoint. It changes no business rule, approves no figure, promotes no metric and
deploys nothing. `GENERATION_BOUND_API_EVIDENCE` stays False. Companion files: the prioritized issue-to-action table
`m8_r1_issue_actions.md` and the Turkish decision sheet `../m8_r1_babamdan_gereken_kararlar_tr.md`._

**Privacy:** counts, classes, schema labels (column headers / letters), check codes, dates and SHA-256 prefixes only.
No customer, account or product code or name, no transaction row and no amount is in this document. Row-level evidence
(the exact codes, Excel row numbers and order documents behind every count) stays on the owner's Mac in
`~/Desktop/ME/Projects/_halsa_r1_reconciliation/private/` (mode 0600, never committed, never sent to chat).

## 1. How it was run

| | |
|---|---|
| Code | `main` @ `2dc55b6` (after PR #33), fresh clone; no code changed |
| Where | Claude's isolated Linux workspace on the owner's Mac (not the owner's folders); Python 3.12.15, pandas 3.1.0, openpyxl 3.1.5 (same pandas / openpyxl as the CP3 acceptance) |
| Inputs | byte-identical, read-only (0400) copies of the three originals; `sources.json` selecting them explicitly with **no declaration and no approval** (the same state as the CP3 explicit upload) |
| Settings | `STOCKAGENT_NO_DOTENV=1`, `AI_PROVIDER=disabled`, no API key, no server, no SQLite written |
| Tools | `python -m scripts.reconcile_sources --core … --today 2026-10-29` (unchanged); two private read-only diagnostic scripts (in the private folder, not committed) that reuse the repository's own selection, validation, manifest, Veri Kontrol detector and gate, then count what is recorded. A privacy guard refuses to write the counts summary if any code or name from the data appears in it |
| Not touched | the owner's `StockAgent` checkout (`c2a3df5`), its `.env`, `data/`, any SQLite, the CP3 acceptance folder |

Originals (unchanged before and after; size and mtime identical to the CP3 record):

| Report | File | SHA-256 prefix | Size |
|---|---|---|---|
| Stok | `STOK 5 Ekim 2026.xlsx` | `63602ae3e8bf` | 1,657,601 |
| Satış (+ `liste`) | `2020-5 ekim satışlar.xlsx` | `be1d895e426b` | 40,293,157 |
| Cari | `Cari 5 Ekim 2026.xlsx` | `672fac2f5bf6` | 59,035 |

## 2. Reproduction — all four recorded counts reproduced exactly

| Recorded (CP3, explicit selection) | Now | Note |
|---|---|---|
| 7 technical reconciliation failures | **7** | `GENEL SONUÇ: FAIL`, exit 1 |
| 24 OPEN reconciliation items | **24** | Step 1's 29 = these 24 + 5 selection items (legacy mtime selection); unchanged explanation |
| 21 Veri Kontrol issues | **21** (14 high, 7 medium) | pure detection (`vkm.detect_core`) on this generation = 20 core issues + `kredi / missing_source` |
| 0 VERIFIED metrics | **0 of 33** | 28 `invalid`, 5 `not_verified` (`collections.detailed`, `owner.kredi`, `receivables.collections`, `scenario.receivables`, `trodat.sales`) |

No count differs, so there is nothing to explain away. Join counts are identical to Step 1 / CP3 (6,754 order customers,
5,485 invoice accounts, 824 Cari accounts, 2,713 sold vs 3,310 stock products, 40,704 order-source ≠ invoice-party rows,
123 D-003 fallback rows, 1 trim-only match).

## 3. The seven technical failures (authoritative enumeration = the script's check code + text)

`reconcile_sources` emits every source-evidence problem under the check code `<source>:evidence`; the seven FAIL
results are exactly the `INVALID` problems of `SourceEvidence.problems()`:

| # | Check code | Text | Field behind it | Count |
|---|---|---|---|---|
| F1 | `stock:evidence` | Kaynakta hatalı Excel formülleri var. | `error_cells` (formula-aware scan of the pipeline columns) | 1,178 `#DIV/0!`, all in AI `STOK Kontrol %` |
| F2 | `stock:evidence` | Kaynakta yinelenen kayıtlar var. | `duplicate_records` (product-code rows − distinct codes) | 1 |
| F3 | `stock:evidence` | Müşteri veya ürün eşleşmesi hatalı. | stock join: `sold_products_not_in_stock` / `stock_duplicate_codes` | 256 unmatched / 1 ambiguous |
| F4 | `sales:evidence` | Kaynakta yinelenen kayıtlar var. | `duplicate_records` (`DataFrame.duplicated()`, all 36 columns) | 4 |
| F5 | `sales:evidence` | Müşteri veya ürün eşleşmesi hatalı. | sales join: unmatched 30 + 1 + 256 = 287; ambiguous 712 + 0 + 1 + 0 + 7 = 720 | |
| F6 | `liste:evidence` | Müşteri veya ürün eşleşmesi hatalı. | master join: 30 order customers unmatched; 7 duplicate master codes | |
| F7 | `aging:evidence` | Müşteri veya ürün eşleşmesi hatalı. | Cari join: 45 accounts not among sales invoice accounts; 1 matching an order-source code only (DQ-6) | |

So the seven failures are **three join verdicts on different sources (F3, F5, F6, F7 — four)**, **two duplicate
verdicts (F2, F4)** and **one Excel-error verdict (F1)**. Several business topics feed more than one failure (the 256
unmatched products appear in F3 and F5; the 30 unmatched order customers in F5 and F6; the 7 master duplicates in F5
and F6), so resolving one topic can clear parts of several failures, and no single failure maps to one topic.

**Structural consequence (important for scope):** the sales join counts `order_customers_with_multiple_invoice_accounts`
(712) as *ambiguous*. Dealer invoicing (D-003: about 22 % of rows are invoiced to another party) is a legitimate,
permanent business structure, so **F5 can never pass** while that rule applies source-wide. Every sales metric that is
judged strictly (no `requires` declaration) is therefore INVALID regardless of data quality. Sales metrics can only
move once (a) their dependencies are declared after code tracing, as 2B did for four stock / Cari metrics, and (b) the
owner approves the identity rule they use (DQ-5 / Q10). This is the main reason no sales metric is a first candidate.

## 4. The 24 OPEN items

| Group | Items | Sources | Nature |
|---|---|---|---|
| Report date not declared | 4 | stock, sales, liste, aging | owner declaration at upload (§6) |
| Control totals not reconciled | 4 | all four | owner / accountant declaration bound to the file fingerprint |
| Mapping approval unknown | 4 | all four | owner approval bound to the file fingerprint |
| `liste` sheet not inspected | 9 | liste | **technical gap**: the formula-aware scan covers only the first sheet of each core workbook; `liste` gets join checks only, so external links, dates, headers, blanks, errors, caches, missing values and duplicates stay "denetlenmedi" |
| Duplicate header | 1 | stock | the two `Stok_Kodu` headers; D-056 resolves them for **scoped** metrics, but the strict source-level verdict still counts them (see T3 in the action table) |
| Footer partial | 1 | stock | column BA, see §5.1 |
| Blank amount cells | 1 | aging | **not a data gap**, see §5.7 |

## 5. Findings per discrepancy

### 5.1 Stock coverage `#DIV/0!` (DQ-4; F1)
- Formula (row 5, structure only): `STOK Kontrol %` (AI) `= AH / ((V + U + T) / 6)`, where T / U / V are
  `2024 / 2025 / 2026 Çıkış Miktari` and AH `Yeni sipariş ile stok adet = AF + AG + AC` (`siparis_dusulmus_miktar` +
  `sipariş` + `Yoldaki Ürünler`). The coverage numerator is therefore **not** `TOPLAM STOK MİKTAR` (W), and the
  denominator is a 2024–2026 outflow sum divided by 6 (2026 is year-to-date). What unit this ratio is in, and whether
  `minseviye` / `maxseviye` are in the same unit, is undocumented — it matters for every Azalan / Fazla Stok row, not
  only for the errors.
- 1,178 of 3,311 product rows are `#DIV/0!`; every one has zero outflow in T + U + V (undefined, not corrupt).
  By `TOPLAM STOK MİKTAR`: 472 positive, 706 zero, 0 negative. `DURUM`: 263 `ÜRETİMDEN KALKTI`, 915 blank.
  `minseviye` / `maxseviye` set (> 0) on 165. `Envanter değeri`: 416 positive, 760 zero, 2 negative.
- Against sales lines: 367 of the 1,178 were sold at some time 2020–2026; **one** has 2024–2026 sales lines — three
  2026 order lines with nothing delivered and nothing cancelled (open orders), which is consistent with zero outflow.
  Conversely 50 products with numeric coverage have no sales lines 2024–2026, so `Çıkış` includes movements other than
  sales orders (outflow ≠ sales).
- Column BA `Birim Maliyet Rapor 2 = AV / W` is `#DIV/0!` on 1,297 rows = exactly the rows with zero stock quantity;
  the two footer cells of BA propagate it (the footer OPEN). BA is not read by any metric; D-059 already keeps it from
  judging quantity-only metrics.
- **Not detected by any check, noted for the owner:** 3 product rows with a **negative** `TOPLAM STOK MİKTAR`
  (numeric coverage, blank `DURUM`) and one `DURUM` value that is a single `.`.

### 5.2 Duplicate stock code (F2; part of F3)
- One code on **two adjacent rows**, same product name; 74 of 90 columns identical, **16 different**: `2026 Çıkış
  Miktari`, `TOPLAM STOK MİKTAR`, `işmodern stok`, `siparis_dusulmus_miktar`, `Yeni sipariş ile stok adet`,
  `STOK Kontrol %`, `satış_2026`, `İrsaliye Net Çıkış değeri`, `Envanter değeri`, the cost / unit-cost / profit columns
  (BA, BC, BF, BG, BH, CC, CD). Both rows hold positive stock and numeric coverage; `DURUM` identical.
- The product is actively sold (134 sales lines, the latest in 2026).
- **Conflicting, not a repeated row.** Whether the two rows are two warehouses / lots to be added, or one of them is
  wrong, cannot be decided from the file. Nothing was merged.

### 5.3 Four duplicate sales rows (F4)
- 4 pairs of rows identical in all 36 columns (`rows_marked_by_pipeline` = 4), all dated 2022, in **3** order
  documents (one document holds two pairs). Each pair is on **adjacent** Excel rows and is the only occurrence of that
  document × product; the documents themselves have 24–47 lines and are **not** repeated as a whole. All are fully
  delivered (`Teslim = miktar`) with a positive amount; 2 of the 3 documents carry an event (`Srmmerk ≠ GENEL`) label.
- **Assessment:** the pattern does not look like a repeated export (that would repeat whole documents or blocks of
  rows). It is consistent with either a product entered twice on the same order in DinamikCRM (legitimate, both count)
  or a duplicated report line (one is spurious). Only the ERP order screen can tell. For context, 21 other
  document × product combinations repeat with **different** values (separate lines) and are not flagged.
- Share: 4 of 182,121 lines. Nothing was removed.

### 5.4 Duplicate customer-master codes (part of F5, F6)
- 7 `KODU` values appear twice in `liste` (18,725 rows; 3 rows have a blank `KODU` and nothing else — the reason the
  join report shows 18,722 master rows; 1 `KODU` is stored as a number, with no effect on any match found).
- **None of the 7 pairs is identical.** In all 7 the two rows disagree on `CARİ FİRMA` (open vs KAPALI) and
  `TEMSİLCİ`; in 6 also on `SEKTÖR`.
- Use: 6 of the 7 codes are both an order-source customer and an invoice account in sales (latest order year: 2026 ×3,
  2024, 2022, 2021); 1 appears in the Cari report; 1 is not used anywhere.
- D-042 never resolves a duplicate master code to one row, so the KAPALI status, sector and representative of these
  customers are unknown, not "open".

### 5.5 Customers and invoice accounts without a master record (part of F5, F6)
- **30 order-source customers** (D-003 canonical codes) are not in `liste`: all come from `Pro_kodu` (none from the
  `Musterikod` fallback), none matches after trimming, 3 have a non-standard code shape, and on **none** of their rows
  is `Pro_kodu = Musterikod`. Every one of their invoice accounts **is** in the master. 465 lines, of which 317 (68 %)
  carry an event label (23 % for all lines). Latest order year: 2021 ×13, 2022 ×7, 2023 ×2, 2024 ×2, 2025 ×5, 2026 ×1.
  The pattern suggests order-source "project" codes (fair / event / dealer projects) or customers removed from the
  master, but that is not established; it is an identity question for the sales team.
- **1 invoice account** is not in the master: its code carries surrounding whitespace and matches a master code exactly
  after trimming (1 line, 2024; not in the Cari report). This is the open whitespace policy (TASKS "Identity whitespace
  policy", Q10) and the single `matches_only_after_trim`.
- Whitespace (distinct codes, counted, never trimmed): 5 order-source codes, 6 invoice codes, 2 sold product codes, 3
  stock product codes (VKM `code_whitespace` = 14 codes, 1 customer affected).
- DQ-5 structure: 712 order customers with more than one invoice account, 173 invoice accounts serving more than one
  order customer, 40,704 rows where the order source differs from the invoice party.

### 5.6 Products sold but missing from stock (F3, part of F5)
- 256 distinct sold product codes are not in the stock export (1,950 lines = 1.07 % of all lines). Latest sale year:
  2021 ×85, 2022 ×86, 2023 ×22, 2024 ×53, 2025 ×10, **2026 ×0**. 63 were sold in 2024–2026; 9 have only zero-amount
  lines; 1 matches a stock code only after trimming.
- 853 stock products were never sold 2020–2026.
- Consistent with the export listing only current product cards, but D-057 forbids classifying a product as
  discontinued without source evidence. Only metrics that use the `sales_product_in_stock` join are affected
  (`stock.clearance_buyers` and strict sales metrics); `stock.reorder` / `stock.overstock` do not use it.

### 5.7 Cari accounts without a supported identity relationship (F7) and the Cari "blank amounts"
- 824 accounts: 796 with prefix `120`, 28 with prefix `320`. Balances (sum of the 11 month buckets): 386 positive,
  402 **negative**, 36 zero.
- **45 accounts are not sales invoice accounts:** 27 are `320` (of 28 in the file) and 18 are `120`. Balance signs:
  31 negative, 10 positive, 4 zero (`120`: 12 negative, 6 positive). All 45 exist in the master (44 open, 1 KAPALI).
  1 further account equals an order-source code but no invoice account (DQ-6).
- The `120 / 320` treatment and the meaning of negative balances are open business questions (D-057 lists 120 / 320).
- **"Tutar alanlarında boş hücreler var" (11 cells) is not a data gap.** All 824 account rows have every bucket
  filled. The sheet ends with two rows without an account code: one completely empty, one whose only cell is an empty
  text string in `Hesap adı`. The streaming scan treats any written cell as making a row a data row, so that row
  counts as one data row with 11 blank buckets (and the scan reports 825 data rows; the CP3 preview's "825" has the
  same cause). **Reproduced synthetically** on `main` (a trailing row with only `""` in column B → `data_rows + 1`,
  `blank_amount_cells` = number of buckets, `missing_required_values + 1`). Candidate technical correction T1.

### 5.8 Footer inconsistencies, mapping and approvals
- Stock footer: 3 report-control rows recognized (2 aggregate, 1 label), 94 cells checked, 92 matched, 0 contradicted,
  2 error-propagated (column BA, §5.1) → OPEN. No contradiction exists.
- Stock rows 1–3 above the header hold numbers and labels; **none is a date** (no report date inside the workbook).
- Cari: no formulas, no footer rows. Sales: no Excel errors, no formulas without a cached result and no invalid dates
  in the scanned columns, no external links (the cached `Temsilci` / `Açık / Kapalı` / `Sektör` lookups remain DQ-5).
- Mapping approval and totals declarations: none supplied for any file (`approval_status = not_supplied`); this keeps
  `mapping_approved` / `totals_consistent` unknown for every source. These are fingerprint-bound declarations the
  owner makes at upload; they are not something the data can establish.

## 6. Reporting dates — what the workbooks establish

| | Stok | Satış | Cari |
|---|---|---|---|
| Filename date | "5 Ekim 2026" | "2020-5 ekim" | "5 Ekim 2026" |
| Date inside the data | none (no date in the title rows or the header) | transactions 2020-08-27 … **2026-10-05** (Monday); 0 invalid dates; 370 lines in 2020 (from late August), 18,848 in 2026 | month buckets "Kasım-2025 ve öncesi" … **"Eylül-2026"** (no October bucket) |
| Workbook properties (`docProps/core.xml`) | modified 2026-10-06 06:00:12 UTC, saved by Microsoft Macintosh Excel; no creation date | modified 2026-10-06 05:44:34 UTC, Microsoft Macintosh Excel; no creation date | no properties part (not re-saved in Excel) |
| File time on the Mac | 2026-10-07 01:53 UTC (copy time) | same | same |

What this establishes and what it does not:
- **Transaction period (sales):** observed 2020-08-27 to 2026-10-05. That is an observation, not the report date.
  2020 starts in late August (contradicting the D-057 "1 January" convention for that year) and 2026 is year-to-date.
- **Whether 5 October is complete cannot be told from the data:** 71 lines on 2026-10-05; the previous nine working
  days had 69–174 (69 on 22 September, 89 on 24 September), so the count is low but within the recent range. The export
  time is not recorded, so a cut-off during the day is neither shown nor excluded.
- **The Excel save times (6 October, about 08:44 and 09:00 Türkiye time) are not export times**: the properties name
  Microsoft Macintosh Excel as the saving application, which indicates the two workbooks were saved in Excel after
  leaving the ERP; the time records that save. Information only, never a report date (D-051).
- **Stock snapshot date: unknown.** Nothing in the stock workbook dates its quantities.
- **Cari snapshot date: unknown, and the filename may not be the as-of date:** the newest bucket is September 2026. If
  the balances were taken on 5 October, invoices of 1–5 October would need an October bucket; either the report is as
  of 30 September, October items are excluded, or buckets are by due month. Unresolved.
- **Completed sales period: unknown.** Whether 2026-10-05 is complete, and whether 2020 is complete from the ERP start,
  is not established.

All dates therefore stay **undeclared** (unknown). The questions for the person who exports the reports are in the
decision sheet, group A.

## 7. Rules already established (preserved; nothing here changes them)

| Decision | What it already settles for these findings |
|---|---|
| D-003 | Canonical customer = `Pro_kodu`, fallback `Musterikod` only when empty; risk / receivables use the invoice account. The 30 unmatched order customers are D-003 identities; their invoice accounts are not substituted. |
| D-023 / D-026 | Field confidence; legitimate recorded transactions only. Duplicate rows are not deleted on suspicion. |
| D-042 | Codes never trimmed or fuzzy-matched; master attributes from `liste` by exact code; a duplicate master code is never resolved to one row; receivables stay at invoice-account grain and are never allocated. |
| D-049 | No verified evidence, no business conclusion. |
| D-051 | Dates are declared, never inferred from file times or the latest transaction. |
| D-055 | Report-control rows recognized only by structure; product count = product-code rows; scoped judgement. |
| D-056 | Stock column A = product code, J = country of origin (column identity only; the duplicated code stays open). |
| D-057 | Owner conventions (staff export, owner uploads, fixed layouts, period from 1 January) — not evidence; open: duplicated stock code, four 2022 sales rows, 120 / 320, unknown dates. |
| D-058 / D-059 | Partial footer is OPEN; column footers judged per field (BA cannot block quantity metrics). |
| D-062 – D-064 | Veri Kontrol records issues and reviews; a review never certifies a metric or deletes a duplicate. |
| D-065 – D-067 | Upload: report type and dates declared by the owner per file, never inferred. |

## 8. Classification summary

| Class | Findings |
|---|---|
| Source-data inconsistency | conflicting duplicate stock code (5.2); 7 conflicting master duplicates (5.4); 4 identical sales pairs, pending ERP check (5.3); 3 negative stock quantities, one `.` DURUM, 2 negative inventory values (5.1); 3 blank master rows (5.4) |
| Identity / mapping issue | 30 order-source codes without master (5.5); 256 sold products not in stock (5.6); 45 Cari accounts not sales invoice accounts incl. 27 `320` (5.7); DQ-5 / DQ-6 structure; whitespace codes |
| Business-rule decision | DQ-4 rule and coverage meaning (5.1); how to treat the duplicate stock rows, the sales pairs and the master conflicts once explained; 120 / 320 and negative balances; attribute source (DQ-5, Q10); whitespace policy; report dates; revenue basis (Q3, unchanged) |
| Possible code defect / technical gap | T1 aging trailing empty-text row counted as blank amounts (reproduced); T2 `liste` sheet not formula-inspected (9 OPEN); T3 strict header verdict ignores the D-056 resolution (1 OPEN); T4 strict sales join can never pass with dealer invoicing (needs metric dependency declarations) |

No calculation code was changed. T1 is the only independently reproduced defect; it is proposed as a separate scoped
PR (see the action table), not fixed here.

## 9. First usable dashboard scope (recommendation; nothing approved)

Ranked by how few unresolved dependencies each has. "Shown" = what the father would see once VERIFIED.

### 9.1 `receivables.collections` — Tahsilat (Cari bakiyeleri) — **first candidate**
- **Why first:** already `not_verified` (not `invalid`), dependency-scoped (aging account / name / representative /
  buckets, no join), no revenue-basis or DQ-5 dependency; the Cari file has no formulas and no errors.
- **Remaining requirements:** Cari snapshot date (group A); definition of the buckets and of "vadesi geçen" (are all
  buckets overdue? invoice month vs due month — `OVERDUE-DEF`); 120 / 320 and negative-balance rule; totals
  declaration (the accountant confirms the report total for the same date); mapping approval; operational freshness
  definition (45-day limit is provisional); T1 fix or the owner's confirmation that the trailing rows are report
  furniture; a real-data reconciliation reference.
- **Dependencies:** none on the sales / stock / master findings (the 45-account join is not used by this metric).
- **Independent tests:** a regression test for the approved 120 / 320 and negative-balance rule; a synthetic Cari with
  trailing empty rows; an owner-local reconciliation of the account count and bucket totals against the accountant's
  figure for the declared date (counts committed only).
- **Father sees:** per Cari account, the open balance by month bucket and the oldest month with a balance, and the total
  — labelled with the declared "as of" date and the approved meaning of the buckets.

### 9.2 `stock.reorder` and `stock.overstock` — Azalan / Fazla Stok — **second candidate (same wave)**
- **Remaining requirements:** stock snapshot date; DQ-4 rule (Q9, refined in the decision sheet); the meaning / unit of
  `STOK Kontrol %` vs `minseviye` / `maxseviye`; the duplicate-code answer (`STOCK-REPORT-SCOPE`); for overstock value
  the `Envanter değeri` valuation basis (`INV-VALUE`); totals declaration and mapping approval for the stock file.
- **Dependencies:** none on sales joins (scoped; the footer of the fields they read reconciles; BA does not count).
  The 3 negative quantities should be explained before the list is shown (they would appear as stock rows).
- **Independent tests:** regression for the approved DQ-4 rule (separate "kapsam hesaplanamadı" list or other);
  synthetic duplicate-code handling per the decision; owner-local reconciliation of list counts and of a sample of
  rows against DinamikCRM screens (stock quantity, min / max) for the declared date.
- **Father sees:** products under their minimum level (with stock, coverage, min / max) and products over their maximum
  (with inventory value if approved), plus — if approved — a separate list of stocked products whose coverage cannot be
  calculated because nothing moved out in 2024–2026.
- `stock.product_counts` can be verified together with them (same requirements minus DQ-4 / valuation); on its own it is
  of little use to the father.

### 9.3 Sales and customers — **not first-release candidates**
- Every sales metric is INVALID today through F4, F5 (permanently, §3) and the revenue basis (Q3). Prerequisites, in
  order: dependency declarations for the chosen sales metric (technical, T4); the four-pairs answer; Q3; the
  attribute-source rule (DQ-5 / Q10) if KAPALI or representative is used; period completeness (2020 start, 5 October).
- The most realistic first sales figure is a **company-level monthly net order amount** with no customer join (it
  avoids DQ-5 / DQ-6 and the master duplicates). It is not a registered metric yet; adding it would be a separate
  reviewed PR after Q3.
- Customer attributes (`customer.master_status`) need the 7 master conflicts resolved and Q10.

### 9.4 Kept separate (pending, not blocking 9.1 / 9.2)
Kredi (file of record, DURUM, snapshot), Gider/Gelir, Bütçe, Hedef, Trodat, detailed collections (M6-4), hosting
(CP2 / CP5 / KVKK), cross-sell, fairs, CRM, AI.

## 10. Tests

Existing standard suite on `main` @ `2dc55b6` (cloud, synthetic, `AI_PROVIDER=disabled`, `REQUIRE_JS_TESTS=1`):
**1719 passed, 71 skipped, 2 xfailed**. The extra skips versus CI (7) are the Playwright browser tests: Chromium could
not be downloaded in the cloud workspace; they run in GitHub Actions on the PR. No code changed in this checkpoint.

## 11. Stop conditions respected
No merge, no deployment, no ERP change, no metric promoted, `GENERATION_BOUND_API_EVIDENCE` False, AI disabled, no
originals / `.env` / SQLite touched, no private data committed or sent to chat.
