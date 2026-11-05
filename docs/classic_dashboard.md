# Klasik Rapor — the original Halsa dashboard, regenerated exactly (D-077)

**Status:** draft PR, not merged, not deployed. Separate from the new Halsa dashboard and from the hosting work
(PR #46).

**What it is.** The owner's father received a dashboard before the advanced Halsa project began. *Klasik Rapor*
regenerates that dashboard from new monthly Excel files with the **original program, unchanged**:
- the same calculations, thresholds, year and period comparisons, classifications, Cari / Tahsilat / Riskli Müşteri
  logic, sorting, row inclusion, tabs, labels, notes, HTML, CSS and JavaScript;
- **none** of the later Halsa rules: no accuracy gate or evidence, no freshness, no D-070 stock retirement, no D-071
  invoice-age reinterpretation, no 120 / 320 classes, no financial password, no banner or note.

It is a historical reproduction, not a corrected analysis.

## 1. The reference

| Item | Identification |
|---|---|
| Historical program | `dashboard_builder.py` at commit **`71f5f2f`** (2026-09-10, root commit of the repository), SHA-256 `76afc196f984c332…`, frozen byte-for-byte as `classic/original_builder.py` |
| Report the father received | `StockAgent/dashboard.html` on the owner's Mac: written 2026-10-06 21:00 local time; title "Şirket Panosu — 6 Ekim 2026"; inputs `STOK 5 Ekim 2026.xlsx`, `2020-5 ekim satışlar.xlsx`, `Cari 5 Ekim 2026.xlsx`; SHA-256 `600b5c36…`, 2,272,679 bytes |
| Claude's recreation (the known-good reference) | `StockAgent/dashboards/dashboard_1_Kasim_2026.html` (2026-11-04): the **unmodified** `71f5f2f` program run on `STOK_1_11_26.xlsx` + `1_Kasım_2020_2026 satışlar.xlsx`, no Cari aging file; SHA-256 `8d003f8e…`, 2,192,873 bytes |

**Differences between the historical code and Claude's recreation:** none in the program. The recreation ran the
`71f5f2f` file unchanged; the earlier ChatGPT file is not used anywhere. The run differed only in how it was invoked:
- the input folder held links whose names were typed by hand (see §3, file-name form);
- it ran on 4 Kasım with today's date;
- it used a random Python hash seed (see §3, tie order).

## 2. How Klasik Rapor reproduces it

`classic/generate.py` only **chooses which files the original program reads, and on which date**:
- **The files you name.** The original program picks files from a folder by name (`stok` / `sat` or `bazl` /
  `cari`, `yas` or `yaş`) and by newest modification time. The generator copies exactly the stock, sales and
  (optional) Cari files you name into a private temporary folder, under their names **as stored on disk**. It checks
  with the original's own name rules that each is read in its intended role and nothing else is, and it verifies
  every copy's SHA-256. Input files are never opened for writing, renamed or moved.
- **The master list is refused as Cari.** The customer / representative master list (`cari temsilci listesi.xlsx`) is
  never accepted as the Cari aging report. The Cari file must have the original aging layout: `Hesap kodu`,
  `Hesap adı`, `Temsilci kodu` and the month / "öncesi" columns, or a "Vadesi geçen" column.
- **The date.** The report date is today; `--date` reproduces an earlier one. The original program writes it in the
  title only.
- **A separate process.** The original program runs in a separate Python process with a minimal environment. The
  process refuses to finish if any module of the new Halsa (`services`, `config`, `app`, `schemas`, the new
  `dashboard_builder`) was loaded.
- **A fixed hash seed.** It runs with `PYTHONHASHSEED=0` (see §3, tie order).
- **No reverse dependency.** No module of the new Halsa imports `classic/` (tested). The new dashboard, its
  published generation, SQLite state and accuracy rules are untouched.

## 3. Equivalence — results

**Real exports (owner's Mac, 2026-11-04, read-only copies; pandas 3.1.0, numpy 2.5.4, openpyxl 3.1.5, Python 3.12):**

| Test | Result |
|---|---|
| A. 5 Ekim files, date 6 Ekim 2026, vs **the report the father received** | same size (2,272,679 bytes); the whole page outside the data (title, header with file names, overview cards, tier counts, CSS, JavaScript) **identical**; data of **8 of 9 tabs identical** (Azalan Stok 648, Fazla Stok 400, Stok Eritme 399, Geri Kazanım 569, Sipariş Büyüklüğü 848, Kâr Marjı 790, Riskli Müşteri 386, Tahsilat 386); **Büyüme / Küçülme 358 rows identical as a set and in the order of "Değişim %"** — 21 rows with exactly equal change % stand in a different order among themselves |
| E. generator vs the original program run directly (same files, date, seed) | **byte-for-byte identical** |
| B. new monthly files (1 Kasım), date 4 Kasım 2026, vs **Claude's recreation** | 2,192,874 vs 2,192,873 bytes; all 7 tabs present (no Cari file → no Tahsilat / Riskli, as the original does); 6 tabs identical; Büyüme / Küçülme identical up to the order of 16 equal-% rows; the page is identical after Unicode normalization — the one byte is the file name in the header (see below) |
| D. same inputs and date twice | byte-identical |
| C. master list given as the Cari file | refused, nothing written |
| Browser (Chromium) on A and B | every tab opens, row counts match the data, sorting, search and detail rows work, no JavaScript error |
| Inputs | SHA-256 of every Excel file unchanged |

**The two unavoidable differences (neither changes a number, label or behaviour):**
1. **Tie order in "Büyüme / Küçülme".**
   - The original program builds that list from a Python `set` and then sorts it by "Değişim %". Rows with exactly the
     same % keep the set's order, and Python randomizes that order on every run.
   - The original therefore produced a different order of tied rows each time it ran (shown in tests with four seeds),
     and the order the father received cannot be recovered.
   - Klasik Rapor fixes the seed, so the same files and date always give the same bytes — one of the orders the
     original itself produces.
2. **File-name form in the header.**
   - macOS often stores names with decomposed letters ("ş" as "s" + combining cedilla). The original showed the name
     as stored; the father's report has the decomposed form.
   - Claude's 4 Kasım recreation typed the names by hand (composed form), so its header differs by one byte while
     looking identical. Klasik Rapor shows the name as stored, like the original.

The report date (title) is whatever date you generate on; with `--date` the original's date is reproduced exactly.

**Synthetic (CI, `tests/test_classic_dashboard.py`, 19 tests):**
- the frozen file equals `git show 71f5f2f:dashboard_builder.py`;
- the generator output is byte-identical to the original run directly and to the committed golden report
  (`tests/fixtures/classic/golden_synthetic.html`);
- the original's tie-order variation is shown; the output is deterministic;
- the as-stored decomposed name appears in the header; without a Cari file there is no Tahsilat / Riskli tab;
- refusals: master list, misleading names, missing / non-.xlsx files, overwrite, changed frozen file;
- inputs are unchanged and nothing else is written; no Halsa import in either direction;
- the browser exercises every tab, search, sorting, the representative filter and detail rows.

The golden test fails if the original or a library ever changes the output.

## 4. Monthly use (owner)

**Double-click:** `classic/Klasik Rapor Oluştur.command` in Finder.
1. It asks for the **stock** file, then the **sales** file.
2. It asks whether an **original-format Cari aging report** exists ("Var, seç" / "Yok, onsuz oluştur").
3. It writes `dashboards/Klasik_Rapor_<tarih>.html` (never overwrites) and opens it.
4. Send that file to the father as before.

The first run creates a private Python environment with the pinned libraries (`classic/requirements.txt`; internet
needed once).

**Fallback command** (Terminal, in the StockAgent folder):
```sh
python3.12 classic/generate.py --stock "data/STOK_1_11_26.xlsx" --sales "data/1_Kasım_2020_2026 satışlar.xlsx" \
    [--cari "data/Cari 1 Kasım 2026.xlsx"] --out "dashboards/Klasik_Rapor_2026-11-04.html" [--date 2026-11-04]
```

**Naming rule of the original** (unchanged, so file names matter):
- the stock file name contains `stok`;
- the sales file name contains `sat` or `bazl` and not `stok` / `cari` / `yas`;
- the Cari aging file name contains `cari`, `yas` or `yaş`.

The generator refuses a name the original would misread; rename the file in that case.

**Tahsilat and Riskli Müşteri** appear only with the original-format Cari aging report (`Cari … 2026.xlsx`). The
1 Kasım delivery has no such report — "cari temsilci listesi.xlsx" is the master list and is refused.

## 5. Things the owner should know (outside the generated HTML, which stays unchanged)

- **Old interpretations.** The classic report shows the original interpretations that later Halsa work found
  unsupported, by design: invoice-age buckets shown as "Vadesi Geçen", the collection-priority note, "borçlu kayıp
  müşteri", obsolete stock thresholds (D-070 / D-071). It is not certified by the accuracy gate.
- **No password.** It is a plain file with no password, so **anyone who gets the file sees the Cari balances**.
  D-075 protects these only inside the new Halsa site.
- **Not on the hosted site.** It is deliberately a local tool, not a page of the hosted Halsa site, so it never
  bypasses the site's accuracy gate or financial password. Publishing it on the site would need a separate owner
  decision.
- **Pinned libraries.** Equivalence was proven with pandas 3.1.0 / numpy 2.5.4 / openpyxl 3.1.5; the launcher pins
  them.
