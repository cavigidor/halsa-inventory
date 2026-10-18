# M7-1 — Searchable Customer 360

**Goal:** search any customer and see one deterministic, confidence-aware view of their sales history, customer-master
data, invoice accounts and receivables, Hedef context, event/fair history and existing actions and follow-ups.

It is the first M7 product checkpoint: a new **Müşteri 360** dashboard tab backed by a dedicated repository. Python
computes every fact. No AI is called on page load, search, selection or rendering, and no Customer 360 data is sent
to an AI provider. Decision: D-042.

## Architecture

```
live M5 context (ContextRepository) ──► source signature ──► Customer360Repository (cache, one snapshot)
        │                                                          │
        │ basenames + as_of_data_date                              ▼
        └──────────────► DATA_FOLDER/<sales basename> ──► build_snapshot()
                         DATA_FOLDER/<aging basename>       ├─ sales lines (pandas, read once) → aggregates
                         HEDEF_PATH (explicit, optional)    ├─ liste (M6-1 framework spec) → master index
                                                            ├─ Cari aging (account level)
                                                            ├─ M6-2 import_order_sources → event history
                                                            └─ M6-3 import_hedef (only if configured)
GET /api/customer360/status | search?q= | {code}  ◄── in-memory indexes (no Excel read per request)
```

- **`services/customer360.py`:**
  - `Customer360Repository`: cache plus signature check, background build in the app.
  - `build_snapshot`: one read of each source.
  - `Customer360Snapshot`: indexes, search and structural `summary()`.
  - `customer_detail`: assembles one customer from the indexes.
- **Separate from the agent context.** `services/context.py` stays minimized and top-N; nothing is added to it or to
  `/api/context`. Customer 360 covers the **full customer population** of the sales workbook (6,754 canonical
  customers on the real 5 Oct 2026 data), not the top-N, action, winback or collections lists.
- **API routes:** `GET /api/customer360/status` (with `?retry=true` to retry a failed build),
  `GET /api/customer360/search?q=&limit=` and `GET /api/customer360/{customer_code}`. All are read-only GETs with
  Pydantic schemas (`schemas.Customer360*`, `C360*`). There is no POST.
- **M5 is untouched.** The M5 drawer endpoint `GET /api/agent/customer/{code}` and every M5 behavior are unchanged.

## Sources

| Source | Used | Grain / rule |
|---|---|---|
| Main sales workbook | **the exact basename of the live context** (`source_files.sales`), resolved inside `DATA_FOLDER` (plain basename only) | sales-order lines; commercial history |
| Customer master | the `liste` sheet of **the same workbook** (M6-1 framework spec) | exact code match |
| Cari aging | the live context's aging basename, if any | **invoice account** level only |
| Order source (`Srmmerk`) | M6-2 `import_order_sources` on the same workbook | order-document level |
| Hedef | **only** `HEDEF_PATH`, plus an optional `HEDEF_SNAPSHOT_DATE` (ISO) | exact code match |

**Never used:** the owner-only sources (Kredi, Gider/Gelir, Bütçe; `Audience.OWNER`), and Trodat (no customer
identity, no approved SKU mapping). There is no automatic choice between Hedef files: without `HEDEF_PATH`, the
Hedef section reads "yapılandırılmadı" and everything else works.

## Source consistency and cache

- **Signature:** sales basename, `as_of_data_date`, aging basename, the context's `generated_at`, and the Hedef path
  and snapshot.
- **On every access** the live context's signature is compared with the cached snapshot's. If they differ, the cached
  snapshot is **dropped at once** and never served against newer data, and a rebuild starts. A controlled refresh
  (new context installed through `replace_context`) therefore always leads to a rebuild from the new sources. The
  refresh code is not changed.
- **Date check:** after reading the sales workbook, its max `Tarih` must equal the live `as_of_data_date`. If not, the
  build fails with `source_date_mismatch` ("…verileri yenileyin."). Mixed snapshots are never served. No mtime is
  used anywhere.
- **No second sales read (M7-6, D-047):** the first build for a context consumes the private, one-use projection of
  the sales frame that `build_context` already read (`services/sales_projection.py`, same `prepare_sales`). Without it
  (another basename, missing columns, a later rebuild) the build reads the workbook as before. The master sheet and
  the M6-2 order-source import still read the workbook through the framework (their fail-closed validation).
- **App mode:** a missing or stale snapshot starts one background build thread. Requests get 503
  `{"state": "building"}`, and the UI polls `/status`. The startup warm-up builds the context, then Customer 360.
  - **Tests and CLI** use `background=False`, which builds synchronously.
- **Errors:** fixed codes and Turkish text, never a path or a stack trace (for example `sales_source_missing`,
  `sales_columns_missing`, `source_date_mismatch`, `build_failed`).

## Canonical identity (D-003, exactly as M5)

- **Canonical customer:** `Pro_kodu` as written; `Musterikod` only when `Pro_kodu` is genuinely empty (missing,
  blank, or the text `nan`, the same rule as `dashboard_builder.load_sales`).
- **Codes are kept exactly:** never trimmed, never case-folded, never fuzzy-matched. A code with a trailing space stays
  a distinct code: it is shown with a warning and does not match the master row without the space. The open
  whitespace policy is not decided here.

## Display name

1. The unique customer-master name (`ÜNVANI 1`), when the code resolves to exactly one master row.
2. Otherwise the canonical sales name (`ProjeIsmi`).
3. The invoice-account name (`Musteri_ismi`), only when the D-003 fallback is used.
4. Otherwise the code.

`display_name_source` says which rule applied. There is no fuzzy name matching.

## Search

- **Not entity resolution:** it is UI text search over code and display name, with a minimum of 2 characters, a
  default of 20 results and a maximum of 50. There is no AI and no fuzzy scoring.
- **Order:** exact code → code prefix → name prefix → name substring → code substring. Ties sort by folded name, then
  code. There is no sales-volume ranking.
- **Folding:** NFC, Turkish-aware lower case, and `ı`/`i` treated alike (so `İKİ` finds `İki`).
- **Results show:** name, code, a KAPALI badge (only when the master knows it) and the last order date. They never
  show financial rankings.

## Customer-master attributes (DQ-5-safe for M7)

- **Origin:** from `liste` only, keyed by the canonical code.
  - Trusted: `CARİ FİRMA` (`Açık` / `Kapalı`) and `ÜNVANI 1`.
  - Unverified: `SEKTÖR` and `TEMSİLCİ` (current assignment only).
- **Not used:** the sales workbook's cached `Temsilci` / `Açık / Kapalı` / `Sektör` lookup columns.
- **M5 unchanged:** legacy `cust_attrs` (DQ-5 stays open for M5 output; a test pins it).
- **Duplicate master code:** status `duplicate`. Rep, sector, name and KAPALI are **not** taken from any row. Sales
  history is kept, and a warning is shown.
- **Status values:** `matched`, `unmatched` or `unavailable` (sheet missing or unreadable). Customer 360 still works
  in every case.

## Sales history (from the trusted sales lines)

- **Facts:** first and last order date, distinct orders (documents), lines, active years, orders in the as-of year,
  orders in the last 365 days, distinct products.
- **Yearly:** amount, quantity, orders and lines per year.
- **Amount label:** **"Net Sipariş Tutarı (Nettutar)"**, the source-recorded ordered net amount. The canonical
  revenue basis (ordered vs delivered) is **not decided**, so it is never labelled plain "Ciro". Delivered revenue is
  not computed.
- **Missing amounts stay missing:** they are counted and never summed as zero. A recorded zero stays zero.
- **No margin or profitability** (DQ-1).

## Products and categories

- **Top 10 products** (`sto_kod`) by source-recorded `Nettutar`: name, quantity, orders, lines, last order date,
  category and group.
- **Mixes:** `kategori` and `anagrup` (top 8 each) with shares of the customer's total.
- **Facts only:** no cross-sell, no Trodat mapping.

## Invoice accounts and receivables

- **Two concepts, kept apart:** `Pro_kodu` (canonical customer) ≠ `Musterikod` (invoice account). For each invoice
  account used by the customer's orders, Customer 360 shows the orders, lines, amount and first/last order date, plus
  whether the account is **shared** with other canonical customers.
- **Aging is per invoice account:** the overdue balance and the oldest non-zero bucket, with the same column rules as
  M5 `m_collections`.
- **Shared accounts:** the balance is shown on the account with the warning *"Bu fatura hesabı birden fazla sipariş
  müşterisi tarafından kullanılıyor; alacak bu müşteriye tek başına atanamaz."* There is **no customer-level total
  and no allocation.**
- **Limitations, stated in the UI:** no document due dates, no per-document overdue days, no document type, no
  document-level priority. *"Detaylı tahsilat raporu sağlanmadı."*
- **M6-4 stays blocked:** no `TAHSİLAT ÖNCELİKLİ`, no substitute.

## Hedef (optional, explicit)

- **Exact code match** against the configured file. The section shows its real D-023 status per field:
  - city, district, sector code, Hedef `Temsilci`, `Müşteri Temsilcisi`, discount tier, yearly stamp quantity and
    source-reported net revenue: `unverified`;
  - historical weekly stamp capacity, Risk, Kredi: **`stale`**, badge **"Eski veri"**;
  - visit count: **`incomplete`**, badge **"Eksik kayıt olabilir"**.
- **Never calculated from these fields:** no potential, penetration, market share, opportunity rank, credit
  blocking, risk labelling or collection priority. The values are display-only.
- **Snapshot date:** declared (`HEDEF_SNAPSHOT_DATE`) or unknown.
- **The configured file is not the file of record**; that decision is still open.

## Salesperson concepts (no salesperson of record)

These are shown separately with their source labels and confidence:
- order `Satici` (top values with order counts);
- master `TEMSİLCİ`;
- Hedef `Temsilci`;
- Hedef `Müşteri Temsilcisi`.

None is chosen as *the* salesperson.

## Event and fair history

- **From M6-2:** orders by canonical type (`general`, `fuar`, `etkinlik`, `saha`), kept **separate**, and distinct
  events per type.
- **Recent event orders:** the 10 latest, with order date, type, edition year and the local raw event label.
- **No fair grouping:** no conversion window, no follow-on attribution, no ROI.
- **Event names stay local:** they appear in the local UI only. They are never logged, never in `summary()`, never
  in AI packets.

## Actions and history (read-only)

- **Current deterministic actions** for the customer, from the live context, with their stored status (open,
  deferred, completed, ignored).
- **Stored history:** earlier actions from SQLite (`store.get_entity_actions`, a new read-only helper) and
  `memory` follow-up records (contacted, offer, outcome, followup, rec_accepted, rec_rejected).
- **No changes:** no new action types, no scoring change, no CRM write forms.

## Confidence badges (UI)

| API value | Badge |
|---|---|
| `trusted` | Güvenilir |
| `unverified` | Doğrulanmadı |
| `incomplete` | Eksik kayıt olabilir |
| `stale` | Eski veri |
| `invalid` | Geçersiz |
| `unavailable` | Yok |

The API always carries the canonical value; the badge is display-only.

## UI

- **Tab and loading:** a **Müşteri 360** tab placed before the AI tab. It is lazy: nothing is fetched until the tab
  is opened. It polls `/status` while building, then offers search (with a 250 ms debounce).
- **Results:** buttons with a KAPALI badge.
- **Detail:** warnings, a compact source-status strip and seven cards:
  1. Kimlik
  2. Satış geçmişi
  3. Ürünler / kategoriler
  4. Cari / alacak
  5. Hedef bilgileri
  6. Fuar / etkinlik geçmişi
  7. Aksiyonlar ve geçmiş

  An unavailable source says so instead of disappearing.
- **Data-date check:** a warning appears if the dashboard file's data date differs from the server's.
- **Safe rendering:** all workbook text is set with `textContent` / safe DOM APIs. There is no `innerHTML` in the
  tab script (tested, plus hostile names and event labels in browser tests).
- **`file://`:** *"Müşteri 360 için panoyu http://127.0.0.1:8000/ üzerinden açın."* There are no requests, and the
  static modules still work.
- **Phone width:** single column, tables scroll inside their card, long names wrap. No horizontal page overflow
  (tested at 390 px).
- **Rest of the dashboard unchanged:** the tab is a marked block (`<!--C360-TAB-START-->…END`). Removing it gives the
  build without it byte-for-byte (tested).

## Privacy

- **Paths:** the API returns basenames only, never absolute paths. Errors carry fixed text.
- **Logs:** one structural build line (counts, statuses, milliseconds) and uvicorn access lines. Customer records,
  names and event labels are never logged.
- **`summary()`:** structural only (counts, basenames, dates, statuses, Hedef match rate).
- **AI separation:** nothing from Customer 360 enters `services/evidence.py` or any AI packet. A 404 may echo the code the local caller supplied.

## Real-data check (read-only, Mac VM, 5 Oct 2026 exports; aggregates only)

**Index:**

| Item | Result |
|---|---|
| Indexed customers | 6,754 |
| Orders / lines | 38,215 / 182,121 |
| Invoice accounts | 5,485 |
| Customers with several invoice accounts | 712 |
| Shared invoice accounts | 173 |
| D-003 fallback customers | 27 |

**Sources:**

| Item | Result |
|---|---|
| Master resolution | 6,718 matched · 30 unmatched · 6 duplicate (7 duplicate master codes) |
| KAPALI known | 95 (unknown for 36) |
| Display names | 6,718 from the master · 36 from `ProjeIsmi` |
| Aging accounts | 824 (779 linked to sales accounts) |
| Customers with event orders | 1,635 (3,051 event orders) |
| Hedef (one explicitly supplied file, smoke only; not authoritative) | 6,427 of 6,754 matched (95.2 %) |

**Performance:**

| Item | Result |
|---|---|
| Build time | 74.5 s (sales read 23 s, M6-2 event import 49 s; 85 s with Hedef) |
| Peak memory | ≈ 820 MB (build process) |
| Detail / search | ≈ 3 ms / ≈ 1–3 ms in process; 41 ms / 14 ms over HTTP |
| Real server | ready 109 s after start (context + Customer 360) |

**Proof checks:**
- 0 AI calls.
- Every source workbook hash- and mtime-identical afterwards.
- Scratch deleted.
- No identities, names, event labels or amounts recorded.

## Deferred (later M7 checkpoints)

- **M7-2:** the daily sales queue and opportunity engine (built on Customer 360).
- **M7-3:** cross-sell, potential and territory intelligence.
- **M7-4:** the January Fair Command Center.
- **M7-5:** lightweight CRM and follow-up writes.
- **M7-6:** owner/management views, integration and polish.
- **Not in M7-1:** ranking or scores of any kind, AI explanations of Customer 360 data (needs a separate review),
  customer-level receivable allocation, Detailed Collections.
