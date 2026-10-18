"""M7-5 — lightweight CRM / follow-up service (docs/m7_lightweight_crm.md, D-046).

"Bir müşteriyle en son ne konuştuk, sonuç neydi, sıradaki iş ne ve ne zaman tekrar takip etmeliyiz?"

Local SQLite state (services/crm_store.py) validated against the live Customer 360 snapshot (exact canonical
codes, D-003). Deliberately light:
- CRM coverage is ALWAYS `incomplete` (D-024) until the owner states that logging is standardized; absence of a
  record is unknown activity, never "not contacted";
- no salesperson / owner aggregation, no counts per person, no ranking from CRM activity;
- nothing is inferred: no next date or next action from an outcome, no "uncontacted customer" task;
- only an EXPLICIT current follow-up that is due creates a Günlük Satış task (fixed operational score);
- trusted master KAPALI: history may be recorded, a follow-up only for collection or service;
- no AI: CRM notes, outcomes, next actions, owner labels and history are never sent to a provider.
"""
from __future__ import annotations

import datetime as dt

from services import crm_store as CS

COVERAGE_STATUS = "incomplete"
COVERAGE_LABEL = "Eksik kayıt olabilir"
COVERAGE_NOTE = ("CRM kayıtları henüz tüm müşteri temaslarını kapsamıyor. Bu veriler çalışan performansı "
                 "karşılaştırmak için kullanılmaz.")
UNKNOWN_NOTE = "Kayıt olmaması temas olmadığı anlamına gelmez; yalnızca bilinmediğini gösterir."
KAPALI_ALLOWED = ("collection", "service")
ACTIVE_WINDOW_DAYS = 365
UPCOMING_DEFAULT_DAYS = 7
MAX_LIST = 200


class CrmError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


def today_iso() -> str:
    return dt.date.today().isoformat()


def master_info(snap, code: str) -> dict:
    e = snap.master_entry(code)
    status = ("unavailable" if snap.master_status != "ok" else "unmatched" if e is None else
              "duplicate" if e.duplicate else "matched")
    warnings = []
    if status == "duplicate":
        warnings.append({"code": "master_duplicate", "message": "Ana kayıtta çift kod: KAPALI durumu belirlenemedi."})
    elif status in ("unmatched", "unavailable"):
        warnings.append({"code": "master_" + status, "message": "Ana kayıt eşleşmedi; KAPALI durumu bilinmiyor."})
    return {"closed": snap.master_closed(code), "master_status": status, "warnings": warnings}


def require_customer(snap, code: str) -> dict:
    base = snap.customers.get(code)
    if base is None:
        raise CrmError(404, "customer_not_found", "Müşteri güncel Müşteri 360 verisinde yok.")
    return base


def due_state(due: str | None, today: str, days: int = UPCOMING_DEFAULT_DAYS) -> str | None:
    if not due:
        return None
    if due < today:
        return "overdue"
    if due == today:
        return "today"
    limit = (dt.date.fromisoformat(today) + dt.timedelta(days=days)).isoformat()
    return "upcoming" if due <= limit else "later"


def followup_suppressed(snap, code: str, purpose: str) -> bool:
    """A sales follow-up of a customer that is (now) trusted KAPALI must not become outreach."""
    return snap.master_closed(code) is True and purpose not in KAPALI_ALLOWED


def _followup_out(f: dict, snap, today: str) -> dict:
    return {"purpose": f["purpose"], "purpose_label": CS.PURPOSE_LABELS.get(f["purpose"], f["purpose"]),
            "due_date": f["due_date"], "due_state": due_state(f["due_date"], today), "next_action": f["next_action"],
            "owner_label": f["owner_label"], "updated_at": f["updated_at"],
            "suppressed_kapali": followup_suppressed(snap, f["customer_code"], f["purpose"])
            if f["customer_code"] in snap.customers else False}


def _interaction_out(r: dict) -> dict:
    return {"id": r["id"], "occurred_on": r["occurred_on"], "channel": r["channel"],
            "channel_label": CS.CHANNEL_LABELS.get(r["channel"], r["channel"]), "purpose": r["purpose"],
            "purpose_label": CS.PURPOSE_LABELS.get(r["purpose"]) if r["purpose"] else None, "outcome": r["outcome"],
            "next_action": r["next_action"], "next_followup": r["next_followup"], "owner_label": r["owner_label"],
            "note": r["detail"], "recorded_at": r["ts"]}


# ---------------------------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------------------------
def customer_view(snap, code: str, today: str | None = None) -> dict:
    """Customer 360 "CRM / Takip" section (also GET /api/crm/customers/{code})."""
    today = today or today_iso()
    rows = CS.interactions(code, limit=20)
    f = CS.get_followup(code)
    legacy, nlegacy = CS.legacy_memory(code, limit=10)
    mi = master_info(snap, code) if code in snap.customers else {"closed": None, "master_status": "unknown",
                                                                 "warnings": []}
    warnings = list(mi["warnings"])
    fo = _followup_out(f, snap, today) if f else None
    if fo and fo["suppressed_kapali"]:
        warnings.append({"code": "followup_suppressed_kapali",
                         "message": "Müşteri KAPALI: satış takibi Günlük Satış'a alınmaz (kayıt silinmedi)."})
    return {"customer_code": code, "coverage_status": COVERAGE_STATUS, "coverage_label": COVERAGE_LABEL,
            "coverage_note": COVERAGE_NOTE, "unknown_note": UNKNOWN_NOTE, "closed": mi["closed"],
            "master_status": mi["master_status"], "warnings": warnings,
            "allowed_followup_purposes": list(KAPALI_ALLOWED) if mi["closed"] is True else [k for k, _ in CS.PURPOSES],
            "structured_interactions": CS.interaction_count(code),
            "last_interaction": _interaction_out(rows[0]) if rows else None, "followup": fo,
            "interactions": [_interaction_out(r) for r in rows],
            "legacy": {"count": nlegacy, "label": "Eski not / geçmiş (yapılandırılmamış)",
                       "items": [{"ts": r["ts"], "kind": r["kind"], "detail": r["detail"],
                                  "next_followup": r["next_followup"]} for r in legacy]},
            "ai_used": False}


def followup_list(snap, mode: str = "due", *, days: int = UPCOMING_DEFAULT_DAYS, limit: int = 100,
                  today: str | None = None) -> dict:
    """due = overdue + today; today = today only; upcoming = (today, today+days]; all. Sorted by due date, then code."""
    today = today or today_iso()
    days = max(1, min(int(days), 90))
    n = max(1, min(int(limit), MAX_LIST))
    horizon = (dt.date.fromisoformat(today) + dt.timedelta(days=days)).isoformat()
    rows, orphans = [], 0
    for f in CS.followups():
        code = f["customer_code"]
        if code not in snap.customers:
            orphans += 1                        # kept in SQLite, never shown as another customer
            continue
        d = f["due_date"]
        keep = {"due": d <= today, "today": d == today, "upcoming": today < d <= horizon, "all": True}.get(mode, False)
        if not keep:
            continue
        mi = master_info(snap, code)
        rows.append({"customer_code": code, "display_name": snap.customers[code]["display_name"], **mi,
                     **_followup_out(f, snap, today)})
    rows.sort(key=lambda r: (r["due_date"], r["customer_code"]))
    return {"mode": mode, "today": today, "days": days, "total": len(rows), "limit": n, "truncated": len(rows) > n,
            "orphan_followups": orphans, "items": rows[:n], "coverage_status": COVERAGE_STATUS,
            "coverage_note": COVERAGE_NOTE, "ai_used": False}


def recent(snap, limit: int = 50) -> dict:
    n = max(1, min(int(limit), 50))
    items = []
    for r in CS.recent_interactions(limit=n * 2):
        code = r["customer_code"]
        if code not in snap.customers:
            continue
        items.append({"id": r["id"], "customer_code": code, "display_name": snap.customers[code]["display_name"],
                      "occurred_on": r["occurred_on"], "channel": r["channel"],
                      "channel_label": CS.CHANNEL_LABELS.get(r["channel"], r["channel"]),
                      "purpose_label": CS.PURPOSE_LABELS.get(r["purpose"]) if r["purpose"] else None,
                      "outcome": r["outcome"], "has_next_action": bool(r["has_next_action"])})
        if len(items) >= n:
            break
    return {"limit": n, "items": items, "note": "Notlar burada gösterilmez; müşteri ayrıntısında açılır.",
            "coverage_note": COVERAGE_NOTE, "ai_used": False}


def summary(snap, today: str | None = None) -> dict:
    """Counts only: system coverage and workload, never people."""
    today = today or today_iso()
    custs = snap.customers
    window_from = (snap.as_of - dt.timedelta(days=ACTIVE_WINDOW_DAYS - 1)).isoformat()
    active = {c for c, b in custs.items() if b.get("last_order_date") and b["last_order_date"] >= window_from}
    structured = CS.structured_customers()
    fus = CS.followups()
    horizon = (dt.date.fromisoformat(today) + dt.timedelta(days=UPCOMING_DEFAULT_DAYS)).isoformat()
    known = [f for f in fus if f["customer_code"] in custs]
    orphan_codes = CS.crm_customer_codes() - set(custs)
    c = CS.counts()
    return {"coverage_status": COVERAGE_STATUS, "coverage_label": COVERAGE_LABEL, "coverage_note": COVERAGE_NOTE,
            "unknown_note": UNKNOWN_NOTE, "today": today, "total_customers": len(custs),
            "active_customers": len(active),
            "customers_with_structured_contact": len(structured & set(custs)),
            "active_customers_with_structured_contact": len(structured & active),
            "structured_interactions": c["structured_interactions"], "legacy_memory_rows": c["legacy_memory_rows"],
            "current_followups": len(known),
            "overdue": sum(1 for f in known if f["due_date"] < today),
            "due_today": sum(1 for f in known if f["due_date"] == today),
            "upcoming_7d": sum(1 for f in known if today < f["due_date"] <= horizon),
            "orphan_customer_records": len(orphan_codes), "ai_used": False}


# ---------------------------------------------------------------------------------------------
# writes (explicit only; validated against the snapshot)
# ---------------------------------------------------------------------------------------------
def add_interaction(snap, code: str, body: dict, today: str | None = None) -> dict:
    """Append one structured contact. KAPALI customers may have history recorded (D-009 restricts outreach, not
    facts). An optional follow-up in the same request is an explicit second write, never inferred."""
    today = today or today_iso()
    require_customer(snap, code)
    if body["occurred_on"] > today:
        raise CrmError(422, "contact_date_in_future", "Temas tarihi bugünden sonra olamaz.")
    fu = body.get("followup")
    if fu is not None:
        _check_followup_allowed(snap, code, fu["purpose"])
    mid = CS.add_interaction(code, occurred_on=body["occurred_on"], channel=body["channel"],
                             purpose=body.get("purpose"), outcome=body.get("outcome"),
                             next_action=body.get("next_action"), owner_label=body.get("owner_label"),
                             note=body.get("note"), next_followup=fu["due_date"] if fu else None)
    if fu is not None:
        CS.set_followup(code, purpose=fu["purpose"], due_date=fu["due_date"], next_action=fu.get("next_action"),
                        owner_label=fu.get("owner_label"), source_memory_id=mid)
    return customer_view(snap, code, today)


def _check_followup_allowed(snap, code: str, purpose: str) -> None:
    if snap.master_closed(code) is True and purpose not in KAPALI_ALLOWED:
        raise CrmError(409, "customer_kapali", "KAPALI müşteri için yalnızca tahsilat veya hizmet takibi planlanabilir.")


def set_followup(snap, code: str, body: dict, today: str | None = None) -> dict:
    require_customer(snap, code)
    if body.get("clear"):
        CS.clear_followup(code)
        return customer_view(snap, code, today)
    _check_followup_allowed(snap, code, body["purpose"])
    CS.set_followup(code, purpose=body["purpose"], due_date=body["due_date"], next_action=body.get("next_action"),
                    owner_label=body.get("owner_label"))
    return customer_view(snap, code, today)


# ---------------------------------------------------------------------------------------------
# Günlük Satış task rows (request time, from SQLite)
# ---------------------------------------------------------------------------------------------
def due_task_rows(today: str | None = None) -> list[dict]:
    return CS.due_followups(today or today_iso())
