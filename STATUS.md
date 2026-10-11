# STATUS.md — current checkpoint

_Updated 2026-10-10 (M3 and Q-1 on `main` @ `a34ccac`; M5 design approved and M6/M7 roadmap documented in PR #3, docs only, awaiting merge)._

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

## Q-1 (done; merged in PR #2)
Branch `claude/q1-summary-quality` from `main` @ `60dffdb` (PR #1 merged; Milestone 3 complete).
Writing-quality prompt rules, schema field guidance and `services/ai/quality.py` measurement are
implemented.
Live run 1 on this branch: `RESULT: OK`, 1 attempt (16.4 s, 2479 in / 1354 out / 704 reasoning), and the
quality metric said OK. The summary now interprets, but on reading it still had defects the metric
missed: fact references tacked onto the end of items ("…; T1"), a name written and referenced twice,
"yok ₺0", and a recommendation listing "missing" fields that do not apply to a lapsed customer.
Fixes: the packet omits non-applicable collections fields (only genuinely empty ones are `missing`);
the quality check now detects tacked-on references, duplicated names and listed missing fields; the
prompt and schema guidance were tightened.
Live run 2: `RESULT: OK` (1 attempt, 7.8 s). The quality check now caught every defect (6 issues), but
the model repeated them despite explicit rules: it uses `[[FACT:..]]` like a citation footnote.
Fix (D-014): a deterministic tidy step after the safety check. Risks, opportunities and the
recommended action carry no inline facts (refs move to `evidence_fact_ids`). In the summary,
citation-style refs (parenthetical groups, after ';' or ',' at a clause end, after a finished verb,
duplicated names) and technical labels are removed. Tidy only removes, never adds a number. Quality
is measured both on the model output and on the tidied output. Standard suite: 134 passed, 7 skipped,
1 xfailed.
Live run 3 (two runs, both `RESULT: OK`; one used the guard retry): tidy fixed several defects, but
1) a year glued after another ref survived a single tidy pass; 2) a mid-sentence run of three amounts
was not handled; 3) the model kept turning informational notes into to-dos (an empty "Sektör" listed
as missing, and the margin warning).
Fixes: tidy runs until stable (max 3 passes) and handles ref sequences; a clause made of 3+ refs is
removed (ids kept as evidence) and the next sentence re-capitalized; `missing` now lists only numeric
facts (empty descriptive fields are omitted); the margin warning says margin is out of scope.
Standard suite: 137 passed, 7 skipped, 1 xfailed.
**Live run 4: ACCEPTED.** `RESULT: OK` on the first attempt (8.3 s, 2749 in / 1287 out / 832 reasoning tokens),
12 facts referenced, every value from Python. The model output had 2 quality issues; tidy applied 1 fix
and the final quality check was OK. Read by a human: the summary interprets rather than lists, the
opportunities are clean, and the action is concrete (who, what, when).
Residual model-judgment notes, not formatting: one risk still mentions missing margin data despite the
out-of-scope warning, a fact label is capitalized mid-sentence, and one risk is vague. Logged as an
optional follow-up (TASKS Later: Q-2); not a reason to keep iterating.

## M5-P1 (design approved)
`docs/m5_action_center.md` (branch `claude/m5-p1-design-note`, PR #3, docs only) has been updated with the
owner's decisions:
- The deterministic queue is the product, and AI only explains (D-015).
- M5 is local-only; multi-user / remote deployment is a future milestone, and M5 must not block it (D-016).
- AI runs only on request: top 5 by default, then "next 5" or per-action, with a cache (D-017).
- Drafts are WhatsApp first, channel × purpose, copy-only, natural B2B tone (D-018).
- The monthly refresh is controlled: validate → build → verify → swap (D-019).
- The dashboard is served same-origin, with a POST guard and escaped AI text (D-020).

There are six bounded implementation slices (M5-1 to M5-6). No code has changed in this checkpoint.

## Roadmap (documented 2026-10-10, not started)
Six additional monthly owner reports (hedef raporu, kredi takip, the detailed collections report,
bütçe, the Trodat sales report, gider/gelir) are planned for **M6** (validated importers → normalized
model → entity resolution) and **M7** (sales operating system). See `docs/roadmap_m6_m7.md` and
D-021 to D-026. None is ingested yet, and the real files stay local and gitignored.

The owner's answers were incorporated on 2026-10-10:
- Data-confidence statuses (D-023): `Haftalık Kaşe Adedi`, `Risk` and `Kredi` are stale historical
  fields; CRM coverage is incomplete; the 2020–2026 sales report and its activity/source (fair) field are
  trusted after verification.
- The new hires are office sales support, not field salespeople (D-024).
- Fair attribution starts from existing data (D-025).

Questions are resolved or reduced to M6-0 technical verification. M5-1 includes the extensibility seams
so M6/M7 actions plug in without redesign.

## Immediate next task
Owner merges PR #3. Then M5-1 (backend prerequisites, no UI).
