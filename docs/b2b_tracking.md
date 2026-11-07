# B2B Takip — father's B2B call lists (D-080)

Two separately named, offline, read-only November reports help Halsa decide whom to call about its B2B platform:

| Report | Generator | Base |
|---|---|---|
| `Halsa_Kasim_2026_Klasik_B2B.html` (first priority) | `python classic/klasik_b2b.py` | the frozen original Klasik builder, unmodified |
| `Halsa_Kasim_2026_Genel_B2B.html` | `python -m services.father_report --b2b` | the V1 general report (D-079) |

Both use one calculation, `halsa_b2b.py` (standard library + pandas + openpyxl only). The original Klasik Rapor
(`classic/generate.py` + `classic/original_builder.py`) and the V1 report without `--b2b` are unchanged.

## Definitions (father, 2026-11-07)

| Term | Rule |
|---|---|
| B2B sale | Sales export, **physical column AG** (header in the November export: `Satici`); the cell, as text, is exactly `B2B`. Other values (salesperson names) are other channels. A value containing "b2b" in another spelling is *unclear*: that customer's B2B figures become unknown (—). AG is located by position and the header row must equal the analysed frame's columns, otherwise every B2B figure is unknown. |
| B2B registration | `b2b_kullanici_rapor.xlsx`, first worksheet, row 1 = header, **physical column B** = customer code. |
| Customer | The dashboards' existing identity: `Pro_kodu`, else `Musterikod` (D-003), computed by each dashboard's own `load_sales`. |
| Confirmed registered | The customer code appears in column B **exactly** (no trimming, no fuzzy matching, leading zeros as stored). |
| Ambiguous (never counted as registered) | Matches only after removing surrounding spaces, or only the customer's invoice account (`Musterikod`) is in the list. Shown as "Belirsiz" with the codes in the warnings. |
| Registration list missing | "Liste yok" — unknown, never "not registered". Lists A and B are not produced; the B2B column shows —. |
| Sipariş | Distinct `sip_evrakno_seri|sip_evrakno_sira` order documents (order, not invoice), never lines. A document with B2B and other lines is *mixed*: counted once per channel and listed for verification. |
| Amounts | `Nettutar` as read. Missing → the affected total is unknown (—), never 0. Zero / negative lines are kept; duplicate lines are not removed. |
| "Son zamanlarda" | **Provisional** 365 days (`--b2b-review-days`), counted back from the latest transaction date in the sales file — never the computer's date. Not an approved rule. |
| KAPALI | The dashboards' existing rule (at least half of the customer's lines `KAPALI`) removes the customer from the call lists; counts are shown. |

### The three call lists (B2B Takip tab, right after Kâr Marjı)

- **A · Bizden alışveriş yapan, B2B kaydı olmayan müşteriler** — customers with sales lines whose status is not
  "confirmed" (listede bulunamadı / belirsiz). Columns: customer, code, representative, sector, last-365-day purchases,
  total purchases in the file, last purchase, registration status, "B2B'den alım" (Evet = has AG = B2B lines although
  not in the list — to check). Sorted by recent purchases.
- **B · B2B kaydı olan, ancak B2B'den alışveriş yapmayan müşteriler** — registration codes without B2B lines:
  "Diğer kanallardan alıyor", "Satış verisinde eşleşme yok" (name / representative unknown; Excel row shown) or
  "Belirsiz" (whitespace / invoice-account only). An invoice-account-only code that does have B2B lines is not put here
  (warning B2B-FATURA-B2B).
- **C · Eskiden B2B'den alıp son zamanlarda almayan müşteriler** — at least one B2B line, none in the review window.
  Columns: last B2B purchase, historical B2B amount, B2B order count, last-365-day other-channel amount and orders,
  registration status. Channel-based only, so it is produced even without the registration list.

Each list has search (incl. codes), representative filter, sorting and (Klasik) the customer detail.

### Other changes

- **Büyüme / Küçülme:** a "B2B" column between Temsilci and Sektör (V1: after "Kaynak temsilci"). `B2B` only for
  confirmed codes, `Belirsiz` (dashed amber) for ambiguous, empty otherwise, — when the list is missing. Rows, order,
  formulas, thresholds and inclusion are unchanged (tested).
- **Customer detail (Klasik):** `Yıl | Harcama | Sipariş | B2B Alımı | Sipariş (B2B)` in every expandable tab.
  Harcama and Sipariş are the original values; "Sip. Ort." is replaced as requested; `*` marks mixed documents.
  "Öne Çıkan Ürünler" uses the original markup and data (tested).
- **V1 Yıllık Müşteri Alışverişi:** adds B2B Alımı, Sipariş (B2B) and Karışık kanal belgesi after "Tam Nettutar".
  The V1 report keeps its design of separate yearly/product tables (no nested detail), all existing masks, review boxes,
  missing-cost handling and retired D-070 / D-071 outputs. The B2B section has the V1 review box (not a registered
  metric, not an API, not an action or AI input).

## How the Klasik variant reuses the original

`classic/klasik_b2b.py` runs the frozen builder in a separate process (same isolation as `generate.py`: role checks,
Cari layout check, `PYTHONHASHSEED=0`, no new-Halsa module may load). It observes the builder instead of copying it:
the frame that `load_sales` reads is kept at its single `pd.read_excel` call, `build()` runs every original calculation,
and the original `_html` renders the original modules plus the three additions. One style block and one script are
appended before the template's fixed ending (asserted). Tests prove the original modules, notes, rows and products are
unchanged apart from the added fields, and that the original Klasik report and its golden test are untouched.

## Generate (owner's Mac, private)

Read-only copies, original file names, outputs inside the private `reports/private/` area (gitignored).

```sh
STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled .venv/bin/python classic/klasik_b2b.py \
  --stock STOK_1_11_26.xlsx --sales "1_Kasım_2020_2026 satışlar.xlsx" \
  --b2b-registration b2b_kullanici_rapor.xlsx \
  --out reports/private/b2b-tracking/Halsa_Kasim_2026_Klasik_B2B.html --date 2026-11-07

STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled .venv/bin/python -m services.father_report --b2b \
  --data <private folder with sources.json> --b2b-registration b2b_kullanici_rapor.xlsx \
  --output reports/private/b2b-tracking/Halsa_Kasim_2026_Genel_B2B.html
```

Both refuse to overwrite. Without `--b2b-registration` the reports say the list is missing.

## November acceptance (counts only)

Run 2026-11-07 on the owner's Mac (isolated Linux VM, Python 3.12.15, pandas 3.1.0, openpyxl 3.1.5, AI disabled) from
`git archive` of baseline `9084b11` plus this branch's four runtime files (hash-identical), on read-only copies.
Originals unchanged before/after (sales `c23967fe…` 40,173,681 B; stock `5cba8f55…` 1,656,117 B; same mtimes).

**`b2b_kullanici_rapor.xlsx` was not found** in `~/Desktop/ME/Projects` or `~/Downloads` (only an older
`B2B Kullanıcı Giriş Raporu.xlsx`, a login report from the December 2025 transfer folder, which was not substituted).
The November reports were therefore generated in the "registration list missing" state: A and B are empty with an
explicit notice, the B2B column shows —, and C is complete.

| Fact | Count |
|---|---:|
| Sales lines / AG = `B2B` lines | 183,764 / 23,329 |
| AG values containing "b2b" in another spelling / blank AG | 0 / 0 |
| B2B order documents / mixed B2B + other documents | 5,683 / 0 |
| B2B lines with zero `Nettutar` / negative / missing / undated | 1,864 / 0 / 0 / 0 |
| Customers with any B2B line / with a B2B line in the window | 860 / 423 |
| Latest transaction / window (exclusive start) | 2026-10-30 / 2025-10-30 |
| **C** (B2B earlier, none in 365 days), both reports | **415** (22 KAPALI excluded) |
| A / B | not produced (registration list missing) |

Independent openpyxl reconciliation (`verify.py`, private): **101,731 checks, 0 discrepancies** — C key set and every
C field in both reports, 8,723 Klasik yearly detail entries (original Harcama and distinct-document Sipariş, B2B
amount, B2B documents, mixed), 13,488 V1 yearly rows, tab position, column position, identical C rows and warnings in
both reports. Original Klasik vs Klasik B2B on the same files: **7,699 checks, 0 discrepancies** (only the requested
additions differ). Offline DOM check of both real files (jsdom, page scripts run, nothing fetched): 28 + 29 checks
passed, 0 JS errors — every tab, A/B/C views and counts, code search, representative filter, sorting, five-column detail
in Winback / Büyüme / Sipariş Büyüklüğü, untouched "Öne Çıkan Ürünler", V1 yearly B2B columns and product history.
Chromium could not run in the Mac VM (browser downloads blocked) and the desktop browser pane cannot open local files;
Chromium was exercised on the synthetic reports (`tests/test_b2b_tracking.py`).

| File | Bytes | SHA-256 |
|---|---:|---|
| `reports/private/b2b-tracking/Halsa_Kasim_2026_Klasik_B2B.html` | 2,968,298 | `00c5d9bc2a04e28151d3aaebd1f980cece13cef7d8212e36bcd49e06c200469f` |
| `reports/private/b2b-tracking/Halsa_Kasim_2026_Genel_B2B.html` | 15,667,748 | `f024daf6744dbab217c5b9e89874f42f058f2cab18c8854f7038bba4a527091a` |

Re-generate both once the registration file is supplied (new names or after moving these aside; nothing overwrites).

## Questions for the father before ambiguous cases count as confirmed

1. Where is the current `b2b_kullanici_rapor.xlsx`, and is column B the ERP customer code (`Pro_kodu`)?
2. Is a B2B user opened for the ordering customer or for the invoice account / dealer (`Musterikod`)? (Decides the
   "Belirsiz (fatura hesabı kayıtlı)" cases.)
3. Are codes with extra spaces the same customer as the listed code?
4. Customers with AG = `B2B` lines but no code in the list: under which code is their B2B user defined?
5. What does "son zamanlarda" mean — 6 months, 12 months, other?
6. Can one order document hold B2B and other-channel lines? (0 in November.)
7. Are zero-amount B2B lines (1,864 in November) free goods, cancellations or something else — should they count as
   B2B purchases for list C?
8. Should KAPALI customers stay out of the call lists?

Screenshots (synthetic data): `docs/images/b2b_takip_synthetic.png`, `docs/images/b2b_takip_C_synthetic.png`,
`docs/images/b2b_customer_detail_synthetic.png`.
