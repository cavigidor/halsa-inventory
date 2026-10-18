# M7-6 — Yönetim (owner / management view) + integration, performance and polish

Decision: **D-047**. Code: `services/owner_view.py` (owner sources, compact views, status, known issues),
`services/sales_projection.py` (shared Customer 360 sales projection), `app.py` (`/api/owner/*`),
`dashboard_builder.py` (`_own_panel`, tab polish, DQ-3), `schemas.py` (`Owner*`). Tests: `tests/test_m7_owner.py`,
`tests/test_m7_owner_browser.py`.

This is the last planned M7 checkpoint. It adds read-only owner visibility and finishes the integration work. It
adds no scoring, workflow or attribution rule.

## 1. Purpose and audience

The **Yönetim** tab answers "how is the operation doing, what do our loans look like, what does the Gider/Gelir
workbook report, how healthy is the Bütçe workbook, and which data problems are still open?"

- Kredi, Gider/Gelir and Bütçe are `Audience.OWNER` sources (D-023 §13).
- **Local single-user caveat:** the application has no authentication. The tab is labelled as owner information
  ("Yönetim bilgisidir … erişim kontrollü DEĞİLDİR"). It is **not** access-controlled; anyone who can open the local
  dashboard can open the tab. A future multi-user deployment must enforce owner/admin roles (D-016).
- Everything is read-only, computed in Python, and never sent to AI.

## 2. Owner-source configuration (explicit paths only)

| Variable | Meaning | Importer call |
|---|---|---|
| `KREDI_PATH` | one explicitly chosen loan-contract export | `import_kredi(path, snapshot_date=KREDI_SNAPSHOT_DATE)` |
| `KREDI_SNAPSHOT_DATE` | optional, `YYYY-MM-DD`, declared | |
| `GIDER_GELIR_PATH` | one explicitly chosen Gider/Gelir workbook | `import_gider_gelir(path, report_year=GIDER_GELIR_REPORT_YEAR)` |
| `GIDER_GELIR_REPORT_YEAR` | optional, `YYYY` | |
| `BUTCE_PATH` | one explicitly chosen Bütçe workbook | `import_butce(path, planning_year=…, snapshot_date=…)` |
| `BUTCE_PLANNING_YEAR`, `BUTCE_SNAPSHOT_DATE` | optional | |

- Nothing is discovered: the `find_*_candidates` helpers are never called, no folder is listed, no file is ordered
  by modification time, and no file of record is decided. Configuring a file is not a file-of-record decision.
- **No owner source is required.** The dashboard, the core refresh, Customer 360, Günlük Satış, Fırsatlar, Fuar
  Merkezi and CRM work with none configured.
- An invalid optional value (a date that is not `YYYY-MM-DD`, a year that is not four digits) fails only that source
  (`config_invalid`).

## 3. Cache and reload lifecycle

- **Lazy:** nothing is read at server start. The first `GET /api/owner/overview` (or `/status`), which happens when
  the Yönetim tab is opened, starts one background load of every configured source.
- **Cached for the process lifetime** as compact derived views: counts, a list of `(currency, due date, remaining
  amount)` tuples for Kredi, monthly / per-line totals for Gider/Gelir, health counts for Bütçe. No importer result,
  workbook row, cell or frame is kept. Later GETs read no Excel.
- **Explicit reload:** the "Yönetim verilerini yeniden oku" button calls `POST /api/owner/reload` (needs
  `X-StockAgent: 1`). It re-reads only the configured owner workbooks.
  - It never refreshes the core sales / stock / Cari sources, never touches the live context, never calls AI.
  - It is ignored while a load is in flight (`started: false`); the button is disabled meanwhile.
  - Each source is replaced atomically when its own load finishes, and only if that load's generation is still the
    current one, so a late result never overwrites a newer one. The previous view stays visible during a reload
    (`reloading: true`); a source that fails on reload shows the failure instead of stale data.
- **Separate workflows:** `POST /api/context/refresh` never reloads owner files, and `POST /api/owner/reload` never
  refreshes core data. A server restart resets the owner cache.
- **Isolation:** each source has its own state `not_configured | loading | ready | failed`. One failure (missing
  file, invalid configuration, importer failure, exception) never blocks or discards another source. Failures show a
  generic Turkish message, the importer's failed step and its error codes, never a path or cell content.

## 4. Sections

### A. Operasyon Özeti (counts only, from live state)
Customer 360 customers; open Günlük Satış customers; collection-first customers; CRM follow-ups overdue / due today;
due fair tasks; active fair campaigns; cross-sell candidate customers; CRM coverage ("Eksik kayıt olabilir").

- The queue counts come from `build_queue(..., upsert=False)`, only when the live context and the Customer 360
  snapshot share their sources (otherwise "hazırlanıyor").
- **Cross-sell broadness:** the share of active customers with a target is computed from the live opportunity model
  (never hard-coded). At ≥ 50 % the neutral note "Çapraz satış modeli geniş bir aday kitlesi üretiyor; eşikler yönetim
  tarafından henüz gözden geçirilmedi." appears. Thresholds are unchanged.
- No person or salesperson counts or rankings.

### B. Finansman / Kredi — "Şirket Finansmanı / Kredi Takibi"
Halsa's own bank-loan installments. Not customer credit, Hedef Risk/Kredi or receivables.

- Shown: contracts, lenders (count), currencies, status counts, `Kaynak anlık tarihi` (declared or "bilinmiyor"),
  `Son kayıtlı ödeme tarihi`, and per currency the next unpaid installment date and the **scheduled obligations**.
- **Obligation = an installment with `remaining_amount` (KALAN TAKSİT TUTARI) > 0.** The importer verifies
  `paid + remaining = installment` (fatal), so a partly paid installment counts only its remaining amount;
  `installment_total` is never used.
- **DURUM is not used to drop obligations.** It is an unverified field. In the real Feb 2026 export, 24 of 25
  contracts say "Kapalı", yet 152 of their installments are unpaid (remaining > 0, no payment slip, due after the
  latest payment). They stay in the schedule and are counted in a warning
  (`status_closed_with_remaining`).
- **Horizons:** cumulative calendar windows from the local current date: today ≤ due ≤ today + 30 / 60 / 90 days,
  plus "90 gün sonrası". Past-due remaining amounts are a **separate** bucket ("Vadesi geçmiş (ayrı)") and are not
  added to 30/60/90: they may have been paid after the export.
- **Freshness is separate:** a horizon relative to today does not mean the workbook is current. Without a declared
  snapshot: "Kaynağın kesin anlık tarihi bilinmiyor; ödeme planı kaynakta kayıtlı kalan tutarlara dayanır." It is
  never called a live bank balance.
- **Currencies are never summed together**; no FX conversion.
- **No remaining-principal total:** `KALAN ANAPARA` repeats a schedule-state value on every installment row and is not
  additive; a single contract-level current principal cannot be derived unambiguously, so no company-wide total is
  shown (`remaining_principal_total` is always `null`).
- **Wording:** "Planlı kredi yükümlülükleri", never "nakit akış tahmini". Supplier payments, receivables timing,
  purchasing, tax and payroll are not combined.
- Lender names and contract codes never leave the importer; only counts.

### C. Gider / Gelir (source-native)
- Shown: report year (and origin), `Revizyon etiketi`, `Kaynak anlık tarihi` (unknown), months with source data, and
  per month the reported sales, cost, gross profit and expense.
- **Totals only where M6 verifies them:** monthly sales / cost / gross profit are the sum of the four native-line
  blocks (the workbook's TOPLAM block equals that sum, a fatal control); monthly expense is the sum of the expense rows
  (the column-totals row equals it, a fatal control). Gross profit is the source value; no `sales − cost`.
- The four native lines **KIRTASIYE / TRODAT / TROTEC / IMALAT** stay separate (sales, cost, gross profit, allocated
  expense). There is no mapping to kaşe / kırtasiye / other-import (`management_mapping = not_supplied`).
- The source-reported budget column (`Bütçe <Y>` of ÖZET) is shown as source values only; no variance.
- A prominent note says the accounting basis (cash vs accrual) is unverified; values are never called cash or
  accrual revenue / expense.
- Warnings (counts): accounting basis unverified, management mapping not supplied, manual allocation overrides,
  annual-total mismatches, gross profit entered separately, ratios outside 0–1, pasted ÖZET pivot mismatches,
  template header year. No expense names or cost centers are exposed.
- No legacy M5 margin, no profitability calculation, no Bütçe reconciliation.

### D. Bütçe Sağlığı (validity / coverage only)
Planning year, revision label, snapshot status, source status, candidate cells, usable (unverified) cells, invalid
cells, coverage %, `#REF!`, `#DIV/0!`, other Excel errors, missing cached formulas, external links and externally
dependent cells. No financial values. The warning "Bütçe kaynağı kısmen kullanılabilir; geçersiz / dış bağımlı hücreler
otomatik karar veya bütçe-vs-gerçek analizinde kullanılmaz." stays visible. Nothing is repaired; no budget-vs-actual;
no comparison with Gider/Gelir (file of record unresolved, invalid cells and an external dependency, accounting basis
unverified, management mapping unresolved).

### E. Veri ve Sistem Durumu
- **Core sources:** sales / stock / Cari aging basenames, `Veri tarihi`, context state.
- **Hedef:** configured or not, basename, status.
- **Owner sources:** each with state and basename.
- **Operational stores:** Customer 360 state and customer count, CRM coverage, fair campaign count, local SQLite
  available.
- **Blocked:** Detaylı Tahsilat — "Sağlanmadı / M6-4 bloke".
- **Açık kararlar / veri eksikleri** (structural, read-only, not settings): DQ-1 margin, DQ-2 KAPALI in Stok Eritme,
  DQ-4 stock-coverage blind spot (live count of products whose coverage cannot be computed), DQ-5 legacy M5 attributes
  (new M7 screens use the Customer 360 master architecture; M5 unchanged), CRM incomplete coverage, Detailed
  Collections missing (blocked), revenue basis, cross-sell thresholds not reviewed, Hedef stale capacity / Risk /
  Kredi, owner-source files of record, fair decisions, salesperson of record. None is marked fixed.

Only basenames appear anywhere (API, UI, logs); never a home, Downloads or full path.

## 5. API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/owner/overview` | all sections; starts the lazy owner load on first call |
| GET | `/api/owner/status` | system status + known issues + owner source states |
| POST | `/api/owner/reload` | guarded; owner workbooks only |

Typed, strict responses (`extra="forbid"`), `ai_used: false`, `access_controlled: false`. No cell-level payloads.

## 6. Performance (profiled first)

Real-data profile on the Mac VM (aggregates only; the VM's speed varied up to 2× between runs, so comparisons are
within one session):

| Step (before) | Time |
|---|---|
| Context build (core sales read dominates) | 30–84 s |
| Customer 360: second `pd.read_excel` of the same sales workbook | ≈ 26–44 s |
| Customer 360: M6-2 order-source import (framework read, both openpyxl views) | 50–100 s |
| Customer 360: master `liste` sheet | ≈ 2–5 s |
| Customer 360: aggregates / aging | ≈ 2 s |

**Change:** `build_context` now computes the Customer 360 sales projection (the unchanged `prepare_sales`, moved to
`services/sales_projection.py`) from the frame it has already read, before `load_sales` modifies that frame, and
hands it privately to the first Customer 360 build of that context object (`SalesFrameHandoff`). The second sales
read is gone.

- The handoff is not a context dict item, never serialized, never pickled with data, never in evidence packets, and
  released after one use. A basename mismatch or a frame lacking a required column falls back to reading the file.
- A refresh A → B carries B's projection on B's context object, so the swap stays atomic.
- Only the compact projection (≈ 156 MB on 182k lines) is kept, and only until Customer 360 consumes it; the raw frame
  is not retained.

| Same session | Customer 360 build | Customer 360 build peak RSS |
|---|---|---|
| before (re-read) | 93.9 s, 101.8 s | ≈ 860 MB |
| after (handoff) | 64.0 s, 76.0 s, 53.1 s | ≈ 740–755 MB |

Context build peak RSS: 493 MB before, 502 MB after.

**Not changed (why):** the M6-2 order-source import is the remaining large step. It is the only path that validates
the order-source columns fail-closed (formulas, Excel errors, blank `Srmmerk`, label conflicts per order). The pandas
frame cannot detect formulas or error cells, so replacing that import with the in-memory frame would weaken M6-2
validation. The Customer 360 event section therefore uses exactly the same import as before and is unchanged. A
single-pass reader (both views of each cell in one XML pass) would roughly halve it; that is framework work, deferred.

## 7. DQ-3 fixed

`m_momentum` sorted by `pct` only after iterating a `set`, so equal-pct rows came out in hash-seed order. It now sorts
by `(pct, customer code)`. Values and membership are unchanged; only the order of equal-pct rows is fixed. Tests prove
identical output across insertion orders and `PYTHONHASHSEED` values, and the synthetic dashboard is byte-identical
across seeds. On the real data the M5 top 15, collections, winback, risk, stock lists, company block, shrinking list
and the Günlük Satış reservoir are identical to `main`.

## 8. Not fixed (owner approval needed)

DQ-1 (margin), DQ-2 (KAPALI in Stok Eritme), DQ-4 (no-coverage stock rule), DQ-5 (legacy M5 attributes). They are
listed in the known-issues panel. Yönetim never uses the M5 margin.

## 9. Polish

- Tab strip: wraps, visible keyboard focus, `aria-current` on the active tab, 36 px touch height on phones.
- Günlük Satış: a late response of an older filter never renders over a newer one (request token).
- Labels added to the Fuar Merkezi selection checkboxes and the CRM "plan in the same entry" fields.
- All M7 tabs checked at 390 px (letter-spacing stress, hostile names): no page-level horizontal overflow.

## 10. AI separation and privacy

No owner data reaches AI, evidence packets, actions, drafts or logs; `owner_view.py` imports no agent, evidence or
provider module and does not log. Responses carry aggregate owner amounts only for the local UI. Lender names,
contract codes, expense names, cost centers, budget cell contents and paths are never exposed.

## 11. Real-data owner smoke (explicit paths for the smoke only; not a file-of-record decision)

| Source | Result (counts only) |
|---|---|
| Kredi (`kredi sözleşmeleri.xlsx`) | ready; 25 contracts, 188 installments, 5 lenders, 1 currency; snapshot unknown; 158 unpaid installments: 7 / 11 / 15 within 30 / 60 / 90 days, 15 after 90 days, 128 past due vs today (the export is from Feb 2026, so many may be paid since); 152 of them carry `Kapalı` status (warning) |
| Gider/Gelir (`2026_gider_gelir_Eylül.xlsx`) | ready; 2026 (filename), 9 months; 108 revenue, 176 expense rows, 2,112 expense months, 84 summary records; 6 budget lines; warnings: manual overrides 23, annual mismatch 1, gross profit separate 21, ratio outside 0–1 1 |
| Bütçe (`BUTCE_2026 Ocak.xlsx`) | ready; partially usable; 2,975 candidates, 2,790 usable, 70 invalid, coverage 99.89 %, `#REF!` 67, `#DIV/0!` 3, 1 external link, 3 dependent cells |
| Load / reload | 1.9–5.3 s / 1.1–2.7 s; overview 3–9 ms; sources unchanged (hash + mtime); 0 AI calls |

A browser smoke on the Mac VM was not possible (no Chromium there and its network cannot download one); the API smoke
covered statuses, sections, reload and path privacy.

## 12. M7 completion boundary

With this checkpoint M7 is complete: Customer 360, Günlük Satış, Fırsatlar, Fuar Merkezi, Takip / CRM and Yönetim
are usable; ranking stays in Python; there is no automatic AI; warnings stay visible; local state persists across
restarts and refreshes. M6-4 remains blocked by a missing export; that does not block M7. Remaining work is listed in
TASKS.md as backlog / future milestones.
