# AGENTS.md — read this first (every session)

Instructions for any AI coding agent (Claude, Codex, …) working on StockAgent.
GitHub (`cavigidor/halsa-inventory`) is the source of truth. Do not rely on chat history.

## Session start
1. Read `AGENTS.md` (this file), then `PROJECT.md`, `STATUS.md`, `TASKS.md`.
2. Read the relevant entries in `DECISIONS.md`.
3. `git status`, `git log --oneline -10`, and confirm the branch.
4. Pick the **single** task under *Current* in `TASKS.md`. Work only on that bounded task.

## Invariants (never break)
- **Python/pandas computes every financial number.** The AI may interpret, prioritize,
  explain or draft. It must never be the source of a number. This is enforced in code:
  the model sees `[[FACT:F001]]` ids, Python resolves them, and `services/ai/grounding.py`
  rejects model-written numbers. Never weaken this.
- Customer identity = order source `Pro_kodu`/`ProjeIsmi` (fallback `Musterikod` only when
  `Pro_kodu` is empty). Never the invoice party.
- Lapsed tiers, KAPALI exclusion, receivable aging and margin handling are deterministic
  Python (`dashboard_builder.py`, `services/`). The AI never decides them.
- KAPALI customers: excluded from opportunities, kept in collections and risk.
- Missing values are never zero.
- The provider gets only a small evidence packet (`services/evidence.py`). It never gets
  Excel files, DataFrames, SQLite files, whole tables or paths.

## Privacy / security
- Never commit: `data/`, `*.xlsx/*.xls/*.csv`, `dashboard.html`, `agent_context.json`,
  `*.db`, `.env`, keys or tokens, logs, generated reports. See `.gitignore`.
- Tests use only the synthetic fixtures in `tests/fixtures/`, which contain invented names and codes.
  Never copy real customer, rep or product names into tests or docs.
- Before every commit, run `git diff --cached --stat` and
  `git diff --cached | grep -nE "sk-|ghp_|github_pat_|sk-ant-"`. Both must be clean.

## Commands
```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Python 3.12+ required
pip install -r requirements.txt
python -m pytest -q                       # standard suite (synthetic, no key, no network)
python -m pytest -m local_data -q         # Mac only: real ERP files in ./data
python dashboard_builder.py data          # writes dashboard.html (gitignored)
uvicorn app:app --reload                  # FastAPI; works with AI_PROVIDER=disabled
python scripts/live_smoke_test.py         # ONE live call, synthetic packet; skips without key
```

## Rules of work
- Do only the current task. Don't refactor unrelated code or change the dashboard UI unless
  the task says so. Don't start Milestone 5 unless `TASKS.md` makes it *Current*.
- Standard tests must never need real data, an API key, or a paid request.
- Add or adjust tests together with the code they cover.
- If you are blocked after reasonable debugging, record the blocker in `STATUS.md` and `TASKS.md`,
  commit the safe partial work, and stop.

## Session end (checkpoint)
1. Run the tests and record the **actual counts** in `STATUS.md`.
2. Update `STATUS.md` and `TASKS.md`. Append to `DECISIONS.md` if a durable decision was made.
   Update `PROJECT.md` only if a stable architectural fact changed.
3. Review `git diff`. Stage only the intended files, by explicit path. Never use `git add .` without reviewing first.
4. Commit, push, and verify the remote (`git ls-remote origin <branch>`).
5. Report to the user and STOP.
