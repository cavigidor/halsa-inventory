"""Server-side grounding: fact-reference resolution + numeric guard.

The model writes prose and, where a number is needed, a placeholder such as
[[FACT:F001]]. This module (pure Python, provider-agnostic):

1. validates every referenced fact id (unknown id -> rejected),
2. rejects malformed / unresolved placeholders,
3. rejects any number the model wrote itself (numeric guard),
4. replaces each placeholder with the exact Python display value.

So every number in the final text comes from Python, never from the model.
"""
import re

from .base import ERR_FACT_REFERENCE, ERR_MALFORMED, ERR_NUMERIC_GUARD

FACT_TOKEN = re.compile(r"\[\[FACT:(F\d{3,4})\]\]")
_LEFTOVER = re.compile(r"\[\[|\]\]|\bFACT\s*:", re.IGNORECASE)

# Structured (non-prose) fields — validated separately, not scanned as text.
ID_LIST_FIELDS = {"evidence_fact_ids", "best_buyer_fact_ids"}
NON_PROSE_FIELDS = ID_LIST_FIELDS | {"confidence", "action_ref"}

_DIGIT = re.compile(r"\d")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# Number words / magnitude words that carry a quantitative claim without digits.
_NUMBER_WORDS = re.compile(
    r"\byüzde\b|\bmilyon|\bmilyar|\bbin\s*(?:tl|lira|₺)|\b(?:iki|üç|dört|beş|on)\s+kat|\bkatına\b|"
    r"\byarı(?:sı|sına|ya)?\b|\bpercent\b|\bmillion\b|\bbillion\b|\bthousand\b|\bhalf\b|\bdouble[ds]?\b",
    re.IGNORECASE)


class GroundingError(ValueError):
    def __init__(self, category, message, details=None):
        super().__init__(message)
        self.category = category
        self.details = details or []


def _strip_allowed(text, allowed):
    out = text
    for t in sorted((a for a in allowed if a), key=len, reverse=True):
        out = out.replace(t, " ")
    return out


# Structural list markers ("1) ", "2. ", "(3) ") at the start of the text or right after a line
# break / ';' / ':' are layout, not business claims. Single digit 1-9 only; anything else is scanned.
_ENUMERATOR = re.compile(r"(?:^|(?<=[\n;:.!?]))[ \t]*\(?[1-9][.)][ \t]+")


def numeric_violations(text, packet):
    """Numbers/quantities in model prose that did not come through a fact placeholder."""
    body = _ENUMERATOR.sub(" ", text)
    body = FACT_TOKEN.sub(" ", body)
    body = _strip_allowed(body, packet.allowed_text)
    allowed_years = packet.allowed_years()
    years_removed = _YEAR.sub(lambda m: " " if m.group(0) in allowed_years else m.group(0), body)
    found = []
    for m in re.finditer(r"[\w.,%₺+\-/]*\d[\w.,%₺+\-/]*", years_removed):
        found.append(m.group(0))
    for m in _NUMBER_WORDS.finditer(years_removed):
        found.append(m.group(0))
    return found


def _iter_prose(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in NON_PROSE_FIELDS:
                continue
            yield from _iter_prose(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_prose(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _iter_id_lists(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ID_LIST_FIELDS:
                yield k, v
            else:
                yield from _iter_id_lists(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_id_lists(v)


def check(data, packet):
    """Raise GroundingError on the first class of violation found; return None if clean."""
    known = packet.by_id
    # 1) structured id lists
    for field, ids in _iter_id_lists(data):
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise GroundingError(ERR_MALFORMED, f"{field} must be a list of fact ids")
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise GroundingError(ERR_FACT_REFERENCE, f"unknown fact id(s) in {field}: {unknown}", unknown)
        if field == "best_buyer_fact_ids":
            wrong = [i for i in ids if known[i]["kind"] != "buyer"]
            if wrong:
                raise GroundingError(ERR_FACT_REFERENCE, f"best_buyer_fact_ids must point to buyer facts: {wrong}", wrong)
    # 2) placeholders in prose
    numeric = []
    for path, text in _iter_prose(data):
        unknown = [fid for fid in FACT_TOKEN.findall(text) if fid not in known]
        if unknown:
            raise GroundingError(ERR_FACT_REFERENCE, f"unknown fact id(s) at {path}: {unknown}", unknown)
        if _LEFTOVER.search(FACT_TOKEN.sub(" ", text)):
            raise GroundingError(ERR_FACT_REFERENCE, f"malformed or unresolved placeholder at {path}")
        v = numeric_violations(text, packet)
        if v:
            numeric.append((path, v))
    if numeric:
        details = [f"{p}: {', '.join(v[:5])}" for p, v in numeric]
        raise GroundingError(ERR_NUMERIC_GUARD, "model-authored numbers found: " + "; ".join(details), details)


def resolve_text(text, packet):
    def sub(m):
        return packet.by_id[m.group(1)]["display_value"]
    out = FACT_TOKEN.sub(sub, text)
    if _LEFTOVER.search(out):
        raise GroundingError(ERR_FACT_REFERENCE, "unresolved placeholder after resolution")
    return out


def resolve(data, packet):
    """Return a deep copy of data with every placeholder replaced by the Python display value."""
    if isinstance(data, dict):
        return {k: (v if k in NON_PROSE_FIELDS else resolve(v, packet)) for k, v in data.items()}
    if isinstance(data, list):
        return [resolve(v, packet) for v in data]
    if isinstance(data, str):
        return resolve_text(data, packet)
    return data


def check_and_resolve(data, packet):
    check(data, packet)
    return resolve(data, packet)


def evidence_for(ids, packet):
    """Facts (id, label, display value) for the API response — values straight from Python."""
    seen, out = set(), []
    for i in ids or []:
        if i in packet.by_id and i not in seen:
            seen.add(i)
            f = packet.by_id[i]
            out.append({"fact_id": i, "label": f["label"], "display_value": f["display_value"]})
    return out


def referenced_ids(data):
    """All fact ids used anywhere in the output (placeholders + id lists)."""
    ids = []
    for _, text in _iter_prose(data):
        ids.extend(FACT_TOKEN.findall(text))
    for _, lst in _iter_id_lists(data):
        ids.extend(lst if isinstance(lst, list) else [])
    return list(dict.fromkeys(ids))
