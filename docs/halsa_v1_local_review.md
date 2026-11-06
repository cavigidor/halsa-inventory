# Halsa V1 — local stock review checkpoint and report delivery

This branch stops at the first reviewable implementation checkpoint. It does **not** finish all V1 analysis tabs or
deliver a complete November father report. No hosting/deployment or merge is authorized. Classic is unchanged.
The full 33-metric inventory and remaining work are in [the coverage matrix](accuracy/v1_metric_coverage.md).

## What works now

The updated dashboard has a **Stok Kaynak İncelemesi** tab. It displays factual, preliminary source records with
original Excel row numbers, exact cached values (including errors and blank-looking strings), nulls rather than
invented zeros, signed quantities and separate duplicate rows. Expandable warnings reuse the existing formula-aware
validation and Veri Kontrol detections and preserve source date, full SHA-256, generation and certification verdict.
Retired recommendations remain retired; the count is not silently certified. No source workbook is modified.

The common served page contains stock records, including source inventory values, for authenticated website viewers
under the existing normal stock disclosure tier. It contains **no Cari account/balance records**. No new route,
password, authentication role, AI call or remote service is introduced. Every served stock review rechecks the
currently selected stock fingerprint. Missing/changed bytes or unknown layout refuse review until refresh. On restart,
the existing re-derivation/publication path still runs first. The new policy marker rejects old pages.

## Generate the current review locally

Use this branch in its isolated checkout; do not overwrite an active private publication or alter original workbooks.
Keep the output and source copies in an ignored/private directory. Python 3.12+ is required.

```sh
cd '/Users/cemavigidor/Documents/ChatGPT/Halsa Dashboard/halsa-inventory'
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p reports/private/review/data
```

Copy the intended **Stock and Sales** Excel files into that data directory, preserving their original basenames and
bytes, then make the copies read-only. Do not copy the customer master list as Cari aging. If an actual current aging
export is supplied, select that exact workbook separately. No modified-time fallback should decide which month is used.
Create `reports/private/review/data/sources.json` using the existing source selection schema; replace filenames with
actual selected basenames:

```json
{
  "schema": "halsa-source-selection/1",
  "core": {
    "stock": {
      "file": "<original stock filename>.xlsx",
      "snapshot_date": "2026-11-01",
      "date_origin": "filename",
      "date_source": "<original stock filename>.xlsx"
    },
    "sales": {"file": "<original sales filename>.xlsx"}
  }
}
```

Use that date **only for a source whose original filename establishes 1 November 2026**. Stock/Sales upload date
inference is still separate pending work; here it is explicit owner-local selection. Do not declare period completeness,
mapping approvals, an ambiguous sales report date or reconciliation simply to unlock a result. Missing fields stay
unknown. The historical Sales filename may describe a range; do not collapse that into a single as-of date.

```sh
STOCKAGENT_NO_DOTENV=1 AI_PROVIDER=disabled .venv/bin/python -m services.refresh \
  --data "$PWD/reports/private/review/data" \
  --dashboard "$PWD/reports/private/review/dashboard.html"
open "$PWD/reports/private/review/dashboard.html"
```

Select **Stok Kaynak İncelemesi**. Its table, search, sort and expandable warnings work in an ordinary browser from
`file://`, without Python or FastAPI on the recipient's computer. This **stock preview** is a snapshot: live source
fingerprint rechecks occur when the application serves it; a downloaded file cannot recheck the owner's originals.
It contains private company stock evidence and is intended only for the father/authorized recipient via private file
transfer. It has no password by itself. Do not upload it publicly. This checkpoint embeds no account-level Cari balances.

## Which features need FastAPI

| Surface | Standalone HTML status |
|---|---|
| New stock-source review table/search/sort/warnings | Works offline; tested in Chromium with no HTTP request |
| Existing static sales modules | HTML/JS architecture is already self-contained, but production still withholds these at this checkpoint; preliminary-result work remains |
| Retired stock reorder/overstock/clearance and overdue/risk | No valid action output; do not restore the old algorithms |
| Cari account balances | Intentionally absent from HTML; existing financial/owner session + fresh generation-bound `/api/finance/cari` needed |
| Customer 360, daily queue, cross-sell/territories, fair history, CRM/fair stored records, owner financial views | Current live API and SQLite/importer state needed; not included as an offline snapshot |
| Upload, publication, verification history, sessions and administrative changes | FastAPI/owner-only; not report functionality |
| AI / drafts / action mutations | Not required for V1, stay gated; no AI use here |

Do not rebuild a backend to share the report. The smallest next delivery is to extend the existing static builder with
labelled calculable sales results and evidence, then make a curated read-only HTML snapshot. A private finance-inclusive
export needs explicit recipient-aware scope, reuse of the existing finance calculation, and tests that it is never served
to website-only viewers. It is not implemented here. Full father acceptance remains open.

## Real inputs and blockers

November Stock and Sales are present on the owner's Mac. The stock read-only comparison is documented in the coverage
matrix. The November full sales calculations and usability acceptance still need their own scoped review surfaces.
The inspected local directories contain an older October aging report and a customer master list, but no November
invoice-age Cari export. Required for refreshed Cari: actual November **Cari aging** workbook with account code/name,
representative and monthly invoice-age buckets. Overdue conclusions additionally require the separate agreement-based
report (red entries); the aging export cannot replace it. Expanded analyses require their actual explicit reports:
Hedef, Kredi Takip, Gider/Gelir, Bütçe, Trodat Satış, Evrak Detaylı Tahsilat, as applicable.

No father-openable **complete V1** artifact is claimed. The remaining blockers are unfinished preliminary sales/other
surfaces, missing current inputs for applicable sections, a curated offline export and father's own acceptance. This
stock preview is independently usable, but it is only the first implementation checkpoint.
