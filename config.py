"""Merkezi ayarlar. Tüm eşikler ve ağırlıklar burada — şeffaf ve değiştirilebilir."""
import os

# --- context / cost control ---
MAX_CONTEXT_ITEMS_PER_CATEGORY = int(os.environ.get("MAX_CONTEXT_ITEMS_PER_CATEGORY", 20))
MAX_DAILY_ACTIONS = int(os.environ.get("MAX_DAILY_ACTIONS", 15))
ENABLE_AI = os.environ.get("ENABLE_AI", "true").lower() == "true"
ENABLE_MACRO = os.environ.get("ENABLE_MACRO", "true").lower() == "true"

# --- inflation resolution (never silently assumed) ---
# Priority: live macro -> cached macro -> this manual value -> unavailable.
# Fraction, e.g. 0.45 = %45. None = not set.
MANUAL_INFLATION_RATE = (
    float(os.environ["MANUAL_INFLATION_RATE"]) if os.environ.get("MANUAL_INFLATION_RATE") else None
)

# --- scoring caps (₺) — normalize money into 0..1 before weighting ---
COLLECT_AMOUNT_CAP = 500_000     # overdue at/above this = full amount score
REV_CAP = 5_000_000              # customer revenue normalization cap
OVERSTOCK_VALUE_CAP = 500_000    # inventory value normalization cap
WINBACK_SPEND_CAP = 2_000_000    # historical spend normalization cap
SHRINK_DELTA_CAP = 500_000       # size of the drop (₺) normalization cap

# --- action priority weights (sum ~1.0) ---
PRIORITY_WEIGHTS = {
    "financial_impact": 0.40,
    "urgency": 0.25,
    "risk": 0.20,
    "confidence": 0.15,
}

# --- priority score bands (0..100) ---
BANDS = [(85, "critical"), (70, "high"), (50, "medium"), (0, "low")]

def band(score: float) -> str:
    for cutoff, name in BANDS:
        if score >= cutoff:
            return name
    return "low"
