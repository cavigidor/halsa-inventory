"""M7-2 — Günlük Satış: the deterministic, customer-centric daily sales queue (docs/m7_daily_sales_queue.md, D-043).

"Bugün kimi aramalıyım, neden ve önce hangi konuyu konuşmalıyım?"

Inputs, all already in memory (no Excel read, no AI):
- the PRIVATE customer candidates of the live M5 context (`ctx.sales_queue_candidates`: the existing producers'
  actions BEFORE the M5 global top-15 cut, so product actions never take sales slots);
- the Customer 360 snapshot of the SAME live sources (identity, display name, KAPALI, invoice-account map);
- the existing SQLite action state (one source of truth with the AI Aksiyon Merkezi).

Rules (no new business thresholds; no second scoring model):
- reasons keep their M5 action ids, scores and categories; one customer = one card;
- queue score = the maximum score of the customer's visible reasons (secondary reasons add nothing);
- a visible collections reason makes the card `collection_first` and its primary step (sequence, not score);
- collections come from the invoice-account world: attached only when the account maps to exactly one canonical
  customer; shared or unlinked accounts are never assigned (counted, still in M5);
- a customer the unique master marks KAPALI keeps only collections reasons;
- order: queue score ↓, collection-first first on ties, customer code ↑.
"""
from __future__ import annotations

import config as C
from services import actions as A
from services import store as ST

#: Customer action categories that feed the queue, and their kind. `collection` is the barrier kind (sequence
#: precedence, invoice-account mapping); `sales` reasons resolve directly to the canonical customer. A later,
#: separately approved M7 source plugs in by adding its existing action category here.
QUEUE_CATEGORIES: dict[str, str] = {"collections": "collection", "winback": "sales", "real_decline": "sales"}
FILTERS = ("all", "collection_first", *QUEUE_CATEGORIES)
LEGACY_REP_PREFIX = "Temsilci:"   # M5 action fact from the sales file's cached lookup (DQ-5): never shown in M7
DRAFT_PURPOSES = ("collection", "offer", "winback", "sales_followup", "fair_invite")


class QueueUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


def _category_order() -> dict[str, int]:
    return {c.key: i for i, c in enumerate(A.categories())}


def build_queue(ctx, snap, *, statuses: dict | None = None, limit: int | None = None, flt: str = "all",
                upsert: bool = True) -> dict:
    """Assemble the queue from the live context's private candidates and the matching Customer 360 snapshot."""
    candidates = getattr(ctx, "sales_queue_candidates", None)
    if candidates is None:
        raise QueueUnavailable("queue_candidates_unavailable",
                               "Günlük Satış adayları bu bağlamda yok; verileri yenileyin.")
    limit = max(1, min(int(C.MAX_DAILY_SALES_QUEUE if limit is None else limit), C.MAX_SALES_QUEUE_LIMIT))
    flt = flt if flt in FILTERS else "all"
    statuses = ST.get_statuses() if statuses is None else statuses
    order = _category_order()
    cats = {c.key: c for c in A.categories()}
    diag = {"candidates": len(candidates), "candidate_reasons_by_category": {k: 0 for k in QUEUE_CATEGORIES},
            "hidden_by_state": 0, "visible_reasons": 0, "grouped_customers": 0,
            "shared_collection_accounts_not_assigned": 0, "unlinked_collection_accounts": 0,
            "unresolved_sales_customers": 0, "closed_sales_reasons_removed": 0, "other_categories_ignored": 0}

    grouped: dict[str, list[dict]] = {}
    for a in candidates:
        kind = QUEUE_CATEGORIES.get(a.get("category"))
        if kind is None or a.get("entity_type") != "customer":
            diag["other_categories_ignored"] += 1
            continue
        diag["candidate_reasons_by_category"][a["category"]] += 1
        row = statuses.get(a["action_id"], {"status": "open"})
        if ST.is_hidden(row):
            diag["hidden_by_state"] += 1
            continue
        entity = str(a["entity_id"])
        if kind == "collection":
            owners = snap.account_customers.get(entity)
            if not owners:
                diag["unlinked_collection_accounts"] += 1
                continue
            if len(owners) > 1:
                diag["shared_collection_accounts_not_assigned"] += 1      # never divided, never assigned
                continue
            customer, scope = owners[0], "invoice_account"
        else:
            if entity not in snap.customers:
                diag["unresolved_sales_customers"] += 1
                continue
            customer, scope = entity, "customer"
        if kind == "sales" and snap.master_closed(customer) is True:
            diag["closed_sales_reasons_removed"] += 1
            continue
        cat = cats.get(a["category"])
        grouped.setdefault(customer, []).append({
            "action_id": a["action_id"], "source": a.get("source"), "category": a["category"],
            "category_label": cat.label_tr if cat else a["category"], "kind": kind,
            "reason": a.get("reason"), "score": float(a["score"]), "priority": a.get("priority"),
            "confidence": a.get("confidence"), "draft_purpose": cat.draft_purpose if cat else None,
            "status": row.get("status", "open"), "defer_until": row.get("defer_until"),
            "scope": scope, "account_code": entity if kind == "collection" else None,
            "facts": [f for f in a.get("facts", []) if not str(f).startswith(LEGACY_REP_PREFIX)],
            "_action": a})
    diag["visible_reasons"] = sum(len(v) for v in grouped.values())

    def reason_key(r):   # score ↓, category registration order, action id
        return (-r["score"], order.get(r["category"], 99), r["action_id"])

    cards = []
    for code, reasons in grouped.items():
        reasons.sort(key=reason_key)
        collections = [r for r in reasons if r["kind"] == "collection"]
        primary = collections[0] if collections else reasons[0]
        queue_score = max(r["score"] for r in reasons)
        c = snap.customers[code]
        e = snap.master_entry(code)
        closed = snap.master_closed(code)
        mstatus = ("unavailable" if snap.master_status != "ok" else "unmatched" if e is None else
                   "duplicate" if e.duplicate else "matched")
        warnings = []
        if mstatus == "duplicate":
            warnings.append({"code": "master_duplicate",
                             "message": "Ana kayıtta çift kod: KAPALI, temsilci ve sektör belirlenmedi."})
        elif mstatus == "unmatched":
            warnings.append({"code": "master_unmatched", "message": "Müşteri kodu ana kayıtta bulunamadı."})
        if collections:
            warnings.append({"code": "collection_account_scope",
                             "message": "Tahsilat fatura hesabı düzeyindedir; Müşteri 360'ta hesap ayrıntısı var."})
        ordered = [primary] + [r for r in reasons if r is not primary]
        purposes = ["collection"] if closed is True else list(DRAFT_PURPOSES)
        default_purpose = "collection" if (collections or closed is True) else (primary["draft_purpose"]
                                                                               or "sales_followup")
        cards.append({"customer_code": code, "display_name": c["display_name"],
                      "last_order_date": c["last_order_date"], "closed": closed, "master_status": mstatus,
                      "queue_score": queue_score, "priority": C.band(queue_score),
                      "collection_first": bool(collections), "primary_action_id": primary["action_id"],
                      "reasons": ordered, "warnings": warnings, "customer360_available": True,
                      "draft": {"default_purpose": default_purpose, "allowed_purposes": purposes,
                                "collection_target": collections[0]["account_code"] if collections else None,
                                "sales_target": code}})
    cards.sort(key=lambda k: (-k["queue_score"], not k["collection_first"], k["customer_code"]))
    diag["grouped_customers"] = len(cards)

    if flt == "collection_first":
        shown = [k for k in cards if k["collection_first"]]
    elif flt in QUEUE_CATEGORIES:
        shown = [k for k in cards if any(r["category"] == flt for r in k["reasons"])]
    else:
        shown = cards
    filter_counts = {"all": len(cards), "collection_first": sum(1 for k in cards if k["collection_first"]),
                     **{f: sum(1 for k in cards if any(r["category"] == f for r in k["reasons"]))
                        for f in QUEUE_CATEGORIES}}
    page = shown[:limit]
    if upsert:   # same table and ids as M5; new rows only for reasons outside the M5 top-15 (state persists)
        ST.upsert_actions([r["_action"] for k in page for r in k["reasons"]])
    for k in page:
        for r in k["reasons"]:
            r.pop("_action", None)
    return {"as_of_data_date": ctx.get("as_of_data_date"), "generated_at": ctx.get("generated_at"),
            "filter": flt, "limit": limit, "visible_customers": len(shown), "returned": len(page),
            "truncated": len(shown) > limit, "filter_counts": filter_counts,
            "filters": [{"key": "all", "label": "Tümü"}, {"key": "collection_first", "label": "Tahsilat önce"}]
            + [{"key": k, "label": cats[k].label_tr if k in cats else k} for k in QUEUE_CATEGORIES],
            "diagnostics": diag, "items": page}


__all__ = ["DRAFT_PURPOSES", "FILTERS", "QUEUE_CATEGORIES", "QueueUnavailable", "build_queue"]
