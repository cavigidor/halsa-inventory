"""Deterministik puanlama. Tüm formüller şeffaf; her puan bileşen kırılımı ile döner
(NEDEN? / denetlenebilirlik için). LLM puan HESAPLAMAZ, sadece AÇIKLAR."""
import datetime as dt
import config as C

AYLAR = {"ocak":1,"şubat":2,"subat":2,"mart":3,"nisan":4,"mayıs":5,"mayis":5,"haziran":6,
         "temmuz":7,"ağustos":8,"agustos":8,"eylül":9,"eylul":9,"ekim":10,"kasım":11,"kasim":11,"aralık":12,"aralik":12}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def bucket_age_months(oldest_label, asof: dt.date):
    """'Temmuz-2025 ve öncesi' / 'Ocak-2026' -> as-of tarihine göre ay cinsinden yaş."""
    if not oldest_label or oldest_label == "—":
        return None
    s = str(oldest_label).lower().replace("ve öncesi", "").replace("ve oncesi", "").strip()
    parts = s.replace("–", "-").split("-")
    if len(parts) < 2:
        return None
    mon = AYLAR.get(parts[0].strip())
    try:
        yr = int(parts[1].strip())
    except ValueError:
        return None
    if not mon:
        return None
    return max(0, (asof.year - yr) * 12 + (asof.month - mon))


# ---------------- Collections priority (0..100) ----------------
def collections_priority(overdue, revenue, still_active, oldest_label, asof):
    """Bileşenler: tutar, yaş, borç/ciro oranı, hâlâ sipariş veriyor mu, tarihsel değer."""
    amount = _clamp((overdue or 0) / C.COLLECT_AMOUNT_CAP)
    age_m = bucket_age_months(oldest_label, asof)
    age = _clamp((age_m or 0) / 12.0)                      # 12+ ay = tam
    ratio = _clamp((overdue or 0) / revenue) if revenue and revenue > 0 else 0.0
    active = 1.0 if still_active else 0.0                   # hâlâ alıp ödemiyor = daha acil
    history = _clamp((revenue or 0) / C.REV_CAP)
    comp = {
        "amount": round(amount, 3), "age": round(age, 3), "ratio": round(ratio, 3),
        "still_active": active, "history": round(history, 3), "age_months": age_m,
    }
    score = 100 * (0.40*amount + 0.20*age + 0.20*ratio + 0.15*active + 0.05*history)
    return round(score, 1), comp


# ---------------- Overstock buyer score (per candidate buyer) ----------------
def buyer_score(product_spend, last_year, active_2026, related_brand, has_overdue):
    """Fazla stok için aday alıcı puanı. Deterministik; Python top-N seçer, AI açıklar."""
    spend = _clamp((product_spend or 0) / 100_000)
    recency = {2026: 1.0, 2025: 0.66, 2024: 0.33}.get(last_year, 0.1)
    activity = 1.0 if active_2026 else 0.0
    related = 1.0 if related_brand else 0.0
    comp = {"spend": round(spend,3), "recency": recency, "activity": activity,
            "related_brand": related, "overdue_penalty": bool(has_overdue)}
    score = 100 * (0.40*spend + 0.30*recency + 0.20*activity + 0.10*related)
    if has_overdue:
        score -= 25
    return round(max(0.0, score), 1), comp


# ---------------- Overall action priority (0..100) ----------------
def action_priority(financial_impact, urgency, risk, confidence):
    """0..1 girdiler -> ağırlıklı 0..100. Ağırlıklar config.PRIORITY_WEIGHTS."""
    w = C.PRIORITY_WEIGHTS
    s = (w["financial_impact"]*_clamp(financial_impact) + w["urgency"]*_clamp(urgency)
         + w["risk"]*_clamp(risk) + w["confidence"]*_clamp(confidence))
    return round(100*s, 1)


def confidence_label(*, has_amount, has_history, has_context):
    """Basit, açıklanabilir güven etiketi."""
    n = sum(bool(x) for x in (has_amount, has_history, has_context))
    return "high" if n >= 3 else "medium" if n == 2 else "low"
