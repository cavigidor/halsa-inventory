# M8-ACCURACY — Metric register

_Status: 2026-10-18, checkpoint M8-ACCURACY (branch `claude/m8-accuracy-fail-closed`, from `main` @ `7f3202d`);
updated by the correction pass after the independent review of PR #27 (see `release_gate.md` §7)._
_Source of truth in code: `services/accuracy.py` (`REGISTER`, `DEFINITIONS`, `ROUTES`). This document mirrors it and
adds the audit evidence. If they disagree, the code wins and this file must be corrected._

Owner directive: **NO VERIFIED EVIDENCE = NO BUSINESS CONCLUSION.** Every analytical output has one internal state:

| State | Meaning | Shown to the owner? |
|---|---|---|
| `VERIFIED` | Source identified and validated; report date known; required inputs present; deterministic calculation; arithmetic and reconciliation checks pass; joins validated; business definition approved; automated regression coverage; the displayed description matches what the calculation establishes. | Yes |
| `NOT_VERIFIED` | Possibly correct, but freshness, completeness, mappings, business meaning or approval is insufficient. | **No** — neutral notice |
| `INVALID` | A known error, inconsistency, missing required field, invalid formula, unsupported operation or failed validation. | **No** — neutral notice |

Notice text (Turkish, shown instead of the output): **"Bu analiz henüz doğrulanmadığı için gösterilmiyor."** plus at
most four short reasons (no numbers, names, paths or exception text).

## How a decision is made (`services/accuracy.Gate.decide`)
1. **Certified ceiling.** The register entry's `certified` state. `VERIFIED` is only accepted by `validate_register()`
   with no open blocker, every definition approved by a `D-xxx` decision, named regression tests and a real-data
   reconciliation reference. **No entry is certified `VERIFIED` today.**
2. **Blockers** (known, documented problems) and **unapproved definitions** downgrade the state.
3. **Runtime evidence** for each source actually read (`SourceEvidence`) can only downgrade: Excel errors, missing
   cached formula results, missing required values, duplicate records, join problems, contradictory totals,
   unsupported layouts, unapproved mappings, failed inspections. **Every field starts unknown (None) and unknown is
   never "passed"**; a failed inspection is `INVALID`, never "0 errors". Missing evidence for a source = `NOT_VERIFIED`.
   **Time** is judged per metric: an *operational* metric (current state) needs a known report date no older than its
   `max_source_age_days` (45); a *historical* metric (a closed past period) needs a known report date, a dated
   `period_end` and a complete period, with no age limit. Both need verified completeness. Age never approves.
4. **Generation binding (M8-ACCURACY-2).** Evidence comes from one source-generation manifest
   (`services/generation.Manifest`): every core and owner source is recorded with the SHA-256 of its **original
   workbook bytes**, its selection origin, declared dates, validation counts and `SourceEvidence`. Each source is read
   once and every parse uses those bytes. The dashboard HTML embeds the manifest's `core_id`
   (`stockagent-generation`); the API binds each request to the published manifest **only for the sources whose
   served fingerprint equals the recorded one** (`Manifest.bound_snapshot`); any other source gets no evidence and the
   code `EVIDENCE-GENERATION:<source>`. A refresh installs the context and the manifest together and restores both on
   failure; an owner reload replaces only that owner record; a restart restores the manifest only if the dashboard and
   every core file still match it. `GET /api/accuracy/status` shows the generation and the per-metric state (no
   values). `validate_register(production=True)` still refuses any VERIFIED certification while
   `GENERATION_BOUND_API_EVIDENCE` is False — it stays False until the owner's real-data acceptance
   (`release_gate.md` §8.3).
5. **Dependencies.** A combined output is the worst of its own state and every output it depends on.
6. Any error while deciding, an unknown key or an unclassified API route = `INVALID` (fail-closed).

Enforcement points (backend / shared response layer, never only JavaScript):
- **Dashboard HTML** — `dashboard_builder.build(..., gate=None)`: a withheld module is emitted with `rows: []`,
  `cols: []` and a `withheld` notice; withheld stat cards are left out; withheld server-driven panels (`sq`, `c360`,
  `opp`) are replaced by a notice **without their script**; the AI panel keeps only server status + controlled refresh.
  `GET /` refuses a dashboard file that lacks the `stockagent-accuracy` policy marker (built before this gate).
- **API** — `app._accuracy_gate` middleware: every route is classified in `ACC.ROUTES`; a metric-bound route returns
  HTTP 409 `{"detail": {"error_code": "accuracy_withheld", …}}` before the endpoint, the provider or SQLite is touched;
  an unclassified route is withheld. Mixed endpoints redact field by field (`ACC.redact_*`).
- **Refresh** — `services/refresh.run_refresh` returns only verified counts (`products` = stock row count, system
  information); plausibility warnings never print an unverified total.
- **Legacy weekly e-mail** — `weekly_agent.py` refuses to run (exit 2).

Verification of real data: **unavailable in this environment** (no ERP exports in the cloud/CI checkout; real-data
tests are owner-local `local_data`). Nothing below claims a real-data reconciliation.

---

## 1. Summary

| Key | UI section | API | State today | Shown |
|---|---|---|---|---|
| `stock.reorder` | Azalan Stok tab; stat "min altı" | `/api/context` (via `context.full`) | NOT_VERIFIED | no |
| `stock.overstock` | Fazla Stok tab; stats "maks üstü", "fazla stok değeri" | via `context.full` | NOT_VERIFIED | no |
| `stock.clearance_buyers` | Stok Eritme tab | via `context.full`, `agent.product_facts` | **INVALID** (DQ-2) | no |
| `customers.winback` | Geri Kazanım tab; stats "kayıp müşteri", "borçlu kayıp müşteri"; tier line | via `context.full`, actions | **INVALID** (DQ-6) | no |
| `customers.momentum` | Büyüme / Küçülme tab | via actions (`real_decline`) | NOT_VERIFIED | no |
| `customers.order_size` | Sipariş Büyüklüğü tab | — | NOT_VERIFIED | no |
| `finance.margin` | Kâr Marjı tab | — | **INVALID** (DQ-1) | no |
| `receivables.risk` | Riskli Müşteri tab | via `context.full` | NOT_VERIFIED | no |
| `receivables.collections` | Tahsilat tab; stat "vadesi geçen toplam" | via `context.full`, actions | NOT_VERIFIED | no |
| `actions.real_decline` | (AI Aksiyon Merkezi / Günlük Satış reason) | via actions | NOT_VERIFIED | no |
| `actions.queue` | ✨ AI Aksiyon Merkezi list | `GET /api/agent/actions` | INVALID (dependencies) | no |
| `ai.interpretation` | AI comments | `POST …/enrich`, `POST …/analyze` | NOT_VERIFIED/INVALID | no |
| `ai.drafts` | message drafts | `POST /api/agent/draft-message` | INVALID (dependencies) | no |
| `agent.customer_facts` | AI drawer | `GET /api/agent/customer/{code}` | INVALID (dependencies) | no |
| `agent.product_facts` | — | `GET /api/agent/product/{code}` | INVALID (dependencies) | no |
| `context.full` | — | `GET /api/context` | INVALID (dependencies) | no |
| `opportunities.cross_sell` | Fırsatlar tab; C360 card; queue reason | `GET /api/opportunities/summary` | NOT_VERIFIED | no |
| `opportunities.territories` | Fırsatlar territory table | `GET /api/opportunities/territories` | NOT_VERIFIED | no |
| `sales_queue` | Günlük Satış tab | `GET /api/sales-queue` | INVALID (dependencies) | no |
| `customer360.profile` | Müşteri 360 tab | `GET /api/customer360/{code}` | NOT_VERIFIED | no |
| `fairs.history` | Fuar Merkezi history; campaign ERP evidence | `GET /api/fairs/history[/{id}]`; fields in campaign APIs | NOT_VERIFIED | no |
| `fairs.segments` | Fuar Merkezi candidates; member segment chips | `GET /api/fairs/candidates`; fields | INVALID (dependencies) | no |
| `crm.active_customers` | CRM tile "Aktif müşterilerden kaydı olan" | field in `GET /api/crm/summary` | NOT_VERIFIED | no |
| `owner.kredi` | Yönetim → Kredi | field in `GET /api/owner/overview` | NOT_VERIFIED | no |
| `owner.gider_gelir` | Yönetim → Gider/Gelir | field in `GET /api/owner/overview` | **INVALID** (contradictory totals) | no |
| `owner.butce` | Yönetim → Bütçe | field in `GET /api/owner/overview` | **INVALID** (#REF!/#DIV/0!, external link) | no |
| `owner.operations` | Yönetim → operational counts | field in `GET /api/owner/overview` | INVALID (dependencies) | no (CRM/fair counts stay) |
| `scenario.receivables` | — | `POST /api/scenario` | NOT_VERIFIED | no |
| `collections.detailed` | — (no surface) | — | NOT_VERIFIED — **source missing** | — |
| `trodat.sales` | — (no surface) | — | NOT_VERIFIED | — |
| `customer.last_order_date` | search hits, fair members | field in `/api/customer360/search`, fair member APIs | NOT_VERIFIED | no |
| `customer.master_status` | KAPALI badge in search, CRM, fair members | fields `closed` / `master_status` in search, CRM, fair member APIs | NOT_VERIFIED | no |

Non-metric surfaces (classified in `ACC.ROUTES`; not business conclusions):
- **system**: `/`, `/docs`, `/openapi.json`, `/redoc`, `/api/health`, `/api/agent/categories`, `/api/ai/calls`
  (metadata only), `/api/macro` (always "unavailable"), `/api/customer360/status`, `/api/owner/status` (system status
  + known issues; the cross-sell count is redacted).
- **redacted directory**: `/api/customer360/search` returns only the customer code and recorded display name; the
  derived last-order date and the open / KAPALI status are withheld (`customer.last_order_date`,
  `customer.master_status`), with notices. The same two fields are withheld in CRM follow-up / customer responses and
  fair member rows. The conservative KAPALI safeguards (no sales follow-up or invite for a trusted-KAPALI customer)
  keep working server-side: they restrict an action and display no analysis.
- **operational** (user-entered records and state changes): CRM interactions / follow-ups / lists, fair campaign
  create / update / archive / add members, action state (`/api/actions/*`), owner reload. Analytic fields attached to
  them are redacted (`crm.active_customers`, `fairs.history`, `fairs.segments`).
- **redacted refresh**: `POST /api/context/refresh` (counts and warnings).

---

## 2. Per-output audit

Field legend: **UI** · **API** · **Source** (workbook / sheet / columns) · **Identity** · **Function** · **Formula**
· **Filters** · **Missing data** · **Business date** · **Completeness** · **Reconciliation** · **Definition approval**
· **Known problems** · **State** · **Shown**.

### `stock.reorder` — Azalan Stok
- **UI:** dashboard tab `reorder`; overview stat "min altı". **API:** inside `/api/context` (`low_stock`).
- **Source:** stock export `STOK <date>.xlsx`, header row 4; `Stok_Kodu`, `Stok_Adı`, `TOPLAM STOK MİKTAR `,
  `STOK Kontrol %` (cached ERP formula), `minseviye`, `maxseviye`, `DURUM`.
- **Identity:** `Stok_Kodu` as read (no join).
- **Function:** `dashboard_builder.m_stock`.
- **Formula:** rows with `maxseviye > 0`, coverage present, `coverage < minseviye`, not `ÜRETİMDEN KALKTI`; sorted by
  `coverage / minseviye`; `crit` when coverage < 0.
- **Missing data:** coverage coerced with `pd.to_numeric(errors='coerce')`; rows with missing/error coverage are
  dropped silently (DQ-4). Stock quantity missing → 0 in the `ins` test.
- **Business date:** none read; the file is chosen by modification time (`select_sources` → `_latest`).
- **Completeness:** **not complete** — on the 5 Oct 2026 export 1,178 of 3,314 products have `#DIV/0!` coverage and
  never appear (`docs/m6_source_inventory.md` §12).
- **Reconciliation:** refresh checks `low_stock_count` context ↔ dashboard equality only (same code path).
- **Definition approval:** coverage rule for no-coverage products pending (owner question #9).
- **Known problems:** DQ-4; unknown report date.
- **Regression tests:** `tests/test_domain_rules.py::test_stock_low_excludes_discontinued_and_overstock_flags_it`.
- **State:** NOT_VERIFIED (DQ-4, STOCK-DATE, REAL-DATA). **Shown:** no.

### `stock.overstock` — Fazla Stok
- **UI:** tab `overstock`; stats "maks üstü", "fazla stok değeri". **API:** `/api/context` (`company.working_capital.overstock_value`).
- **Source:** as above + `Envanter değeri` (cached export column).
- **Function / formula:** `m_stock`: `maxseviye > 0`, coverage present, `coverage > maxseviye`, stock ≠ 0; sorted by
  `Envanter değeri`; value total = Σ `Envanter değeri` of listed rows.
- **Missing data:** same DQ-4 exclusion; missing inventory value → `_n()` returns 0 (a substituted zero).
- **Business date / completeness:** as `stock.reorder`.
- **Definition approval:** inventory valuation basis of `Envanter değeri` is undocumented; DQ-4 rule pending.
- **State:** NOT_VERIFIED (DQ-4, STOCK-DATE, INV-VALUE, REAL-DATA). **Shown:** no.

### `stock.clearance_buyers` — Stok Eritme
- **UI:** tab `osmove` (call list per overstock product). **API:** product facts / actions (overstock category).
- **Source:** overstock rows + sales `sto_kod`, `Nettutar`, `miktar`, `Tarih` (2024–2026).
- **Identity:** buyer = order-source customer `_cust` (D-003).
- **Function:** `dashboard_builder.m_overstock_move` (and `services/context.score_buyers` for actions).
- **Formula:** per overstock SKU, customers who bought it 2024–2026, sorted by quantity, top 12.
- **Filters:** **no KAPALI filter in the dashboard list** (DQ-2; the AI context filters since D-009).
- **Missing data:** `Nettutar` missing → 0 (`load_sales`). `score_buyers` looks up overdue by order-source code in an
  invoice-account map (same identity issue as DQ-6).
- **State:** **INVALID** (DQ-2 + dependency on `stock.overstock`). **Shown:** no.

### `customers.winback` — Geri Kazanım
- **UI:** tab `winback`; stats "kayıp müşteri", "borçlu kayıp müşteri"; tier distribution line. **API:** `/api/context`
  (`winback`), actions (`winback`), drawer.
- **Source:** sales `Pro_kodu`/`ProjeIsmi`/`Musterikod`, `Tarih`, `Nettutar`, cached `Temsilci`/`Açık / Kapalı`/`Sektör`;
  Cari aging (`Hesap kodu` + month buckets) for the overdue column.
- **Identity:** D-003 order-source customer; **overdue looked up with the order-source code in a map keyed by the
  invoice account** (`m_lapsed`: `owed.get(str(c), 0)`).
- **Function:** `dashboard_builder.m_lapsed` (+ `cust_attrs`).
- **Formula:** total over `QUAL_YEARS` (2021–2025) ≥ 30,000; no 2026 purchase; strong year > 10,000; T1 ≥ 3 strong
  years, T2 = 2, T3 = 1 only if 2024/2025.
- **Filters:** KAPALI (from invoice-account lookups, DQ-5) excluded.
- **Missing data:** missing `Nettutar` summed as 0; customers not found in aging show overdue 0.
- **Business date:** 2026 hard-coded as the lapsed year; sales as-of = max `Tarih`.
- **Definition approval:** window 2020 vs 2021 open (TASKS *Blocked*, owner question #11); revenue basis open (#3).
- **Known problems:** **DQ-6 (new, this checkpoint):** an order-source customer invoiced through another account that
  owes money shows overdue 0 / "Yok" — pinned by `tests/test_m8_accuracy.py::test_dq6_winback_overdue_identity_mismatch`
  and the strict xfail `test_dq6_winback_never_claims_zero_overdue_for_a_debtor_invoice_account`. Also DQ-5, AMT-ZERO.
- **Regression tests:** `tests/test_domain_rules.py::test_lapsed_tiers`, `test_lapsed_thresholds_are_the_configured_ones`.
- **State:** **INVALID**. **Shown:** no.

### `customers.momentum` — Büyüme / Küçülme
- **Source / identity:** sales lines, D-003. **Function:** `m_momentum`.
- **Formula:** year-to-date (up to the data date's day of year) 2025 vs 2026 per customer; included when 2025 ≥ 30,000
  and 2026 > 0; `pct = (2026 − 2025) / 2025`. Sorted by `(pct, code)` (DQ-3 fixed).
- **Missing data:** missing `Nettutar` → 0. **Filters:** KAPALI via DQ-5 lookups.
- **Label check:** column header says "Oca–bugün" while the window ends at the **data date**, not today.
- **State:** NOT_VERIFIED (REVENUE-BASIS, AMT-ZERO, DQ-5, REAL-DATA). **Shown:** no.

### `customers.order_size` — Sipariş Büyüklüğü
- **Function:** `m_ordersize`. **Formula:** 2024–2026 revenue and distinct order documents per customer; included when
  ≥ 3 orders and ≥ 30,000; AOV = revenue / orders; change = AOV 2026 vs 2025.
- **Missing data:** missing `Nettutar` → 0; zero-value (free-goods) documents count as orders.
- **State:** NOT_VERIFIED (REVENUE-BASIS, AMT-ZERO, DQ-5, REAL-DATA). **Shown:** no.

### `finance.margin` — Kâr Marjı
- **Function:** `m_margin`. **Formula:** 2025–2026, revenue ≥ 50,000, margin = Σ `Net_Tutar_Maliyet_Dusulmus` / Σ `Nettutar`.
- **Missing data:** missing cost → `fillna(0)` = counted as zero profit (DQ-1).
- **Regression tests:** strict xfail `tests/test_domain_rules.py::test_dashboard_margin_does_not_treat_missing_cost_as_zero`.
- **State:** **INVALID**. **Shown:** no.

### `receivables.risk` — Riskli Müşteri
- **Identity:** invoice account (`Musterikod` ↔ Cari `Hesap kodu`, D-003 consequence). **Function:** `m_risk`.
- **Formula:** accounts with positive aging balance; revenue 2025–2026 by invoice account; `ratio = overdue / revenue`;
  "2026 Sipariş" = any 2026 line; KAPALI tag if ≥ 50 % of the account's lines say KAPALI (cached lookup).
- **Missing data:** blank aging buckets → 0; missing `Nettutar` → 0.
- **Definition approval:** overdue meaning of the buckets, revenue basis, salesperson of record — all open.
- **State:** NOT_VERIFIED. **Shown:** no.

### `receivables.collections` — Tahsilat
- **Source:** Cari aging `Cari <date>.xlsx`: `Hesap kodu`, `Hesap adı`, `Temsilci kodu`, month buckets
  (`<Ay-YYYY> ve öncesi`, monthly columns); `Toplam Döviz Bakiye` ignored.
- **Function:** `m_collections`. **Formula:** balance = single "vadesi geçen" column if present, else Σ month
  buckets; accounts with balance > 0; oldest = first non-zero bucket header.
- **Missing data:** blank buckets → 0. **Business date:** not read (bucket headers carry months; file chosen by mtime).
- **Definition approval:** that every bucket is "overdue" (vs. not yet due) and the currency treatment are unconfirmed.
- **Regression tests:** `tests/test_domain_rules.py::test_receivable_aging`, `test_kapali_kept_in_risk_and_collections`.
- **State:** NOT_VERIFIED (AGING-DATE, OVERDUE-DEF, REAL-DATA). **Shown:** no.

### `actions.real_decline` — Reel Satış Düşüşü
- **Function:** `services/context.build_context` + `services/growth.calculate_real_growth` +
  `action_sources.produce_real_decline`. **Formula:** nominal YTD change deflated by `MANUAL_INFLATION_RATE`.
- **Definition approval:** the inflation figure is a manual environment value (Milestone 4 not built).
- **State:** NOT_VERIFIED. **Shown:** no.

### `actions.queue` — AI Aksiyon Merkezi (deterministic queue)
- **Function:** `services/actions`, `services/action_sources`, `services/scoring`. Ranks collections, overstock,
  win-back and real-decline actions with transparent scores.
- **Definition approval:** score weights and `COLLECTIONS_THRESHOLD` are not owner-approved.
- **State:** INVALID (depends on DQ-6 / DQ-2 outputs). **Shown:** no. The panel keeps the server status and the
  controlled refresh button only.

### `ai.interpretation`, `ai.drafts` — AI text
- **Function:** `services/agent.py` (grounded, numeric guard). **Policy:** model text is an interpretation, never
  evidence; withheld in production regardless of `AI_PROVIDER`. Default provider stays `disabled`
  (`tests/test_m8_accuracy.py::test_default_ai_provider_is_disabled`). No automatic AI call exists; the explicit POST
  endpoints are refused before the provider is reached
  (`test_ai_is_off_and_never_called_even_with_a_provider_available`).
- **State:** NOT_VERIFIED / INVALID. **Shown:** no.

### `agent.customer_facts`, `agent.product_facts`, `context.full`
- Composite read endpoints over the outputs above (`services/entity.py`, `services/evidence.py`, `/api/context`).
- **State:** INVALID (dependencies). **Shown:** no.

### `opportunities.cross_sell` — Çapraz satış
- **Source:** sales lines (`anagrup`, `Tarih`, customer). **Function:** `services/opportunities.build_cross_sell`.
- **Formula:** active cohort = buyers in the last 365 days before the data date; anagrup pairs with support ≥ 20/20/10,
  confidence ≥ 0.30, lift ≥ 1.20; target = a group the customer never bought; score = min(69, confidence × 100).
- **Known problems:** ~78 % of active customers receive a target (TASKS); a historical association is not intent.
- **State:** NOT_VERIFIED (XSELL, ACTIVE-WINDOW, REAL-DATA). **Shown:** no.

### `opportunities.territories`
- Hedef city / district / sector counts joined to the cross-sell model (`services/opportunities.territories`).
- **State:** NOT_VERIFIED (Hedef file of record; stale fields; depends on cross-sell). **Shown:** no.

### `sales_queue` — Günlük Satış
- `services/sales_queue.build_queue`: one card per customer from collections / win-back / real decline / cross-sell
  candidates plus CRM and fair follow-ups. **State:** INVALID (dependencies). **Shown:** no. Due CRM follow-ups
  remain visible in the CRM tab (operational).

### `customer360.profile` — Müşteri 360
- `services/customer360.py`: full customer population, yearly "Net Sipariş Tutarı", invoice-account receivables (no
  allocation), master attributes from `liste` (DQ-5-safe), optional Hedef. Missing amounts are counted, never zero.
- **Open:** revenue basis; trailing-space code policy; salesperson of record; Hedef file of record; aging date.
- **State:** NOT_VERIFIED. **Shown:** no (the directory search stays as a reference lookup).

### `fairs.history`, `fairs.segments`
- `services/fairs.py` FUAR-only history (orders, customers, Net Sipariş Tutarı by edition); `services/fair_center.py`
  segments (historical FUAR, active, win-back, real decline, cross-sell, collections).
- No conversion, attendance, cost attribution or ROI exists or is computed (D-045) — preserved.
- **State:** NOT_VERIFIED / INVALID. **Shown:** no. Campaigns, members, stages, dates and notes (user-entered) stay.

### `crm.active_customers`
- `services/crm.summary`: "active" = last order within 365 days of the data date. **State:** NOT_VERIFIED.
- Interaction and follow-up records are operational; coverage stays `incomplete` ("Eksik kayıt olabilir"); missing
  records are never read as "not contacted" (D-046).

### `owner.kredi`
- `services/importers/kredi.py` (identities fatal), `services/owner_view.kredi_schedule` (unpaid remaining by due date,
  per currency, 30/60/90). `DURUM` never drops an installment (D-047). **Open:** DURUM / Kapalı meaning (answer due
  2026-10-19), snapshot date (optional `KREDI_SNAPSHOT_DATE`), file of record (Feb 2026 vs June 2026 files).
- **State:** NOT_VERIFIED. **Shown:** no. No outstanding bank-debt figure is published.

### `owner.gider_gelir`
- `services/importers/gider_gelir.py`, `owner_view.build_gider_gelir_view` (source-native lines, months, budget column).
- **Known problems:** `<Y> TOPLAM ≠ Σ months` is a warning (1 real row differs); ÖZET pivot vs expense table mismatches
  are warnings; 23 manual allocation overrides; template header year carry-over; snapshot date unknown.
- **Open:** accounting basis (cash vs accrual), business-line mapping (owner question #4).
- **State:** **INVALID**. **Shown:** no. No profitability is computed.

### `owner.butce`
- `services/importers/butce.py` cell-level validity and coverage (never repaired). **Known problems:** `#REF!`,
  `#DIV/0!`, external link to `2026_gider_gelir_TASLAK.xlsx`, file of record undecided (D-041).
- **State:** **INVALID**. **Shown:** no. No budget variance is computed.

### `owner.operations`
- `services/owner_view.operations`: queue / cross-sell counts are withheld; CRM counts and campaign counts stay.

### `scenario.receivables`
- `services/scenarios.run_scenario` over `overdue_total` and request assumptions. **State:** NOT_VERIFIED.

### `customer.last_order_date`, `customer.master_status` (directory attributes)
- **Last order date:** `services/customer360.py` = max `Tarih` of the customer's order lines; cancelled
  (`Vazgecilen`) and undelivered (`Kalan`) parts count; trailing-space code policy open; no report-date / completeness
  verification. **State:** NOT_VERIFIED. **Shown:** no.
- **Master status:** `snap.master_closed` from `liste` `CARİ FİRMA`; the attribute source of record is open (Q10,
  DQ-5); ~10 duplicate master codes. **State:** NOT_VERIFIED. **Shown:** no (operational safeguards stay).

### `collections.detailed`, `trodat.sales`
- No display surface. Detailed collections: source `Evrak Detaylı Tahsilat Raporu` **missing** (M6-4 blocked; never
  derived from Cari aging). Trodat: native importer only; SKU mapping and units unverified (D-039).

---

## 3. Unfinished audit areas (explicit)
- **Real-data reconciliation** of every metric: not possible here; owner-local only.
- **Formula-aware reading of the core sources:** the M5 path reads stock / sales / Cari with pandas, which turns Excel
  errors and missing cached results into NaN. The collector counts them as missing required values (INVALID), but
  cannot tell error, missing cache and blank apart. Routing the core sources through the M6 framework is future work.
- **Join validation** (`SourceEvidence.joins_validated`) is not computed for any source; it stays unknown → NOT_VERIFIED.
- **Generation-bound evidence for the API, owner views and refresh** (mandatory M8-ACCURACY-2 criterion).
- **Formula-aware core reading:** pandas cannot report missing cached formula results, so `missing_cached_formulas`
  stays unknown for the core exports (NOT_VERIFIED).
- **Control totals** (`totals_consistent`) are not available for the core exports; unknown → NOT_VERIFIED.
- **Customer 360 field-level audit** (each card), **Günlük Satış scoring** and **fair edition arithmetic** were audited
  at the module level only, because each is withheld as a whole.
