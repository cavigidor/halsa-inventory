"""Klasik Rapor + B2B Takip (D-080) — a SEPARATE, explicitly requested variant of the original dashboard.

The original Klasik Rapor (`generate.py` + the frozen `original_builder.py`, commit 71f5f2f) is not changed and keeps
producing byte-identical reports. This variant runs the SAME frozen builder, unmodified, in a separate process and only
observes it:
- the sales frame the builder reads is captured at its single `pd.read_excel` call inside `load_sales` (no second read);
- the builder's own `build()` runs every original calculation; its renderer `_html` is called with the original
  arguments after three additions: the "B2B" column in Büyüme / Küçülme (after Temsilci), the B2B Alımı / Sipariş (B2B)
  fields in each yearly detail entry, and a "B2B Takip" tab placed right after Kâr Marjı;
- one style block and one script are appended (shared `halsa_b2b` presentation; the original page ends with a fixed
  template, asserted): the five-column yearly table, the B2B badge and the single B2B Takip table (İzahat filter).
No original formula, threshold, sort order, inclusion rule, label or tab changes; "Öne Çıkan Ürünler" is rendered with
the original markup. The B2B rules come only from `halsa_b2b.py` (stdlib + pandas + openpyxl; loaded by file path), the
same calculation the V1 general report uses. Nothing of the new Halsa application (services / config / app / schemas /
dashboard_builder) may be loaded: the child refuses to finish if one is.

Usage:
    python classic/klasik_b2b.py --stock STOK.xlsx --sales "satışlar.xlsx" [--b2b-registration b2b_kullanici_rapor.xlsx]
        [--cari "Cari ….xlsx"] --out reports/private/Halsa_Kasim_2026_Klasik_B2B.html [--date 2026-11-07]
        [--b2b-review-days 365] [--b2b-list-complete]
Without --b2b-registration the report states that the registration list is missing (never "not registered").
--b2b-list-complete is the owner's declaration that the export lists every B2B user; only then is a code with no
exact, ambiguous or invoice-account match shown as 'YOK' (otherwise 'Listede yok').
Prints counts and the output's SHA-256 only; never a business value.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile

import generate as G

CORE = os.path.join(os.path.dirname(G.HERE), "halsa_b2b.py")
REGISTRATION_NAME = "b2b_kullanici_rapor.xlsx"

CHILD = r"""
import json, os, sys, datetime as dt, importlib.util
classic, folder, out, day, roles, core, reg, reg_name, days, complete = sys.argv[1:11]
roles = json.loads(roles)
sys.path.insert(0, classic)
import original_builder as ob
spec = importlib.util.spec_from_file_location('halsa_b2b', core)
hb = importlib.util.module_from_spec(spec); sys.modules['halsa_b2b'] = hb; spec.loader.exec_module(hb)
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

cap = {}
real_pd, real_load, real_html = ob.pd, ob.load_sales, ob._html
class _ReadOnce:                       # forwards everything to pandas; keeps a copy of the frame load_sales reads
    def __getattr__(self, n): return getattr(real_pd, n)
    def read_excel(self, *a, **k):
        df = real_pd.read_excel(*a, **k); cap['raw'] = df.copy(); return df
def _load(path):
    ob.pd = _ReadOnce()
    try: df = real_load(path)
    finally: ob.pd = real_pd
    cap['path'] = path; cap['base'] = df; return df
def _render(modules, stats, meta, later):
    cap['args'] = (modules, stats, meta, later); return ''
ob.load_sales, ob._html = _load, _render
try:
    _, counts = ob.build(folder, os.path.join(os.path.dirname(out), 'original-unused.html'), today=dt.date.fromisoformat(day))
finally:
    ob.load_sales, ob._html, ob.pd = real_load, real_html, real_pd
modules, stats, meta, later = cap['args']
b2b = {}
if 'base' in cap:
    reg_bytes = open(reg, 'rb').read() if reg else None
    registration = hb.read_registration(reg_bytes, reg_name)
    res = hb.analyse(cap['raw'], cap['base'], ob.cust_attrs(cap['base']), registration, hb.sheet_header(cap['path']),
                     review_days=int(days))
    for m in modules.values():
        hb.add_year_fields(m['rows'], res)
    if 'momentum' in modules:
        hb.add_flag_column(modules['momentum'], res, after='rep')
    mod = hb.module(res, registration_complete=complete == '1')
    cust_rows = [r for r in mod['rows'] if r['side'] == 'musteri']
    det = ob._cust_detail(cap['base'][cap['base']['_cust'].isin({r['code'] for r in cust_rows})])
    for r in cust_rows:
        if r['code'] in det: r['detail'] = det[r['code']]
    hb.add_year_fields(mod['rows'], res)
    mod['expand'] = True
    modules = hb.insert_after(modules, 'margin', 'b2b', mod)
    b2b = dict(res['counts'], unified=hb.unified_counts(mod['rows']), registration_complete=complete == '1')
page = hb.inject(real_html(modules, stats, meta, later))
with open(out, 'w', encoding='utf-8') as f: f.write(page)
loaded = sorted(m for m in sys.modules if m.split('.')[0] in %(forbidden)r)
if loaded:
    print(json.dumps({"error": "isolation", "modules": loaded})); sys.exit(5)
print(json.dumps({"counts": counts, "b2b": b2b, "pandas": __import__('pandas').__version__,
                  "numpy": __import__('numpy').__version__, "openpyxl": __import__('openpyxl').__version__,
                  "python": sys.version.split()[0]}, default=str))
""" % {"forbidden": G.FORBIDDEN_MODULES}


def generate_b2b(stock: str, sales: str, out: str, registration: str | None = None, cari: str | None = None,
                 day: dt.date | None = None, overwrite: bool = False, review_days: int = 365,
                 python: str | None = None, registration_complete: bool = False) -> dict:
    if G._sha(G.BUILDER) != G.BUILDER_SHA256:
        raise G.ClassicError("classic/original_builder.py değişmiş; orijinal pano üretilemez")
    if not os.path.isfile(CORE):
        raise G.ClassicError("halsa_b2b.py bulunamadı")
    if review_days < 1:
        raise G.ClassicError("inceleme süresi en az 1 gün olmalı")
    if registration_complete and registration is None:
        raise G.ClassicError("kayıt listesi tamlık beyanı için B2B kayıt dosyası gerekir")
    day = day or dt.date.today()
    inputs = {"stock": stock, "sales": sales, **({"aging": cari} if cari else {})}
    for role, p in list(inputs.items()):
        if p and not os.path.isfile(p) and os.path.isdir(os.path.dirname(os.path.abspath(p))):
            try:
                inputs[role] = p = os.path.join(os.path.dirname(os.path.abspath(p)), G.stored_name(p))
            except G.ClassicError:
                pass
        if not p or not os.path.isfile(p):
            raise G.ClassicError(f"dosya yok: {role}")
        if not p.lower().endswith(".xlsx"):
            raise G.ClassicError(f"yalnızca .xlsx: {role}")
    if registration is not None:
        if not os.path.isfile(registration):
            raise G.ClassicError(f"B2B kayıt dosyası bulunamadı: {os.path.basename(registration)}")
        if not registration.lower().endswith(".xlsx"):
            raise G.ClassicError("B2B kayıt dosyası .xlsx olmalı")
    out = os.path.abspath(out)
    if os.path.exists(out) and not overwrite:
        raise G.ClassicError("çıktı dosyası zaten var (üzerine yazmak için --overwrite)")
    for p in [*inputs.values(), *( [registration] if registration else [])]:
        if os.path.abspath(p) == out:
            raise G.ClassicError("çıktı bir girdi dosyası olamaz")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    every = {**inputs, **({"registration": registration} if registration else {})}
    before = {role: G._sha(p) for role, p in every.items()}
    names = {role: G.stored_name(p) for role, p in inputs.items()}
    if len(set(names.values())) != len(names):
        raise G.ClassicError("iki rapor aynı dosya adını taşıyor")
    with tempfile.TemporaryDirectory(prefix="klasik-b2b-") as tmp:
        folder = os.path.join(tmp, "girdi")
        os.mkdir(folder, 0o700)
        for role, p in inputs.items():
            dst = os.path.join(folder, names[role])
            shutil.copyfile(p, dst)
            if G._sha(dst) != before[role]:
                raise G.ClassicError(f"kopya doğrulanamadı: {role}")
        reg_copy, reg_name = "", REGISTRATION_NAME
        if registration:
            os.mkdir(os.path.join(tmp, "kayit"), 0o700)               # never in the folder the builder scans
            reg_name = G.stored_name(registration)
            reg_copy = os.path.join(tmp, "kayit", "kayit.xlsx")
            shutil.copyfile(registration, reg_copy)
            if G._sha(reg_copy) != before["registration"]:
                raise G.ClassicError("kopya doğrulanamadı: B2B kayıt")
        staged = os.path.join(tmp, "rapor.html")
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
        env["PYTHONHASHSEED"] = G.HASH_SEED
        r = subprocess.run([python or sys.executable, "-s", "-P", "-c", CHILD, G.HERE, folder, staged, day.isoformat(),
                            json.dumps(names, ensure_ascii=False), CORE, reg_copy, reg_name, str(int(review_days)),
                            "1" if registration_complete else "0"],
                           capture_output=True, text=True, env=env, cwd=tmp)
        last = (r.stdout.strip().splitlines() or ["{}"])[-1]
        try:
            res = json.loads(last)
        except ValueError:
            res = {}
        if r.returncode != 0 or "counts" not in res:
            code = res.get("error")
            msg = G.MESSAGES.get(code, "B2B klasik pano oluşturulamadı")
            raise G.ClassicError(msg.format(role=res.get("role", "")) if code else
                                 msg + (f" ({r.stderr.strip().splitlines()[-1][:200]})" if r.stderr.strip() else ""))
        shutil.move(staged, out)
    os.chmod(out, 0o600)
    after = {role: G._sha(p) for role, p in every.items()}
    if after != before:
        raise G.ClassicError("girdi dosyaları üretim sırasında değişti")
    return {"out": out, "sha256": G._sha(out), "date": day.isoformat(), "inputs": names,
            "registration": os.path.basename(registration) if registration else None, **res}


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python classic/klasik_b2b.py", description="Klasik Rapor + B2B Takip")
    ap.add_argument("--stock", required=True)
    ap.add_argument("--sales", required=True)
    ap.add_argument("--b2b-registration", help="b2b_kullanici_rapor.xlsx (B sütunu = kayıtlı müşteri kodu)")
    ap.add_argument("--cari")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", help="rapor tarihi YYYY-MM-DD (varsayılan: bugün)")
    ap.add_argument("--b2b-review-days", type=int, default=365, help="geçici 'son zamanlarda' süresi (gün)")
    ap.add_argument("--b2b-list-complete", action="store_true",
                    help="sahip beyanı: kayıt listesi tüm B2B kullanıcılarını içerir (listede olmayan kod 'YOK' gösterilir)")
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = generate_b2b(a.stock, a.sales, a.out, registration=a.b2b_registration, cari=a.cari,
                           overwrite=a.overwrite, review_days=a.b2b_review_days,
                           day=dt.date.fromisoformat(a.date) if a.date else None,
                           registration_complete=a.b2b_list_complete)
    except (G.ClassicError, ValueError) as e:
        print(f"B2B klasik rapor oluşturulamadı: {e}", file=sys.stderr)
        return 2
    b = res.get("b2b") or {}
    print(f"B2B klasik rapor hazır: {res['out']}")
    u = b.get("unified") or {}
    print(f"  B2B Takip — {u.get('rows')} satır (müşteri {u.get('customers')}, yalnız kayıt kodu "
          f"{u.get('registration_only')}) · B2B Kaydı {u.get('b2b_kaydi')} · kayıt listesi: "
          f"{b.get('registration_status')} · B2B satırı {b.get('b2b_lines')}")
    print(f"  SHA-256 {res['sha256'][:16]} · pandas {res['pandas']} · openpyxl {res['openpyxl']} · Python {res['python']}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
