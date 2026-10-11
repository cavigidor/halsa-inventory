"""Şirket verisi -> deterministik hesaplar (dashboard_builder) -> normalize edilmiş
'agent_context'. AI'ya ham Excel satırları DEĞİL, sadece bu yapılandırılmış gerçekler gider."""
import os, sys, json, datetime as dt
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd
import config as C
import dashboard_builder as db
from services import scoring as SC
from services import growth as GR


def _money(x):
    try:
        return f"\u20ba{float(x):,.0f}"
    except Exception:
        return "\u2014"


def resolve_inflation(explicit=None, macro=None):
    """Öncelik: canlı macro -> cache -> manuel -> yok. Asla sessizce varsayma."""
    if macro and macro.get("cpi", {}).get("available"):
        return macro["cpi"]["value"], "macro:" + macro["cpi"].get("source", "?")
    if explicit is not None:
        return explicit, "manual"
    if C.MANUAL_INFLATION_RATE is not None:
        return C.MANUAL_INFLATION_RATE, "manual_config"
    return None, "unavailable"


def build_context(data_folder, inflation_rate=None, macro=None, top_n=None):
    top_n = top_n or C.MAX_CONTEXT_ITEMS_PER_CATEGORY
    sp = db._latest(data_folder, db.is_stock)
    ep = db._latest(data_folder, db.is_sales)
    ap = db._latest(data_folder, db.is_aging)
    if not (sp and ep):
        raise FileNotFoundError("Stok ve satış dosyaları gerekli.")

    low, over, total = db.m_stock(sp)
    coll, owed = (db.m_collections(ap) if ap else ([], {}))
    sdf = db.load_sales(ep)
    attr = db.cust_attrs(sdf); cinv = db.inv_closed(sdf)
    lap = db.m_lapsed(sdf, owed, attr)
    mom = db.m_momentum(sdf, attr)
    osmove = db.m_overstock_move(sdf, over)
    risk = db.m_risk(sdf, owed, cinv, attr)

    asof = pd.to_datetime(sdf["_date"], errors="coerce").max()
    asof_d = asof.date() if pd.notna(asof) else dt.date.today()
    infl, infl_src = resolve_inflation(inflation_rate, macro)

    # ---- precompute for overstock buyer scoring ----
    custs_2026 = set(sdf.loc[sdf["_yr"] == 2026, "_cust"])
    recent = sdf[sdf["_yr"].isin([2025, 2026])]
    cust_anagrup = recent.groupby("_cust")["anagrup"].apply(lambda s: set(x for x in s if pd.notna(x))).to_dict()
    prod_anagrup = sdf.groupby(sdf["sto_kod"].astype(str))["anagrup"].first().to_dict()

    def score_buyers(product_code, limit=8):
        p = str(product_code)
        sub = sdf[sdf["sto_kod"].astype(str) == p]
        if sub.empty:
            return []
        pana = prod_anagrup.get(p)
        g = sub.groupby("_cust").agg(spend=("Nettutar", "sum"), last=("_yr", "max"),
                                     qty=("miktar", "sum"), name=("_cname", "first"))
        out = []
        for cust, r in g.iterrows():
            # KAPALI customers are never sales-opportunity candidates (domain invariant).
            # They remain in collections/risk. (Milestone 3 fix; see DECISIONS.md D-009.)
            if attr.get(str(cust), {}).get("closed"):
                continue
            has_od = (owed.get(str(cust), 0) or 0) > 0
            related = bool(pana and pana in cust_anagrup.get(cust, set()))
            s, comp = SC.buyer_score(r["spend"], int(r["last"]) if pd.notna(r["last"]) else None,
                                     cust in custs_2026, related, has_od)
            out.append({"code": str(cust), "name": str(r["name"]), "spend": round(float(r["spend"])),
                        "qty": round(float(r["qty"])), "last": int(r["last"]) if pd.notna(r["last"]) else None,
                        "active_2026": cust in custs_2026, "has_overdue": has_od,
                        "related_brand": related, "score": s, "drivers": comp})
        out.sort(key=lambda x: -x["score"])
        return out[:limit]

    # ---- collections scored ----
    coll_scored = []
    for r in coll:
        # revenue for ratio: risk table carries invoice revenue; approx via owed vs — use risk map
        rev = next((rk["rev"] for rk in risk if rk["code"] == r["code"]), 0)
        active = any(rk["code"] == r["code"] and rk["active"] == "Evet" for rk in risk)
        s, comp = SC.collections_priority(r["overdue"], rev, active, r["oldest"], asof_d)
        coll_scored.append({**r, "revenue_25_26": rev, "still_active": active,
                            "priority_score": s, "drivers": comp})
    coll_scored.sort(key=lambda x: -x["priority_score"])

    # ---- shrinking (real growth) ----
    shrink = []
    for r in mom:
        rg = GR.calculate_real_growth(r["y2025"], r["y2026"], infl)
        row = {**{k: r[k] for k in ("code", "name", "rep", "sector", "y2025", "y2026", "delta", "pct")},
               "nominal_pct": rg["nominal_pct"], "real_pct": rg["real_pct"],
               "real_available": rg["available"]}
        shrink.append(row)
    # "reel satış düşüşü": reel negatif (varsa)
    shrink_real_neg = [r for r in shrink if r["real_available"] and r["real_pct"] is not None and r["real_pct"] < 0]

    # ---- overstock candidates with scored buyers ----
    over_cand = []
    for o in osmove[:top_n]:
        buyers = score_buyers(o["code"])
        over_cand.append({**o, "top_buyers": buyers})

    def cut(lst):
        return lst[:top_n]

    macro_block = macro or {"available": False, "reason": "not_fetched",
                            "note": "Milestone 4'te TCMB/TÜİK sağlayıcısı eklenecek."}

    ctx = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of_data_date": asof_d.isoformat(),
        "source_files": {"stock": os.path.basename(sp), "sales": os.path.basename(ep),
                         "aging": os.path.basename(ap) if ap else None},
        "config": {"max_context_items_per_category": top_n,
                   "inflation_rate": infl, "inflation_source": infl_src},
        "company": {
            "products_scanned": total,
            "working_capital": {
                "overstock_value": round(sum(o["inv"] for o in over)),
                "overdue_total": round(sum(r["overdue"] for r in coll)),
                "low_stock_count": len(low),
                "lapsed_count": len(lap),
                "shrinking_real_count": (len(shrink_real_neg) if infl is not None else None),
                "fx_exposure": {"available": False, "reason": "no_supplier_currency_data"},
            },
        },
        "macro": macro_block,
        "collections": cut(coll_scored),
        "overstock": over_cand,
        "winback": cut(lap),
        "shrinking": cut(sorted(shrink, key=lambda x: (x["real_pct"] if x["real_pct"] is not None else x["pct"]))),
        "low_stock": cut(low),
        "risk": cut(risk),
    }
    ctx["actions"] = build_actions(ctx, coll_scored, over_cand, lap, shrink_real_neg, infl)
    return ctx


def build_actions(ctx, coll_scored, over_cand, lap, shrink_real_neg, infl):
    """Deterministik, önceliklendirilmiş aksiyon adayları (AI öncesi).
    M5-1 (D-022): produced by the registered action sources (services/action_sources.py) through the
    generic contract in services/actions.py; ranking is the Python score only."""
    from services import action_sources  # noqa: F401  (registers the built-in sources)
    from services import actions as A
    return A.produce_all({"ctx": ctx, "coll_scored": coll_scored, "over_cand": over_cand, "lap": lap,
                          "shrink_real_neg": shrink_real_neg, "infl": infl})


def write_context(data_folder, out_path=None, **kw):
    ctx = build_context(data_folder, **kw)
    out_path = out_path or os.path.join(data_folder, "agent_context.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)
    return out_path, ctx
