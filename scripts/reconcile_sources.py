"""Owner-local, READ-ONLY source reconciliation (M8-ACCURACY-2). Run on the owner's Mac against the private exports.

    python -m scripts.reconcile_sources --kredi "data/kredi takip(1).xlsx" --today 2026-10-19
    python -m scripts.reconcile_sources --kredi "data/kredi takip(1).xlsx" --show-values        # amounts: local only
    python -m scripts.reconcile_sources --core data                                             # stock / sales / Cari
    python -m scripts.reconcile_sources --kredi FILE --expect-sha256 <hash> --evidence-out     # → data/reconciliation/
    python -m scripts.reconcile_sources --core data --metric-diagnostics   # + read-only strict vs per-metric view

Guarantees:
- reads the workbook bytes once and never writes to, repairs or re-saves any workbook;
- no AI provider, no network, nothing uploaded; nothing is written unless `--evidence-out` is given, and that file
  holds fingerprints, counts and PASS / FAIL / OPEN results only (never amounts, codes, bank or customer names).
  Without a path it goes to the git-ignored `data/reconciliation/`; an explicit path inside this repository must be
  under `data/` (refused otherwise, so private evidence is never left where it could be committed);
- default console output is privacy-safe (counts, fingerprints, results). `--show-values` prints amount totals to the
  LOCAL console for the owner; never paste that output into GitHub, chat or an AI tool;
- the workbook's saved Excel filter is reported as a view and never treated as proof that hidden rows are settled;
- TECHNICAL validation only. A PASS here is not a financial / accounting approval and does not verify bank liabilities.

Overall result (printed last, and `overall` in the evidence file):
  PASS = every check passed;  OPEN = no technical failure, but business questions remain (owner / accountant decision);
  FAIL = at least one technical check failed.
Exit code: 0 = PASS, 1 = FAIL, 2 = unreadable input / usage error / refused evidence path, 3 = OPEN,
4 = the checks ran but the evidence file could not be written (nothing else was written).

`--metric-diagnostics` (M8-R3, opt-in, with `--core`): after the overall result, a READ-ONLY comparison of the strict
source-wide result with the actual gate decision of each candidate metric (services/metric_diagnostics.py), from the
same manifest and the same frozen bytes. It adds no check, never changes the overall result or the exit code, promotes
nothing and writes nothing (with `--evidence-out` the evidence file gains a `metric_diagnostics` section).

Evidence output never touches an input: the path is refused if it is (or links to) the Kredi workbook, the selection
file or any workbook in the core folder, or if anything already exists there; the file is created exclusively
(O_CREAT | O_EXCL), so no existing file can be truncated.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BANNER = ("Teknik doğrulama: kaynak yapısı ve aritmetik. Bu bir finansal / muhasebe onayı DEĞİLDİR ve banka "
          "yükümlülüklerini doğrulamaz.")


class Out:
    def __init__(self, show_values: bool):
        self.show_values = show_values
        self.results: list[dict] = []
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        self.lines.append(text)
        print(text)

    def check(self, code: str, status: str, text: str, **counts) -> None:
        """status: PASS / FAIL / OPEN (an unresolved business question; not a technical failure)."""
        self.results.append({"code": code, "status": status, **{k: v for k, v in counts.items()
                                                                 if isinstance(v, (int, bool)) or v is None}})
        extra = " · ".join(f"{k}={v}" for k, v in counts.items() if isinstance(v, (int, bool)) or v is None)
        self.line(f"  [{status:4}] {text}" + (f"  ({extra})" if extra else ""))

    def money(self, label: str, totals: dict) -> None:
        if not self.show_values:
            return
        for cur, t in sorted(totals.items(), key=lambda kv: str(kv[0])):
            parts = ", ".join(f"{f}={t[f]:,.2f}" if isinstance(t.get(f), float) else f"{f}={t.get(f)}"
                              for f in ("installment_total", "principal", "interest", "bsmv", "kkdf", "paid_amount",
                                        "remaining_amount"))
            self.line(f"      {label} [{cur}] {t['count']} taksit: {parts}")


def _today(v: str | None) -> dt.date:
    return dt.date.fromisoformat(v) if v else dt.date.today()


def reconcile_kredi(path: str, out: Out, *, today: dt.date, snapshot: dt.date | None, expect_sha: str | None) -> dict:
    from services import generation as GEN
    from services import kredi_schedule as KS
    from services.importers import kredi as K
    fs = GEN.FrozenSource.read("kredi", path)
    out.line(f"Kredi kaynağı: {fs.file}")
    out.line(f"  SHA-256: {fs.sha256}")
    out.line(f"  Dosya değişiklik zamanı (yalnızca bilgi; iş tarihi DEĞİL): {fs.file_modified}")
    if expect_sha:
        out.check("expected_fingerprint", "PASS" if expect_sha == fs.sha256 else "FAIL",
                  "Bilinen kaynak dosya ile aynı içerik (parmak izi)")
    res = K.import_kredi(path, snapshot_date=snapshot, content=fs.data)
    c = res.counts
    out.check("layout", "PASS" if c.layout else "FAIL", f"Düzen tanındı: {c.layout or 'desteklenmiyor'}")
    if not res.ok:
        codes = sorted(set(res.report.codes("error")))
        out.check("import", "FAIL", "Kaynak doğrulanamadı; hata kodları: " + ", ".join(codes))
        return {"file": fs.file, "sha256": fs.sha256, "ok": False}
    out.check("import", "PASS", "Tüm satırlar içe aktarıldı ve doğrulandı (gizli satırlar dahil)",
              installments=c.installments, contracts=c.contracts)
    out.check("saved_filter", "PASS", "Kaydedilmiş filtre görünümü okundu (bir görünümdür, ödeme bilgisi değildir)",
              saved_filter=c.saved_filter,
              visible_rows=c.visible_installment_rows, hidden_rows=c.hidden_installment_rows)
    out.check("control_totals", "PASS" if c.control_totals_match else ("FAIL" if c.control_total_row else "OPEN"),
              "ERP kontrol toplamı satırı = tüm kaynak satırlarının toplamı", present=c.control_total_row)
    if c.subtotal_row:
        st = "PASS" if c.filtered_totals_match is True else ("FAIL" if c.filtered_totals_match is False else "OPEN")
        out.check("filtered_subtotals", st, "SUBTOTAL hücreleri = görünür satırların toplamı (yalnızca filtre görünümü)",
                  **{f"subtotal_{k}": v for k, v in c.subtotal_checks.items()})
    for rule, label in (("installment_components", "Anapara + faiz + BSMV + KKDF = taksit"),
                        ("paid_plus_remaining", "Ödenen + kalan = taksit")):
        r = c.rules.get(rule, {})
        out.check(rule, "PASS" if r.get("failed") == 0 else "FAIL", label, checked=r.get("checked"),
                  failed=r.get("failed"))
    cats = KS.categorize(res.installments, today)
    view = KS.saved_view_reconciliation(res.installments, res.contracts, today)
    hp = view["hidden_with_positive_remaining"]
    out.check("hidden_positive_remaining", "OPEN" if hp["count"] else "PASS",
              "Filtrede gizli olup kalan tutarı pozitif taksitler (ödendiği VARSAYILMAZ)", count=hp["count"],
              past_due=hp["past_due"], due_today=hp["due_today"], future=hp["future"])
    out.check("negative_remaining", "OPEN" if c.negative_remaining_rows else "PASS",
              "Negatif kalan tutar (sıfır sayılmaz, düzeltilmez)", count=c.negative_remaining_rows)
    out.check("kapali_positive_remaining", "OPEN" if c.closed_with_positive_remaining else "PASS",
              "DURUM 'Kapalı' ama kalan tutar pozitif (DURUM anlamı doğrulanmadı)", count=c.closed_with_positive_remaining)
    out.check("past_due_positive_remaining", "OPEN" if cats[KS.PAST_DUE_REMAINING] else "PASS",
              f"Vadesi {today.isoformat()} tarihinden önce geçmiş, kalan tutarı pozitif taksitler",
              count=len(cats[KS.PAST_DUE_REMAINING]))
    out.check("due_today_positive_remaining", "OPEN" if cats[KS.DUE_TODAY_REMAINING] else "PASS",
              "Bugün vadesi gelen, kalan tutarı pozitif taksitler", count=len(cats[KS.DUE_TODAY_REMAINING]))
    out.check("snapshot_date", "PASS" if snapshot else "OPEN",
              "Kaynağın iş anlık tarihi " + ("beyan edildi" if snapshot else "bilinmiyor (dosya zamanı kullanılmaz)"))
    out.check("currencies", "PASS", "Para birimleri ayrı tutulur (kur çevirisi yok)", currencies=len(c.currencies))
    out.line("  Görünür satırlar ay bazında (filtre görünümü):")
    for key, t in view["visible_by_month"].items():
        out.line(f"      {key[0]} {key[1]}-{key[2]:02d}: {t['count']} taksit" if key[2] else f"      {key}: {t['count']}")
    out.money("Tüm kaynak", view["full_source_totals"])
    out.money("Görünür (filtre)", view["visible_totals"])
    out.money("Gizli, kalan > 0", hp["totals"])
    if out.show_values:
        for key, t in view["visible_by_month"].items():
            out.money(f"Görünür {key[1]}-{key[2]:02d}", {key[0]: t})
    out.line("  Not: seçili tarih aralığının toplamı şirketin toplam borcu değildir; planlanan ödeme, gerçekleşecek "
             "ödeme anlamına gelmez; bu bir nakit akış tahmini değildir.")
    return {"file": fs.file, "sha256": fs.sha256, "ok": True}


def reconcile_core(data_folder: str, out: Out, *, today: dt.date, keep: dict | None = None) -> dict:
    from services import generation as GEN
    from services import refresh as RF
    try:
        paths, decl, origin = GEN.select_core(data_folder)
    except GEN.SelectionError as e:
        out.check("selection", "FAIL", f"Kaynak seçimi geçersiz: {e}")
        return {"ok": False}
    out.check("selection", "PASS" if origin == "explicit" else "OPEN",
              "Kaynaklar açıkça seçildi (data/sources.json)" if origin == "explicit" else
              "data/sources.json yok: değişiklik zamanına göre seçim (kanıt sayılmaz)")
    frozen = GEN.freeze(paths)
    v = RF.validate(paths, None, today, frozen=frozen)
    out.check("refresh_validation", "PASS" if v["ok"] else "FAIL", "Yenileme doğrulaması", errors=len(v["errors"]))
    if not v["ok"]:
        return {"ok": False}
    m = GEN.build_manifest(frozen, v["frames"], decl, origin, today)
    if keep is not None:          # M8-R3 diagnostics: this exact manifest + an independent hash of the bytes read
        served = {k: GEN.sha256_bytes(fs.data) for k, fs in frozen.items()}
        if "sales" in served:
            served["liste"] = served["sales"]                   # the master is a sheet of the sales workbook
        keep.update(manifest=m, served=served)
    for r in m.core:
        out.line(f"{r.key}: {r.file} · SHA-256 {r.sha256}")
        rows = (r.validation or {}).get("rows") if r.key == "stock" else None
        if rows:
            _stock_rows(out, rows)
        ev = r.evidence
        rd = ev.report_date
        out.check(f"{r.key}:report_date", "PASS" if rd else "OPEN",
                  f"İş tarihi ({r.date_kind}): " + (f"beyan edildi {rd.isoformat()}" if rd else
                                                     "beyan edilmedi (dosya zamanı kullanılmaz)"))
        if ev.latest_transaction_date:
            out.line(f"      en son işlem tarihi (gözlenen; rapor tarihi değildir): {ev.latest_transaction_date}")
        for st, why in ev.problems():
            out.check(f"{r.key}:evidence", "FAIL" if st.value == "invalid" else "OPEN", why)
        if not ev.problems():
            out.check(f"{r.key}:evidence", "PASS", "Kaynak kanıtında sorun yok")
    out.line("Eşleştirme raporu (sayılar; yeni eşleştirme kuralı yok, D-003 korunur):")
    for k, val in sorted(m.joins.items()):
        out.line(f"      {k}: {val}")
    return {"ok": True, "core_id": m.core_id, "sources": [{"key": r.key, "file": r.file, "sha256": r.sha256}
                                                           for r in m.core]}


def _stock_rows(out: Out, rows: dict) -> None:
    """M8-ACCURACY-2B: stock row diagnostics (counts only) — report-control rows are recognized, reconciled and never
    counted as products; nothing is dropped, repaired or deduplicated."""
    from services import source_validation as SV
    counts = rows.get("counts") or {}
    out.line("  Stok satırları (sayılar):")
    for k, label in SV.StockRowAnalysis.LABELS_TR.items():
        out.line(f"      {label}: {counts.get(k)}")
    fk, chk = rows.get("footer_kinds") or {}, rows.get("footer_checks") or {}
    out.line(f"      tanınan toplam satırı türleri: toplam formülü={fk.get('aggregate', 0)} · etiket={fk.get('label', 0)}"
             f" · belirsiz={rows.get('ambiguous_rows', 0)}")
    # PASS only when EVERY checked footer cell reconciled (matched == checked); a contradiction is FAIL; anything
    # partial (error-propagated, missing cache, unsupported, ambiguous rows, nothing checked) is OPEN — the matched
    # and the unreconciled counts stay visible, and a partial reconciliation never verifies the totals
    st = rows.get("footer_status") or "OPEN"
    out.check("stock_footer_totals", st, "Rapor toplam satırları = kapsadıkları ürün satırlarının toplamı "
              "(tümü eşleşmeden PASS yok)", **{f"footer_{k}": v for k, v in chk.items()})
    for col, c in sorted((rows.get("footer_by_column") or {}).items()):
        if c.get("matched", 0) != c.get("checked", 0):       # only the columns that did not fully reconcile
            out.line("      toplamı doğrulanamayan sütun: {0} · kontrol={1} · eşleşen={2} · çelişen={3} · "
                     "hata yayılımı={4} · önbelleksiz={5} · desteklenmeyen={6}".format(
                         col, c.get("checked"), c.get("matched"), c.get("mismatched"), c.get("error_propagated"),
                         c.get("missing_cache"), c.get("unsupported")))
    t = rows.get("template") or {}
    tstat = t.get("status")
    out.check("stock_identity_header",
              "PASS" if tstat in ("approved_template", "single_identity_header") else "FAIL",
              "Ürün kodu sütunu belirlenebilir: tek 'Stok_Kodu' başlığı ya da sahip onaylı şablon (D-056; A = ürün "
              f"kodu, J = menşe ülke) — onaysız/değişmiş düzen reddedilir · durum={tstat} · şablon={t.get('template')}"
              f" · ürün kodu sütunu={t.get('identity_column')} · menşe ülke sütunu={t.get('country_of_origin_column')}",
              columns=t.get("columns"), identity_headers=t.get("identity_header_columns"))
    cov = rows.get("coverage") or {}
    if cov:
        out.line("      STOK Kontrol % (kapsam): tanımsız (#DIV/0!)={0} · diğer hata={1} · boş={2} · sayı={3} · "
                 "tanımsız ama stoklu={4} · stok miktarı eksik={5}".format(
                     cov.get("undefined_div0"), cov.get("other_error"), cov.get("blank"), cov.get("numeric"),
                     cov.get("undefined_with_stock"), cov.get("stock_quantity_missing")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.reconcile_sources", description=__doc__.split("\n")[0])
    ap.add_argument("--kredi", help="Kredi workbook (private, local path)")
    ap.add_argument("--core", help="data folder with the core ERP exports (and optional sources.json)")
    ap.add_argument("--today", help="reference date YYYY-MM-DD (default: local today)")
    ap.add_argument("--snapshot-date", help="declared Kredi snapshot date YYYY-MM-DD (unknown if omitted)")
    ap.add_argument("--expect-sha256", help="fail unless the Kredi workbook has exactly this fingerprint")
    ap.add_argument("--show-values", action="store_true", help="print amount totals to the LOCAL console only")
    ap.add_argument("--metric-diagnostics", action="store_true",
                    help="with --core: read-only strict vs per-metric gate comparison (never changes the result)")
    ap.add_argument("--evidence-out", nargs="?", const="", default=None,
                    help="write privacy-safe evidence JSON (fingerprints, counts, results); without a path: "
                         "data/reconciliation/ (git-ignored); inside the repository only under data/")
    a = ap.parse_args(argv)
    if not (a.kredi or a.core):
        ap.print_usage()
        return 2
    evidence_path = None
    if a.evidence_out is not None:
        evidence_path = _evidence_path(a.evidence_out, "kredi" if a.kredi and not a.core else
                                       ("core" if a.core and not a.kredi else "sources"))
        if evidence_path is None:
            print("Kanıt dosyası bu depo içinde yalnızca git'e girmeyen data/ klasörüne yazılabilir "
                  "(veya depo dışına). Örnek: --evidence-out (varsayılan: data/reconciliation/).")
            return 2
        clash = _evidence_clash(evidence_path, _protected_inputs(a.kredi, a.core))
        if clash:
            print(clash)
            return 2
    try:
        today = _today(a.today)
        snap = dt.date.fromisoformat(a.snapshot_date) if a.snapshot_date else None
    except ValueError:
        print("Tarih YYYY-AA-GG olmalı.")
        return 2
    out = Out(a.show_values)
    out.line(BANNER)
    out.line(f"Referans tarihi: {today.isoformat()}")
    summary: dict = {"tool": "reconcile_sources", "version": 1, "today": today.isoformat(), "results": out.results}
    try:
        if a.kredi:
            summary["kredi"] = reconcile_kredi(a.kredi, out, today=today, snapshot=snap, expect_sha=a.expect_sha256)
        keep: dict | None = {} if (a.metric_diagnostics and a.core) else None
        if a.core:
            summary["core"] = reconcile_core(a.core, out, today=today, keep=keep)
    except OSError:
        print("Dosya okunamadı.")
        return 2
    failed = sum(1 for r in out.results if r["status"] == "FAIL")
    open_ = sum(1 for r in out.results if r["status"] == "OPEN")
    overall = "FAIL" if failed else ("OPEN" if open_ else "PASS")
    summary["overall"], summary["failed"], summary["open"] = overall, failed, open_
    out.line(f"Sonuç: {failed} teknik hata, {open_} çözülmemiş konu (sahip / muhasebe kararı gerekli).")
    out.line(f"GENEL SONUÇ: {overall} — " + {
        "PASS": "tüm teknik kontroller geçti; finansal onay değildir.",
        "OPEN": "teknik hata yok; çözülmemiş iş soruları var (doğrulanmış sayılmaz).",
        "FAIL": "en az bir teknik kontrol başarısız (kaynak doğrulanmadı)."}[overall])
    if a.metric_diagnostics:
        summary["metric_diagnostics"] = _metric_diagnostics(keep, out.results, overall, today)
    if evidence_path:
        err = _write_evidence_exclusive(evidence_path, summary, _protected_inputs(a.kredi, a.core))
        if err:
            out.line(f"KANIT DOSYASI YAZILAMADI: {err} Sonuçlar yukarıdadır; kaynak dosyalara dokunulmadı.")
            return 4
        out.line(f"Kanıt dosyası yazıldı (değer yok): {_shown(evidence_path)}")
    return {"PASS": 0, "FAIL": 1, "OPEN": 3}[overall]


def _metric_diagnostics(keep: dict | None, results: list[dict], overall: str, today: dt.date) -> dict:
    """M8-R3: printed and returned only; it never touches `results`, so the overall result and exit code are the
    ones computed above."""
    from services import metric_diagnostics as MD
    if not keep or keep.get("manifest") is None:
        msg = ("METRİK TANILAMA: kullanılamaz — " + ("yalnızca --core ile çalışır." if keep is None else
               "kaynak doğrulaması tamamlanmadığı için kanıt yok (hiçbir metrik değerlendirilmedi)."))
        print(msg)
        return {"schema": "halsa-metric-diagnostics/1", "available": False}
    d = MD.diagnose(keep["manifest"], keep["served"], today, strict_overall=overall,
                    strict_sources=MD.strict_by_source(results))
    for line in MD.render_tr(d):
        print(line)
    return {"available": True, **d}


# ---------------------------------------------------------------------------------------------
# evidence output safety: never an input, never an existing file, never truncating anything
# ---------------------------------------------------------------------------------------------
_WORKBOOK_EXT = (".xlsx", ".xlsm", ".xls", ".xltx", ".json", ".csv")


def _protected_inputs(kredi: str | None, core: str | None) -> list[str]:
    """Every file this run reads or depends on: the Kredi workbook, the core folder's selection file, the selected
    workbooks and (conservatively) every workbook / JSON file directly in the core folder."""
    out = []
    if kredi:
        out.append(kredi)
    if core:
        out.append(os.path.join(core, "sources.json"))
        try:
            from services import generation as GEN
            paths, _, _ = GEN.select_core(core)
            out += [p for p in paths.values() if p]
        except Exception:
            pass
        try:
            out += [os.path.join(core, n) for n in os.listdir(core) if n.lower().endswith(_WORKBOOK_EXT)]
        except OSError:
            pass
    return out


def _same_file(a: str, b: str) -> bool:
    """Same path after resolving symlinks, or the same file object (hard links: device + inode)."""
    try:
        if os.path.realpath(a) == os.path.realpath(b):
            return True
        sa, sb = os.stat(a), os.stat(b)
        return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)
    except OSError:
        return False


def _evidence_clash(path: str, protected: list[str]) -> str | None:
    for p in protected:
        if _same_file(path, p):
            return ("Kanıt dosyası bir kaynak dosyanın yerine yazılamaz (aynı dosya, bağlantı veya kısayol). "
                    "Başka bir dosya adı verin veya yolu boş bırakın (data/reconciliation/).")
    if os.path.lexists(path):
        return "Kanıt dosyası zaten var; mevcut dosyaların üzerine asla yazılmaz. Yeni bir dosya adı verin."
    return None


def _write_evidence_exclusive(path: str, summary: dict, protected: list[str]) -> str | None:
    """Create a NEW file (O_CREAT | O_EXCL: fails if anything — file, link or dangling symlink — already exists at
    the path, so no existing file can ever be truncated). A partly written file of ours is removed. Returns an error
    text, or None on success."""
    clash = _evidence_clash(path, protected)          # re-checked right before writing
    if clash:
        return clash
    if os.path.dirname(path) == DEFAULT_EVIDENCE_DIR:
        try:
            os.makedirs(DEFAULT_EVIDENCE_DIR, mode=0o700, exist_ok=True)
        except OSError as e:
            return f"klasör oluşturulamadı ({type(e).__name__})."
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return "dosya bu arada oluşturulmuş; üzerine yazılmadı."
    except OSError as e:
        return f"dosya oluşturulamadı ({type(e).__name__})."
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as e:
        try:
            os.remove(path)                            # only the file this call created
        except OSError:
            pass
        return f"yazma hatası ({type(e).__name__})."
    return None


DEFAULT_EVIDENCE_DIR = os.path.join(ROOT, "data", "reconciliation")


def _inside(path: str, folder: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(folder)]) == os.path.realpath(folder)
    except ValueError:                                   # different drives (Windows)
        return False


def _evidence_path(value: str, kind: str) -> str | None:
    """Where the evidence file goes. Default: data/reconciliation/ (git-ignored). Inside this repository only under
    data/; anywhere outside the repository is the owner's choice. None = refused."""
    if value == "":
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        return os.path.join(DEFAULT_EVIDENCE_DIR, f"kanit_{kind}_{stamp}.json")
    path = os.path.abspath(value)
    if _inside(path, ROOT) and not _inside(path, os.path.join(ROOT, "data")):
        return None
    return path


def _shown(path: str) -> str:
    return os.path.relpath(path, ROOT) if _inside(path, ROOT) else os.path.basename(path)


if __name__ == "__main__":
    sys.exit(main())
