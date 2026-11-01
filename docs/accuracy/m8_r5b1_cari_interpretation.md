# M8-R5B1 — Cari accounting interpretation (D-071, 2026-10-31)

Fresh clone of GitHub `main` @ `3f2419940d1a6e185bee433d35f7300091138a1e`, branch `claude/m8-r5b1-cari-interpretation`.
Synthetic data only; no private workbook, `.env`, SQLite or Mac folder used. Not merged, not deployed, no metric
promoted, `GENERATION_BOUND_API_EVIDENCE` False. PR #39 untouched.

## Business rules (D-071)

The Cari export's month buckets are **invoice age** (time since the invoice was issued), not due dates. Overdue status
depends on each customer's agreement; the ERP report that shows it (red-highlighted entries) has not been supplied.
Accounts starting with **320 are suppliers**; 120 is not thereby a confirmed customer account. A **negative** customer
balance means Halsa owes the customer. Source values are authoritative.

## Dependency trace — every calculation and consumer found

| Path | Old behaviour | New behaviour |
|---|---|---|
| `dashboard_builder.m_collections` | accounts with Σ buckets > 0 as "Vadesi Geçen"; negatives and zeros dropped; blank bucket = 0; ranked by size | every numeric-coded account; signed balance (blank bucket → unknown); oldest non-zero bucket = invoice age; class (320 supplier / unclassified); direction; neutral code order; returns `{code: balance}` that nothing reads as overdue |
| Dashboard "Tahsilat" tab + note "kovalayın" | overdue list, chase the top | "Cari Bakiye (fatura yaşı)": account, rep, class, signed balance, direction, oldest invoice month; fixed D-071 notes |
| Stat cards "vadesi geçen toplam", "borçlu kayıp müşteri" | overdue total; lapsed customers with debt | removed (also from `STAT_METRIC`) |
| `m_risk` → "Riskli Müşteri" | positive balance + debt/revenue ratio + "alıp ödemiyor" | returns `[]`; tab shows the withdrawal reason |
| `m_lapsed` → Geri Kazanım | "Vadesi Geçen" column, yellow "collect before chasing" highlight (also DQ-6 identity mismatch) | no debt field; `owed` argument accepted, never read |
| `services/context.py` | collection scoring (`collections_priority`), `collections` / `risk` lists, `overdue_total`, buyers' `has_overdue` | no Cari read; `collections = risk = []`; `overdue_total = None` (unavailable, not zero); no debt flag |
| `action_sources.produce_collections` | TAHSİLAT actions above a priority threshold | returns `[]` (stale / injected rows too); category kept registered |
| `action_sources.produce_winback` | overdue barrier lowered confidence, "Vadesi geçen borç" fact, "önce tahsilat" reason | removed |
| `services/entity.py` customer index | `overdue`, `oldest_overdue`, `collections_score`, `debt_to_revenue_pct` merged into facts | identity only from collection / risk rows |
| `services/evidence.py` customer packet | "Vadesi geçen bakiye", `has_overdue` flag, oldest debt, debt ratio, collection score | overdue reported **missing** with a warning; none of the rest |
| `evidence.draft_packet` (collection) | overdue amount and oldest debt for the customer message | no amount; overdue missing; warning: no lateness, due date or amount may be written |
| `evidence.product_packet` | buyers' "vadesi geçen borcu var" (product AI already retired) | removed |
| `prompts/business_agent.txt` | "önce tahsilat görüşmesi" example; KAPALI "yalnızca tahsilat ve risk" | rule: buckets are not due dates; no overdue / collection / repayment / debt-risk claims |
| `services/customer360.py` | per invoice account `overdue = balance if > 0 else 0` (negatives zeroed) | signed `balance`, `account_class`, `direction` (+ customer labels), `oldest_bucket`, `overdue: null`, `overdue_status: "unavailable"`; D-071 limitations |
| `schemas.C360Aging` + Customer 360 card JS | "Vadesi geçen bakiye (hesap)" | "Cari hesap": class, signed balance, direction, invoice month, overdue "bilinmiyor" |
| Günlük Satış / Fuar / Yönetim `collection_first` | true when a Cari collection reason existed | mechanism unchanged; no Cari reason reaches it (CRM collection follow-ups still do); two JS strings no longer say "vadesi geçen" |
| `services/refresh.verify` | reconciled the overdue total | reconciles `None` = `None`; a number where none exists fails |
| `app.scenario` | funding cost / erosion on the overdue total | total is `None` → both results unavailable |
| `services/accuracy.py` | OVERDUE-DEF, DQ-6 on win-back | see "Gate" below |
| `services/vkm.py` | text "which accounts belong in collections" | text "320 = suppliers; others unclassified" (detection unchanged) |
| Not touched | CRM follow-up due states, Kredi installment due dates / past-due, source scanner, reconciliation, Veri Kontrol detection, sales / stock calculations | unchanged |

## What remains factual and available

Per Cari account: code, name, representative, signed balance as exported (or unknown), the oldest month bucket with a
non-zero amount (invoice age), the 320-supplier class, and the sign direction. In Customer 360 the same per invoice
account, never allocated across shared accounts. All of it is still withheld by the gate until VERIFIED.

## What is withheld pending the agreement-based overdue report

Overdue amounts and totals, due dates, lateness, collection priority / TAHSİLAT actions, customer risk classification
and ratios, repayment warnings, AI collection outreach, the scenario's receivables basis.

## 320 accounts and negative balances

`320…` → class "Tedarikçi (320)", no customer direction label, never a customer receivable or action; signed amount
kept. Negative balances are kept with their sign, labelled "Halsa hesap sahibine borçlu" (dashboard) / "Halsa
müşteriye borçlu" (Customer 360, where the account is the customer's own invoice account). No other prefix is
classified.

## Gate

`receivables.risk`: + `CARI-OVERDUE-UNSUPPORTED` (INVALID). `receivables.collections`: relabelled "Cari bakiye ve fatura
yaşı"; blockers AGING-DATE, CARI-ACCOUNT-CLASS (new, NOT_VERIFIED), CARI-FX (the FX column was already documented as
unread), REAL-DATA; definition `cari_account_class` (unapproved). OVERDUE-DEF removed (answered). DQ-6 removed from
`customers.winback` (no code path; still INVALID by ceiling). Certified ceilings and approvals unchanged; 0 of 33
VERIFIED. `POLICY_VERSION` → `m8-r5b1-cari-invoice-age` (old pages need the existing controlled refresh).

## Tests

New `tests/test_m8_r5b1_cari_interpretation.py` (19): long-outstanding and recent invoices, signed balances, 320,
unclassified prefixes, blank / zero buckets, unchanged source bytes, context / actions / win-back / AI packets / drafts /
prompt, Customer 360 (negative, shared, supplier), dashboard, refresh, scenario, register, real API, CRM / Kredi dates.
Negative control: 15 of the 19 fail on `main`'s code (the 4 that pass are preservation guards).

Queue-mechanics tests (Günlük Satış, Fuar, CRM, Fırsatlar, their browser tests) keep exercising D-043 with an explicitly
synthetic stand-in producer (`tests/fixtures/synthetic_collections.py`; the browser servers opt in through a switch
that exists only in `tests/accuracy_harness_app.py`). Updated: domain rules, Customer 360, refresh, quality replay,
backend drafts (identity-only KAPALI test customer), accuracy (DQ-6 strict xfail replaced by a positive test).

Pristine `main` baseline (same environment): 1893 passed / 7 skipped / 2 xfailed. Branch full suite (browser + JS
required, AI disabled): **1912 passed / 7 skipped / 1 xfailed**; focused set 438 passed / 1 xfailed. CI: in the PR.

## Acceptance matrix

| Criterion | Result |
|---|---|
| No overdue claim from invoice age in any dashboard, API, action, AI or draft path | PASS (synthetic) |
| Invoice-age buckets and signed balances preserved; blank = unknown, zero / negative kept | PASS |
| No collection urgency, risk classification, repayment warning or AI outreach from invoice age | PASS |
| Missing overdue evidence is unknown, never zero | PASS |
| 320 suppliers applied; no other prefix rule | PASS |
| Unrelated sales / customer-history / CRM / Kredi behaviour unchanged | PASS (full suite) |
| Source bytes, reconciliation, Veri Kontrol detection intact | PASS |
| No promotion; flag False | PASS |
| Planner independent review; owner-local check on the real Cari export | PENDING |
