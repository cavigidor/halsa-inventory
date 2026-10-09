"""Merkezi ayarlar. Tüm eşikler ve ağırlıklar burada — şeffaf ve değiştirilebilir."""
import os


def _load_dotenv():
    """Load ./.env (gitignored) into os.environ without overriding real env vars.
    Minimal parser, no dependency. Skipped when STOCKAGENT_NO_DOTENV is set (tests)."""
    if os.environ.get("STOCKAGENT_NO_DOTENV"):
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split(" #", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)


_load_dotenv()

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

# --- AI provider settings (Milestone 3) ---
# Read at call time (not import time) so tests and the factory see the current env.
# No secret is ever stored here; OPENAI_API_KEY is read only by the provider.
AI_PROVIDERS = ("disabled", "mock", "openai", "anthropic")
DEFAULT_MODELS = {                       # the ONLY place default model names live
    "openai": "gpt-5-mini",
    "anthropic": "claude-sonnet-4-5",
}


def _env_int(name, default, lo, hi):
    try:
        v = int(os.environ.get(name, "") or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def ai_settings():
    """Current AI configuration from the environment (bounded, never secret)."""
    provider = (os.environ.get("AI_PROVIDER", "") or "disabled").strip().lower()
    if provider in ("", "none", "off", "false", "0"):
        provider = "disabled"
    return {
        "provider": provider,
        "model": (os.environ.get("AI_MODEL", "") or "").strip() or DEFAULT_MODELS.get(provider),
        "timeout_seconds": _env_int("AI_TIMEOUT_SECONDS", 30, 1, 120),
        "max_output_tokens": _env_int("AI_MAX_OUTPUT_TOKENS", 3000, 256, 16000),
        # SDK-level retries for transient errors only (429 / 5xx / connection). Bounded.
        "max_transient_retries": _env_int("AI_MAX_RETRIES", 1, 0, 3),
        # optional, only for reasoning models (e.g. "low"); empty = not sent
        "reasoning_effort": (os.environ.get("AI_REASONING_EFFORT", "") or "").strip().lower() or None,
    }


# Evidence-packet guardrails
MAX_EVIDENCE_FACTS = 60          # per packet
MAX_EVIDENCE_PACKET_BYTES = 24_000
NUMERIC_GUARD_RETRIES = 1        # at most one stricter retry after a numeric/fact violation

# --- priority score bands (0..100) ---
BANDS = [(85, "critical"), (70, "high"), (50, "medium"), (0, "low")]

def band(score: float) -> str:
    for cutoff, name in BANDS:
        if score >= cutoff:
            return name
    return "low"
