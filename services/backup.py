"""M8-CP2: consistent backup and isolated restore of the hosted Halsa state (owner / operator tool; no HTTP route).

What a recovery needs (everything else is code, rebuilt by the host):
- ``state/agent_state.db`` — SQLite: sessions, action decisions, CRM / follow-ups, fair campaigns, Veri Kontrol
  history and notes, Upload Center history. Copied with SQLite's online backup API (a consistent snapshot even while
  the server runs), then ``PRAGMA integrity_check``-ed. A live database file is never copied byte by byte.
- ``data/`` — the published workbooks and the selection files (``sources.json``, ``owner_sources.json``).
- ``published/`` — the published page and its generation manifest (``dashboard.generation.json``) (+ the previous
  page). After a restore the server re-validates them against the restored workbooks before serving anything.
- ``uploads/`` (optional, ``--include-uploads``) — Upload Center blobs / batches; only needed to re-open old batches.

Consistency: the copy is taken while holding the publication file lock (the one every refresh / Upload Center
publication holds, ``<dashboard>.refresh.lock``), and the selection / manifest digests are compared before and
after; a change aborts the backup (run it again). Temporary files of in-flight writes are never copied.

Restore never overwrites anything: it writes into a NEW, empty directory, after verifying every SHA-256 of the
backup manifest, and refuses unsafe paths. Point ``HALSA_DATA_ROOT`` at that directory and restart.

    python -m services.backup create  --dest /var/data/halsa-backups [--include-uploads]
    python -m services.backup verify  /var/data/halsa-backups/halsa-backup-20261104T020000Z
    python -m services.backup restore /path/to/halsa-backup-… --target /var/data/halsa-restored

The output contains private business data and session state: encrypt it before it leaves the instance
(docs/m8_cp2_hosting.md §6). Prints counts and relative paths only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys

from services import paths as PATHS

SCHEMA = "halsa-backup/1"
MANIFEST = "BACKUP_MANIFEST.json"
DB_REL = "state/agent_state.db"
TOP = ("data", "published", "uploads", "state")
# temporary files of atomic writes in flight (services/refresh, generation, upload_center, upload_files, app)
TEMP_SUFFIXES = (".new", ".tmp", ".kopya", ".geri", ".part", ".lock", ".restore.new")
TEMP_PREFIXES = (".sel-", ".gen-", "in-", "rx-")
PUBLISHED_NAMES = ("dashboard.html", "dashboard.generation.json", "dashboard.prev.html")


class BackupError(RuntimeError):
    pass


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_temp(name: str) -> bool:
    return name.endswith(TEMP_SUFFIXES) or name.startswith(TEMP_PREFIXES) or name.endswith(("-journal", "-wal", "-shm"))


def sources(env=None) -> dict:
    """The live locations, from the same variables the server uses (HALSA_DATA_ROOT fills them)."""
    env = os.environ if env is None else env
    PATHS.apply(env)
    from services import generation as GEN
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = env.get("DATA_FOLDER") or os.path.join(root, "data")
    dash = env.get("DASHBOARD_PATH") or os.path.join(root, "dashboard.html")
    stem, ext = os.path.splitext(dash)
    return {"data": data, "uploads": env.get("UPLOAD_DIR") or os.path.join(data, "uploads"),
            "db": env.get("AGENT_DB") or os.path.join(root, "data", "agent_state.db"),
            "published": [dash, GEN.manifest_path(dash), f"{stem}.prev{ext or '.html'}"],
            "lock": dash + ".refresh.lock"}


def _walk(base: str, skip_dirs: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    out = []
    if not os.path.isdir(base):
        return out
    skip = {os.path.realpath(d) for d in skip_dirs if d}
    for d, dirs, files in os.walk(base):
        dirs[:] = sorted(x for x in dirs if os.path.realpath(os.path.join(d, x)) not in skip)
        for f in sorted(files):
            p = os.path.join(d, f)
            if _is_temp(f) or not os.path.isfile(p) or os.path.islink(p):
                continue
            out.append((p, os.path.relpath(p, base).replace(os.sep, "/")))
    return out


def _guard_digest(src: dict) -> dict:
    """Digests of the files that define what is published (selection + manifest + page)."""
    from services import generation as GEN
    out = {}
    for p in (os.path.join(src["data"], GEN.SELECTION_FILE), os.path.join(src["data"], GEN.OWNER_SELECTION_FILE),
              *src["published"][:2]):
        out[os.path.basename(p)] = _sha(p) if os.path.isfile(p) else None
    return out


def _sqlite_snapshot(db: str, out: str) -> None:
    if not os.path.isfile(db):
        raise BackupError("state database not found")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)                               # consistent snapshot, safe while the server writes
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if ok != "ok":
        raise BackupError("state database copy failed its integrity check")


def create(dest: str, *, include_uploads: bool = False, env=None, now: dt.datetime | None = None) -> str:
    from services import refresh as RF
    src = sources(env)
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    out = os.path.join(os.path.abspath(dest), f"halsa-backup-{stamp}")
    if os.path.exists(out):
        raise BackupError("a backup with this timestamp exists already")
    os.makedirs(os.path.join(out, "state"), mode=0o700)
    files = []
    try:
        lock_dir = os.path.dirname(src["lock"])
        os.makedirs(lock_dir, exist_ok=True)
        with RF.refresh_lock(src["lock"]):            # no publication can swap files while we copy
            before = _guard_digest(src)
            _sqlite_snapshot(src["db"], os.path.join(out, DB_REL))
            plan = [(p, "data/" + r) for p, r in _walk(src["data"], skip_dirs=(src["uploads"],))]
            # the standard layout names (services/paths.LAYOUT), whatever DASHBOARD_PATH is called locally
            plan += [(p, "published/" + n) for p, n in zip(src["published"], PUBLISHED_NAMES) if os.path.isfile(p)]
            if include_uploads:
                plan += [(p, "uploads/" + r) for p, r in _walk(src["uploads"])]
            for p, rel in plan:
                target = os.path.join(out, *rel.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(p, target)
            if _guard_digest(src) != before:
                raise BackupError("the published files changed during the backup; run it again")
        for rel in [DB_REL] + [r for _, r in plan]:
            p = os.path.join(out, *rel.split("/"))
            files.append({"path": rel, "sha256": _sha(p), "bytes": os.path.getsize(p)})
        doc = {"schema": SCHEMA, "created_at": stamp, "include_uploads": include_uploads, "files": files}
        with open(os.path.join(out, MANIFEST), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
        for d, _, fs in os.walk(out):
            os.chmod(d, 0o700)
            for f in fs:
                os.chmod(os.path.join(d, f), 0o600)
    except RF.RefreshBusy:
        shutil.rmtree(out, ignore_errors=True)
        raise BackupError("a publication is running; run the backup again when it has finished") from None
    except Exception:
        shutil.rmtree(out, ignore_errors=True)        # never leave a half backup that looks complete
        raise
    return out


def _safe_rel(rel: str) -> bool:
    parts = rel.split("/")
    return (bool(rel) and not rel.startswith("/") and "\\" not in rel and parts[0] in TOP
            and all(p not in ("", ".", "..") for p in parts))


def verify(backup: str) -> dict:
    try:
        doc = json.load(open(os.path.join(backup, MANIFEST), encoding="utf-8"))
    except (OSError, ValueError):
        raise BackupError("backup manifest missing or unreadable") from None
    if doc.get("schema") != SCHEMA:
        raise BackupError("unknown backup format")
    seen = set()
    for f in doc.get("files", []):
        rel = f.get("path", "")
        if not _safe_rel(rel) or rel in seen:
            raise BackupError("unsafe or duplicate path in the backup manifest")
        seen.add(rel)
        p = os.path.join(backup, *rel.split("/"))
        if not os.path.isfile(p) or os.path.islink(p) or _sha(p) != f.get("sha256"):
            raise BackupError(f"backup file missing or changed: {rel}")
    if DB_REL not in seen:
        raise BackupError("backup has no state database")
    return doc


def restore(backup: str, target: str) -> dict:
    doc = verify(backup)
    target = os.path.abspath(target)
    if os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
        raise BackupError("restore target must be a new or empty directory (nothing is ever overwritten)")
    os.makedirs(target, mode=0o700, exist_ok=True)
    for f in doc["files"]:
        src = os.path.join(backup, *f["path"].split("/"))
        dst = os.path.join(target, *f["path"].split("/"))
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o600)
        if _sha(dst) != f["sha256"]:
            raise BackupError(f"restored file differs: {f['path']}")
    con = sqlite3.connect(os.path.join(target, *DB_REL.split("/")))
    try:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("restored state database failed its integrity check")
    finally:
        con.close()
    for d in ("data", "uploads", "published"):
        os.makedirs(os.path.join(target, d), mode=0o700, exist_ok=True)
    return {"files": len(doc["files"]), "target": target}


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m services.backup", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--dest", required=True)
    c.add_argument("--include-uploads", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("backup")
    r = sub.add_parser("restore")
    r.add_argument("backup")
    r.add_argument("--target", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "create":
            out = create(a.dest, include_uploads=a.include_uploads)
            n = len(json.load(open(os.path.join(out, MANIFEST)))["files"])
            print(f"backup created: {out} ({n} files, SQLite integrity ok)")
        elif a.cmd == "verify":
            print(f"backup verified: {len(verify(a.backup)['files'])} files, every SHA-256 matches")
        else:
            res = restore(a.backup, a.target)
            print(f"restored {res['files']} files into {res['target']}; set HALSA_DATA_ROOT to it and restart")
    except Exception as e:
        print(f"backup {a.cmd} failed: {e if isinstance(e, BackupError) else type(e).__name__}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
