"""M7-4 — Fuar Merkezi orchestration: campaign views, manual curation, candidate segments, Customer 360 context.

Combines (read-only) the live Customer 360 snapshot, the private historical FUAR index and reason index of the live
context, and (read/write) the local fair-campaign state in SQLite (services/fair_store.py). No Excel read, no AI,
no logging of names, codes, notes or event names.

Principles (D-045):
- campaign membership is curated explicitly by the user; segments are read-only filters and never enroll anyone;
- funnel stages are user-entered; ERP FUAR-attributed orders are shown as separate evidence and never move a stage;
- a campaign links to ERP data only through EXACT edition year + event stem + sequence (no fuzzy matching);
- trusted master KAPALI blocks fair sales outreach; an unknown master status (duplicate / unmatched / unavailable)
  is never turned into KAPALI (warning only).
"""
from __future__ import annotations

import datetime as dt

from services import fair_store as FS
from services import fairs as FA

SEGMENTS = (("historical_fuar", "Geçmiş fuar müşterisi"), ("active", "Son 365 günde aktif"),
            ("winback", "Geri Kazanım"), ("real_decline", "Reel Satış Düşüşü"), ("cross_sell", "Çapraz Satış"),
            ("collections", "Tahsilat hassasiyeti"))
SEGMENT_KEYS = tuple(k for k, _ in SEGMENTS)
ACTIVE_WINDOW_DAYS = 365
ERP_FOUND = "ERP'de bu fuara atfedilmiş sipariş bulundu."
MANUAL_NOTE = ("Aşamalar elle girilen operasyonel durumdur; satış verisinden çıkarılmaz. ERP siparişi ayrı kanıt "
               "olarak gösterilir ve aşamayı kendiliğinden değiştirmez.")


class FairError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------
def today_iso() -> str:
    return dt.date.today().isoformat()


def linked_event_id(campaign: dict) -> str | None:
    if not campaign.get("event_stem"):
        return None
    return FA.fuar_event_id(campaign["edition_year"], campaign["event_stem"], campaign.get("event_sequence"))


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


_SEG_CACHE: dict = {}


def segment_index(ctx, snap, model=None) -> dict:
    """{segment: {code: score-or-1}} plus per-customer queue score, from memory only. Cached per (ctx, snap)."""
    key = (id(ctx), id(snap))
    hit = _SEG_CACHE.get(key)
    if hit is not None and hit[0] is ctx and hit[1] is snap:
        return hit[2]
    ri = getattr(ctx, "reason_index", None) or {}
    hist = getattr(ctx, "fairs", None)
    model = model if model is not None else getattr(ctx, "opportunities", None)
    window_from = (snap.as_of - dt.timedelta(days=ACTIVE_WINDOW_DAYS - 1)).isoformat()
    custs = snap.customers
    seg = {k: set() for k in SEGMENT_KEYS}
    score: dict[str, float] = {}

    def bump(c, s):
        score[c] = max(score.get(c, 0.0), float(s))
    for c, b in custs.items():
        if b.get("last_order_date") and b["last_order_date"] >= window_from:
            seg["active"].add(c)
    if hist is not None and hist.status == "ok":
        seg["historical_fuar"] = {c for c in hist.customer_editions if c in custs}
    for cat in ("winback", "real_decline", "cross_sell"):
        for c, s in (ri.get(cat) or {}).items():
            if c in custs:
                seg[cat].add(c)
                bump(c, s)
    if model is not None and getattr(model, "status", None) == "ok":
        seg["cross_sell"] |= {c for c in model.targets if c in custs}
    for acc, s in (ri.get("collections") or {}).items():
        owners = snap.account_customers.get(acc) or []
        if len(owners) == 1 and owners[0] in custs:          # exclusive invoice account only (as Günlük Satış)
            seg["collections"].add(owners[0])
            bump(owners[0], s)
    out = {"segments": seg, "score": score}
    _SEG_CACHE.clear()
    _SEG_CACHE[key] = (ctx, snap, out)
    return out


def customer_segments(idx: dict, code: str) -> list[str]:
    return [k for k in SEGMENT_KEYS if code in idx["segments"][k]]


# ---------------------------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------------------------
def require_campaign(campaign_id: int) -> dict:
    c = FS.get_campaign(campaign_id)
    if c is None:
        raise FairError(404, "campaign_not_found", "Kampanya bulunamadı.")
    return c


def linked_evidence(campaign: dict, hist) -> dict:
    eid = linked_event_id(campaign)
    if eid is None:
        return {"configured": False, "found": False, "event_id": None}
    e = hist.editions.get(eid) if (hist is not None and hist.status == "ok") else None
    if e is None:
        return {"configured": True, "found": False, "event_id": eid}
    return {"configured": True, "found": True, "event_id": eid, "label": e.label, "orders": e.orders,
            "customers": e.customers, "net_order_amount": e.net_order_amount, "first_order_date": e.first_order_date,
            "last_order_date": e.last_order_date}


def campaign_summary(campaign: dict, hist, today: str | None = None) -> dict:
    today = today or today_iso()
    rows = FS.members(campaign["campaign_id"])
    by = {k: 0 for k in FS.STAGE_LABELS}
    due = 0
    for r in rows:
        by[r["stage"]] = by.get(r["stage"], 0) + 1
        if (r["stage"] not in FS.TERMINAL_STAGES and r["next_action_date"] and r["next_action_date"] <= today
                and campaign["status"] in FS.OPEN_CAMPAIGN):
            due += 1
    return {**{k: campaign[k] for k in ("campaign_id", "name", "edition_year", "start_date", "end_date",
                                        "event_stem", "event_sequence", "status", "created_at", "updated_at")},
            "total_customers": len(rows), "active_customers": sum(1 for r in rows if r["stage"] in FS.ACTIVE_STAGES),
            "stage_counts": by, "due_tasks": due, "linked_event": linked_evidence(campaign, hist)}


def campaign_customers(campaign: dict, ctx, snap, *, stage: str | None = None, due: bool = False,
                       segment: str | None = None, q: str | None = None, limit: int = 100,
                       today: str | None = None) -> dict:
    from services.customer360 import fold
    today = today or today_iso()
    hist = getattr(ctx, "fairs", None)
    idx = segment_index(ctx, snap)
    eid = linked_event_id(campaign)
    qf = fold(q) if q else None
    out = []
    for r in FS.members(campaign["campaign_id"]):
        code = r["customer_code"]
        if stage and r["stage"] != stage:
            continue
        is_due = bool(r["stage"] not in FS.TERMINAL_STAGES and r["next_action_date"] and r["next_action_date"] <= today)
        if due and not is_due:
            continue
        segs = customer_segments(idx, code)
        if segment and segment not in segs:
            continue
        base = snap.customers.get(code)
        name = base["display_name"] if base else code
        if qf and not (qf in fold(name) or fold(code).startswith(qf)):
            continue
        mi = master_info(snap, code) if base else {"closed": None, "master_status": "unknown",
                                                  "warnings": [{"code": "not_in_customer360",
                                                                "message": "Müşteri güncel satış verisinde yok."}]}
        erp = hist.customer_orders(code, eid) if (eid and hist is not None) else 0
        mismatch = None
        if eid:
            if erp and r["stage"] != "order":
                mismatch = "erp_order_stage_differs"
            elif not erp and r["stage"] == "order":
                mismatch = "stage_order_without_erp"
        out.append({"customer_code": code, "display_name": name, "in_customer360": base is not None,
                    "last_order_date": base["last_order_date"] if base else None, **mi,
                    "stage": r["stage"], "stage_label": FS.STAGE_LABELS.get(r["stage"], r["stage"]),
                    "next_action_date": r["next_action_date"], "due": is_due, "note": r["note"],
                    "has_note": bool(r["note"]), "segments": segs, "collection_first": "collections" in segs,
                    "erp_fuar_orders": erp, "erp_evidence": ERP_FOUND if erp else None, "erp_mismatch": mismatch,
                    "updated_at": r["updated_at"]})
    out.sort(key=lambda x: (not x["due"], x["next_action_date"] or "9999", x["display_name"].casefold(),
                            x["customer_code"]))
    n = max(1, min(int(limit), 500))
    return {"total": len(out), "limit": n, "truncated": len(out) > n, "items": out[:n], "note": MANUAL_NOTE}


# ---------------------------------------------------------------------------------------------
# mutations (validated against the live Customer 360 snapshot)
# ---------------------------------------------------------------------------------------------
def _open_campaign(campaign_id: int) -> dict:
    c = require_campaign(campaign_id)
    if c["status"] not in FS.OPEN_CAMPAIGN:
        raise FairError(409, "campaign_archived", "Arşivlenmiş kampanyada değişiklik yapılamaz.")
    return c


def add_customers(campaign_id: int, codes: list[str], snap, stage: str = "invite_candidate") -> dict:
    _open_campaign(campaign_id)
    res = {"added": [], "already_present": [], "blocked_kapali": [], "invalid": [], "warnings": []}
    seen = set()
    for code in codes:
        if code in seen:
            continue
        seen.add(code)
        if code not in snap.customers:
            res["invalid"].append(code)
            continue
        mi = master_info(snap, code)
        if mi["closed"] is True:
            res["blocked_kapali"].append(code)
            continue
        if FS.add_member(campaign_id, code, stage):
            res["added"].append(code)
            if mi["warnings"]:
                res["warnings"].append({"customer_code": code, **mi["warnings"][0]})
        else:
            res["already_present"].append(code)
    res["counts"] = {k: len(res[k]) for k in ("added", "already_present", "blocked_kapali", "invalid")}
    return res


def update_customer(campaign_id: int, code: str, snap, **fields) -> dict:
    _open_campaign(campaign_id)
    if FS.get_member(campaign_id, code) is None:
        raise FairError(404, "member_not_found", "Müşteri bu kampanyada yok.")
    stage = fields.get("stage", FS._UNSET)
    if stage is not FS._UNSET and stage in FS.ACTIVE_STAGES and code in snap.customers \
            and snap.master_closed(code) is True:
        raise FairError(409, "customer_kapali", "KAPALI müşteri için aktif fuar aşaması seçilemez.")
    if code not in snap.customers and stage is not FS._UNSET and stage in FS.ACTIVE_STAGES:
        raise FairError(409, "customer_unknown", "Müşteri güncel satış verisinde yok.")
    try:
        return FS.update_member(campaign_id, code, **fields)
    except FS.FairStoreError as e:
        raise FairError(422, e.code, e.message)


# ---------------------------------------------------------------------------------------------
# candidate discovery (read-only)
# ---------------------------------------------------------------------------------------------
def candidates(ctx, snap, *, segment: str | None = None, q: str | None = None, campaign_id: int | None = None,
               fuar_first: bool = False, limit: int = 50) -> dict:
    from services.customer360 import fold
    idx = segment_index(ctx, snap)
    seg = idx["segments"]
    members = {r["customer_code"]: r["stage"] for r in FS.members(campaign_id)} if campaign_id else {}
    pool = seg[segment] if segment in SEGMENT_KEYS else set().union(*seg.values())
    qf = fold(q) if q else None
    kapali = 0
    rows = []
    for code in pool:
        b = snap.customers.get(code)
        if b is None:
            continue
        if qf and not (qf in fold(b["display_name"]) or fold(code).startswith(qf)):
            continue
        mi = master_info(snap, code)
        if mi["closed"] is True:
            kapali += 1                      # trusted KAPALI: never a fair outreach candidate
            continue
        segs = customer_segments(idx, code)
        rows.append({"customer_code": code, "display_name": b["display_name"],
                     "last_order_date": b["last_order_date"], **mi, "segments": segs,
                     "queue_score": idx["score"].get(code), "collection_first": "collections" in segs,
                     "historical_fuar": "historical_fuar" in segs, "in_campaign": code in members,
                     "campaign_stage": members.get(code)})
    rows.sort(key=lambda r: ((not r["historical_fuar"]) if fuar_first else False,
                             -(r["queue_score"] if r["queue_score"] is not None else -1.0),
                             _neg_date(r["last_order_date"]), r["customer_code"]))
    n = max(1, min(int(limit), 200))
    counts = {k: sum(1 for c in seg[k] if c in snap.customers) for k in SEGMENT_KEYS}
    return {"segment": segment if segment in SEGMENT_KEYS else "all", "total": len(rows), "limit": n,
            "truncated": len(rows) > n, "kapali_excluded": kapali, "segment_counts": counts,
            "segments": [{"key": k, "label": l} for k, l in SEGMENTS], "items": rows[:n],
            "note": "Segmentler yalnızca filtredir; hiçbir müşteri otomatik olarak davet listesine eklenmez."}


def _neg_date(d: str | None):
    """Sort key: later dates first; missing last."""
    if not d:
        return (1, "")
    return (0, "".join(chr(0x10FFFF - ord(ch)) for ch in d))


# ---------------------------------------------------------------------------------------------
# Customer 360 (read-only)
# ---------------------------------------------------------------------------------------------
def customer360_campaigns(code: str, hist) -> list[dict]:
    out = []
    for m in FS.memberships(code):
        eid = linked_event_id(m)
        erp = hist.customer_orders(code, eid) if (eid and hist is not None) else 0
        out.append({"campaign_id": m["campaign_id"], "name": m["name"], "status": m["status"],
                    "edition_year": m["edition_year"], "start_date": m["start_date"], "stage": m["stage"],
                    "stage_label": FS.STAGE_LABELS.get(m["stage"], m["stage"]),
                    "next_action_date": m["next_action_date"], "has_note": bool(m["note"]),
                    "linked_event": eid is not None, "erp_fuar_orders": erp,
                    "erp_evidence": ERP_FOUND if erp else None})
    return out


def edition_detail(edition, snap, campaign_id: int | None = None, customer_limit: int = 100) -> dict:
    members = {r["customer_code"]: r["stage"] for r in FS.members(campaign_id)} if campaign_id else {}
    n = max(1, min(int(customer_limit), 500))
    rows = []
    for r in edition.customer_rows[:n]:
        b = snap.customers.get(r["customer_code"]) if snap is not None else None
        mi = master_info(snap, r["customer_code"]) if b else {"closed": None, "master_status": "unknown",
                                                               "warnings": []}
        rows.append({**r, "display_name": b["display_name"] if b else r["customer_code"],
                     "in_customer360": b is not None, **mi, "in_campaign": r["customer_code"] in members})
    return {**edition.summary(), "amount_label": FA.AMOUNT_LABEL, "amount_note": FA.AMOUNT_NOTE,
            "notes": FA.SCOPE_NOTES, "customer_sort": "Fuar etiketli sipariş tutarına göre",
            "customer_limit": n, "customers_truncated": len(edition.customer_rows) > n,
            "top_products": edition.top_products, "top_groups": edition.top_groups,
            "top_categories": edition.top_categories, "customer_rows": rows}
