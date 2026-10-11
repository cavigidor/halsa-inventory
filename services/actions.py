"""Deterministic action queue: generic action contract + action-source registry (M5-1, D-022).

Every action, whatever produced it, has the same shape (see `REQUIRED_FIELDS`). Sources register
themselves with the categories they emit and a `produce(inputs) -> list[action]` function. Adding a
new M6/M7 source (underpenetration, collection-first, cross-sell, follow-up, fair invite, …) means
registering one more source; the agent, the enrich endpoint, the AI cache and the UI do not change.

Ranking is global and deterministic: all actions are sorted by their Python score (0–100,
`services/scoring.py`), stable in registration order, and cut to `config.MAX_DAILY_ACTIONS`.
The AI never adds, removes, ranks or re-orders actions (D-015).
"""
from dataclasses import asdict, dataclass

import config as C
from services import store as ST

REQUIRED_FIELDS = ("action_id", "source", "category", "entity_type", "entity_id", "title", "reason",
                   "facts", "drivers", "score", "priority", "confidence", "requires_approval")
ENTITY_TYPES = {"customer", "product", "account", "company"}
CONFIDENCES = {"high", "medium", "low"}


@dataclass(frozen=True)
class Category:
    key: str
    label_tr: str
    color: str
    entity_type: str
    source: str


@dataclass(frozen=True)
class ActionSource:
    name: str
    categories: tuple          # tuple[Category, ...]
    produce: object            # callable(inputs: dict) -> list[dict]


_SOURCES = []                  # registration order = tie-break order for equal scores


class ContractError(ValueError):
    pass


def register(source: ActionSource):
    if any(s.name == source.name for s in _SOURCES):
        raise ValueError(f"action source already registered: {source.name}")
    keys = {c.key for s in _SOURCES for c in s.categories}
    for c in source.categories:
        if c.key in keys:
            raise ValueError(f"category already registered: {c.key}")
        if c.entity_type not in ENTITY_TYPES:
            raise ValueError(f"unknown entity_type {c.entity_type} for {c.key}")
    _SOURCES.append(source)
    return source


def unregister(name):
    """For tests / extensibility checks only."""
    for i, s in enumerate(_SOURCES):
        if s.name == name:
            del _SOURCES[i]
            return True
    return False


def sources():
    return list(_SOURCES)


def categories():
    return [c for s in _SOURCES for c in s.categories]


def category(key):
    return next((c for c in categories() if c.key == key), None)


def categories_payload():
    return [asdict(c) for c in categories()]


def action_id(category_key, entity_id, facts):
    """Stable id: category + entity + hash of the Python facts (changes when the facts change)."""
    return ST.content_key(category_key, str(entity_id), ST.hash_facts(facts))


def make_action(*, source, category, entity_type, entity_id, title, reason, facts, drivers, score,
                confidence):
    """Build a contract-conformant action. `priority` is derived from the Python score."""
    a = {"source": source, "category": category, "entity_type": entity_type,
         "entity_id": str(entity_id), "title": title, "reason": reason, "facts": list(facts),
         "drivers": dict(drivers or {}), "score": float(score), "priority": C.band(score),
         "confidence": confidence, "requires_approval": True,
         "interpretation": None, "recommendation": None}     # AI fields: filled later, optionally
    a["action_id"] = action_id(category, a["entity_id"], a["facts"])
    return a


def validate(a):
    missing = [k for k in REQUIRED_FIELDS if k not in a]
    if missing:
        raise ContractError(f"action missing fields: {missing}")
    if not isinstance(a["facts"], list) or not all(isinstance(f, str) for f in a["facts"]):
        raise ContractError("facts must be a list of Python-formatted strings")
    if not (0 <= float(a["score"]) <= 100):
        raise ContractError(f"score out of range: {a['score']}")
    if a["confidence"] not in CONFIDENCES:
        raise ContractError(f"bad confidence: {a['confidence']}")
    if a["entity_type"] not in ENTITY_TYPES:
        raise ContractError(f"bad entity_type: {a['entity_type']}")
    if category(a["category"]) is None:
        raise ContractError(f"unregistered category: {a['category']}")
    if not str(a["reason"]).strip():
        raise ContractError("reason must be a non-empty deterministic sentence")
    return a


def produce_all(inputs, limit=None):
    """Run every registered source, validate, rank by Python score (stable), cut to the daily limit."""
    acts = []
    for s in _SOURCES:
        for a in s.produce(inputs) or []:
            a.setdefault("source", s.name)
            acts.append(validate(a))
    acts.sort(key=lambda a: -a["score"])          # stable: equal scores keep registration order
    return acts[: (limit or C.MAX_DAILY_ACTIONS)]


def normalize(a):
    """Bring an action from an older/injected context up to the contract (tests, cached contexts).
    Missing id/source/reason are derived deterministically; nothing is invented by AI."""
    a = dict(a)
    a.setdefault("facts", [])
    a.setdefault("drivers", {})
    a.setdefault("requires_approval", True)
    a.setdefault("action_id", action_id(a["category"], a["entity_id"], a["facts"]))
    cat = category(a["category"])
    a.setdefault("source", cat.source if cat else "legacy")
    if not str(a.get("reason") or "").strip():
        a["reason"] = DEFAULT_REASONS.get(a["category"], "Python kurallarıyla belirlenen aksiyon.")
    a.setdefault("priority", C.band(a.get("score", 0)))
    return a


# Generic per-category fallback reasons (used only when an action carries none, e.g. old contexts).
DEFAULT_REASONS = {}
