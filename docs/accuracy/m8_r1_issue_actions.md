# M8-R1 — Prioritized issue-to-action table

_2026-10-29. Companion to `m8_r1_reconciliation_report.md` (evidence) and `../m8_r1_babamdan_gereken_kararlar_tr.md`
(the owner's questions). Counts only; the exact codes / rows behind each count are in the private folder on the Mac.
Nothing here is approved; "responsible" names who can supply the answer, not an approval that exists. An answer is
evidence for a later decision and change; it does not by itself clear a reconciliation failure or make a metric
verifiable — see §C (strict failure vs per-metric eligibility vs the release precondition) and §D (open conflict)._

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
| P1 | **T1** Cari trailing row with only an empty text cell counted as a data row | `inspect_core("aging")`: 825 data rows for 824 accounts; `blank_amount_cells` 11 = one row × 11 buckets; reproduced synthetically on `main` | CODE (reproduced) | 1 OPEN (`aging:evidence` blank amounts), VKM `amount_blank`; caps `receivables.collections` at NOT_VERIFIED. The owner confirming the row is report furniture does **not** change this evidence; only an implemented, independently tested correction does | In `scan_sheet`, a cell whose value is an empty string and has no formula does not make a row a data row (all core sources). Never drop a row that has any identity or amount | synthetic: empty-string-only row ignored; a row with a code and blank buckets still counts as blank; a row with "" in B and an amount still counts; stock footer recognition unchanged; real-data rerun shows 824 rows and 0 blank cells |
| P2 | **T2** `liste` sheet not inspected by the formula-aware scan | 9 OPEN "… denetimi yapılmadı" on `liste` | CODE (gap) | `customer.master_status` can never verify | Scan the `liste` sheet (sheet by name) like the other core sheets; duplicate `KODU` stays a reported problem | synthetic master with errors / blanks / duplicates; evidence fields set |
| P3 | **T3** strict header verdict ignores the D-056 resolution | stock `ambiguous_headers` = 1 although the template resolves `Stok_Kodu` for scoped metrics | CODE (inconsistency) | 1 OPEN; unscoped stock consumers | Count only unresolved duplicate headers in `ambiguous_headers` (as `fields()` already does), or document that the strict verdict is intentionally stricter. Reviewer's call | unit test both ways |
| P2 | **T4** strict sales join can never pass while dealer invoicing exists | `order_customers_with_multiple_invoice_accounts` (712) is "ambiguous" in the sales join and source evidence | CODE (scope) | every unscoped sales metric INVALID permanently | Trace and declare `requires` for the first sales metric(s) chosen (2B pattern); do not remove DQ-5 from the join report | per-metric scoped-judgement tests |
| — | Private diagnostics | the two read-only scripts used here | tooling | — | Optional: fold the counts-only parts into `reconcile_sources` later | — |

## C. Three different things: strict failure, per-metric eligibility, release precondition

They are easy to conflate, so this section keeps them apart. **No owner answer and no metric scoping clears a strict
failure by itself**, and **per-metric eligibility does not unlock any metric** while the release precondition (C3)
stands.

### C1. Clearing a strict, source-wide reconciliation failure (`reconcile_sources`)
`scripts/reconcile_sources.py::reconcile_core()` judges every source with the **unscoped** `SourceEvidence.problems()`.
A FAIL disappears only when the recorded count it is built on becomes zero in the source bytes, or when the check
itself is changed by a separately reviewed, owner-authorized code change. Answers, Veri Kontrol reviews and
declarations do not change these counts (D-058, D-062: a review never certifies).

| Failure | Strict condition that makes it FAIL | Owner answer alone clears it? | What would actually clear it |
|---|---|---|---|
| F1 stock Excel errors | `error_cells` > 0 (the 1,178 `#DIV/0!` in AI) | No — an approved DQ-4 display rule leaves the error cells in the source | an export whose AI column no longer contains Excel errors (a change of the DinamikCRM report template), or a reviewed change of how an approved "undefined coverage" state is recorded in evidence |
| F2 stock duplicate | `duplicate_records` > 0 (1) | No | the stock card / export corrected so the code appears once, or a reviewed code change recognizing an approved, fingerprint-bound row grain |
| F3 stock join | `sold_products_not_in_stock` (256) or `stock_duplicate_codes` (1) > 0 | No | F2 cleared **and** an export that lists every sold code, or a reviewed change of the join check after an approved rule for historical products |
| F4 sales duplicates | `duplicate_records` > 0 (4) | No | a corrected export, or a reviewed, fingerprint-bound acknowledgement mechanism (new code) after the ERP check |
| F5 sales join | unmatched (287) or ambiguous (720, of which 712 dealer-invoiced customers) > 0 | No | **not reachable under the current check while dealer invoicing exists**; only a reviewed change of the check definition (e.g. an owner-approved relationship no longer counted as ambiguous), in addition to the 30 / 1 / 256 / 7 items. Metric scoping (T4) does **not** clear F5 |
| F6 master join | 30 unmatched order codes or 7 duplicate master codes > 0 | No | the master corrected in DinamikCRM (codes present once each) or a reviewed check change after the C3 answer |
| F7 Cari join | 45 accounts not among sales invoice accounts or 1 DQ-6 account > 0 | No | under the current check only if every Cari account is a sales invoice account — the 27 `320` and 18 `120` accounts remain after any 120 / 320 answer, so a reviewed check change is needed |

### C2. Making one dependency-scoped metric eligible for verification (the gate's per-metric judgement)
A metric with a `requires` declaration is judged by `SourceEvidence._scoped_problems()`: the source-**integrity**
problems still apply (duplicate records, layout, selection, totals, mapping), field-level problems only for the fields
it reads, join problems only for the joins it uses (D-055, D-059). Eligibility is necessary, not sufficient (C3).

| Candidate | Still judged (scoped) on the 5 Oct files | Not judged | Needed for eligibility |
|---|---|---|---|
| `receivables.collections` | `bucket:*` blank cells = 11 → NOT_VERIFIED (T1); report date; totals declaration; mapping approval; blockers `AGING-DATE`, `OVERDUE-DEF`, `REAL-DATA`; definitions `overdue_definition`, `operational_freshness` | the Cari join (F7) | **T1 implemented and independently tested** (owner confirmation that the row is report furniture does not change the 11 cells); A3 / C1 answers recorded as D-xxx; declarations; real-data reconciliation reference; regression tests |
| `stock.reorder`, `stock.overstock` | stock `duplicate_records` = 1 → INVALID (source integrity, not removed by scoping); AI field `error_cells` = 1,178 → INVALID; date; totals; mapping; blockers `DQ-4`, `STOCK-DATE`, `STOCK-REPORT-SCOPE`, `INV-VALUE` (overstock), `REAL-DATA` | sales joins; BA footer (D-059) | F2 cleared in the source or by reviewed code; an **implemented** DQ-4 evidence handling after the B1 / B2 answers; B3 / B4 answers; declarations; reconciliation reference; tests |
| any sales metric | F4 (source integrity) and, if strict, F5 | — | T4 declarations, the four-pairs handling, Q3, DQ-5 / Q10 where used |

### C3. The existing release precondition (`release_gate.md` §8.3) — unchanged
§8.3 step 2 requires `GENEL SONUÇ` of `reconcile_sources --core` **not to be FAIL** before
`GENERATION_BOUND_API_EVIDENCE` may become True, and `validate_register(production=True)` refuses every VERIFIED
certification while that flag is False. D-059 (§15) records that the source's overall reconciliation stays OPEN / FAIL
even where a narrowly scoped metric could pass. Today `GENEL SONUÇ` is FAIL (F1–F7). **So no metric can be promoted
— however eligible under C2 — until either C1 is reached or §8.3 is explicitly revised (section D).**

Even then, every promotion still needs declared dates, the totals / mapping declarations, approved definitions
(D-xxx), regression tests and a real-data reconciliation reference in its own reviewed PR (`release_gate.md` §2).

## D. Open decision: the global release precondition vs the first-metric strategy

**Conflict.** The recommended first scope (Tahsilat, then Azalan / Fazla Stok) relies on per-metric scoping (C2).
The release gate requires the strict, all-source reconciliation not to FAIL (C3). Under the current checks F5 is
structurally unreachable while dealer invoicing exists, and F1, F3 and F7 need either source changes or check-definition
changes; owner answers alone cannot reach C3. As written, the two cannot both be satisfied by answers and per-metric
work. This PR **does not** change, weaken or reinterpret §8.3, the flag, any check or any metric.

**Recommended resolution path** (each step a separate, independently reviewed PR or decision; none started):
1. **Evidence first:** owner / accountant answers to A, B and C1 recorded as D-xxx (no rule implemented by the answer
   alone).
2. **Technical corrections with tests:** T1; then the DQ-4 evidence handling and the duplicate-code handling once B1–B3
   are decided; T2 / T3 as the reviewer decides. Each rerun owner-locally, counts only.
3. **Reporting only, no gate change:** extend `reconcile_sources` to print, beside the unchanged strict `GENEL SONUÇ`,
   the scoped gate decision of each named candidate metric, so reviewers see both verdicts from the same bytes.
4. **Release-gate decision checkpoint (owner-authorized, independently reviewed, D-xxx):** with the evidence of steps 1–3,
   decide explicitly either
   - (a) **keep §8.3 as is** — then list and complete the source corrections and reviewed check changes needed to reach
     a non-FAIL strict result (including a decision on how F5 is defined), before any flag change; or
   - (b) **revise §8.3** to a per-metric precondition. Minimum safeguards if chosen: the strict verdict is still computed,
     printed and stored unchanged and is never relabelled PASS; every remaining strict FAIL is explained, listed in
     Veri Kontrol and owner-reviewed; none of them lies in the scoped dependencies of a metric being promoted; every
     unscoped or affected metric stays withheld (tests prove it); the change is its own PR with a decision record.
   This report does not choose between (a) and (b).
5. Only after step 4: a separate PR for the flag (§8.3 step 6), then one reviewed promotion PR per metric (§2).
