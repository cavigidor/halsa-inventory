"""Nominal ve enflasyondan arındırılmış (reel) büyüme — tamamen deterministik.
LLM asla bu hesabı yapmaz."""


def calculate_real_growth(previous, current, inflation_rate):
    """
    previous, current: aynı dönem (ör. Oca–bugün) ciro, ₺.
    inflation_rate: kesir (0.45 = %45) veya None.
    Döner: nominal_pct, real_pct, available, reason.
    """
    if previous is None or previous <= 0:
        return {"nominal_pct": None, "real_pct": None, "available": False, "reason": "no_prior_period"}
    nominal = (current - previous) / previous
    out = {"nominal_pct": round(nominal * 100, 1), "real_pct": None, "available": False}
    if inflation_rate is None:
        out["reason"] = "no_inflation_rate"
        return out
    # reel = (1+nominal)/(1+enflasyon) - 1
    real = ((1 + nominal) / (1 + inflation_rate)) - 1
    out["real_pct"] = round(real * 100, 1)
    out["available"] = True
    return out


def financing_cost(principal, days_outstanding, annual_rate):
    """Uzun vadeli alacağın tahmini fonlama / fırsat maliyeti (muhasebe zararı DEĞİL)."""
    if principal is None or annual_rate is None or days_outstanding is None:
        return None
    if principal <= 0 or days_outstanding <= 0:
        return 0.0
    return round(principal * annual_rate * (days_outstanding / 365.0), 2)
