"""M8-CP3 — Excel Upload Center (docs/m8_cp3_upload_center.md, decision D-065).

Owner-only workflow inside the existing website:

    select Excel files → declare report details → validate (background job) → preview → explicit publication

There is NO connection of any kind to DinamikCRM: the owner exports the reports there and uploads the files here.

Rules (all enforced in code):
- The report type of every file is chosen by the owner; it is never read from the file name. Dates are DECLARED by
  the owner or recorded as unknown — never taken from a modification time or the latest transaction. Exception: the
  Cari report date is the date in its original file name (D-072 D1; the owner rule covers all exports, implemented
  for Cari only so far — Stok / Satış are a separate checkpoint).
  Stored files get content-addressed names (services/upload_files.py), so no reader can infer anything from a name.
- Validation reuses the deterministic pipeline unchanged: the controlled refresh (`services/refresh.run_refresh`) in
  stage-only mode for stock / sales / Cari (validate → build → verify into a private preview, nothing swapped or
  installed), the M6 owner importers through `OwnerRepository.prepare` for Kredi / Gider-Gelir / Bütçe, the Veri
  Kontrol detectors (`services/vkm_store.preview_delta`, read-only) and the accuracy gate. No calculation is duplicated
  and no AI is involved.
- The preview changes nothing live: no dashboard, context, manifest, owner view, selection file or VKM history.
- Publication is explicit and reuses the generation-bound publication: the refresh pipeline with the batch as its
  explicit selection and the previewed `core_id` as the required identity (anything else is refused before any swap);
  the selection files are written inside the swap step and restored if it fails; uploaded owner workbooks are
  installed in the same publish-lock step. A failure keeps the previous dashboard, manifest, context and selection.
- A preview can be published only while it is the newest batch and nothing was published since it was built; an
  identical set (same bytes, same declarations) is reported as "no change" and publishes nothing.
- Nothing here touches the accuracy gate, a metric's verification or `GENERATION_BOUND_API_EVIDENCE`; no approval,
  mapping, source authority, business definition, reporting date or Kredi DURUM meaning is ever inferred.

Persistence: the existing private SQLite file (append-only `upload_event` history enforced by triggers; batches and
jobs as current state) and the private upload folder (`UPLOAD_DIR`, default `<data>/uploads`). Payloads carry file
labels, fingerprint prefixes, schema labels, codes and counts — never workbook values, names or amounts.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from xml.etree import ElementTree as ET

from services import generation as GEN
from services import request_pin as RP
from services import store as ST
from services import upload_files as UF

log = logging.getLogger("upload")

# ---------------------------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------------------------
CORE_SLOTS = ("stock", "sales", "aging")
OWNER_SLOTS = ("kredi", "gider_gelir", "butce")
SLOTS: dict[str, dict[str, Any]] = {
    "stock": {"label": "Stok", "role": "core", "fields": ("snapshot_date",),
              "hint": "DinamikCRM stok raporu (başlık 4. satırda)."},
    "sales": {"label": "Satış", "role": "core", "fields": ("report_date", "period_start", "period_end",
                                                          "period_complete"),
              "hint": "Ürün ve müşteri bazlı satış raporu ('liste' sayfasıyla)."},
    "aging": {"label": "Cari", "role": "core", "fields": ("snapshot_date",),
              "hint": ("Cari yaşlandırma raporu. Rapor tarihi dosya adındaki tarihtir (ör. 'Cari 5 Ekim 2026'); "
                       "dosya adında tarih yoksa yükleme tarihi kullanılır (D-072). Tarih alanını boş bırakın.")},
    "kredi": {"label": "Kredi", "role": "owner", "fields": ("snapshot_date",),
              "hint": "Kredi takip / kredi sözleşmeleri dosyası."},
    "gider_gelir": {"label": "Gider/Gelir", "role": "owner", "fields": ("report_year",),
                    "hint": "Gider / gelir raporu. Rapor yılı beyan edilmeden okunamaz."},
    "butce": {"label": "Bütçe", "role": "owner", "fields": ("planning_year", "snapshot_date"),
              "hint": "Bütçe dosyası. Plan yılı beyan edilmeden okunamaz."},
}
FIELD_LABELS = {"snapshot_date": "Rapor (anlık) tarihi", "report_date": "Rapor tarihi",
                "period_start": "Dönem başlangıcı", "period_end": "Dönem sonu",
                "period_complete": "Dönem tamamlandı mı?", "report_year": "Rapor yılı", "planning_year": "Plan yılı"}
DATE_FIELDS = ("snapshot_date", "report_date", "period_start", "period_end")
YEAR_FIELDS = ("report_year", "planning_year")
MAX_FILES = 6

OPEN_STATUSES = ("draft", "validating", "ready", "invalid", "publishing")
FINAL_STATUSES = ("published", "unchanged", "superseded", "discarded")
STATUS_TR = {"draft": "Hazırlanıyor", "validating": "Doğrulanıyor", "ready": "Önizleme hazır — yayın bekliyor",
             "invalid": "Doğrulama sorunları var — yayınlanamaz", "publishing": "Yayınlanıyor",
             "published": "Yayınlandı", "unchanged": "Değişiklik yoktu — yayın yapılmadı",
             "publish_failed": "Yayın başarısız — önceki veriler korunuyor", "stale": "Önizleme güncelliğini yitirdi",
             "superseded": "Daha yeni bir yükleme başlatıldı", "discarded": "Vazgeçildi",
             "interrupted": "Sunucu yeniden başladığı için işlem yarıda kaldı"}
JOB_STATES = ("queued", "running", "done", "failed", "interrupted")
STAGE_TR = {"queued": "Sırada bekliyor", "checking": "Dosyalar ve beyanlar kontrol ediliyor",
            "core_validate": "Stok, satış ve cari raporları okunuyor ve doğrulanıyor",
            "core_build": "Pano önizlemesi hazırlanıyor (büyük satış dosyasında birkaç dakika sürebilir)",
            "owner": "Kredi, Gider/Gelir ve Bütçe dosyaları okunuyor",
            "compare": "Veri Kontrol ve pano etkileri karşılaştırılıyor",
            "copy": "Dosyalar yayın klasörüne kopyalanıyor", "publish_core": "Pano yayınlanıyor",
            "publish_owner": "Yönetim kaynakları yayınlanıyor", "done": "Tamamlandı", "failed": "Başarısız",
            "interrupted": "Yarıda kaldı"}
VALIDATE_STAGES = ("checking", "core_validate", "core_build", "owner", "compare")
PUBLISH_STAGES = ("checking", "copy", "core_validate", "core_build", "publish_core", "publish_owner")

MSG = {
    "no_files": "Önce en az bir Excel dosyası seçin.",
    "too_many_files": f"Bir yüklemede en fazla {MAX_FILES} dosya olabilir (her rapor türü için bir dosya).",
    "slot_missing": "Her dosya için rapor türünü seçin.",
    "slot_unknown": "Bilinmeyen rapor türü.",
    "slot_twice": "Aynı rapor türü için iki dosya seçildi; her rapor türü için bir dosya yükleyin.",
    "same_file_twice": "Aynı dosya iki farklı rapor türü olarak seçildi.",
    "bad_date": "Tarih YYYY-AA-GG biçiminde olmalı (veya boş bırakılmalı).",
    "bad_year": "Yıl 2000–2099 arasında dört haneli olmalı (veya boş bırakılmalı).",
    "bad_period": "Dönem başlangıcı dönem sonundan sonra olamaz.",
    "field_not_allowed": "Bu rapor türü için bu alan kullanılmaz.",
    "batch_missing": "Yükleme bulunamadı.",
    "batch_closed": "Bu yükleme artık değiştirilemez; yeni bir yükleme başlatın.",
    "batch_busy": "Bu yükleme şu anda işleniyor; bitmesini bekleyin.",
    "not_latest": "Bu önizleme artık geçerli değil: daha yeni bir yükleme başlatıldı.",
    "not_ready": "Yayınlanabilecek bir önizleme yok; önce dosyaları doğrulayın.",
    "token": "Önizleme onayı eşleşmedi; sayfayı yenileyip önizlemeyi yeniden kontrol edin.",
    "stale": ("Bu önizleme artık güncel değil: önizleme hazırlandıktan sonra veriler yeniden yayımlandı veya kaynak "
              "seçimi değişti. Hiçbir şey yayınlanmadı; dosyaları yeniden doğrulayın."),
    "job_running": "Başka bir doğrulama veya yayın işlemi sürüyor; bitmesini bekleyin.",
    "file_missing": "Yüklenen dosya sunucuda bulunamadı veya değişmiş; dosyayı yeniden yükleyin.",
    "core_required": ("Stok ve Satış raporu zorunludur. Şu anda yayında açıkça seçilmiş (beyanlı) bir kaynak seti "
                      "olmadığı için eksik raporlar devralınamaz; Stok ve Satış raporlarını birlikte yükleyin."),
    "carried_changed": ("Yayındaki {label} raporu diskte değişmiş veya bulunamadı; devralınamaz. Bu raporu da "
                        "yükleyin."),
    "internal": "Beklenmeyen bir hata oluştu; hiçbir şey yayınlanmadı. Tekrar deneyin.",
    "cari_date_conflict": ("Cari raporunun tarihi dosya adındaki tarihtir (D-072); girilen tarih bununla çelişiyor. "
                           "Tarih alanını boş bırakın veya dosya adını düzeltin."),
    "cari_date_ambiguous": ("Cari dosyasının adındaki tarih okunamadı (geçersiz veya birden fazla farklı tarih). "
                            "Dosyayı tek bir rapor tarihiyle yeniden adlandırıp yükleyin."),
}
# importer error codes the owner can act on (anything else is shown as its code under a fixed sentence)
IMPORT_CODE_TR = {
    "report_year_unknown": "Rapor yılı beyan edilmedi; Gider/Gelir dosyası rapor yılı olmadan okunamaz.",
    "planning_year_unknown": "Plan yılı beyan edilmedi; Bütçe dosyası plan yılı olmadan okunamaz.",
    "sheet_missing": "Beklenen sayfa dosyada yok (yanlış rapor türü veya farklı yıl olabilir).",
    "layout_unrecognized": "Dosyanın düzeni bu rapor türünün desteklenen şablonlarıyla uyuşmuyor.",
    "missing_required_column": "Zorunlu sütunlar eksik (yanlış rapor türü seçilmiş olabilir).",
}


class UploadError(Exception):
    """A refused request: HTTP status, stable code, fixed Turkish message (never echoes input)."""

    def __init__(self, status: int, code: str, message: str | None = None, **detail):
        super().__init__(code)
        self.status, self.code, self.message, self.detail = status, code, message or MSG.get(code, code), detail


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------
# storage (private SQLite, CREATE IF NOT EXISTS only)
# ---------------------------------------------------------------------------------------------
def init_tables(c) -> None:
    c.execute("""CREATE TABLE IF NOT EXISTS upload_batch(
        batch_id TEXT PRIMARY KEY, seq INTEGER NOT NULL UNIQUE, status TEXT NOT NULL, created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, actor TEXT, files TEXT NOT NULL, assignments TEXT NOT NULL, digest TEXT,
        preview TEXT, preview_token TEXT, base TEXT, candidate TEXT, message TEXT, published_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS upload_event(
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL, event TEXT NOT NULL, at TEXT NOT NULL,
        actor TEXT, detail TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS upload_job(
        job_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, stage TEXT,
        created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error_code TEXT, message TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS upload_event_batch ON upload_event(batch_id, event_id)")
    for op in ("UPDATE", "DELETE"):
        c.execute(f"""CREATE TRIGGER IF NOT EXISTS upload_event_no_{op.lower()} BEFORE {op} ON upload_event
                      BEGIN SELECT RAISE(ABORT, 'upload_event is append-only'); END""")


def _event(c, batch_id: str, event: str, actor: str | None, **detail) -> None:
    c.execute("INSERT INTO upload_event(batch_id,event,at,actor,detail) VALUES(?,?,?,?,?)",
              (batch_id, event, _now(), actor, json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)))


# ---------------------------------------------------------------------------------------------
# declarations (owner input; validated, never completed by inference)
# ---------------------------------------------------------------------------------------------
def _parse_date(v):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise UploadError(422, "bad_date")
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise UploadError(422, "bad_date")


def _parse_year(v):
    if v in (None, ""):
        return None
    s = str(v)
    if not re.fullmatch(r"20\d{2}", s):
        raise UploadError(422, "bad_year")
    return int(s)


def clean_declarations(slot: str, raw: Mapping[str, Any] | None) -> dict:
    """The owner's declarations for one file, validated. Unknown → None (recorded as unknown, never guessed)."""
    raw = dict(raw or {})
    allowed = SLOTS[slot]["fields"]
    for k, v in raw.items():
        if k not in FIELD_LABELS:
            raise UploadError(422, "field_not_allowed")
        if k not in allowed and v not in (None, ""):
            raise UploadError(422, "field_not_allowed")
    out: dict[str, Any] = {}
    for f in allowed:
        v = raw.get(f)
        if f in DATE_FIELDS:
            out[f] = _parse_date(v)
        elif f in YEAR_FIELDS:
            out[f] = _parse_year(v)
        elif f == "period_complete":
            if v not in (None, "", True, False):
                raise UploadError(422, "bad_period")
            out[f] = None if v == "" else v
    if out.get("period_start") and out.get("period_end") and out["period_start"] > out["period_end"]:
        raise UploadError(422, "bad_period")
    return out


def _decl_json(d: Mapping[str, Any]) -> dict:
    return {k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in d.items()}


def _decl_from_json(slot: str, d: Mapping[str, Any]) -> dict:
    return clean_declarations(slot, {k: (str(v) if k in YEAR_FIELDS and v is not None else v) for k, v in d.items()})


def cari_date(f: Mapping[str, Any]) -> dict:
    """D-072 D1 for one uploaded Cari file: the date in its ORIGINAL file name (the sanitized label the browser sent),
    else the date it was uploaded; the provenance is recorded. A current-state snapshot of the authoritative DinamikCRM
    report (D-072 D3) is complete as of that date (date contradictions still make it incomplete in the evidence)."""
    from services import cari_policy as CARI
    up = f.get("uploaded_on")
    upload_date = dt.date.fromisoformat(up) if up else dt.date.today()
    try:
        day, origin = CARI.report_date(f.get("label"), upload_date)
    except CARI.AmbiguousFilenameDate:
        raise UploadError(422, "cari_date_ambiguous", file_id=f.get("file_id"))
    return {"snapshot_date": day, "date_origin": origin, "date_source": f.get("label") if origin == "filename" else None,
            "period_complete": True}


def unknown_fields(slot: str, decl: Mapping[str, Any]) -> list[str]:
    return [FIELD_LABELS[f] for f in SLOTS[slot]["fields"] if decl.get(f) is None]


# ---------------------------------------------------------------------------------------------
# dependencies supplied by the application (kept explicit so the service can be tested in isolation)
# ---------------------------------------------------------------------------------------------
@dataclass
class Deps:
    data_folder: str
    dashboard_path: str
    upload_root: str
    generations: Callable[[], Any]            # → GenerationStore
    context_repo: Callable[[], Any]           # → ContextRepository
    owner_repo: Callable[[], Any]             # → OwnerRepository
    install: Callable[[Any], None]            # installs a verified staged context + its generation (app)
    live_gate: Callable[[], Any]              # the gate bound to the SERVED generation (app._build_gate)
    inflation_rate: float | None = None


# ---------------------------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------------------------
class UploadCenter:
    def __init__(self, deps: Deps):
        self.deps = deps
        self.blobs = UF.BlobStore(deps.upload_root)
        self._lock = threading.Lock()           # batch state transitions
        self._job_lock = threading.Lock()
        self._active_job: str | None = None
        self._threads: list[threading.Thread] = []

    # -- startup -------------------------------------------------------------------------------------------
    def recover(self) -> None:
        """A job that was queued or running when the process stopped is interrupted; its batch must be validated
        again. The live data is consistent either way (publication is atomic and restores on failure)."""
        with ST._conn() as c:
            jobs = c.execute("SELECT job_id, batch_id, kind FROM upload_job WHERE state IN ('queued','running')").fetchall()
            for j in jobs:
                c.execute("""UPDATE upload_job SET state='interrupted', stage='interrupted', finished_at=?,
                             error_code='interrupted', message=? WHERE job_id=?""",
                          (_now(), STATUS_TR["interrupted"], j["job_id"]))
                c.execute("UPDATE upload_batch SET status='interrupted', updated_at=?, message=? WHERE batch_id=? "
                          "AND status IN ('validating','publishing')", (_now(), STATUS_TR["interrupted"], j["batch_id"]))
                _event(c, j["batch_id"], "interrupted", "system", kind=j["kind"])

    # -- batches -------------------------------------------------------------------------------------------
    def _row(self, c, batch_id: str):
        if not re.fullmatch(r"b-[0-9a-f]{20}", batch_id or ""):
            raise UploadError(404, "batch_missing")
        r = c.execute("SELECT * FROM upload_batch WHERE batch_id=?", (batch_id,)).fetchone()
        if r is None:
            raise UploadError(404, "batch_missing")
        return r

    def create_batch(self, actor: str) -> dict:
        with self._lock, ST._conn() as c:
            if self._active_job is not None:
                raise UploadError(409, "job_running")
            seq = (c.execute("SELECT COALESCE(MAX(seq),0) FROM upload_batch").fetchone()[0] or 0) + 1
            bid = "b-" + secrets.token_hex(10)
            for r in c.execute("SELECT batch_id FROM upload_batch WHERE status IN ('draft','ready','invalid',"
                               "'interrupted','stale','publish_failed')").fetchall():
                c.execute("UPDATE upload_batch SET status='superseded', updated_at=? WHERE batch_id=?", (_now(), r[0]))
                _event(c, r[0], "superseded", actor, by=bid)
            c.execute("""INSERT INTO upload_batch(batch_id,seq,status,created_at,updated_at,actor,files,assignments)
                         VALUES(?,?,'draft',?,?,?,'[]','{}')""", (bid, seq, _now(), _now(), actor))
            _event(c, bid, "created", actor)
            return self._view(c, self._row(c, bid))

    def check_open(self, batch_id: str) -> None:
        """Cheap pre-check before any byte of an upload is read: the batch exists and accepts files."""
        with ST._conn() as c:
            r = self._row(c, batch_id)
            if r["status"] not in ("draft", "ready", "invalid"):
                raise UploadError(409, "batch_closed")
            if len(json.loads(r["files"])) >= MAX_FILES:
                raise UploadError(422, "too_many_files")

    def add_file(self, batch_id: str, chunks, *, length: int | None, label: str | None, actor: str) -> dict:
        # validate the batch first (cheap), receive outside the lock (streaming), then attach under the lock
        with ST._conn() as c:
            r = self._row(c, batch_id)
            if r["status"] not in ("draft", "ready", "invalid"):
                raise UploadError(409, "batch_closed")
            if len(json.loads(r["files"])) >= MAX_FILES:
                raise UploadError(422, "too_many_files")
        stored = self.blobs.receive(chunks, declared_length=length)       # raises UF.UploadRejected
        with self._lock, ST._conn() as c:
            r = self._row(c, batch_id)
            if r["status"] not in ("draft", "ready", "invalid"):
                raise UploadError(409, "batch_closed")
            files = json.loads(r["files"])
            if len(files) >= MAX_FILES:
                raise UploadError(422, "too_many_files")
            dup = next((f for f in files if f["sha256"] == stored.sha256), None)
            if dup is None:
                fid = "f-" + secrets.token_hex(6)
                files.append({"file_id": fid, "sha256": stored.sha256, "size": stored.size, "name": stored.name,
                              "label": UF.clean_label(label), "parts": stored.parts,
                              "uploaded_on": dt.date.today().isoformat()})
            else:
                fid = dup["file_id"]                       # the same content twice in one batch is one file
            c.execute("""UPDATE upload_batch SET files=?, status='draft', preview=NULL, preview_token=NULL,
                         candidate=NULL, base=NULL, message=NULL, updated_at=? WHERE batch_id=?""",
                      (json.dumps(files), _now(), batch_id))
            _event(c, batch_id, "file_added" if dup is None else "file_repeated", actor, file_id=fid,
                   sha256=stored.sha256[:12], size=stored.size, stored_before=stored.existed)
            return {"file": next(f for f in self._files_view(files) if f["file_id"] == fid),
                    "duplicate_in_batch": dup is not None, "already_stored": stored.existed,
                    "batch": self._view(c, self._row(c, batch_id))}

    def remove_file(self, batch_id: str, file_id: str, actor: str) -> dict:
        with self._lock, ST._conn() as c:
            r = self._row(c, batch_id)
            if r["status"] not in ("draft", "ready", "invalid"):
                raise UploadError(409, "batch_closed")
            files = [f for f in json.loads(r["files"]) if f["file_id"] != file_id]
            assignments = {k: v for k, v in json.loads(r["assignments"]).items() if k != file_id}
            c.execute("""UPDATE upload_batch SET files=?, assignments=?, status='draft', preview=NULL,
                         preview_token=NULL, candidate=NULL, base=NULL, message=NULL, updated_at=? WHERE batch_id=?""",
                      (json.dumps(files), json.dumps(assignments), _now(), batch_id))
            _event(c, batch_id, "file_removed", actor, file_id=file_id)
            return self._view(c, self._row(c, batch_id))

    def discard(self, batch_id: str, actor: str) -> dict:
        with self._lock, ST._conn() as c:
            r = self._row(c, batch_id)
            if r["status"] in ("validating", "publishing"):
                raise UploadError(409, "batch_busy")
            if r["status"] in FINAL_STATUSES:
                raise UploadError(409, "batch_closed")
            c.execute("UPDATE upload_batch SET status='discarded', updated_at=? WHERE batch_id=?", (_now(), batch_id))
            _event(c, batch_id, "discarded", actor)
            return self._view(c, self._row(c, batch_id))

    # -- validation ------------------------------------------------------------------------------------------
    def _assignments(self, files: list[dict], raw: Mapping[str, Any]) -> dict:
        if not files:
            raise UploadError(422, "no_files")
        out, used = {}, {}
        for f in files:
            a = (raw or {}).get(f["file_id"])
            if not isinstance(a, Mapping) or not a.get("slot"):
                raise UploadError(422, "slot_missing", file_id=f["file_id"])
            slot = a["slot"]
            if slot not in SLOTS:
                raise UploadError(422, "slot_unknown", file_id=f["file_id"])
            if slot in used:
                raise UploadError(422, "slot_twice", slot=slot)
            used[slot] = f["file_id"]
            decl = a.get("declarations") or {}
            if not isinstance(decl, Mapping):
                raise UploadError(422, "bad_date", file_id=f["file_id"])
            clean = clean_declarations(slot, decl)
            entry = {"slot": slot, "declarations": _decl_json(clean)}
            if slot == "aging":
                derived = cari_date(f)
                if clean.get("snapshot_date") not in (None, derived["snapshot_date"]):
                    raise UploadError(422, "cari_date_conflict", file_id=f["file_id"])
                entry["declarations"]["snapshot_date"] = derived["snapshot_date"].isoformat()
                entry["derived"] = _decl_json(derived)
            out[f["file_id"]] = entry
        if len({f["sha256"] for f in files}) != len(files):
            raise UploadError(422, "same_file_twice")
        return out

    def start_validation(self, batch_id: str, assignments: Mapping[str, Any], actor: str) -> dict:
        with self._lock, ST._conn() as c:
            r = self._row(c, batch_id)
            if r["status"] not in ("draft", "ready", "invalid"):
                raise UploadError(409, "batch_closed" if r["status"] not in ("validating", "publishing")
                                  else "batch_busy")
            latest = c.execute("SELECT MAX(seq) FROM upload_batch").fetchone()[0]
            if r["seq"] != latest:
                raise UploadError(409, "not_latest")
            files = json.loads(r["files"])
            a = self._assignments(files, assignments)
            digest = _digest(sorted((v["slot"], next(f["sha256"] for f in files if f["file_id"] == k),
                                     v["declarations"], v.get("derived") or {}) for k, v in a.items()))
            job_id = self._claim_job(c, batch_id, "validate")
            c.execute("""UPDATE upload_batch SET assignments=?, digest=?, status='validating', preview=NULL,
                         preview_token=NULL, candidate=NULL, base=NULL, message=NULL, updated_at=? WHERE batch_id=?""",
                      (json.dumps(a), digest, _now(), batch_id))
            _event(c, batch_id, "validation_started", actor, job_id=job_id,
                   slots=sorted(v["slot"] for v in a.values()))
        self._start(job_id, lambda: self._run_validate(batch_id, job_id))
        return self.job(job_id)

    # -- publication -----------------------------------------------------------------------------------------
    def start_publication(self, batch_id: str, token: str, actor: str) -> dict:
        with self._lock, ST._conn() as c:
            r = self._row(c, batch_id)
            if r["status"] in ("validating", "publishing"):
                raise UploadError(409, "batch_busy")
            latest = c.execute("SELECT MAX(seq) FROM upload_batch").fetchone()[0]
            if r["seq"] != latest or r["status"] == "superseded":
                raise UploadError(409, "not_latest")
            if r["status"] != "ready" or not r["preview_token"]:
                raise UploadError(409, "not_ready")
            if not isinstance(token, str) or not secrets.compare_digest(token, r["preview_token"]):
                raise UploadError(409, "token")
            base = json.loads(r["base"] or "{}")
            if self._base() != base:
                c.execute("UPDATE upload_batch SET status='stale', preview_token=NULL, updated_at=?, message=? "
                          "WHERE batch_id=?", (_now(), MSG["stale"], batch_id))
                _event(c, batch_id, "stale_refused", actor)
                c.commit()                                   # recorded even though the request is refused
                raise UploadError(409, "stale")
            job_id = self._claim_job(c, batch_id, "publish")
            c.execute("UPDATE upload_batch SET status='publishing', preview_token=NULL, updated_at=? WHERE batch_id=?",
                      (_now(), batch_id))
            _event(c, batch_id, "publication_started", actor, job_id=job_id)
        self._start(job_id, lambda: self._run_publish(batch_id, job_id, actor))
        return self.job(job_id)

    # -- jobs ------------------------------------------------------------------------------------------------
    def _claim_job(self, c, batch_id: str, kind: str) -> str:
        with self._job_lock:
            if self._active_job is not None:
                raise UploadError(409, "job_running")
            job_id = "j-" + secrets.token_hex(8)
            self._active_job = job_id
        c.execute("""INSERT INTO upload_job(job_id,batch_id,kind,state,stage,created_at) VALUES(?,?,?,'queued',
                     'queued',?)""", (job_id, batch_id, kind, _now()))
        return job_id

    def _start(self, job_id: str, fn: Callable[[], None]) -> None:
        def run():
            try:
                fn()
            finally:
                with self._job_lock:
                    if self._active_job == job_id:
                        self._active_job = None
        t = threading.Thread(target=run, name=f"upload-{job_id}", daemon=True)
        self._threads.append(t)
        t.start()

    def wait_idle(self, timeout: float = 60.0) -> bool:
        """Tests / shutdown: wait until no job is running."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._job_lock:
                if self._active_job is None:
                    return True
            time.sleep(0.02)
        return False

    def _stage(self, job_id: str, stage: str) -> None:
        with ST._conn() as c:
            c.execute("UPDATE upload_job SET state='running', stage=?, started_at=COALESCE(started_at,?) WHERE job_id=?",
                      (stage, _now(), job_id))

    def _finish(self, job_id: str, state: str, code: str | None = None, message: str | None = None) -> None:
        with ST._conn() as c:
            c.execute("UPDATE upload_job SET state=?, stage=?, finished_at=?, error_code=?, message=? WHERE job_id=?",
                      (state, "done" if state == "done" else state, _now(), code, message, job_id))

    def job(self, job_id: str) -> dict:
        if not re.fullmatch(r"j-[0-9a-f]{16}", job_id or ""):
            raise UploadError(404, "batch_missing", "İşlem bulunamadı.")
        with ST._conn() as c:
            j = c.execute("SELECT * FROM upload_job WHERE job_id=?", (job_id,)).fetchone()
            if j is None:
                raise UploadError(404, "batch_missing", "İşlem bulunamadı.")
            stages = VALIDATE_STAGES if j["kind"] == "validate" else PUBLISH_STAGES
            idx = stages.index(j["stage"]) + 1 if j["stage"] in stages else (len(stages) if j["state"] == "done" else 0)
            started = j["started_at"] or j["created_at"]
            elapsed = None
            try:
                t0 = dt.datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                t1 = (dt.datetime.strptime(j["finished_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                      if j["finished_at"] else dt.datetime.now(dt.timezone.utc))
                elapsed = max(0, int((t1 - t0).total_seconds()))
            except (TypeError, ValueError):
                pass
            return {"job_id": j["job_id"], "batch_id": j["batch_id"], "kind": j["kind"], "state": j["state"],
                    "stage": j["stage"], "stage_label": STAGE_TR.get(j["stage"], j["stage"]),
                    "step": idx, "steps": len(stages), "elapsed_seconds": elapsed,
                    "error_code": j["error_code"], "message": j["message"],
                    "batch": self._view(c, self._row(c, j["batch_id"]))}

    # -- the live state the preview is bound to ------------------------------------------------------------------
    def _selection_bytes(self, name: str) -> bytes:
        try:
            with open(os.path.join(self.deps.data_folder, name), "rb") as fh:
                return fh.read()
        except OSError:
            return b""

    def _base(self) -> dict:
        """What a preview is built against: the served core generation and both selection files. Anything published
        afterwards (another upload, a manual refresh with a different selection, …) makes the preview stale."""
        m = self.deps.generations().current()
        return {"core_id": m.core_id if (m is not None and m.core) else None,
                "selection": hashlib.sha256(self._selection_bytes(GEN.SELECTION_FILE) + b"\x00" +
                                            self._selection_bytes(GEN.OWNER_SELECTION_FILE)).hexdigest()}

    # -- planning ------------------------------------------------------------------------------------------------
    def _plan(self, r, *, where: str) -> dict:
        """The report set this batch would publish: uploaded files (+ declarations) and, for core reports not in the
        batch, the reports of the CURRENT explicit selection (carried over unchanged, with their own declarations).
        `where` = "blob" (preview, private copies) or "data" (publication, the data-folder copies; same names)."""
        files = {f["file_id"]: f for f in json.loads(r["files"])}
        a = json.loads(r["assignments"] or "{}")
        uploaded = {}
        for fid, v in a.items():
            f = files.get(fid)
            if f is None:
                raise UploadError(409, "file_missing")
            slot = v["slot"]
            path = (self.blobs.path(f["sha256"]) if where == "blob"
                    else os.path.join(self.deps.data_folder, f["name"]))
            uploaded[slot] = {"file_id": fid, "sha256": f["sha256"], "name": f["name"], "label": f["label"],
                              "path": path, "declarations": _decl_from_json(slot, v["declarations"]),
                              "derived": dict(v.get("derived") or {})}
        errors, warnings = [], []
        m = self.deps.generations().current()
        live_core = {x.key: x for x in (m.core if m is not None else ()) if x.key in CORE_SLOTS}
        explicit = m is not None and m.selection_origin == "explicit" and bool(live_core)
        core = {}
        core_changed = any(s in uploaded for s in CORE_SLOTS)
        if core_changed:
            for key in CORE_SLOTS:
                if key in uploaded:
                    core[key] = {**uploaded[key], "carried": False}
                elif explicit and key in live_core:
                    rec = live_core[key]
                    p = os.path.join(self.deps.data_folder, rec.file)
                    if GEN.sha256_file(p) != rec.sha256:
                        errors.append({"code": "carried_changed", "slot": key,
                                       "message": MSG["carried_changed"].format(label=SLOTS[key]["label"])})
                        continue
                    decl = {f: rec.declared.get(f) for f in SLOTS[key]["fields"]}
                    decl = clean_declarations(key, _decl_json({k: v for k, v in decl.items()
                                                               if not isinstance(v, (dict, list))}))
                    derived = {f: rec.declared.get(f) for f in ("period_complete", "date_origin", "date_source")
                               if rec.declared.get(f) is not None and f not in SLOTS[key]["fields"]}
                    core[key] = {"file_id": None, "sha256": rec.sha256, "name": rec.file,
                                 "label": self._labels().get(rec.file, rec.file),
                                 "path": p, "declarations": decl, "carried": True, "derived": derived,
                                 "approval": rec.declared.get("approval")}
            if not ({"stock", "sales"} <= set(core)) and not any(e["code"] == "carried_changed" for e in errors):
                errors.append({"code": "core_required", "message": MSG["core_required"]})
            if "aging" not in core:
                warnings.append({"code": "aging_absent", "message": "Cari raporu bu yayında yok: tahsilat ve risk "
                                                                    "bölümleri oluşturulmaz."})
        owner = {k: v for k, v in uploaded.items() if k in OWNER_SLOTS}
        return {"uploaded": uploaded, "core": core, "core_changed": core_changed, "owner": owner,
                "errors": errors, "warnings": warnings, "live": m}

    @staticmethod
    def _labels() -> dict[str, str]:
        """Stored name → the owner's original file label (display only), from the upload history."""
        out: dict[str, str] = {}
        with ST._conn() as c:
            for r in c.execute("SELECT files FROM upload_batch ORDER BY seq"):
                for f in json.loads(r["files"]):
                    out[f["name"]] = f["label"]
        return out

    def _core_selection(self, core: Mapping[str, dict]):
        paths = {k: (core[k]["path"] if k in core else None) for k in CORE_SLOTS}
        decls = {}
        for k in CORE_SLOTS:
            d = {f: None for f in GEN._DECL_FIELDS}       # the exact field set the selection reader re-derives
            if k in core:
                d.update({f: v for f, v in core[k]["declarations"].items() if f in d})
                d.update({f: v for f, v in (core[k].get("derived") or {}).items()
                          if f in ("period_complete", "date_origin", "date_source")})
                if core[k].get("approval"):
                    d["approval"] = core[k]["approval"]
            decls[k] = d
        return paths, decls

    @staticmethod
    def _owner_options(slot: str, decl: Mapping[str, Any]) -> dict:
        return {f: (None if decl.get(f) is None else (decl[f].isoformat() if isinstance(decl[f], dt.date)
                                                     else str(decl[f])))
                for f in GEN.OWNER_OPTION_FIELDS[slot]}

    # -- the validation job ----------------------------------------------------------------------------------
    def _run_validate(self, batch_id: str, job_id: str) -> None:
        try:
            self._stage(job_id, "checking")
            with ST._conn() as c:
                r = self._row(c, batch_id)
            preview, candidate = self._build_preview(r, job_id)
            ok = not preview["errors"]
            with self._lock, ST._conn() as c:
                cur = self._row(c, batch_id)
                if cur["status"] != "validating":                 # superseded / discarded meanwhile
                    self._finish(job_id, "done")
                    return
                token = secrets.token_urlsafe(24) if ok else None
                c.execute("""UPDATE upload_batch SET status=?, preview=?, preview_token=?, base=?, candidate=?,
                             message=NULL, updated_at=? WHERE batch_id=?""",
                          ("ready" if ok else "invalid", json.dumps(preview, ensure_ascii=False, default=str), token,
                           json.dumps(preview["base"]), json.dumps(candidate), _now(), batch_id))
                _event(c, batch_id, "validated" if ok else "validation_failed", "system", job_id=job_id,
                       errors=[e["code"] for e in preview["errors"]], noop=preview["noop"],
                       vkm=preview["vkm"]["counts"])
            self._finish(job_id, "done")
        except UploadError as e:
            self._fail_validation(batch_id, job_id, e.code, e.message)
        except Exception as e:                                         # never a stack trace or a value in the payload
            log.exception("upload: validation failed (%s)", type(e).__name__)
            self._fail_validation(batch_id, job_id, "internal", MSG["internal"])

    def _fail_validation(self, batch_id, job_id, code, message):
        with self._lock, ST._conn() as c:
            c.execute("UPDATE upload_batch SET status='invalid', message=?, updated_at=? WHERE batch_id=? AND "
                      "status='validating'", (message, _now(), batch_id))
            _event(c, batch_id, "validation_failed", "system", job_id=job_id, errors=[code])
        self._finish(job_id, "failed", code, message)

    def _build_preview(self, r, job_id: str) -> tuple[dict, dict]:
        from services import refresh as RF
        from services import owner_view as OV
        from services import vkm_store as VKMS
        from services import vkm as V
        from services import accuracy as ACC

        base = self._base()
        for f in json.loads(r["files"]):
            if not self.blobs.verify(f["sha256"]):
                raise UploadError(409, "file_missing")
        plan = self._plan(r, where="blob")
        errors, warnings = list(plan["errors"]), list(plan["warnings"])
        live = plan["live"]
        repo = self.deps.context_repo()
        live_ctx = repo.live() if hasattr(repo, "live") else repo.current()
        sources = []
        cand_core = None
        report = None
        # 1. core: the controlled refresh, stage-only (nothing swapped or installed)
        if plan["core_changed"] and not errors:
            holder: dict = {}
            paths, decls = self._core_selection(plan["core"])
            bdir = os.path.join(self.deps.upload_root, "batches", r["batch_id"])
            os.makedirs(bdir, mode=0o700, exist_ok=True)
            hooks = RF.PublishHooks(selection=(paths, decls, "explicit"), stage_only=True, staged_out=holder,
                                    progress=lambda s: self._stage(job_id, s),
                                    previous_date=RF.live_dashboard_date(self.deps.dashboard_path))
            report = RF.run_refresh(self.deps.data_folder, os.path.join(bdir, "onizleme.html"),
                                    previous_context=live_ctx, inflation_rate=self.deps.inflation_rate, hooks=hooks)
            if not report.get("ok"):
                for i, msg in enumerate(report.get("errors") or [report.get("message")]):
                    errors.append({"code": "core_validation", "message": msg})
            else:
                cand_core = holder.get("manifest")
            for w in report.get("warnings") or []:
                if "Karşılaştırılacak canlı bağlam yok" in w:
                    continue
                warnings.append({"code": "core_warning", "message": w})
            shutil.rmtree(bdir, ignore_errors=True)
        # 2. owner workbooks: the M6 importers through the owner repository, without publishing anything
        self._stage(job_id, "owner")
        owner_repo = self.deps.owner_repo()
        owner_entries = {}
        for key, u in plan["owner"].items():
            cfg = OV.OwnerSourceConfig(key, u["path"], self._owner_options(key, u["declarations"]), origin="upload")
            e = owner_repo.prepare(key, cfg)
            owner_entries[key] = e
            if e.get("state") != OV.READY:
                codes = list(e.get("issue_codes") or [])
                msgs = [IMPORT_CODE_TR[c] for c in codes if c in IMPORT_CODE_TR]
                errors.append({"code": "owner_validation", "slot": key, "issue_codes": codes,
                               "message": f"{SLOTS[key]['label']}: " + (" ".join(msgs) if msgs else
                                          OV.FAIL_MESSAGES.get(e.get("error_code"), "Dosya doğrulanamadı.")
                                          + (f" (kodlar: {', '.join(codes)})" if codes else ""))})
        # 3. the candidate generation (core + owner records) and its comparison with what is served
        self._stage(job_id, "compare")
        core_manifest = cand_core if cand_core is not None else (live if (live is not None and live.core) else None)
        cand_full = core_manifest
        if cand_full is not None:
            for k, e in owner_entries.items():
                if e.get("record") is not None:
                    cand_full = cand_full.with_owner(k, e["record"])
        # VKM delta (read-only)
        obs = {}
        owner_paths = {k: c.path for k, c in owner_repo.configs().items()}
        owner_paths.update({k: u["path"] for k, u in plan["owner"].items()})
        if cand_core is not None:
            obs.update(VKMS.core_observations(cand_core, owner_paths, VKMS.previously_seen_sources()))
        for k, e in owner_entries.items():
            if e.get("record") is not None:
                obs[k] = VKMS.owner_observation(None, k, e["record"])
        vkm = VKMS.preview_delta(obs) if obs else {"sources": [], "counts": {k: 0 for k in VKMS.PREVIEW_OUTCOMES}}
        # sources (uploaded and carried), recognized sheets / columns, declarations, missing information
        for key in (*CORE_SLOTS, *OWNER_SLOTS):
            u = plan["core"].get(key) if key in CORE_SLOTS else plan["owner"].get(key)
            if u is None:
                continue
            sources.append(self._source_view(key, u, cand_core, owner_entries.get(key)))
        missing = []
        for s in sources:
            if s["unknown_fields"]:
                missing.append({"slot": s["slot"], "message": f"{s['slot_label']}: beyan edilmedi — "
                                + ", ".join(s["unknown_fields"]) + " (bilinmiyor olarak kaydedilir)."})
        if plan["core_changed"] and "aging" not in plan["core"]:
            missing.append({"slot": "aging", "message": "Cari raporu yok (tahsilat ve risk bölümleri oluşturulmaz)."})
        cfgs = owner_repo.configs()
        for k in OWNER_SLOTS:
            if k not in plan["owner"] and not cfgs[k].configured and k in V.EXPECTED_ALWAYS:
                missing.append({"slot": k, "message": f"{SLOTS[k]['label']} dosyası yayında yok ve bu yüklemede de yok."})
        if plan["core_changed"]:
            dates = {k: (v["declarations"].get("snapshot_date") or v["declarations"].get("report_date")
                         or v["declarations"].get("period_end")) for k, v in plan["core"].items()}
            known = {d for d in dates.values() if d is not None}
            if len(known) > 1:
                warnings.append({"code": "mixed_dates", "message": "Stok, satış ve cari raporlarının beyan edilen "
                                 "tarihleri farklı; raporların aynı döneme ait olduğundan emin olun."})
        # effects on the dashboard sections and the gate (both gates from the production gate; nothing promoted)
        sections = self._sections(cand_full, core_manifest is not None)
        noop = self._is_noop(plan, cand_core, live, cfgs)
        reconciliation = self._reconciliation(cand_core, owner_entries)
        as_of = {"current": (live_ctx or {}).get("as_of_data_date") if live_ctx else
                 RF.live_dashboard_date(self.deps.dashboard_path),
                 "candidate": (report or {}).get("candidate_as_of_data_date") if plan["core_changed"] else None,
                 "note": "En son satış işleminin tarihi (gözlenen). Rapor tarihi değildir; rapor tarihi yalnızca "
                         "beyan edilir."}
        preview = {"batch_id": r["batch_id"], "created_at": _now(), "errors": errors, "warnings": warnings,
                   "missing": missing, "sources": sources, "core_changed": plan["core_changed"],
                   "owner_changed": sorted(plan["owner"]), "noop": noop, "vkm": vkm, "sections": sections,
                   "reconciliation": reconciliation, "as_of": as_of, "base": base,
                   "candidate_core_id": cand_core.core_id[:12] if cand_core is not None else None,
                   "generation_bound_api_evidence": ACC.GENERATION_BOUND_API_EVIDENCE,
                   "notice": ("Bu önizleme canlı verileri değiştirmez. Yayınlamak hiçbir analizi doğrulanmış yapmaz: "
                              "doğrulanmamış analizler gizli kalır."), "ai_used": False}
        candidate = {"core_id": cand_core.core_id if cand_core is not None else None,
                     "core_changed": plan["core_changed"],
                     "owner": {k: (e.get("record").sha256 if e.get("record") is not None else None)
                               for k, e in owner_entries.items()}}
        return preview, candidate

    def _source_view(self, key, u, cand_core, owner_entry) -> dict:
        decl = u["declarations"]
        view = {"slot": key, "slot_label": SLOTS[key]["label"], "role": SLOTS[key]["role"],
                "label": u["label"], "carried": bool(u.get("carried")), "sha256": u["sha256"][:12],
                "declarations": [{"field": f, "label": FIELD_LABELS[f],
                                  "value": (decl[f].isoformat() if isinstance(decl.get(f), dt.date) else decl.get(f))}
                                 for f in SLOTS[key]["fields"]],
                "unknown_fields": unknown_fields(key, decl), "sheets": _sheet_names(u["path"])}
        derived = u.get("derived") or {}
        if derived.get("date_origin"):
            from services import cari_policy as CARI
            view["declarations"].append({"field": "date_origin", "label": "Tarih kaynağı",
                                         "value": CARI.DATE_ORIGIN_TR.get(derived["date_origin"], derived["date_origin"])
                                         + (f" — {derived['date_source']}" if derived.get("date_source") else "")})
        if key in CORE_SLOTS:
            from services import source_validation as SV
            rec = cand_core.record(key) if cand_core is not None else None
            v = dict(rec.validation or {}) if rec is not None else {}
            missing = set(v.get("missing_headers") or [])
            view["columns"] = [{"name": h.strip() or h, "found": (h not in missing) if rec is not None else None}
                               for h in SV.CORE[key]["required"]]
            view["data_rows"] = v.get("data_rows") if rec is not None else None
            view["duplicate_headers"] = list(v.get("duplicate_headers") or [])
            view["layout_supported"] = v.get("layout_supported") if rec is not None else None
            tmpl = ((v.get("rows") or {}).get("template") or {}) if key == "stock" else {}
            view["template"] = ({"status": tmpl.get("status"), "identity_column": tmpl.get("identity_column")}
                                if tmpl else None)
            if key == "sales" and cand_core is not None:
                liste = cand_core.record("liste")
                view["master_sheet"] = bool(liste and liste.evidence and liste.evidence.available)
        else:
            e = owner_entry or {}
            rec = e.get("record")
            v = dict(rec.validation or {}) if rec is not None else {}
            view["state"] = e.get("state")
            view["layout"] = v.get("layout")
            view["failed_step"] = e.get("failed_step") or v.get("failed_step")
            view["issue_codes"] = list(e.get("issue_codes") or [])
            if key == "kredi":
                view["counts"] = {k: v.get(k) for k in ("installments", "contracts", "hidden_installment_rows",
                                                        "negative_remaining_rows", "closed_with_positive_remaining")
                                  if v.get(k) is not None}
        return view

    @staticmethod
    def _reconciliation(cand_core, owner_entries) -> list[dict]:
        """Reconciliation status from the evidence the pipeline recorded (no recomputation, no promotion)."""
        out = []
        tr = {True: "Tutarlı", False: "Çelişki var", None: "Doğrulanmadı / bilinmiyor"}
        foot_tr = {"PASS": "Tüm rapor toplamları eşleşti", "OPEN": "Kısmi — bazı toplamlar doğrulanamadı",
                   "FAIL": "Rapor toplamı satırlarla çelişiyor", None: "Rapor toplamı satırı yok / denetlenmedi"}
        if cand_core is not None:
            for rec in cand_core.core:
                ev = rec.evidence
                if ev is None:
                    continue
                out.append({"source": rec.key, "label": SLOTS.get(rec.key, {}).get("label") or "Müşteri ana listesi",
                            "totals": tr.get(ev.totals_consistent), "footer": foot_tr.get(ev.footer_totals)
                            if rec.key == "stock" else None,
                            "joins": ("Eşleştirme denetlendi" if ev.joins_validated else
                                      ("Eşleştirme sorunları var" if ev.joins_validated is False else "Denetlenmedi")),
                            "join_unmatched": ev.join_unmatched, "duplicates": ev.duplicate_records,
                            "mapping": "Onaylanmadı" if not ev.mapping_approved else "Onaylı"})
        for k, e in owner_entries.items():
            rec = e.get("record")
            if rec is None or rec.evidence is None:
                continue
            out.append({"source": k, "label": SLOTS[k]["label"], "totals": tr.get(rec.evidence.totals_consistent),
                        "footer": None, "joins": None, "join_unmatched": None,
                        "duplicates": rec.evidence.duplicate_records, "mapping": "Onaylanmadı"})
        return out

    def _sections(self, cand_full, has_candidate: bool) -> dict:
        from services import accuracy as ACC
        from services import vkm as V
        cur_gate = self.deps.live_gate()
        if cand_full is not None:
            served = {x.key: x.sha256 for x in (*cand_full.core, *cand_full.owner)}
            cand_gate = ACC.current_gate(snapshot=cand_full.bound_snapshot(served),
                                         expected_generation=cand_full.generation_id)
        else:
            cand_gate = ACC.current_gate()
        state_tr = {"verified": "Gösterilir (doğrulanmış)", "not_verified": "Gizli (doğrulanmadı)",
                    "invalid": "Gizli (geçersiz / eksik kanıt)", "unavailable": "Gizli (veri yok)"}
        tabs = [("Azalan Stok", "stock.reorder"), ("Fazla Stok", "stock.overstock"),
                ("Stok Eritme", "stock.clearance_buyers"), ("Geri Kazanım", "customers.winback"),
                ("Büyüme / Küçülme", "customers.momentum"), ("Sipariş Büyüklüğü", "customers.order_size"),
                ("Kâr Marjı", "finance.margin"), ("Riskli Müşteri", "receivables.risk"),
                ("Tahsilat", "receivables.collections"), ("Günlük Satış", ACC.PANEL_METRIC["sq"]),
                ("Müşteri 360", ACC.PANEL_METRIC["c360"]), ("Fırsatlar", ACC.PANEL_METRIC["opp"]),
                ("AI Aksiyon Merkezi", ACC.PANEL_METRIC["ai"]), ("Yönetim · Kredi", "owner.kredi"),
                ("Yönetim · Gider/Gelir", "owner.gider_gelir"), ("Yönetim · Bütçe", "owner.butce")]
        rows = []
        for title, key in tabs:
            if key not in ACC.REGISTER:
                continue
            a, b = cur_gate.decide(key), cand_gate.decide(key)
            rows.append({"title": title, "metric": key, "current": a.state.value, "candidate": b.state.value,
                         "current_label": state_tr.get(a.state.value, a.state.value),
                         "candidate_label": state_tr.get(b.state.value, b.state.value),
                         "displayable_after": b.displayable, "changes": a.displayable != b.displayable})
        metrics = [{"key": k, "state": cand_gate.decide(k).state.value} for k in ACC.REGISTER]
        withheld = [m for m in metrics if m["state"] != "verified"]
        return {"tabs": rows, "metrics_total": len(metrics), "metrics_withheld": len(withheld),
                "metrics_verified": len(metrics) - len(withheld),
                "withheld_titles": V.metric_titles([m["key"] for m in withheld]),
                "note": ("Doğrulanmamış analizler yayından sonra da gizli kalır. Bir dosyanın yüklenmesi veya "
                         "doğrulamadan geçmesi hiçbir analizi doğrulanmış yapmaz.")}

    @staticmethod
    def _is_noop(plan, cand_core, live, cfgs) -> bool:
        if plan["core_changed"]:
            if cand_core is None or live is None or not live.core or cand_core.core_id != live.core_id:
                return False
        for key, u in plan["owner"].items():
            cfg = cfgs.get(key)
            if cfg is None or cfg.origin != "upload" or os.path.basename(cfg.path or "") != u["name"]:
                return False
            want = UploadCenter._owner_options(key, u["declarations"])
            if any((cfg.options.get(f) or None) != want.get(f) for f in want):
                return False
        return bool(plan["core_changed"] or plan["owner"])

    # -- the publication job ---------------------------------------------------------------------------------
    def _run_publish(self, batch_id: str, job_id: str, actor: str) -> None:
        try:
            self._stage(job_id, "checking")
            with ST._conn() as c:
                r = self._row(c, batch_id)
            preview = json.loads(r["preview"] or "{}")
            candidate = json.loads(r["candidate"] or "{}")
            if preview.get("noop"):
                # "nothing to publish" is only true for the version the preview was built against: checked again,
                # atomically, against what is served NOW (another publication may have happened since the request)
                with RP.PUBLISH_LOCK:
                    fresh = self._base() == json.loads(r["base"] or "{}")
                if not fresh:
                    self._failed_publish(batch_id, job_id, actor, "stale", MSG["stale"])
                    return
                self._done_publish(batch_id, job_id, actor, "unchanged", "unchanged")
                return
            result = self._publish(r, candidate, job_id)
            if result["ok"]:
                self._done_publish(batch_id, job_id, actor, "published", "published",
                                   warnings=result.get("warnings") or [])
            else:
                self._failed_publish(batch_id, job_id, actor, result.get("code") or "publish_failed",
                                     result["message"], retry=result.get("retry", False))
        except UploadError as e:
            self._failed_publish(batch_id, job_id, actor, e.code, e.message)
        except Exception as e:
            log.exception("upload: publication failed (%s)", type(e).__name__)
            self._failed_publish(batch_id, job_id, actor, "internal", MSG["internal"])

    def _done_publish(self, batch_id, job_id, actor, status, event, warnings=()):
        with self._lock, ST._conn() as c:
            c.execute("UPDATE upload_batch SET status=?, published_at=?, updated_at=?, message=? WHERE batch_id=?",
                      (status, _now() if status == "published" else None, _now(),
                       ("Değişiklik yoktu: yüklenen dosyalar ve beyanlar yayındakiyle aynı. Hiçbir şey yeniden "
                        "yayınlanmadı." if status == "unchanged" else
                        ("Yayınlandı. " + " ".join(warnings)).strip()), batch_id))
            _event(c, batch_id, event, actor, job_id=job_id)
        self._finish(job_id, "done")

    def _failed_publish(self, batch_id, job_id, actor, code, message, retry=False):
        with self._lock, ST._conn() as c:
            # busy (another refresh running): nothing changed and the preview is still valid → publishable again
            status = "ready" if retry else ("stale" if code == "stale" else "publish_failed")
            if retry:
                c.execute("UPDATE upload_batch SET status='ready', preview_token=?, message=?, updated_at=? "
                          "WHERE batch_id=?", (secrets.token_urlsafe(24), message, _now(), batch_id))
            else:
                c.execute("UPDATE upload_batch SET status=?, message=?, updated_at=? WHERE batch_id=?",
                          (status, message, _now(), batch_id))
            _event(c, batch_id, "publication_failed", actor, job_id=job_id, code=code)
        self._finish(job_id, "failed", code, message)

    def _publish(self, r, candidate: Mapping[str, Any], job_id: str) -> dict:
        """Publish exactly the previewed set (see `_publish_set`). Data-folder copies created by THIS attempt are removed
        again when it does not commit, so a failed publication leaves the data folder as it was."""
        copied: list[str] = []
        ok = False
        try:
            result = self._publish_set(r, candidate, job_id, copied)
            ok = bool(result.get("ok"))
            return result
        finally:
            if not ok and copied:
                with RP.PUBLISH_LOCK:                        # never remove a file a (newer) live selection names
                    live = self._selection_bytes(GEN.SELECTION_FILE) + self._selection_bytes(GEN.OWNER_SELECTION_FILE)
                    for path in copied:
                        if os.path.basename(path).encode() in live:
                            continue
                        try:
                            os.remove(path)
                        except OSError:
                            pass

    def _publish_set(self, r, candidate: Mapping[str, Any], job_id: str, copied: list) -> dict:
        from services import refresh as RF
        from services import owner_view as OV
        if self._base() != json.loads(r["base"] or "{}"):
            return {"ok": False, "code": "stale", "message": MSG["stale"]}
        # 1. copy the exact previewed bytes into the data folder (content-addressed names: same names as previewed)
        self._stage(job_id, "copy")
        plan_blob = self._plan(r, where="blob")
        if plan_blob["errors"]:
            return {"ok": False, "code": "plan", "message": plan_blob["errors"][0]["message"]}
        for u in plan_blob["uploaded"].values():
            if not self.blobs.verify(u["sha256"]):
                raise UploadError(409, "file_missing")
            dest = os.path.join(self.deps.data_folder, u["name"])
            if os.path.exists(dest):
                if GEN.sha256_file(dest) != u["sha256"]:
                    raise UploadError(409, "file_missing")
                continue
            tmp = dest + ".kopya"
            shutil.copyfile(u["path"], tmp)
            os.chmod(tmp, 0o600)
            if GEN.sha256_file(tmp) != u["sha256"]:
                os.remove(tmp)
                raise UploadError(409, "file_missing")
            os.replace(tmp, dest)
            copied.append(dest)
        plan = self._plan(r, where="data")
        if plan["errors"]:
            return {"ok": False, "code": "plan", "message": plan["errors"][0]["message"]}
        # 2. owner workbooks: imported from the data-folder copies BEFORE anything changes; must match the preview
        owner_repo = self.deps.owner_repo()
        entries, cfgs = {}, {}
        for key, u in plan["owner"].items():
            cfg = OV.OwnerSourceConfig(key, u["path"], self._owner_options(key, u["declarations"]), origin="upload")
            e = owner_repo.prepare(key, cfg)
            rec = e.get("record")
            if e.get("state") != OV.READY or rec is None or rec.sha256 != (candidate.get("owner") or {}).get(key):
                return {"ok": False, "code": "owner_mismatch",
                        "message": f"{SLOTS[key]['label']} dosyası önizlemedeki gibi okunamadı; hiçbir şey "
                                   "yayınlanmadı. Dosyaları yeniden doğrulayın."}
            entries[key], cfgs[key] = e, cfg
        # 3. the selection files of the batch (written inside the swap step; restored if it fails)
        core_doc = None
        if plan["core_changed"]:
            core_doc = GEN.core_selection_document({k: {"file": v["name"], **v["declarations"],
                                                        **{f: x for f, x in (v.get("derived") or {}).items()
                                                           if f in ("period_complete", "date_origin", "date_source")},
                                                        **({"approval": v["approval"]} if v.get("approval") else {})}
                                                    for k, v in plan["core"].items()})
        owner_doc = None
        if plan["owner"]:
            try:
                existing = GEN.load_owner_selection(self.deps.data_folder) or {}
            except GEN.SelectionError:
                existing = {}
            merged = {k: {"file": os.path.basename(v["path"]), **{f: v["options"].get(f)
                                                                  for f in GEN.OWNER_OPTION_FIELDS[k]}}
                      for k, v in existing.items()}
            for k, u in plan["owner"].items():
                merged[k] = {"file": u["name"], **self._owner_options(k, u["declarations"])}
            owner_doc = GEN.owner_selection_document(merged)

        # 4. the commit (under PUBLISH_LOCK). Everything it changes is captured first and restored by ONE rollback
        #    if ANY step fails — a selection file, the owner configuration, the dashboard / manifest swap (refresh),
        #    the context / generation install, or the owner workbook installation. Generation notifications (the
        #    Veri Kontrol observers) are held and delivered only when the whole batch committed, so a rolled-back
        #    publication never writes an observation or a history event.
        gens = self.deps.generations()
        repo = self.deps.context_repo()
        txn: dict = {}
        base = json.loads(r["base"] or "{}")

        def precheck():
            """Under PUBLISH_LOCK, before ANY selection file, configuration or dashboard byte changes: the served core
            generation and both selection files must still be exactly what the preview was built against. The
            earlier checks run before the (long) copy / import / build; another publication may have happened since."""
            return None if self._base() == base else MSG["stale"]

        def rollback():
            for path, before in reversed(txn.get("files", [])):
                try:
                    if before is None:
                        try:
                            os.remove(path)
                        except FileNotFoundError:
                            pass
                    else:
                        tmp = path + ".geri"
                        with open(tmp, "wb") as fh:
                            fh.write(before)
                        os.replace(tmp, path)
                except Exception:
                    log.exception("upload: could not restore a selection file")
            if "owner" in txn:
                owner_repo.restore_state(txn["owner"])
            if "gen" in txn:
                gens.restore_state(txn["gen"])
            if "ctx" in txn:
                live = txn["ctx"]
                if hasattr(repo, "restore_live"):
                    repo.restore_live(live)
                elif live is not None:
                    repo.replace_context(live)
            gens.release_notifications(deliver=False)

        def begin():
            """before_swap: snapshot, hold notifications, write both selections, stage the owner configuration.
            Self-contained: a failure in any of these steps rolls back the steps already done, then re-raises."""
            txn.clear()
            txn["ctx"] = repo.live() if hasattr(repo, "live") else repo.current()
            txn["gen"] = gens.snapshot_state()
            txn["owner"] = owner_repo.snapshot_state()
            txn["files"] = []
            gens.hold_notifications()
            try:
                for name, doc in ((GEN.SELECTION_FILE, core_doc), (GEN.OWNER_SELECTION_FILE, owner_doc)):
                    if doc is None:
                        continue
                    path = os.path.join(self.deps.data_folder, name)
                    before = self._selection_bytes(name) if os.path.exists(path) else None
                    txn["files"].append((path, before))     # recorded first: a half-written replace is restored too
                    GEN.write_json_atomic(path, doc)
                if cfgs:
                    owner_repo.stage_configs(cfgs)
            except Exception:
                rollback()
                raise
            return rollback

        def install_owner():
            if entries:
                self._stage(job_id, "publish_owner")
                owner_repo.install_entries(entries)

        def commit():
            gens.release_notifications(deliver=True)

        try:
            if plan["core_changed"]:
                paths, decls = self._core_selection(plan["core"])
                rep = RF.run_refresh(self.deps.data_folder, self.deps.dashboard_path,
                                     previous_context=repo.live() if hasattr(repo, "live") else repo.current(),
                                     install=self.deps.install, inflation_rate=self.deps.inflation_rate,
                                     hooks=RF.PublishHooks(selection=(paths, decls, "explicit"),
                                                           expect_core_id=candidate.get("core_id"),
                                                           precheck=precheck,
                                                           before_swap=begin, after_install=install_owner,
                                                           on_commit=commit,
                                                           progress=lambda s: self._stage(job_id, s)))
                if not rep.get("ok"):
                    if rep.get("step") == "stale":               # a newer publication won: it stays as it is
                        return {"ok": False, "code": "stale", "message": MSG["stale"]}
                    busy = rep.get("step") == "busy"
                    if busy:
                        return {"ok": False, "code": "busy", "retry": True,
                                "message": "Başka bir yenileme sürüyor; önizleme geçerli, birazdan yeniden yayınlayın."}
                    kept = (RF.PREVIOUS_KEPT if rep.get("previous_data_kept")
                            else "Önceki pano geri yüklenemedi; kontrol edin.")
                    return {"ok": False, "code": "refresh_failed",
                            "message": f"{rep.get('message') or MSG['internal']} {kept}"}
                return {"ok": True}
            with RP.PUBLISH_LOCK:                                    # owner workbooks only: one step for every reader
                if precheck():
                    return {"ok": False, "code": "stale", "message": MSG["stale"]}
                try:
                    begin()                                          # rolls back its own partial work on failure
                except Exception:
                    log.exception("upload: selection could not be written")
                    return {"ok": False, "code": "selection_failed",
                            "message": "Kaynak seçimi kaydedilemedi; hiçbir şey yayınlanmadı. " + RF.PREVIOUS_KEPT}
                try:
                    install_owner()
                except Exception:
                    log.exception("upload: owner workbook installation failed")
                    rollback()
                    return {"ok": False, "code": "owner_install_failed",
                            "message": "Yönetim kaynakları yayınlanamadı; önceki seçim ve görünümler geri yüklendi. "
                                       + RF.PREVIOUS_KEPT}
                commit()
            return {"ok": True}
        finally:
            gens.release_notifications(deliver=False)               # never leave this thread holding notifications

    # -- read models -----------------------------------------------------------------------------------------
    @staticmethod
    def _files_view(files: list[dict]) -> list[dict]:
        return [{"file_id": f["file_id"], "label": f["label"], "size": f["size"], "sha256": f["sha256"][:12]}
                for f in files]

    def _view(self, c, r) -> dict:
        a = json.loads(r["assignments"] or "{}")
        preview = json.loads(r["preview"]) if r["preview"] else None
        if preview is not None:
            preview = {k: v for k, v in preview.items() if k != "base"}
        return {"batch_id": r["batch_id"], "seq": r["seq"], "status": r["status"],
                "status_label": STATUS_TR.get(r["status"], r["status"]), "created_at": r["created_at"],
                "updated_at": r["updated_at"], "published_at": r["published_at"], "message": r["message"],
                "files": self._files_view(json.loads(r["files"])), "assignments": a,
                "preview": preview, "preview_token": r["preview_token"] if r["status"] == "ready" else None,
                "publishable": r["status"] == "ready" and bool(r["preview_token"])}

    def overview(self) -> dict:
        """Owner page model: the slot catalogue, limits, the current (newest open) batch, the active job, the
        published selection and a short history. No workbook value."""
        with ST._conn() as c:
            rows = c.execute("SELECT * FROM upload_batch ORDER BY seq DESC LIMIT 12").fetchall()
            current = next((r for r in rows if r["status"] in (*OPEN_STATUSES, "interrupted", "stale",
                                                              "publish_failed")), None)
            if current is not None and current["seq"] != rows[0]["seq"]:
                current = None
            labels = self._labels()
            hist = []
            for r in rows:
                a = json.loads(r["assignments"] or "{}")
                fl = {f["file_id"]: f["label"] for f in json.loads(r["files"])}
                hist.append({"batch_id": r["batch_id"], "status": r["status"],
                             "status_label": STATUS_TR.get(r["status"], r["status"]), "created_at": r["created_at"],
                             "published_at": r["published_at"],
                             "reports": [{"slot": v["slot"], "slot_label": SLOTS[v["slot"]]["label"],
                                          "label": fl.get(k)} for k, v in a.items() if v.get("slot") in SLOTS]})
            job = None
            with self._job_lock:
                active = self._active_job
            if active:
                job = self.job(active)
            out = {"slots": [{"key": k, "label": v["label"], "role": v["role"], "hint": v["hint"],
                              "fields": [{"key": f, "label": FIELD_LABELS[f],
                                          "type": ("date" if f in DATE_FIELDS else ("year" if f in YEAR_FIELDS
                                                                                     else "bool"))}
                                         for f in v["fields"]]} for k, v in SLOTS.items()],
                   "limits": {"max_files": MAX_FILES, "max_bytes": UF.MAX_BYTES, "accept": ".xlsx"},
                   "batch": self._view(c, current) if current is not None else None, "job": job,
                   "published": self._published(labels), "history": hist,
                   "notice": ("Dosyalar yalnızca yönetici tarafından yüklenir, önce doğrulanır ve ancak siz "
                              "'Değişiklikleri Yayınla' dediğinizde panoya yansır. DinamikCRM'e hiçbir bağlantı "
                              "yoktur."), "ai_used": False}
        return out

    def _published(self, labels: Mapping[str, str]) -> list[dict]:
        m = self.deps.generations().current()
        out = []
        cfgs = self.deps.owner_repo().configs()
        for rec in (m.core if m is not None else ()):
            if rec.key not in CORE_SLOTS:
                continue
            out.append({"slot": rec.key, "slot_label": SLOTS[rec.key]["label"],
                        "label": labels.get(rec.file, rec.file), "sha256": rec.sha256[:12],
                        "origin": rec.selection_origin,
                        "declared": {f: (v.isoformat() if isinstance(v, dt.date) else v)
                                     for f, v in (rec.declared or {}).items()
                                     if f in SLOTS[rec.key]["fields"]}})
        for k in OWNER_SLOTS:
            cfg = cfgs.get(k)
            if cfg is None or not cfg.configured:
                continue
            name = os.path.basename(cfg.path)
            out.append({"slot": k, "slot_label": SLOTS[k]["label"], "label": labels.get(name, name), "sha256": None,
                        "origin": cfg.origin, "declared": {f: cfg.options.get(f) for f in SLOTS[k]["fields"]}})
        return out

    def batch(self, batch_id: str) -> dict:
        with ST._conn() as c:
            return self._view(c, self._row(c, batch_id))

    def events(self, batch_id: str) -> list[dict]:
        with ST._conn() as c:
            self._row(c, batch_id)
            return [{"event": e["event"], "at": e["at"], "actor": e["actor"]}
                    for e in c.execute("SELECT * FROM upload_event WHERE batch_id=? ORDER BY event_id", (batch_id,))]


def _sheet_names(path: str) -> list[str]:
    """Sheet names from `xl/workbook.xml` (schema labels; the container was checked when it was received)."""
    try:
        with zipfile.ZipFile(path) as z, z.open("xl/workbook.xml") as fh:
            root = ET.parse(fh).getroot()
        return [s.get("name") for s in root.iter() if s.tag.endswith("}sheet") and s.get("name")][:40]
    except Exception:
        return []


__all__ = ["UploadCenter", "UploadError", "Deps", "SLOTS", "CORE_SLOTS", "OWNER_SLOTS", "STATUS_TR", "STAGE_TR",
           "MSG", "clean_declarations", "init_tables"]
