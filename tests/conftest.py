"""Shared test setup.

- Puts the repo root on sys.path.
- Isolates the SQLite state DB per test (never touches ./data/agent_state.db).
- Provides SYNTHETIC ERP exports (tests/fixtures/synthetic_erp.py) — no real data needed.
- Guarantees no test can reach a paid API: OPENAI_API_KEY / ANTHROPIC_API_KEY are removed.
"""
import os
import sys

os.environ["STOCKAGENT_NO_DOTENV"] = "1"   # tests never read a developer's real .env / keys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests.fixtures import synthetic_erp  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER", "AI_MODEL", "KREDI_PATH", "KREDI_SNAPSHOT_DATE",
              "GIDER_GELIR_PATH", "GIDER_GELIR_REPORT_YEAR", "BUTCE_PATH", "BUTCE_PLANNING_YEAR", "BUTCE_SNAPSHOT_DATE"):
        monkeypatch.delenv(k, raising=False)
    from services import store as ST
    monkeypatch.setattr(ST, "DB_PATH", str(tmp_path / "agent_state_test.db"))
    ST.init_db()
    yield


@pytest.fixture(scope="session", autouse=True)
def _accuracy_algorithm_harness():
    """M8-ACCURACY. The production gate withholds every output that is not VERIFIED (none is today). The pre-existing
    tests of the preserved algorithms and their UI run against the TEST-ONLY harness (tests/accuracy_harness.py) for
    the whole session, including module- and session-scoped fixtures that build dashboards."""
    from services import accuracy as ACC
    from tests import accuracy_harness as H
    original = ACC.current_gate
    ACC.current_gate = H.harness_gate
    yield
    ACC.current_gate = original


@pytest.fixture(autouse=True)
def _accuracy_gate_mode(request, monkeypatch):
    """Tests marked `production_gate` run the REAL production gate."""
    if request.node.get_closest_marker("production_gate") is not None:
        from services import accuracy as ACC
        from tests import accuracy_harness as H
        monkeypatch.setattr(ACC, "current_gate", H.production_gate)
    yield


@pytest.fixture(scope="session")
def synthetic_dir(tmp_path_factory):
    return synthetic_erp.write_all(str(tmp_path_factory.mktemp("synthetic_erp")))


@pytest.fixture(scope="session")
def synthetic_ctx(synthetic_dir):
    from services import context as CTX
    return CTX.build_context(synthetic_dir, inflation_rate=0.40)
