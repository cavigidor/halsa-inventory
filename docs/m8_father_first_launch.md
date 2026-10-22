# M8 — Father-first production launch (plan, no implementation yet)

> **Revision 2026-10-22 (D-060, M8-CP1 implemented locally, not deployed).** Halsa Analysis is a **browser-based web
> application** — the father opens a link; nothing is installed. **Viewing needs no login.** The **owner** logs in
> (one password, no accounts) for every administrative or data-changing operation: report upload / refresh, CRM and
> follow-up edits, fair and action changes, data-review decisions, verification approvals, business rules, system
> settings, paid AI, and the owner-only Yönetim / diagnostics views. Public-by-link viewing carries real disclosure
> risk (customer names, CRM and fair notes are viewer-visible today; every future VERIFIED metric becomes viewer-visible
> too) — see `docs/m8_cp1_access.md` §6. **External deployment still requires the owner's explicit, recorded
> sign-off.** Points 1–2 and the CP1 paragraph below are kept for the record and marked where superseded.

**Status:** Planning checkpoint, 2026-10-18. The owner lives in the USA and handles technical support, hosting,
monthly uploads and business maintenance. His father in Türkiye (age 70) should only have to open a bookmarked
HTTPS link and use a Turkish dashboard. The staff exports monthly ERP workbooks; the owner uploads them.
**Normal recurring technical step after launch: upload and confirm one validated monthly batch; no shell,
GitHub, server restart, workbook copies between computers, or dashboard build command.**

This plan preserves all existing M1–M7 functionality, as well as the *uncompleted* father-message priorities. It
does not pretend that every requested capability is already built or that all business inputs are sufficient.
The original voice recordings / verbatim transcripts are **not** included in the verified repository sources;
a true quote-by-quote signoff requires that source or the father's explicit confirmation. Do not label inferred
features as direct voice requests without evidence. Business goals recorded in the repository and prior planning
include stamp/kaşe profitability and market position, aging stationery inventory/cash conversion, stockout/import
risk, customer acquisition (particularly Google and trade fairs), customer follow-up, staffing support, targets,
and owner-financial reporting.

## Hard boundaries and operating design

1. **Users at launch** *(superseded in part by D-060: the father views without a login; only the owner logs in, for
   administration)*: owner + father only, both owner/admin authorization, each with their own login.
   Salesperson provisioning and per-portfolio authorization are later. DO NOT allow salespeople access to the
   present owner-financial screens. Never expose the current unauthenticated app on the public internet.
2. **Access** *(superseded in part by D-060: read-only pages open without login; owner-only routes, mutations and
   verification remain protected; `/docs` and `/openapi.json` are disabled)*: bookmarked HTTPS URL, accessible from
   US and Türkiye; father's routine is open page and read/click.
   Login should be low-friction but secure, with persistent bounded session, clear Turkish login/recovery UX.
   Never hardcode shared passwords, bypass authentication in endpoints/static HTML, or treat `X-StockAgent` as
   identity verification. Secure all routes including `/`, `/docs`, `/openapi.json`, state mutations, files
   and APIs before any external deployment.
3. **Computations:** Python deterministically computes all numbers and ranks actions. AI is disabled by default,
   optional on-demand only, and gets no owner financial workbooks or raw ERP data. No automatic sending of drafts.
4. **Hosting:** keep existing FastAPI and SQLite initially if a **verified persistent volume + off-instance
   encrypted backups + tested restore** suffice; no PostgreSQL migration for its own sake. One app instance/
   process and a safe single-writer workflow; never run SQLite on ephemeral deployment storage. Select host based
   on durable storage, security and data-transfer compliance for Halsa's data in Türkiye. Owner handles accounts/
   billing/secrets, not father.
5. **Monthly update:** One owner-only `Veri Güncelleme` workflow, staged upload of individually identified and
   explicitly approved report types and period. It validates Excel formula/cached values and business date;
   computes ALL affected M5–M7 dashboards, APIs and derived owner views; commits one consistent generation or
   retains the preceding one. No "new core vs old owner" mixed-period publication. Store run history and provenance,
   active period/source basenames, required/optional missing status, no silent automatic file-of-record selection.
   Uploading a workbook that is not supplied must show `missing` / `stale`, never fill with zero or infer facts.
   Support partially supplied optional source types without claiming the entire bundle is complete.
6. **Preserve local operator state:** SQLite action statuses, CRM interactions/follow-ups, fair campaigns/members,
   user accounts and optional AI cache survive updates, restarts and rollbacks. Old action IDs must not be falsely
   applied to unrelated new facts. Validate concurrent update requests and state writes; fail closed.
7. **Backups/recovery:** before publishing a batch preserve the previous valid generation, protect persistent
   state and uploads, automatic encrypted off-instance backups (with rotation), restore drill, simple rollback UI
   for owner and clear log status. On importer, validation, publish or startup failure serve previous valid view;
   never publish half-built data or leak filesystem paths/user data to clients/logs.
8. **UX:** Turkish, readable typography/contrast at desktop and phone width, big obvious controls, simple navigation,
   visible `son başarılı güncelleme`, data period and warnings, clear progress/errors, helpful no-data displays.
   Avoid hosting accounts, terminals, deployment links and technical jargon in father's UI.
9. **Unapproved business semantics stay unresolved:** Kredi `DURUM=Kapalı` is UNVERIFIED until 2026-10-19;
   do not use it to suppress unpaid installments. Snapshot freshness/current loan balances are separate questions.
   DQ-1/2/4/5, file of record, margin/cost completeness, tax/accounting basis, fair taxonomy/windows/ROI,
   CRM incompleteness, cross-sell threshold, owner report lines, SKU mapping, revenue definitions and legacy
   data cannot be "solved" by guessing. M6-4 detailed collections requires the actual export (no Cari proxy).
10. **Release gate:** synthetic CI success alone ≠ ready. Independent production authorization check, actual private
    workbook preflight (local/private only), simulated valid/invalid monthly upload with rollback, backup/restore,
    long-running hosted app restart, HTTPS/US↔TR remote-browser check and father usability acceptance required.
    Owner must explicitly confirm the final scope and any excluded/blocked source-dependent functionality.

## Existing baseline (verified in repo)

- Complete technical M7 checkpoints: Customer 360, Günlük Satış, Fırsatlar (cross-sell), Fuar Merkezi,
  Takip/CRM, Yönetim (PR #24 merged); M5 deterministic action/AI only-on-click; PR #25 local owner preflight.
- Current `services/refresh.py` / `POST /api/context/refresh`: controlled **core-only** sales/stock/Cari
  validate→build→verify→swap. Optional Hedef/Kredi/Gider-Gelir/Bütçe have distinct loaders and are NOT
  coordinated by that refresh; the owner button reloads owner workbooks only.
- Owner finans: Kredi scheduled payments with unknown `DURUM`, Gider/Gelir *source-native* figures with
  unverified accounting basis and management mapping, Bütçe **validity view only**; no verified budget-vs-actual.
- Existing SQLite CRM/fair/action history is local. No public URL, authentication, role enforcement or live remote
  verification has been claimed. Current generated HTML and APIs are not suitable for public deployment as-is.

## Capability acceptance matrix (not equal to "done")

| Business need | Present | Gate for being called finished |
|---|---|---|
| Stock shortage, overstock, clearance, win-back, growth/decline, order-size, receivables, action queue | Built | Monthly update recomputes; DQ-1/2/4/5 owner rule signoff, source dates correct |
| Customer 360, daily sales queue, cross-sell | Built | Correct data publication; owner approves/tunes overly broad cross-sell (78% active candidates) |
| Fair management | Historical attribution + lightweight campaigns built | Owner supplies taxonomy, follow-on conversion window, lists/costs if ROI required |
| CRM | Manual interactions/followups built | Shared state persists; logging coverage remains `incomplete` until justified |
| Sales targets and territories | Hedef importer/read-only context built | Fresh authoritative Hedef, stale capacity/risk/credit never used as current |
| Trodat cross-product intelligence | Native importer built, no SKU mapping | Trusted model→Halsa SKU mapping / units and agreed reports |
| Gider/Gelir, profitability and budget comparison | Native-line read-only and budget-health built | Finance supplies mapping/basis/repaired workbook/source authority; no invented variances |
| Company credit obligations | Conservative schedule built | Owner clarification of `DURUM` + authoritative dated source; not a cash forecast |
| Detailed collections | BLOCKED | `Evrak Detaylı Tahsilat Raporu` and collection thresholds |
| New customer acquisition | Historical order-source/fair signals; new-buyer workflow incomplete | Owner defines supplied prospect sources, Google attribution and acceptable workflow; no scraping |
| Digital marketing/website/product/email assistance, import and machinery risks | Business priorities, not completed dashboard modules | Explicit owner decision on whether these are M8 release gates vs separate projects and their data |
| Unified monthly upload and hosted father experience | NOT BUILT | End-to-end production acceptance tests and father usability trial |

## Expected Codex checkpoints (8 implementation PRs after this plan; one optional Kredi follow-up)

**CP1 — Simplified access (as implemented, D-060):** one owner administration login (scrypt hash in the private
`.env`, server-side revocable sessions, CSRF, throttling, secure cookies, fail-closed register of every route); viewer
pages without login; owner-only Yönetim and diagnostics; `/docs` disabled; no deployment. Details and the disclosure
inventory: `docs/m8_cp1_access.md`. *Original CP1 text (superseded):* create real owner/father auth, secure all HTML/API/mutations and verification,
session management, hardened headers, CSRF, login/recovery path, 2 seeded owner accounts via noncommitted secret flow.
No public deployment until accepted. CI: unauthenticated read/write uniformly denied, identity not from header.

**CP2 — Deployable persistent runtime:** host-compatible startup, paths and single-writer setup, persistent SQLite/
uploads, health/readiness, safe process restart, staging env, no embedded secrets. Do not claim durability without
actual host volume and backup configuration. CI: temp persisted mount + restart test.

**CP3 — Owner-only upload UX and staging:** browser multi-report drop/select with manifest, explicit source type,
business date, expected reporting period, size limits and safe filenames, validation preview per existing importer.
No publication. CI: malformed files, path traversal, MIME spoof, formula errors, ambiguity, oversized requests.

**CP4 — One-generation monthly publication:** orchestrate core+optional sources, reuse validated imports without
weakening fail-closed checks; generate dashboard and Customer 360/opportunities/fairs/owner views consistently, publish
one revision atomically and invalidate old in-memory caches, preserve untouched operator state and last-good revision.
CI: mixed-period prevention, new-source arrival, missing optional source, fail/restart mid-swap, rollback.

**CP5 — Backups, recovery and operations:** versioned uploads/data, durable daily off-instance backup schedule,
restore drill, alerts to owner only, log redaction, update history/retry/publish status and runbook. Confirm backup
with real hosting rather than believing the presence of a local script. CI: restore synthetic CRM/fair/action state.

**CP6 — Agreed scope gap closure:** father-response-driven, separately gated fixes to DQ/revenue, fair/CRM/targets/
Trodat/acquisition where available data and explicit approval support them. Maintain a visible pending list for
blocked items. No speculative finance, no proxy for M6-4, no automatic launch of new website/marketing systems.
If too large, split CP6 into multiple bounded PRs and revise estimate openly.

**CP7 — Father-first Turkish UX / acceptance harness:** simplify navigation, readability and source-status warnings;
auto-health/user-friendly errors, accessible login, phone/desktop 390px checks, support/no technical screens for
father; add end-to-end browser suites for owner upload/refresh/fail-safe/data consistency.

**CP8 — Real hosting and cutover:** *(D-060: first obtain the owner's recorded sign-off on the public-by-link
disclosure inventory, `docs/m8_cp1_access.md` §6; HTTPS with trusted proxy headers is required for owner login)*
provision hosting through user-authorized integrations, private TLS domain,
authorized accounts, persistent storage, backups, upload real exports outside GitHub, full integration smoke from
USA/Türkiye, father-facing bookmark, safe rollback, signoff checklist. Never claim deployed until URL and tests work.

**Kredi follow-up**: one narrow separate change after the definition arrives; if status is informational only,
document it rather than forcing a rule change. Reconcile against a dated authoritative source.

## User-only external actions

Owner must supply hosting/payment access and any custom DNS controls; choose/approve where Halsa's private ERP
data may be stored (review Turkish KVKK cross-border transfer implications); upload actual exports via encrypted
private application flow (never GitHub/chat); set up personal/father accounts securely; decide official file-of-record
for each source and answer unresolved business questions; personally test father login from Türkiye *(D-060: the
father no longer logs in — test the father's link without login and the owner's login from the US instead)*.

The father should never be responsible for these tasks. Staff only generate the monthly ERP reports.
