# Roadmap — M6 (additional data sources) and M7 (sales operating system)

Status: **documented 2026-10-10, not started.** Owner answers to the open questions were incorporated
on 2026-10-10. Six additional monthly Excel reports materially affect the future architecture, but
**none of them is ingested in M5**.
Decisions: D-021 (roadmap and source-pipeline rule), D-022 (M5 action contract is extensible),
D-023 (data confidence), D-024 (sales organization and CRM coverage), D-025 (fair and acquisition
source attribution), D-026 (legitimate recorded transactions only).

Order: **M5 (AI Aksiyon Merkezi) is finished first → M6 → M7.** M4 (live macro data) and
*Multi-user / remote deployment* (D-016) are separate and unchanged.

---

## 1. Architectural rules for every source

### 1.1 Source pipeline (D-021)
Do **not** build spreadsheet-specific features directly into dashboard code. Every source goes
through the same layers:

```
Excel source (local, gitignored)
  → source-specific validator / importer      (columns, types, header row, dates, broken formulas)
  → normalized deterministic model            (only stable, useful fields; never the whole sheet)
  → entity resolution                         (connect to the existing customer / product identity)
  → analytics / services                      (Python computes every derived value)
  → actions                                   (same action contract as M5, D-022)
  → dashboard / API
  → optional AI explanation                   (advisory only; grounding + numeric guard as in M3)
```

- **Python owns every calculation.** The AI never computes, classifies, ranks or attributes money.
- **Real files stay local and gitignored** (`*.xlsx`, `data/`). GitHub holds the importer code,
  schemas, validation rules, **synthetic fixtures**, tests and docs. It never holds Halsa's workbooks.
- **Validation is fail-closed.** An importer reports source-quality problems explicitly: missing
  columns, unparseable values, formula errors such as `#REF!` or `#DIV/0!`, and stale dates. Bad data
  produces a warning or error and never silently enters StockAgent. Imports follow the same
  validate → build → verify → swap discipline as the monthly refresh (D-019).
- **Identity:** sales customer identity remains `Pro_kodu` / `ProjeIsmi` (D-003). Account and invoice
  identifiers in other reports (`Musterikod`, `Hesap kodu`, …) may differ, so every join goes through an
  explicit entity-resolution step with a match report (matched / unmatched / ambiguous). No silent joins.
- Monthly reports stay **replaceable**: a layout change breaks one importer and its tests, not the dashboard.

### 1.2 Not all source fields are equally trustworthy (D-023)
Normalized fields and sources carry **deterministic data-quality metadata** where it matters. These
statuses are assigned by Python and business rules, never by the AI:

| Status | Meaning | Examples (owner answers, 2026-10-10) |
|---|---|---|
| `trusted` | current and reliable (after technical validation) | 2020–2026 sales history; the order activity/source (fair) field in that report |
| `incomplete` | data exists, coverage is partial | CRM activity and call/visit outcomes |
| `stale` | a historical value, no longer maintained | `Haftalık Kaşe Adedi`; `Risk`; `Kredi` (hedef raporu) |
| `unverified` | meaning or grain not yet technically verified | any field before M6-0 verification |
| `invalid` | failed validation | bütçe cells with formula/reference errors |

Rules:
- Downstream Python logic may only use a non-`trusted` field in ways its status allows. A `stale` field
  never drives an automated decision as if it were current.
- Evidence packets carry the status as a warning, so the AI receives it alongside the value.
- **The AI must never upgrade a low-confidence or stale field into a factual current statement.**
- This is not a generic framework: each importer declares statuses for the fields it exposes.

### 1.3 Legitimate recorded transactions only (D-026)
StockAgent models only legitimate, recorded company transactions and validated accounting data. Any
future financing, shareholder or interest scenario uses recorded values and owner/accountant-approved
accounting treatment.

## 2. Trusted core: the 2020–2026 sales report

The owner considers the **2020–2026 sales report** (the existing main sales export) one of the most
valid and useful commercial datasets available. It is treated as **high-confidence commercial history,
pending technical verification**.

- It contains an **activity / source field** that records fair names and other order sources. The owner
  considers it a **valid, trustworthy record of where an order originated** (D-025).
- **Fair-generated orders can already be identified** in this data.
- The exact row grain (customer × SKU × document line?), the customer and product identifiers, the
  activity/source values, dates, quantities and sales values are **verified locally in M6-0** before any
  SKU-level or attribution feature relies on them. No SKU granularity is claimed until then.

## 3. M6 — Additional Data Sources / Sales Intelligence Foundation

Goal: deterministic, validated importers and adapters for the **useful parts** of the new reports,
normalized and connected to the existing entity layer. This is not a copy of the spreadsheets.

### Source 1 — Hedef raporu (`hedef raporu.xlsx`)
Contents (as described by the owner; to be confirmed by header inspection in M6-0): customer/account,
city, district, sector, salesperson, `Haftalık Kaşe Adedi`, historical stamp quantities, sales values,
visit count, `Risk`, `Kredi`.

**`Haftalık Kaşe Adedi`: a historical capacity estimate, `stale` (D-023).**
- *Meaning:* created about 30 years ago as an estimate of the customer's **total** stamp-production
  capacity / market volume. For example, about 50 stamps/week ≈ about 2,500/year, against which Halsa
  could compare its own volume (historically with an aspiration of capturing perhaps 20–30%).
- *Maintenance:* manually maintained in the past. There is **no reliable ongoing update process**, and it
  has effectively not been refreshed for about **4–5 years**. A customer recorded at 50/week may now be at
  300/week, and one recorded at 300/week may have shrunk or closed.
- *Rules:* keep the field, but **do not treat it as current capacity**. Normalize it as a historical
  capacity estimate (working name `historical_weekly_stamp_capacity`, status `stale`; the final name
  follows the codebase conventions). It may be used only with an explicit freshness/confidence warning.
  **Do not calculate current penetration against it as if it were authoritative.** A future
  deterministic model may compare the historical estimate with recent actual purchasing and/or a
  refreshed capacity estimate. The AI never states it as a current fact without the stale-data warning.

**`Risk` and `Kredi`: historical reference fields only, `stale` (D-023).**
- *Historical meaning:* `Risk` ≈ the total exposure Halsa was willing to carry for the customer;
  `Kredi` ≈ unsecured/open-account credit extendable without additional documents or security. For
  example, Risk ≈ 1M TL and Kredi ≈ 100k TL meant exposure up to about 1M might be acceptable, with
  about 100k extendable without extra security.
- *Status:* not reliably maintained for about 4–5 years.
- *Rules:* preserve them where useful, with a stale/unverified status. They **must not drive** automated
  credit decisions, collection actions, selling restrictions or AI recommendations as if they were valid
  current limits. Current-risk logic comes from trustworthy current receivables, payment and collections
  data and **explicit owner-approved rules**.

Eventual uses (subject to the rules above):
- **High potential / low penetration.** This is *not* Geri Kazanım, which already exists and is not
  rebuilt. It asks which active or known customers have more potential than Halsa captures. Because the
  only potential field is stale, this needs a deterministic model that compares the historical estimate
  with recent purchasing behavior, or a refreshed estimate, and shows confidence warnings.
- **Territory / portfolio views** for the two current primary salespeople: geographic coverage,
  high-potential accounts by region, unassigned accounts. No performance metrics from CRM activity
  counts (§5, D-024).

### Source 2 — Detailed collections report (`Evrak Detaylı Tahsilat Raporu`)
Enriches the existing collection and risk logic with document-level overdue amounts, aging, the oldest
overdue item and collection urgency. Identifiers may not match sales identity, so the entity-resolution
layer is used. Example future classification (Python-determined, never AI): **`TAHSİLAT ÖNCELİKLİ`**,
for a commercially valuable customer where additional credit should be reviewed before aggressive
selling. Its inputs are trustworthy current receivables data and owner-approved thresholds, **not** the
stale `Risk`/`Kredi` fields.

### Source 3 — Trodat sales report (monthly)
A product/SKU intelligence source: SKU trends, product-family growth, top-selling and declining models,
product concentration, cross-sell candidates, fair display priorities and fair inventory preparation,
and customer/product opportunities. It will eventually be combined with stock, historical sales,
customer purchases and product profitability *where reliable* (margin gaps, DQ-1).

### Source 4 — Gider / gelir (`2026_gider_gelir_Eylül.xlsx`)
An owner/management source: sales and gross-profit view **by business line** (kaşe/stamp,
kırtasiye/stationery, other/import lines), operating trend, fair economics, profitability context.
Python calculates all derived values; the AI never calculates profitability. It feeds an
**Owner / Yönetim** view, not salesperson screens.

### Source 5 — Bütçe (`bütçe eylül.xlsx`): `invalid` until validated
The workbook currently appears to contain **formula/reference errors**. It is not an authoritative
automated source until an importer validates the relevant cells and reports problems explicitly. A
broken formula is a validation error and never silently imported. Eventual uses: budget vs actual,
spending categories, fair budget, operating variance.

### Source 6 — Kredi takip (`kredi takip.xlsx`)
An owner / cash-planning source: loan payment calendar, outstanding principal, interest, next payments,
and 30/60/90-day financing commitments. It may later combine with receivables, supplier obligations (if
available), expected sales, inventory purchases and fair expenses into an owner cash-planning module.
It is not put into salesperson workflows without a specific business need.

### Proposed M6 sequence (each a bounded checkpoint; refined after M6-0)
- **M6-0 Source inventory and technical verification** (docs only; real files read **locally**, never
  committed). For each source: headers, types, fill rates and value *shapes* (never values). In
  particular: the exact row grain of the 2020–2026 sales report; the identifiers that join customer,
  product and source data; the values of the activity/source field; the CRM fields actually available;
  where the historical fair invitation/attendance lists live; and the thresholds needed for future
  deterministic rules.
- **M6-1 Importer framework:** a shared validator/importer base, a validation report, fail-closed rules,
  per-field data-confidence status (D-023), the entity-resolution match report, and synthetic-fixture
  conventions.
- **M6-2 Order activity/source normalization** in the 2020–2026 sales report: the fair and order-source
  attribution foundation (D-025).
- **M6-3 Hedef raporu importer** with stale-field statuses for `Haftalık Kaşe Adedi`, `Risk` and `Kredi`.
- **M6-4 Detailed collections importer**, with `TAHSİLAT ÖNCELİKLİ` after owner sign-off on thresholds.
- **M6-5 Trodat sales importer**: SKU / product-family model.
- **M6-6 Owner sources:** gider/gelir by business line, then kredi takip, then bütçe (only with
  cell-level validation).

## 4. M7 — Sales Operating System

After M6 normalizes the required data, turn the deterministic analyses into a practical sales workflow.
The rule throughout: **Python selects and ranks; AI may explain but never selects or ranks.**

1. **Daily sales queue: "Bugün kimi aramalıyım?"** Python-ranked accounts, with deterministic reasons
   such as: high potential / low penetration (with its confidence warning), growth opportunity,
   declining active account, cross-sell opportunity, follow-up due, January fair invite due,
   collection-first, or an existing Geri Kazanım action.
2. **Customer 360**, combining where available:
   - *Sales:* history, current year, growth or shrink, last purchase, order behavior, categories.
   - *Potential:* historical capacity estimate (stale), penetration only via the deterministic model,
     cross-sell.
   - *Financial:* receivables, overdue position (current data; Risk/Kredi shown only as stale
     historical reference).
   - *Commercial:* salesperson, geography, sector, visits and follow-ups (with CRM coverage warning).
   - *Fair:* attributed orders from the activity/source field; invitation/attendance once those lists
     are recovered.
   - *AI:* advisory interpretation only.
3. **Cross-sell:** deterministic product/category gaps, for example a customer who buys Trodat but not
   relevant complementary products, or a strong customer with an unusually narrow assortment. Python
   detects the pattern.
4. **January Fair Command Center.** Fair-generated orders are **already identifiable** through the
   trusted activity/source field (D-025). So M7 starts from **existing attribution**, not a new
   attribution system: fair customers, orders, revenue, products and repeat purchases after the fair,
   all computed deterministically. Historical invitation/attendance lists can be recovered later to add
   the funnel: `Davet Edilecek → Arandı → Kabul → Otel Teyit → Görüşme Planlandı → Katıldı → Teklif →
   Sipariş → Takip`. Customer groups: strategic existing, high potential / low penetration, Geri Kazanım
   candidate, growth, new prospect, collection-sensitive strategic. Event ROI is only computed from
   recorded, attributed values.
5. **Lightweight CRM / follow-up (minimal):** salesperson, last contact, outcome, next action, next
   follow-up date, optional note. It complements ERP facts and does not replace the ERP. It includes a
   **CRM coverage / data-completeness** requirement (D-024): CRM-derived metrics carry a coverage warning
   until logging is standardized, and Python never ranks salesperson performance on raw activity counts
   without approved completeness logic.
6. **Sales Support / Inside Sales Support** (planned office-based assistants who support the two primary
   salespeople with administrative and customer follow-up work). The likely need is a follow-up and
   task queue they can work through on behalf of the salespeople. These hires are **not** additional
   field salespeople: no new territories, no direct sales-performance comparisons. Not designed in
   detail yet.
7. **New buyer discovery and acquisition-source tracking** (D-025). Current acquisition channels are
   Google / inbound search (considered particularly valuable), fairs, referrals, distributor lists, and
   historical records / reactivation. New customers are hard to acquire. Future categories might be
   `google_inbound`, `fair`, `referral`, `distributor_list`, `historical_reactivation` and `other`.
   *Geri Kazanım* (reactivating past customers) and *Yeni Potansiyel Müşteri* (no known prior Halsa
   relationship, checked against the entity layer) stay separate. **No web scraping or external search
   in M5 or M6.**

Per-salesperson queues and access for the sales team depend on the **Multi-user / remote deployment**
milestone (D-016).

## 5. Sales organization and CRM data quality (D-024)

- **Two current primary salespeople** (names are never written into the repository).
- **Planned new hires are office-based sales support / inside-sales assistants**, not field
  salespeople (§4.6). There is no plan to add field salespeople.
- **CRM usage is inconsistent between roles:** one primary salesperson logs consistently, the other
  intermittently, and the senior field representative does not use the CRM consistently.
- **Consequence:** CRM interaction counts are **not** comparable performance metrics. For example, 120
  vs 30 vs 0 records must not be read as 4× activity or no activity, since the difference may be logging
  behavior. Call/visit outcomes exist where entries exist, but they are not a complete history of sales
  interactions.

## 6. Business context for future prioritization (not scope)

Owner context recorded to guide later choices. **None of this is M5/M6 scope**, and nothing here is an
AI decision-maker.

- **Stamp (kaşe) business:** the core, resilient business and an important profit driver. Management's
  problem is to maximize reasonable margin without losing unacceptable volume. Small unit-price
  differences have large annual effects because volume is large. Future deterministic analysis could
  cover price realization, discount leakage, customer-level pricing, volume vs price changes, margin
  opportunity and retention after price changes. It would be based on validated cost and sales data.
  **No AI price setter.**
- **Stationery (kırtasiye) business:** demand is weak, payment terms are long, and some inventory should
  be reduced or liquidated rather than expanded. Future analysis could cover inventory age, stock value,
  sales velocity, margin, obsolescence/expiry risk, liquidation candidates and cash conversion, from
  stock and commercial data.
- **Business-line visibility:** Owner/Yönetim views should separate kaşe/stamp, kırtasiye/stationery and
  other/import lines where possible (natural home: gider/gelir).
- **Imports and availability:** import and customs delays cause stockout risk, lost sales and temporary
  scarcity (sometimes pricing power). This opportunity covers purchase/shipment/ETA/customs/stockout
  visibility, and **only if trustworthy data becomes available**. Nothing is invented.
- **Operational knowledge risk:** reduced staff coverage of machine/product technical knowledge, with
  two salespeople being cross-trained. A future opportunity is a small internal product/machine
  knowledge base.

## 7. Owner questions: status (2026-10-10)

None of these block M5.

| # | Question | Status | Answer / remaining step |
|---|---|---|---|
| 1 | What does `Haftalık Kaşe Adedi` represent? | **Answered** | Historical estimate of the customer's total stamp capacity (about 30 years old); `stale` (§3) |
| 2 | Who maintains it? | **Answered** | Manually maintained in the past; there is no current owner or process |
| 3 | How often is it updated? | **Answered** | Not systematically refreshed for about 4–5 years |
| 4 | How reliable is it? | **Answered** | Low for current use; historical signal with a warning only |
| 5 | What do `Risk` and `Kredi` mean? | **Answered** | Historical exposure / unsecured-credit limits; stale; reference only (§3) |
| 6 | Who are the current salespeople? | **Answered** | Two primary salespeople (names kept out of the repo) |
| 7 | How will the new hires be assigned? | **Answered** | Office-based sales support, not field territories (§4.6) |
| 8 | Are visits recorded consistently? | **Answered** | No, CRM usage is inconsistent between roles (§5) |
| 9 | Are call/visit outcomes recorded? | **Partially answered** | They exist where CRM entries exist, but coverage is incomplete. The fields actually available are verified in M6-0 |
| 10 | Do historical fair invite/attendance lists exist? | **Answered** (technical follow-up) | Yes, recoverable. File locations and format are confirmed in M6-0 |
| 11 | Can fair orders be identified in the ERP? | **Answered** | Yes, via the trusted activity/source field in the 2020–2026 sales report (§2) |
| 12 | Does the sales export contain customer + SKU detail? | **Mostly answered, technical verification required** | Trusted commercial source; exact row grain and identifiers are verified in M6-0 |

**Remaining technical verification (M6-0):** exact workbook headers and types; the exact row grain of
the 2020–2026 sales report; the join identifiers for customer, product and source; the CRM fields
actually available; the locations of the historical fair lists; and thresholds for future deterministic
rules (with owner sign-off).

## 8. What M5 must guarantee so M6/M7 plug in without a redesign (D-022)

See `docs/m5_action_center.md` §14. In short: one generic action contract, categories described by the
server rather than hard-coded in the UI, and deterministic action sources registered behind one
interface. The data-confidence statuses (D-023) will flow into evidence packets as warnings through the
existing `warn()` / `fact()` API, so M5's AI pipeline needs no change to respect them.
