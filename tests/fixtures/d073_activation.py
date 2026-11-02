"""TEST-ONLY: the D-073 activation set for `receivables.collections`, exactly as it would be committed after the owner
approves the proposed Cari freshness policy. Never imported by production code.

The production register (services/accuracy.py) already carries everything else the promotion needs: no blockers,
the metric-specific freshness definition (45 days, UNAPPROVED), D-xxx approval references, regression tests and the
real-data reconciliation reference. Activation is exactly three changes — and tests/test_m8_d073_cari_promotion.py
proves it is these three and nothing else:

1. `DEFINITIONS["cari_balance_freshness"]`: approved=True, decision_ref="D-073" (the owner's recorded sign-off);
2. `REGISTER["receivables.collections"]`: certified=VERIFIED;
3. `GENERATION_BOUND_API_EVIDENCE = True`.

Tests apply it with pytest's monkeypatch to the REAL production module, so the real gate, the real
`validate_register(production=True)`, the real dashboard builder and the real app routes decide.
"""
import dataclasses

from services import accuracy as ACC

METRIC = "receivables.collections"
FRESHNESS = "cari_balance_freshness"
DECISION = "D-073"


def activated(register=None, definitions=None):
    """(register, definitions) with the D-073 activation applied; the inputs are not modified."""
    reg = dict(ACC.REGISTER if register is None else register)
    defs = dict(ACC.DEFINITIONS if definitions is None else definitions)
    defs[FRESHNESS] = dataclasses.replace(defs[FRESHNESS], approved=True, decision_ref=DECISION)
    reg[METRIC] = dataclasses.replace(reg[METRIC], certified=ACC.State.VERIFIED)
    return reg, defs


def activate(monkeypatch):
    """Apply the activation to the production module for one test (undone by monkeypatch) and validate it with the
    production rules. Returns (register, definitions)."""
    reg, defs = activated()
    monkeypatch.setattr(ACC, "GENERATION_BOUND_API_EVIDENCE", True)
    monkeypatch.setattr(ACC, "REGISTER", reg)
    monkeypatch.setattr(ACC, "DEFINITIONS", defs)
    ACC.validate_register(production=True)
    return reg, defs
