# M8-ACCURACY — Open verification items

_Status: 2026-10-18. Each item names the exact evidence and what is required to resolve it. Nothing here is
resolved by this checkpoint unless it says so. Codes match `services/accuracy.py` blockers._

Owner questions are numbered as in `docs/m8_owner_questions_tr.md` (Q#). "Withheld" means the output is replaced by
"Bu analiz henüz doğrulanmadığı için gösterilmiyor." in the dashboard and the API.

## Known data-quality problems

### DQ-1 — Missing cost counted as zero profit (Kâr Marjı) · INVALID
- **Evidence:** `dashboard_builder.load_sales` sets `_prof = Net_Tutar_Maliyet_Dusulmus.fillna(0)`; `m_margin` divides
  Σ`_prof` by Σ`Nettutar`. Strict xfail `tests/test_domain_rules.py::test_dashboard_margin_does_not_treat_missing_cost_as_zero`.
  M6-0: cost columns are partially filled.
- **Now:** module `margin` withheld.
- **To resolve:** owner rule (Q7): exclude rows without cost from both revenue and profit and show cost coverage, or
  another approved rule; implement; remove the xfail; real-data reconciliation of margin against an accounting source.

### DQ-2 — KAPALI customers in the Stok Eritme buyer lists · INVALID
- **Evidence:** `dashboard_builder.m_overstock_move` has no KAPALI filter (the AI context was fixed in D-009). On the
  5 Oct 2026 data, 2 of the top 20 overstock products were affected (TASKS).
- **Now:** module `osmove` withheld; overstock actions withheld.
- **To resolve:** owner approval (Q8) to exclude KAPALI from the call list using the trusted master (`liste`), plus
  DQ-4 and DQ-6 resolved for the buyer scoring.

### DQ-4 — `#DIV/0!` stock coverage removes products from Azalan / Fazla Stok · NOT_VERIFIED
- **Evidence:** `m_stock` requires `STOK Kontrol %`; `num()` coerces `#DIV/0!` to NaN. 1,178 of 3,314 products on the
  5 Oct 2026 export (`docs/m6_source_inventory.md` §12). The lists and the "min altı" / "maks üstü" counts are therefore
  incomplete while being presented as complete. The pandas reader cannot tell error cells from blanks.
- **Now:** `reorder`, `overstock` modules and their stat cards withheld. The DQ-4 count stays visible in Yönetim.
- **To resolve:** owner rule (Q9) (e.g. a separate "kapsam hesaplanamadı" list), a declared stock report date, the
  inventory valuation basis of `Envanter değeri`, and a reconciliation of counts against the ERP.

### DQ-5 — Invoice-account attributes applied to order-source customers · NOT_VERIFIED
- **Evidence:** `cust_attrs` takes cached `Temsilci` / `Açık / Kapalı` / `Sektör` (XLOOKUPs on `Musterikod`) and applies
  them to `Pro_kodu` customers; 10.6 % of order-source customers have several invoice accounts (M6-0 §2.3). Customer 360
  already uses the master (`liste`) instead (D-042).
- **Now:** every M5 module that filters by KAPALI or shows a rep is withheld.
- **To resolve:** owner decision (Q10) on the attribute source and salesperson of record; move M5 to master attributes.

### DQ-6 — Win-back overdue joins invoice-account debt by the order-source code · INVALID (new)
- **Evidence:** `m_lapsed` uses `owed.get(str(c), 0)` where `c` is the order-source customer and `owed` is keyed by Cari
  `Hesap kodu` (invoice account). A dealer-invoiced customer whose invoice account owes shows **0 / "Yok"**.
  `services/context.score_buyers` uses the same lookup for `has_overdue`. Pinned by
  `tests/test_m8_accuracy.py::test_dq6_winback_overdue_identity_mismatch` and the strict xfail
  `test_dq6_winback_never_claims_zero_overdue_for_a_debtor_invoice_account`.
- **Now:** `winback` module, "kayıp müşteri" / "borçlu kayıp müşteri" stats, tier line, win-back actions withheld;
  listed in Yönetim known issues.
- **To resolve:** owner-approved rule consistent with D-042 (show the invoice-account balance separately, never
  allocated; "unknown" instead of zero when no account matches); implement; remove the xfail.

## Source-specific items

### KREDİ — DURUM / Kapalı meaning · NOT_VERIFIED
- **Evidence:** in the Feb 2026 export `DURUM = Kapalı` appears on 152 unpaid installments (TASKS); `build_kredi_view`
  counts DURUM only and keeps every unpaid installment (D-047). Snapshot date optional; two candidate files pass.
- **Now:** the whole Kredi section is withheld. No outstanding bank-debt figure is published. Kapalı is **not** read
  as paid.
- **To resolve:** the owner's 2026-10-19 answer recorded as a D-xxx decision; authoritative dated file of record; a
  reconciliation against a bank statement or the ERP balance.

### GİDER/GELİR — basis, mapping, contradictory totals · INVALID
- **Evidence:** `docs/m6_gider_gelir.md`: `<Y> TOPLAM = Σ months` is only a warning (1 real row differs); ÖZET expense
  cells differ from the expense table (pasted pivot); 23 manual allocation overrides; monthly headers carry the template
  year; snapshot date unknown; accounting basis and business-line mapping unknown (Q4).
- **Monthly vs annual investigation (this checkpoint):** the importer surfaces the mismatch as
  `annual_totals_not_matching_months` and `summary_expense_vs_expense_table.failed`; which figure is correct cannot be
  determined from the workbook alone — both are recorded values. No real file was available here to inspect further.
- **Now:** the whole Gider/Gelir section is withheld; no management profitability is calculated.
- **To resolve:** accounting basis and mapping decisions; finance confirms which of the contradicting totals is right
  (or fixes the workbook); reconciliation with the sales and accounting books.

### BÜTÇE — `#REF!`, `#DIV/0!`, external workbook · INVALID
- **Evidence:** M6-0 §10 (67 `#REF!` + 3 `#DIV/0!` in the Ocak file; external link to `2026_gider_gelir_TASLAK.xlsx`);
  D-041 (never authoritative, no repairs).
- **Now:** Bütçe section withheld (including its health view); no budget variance exists or is computed.
- **To resolve:** file of record (Q5), repaired workbook from finance, removal or approval of the external link.

### HEDEF — stale capacity, Risk, Kredi · NOT_VERIFIED
- **Evidence:** M6-0 §5, D-037: `Haftalık Kaşe Adedi`, `Risk`, `Kredi` have no date and look like defaults.
- **Now:** Customer 360 and territory counts (the only Hedef consumers) are withheld.
- **To resolve:** Hedef file of record and cadence (Q2); stale fields are never to be shown as current values.

### CROSS-SELL — broad population, association ≠ intent · NOT_VERIFIED
- **Evidence:** ~78 % of active customers get at least one target with the current guards (TASKS);
  `config.CROSS_SELL_*` unchanged.
- **Now:** Fırsatlar tab, cross-sell reasons and segments withheld.
- **To resolve:** owner threshold review (Q12); wording that presents candidates as hypotheses, never as sales.

### FAIRS — taxonomy, conversion, ROI · NOT_VERIFIED
- **Evidence:** D-045: FUAR-only history; no conversion window, attendance or cost attribution exists; Q16 open.
- **Now:** fair history, candidate segments and ERP evidence on campaigns withheld. Campaigns, members, stages,
  next-action dates and notes (user-entered) stay usable.
- **To resolve:** taxonomy and revenue basis decisions; nothing about conversion / attendance / ROI may be shown until
  their sources exist.

### CRM — incomplete coverage · operational
- **Evidence:** D-024 / D-046; coverage label "Eksik kayıt olabilir".
- **Now:** interactions and follow-ups (user-entered) stay; the "aktif müşteri" counts are withheld (365-day window
  not approved). Missing interactions are unknown activity, never "not contacted".
- **To resolve:** approve the active-customer window; standardized logging before any coverage claim.

### DETAILED COLLECTIONS — source missing · UNAVAILABLE
- **Evidence:** M6-4 BLOCKED; `Evrak Detaylı Tahsilat Raporu` not supplied.
- **Now:** no surface; nothing is derived from the account-level Cari aging.
- **To resolve:** the export (Q15) and collection thresholds.

### SALES AND CUSTOMER IDENTITY · rule preserved, joins not validated
- **Evidence:** D-003 kept (approved definition `customer_identity`). Open: trailing-space codes (20 `Musterikod` and
  19 `Pro_kodu` values; exact matching leaves one invoice account unmatched); 0.004 % exact duplicate rows and 0.03 %
  repeated document × SKU lines (M6-0 §2.2); ~10 duplicate master codes (§3). No unapproved normalization was found in
  the M5 path (codes are `astype(str)` without trimming; Customer 360 keeps them exactly).
- **To resolve:** whitespace policy decision; a join-validation step recorded as `SourceEvidence.joins_validated`.

### REVENUE — cancelled, delivered, returns, costs, missing values, periods · NOT_VERIFIED
- **Evidence (code + M6-0 §2.2):** every M5 revenue figure sums `Nettutar` = **ordered** net amount; it includes the
  undelivered (`Kalan`, 0.08 % of rows) and cancelled (`Vazgecilen`, 0.10 % of rows) parts; **no cancellation filter**
  and no delivered-quantity basis exist in `load_sales`; **returns are not represented** (no negative amounts in the
  export); `Nettutar = 0` on 10 % of rows (free goods) and those documents still count as orders; VAT inclusion is
  unverified; **missing `Nettutar` is summed as 0** (`fillna(0)`, the AMT-ZERO blocker; the collector escalates to
  INVALID when a missing amount is present); 2020 is a partial year (from late August); 2026 is year-to-date.
- **To resolve:** revenue-basis decision (Q3: order / delivery / invoice; treatment of cancellations and returns);
  implement it once in Python; stop substituting zero; reconcile yearly totals against accounting.

### CUSTOMER DIRECTORY — derived attributes · NOT_VERIFIED
- **Evidence:** search hits carried `last_order_date` (max order-line date, cancelled / undelivered parts included) and
  the master KAPALI flag (`liste` CARİ FİRMA; attribute source of record open, DQ-5 / Q10). Raised by the PR #27 review.
- **Now:** withheld in search, CRM and fair member responses; code and recorded name remain.
- **To resolve:** last-order-date basis (Q3), attribute source of record (Q10), real-data reconciliation.

## Cross-cutting items
- **Generation-bound evidence (review of PR #27, mandatory for M8-ACCURACY-2):** the API receives no evidence today;
  the snapshot of the published generation must be shared by dashboard, API, owner views and refresh before any
  promotion (`release_gate.md` §6 A–F).
- **Evidence inspection (fixed):** `_excel_errors` previously reported 0 when inspection raised (always for numeric
  columns); it now fails closed.
- **Report dates:** stock and Cari exports are chosen by modification time; no report date is read or declared. The
  M8 upload (CP3) must capture a declared business date per source; until then stock / Cari outputs stay NOT_VERIFIED.
- **Real-data reconciliation:** required for every promotion; owner-local only.
- **Legacy `weekly_agent.py`:** a separate, unverified copy of the stock / lapsed rules; disabled (exit 2).
- **Lapsed window:** spec 2020–2025 vs code 2021–2025 (Q11).
