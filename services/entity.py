"""ContextRepository — agent_context'i bir kez üretir/önbelleğe alır ve varlık-bazlı
(müşteri/ürün) daraltılmış bağlam sağlar. Agent Excel'e ASLA dokunmaz; bu katmanı okur.
Testlerde hazır bir context dict enjekte edilebilir (Excel gerekmez)."""
import os, sys, threading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from services import context as CTX
from services import request_pin as RP


class ContextRepository:
    def __init__(self, data_folder=None, context=None, inflation_rate=None, generation_store=None,
                 on_generation_accepted=None):
        self._folder = data_folder
        self._ctx = context
        self._infl = inflation_rate
        self._generations = generation_store   # M8-ACCURACY-2: build from the restored generation's exact bytes
        self._on_accepted = on_generation_accepted   # (manifest, paths, frames) after a re-derived generation is accepted
        self._post_build = None
        self._cust_idx = None
        self._prod_idx = None
        self._idx_ctx = None
        self._idx_lock = threading.Lock()   # indices only; never held during a context build
        self._lock = threading.Lock()   # warm-up thread and requests never build twice in parallel
        self.last_error = None          # short description of the last failed build (for /api/health)

    # ---- context ----
    def is_ready(self):
        """True once the deterministic context exists (never triggers a build)."""
        return self._ctx is not None

    def context(self, refresh=False):
        """The live context (built on first use). Inside an HTTP request the context is pinned: every read in that
        request returns the same object, even if a refresh installs a newer one meanwhile (services/request_pin)."""
        if refresh:
            return self._context_unpinned(refresh=True)
        return RP.pinned(self, "ctx", self._context_unpinned)

    def _context_unpinned(self, refresh=False):
        if self._ctx is not None and not refresh:
            return self._ctx
        with self._lock:
            if self._ctx is None or refresh:
                if not self._folder:
                    self.last_error = "Veri klasörü ayarlı değil."
                    raise RuntimeError(self.last_error)
                try:
                    self._ctx = self._build()
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                    raise
                self.last_error = None
                self._cust_idx = self._prod_idx = None
        job, self._post_build = self._post_build, None
        if job is not None:
            if self._on_accepted is None:                    # nobody re-publishes here: nothing to wait for
                self._generations.dashboard_failed(job[0].core_id)
            else:                                            # outside the build lock (it takes the publish lock)
                try:
                    self._on_accepted(*job)
                except Exception:                            # the dashboard stays unpublished → GET / withholds
                    self._generations.dashboard_failed(job[0].core_id)
        return self._ctx

    def _build(self):
        """With a published or restart candidate generation: freeze the recorded files, require their recorded
        fingerprints, RE-DERIVE every validation result and all evidence from those bytes and the current selection
        file, and accept the generation only if the re-derived manifest has exactly the recorded identity (which covers
        the evidence). The saved manifest file is never trusted as evidence (review of PR #28 issue 2). Otherwise: the
        legacy build, which carries no generation and therefore receives no evidence."""
        store = self._generations
        cand = store.candidate() if store is not None else None
        if cand is not None and cand.core:
            from services import generation as GEN
            built = GEN.rederive_core(self._folder, cand)
            if built is not None:
                m, paths, frames = built
                m = store.accept_rederived(cand, m)
                if m is not None:
                    self._post_build = (m, paths, frames)        # re-publish the dashboard from these bytes
                    ctx = CTX.build_context(self._folder, inflation_rate=self._infl, sources=paths, frames=frames)
                    ctx.generation = {"core_id": m.core_id, "served": {r.key: r.sha256 for r in m.core}}
                    ctx.generation_manifest = m
                    return ctx
            else:
                store.reject_candidate(cand, "source_changed")    # the generation no longer matches the files
        return CTX.build_context(self._folder, inflation_rate=self._infl)

    def refresh(self):
        return self.context(refresh=True)

    def current(self):
        """The live context or None. Never builds (M5-5: the refresh compares against it). Pinned per request."""
        return RP.pinned(self, "ctx", lambda: self._ctx)

    def live(self):
        """The live context, never pinned (background maintenance only; never for a response)."""
        return self._ctx

    def replace_context(self, staged_ctx):
        """Install an already-built, verified context (M5-5 swap). Under the lock: replace it, drop the
        customer/product indices and the last error. No Excel read, no AI, no SQLite access."""
        if not isinstance(staged_ctx, dict) or "actions" not in staged_ctx:
            raise ValueError("not a built context")
        with self._lock:
            self._ctx = staged_ctx
            self._cust_idx = self._prod_idx = None
            self.last_error = None
        return self._ctx

    def restore_live(self, ctx):
        """M8-CP3: put back the exact live context object of before a failed publication (None = not built yet)."""
        with self._lock:
            self._ctx = ctx
            self._cust_idx = self._prod_idx = None

    # ---- indices built from the assembled context (no Excel re-read) ----
    def _indices(self):
        """Indices of the context THIS caller sees (pinned in a request). Cached only for the live context, so a
        request pinned to an older generation can never leave its index behind for newer requests."""
        ctx = self.context()
        with self._idx_lock:
            if self._idx_ctx is ctx and self._cust_idx is not None:
                return self._cust_idx, self._prod_idx
        cust, prod = self._compute_indices(ctx)
        with self._idx_lock:
            if ctx is self._ctx:
                self._idx_ctx, self._cust_idx, self._prod_idx = ctx, cust, prod
        return cust, prod

    def _build_indices(self):
        """Build (and cache, for the live context) the indices."""
        self._indices()

    def _compute_indices(self, ctx):
        cust = self._customer_index(ctx.get("winback", []), ctx.get("shrinking", []), ctx.get("collections", []),
                                    ctx.get("risk", []), ctx.get("action_customers") or {})
        # M7-2: customers of the private sales-queue candidates that the public index does not know. Fallback only:
        # every customer already in the public index keeps exactly its M5 facts.
        private = getattr(ctx, "sales_queue_rows", None) or {}
        if private:
            extra = self._customer_index([], [], [], [], private)
            for code, d in extra.items():
                cust.setdefault(code, d)
        for code in ctx.get("closed_customers", []):     # authoritative KAPALI classification (M5-4)
            if str(code) in cust:
                cust[str(code)]["closed"] = True
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
        return cust, prod

    @staticmethod
    def _customer_index(winback, shrinking, collections, risk, extra):
        cust = {}
        def merge(code, **kw):
            d = cust.setdefault(str(code), {"customer_code": str(code)})
            for k, v in kw.items():
                if v is not None and d.get(k) in (None, "", []):
                    d[k] = v
        for r in list(winback) + extra.get("winback", []):   # extra: rows of action customers outside the cuts (M5-4)
            merge(r["code"], name=r["name"], rep=r.get("rep"), sector=r.get("sector"),
                  historical_spend=r.get("spend"), last_active_year=r.get("last"),
                  winback_tier=r.get("tier"), overdue=r.get("overdue"),
                  top_products=[p["name"] for p in r.get("detail", {}).get("products", [])],
                  years=r.get("detail", {}).get("years", []))
        for r in list(shrinking) + extra.get("shrinking", []):
            merge(r["code"], name=r["name"], rep=r.get("rep"), sector=r.get("sector"),
                  sales_2025=r.get("y2025"), sales_2026=r.get("y2026"),
                  nominal_pct=r.get("nominal_pct"), real_pct=r.get("real_pct"))
        for r in list(collections) + extra.get("collections", []):
            merge(r["code"], name=r["name"], rep=r.get("rep"),
                  overdue=r.get("overdue"), oldest_overdue=r.get("oldest"),
                  collections_score=r.get("priority_score"),
                  revenue_25_26=r.get("revenue_25_26"), still_active=r.get("still_active"))
        for r in extra.get("customers", []):   # M7-3: code + sales name of cross-sell-only queue customers
            merge(r["code"], name=r["name"])
        for r in risk:
            merge(r["code"], name=r["name"], rep=r.get("rep"), overdue=r.get("overdue"),
                  revenue_25_26=r.get("rev"), still_ordering_2026=(r.get("active") == "Evet"),
                  debt_to_revenue_pct=r.get("ratio"), closed=r.get("closed"))
        return cust

    def customer(self, code):
        return self._indices()[0].get(str(code))

    def product(self, code):
        return self._indices()[1].get(str(code))
