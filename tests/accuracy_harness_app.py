"""TEST-ONLY ASGI entry point for the browser smoke tests of preserved (currently withheld) features:
`python -m uvicorn tests.accuracy_harness_app:app`. Production runs `app:app`, which always uses the real gate."""
from tests.accuracy_harness import install

install()

from app import app  # noqa: E402,F401
