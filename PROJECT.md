# PROJECT.md — what StockAgent is (stable facts)

## Business context
StockAgent ("Halsa ERP analytics") serves a Turkish wholesale and stationery company.
Every month the ERP exports three Excel files:
- **Sales**: multi-year line items, about 180K rows, 2020 to the present.
- **Stock**: about 3,300 SKUs. The header is on row 4.
- **Receivables (cari)**: monthly aging buckets.

The goal is to turn these exports into concrete daily actions: whom to collect from,
what to reorder, which overstock to sell and to whom, and which lapsed customers to win back.

## Architecture
Two layers. The rule that matters most: **numbers come only from Python.**

```
data/*.xlsx (private, Mac only)
   │
   ▼
dashboard_builder.py ── m_stock / m_collections / load_sales / m_lapsed / m_momentum /
   │                    m_margin / m_ordersize / m_overstock_move / m_risk
   │──► dashboard.html            (Layer 1: deterministic Turkish dashboard, in use; plus the
   │                              server-driven "✨ AI Aksiyon Merkezi" tab from _ai_panel(), M5-2)
   ▼
services/context.py   build_context(): normalized, top-N "agent context"; the action queue comes from
   │                  registered sources (services/actions.py + action_sources.py), ranked by
   │                  services/scoring.py; AI never ranks (D-015, D-022)
   ▼
services/entity.py    ContextRepository: per-customer / per-product slices
   ▼
services/evidence.py  EvidencePacket: small task packet, facts F001.. with Python display values
   ▼
services/agent.py     BusinessActionAgent: validate packet → provider → grounding → resolve
   │                  (services/ai/grounding.py: fact ids, numeric guard, ≤1 retry)
   ▼
services/ai/          AIProvider interface (base.py) · factory.py (only selection point)
                      openai_provider.py (Responses API) · mock_provider.py · anthropic_provider.py
   ▼
app.py                FastAPI (Layer 2 API). Deterministic endpoints never depend on AI.
services/store.py     SQLite: action status, history, memory, ai_calls metadata (no content)
```

## Important files
| Path | Role |
|---|---|
| `dashboard_builder.py` | Deterministic engine and HTML dashboard. Source of every number. |
| `services/context.py` | Agent context (deterministic analytics inputs for the action queue) |
| `services/actions.py` | Generic action contract, action-source registry, server-described categories (D-022) |
| `services/action_sources.py` | Built-in deterministic action sources (collections, overstock, win-back, real decline) |
| `services/scoring.py`, `growth.py`, `scenarios.py` | Transparent scoring, real vs nominal growth, scenario arithmetic |
| `services/evidence.py` | Evidence packets, Turkish display formatting, minimization check |
| `services/ai/grounding.py` | Fact-reference resolution and the numeric guard |
| `services/agent.py` | Grounded AI pipeline per task |
| `services/ai/*` | Provider abstraction and implementations |
| `config.py` | Thresholds, weights, AI settings (`ai_settings()`), default model names |
| `prompts/business_agent.txt` | System prompt (rules only, never data) |
| `schemas.py` | API and strict AI-output Pydantic schemas |
| `tests/fixtures/synthetic_erp.py` | Synthetic ERP exports used by CI |
| `services/refresh.py` | Controlled refresh (validate → build staging → verify → swap), API + CLI |
| `services/importers/` | M6 importer framework: SourceSpec, formula-aware read-only Excel reading, validation report, confidence, exact-match entity resolution, fail-closed staging (`docs/m6_importer_framework.md`, D-035); not yet used by M5 |
| `services/importers/sales_activity.py` | M6-2: `Srmmerk` → one source attribution per order document (`general` / `fuar` / `etkinlik` / `saha`), order and line keys, D-003 customer, exact master match (`docs/m6_order_source.md`, D-036); not yet used by M5 |
| `services/importers/hedef.py` | M6-3: Hedef raporu → account model with field confidence (stale capacity/Risk/Kredi, incomplete visits), exact schema variants, exact master match, optional technical reconciliation/assignment checks (`docs/m6_hedef.md`, D-037); explicit file only; not yet used by M5 |
| `services/importers/kredi.py` | Kredi takip (pulled forward, D-038): owner-only company loan installments, amortizing only, fatal arithmetic identities, control-total row check (`docs/m6_kredi.md`); explicit file only; not used by M5 |
| `services/importers/trodat.py` | M6-5: Trodat sales report → native model × variant × month (`reported_quantity`, unverified) via an explicit matrix spec; workbook totals checked; no Halsa SKU mapping (`docs/m6_trodat.md`, D-039); explicit file only; not used by M5 |
| `services/importers/gider_gelir.py` | M6-6: Gider/Gelir (owner only) → expenses with native-line allocations, revenue by native line, unique ÖZET facts; ÖZET/Sheet1 duplicates are verification-only or excluded; no management mapping (`docs/m6_gider_gelir.md`, D-040); explicit file only; not used by M5 |
| `tests/fixtures/xlsx_synth.py` | Runtime OOXML writer for synthetic workbooks with cached formulas, error cells and external links |
| `tests/test_m5_browser.py` | Playwright smoke tests: real FastAPI + Chromium, synthetic data, disabled/mock AI |
| `tests/js/` | jsdom DOM tests for the AI tab (node, pinned `package-lock.json`; run via `tests/test_m5_ui.py`) |
| `weekly_agent.py` | Older optional weekly email agent (unused) |

## Domain rules (invariants)
- **Customer identity**: order source `Pro_kodu` / `ProjeIsmi`, not the invoice party
  `Musterikod` (about 22% differ). `Musterikod` is used only when `Pro_kodu` is empty.
- **Lapsed tiering**: total ≥ ₺30.000 over the qualification years and no 2026 purchase.
  A strong year is > ₺10.000. T1 = 3+ strong years, T2 = 2, T3 = 1 only if that year is 2024 or 2025.
  The code uses qualification years 2021–2025 (`QUAL_YEARS`); see TASKS.md *Blocked* about 2020.
- **KAPALI** (closed) customers are excluded from opportunity lists (win-back, momentum,
  overstock buyer candidates) and kept in collections and risk.
- **Margin**: `Net_Tutar_Maliyet_Dusulmus` has gaps, so margin outliers are unreliable.
  Margin is never sent to the AI.
- **Receivables**: dynamic monthly aging buckets detected from the column headers.
  The oldest non-zero bucket is the age.
- **Missing ≠ zero** anywhere in the AI layer.

## AI layer (Milestone 3)
- Providers: `disabled` (default), `mock`, `openai` (Responses API, structured output,
  `store=False`), `anthropic` (legacy adapter). Selection happens only in `services/ai/factory.py`.
- The model sees an evidence packet: `schema_version, task, as_of_data_date, entity, facts[],
  classifications[], flags[], missing[], warnings[], items[]`.
- The model writes `[[FACT:F001]]`. Python validates the ids, rejects malformed or unresolved
  placeholders and any model-authored numbers or number words, then substitutes the exact display value.
  On a violation the model gets one stricter retry, then the answer is rejected.
- All provider failures are categorized. They never raise and never affect deterministic analytics.

## Milestone map
| # | Milestone | State |
|---|---|---|
| — | Deterministic dashboard | done, in use |
| 1 | Deterministic foundation (context, scoring, growth, scenarios, SQLite) | done |
| 2 | FastAPI backend and provider-agnostic AI layer, runs without a key | done |
| 3 | Real LLM (OpenAI) behind the abstraction, with numeric safety | done (see STATUS.md) |
| 4 | Live macro data (TCMB/TÜİK inflation, FX) | later |
| 5 | "✨ AI Aksiyon Merkezi" tab: deterministic action queue first, AI on demand (design: `docs/m5_action_center.md`) | **complete** (M5-1 to M5-6: backend, read-only tab, action state, drafts + drawer, controlled refresh, browser verification) |
| 6 | Additional data sources / sales intelligence foundation: validated importers for hedef raporu, detailed collections, Trodat sales, gider/gelir, kredi takip, bütçe (`docs/roadmap_m6_m7.md`) | in progress: M6-0 source inventory done (`docs/m6_source_inventory.md`, D-034); M6-1 importer framework done (`services/importers/`, D-035); M6-2 order-source normalization done (D-036); M6-3 Hedef importer done (D-037); M6-4 blocked (missing export), so the Kredi importer was pulled forward (D-038); M6-5 Trodat importer done (D-039); M6-6 Gider/Gelir done (D-040), Bütçe next |
| 7 | Sales operating system: daily sales queue, customer 360, cross-sell, January fair command center, light CRM, new-buyer discovery | planned (D-021) |
| — | Multi-user / remote deployment (auth, admin and salesperson roles, private deployment, backups) | future, out of scope for M5 (D-016) |

## Product principle (D-015)
The deterministic action queue is the product: Python decides, ranks and explains every action and
every number. AI only interprets selected actions and drafts messages, and the system is fully useful
with AI disabled.

## Data-source architecture (D-021)
Every Excel source, current or future, flows: source-specific validator/importer → normalized
deterministic model → entity resolution → analytics/services → actions → dashboard/API → optional AI
explanation. Dashboard code never depends on a specific workbook layout. Validation is fail-closed.
Sources planned for M6: hedef raporu (potential, territory, visits), the detailed collections report,
the Trodat sales report (SKU intelligence), and the owner-only sources gider/gelir, kredi takip and
bütçe (bütçe gated by cell-level validation because of formula errors).
Every M6 source is a `SourceSpec` run through `services/importers.run_import` (D-035): exact headers at a declared
row or an explicit matrix map, Excel errors and missing formula caches as `invalid`, exact-match entity resolution,
and reports that contain shapes and counts only.

**Data confidence (D-023):** fields carry a Python-assigned status: `trusted`, `incomplete`, `stale`,
`unverified` or `invalid`. The 2020–2026 sales history (including its order activity/source field, used
for fair attribution) is the trusted commercial core. CRM is incomplete. `Haftalık Kaşe Adedi`, `Risk`
and `Kredi` are stale historical fields that never drive decisions as current values. The AI never
upgrades a stale field into a current fact. StockAgent models only legitimate, recorded transactions
(D-026).

## Privacy boundary
GitHub holds code, tests, prompts, schemas, docs, synthetic fixtures, CI and config templates.
The Mac holds the real Excel exports, SQLite state, `.env` and keys, `agent_context.json`, and
generated dashboards and reports. Those never leave the Mac through git.
