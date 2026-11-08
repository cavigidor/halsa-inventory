# B2B Takip — father's B2B call lists (D-080; one table with İzahat: D-082)

Two separately named, offline, read-only November reports help Halsa decide whom to call about its B2B platform:

| Report | Generator | Base |
|---|---|---|
| `Halsa_Kasim_2026_Klasik_B2B_Tam.html` (first priority) | `python classic/klasik_b2b.py` | the frozen original Klasik builder, unmodified |
| `Halsa_Kasim_2026_Genel_B2B_Tam.html` | `python -m services.father_report --b2b` | the V1 general report (D-079) |

Both use one calculation, `halsa_b2b.py` (standard library + pandas + openpyxl only). The original Klasik Rapor
(`classic/generate.py` + `classic/original_builder.py`) and the V1 report without `--b2b` are unchanged.

## Definitions (father, 2026-11-07)

| Term | Rule |
|---|---|
| B2B sale | Sales export, **physical column AG** (header in the November export: `Satici`); the cell, as text, is exactly `B2B`. Other values (salesperson names) are other channels. A value containing "b2b" in another spelling is *unclear*: that customer's B2B figures become unknown (—). AG is located by position and the header row must equal the analysed frame's columns, otherwise every B2B figure is unknown. |
| B2B registration | `b2b_kullanici_rapor.xlsx`, first worksheet, row 1 = header, **physical column B** = customer code; the header must be `Kod No`. A wrong-layout, empty or unreadable workbook is treated like a missing one (never "zero registrations"). Only column F `Kullanıcı Cari` is also read, to label codes that never appear in sales; user names and e-mail addresses are never read. |
| Customer | The dashboards' existing identity: `Pro_kodu`, else `Musterikod` (D-003), computed by each dashboard's own `load_sales`. |
| Confirmed registered | The customer code appears in column B **exactly** (no trimming, no fuzzy matching, leading zeros as stored). |
| Ambiguous (never counted as registered) | Matches only after removing surrounding spaces, or only the customer's invoice account (`Musterikod`) is in the list. Shown as "Belirsiz" with the codes in the warnings. |
| Registration list missing | "Liste yok" — unknown, never "not registered". Lists A and B are not produced; the B2B column shows —. |
| Sipariş | Distinct `sip_evrakno_seri|sip_evrakno_sira` order documents per customer and year (the dashboards' established rule; order, not invoice), never lines. Identifiers recorded for more than one customer or year are counted and reported; a *mixed* document (B2B and other lines) is judged within one customer only, counted once per channel and listed. |
| Amounts | `Nettutar` as read. Missing → the affected total is unknown (—), never 0. Zero / negative lines are kept; duplicate lines are not removed. |
| "Son zamanlarda" | **Provisional** 365 days (`--b2b-review-days`), counted back from the latest transaction date in the sales file — never the computer's date. Not an approved rule. |
| KAPALI | The dashboards' existing rule (at least half of the customer's lines `KAPALI`) removes the customer from the call lists; counts are shown. |

### One table with İzahat (B2B Takip tab, right after Kâr Marjı) — D-082

Father's feedback on the delivered report (2026-11-08): "go by the rightmost column instead of four choices; B2B record
should be VAR or YOK, not 'bulunamadı'; with four choices there is no 'Tümü'". The four buttons are replaced by **one
table** (default: Tümü) with **B2B Kaydı** and **İzahat** as the last two columns and an **İzahat** select next to the
representative filter. The classification below (A / B / C / Doğrulanacak, kept internally as `views`) is unchanged;
each row of it appears exactly once, plus the registered customers that no call list showed (B2B buyers in the window).

| İzahat (filter label) | B2B Kaydı | From |
|---|---|---|
| B2B kaydı var · B2B'den alıyor | VAR | registered (exact), B2B line in the window — previously in no list |
| B2B kaydı var · eskiden B2B'den alıyordu | VAR | C, registered |
| B2B kaydı var · B2B'den hiç almamış | VAR | B, other channels |
| B2B kaydı var · bu kodla satış yok | VAR | B, registration-only code (separate row; name from the list, no representative) |
| B2B kayıt listesinde yok | **Listede yok** (YOK only with the owner's declaration) | A |
| Kontrol · fatura hesabı (bayi) kayıtlı | Kontrol | Doğrulanacak |
| Kontrol · B2B'den alıyor, kodu listede yok | Kontrol | Doğrulanacak (+ "eskiden B2B'den alıyordu" when also in C) |
| Kontrol · boşluk farkı / kayıt kodu satışta başka biçimde | Kontrol | Doğrulanacak |
| Kontrol · kodu listede yok, kanal yazımı belirsiz | Kontrol | Doğrulanacak (no list match; may be a B2B buyer) |
| B2B kaydı var · kanal yazımı belirsiz | **VAR** | registered (exact) with an ambiguous AG spelling — registration and channel kept apart |

- **YOK is never inferred.** An exact-code miss is not proof of no registration. "Listede yok" becomes "YOK" only with
  `--b2b-list-complete` (both generators): the owner's declaration that `b2b_kullanici_rapor.xlsx` lists every B2B user.
  Kontrol rows never become YOK; without a usable list the column is "—" and the table shows only the former B2B buyers.
- A sales customer and a registration-only code are always separate rows (a code differing only by spaces is not merged).
- KAPALI customers stay out (existing rule). Columns: customer, code, representative, sector, last-365-day purchases,
  total, last purchase, last B2B purchase, B2B total, B2B Kaydı, İzahat. Search covers every column incl. İzahat.

- **Registration identity and sales channel are separate:** an exact Kod No match is VAR whatever the AG spelling; the
  İzahat explains the unverified channel (B2B amount stays "—"). Kontrol is only for an unresolved registration identity
  (space difference, invoice account, a code not in the list but with B2B or B2B-like purchases).
- **Klasik paging (B2B Takip only):** the frozen renderer draws every row, which took 0.5–1.8 s per interaction at
  7,000 rows. The shared script pages B2B Takip by 200 rows; representative filter, search and sort run over every row of
  the current İzahat choice (the original rules) before one page is drawn, and the rows are shipped apart
  (`b2b_rows`) so the start-up draw of every panel no longer renders the full table. V1 keeps its own paging.

In the Klasik report the customer detail inside B2B Takip is built when a row is first opened (same markup and data);
the original tabs are rendered exactly as before.

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
  --out reports/private/b2b-tracking/Halsa_Kasim_2026_Klasik_B2B_Tam.html --date 2026-11-07

STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled .venv/bin/python -m services.father_report --b2b \
  --data <private folder with sources.json> --b2b-registration b2b_kullanici_rapor.xlsx \
  --output reports/private/b2b-tracking/Halsa_Kasim_2026_Genel_B2B_Tam.html
```

Both refuse to overwrite. Without `--b2b-registration` the reports say the list is missing.

## November acceptance (counts only)

Run 2026-11-07 on the owner's Mac (isolated Linux VM, Python 3.12.15, pandas 3.1.0, openpyxl 3.1.5, AI disabled) from
`git archive` of baseline `9084b11` plus this branch's runtime files (hash-identical to the PR head), on read-only
copies. Originals unchanged before/after: sales `c23967fe…` (40,173,681 B), stock `5cba8f55…` (1,656,117 B),
registration `dbad60b346ea9a4cc984d89e315d5b9b3c56072413a455dfbd45d0f9adbde320` (193,682 B; the owner's download from the
6 November "dashboard" e-mail, in the private `data/` folder). The older December 2025 login report was not used.

| Registration list | Count |
|---|---:|
| Column B header | `Kod No` |
| Populated codes / distinct codes / codes appearing more than once | 2,113 / 1,996 / 107 |
| Rows without a customer code | 31 |
| Codes stored as numbers | 0 |
| Distinct codes matching an order-source customer exactly | 1,527 |
| Distinct codes not found as a customer (466 no sales at all → B; 3 only via spaces / invoice account → Doğrulanacak) | 469 |

| Sales and classification | Count |
|---|---:|
| Sales lines / AG = `B2B` lines / other spellings / blank AG | 183,764 / 23,329 / 0 / 0 |
| Order documents / B2B documents | 38,563 / 5,683 |
| Identifiers used by >1 customer / by >1 year (none with B2B lines) | 22 / 4 |
| Mixed B2B + other documents (within a customer) | 0 |
| B2B lines with zero / negative / missing amount / undated | 1,864 / 0 / 0 / 0 |
| Customers / with B2B lines / with B2B lines in the window | 6,801 / 860 / 423 |
| Customer status: exact / ambiguous via invoice account / ambiguous via spaces / not found | 1,527 / 368 / 1 / 4,905 |
| B2B buyers whose code is not confirmed in the list (B2B lines) | 29 (358) |
| **A** definite gaps (146 KAPALI excluded) | **4,739** (1,024 bought in the last 365 days) |
| **B** registered, no B2B purchase: other channels / no sales match (20 KAPALI excluded) | **1,142**: 676 / 466 |
| **C** former B2B buyers, none in 365 days (22 KAPALI excluded) | **415** |
| **Doğrulanacak** (32 KAPALI excluded) | **360**: 342 invoice-account match, 1 space difference, 14 B2B buyers not in the list, 1 registration code only with spaces, 2 registration codes only as invoice account |
| Latest transaction / window (exclusive start) | 2026-10-30 / 2025-10-30 |

Independent openpyxl reconciliation (`verify_tam.py`, private; implemented separately from `halsa_b2b.py`):
**171,000 checks, 0 discrepancies** — A, B, C and both Doğrulanacak key sets and their fields in both reports, the B2B
column of every Büyüme / Küçülme row, every Klasik yearly detail entry (original Harcama and distinct-document Sipariş,
B2B amount, B2B documents, mixed) and every V1 yearly row, tab and column positions, identical lists and warnings in both
reports. Original Klasik vs Klasik B2B on the same files: **7,706 checks, 0 discrepancies** (only the requested additions
differ; "Öne Çıkan Ürünler" identical). Offline DOM check of both real files (jsdom, page scripts run, nothing fetched):
30 + 31 checks, 0 JS errors — every tab, the four views and counts, code search, representative filter, sorting,
five-column detail (incl. a lazily opened B2B Takip detail), V1 yearly B2B columns and product history. Chromium could
not run in the Mac VM and the desktop browser pane cannot open local files; Chromium was exercised on synthetic reports,
including one at the November scale (≈4,000 list-A rows: B2B tab 1.2 s, sort 1.0 s on the test machine).

| File (`reports/private/b2b-tracking/`) | Bytes | SHA-256 |
|---|---:|---|
| `Halsa_Kasim_2026_Klasik_B2B_Tam.html` | 7,349,454 | `e382b2afe8d4ff6be25180c699bbfb7201eaf514ecd584ff53de83ff54e85841` |
| `Halsa_Kasim_2026_Genel_B2B_Tam.html` | 17,145,303 | `0e97dce76d1809b725f9de81b90675a4a12b4127f49eb1bb28caf6f84dfd659e` |

The earlier "list missing" reports (`Halsa_Kasim_2026_Klasik_B2B.html`, `Halsa_Kasim_2026_Genel_B2B.html`) are kept
unchanged. Two intermediate `_Tam` drafts made during this checkpoint were moved, not deleted, to
`superseded-drafts/` (`…_Tam_taslak1.html`).

## Questions for the father before ambiguous cases count as confirmed

1. **Invoice-account matches (342 customers, 32 more KAPALI):** is a B2B user opened for the dealer / invoice account
   (`Musterikod`) on behalf of the ordering customers? If yes, these customers can be treated as registered.
2. **14 customers buy through B2B (AG = `B2B`) but their code is not in `Kod No`** (29 including ambiguous / KAPALI;
   358 lines): under which code is their B2B user defined?
3. **31 registration rows have no `Kod No`:** which customers do they belong to?
4. **466 registered codes have no sales in 2020–2026:** new customers, or a different ordering code?
5. Are the 1 space-difference customer and the 1 space-difference registration code the same customer as the listed code?
6. What does "son zamanlarda" mean — 6 months, 12 months (provisional), other?
7. Are zero-amount B2B lines (1,864) free goods or cancellations, and should they count as B2B purchases for list C?
8. Should KAPALI customers stay out of the call lists?

Screenshots (synthetic data): `docs/images/b2b_takip_synthetic.png` (A), `docs/images/b2b_takip_V_synthetic.png`
(Doğrulanacak), `docs/images/b2b_takip_C_synthetic.png` (C with an opened detail), `docs/images/b2b_customer_detail_synthetic.png`.

## D-082 — registration reconciliation and the unified table (2026-11-08, counts only)

Read-only reconciliation of the verified files (registration `dbad60b3…`, sales `c23967fe…`); the delivered report's
classification was reproduced exactly first. Row-level table (codes, reasons, candidates) stays private on the Mac:
`reports/private/b2b-tracking/eslesme-mutabakati-2026-11-08/`.

- **Formats:** every code is text in both files (no numeric cells), and 1,994 of 1,996 registration codes and 6,794 of
  6,801 customer codes share the shape `999.99.9999`. Whitespace, case, number/text, leading zeros and separators were
  tested: only **1 pair** differs by spaces (already Kontrol). **"Listede bulunamadı" is not a formatting problem.**
- **Sales customers without an exact match (5,274):** 4,852 have no candidate in the list at all (A: 4,695, of which
  1,014 bought in the last 365 days; 146 KAPALI; 11 B2B buyers), 368 have their invoice account (bayi) registered,
  44 share a name with a different registered code (34 of those codes are separate sales customers), 9 a similar name.
  Names are weak evidence: on the 1,527 exact matches, registration and sales names agree on 718.
- **Registration side:** 2,144 user rows; 31 have no Kod No and no company name; 107 codes repeat (117 extra rows; each a
  different user e-mail, none identical). 469 distinct codes are not sales customers: 436 no candidate, 28 same name
  under another code, 2 similar name, 2 only as invoice account, 1 only with spaces.
- **Unified table (November):** 7,056 rows = 6,587 customers + 469 registration-only codes; B2B Kaydı VAR 1,957 ·
  Listede yok 4,739 · Kontrol 360. 420 registered active B2B buyers were in no previous list.
- Previews (private, not sent): `reports/private/b2b-tracking/onizleme-2026-11-08/` — Klasik and Genel, plus a Klasik
  preview with the declaration (illustration only). Verified against the delivered `_Tam` files: every other tab
  identical, every list row present once with the same amounts, the 420 new rows recomputed independently.

### PR #53 review (2026-11-08)

- **466 vs 469 registration codes:** different scopes, both correct. 466 = codes that appear nowhere in sales (not as
  ordering customer, not as invoice account, not with spaces) — the owner question "satışı olmayan kayıtlı kod". 469 =
  codes that are not exactly an ordering-customer code = those 466 + 3 that appear in sales only in another form
  (2 only as invoice account, 1 only with a space difference; Doğrulanacak, now "Kontrol · kayıt kodu satışta başka
  biçimde"). The unified table has exactly these 469 registration-only rows. (The private table's `views` column shows
  "—" for the 3; they are the Doğrulanacak registration-side rows.)
- Name-based candidates (44 customers + 28 registration codes) and invoice-account cases (368 + 2) stay unresolved.
- **Responsiveness (headless Chromium, synthetic report at the November size: 7,078 rows, 11 MB; 2 vCPU; median of 3;
  event → next painted frame):**

| Interaction | D-080 layout (main) | D-082 unpaged | D-082 paged | paged, CPU ×4 |
|---|---|---|---|---|
| open file | 3.7 s | 5.3 s | 3.7 s | 17.5 s (D-080: 17.0 s) |
| open B2B Takip | 0.50 s | 1.47 s | 0.08 s | 0.28 s |
| search keystroke (max) | 0.83 s | 1.71 s | 0.11 s | 0.24 s |
| change view / İzahat | 0.56 s | 1.83 s | 0.04 s | 0.18 s |
| sort by purchases | 0.54 s | 1.48 s | 0.04 s | 0.15 s |
| open customer detail | 0.17 s | 0.33 s | 0.01 s | 0.03 s |
| back to B2B Takip | 0.41 s | 0.91 s | 0.05 s | 0.23 s |

  Not a real-browser acceptance: no Chrome was connected, the built-in browser cannot open local files, and the private
  preview stays on the Mac. The real previews passed jsdom DOM checks (filters, a last-page code search, global sort,
  paging, detail, representative filter, every tab; 0 JS errors). Safari / Chrome acceptance remains with the owner.
