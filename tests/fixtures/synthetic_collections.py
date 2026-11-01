"""Test-only stand-in for collection reasons (M8-R5B1 / D-071).

Production no longer derives any collection action from the Cari export: its month buckets are invoice age, not due
dates. The Günlük Satış / Fuar / CRM / Fırsatlar queue mechanics that handle a collection reason (D-043: invoice-account
scope, shared accounts never divided, unlinked accounts never assigned, KAPALI collection-only cards, collection-first
sequence) stay in the code for a future agreement-based producer. These tests keep exercising those mechanics by
temporarily swapping the built-in producer for explicitly SYNTHETIC reasons.

Entity / score / confidence values are the ones the withdrawn producer gave on each synthetic fixture, so the existing
ordering expectations keep their meaning. Titles, facts and reasons are synthetic and carry no overdue claim.
"""
from __future__ import annotations

import dataclasses
from contextlib import contextmanager

from services import actions as A
from services import action_sources as AS

# (invoice account, display name, score, confidence) per fixture
QUEUE = (("910.10.0003", "Sentetik Kuyruk Üç", 70.4, "high"),
         ("910.19.0001", "Sentetik Kuyruk Bir Bayi", 69.6, "high"),
         ("910.29.0056", "Sentetik Ortak Fatura", 68.8, "high"),
         ("910.10.0001", "Sentetik Kuyruk Bir", 65.6, "high"),
         ("910.88.0001", "Sentetik Bağlantısız", 63.6, "medium"))
FAIR = (("930.10.0015", "Sentetik Fuar 15", 73.6, "high"),)
OPP = (("920.10.0026", "Sentetik Fırsat 26", 73.6, "high"),)

REASON = "Sentetik test tahsilat gerekçesi (üretimde Cari fatura yaşından üretilmez, D-071)."


def actions(rows):
    return [A.make_action(source=AS.CAT_COLLECTIONS.source, category="collections", entity_type="customer",
                          entity_id=acc, title=f"TAHSİLAT (sentetik) — {name}", reason=REASON,
                          facts=[f"Sentetik tahsilat gerekçesi: {acc}"], drivers={}, score=score, confidence=conf)
            for acc, name, score, conf in rows]


@contextmanager
def synthetic_collection_reasons(rows):
    """Swap the built-in (withdrawn) collections producer for synthetic reasons; restore it afterwards."""
    i = next(n for n, s in enumerate(A._SOURCES) if s.name == AS.CAT_COLLECTIONS.source)
    orig = A._SOURCES[i]
    A._SOURCES[i] = dataclasses.replace(orig, produce=lambda _inp: actions(rows))
    try:
        yield
    finally:
        A._SOURCES[i] = orig


KAPALI_TEST_CODE = "900.00.0007"          # tests/fixtures/synthetic_erp.py: the synthetic KAPALI customer


def with_kapali_identity(ctx):
    """The synthetic KAPALI customer was resolvable in the customer index only through the Cari collection / risk rows,
    which no longer enter the context (D-071). Tests of the KAPALI draft rules (D-030) and the drawer get an
    identity-only entry: code and name, no Cari amount, no invoice age, no score."""
    import copy
    c = copy.deepcopy(ctx)
    extra = dict(c.get("action_customers") or {})
    if not any(str(r.get("code")) == KAPALI_TEST_CODE for r in extra.get("collections", [])):
        extra["collections"] = list(extra.get("collections", [])) + [
            {"code": KAPALI_TEST_CODE, "name": "Sentetik Kapali Ltd", "rep": None}]
    c["action_customers"] = extra
    return c
