"""Kredi installment schedule views (M8-ACCURACY-2) — deterministic, source-derived, NOT displayed yet.

Pure functions over the validated `LoanInstallment` / `LoanContract` rows of `services.importers.kredi`. They exist so
the father's intended year / month / bank views can be verified later; the Kredi module stays withheld by the accuracy
gate (`owner.kredi`) until its blockers are resolved (DURUM meaning, saved-filter exclusions, negative residual,
authoritative workbook, snapshot date, reconciliation against payment records).

Rules:
- every amount is an exact source amount (sum of recorded values); nothing is estimated, repaired or zeroed;
- a missing value makes the group's total unknown (None) and is counted — never treated as 0;
- currencies are never merged: every group key starts with the currency (no FX);
- the workbook's saved filter is a VIEW (`hidden_in_source`), never a payment fact;
- DURUM is reported as recorded and never used to decide whether an installment is paid;
- a date-range selection total is a selection total, never "the company's complete debt";
- scheduled ≠ will be paid: this is not a cash-flow forecast.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

AMOUNT_FIELDS = ("principal", "interest", "bsmv", "kkdf", "installment_total", "paid_amount", "remaining_amount")

# category keys (Part 5 A–F)
ALL = "all_installments"                              # A
SELECTED = "selected_by_filter"                       # B (user-defined filter; see `select`)
FUTURE_REMAINING = "future_with_remaining"            # C: due after `today`, remaining > 0
PAST_DUE_REMAINING = "past_due_with_remaining"        # D: due before `today`, remaining > 0
DUE_TODAY_REMAINING = "due_today_with_remaining"      # E: due on `today`, remaining > 0
INVALID_OR_CONTRADICTORY = "invalid_or_contradictory" # F: negative / unknown amounts, Kapalı with positive remaining
RECORDED_SETTLED = "recorded_remaining_zero"          # remaining recorded as exactly 0 (a source fact, not a bank fact)


def _v(nv):
    v = getattr(nv, "value", None)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@dataclass
class Totals:
    count: int = 0
    sums: dict[str, float | None] = field(default_factory=lambda: {f: 0.0 for f in AMOUNT_FIELDS})
    missing: dict[str, int] = field(default_factory=lambda: {f: 0 for f in AMOUNT_FIELDS})

    def add(self, inst) -> None:
        self.count += 1
        for f in AMOUNT_FIELDS:
            v = _v(getattr(inst, f))
            if v is None:
                self.missing[f] += 1
                self.sums[f] = None
            elif self.sums[f] is not None:
                self.sums[f] = round(self.sums[f] + v, 2)

    def as_dict(self) -> dict:
        return {"count": self.count, **{f: self.sums[f] for f in AMOUNT_FIELDS},
                "missing": {f: n for f, n in self.missing.items() if n}}


def contract_index(contracts) -> dict[str, object]:
    return {c.contract_key: c for c in contracts}


def due(inst) -> dt.date | None:
    return getattr(inst.due_date, "value", None)


def is_contradictory(inst) -> bool:
    rem = _v(inst.remaining_amount)
    if any(_v(getattr(inst, f)) is None for f in AMOUNT_FIELDS) or due(inst) is None:
        return True
    if any((_v(getattr(inst, f)) or 0) < 0 for f in AMOUNT_FIELDS):
        return True
    return inst.status.value == "closed" and rem is not None and rem > 0


def categorize(installments: Iterable, today: dt.date) -> dict[str, list]:
    """Every installment lands in ALL; C/D/E by recorded remaining amount and due date relative to `today`;
    F flags (independently) negative or unknown amounts and Kapalı rows that still record a positive remaining."""
    out: dict[str, list] = {k: [] for k in (ALL, FUTURE_REMAINING, PAST_DUE_REMAINING, DUE_TODAY_REMAINING,
                                             INVALID_OR_CONTRADICTORY, RECORDED_SETTLED)}
    for i in installments:
        out[ALL].append(i)
        rem, d = _v(i.remaining_amount), due(i)
        if is_contradictory(i):
            out[INVALID_OR_CONTRADICTORY].append(i)
        if rem is None or d is None:
            continue
        if rem > 0:
            out[FUTURE_REMAINING if d > today else PAST_DUE_REMAINING if d < today else DUE_TODAY_REMAINING].append(i)
        elif rem == 0:
            out[RECORDED_SETTLED].append(i)
    return out


def select(installments: Iterable, contracts=None, *, year: int | None = None, month: int | None = None,
           lender: str | None = None, currency: str | None = None, due_from: dt.date | None = None,
           due_to: dt.date | None = None, visible_only: bool | None = None) -> list:
    """Category B: an explicit, user-defined filter. `visible_only=True` reproduces the workbook's saved view (only
    for reconciliation against it); the default ignores the saved filter entirely."""
    idx = contract_index(contracts or [])
    out = []
    for i in installments:
        d = due(i)
        c = idx.get(i.contract_key)
        if year is not None and (d is None or d.year != year):
            continue
        if month is not None and (d is None or d.month != month):
            continue
        if due_from is not None and (d is None or d < due_from):
            continue
        if due_to is not None and (d is None or d > due_to):
            continue
        if lender is not None and (c is None or c.lender_code != lender):
            continue
        if currency is not None and (c is None or c.currency != currency):
            continue
        if visible_only is not None and i.hidden_in_source == visible_only:
            continue
        out.append(i)
    return out


def breakdown(installments: Iterable, contracts, by: tuple[str, ...] = ("year", "month")) -> dict[tuple, dict]:
    """Totals per (currency, *by) group; `by` ⊆ {year, month, lender, due_date}. Currency is ALWAYS the first key."""
    idx = contract_index(contracts)
    groups: dict[tuple, Totals] = defaultdict(Totals)
    for i in installments:
        c = idx.get(i.contract_key)
        d = due(i)
        parts = [c.currency if c else None]
        for b in by:
            parts.append({"year": d.year if d else None, "month": d.month if d else None,
                          "lender": c.lender_code if c else None, "due_date": d}.get(b))
        groups[tuple(parts)].add(i)
    return {k: v.as_dict() for k, v in sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0]))}


def totals(installments: Iterable, contracts) -> dict[str, dict]:
    """Per-currency totals (never one cross-currency number)."""
    return {k[0]: v for k, v in breakdown(installments, contracts, by=()).items()}


def saved_view_reconciliation(installments: Iterable, contracts, today: dt.date) -> dict:
    """The workbook's saved filter vs the full source: visible / hidden counts and totals, and — because a filter is
    not proof of settlement — the hidden rows that still record a positive remaining amount, by due date."""
    rows = list(installments)
    visible = [i for i in rows if not i.hidden_in_source]
    hidden = [i for i in rows if i.hidden_in_source]
    hidden_pos = [i for i in hidden if (_v(i.remaining_amount) or 0) > 0]
    return {
        "source_installments": len(rows), "visible_installments": len(visible), "hidden_installments": len(hidden),
        "full_source_totals": totals(rows, contracts), "visible_totals": totals(visible, contracts),
        "visible_by_month": breakdown(visible, contracts, ("year", "month")),
        "hidden_with_positive_remaining": {
            "count": len(hidden_pos),
            "past_due": len([i for i in hidden_pos if due(i) and due(i) < today]),
            "due_today": len([i for i in hidden_pos if due(i) == today]),
            "future": len([i for i in hidden_pos if due(i) and due(i) > today]),
            "totals": totals(hidden_pos, contracts)},
    }


__all__ = ["categorize", "select", "breakdown", "totals", "saved_view_reconciliation", "is_contradictory", "ALL",
           "SELECTED", "FUTURE_REMAINING", "PAST_DUE_REMAINING", "DUE_TODAY_REMAINING", "INVALID_OR_CONTRADICTORY",
           "RECORDED_SETTLED", "AMOUNT_FIELDS"]
