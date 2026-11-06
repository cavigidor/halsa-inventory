# Halsa V1 — provisional Stock/Sales dashboard and private offline report

This checkpoint follows stock PR #49, squash-merged as `112062384d22d32a542088aedd34da80a6019a84`.
Merged-main tree equals approved `0ea9d34826ef3415c17a438b6a05bc6dafced6ea`; its CI passed
2,107 / 8 skipped / 1 xfailed. This new implementation stays a draft for independent review.

## Available analysis and its limits

| Report section | Existing calculation reused | Meaning and unresolved question |
|---|---|---|
| Stok Kaynak İncelemesi | `stock_review`, formula-aware validation/VKM | Raw stock records/counts, errors, signs, blanks and exact Excel rows. Old min/max/coverage never becomes an action |
| Büyüme / Küçülme | `m_momentum` | Fixed 2025/2026, day-of-year cutoff at the latest observed transaction; prior amount ≥30,000, current >0. Nominal ordered Nettutar, not delivered/invoiced revenue or inflation-adjusted growth |
| Sipariş Büyüklüğü | `m_ordersize` | Fixed 2024–26, ≥3 distinct `series\|number` documents, ≥30,000 Nettutar. Free/zero documents count. Does the identifier repeat across years? |
| Eski Müşteri / Geri Kazanım İncelemesi | `m_lapsed` | Same 2021–25 qualification, strong-year thresholds and tiers; 2026 net total ≤0 can include actual 2026 orders/returns. The report also shows observed 2026 document counts and last transaction |
| Satış / Kârlılık Kanıtı | `m_margin` and its original ≥50,000 / 2025–26 scope | Existing source after-cost field, known portion separately. Missing cost makes full profit and ratio unknown, never zero. What accounting/cost basis produced the field? |
| Yıllık Müşteri Alışverişi | Customer 360 `_agg` + `prepare_sales` | Customer × dated year, source amounts, lines/documents and average. Known portion separate from full total; missing dates may affect a customer's period attribution |
| Müşteri / Ürün Alışverişi | Customer 360 `_products` | Existing top 10 per customer by known source amount across the file's history; descriptive, not a complete product list or recommendation. Quantities stay product-specific; missing quantity stays unknown |
| Satış Kaynak Kanıtı | Same raw sales frame and source validation | Every flagged record, source Excel row, identity/product/date, numeric source amounts or unknown and the issue. Includes missing cost and duplicate records; records are not deduplicated |
| Fuar Sipariş Geçmişi | `fairs.build_fair_history` and existing M6 label parser | FUAR only; conflicted-label documents excluded/counted; missing amounts remain unknown. No attendance, conversion, subsequent-sales attribution or ROI |
| Veri Kapsamı / Eksikler | Fixed scope statement | Current Cari absent/excluded, overdue unsupported, stock actions retired, Hedef/Trodat unselected, owner financial sources outside this general report |

The comparison producers' thresholds, years, rounding and KAPALI exclusion are unchanged. Source invoice-account
attributes can still differ from the D-003 order-source customer (DQ-5). Their assumption and ERP question appear
beside the tables. Descriptive yearly/product history includes closed customers and is not an opportunity list.
Missing inputs suppress only dependent values: a missing document key does not hide a known net amount. Unknown-date
or amount records do not become zero. The inherited nested detail payload, which could contain substituted zeros,
is not exported; the separate guarded yearly/product tables provide that history instead.

All review surfaces say **İnceleme için / doğrulanmadı** and keep their actual register verdict. There are still
33 registered metrics and only the existing Cari list is VERIFIED-certified. No action, AI, scenario, CRM or metric
API gate is opened. The app's served HTML checks each required current Stock/Sales fingerprint, generation, policy
and published HTML digest; changed or missing sources refuse the cached review. Three roles and finance-only Cari
remain unchanged. The policy marker is `m8-v1-sales-review`.

Inherited VKM questions now distinguish missing cells, uncached/error formulas, duplicate stock codes, footer ranges,
missing identity, selected filename/date/period, ambiguous headers/mapping and joins. They ask about the actual field,
source assumption and records, rather than asking whether a generic finding is “the same”.

## Generate one offline file

Only the owner needs Python 3.12+ and the existing requirements. The recipient needs a browser and the single HTML file.
Use read-only copies of the intended original Stock and Sales exports, preserving original filenames/bytes. Keep the
folder/output private and ignored by Git. Do not put a customer master list into the aging slot.

Create `sources.json` in the private input folder with the existing schema:

```json
{
  "schema": "halsa-source-selection/1",
  "core": {
    "stock": {
      "file": "<actual-original-stock-name>.xlsx",
      "snapshot_date": "2026-11-01",
      "date_origin": "filename",
      "date_source": "<actual-original-stock-name>.xlsx"
    },
    "sales": {"file": "<actual-original-sales-name>.xlsx"}
  }
}
```

The example Stock date applies only when the original filename establishes 1 November. Do not infer one date from
an ambiguous historical Sales filename or claim completeness/mapping approval. The latest transaction is labelled
as an observation. Stock/Sales automatic filename-date inference remains separate work; no existing provenance is
rewritten by this export.

From the repository checkout:

```sh
STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled .venv/bin/python -m services.father_report \
  --data "$PWD/reports/private/v1-sales-report/data" \
  --output "$PWD/reports/private/Halsa_Baba_Genel_Rapor_Yeni.html"
open "$PWD/reports/private/Halsa_Baba_Genel_Rapor_Yeni.html"
```

The output path must be a **new** `.html` file. Existing reports/symlinks/workbooks are never overwritten. The generator
reads explicit Stock/Sales inputs only, freezes them, applies existing validation and rechecks their fingerprints before
writing. Even a missing/invalid optional Cari or owner selection does not block this general report, because those
files are not read. No SQLite, refresh publication, live service, AI provider or remote connection is used. Output is
one atomic, mode-600 file. No password or password hash is embedded. With `HALSA_DATA_ROOT`, existing path checks apply.

The HTML has inline CSS/JS, search (including customer codes), sorting, representative filters where supplied,
100-row pagination for large sales tables, and expandable source/warning details. It has no server-dependent tabs,
CRM, upload, login, fair-campaign or action controls. Source quantities in product history are not silently rounded
by the table formatter. There are no external assets or required network requests.

As a downloaded snapshot it cannot enforce application passwords or recheck originals on the recipient's computer.
Share it privately with the intended authorized father/company recipient. **No Cari account balances, invoice-age
balances, Kredi, Gider/Gelir or Bütçe source data are included.** A separately authorized private financial deliverable
would be a separate scope; the financial web endpoints stay protected. The general report can be opened now without
an always-on Mac, hosting, tunnel or a new backend.

## Real November acceptance (counts only)

Read-only copies of the actual November Stock/Sales exports were used, with explicit selection. No current Cari
aging file was substituted by an older export or the customer master list. Original SHA-256, size and mtime stayed
unchanged. The latest observed sales transaction is 2026-10-30, not an attestation of report date/completeness.

| Section | Exported rows |
|---|---:|
| Stock source evidence | 3,316 (3,313 product-code rows; 3 missing identities) |
| Growth / decline | 390 |
| Order sizes | 864 |
| Win-back rule candidates | 552 |
| Profitability evidence | 807 |
| Customer/year history | 13,488 |
| Customer/product history (existing top-10 scope) | 37,000 |
| Flagged sales-source records | 2,783 |
| Fair history | 88 |

Independent openpyxl + Python aggregation reproduced the exported keys, source fields and arithmetic:
**550,229 checks; zero discrepancies**, with tolerance 1e-6 / relative 1e-12 for independently accumulated floating
sums/ratios (whole-lira/one-decimal legacy rounding preserved). The independent fair aggregation shares only the
existing label grammar, not the production aggregation. Counts include source fields, derived fields and key-set checks;
this is not a certification of unapproved business definitions.

- 2,775 source records in 2025–26 lack the after-cost field; 547 profitability rows have unknown full profit/ratio.
- Eight source records are involved in identical-row duplicates (first occurrences included); no deduplication.
- Four win-back candidates have 2026 documents. The inherited non-positive-net rule is shown explicitly.
- Stock coverage errors affect 1,168 source records. Old stock recommendations remain retired.

Private HTML, workbooks, independent comparison script, fingerprints and row details stay on the Mac under ignored
`reports/private/`. Only synthetic test data/screenshots are suitable for GitHub or shared review logs.

## Remaining acceptance

This is usable coverage of the available Stock/Sales analyses, with genuine invalid/unavailable values made explicit.
Optional missing sources do not block opening it. Independent PR review is required before merging this implementation;
father still needs to open the privately shared file and compare the displayed rows/questions with his ERP exports.
His answers may justify later source or rule changes; the report does not invent them. Current November Cari aging
and the separate agreement-based overdue report remain missing for their respective financial analyses and are not
gates on reviewing the general report.
