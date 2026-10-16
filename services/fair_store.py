"""M7-4 — local fair-campaign operational state (SQLite, same database as the action state; D-045).

Only user-maintained workflow state is stored here: campaigns, campaign members (canonical customer code, funnel
stage, next-action date, short note) and an audit history. No Excel business fact is copied; identity, KAPALI and
ERP evidence are always read from the live Customer 360 snapshot / FUAR index at request time.

Funnel stages are manual: nothing here is inferred from sales data, and nothing advances a stage automatically.
All SQL is parameterized. Nothing is logged (no names, codes, notes or event names).
"""
from __future__ import annotations

import datetime as dt

from services import store as ST

#: Ordered operational funnel (stable English keys → Turkish labels) and terminal stages.
STAGES = (("invite_candidate", "Davet Edilecek"), ("called", "Arandı"), ("accepted", "Kabul"),
          ("hotel_confirmed", "Otel Teyit"), ("meeting_planned", "Görüşme Planlandı"), ("attended", "Katıldı"),
          ("offer", "Teklif"), ("order", "Sipariş"), ("follow_up", "Takip"))
TERMINAL = (("declined", "Reddetti"), ("removed", "Listeden çıkarıldı"))
STAGE_LABELS = dict(STAGES + TERMINAL)
ACTIVE_STAGES = tuple(k for k, _ in STAGES)
TERMINAL_STAGES = tuple(k for k, _ in TERMINAL)
CAMPAIGN_STATUSES = ("planned", "active", "archived")
OPEN_CAMPAIGN = ("planned", "active")
NOTE_MAX = 500
NAME_MAX = 120
STEM_MAX = 120


class FairStoreError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


def init_tables(c) -> None:
    """Create the fair tables if missing (called from store.init_db; existing tables and rows are untouched)."""
    c.execute("""CREATE TABLE IF NOT EXISTS fair_campaigns(
        campaign_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, edition_year INTEGER NOT NULL,
        start_date TEXT NOT NULL, end_date TEXT, event_stem TEXT, event_sequence INTEGER,
        status TEXT NOT NULL DEFAULT 'planned', created_at TEXT, updated_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS fair_customers(
        campaign_id INTEGER NOT NULL, customer_code TEXT NOT NULL, stage TEXT NOT NULL,
        next_action_date TEXT, note TEXT, created_at TEXT, updated_at TEXT,
        PRIMARY KEY (campaign_id, customer_code))""")
    c.execute("""CREATE TABLE IF NOT EXISTS fair_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL, customer_code TEXT, ts TEXT,
        event TEXT NOT NULL, from_stage TEXT, to_stage TEXT, detail TEXT)""")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _row(r) -> dict | None:
    return dict(r) if r is not None else None


# ---------------------------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------------------------
def create_campaign(*, name: str, edition_year: int, start_date: str, end_date: str | None = None,
                    event_stem: str | None = None, event_sequence: int | None = None, status: str = "planned") -> dict:
    if status not in OPEN_CAMPAIGN:
        raise FairStoreError("bad_status", "Yeni kampanya planlandı veya aktif olabilir.")
    now = _now()
    with ST._conn() as c:
        cur = c.execute("""INSERT INTO fair_campaigns(name,edition_year,start_date,end_date,event_stem,event_sequence,
                           status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (name, int(edition_year), start_date, end_date, event_stem, event_sequence, status, now, now))
        cid = cur.lastrowid
        c.execute("INSERT INTO fair_history(campaign_id,customer_code,ts,event,detail) VALUES(?,?,?,?,?)",
                  (cid, None, now, "campaign_created", status))
    return get_campaign(cid)


def get_campaign(campaign_id: int) -> dict | None:
    with ST._conn() as c:
        return _row(c.execute("SELECT * FROM fair_campaigns WHERE campaign_id=?", (int(campaign_id),)).fetchone())


def list_campaigns() -> list[dict]:
    """Open campaigns first (by start date), then archived ones (newest first); stable by id."""
    with ST._conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM fair_campaigns")]
    rows.sort(key=lambda r: (r["status"] == "archived", r["start_date"] if r["status"] != "archived" else "",
                             -r["campaign_id"] if r["status"] == "archived" else r["campaign_id"]))
    return rows


_CAMPAIGN_FIELDS = ("name", "edition_year", "start_date", "end_date", "event_stem", "event_sequence", "status")


def update_campaign(campaign_id: int, fields: dict) -> dict:
    cur = get_campaign(campaign_id)
    if cur is None:
        raise FairStoreError("campaign_not_found", "Kampanya bulunamadı.")
    if cur["status"] == "archived":
        raise FairStoreError("campaign_archived", "Arşivlenmiş kampanya değiştirilemez.")
    upd = {k: v for k, v in fields.items() if k in _CAMPAIGN_FIELDS}
    if "status" in upd and upd["status"] not in CAMPAIGN_STATUSES:
        raise FairStoreError("bad_status", "Geçersiz kampanya durumu.")
    merged = {**cur, **upd}
    if merged.get("end_date") and merged["end_date"] < merged["start_date"]:
        raise FairStoreError("end_before_start", "Bitiş tarihi başlangıçtan önce olamaz.")
    if not upd:
        return cur
    now = _now()
    sets = ", ".join(f"{k}=?" for k in upd)          # keys come from the fixed allowlist above
    with ST._conn() as c:
        c.execute(f"UPDATE fair_campaigns SET {sets}, updated_at=? WHERE campaign_id=?",
                  (*upd.values(), now, int(campaign_id)))
        c.execute("INSERT INTO fair_history(campaign_id,customer_code,ts,event,detail) VALUES(?,?,?,?,?)",
                  (int(campaign_id), None, now, "campaign_archived" if upd.get("status") == "archived"
                   else "campaign_updated", ",".join(sorted(upd))))
    return get_campaign(campaign_id)


def archive_campaign(campaign_id: int) -> dict:
    return update_campaign(campaign_id, {"status": "archived"})


# ---------------------------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------------------------
def get_member(campaign_id: int, code: str) -> dict | None:
    with ST._conn() as c:
        return _row(c.execute("SELECT * FROM fair_customers WHERE campaign_id=? AND customer_code=?",
                              (int(campaign_id), code)).fetchone())


def members(campaign_id: int) -> list[dict]:
    with ST._conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM fair_customers WHERE campaign_id=? ORDER BY customer_code",
                                           (int(campaign_id),))]


def add_member(campaign_id: int, code: str, stage: str = "invite_candidate") -> bool:
    """True if added; False if already present (idempotent, nothing changes)."""
    if stage not in ACTIVE_STAGES:
        raise FairStoreError("bad_stage", "Eklenen müşteri için aşama geçerli bir aktif aşama olmalı.")
    now = _now()
    with ST._conn() as c:
        if c.execute("SELECT 1 FROM fair_customers WHERE campaign_id=? AND customer_code=?",
                     (int(campaign_id), code)).fetchone():
            return False
        c.execute("""INSERT INTO fair_customers(campaign_id,customer_code,stage,next_action_date,note,created_at,
                     updated_at) VALUES(?,?,?,?,?,?,?)""", (int(campaign_id), code, stage, None, None, now, now))
        c.execute("""INSERT INTO fair_history(campaign_id,customer_code,ts,event,from_stage,to_stage)
                     VALUES(?,?,?,?,?,?)""", (int(campaign_id), code, now, "added", None, stage))
    return True


_UNSET = object()


def update_member(campaign_id: int, code: str, *, stage=_UNSET, next_action_date=_UNSET, note=_UNSET) -> dict:
    cur = get_member(campaign_id, code)
    if cur is None:
        raise FairStoreError("member_not_found", "Müşteri bu kampanyada yok.")
    if stage is not _UNSET and stage not in STAGE_LABELS:
        raise FairStoreError("bad_stage", "Geçersiz aşama.")
    if note is not _UNSET and note is not None and len(note) > NOTE_MAX:
        raise FairStoreError("note_too_long", f"Not en fazla {NOTE_MAX} karakter olabilir.")
    now = _now()
    events = []
    upd = {}
    if stage is not _UNSET and stage != cur["stage"]:
        upd["stage"] = stage
        events.append(("removed" if stage == "removed" else "restored" if cur["stage"] == "removed" else
                       "stage_changed", cur["stage"], stage, None))
    if next_action_date is not _UNSET and next_action_date != cur["next_action_date"]:
        upd["next_action_date"] = next_action_date
        events.append(("next_action_changed", None, None, next_action_date or "cleared"))
    if note is not _UNSET and (note or None) != cur["note"]:
        upd["note"] = note or None
        events.append(("note_updated", None, None, "set" if note else "cleared"))   # never the note text
    if not upd:
        return cur
    sets = ", ".join(f"{k}=?" for k in upd)          # keys from the fixed set above
    with ST._conn() as c:
        c.execute(f"UPDATE fair_customers SET {sets}, updated_at=? WHERE campaign_id=? AND customer_code=?",
                  (*upd.values(), now, int(campaign_id), code))
        for ev, fr, to, detail in events:
            c.execute("""INSERT INTO fair_history(campaign_id,customer_code,ts,event,from_stage,to_stage,detail)
                         VALUES(?,?,?,?,?,?,?)""", (int(campaign_id), code, now, ev, fr, to, detail))
    return get_member(campaign_id, code)


def history(campaign_id: int, code: str | None = None, limit: int = 200) -> list[dict]:
    q = "SELECT * FROM fair_history WHERE campaign_id=?"
    args: list = [int(campaign_id)]
    if code is not None:
        q += " AND customer_code=?"
        args.append(code)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    with ST._conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def memberships(code: str) -> list[dict]:
    """Every campaign membership of one customer (Customer 360, read-only)."""
    with ST._conn() as c:
        return [dict(r) for r in c.execute(
            """SELECT m.*, c.name, c.status, c.edition_year, c.start_date, c.end_date, c.event_stem, c.event_sequence
               FROM fair_customers m JOIN fair_campaigns c ON c.campaign_id=m.campaign_id
               WHERE m.customer_code=? ORDER BY c.status='archived', c.start_date, c.campaign_id""", (code,))]


def due_members(today: str) -> list[dict]:
    """Members with an explicit next-action date on/before `today`, in an open campaign, in a non-terminal stage."""
    with ST._conn() as c:
        return [dict(r) for r in c.execute(
            f"""SELECT m.*, c.name, c.status FROM fair_customers m JOIN fair_campaigns c ON c.campaign_id=m.campaign_id
               WHERE c.status IN ('planned','active') AND m.next_action_date IS NOT NULL AND m.next_action_date <= ?
               AND m.stage NOT IN ({",".join("?" * len(TERMINAL_STAGES))})
               ORDER BY m.campaign_id, m.customer_code""", (today, *TERMINAL_STAGES))]


def diagnostics() -> dict:
    """Counts only."""
    with ST._conn() as c:
        camps = [dict(r) for r in c.execute("SELECT status FROM fair_campaigns")]
        stages = {r[0]: r[1] for r in c.execute("SELECT stage, COUNT(*) FROM fair_customers GROUP BY stage")}
    return {"campaigns": len(camps), "open_campaigns": sum(1 for r in camps if r["status"] in OPEN_CAMPAIGN),
            "campaign_customers": sum(stages.values()), "customers_by_stage": stages}
