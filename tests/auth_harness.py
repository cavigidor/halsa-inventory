"""TEST-ONLY owner-session harness for M8-CP1 (D-060). Never imported by production code.

Since M8-CP1 every data-changing route needs the owner's session and CSRF token. The pre-existing in-process
(TestClient) tests of the preserved M5–M7 features exercise those features, not the login, so for the whole session
they run as an already-authenticated owner through this harness — the same pattern as tests/accuracy_harness.py.
The application has no environment variable, flag, header or parameter that does the same.

Tests marked ``@pytest.mark.production_auth`` (tests/test_m8_cp1_access.py) run the REAL session resolution, and the
browser tests always use the real login (tests/browser_auth.py). The accuracy gate is independent of this harness.
"""
from services import owner_auth as AUTH

_PRODUCTION_REQUEST_SESSION = AUTH.request_session
assert getattr(_PRODUCTION_REQUEST_SESSION, "__module__", "") == "services.owner_auth", "harness imported after patching"


class HarnessOwnerSession(AUTH.Session):
    """An owner session whose CSRF check always passes. TEST ONLY."""

    def csrf_ok(self, presented):
        return True


HARNESS_SESSION = HarnessOwnerSession(token_hash="test-harness", csrf_token="test-harness", created_at=0.0,
                                      expires_at=4102444800.0, idle_expires_at=4102444800.0)


def harness_request_session(request):
    return HARNESS_SESSION


def production_request_session(request):
    return _PRODUCTION_REQUEST_SESSION(request)
