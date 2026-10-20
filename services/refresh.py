"""Controlled monthly refresh (M5-5, D-019, D-032): validate → build staging → verify → atomic swap → report.

One implementation behind both `POST /api/context/refresh` and `python -m services.refresh`.

- Frozen sources: the stock, sales and (optional) aging workbooks are selected once (explicitly via
  `data/sources.json`, else the legacy modification-time choice, recorded as not explicit) and their BYTES are read
  once (M8-ACCURACY-2). Validation, the formula-aware inspection, the staged context, the staged dashboard and the
  generation manifest all come from those same bytes.
- The manifest (`<dashboard>.generation.json`: fingerprints, dates, validation counts and evidence; no values) is
  swapped together with the dashboard and installed together with the context; a failure keeps all three previous.
- Only the current M5 sources are refreshed. No M6 report is ingested.
- Nothing live changes before verification. The staged dashboard is written next to the live one
  (`dashboard.html.new`) and moved in with os.replace; the staged context is installed through
  ContextRepository.replace_context. `dashboard.prev.html` keeps the previous version.
- One refresh at a time, across processes: a non-blocking flock on `<dashboard>.refresh.lock`.
- Deterministic: no AI provider is imported or called. Action state, history and the AI cache are untouched.
"""
import argparse
import dataclasses
import datetime as dt
import fcntl
import logging
import os
import re
import shutil
import sys
import time
import warnings as warnings_module
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as C                     # noqa: E402
import dashboard_builder as db         # noqa: E402
from services import context as CTX    # noqa: E402
from services import generation as GEN  # noqa: E402
from services import request_pin as RP  # noqa: E402

log = logging.getLogger("refresh")

DEFAULT_DATA_FOLDER = os.environ.get("DATA_FOLDER", os.path.join(ROOT, "data"))
DEFAULT_DASHBOARD = os.environ.get("DASHBOARD_PATH", os.path.join(ROOT, "dashboard.html"))
# A count or total that falls by more than this share against the live data is reported as a warning
# (not an error: real business data can legitimately change a lot from one month to the next).
PLAUSIBILITY_DROP = float(os.environ.get("REFRESH_MAX_DROP", "0.5"))

# Columns the CURRENT deterministic pipeline reads (dashboard_builder + services/context.py).
STOCK_REQUIRED = [db.SC[k] for k in ("code", "name", "stock", "cov", "min", "max", "inv", "status")]
SALES_REQUIRED = ["Tarih", "Nettutar", "miktar", "sip_evrakno_seri", "sip_evrakno_sira", "Pro_kodu", "ProjeIsmi",
                  "Musterikod", "Musteri_ismi", "sto_kod", "StokIsmi", "anagrup"]
SALES_OPTIONAL = ["Net_Tutar_Maliyet_Dusulmus", "Temsilci", "Sektör", "Açık / Kapalı"]   # optional today; stay optional
AGING_REQUIRED = ["Hesap kodu", "Hesap adı", "Temsilci kodu"]
_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

STEP_TR = {"busy": "kilit", "validate": "doğrulama", "build": "oluşturma", "verify": "kontrol",
           "swap": "değiştirme", "done": "tamam"}
PREVIOUS_KEPT = "Önceki veriler kullanılmaya devam ediyor."


class RefreshBusy(Exception):
    pass


class StepError(Exception):
    def __init__(self, step, message):
        super().__init__(message)
        self.step, self.message = step, message


def paths_for(dashboard_path):
    root, ext = os.path.splitext(dashboard_path)
    mpath = GEN.manifest_path(dashboard_path)
    return {"live": dashboard_path, "staged": dashboard_path + ".new", "prev": f"{root}.prev{ext or '.html'}",
            "prev_tmp": f"{root}.prev{ext or '.html'}.tmp", "lock": dashboard_path + ".refresh.lock",
            "manifest": mpath, "manifest_staged": mpath + ".new", "manifest_prev_tmp": mpath + ".prev.tmp"}


# ---------------- one refresh at a time (server and CLI) ----------------
@contextmanager
def refresh_lock(lock_path):
    """Non-blocking exclusive flock. The kernel releases it if the process dies, so it can never go stale.
    The lock file is removed on a clean exit; an inode check prevents two holders after that removal."""
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RefreshBusy()
    try:
        try:
            same = os.stat(lock_path).st_ino == os.fstat(fd).st_ino
        except FileNotFoundError:
            same = False
        if not same:                       # the previous holder removed this file between our open and flock
            raise RefreshBusy()
        os.write(fd, str(os.getpid()).encode())
        try:
            yield
        finally:
            try:
                os.unlink(lock_path)       # still holding the lock: nobody else can own this inode
            except FileNotFoundError:
                pass
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------- 1. validate ----------------
def _check(checks, name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    return ok


def _read(checks, errors, label, path, reader, frozen=None):
    try:
        df = reader(frozen.stream() if frozen is not None else path)   # M8-ACCURACY-2: the frozen bytes
    except Exception as e:                 # not an Excel workbook, corrupt, locked …
        log.warning("refresh: cannot read %s (%s)", os.path.basename(path), type(e).__name__)
        _check(checks, f"{label}: okunabilir Excel", False, os.path.basename(path))
        errors.append(f"{label} dosyası okunamadı ({os.path.basename(path)}): geçerli bir Excel çalışma kitabı değil.")
        return None
    _check(checks, f"{label}: okunabilir Excel", True, os.path.basename(path))
    return df


def _columns(checks, errors, label, df, required, hint=""):
    missing = [c for c in required if c not in df.columns]
    _check(checks, f"{label}: gerekli sütunlar", not missing, ", ".join(missing))
    if missing:
        errors.append(f"{label} dosyasında gerekli sütunlar yok: {', '.join(repr(c) for c in missing)}.{hint}")
    return not missing


def live_dashboard_date(dashboard_path):
    """The data date embedded in the live dashboard (`data-asof`), or None. Read from our own markup."""
    try:
        with open(dashboard_path, encoding="utf-8") as f:
            m = re.search(r'<div id="ai" class="panel" data-asof="(\d{4}-\d{2}-\d{2})"', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def validate(sources, previous_date=None, today=None, frozen=None):
    """Validate the frozen selection. Returns dict(ok, checks, errors, warnings, frames, as_of_data_date).
    `frozen` ({key: FrozenSource}): parse these bytes instead of re-reading the files."""
    checks, errors, warnings, frames = [], [], [], {}
    frozen = frozen or {}
    today = today or dt.date.today()
    sp, ep, ap = sources.get("stock"), sources.get("sales"), sources.get("aging")

    # stock (header on row 4, as dashboard_builder reads it)
    if _check(checks, "Stok dosyası seçildi", bool(sp), os.path.basename(sp) if sp else ""):
        st = _read(checks, errors, "Stok", sp, db.read_stock, frozen.get("stock"))
        if st is not None and _columns(checks, errors, "Stok", st, STOCK_REQUIRED,
                                       f" Başlık satırı {db.STOCK_HEADER_ROW + 1}. satırda olmalı."):
            n = int(st[db.SC["code"]].notna().sum())
            if _check(checks, "Stok: ürün satırı var", n > 0, f"{n} satır"):
                frames["stock"] = st
            else:
                errors.append("Stok dosyasında ürün satırı yok.")
    else:
        errors.append("Stok dosyası bulunamadı (data klasöründe adında 'stok' geçen bir .xlsx olmalı).")

    # sales
    as_of = None
    if _check(checks, "Satış dosyası seçildi", bool(ep), os.path.basename(ep) if ep else ""):
        sa = _read(checks, errors, "Satış", ep, db.read_sales_raw, frozen.get("sales"))
        if sa is not None and _columns(checks, errors, "Satış", sa, SALES_REQUIRED):
            opt_missing = [c for c in SALES_OPTIONAL if c not in sa.columns]
            if opt_missing:
                warnings.append("Satış dosyasında isteğe bağlı sütunlar yok: " + ", ".join(opt_missing)
                                + ("; KAPALI müşteri ayrımı yapılamaz." if "Açık / Kapalı" in opt_missing else "."))
            if not _check(checks, "Satış: satır var", len(sa) > 0, f"{len(sa)} satır"):
                errors.append("Satış dosyasında satır yok.")
            else:
                with warnings_module.catch_warnings():          # mixed formats: unparseable cells become NaT
                    warnings_module.simplefilter("ignore")
                    dates = db.pd.to_datetime(sa["Tarih"], errors="coerce")
                valid = int(dates.notna().sum())
                if _check(checks, "Satış: geçerli tarih", valid > 0, f"{valid}/{len(sa)} satır"):
                    as_of = dates.max().date().isoformat()
                    frames["sales"] = sa
                    if valid < len(sa):
                        warnings.append(f"Satış dosyasında {len(sa) - valid} satırın tarihi okunamadı.")
                    if dates.max().date() > today:
                        warnings.append(f"Satış verisinin en son tarihi ({as_of}) bugünden ileride.")
                else:
                    errors.append("Satış dosyasında okunabilir tarih yok; veri tarihi belirlenemedi.")
    else:
        errors.append("Satış dosyası bulunamadı (data klasöründe adında 'sat' geçen bir .xlsx olmalı).")

    # aging (optional, as today; validated when selected)
    if ap:
        ag = _read(checks, errors, "Cari (yaşlandırma)", ap, db.read_aging_raw, frozen.get("aging"))
        if ag is not None:
            cols = [str(c).strip() for c in ag.columns]
            view = ag.copy()
            view.columns = cols
            if _columns(checks, errors, "Cari (yaşlandırma)", view, AGING_REQUIRED):
                buckets = [c for c in cols if "öncesi" in c or any(m in c for m in _MONTHS)]
                single = next((c for c in cols if "vadesi geçen" in c.lower()), None)
                if not _check(checks, "Cari: yaşlandırma sütunları", bool(buckets or single),
                              f"{len(buckets)} dönem sütunu" if buckets else (single or "")):
                    errors.append("Cari dosyasında yaşlandırma sütunları (ay dönemleri veya 'Vadesi geçen bakiye') yok.")
                accounts = int(view["Hesap kodu"].astype(str).str.match(r"^\d", na=False).sum())
                if _check(checks, "Cari: hesap satırı var", accounts > 0, f"{accounts} hesap"):
                    if buckets or single:
                        frames["aging"] = ag
                else:
                    errors.append("Cari dosyasında hesap satırı yok.")
    else:
        _check(checks, "Cari dosyası", True, "yok (isteğe bağlı; tahsilat bölümleri boş kalır)")
        warnings.append("Cari (yaşlandırma) dosyası yok; tahsilat ve risk bölümleri oluşturulmaz.")

    # data-date safety: never older than the live data
    if as_of:
        if previous_date:
            ok = as_of >= previous_date
            _check(checks, "Veri tarihi eski değil", ok, f"yeni {as_of}, mevcut {previous_date}")
            if not ok:
                errors.append(f"Yeni veri tarihi ({as_of}) mevcut veri tarihinden ({previous_date}) eski.")
        else:
            _check(checks, "Veri tarihi eski değil", True, "önceki veri tarihi yok (ilk oluşturma)")
    return {"ok": not errors, "checks": checks, "errors": errors, "warnings": warnings,
            "frames": frames, "as_of_data_date": as_of}


# ---------------- 3. verify ----------------
def context_counts(ctx):
    """Counts/totals taken from a built context (staged or live) for the report and plausibility."""
    if not ctx:
        return {}
    wc = (ctx.get("company") or {}).get("working_capital") or {}
    return {"products": (ctx.get("company") or {}).get("products_scanned"),
            "low_stock": wc.get("low_stock_count"), "lapsed": wc.get("lapsed_count"),
            "overstock_value": wc.get("overstock_value"), "overdue_total": wc.get("overdue_total"),
            "actions": len(ctx.get("actions") or [])}


_COUNT_TR = {"products": "Ürün", "low_stock": "Azalan stok", "lapsed": "Kayıp müşteri", "overstock_value": "Fazla stok değeri",
             "overdue_total": "Vadesi geçen toplam", "actions": "Aksiyon"}


def verify(ctx, stats, html_path, expected_as_of, previous_context=None, drop=PLAUSIBILITY_DROP):
    errors, warnings = [], []
    wc = ctx["company"]["working_capital"]
    # same data date everywhere
    if not (ctx.get("as_of_data_date") == stats.get("asof") == expected_as_of):
        errors.append("Bağlam ve pano farklı veri tarihleri üretti.")
    # context ↔ dashboard reconciliation (both from the same dashboard_builder results)
    pairs = [("vadesi geçen toplam", wc.get("overdue_total"), round(stats.get("overdue_total", 0))),
             ("fazla stok değeri", wc.get("overstock_value"), round(stats.get("overstock_value", 0))),
             ("kayıp müşteri sayısı", wc.get("lapsed_count"), stats.get("lapsed")),
             ("azalan stok sayısı", wc.get("low_stock_count"), stats.get("low")),
             ("ürün sayısı", ctx["company"].get("products_scanned"), stats.get("products"))]
    for label, a, b in pairs:
        if a != b:
            errors.append(f"Bağlam ve pano tutmuyor: {label}.")
    if not stats.get("products"):
        errors.append("Oluşturulan veride ürün yok.")
    # M8-ACCURACY-2: the staged dashboard, the staged context and the manifest describe one generation
    m = getattr(ctx, "generation_manifest", None)
    if m is not None:
        if (getattr(ctx, "generation", None) or {}).get("core_id") != m.core_id or stats.get("generation") != m.core_id:
            errors.append("Pano, bağlam ve kaynak üretimi (manifest) aynı veri üretimine ait değil.")
    # staged dashboard structure
    try:
        size = os.path.getsize(html_path)
        with open(html_path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        size, html = 0, ""
    if size < 1000 or not html.rstrip().endswith("</html>"):
        errors.append("Oluşturulan pano boş veya eksik.")
    else:
        need = ['<div class="tabs">', "const DATA=", '<div id="panels"></div>', '<button class="tab" data-t="ai">',
                f'<div id="ai" class="panel" data-asof="{expected_as_of}"']
        need += [f'data-t="{t}"' for t in ["overview"] + list(stats.get("tabs") or [])]
        missing = [n for n in need if n not in html]
        if missing:
            errors.append(f"Oluşturulan panoda beklenen yapı eksik ({len(missing)} öğe).")
    # plausibility against the live data (warnings only)
    new, old = context_counts(ctx), context_counts(previous_context)
    from services import accuracy as ACC
    gate = ACC.current_gate()
    for k, v in new.items():
        p = old.get(k)
        if p and v is not None and p > 0 and v < p * (1 - drop):
            mk = ACC.REFRESH_COUNT_METRIC.get(k, "unregistered")
            if mk is None or (mk != "unregistered" and gate.displayable(mk)):
                warnings.append(f"{_COUNT_TR[k]} belirgin düştü: {p:,} → {v:,}.".replace(",", "."))
            else:   # M8-ACCURACY: an unverified total is never printed, not even in a warning
                warnings.append(f"{_COUNT_TR[k]} önceki veriye göre belirgin düştü (değer doğrulanmadığı için gösterilmiyor).")
    if previous_context is None:
        warnings.append("Karşılaştırılacak canlı bağlam yok; sayı makullük kontrolü atlandı.")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "counts": new}


# ---------------- pipeline ----------------
def _report(**kw):
    base = {"ok": False, "step": "validate", "step_tr": "", "message": "", "sources": {"stock": None, "sales": None, "aging": None},
            "previous_as_of_data_date": None, "candidate_as_of_data_date": None, "as_of_data_date": None,
            "counts": {}, "warnings": [], "errors": [], "checks": [], "previous_data_kept": True, "duration_ms": 0}
    base.update(kw)
    base["step_tr"] = STEP_TR.get(base["step"], base["step"])
    return base


def _restore_previous(p, had_live, had_manifest):
    """Put the previous dashboard AND its manifest back (they describe one generation). True when restored."""
    ok = True
    for live, tmp, had in (("live", "prev_tmp", had_live), ("manifest", "manifest_prev_tmp", had_manifest)):
        try:
            if had and os.path.exists(p[tmp]):
                os.replace(p[tmp], p[live])
            elif not had:
                _remove(p[live])
        except Exception:
            log.exception("refresh: could not restore the previous %s", live)
            ok = False
    return ok and had_live


def _after_manifest_swap():
    """Test seam between the two file replacements (a crash here leaves a new manifest beside the old dashboard)."""


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("refresh: could not remove a staging file (%s)", type(e).__name__)


def run_refresh(data_folder=None, dashboard_path=None, *, previous_context=None, install=None,
                inflation_rate=None, today=None, drop=None):
    """The one refresh pipeline. `install(staged_ctx)` makes the verified context live (the API passes
    ContextRepository.replace_context; the CLI passes nothing). Returns the report dict. Never raises."""
    t0 = time.monotonic()
    data_folder = data_folder or DEFAULT_DATA_FOLDER
    dashboard_path = dashboard_path or DEFAULT_DASHBOARD
    p = paths_for(dashboard_path)
    try:
        with refresh_lock(p["lock"]):
            rep = _pipeline(data_folder, p, previous_context, install, inflation_rate, today,
                            PLAUSIBILITY_DROP if drop is None else drop)
    except RefreshBusy:
        rep = _report(step="busy", message="Başka bir yenileme sürüyor; bitmesini bekleyin.",
                      errors=["Başka bir yenileme sürüyor."],
                      previous_as_of_data_date=_prev_date(previous_context, dashboard_path),
                      as_of_data_date=_prev_date(previous_context, dashboard_path))
    rep["duration_ms"] = int((time.monotonic() - t0) * 1000)
    log.info("refresh: ok=%s step=%s date=%s (%d ms)", rep["ok"], rep["step"], rep["as_of_data_date"], rep["duration_ms"])
    from services import accuracy as ACC
    return ACC.redact_refresh_report(rep, ACC.current_gate())   # M8-ACCURACY: API and CLI never print unverified totals


def _prev_date(previous_context, dashboard_path):
    dates = [d for d in ((previous_context or {}).get("as_of_data_date"), live_dashboard_date(dashboard_path)) if d]
    return max(dates) if dates else None


def _pipeline(data_folder, p, previous_context, install, inflation_rate, today, drop):
    prev_date = _prev_date(previous_context, p["live"])
    common = {"sources": {"stock": None, "sales": None, "aging": None}, "previous_as_of_data_date": prev_date,
              "as_of_data_date": prev_date}
    warnings = []
    for k in ("staged", "prev_tmp", "manifest_staged", "manifest_prev_tmp"):
        _remove(p[k])
    try:
        # 0. explicit selection, one frozen read of every selected workbook (M8-ACCURACY-2)
        try:
            sources, declarations, origin = GEN.select_core(data_folder)
        except GEN.SelectionError as e:
            raise StepError("validate", f"Kaynak seçimi geçersiz: {e}.") from None
        common["sources"] = {k: (os.path.basename(v) if v else None) for k, v in sources.items()}
        try:
            frozen = GEN.freeze(sources)
        except OSError:
            raise StepError("validate", "Seçilen kaynak dosyalarından biri okunamadı.") from None
        if origin != "explicit":
            warnings.append("Kaynak dosyalar açıkça seçilmedi (data/sources.json yok); dosya değişiklik tarihine "
                            "göre seçim yapıldı. Bu üretim doğrulama kanıtı sayılmaz.")
        # 1. validate — nothing live is touched
        v = validate(sources, prev_date, today, frozen=frozen)
        common.update(candidate_as_of_data_date=v["as_of_data_date"], checks=v["checks"])
        if not v["ok"]:
            raise StepError("validate", v["errors"][0] if len(v["errors"]) == 1
                            else f"{v['errors'][0]} (+{len(v['errors']) - 1} hata daha)") from None
        warnings += v["warnings"]
        frames = v["frames"]

        # 2. build into staging (in memory + dashboard.html.new)
        try:
            manifest = GEN.build_manifest(frozen, frames, declarations, origin, today)
            staged = CTX.build_context(data_folder, inflation_rate=inflation_rate, sources=sources, frames=frames)
            staged.generation = {"core_id": manifest.core_id, "served": {r.key: r.sha256 for r in manifest.core}}
            staged.generation_manifest = manifest
            stats = {}
            db.build(data_folder, p["staged"], today=today, sources=sources, frames=frames, stats_out=stats,
                     manifest=manifest)
            # the exact HTML bytes published with this generation (GET / serves only these bytes for it)
            manifest = dataclasses.replace(manifest, dashboard_sha256=GEN.sha256_file(p["staged"]))
            staged.generation_manifest = manifest
            GEN.write_manifest(p["manifest_staged"], manifest)
        except Exception as e:
            log.exception("refresh: build failed")
            raise StepError("build", f"Bağlam veya pano oluşturulamadı ({type(e).__name__}).") from None

        # 3. verify the staged result before anything goes live
        ver = verify(staged, stats, p["staged"], v["as_of_data_date"], previous_context, drop)
        warnings += ver["warnings"]
        common["counts"] = ver["counts"]
        if not ver["ok"]:
            common["errors"] = ver["errors"]
            raise StepError("verify", ver["errors"][0])

        # 4. publish: dashboard, manifest and context as ONE step for every in-process reader (request_pin
        #    PUBLISH_LOCK — a request captures its gate inputs under the same lock). On any failure the previous
        #    dashboard, manifest and context are restored inside the same step. Another process (a restart) can see
        #    the two files mid-swap; restore then refuses the pair (dashboard / manifest generation mismatch).
        had_live = os.path.exists(p["live"])
        had_manifest = os.path.exists(p["manifest"])
        with RP.PUBLISH_LOCK:
            try:
                if had_live:
                    shutil.copy2(p["live"], p["prev_tmp"])
                if had_manifest:
                    shutil.copy2(p["manifest"], p["manifest_prev_tmp"])
                os.replace(p["manifest_staged"], p["manifest"])
                _after_manifest_swap()
                os.replace(p["staged"], p["live"])
            except Exception as e:
                log.exception("refresh: dashboard swap failed")
                _restore_previous(p, had_live, had_manifest)
                raise StepError("swap", f"Pano değiştirilemedi ({type(e).__name__}).") from None
            if install is not None:
                try:
                    install(staged)
                except Exception as e:
                    log.exception("refresh: context install failed")
                    restored = _restore_previous(p, had_live, had_manifest)
                    raise StepError("swap", f"Yeni bağlam devreye alınamadı ({type(e).__name__}); "
                                    + ("önceki pano geri yüklendi." if restored else "pano geri yüklenemedi.")) from None
        if had_live:
            try:
                os.replace(p["prev_tmp"], p["prev"])
            except Exception:
                warnings.append("Önceki pano yedeği (dashboard.prev.html) yazılamadı.")
        msg = f"Veriler yenilendi. Yeni veri tarihi: {v['as_of_data_date']}."
        return _report(ok=True, step="done", message=msg, previous_data_kept=False, warnings=warnings,
                       **{**common, "as_of_data_date": v["as_of_data_date"]})
    except StepError as e:
        kept = e.step != "swap" or "geri yüklendi" in e.message
        return _report(step=e.step, message=e.message, warnings=warnings, previous_data_kept=kept,
                       **{"errors": [e.message], **common})
    except Exception as e:                                         # anything unexpected: still a clean report
        log.exception("refresh: unexpected failure")
        return _report(step="build", message=f"Beklenmeyen hata ({type(e).__name__}).", **common)
    finally:
        for k in ("staged", "prev_tmp", "manifest_staged", "manifest_prev_tmp"):
            _remove(p[k])


# ---------------- CLI ----------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m services.refresh",
                                 description="Controlled refresh of the dashboard from the exports in the data folder "
                                             "(validate → build → verify → swap). While the app is running, prefer the "
                                             "'↻ Verileri yenile' button: it also updates the running server.")
    ap.add_argument("--data", default=None, help="data folder (default: DATA_FOLDER or ./data)")
    ap.add_argument("--dashboard", default=None, help="live dashboard path (default: DASHBOARD_PATH or ./dashboard.html)")
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = ap.parse_args(argv)
    rep = run_refresh(args.data, args.dashboard, inflation_rate=C.MANUAL_INFLATION_RATE)
    if args.json:
        import json
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        s = rep["sources"]
        print(("OK" if rep["ok"] else f"BAŞARISIZ ({rep['step_tr']})") + f": {rep['message']}")
        print(f"  Dosyalar: stok={s.get('stock')} · satış={s.get('sales')} · cari={s.get('aging') or '—'}")
        print(f"  Veri tarihi: önceki={rep['previous_as_of_data_date'] or '—'} · şimdi={rep['as_of_data_date'] or '—'}")
        if rep["counts"]:
            print("  Sayılar: " + ", ".join(f"{k}={v}" for k, v in rep["counts"].items()))
        for w in rep["warnings"]:
            print(f"  Uyarı: {w}")
        for e in rep["errors"][1:]:
            print(f"  Hata: {e}")
        if not rep["ok"]:
            print(f"  {PREVIOUS_KEPT}" if rep["previous_data_kept"] else "  Önceki pano geri yüklenemedi; kontrol edin.")
    return 0 if rep["ok"] else (3 if rep["step"] == "busy" else 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    sys.exit(main())
