# STATUS.md — current checkpoint

_Updated 2026-10-09 (M3 close-out: push verified, CI green, live OpenAI smoke test PASSED)._

## Git
- **Branch:** `claude/milestone-3-live-ai`, based on `main`.
- **`main`:** `71f5f2f` (original dashboard) → `d37db1f` (bootstrap: M1+M2 import, hardened `.gitignore`).
- **This checkpoint:** the commit that last touched this file (`git log -1 -- STATUS.md`).
- **GitHub sync:** VERIFIED 2026-10-09. `git ls-remote origin` shows `main` = `d37db1f` and
  `claude/milestone-3-live-ai` = `dfa57de`, matching local (the user pushed from the Mac). Remote
  default branch (HEAD) = `main`.

## Milestone
Milestone 3 (real LLM behind the abstraction, with numeric safety) is implemented and tested.
Push and CI are done. Remaining close-out: one live smoke call (must run in the Mac's own Terminal,
see below), then merge to `main`.

## What works
- Deterministic dashboard (`python dashboard_builder.py data`). This file was unchanged by M3.
- Agent context, scoring, growth, scenarios and SQLite action state (M1).
- FastAPI backend. It starts with no key, and every deterministic endpoint works with AI disabled (M2).
- **M3:**
  - `services/ai/openai_provider.py`: Responses API, Pydantic structured output, finite timeout,
    bounded transient retries, `store=False`, every error categorized and none raised.
  - `services/ai/factory.py`: the only provider-selection point (`disabled|mock|openai|anthropic`).
  - `config.ai_settings()`: `AI_PROVIDER`, `AI_MODEL`, `AI_TIMEOUT_SECONDS`,
    `AI_MAX_OUTPUT_TOKENS`, `AI_MAX_RETRIES`, `AI_REASONING_EFFORT`. `.env` is auto-loaded.
  - `services/evidence.py`: evidence packets, Turkish display formatting, minimization validation.
  - `services/ai/grounding.py`: fact-id validation, placeholder resolution, numeric guard.
  - `services/agent.py`: grounded pipeline with one bounded stricter retry. KAPALI rules are enforced
    in Python. Provider exceptions are contained.
  - `services/store.py`: an `ai_calls` metadata table (no prompts or content), exposed at `GET /api/ai/calls`.
  - API responses now carry `evidence[]` (fact id, label, Python display value), `ai_error_category`
    and `ai_warnings`.
  - `services/context.py`: KAPALI customers removed from overstock buyer candidates (D-009).

## Tests (actual results, 2026-10-09)
| Where | Command | Result |
|---|---|---|
| Baseline before M3, Mac, real data | `python -m pytest tests/ -q` | **32 passed** |
| Baseline before M3, no data (CI-like) | same | 27 passed, 1 failed, 4 errors (needed real Excel) |
| After M3, cloud, no data | `python -m pytest -q` | **106 passed, 7 skipped (local_data), 1 xfailed** |
| After .env-loader fix, cloud, no data | `python -m pytest -q` | **107 passed, 7 skipped, 1 xfailed** |
| After quota category, cloud, no data | `python -m pytest -q` | **108 passed, 7 skipped, 1 xfailed** |
| After create()+diagnostics, cloud, no data | `python -m pytest -q` | **111 passed, 7 skipped, 1 xfailed** |
| After guard false-positive fixes, cloud, no data | `python -m pytest -q` | **121 passed, 7 skipped, 1 xfailed** |
| After M3, Mac VM, real 5 Oct 2026 data | `python -m pytest -q` | **113 passed, 1 xfailed** |

The xfail is the strict known issue DQ-1 (dashboard margin treats missing cost as zero).
CI: GitHub Actions run `37972839805` on `claude/milestone-3-live-ai` @ `dfa57de` — **success**
(every step green, including `python -m pytest -q`). The per-test counts could not be read from the
cloud session (the log download was forbidden); expected 106 passed / 7 skipped / 1 xfailed.
`main` @ `d37db1f` predates the workflow file, so it has no CI run yet.

## Live provider test
**PASSED 2026-10-09** (owner's Mac Terminal, `python scripts/live_smoke_test.py`, synthetic packet):
`provider=openai model=gpt-5-mini reasoning_effort=low max_output_tokens=3000`
- attempt 1: rejected by the numeric guard (`numeric_guard`, 12.1 s, 1920 in / 1227 out / 256 reasoning tokens)
- attempt 2 (the one bounded stricter retry): **success** (9.0 s, 2048 in / 917 out / 256 reasoning tokens)
- `RESULT: OK — schema valid, 12 fact(s) referenced, all values from Python: True`

Every number in the final summary (₺184.230, ₺0, yearly revenue and order counts) was inserted by
Python from `[[FACT:..]]` references. The model wrote no numbers itself.

Earlier attempts the same day, all handled as designed (categorized failure, nothing fabricated):
1. HTTP 429 `insufficient_quota`, because the API account had no credit. Now its own `quota` category.
2. `malformed` after 20 s. A truncated answer was hidden by `responses.parse()`; fixed in D-012.
3. The guard rejected both attempts on non-financial tokens; fixed in D-013.

Observation: the passing summary mostly lists facts ("₺61.000, 4, ₺70.230, 5 …") rather than
interpreting them. That is a prompt-quality item, not a safety issue (TASKS *Next*).
The cloud and Mac sandboxes still cannot reach `api.openai.com`, so live runs happen in the Mac Terminal.

## Provider configuration
Default `AI_PROVIDER=disabled`. Default `reasoning.effort=low` applies only to the default
OpenAI model. The OpenAI default model `gpt-5-mini` lives in
`config.DEFAULT_MODELS`. It has not been verified live; override it with `AI_MODEL`.

## Not implemented / known issues
- Milestone 4 (live macro data) and Milestone 5 (dashboard AI tab) are not started.
- DQ-1: dashboard margin uses `fillna(0)` for missing cost (owner decision needed).
- DQ-2: the dashboard "Stok Eritme" list still includes KAPALI customers (owner decision needed).
- The lapsed qualification window is 2021–2025 in code but 2020–2025 in the spec (TASKS *Blocked*).
- `arama_listesi.py` is missing. It wasn't in the M1/M2 zips or on the Mac.
- The numeric guard catches digits and common magnitude or percent words. It doesn't catch every
  spelled-out small number (for example "iki müşteri").

## Local-only dependencies
- Python 3.12+ (`dashboard_builder.py` doesn't parse on 3.10).
- Real exports in `./data` (Mac): `2020-5 ekim satışlar.xlsx`, `STOK 5 Ekim 2026.xlsx`, `Cari 5 Ekim 2026.xlsx`.
- A pre-M3 backup of the Mac folder: `~/Desktop/ME/Projects/StockAgent_backup_2026-10-08_pre-M3`.

## Immediate next task
TASKS M3-C3: review and merge the pull request `claude/milestone-3-live-ai` → `main`.
