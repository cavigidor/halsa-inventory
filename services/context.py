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
from services import actions as A


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


class AgentContext(dict):
    """The agent context (a plain dict for every existing reader) plus PRIVATE runtime attributes for M7-2.

    The attributes are not dict items, so they never reach `/api/context`, `agent_context.json`, evidence packets or
    logs (json / pydantic serialize items only). They travel with this exact object, so a controlled refresh that
    installs a new context installs its matching candidates at the same moment.
    - `sales_queue_candidates`: tuple of customer actions of the sales-queue categories from the UNCUT candidate
      list (Python score order); M7-3 (D-044): at most `MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY` per category, so
      one category can never starve another.
    - `sales_queue_rows`: the already-computed source rows of those candidates (collections / winback / shrinking,
      plus a code+name row for cross-sell-only customers), so the existing draft and drawer endpoints can resolve
      them; used only for codes the public index lacks.
    - `opportunities`: the M7-3 cross-sell model (services/opportunities.CrossSellModel), built from the sales
      frame already read here.
    """

    sales_queue_candidates = None
    sales_queue_rows = None
    opportunities = None
    fairs = None              # M7-4: historical FUAR index (services/fairs.FairHistory), from the frame read here
    reason_index = None       # M7-4: {category: {entity_id: max score}} over ALL uncut customer candidates
    # M7-6: the Customer 360 projection of the sales frame read here (sales_projection.SalesFrameHandoff), used ONCE
    # by the first Customer 360 build of this exact context so the sales workbook is not read a second time.
    sales_c360_handoff = None
    # M7-6: structural data-quality counts for the Yönetim known-issues panel (counts only, no rows)
    data_quality = None
    # M8-ACCURACY-2: the source generation this context was computed from: {"core_id": …, "served": {source: sha256}}.
    # None for a context not built from a frozen, recorded selection (it then receives no verification evidence).
    generation = None
    generation_manifest = None


def build_context(data_folder, inflation_rate=None, macro=None, top_n=None, sources=None, frames=None):
    """sources/frames (M5-5): a frozen source selection and its already-read raw frames (controlled refresh);
    by default the newest files in data_folder are selected and read, as before."""
    top_n = top_n or C.MAX_CONTEXT_ITEMS_PER_CATEGORY
    src = sources or db.select_sources(data_folder)
    fr = frames or {}
    sp, ep, ap = src.get("stock"), src.get("sales"), src.get("aging")
    if not (sp and ep):
        raise FileNotFoundError("Stok ve satış dosyaları gerekli.")

    # M7-6: read each core frame once here (as before) and keep a pristine reference long enough to derive the
    # DQ-4 count and the compact Customer 360 projection; the M5 computations below are unchanged.
    raw_stock = fr.get("stock")
    if raw_stock is None:
        raw_stock = db.read_stock(sp)
    low, over, total = db.m_stock(sp, raw_stock)
    # DQ-4 count among rows that carry a product code (M8-ACCURACY-2B: the export's report-total rows have no
    # coverage and are not products; previously they were counted)
    _codes = raw_stock[db.SC["code"]].astype(str).str.strip() if db.SC["code"] in raw_stock.columns else None
    _has = (raw_stock[db.SC["code"]].notna() & (_codes != "") & (_codes.str.lower() != "nan")) if _codes is not None else None
    dq = {"stock_coverage_unavailable_products": int((db.num(raw_stock[db.SC["cov"]]).isna() & _has).sum())
          if (db.SC["cov"] in raw_stock.columns and _has is not None) else None}
    del raw_stock
    # D-071: Cari invoice-age balances are not overdue debt. The context (actions, AI evidence, sales queue) carries no
    # collection candidates, priority scores, overdue totals or debt flags; the factual balances stay in the dashboard
    # module and Customer 360 (invoice-account grain). Nothing here reads the Cari file.
    coll, owed = [], {}
    raw_sales = fr.get("sales")
    own_read = raw_sales is None
    if own_read:
        raw_sales = db.read_sales_raw(ep)
    from services import sales_projection as SP
    handoff = SP.prepare_handoff(ep, raw_sales)          # before load_sales modifies its own frame in place
    sdf = db.load_sales(ep, raw_sales, copy=not own_read)
    del raw_sales
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
            related = bool(pana and pana in cust_anagrup.get(cust, set()))
            s, comp = SC.buyer_score(r["spend"], int(r["last"]) if pd.notna(r["last"]) else None,
                                     cust in custs_2026, related, False)
            out.append({"code": str(cust), "name": str(r["name"]), "spend": round(float(r["spend"])),
                        "qty": round(float(r["qty"])), "last": int(r["last"]) if pd.notna(r["last"]) else None,
                        "active_2026": cust in custs_2026,
                        "related_brand": related, "score": s, "drivers": comp})
        out.sort(key=lambda x: -x["score"])
        return out[:limit]

    # ---- collections: none (D-071, no collection urgency from invoice age) ----
    coll_scored = []

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

    ctx = AgentContext({
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "as_of_data_date": asof_d.isoformat(),
        "source_files": {"stock": os.path.basename(sp), "sales": os.path.basename(ep),
                         "aging": os.path.basename(ap) if ap else None},
        "config": {"max_context_items_per_category": top_n,
                   "inflation_rate": infl, "inflation_source": infl_src},
        "company": {
            "products_scanned": total,
            "working_capital": {
                "overstock_value": None,  # D-070: withdrawn classification, not a zero-valued measurement
                "overdue_total": None,    # D-071: unavailable (no due dates / agreement report), never zero
                "low_stock_count": None,
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
    })
    # M5-4: every customer code classified KAPALI (order-source attribute or invoice party), so the draft
    # refusal does not depend on whether the customer happens to be in the top-N risk list.
    ctx["closed_customers"] = sorted({c for c, a in attr.items() if a.get("closed")} | set(cinv))
    ctx.sales_c360_handoff = handoff
    ctx.data_quality = dq
    # M7-3: cross-sell model from the sales frame already read above (no Excel read; private, never serialized).
    from services import opportunities as OP
    ctx.opportunities = OP.build_cross_sell(sdf, asof_d)
    # M5 actions = the action-center surface of the uncut candidates cut to MAX_DAILY_ACTIONS: identical to
    # build_actions() (same producers, same stable sort, same cut; sales-queue-only categories never enter).
    candidates = build_action_candidates(ctx, coll_scored, over_cand, lap, shrink_real_neg, infl,
                                         cross_sell=ctx.opportunities)
    ctx["actions"] = A.action_center(candidates)[:C.MAX_DAILY_ACTIONS]
    ctx.sales_queue_candidates = sales_queue_reservoir(candidates)
    # M7-4: historical FUAR index (FUAR-attributed orders only) and a compact reason index for fair candidate
    # segments; both from data already in memory, both private (never serialized).
    from services import fairs as FA
    ctx.fairs = FA.build_fair_history(sdf, asof_d)
    idx = {}
    for a in candidates:
        if a.get("entity_type") == "customer":
            d = idx.setdefault(a["category"], {})
            e = str(a["entity_id"])
            d[e] = max(d.get(e, 0.0), float(a["score"]))
    ctx.reason_index = idx
    qcodes = {str(a["entity_id"]) for a in ctx.sales_queue_candidates}
    ctx.sales_queue_rows = {"collections": [r for r in coll_scored if str(r["code"]) in qcodes],
                            "winback": [r for r in lap if str(r["code"]) in qcodes],
                            "shrinking": [r for r in shrink if str(r["code"]) in qcodes],
                            "customers": [{"code": c, "name": ctx.opportunities.names.get(c, c)}
                                          for c in sorted({str(a["entity_id"]) for a in ctx.sales_queue_candidates
                                                           if a["category"] == "cross_sell"})]}
    # M5-4: the customer drawer and drafts look customers up in the lists above, which are cut to top-N, while
    # actions are produced from the full lists. Carry the already-computed rows of every customer that has an
    # action but fell outside a cut (no new calculation; the top-N lists themselves are unchanged).
    act_codes = {str(a["entity_id"]) for a in ctx["actions"] if a.get("entity_type") == "customer"}
    def _extra(key, full):
        have = {str(r["code"]) for r in ctx[key]}
        return [r for r in full if str(r["code"]) in act_codes and str(r["code"]) not in have]
    ctx["action_customers"] = {"collections": _extra("collections", coll_scored), "winback": _extra("winback", lap),
                               "shrinking": _extra("shrinking", shrink)}
    return ctx


def build_action_candidates(ctx, coll_scored, over_cand, lap, shrink_real_neg, infl, cross_sell=None):
    """All deterministic actions, ranked, BEFORE the daily cut (M7-2 seam; same producers as build_actions, plus the
    M7-3 cross-sell source when its model is given)."""
    from services import action_sources  # noqa: F401  (registers the built-in sources)
    return A.produce_candidates({"ctx": ctx, "coll_scored": coll_scored, "over_cand": over_cand, "lap": lap,
                                 "shrink_real_neg": shrink_real_neg, "infl": infl, "cross_sell": cross_sell})


def sales_queue_reservoir(candidates, per_category=None):
    """M7-3 (D-044): the private Günlük Satış reservoir. For every sales-queue category keep its N best-scored
    customer candidates (N = MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY), then their union in the global candidate
    order (score ↓, stable registration order). No quota applies to the final queue page."""
    from services import action_sources  # noqa: F401
    per = int(per_category if per_category is not None else C.MAX_SALES_QUEUE_CANDIDATES_PER_CATEGORY)
    keys = {c.key for c in A.categories(A.SURFACE_SALES_QUEUE)}
    seen = {}
    out = []
    for a in candidates:
        k = a.get("category")
        if k in keys and a.get("entity_type") == "customer" and seen.get(k, 0) < per:
            seen[k] = seen.get(k, 0) + 1
            out.append(a)
    return tuple(out)


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
