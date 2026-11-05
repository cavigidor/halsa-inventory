#!/bin/bash
# Klasik Rapor Oluştur — the ORIGINAL Halsa dashboard (commit 71f5f2f), from the Excel files you choose.
# Double-click in Finder. It asks for the stock file, the sales file and (optionally) the Cari aging report, writes
# dashboards/Klasik_Rapor_<tarih>.html next to this folder and opens it. Nothing else is read, changed or uploaded;
# the new Halsa application, its database and its published dashboard are not involved.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
say() { osascript -e "display dialog \"$1\" buttons {\"Tamam\"} default button \"Tamam\" with title \"Klasik Rapor\"" >/dev/null 2>&1 || echo "$1"; }
pick() { osascript -e "POSIX path of (choose file with prompt \"$1\" of type {\"org.openxmlformats.spreadsheetml.sheet\", \"xlsx\"})" 2>/dev/null; }

# a private Python 3.12 environment with the pinned libraries the equivalence was proven with (created once)
PY="$HERE/.venv/bin/python"
if [ ! -x "$PY" ]; then
  BASE="$(command -v python3.12 || command -v python3)"
  if [ -z "$BASE" ] || ! "$BASE" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    say "Python 3.12 bulunamadı. Lütfen Python 3.12 kurun ve tekrar deneyin."; exit 1
  fi
  "$BASE" -m venv "$HERE/.venv" && "$PY" -m pip install -q -r "$HERE/requirements.txt" || {
    say "Gerekli kütüphaneler kurulamadı (internet bağlantısını kontrol edin)."; rm -rf "$HERE/.venv"; exit 1; }
fi

STOCK="$(pick "1/3 — Stok dosyasını seçin (ör. STOK_1_11_26.xlsx)")" || exit 0
SALES="$(pick "2/3 — Satış dosyasını seçin (ör. 1_Kasım_2020_2026 satışlar.xlsx)")" || exit 0
CARI=""
ANS="$(osascript -e 'button returned of (display dialog "3/3 — Orijinal formatta Cari yaşlandırma raporu (Hesap kodu, Hesap adı, Temsilci kodu ve ay sütunları) var mı? Müşteri / temsilci listesi bu rapor değildir." buttons {"Yok, onsuz oluştur", "Var, seç"} default button "Var, seç" with title "Klasik Rapor")' 2>/dev/null)" || exit 0
if [ "$ANS" = "Var, seç" ]; then CARI="$(pick "3/3 — Cari yaşlandırma raporunu seçin")" || exit 0; fi

mkdir -p "$REPO/dashboards"
OUT="$REPO/dashboards/Klasik_Rapor_$(date +%Y-%m-%d).html"; n=2
while [ -e "$OUT" ]; do OUT="$REPO/dashboards/Klasik_Rapor_$(date +%Y-%m-%d)_$n.html"; n=$((n+1)); done

ARGS=(--stock "$STOCK" --sales "$SALES" --out "$OUT")
[ -n "$CARI" ] && ARGS+=(--cari "$CARI")
if MSG="$("$PY" "$HERE/generate.py" "${ARGS[@]}" 2>&1)"; then
  echo "$MSG"; open "$OUT"
else
  say "$(echo "$MSG" | tail -1 | sed 's/"/'"'"'/g')"; exit 2
fi
