# Halsa V1 checkpoint 1 — coverage of all 33 registered metrics

Owner request: 2026-11-06; baseline main `fbca73d312da4a3217e8751546e039db883b4783`.
This is the **first reviewable implementation checkpoint**, not completion of the V1 release.
Inventory traces `REGISTER`, `MODULE_METRIC`, `PANEL_METRIC`, `ROUTES`, the builder and producers.
Computability below describes current code and required inputs; it is **not real-data acceptance of 33 outputs**.
Ceilings, blockers, definitions, source validation and VERIFIED certification are unchanged.

`NV` = certified NOT_VERIFIED, `INV` = certified INVALID, `V` = certified VERIFIED.
A runtime decision can be worse than its ceiling. RETIRED / UNAVAILABLE / PRELIMINARY are presentation states,
not new certifications. The first preliminary source review is independent of `Decision.displayable`.

| # | Registered key | Ceiling | Calculation / source requirement and limitation | Visible after this checkpoint / next action |
|---|---|---|---|---|
| 1 | `stock.reorder` | NV | RETIRED D-070; no valid current min/max policy; producer returns no recommendations | Underlying stock rows, old fields and warnings in Stok Kaynak İncelemesi. No reorder ranking |
| 2 | `stock.overstock` | NV | RETIRED D-070; no supported overstock amount | Same raw stock evidence; no overstock conclusion or total |
| 3 | `stock.product_counts` | NV | Calculable source row / distinct-code counts on inspected stock bytes; duplicates and ambiguous identity kept separate | **PRELIMINARY** counts + searchable exact stock source records, Excel rows, errors, signs, blanks, provenance and VKM warnings; certified header/refresh count stays gated |
| 4 | `stock.clearance_buyers` | INV | RETIRED D-070; depends on withdrawn overstock classification | Stock input evidence only; no clearance / buyer action ranking |
| 5 | `customers.winback` | INV | Existing deterministic sales aggregation runs; hard-coded 2021–25/2026 windows, amount→zero and DQ-5 attribution make conclusions unsafe | Still withheld. Next: factual sales evidence + labelled preliminary calculation after tracing affected records, no action propagation |
| 6 | `customers.momentum` | NV | Calculable 2025/26 YTD order amounts if sales dates/amounts readable; missing amounts and master attributes unresolved; window ends at data date | Still withheld. Add exact window, amounts/identity diagnostics and preliminary warning surface |
| 7 | `customers.order_size` | NV | Calculable 2024–26 revenue/distinct-document ratios; missing money, free documents and master attribution unresolved | Still withheld. Audit missing inputs and denominator; label order-source arithmetic |
| 8 | `finance.margin` | INV | Known invalid missing-cost→zero path DQ-1; current result cannot establish profit | Still withheld. Expose source revenue/cost evidence and affected counts; do not certify current margin |
| 9 | `receivables.risk` | NV | RETIRED D-071; invoice-age buckets do not establish overdue/risk; runtime INVALID | No risk ranking. Underlying balances remain existing finance-only Cari list when eligible |
| 10 | `receivables.collections` | V | Existing signed bucket sums, independent reproduction, mapping, original report date, 60-day freshness; no overdue claim | Existing `/api/finance/cari`, financial/owner session only, fresh bound source only. Current November aging export not found; never substitute customer master list |
| 11 | `actions.real_decline` | NV | Manual inflation assumption plus sales momentum; no independently supplied inflation basis | Withheld; no V1 action release. A future labelled arithmetic view would need explicit assumptions |
| 12 | `actions.queue` | NV | Composite ranks of uncertified inputs and priority thresholds | Withheld (runtime INVALID); no propagation of provisional evidence into actions |
| 13 | `ai.interpretation` | NV | AI not a calculation or primary evidence; unnecessary for V1 | Disabled / withheld; not a delivery dependency |
| 14 | `ai.drafts` | NV | AI depends on uncertified facts; unnecessary for V1 | Disabled / withheld; no drafts generated |
| 15 | `agent.customer_facts` | NV | Composite of winback/Cari/momentum/withdrawn risk | Withheld; do not unlock composite endpoint to expose a subset |
| 16 | `agent.product_facts` | NV | Depends on retired stock actions; D-070 withdrawal | Retired action card remains withheld; raw stock facts available in source review |
| 17 | `context.full` | NV | Composite includes unsupported/retired analyses and private internal context | Owner-only and accuracy-withheld; not an export interface |
| 18 | `opportunities.cross_sell` | NV | Historical group association support/confidence/lift computable from sales; thresholds and cohort not accepted as buying intent | Still withheld; next inspect associations as descriptive evidence, no purchase recommendation claim |
| 19 | `opportunities.territories` | NV | Requires Hedef + sales; Hedef file/age and cross-sell dependency unresolved | Still withheld; missing inputs must be explicit; do not manufacture territories |
| 20 | `sales_queue` | NV | Composite action scores plus local follow-ups; not a factual sales report | Withheld (runtime INVALID); do not release action rankings. CRM remains in Dinamik CRM for V1 use |
| 21 | `customer360.profile` | NV | Sales facts calculable, optional master/Hedef cards and invoice-account links need field-level audit; Cari restricted | Still withheld except existing redacted directory; next expose cards individually, preserve finance redaction |
| 22 | `fairs.history` | NV | FUAR-labelled historical order counts/amounts computable from sales; taxonomy/revenue meaning not confirmed | Still withheld; next descriptive labelled history, no attendance/ROI inference |
| 23 | `fairs.segments` | NV | Composite action segments depend on unverified/retired sources | Still withheld (runtime INVALID); do not revive collections segment from invoice age |
| 24 | `crm.active_customers` | NV | Calculable last-order-within-365-days count if sales dates readable; activity definition unapproved | Analytic tile withheld; existing user-entered records unchanged, no new CRM work |
| 25 | `owner.kredi` | NV | Explicit Kredi workbook; installment identities must pass; status/file/date unresolved; schedule is not bank debt proof | Finance/owner-only existing section stays withheld. File of record needed; no fabricated schedule |
| 26 | `owner.gider_gelir` | INV | Source-native line/month figures exist if workbook supplied; conflicting totals/accounting basis prohibit profit conclusion | Finance/owner-only withheld. Next raw rows, conflicting totals and accounting question |
| 27 | `owner.butce` | INV | Cell-level evidence available if workbook supplied; REF/DIV errors/external links prohibit blanket variance result | Finance/owner-only withheld. Next valid source cells with invalid/unavailable cells and counts preserved |
| 28 | `owner.operations` | NV | Composite queue/cross-sell counts; local record counts separate | Analytic counts withheld; operational record counts unchanged; no new workflow |
| 29 | `scenario.receivables` | NV | Original overdue basis withdrawn D-071; scenario producer has no valid overdue input | UNAVAILABLE as business conclusion; need agreement-based overdue report and reviewed rule; no scenario from invoice age |
| 30 | `collections.detailed` | NV | UNAVAILABLE: Evrak Detaylı Tahsilat export absent, importer/surface not implemented | No invented results; exact missing export identified |
| 31 | `trodat.sales` | NV | Importer computes native model/variant/month quantities when explicit Trodat workbook supplied; no UI; SKU units/mapping unresolved | No surface in this checkpoint; native source evidence can be added separately without invented SKU mapping |
| 32 | `customer.last_order_date` | NV | Sales max date computable; cancelled/undelivered parts included, completeness/whitespace policy unresolved | Still redacted in directory/CRM/fairs. Next labelled source observation with affected-record diagnostics |
| 33 | `customer.master_status` | NV | Source master status readable; duplicate master codes and source-of-record conflict unresolved | Still redacted; conservative closed-customer action restrictions remain. Next raw master evidence without inventing status |

## Acceptance performed at this checkpoint

Synthetic unit/integration tests distinguish preliminary visibility from certification, test error/blank/zero/sign and
precision preservation, duplicate and missing identity rows, missing/changed/unsupported sources, fingerprint and
HTML-marker authorization, escaping, and unchanged retired/AI/finance boundaries. Browser checks exercise the real
production server, search, sort, expandable warnings and retired tabs. Existing D-075 three-role/revocation/generation
coverage remains required.

November stock only, independent acceptance on a read-only private copy: **3,316 nonempty source records; 3,313 code
rows / 3,313 distinct codes; 0 duplicate occurrences; 3 missing-identity rows; 1,168 coverage-error records; 0 negative
quantity records**. Every displayed field (8 fields × 3,316 records = **26,528**) compared with an independent openpyxl
read: **0 mismatches**. Exact source hash, size and mtime unchanged. Snapshot 2026-11-01 manually declared from the
original filename under the already agreed owner rule; no Stock upload-date implementation was silently added.
Those are source observations, not product certification. The existing strict runtime verdict remains INVALID.
No private record, value, source workbook, acceptance script or generated report is committed.

The first real-file comparison found that the diagnostic XML reader intentionally drops whitespace-only strings.
The review now uses original cached cell values for display, preserving such strings too; formula-cache/error
warnings still use the existing diagnostic reader. Validation, register and source interpretation were not changed.

The normal controlled refresh also ran successfully on explicit read-only copies of the November Stock and Sales
files (aging absent, no customer-master substitution, no inflation assumption supplied). Original hash/size/mtime
checks passed for both. Existing sales producers ran, but their results remained withheld; their numerical acceptance
is still pending. The latest observed sales transaction was 2026-10-30; that is not a report-date or completeness claim.
**Sales analyses were not accepted in this stock checkpoint**.
November Cari aging export was not found in the inspected StockAgent and related acceptance directories; only an
older October aging export and a customer master list were found. No substitution was made. Expanded sections need
the actual explicit Hedef, Kredi, Gider/Gelir, Bütçe, Trodat or detailed collections reports, as applicable.
