# M8-CP3 — Excel Upload Center ("Dosya Yükleme")

Decision: **D-065**. Status: implemented on `claude/m8-cp3-upload-center`, locally and CI tested, **not deployed**.

The owner-only workflow inside the existing website:

**Select Excel files → declare report details → validate (background job) → preview → explicitly approve publication
("Değişiklikleri Yayınla") → dashboard updates.**

There is **no connection of any kind to DinamikCRM**: the owner exports the reports there and uploads the files here.
No credentials, API, database connection, synchronization or automatic export exist.

## 1. Where it lives

| Piece | File |
|---|---|
| Receiving one workbook safely (size, type, archive, content-addressed storage) | `services/upload_files.py` |
| Batches, declarations, background jobs, preview, publication | `services/upload_center.py` |
| Turkish owner UI (dashboard tab and the fixed `/yukleme` page) | `services/upload_ui.py` |
| Routes | `app.py` (section "M8-CP3 Excel Upload Center") |
| Refresh hooks (explicit selection, expected identity, swap / install hooks, stage-only) | `services/refresh.py` (`PublishHooks`) |
| Selection files (core `sources.json`, owner `owner_sources.json`) | `services/generation.py` |
| Uploaded owner workbooks in the owner repository | `services/owner_view.py` (`config_with_uploads`, `prepare`, `stage_configs`, `install_entries`) |
| Read-only Veri Kontrol preview | `services/vkm_store.preview_delta` |
| Name check exemption for owner-typed uploads | `services/importers/base.explicit_source_type` |

The UI is reachable two ways: the **Dosya Yükleme** tab of the dashboard (the button is hidden unless the page's
session check confirms the owner) and the fixed page **`/yukleme`** (no data inside; it works on a fresh server where
no dashboard has ever been published — the "Pano henüz yayımlanmadı" page links to it).

## 2. Report types and declarations (never inferred)

| Type (UI) | Key | Declared by the owner (all optional; empty = unknown) | Used by |
|---|---|---|---|
| Stok | `stock` | Rapor (anlık) tarihi | core refresh (`sources.json`) |
| Satış | `sales` | Rapor tarihi, dönem başlangıcı / sonu, dönem tamamlandı mı? | core refresh |
| Cari | `aging` | Rapor (anlık) tarihi | core refresh |
| Kredi | `kredi` | Rapor (anlık) tarihi | owner repository (`owner_sources.json`) |
| Gider/Gelir | `gider_gelir` | Rapor yılı (required by the importer to read the file) | owner repository |
| Bütçe | `butce` | Plan yılı (required by the importer), rapor tarihi | owner repository |

- The report type of every file is **chosen** by the owner from a list; nothing is preselected from the file name.
- A date is never taken from a file name, a modification time or the latest transaction. Unknown declarations are
  recorded as unknown (left out of the selection file; `report_date_origin` stays empty), so the accuracy gate keeps
  withholding exactly as before.
- Stored files get **content-addressed names** (`yukleme-<128-bit SHA-256 prefix>.xlsx`). No reader can infer a type,
  date or year from them, and they never match the legacy keyword selection (`stok`, `sat`, `cari`, `yas`).
  Consequently Gider/Gelir and Bütçe need their year declared (their importers otherwise fall back to the year in the
  file name, which no longer exists): without it the preview reports "Rapor yılı / Plan yılı beyan edilmedi".
- The M6 importer framework's file-name pattern check does not apply to a workbook whose type the owner chose
  (`explicit_source_type()`, scoped to those imports; recorded as an INFO issue). Every schema, layout and rule check
  still applies.
- Hedef and Trodat are not part of the generation manifest today and are therefore **not** offered (they would bypass
  the generation-bound evidence). They remain configured by `HEDEF_PATH` / their own process.

## 3. Upload safety (owner only)

Per file, before it is stored: at most `UPLOAD_MAX_BYTES` (default 96 MB; the real sales export is ~40 MB) checked
while streaming; ZIP local-file signature; `[Content_Types].xml` and `xl/workbook.xml`; ≤ 5 000 parts; no absolute,
drive-letter, backslash or `..` part names; only stored / deflated parts; no encrypted part; declared uncompressed size
bounded in total (3 GB) and per part (2 GB) and every part above 1 MB at most 250:1 compressed (ZIP bombs refused
before anything inflates them; Python's zipfile never inflates beyond the declared size and checks the CRC); no DTD or
entity declaration in any XML part. Legacy `.xls`, password-protected (OLE) workbooks, CSV and renamed files are
refused with fixed Turkish messages (no input is echoed).

Storage: `UPLOAD_DIR` (default `<DATA_FOLDER>/uploads`, private, gitignored with `data/`, folders 0700, files 0600) —
`blobs/` (content-addressed, the same content is stored once), `incoming/` (temporary, always cleaned),
`batches/<id>/` (temporary preview build, removed after validation). Nothing there is ever served by the web app.

A batch accepts at most 6 files (one per report type). The same content twice in one batch is one file; the same
content in two types is refused.

## 4. Background validation (no live change)

`POST /api/uploads/{batch}/validate` records the declarations and returns a job (HTTP 202); the page polls
`GET /api/uploads/jobs/{job}` (Turkish stage text, step / steps, elapsed seconds). One job runs at a time; a job that
was running when the server stopped is marked "yarıda kaldı" at the next start and the batch must be validated again.

The job reuses the deterministic pipeline unchanged:

1. **Plan.** Uploaded files + declarations. For core reports not in the batch, the reports of the CURRENT explicit
   selection are carried over unchanged with their own declarations (and any fingerprint-bound owner approval, as is).
   If the live selection is not explicit (legacy modification-time choice), nothing is carried: Stok and Satış must be
   uploaded together. A carried file whose bytes changed on disk is an error.
2. **Core** (`stock` / `sales` / `aging` in the batch): `run_refresh` with `PublishHooks(selection=…, stage_only=True)`
   — the same validate → build → verify as a publication, into a private preview path; nothing is swapped or installed.
   Its errors (missing columns, unreadable layout, older data than the live data, …) become preview errors. A valid
   workbook with another report's layout is reported as "beklenen düzeninde değil … yanlış rapor türü seçilmiş olabilir"
   (no longer "geçerli bir Excel çalışma kitabı değil").
3. **Owner workbooks**: `OwnerRepository.prepare()` — the M6 importers through the existing loaders, without publishing.
4. **Compare**: the candidate generation (core records + owner records) against what is served: VKM delta
   (`preview_delta`, read-only: new / returns / still present / resolved / not checked / source missing), the gate's
   decision for every dashboard section now vs after, every registered metric still withheld, the observed latest
   transaction date (labelled "gözlenen; rapor tarihi değildir"), reconciliation status from the recorded evidence,
   recognized sheets and required columns, missing source information, mixed declared dates.

The preview changes **nothing** live: dashboard, manifest, context, owner views, both selection files and the VKM
history are untouched (tested). The preview carries file labels, fingerprint prefixes, schema labels, codes and counts —
never workbook values, names or amounts.

## 5. Explicit publication ("Değişiklikleri Yayınla")

`POST /api/uploads/{batch}/publish` with the preview token → a publication job:

1. **Refusals before anything changes:** not the newest batch (a newer upload supersedes older previews), not ready,
   wrong token, or **stale** — the served core generation or either selection file changed since the preview
   (`base`); an identical set (same bytes + declarations as served) publishes nothing ("Değişiklik yoktu").
2. The exact previewed bytes are copied into the data folder under the same content-addressed names (fingerprints
   verified). Owner workbooks are imported from those copies and must reproduce the previewed record fingerprints.
3. **Core in the batch:** `run_refresh` with `PublishHooks(selection=…, expect_core_id=<previewed core_id>,
   before_swap=…, after_install=…)`. The candidate must have exactly the previewed identity (else refused before any
   swap). Under `PUBLISH_LOCK`: the selection files are written (atomic) and the owner configs staged right before the
   dashboard / manifest swap; the context and generation are installed (`_install_generation`); the uploaded owner
   workbooks are installed in the same step. Any swap / install failure restores the previous dashboard, manifest,
   context, both selection files and the owner configuration.
4. **Owner workbooks only:** under `PUBLISH_LOCK`, the owner selection is written and the prepared entries installed
   (their generation records are published by the existing `on_record` path); a failure restores the selection and
   configuration.
5. VKM observes the accepted generation through the existing observers (no special path). CRM notes, follow-ups, fair
   campaigns, action state and VKM review history are never touched (tested across publications and restarts).

Persistence across restarts: the core selection is `data/sources.json` (existing schema; restart re-derives the same
generation), the owner selection is `data/owner_sources.json` (schema `halsa-owner-selection/1`; an uploaded workbook
takes precedence over its environment path; an unreadable file is ignored and the environment configuration stays).

## 6. Access

| Route | Level |
|---|---|
| `GET /yukleme` | viewer (fixed page, no data) |
| `GET /api/uploads`, `GET /api/uploads/{batch}`, `GET /api/uploads/jobs/{job}` | owner_read |
| `POST /api/uploads`, `…/files`, `…/files/{file}/remove`, `…/validate`, `…/publish`, `…/discard` | owner_write |

Owner session + CSRF token + same-origin Origin (existing middleware) + the local `X-StockAgent` guard. Public viewers
get 401, financial viewers 403 `owner_required` on every upload route; their page shows a login notice and never calls
the API. Accuracy classification: `system` for reads, `operational` for writes (no business conclusion).

## 7. History

`upload_batch` (current state), `upload_job` (current state) and the append-only `upload_event` log (triggers refuse
UPDATE / DELETE) in the private SQLite file. Events: created, file_added / file_repeated / file_removed,
validation_started / validated / validation_failed, superseded, discarded, stale_refused, publication_started /
published / unchanged / publication_failed, interrupted. Details are codes and counts only.

## 8. Accuracy (unchanged)

`GENERATION_BOUND_API_EVIDENCE = False`; no metric becomes VERIFIED because a workbook loads, validates or is
published. No approval, mapping, source authority, business definition, reporting date or Kredi `DURUM` meaning is
inferred; a new upload never carries an approval.

## 9. Limitations / open

- Not deployed; real-data publication through the UI is an owner-local acceptance step (the Step 1 private files).
- Uploaded workbooks are kept (content-addressed, deduplicated) for traceability; there is no retention / cleanup
  policy yet (CP5).
- One validation or publication job at a time per server process; a single-process server is assumed (CP2).
- A publication changes the date-dependent evidence only on the day it was previewed: a preview validated before
  midnight and published after it may be refused as "önizlemeyle aynı değil" and must be validated again.
- Hedef / Trodat uploads are out of scope (not in the generation manifest).
- A crash between writing the selection files and the dashboard swap (a few milliseconds inside one lock) leaves a
  selection that no longer matches the published manifest: the restart then fails closed (nothing served) until the
  owner re-validates and publishes.
