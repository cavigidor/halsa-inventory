"""Request-pinned source views (M8-ACCURACY-2 correction, review of PR #28 issue 1).

Problem fixed: the accuracy middleware decided with one look at the live data, and the endpoint then read the context,
the owner views and the Customer 360 snapshot again. A refresh or an owner reload in between could authorize with
generation A's evidence and return generation B's numbers.

Rule: inside one HTTP request every shared source view is read AT MOST ONCE and then frozen for the rest of that
request:
- the core context (`ContextRepository.current()` / `.context()`),
- the owner-source entries (`OwnerRepository.snapshot()`),
- the Customer 360 snapshot (`Customer360Repository.snapshot()`),
- and the gate itself (built once per request, from the published manifest and the fingerprints of the views pinned
  in this request — `app.accuracy_gate`).

So the evidence that authorizes a response is bound (by fingerprint) to exactly the objects the response is built
from. A refresh during the request installs new objects for LATER requests; this request keeps its own consistent
(previous) generation. A view that was absent when the gate was built (e.g. the context was not built yet) may still
be loaded later in the request, but it was never part of the gate's evidence, so nothing that needs its evidence can be
displayed from it — it fails closed, never across generations.

Pinned objects are never mutated: a refresh replaces the context object, an owner reload replaces the entry dicts and
Customer 360 builds a new snapshot. Outside a request (CLI, background threads, direct test calls) nothing is pinned.

`PUBLISH_LOCK` serializes publication (dashboard + manifest + context swap, owner view + owner record swap) with the
capture of a request's gate inputs, so a request never even sees a half-published transition (which would be safe
anyway — the fingerprints would not bind — but would withhold needlessly).
"""
from __future__ import annotations

import contextvars
import threading
from typing import Any, Callable

PUBLISH_LOCK = threading.RLock()

_UNSET = object()
_VIEW: contextvars.ContextVar["RequestView | None"] = contextvars.ContextVar("halsa_request_view", default=None)


class RequestView:
    """The views pinned by one request. Thread-safe (FastAPI runs sync dependencies / endpoints in worker threads)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._slots: dict[tuple[str, int], Any] = {}
        self._refs: list = []            # keep pinned owners alive so id() keys stay unique for the request
        self.gate = None
        self.manifest = None
        self.served: dict | None = None

    def pin(self, owner: object, slot: str, fetch: Callable[[], Any]) -> Any:
        key = (slot, id(owner))
        with self._lock:
            v = self._slots.get(key, _UNSET)
            if v is not _UNSET and v is not None:
                return v                                   # frozen for the rest of the request
            v = fetch()                                    # first read (or the source was absent until now)
            self._slots[key] = v
            self._refs.append(owner)
            return v

    def peek(self, owner: object, slot: str) -> Any:
        with self._lock:
            v = self._slots.get((slot, id(owner)), _UNSET)
            return None if v is _UNSET else v

    def lock(self):
        return self._lock


def current_view() -> RequestView | None:
    return _VIEW.get()


def begin() -> contextvars.Token:
    return _VIEW.set(RequestView())


def end(token: contextvars.Token) -> None:
    _VIEW.reset(token)


def pinned(owner: object, slot: str, fetch: Callable[[], Any]) -> Any:
    """`fetch()` outside a request; inside a request the first non-None result, frozen for the request."""
    v = _VIEW.get()
    return fetch() if v is None else v.pin(owner, slot, fetch)


__all__ = ["PUBLISH_LOCK", "RequestView", "current_view", "begin", "end", "pinned"]
