"""TEST-ONLY algorithm harness for M8-ACCURACY. Never imported by production code.

The owner directive withholds every output that is not VERIFIED, and today none is. The deterministic algorithms are
kept (and must keep their regression coverage) so they can be verified later. The pre-existing test suite therefore
runs against this harness, which lets every output through. It exists only under tests/; the application has no
environment variable, flag or parameter that does the same.

Tests marked ``@pytest.mark.production_gate`` (tests/test_m8_accuracy.py and friends) run the real production gate.
"""
from services import accuracy as ACC

_PRODUCTION_CURRENT_GATE = ACC.current_gate
assert getattr(_PRODUCTION_CURRENT_GATE, "__module__", "") == "services.accuracy", "harness imported after patching"


class AlgorithmHarnessGate(ACC.Gate):
    """Lets every metric through so the preserved algorithms can be regression-tested. TEST ONLY."""

    def decide(self, key):
        title = ACC.REGISTER[key].title_tr if key in ACC.REGISTER else key
        return ACC.Decision(key, title, ACC.State.VERIFIED)

    def bound_to(self, collected):
        return self


def harness_gate(today=None, snapshot=None, expected_generation=None):
    return AlgorithmHarnessGate(today=today)


def install():
    """Used by the browser tests' server subprocess (tests/accuracy_harness_app.py)."""
    ACC.current_gate = harness_gate


def production_gate(today=None, snapshot=None, expected_generation=None):
    return _PRODUCTION_CURRENT_GATE(today=today, snapshot=snapshot, expected_generation=expected_generation)
