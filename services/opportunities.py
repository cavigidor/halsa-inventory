"""M7-3 — deterministic opportunity intelligence (docs/m7_opportunity_intelligence.md, D-044).

1. **Cross-sell at the native `anagrup` level** (a product GROUP, never a SKU), from the trusted sales frame that
   `services/context.build_context` has already read (`sdf`; no Excel read here):
   - active cohort = customers with at least one order in the `CROSS_SELL_RECENT_DAYS` window ending at the sales
     DATA date (never today());
   - for every directed group pair A → B among the cohort's recent purchases:
     source_buyers, target_buyers, pair_buyers, confidence = pair/source, target_prevalence = target/cohort,
     lift = confidence/prevalence; a pair is eligible only when every `config.CROSS_SELL_*` guard holds;
   - a customer qualifies for target B when it bought an eligible source A inside the window AND has never bought B
     anywhere in the trusted sales history (a genuine assortment gap, not "not recently");
   - one opportunity per target group (strongest source chosen deterministically), at most
     `CROSS_SELL_MAX_TARGETS_PER_CUSTOMER` per customer;
   - score = min(`CROSS_SELL_SCORE_CAP`, round(confidence × 100, 1)): never "high" or "critical".
   Confidence here means "share of active source-group buyers in whom the target group is also observed"; it is not
   the probability that this customer will buy. No amount, margin, stock, Hedef or salesperson field is used.

2. **Descriptive territory intelligence** from an explicitly configured Hedef file (city / district / sector code,
   all `unverified`): counts only. Stale capacity, Risk and Kredi are never read here; nothing is ranked by
   salesperson; no score or action is created from a territory.

The model is attached PRIVATELY to the agent context (`AgentContext.opportunities`) and never serialized.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config as C

DIMENSION = "anagrup"
DIMENSION_LABEL = "Ana grup (anagrup)"
TERRITORY_DIMENSIONS = {"city": "İl", "district": "İlçe", "sector": "Sektör kodu"}
_HEDEF_FIELD = {"city": "city", "district": "district", "sector": "sector_code"}
GROUP_NOTE = ("Bu bir ürün-grubu fırsat sinyalidir; tekliften önce uygun ürün ve stok ayrıca kontrol "
              "edilmelidir.")
CONFIDENCE_NOTE = ("Oran: son {days} günde kaynak grubu alan aktif müşteriler arasında hedef grubu da alanların "
                   "payıdır; bu müşterinin satın alma olasılığı değildir.")
SCOPE_NOTE = ("Müşterinin toplam talebi bilinmez; ciro veya dönüşüm tahmini yapılmaz. Belirli bir ürün "
              "(SKU) önerilmez.")
TERRITORY_NOTE = ("Hedef bölge/sektör alanları doğrulanmamış kaynak bilgisidir; bu tablo satış performansı "
                  "sıralaması değildir.")
NOT_CONFIGURED_NOTE = "Bölge / sektör görünümü için Hedef dosyası yapılandırılmadı."
FAILED_NOTE = "Hedef dosyası yapılandırılmış ama okunamadı; bölge / sektör görünümü kullanılamıyor."


# ---------------------------------------------------------------------------------------------
# model types
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Thresholds:
    recent_days: int
    min_source_buyers: int
    min_target_buyers: int
    min_pair_buyers: int
    min_confidence: float
    min_lift: float
    max_targets: int

    @classmethod
    def from_config(cls) -> "Thresholds":
        return cls(C.CROSS_SELL_RECENT_DAYS, C.CROSS_SELL_MIN_SOURCE_BUYERS, C.CROSS_SELL_MIN_TARGET_BUYERS,
                   C.CROSS_SELL_MIN_PAIR_BUYERS, C.CROSS_SELL_MIN_CONFIDENCE, C.CROSS_SELL_MIN_LIFT,
                   C.CROSS_SELL_MAX_TARGETS_PER_CUSTOMER)

    def as_dict(self) -> dict:
        return {"recent_days": self.recent_days, "min_source_buyers": self.min_source_buyers,
                "min_target_buyers": self.min_target_buyers, "min_pair_buyers": self.min_pair_buyers,
                "min_confidence": self.min_confidence, "min_lift": self.min_lift,
                "max_targets_per_customer": self.max_targets, "score_cap": C.CROSS_SELL_SCORE_CAP}


@dataclass(frozen=True)
class PairStat:
    source: str
    target: str
    source_buyers: int
    target_buyers: int
    pair_buyers: int
    confidence: float
    target_prevalence: float
    lift: float

    def key(self):   # strongest first: confidence ↓, lift ↓, pair support ↓, then stable text order
        return (-self.confidence, -self.lift, -self.pair_buyers, self.source, self.target)

    def as_dict(self) -> dict:
        return {"source_group": self.source, "target_group": self.target, "source_buyers": self.source_buyers,
                "target_buyers": self.target_buyers, "pair_buyers": self.pair_buyers,
                "confidence": round(self.confidence, 4), "target_prevalence": round(self.target_prevalence, 4),
                "lift": round(self.lift, 4)}


@dataclass(frozen=True)
class Target:
    """One cross-sell opportunity of one customer: target group B with its strongest source evidence."""
    target: str
    source: str
    source_buyers: int
    target_buyers: int
    pair_buyers: int
    confidence: float
    lift: float
    supporting_sources: tuple = ()

    @property
    def score(self) -> float:
        return cross_sell_score(self.confidence)

    def as_dict(self) -> dict:
        return {"target_group": self.target, "source_group": self.source, "source_buyers": self.source_buyers,
                "target_buyers": self.target_buyers, "pair_buyers": self.pair_buyers,
                "confidence": round(self.confidence, 4), "lift": round(self.lift, 4), "score": self.score,
                "priority": C.band(self.score), "never_purchased": True,
                "supporting_sources": list(self.supporting_sources)}


def cross_sell_score(confidence: float) -> float:
    """The only cross-sell score: association confidence in points, capped below "high" (D-044)."""
    return float(min(C.CROSS_SELL_SCORE_CAP, round(float(confidence) * 100, 1)))


@dataclass
class CrossSellModel:
    status: str                          # "ok" | "unavailable"
    as_of: str | None
    window_start: str | None
    thresholds: Thresholds
    active_customers: int = 0
    groups: tuple = ()                   # labels in the model (stable text order)
    pairs: tuple = ()                    # eligible PairStat, strongest first
    targets: dict = field(default_factory=dict)    # customer code -> tuple[Target] (≤ max_targets, ordered)
    names: dict = field(default_factory=dict)      # customer code -> sales name (only customers with targets)
    diagnostics: dict = field(default_factory=dict)
    reason: str | None = None

    dimension = DIMENSION

    def for_customer(self, code: str) -> tuple:
        return self.targets.get(str(code), ())

    def summary(self, pair_limit: int = 20) -> dict:
        n = max(1, min(int(pair_limit), C.MAX_OPPORTUNITY_PAIRS))
        bands = Counter(C.band(t.score) for ts in self.targets.values() for t in ts)
        return {"status": self.status, "reason": self.reason, "dimension": DIMENSION,
                "dimension_label": DIMENSION_LABEL, "as_of_data_date": self.as_of, "window_start": self.window_start,
                "thresholds": self.thresholds.as_dict(), "active_customers": self.active_customers,
                "groups_in_model": len(self.groups), "eligible_pairs": len(self.pairs),
                "customers_with_opportunities": len(self.targets),
                "opportunity_targets": sum(len(v) for v in self.targets.values()),
                "targets_by_priority": {k: int(bands.get(k, 0)) for k in ("critical", "high", "medium", "low")},
                "pair_limit": n, "pairs_truncated": len(self.pairs) > n,
                "pairs": [p.as_dict() for p in self.pairs[:n]],
                "notes": [GROUP_NOTE, CONFIDENCE_NOTE.format(days=self.thresholds.recent_days), SCOPE_NOTE]}


# ---------------------------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------------------------
def _labels(series: pd.Series) -> pd.Series:
    """Exact group labels; empty, whitespace-only and NaN are 'no group' (labels are never trimmed or merged)."""
    def one(v):
        if v is None or (not isinstance(v, str) and pd.isna(v)):
            return None
        t = str(v)
        return t if (t.strip() and t.strip().lower() != "nan") else None
    return pd.Series([one(v) for v in series.tolist()], index=series.index, dtype=object)


def _unavailable(reason, th, as_of=None):
    return CrossSellModel(status="unavailable", as_of=as_of, window_start=None, thresholds=th, reason=reason,
                          diagnostics={"status": "unavailable", "reason": reason})


def build_cross_sell(sdf: pd.DataFrame, as_of: dt.date, thresholds: Thresholds | None = None) -> CrossSellModel:
    """Association model + per-customer targets from the already-normalized sales frame (`load_sales` output)."""
    th = thresholds or Thresholds.from_config()
    if sdf is None or DIMENSION not in sdf.columns or "_cust" not in sdf.columns or "_date" not in sdf.columns:
        return _unavailable("dimension_missing", th)
    as_of_ts = pd.Timestamp(as_of)
    start_ts = as_of_ts - pd.Timedelta(days=th.recent_days)       # window: (as_of − N days, as_of]
    cust = sdf["_cust"].astype(str)
    date = pd.to_datetime(sdf["_date"], errors="coerce")
    grp = _labels(sdf[DIMENSION])
    has_grp = grp.notna()
    recent = date.notna() & (date > start_ts) & (date <= as_of_ts)

    lines = int(len(sdf))
    diag = {"status": "ok", "dimension": DIMENSION, "lines": lines, "lines_with_group": int(has_grp.sum()),
            "line_coverage": round(float(has_grp.mean()), 4) if lines else 0.0,
            "distinct_groups": int(grp[has_grp].nunique()), "customers": int(cust.nunique()),
            "customers_with_group": int(cust[has_grp].nunique()),
            "window_days": th.recent_days}
    cohort = sorted(set(cust[recent]))
    n = len(cohort)
    diag["active_customers"] = n
    diag["active_customers_with_group"] = int(cust[recent & has_grp].nunique())
    if n == 0 or not has_grp.any():
        m = _unavailable("no_active_group_data", th, as_of.isoformat())
        m.diagnostics.update(diag)
        return m

    rec = pd.DataFrame({"c": cust[recent & has_grp], "g": grp[recent & has_grp]}).drop_duplicates()
    ever = pd.DataFrame({"c": cust[has_grp], "g": grp[has_grp]}).drop_duplicates()
    groups = tuple(sorted(rec["g"].unique()))
    gi = {g: i for i, g in enumerate(groups)}
    ci = {c: i for i, c in enumerate(cohort)}
    G = len(groups)
    R = np.zeros((n, G), dtype=np.int32)                 # recent purchases of the cohort (customer × group)
    R[rec["c"].map(ci).to_numpy(), rec["g"].map(gi).to_numpy()] = 1
    E = np.zeros((n, G), dtype=bool)                     # full-history purchases (only groups in the model matter)
    ev = ever[ever["c"].isin(ci) & ever["g"].isin(gi)]
    E[ev["c"].map(ci).to_numpy(), ev["g"].map(gi).to_numpy()] = True

    buyers = R.sum(axis=0).astype(np.int64)              # source_buyers[A] == target_buyers[A] within the cohort
    pair = (R.T @ R).astype(np.int64)                    # pair[A, B] = cohort customers who bought both recently
    with np.errstate(divide="ignore", invalid="ignore"):
        conf = np.where(buyers[:, None] > 0, pair / buyers[:, None], 0.0)
        prev = buyers / n
        lift = np.where(prev[None, :] > 0, conf / prev[None, :], 0.0)
    elig = ((buyers[:, None] >= th.min_source_buyers) & (buyers[None, :] >= th.min_target_buyers)
            & (pair >= th.min_pair_buyers) & (conf >= th.min_confidence) & (lift >= th.min_lift))
    np.fill_diagonal(elig, False)

    pairs = sorted((PairStat(groups[a], groups[b], int(buyers[a]), int(buyers[b]), int(pair[a, b]),
                             float(conf[a, b]), float(prev[b]), float(lift[a, b]))
                    for a, b in zip(*np.nonzero(elig))), key=PairStat.key)
    sources_for = {}                                     # target index -> eligible source indices, strongest first
    for p in pairs:
        sources_for.setdefault(gi[p.target], []).append(gi[p.source])

    targets, names = {}, {}
    rejected_purchased = 0
    if sources_for:
        tgt_idx = np.array(sorted(sources_for), dtype=np.int64)
        has_src = (R @ elig[:, tgt_idx].astype(np.int32)) > 0     # customer × target: bought an eligible source
        for row in range(n):
            hits = np.nonzero(has_src[row])[0]
            if not len(hits):
                continue
            opts = []
            for h in hits:
                b = int(tgt_idx[h])
                if E[row, b]:
                    rejected_purchased += 1              # bought B some time: not an assortment gap
                    continue
                srcs = [a for a in sources_for[b] if R[row, a]]
                a = srcs[0]
                opts.append(Target(groups[b], groups[a], int(buyers[a]), int(buyers[b]), int(pair[a, b]),
                                   float(conf[a, b]), float(lift[a, b]),
                                   tuple(groups[x] for x in srcs[1:3])))
            if opts:
                opts.sort(key=lambda t: (-t.confidence, -t.lift, -t.pair_buyers, t.target))
                targets[cohort[row]] = tuple(opts[: th.max_targets])
    if targets:
        mask = cust.isin(list(targets))
        nm = (sdf.loc[mask, "_cname"].groupby(cust[mask]).first().to_dict() if "_cname" in sdf.columns else {})
        names = {c: str(nm.get(c, c)) for c in targets}
    bands = Counter(C.band(t.score) for ts in targets.values() for t in ts)
    diag.update(groups_in_model=G, eligible_pairs=len(pairs), candidate_customers=len(targets),
                targets=sum(len(v) for v in targets.values()),
                targets_by_priority={k: int(bands.get(k, 0)) for k in ("critical", "high", "medium", "low")},
                targets_rejected_already_purchased=rejected_purchased)
    return CrossSellModel(status="ok", as_of=as_of.isoformat(), window_start=(start_ts + pd.Timedelta(days=1))
                          .date().isoformat(), thresholds=th, active_customers=n, groups=groups, pairs=tuple(pairs),
                          targets=dict(sorted(targets.items())), names=names, diagnostics=diag)


# ---------------------------------------------------------------------------------------------
# Turkish formatting (facts are Python strings; AI never formats them)
# ---------------------------------------------------------------------------------------------
def fmt_pct(x: float) -> str:
    return "%" + f"{x * 100:.1f}".replace(".", ",")


def fmt_ratio(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def target_facts(t: Target, days: int) -> list[str]:
    return [f"Kaynak grup (son {days} gün): {t.source}",
            f"Hedef grup: {t.target}",
            f"Son {days} günde kaynak grubu alan aktif müşteri: {t.source_buyers}",
            f"Bunlardan hedef grubu da alan: {t.pair_buyers} ({fmt_pct(t.confidence)})",
            f"Lift: {fmt_ratio(t.lift)}",
            "Bu müşterinin satış geçmişinde hedef gruptan alım: yok"]


def target_reason(t: Target) -> str:
    return (f"Bu müşteri yakın dönemde {t.source} grubundan alışveriş yaptı; benzer aktif müşterilerde {t.target} "
            f"grubu birlikte sık görülüyor ve bu müşteri {t.target} grubundan geçmişte alışveriş yapmadı.")


# ---------------------------------------------------------------------------------------------
# Customer 360 section
# ---------------------------------------------------------------------------------------------
def customer_section(model: CrossSellModel | None, code: str, closed: bool | None) -> dict:
    base = {"label": "Ürün kapsam fırsatı", "dimension": DIMENSION, "dimension_label": DIMENSION_LABEL,
            "recent_window_days": C.CROSS_SELL_RECENT_DAYS, "items": [], "closed": closed,
            "notes": [GROUP_NOTE, CONFIDENCE_NOTE.format(days=C.CROSS_SELL_RECENT_DAYS), SCOPE_NOTE]}
    if model is None or model.status != "ok":
        return {**base, "status": "unavailable"}
    items = [t.as_dict() for t in model.for_customer(code)]
    if closed is True and items:
        base["notes"] = base["notes"] + ["KAPALI müşteri: bu fırsatlar Günlük Satış'a satış gerekçesi olarak "
                                         "alınmaz."]
    return {**base, "status": "available" if items else "none", "items": items,
            "recent_window_days": model.thresholds.recent_days}


# ---------------------------------------------------------------------------------------------
# territory (descriptive, unverified, counts only)
# ---------------------------------------------------------------------------------------------
def territories(snap, model: CrossSellModel | None, dimension: str, limit: int | None = None) -> dict:
    """Counts per Hedef city / district / sector code. Uses ONLY those unverified fields of the explicit Hedef file
    plus Customer 360 activity and the cross-sell model; never capacity, Risk, Kredi or any salesperson field."""
    if dimension not in TERRITORY_DIMENSIONS:
        raise ValueError("unknown dimension")
    n = max(1, min(int(limit or 50), C.MAX_TERRITORY_ROWS))
    base = {"dimension": dimension, "dimension_label": TERRITORY_DIMENSIONS[dimension], "confidence": "unverified",
            "note": TERRITORY_NOTE, "limit": n, "rows": [], "total_rows": 0, "truncated": False,
            "coverage": None, "hedef_file": snap.hedef_meta.get("file") if snap.hedef_meta else None,
            "hedef_snapshot_date": snap.hedef_meta.get("snapshot_date") if snap.hedef_meta else None,
            "as_of_data_date": snap.as_of.isoformat()}
    if snap.hedef_status == "not_configured":
        return {**base, "status": "not_configured", "message": NOT_CONFIGURED_NOTE}
    if snap.hedef_status != "ok":
        return {**base, "status": "failed", "message": FAILED_NOTE}
    attr = _HEDEF_FIELD[dimension]
    window_from = (snap.as_of - dt.timedelta(days=C.CROSS_SELL_RECENT_DAYS - 1)).isoformat()
    year = snap.as_of.year
    targets = model.targets if (model is not None and model.status == "ok") else {}
    rows: dict[str, dict] = {}
    matched = missing = 0
    for code in sorted(snap.hedef):
        c = snap.customers.get(code)
        if c is None:
            continue
        matched += 1
        nv = getattr(snap.hedef[code], attr)
        val = nv.value if nv is not None else None
        if val is None or str(val).strip() == "":
            missing += 1
            continue
        label = str(val)
        r = rows.setdefault(label, {"label": label, "matched_customers": 0, "active_customers": 0,
                                    "current_year_customers": 0, "cross_sell_customers": 0,
                                    "cross_sell_targets": 0})
        r["matched_customers"] += 1
        last = c.get("last_order_date")
        if last and last >= window_from:
            r["active_customers"] += 1
        if year in (c.get("active_years") or []):
            r["current_year_customers"] += 1
        ts = targets.get(code)
        if ts:
            r["cross_sell_customers"] += 1
            r["cross_sell_targets"] += len(ts)
    ordered = sorted(rows.values(), key=lambda r: (-r["cross_sell_customers"], -r["matched_customers"], r["label"]))
    return {**base, "status": "ok", "message": None, "rows": ordered[:n], "total_rows": len(ordered),
            "truncated": len(ordered) > n,
            "coverage": {"hedef_accounts": len(snap.hedef), "matched_customers": matched,
                         "missing_value_customers": missing, "cross_sell_model": model.status if model else "none"}}


__all__ = ["CrossSellModel", "PairStat", "Target", "Thresholds", "build_cross_sell", "cross_sell_score",
           "customer_section", "target_facts", "target_reason", "territories", "TERRITORY_DIMENSIONS"]
