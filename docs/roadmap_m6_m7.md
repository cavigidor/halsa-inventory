# Roadmap — M6 (additional data sources) and M7 (sales operating system)

Status: **documented 2026-10-10, not started.** The owner has provided six additional monthly Excel
reports. They materially affect the future architecture, but **none of them is ingested in M5**.
Decisions: D-021 (roadmap and source-pipeline rule), D-022 (M5 action contract is extensible).

Order: **M5 (AI Aksiyon Merkezi) is finished first → M6 → M7.** M4 (live macro data) and
*Multi-user / remote deployment* (D-016) are separate and unchanged.

---

## 1. Architectural rule for every new source (D-021)

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

## 2. M6 — Additional Data Sources / Sales Intelligence Foundation

Goal: deterministic, validated importers and adapters for the **useful parts** of the new reports,
normalized and connected to the existing entity layer. This is not a copy of the spreadsheets.

### Source 1 — Hedef raporu (`hedef raporu.xlsx`): highest priority
Contents (as described by the owner; to be confirmed by header inspection in M6-0): customer/account,
city, district, sector, salesperson, `Haftalık Kaşe Adedi` (weekly stamp quantity / potential),
historical stamp quantities, sales values, visit count, risk/credit-related fields.

Eventual uses:
- **High potential / low penetration.** This is *not* Geri Kazanım, which already exists and is not
  rebuilt. The question is: *which active or known customers have significantly more potential than the
  volume Halsa currently captures?* Once its meaning is confirmed, Python may compare estimated
  potential (possibly from `Haftalık Kaşe Adedi`) with actual Halsa purchase volume.
  **The field's business definition and reliability must be confirmed with the owner first. No
  interpretation is invented.**
- **Salesperson / territory intelligence:** portfolios, geographic coverage, high-potential accounts by
  region, unassigned accounts, workload, visit activity, and new-hire portfolio allocation.
  **No performance metrics** until the meaning and quality of the fields are confirmed.

### Source 2 — Detailed collections report (`Evrak Detaylı Tahsilat Raporu`)
Enriches the existing collection and risk logic. Possible fields: overdue amount, aging, oldest overdue
item (document-level), collection urgency. Identifiers may not match sales identity, so the
entity-resolution layer is used.
Example future classification (Python-determined, never AI): **`TAHSİLAT ÖNCELİKLİ`**, for a
commercially valuable customer where additional credit should be reviewed before aggressive selling.
This supports sales-vs-collection decisions.

### Source 3 — Trodat sales report (monthly)
A product/SKU intelligence source: SKU trends, product-family growth, top-selling and declining models,
product concentration, cross-sell candidates, fair display priorities and fair inventory preparation,
and customer/product opportunities. It will eventually be combined with stock, historical sales,
customer purchases and product profitability *where reliable* (margin gaps, DQ-1), for the January fair
and normal sales work.

### Source 4 — Gider / gelir (`2026_gider_gelir_Eylül.xlsx`)
An owner/management source: sales and gross-profit view by business line (Trodat vs kırtasiye vs other
divisions), operating trend, fair economics, profitability context. **Python calculates all derived
values; the AI never calculates profitability.** It feeds an **Owner / Yönetim** view, not salesperson
screens.

### Source 5 — Bütçe (`bütçe eylül.xlsx`): cautious
The workbook currently appears to contain **formula/reference errors**. It is **not an authoritative
automated source** until an importer validates the relevant cells and reports problems explicitly. A
broken formula is a validation warning or error and never silently imported. Eventual uses: budget vs
actual, spending categories, fair budget, operating variance.

### Source 6 — Kredi takip (`kredi takip.xlsx`)
An owner / cash-planning source: loan payment calendar, outstanding principal, interest, next payments,
and 30/60/90-day financing commitments. It may later combine with receivables, supplier obligations (if
available), expected sales, inventory purchases and fair expenses into an **owner cash-planning
module**. It is **not** put into salesperson workflows without a specific business need.

### Proposed M6 sequence (each a bounded checkpoint; refined after M6-0)
- **M6-0 Source inventory and owner Q&A.** Read the real files' headers and data types *locally*,
  write a field inventory per source (names, types, fill rate, sample *shapes* only, never values), and
  get answers to the §4 questions. Docs only.
- **M6-1 Importer framework.** Shared validator/importer base, a validation report model, fail-closed
  rules, the entity-resolution match report, and synthetic-fixture conventions.
- **M6-2 Hedef importer** → normalized accounts / potential / territory model.
- **M6-3 Detailed collections importer** → enriched receivables; the `TAHSİLAT ÖNCELİKLİ` rule (after
  owner sign-off on its thresholds).
- **M6-4 Trodat importer** → SKU / product-family model.
- **M6-5 Owner sources:** gider/gelir, then kredi takip, then bütçe (only with cell-level validation).

## 3. M7 — Sales Operating System

After M6 normalizes the required data, turn the deterministic analyses into a practical sales workflow.
The rule throughout: **Python selects and ranks; AI may explain but never selects or ranks.**

1. **Daily sales queue: "Bugün kimi aramalıyım?"** Python-ranked accounts, with deterministic reasons
   such as: high potential / low penetration, growth opportunity, declining active account, cross-sell
   opportunity, follow-up due, January fair invite due, collection-first, or an existing Geri Kazanım
   action.
2. **Customer 360**, one screen combining, where available:
   - *Sales:* history, current year, growth or shrink, last purchase, order behavior, categories.
   - *Potential:* potential, Halsa penetration, cross-sell.
   - *Financial:* receivables, overdue position, risk/credit indicators.
   - *Commercial:* salesperson, geography, sector, visits and follow-ups.
   - *Fair:* invitation, attendance, meeting, quote, fair-attributed order.
   - *AI:* advisory interpretation only.
3. **Cross-sell:** deterministic product/category gaps, for example a customer who buys Trodat but not
   relevant complementary products, buys one family but not a commonly paired one, or is a strong
   customer with an unusually narrow assortment. Python detects the pattern; no LLM discovers purchasing
   patterns.
4. **January Fair Command Center.** Stages: `Davet Edilecek → Arandı → Kabul → Otel Teyit → Görüşme
   Planlandı → Katıldı → Teklif → Sipariş → Takip`. Customer groups: strategic existing customer, high
   potential / low penetration, Geri Kazanım candidate, growth customer, new prospect, and
   collection-sensitive strategic customer. Fair attribution would eventually give invited, attended,
   reactivated, new customers, quotes, orders, post-fair revenue and gross profit, and event ROI. **All
   financial attribution is deterministic.**
5. **Lightweight CRM / follow-up (minimal):** salesperson, last contact, outcome, next action, next
   follow-up date, optional note. It complements ERP facts and does not replace the ERP.
6. **New buyer discovery** is separate from Geri Kazanım. *Geri Kazanım* means the customer bought from
   Halsa before and should potentially return. *Yeni Potansiyel Müşteri* means there is no known prior
   Halsa purchasing relationship; a candidate must be checked against the customer/entity layer before
   it is labeled new. **No external scraping or search in M5 or M6.**

Per-salesperson queues and access for the sales team depend on the **Multi-user / remote deployment**
milestone (D-016).

## 4. Open questions — owner must confirm (do not guess; do not block M5)

These matter for M6/M7. Answers that involve **personal names** (for example the salespeople) are kept
locally and are **not** written into the repository; the repo records roles only.

1. What exactly does `Haftalık Kaşe Adedi` represent?
2. Who maintains it?
3. How frequently is it updated?
4. How reliable is it?
5. What exactly do `Risk` and `Kredi` mean in the Hedef report?
6. Who are the current two salespeople? (roles only in the repo)
7. How will the two new hires be assigned?
8. Are sales visits currently recorded consistently?
9. Are the outcomes of calls/visits recorded anywhere?
10. Do historical January fair invite/attendance lists exist?
11. Can fair-generated orders currently be identified in the ERP?
12. Does the main sales export reliably contain customer + SKU/product detail?

## 5. What M5 must guarantee so M6/M7 plug in without a redesign (D-022)

See `docs/m5_action_center.md` §14. In short: one generic action contract, categories described by the
server rather than hard-coded in the UI, and deterministic action sources registered behind one
interface.
