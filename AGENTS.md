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
- KAPALI customers: excluded from opportunities, kept in the Cari balances.
- **Cari aging buckets are invoice age, not overdue debt (D-071).** Never derive overdue amounts, due dates, collection
  priority, customer risk or repayment warnings from them; overdue stays unknown until the agreement-based report is
  supplied and reviewed. 120 accounts are customers and 320 suppliers (D-072); no other prefix is classified. Signed
  balances keep their sign; the USD column is never combined with the TL buckets (nor required by the Cari mapping
  check); the report date is the original file name's date (owner rule for all exports, D-072) — implemented for Cari
  (else the upload date, with provenance); Stok / Satış implementation is a separate checkpoint, and a historical range
  in a file name is never collapsed into one report date.
- Missing values are never zero.
- **Persistent state (D-076):** everything the app must keep lives under `HALSA_DATA_ROOT` when it is set (data,
  uploads, SQLite, published page; `services/paths.py`); never write persistent or staging files elsewhere (code
  directory, `/tmp`), stage atomic writes next to their target and keep one process / one worker (single writer).

- **Klasik Rapor (D-077):** `classic/original_builder.py` is a frozen byte copy of commit 71f5f2f — never edit it, and
  never apply a new Halsa rule to `classic/`. `classic/` imports nothing from the new Halsa and nothing outside
  `classic/` imports it; the golden test (`tests/test_classic_dashboard.py`) must keep passing unchanged.
- **V1 review-first presentation (owner directive 2026-11-06, D-078):** explicitly implemented preliminary source
  review may display calculable factual evidence with concrete Turkish warnings, without certification or action
  propagation. First surface: `services/stock_review.py`, stock-only, generation/fingerprint-bound on every served
  page. D-079 adds explicit preliminary sales calculations/history with dependent-value masking and the private
  `services/father_report.py` offline general report. It excludes Cari/owner-financial source data, preserves existing
  business rules and never feeds actions/AI or opens metric APIs. No general gate switch; D-070 / D-071 stay retired.
- **No verified evidence = no certified business conclusion (D-049, visibility revised by D-078).** Apart from the
  explicit preliminary Stock/Sales review surfaces above, only outputs certified VERIFIED in `services/accuracy.py`
  may be displayed (D-073: only `receivables.collections`, on fresh — 60 days — generation-bound evidence). Never add a flag, env var or parameter that bypasses the gate; never classify a new route as
  anything but a metric unless it carries no business conclusion; every new route must be listed in `ACC.ROUTES`.
- The provider gets only a small evidence packet (`services/evidence.py`). It never gets
  Excel files, DataFrames, SQLite files, whole tables or paths.
- **Access (D-074, revising D-060 / D-061):** nothing is viewable without a session. Anonymous visitors get only the
  Turkish site-password page, the owner-login page and the data-free liveness probe (`public`: `/giris`, and
  `/livez` — D-076: constant body, answered without any session / SQLite / context access; never add data to it). `viewer` routes need any session —
  the shared website password, the financial password or the owner password; never add a public data route, and
  never fall back to public viewing when a hash is missing. Hosting / deployment is still a separate, unauthorized
  checkpoint. **Account-level Cari balances need the financial password (D-075):** never embed Cari rows (or any
  account code paired with an amount) in the published page, which is the same for every role; they are served only by
  the `finance_read` metric route `GET /api/finance/cari`, and Customer 360 nulls the Cari balance fields for a
  session without the financial password. The Yönetim financial projection is
  `finance_read` (financial-viewer password or owner, GET only); every state-changing, work-starting, paid-AI or
  administrative route is `owner_write` and every full-Yönetim / diagnostic read is `owner_read` in
  `services/access.py`. A financial session must never gain an owner permission. Every new route must
  be listed there (as well as in `ACC.ROUTES`); never add a flag, header or parameter that skips the owner session or
  CSRF check, never treat `X-StockAgent` as identity, and never commit a password or its hash. A new viewer-visible
  field must be added to the disclosure inventory in `docs/m8_cp1_access.md` §6.

## Privacy / security
- Never commit: `data/`, `*.xlsx/*.xls/*.csv`, `dashboard.html`, `agent_context.json`,
  `*.db`, `.env`, keys or tokens, logs, generated reports. See `.gitignore`.
- Tests use only the synthetic fixtures in `tests/fixtures/`, which contain invented names and codes.
  Never copy real customer or salesperson names, or real product records, into tests or docs.
  Business-line and source names (for example the Trodat division or "hedef raporu") are fine.
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
