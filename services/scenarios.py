"""Ekonomik senaryo aritmetiği — TAHMİN DEĞİL, VARSAYIM. Tüm hesap Python'da.
FX'e duyarlı stok etkisi tedarikçi-para-birimi verisi gerektirir; yoksa 'unavailable' döner."""
import config as C

# Kullanıcı düzenleyebilir; bunlar sadece başlangıç varsayımları.
PRESETS = {
    "base":     {"label": "Baz",          "usdtry_change_pct": 0,  "eurtry_change_pct": 0,  "supplier_price_change_pct": 0},
    "moderate": {"label": "Orta Stres",   "usdtry_change_pct": 10, "eurtry_change_pct": 10, "supplier_price_change_pct": 10},
    "high":     {"label": "Yüksek Stres", "usdtry_change_pct": 20, "eurtry_change_pct": 20, "supplier_price_change_pct": 20},
}


def run_scenario(*, receivables_total, financing_rate, inflation_rate,
                 fx_sensitive_inventory_value=None, assumptions):
    """
    Girdiler açıkça VARSAYIM olarak etiketlenir.
    Hesaplanabilenler: alacakların fonlama/fırsat maliyeti; enflasyonla reel erime.
    Hesaplanamayan (tedarikçi verisi yok): FX'e duyarlı stok değeri etkisi -> available:False.
    """
    a = assumptions
    out = {"assumptions": a, "is_forecast": False, "results": {}}

    # 1) Alacakların yıllık fonlama/fırsat maliyeti (varsayılan 120 gün)
    if receivables_total is not None and financing_rate is not None:
        days = a.get("avg_days_outstanding", 120)
        out["results"]["receivables_funding_cost"] = {
            "value": round(receivables_total * financing_rate * (days/365.0), 2),
            "days": days, "annual_rate": financing_rate, "available": True,
            "label": "Alacakların tahmini fonlama maliyeti",
        }
    else:
        out["results"]["receivables_funding_cost"] = {"available": False, "reason": "financing_rate_or_receivables_missing"}

    # 2) Nominal bir tutarın enflasyonla reel erimesi (senaryo enflasyonu)
    infl = a.get("inflation_pct")
    infl = (infl/100.0) if infl is not None else inflation_rate
    if infl is not None and receivables_total is not None:
        out["results"]["receivables_real_erosion"] = {
            "value": round(receivables_total * (infl/(1+infl)) * (a.get("avg_days_outstanding",120)/365.0), 2),
            "inflation_pct": round(infl*100,1), "available": True,
            "label": "Alacakların bekleme süresinde reel değer kaybı (yaklaşık)",
        }
    else:
        out["results"]["receivables_real_erosion"] = {"available": False, "reason": "inflation_missing"}

    # 3) FX'e duyarlı stok — tedarikçi para birimi verisi gerekli
    if fx_sensitive_inventory_value:
        chg = a.get("usdtry_change_pct", 0)/100.0
        out["results"]["fx_inventory_replacement_delta"] = {
            "value": round(fx_sensitive_inventory_value * chg, 2),
            "usdtry_change_pct": a.get("usdtry_change_pct", 0), "available": True,
            "label": "FX'e duyarlı stokun yerine koyma değeri değişimi",
        }
    else:
        out["results"]["fx_inventory_replacement_delta"] = {
            "available": False, "reason": "no_supplier_currency_data",
            "label": "Tedarikçi para birimi verisi olmadan hesaplanamaz",
        }
    return out
