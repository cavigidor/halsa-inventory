"""Klasik Rapor — the ORIGINAL Halsa dashboard, exactly as the father received it, from new Excel files.

The calculations, rules, labels, HTML, CSS and JavaScript are those of `classic/original_builder.py`, a byte-for-byte
copy of `dashboard_builder.py` at commit 71f5f2f (2026-09-10; SHA-256 pinned in tests/test_classic_dashboard.py).
This wrapper only chooses WHICH files the original builder reads and on WHICH date it runs; it never changes a value,
a column, a file name or a calculation, and it never imports anything from the new Halsa application (no accuracy
gate, no evidence, no Cari / stock policy, no access control, no SQLite, no configuration).

How it keeps the original's behaviour exact:
- The original builder picks its inputs from a folder by file name (`stok` / `sat`·`bazl` / `cari`·`yas`·`yaş`) and
  the newest modification time. The wrapper copies exactly the chosen files — under their names as stored on disk,
  which the report shows in its header — into a private temporary folder, checks with the builder's own name rules
  that each file will be read in its intended role (and that nothing else will be), and verifies every copy's SHA-256.
- The original builder writes the report date (`today`) in the title; `--date` sets it for a reproduction.
- The original builder iterates a Python set when it lists "Büyüme / Küçülme": rows with exactly the same "Değişim %"
  came out in a random order on every run. The wrapper runs it with PYTHONHASHSEED=0, so the same inputs and date give
  the same bytes every time (it picks one of the orders the original itself could produce; nothing else changes).
- The builder runs in a separate Python process with a minimal environment (no PYTHONPATH, no user site packages,
  `-s -P`; only this folder is added to its import path — `-I` is not used because it would ignore PYTHONHASHSEED);
  the child refuses to finish if any new-Halsa module was loaded.

Refused (nothing is generated): a missing or non-.xlsx file; a file whose name would make the original builder read
it in another role (rename it); a "Cari" file that is not the original-format aging report (e.g. the customer /
representative master list "cari temsilci listesi.xlsx": the aging report has `Hesap kodu`, `Hesap adı`,
`Temsilci kodu` and the month / "öncesi" columns or a "Vadesi geçen" column).

Usage (fallback command; the owner's double-click launcher is `Klasik Rapor Oluştur.command`):
    python classic/generate.py --stock STOK.xlsx --sales "satışlar.xlsx" [--cari "Cari 1 Kasım 2026.xlsx"] \
        --out dashboards/klasik_rapor.html [--date 2026-11-04] [--overwrite]
Prints the row counts and the output's SHA-256; never a business value.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
BUILDER = os.path.join(HERE, "original_builder.py")
BUILDER_SHA256 = "76afc196f984c3320e62e9a1244f57ba344054f53f009c49cb261fbc328e3646"   # git show 71f5f2f:dashboard_builder.py
HASH_SEED = "0"
# modules of the NEW Halsa application: none may be loaded while the classic report is generated
FORBIDDEN_MODULES = ("services", "config", "app", "schemas", "dashboard_builder")


class ClassicError(Exception):
    pass


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def stored_name(path: str) -> str:
    """The file's name exactly as stored on disk (a macOS name may be decomposed, e.g. 's' + U+0327 for 'ş'; the
    original builder showed the stored name, so the report must too)."""
    d, base = os.path.split(os.path.abspath(path))
    want = unicodedata.normalize("NFC", base)
    try:
        entries = os.listdir(d)
    except OSError:
        raise ClassicError(f"klasör okunamadı: {base}") from None
    if base in entries:
        return base
    hits = [e for e in entries if unicodedata.normalize("NFC", e) == want]
    if len(hits) != 1:
        raise ClassicError(f"dosya bulunamadı veya adı belirsiz: {base}")
    return hits[0]


# The child: runs the frozen original builder, nothing else. Arguments: classic dir, input dir, output, date, roles json.
CHILD = r"""
import json, os, sys, datetime as dt
classic, folder, out, day, roles = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], json.loads(sys.argv[5])
sys.path.insert(0, classic)
import original_builder as ob
names = sorted(os.listdir(folder))
picked = {"stock": [n for n in names if ob.is_stock(n)], "sales": [n for n in names if ob.is_sales(n)],
          "aging": [n for n in names if ob.is_aging(n)]}
for role in ("stock", "sales", "aging"):
    want = [roles[role]] if roles.get(role) else []
    if picked[role] != want:
        print(json.dumps({"error": "role", "role": role})); sys.exit(3)
if roles.get("aging"):
    import openpyxl
    wb = openpyxl.load_workbook(os.path.join(folder, roles["aging"]), read_only=True)
    head = [str(c).strip() for c in next(wb.worksheets[0].iter_rows(max_row=1, values_only=True)) if c is not None]
    wb.close()
    months = ['Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık']
    buckets = [c for c in head if 'öncesi' in c or any(m in c for m in months)]
    single = [c for c in head if 'vadesi geçen' in c.lower()]
    if not {'Hesap kodu', 'Hesap adı', 'Temsilci kodu'} <= set(head) or not (buckets or single):
        print(json.dumps({"error": "aging_format"})); sys.exit(4)
out_path, counts = ob.build(folder, out, today=dt.date.fromisoformat(day))
loaded = sorted(m for m in sys.modules if m.split('.')[0] in %(forbidden)r)
if loaded:
    print(json.dumps({"error": "isolation", "modules": loaded})); sys.exit(5)
print(json.dumps({"counts": counts, "pandas": __import__('pandas').__version__,
                  "numpy": __import__('numpy').__version__, "openpyxl": __import__('openpyxl').__version__,
                  "python": sys.version.split()[0]}))
""" % {"forbidden": FORBIDDEN_MODULES}

MESSAGES = {
    "role": "Dosya adı, orijinal panonun dosya seçme kuralıyla başka bir rapor gibi okunur ({role}). "
            "Stok dosyasının adında 'stok', satış dosyasının adında 'sat' veya 'bazl', Cari yaşlandırma dosyasının "
            "adında 'cari' veya 'yaş' geçmeli ve başka bir rapor adını içermemeli; dosyayı yeniden adlandırın.",
    "aging_format": "Seçilen Cari dosyası orijinal formatta Cari yaşlandırma raporu değil (Hesap kodu, Hesap adı, "
                    "Temsilci kodu ve ay / 'öncesi' sütunları gerekir). Müşteri / temsilci listesi kullanılamaz.",
    "isolation": "Klasik rapor, yeni Halsa modülleri yüklendiği için durduruldu.",
}


def generate(stock: str, sales: str, out: str, cari: str | None = None, day: dt.date | None = None,
             overwrite: bool = False, python: str | None = None) -> dict:
    if _sha(BUILDER) != BUILDER_SHA256:
        raise ClassicError("classic/original_builder.py değişmiş; orijinal pano üretilemez")
    day = day or dt.date.today()
    inputs = {"stock": stock, "sales": sales, **({"aging": cari} if cari else {})}
    for role, p in list(inputs.items()):
        if p and not os.path.isfile(p) and os.path.isdir(os.path.dirname(os.path.abspath(p))):
            try:                                             # the same name in another Unicode form (as macOS allows)
                inputs[role] = p = os.path.join(os.path.dirname(os.path.abspath(p)), stored_name(p))
            except ClassicError:
                pass
        if not p or not os.path.isfile(p):
            raise ClassicError(f"dosya yok: {role}")
        if not p.lower().endswith(".xlsx"):
            raise ClassicError(f"yalnızca .xlsx: {role}")
    out = os.path.abspath(out)
    if os.path.exists(out) and not overwrite:
        raise ClassicError("çıktı dosyası zaten var (üzerine yazmak için --overwrite)")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    before = {role: _sha(p) for role, p in inputs.items()}
    names = {role: stored_name(p) for role, p in inputs.items()}
    if len(set(names.values())) != len(names):
        raise ClassicError("iki rapor aynı dosya adını taşıyor")
    with tempfile.TemporaryDirectory(prefix="klasik-") as tmp:
        folder = os.path.join(tmp, "girdi")
        os.mkdir(folder, 0o700)
        for role, p in inputs.items():
            dst = os.path.join(folder, names[role])
            shutil.copyfile(p, dst)                          # an isolated copy; the original file is never touched
            if _sha(dst) != before[role]:
                raise ClassicError(f"kopya doğrulanamadı: {role}")
        staged = os.path.join(tmp, "rapor.html")
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
        env["PYTHONHASHSEED"] = HASH_SEED
        r = subprocess.run([python or sys.executable, "-s", "-P", "-c", CHILD, HERE, folder, staged, day.isoformat(),
                            json.dumps(names, ensure_ascii=False)],
                           capture_output=True, text=True, env=env, cwd=tmp)
        last = (r.stdout.strip().splitlines() or ["{}"])[-1]
        try:
            res = json.loads(last)
        except ValueError:
            res = {}
        if r.returncode != 0 or "counts" not in res:
            code = res.get("error")
            msg = MESSAGES.get(code, "orijinal pano oluşturulamadı")
            raise ClassicError(msg.format(role=res.get("role", "")) if code else
                               msg + (f" ({r.stderr.strip().splitlines()[-1][:200]})" if r.stderr.strip() else ""))
        os.replace(staged, out) if os.path.dirname(staged) == os.path.dirname(out) else shutil.move(staged, out)
    after = {role: _sha(p) for role, p in inputs.items()}
    if after != before:
        raise ClassicError("girdi dosyaları üretim sırasında değişti")
    return {"out": out, "sha256": _sha(out), "date": day.isoformat(), "inputs": names, **res}


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python classic/generate.py", description="Klasik Rapor Oluştur")
    ap.add_argument("--stock", required=True)
    ap.add_argument("--sales", required=True)
    ap.add_argument("--cari")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", help="rapor tarihi YYYY-MM-DD (varsayılan: bugün)")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = generate(a.stock, a.sales, a.out, cari=a.cari, overwrite=a.overwrite,
                       day=dt.date.fromisoformat(a.date) if a.date else None)
    except (ClassicError, ValueError) as e:
        print(f"Klasik rapor oluşturulamadı: {e}", file=sys.stderr)
        return 2
    c = res["counts"]
    print(f"Klasik rapor hazır: {res['out']}")
    print(f"  tarih {res['date']} · Azalan Stok {c['low']} · Fazla Stok {c['over']} · Stok Eritme {c['osmove']} · "
          f"Geri Kazanım {c['lapsed']} · Büyüme/Küçülme {c['momentum']} · Sipariş Büyüklüğü {c['ordersize']} · "
          f"Kâr Marjı {c['margin']} · Tahsilat {c['coll']} · Riskli Müşteri {c['risk']}")
    print(f"  SHA-256 {res['sha256'][:16]} · pandas {res['pandas']} · openpyxl {res['openpyxl']} · Python {res['python']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
