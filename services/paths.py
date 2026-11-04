"""M8-CP2: one configurable persistent directory for everything the hosted app must keep.

Hosting (e.g. a single web service with one mounted disk) loses every file outside the mount at each deploy or
restart. `HALSA_DATA_ROOT` names that mount (an absolute path); the persistent paths default to fixed places under it:

    <root>/data/                  DATA_FOLDER    selected workbooks, sources.json, owner_sources.json
    <root>/uploads/               UPLOAD_DIR     Upload Center blobs, batches, previews
    <root>/state/agent_state.db   AGENT_DB       SQLite: sessions, actions, CRM, fairs, Veri Kontrol, upload history
    <root>/published/dashboard.html DASHBOARD_PATH the published page + dashboard.generation.json (+ .prev)

An explicitly set variable still wins, but with a data root every persistent path (and every configured owner / Hedef
workbook path) must be absolute and inside the root — otherwise `check()` refuses to start (fail closed: a path left
on the ephemeral disk would silently lose data at the next deploy). Without `HALSA_DATA_ROOT` nothing changes (local
use keeps the historical defaults). Atomic writes already stage their temporary files next to the target (same
directory → same filesystem, so `os.replace` is atomic); `durable_replace` adds the fsyncs.
"""
from __future__ import annotations

import os

ROOT_ENV = "HALSA_DATA_ROOT"
LAYOUT = {"DATA_FOLDER": "data", "UPLOAD_DIR": "uploads", "AGENT_DB": os.path.join("state", "agent_state.db"),
          "DASHBOARD_PATH": os.path.join("published", "dashboard.html")}
INPUT_PATHS = ("KREDI_PATH", "GIDER_GELIR_PATH", "BUTCE_PATH", "HEDEF_PATH")      # optional, read-only workbooks


class DataRootError(RuntimeError):
    """The hosted storage configuration would lose data; the server must not start."""


def data_root(env=None) -> str | None:
    env = os.environ if env is None else env
    v = (env.get(ROOT_ENV) or "").strip()
    return v or None


def apply(env=None) -> None:
    """Fill the persistent-path variables from HALSA_DATA_ROOT (explicit values are kept; validated by `check`)."""
    env = os.environ if env is None else env
    root = data_root(env)
    if root is None:
        return
    for k, rel in LAYOUT.items():
        if not (env.get(k) or "").strip():
            env[k] = os.path.join(root, rel)


def _inside(path: str, root: str) -> bool:
    p, r = os.path.realpath(path), os.path.realpath(root)
    return p == r or p.startswith(r.rstrip(os.sep) + os.sep)


def check(paths: dict[str, str], env=None) -> dict[str, str]:
    """With a data root: the root must be an existing, writable, absolute directory and every persistent path (and
    every configured input workbook) absolute and inside it. Creates the layout's directories. Returns the paths.
    Error messages name the variable, never a file's content."""
    env = os.environ if env is None else env
    root = data_root(env)
    if root is None:
        return paths
    if not os.path.isabs(root):
        raise DataRootError(f"{ROOT_ENV} must be an absolute path")
    if not os.path.isdir(root):
        raise DataRootError(f"{ROOT_ENV} does not exist or is not a directory (is the disk mounted?)")
    if not os.access(root, os.W_OK | os.X_OK):
        raise DataRootError(f"{ROOT_ENV} is not writable")
    for k, p in paths.items():
        if not p or not os.path.isabs(p):
            raise DataRootError(f"{k} must be an absolute path inside {ROOT_ENV}")
        if not _inside(p, root):
            raise DataRootError(f"{k} is outside {ROOT_ENV}; it would be lost at the next deploy")
    for k in INPUT_PATHS:
        p = (env.get(k) or "").strip()
        if p and (not os.path.isabs(p) or not _inside(p, root)):
            raise DataRootError(f"{k} is outside {ROOT_ENV}; upload owner workbooks through the Upload Center")
    for k in ("DATA_FOLDER", "UPLOAD_DIR"):
        if paths.get(k):
            os.makedirs(paths[k], exist_ok=True)
    for k in ("AGENT_DB", "DASHBOARD_PATH"):
        if paths.get(k):
            os.makedirs(os.path.dirname(paths[k]), exist_ok=True)
    return paths


def fsync_dir(path: str) -> None:
    """Persist a rename / create in `path` (best effort: not every filesystem supports directory fsync)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def fsync_file(path: str) -> None:
    with open(path, "rb+") as fh:
        os.fsync(fh.fileno())


def durable_replace(tmp: str, dest: str) -> None:
    """fsync the staged file, atomically move it over `dest` (same directory) and fsync the directory, so a crash or
    host restart right after a publication can't leave a renamed-but-empty file behind."""
    fsync_file(tmp)
    os.replace(tmp, dest)
    fsync_dir(os.path.dirname(os.path.abspath(dest)))
