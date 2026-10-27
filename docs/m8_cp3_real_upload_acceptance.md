# M8-CP3 — owner-local real Excel upload acceptance (2026-10-26 / 27)

Privacy-safe report: report types, file basenames already recorded in Step 1, SHA-256 prefixes, counts, states and
issue / check codes only. No amounts, customer / product names or codes, or rows left the owner's Mac.

**Result: owner-local real Stok/Satış/Cari upload acceptance COMPLETE after correction.** All 25 acceptance conditions were tested; the sole preview display defect (unknown required-column state shown as ✓) was fixed by independently reviewed PR #33 (`6bcfb64`), squash-merged into `main` as `42b2d10` on 2026-10-27 (GitHub Actions `40526354833`: 1783 passed / 7 skipped / 2 xfailed). The original owner-local real-upload run occurred on the pre-fix build; the label fix passed synthetic browser regression tests and did not change validation or publication. No metric is VERIFIED; the dashboard is **not** ready for the father.

## How it was run

- **Code:** `main` @ `9c200db` (= PR #32 squash `e2b0e74` + two docs commits; the merged tree is byte-identical to the
  reviewed head `5b78a25`), copied without `.git` into an isolated folder on the owner's Mac
  (`~/Desktop/ME/Projects/_halsa_cp3_acceptance`) with its own Python 3.12.2 environment (fastapi 0.162.0,
  pandas 3.1.0, openpyxl 3.1.5, uvicorn 0.54.0), data folder, SQLite file, upload storage and dashboard.
  `STOCKAGENT_NO_DOTENV=1`, `AI_PROVIDER=disabled`, no API keys; server bound to **127.0.0.1:8765 only** and run
  natively on the Mac (the cloud sandbox cannot keep a server alive for a validation + restart).
- **Owner authentication:** throwaway owner and financial-viewer passwords provisioned with the repo's own
  `python -m services.owner_auth hash-password [--finance]` (driven over a pseudo-terminal), stored 0600 in the
  isolated folder, never displayed; the owner pasted the owner password from the clipboard into the login form.
- **Browser:** the Claude desktop app's built-in browser pane (Chromium) on the Mac, at `http://127.0.0.1:8765`.
  The owner logged in and chose the three real exports in the native file dialog; Claude chose the report types,
  left every declaration empty, ran validation, read the preview and published through the UI. Synthetic broken
  workbooks were added through the page's own file input (same upload handler as the dialog).
- **Owner's real checkout:** `~/Desktop/ME/Projects/StockAgent` stayed at `main` @ `c2a3df5`, clean; its `.env`,
  `data/` and (absent) SQLite file were not used or written. The checkout was not pulled.
- **Originals:** read only. Before and after the whole acceptance:

| Report type (chosen in the UI) | File (basename) | SHA-256 prefix | Size | mtime |
|---|---|---|---|---|
| Stok | `STOK 5 Ekim 2026.xlsx` | `63602ae3e8bf` | 1,657,601 | unchanged |
| Satış | `2020-5 ekim satışlar.xlsx` | `be1d895e426b` | 40,293,157 | unchanged |
| Cari | `Cari 5 Ekim 2026.xlsx` | `672fac2f5bf6` | 59,035 | unchanged |

Kredi, Gider/Gelir and Bütçe were **not** tested: no file of record is available (the revised filtered
`kredi takip(1).xlsx` is still missing; the owner workbooks on the Mac are May–June 2026 candidates).

## Starting state (before any upload)

Byte-identical copies of the three exports in the isolated data folder, published with the existing controlled
refresh (`python -m services.refresh`, legacy file-date selection `mtime_fallback`, data date 2026-10-05, ~100 s):
dashboard and manifest agree; restart re-derived the generation; gate 28 `invalid` + 5 `not_verified`, **0 VERIFIED**.
Veri Kontrol 24 open (17 high, 7 medium), in sync = Step 1's 23 core issues + `kredi / missing_source` (no Kredi file
configured in the isolated copy). Seeded synthetic history to check preservation: one CRM interaction and one
follow-up on a real customer code (never printed), one fair campaign, one Veri Kontrol review (`incelendi` + note).
No action could be seeded: the sales queue is withheld by the accuracy gate (409), so the action tables stay empty.

## Acceptance conditions

| # | Condition | Result | Evidence |
|---|---|---|---|
| 1 | All three real workbooks accepted without editing | **PASS** | upload 201 ×3; layout "Destekleniyor"; rows Stok 3,311 · Satış 182,121 (+ `liste`) · Cari 825 |
| 2 | Required sheets and columns recognised | **PASS** | every required column ✓ for the three (Stok: approved DinamikCRM template, product code column A) |
| 3 | File type from explicit selection, not file name | **PASS** | every type selector started at "— Rapor türünü seçin —", also for `STOK …`; types chosen in the UI |
| 4 | Reconciliation consistent with Step 1 | **PASS** | see below: 7 technical failures; 29 → 24 OPEN fully explained by the explicit selection |
| 5 | Veri Kontrol identifies the known problems | **PASS** | preview delta 0 new, 0 returning, 21 continuing, 3 resolved (`selection_not_explicit` ×3) |
| 6 | Missing declarations stay unknown | **PASS** | no field pre-filled from "5 Ekim 2026"; preview "bilinmiyor (beyan edilmedi)"; `sources.json` carries no declaration and no approval |
| 7 | Unverified metrics withheld | **PASS** | preview 33 of 33 hidden now and after; after publication 28 `invalid` + 5 `not_verified`, 0 VERIFIED; `GENERATION_BOUND_API_EVIDENCE` False |
| 8 | Live dashboard and records unchanged before publication | **PASS** | snapshot before vs after validation: only `upload_*` tables and the private upload store changed |
| 9 | Publication job completes | **PASS** | "Yayınlandı. Pano güncellendi" in 64 s (validation 58 s) |
| 10 | Uploaded files become the explicit selection | **PASS** | `sources.json` (`halsa-source-selection/1`): stock / sales / aging → content-addressed `yukleme-<sha>` files present in the data folder; manifest and status `selection_origin = explicit`, all sources bound |
| 11 | Dashboard and manifest agree | **PASS** | manifest dashboard digest = dashboard bytes; served `X-Halsa-Generation` = status `generation_id` |
| 12 | Selection files persist | **PASS** | same `sources.json` bytes after restart; `owner_sources.json` not created (no owner upload) |
| 13 | CRM, follow-ups, fairs, actions, Veri Kontrol history preserved | **PASS** | CRM / follow-up / fair tables byte-identical; action tables unchanged (empty); all 25 earlier VKM events kept, 24 appended; the review stays `incelendi` |
| 14 | Still withholds unverified metrics | **PASS** | as row 7 |
| 15 | No DinamikCRM connection | **PASS** | no such code path; the server process held only its 127.0.0.1:8765 listener (0 non-loopback connections) |
| 16 | Restart restores the same generation | **PASS** | same `core_id` / `generation_id`; `restore_reason` none; dashboard re-published from the re-derived evidence with identical bytes (the manifest file is rewritten by design) |
| 17 | Dashboard opens normally after restart | **PASS** | `GET /` 200, `lang="tr"`, generation header matches |
| 18 | Uploaded files and declarations still selected | **PASS** | as row 12; `/yukleme` "Şu anda yayında" lists the three with "tarih beyan edilmedi" |
| 19 | Veri Kontrol synchronised | **PASS** | `in_sync`, 21 open |
| 20 | CRM, fairs, action history intact after restart | **PASS** | private tables byte-identical to the post-publication snapshot |
| 21 | Owner login works; financial viewers cannot upload | **PASS** | owner re-logged in through the UI after the restart; financial session → `GET/POST /api/uploads` 403 `owner_required`; viewer 401; owner without CSRF 403 `csrf_failed`, foreign Origin 403 `origin_rejected`, without the local guard 403 |
| 22 | Malformed uploads | **PASS** | text file named `.xlsx` → 422 `not_xlsx`; truncated `.xlsx` → 422 `corrupt`; Turkish messages; nothing stored |
| 23 | Incorrect report type / validation failure | **PASS with defect** | real Cari export chosen as "Stok" + synthetic wrong-layout workbook as "Satış": validation `invalid`, blocking error `core_validation` ("Stok dosyasında gerekli sütunlar yok … Başlık satırı 4. satırda olmalı"), no publish button, publish 409 `not_ready`. **Defect:** the preview listed every required column of the unreadable files with ✓ (state unknown) |
| 24 | Invalid / stale publication attempts | **PASS** | wrong token on a published batch → 409 `not_ready`; a ready no-change preview whose own valid token is used after a newer upload was started → 409 `not_latest`, batch `superseded` |
| 25 | A failed attempt keeps the accepted dashboard and selection | **PASS** | after all failure tests: dashboard, manifest, both selection files, data folder, generation, gate, Veri Kontrol, CRM / fair / action tables identical to the post-restart snapshot; upload history append-only (UPDATE / DELETE triggers present) |

Not run on real files: a base-changing stale preview (another publication between preview and publish). There is no
natural way to produce it with one set of exports; it is covered by the D-067 race tests in CI.

## Reconciliation and Veri Kontrol (counts only)

- Upload preview (`Mutabakat durumu`): Stok 256 unmatched, 1 duplicate code, footer totals partial; Satış 287 unmatched
  (256 products + 30 order customers + 1 invoice account), 4 duplicate rows; Cari 45 unmatched; customer master list
  30 unmatched; totals "Doğrulanmadı / bilinmiyor", mapping "Onaylanmadı" for all.
- `scripts.reconcile_sources --core` on the published explicit selection: **FAIL — 7 technical failures, 24 OPEN**.
  The same three files with the legacy selection (no `sources.json`, as in Step 1): **7 technical failures, 29 OPEN**
  — identical to Step 1. The only difference is 5 OPEN items about the selection itself (4 × "Kaynak dosya açıkça
  seçilmedi", 1 × "data/sources.json yok"), which the explicit upload selection resolves. Join counts identical to
  Step 1 (6,754 order customers, 5,485 invoice accounts, 824 aging accounts, 2,713 sold vs 3,310 stock products,
  40,704 order-source ≠ invoice-party rows, 123 fallback rows, 1 trim-only match).
- Veri Kontrol after publication: **21 open** (14 high, 7 medium), 3 `cozuldu_kaynakta`, 1 `incelendi`, in sync.
  21 = Step 1's 23 core issues − 3 `selection_not_explicit` + `kredi / missing_source`. Codes: stock
  `stock_duplicate_code`, `stock_coverage_undefined`, `footer_partial`, `mapping_unknown`, `report_date_missing`;
  sales `sales_duplicate_rows`, `join_sales_product_not_in_stock`, `join_order_customer_not_in_master`,
  `join_invoice_account_not_in_master`, `dq5_multiple_invoice_accounts`, `code_whitespace`, `period_unknown`,
  `mapping_unknown`, `report_date_missing`; liste `master_duplicate_codes`; aging `join_aging_account_not_in_sales`,
  `dq6_aging_order_code_only`, `amount_blank`, `mapping_unknown`, `report_date_missing`; kredi `missing_source`.

## Defect and correction

**Unknown required-column state shown as ✓.** When the selected type cannot be read (wrong report type, unreadable
layout), the preview correctly reports each required column's state as unknown (`found: null`), but
`services/upload_ui.py` rendered every non-missing state with "✓ ", so the owner saw all columns ticked directly under
a blocking "required columns missing" error (also for the carried-over Cari report). Publication was never possible —
no data risk — but the preview contradicted itself. Correction (UI only): unknown → "? " (amber chip, tooltip
"Denetlenemedi") plus a note "dosya bu rapor türü olarak okunamadığı için sütunlar denetlenemedi"; found → "✓ ",
missing → "✗ " unchanged. Browser regressions in `tests/test_m8_cp3_browser.py` (fails on `main`, passes with the fix).

## Observations (not defects)

- Each owner session limit is 5 (`MAX_SESSIONS`); a test harness logging in repeatedly drops the oldest (browser)
  session — by design. The harness was changed to log out after each check.
- Two separate file selections each replace the previous upload error banner; each error remains in the network
  response and the history. Selecting several files at once shows each result.
- The built-in browser pane cannot paste into a password field programmatically or open the native file dialog for
  Claude; those two steps were done by the owner.

## Next smallest checkpoint toward the father-first launch

After the correction PR is merged: **owner business answers for the 7 technical reconciliation failures** (duplicate
stock code, 4 duplicate sales rows, 7 duplicate master codes, unmatched products / customers / invoice account, DQ-4
undefined coverage) and the **report dates of the 5 Oct exports**, recorded as decisions and declared in the upload
— the smallest step that can move any metric toward VERIFIED. In parallel, the hosting blockers remain: CP2
(single-process serving), CP5 (upload retention / cleanup), hosting region / KVKK sign-off.

The isolated acceptance folder on the Mac contains private data copies and should be deleted by the owner after review.
