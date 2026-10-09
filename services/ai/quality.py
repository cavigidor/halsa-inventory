"""Deterministic, provider-agnostic WRITING-QUALITY measurements of AI output (Q-1).

These never decide numbers and never reject output (safety is grounding.py's job). They measure
whether prose interprets facts or just lists them, so tests and the live smoke test can check
the prompt's effect objectively.
"""
import re

from .grounding import FACT_TOKEN, NON_PROSE_FIELDS

# limits used for the Q-1 verdict (mirrors the guidance in schemas.py / the prompt)
MAX_PLACEHOLDERS = {"summary": 4, "interpretation": 2, "status": 2, "risk": 2, "opportunity": 2,
                    "recommended_action": 1, "recommendation": 1, "message": 2}
MAX_LIST_ITEMS = {"risks": 3, "opportunities": 3}
MAX_ITEM_PLACEHOLDERS = 1
FACT_RUN_LIMIT = 3            # 3+ placeholders separated only by punctuation = a fact dump
_RUN = re.compile(r"(?:\[\[FACT:F\d{3,4}\]\][\s,;/–\-]*(?:ve\s+)?){%d,}" % FACT_RUN_LIMIT)
_JARGON = re.compile(r"\b(paket|paketi|pakette|fact|kanıt paketi|flag)\b", re.IGNORECASE)


def _sentences(text):
    return len([x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x.strip()])


def assess(data):
    """Return {"fields": {...}, "issues": [...]} for raw (unresolved) model output."""
    fields, issues = {}, []
    for key, val in (data or {}).items():
        if key in NON_PROSE_FIELDS or key == "warnings":
            continue
        if isinstance(val, str):
            n = len(FACT_TOKEN.findall(val))
            fields[key] = {"placeholders": n, "sentences": _sentences(val),
                           "fact_run": bool(_RUN.search(val)),
                           "jargon": bool(_JARGON.search(FACT_TOKEN.sub(" ", val)))}
            lim = MAX_PLACEHOLDERS.get(key)
            if lim is not None and n > lim:
                issues.append(f"{key}: {n} fact refs (max {lim})")
            if fields[key]["fact_run"]:
                issues.append(f"{key}: lists facts back-to-back")
            if fields[key]["jargon"]:
                issues.append(f"{key}: technical wording (paket/fact)")
            if key == "summary" and fields[key]["sentences"] > 4:
                issues.append(f"summary: {fields[key]['sentences']} sentences (max 4)")
        elif isinstance(val, list) and all(isinstance(v, str) for v in val):
            counts = [len(FACT_TOKEN.findall(v)) for v in val]
            fields[key] = {"items": len(val), "max_item_placeholders": max(counts, default=0)}
            lim = MAX_LIST_ITEMS.get(key)
            if lim is not None and len(val) > lim:
                issues.append(f"{key}: {len(val)} items (max {lim})")
            if key in MAX_LIST_ITEMS and max(counts, default=0) > MAX_ITEM_PLACEHOLDERS:
                issues.append(f"{key}: an item has {max(counts)} fact refs (max {MAX_ITEM_PLACEHOLDERS})")
        elif isinstance(val, list):                       # e.g. AIActionsOut.items
            for i, item in enumerate(val):
                if isinstance(item, dict):
                    sub = assess(item)
                    for k, v in sub["fields"].items():
                        fields[f"{key}[{i}].{k}"] = v
                    issues.extend(f"{key}[{i}].{x}" for x in sub["issues"])
    return {"fields": fields, "issues": issues}
