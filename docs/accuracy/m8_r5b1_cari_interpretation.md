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

Per Cari account: code, name, representative, signed balance as exported to the kuruş (or unknown), the oldest month
bucket with a non-zero amount (invoice age), the 320-supplier class, and the sign named neutrally. In Customer 360 the same per invoice
account, never allocated across shared accounts. All of it is still withheld by the gate until VERIFIED.

## What is withheld pending the agreement-based overdue report

Overdue amounts and totals, due dates, lateness, collection priority / TAHSİLAT actions, customer risk classification
and ratios, repayment warnings, AI collection outreach, the scenario's receivables basis.

## 320 accounts and negative balances

`320…` → class "Tedarikçi (320)", never a customer receivable or action; signed amount kept; sign named neutrally
("Negatif / Pozitif / Sıfır bakiye") because no supplier sign convention is confirmed. Unclassified accounts (120 and
others) in the account-level list: the same neutral sign names. Customer wording ("Halsa müşteriye borçlu" for a
negative, "Müşteri Halsa'ya borçlu" for a positive balance) appears only in Customer 360, on an invoice account used by
that customer's own orders and not a 320 account. The positive-side wording is the converse of rule D (a customer
balance is what Halsa is owed); it is not used for any account whose customer link is not established. No other prefix
is classified.

## Review corrections (PR #41 independent review)

1. **Exact signed amount.** `m_collections` returned `_n(b)`, which rounds to whole lira (−0.40 → 0 with direction
   "credit"). Now the balance is the exact decimal sum of the bucket values (computed on each value's shortest
   representation, so 0.1 + 0.2 = 0.3; −0.0 normalised to 0.0); blank stays None. Customer 360 reads the same rows. The
   Cari balance cells (dashboard table and Customer 360 card) use a separate `fmtMoneyExact` (2–6 decimals, tr-TR); the
   global `fmtMoney` is unchanged. Workbook bytes untouched.
2. **Neutral sign wording.** `BALANCE_NOTE` no longer states a debtor / creditor meaning for every account holder; the
   account-level list uses neutral sign names for all classes; `cari_policy.direction_label` gives customer wording only
   for a customer's own non-supplier invoice account (Customer 360). Column renamed "Bakiye İşareti".
3. **Predecessor policy.** `tests/test_m8_r5a_authorization_regressions.py` part B now runs for `m8-accuracy-1` and
   the immediate predecessor `m8-r5a-stock-retirement`. The R5A-era page is emulated with the old Cari "Tahsilat /
   Vadesi Geçen" snippet written before the refresh digests it (page and manifest consistent): refused live (`503`,
   `policy_marker_missing`) and after restart (`no_accepted_generation`, `evidence_mismatch`), no data, no old Cari
   claim; regeneration under the current policy serves normally with the synthetic register and, under the production
   register, displays neither `receivables.collections` nor `receivables.risk`. Negative control: with the marker left
   at the R5A value the old page is served `200`.
4. **Buyer overdue driver — unreachable, code unchanged.** `context.score_buyers` (passing `has_overdue=False` to
   `scoring.buyer_score`) runs only for clearance candidates; `m_overstock_move` returns none since D-070, so it is
   never called. Its only consumer, `evidence.product_packet` (`ranked_buyers`), sits behind `agent.analyze_product`,
   which refuses before building a packet; draft requests get no product (empty product index). The
   `overdue_penalty` driver therefore reaches no output, API or AI packet. Guarded by
   `test_buyer_overdue_driver_is_unreachable` (`buyer_score` patched to fail; context build never calls it).

## Gate

`receivables.risk`: + `CARI-OVERDUE-UNSUPPORTED` (INVALID). `receivables.collections`: relabelled "Cari bakiye ve fatura
yaşı"; blockers AGING-DATE, CARI-ACCOUNT-CLASS (new, NOT_VERIFIED), CARI-FX (the FX column was already documented as
unread), REAL-DATA; definition `cari_account_class` (unapproved). OVERDUE-DEF removed (answered). DQ-6 removed from
`customers.winback` (no code path; still INVALID by ceiling). Certified ceilings and approvals unchanged; 0 of 33
VERIFIED. `POLICY_VERSION` → `m8-r5b1-cari-invoice-age` (old pages need the existing controlled refresh).

## Tests

New `tests/test_m8_r5b1_cari_interpretation.py` (24 after the review corrections: + exact decimals incl. −0.40,
0.01, 0.1 + 0.2, a genuine zero and a blank; C360 = dashboard exact amounts; no debtor wording for 320 / unclassified;
fractional lira in the browser; buyer driver unreachable). Original 19: long-outstanding and recent invoices, signed balances, 320,
unclassified prefixes, blank / zero buckets, unchanged source bytes, context / actions / win-back / AI packets / drafts /
prompt, Customer 360 (negative, shared, supplier), dashboard, refresh, scenario, register, real API, CRM / Kredi dates.
Negative control: 15 of the 19 fail on `main`'s code (the 4 that pass are preservation guards).

Queue-mechanics tests (Günlük Satış, Fuar, CRM, Fırsatlar, their browser tests) keep exercising D-043 with an explicitly
synthetic stand-in producer (`tests/fixtures/synthetic_collections.py`; the browser servers opt in through a switch
that exists only in `tests/accuracy_harness_app.py`). Updated: domain rules, Customer 360, refresh, quality replay,
backend drafts (identity-only KAPALI test customer), accuracy (DQ-6 strict xfail replaced by a positive test).

Pristine `main` baseline (same environment): 1893 passed / 7 skipped / 2 xfailed. Branch full suite (browser + JS
required, AI disabled): **1912 passed / 7 skipped / 1 xfailed** at `07c4969` (CI push 41259418112, PR 41259449374 ✅).
After the review corrections: **1920 passed / 7 skipped / 1 xfailed** (1,928 collected = 1,920 + 5 new Cari tests + 3
predecessor-policy cases); focused Cari 168 passed / 1 xfailed, authorization 204 passed, generation / restart 225
passed. CI on the corrected head: in the PR.

## Acceptance matrix

| Criterion | Result |
|---|---|
| No overdue claim from invoice age in any dashboard, API, action, AI or draft path | PASS (synthetic) |
| Exact signed amounts (decimals, small negatives, zero, blank) in dashboard, Customer 360 and the browser | PASS |
| No debtor / receivable wording for 320 or unclassified accounts | PASS |
| Pages / manifests of `m8-r5a-stock-retirement` refused live and after restart; regeneration follows the real gate | PASS |
| Buyer overdue driver unreachable (documented, guarded) | PASS |
| Invoice-age buckets and signed balances preserved; blank = unknown, zero / negative kept | PASS |
| No collection urgency, risk classification, repayment warning or AI outreach from invoice age | PASS |
| Missing overdue evidence is unknown, never zero | PASS |
| 320 suppliers applied; no other prefix rule | PASS |
| Unrelated sales / customer-history / CRM / Kredi behaviour unchanged | PASS (full suite) |
| Source bytes, reconciliation, Veri Kontrol detection intact | PASS |
| No promotion; flag False | PASS |
| Planner independent review; owner-local check on the real Cari export | PENDING |
