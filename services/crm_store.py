"""M7-5 — lightweight CRM storage on the existing local SQLite (docs/m7_lightweight_crm.md, D-046).

Two complementary concepts:
- **Append-only interaction history** in the existing `memory` table: structured rows get the fixed kind
  `crm_contact` plus nullable columns (`occurred_on`, `channel`, `purpose`, `outcome`, `next_action`, `owner_label`;
  `detail` holds the optional note, `next_followup` the follow-up date chosen in the same entry). Old memory rows are
  never rewritten; they stay readable as legacy history.
- **One current follow-up per canonical customer** in `crm_followups` (operational state, not history). The current
  task is never derived from old `next_followup` values, so a later interaction can never be resurrected over.

All SQL is parameterized. Nothing here logs (no names, codes, notes, outcomes, next actions or owner labels).
"""
from __future__ import annotations

import datetime as dt

from services import store as ST

CRM_KIND = "crm_contact"
CHANNELS = (("phone", "Telefon"), ("whatsapp", "WhatsApp"), ("email", "E-posta"), ("visit", "Ziyaret"),
            ("other", "Diğer"))
PURPOSES = (("sales_followup", "Satış takibi"), ("collection", "Tahsilat"), ("service", "Hizmet / destek"),
            ("other", "Diğer"))
CHANNEL_LABELS = dict(CHANNELS)
PURPOSE_LABELS = dict(PURPOSES)
OUTCOME_MAX, NEXT_ACTION_MAX, NOTE_MAX, OWNER_MAX = 120, 160, 500, 80
_MEMORY_COLUMNS = ("occurred_on", "channel", "purpose", "outcome", "next_action", "owner_label")


def init_tables(c) -> None:
    """Add the CRM columns to `memory` (only those missing) and create `crm_followups` if missing. Non-destructive."""
    have = {r[1] for r in c.execute("PRAGMA table_info(memory)")}
    for col in _MEMORY_COLUMNS:
        if col not in have:
            c.execute(f"ALTER TABLE memory ADD COLUMN {col} TEXT")       # fixed names from the tuple above
    c.execute("""CREATE TABLE IF NOT EXISTS crm_followups(
        customer_code TEXT PRIMARY KEY, purpose TEXT NOT NULL, due_date TEXT NOT NULL, next_action TEXT,
        owner_label TEXT, source_memory_id INTEGER, created_at TEXT, updated_at TEXT)""")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------
# interactions (append-only)
# ---------------------------------------------------------------------------------------------
def add_interaction(code: str, *, occurred_on: str, channel: str, purpose: str | None = None,
                    outcome: str | None = None, next_action: str | None = None, owner_label: str | None = None,
                    note: str | None = None, next_followup: str | None = None) -> int:
    if channel not in CHANNEL_LABELS:
        raise ValueError("bad channel")
    if purpose is not None and purpose not in PURPOSE_LABELS:
        raise ValueError("bad purpose")
    with ST._conn() as c:
        cur = c.execute("""INSERT INTO memory(entity_type,entity_id,ts,kind,detail,next_followup,occurred_on,channel,
                           purpose,outcome,next_action,owner_label) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                        ("customer", code, _now(), CRM_KIND, note or None, next_followup, occurred_on, channel,
                         purpose, outcome or None, next_action or None, owner_label or None))
        return int(cur.lastrowid)


def interactions(code: str, limit: int = 20) -> list[dict]:
    with ST._conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT * FROM memory WHERE entity_type='customer' AND entity_id=? AND kind=?
               ORDER BY occurred_on DESC, id DESC LIMIT ?""", (code, CRM_KIND, int(limit)))]


def interaction_count(code: str) -> int:
    with ST._conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM memory WHERE entity_type='customer' AND entity_id=? AND kind=?",
                             (code, CRM_KIND)).fetchone()[0])


def recent_interactions(limit: int = 50) -> list[dict]:
    """Bounded global feed WITHOUT notes (sensitive text stays in the customer view)."""
    with ST._conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT id, entity_id AS customer_code, occurred_on, channel, purpose, outcome,
                      (next_action IS NOT NULL AND next_action != '') AS has_next_action
               FROM memory WHERE entity_type='customer' AND kind=? ORDER BY occurred_on DESC, id DESC LIMIT ?""",
            (CRM_KIND, int(limit)))]


def legacy_memory(code: str, limit: int = 10) -> tuple[list[dict], int]:
    """Older, non-CRM memory rows of a customer (read-only legacy history)."""
    with ST._conn() as c:
        n = int(c.execute("SELECT COUNT(*) FROM memory WHERE entity_type='customer' AND entity_id=? "
                          "AND (kind IS NULL OR kind != ?)", (code, CRM_KIND)).fetchone()[0])
        rows = [dict(r) for r in c.execute(
            """SELECT id, ts, kind, detail, next_followup FROM memory WHERE entity_type='customer' AND entity_id=?
               AND (kind IS NULL OR kind != ?) ORDER BY ts DESC, id DESC LIMIT ?""", (code, CRM_KIND, int(limit)))]
    return rows, n


def structured_customers() -> set[str]:
    with ST._conn() as c:
        return {r[0] for r in c.execute("SELECT DISTINCT entity_id FROM memory WHERE entity_type='customer' AND kind=?",
                                        (CRM_KIND,))}


def counts() -> dict:
    with ST._conn() as c:
        crm = int(c.execute("SELECT COUNT(*) FROM memory WHERE kind=?", (CRM_KIND,)).fetchone()[0])
        legacy = int(c.execute("SELECT COUNT(*) FROM memory WHERE kind IS NULL OR kind != ?", (CRM_KIND,)).fetchone()[0])
        fu = int(c.execute("SELECT COUNT(*) FROM crm_followups").fetchone()[0])
    return {"structured_interactions": crm, "legacy_memory_rows": legacy, "current_followups": fu}


# ---------------------------------------------------------------------------------------------
# current follow-up (one row per customer)
# ---------------------------------------------------------------------------------------------
def get_followup(code: str) -> dict | None:
    with ST._conn() as c:
        r = c.execute("SELECT * FROM crm_followups WHERE customer_code=?", (code,)).fetchone()
    return dict(r) if r is not None else None


def set_followup(code: str, *, purpose: str, due_date: str, next_action: str | None = None,
                 owner_label: str | None = None, source_memory_id: int | None = None) -> dict:
    """Create or replace THE current follow-up of a customer (a primary key enforces one per customer)."""
    if purpose not in PURPOSE_LABELS:
        raise ValueError("bad purpose")
    now = _now()
    with ST._conn() as c:
        c.execute("""INSERT INTO crm_followups(customer_code,purpose,due_date,next_action,owner_label,source_memory_id,
                     created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)
                     ON CONFLICT(customer_code) DO UPDATE SET purpose=excluded.purpose, due_date=excluded.due_date,
                     next_action=excluded.next_action, owner_label=excluded.owner_label,
                     source_memory_id=excluded.source_memory_id, updated_at=excluded.updated_at""",
                  (code, purpose, due_date, next_action or None, owner_label or None, source_memory_id, now, now))
    return get_followup(code)


def clear_followup(code: str) -> bool:
    with ST._conn() as c:
        return c.execute("DELETE FROM crm_followups WHERE customer_code=?", (code,)).rowcount > 0


def followups() -> list[dict]:
    with ST._conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM crm_followups ORDER BY due_date, customer_code")]


def due_followups(today: str) -> list[dict]:
    """Current follow-ups due today or earlier (oldest first, then code)."""
    with ST._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM crm_followups WHERE due_date <= ? ORDER BY due_date, customer_code", (today,))]


def crm_customer_codes() -> set[str]:
    """Every customer code that has CRM state (structured interactions or a current follow-up)."""
    with ST._conn() as c:
        a = {r[0] for r in c.execute("SELECT DISTINCT entity_id FROM memory WHERE entity_type='customer' AND kind=?",
                                     (CRM_KIND,))}
        b = {r[0] for r in c.execute("SELECT customer_code FROM crm_followups")}
    return a | b
