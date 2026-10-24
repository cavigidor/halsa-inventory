"""M8-VKM-1 — Veri Kontrol Merkezi persistence, cross-month carry-over and owner review (design §3–§5, D-062).

Storage: the existing private SQLite file (`services/store.DB_PATH`, gitignored), created non-destructively. All SQL is
parameterized. Nothing here logs an issue's text, a note or a count.

Tables
- ``dq_issue``         one row per stable issue (current state). Never deleted: a trigger refuses DELETE.
- ``dq_issue_event``   APPEND-ONLY history of every detection, carry-over and owner decision (triggers refuse UPDATE
                       and DELETE).
- ``dq_observation``   APPEND-ONLY log of every observed upload per source (state, fingerprint, digest of its issues).
- ``dq_source_state``  the latest observation per source (what the current issue list describes).

Observation (only on an ACCEPTED generation — the publication points of `services/generation.GenerationStore`):
- a source that was uploaded: new issue → ``detected`` / ``acik``; still there → ``still_present`` (the owner's
  decision is kept; if a count grew, an ``incelendi`` / ``kabul_edildi`` decision returns to ``yeniden_acildi``);
  gone → ``not_present`` / ``cozuldu_kaynakta``; back again → ``reopened`` / ``yeniden_acildi``;
- a source that was NOT uploaded: its open issues get ``source_missing`` (status unchanged, never resolved) and the
  source gets one ``missing_source`` issue;
- the same observation again (repeated refresh, restart re-derivation) changes nothing and appends nothing.

Review (owner only, enforced by services/access.py + CSRF): ``incelendi`` / ``kabul_edildi`` / back to ``acik`` and
notes. The reviewer is the authenticated session's role, never a request field. A review submitted against an older
observation (``vkm_generation``) is refused. Review NEVER touches source data, evidence, the gate or a manifest.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from services import store as ST
from services import vkm as V

log = logging.getLogger("vkm")
_LOCK = threading.Lock()
NOTE_MAX = 500
SEVERITY_ORDER = {s: i for i, s in enumerate(V.SEVERITIES)}
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class VKMError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


STALE_TR = ("Veriler bu sayfa açıldıktan sonra yeniden gözlendi; liste yenilendi. Lütfen konuyu kontrol edip tekrar "
            "deneyin.")
NOT_FOUND_TR = "Bu kayıt bulunamadı."
TRANSITION_TR = "Bu konunun şu anki durumunda bu işlem yapılamaz."
NOTE_EMPTY_TR = "Not boş olamaz."
NOTE_LONG_TR = f"Not en fazla {NOTE_MAX} karakter olabilir."


# ---------------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------------
def init_tables(c) -> None:
    """CREATE IF NOT EXISTS only (existing state untouched). Append-only and no-delete rules are database triggers."""
    c.execute("""CREATE TABLE IF NOT EXISTS dq_issue(
        issue_id TEXT PRIMARY KEY, issue_type TEXT NOT NULL, check_code TEXT NOT NULL, severity TEXT NOT NULL,
        source_key TEXT NOT NULL, locator TEXT NOT NULL, field_name TEXT, column_letter TEXT, join_id TEXT,
        affected_metrics TEXT NOT NULL,
        first_seen_generation TEXT NOT NULL, last_seen_generation TEXT NOT NULL,
        first_seen_source_sha TEXT NOT NULL, last_seen_source_sha TEXT NOT NULL,
        first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
        period_start TEXT, period_end TEXT,
        detail_counts TEXT NOT NULL, detail_labels TEXT NOT NULL,
        status TEXT NOT NULL, presence TEXT NOT NULL, reviewer TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS dq_issue_event(
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id TEXT NOT NULL REFERENCES dq_issue(issue_id),
        generation_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, event TEXT NOT NULL, status_after TEXT,
        detail_counts TEXT, reason TEXT, actor TEXT, note TEXT, at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS dq_observation(
        observation_id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL, state TEXT NOT NULL,
        generation_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, digest TEXT NOT NULL, issue_count INTEGER NOT NULL,
        detector_version TEXT NOT NULL, at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS dq_source_state(
        source_key TEXT PRIMARY KEY, state TEXT NOT NULL, generation_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        digest TEXT NOT NULL, period_start TEXT, period_end TEXT, observed_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS dq_issue_event_issue ON dq_issue_event(issue_id, event_id)")
    c.execute("CREATE INDEX IF NOT EXISTS dq_issue_source ON dq_issue(source_key)")
    for table in ("dq_issue_event", "dq_observation"):
        for op in ("UPDATE", "DELETE"):
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table}
                          BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""")    # fixed names above
    c.execute("""CREATE TRIGGER IF NOT EXISTS dq_issue_no_delete BEFORE DELETE ON dq_issue
                 BEGIN SELECT RAISE(ABORT, 'dq_issue rows are never deleted'); END""")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso(d) -> str | None:
    if d in (None, ""):
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


# ---------------------------------------------------------------------------------------------
# observations
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Observation:
    """What one accepted upload says about one source: present with its issues, or missing."""
    state: str                                    # "present" | "missing"
    generation_id: str
    source_sha256: str = ""
    issues: tuple[V.Issue, ...] = ()
    period_start: str | None = None
    period_end: str | None = None

    def all_issues(self, key: str) -> tuple[V.Issue, ...]:
        return (V.missing_issue(key),) if self.state == "missing" else self.issues

    def digest(self, key: str) -> str:
        body = [V.DETECTOR_VERSION, self.state, self.source_sha256,
                sorted((i.issue_id, sorted(i.counts.items()), list(i.labels)) for i in self.all_issues(key))]
        return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode("utf-8")).hexdigest()


def observe(observations: Mapping[str, Observation], *, now: str | None = None,
            guard: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Reconcile the store with one accepted upload's observations (one transaction). Idempotent per source: an
    observation whose digest equals the last one for that source changes and appends nothing. `guard` is evaluated
    under the write lock; False → nothing is written (the observed generation is no longer the published one)."""
    now = now or _now()
    changed: list[str] = []
    with _LOCK, ST._conn() as c:
        if guard is not None and not guard():
            return {"changed": [], "superseded": True}
        for key in sorted(observations):
            if _observe_source(c, key, observations[key], now):
                changed.append(key)
    return {"changed": changed}


def _observe_source(c, key: str, obs: Observation, now: str) -> bool:
    digest = obs.digest(key)
    prev = c.execute("SELECT digest FROM dq_source_state WHERE source_key=?", (key,)).fetchone()
    if prev is not None and prev["digest"] == digest:
        return False
    current = {i.issue_id: i for i in obs.all_issues(key)}
    c.execute("""INSERT INTO dq_observation(source_key,state,generation_id,source_sha256,digest,issue_count,
                 detector_version,at) VALUES(?,?,?,?,?,?,?,?)""",
              (key, obs.state, obs.generation_id, obs.source_sha256, digest, len(current), V.DETECTOR_VERSION, now))
    existing = {r["issue_id"]: r for r in c.execute("SELECT * FROM dq_issue WHERE source_key=?", (key,))}
    for iid, issue in current.items():
        _upsert(c, issue, existing.get(iid), obs, now)
    for iid, r in existing.items():
        if iid in current:
            continue
        if obs.state == "missing":
            # absence of the FILE is never evidence that its problems disappeared (design §3.2, acceptance 7)
            if r["presence"] == "present" and r["status"] != "cozuldu_kaynakta":
                c.execute("UPDATE dq_issue SET presence='source_missing', updated_at=? WHERE issue_id=?", (now, iid))
                _event(c, iid, obs.generation_id, obs.source_sha256, "source_missing", r["status"], None, None,
                       "system", None, now)
        elif r["presence"] != "absent":
            # the source WAS uploaded and the problem is not in it: resolved in the source (not by review)
            c.execute("""UPDATE dq_issue SET presence='absent', status='cozuldu_kaynakta', updated_at=?
                         WHERE issue_id=?""", (now, iid))
            _event(c, iid, obs.generation_id, obs.source_sha256, "not_present", "cozuldu_kaynakta", None, None,
                   "system", None, now)
    c.execute("""INSERT INTO dq_source_state(source_key,state,generation_id,source_sha256,digest,period_start,
                 period_end,observed_at) VALUES(?,?,?,?,?,?,?,?)
                 ON CONFLICT(source_key) DO UPDATE SET state=excluded.state, generation_id=excluded.generation_id,
                 source_sha256=excluded.source_sha256, digest=excluded.digest, period_start=excluded.period_start,
                 period_end=excluded.period_end, observed_at=excluded.observed_at""",
              (key, obs.state, obs.generation_id, obs.source_sha256, digest, obs.period_start, obs.period_end, now))
    return True


def _counts_json(counts: Mapping[str, int]) -> str:
    return json.dumps({k: int(v) for k, v in sorted(counts.items())}, sort_keys=True)


def _upsert(c, issue: V.Issue, row, obs: Observation, now: str) -> None:
    counts = _counts_json(issue.counts)
    labels = json.dumps(list(issue.labels), ensure_ascii=False)
    affected = json.dumps(list(issue.affected_metrics()))
    if row is None:
        c.execute("""INSERT INTO dq_issue(issue_id,issue_type,check_code,severity,source_key,locator,field_name,
                     column_letter,join_id,affected_metrics,first_seen_generation,last_seen_generation,
                     first_seen_source_sha,last_seen_source_sha,first_seen_at,last_seen_at,period_start,period_end,
                     detail_counts,detail_labels,status,presence,reviewer,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'acik','present',NULL,?,?)""",
                  (issue.issue_id, issue.issue_type, issue.check, issue.severity, issue.source_key, issue.locator,
                   issue.field_name, issue.column, issue.join, affected, obs.generation_id, obs.generation_id,
                   obs.source_sha256, obs.source_sha256, now, now, obs.period_start, obs.period_end, counts, labels,
                   now, now))
        _event(c, issue.issue_id, obs.generation_id, obs.source_sha256, "detected", "acik", counts, None, "system",
               None, now)
        return
    prev_counts = json.loads(row["detail_counts"] or "{}")
    grew = any(int(v) > int(prev_counts.get(k, 0)) for k, v in issue.counts.items())
    status, event, reason = row["status"], "still_present", None
    if status == "cozuldu_kaynakta" or row["presence"] == "absent":
        status, event, reason = "yeniden_acildi", "reopened", "returned"
    elif grew and status in ("incelendi", "kabul_edildi"):
        status, event, reason = "yeniden_acildi", "reopened", "counts_increased"
    elif row["presence"] == "source_missing":
        reason = "source_returned"
    c.execute("""UPDATE dq_issue SET severity=?, check_code=?, field_name=?, column_letter=?, join_id=?,
                 affected_metrics=?, last_seen_generation=?, last_seen_source_sha=?, last_seen_at=?, period_start=?,
                 period_end=?, detail_counts=?, detail_labels=?, status=?, presence='present', updated_at=?
                 WHERE issue_id=?""",
              (issue.severity, issue.check, issue.field_name, issue.column, issue.join, affected, obs.generation_id,
               obs.source_sha256, now, obs.period_start, obs.period_end, counts, labels, status, now, issue.issue_id))
    _event(c, issue.issue_id, obs.generation_id, obs.source_sha256, event, status, counts, reason, "system", None, now)


def _event(c, iid, generation_id, sha, event, status_after, counts, reason, actor, note, now) -> int:
    cur = c.execute("""INSERT INTO dq_issue_event(issue_id,generation_id,source_sha256,event,status_after,
                       detail_counts,reason,actor,note,at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (iid, generation_id, sha or "", event, status_after, counts, reason, actor, note, now))
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------------------------
# building observations from a published generation (services/generation.Manifest)
# ---------------------------------------------------------------------------------------------
def _period(record) -> tuple[str | None, str | None]:
    d = getattr(record, "declared", None) or {}
    start = _iso(d.get("period_start"))
    end = _iso(d.get("period_end") or d.get("report_date") or d.get("snapshot_date"))
    return start, end


def core_observations(manifest, owner_paths: Mapping[str, str | None] | None = None,
                      previously_seen: Iterable[str] = ()) -> dict[str, Observation]:
    """Observations for one accepted CORE generation: stock / sales / aging / liste (present or missing), plus the
    owner sources that are expected but cannot be loaded (not configured, or the configured file does not exist).
    Configured owner sources whose file exists are observed by their own loader (`owner_observation`)."""
    gen = manifest.core_id
    recs = {r.key: r for r in manifest.core}
    joins = manifest.joins or {}
    out: dict[str, Observation] = {}
    for key in ("stock", "sales", "aging"):
        r = recs.get(key)
        if r is None:
            out[key] = Observation("missing", gen)
            continue
        ps, pe = _period(r)
        out[key] = Observation("present", gen, r.sha256, tuple(V.detect_core(r, joins)), ps, pe)
    if "sales" in recs:
        r = recs.get("liste")
        if r is None or r.evidence is None or r.evidence.available is False:
            out["liste"] = Observation("missing", gen, recs["sales"].sha256)
        else:
            ps, pe = _period(r)
            out["liste"] = Observation("present", gen, r.sha256, tuple(V.detect_core(r, joins)), ps, pe)
    seen = set(previously_seen)
    for key in V.OWNER_SOURCES:
        path = (owner_paths or {}).get(key)
        if path and os.path.isfile(path):
            continue                                   # its own loader observes it (lazy, on first Yönetim open)
        if path or key in V.EXPECTED_ALWAYS or key in seen:
            out[key] = Observation("missing", gen)
    return out


def owner_observation(manifest, key: str, record) -> Observation:
    gen = manifest.generation_id if manifest is not None else "none"
    if record is None:
        return Observation("missing", gen)
    ps, pe = _period(record)
    return Observation("present", gen, record.sha256, tuple(V.detect_owner(record)), ps, pe)


def previously_seen_sources() -> set[str]:
    with ST._conn() as c:
        return {r[0] for r in c.execute("SELECT source_key FROM dq_source_state")}


def make_observer(owner_paths: Callable[[], Mapping[str, str | None]]):
    """The `services/generation.GenerationStore` observer (registered by app.py). Never raises; logs no content.

    core  → observe the accepted core generation, unless another core generation is current by now (its own
            notification follows);
    owner → observe that owner source's record, unless the store already serves a different record for it."""
    def _observer(kind: str, manifest, key: str | None, store) -> None:
        try:
            def current():
                return store.current() if store is not None else manifest
            if kind == "core":
                if manifest is None or not manifest.core:
                    return
                obs = core_observations(manifest, owner_paths(), previously_seen_sources())
                # checked again under the write lock: a newer generation published meanwhile observes itself
                observe(obs, guard=lambda: (current() is not None and current().core_id == manifest.core_id))
            elif kind == "owner" and key in V.OWNER_SOURCES:
                rec = manifest.record(key) if manifest is not None else None

                def same_record():
                    cur = current()
                    cur_rec = cur.record(key) if cur is not None else None
                    return (rec.sha256 if rec else None) == (cur_rec.sha256 if cur_rec else None)
                observe({key: owner_observation(manifest, key, rec)}, guard=same_record)
        except Exception as e:                             # publication must never fail because of the review log
            log.warning("veri kontrol gözlemi yapılamadı (%s)", type(e).__name__)
    return _observer


# ---------------------------------------------------------------------------------------------
# the observation token a review must match (stale-generation check)
# ---------------------------------------------------------------------------------------------
def vkm_generation(c=None) -> str:
    def run(conn):
        rows = conn.execute("SELECT source_key, state, source_sha256, digest FROM dq_source_state "
                            "ORDER BY source_key").fetchall()
        body = json.dumps([list(r) for r in rows])
        return "vkm-" + hashlib.sha256(body.encode()).hexdigest()[:20]
    if c is not None:
        return run(c)
    with ST._conn() as conn:
        return run(conn)


# ---------------------------------------------------------------------------------------------
# owner review
# ---------------------------------------------------------------------------------------------
REVIEW_EVENTS = {"incelendi": "reviewed", "kabul_edildi": "acknowledged", "acik": "reopened"}
ALLOWED_FROM = {"incelendi": ("acik", "yeniden_acildi", "kabul_edildi"),
                "kabul_edildi": ("acik", "yeniden_acildi", "incelendi"),
                "acik": ("incelendi", "kabul_edildi")}
ACTION_TR = {"incelendi": "İncelendi olarak işaretle", "kabul_edildi": "Kabul et (bilinen durum)",
             "acik": "Yeniden aç"}


def clean_note(note: str | None, *, required: bool = False) -> str | None:
    if note is None:
        if required:
            raise VKMError(422, "note_empty", NOTE_EMPTY_TR)
        return None
    text = _CTRL.sub("", str(note)).strip()
    if not text:
        if required:
            raise VKMError(422, "note_empty", NOTE_EMPTY_TR)
        return None
    if len(text) > NOTE_MAX:
        raise VKMError(422, "note_too_long", NOTE_LONG_TR)
    return text


def allowed_actions(status: str) -> list[str]:
    return [a for a, frm in ALLOWED_FROM.items() if status in frm]


def _check_generation(c, presented: str) -> None:
    if not isinstance(presented, str) or presented != vkm_generation(c):
        raise VKMError(409, "stale_generation", STALE_TR)


def review(issue_id: str, action: str, *, vkm_generation_presented: str, actor: str,
           note: str | None = None) -> dict:
    """An owner decision. Changes ONLY the issue's review status and appends one history event."""
    if action not in REVIEW_EVENTS:
        raise VKMError(422, "bad_action", TRANSITION_TR)
    text = clean_note(note)
    now = _now()
    with _LOCK, ST._conn() as c:
        _check_generation(c, vkm_generation_presented)
        r = c.execute("SELECT * FROM dq_issue WHERE issue_id=?", (issue_id,)).fetchone()
        if r is None:
            raise VKMError(404, "not_found", NOT_FOUND_TR)
        if r["status"] not in ALLOWED_FROM[action]:
            raise VKMError(409, "transition_not_allowed", TRANSITION_TR)
        c.execute("UPDATE dq_issue SET status=?, reviewer=?, updated_at=? WHERE issue_id=?",
                  (action, actor, now, issue_id))
        _event(c, issue_id, r["last_seen_generation"], r["last_seen_source_sha"], REVIEW_EVENTS[action], action,
               None, "owner" if action == "acik" else None, actor, text, now)
    return issue_detail(issue_id)


def add_note(issue_id: str, note: str, *, vkm_generation_presented: str, actor: str) -> dict:
    text = clean_note(note, required=True)
    now = _now()
    with _LOCK, ST._conn() as c:
        _check_generation(c, vkm_generation_presented)
        r = c.execute("SELECT * FROM dq_issue WHERE issue_id=?", (issue_id,)).fetchone()
        if r is None:
            raise VKMError(404, "not_found", NOT_FOUND_TR)
        _event(c, issue_id, r["last_seen_generation"], r["last_seen_source_sha"], "note_added", r["status"], None,
               None, actor, text, now)
    return issue_detail(issue_id)


# ---------------------------------------------------------------------------------------------
# read models
# ---------------------------------------------------------------------------------------------
ACTOR_TR = {"system": "Sistem", "owner": "Yönetici"}


def _short(x: str | None, n: int = 12) -> str | None:
    return x[:n] if x else None


def _counts_view(raw: str | None) -> list[dict]:
    d = json.loads(raw or "{}")
    return [{"key": k, "label": V.COUNT_TR.get(k, k), "value": int(v)} for k, v in sorted(d.items())]


def _location(r) -> str:
    parts = []
    if r["column_letter"]:
        parts.append(f"Sütun {r['column_letter']}")
    if r["field_name"]:
        parts.append(f"Alan: {str(r['field_name']).strip()}")
    if r["join_id"]:
        from services import source_validation as SV
        parts.append(f"İlişki: {SV.JOINS.get(r['join_id'], {}).get('title_tr', r['join_id'])}")
    return " · ".join(parts) or "Dosyanın tamamı"


def _row_view(r, extra: Mapping[str, Any] | None = None) -> dict:
    t = V.texts(r["check_code"], r["source_key"], r["field_name"], r["column_letter"])
    return {"issue_id": r["issue_id"], "issue_type": r["issue_type"],
            "type_label": V.TYPES.get(r["issue_type"], {}).get("label", r["issue_type"]),
            "check": r["check_code"], "title": t["title"], "severity": r["severity"],
            "severity_label": V.SEVERITY_TR.get(r["severity"], r["severity"]),
            "source_key": r["source_key"], "source_label": V.SOURCE_TR.get(r["source_key"], r["source_key"]),
            "location": _location(r), "column": r["column_letter"],
            "affected_metrics": V.metric_titles(json.loads(r["affected_metrics"] or "[]")),
            "first_seen_at": r["first_seen_at"], "last_seen_at": r["last_seen_at"],
            "first_seen_generation": _short(r["first_seen_generation"]),
            "last_seen_generation": _short(r["last_seen_generation"]),
            "period_start": r["period_start"], "period_end": r["period_end"],
            "presence": r["presence"], "presence_label": V.PRESENCE_TR.get(r["presence"], r["presence"]),
            "status": r["status"], "status_label": V.STATUS_TR.get(r["status"], r["status"]),
            "counts": _counts_view(r["detail_counts"]), **(extra or {})}


def _sort_key(r):
    resolved = r["status"] == "cozuldu_kaynakta"
    return (resolved, SEVERITY_ORDER.get(r["severity"], 9), {"present": 0, "source_missing": 1}.get(r["presence"], 2),
            r["source_key"], r["issue_id"])


def _filter_rows(rows, *, status: str = "open", severity: str | None = None, issue_type: str | None = None,
                 source: str | None = None, presence: str | None = None):
    out = []
    for r in rows:
        if status == "open" and r["status"] not in V.OPEN_STATUSES:
            continue
        if status not in ("open", "all") and r["status"] != status:
            continue
        if severity and r["severity"] != severity:
            continue
        if issue_type and r["issue_type"] != issue_type:
            continue
        if source and r["source_key"] != source:
            continue
        if presence and r["presence"] != presence:
            continue
        out.append(r)
    return sorted(out, key=_sort_key)


def catalogue() -> dict:
    return {"statuses": [{"key": k, "label": V.STATUS_TR[k]} for k in V.STATUSES],
            "severities": [{"key": k, "label": V.SEVERITY_TR[k]} for k in V.SEVERITIES],
            "types": [{"key": k, "label": v["label"]} for k, v in V.TYPES.items()],
            "sources": [{"key": k, "label": V.SOURCE_TR[k]} for k in (*V.CORE_SOURCES, *V.OWNER_SOURCES)],
            "presence": [{"key": k, "label": V.PRESENCE_TR[k]} for k in V.PRESENCE]}


def _tally(rows) -> dict:
    open_rows = [r for r in rows if r["status"] in V.OPEN_STATUSES]
    return {"open_total": len(open_rows), "total": len(rows),
            "by_severity": {s: sum(1 for r in open_rows if r["severity"] == s) for s in V.SEVERITIES},
            "by_status": {s: sum(1 for r in rows if r["status"] == s) for s in V.STATUSES},
            "by_type": [{"key": k, "label": v["label"], "open": sum(1 for r in open_rows if r["issue_type"] == k)}
                        for k, v in V.TYPES.items()],
            "present_in_current_upload": sum(1 for r in open_rows if r["presence"] == "present"),
            "unknown_source_missing": sum(1 for r in open_rows if r["presence"] == "source_missing")}


def public_summary() -> dict:
    """D-062 viewer-level summary (public viewer and financial viewer): counts of issues by severity / status / type,
    whether anything was observed and the date of the last observation. No issue ID, title detail, source, locator,
    count of affected records, file name, fingerprint, note or reviewer."""
    with ST._conn() as c:
        rows = c.execute("SELECT issue_type, severity, status, presence FROM dq_issue").fetchall()
        last = c.execute("SELECT MAX(observed_at) FROM dq_source_state").fetchone()[0]
    t = _tally(rows)
    return {"observed": last is not None, "last_observed_on": last[:10] if last else None,
            "open_total": t["open_total"], "by_severity": t["by_severity"], "by_status": t["by_status"],
            "by_type": [x for x in t["by_type"] if x["open"]],
            "notice": V.NOT_APPROVAL_TR, "detail_requires_owner": True}


def list_issues(*, status: str = "open", severity: str | None = None, issue_type: str | None = None,
                source: str | None = None, presence: str | None = None) -> dict:
    with ST._conn() as c:
        rows = c.execute("SELECT * FROM dq_issue").fetchall()
        notes = {r[0]: r[1] for r in c.execute(
            "SELECT issue_id, COUNT(*) FROM dq_issue_event WHERE note IS NOT NULL GROUP BY issue_id")}
        last_review = {}
        for r in c.execute("""SELECT issue_id, event, at FROM dq_issue_event
                              WHERE event IN ('reviewed','acknowledged','reopened') AND actor <> 'system'
                              ORDER BY event_id"""):
            last_review[r[0]] = {"event": r[1], "event_label": V.EVENT_TR.get(r[1], r[1]), "at": r[2]}
        states = c.execute("SELECT * FROM dq_source_state ORDER BY source_key").fetchall()
        gen = vkm_generation(c)
    picked = _filter_rows(rows, status=status, severity=severity, issue_type=issue_type, source=source,
                          presence=presence)
    return {"vkm_generation": gen, "notice": V.NOT_APPROVAL_TR, "counts": _tally(rows),
            "sources": [{"key": s["source_key"], "label": V.SOURCE_TR.get(s["source_key"], s["source_key"]),
                         "state": s["state"], "state_label": "Yüklendi" if s["state"] == "present" else "Yüklenmedi",
                         "observed_at": s["observed_at"], "generation": _short(s["generation_id"]),
                         "fingerprint": _short(s["source_sha256"]), "period_start": s["period_start"],
                         "period_end": s["period_end"]} for s in states],
            "filters": {"status": status, "severity": severity, "type": issue_type, "source": source,
                        "presence": presence},
            "issues": [_row_view(r, {"notes": int(notes.get(r["issue_id"], 0)),
                                     "last_review": last_review.get(r["issue_id"])}) for r in picked],
            "catalogue": catalogue()}


def issue_detail(issue_id: str) -> dict:
    with ST._conn() as c:
        r = c.execute("SELECT * FROM dq_issue WHERE issue_id=?", (issue_id,)).fetchone()
        if r is None:
            raise VKMError(404, "not_found", NOT_FOUND_TR)
        events = c.execute("SELECT * FROM dq_issue_event WHERE issue_id=? ORDER BY event_id DESC",
                           (issue_id,)).fetchall()
        gen = vkm_generation(c)
    t = V.texts(r["check_code"], r["source_key"], r["field_name"], r["column_letter"])
    hist = [{"event_id": e["event_id"], "at": e["at"], "event": e["event"], "event_label": V.EVENT_TR.get(e["event"], e["event"]),
             "actor": e["actor"], "actor_label": ACTOR_TR.get(e["actor"] or "", e["actor"] or "—"),
             "status_after": e["status_after"], "status_after_label": V.STATUS_TR.get(e["status_after"] or "", None),
             "reason": e["reason"], "reason_label": V.REASON_TR.get(e["reason"] or "", None),
             "counts": _counts_view(e["detail_counts"]) if e["detail_counts"] else [],
             "generation": _short(e["generation_id"]), "note": e["note"]} for e in events]
    return {"vkm_generation": gen, "notice": V.NOT_APPROVAL_TR,
            "issue": _row_view(r, {"explanation": t["explain"], "action": t["action"],
                                   "labels": json.loads(r["detail_labels"] or "[]"),
                                   "fingerprint_first": _short(r["first_seen_source_sha"]),
                                   "fingerprint_last": _short(r["last_seen_source_sha"]),
                                   "allowed_actions": [{"key": a, "label": ACTION_TR[a]}
                                                       for a in allowed_actions(r["status"])],
                                   "notes": sum(1 for e in events if e["note"] is not None)}),
            "history": hist}


__all__ = ["init_tables", "observe", "Observation", "core_observations", "owner_observation", "make_observer",
           "vkm_generation", "review", "add_note", "public_summary", "list_issues", "issue_detail", "VKMError",
           "clean_note", "allowed_actions", "NOTE_MAX", "previously_seen_sources"]
