# M8-R1 — Prioritized issue-to-action table

_2026-10-29. Companion to `m8_r1_reconciliation_report.md` (evidence) and `../m8_r1_babamdan_gereken_kararlar_tr.md`
(the owner's questions). Counts only; the exact codes / rows behind each count are in the private folder on the Mac.
Nothing here is approved; "responsible" names who can supply the answer, not an approval that exists._

Priority: **P1** blocks the recommended first release (Tahsilat, Azalan / Fazla Stok) · **P2** blocks sales / customer
metrics · **P3** context, does not block a first-release metric.

Classification: **SRC** source-data inconsistency · **ID** identity / mapping issue · **RULE** business-rule decision ·
**CODE** possible code defect / technical gap.

## A. Business items (need an answer; none can be corrected technically without one)

| Pri | Issue / check code | Source | Evidence (reproducible: yes, all) | Class | Blocks | Safe technical correction without business input? | Smallest answer / evidence needed | Recommended action · responsible |
|---|---|---|---|---|---|---|---|---|
| P1 | Report dates · `*:report_date` (4 OPEN), VKM `report_date_missing` ×3, `period_unknown` | stock, sales, liste, aging | no date in stock / Cari data; sales max 2026-10-05, last-day completeness unknown; Cari newest bucket Eylül-2026; Excel save 6 Oct (not export time) | RULE | every metric (freshness, period) | No — dates are declared (D-051) | For each file: "as of" date (and time); whether 5 Oct sales are complete; Cari bucket basis and as-of | Ask the person who exports from DinamikCRM (decision sheet A1–A3) · **satış ekibi / raporu çıkaran kişi**; owner declares at upload |
| P1 | Cari 120 / 320 and negative balances · `aging:evidence` FAIL (F7), VKM `join_aging_account_not_in_sales` (45), `dq6_aging_order_code_only` (1) | aging | 28 `320` accounts (27 of the 45 unmatched); 402 of 824 balances negative; 18 `120` accounts with no 2020–26 sales | RULE / ID | `receivables.risk`, `customers.winback`, `customer360` (strict); the rule is needed for `receivables.collections` content | No | Are `320` accounts suppliers to be excluded from Tahsilat? What does a negative balance mean, and how should it show? | Decision sheet C1 · **muhasebe**, decided by the owner |
| P1 | Bucket meaning / "vadesi geçen" · blocker `OVERDUE-DEF` | aging | bucket headers are months; no due-date field | RULE | `receivables.collections`, `.risk` | No | Are the months invoice months or due months; which buckets are overdue on the as-of date? | Decision sheet A3 / C1 · **muhasebe** |
| P1 | DQ-4 undefined coverage · `stock:evidence` FAIL (F1), VKM `stock_coverage_undefined` | stock (AI) | 1,178 rows, all zero outflow 2024–26; 472 with stock; 263 `ÜRETİMDEN KALKTI`; 165 with min / max | RULE | `stock.reorder`, `stock.overstock` (+ dependants) | No (any list or exclusion is a rule) | Owner rule for products whose coverage cannot be calculated (Q9 refined) | Decision sheet B1 · **baba** |
| P1 | Coverage unit · blocker `DQ-4` / definition `stock_coverage_rule` | stock (AI, BN, BO) | `AI = AH / ((T+U+V)/6)`, AH = stock net of orders + incoming + in transit | RULE | `stock.reorder`, `stock.overstock` | No | What "STOK Kontrol %" measures and whether min / max are in the same unit | Decision sheet B2 · **baba** or whoever set up the report |
| P1 | Duplicate stock code · `stock:evidence` FAIL (F2), part of F3; VKM `stock_duplicate_code`; blocker `STOCK-REPORT-SCOPE` | stock | 1 code, 2 adjacent rows, 16 conflicting columns (quantities, coverage, value, cost), active product | SRC | `stock.reorder`, `.overstock`, `.product_counts` | No (sum / pick = rule) | DinamikCRM: why two stock lines (two warehouses / lots, or an error)? add, show both, or correct | Decision sheet B3 · **depo / satış ekibi** checks; owner decides |
| P1 | 3 negative stock quantities (no check) | stock (W) | 3 rows, numeric coverage, blank `DURUM` | SRC | would appear in Azalan Stok as-is | No | Are these real (oversold / unposted receipts) or errors? | Decision sheet B3 note · **depo** |
| P2 | `Envanter değeri` valuation · blocker `INV-VALUE` | stock (AV) | value basis undocumented; 2 negative values among undefined rows | RULE | `stock.overstock` value only | No | Which cost the value uses (last purchase / average / current) | Decision sheet B4 · **muhasebe** |
| P2 | 4 duplicate sales rows · `sales:evidence` FAIL (F4), VKM `sales_duplicate_rows` | sales | 4 identical pairs, 2022, 3 orders, adjacent rows, documents not repeated as a whole | SRC | every sales metric (source integrity) | No (removing is forbidden) | Look up the 3 orders (private list): is the product on the order twice or once? | Decision sheet D1 · **satış ekibi**; result recorded as a decision |
| P2 | 7 conflicting master duplicates · `liste:evidence` FAIL (F6), part of F5; VKM `master_duplicate_codes` | liste | 7 pairs, all conflict on open / KAPALI and representative; 6 used in sales (3 ordered in 2026) | SRC | `customer.master_status`, every KAPALI / representative use, `customer360` | No (D-042) | Which row is current for each code; correct in DinamikCRM | Decision sheet C2 · **muhasebe** (master data) |
| P2 | 30 order-source codes not in master · F5, F6; VKM `join_order_customer_not_in_master` | sales, liste | all `Pro_kodu`; 68 % event lines; never equal to their invoice account; 20 last ordered 2021–22, 1 in 2026 | ID | strict sales / customer metrics | No | Customer or project / event code? If project: which customer should it count as? | Decision sheet C3 · **satış ekibi** |
| P2 | Attribute source for dealer-invoiced customers (DQ-5) · VKM `dq5_multiple_invoice_accounts` | sales, liste | 712 / 173; 40,704 rows | RULE | KAPALI filters, representative, momentum, order size, risk | No (Q10) | Representative / open-closed from the ordering customer's card, the invoice account's card, or both shown | Decision sheet C4 · **baba** |
| P2 | Whitespace codes · VKM `code_whitespace` | sales, stock | 5 order, 6 invoice, 2 sold product, 3 stock codes; 1 invoice account matches only after trimming | RULE | `customer.last_order_date`, `customer360` (`CODE-WHITESPACE`) | No (a spec change) | May codes that differ only by leading / trailing spaces be treated as the same? | Decision sheet C4 (sub-question) · **baba** |
| P2 | Revenue basis (Q3, already asked) · blockers `REVENUE-BASIS`, `AMT-ZERO` | sales | unchanged (M6-0 §2.2) | RULE | every sales amount | No | Q3 answer | Decision sheet D2 (reminder) · **baba / muhasebe** |
| P3 | 256 sold products not in stock · F3, F5; VKM `join_sales_product_not_in_stock` | sales, stock | 0 sold in 2026; 63 in 2024–26; 1 trim-only match | ID | `stock.clearance_buyers`, strict sales metrics | No (no discontinued label without evidence, D-057) | Does the stock export list all product cards or only active ones? | Decision sheet A1 (sub-question) · **raporu çıkaran kişi** |
| P3 | 45 Cari accounts not sales invoice accounts (120 part) · F7 | aging | 18 `120` accounts (12 negative, 6 positive) | ID | strict receivables / win-back | No | Covered by the 120 / 320 answer; no further question in the first sheet | — |
| P3 | Stock footer partial (BA) · `stock_footer_totals` OPEN, VKM `footer_partial` | stock | 2 BA footer cells propagate `#DIV/0!` from zero-stock rows | SRC (explained) | none of the quantity metrics (D-059) | Not needed | — | Record as explained; no question |
| P3 | `DURUM` value "." on one row; 3 blank `liste` rows; 1 numeric `KODU` | stock, liste | counts | SRC | none directly | No | — | Mention to the exporter with A1; no decision |

## B. Technical items (no business answer needed; each a separate scoped PR, none done here)

| Pri | Item | Evidence | Class | Effect today | Proposed correction (not implemented) | Tests needed |
|---|---|---|---|---|---|---|
| P1 | **T1** Cari trailing row with only an empty text cell counted as a data row | `inspect_core("aging")`: 825 data rows for 824 accounts; `blank_amount_cells` 11 = one row × 11 buckets; reproduced synthetically on `main` | CODE (reproduced) | 1 OPEN (`aging:evidence` blank amounts), VKM `amount_blank`; caps `receivables.collections` at NOT_VERIFIED | In `scan_sheet`, a cell whose value is an empty string and has no formula does not make a row a data row (all core sources). Never drop a row that has any identity or amount | synthetic: empty-string-only row ignored; a row with a code and blank buckets still counts as blank; a row with "" in B and an amount still counts; stock footer recognition unchanged; real-data rerun shows 824 rows and 0 blank cells |
| P2 | **T2** `liste` sheet not inspected by the formula-aware scan | 9 OPEN "… denetimi yapılmadı" on `liste` | CODE (gap) | `customer.master_status` can never verify | Scan the `liste` sheet (sheet by name) like the other core sheets; duplicate `KODU` stays a reported problem | synthetic master with errors / blanks / duplicates; evidence fields set |
| P3 | **T3** strict header verdict ignores the D-056 resolution | stock `ambiguous_headers` = 1 although the template resolves `Stok_Kodu` for scoped metrics | CODE (inconsistency) | 1 OPEN; unscoped stock consumers | Count only unresolved duplicate headers in `ambiguous_headers` (as `fields()` already does), or document that the strict verdict is intentionally stricter. Reviewer's call | unit test both ways |
| P2 | **T4** strict sales join can never pass while dealer invoicing exists | `order_customers_with_multiple_invoice_accounts` (712) is "ambiguous" in the sales join and source evidence | CODE (scope) | every unscoped sales metric INVALID permanently | Trace and declare `requires` for the first sales metric(s) chosen (2B pattern); do not remove DQ-5 from the join report | per-metric scoped-judgement tests |
| — | Private diagnostics | the two read-only scripts used here | tooling | — | Optional: fold the counts-only parts into `reconcile_sources` later | — |

## C. What resolves which failure

| Failure | Cleared when |
|---|---|
| F1 stock Excel errors | an approved DQ-4 rule that accounts for undefined coverage (code change with tests); the `#DIV/0!` cells themselves stay in the source |
| F2 stock duplicate | the duplicate-code answer + an implemented, approved handling (or a corrected export) |
| F3 stock join | F2 + the 256 sold-not-in-stock products accepted under an approved rule (or scoped out of the metrics that do not use them — already true for reorder / overstock) |
| F4 sales duplicates | ERP check of the 3 orders + an approved, fingerprint-bound handling |
| F5 sales join | 30 order codes + 1 whitespace account + 256 products answered, 7 master duplicates fixed, **and** DQ-5 handled by metric scoping (T4) — the 712 multi-invoice customers are structural |
| F6 master join | 30 order codes + 7 master duplicates |
| F7 Cari join | 120 / 320 rule + DQ-6 rule (the 18 `120` accounts without sales are expected under an approved rule) |

Even with all seven cleared, every metric still needs declared dates, the totals / mapping declarations, its approved
definitions (D-xxx), regression tests and a real-data reconciliation reference before a reviewed promotion PR
(`release_gate.md` §2).
