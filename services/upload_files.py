"""M8-CP3 Excel Upload Center — receiving one workbook safely (docs/m8_cp3_upload_center.md §3).

What an upload must pass BEFORE it is stored (nothing is parsed into business data here):
- size: at most ``UPLOAD_MAX_BYTES`` on the wire (checked while streaming, so an oversized body is never kept);
- type: an Office Open XML workbook — ZIP local-file signature, `[Content_Types].xml` and `xl/workbook.xml`. A legacy
  `.xls`, a password-protected (encrypted, OLE container) workbook, a CSV or anything renamed to `.xlsx` is refused;
- archive: a bounded number of parts; no absolute, drive-letter, backslash or `..` part names; only stored / deflated
  parts; no encrypted part; declared uncompressed size bounded in total and per part, and every part's compression
  ratio bounded (a "ZIP bomb" is refused before any reader inflates it). Python's zipfile never inflates beyond a
  part's declared size and verifies its CRC, so these declared sizes bound what any later reader (pandas, openpyxl,
  the formula-aware inspection) can allocate;
- XML: no part may contain a DTD or entity declaration (no Excel workbook does; this closes entity-expansion attacks
  independently of the XML parser's own protections).

Storage: private, content-addressed — ``<upload root>/blobs/yukleme-<128-bit SHA-256 prefix>.xlsx`` (mode 0600, the
folder 0700), outside every static or served directory. The stored name never contains the original file name, so no
date, report type or year can ever be read from it, and it never matches the legacy keyword selection
(`dashboard_builder.select_sources`: "stok", "sat", "cari", "yas"…). The same content uploaded twice is stored once.
The original name is kept only as a sanitized display label.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass

MAX_BYTES = int(os.environ.get("UPLOAD_MAX_BYTES") or 96 * 1024 * 1024)            # the real sales export is ~40 MB
MAX_UNCOMPRESSED = int(os.environ.get("UPLOAD_MAX_UNCOMPRESSED") or 3 * 1024 ** 3)
MAX_PART_UNCOMPRESSED = int(os.environ.get("UPLOAD_MAX_PART_UNCOMPRESSED") or 2 * 1024 ** 3)
MAX_PARTS = 5000
MAX_RATIO = 250                    # uncompressed / compressed, per part above 1 MiB (real exports stay far below)
RATIO_FLOOR = 1024 * 1024
XML_HEAD = 8192
STORED_PREFIX = "yukleme-"
NAME_HEX = 32                      # 128-bit content prefix in the stored name (the full SHA-256 is kept as well)
LABEL_MAX = 120
_DTD = (b"<!DOCTYPE", b"<!ENTITY", b"<!doctype", b"<!entity")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class UploadRejected(ValueError):
    """The upload is refused. `code` is stable (tests, logs); `message` is fixed Turkish text (never echoes input)."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code, self.message = code, message


MESSAGES = {
    "too_large": "Dosya çok büyük. En fazla {mb} MB yüklenebilir.",
    "empty": "Dosya boş.",
    "not_xlsx": "Bu dosya bir Excel çalışma kitabı (.xlsx) değil. Eski .xls, CSV veya şifreli dosyalar kabul edilmez; "
                "raporu DinamikCRM'den .xlsx olarak yeniden alın.",
    "corrupt": "Excel dosyası bozuk veya eksik indirilmiş görünüyor; raporu yeniden alıp tekrar yükleyin.",
    "unsafe_archive": "Dosya güvenli olmayan bir yapı içeriyor ve kabul edilmedi.",
    "encrypted": "Şifreli (parola korumalı) Excel dosyaları kabul edilmez; şifresiz olarak kaydedip yükleyin.",
    "too_many_parts": "Dosya beklenenden çok fazla parça içeriyor ve kabul edilmedi.",
    "expands_too_much": "Dosyanın açılmış boyutu izin verilen sınırı aşıyor ve kabul edilmedi.",
}


def reject(code: str) -> UploadRejected:
    return UploadRejected(code, MESSAGES[code].format(mb=MAX_BYTES // (1024 * 1024)))


def clean_label(name: str | None) -> str:
    """Display label from the browser's file name: no path, no control characters, bounded. Never used as a path,
    never parsed for a date or a report type."""
    raw = unicodedata.normalize("NFC", str(name or ""))
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = _CTRL.sub("", raw).strip()
    return (raw[:LABEL_MAX] or "adsız dosya")


def stored_name(sha256: str) -> str:
    return f"{STORED_PREFIX}{sha256[:NAME_HEX]}.xlsx"


def is_stored_name(name: str) -> bool:
    return bool(re.fullmatch(rf"{STORED_PREFIX}[0-9a-f]{{{NAME_HEX}}}\.xlsx", name or ""))


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    size: int
    name: str                      # stored basename (content-addressed)
    path: str
    parts: int
    uncompressed: int
    existed: bool                  # the same content was already stored (identical upload)


def check_archive(path: str) -> tuple[int, int]:
    """Validate the workbook container (see the module docstring). Returns (parts, declared uncompressed bytes)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        raise reject("corrupt")
    if not head:
        raise reject("empty")
    if head[:4] != b"PK\x03\x04":
        raise reject("not_xlsx")                       # .xls / encrypted OLE container / CSV / anything else
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError):
        raise reject("corrupt")
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_PARTS:
            raise reject("too_many_parts")
        names = set()
        total = 0
        for i in infos:
            n = i.filename
            if (not n or n.startswith(("/", "\\")) or "\\" in n or re.match(r"^[A-Za-z]:", n)
                    or any(seg == ".." for seg in n.split("/")) or "\x00" in n):
                raise reject("unsafe_archive")
            if i.flag_bits & 0x1:
                raise reject("encrypted")
            if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise reject("unsafe_archive")
            if i.file_size > MAX_PART_UNCOMPRESSED:
                raise reject("expands_too_much")
            if i.file_size > RATIO_FLOOR and i.file_size > MAX_RATIO * max(i.compress_size, 1):
                raise reject("expands_too_much")
            total += i.file_size
            if total > MAX_UNCOMPRESSED:
                raise reject("expands_too_much")
            names.add(n)
        if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
            raise reject("not_xlsx")
        for i in infos:
            if i.filename.endswith((".xml", ".rels", ".vml")):
                try:
                    with zf.open(i) as part:
                        head = part.read(XML_HEAD)
                except (zipfile.BadZipFile, OSError, RuntimeError, EOFError, NotImplementedError):
                    raise reject("corrupt")
                if any(t in head for t in _DTD):
                    raise reject("unsafe_archive")
        return len(infos), total


class BlobStore:
    """Private, content-addressed storage of accepted workbooks."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.blobs = os.path.join(self.root, "blobs")
        self.incoming = os.path.join(self.root, "incoming")

    def _ensure(self) -> None:
        for d in (self.root, self.blobs, self.incoming):
            os.makedirs(d, mode=0o700, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass

    def path(self, sha256: str) -> str:
        return os.path.join(self.blobs, stored_name(sha256))

    def has(self, sha256: str) -> bool:
        return os.path.isfile(self.path(sha256))

    def receive(self, chunks, *, declared_length: int | None = None) -> StoredFile:
        """Stream `chunks` (an iterable of bytes) into a private temporary file with the size bound, validate the
        container and move it into place under its content-addressed name. Nothing invalid is ever kept."""
        self._ensure()
        if declared_length is not None and declared_length > MAX_BYTES:
            raise reject("too_large")
        fd, tmp = tempfile.mkstemp(prefix="in-", suffix=".part", dir=self.incoming)
        h = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise reject("too_large")
                    h.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise reject("empty")
            os.chmod(tmp, 0o600)
            parts, total = check_archive(tmp)
            sha = h.hexdigest()
            dest = self.path(sha)
            existed = os.path.isfile(dest)
            if existed:
                os.remove(tmp)
            else:
                os.replace(tmp, dest)
            return StoredFile(sha, size, stored_name(sha), dest, parts, total, existed)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def verify(self, sha256: str) -> bool:
        """The stored content still has its recorded fingerprint (never trust a file name alone)."""
        p = self.path(sha256)
        try:
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest() == sha256
        except OSError:
            return False


__all__ = ["UploadRejected", "BlobStore", "StoredFile", "check_archive", "clean_label", "stored_name",
           "is_stored_name", "MAX_BYTES", "MESSAGES", "reject"]
