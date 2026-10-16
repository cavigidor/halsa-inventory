"""Exact-match entity resolution with aggregate match reports.

Exact means exact: the identifier string must equal a master key string. No case folding, no
trimming beyond what the field spec declared, no fuzzy / edit-distance / embedding / AI matching.
A key that occurs more than once in the master is never resolved to one of its rows
(`duplicate_master`). Reports carry counts and rates only.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .models import (
    Confidence,
    IdentifierRole,
    MatchReport,
    MatchStatus,
    NormalizedRecord,
    NormalizedValue,
    Resolution,
)


@dataclass
class MasterIndex:
    """Index over master records by one or more key fields (the first is the canonical key).

    Built from normalized records; invalid / empty keys are not indexed (counted only).
    """

    name: str
    key_fields: tuple[str, ...]
    size: int = 0
    invalid_keys: int = 0
    _index: dict[str, dict[str, list[int]]] = field(default_factory=dict)  # key field → value → rows

    @classmethod
    def build(cls, name: str, records: Sequence[NormalizedRecord], key_fields: Sequence[str]) -> "MasterIndex":
        if not key_fields:
            raise ValueError("at least one key field")
        idx = cls(name=name, key_fields=tuple(key_fields), size=len(records))
        idx._index = {k: defaultdict(list) for k in idx.key_fields}
        for i, rec in enumerate(records):
            for k in idx.key_fields:
                nv = rec.values.get(k)
                if nv is None or nv.value is None or nv.confidence == Confidence.INVALID:
                    if k == idx.key_fields[0]:
                        idx.invalid_keys += 1
                    continue
                if not isinstance(nv.value, str):
                    raise TypeError("master keys must be opaque strings")
                idx._index[k][nv.value].append(i)
        idx._index = {k: dict(v) for k, v in idx._index.items()}
        return idx

    @classmethod
    def from_result(cls, result: "object", key_fields: Sequence[str], name: str | None = None) -> "MasterIndex":
        """Build from a staged ImportResult. A failed import cannot serve as a master."""
        if not getattr(result, "ok", False):
            raise ValueError("cannot build a master index from a failed import")
        return cls.build(name or result.source_key, result.records, key_fields)  # type: ignore[attr-defined]

    @property
    def duplicate_keys(self) -> int:
        return sum(1 for rows in self._index[self.key_fields[0]].values() if len(rows) > 1)

    def resolve(self, value: str | None) -> Resolution:
        if value is None:
            return Resolution(MatchStatus.NULL)
        hits: list[int] = []
        duplicate = False
        for k in self.key_fields:
            rows = self._index[k].get(value)
            if not rows:
                continue
            if len(rows) > 1:
                duplicate = True
            hits.extend(rows)
        if duplicate:
            return Resolution(MatchStatus.DUPLICATE_MASTER)
        distinct = set(hits)
        if not distinct:
            return Resolution(MatchStatus.UNMATCHED)
        if len(distinct) > 1:
            return Resolution(MatchStatus.AMBIGUOUS)
        return Resolution(MatchStatus.MATCHED, hits[0])


def resolve_values(values: Iterable[NormalizedValue | None], master: MasterIndex, *, field_name: str,
                   role: IdentifierRole) -> tuple[list[Resolution], MatchReport]:
    """Resolve identifier values; invalid or empty identifiers are `null` (never looked up)."""
    out: list[Resolution] = []
    for nv in values:
        usable = nv is not None and nv.value is not None and nv.confidence != Confidence.INVALID
        out.append(master.resolve(nv.value if usable else None))  # type: ignore[union-attr]
    return out, match_report(out, master, field_name=field_name, role=role)


def match_report(resolutions: Sequence[Resolution], master: MasterIndex, *, field_name: str,
                 role: IdentifierRole) -> MatchReport:
    counts = {s: 0 for s in MatchStatus}
    for r in resolutions:
        counts[r.status] += 1
    total = len(resolutions)
    non_null = total - counts[MatchStatus.NULL]
    rates = {s.value: (round(counts[s] / non_null, 4) if non_null else 0.0)
             for s in MatchStatus if s != MatchStatus.NULL}
    rates[MatchStatus.NULL.value] = round(counts[MatchStatus.NULL] / total, 4) if total else 0.0
    return MatchReport(field=field_name, role=role, master=master.name, total=total, non_null=non_null,
                       counts=counts, rates=rates, master_size=master.size,
                       master_duplicate_keys=master.duplicate_keys, master_invalid_keys=master.invalid_keys)
