"""TEST-ONLY: the production release state the suite asserts (D-073, 2026-11-01).

Exactly one metric is certified VERIFIED — the factual Cari list `receivables.collections` — and the generation-bound
evidence flag is True. Certification is a ceiling: the list is shown only on fresh, bound, scoped evidence. Every other
metric keeps its own ceiling and stays withheld. Tests that used to assert "nothing is VERIFIED / flag False" assert
this state instead, so any further promotion must change this one file and its review.
"""
from services import accuracy as ACC

CERTIFIED = ("receivables.collections",)


def certified_keys() -> list[str]:
    return [k for k, m in ACC.REGISTER.items() if m.certified == ACC.State.VERIFIED]


def assert_release_state() -> None:
    assert len(ACC.REGISTER) == 33
    assert certified_keys() == list(CERTIFIED)
    assert ACC.GENERATION_BOUND_API_EVIDENCE is True


def verified_outside_certified(metrics) -> list:
    """Status-route metric entries that are VERIFIED although not certified (must always be empty)."""
    return [m for m in metrics if m["state"] == "verified" and m["key"] not in CERTIFIED]
