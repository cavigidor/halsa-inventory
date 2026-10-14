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
uvicorn app:app --host 127.0.0.1          # local only; dashboard at http://127.0.0.1:8000/ , API docs at /docs
```

## Monthly refresh
Replace the exports in `./data/` (stock, sales, optional cari/aging), then press **"↻ Verileri yenile"** in the
AI Aksiyon Merkezi tab (or `POST /api/context/refresh`). The refresh validates the files (required columns, rows,
readable sales dates, and a data date that is not older than the current one), builds the new context and
dashboard in staging, verifies them (context ↔ dashboard totals, dashboard structure, data date; large drops
are reported as warnings), then swaps them in atomically and reloads the page. On any failure nothing changes:
the message names the failing step and the previous data stays in service. `dashboard.prev.html` keeps the
previous version. The refresh never calls the AI and keeps action state and the AI cache.

Offline equivalent (no server needed; while the app runs, prefer the button so the server is updated too):
```bash
python -m services.refresh                # exit 0 = refreshed, 1 = failed (nothing changed), 3 = another refresh running
```
The refresh lock uses `flock`, so refresh is supported on macOS and Linux (not Windows).
Main endpoints:
- **Deterministic (never call the AI):** `/` (the generated dashboard), `/api/health` (including
  `context_ready`), `/api/context`, `/api/agent/categories`, `/api/agent/actions` (all open actions ranked
  by Python), `/api/agent/customer/{code}` (facts, Python-formatted `display_facts`, stored notes) and
  `/api/agent/product/{code}`.
- **AI, explicit POST only:** `POST /api/agent/actions/enrich` with `{"action_ids": [...]}` (at most 5 open
  actions, one provider call, cached), `POST /api/agent/customer/{code}/analyze`,
  `POST /api/agent/product/{code}/analyze`, and `POST /api/agent/draft-message` with
  `{"customer_code", "channel": "whatsapp"|"email", "purpose": "collection"|"offer"|"winback"|"sales_followup"|"fair_invite"}`
  (optional `product_code`, `additional_instruction`). The old `message_type` field is still accepted and
  mapped. KAPALI customers get collection drafts only. Drafts are copy-only: nothing is ever sent.
- **Refresh:** `POST /api/context/refresh` (409 while another refresh runs), returns a structured report.
- **Action state:** `POST /api/actions/{id}/complete|defer|dismiss|reopen` (`reopen` is the undo; `defer` takes
  `{"until": "YYYY-MM-DD"}`, a future date, default +7 days; unknown ids give 404).
- **Other:** `POST /api/scenario`, and `/api/ai/calls` (call metadata only).

Every POST must send the header `X-StockAgent: 1`. This is a local guard against cross-site requests;
the AI Aksiyon Merkezi tab sends it automatically, and scripts or curl must add it.

### ✨ AI Aksiyon Merkezi tab
Open **http://127.0.0.1:8000/** (not the file) and click the last tab. It lists the open actions ranked
by Python with the reason and the facts, filterable by the categories the server describes. Opening the
tab never calls the AI. "✨ AI yorumlarını oluştur" (then "Sonraki 5") or the per-card button asks for AI
comments on at most 5 actions per click; AI text is shown separately, marked "öneridir", and cached.
Each card also has "✓ Yapıldı", "⏸ Ertele" (date picker, default +7 days) and "✕ Gizle". The change is saved
in the local SQLite state DB only after the server confirms it, and a "Geri al" bar allows undo for 5 seconds.
Customer cards also have "✉ Taslak" (WhatsApp first; choose channel and purpose, then "Taslak oluştur";
review, "Kopyala" and send it yourself) and "🔍 Müşteri" (a side drawer with the customer's Python facts and
stored notes, loaded once per page session, with no AI).
Opened as a file or with the server stopped, the tab shows how to start the server; the other tabs work
as before.

## AI provider configuration (`.env`)
| Variable | Default | Notes |
|---|---|---|
| `AI_PROVIDER` | `disabled` | `disabled`, `openai`, `mock` or `anthropic` |
| `OPENAI_API_KEY` | empty | Optional. The app starts and the analytics work without it |
| `AI_MODEL` | provider default in `config.DEFAULT_MODELS` | |
| `AI_TIMEOUT_SECONDS` | 30 | Bounded to 1–120 |
| `AI_MAX_OUTPUT_TOKENS` | 4000 | Bounded to 256–16000 |
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
npm ci --prefix tests/js            # optional: enables the AI-tab DOM tests (Node 20+); skipped otherwise
pip install -r requirements-dev.txt && python -m playwright install chromium   # optional: browser smoke tests
```
The standard suite uses `tests/fixtures/synthetic_erp.py`, which contains invented customers,
codes and amounts only. CI (`.github/workflows/tests.yml`) runs it on Python 3.12, with Node 22 and
`REQUIRE_JS_TESTS=1` so the AI-tab DOM tests (`tests/js/`, jsdom) must run there, and with Chromium and
`REQUIRE_BROWSER_TESTS=1` so the Playwright smoke tests (`tests/test_m5_browser.py`: the real app in a real
browser, synthetic data, mock/disabled AI) must run too.

## Never commit
Real Excel/CSV exports, `data/`, `dashboard.html` and other generated reports,
`agent_context.json`, any `*.db`/`*.sqlite`, `.env`, API keys or tokens, and logs.
`.gitignore` enforces this. Review `git diff --cached` before every commit anyway.
