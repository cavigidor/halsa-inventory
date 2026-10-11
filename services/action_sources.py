"""Built-in deterministic action sources (M5-1). They reproduce the M1 `build_actions` logic exactly
(same scores, facts and order) and add the `source` and a deterministic Turkish `reason`.

Inputs (dict) come from `services/context.build_context`: ctx, coll_scored, over_cand, lap,
shrink_real_neg, infl. New sources (M6/M7) register in their own modules the same way.
"""
import config as C
import dashboard_builder as db
from services import actions as A
from services import scoring as SC

_CONF_NUM = {"high": 1, "medium": 0.6, "low": 0.3}
COLLECTIONS_THRESHOLD = 50          # collections priority score at/above which a TAHSİLAT action exists


def _money(x):
    try:
        return f"₺{float(x):,.0f}"
    except Exception:
        return "—"


# ---------------- categories (server-described, D-022) ----------------
CAT_COLLECTIONS = A.Category("collections", "Tahsilat", "#b45309", "customer", "collections_aging")
CAT_OVERSTOCK = A.Category("overstock", "Stok Eritme", "#0e7490", "product", "overstock_buyers")
CAT_WINBACK = A.Category("winback", "Geri Kazanım", "#6d28d9", "customer", "winback_lapsed")
CAT_REAL_DECLINE = A.Category("real_decline", "Reel Satış Düşüşü", "#9f1239", "customer", "real_growth")

A.DEFAULT_REASONS.update({
    "collections": "Vadesi geçen alacak tahsilat öncelik eşiğini aştı.",
    "overstock": "Stok maksimum seviyenin üzerinde; bağlı sermaye eritilmeli.",
    "winback": f"Geçmişte güçlü alım yapan müşteri {db.LAPSED_YEAR} yılında hiç alım yapmadı.",
    "real_decline": "Bu yılki ciro, enflasyondan arındırıldığında geçen yılın aynı döneminin altında.",
})


# ---------------- producers ----------------
def produce_collections(inp):
    out = []
    for r in inp["coll_scored"]:
        if r["priority_score"] < COLLECTIONS_THRESHOLD:
            continue
        fi = min(1.0, (r["overdue"] or 0) / C.COLLECT_AMOUNT_CAP)
        urg = min(1.0, (r["drivers"].get("age_months") or 0) / 12)
        rsk = r["drivers"]["ratio"]
        conf = SC.confidence_label(has_amount=r["overdue"] > 0, has_history=r["revenue_25_26"] > 0,
                                   has_context=r["drivers"].get("age_months") is not None)
        score = SC.action_priority(fi, urg, rsk, _CONF_NUM[conf])
        facts = [f"Vadesi geçen: {_money(r['overdue'])}",
                 f"En eski borç: {r['oldest']}",
                 f"2026 sipariş: {'Aktif' if r['still_active'] else 'Yok'}",
                 f"2025–2026 ciro: {_money(r['revenue_25_26'])}"]
        reason = A.DEFAULT_REASONS["collections"]
        if r.get("still_active"):
            reason += " Müşteri bu yıl sipariş vermeye devam ediyor (alıp ödemiyor)."
        out.append(A.make_action(source=CAT_COLLECTIONS.source, category="collections",
                                 entity_type="customer", entity_id=r["code"],
                                 title=f"TAHSİLAT — {r['name']}", reason=reason, facts=facts,
                                 drivers=r["drivers"], score=score, confidence=conf))
    return out


def produce_overstock(inp):
    out = []
    for o in inp["over_cand"]:
        fi = min(1.0, (o["inv"] or 0) / C.OVERSTOCK_VALUE_CAP)
        conf = SC.confidence_label(has_amount=o["inv"] > 0, has_history=o["buyers"] > 0,
                                   has_context=len(o.get("top_buyers", [])) > 0)
        score = SC.action_priority(fi, 0.4, 0.3, _CONF_NUM[conf])
        best = o["top_buyers"][0]["name"] if o.get("top_buyers") else "—"
        facts = [f"Fazla stok değeri: {_money(o['inv'])}",
                 f"Stok: {o['stock']}", f"Geçmiş alıcı sayısı: {o['buyers']}",
                 f"En güçlü aday: {best}"]
        reason = A.DEFAULT_REASONS["overstock"]
        reason += (" Bu ürünü daha önce alan müşteriler var; aday alıcı listesi hazır."
                   if o.get("top_buyers") else " Uygun geçmiş alıcı bulunamadı.")
        out.append(A.make_action(source=CAT_OVERSTOCK.source, category="overstock",
                                 entity_type="product", entity_id=o["code"],
                                 title=f"STOK ERİTME — {o['name']}", reason=reason, facts=facts,
                                 drivers={"inv": o["inv"], "buyers": o["buyers"]}, score=score,
                                 confidence=conf))
    return out


def produce_winback(inp):
    out = []
    for r in inp["lap"]:
        fi = min(1.0, (r["spend"] or 0) / C.WINBACK_SPEND_CAP)
        barrier = (r.get("overdue") or 0) > 0
        conf = "high" if not barrier else "medium"
        score = SC.action_priority(fi, 0.3, 0.5, _CONF_NUM[conf])
        facts = [f"Toplam geçmiş harcama: {_money(r['spend'])}",
                 f"Kademe: {r['tier']}", f"Son aktif yıl: {r['last']}",
                 f"Vadesi geçen borç: {_money(r['overdue']) if barrier else 'Yok'}",
                 f"Temsilci: {r.get('rep') or '—'}"]
        reason = (f"Geçmişte güçlü alım yapan müşteri (Kademe {r['tier']}) "
                  f"{db.LAPSED_YEAR} yılında hiç alım yapmadı.")
        if barrier:
            reason += " Vadesi geçen borcu var; önce tahsilat konuşulmalı."
        out.append(A.make_action(source=CAT_WINBACK.source, category="winback",
                                 entity_type="customer", entity_id=r["code"],
                                 title=f"GERİ KAZANIM — {r['name']}", reason=reason, facts=facts,
                                 drivers={"tier": r["tier"], "spend": r["spend"]}, score=score,
                                 confidence=conf))
    return out


def produce_real_decline(inp):
    out = []
    for r in inp["shrink_real_neg"]:
        fi = min(1.0, abs(r["delta"] or 0) / C.SHRINK_DELTA_CAP)
        urg = min(1.0, abs(r["real_pct"]) / 50)
        score = SC.action_priority(fi, urg, 0.5, 1.0)
        facts = [f"Nominal değişim: {r['nominal_pct']:+.0f}%",
                 f"Reel değişim: {r['real_pct']:+.0f}%",
                 f"2025 (Oca–bugün): {_money(r['y2025'])}",
                 f"2026 (Oca–bugün): {_money(r['y2026'])}",
                 f"Temsilci: {r.get('rep') or '—'}"]
        out.append(A.make_action(source=CAT_REAL_DECLINE.source, category="real_decline",
                                 entity_type="customer", entity_id=r["code"],
                                 title=f"REEL SATIŞ DÜŞÜŞÜ — {r['name']}",
                                 reason=A.DEFAULT_REASONS["real_decline"], facts=facts,
                                 drivers={"nominal_pct": r["nominal_pct"], "real_pct": r["real_pct"]},
                                 score=score, confidence="high"))
    return out


# Registration order = M1 order (tie-break for equal scores). Idempotent on re-import.
_BUILTINS = (
    A.ActionSource(CAT_COLLECTIONS.source, (CAT_COLLECTIONS,), produce_collections),
    A.ActionSource(CAT_OVERSTOCK.source, (CAT_OVERSTOCK,), produce_overstock),
    A.ActionSource(CAT_WINBACK.source, (CAT_WINBACK,), produce_winback),
    A.ActionSource(CAT_REAL_DECLINE.source, (CAT_REAL_DECLINE,), produce_real_decline),
)
for _s in _BUILTINS:
    if all(x.name != _s.name for x in A.sources()):
        A.register(_s)
