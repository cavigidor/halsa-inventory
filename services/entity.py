"""ContextRepository — agent_context'i bir kez üretir/önbelleğe alır ve varlık-bazlı
(müşteri/ürün) daraltılmış bağlam sağlar. Agent Excel'e ASLA dokunmaz; bu katmanı okur.
Testlerde hazır bir context dict enjekte edilebilir (Excel gerekmez)."""
import os, sys, threading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from services import context as CTX


class ContextRepository:
    def __init__(self, data_folder=None, context=None, inflation_rate=None):
        self._folder = data_folder
        self._ctx = context
        self._infl = inflation_rate
        self._cust_idx = None
        self._prod_idx = None
        self._lock = threading.Lock()   # warm-up thread and requests never build twice in parallel
        self.last_error = None          # short description of the last failed build (for /api/health)

    # ---- context ----
    def is_ready(self):
        """True once the deterministic context exists (never triggers a build)."""
        return self._ctx is not None

    def context(self, refresh=False):
        if self._ctx is not None and not refresh:
            return self._ctx
        with self._lock:
            if self._ctx is None or refresh:
                if not self._folder:
                    self.last_error = "Veri klasörü ayarlı değil."
                    raise RuntimeError(self.last_error)
                try:
                    self._ctx = CTX.build_context(self._folder, inflation_rate=self._infl)
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                    raise
                self.last_error = None
                self._cust_idx = self._prod_idx = None
        return self._ctx

    def refresh(self):
        return self.context(refresh=True)

    # ---- indices built from the assembled context (no Excel re-read) ----
    def _build_indices(self):
        ctx = self.context()
        cust = {}
        def merge(code, **kw):
            d = cust.setdefault(str(code), {"customer_code": str(code)})
            for k, v in kw.items():
                if v is not None and d.get(k) in (None, "", []):
                    d[k] = v
        for r in ctx.get("winback", []):
            merge(r["code"], name=r["name"], rep=r.get("rep"), sector=r.get("sector"),
                  historical_spend=r.get("spend"), last_active_year=r.get("last"),
                  winback_tier=r.get("tier"), overdue=r.get("overdue"),
                  top_products=[p["name"] for p in r.get("detail", {}).get("products", [])],
                  years=r.get("detail", {}).get("years", []))
        for r in ctx.get("shrinking", []):
            merge(r["code"], name=r["name"], rep=r.get("rep"), sector=r.get("sector"),
                  sales_2025=r.get("y2025"), sales_2026=r.get("y2026"),
                  nominal_pct=r.get("nominal_pct"), real_pct=r.get("real_pct"))
        for r in ctx.get("collections", []):
            merge(r["code"], name=r["name"], rep=r.get("rep"),
                  overdue=r.get("overdue"), oldest_overdue=r.get("oldest"),
                  collections_score=r.get("priority_score"),
                  revenue_25_26=r.get("revenue_25_26"), still_active=r.get("still_active"))
        for r in ctx.get("risk", []):
            merge(r["code"], name=r["name"], rep=r.get("rep"), overdue=r.get("overdue"),
                  revenue_25_26=r.get("rev"), still_ordering_2026=(r.get("active") == "Evet"),
                  debt_to_revenue_pct=r.get("ratio"), closed=r.get("closed"))
        prod = {}
        for r in ctx.get("low_stock", []):
            prod[str(r["code"])] = {"product_code": str(r["code"]), "name": r["name"],
                                    "status": "low_stock", "stock": r.get("stock"),
                                    "coverage": r.get("cov"), "min_level": r.get("min")}
        for r in ctx.get("overstock", []):
            d = prod.setdefault(str(r["code"]), {"product_code": str(r["code"]), "name": r["name"]})
            d.update({"status": "overstock", "stock": r.get("stock"),
                      "inventory_value": r.get("inv"), "historical_buyer_count": r.get("buyers"),
                      "ranked_buyers": r.get("top_buyers", [])})
        self._cust_idx, self._prod_idx = cust, prod

    def customer(self, code):
        if self._cust_idx is None:
            self._build_indices()
        return self._cust_idx.get(str(code))

    def product(self, code):
        if self._prod_idx is None:
            self._build_indices()
        return self._prod_idx.get(str(code))
