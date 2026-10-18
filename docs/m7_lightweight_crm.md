# M7-5 — Lightweight CRM / Follow-up (Takip / CRM)

Decision: **D-046**. Code: `services/crm_store.py` (SQLite), `services/crm.py` (rules and views),
`services/action_sources.py` (`crm_followup` source), `services/sales_queue.py` (request-time task), `app.py`
(`/api/crm/*`), `dashboard_builder.py` (`_crm_panel`, Customer 360 `crmCard`). Tests: `tests/test_m7_crm.py`,
`tests/test_m7_crm_browser.py`.

The product question:

> "Bir müşteriyle en son ne konuştuk, sonuç neydi, sıradaki iş ne ve ne zaman tekrar takip etmeliyiz?"

M7-5 answers it with two small, explicit records. Nothing is inferred, nothing is scored from people, and nothing is
sent to AI.

## 1. Two concepts

| Concept | Where | Shape | Edits |
|---|---|---|---|
| **Interaction history** | existing `memory` table, fixed kind `crm_contact` | append-only rows: contact date, channel, optional purpose, outcome, next action, owner label, note | never edited or deleted; a correction is a new entry |
| **Current follow-up** | new `crm_followups` table | at most **one row per canonical customer code**: purpose, due date, next action, owner label | replaced or cleared explicitly |

The current follow-up is operational state, not history. It is never derived from older `next_followup` values, so a
newer interaction can never be "resurrected over" by an old date.

## 2. Storage and migration (`services/crm_store.py`)

- `init_tables(c)` runs from `store.init_db()` after the fair tables.
  - It reads `PRAGMA table_info(memory)` and adds only the missing nullable columns: `occurred_on`, `channel`,
    `purpose`, `outcome`, `next_action`, `owner_label`. `detail` holds the note and `next_followup` the follow-up date
    chosen in the same entry.
  - It creates `crm_followups(customer_code PRIMARY KEY, purpose, due_date, next_action, owner_label,
    source_memory_id, created_at, updated_at)` if missing.
- Safe on fresh, M5-era, M7-4 (fair) and old-memory databases; idempotent. Old memory rows, action state, AI cache and
  fair state are untouched.
- Old (non-`crm_contact`) memory rows stay readable as **legacy history** ("Eski notlar"). They are never counted as
  structured CRM coverage.
- All SQL is parameterized. The module never logs.

## 3. Validation (`schemas.py`, `services/crm.py`)

| Field | Rule |
|---|---|
| customer code | exact canonical Customer 360 code (D-003, never trimmed); unknown → 404 `customer_not_found` |
| contact date | valid ISO date; backdating allowed; future → 422 `contact_date_in_future` |
| channel | `phone` / `whatsapp` / `email` / `visit` / `other` |
| purpose | optional; `sales_followup` / `collection` / `service` / `other` |
| outcome / next action / note / owner label | ≤ 120 / ≤ 160 / ≤ 500 / ≤ 80 characters |
| follow-up | `purpose` + `due_date` required, or `{"clear": true}` |

Inputs use `extra="forbid"` (no arbitrary kind). A 422 on `/api/crm/*` never echoes the submitted text (custom
validation handler: location, type and message only).

**Owner label** is free text the user types ("Ofis destek", say). It is manual and non-authoritative, it is **never
aggregated**, never a queue fact and never a filter. No salesperson of record is inferred from it.

## 4. Coverage (D-024)

- `coverage_status` is **always `incomplete`**, labelled **"Eksik kayıt olabilir"**, with the note "CRM kayıtları
  henüz tüm müşteri temaslarını kapsamıyor. Bu veriler çalışan performansı karşılaştırmak için kullanılmaz."
- It does not upgrade with volume. Only an owner decision that logging is standardized could change it.
- Missing records mean **unknown activity**, never "not contacted". There is no "uncontacted customer" task.
- `GET /api/crm/summary` returns **counts only**: total customers, customers with a structured contact, active
  customers with one, structured interactions, legacy rows, current follow-ups, overdue / today / next 7 days, orphan
  records.
- **No person metrics** of any kind: no per-owner counts, no rankings, no conversion rates, no leaderboard.

## 5. KAPALI (trusted master status)

| Situation | Behaviour |
|---|---|
| record an interaction | always allowed |
| follow-up for a KAPALI customer | only `collection` or `service`; `sales_followup` / `other` → 409 `customer_kapali` |
| interaction + a sales follow-up in one request | rejected before anything is written |
| duplicate master rows | no KAPALI invented; a `master_duplicate` warning only |
| customer becomes KAPALI later | its sales follow-up is **suppressed** from Günlük Satış (warning `followup_suppressed_kapali`), state is kept |

## 6. Günlük Satış task (`crm_followup`)

- Built **at request time** from SQLite (`sales_queue.crm_task_actions(today)`), never in the M5 action center and
  never in the build-time reservoir.
- Due means `due_date ≤ today` (local date). Future follow-ups do not appear.
- Category `crm_followup`, label **"Takip Zamanı"**, sales-queue surface only.
- Fixed operational score **`CRM_FOLLOWUP_SCORE = 60`**, confidence `high` (the task exists). It does not depend on
  revenue, age of the date or customer size. The reason says: "Bu, kullanıcının girdiği bir takip tarihidir; satış
  olasılığı veya müşteri değeri anlamına gelmez."
- Action-id facts: purpose, due date, next action. The note and owner label are never facts, so changing the owner
  label keeps the same task id; a new date or next action is a new task.
- Card sequence: **collections → crm_followup → winback → fair_followup → rest by score**. The card's queue score is
  still the maximum reason score.
- Kind: `collection` / `service` follow-ups are `operational` (not a sales push); `sales_followup` / `other` are
  `sales`.
- Draft purpose: `collection` → collection; `sales_followup` → sales follow-up; `service` / `other` → **no draft**
  (the card hides "Mesaj taslağı" when no reason has a fitting purpose). Drafts remain explicit clicks; the request
  carries only code, channel and purpose.
- Completing / deferring / dismissing the queue task uses the shared action state and **never erases the follow-up**.
  Changing the follow-up creates a new task id; clearing it removes the task; old action rows stay in history.

## 7. Separation

- **AI:** `store.get_memory` excludes `crm_contact` rows and returns only the original columns, so evidence packets
  never contain CRM notes, outcomes, next actions or owner labels. No CRM module imports the agent, evidence or a
  provider.
- **Fairs:** fair funnel state (M7-4) and CRM state are separate tables; a fair note or next-action date is never
  copied into CRM, and both tasks may appear on one card.
- **Digital marketing report:** `Dijital Pazarlama Raporu_2025.xlsx` is not imported.
- **Orphans:** CRM rows whose code is no longer in Customer 360 are counted (`orphan_customer_records`,
  `orphan_followups`), never shown, reassigned or deleted.
- **Refresh:** CRM state is local SQLite and survives source refreshes.
- **Excel:** no workbook is read per request.

## 8. API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/crm/summary` | counts only |
| GET | `/api/crm/followups?mode=due\|today\|upcoming\|all&days=7&limit=…` | sorted by due date, then code; ≤ 200 |
| GET | `/api/crm/recent?limit=…` | ≤ 50; **no notes** |
| GET | `/api/crm/customers/{code}` | coverage, warnings, allowed purposes, last interaction, current follow-up, timeline, legacy |
| POST | `/api/crm/customers/{code}/interactions` | append one interaction; optional `followup` is an explicit second write |
| PUT | `/api/crm/customers/{code}/followup` | set / replace, or `{"clear": true}` |

Writes need `X-StockAgent: 1` (D-020). Customer 360 detail gains a read-only `crm` section. While Customer 360 is
building, `/api/crm/*` returns 503 `building`.

## 9. UI

- **New tab "Takip / CRM"** (after "Fuar Merkezi"; marker-wrapped, byte-identical page without it):
  - coverage warning (always visible);
  - summary tiles (counts only);
  - due / overdue and upcoming (7 days) lists;
  - customer search and quick log: interaction form (date defaults to today, max today), optional "plan the next
    follow-up in the same entry", follow-up form (KAPALI customers see only collection / service), timeline, legacy
    notes;
  - recent interactions (no notes).
- Under `file://` the tab shows "Takip / CRM için panoyu http://127.0.0.1:8000/ üzerinden açın." and makes no request.
- **Customer 360 "CRM / Takip" card:** read-only last contact, outcome, next action, next follow-up; buttons
  "Etkileşim kaydet" / "Takip planla" open the CRM tab on that customer.
- Text only (`textContent`, no `innerHTML`); phone layout checked at 390 px with a letter-spacing stress.

## 10. Real-data check (5 Oct 2026 exports; Mac VM; isolated temp DB; aggregates only)

| Item | Result |
|---|---|
| Customer 360 customers / trusted KAPALI / open master / duplicate master | 6,754 / 95 / 6,623 / 6 |
| **Structured CRM records currently available to M7-5** | **0** (no CRM export exists; D-024 — coverage is incomplete) |
| Synthetic smoke (temp DB): 4 interactions, 4 follow-ups (overdue / today ×2 / +4 days) | all writes 200 |
| KAPALI | contact 200 · sales follow-up 409 · collection 200 · service 200 |
| Future contact / missing guard header | 422 / 403 |
| Queue | 3 `crm_followup` cards, score 60, future one absent; kinds sales ×2, operational ×1; drafts sales ×2, none ×1 (service) |
| Existing reasons | scores unchanged; M5 top 15 identical; reservoir unchanged (200 / 7 / 200 / 200) |
| Complete task / clear follow-up | follow-up kept, card hidden / history kept |
| Latency | CRM endpoints 1–9 ms median; queue 6 ms; Customer 360 detail 6 ms |
| AI calls / sources / temp DB | 0 / unchanged / deleted |

## 11. Open items

- Owner: when (if ever) CRM logging is standardized enough to drop "Eksik kayıt olabilir".
- Owner: whether a CRM export exists to import as legacy history (none received).
- Whether `CRM_FOLLOWUP_SCORE` (60) fits; not tuned.
- Owner labels stay free text; any assignment model needs a salesperson-of-record decision first.
