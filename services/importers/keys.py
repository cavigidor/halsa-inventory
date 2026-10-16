"""Deterministic synthetic record keys.

Some sources have no native line number (the sales report: document × SKU repeats in about
0.03 % of rows). A record key is a SHA-256 digest of the declared identity parts plus the
occurrence of that identity in source order. No Python `hash()` (randomized per process), so a
key is stable across runs for unchanged, identically ordered source records.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Iterable, Sequence

_SEP = "\x1f"


def _encode(part: Any) -> str:
    # Type-tagged so that "1" (string) and 1 (int) never collide, and None is distinct from "".
    if part is None:
        return "n:"
    if isinstance(part, bool):
        return f"b:{int(part)}"
    if isinstance(part, int):
        return f"i:{part}"
    if isinstance(part, float):
        return f"f:{part!r}"
    if isinstance(part, dt.datetime):
        return f"t:{part.isoformat()}"
    if isinstance(part, dt.date):
        return f"d:{part.isoformat()}"
    if isinstance(part, str):
        return "s:" + part.replace("\\", "\\\\").replace(_SEP, "\\x1f")
    raise TypeError(f"unsupported identity part type: {type(part).__name__}")


def record_key(source: str, parts: Sequence[Any], occurrence: int = 1, length: int = 20) -> str:
    """`<source>:<hex digest>` of the identity parts and their 1-based occurrence."""
    if occurrence < 1:
        raise ValueError("occurrence is 1-based")
    payload = _SEP.join([_encode(source), *(_encode(p) for p in parts), f"o:{occurrence}"])
    return f"{source}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:length]}"


def keys_with_occurrence(source: str, identities: Iterable[Sequence[Any]]) -> list[str]:
    """Keys for identities in source order; repeated identities get occurrence 1, 2, 3 …"""
    seen: dict[tuple[str, ...], int] = {}
    out: list[str] = []
    for parts in identities:
        enc = tuple(_encode(p) for p in parts)
        seen[enc] = seen.get(enc, 0) + 1
        out.append(record_key(source, parts, seen[enc]))
    return out
