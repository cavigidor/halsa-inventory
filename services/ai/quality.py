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


# a fact reference glued onto the end of a clause: "...sağlanabilir; [[FACT:F002]]" / "... [[FACT:F009]]."
_DANGLING = re.compile(r"[;,]\s*\[\[FACT:F\d{3,4}\]\]\s*[.!]?\s*$")


def _resolved_dupes(text, packet):
    """Text facts written verbatim AND referenced in the same field ("TEMSILCI-A ... [[FACT:F002]]")."""
    if packet is None:
        return []
    out = []
    for fid in set(FACT_TOKEN.findall(text)):
        f = packet.by_id.get(fid)
        if f and f["kind"] in ("text", "buyer") and len(f["display_value"]) >= 2:
            if f["display_value"] in FACT_TOKEN.sub(" ", text):
                out.append(f["display_value"])
    return out


def _missing_listed(text, packet):
    if packet is None:
        return 0
    return sum(1 for m in packet.missing if m and m in text)


def _sentences(text):
    return len([x for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x.strip()])


def assess(data, packet=None):
    """Return {"fields": {...}, "issues": [...]} for raw (unresolved) model output.
    With the packet, also checks duplicated names and listed missing fields."""
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
            if _DANGLING.search(val) and not val.strip().startswith("[[FACT"):
                issues.append(f"{key}: fact reference tacked onto the end")
            for d in _resolved_dupes(val, packet):
                issues.append(f"{key}: '{d}' written and referenced twice")
            if _missing_listed(val, packet) >= 2:
                issues.append(f"{key}: lists missing fields one by one")
            tmp = []
            _tidy_text(val, key, packet, tmp)
            issues.extend(f"needs tidy: {x}" for x in tmp)
        elif isinstance(val, list) and all(isinstance(v, str) for v in val):
            counts = [len(FACT_TOKEN.findall(v)) for v in val]
            for i, v in enumerate(val):
                if _DANGLING.search(v) and not v.strip().startswith("[[FACT"):
                    issues.append(f"{key}[{i}]: fact reference tacked onto the end")
                for d in _resolved_dupes(v, packet):
                    issues.append(f"{key}[{i}]: '{d}' written and referenced twice")
                tmp = []
                _tidy_text(v, key, packet, tmp)
                issues.extend(f"needs tidy: {x}" for x in tmp)
            fields[key] = {"items": len(val), "max_item_placeholders": max(counts, default=0)}
            lim = MAX_LIST_ITEMS.get(key)
            if lim is not None and len(val) > lim:
                issues.append(f"{key}: {len(val)} items (max {lim})")
            if key in MAX_LIST_ITEMS and max(counts, default=0) > MAX_ITEM_PLACEHOLDERS:
                issues.append(f"{key}: an item has {max(counts)} fact refs (max {MAX_ITEM_PLACEHOLDERS})")
        elif isinstance(val, list):                       # e.g. AIActionsOut.items
            for i, item in enumerate(val):
                if isinstance(item, dict):
                    sub = assess(item, packet)
                    for k, v in sub["fields"].items():
                        fields[f"{key}[{i}].{k}"] = v
                    issues.extend(f"{key}[{i}].{x}" for x in sub["issues"])
    return {"fields": fields, "issues": list(dict.fromkeys(issues))}


# =====================================================================================
# Deterministic tidy-up (Q-1 iteration 3)
# The model tends to use [[FACT:..]] like a citation footnote, gluing one onto the end of
# sentences. Prompt rules did not stop it, so Python normalizes the prose. This step ONLY
# REMOVES placeholders / technical tokens (moving their ids into evidence_fact_ids); it can
# never add a number or change a value. It runs after grounding.check and before resolve.
# =====================================================================================
NO_INLINE_FIELDS = {"risks", "opportunities", "recommended_action", "recommendation"}
_PH = r"\[\[FACT:F\d{3,4}\]\]"
_PH_RE = re.compile(_PH)
_PAREN = re.compile(r"\s*\(([^()]*)\)")
_SEP_DANGLING = re.compile(r"\s*[;,]\s*%s(?:\s*(?:[,;/]|ve)\s*%s)*\s*(?=[.!?](?:\s|$)|$)" % (_PH, _PH))
_END_AFTER_WORD = re.compile(r"(?<!\])(\b[^\s\[]+)((?:\s*[,;]?\s+%s)+)(?=\s*[.!?](?:\s|$)|\s*$)" % _PH)
# 3+ fact refs in a row anywhere ("Yıllık cirolar [[F]], [[F]], [[F]];") -> drop that whole clause
_RUN_CLAUSE = re.compile(r"[^.;!?]*?%s(?:[\s,/–\-]*(?:ve\s+)?%s){2,}[^.;!?]*[.;!?]?\s*" % (_PH, _PH))
# common Turkish finite-verb endings (clause is complete -> a following fact is a citation)
_VERB_END = re.compile(r"(?:yor|abilir|ebilir|amaz|emez|dır|dir|dur|dür|tır|tir|tur|tür|malı|meli|"
                       r"sın|sin|sun|sün|sınlar|sinler|ın|in|un|ün|ayın|eyin|acak|ecek|mış|miş|muş|müş|"
                       r"dı|di|du|dü|tı|ti|tu|tü)$", re.IGNORECASE)
_TECH_TOKEN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def _tr_upper_first(t):
    if not t:
        return t
    c = t[0]
    return ({"i": "İ", "ı": "I"}.get(c) or c.upper()) + t[1:]


def _clean_spacing(t):
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([.,;:!?)])", r"\1", t)
    t = re.sub(r"([;,])\s*([.!?])", r"\2", t)
    t = re.sub(r"[;,]\s*$", "", t)
    t = re.sub(r"([.!?]\s+)([a-zçğıöşü])", lambda m: m.group(1) + _tr_upper_first(m.group(2)), t)
    t = t.strip()
    return _tr_upper_first(t) if t and t[0].islower() else t


def _tidy_text(text, field, packet, fixes):
    """Run the tidy pass until stable (max 3 passes: removals can expose new citation patterns)."""
    moved, t = [], text
    for _ in range(3):
        nt, mv = _tidy_once(t, field, packet, fixes)
        moved += mv
        if nt == t:
            break
        t = nt
    return t, moved


def _tidy_once(text, field, packet, fixes):
    moved = []
    t = text
    if field in NO_INLINE_FIELDS:
        found = _PH_RE.findall(t)
        if found:
            moved += [m[7:-2] for m in found]
            t = _PH_RE.sub("", t)
            fixes.append(f"{field}: {len(found)} inline fact ref(s) moved to evidence")
    else:
        # (a) parenthetical groups made only of fact refs, or of a technical identifier
        def paren(m):
            inner = m.group(1)
            rest = re.sub(r"\bve\b", " ", _PH_RE.sub(" ", inner))
            if _PH_RE.search(inner) and not re.sub(r"[\s,;/–\-]", "", rest):
                moved.extend(x[7:-2] for x in _PH_RE.findall(inner))
                fixes.append(f"{field}: parenthetical fact refs moved to evidence")
                return ""
            return m.group(0)
        t = _PAREN.sub(paren, t)
        # (b) refs glued after ';' / ',' at the end of a clause
        def sep(m):
            moved.extend(x[7:-2] for x in _PH_RE.findall(m.group(0)))
            fixes.append(f"{field}: tacked-on fact ref moved to evidence")
            return ""
        t = _SEP_DANGLING.sub(sep, t)
        # (c) ref right after a finished verb at sentence end ("... başlatabilir [[FACT:F002]].")
        def verb(m):
            word = m.group(1).rstrip(".,;:!?\"')")
            if _VERB_END.search(word):
                moved.extend(x[7:-2] for x in _PH_RE.findall(m.group(2)))
                fixes.append(f"{field}: fact ref after a complete clause moved to evidence")
                return m.group(1)
            return m.group(0)
        t = _END_AFTER_WORD.sub(verb, t)
        # (c2) a clause that is just a run of facts -> removed (ids kept as evidence)
        def run(m):
            moved.extend(x[7:-2] for x in _PH_RE.findall(m.group(0)))
            fixes.append(f"{field}: clause listing facts back-to-back removed")
            return " "
        t = _RUN_CLAUSE.sub(run, t)
        # (d) text facts both written out and referenced -> drop the reference
        if packet is not None:
            plain = _PH_RE.sub(" ", t)
            for fid in dict.fromkeys(x[7:-2] for x in _PH_RE.findall(t)):
                f = packet.by_id.get(fid)
                if f and f["kind"] in ("text", "buyer") and len(f["display_value"]) >= 2 \
                        and f["display_value"] in plain:
                    t = re.sub(r"\s*[;,]?\s*\[\[FACT:%s\]\]" % fid, "", t)
                    moved.append(fid)
                    fixes.append(f"{field}: duplicated '{f['display_value']}' reference removed")
    # (e) technical identifiers in parentheses, e.g. "(lapsed_no_2026_purchase)"
    def tech(m):
        if _TECH_TOKEN.match(m.group(1).strip()):
            fixes.append(f"{field}: technical label removed")
            return ""
        return m.group(0)
    t = _PAREN.sub(tech, t)
    return _clean_spacing(t), moved


def tidy(data, packet=None, _field=None):
    """Return (tidied_copy, fixes). Moved fact ids are appended to the nearest evidence_fact_ids."""
    fixes = []
    if not isinstance(data, dict):
        return data, fixes
    out, moved_here = {}, []
    for key, val in data.items():
        if key in NON_PROSE_FIELDS or key == "warnings":
            out[key] = val
        elif isinstance(val, str):
            out[key], mv = _tidy_text(val, key, packet, fixes)
            moved_here += mv
        elif isinstance(val, list) and all(isinstance(v, str) for v in val):
            items = []
            for v in val:
                tv, mv = _tidy_text(v, key, packet, fixes)
                moved_here += mv
                if tv:
                    items.append(tv)
            out[key] = items
        elif isinstance(val, list):
            sub_items = []
            for item in val:
                ti, f = tidy(item, packet)
                fixes += f
                sub_items.append(ti)
            out[key] = sub_items
        else:
            out[key] = val
    if moved_here:
        ev = list(out.get("evidence_fact_ids", []) or [])
        out["evidence_fact_ids"] = list(dict.fromkeys(ev + moved_here))
    return out, fixes
