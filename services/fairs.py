"""M7-4 — historical FUAR intelligence (docs/m7_fair_command_center.md, D-045).

Trusted basis: the main sales report frame that `services/context.build_context` has already read (`sdf`), with the
M6-2 label grammar (`services.importers.sales_activity.parse_label`, reused, not re-implemented). Unique `Srmmerk`
labels are parsed once; nothing is re-read from Excel.

Rules (owner decisions stay open, so nothing is assumed):
- historical fair facts = orders whose label type is **FUAR only**. ETKİNLİK, SAHA and GENEL are excluded; there is
  no `is_fair` grouping (D-036);
- order-document grain (`sip_evrakno_seri` + `sip_evrakno_sira`): a multi-line order counts once; amounts and
  quantities sum its lines; an order whose lines carry different labels is excluded (as in M6-2);
- edition identity = M6-2 `event_id` (edition year + exact stem + type + sequence); different stems never merge;
- an order with conflicting/missing dates stays in totals but not in date ranges (counted);
- an order whose lines disagree on the canonical D-003 customer stays in event totals but in no customer list
  (counted as unattributed-customer);
- amounts are the source-recorded ordered net amount ("Net Sipariş Tutarı (Nettutar)"), never revenue, delivered
  revenue, margin or ROI; missing amounts are counted, never filled;
- no conversion window: later GENEL orders are never attributed to a fair; a FUAR-tagged order is evidence of
  attribution, not of attendance or invitation.

Event stems may be partner names: they are kept for the LOCAL app only; `diagnostics` carries counts only.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from services.importers.sales_activity import LabelError, SourceType, parse_label
from services.importers.keys import record_key

AMOUNT_LABEL = "Net Sipariş Tutarı (Nettutar)"
AMOUNT_NOTE = ("Kaynakta kayıtlı sipariş net tutarıdır (Nettutar); teslim edilen / faturalanan ciro değildir. "
               "Ciro esası kararı açık.")
SCOPE_NOTES = [
    "Yalnızca Srmmerk etiketi FUAR olan siparişler; ETKİNLİK ve SAHA ayrı tutulur ve buraya dahil edilmez.",
    "Fuar sonrası dönüşüm penceresi seçilmediği için sonraki GENEL siparişler fuara atfedilmez.",
    "Fuar maliyeti fuara bağlı olarak kaydedilmediği için getiri (ROI) hesaplanmaz.",
    "Fuara atfedilmiş sipariş, davet veya katılım kaydı değildir; geçmiş davet / katılım listesi yok.",
]
TOP_N = 10


def fuar_event_id(edition_year: int, stem: str, sequence: int | None) -> str:
    """The M6-2 event-edition id for a FUAR (used to link a campaign to ERP data by EXACT fields only)."""
    return record_key("sales_event", [int(edition_year), stem, SourceType.FUAR.value, sequence])


@dataclass
class Edition:
    event_id: str
    edition_year: int
    stem: str
    sequence: int | None
    label: str
    first_order_date: str | None = None
    last_order_date: str | None = None
    orders: int = 0
    lines: int = 0
    customers: int = 0
    products: int = 0
    quantity: float = 0.0
    net_order_amount: float | None = None
    missing_amount_lines: int = 0
    date_unusable_orders: int = 0
    customer_conflict_orders: int = 0
    top_products: list = field(default_factory=list)
    top_groups: list = field(default_factory=list)
    top_categories: list = field(default_factory=list)
    customer_rows: list = field(default_factory=list)   # canonical customers only, descriptive order

    def summary(self) -> dict:
        return {"event_id": self.event_id, "edition_year": self.edition_year, "label": self.label,
                "stem": self.stem, "sequence": self.sequence, "first_order_date": self.first_order_date,
                "last_order_date": self.last_order_date, "orders": self.orders, "lines": self.lines,
                "customers": self.customers, "products": self.products, "quantity": self.quantity,
                "net_order_amount": self.net_order_amount, "missing_amount_lines": self.missing_amount_lines,
                "date_unusable_orders": self.date_unusable_orders,
                "customer_conflict_orders": self.customer_conflict_orders}


@dataclass
class FairHistory:
    status: str                                  # ok | unavailable
    as_of: str | None
    editions: dict = field(default_factory=dict)          # event_id -> Edition (year ↓, label ↑)
    customer_editions: dict = field(default_factory=dict)  # canonical customer -> {event_id: orders}
    diagnostics: dict = field(default_factory=dict)        # counts only
    reason: str | None = None

    def is_historical_fuar_customer(self, code: str) -> bool:
        return str(code) in self.customer_editions

    def customer_orders(self, code: str, event_id: str) -> int:
        return int(self.customer_editions.get(str(code), {}).get(event_id, 0))

    def list(self, year: int | None = None, q: str | None = None, limit: int = 50) -> tuple[list, int]:
        rows = [e for e in self.editions.values() if (year is None or e.edition_year == year)
                and (not q or q.casefold() in e.label.casefold())]
        return [e.summary() for e in rows[:limit]], len(rows)

    def years(self) -> list[int]:
        return sorted({e.edition_year for e in self.editions.values()}, reverse=True)


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def _tops_by_edition(f: pd.DataFrame, key: str, name_col: str | None) -> dict:
    """{event_id: top-N rows by Nettutar (then quantity, label)} for one dimension, computed once for all editions."""
    if key not in f.columns:
        return {}
    x = f.dropna(subset=[key])
    if x.empty:
        return {}
    g = x.groupby(["_eid", key], sort=True)
    t = pd.DataFrame({"amount": g["_amt"].sum(min_count=1), "quantity": g["_qty"].sum(), "orders": g["_ord"].nunique(),
                      "lines": g.size()})
    if name_col and name_col in x.columns:
        t["name"] = g[name_col].first()
    t = t.reset_index()
    t["_sort"] = t["amount"].fillna(float("-inf"))
    t = t.sort_values(["_eid", "_sort", "quantity", key], ascending=[True, False, False, True])
    out: dict = {}
    for r in t.groupby("_eid", sort=False).head(TOP_N).to_dict("records"):
        d = {"label": str(r[key]), "net_order_amount": _num(r["amount"]), "quantity": float(r["quantity"]),
             "orders": int(r["orders"]), "lines": int(r["lines"])}
        if "name" in r:
            d["name"] = None if r["name"] is None or pd.isna(r["name"]) else str(r["name"])
        out.setdefault(r["_eid"], []).append(d)
    return out


def build_fair_history(sdf: pd.DataFrame, as_of: dt.date | None = None) -> FairHistory:
    """Historical FUAR editions from the already-normalized sales frame (`dashboard_builder.load_sales` output)."""
    need = {"Srmmerk", "sip_evrakno_seri", "sip_evrakno_sira", "_cust", "_date"}
    if sdf is None or not need <= set(sdf.columns):
        return FairHistory(status="unavailable", as_of=as_of.isoformat() if as_of else None, reason="columns_missing",
                           diagnostics={"status": "unavailable", "reason": "columns_missing"})
    labels = sdf["Srmmerk"]
    parsed: dict = {}
    unparsed = 0
    for raw in pd.unique(labels):
        try:
            parsed[raw] = parse_label(raw)
        except (LabelError, TypeError):
            parsed[raw] = None
            unparsed += 1
    order_key = sdf["sip_evrakno_seri"].astype(str) + "|" + sdf["sip_evrakno_sira"].astype(str)
    labels_per_order = labels.groupby(order_key).nunique(dropna=False)
    conflict_orders = set(labels_per_order[labels_per_order > 1].index)
    type_of = {raw: (p.source_type.value if p is not None else "unparsed") for raw, p in parsed.items()}
    by_type = labels.map(type_of).astype(object)        # plain strings (pandas may coerce enums to text)
    fuar_mask = (by_type == SourceType.FUAR.value) & ~order_key.isin(conflict_orders)

    diag = {"status": "ok", "lines": int(len(sdf)), "distinct_labels": len(parsed), "unparsed_labels": unparsed,
            "label_conflict_orders": len(conflict_orders),
            "orders_by_type": {t.value: int(order_key[(by_type == t.value) & ~order_key.isin(conflict_orders)]
                                            .nunique()) for t in SourceType}}
    f = sdf.loc[fuar_mask].copy()
    f["_ord"] = order_key[fuar_mask]
    eid_of = {raw: p.event_id for raw, p in parsed.items() if p is not None and p.source_type is SourceType.FUAR}
    f["_eid"] = f["Srmmerk"].map(eid_of).astype(object)
    parsed_by_eid = {p.event_id: p for p in parsed.values() if p is not None and p.source_type is SourceType.FUAR}
    if "_amt_missing" in f.columns:
        f["_amt"] = f["Nettutar"].where(~f["_amt_missing"].astype(bool))
    else:
        f["_amt"] = f["Nettutar"]
    f["_d"] = pd.to_datetime(f["_date"], errors="coerce")

    # ---- order-document grain (vectorized once; no per-edition Python loops over rows) ----
    f["_dna"] = f["_d"].isna()
    f["_qty"] = f["miktar"] if "miktar" in f.columns else 0.0
    f["_sku"] = f["sto_kod"].astype(str) if "sto_kod" in f.columns else ""
    g = f.groupby("_ord", sort=False)
    o = pd.DataFrame({"eid": g["_eid"].first(), "d_n": g["_d"].nunique(), "d_na": g["_dna"].max(),
                      "d": g["_d"].first(), "c_n": g["_cust"].nunique(dropna=False), "cust": g["_cust"].first(),
                      "amount": g["_amt"].sum(min_count=1), "qty": g["_qty"].sum(), "lines": g.size()})
    o["ok_date"] = (o["d_n"] == 1) & ~o["d_na"].astype(bool)
    o["ok_cust"] = o["c_n"] == 1
    o["cust"] = o["cust"].astype(str)

    def _range(frame):
        dd = frame.loc[frame["ok_date"], "d"]
        return (dd.min().date().isoformat(), dd.max().date().isoformat()) if len(dd) else (None, None)

    per_e = f.groupby("_eid", sort=False)
    e_lines, e_products = per_e.size(), per_e["_sku"].nunique()
    e_qty, e_amt = per_e["_qty"].sum(), per_e["_amt"].sum(min_count=1)
    e_miss = f["_amt"].isna().groupby(f["_eid"]).sum()
    e_label = f.assign(_l=f["Srmmerk"].astype(str)).groupby("_eid")["_l"].min()
    oc = o[o["ok_cust"]]
    cg = oc.groupby(["eid", "cust"], sort=True)
    crow = pd.DataFrame({"orders": cg.size(), "lines": cg["lines"].sum(), "amount": cg["amount"].sum(min_count=1),
                         "qty": cg["qty"].sum()})
    okd = oc[oc["ok_date"]].groupby(["eid", "cust"])["d"]
    crow["first"], crow["last"] = okd.min(), okd.max()
    tops = {k: _tops_by_edition(f, k, extra) for k, extra in (("_sku", "StokIsmi"), ("anagrup", None),
                                                               ("kategori", None))}
    editions: dict[str, Edition] = {}
    cust_editions: dict[str, dict] = {}
    totals = Counter()
    for eid, oe in o.groupby("eid", sort=False):
        p = parsed_by_eid[eid]
        first, last = _range(oe)
        e = Edition(event_id=eid, edition_year=int(p.edition_year), stem=p.stem, sequence=p.sequence,
                    label=str(e_label[eid]), first_order_date=first, last_order_date=last, orders=int(len(oe)),
                    lines=int(e_lines[eid]), customers=int(oe.loc[oe["ok_cust"], "cust"].nunique()),
                    products=int(e_products[eid]), quantity=float(e_qty[eid]), net_order_amount=_num(e_amt[eid]),
                    missing_amount_lines=int(e_miss[eid]), date_unusable_orders=int((~oe["ok_date"]).sum()),
                    customer_conflict_orders=int((~oe["ok_cust"]).sum()),
                    top_products=tops["_sku"].get(eid, []), top_groups=tops["anagrup"].get(eid, []),
                    top_categories=tops["kategori"].get(eid, []))
        rows = []
        if eid in crow.index.get_level_values(0):
            for c, r in crow.loc[eid].iterrows():
                rows.append({"customer_code": str(c), "orders": int(r["orders"]), "lines": int(r["lines"]),
                             "net_order_amount": _num(r["amount"]), "quantity": float(r["qty"]),
                             "first_order_date": r["first"].date().isoformat() if pd.notna(r["first"]) else None,
                             "last_order_date": r["last"].date().isoformat() if pd.notna(r["last"]) else None})
                cust_editions.setdefault(str(c), {})[eid] = int(r["orders"])
        rows.sort(key=lambda r: (-(r["net_order_amount"] if r["net_order_amount"] is not None else float("-inf")),
                                 -r["orders"], r["customer_code"]))
        e.customer_rows = rows
        editions[eid] = e
        totals["orders"] += e.orders
        totals["date_unusable"] += e.date_unusable_orders
        totals["customer_conflict"] += e.customer_conflict_orders
        totals["missing_amount_lines"] += e.missing_amount_lines
    ordered = dict(sorted(editions.items(), key=lambda kv: (-kv[1].edition_year, kv[1].label, kv[0])))
    years = [e.edition_year for e in ordered.values()]
    diag.update(editions=len(ordered), edition_year_min=min(years) if years else None,
                edition_year_max=max(years) if years else None, fuar_orders=int(totals["orders"]),
                fuar_lines=int(len(f)), fuar_customers=len(cust_editions),
                fuar_lines_with_amount=int(f["_amt"].notna().sum()),
                fuar_missing_amount_lines=int(totals["missing_amount_lines"]),
                fuar_date_unusable_orders=int(totals["date_unusable"]),
                fuar_customer_conflict_orders=int(totals["customer_conflict"]),
                fuar_lines_with_group=int(f["anagrup"].notna().sum()) if "anagrup" in f.columns else 0,
                fuar_lines_with_category=int(f["kategori"].notna().sum()) if "kategori" in f.columns else 0,
                fuar_products=int(f["sto_kod"].astype(str).nunique()) if "sto_kod" in f.columns else 0)
    return FairHistory(status="ok", as_of=as_of.isoformat() if as_of else None, editions=ordered,
                       customer_editions=cust_editions, diagnostics=diag)


__all__ = ["AMOUNT_LABEL", "AMOUNT_NOTE", "Edition", "FairHistory", "SCOPE_NOTES", "build_fair_history",
           "fuar_event_id"]
