# StockAgent: Halsa ERP analytics

StockAgent turns the monthly ERP Excel exports of a Turkish wholesale and stationery company
into a deterministic, interactive Turkish dashboard (reorder, overstock, stock clearance,
win-back, collections, growth and shrink, order size, margin, risk). On top of that, an AI
action layer interprets the results, prioritizes them and drafts messages.

> **Core rule:** Python/pandas computes every financial number. The AI may interpret,
> prioritize, explain or draft. It can never invent, recalculate or alter a number, and
> this is enforced in code, not only in the prompt.

The architecture is described in [`PROJECT.md`](PROJECT.md).

## Repository memory (start here if you're an AI agent or a new contributor)
| File | Purpose |
|---|---|
| [`AGENTS.md`](AGENTS.md) | Working rules for coding agents, read at the start of every session |
| [`PROJECT.md`](PROJECT.md) | What the system is: architecture, domain rules, milestones |
| [`STATUS.md`](STATUS.md) | Current checkpoint: what works, test results, sync state |
| [`TASKS.md`](TASKS.md) | Backlog: Current / Next / Later / Blocked / Completed |
| [`DECISIONS.md`](DECISIONS.md) | Architecture decision log (append-only) |

## Install
Python **3.12+** is required.
```bash
git clone https://github.com/cavigidor/halsa-inventory.git StockAgent
cd StockAgent
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env locally; it is gitignored
```

## Private ERP files
Put the monthly exports in `./data/` (gitignored). Files are detected by name:
- a name containing `stok`: stock (header on row 4)
- a name containing `cari` or `yaş`: receivables aging
- a name containing `sat` (and not the above): sales

When there are several files of one kind, the newest is used.

## Run
```bash
python dashboard_builder.py data          # -> dashboard.html (gitignored)
uvicorn app:app --reload                  # API at http://127.0.0.1:8000/docs
```
Main endpoints: `/api/health`, `/api/context`, `/api/agent/actions`,
`/api/agent/customer/{code}`, `/api/agent/product/{code}`, `/api/agent/draft-message`,
`/api/scenario`, `/api/actions/{id}/complete|defer|dismiss`, `/api/ai/calls` (metadata only).

## AI provider configuration (`.env`)
| Variable | Default | Notes |
|---|---|---|
| `AI_PROVIDER` | `disabled` | `disabled`, `openai`, `mock` or `anthropic` |
| `OPENAI_API_KEY` | empty | Optional. The app starts and the analytics work without it |
| `AI_MODEL` | provider default in `config.DEFAULT_MODELS` | |
| `AI_TIMEOUT_SECONDS` | 30 | Bounded to 1–120 |
| `AI_MAX_OUTPUT_TOKENS` | 3000 | Bounded to 256–16000 |
| `AI_MAX_RETRIES` | 1 | SDK retries for transient errors only, 0–3 |
| `AI_REASONING_EFFORT` | empty | Only for reasoning models |

The AI receives only a small evidence packet with fact ids (`F001`, …). When it writes
`[[FACT:F001]]`, Python substitutes the exact value. Any number the model writes itself is
rejected (one stricter retry, then failure). Provider failures never break the deterministic
endpoints.

To check a key with **one** live call on synthetic data:
```bash
AI_PROVIDER=openai python scripts/live_smoke_test.py
```

## Tests
```bash
python -m pytest -q                 # standard suite: synthetic data, no key, no network
python -m pytest -m local_data -q   # on the Mac only: real exports in ./data
```
The standard suite uses `tests/fixtures/synthetic_erp.py`, which contains invented customers,
codes and amounts only. CI (`.github/workflows/tests.yml`) runs it on Python 3.12.

## Never commit
Real Excel/CSV exports, `data/`, `dashboard.html` and other generated reports,
`agent_context.json`, any `*.db`/`*.sqlite`, `.env`, API keys or tokens, and logs.
`.gitignore` enforces this. Review `git diff --cached` before every commit anyway.
