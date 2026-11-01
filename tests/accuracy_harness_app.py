"""TEST-ONLY ASGI entry point for the browser smoke tests of preserved (currently withheld) features:
`python -m uvicorn tests.accuracy_harness_app:app`. Production runs `app:app`, which always uses the real gate."""
import os

from tests.accuracy_harness import install

install()

# TEST-ONLY (D-071): production derives no collection reason from Cari invoice age. Browser tests of the generic queue
# mechanics opt in to the explicitly synthetic stand-in (tests/fixtures/synthetic_collections.py) for their fixture.
# This switch exists only in this test entry point; app:app has nothing like it. Installed before the app is imported,
# so the server's first context build already sees it.
_SYN = os.environ.get("HALSA_TEST_SYNTHETIC_COLLECTIONS")
if _SYN:
    from tests.fixtures import synthetic_collections as _SCOL
    _SYN_SWAP = _SCOL.synthetic_collection_reasons(getattr(_SCOL, _SYN))   # keep a reference: collecting the
    _SYN_SWAP.__enter__()                                                   # context manager would undo the swap

from app import app  # noqa: E402,F401
