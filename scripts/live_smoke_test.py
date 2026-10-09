"""ONE tiny live provider call with a SYNTHETIC evidence packet (no company data).

Usage (on a machine with a key in the environment or .env):
    AI_PROVIDER=openai python scripts/live_smoke_test.py
Exits 0 and prints SKIPPED when no OPENAI_API_KEY is set. Makes at most 2 requests
(one call + at most one numeric-guard retry). Prints metadata only.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main():
    import config as C            # loads ./.env if present
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        print("SKIPPED: OPENAI_API_KEY not set — no live call made.")
        return 0
    from services import evidence as EV
    from services.agent import BusinessActionAgent
    from services.ai.openai_provider import OpenAIProvider

    settings = C.ai_settings(provider="openai")
    calls = []
    agent = BusinessActionAgent(OpenAIProvider(settings), recorder=calls.append)
    cust = {"customer_code": "900.00.0001", "name": "Sentetik T1 Kirtasiye", "rep": "TEMSILCI-A",
            "winback_tier": 1, "historical_spend": 184230, "last_active_year": 2025, "overdue": 0,
            "years": [{"y": 2023, "spend": 61000, "orders": 4}, {"y": 2024, "spend": 70230, "orders": 5},
                      {"y": 2025, "spend": 53000, "orders": 3}]}
    packet = EV.customer_packet(cust)
    data, r = agent.analyze_customer(cust)
    print(f"provider=openai model={settings['model']} reasoning_effort={settings.get('reasoning_effort')} "
          f"max_output_tokens={settings['max_output_tokens']}")
    for c in calls:
        print(f"  attempt={c['attempt']} success={c['success']} category={c.get('error_category')} "
              f"latency_ms={c.get('latency_ms')} tokens_in={c.get('input_tokens')} "
              f"tokens_out={c.get('output_tokens')} reasoning_tokens={c.get('reasoning_tokens')}")
    if data is None:
        print(f"RESULT: FAILED ({r['category']}): {r['reason']}")
        return 1
    displays = {f["display_value"] for f in packet.facts}
    used = [e["display_value"] for e in data["evidence"]]
    print(f"RESULT: OK — schema valid, {len(data['evidence'])} fact(s) referenced, all values from Python: "
          f"{all(u in displays for u in used)}")
    q = r.get("quality_issues", [])
    print("quality (Q-1):", "OK — interprets, no fact lists" if not q else "ISSUES — " + "; ".join(q))
    print("summary:", data["summary"])
    print("risks:", " | ".join(data["risks"]))
    print("opportunities:", " | ".join(data["opportunities"]))
    print("recommended_action:", data["recommended_action"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
