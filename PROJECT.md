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
   │──► dashboard.html            (Layer 1: deterministic Turkish dashboard, in use)
   ▼
services/context.py   build_context(): normalized, top-N "agent context" + scored,
   │                  prioritized deterministic actions (services/scoring.py, growth.py)
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
| `services/context.py` | Agent context and deterministic action candidates |
| `services/scoring.py`, `growth.py`, `scenarios.py` | Transparent scoring, real vs nominal growth, scenario arithmetic |
| `services/evidence.py` | Evidence packets, Turkish display formatting, minimization check |
| `services/ai/grounding.py` | Fact-reference resolution and the numeric guard |
| `services/agent.py` | Grounded AI pipeline per task |
| `services/ai/*` | Provider abstraction and implementations |
| `config.py` | Thresholds, weights, AI settings (`ai_settings()`), default model names |
| `prompts/business_agent.txt` | System prompt (rules only, never data) |
| `schemas.py` | API and strict AI-output Pydantic schemas |
| `tests/fixtures/synthetic_erp.py` | Synthetic ERP exports used by CI |
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
| 5 | "✨ AI Aksiyon Merkezi" tab: deterministic action queue first, AI on demand (design: `docs/m5_action_center.md`) | design approved; implementation next |
| — | Multi-user / remote deployment (auth, admin and salesperson roles, private deployment, backups) | future, out of scope for M5 (D-016) |

## Product principle (D-015)
The deterministic action queue is the product: Python decides, ranks and explains every action and
every number. AI only interprets selected actions and drafts messages, and the system is fully useful
with AI disabled.

## Privacy boundary
GitHub holds code, tests, prompts, schemas, docs, synthetic fixtures, CI and config templates.
The Mac holds the real Excel exports, SQLite state, `.env` and keys, `agent_context.json`, and
generated dashboards and reports. Those never leave the Mac through git.
