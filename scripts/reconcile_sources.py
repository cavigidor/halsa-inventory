"""Owner-local, READ-ONLY source reconciliation (M8-ACCURACY-2). Run on the owner's Mac against the private exports.

    python -m scripts.reconcile_sources --kredi "data/kredi takip(1).xlsx" --today 2026-10-19
    python -m scripts.reconcile_sources --kredi "data/kredi takip(1).xlsx" --show-values        # amounts: local only
    python -m scripts.reconcile_sources --core data                                             # stock / sales / Cari
    python -m scripts.reconcile_sources --kredi FILE --expect-sha256 <hash> --evidence-out data/kredi_evidence.json

Guarantees:
- reads the workbook bytes once and never writes to, repairs or re-saves any workbook;
- no AI provider, no network, nothing uploaded; nothing is written unless `--evidence-out` is given, and that file
  holds fingerprints, counts and PASS / FAIL / OPEN results only (never amounts, codes, bank or customer names);
- default console output is privacy-safe (counts, fingerprints, results). `--show-values` prints amount totals to the
  LOCAL console for the owner; never paste that output into GitHub, chat or an AI tool;
- the workbook's saved Excel filter is reported as a view and never treated as proof that hidden rows are settled;
- TECHNICAL validation only. A PASS here is not a financial / accounting approval and does not verify bank liabilities.

Exit code: 0 = every technical check passed (OPEN items may remain), 1 = a technical check failed, 2 = unreadable /
usage error.
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


def reconcile_core(data_folder: str, out: Out, *, today: dt.date) -> dict:
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
    for r in m.core:
        out.line(f"{r.key}: {r.file} · SHA-256 {r.sha256}")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.reconcile_sources", description=__doc__.split("\n")[0])
    ap.add_argument("--kredi", help="Kredi workbook (private, local path)")
    ap.add_argument("--core", help="data folder with the core ERP exports (and optional sources.json)")
    ap.add_argument("--today", help="reference date YYYY-MM-DD (default: local today)")
    ap.add_argument("--snapshot-date", help="declared Kredi snapshot date YYYY-MM-DD (unknown if omitted)")
    ap.add_argument("--expect-sha256", help="fail unless the Kredi workbook has exactly this fingerprint")
    ap.add_argument("--show-values", action="store_true", help="print amount totals to the LOCAL console only")
    ap.add_argument("--evidence-out", help="write privacy-safe evidence JSON (fingerprints, counts, results)")
    a = ap.parse_args(argv)
    if not (a.kredi or a.core):
        ap.print_usage()
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
        if a.core:
            summary["core"] = reconcile_core(a.core, out, today=today)
    except OSError:
        print("Dosya okunamadı.")
        return 2
    failed = sum(1 for r in out.results if r["status"] == "FAIL")
    open_ = sum(1 for r in out.results if r["status"] == "OPEN")
    out.line(f"Sonuç: {failed} teknik hata, {open_} çözülmemiş konu (sahip / muhasebe kararı gerekli).")
    if a.evidence_out:
        with open(a.evidence_out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=1)
        out.line(f"Kanıt dosyası yazıldı (değer yok): {os.path.basename(a.evidence_out)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
