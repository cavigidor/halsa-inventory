# Post-M7 local operating checklist — owner view

**Scope:** bring the completed M7 dashboard into everyday *local, single-user* use. This checklist does not
approve loan balances, accounting categories, margin rules, or files of record. It does not unblock M6-4.

## 1. Update and protect the Mac copy

1. Make a private backup of the local project, especially `.env`, `data/`, the SQLite state DB, and any
   private exports. These items must **never** be committed.
2. In the existing `halsa-inventory` checkout, check `git status` before updating; do not overwrite
   local edits. Pull `main` only once any local changes are safely handled.
3. Activate the Python **3.12+** environment and install `requirements.txt`. For a new setup see the README.
4. Keep the server bound to **127.0.0.1**. Yönetim is **not access-controlled**; do not open it on the LAN,
   internet, or a shared computer. Owner/admin roles require the future authentication milestone.
5. Leave `AI_PROVIDER=disabled` initially. All M7 operational and owner views work without an AI key.

## 2. Explicitly choose local owner exports

In the *private* `.env`, set **only** paths to files you have individually checked. Example keys (no actual
paths or amounts belong in GitHub):

```dotenv
# These are examples of variable names, NOT chosen or approved files of record.
HEDEF_PATH=
HEDEF_SNAPSHOT_DATE=
KREDI_PATH=
KREDI_SNAPSHOT_DATE=
GIDER_GELIR_PATH=
GIDER_GELIR_REPORT_YEAR=
BUTCE_PATH=
BUTCE_PLANNING_YEAR=
BUTCE_SNAPSHOT_DATE=
```

Use exact local paths (they may contain spaces). Date format is `YYYY-MM-DD`; year format is `YYYY`.
Do **not** infer a snapshot date from the latest payment, the filename, the file's modification time or today's
date. Leave the corresponding date/year blank if not known. Each owner file is optional and isolated.

**Important:** a selected test/display file is not automatically the authoritative *file of record*.
The February and June Kredi exports, different Bütçe revisions, and old Hedef reports are distinct candidates;
the program must not silently choose one. Kredi `DURUM=Kapalı` is unverified and never used as a paid-off rule.

## 3. Validate owner files locally (before opening Yönetim)

```bash
python -m scripts.owner_preflight
# If structured output is useful:
python -m scripts.owner_preflight --json
```

This is a **read-only** validation using the existing M6 importers. It neither sends data to an AI provider nor
changes Excel, SQLite, the dashboard or any action. It prints only file *basenames*, source states, validation
codes, selected counts, and unresolved assumptions. It does **not** display raw amounts, lender names, contracts,
expense labels or full paths.

- `validated`: all *configured* owner files passed structural validation; **not** financial approval.
- `not_configured`: no owner file was selected. Other M7 features still run.
- `failed` (exit 1): one or more selected files failed, while other sources may be valid. Check the path,
  optional date/year and importer reports. Do not alter business rules to force a workbook through.
- Exit 0 means there were **no importer failures**, not that every optional workbook was configured or
  that finance signed off on its contents.

The preflight does **not** check core sales/stock/Cari, optional Hedef, the browser, or SQLite. Those are covered
by the next steps, and its output must not be described as an all-system green light.

## 4. Start the actual dashboard and check it

```bash
python dashboard_builder.py data
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/** — not `dashboard.html` through `file://`. The first context and Customer 360
load may take noticeably longer on the large sales workbook. In a browser:

1. Check `/api/health` reports `context_ready: true` and review the `as_of_data_date`. If it is not ready,
   investigate `context_error` before interpreting business numbers.
2. Open **Müşteri 360**, **Günlük Satış**, **Fırsatlar**, **Fuar Merkezi**, **Takip / CRM**, and **Yönetim**.
   Confirm they load and display the expected source dates. Also check desktop and phone-width layouts.
3. In Yönetim → **Veri ve Sistem Durumu**, verify the owner source basenames and states. A source left blank
   must say not configured; a failed source must never silently reuse stale values.
4. Check **Kredi** warnings and source date. The scheduled 30/60/90-day amounts are *not* live bank balances
   or cash-flow forecasts. The February snapshot may be stale; past-due installments are separate.
5. Check **Gider/Gelir** as *source-reported* monthly amounts with four native lines. The cash/accrual basis
   and management mapping remain unresolved. **Bütçe** is a cell-health view, not budget-vs-actual.
6. Check CRM says **Eksik kayıt olabilir**, and that the cross-sell candidate share warning is shown if broad.
   No salesperson rankings or automatic sends are enabled.
7. Check the local action history, fair campaigns and CRM follow-ups survive a normal restart. Back up the
   SQLite state file rather than deleting/recreating it. Do not use production data to test destructive controls.
8. Test a controlled monthly core refresh only when you have *new, verified core exports*. Owner files use
   their own **Yönetim verilerini yeniden oku** button, which does **not** refresh core sales/stock/Cari.
9. Keep AI disabled during validation. If enabled later, AI is **on demand only** and messages remain copy-only.

**Acceptance for local trial use:** core context date is correct, M7 tabs render, configured owner sources pass,
warnings remain visible, and state persists. Finance numbers remain labeled unverified until the corresponding
business definitions and files of record are approved. Do not share the owner view with salespeople.

## 5. Kredi clarification handoff (pending)

When the person maintaining the Kredi export explains `DURUM`, record *what the status means*, whether it applies
to **contract** or **installment** closure, how partial payments / refinancing / early repayment appear, and which
workbook is authoritative **as of which date**. Confirm why `Kapalı` coexists with positive `KALAN TAKSİT TUTARI`
and missing payment slips. Do not accept the status as proof of payment based on its label alone.

**After receiving that definition:** update the importer/owner interpretation *only if supported by the answer*,
add synthetic regression cases, rerun the full suite and the local owner preflight, then verify real-data warnings
and the UI. Otherwise leave the current conservative logic and document the business definition. This should be
a narrow follow-up PR, not a rewrite of M7.

## 6. Other explicitly unresolved items

- **BLOCKED M6-4:** `Evrak Detaylı Tahsilat Raporu` export is missing. Do not substitute Cari aging.
- **Owner decision required:** DQ-1 (margin), DQ-2 (KAPALI in stock clearance), DQ-4 (#DIV/0! stock coverage),
  DQ-5 (invoice-account customer attributes), revenue/accounting basis, native-line mapping, files of record,
  whitespace policy, Trodat mapping and salesperson of record.
- **Operational decisions:** cross-sell thresholds, fair taxonomy/conversion/ROI, January fair campaign details,
  and CRM coverage/score. Existing scores and source-native labels are kept.
- **Future work, not a launch gate for one local user:** single-pass order-source reader, authentication /
  role-based owner access, optional CRM import if an export appears, new-buyer discovery.

See [TASKS.md](../TASKS.md) for the full backlog and [m7_owner_integration.md](m7_owner_integration.md) for
the technical contracts. This document is a checklist, not an approval of Halsa's books.
