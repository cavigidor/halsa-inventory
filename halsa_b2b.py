"""B2B tracking (D-080) — one calculation shared by the Klasik B2B variant and the V1 general report.

Father's definitions (2026-11-07), applied exactly and nothing more:
- Sales channel: physical Excel column **AG** of the sales export. A line is a B2B-channel sale when that cell, as
  text, is exactly ``B2B``. The column is located by physical position and its header is checked against the frame
  that is analysed; any mismatch makes every B2B figure unknown (never 0).
- Registration: physical Excel column **B** of ``b2b_kullanici_rapor.xlsx`` (first worksheet, row 1 = header). A
  customer is *confirmed* registered only when its order-source code (``_cust`` = ``Pro_kodu``, else ``Musterikod``,
  computed by the dashboard's own ``load_sales``) appears in column B exactly — no trimming, no fuzzy matching.
  Whitespace-only matches and matches only through the invoice account (``Musterikod``) stay *ambiguous*.
- A missing registration file is "unknown", never "not registered": calling lists A and B are then not produced.
- "Son zamanlarda" is a PROVISIONAL review window (default 365 days) counted back from the latest observed sales
  transaction date in the file — never the computer's date.

Amounts are summed from the source ``Nettutar`` as read; a missing amount makes the affected total unknown (None),
negative and zero lines are kept, duplicate lines are not removed. "Sipariş" counts distinct order documents
(``sip_evrakno_seri|sip_evrakno_sira`` — order, not invoice), never lines; a document holding both B2B and other
lines ("mixed") is counted once per channel and listed for verification.

This module imports only the standard library, pandas and openpyxl. It must never import the new Halsa application
(services / config / app / dashboard_builder) or the frozen Klasik builder: both callers hand it their own frames.
"""
from __future__ import annotations

import hashlib
import io
import json
import collections
import math

import pandas as pd

CHANNEL_COLUMN = "AG"
CHANNEL_INDEX = 32                      # A=0 … AG=32 (physical position)
CHANNEL_VALUE = "B2B"
REGISTRATION_COLUMN = "B"
REGISTRATION_FILE = "b2b_kullanici_rapor.xlsx"
REGISTRATION_HEADER = "Kod No"          # physical column B header of the supplied registration export
REGISTRATION_NAME_HEADER = "Kullanıcı Cari"   # company name, used only to label registration-only codes
REVIEW_DAYS = 365                       # provisional review window, not an approved business rule
ANCHOR = "</script></div></body></html>"  # the exact ending of both dashboards' HTML templates

STATUS_TEXT = {
    "confirmed": "Kayıtlı",
    "ws": "Belirsiz (boşluk farkı)",
    "inv": "Belirsiz (fatura hesabı kayıtlı)",
    "not_found": "Listede bulunamadı",
    "unknown": "Liste yok",
}
REG_STATE_TEXT = {"missing": "bu rapora verilmedi", "unreadable": "okunamadı",
                  "wrong_layout": "beklenen düzende değil (B sütunu başlığı 'Kod No' değil)",
                  "empty": "B sütununda hiç müşteri kodu yok"}
FLAG_TEXT = {"confirmed": "B2B", "ws": "Belirsiz", "inv": "Belirsiz", "not_found": "", "unknown": "—"}
VIEWS = ("A", "B", "C", "V")
CLOSED_LABEL = {"A": "listede yok", "B": "kayıtlı, B2B'den almamış", "C": "eskiden B2B'den alan", "V": "Kontrol"}            # internal classification (unchanged); shown as one table with an İzahat


# --------------------------------------------------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------------------------------------------------
def _blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    return str(v).strip() == "" or str(v).strip().lower() in ("nan", "none", "<na>")


def sheet_header(source) -> list:
    """Row 1 of the first worksheet, by physical position (path, bytes or file object)."""
    import openpyxl
    src = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
    try:
        row = next(wb.worksheets[0].iter_rows(min_row=1, max_row=1, values_only=True), ())
        return [None if v is None else str(v) for v in row]
    finally:
        wb.close()


def read_registration(data: bytes | None, name: str = REGISTRATION_FILE,
                      expected_header: str | None = REGISTRATION_HEADER) -> dict:
    """Column B of the first worksheet (row 1 = header). Never infers absence as 'not registered': a missing,
    unreadable, wrong-layout (column B header is not ``expected_header``) or empty workbook is 'unknown'."""
    reg = {"file": name, "status": "missing", "sha256": None, "sheets": [], "header": None, "rows": 0,
           "codes": [], "unique": [], "duplicates": {}, "numeric_cells": 0, "blank_cells": 0, "other_columns": [],
           "missing_code_rows": [], "names": {}, "expected_header": expected_header}
    if data is None:
        return reg
    reg["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                            # noqa: BLE001 — any unreadable workbook
        reg["status"] = "unreadable"
        return reg
    try:
        reg["sheets"] = list(wb.sheetnames)
        ws = wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)
        head = next(rows, ())
        reg["header"] = None if len(head) < 2 or head[1] is None else str(head[1])
        reg["other_columns"] = [None if h is None else str(h) for i, h in enumerate(head) if i != 1]
        heads = [None if h is None else str(h).strip() for h in head]
        name_ix = heads.index(REGISTRATION_NAME_HEADER) if REGISTRATION_NAME_HEADER in heads else None
        for excel_row, r in enumerate(rows, start=2):
            v = r[1] if len(r) > 1 else None
            if _blank(v):
                if any(not _blank(x) for x in r):
                    reg["blank_cells"] += 1
                    reg["missing_code_rows"].append(excel_row)
                continue
            reg["rows"] += 1
            if isinstance(v, bool):
                text, numeric = str(v), True
            elif isinstance(v, (int, float)):
                numeric = True
                text = str(int(v)) if float(v).is_integer() else repr(v)
            else:
                text, numeric = str(v), False
            reg["numeric_cells"] += numeric
            reg["codes"].append({"row": excel_row, "code": text, "numeric": numeric})
            nm = r[name_ix] if name_ix is not None and len(r) > name_ix else None
            if not _blank(nm):
                reg["names"].setdefault(text, str(nm).strip())
    finally:
        wb.close()
    seen = {}
    for c in reg["codes"]:
        seen[c["code"]] = seen.get(c["code"], 0) + 1
    reg["unique"] = list(seen)
    reg["duplicates"] = {k: n for k, n in seen.items() if n > 1}
    if expected_header is not None and (reg["header"] or "").strip() != expected_header:
        reg["status"] = "wrong_layout"
    elif not reg["codes"]:
        reg["status"] = "empty"
    else:
        reg["status"] = "ok"
    return reg


def channel(raw: pd.DataFrame, header: list | None) -> tuple[pd.Series | None, dict]:
    """Per line: 'b2b' | 'other' | 'blank' | 'unclear', or None when AG cannot be located safely."""
    info = {"column": CHANNEL_COLUMN, "header": None, "ok": False, "reason": None}
    cols = [str(c) for c in raw.columns]
    if not header or len(header) <= CHANNEL_INDEX or header[CHANNEL_INDEX] is None:
        info["reason"] = "AG sütununda başlık yok"
        return None, info
    info["header"] = header[CHANNEL_INDEX]
    if len(cols) != len(header) or [str(h) for h in header] != cols:
        info["reason"] = "Excel başlık satırı ile okunan sütunlar aynı değil (boş/tekrar başlık olabilir)"
        return None, info
    col = raw.iloc[:, CHANNEL_INDEX]
    text = col.map(lambda v: "" if _blank(v) else str(v))
    out = pd.Series("other", index=raw.index, dtype=object)
    out[text.eq("")] = "blank"
    lowered = text.str.lower()
    out[lowered.str.contains("b2b", regex=False) & ~text.eq(CHANNEL_VALUE)] = "unclear"
    out[text.eq(CHANNEL_VALUE)] = "b2b"
    info["ok"] = True
    return out, info


# --------------------------------------------------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------------------------------------------------
def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return int(f) if f.is_integer() else round(f, 2)


def _iso(v):
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return pd.Timestamp(v).date().isoformat()


def analyse(raw: pd.DataFrame, base: pd.DataFrame, attrs: dict, registration: dict, header: list | None,
            review_days: int = REVIEW_DAYS) -> dict:
    """raw = the sales frame exactly as read (same index as base); base = the dashboard's own load_sales output
    (``_cust``, ``_cname``, ``_inv``, ``_date``, ``_yr``, ``_ord``); attrs = that dashboard's cust_attrs."""
    if not raw.index.equals(base.index):
        raise ValueError("raw and processed sales frames are not aligned")
    ch, chinfo = channel(raw, header)
    reg_ok = registration.get("status") == "ok"
    d = pd.DataFrame(index=base.index)
    d["cust"] = base["_cust"].astype(str)
    d["cust_missing"] = base["_cust"].map(_blank)
    d["inv"] = base["_inv"].astype(str)
    d["name"] = base["_cname"]
    d["date"] = base["_date"]
    d["yr"] = base["_yr"]
    d["ord"] = base["_ord"]
    d["doc_missing"] = raw["sip_evrakno_seri"].map(_blank) | raw["sip_evrakno_sira"].map(_blank)
    d["amt"] = pd.to_numeric(raw["Nettutar"], errors="coerce")
    d["amt_missing"] = d["amt"].isna()
    d["ch"] = ch if ch is not None else "unknown"
    d["b2b"] = d["ch"].eq("b2b")
    d["unclear"] = d["ch"].isin(["unclear", "unknown"])
    d = d[~d["cust_missing"]]

    asof = d["date"].max()
    asof = None if pd.isna(asof) else pd.Timestamp(asof)
    start = (asof - pd.Timedelta(days=review_days)) if asof is not None else None
    d["undated"] = d["date"].isna()
    d["recent"] = (d["date"] > start) if start is not None else False

    def total(sub):
        if sub.empty:
            return 0
        if sub["amt_missing"].any():
            return None
        return _num(sub["amt"].sum())

    # ---- registration matching -----------------------------------------------------------------------------------
    R = set(registration.get("unique") or [])
    R_strip = {}
    for r in R:
        R_strip.setdefault(r.strip(), set()).add(r)
    custs = set(d["cust"])
    invs_of = d.groupby("cust")["inv"].agg(lambda s: set(s)).to_dict()
    custs_of_inv = d.groupby("inv")["cust"].agg(lambda s: set(s)).to_dict()
    cust_strip, inv_strip = {}, {}
    for c in custs:
        cust_strip.setdefault(c.strip(), set()).add(c)
    for i in custs_of_inv:
        inv_strip.setdefault(i.strip(), set()).add(i)

    def status(c):
        if not reg_ok:
            return "unknown"
        if c in R:
            return "confirmed"
        if c.strip() in R_strip:
            return "ws"
        if any(i != c and (i in R or i.strip() in R_strip) for i in invs_of.get(c, ())):
            return "inv"
        return "not_found"

    st = {c: status(c) for c in custs}

    # ---- per customer ----------------------------------------------------------------------------------------------
    names = d.groupby("cust")["name"].first().to_dict()
    g = d.groupby("cust", sort=True)
    per = {}
    for c, sub in g:
        b = sub[sub["b2b"]]
        other = sub[~sub["b2b"] & ~sub["unclear"]]
        undated_unknown = bool(sub["undated"].any())
        a = attrs.get(c, {})
        rec_all = sub[sub["recent"]]
        rec_other = other[other["recent"]]
        unclear = bool(sub["unclear"].any())
        per[c] = {
            "code": c, "name": "" if _blank(names.get(c)) else str(names.get(c)).strip(),
            "rep": a.get("rep", ""), "sector": a.get("sector", ""), "closed": bool(a.get("closed")),
            "status": st[c], "reg": STATUS_TEXT[st[c]],
            "total": total(sub), "recent": None if undated_unknown else total(rec_all),
            "last": _iso(sub["date"].max()), "orders": None if sub["doc_missing"].any() else int(sub["ord"].nunique()),
            "b2b_lines": int(len(b)), "has_b2b": bool(len(b)),
            "b2b_total": None if unclear else total(b),
            "b2b_orders": None if (unclear or b["doc_missing"].any()) else int(b["ord"].nunique()),
            "last_b2b": _iso(b["date"].max()) if len(b) else None,
            "recent_b2b": bool(b["recent"].any()), "unclear": unclear,
            "b2b_undated": bool(b["undated"].any()),
            "recent_other": None if (unclear or undated_unknown) else total(rec_other),
            "recent_other_orders": None if (unclear or undated_unknown or rec_other["doc_missing"].any())
            else int(rec_other["ord"].nunique()),
        }

    # ---- documents and years -----------------------------------------------------------------------------------------
    # The order identifier (seri|sıra) is checked before it is used: an identifier recorded for more than one
    # customer or year is reported, and a mixed-channel document is judged within one customer only (never across
    # customers). Order counts keep the dashboards' established rule (distinct identifiers per customer and year).
    keyed = d[~d["doc_missing"]]
    per_doc = keyed.groupby("ord").agg(n_cust=("cust", "nunique"), n_year=("yr", "nunique"), has_b=("b2b", "any"))
    doc_multi_cust = per_doc[per_doc["n_cust"] > 1]
    doc_multi_year = per_doc[per_doc["n_year"] > 1]
    pairs = keyed.groupby(["cust", "ord"]).agg(has_b=("b2b", "any"), all_b=("b2b", "all"))
    mixed_pairs = pairs[pairs["has_b"] & ~pairs["all_b"]]
    mixed_docs = sorted(f"{o} → {c}" for c, o in mixed_pairs.index)
    d["mixed"] = pd.MultiIndex.from_arrays([d["cust"], d["ord"]]).isin(mixed_pairs.index) & ~d["doc_missing"]
    yearly = {}
    dated = d[~d["undated"]]
    for (c, y), sub in dated.groupby(["cust", "yr"]):
        b = sub[sub["b2b"]]
        unclear = bool(sub["unclear"].any())
        yearly.setdefault(c, {})[int(y)] = {
            "b2b": None if unclear else total(b),
            "b2b_orders": None if (unclear or b["doc_missing"].any()) else int(b["ord"].nunique()),
            "b2b_mixed": int(sub.loc[sub["mixed"], "ord"].nunique()),
        }

    # ---- views -------------------------------------------------------------------------------------------------------
    # A  definite gaps: bought from Halsa, code not in the list (exact, nor ambiguous), no B2B-channel purchase.
    # B  registered (exact) without B2B-channel purchases, incl. codes with no sales at all. Needs a verified AG.
    # C  B2B-channel buyers without B2B purchases in the provisional window. Needs a verified AG.
    # V  verification: ambiguous registration matches (customer side and registration side) and B2B-channel buyers
    #    whose code is absent from the list. Never counted as registered or unregistered.
    ch_ok = bool(chinfo["ok"])
    views = {k: [] for k in VIEWS}
    closed_out = {k: 0 for k in VIEWS}
    common = ("code", "name", "rep", "sector", "reg")
    reg_names = registration.get("names") or {}
    reg_rows = {}
    for c in registration.get("codes") or []:
        reg_rows.setdefault(c["code"], c["row"])
    excluded_inv_b2b = []

    def vrow(p, kind):
        row = {k: p[k] for k in common}
        row.update(cat="V", kind=kind, recent=p["recent"], total=p["total"], last=p["last"],
                   b2b_seen=(None if not ch_ok else "Evet" if p["has_b2b"] else "Belirsiz kanal" if p["unclear"] else "Hayır"),
                   b2b_total=p["b2b_total"] if p["has_b2b"] else None, last_b2b=p["last_b2b"], excel_row=None,
                   linked="")
        return row

    if reg_ok:
        for c, p in per.items():
            if p["status"] == "confirmed":
                continue
            if p["closed"]:
                closed_out["V" if (p["status"] != "not_found" or p["has_b2b"] or p["unclear"]) else "A"] += 1
                continue
            if p["status"] in ("ws", "inv"):
                views["V"].append(vrow(p, "Belirsiz kayıt eşleşmesi — " + ("boşluk farkı" if p["status"] == "ws"
                                                                         else "fatura hesabı kayıtlı")))
            elif ch_ok and p["has_b2b"]:
                views["V"].append(vrow(p, "B2B'den alıyor, kodu kayıt listesinde yok"))
            elif ch_ok and p["unclear"]:
                views["V"].append(vrow(p, "Kanal yazımı belirsiz ('B2B' farklı yazılmış)"))
            else:
                row = {k: p[k] for k in common}
                row.update(cat="A", recent=p["recent"], total=p["total"], last=p["last"],
                           b2b_seen=("Hayır" if ch_ok else None))
                views["A"].append(row)
        for r in registration.get("unique") or []:
            p = per.get(r)
            if p is not None:
                if not ch_ok or p["has_b2b"] or p["unclear"]:
                    continue
                if p["closed"]:
                    closed_out["B"] += 1
                    continue
                row = {k: p[k] for k in common}
                row.update(cat="B", sub="Diğer kanallardan alıyor", recent=p["recent"], total=p["total"],
                           last=p["last"], excel_row=reg_rows.get(r))
                views["B"].append(row)
                continue
            ws = sorted(cust_strip.get(r.strip(), set()))
            inv_hits = sorted(inv_strip.get(r.strip(), set())) if not ws else []
            inv_c = sorted({x for i in inv_hits for x in custs_of_inv.get(i, ())})
            nm = reg_names.get(r, "")
            if ws or inv_c:
                linked = ws or inv_c
                lines = d[d["cust"].isin(ws)] if ws else d[d["inv"].isin(inv_hits)]
                bought_b2b = bool(lines["b2b"].any())
                if bought_b2b:
                    excluded_inv_b2b.append(r)
                kind = ("Kayıt kodu yalnız boşluk farkıyla satış müşterisi" if ws else
                        "Kayıt kodu yalnız fatura hesabı olarak satışta")
                views["V"].append({"code": r, "name": nm, "rep": "", "sector": "", "reg": STATUS_TEXT["confirmed"],
                                   "cat": "V", "kind": kind, "recent": None, "total": None, "last": None,
                                   "b2b_seen": None if not ch_ok else ("Evet" if bought_b2b else "Hayır"),
                                   "b2b_total": None, "last_b2b": None, "excel_row": reg_rows.get(r),
                                   "linked": ", ".join(linked[:10]) + (f" … (+{len(linked) - 10})" if len(linked) > 10 else "")})
            elif ch_ok:
                views["B"].append({"code": r, "name": nm, "rep": "", "sector": "", "reg": STATUS_TEXT["confirmed"],
                                   "cat": "B", "sub": "Satış verisinde eşleşme yok" + (" (ad: kayıt listesi)" if nm else ""),
                                   "recent": None, "total": None, "last": None, "excel_row": reg_rows.get(r)})
    for c, p in per.items():
        if not ch_ok or not p["has_b2b"] or p["recent_b2b"]:
            continue
        if p["closed"]:
            closed_out["C"] += 1
            continue
        row = {k: p[k] for k in common}
        row.update(cat="C", last_b2b=p["last_b2b"], b2b_total=p["b2b_total"], b2b_orders=p["b2b_orders"],
                   recent_other=p["recent_other"], recent_other_orders=p["recent_other_orders"],
                   date_note=("B2B satırlarında tarih eksik — doğrulanmalı" if p["b2b_undated"] else ""))
        views["C"].append(row)
    big = lambda v: -(v if isinstance(v, (int, float)) else -1e18)
    views["A"].sort(key=lambda r: (big(r["recent"]), big(r["total"]), r["code"]))
    views["B"].sort(key=lambda r: (r["sub"], big(r["recent"]), r["code"]))
    views["C"].sort(key=lambda r: (-(int((r["last_b2b"] or "0000-00-00").replace("-", ""))), r["code"]))
    views["V"].sort(key=lambda r: (r["kind"], big(r["recent"]), r["code"]))

    # ---- counts and warnings -----------------------------------------------------------------------------------------
    b2b_lines = d[d["b2b"]]
    unreg_buyers = sorted(c for c, p in per.items() if p["has_b2b"] and p["status"] != "confirmed") if reg_ok else []
    ws_c = sorted(c for c, s in st.items() if s == "ws")
    inv_c = sorted(c for c, s in st.items() if s == "inv")
    reg_unmatched = [r for r in (registration.get("unique") or []) if r not in custs]
    vk = collections.Counter(r["kind"] for r in views["V"])
    counts = {
        "lines": int(len(d)), "lines_identity_missing": int(len(base) - len(d)),
        "channel_ok": ch_ok, "channel_header": chinfo["header"],
        "b2b_lines": int(len(b2b_lines)), "blank_channel_lines": int(d["ch"].eq("blank").sum()),
        "unclear_channel_lines": int(d["ch"].eq("unclear").sum()),
        "b2b_amount_missing": int(b2b_lines["amt_missing"].sum()), "b2b_amount_negative": int(b2b_lines["amt"].lt(0).sum()),
        "b2b_amount_zero": int(b2b_lines["amt"].eq(0).sum()), "b2b_undated": int(b2b_lines["undated"].sum()),
        "undated_lines": int(d["undated"].sum()), "doc_missing_lines": int(d["doc_missing"].sum()),
        "documents": int(per_doc.shape[0]), "b2b_documents": int(b2b_lines.loc[~b2b_lines["doc_missing"], "ord"].nunique()),
        "doc_ids_multi_customer": int(len(doc_multi_cust)), "doc_ids_multi_customer_b2b": int(doc_multi_cust["has_b"].sum()),
        "doc_ids_multi_year": int(len(doc_multi_year)), "doc_ids_multi_year_b2b": int(doc_multi_year["has_b"].sum()),
        "mixed_documents": int(len(mixed_pairs)),
        "customers": int(len(per)), "b2b_customers": int(sum(p["has_b2b"] for p in per.values())),
        "registration_status": registration.get("status"), "registration_header": registration.get("header"),
        "registration_rows": int(registration.get("rows") or 0),
        "registration_unique": int(len(R)), "registration_duplicate_codes": int(len(registration.get("duplicates") or {})),
        "registration_missing_code_rows": int(len(registration.get("missing_code_rows") or [])),
        "registration_numeric_cells": int(registration.get("numeric_cells") or 0),
        "registration_matched_exact": int(len(R & custs)), "registration_unmatched": int(len(reg_unmatched)),
        "status_confirmed": int(sum(s == "confirmed" for s in st.values())), "status_ambiguous_ws": len(ws_c),
        "status_ambiguous_inv": len(inv_c), "status_not_found": int(sum(s == "not_found" for s in st.values())),
        "status_unknown": int(sum(s == "unknown" for s in st.values())),
        "b2b_buyers_not_confirmed": len(unreg_buyers),
        "b2b_lines_not_confirmed": int(b2b_lines["cust"].isin(set(unreg_buyers)).sum()),
        "registration_inv_with_b2b": len(excluded_inv_b2b),
        "view_A": len(views["A"]), "view_B": len(views["B"]), "view_C": len(views["C"]), "view_V": len(views["V"]),
        "view_B_other_channels": sum(r["sub"].startswith("Diğer kanallardan") for r in views["B"]),
        "view_B_no_sales": sum(r["sub"].startswith("Satış verisinde") for r in views["B"]),
        "view_V_kinds": dict(sorted(vk.items())),
        "closed_excluded": dict(closed_out),
        "asof": _iso(asof), "window_start": _iso(start), "review_days": review_days,
    }
    warnings = _warnings(counts, registration, chinfo, ws_c, inv_c, unreg_buyers, excluded_inv_b2b, mixed_docs)
    return {"counts": counts, "status": st, "per": per, "yearly": yearly, "views": views, "warnings": warnings,
            "channel": chinfo, "registration": {k: registration.get(k) for k in
                                                ("file", "status", "sha256", "sheets", "header", "rows")}}


def _codes(xs, limit=None):
    xs = list(xs)
    shown = xs if limit is None else xs[:limit]
    return ", ".join(shown) + (f" … (+{len(xs) - len(shown)})" if len(xs) > len(shown) else "")


def _w(code, title, issue, evidence, question, resolution, severity="orta", codes=None):
    return {"code": code, "title": title, "issue": issue, "evidence": evidence, "question": question,
            "resolution": resolution, "severity": severity, "codes": list(codes or [])}


def _warnings(c, reg, chinfo, ws_c, inv_c, unreg, inv_b2b, mixed_docs):
    out = []
    if reg.get("status") != "ok":
        out.append(_w("B2B-KAYIT-YOK", f"B2B kayıt listesi kullanılamadı: {reg.get('file')}",
                      "Kayıt listesi " + REG_STATE_TEXT.get(reg.get("status"), "kullanılamadı") + ". Bu, müşterilerin "
                      "kayıtsız olduğu anlamına GELMEZ; B2B Kaydı (VAR / YOK) gösterilmedi, tablo yalnız AG sütununa göre "
                      "eskiden B2B'den alanları içerir ve Büyüme / Küçülme'deki B2B sütunu — gösterir.",
                      f"Dosya durumu: {reg.get('status')}; B sütunu başlığı: {reg.get('header') or 'yok'} "
                      f"(beklenen: {reg.get('expected_header') or '—'}); kod sayısı: {reg.get('rows') or 0}.",
                      "Güncel b2b_kullanici_rapor.xlsx dosyası nerede? B sütunu ('Kod No') hâlâ müşteri kodu mu?",
                      "Doğru dosyayı sağlayın ve raporu yeniden oluşturun.", "yuksek"))
    out.append(_w("B2B-KANAL", "B2B satışı = AG sütunu tam olarak 'B2B'",
                  f"Satış dosyasının fiziksel {CHANNEL_COLUMN} sütunu (başlık: {chinfo.get('header') or 'bulunamadı'}) "
                  "satır bazında okunur; yalnız tam 'B2B' değeri B2B kanalıdır, diğer değerler başka kanal sayılır.",
                  (f"B2B satırı: {c['b2b_lines']}; B2B belgesi: {c['b2b_documents']}; boş kanal satırı: "
                   f"{c['blank_channel_lines']}; 'B2B' içeren ama farklı yazılmış: {c['unclear_channel_lines']}."
                   if chinfo.get("ok") else f"AG sütunu güvenle bulunamadı: {chinfo.get('reason')}. B2B tutarları bilinmiyor."),
                  "AG sütununda B2B dışındaki değerler satıcı/temsilci adı mı? B2B siparişi her zaman 'B2B' olarak mı yazılıyor?",
                  "Farklı yazım görülürse ERP kaydını kontrol edin; rapor yalnız tam eşleşmeyi B2B sayar.",
                  "orta" if chinfo.get("ok") else "yuksek"))
    if not chinfo.get("ok"):
        out.append(_w("B2B-KANAL-YOK", "'B2B'den almamış' ve 'eskiden B2B'den alıyordu' açıklamaları üretilmedi",
                      "B2B alımı doğrulanamadığı için bu açıklamalar sıfır B2B alımı varsayılarak üretilmez; kayıtlı "
                      "müşteriler İzahat'ta 'kanal doğrulanamadı' olarak gösterilir.", f"Neden: {chinfo.get('reason')}.",
                      "Satış dosyasında B2B kanalı hâlâ AG sütununda mı?", "Doğru düzendeki satış dosyasıyla yeniden oluşturun.",
                      "yuksek"))
    out.append(_w("B2B-PENCERE", f"'Son zamanlarda' = son {c['review_days']} gün (geçici inceleme ölçütü)",
                  "Onaylı iş kuralı değildir; kolayca değiştirilebilir. Pencere bilgisayarın tarihinden değil, satış "
                  "dosyasındaki en son işlem tarihinden geriye sayılır.",
                  f"Son gözlenen işlem: {c['asof'] or 'bilinmiyor'}; pencere başlangıcı (hariç): {c['window_start'] or 'bilinmiyor'}.",
                  "B2B'den 'son zamanlarda almamış' için hangi süre doğru: 6 ay, 12 ay, başka?",
                  "Babanızın onayladığı süre ile yeniden oluşturun (--b2b-review-days)."))
    out.append(_w("B2B-BELGE", "Sipariş sayısı = farklı sipariş belgesi (seri|sıra), satır değil",
                  "sip_evrakno_seri + sip_evrakno_sira sipariş evrakını gösterir; fatura sayısı değildir. Aynı belgede "
                  "hem B2B hem başka kanal satırı varsa belge her kanalda bir kez sayılır ve doğrulama için listelenir.",
                  f"Belge: {c['documents']}; B2B belgesi: {c['b2b_documents']}. Birden çok müşteride kullanılan belge "
                  f"numarası: {c['doc_ids_multi_customer']} (B2B satırı olan: {c['doc_ids_multi_customer_b2b']}); birden "
                  f"çok yılda: {c['doc_ids_multi_year']} (B2B: {c['doc_ids_multi_year_b2b']}). Karışık kanal yalnız aynı "
                  f"müşterinin belgesinde aranır: {c['mixed_documents']}. Belge numarası eksik satır: {c['doc_missing_lines']}."
                  + (f" Örnekler (belge → müşteri): {_codes(mixed_docs, 40)}." if mixed_docs else ""),
                  "Bir sipariş belgesinde B2B ve başka kanal satırı birlikte olabilir mi? Bu belgeler B2B siparişi mi?",
                  "Karışık belgeleri ERP sipariş ekranında kontrol edin.", "orta" if (c["mixed_documents"] or c["doc_ids_multi_customer_b2b"]) else "dusuk",
                  mixed_docs))
    if c["b2b_amount_missing"] or c["b2b_amount_negative"] or c["b2b_amount_zero"] or c["b2b_undated"]:
        out.append(_w("B2B-TUTAR", "B2B satırlarında eksik / sıfır / negatif tutar veya eksik tarih",
                      "Eksik tutar 0 sayılmaz (ilgili toplam — olur); sıfır ve negatif satırlar olduğu gibi toplanır; "
                      "kayıt silinmez veya tekilleştirilmez.",
                      f"Eksik tutar: {c['b2b_amount_missing']}; sıfır: {c['b2b_amount_zero']}; negatif: "
                      f"{c['b2b_amount_negative']}; tarihi eksik B2B satırı: {c['b2b_undated']}.",
                      "Negatif/sıfır B2B satırları iade, iptal veya bedelsiz ürün mü?",
                      "ERP belgelerinden kontrol edin.", "orta"))
    if reg.get("status") == "ok":
        if ws_c:
            out.append(_w("B2B-BOSLUK", "Kayıt kodu yalnız boşluk farkıyla eşleşiyor",
                          "Kodlar kırpılmadan karşılaştırılır; yalnız baştaki/sondaki boşlukla eşleşen müşteri kayıtlı "
                          "sayılmaz; İzahat'ta 'Kontrol · boşluk farkı' olarak gösterilir.", f"Müşteri: {len(ws_c)} — {_codes(ws_c)}.",
                          "Bu kodlar kayıt listesindekiyle aynı müşteri mi?",
                          "ERP'de kodun doğru yazımını düzeltin veya teyit edin.", "orta", ws_c))
        if inv_c:
            out.append(_w("B2B-FATURA", "Fatura hesabı kayıtlı, sipariş müşterisi değil",
                          "Müşteri kimliği sipariş kaynağıdır (Pro_kodu; boşsa Musterikod). Bu müşterilerin siparişleri "
                          "kayıt listesindeki bir fatura hesabına faturalanmış; müşterinin kendisinin kayıtlı olduğu "
                          "kanıtlanmadı. İzahat'ta 'Kontrol · fatura hesabı (bayi) kayıtlı' olarak gösterilir; kayıtlı da "
                          "kayıtsız da sayılmaz.",
                          f"Müşteri: {len(inv_c)} — {_codes(inv_c)}.",
                          "B2B kullanıcısı bayi/fatura hesabı adına mı açılıyor, yoksa sipariş veren müşteri adına mı?",
                          "Hangi kodun B2B kaydı için esas olduğunu belirtin.", "yuksek", inv_c))
        if inv_b2b:
            out.append(_w("B2B-FATURA-B2B", "Yalnız fatura hesabı olarak eşleşen kayıt kodunun B2B alımı var",
                          "Bu kayıt kodları sipariş müşterisi olarak görünmüyor, ama bağlı satışlarda B2B satırları "
                          "var; 'B2B'den hiç almamış' sayılmadı, İzahat'ta 'Kontrol' olarak gösterilir.", f"Kayıt kodu: {len(inv_b2b)} — {_codes(inv_b2b)}.",
                          "Bu B2B kullanıcıları bayi hesabı mı?", "Kodun eşleşme kuralını teyit edin.", "orta", inv_b2b))
        if unreg:
            out.append(_w("B2B-KAYITSIZ-ALIM", "B2B'den alım var ama kayıt listesinde teyit edilmedi",
                          "AG = B2B satırı olan müşterinin kodu B sütununda birebir yok. Kayıt başka kodla (ör. fatura "
                          "hesabı) yapılmış olabilir; müşteri 'listede yok' değil, İzahat'ta 'Kontrol' olarak gösterilir.",
                          f"Müşteri: {len(unreg)}; B2B satırı: {c['b2b_lines_not_confirmed']} — {_codes(unreg)}.",
                          "Bu müşterilerin B2B kullanıcısı hangi kodla tanımlı?",
                          "Kayıt listesindeki kodu düzeltin veya eşleşmeyi teyit edin.", "yuksek", unreg))
        if reg.get("duplicates"):
            dups = sorted(reg["duplicates"])
            out.append(_w("B2B-KAYIT-TEKRAR", "Kayıt listesinde tekrar eden kod",
                          "Aynı kod birden çok satırda (ör. bir firmanın birden çok kullanıcısı). Müşteri bir kez sayılır.",
                          f"Tekrarlı kod: {len(dups)} — {_codes(dups)}.",
                          "Tekrarlar aynı firmanın farklı kullanıcıları mı?", "Bilgi amaçlıdır.", "dusuk", dups))
        if c["registration_unmatched"]:
            out.append(_w("B2B-KAYIT-SATISSIZ", "Kayıtlı ama satış müşterisi olarak bulunamayan kodlar",
                          "Bu kodlar tabloda ayrı satırdır (İzahat: 'bu kodla satış yok' veya 'Kontrol'); "
                          "temsilci satış dosyasından alınamaz, ad varsa kayıt listesinin 'Kullanıcı Cari' sütunundandır.",
                          f"Kod: {c['registration_unmatched']} / {c['registration_unique']}.",
                          "Bu kayıtlar yeni müşteri mi, yoksa farklı kodla mı satış yapılıyor?",
                          "Kodları ERP cari listesinde kontrol edin.", "orta"))
        if c["registration_missing_code_rows"]:
            rows = reg.get("missing_code_rows") or []
            out.append(_w("B2B-KAYIT-KODSUZ", "Kayıt listesinde müşteri kodu boş satırlar",
                          "Bu kullanıcı satırlarının B sütunu (Kod No) boş; hangi müşteriye ait oldukları bilinmiyor ve "
                          "hiçbir müşteriyle eşleştirilmedi.", f"Satır: {len(rows)} — Excel satırları: "
                          f"{_codes([str(x) for x in rows], 40)}.",
                          "Bu B2B kullanıcıları hangi müşteri koduna bağlı?", "Kod No alanını ERP/B2B panelinde tamamlayın.",
                          "orta"))
        if c["registration_numeric_cells"]:
            out.append(_w("B2B-KAYIT-SAYI", "Kayıt listesinde sayı olarak saklanmış kod",
                          "Sayı hücresi metne çevrildi; baştaki sıfırlar Excel'de kaybolmuş olabilir.",
                          f"Hücre: {c['registration_numeric_cells']}.", "Bu kodlar metin olarak mı dışa aktarılmalı?",
                          "Dosyayı kodlar metin olacak şekilde yeniden alın.", "orta"))
    out.append(_w("B2B-KAPALI", "KAPALI firmalar B2B Takip tablosuna alınmadı",
                  "Panonun mevcut kuralı: satırların en az yarısında 'Açık / Kapalı' = KAPALI olan müşteri fırsat listelerine "
                  "ve B2B Takip tablosuna alınmaz. Yıllık detay ve Büyüme / Küçülme kuralları değişmedi.",
                  "Çıkarılan (sınıf başına; bir müşteri iki sınıfta sayılabilir): " + " · ".join(
                      f"{CLOSED_LABEL[k]} {v}" for k, v in c["closed_excluded"].items()) + ".",
                  "KAPALI bilgisi sipariş müşterisine mi ait?", "Bilgi amaçlıdır.", "dusuk"))
    return out


# --------------------------------------------------------------------------------------------------------------------
# presentation (shared by both dashboards)
# --------------------------------------------------------------------------------------------------------------------
# One table (father's feedback, 2026-11-08): every row of the A / B / C / Doğrulanacak classification once, plus the
# registered customers that are in none of those lists, with a B2B Kaydı value and an İzahat (explanation) as the last
# column. The classification above is unchanged; only its presentation is unified. A sales customer and a
# registration-only code are always separate rows (a registration code is never merged with a different sales code).
REG_VALUE = {"var": "VAR", "yok": "YOK", "listede_yok": "Listede yok", "kontrol": "Kontrol", "bilinmiyor": "—"}
# key → (B2B Kaydı, İzahat text, filter label); {d} = review days
IZAHAT = {
    "aktif": ("var", "B2B kaydı var · B2B'den alıyor (son {d} gün)", "B2B kaydı var · B2B'den alıyor"),
    "eski": ("var", "B2B kaydı var · eskiden B2B'den alıyordu, son {d} günde almadı",
             "B2B kaydı var · eskiden B2B'den alıyordu"),
    "diger": ("var", "B2B kaydı var · B2B'den hiç almamış, diğer kanallardan alıyor",
              "B2B kaydı var · B2B'den hiç almamış"),
    "kayit_satissiz": ("var", "B2B kaydı var · bu kodla satış yok (ad: kayıt listesi)",
                       "B2B kaydı var · bu kodla satış yok"),
    "kanal_yok": ("var", "B2B kaydı var · B2B kanalı (AG sütunu) doğrulanamadı", "B2B kaydı var · kanal doğrulanamadı"),
    "listede_yok": ("listede_yok", "Kodu B2B kayıt listesinde yok · bizden alıyor, B2B'den hiç almamış",
                    "B2B kayıt listesinde yok"),
    "bayi": ("kontrol", "Kontrol · kendi kodu listede yok, fatura hesabı (bayi) listede var",
             "Kontrol · fatura hesabı (bayi) kayıtlı"),
    "b2b_kayitsiz": ("kontrol", "Kontrol · B2B'den alıyor ama kodu listede yok",
                     "Kontrol · B2B'den alıyor, kodu listede yok"),
    "bosluk": ("kontrol", "Kontrol · kod listede yalnız boşluk farkıyla var", "Kontrol · boşluk farkı"),
    "kanal": ("kontrol", "Kontrol · B2B kanalı yazımı belirsiz ('B2B' farklı yazılmış)", "Kontrol · kanal yazımı belirsiz"),
    "kayit_bagli": ("kontrol", "Kontrol · kayıt kodu satışta yalnız fatura hesabı veya boşluk farkıyla görünüyor",
                    "Kontrol · kayıt kodu satışta başka biçimde"),
    "liste_kullanilamadi": ("bilinmiyor", "Kayıt listesi kullanılamadı · eskiden B2B'den alıyordu, son {d} günde almadı",
                            "Kayıt bilinmiyor · eskiden B2B'den alıyordu"),
}
# Only with the owner's explicit declaration that the registration export lists every B2B user is a customer whose
# code has no exact, ambiguous or invoice-account match (and who never bought through B2B) shown as YOK.
IZAHAT_COMPLETE = {"listede_yok": ("yok", "B2B kaydı yok · bizden alıyor, B2B'den hiç almamış", "B2B kaydı yok")}
V_KIND_KEY = {"Belirsiz kayıt eşleşmesi — boşluk farkı": "bosluk",
              "Belirsiz kayıt eşleşmesi — fatura hesabı kayıtlı": "bayi",
              "B2B'den alıyor, kodu kayıt listesinde yok": "b2b_kayitsiz",
              "Kanal yazımı belirsiz ('B2B' farklı yazılmış)": "kanal",
              "Kayıt kodu yalnız boşluk farkıyla satış müşterisi": "kayit_bagli",
              "Kayıt kodu yalnız fatura hesabı olarak satışta": "kayit_bagli"}
IZAHAT_ORDER = tuple(IZAHAT)


def _complete(result, registration_complete):
    return bool(registration_complete) and result["counts"]["registration_status"] == "ok"


def _izahat(key, days, complete):
    reg, text, label = (IZAHAT_COMPLETE if complete and key in IZAHAT_COMPLETE else IZAHAT)[key]
    return REG_VALUE[reg], text.format(d=days), label


def unified_rows(result: dict, *, registration_complete: bool = False) -> list:
    """The single B2B Takip table. Each sales customer once (a C row that is also in Doğrulanacak is one row: the
    Kontrol reason leads, the former-B2B fact is appended); registration-only codes stay separate rows. `prev` keeps
    the classification letters (A / B / C / V; '' = registered customer in no call list)."""
    c = result["counts"]
    days = c["review_days"]
    per = result["per"]
    complete = _complete(result, registration_complete)
    rows, by = [], {}

    def add(side, code, key, src, letter, extra=""):
        k = (side, code)
        if k in by:
            r = by[k]
            r["prev"] += letter
            if extra and extra not in r["izahat"]:
                r["izahat"] += " · " + extra
            return
        reg, text, _ = _izahat(key, days, complete)
        p = per.get(code) if side == "musteri" else None
        r = {"side": side, "code": code, "name": src.get("name") or (p or {}).get("name", ""),
             "rep": src.get("rep", ""), "sector": src.get("sector", ""),
             "recent": p["recent"] if p else None, "total": p["total"] if p else None, "last": p["last"] if p else None,
             "last_b2b": p["last_b2b"] if p else None,
             "b2b_total": (p["b2b_total"] if c["channel_ok"] else None) if p else None,
             "b2b_reg": reg, "izahat_key": key, "izahat": text + (" · " + extra if extra else ""), "prev": letter}
        by[k] = r
        rows.append(r)

    former = f"eskiden B2B'den alıyordu, son {days} günde almadı"
    for v in result["views"]["V"]:
        side = "musteri" if v["code"] in per and not v.get("excel_row") else "kayit"
        extra = ""
        if side == "kayit":
            parts = ([f"bağlı satış kodu: {v['linked']}"] if v.get("linked") else []) + \
                    ([f"kayıt satırı {v['excel_row']}"] if v.get("excel_row") else [])
            extra = " · ".join(parts)
        add(side, v["code"], V_KIND_KEY[v["kind"]], v, "V", extra)
    for a in result["views"]["A"]:
        add("musteri", a["code"], "listede_yok", a, "A")
    for b in result["views"]["B"]:
        if b["code"] in per:
            add("musteri", b["code"], "diger", b, "B")
        else:
            add("kayit", b["code"], "kayit_satissiz", b, "B", f"kayıt satırı {b['excel_row']}" if b.get("excel_row") else "")
    for x in result["views"]["C"]:
        st = per[x["code"]]["status"]
        key = "eski" if st == "confirmed" else "liste_kullanilamadi" if st == "unknown" else None
        if key is None:                                    # also in Doğrulanacak: one row, its Kontrol reason first
            add("musteri", x["code"], "b2b_kayitsiz", x, "C", former)
        else:
            add("musteri", x["code"], key, x, "C")
    if c["registration_status"] == "ok":                   # registered customers that no call list shows
        for code, p in sorted(per.items()):
            if p["status"] != "confirmed" or p["closed"] or ("musteri", code) in by:
                continue
            key = ("kanal_yok" if not c["channel_ok"] else "kanal" if p["unclear"] else
                   "aktif" if p["has_b2b"] and p["recent_b2b"] else None)
            if key:
                add("musteri", code, key, p, "")
    big = lambda v: -(v if isinstance(v, (int, float)) else -1e18)
    rows.sort(key=lambda r: (r["side"] != "musteri", big(r["recent"]), big(r["total"]), r["code"]))
    return rows


def unified_groups(rows, result: dict, *, registration_complete: bool = False) -> list:
    complete = _complete(result, registration_complete)
    n = collections.Counter(r["izahat_key"] for r in rows)
    return [{"key": k, "label": _izahat(k, result["counts"]["review_days"], complete)[2], "n": n[k]}
            for k in IZAHAT_ORDER if n[k]]


def unified_cols(days=REVIEW_DAYS):
    money = lambda k, l: {"k": k, "l": l, "money": 1}
    return [{"k": "name", "l": "Müşteri"}, {"k": "code", "l": "Kod"}, {"k": "rep", "l": "Temsilci"},
            {"k": "sector", "l": "Sektör"}, money("recent", f"Son {days} gün alım"), money("total", "Toplam alım (dosya)"),
            {"k": "last", "l": "Son alış"}, {"k": "last_b2b", "l": "Son B2B alış"},
            money("b2b_total", "B2B alımı (toplam)"), {"k": "b2b_reg", "l": "B2B Kaydı"}, {"k": "izahat", "l": "İzahat"}]


def unified_note(result: dict, *, registration_complete: bool = False):
    counts = result["counts"]
    days = counts["review_days"]
    yok = ("YOK = kod kayıt listesinde hiçbir biçimde yok (listenin tüm B2B kullanıcılarını içerdiği beyan edildi). "
           if _complete(result, registration_complete) else
           "'Listede yok' = kod kayıt listesinde ne birebir, ne boşluk farkıyla, ne fatura hesabıyla geçiyor; listenin "
           "tüm B2B kullanıcılarını içerdiği henüz teyit edilmediği için YOK yazılmadı. ")
    return ("Tüm müşteriler tek tabloda; son sütun İzahat her satırın B2B durumunu açıklar ve üstteki İzahat seçimiyle "
            "süzülebilir (varsayılan: Tümü). B2B Kaydı: VAR = kod, b2b_kullanici_rapor.xlsx B sütununda (Kod No) birebir "
            "var. " + yok + "Kontrol = eşleşme belirsiz; kayıtlı da kayıtsız da sayılmadı, aramadan önce doğru kodu teyit "
            "edin. B2B alımı = satış dosyası AG sütunu tam 'B2B' olan satırlar. 'Son zamanlarda' = son "
            f"{days} gün (GEÇİCİ; en son satış tarihi {counts['asof']}'den geriye). Kayıt listesinde olup bu kodla satışı "
            "olmayan kodlar ayrı satırdır (temsilci bilinmiyor). KAPALI firmalar tabloya alınmadı. Sıralama: son dönem alımı.")


def module(result: dict, *, review_html: str | None = None, registration_complete: bool = False) -> dict:
    """The 'B2B Takip' table module for either dashboard's generic renderer: one table with an İzahat filter."""
    c = result["counts"]
    rows = unified_rows(result, registration_complete=registration_complete)
    unavailable = c["registration_status"] != "ok"
    m = {"title": "B2B Takip", "rows": rows, "cols": unified_cols(c["review_days"]), "filter": "rep",
         "note": unified_note(result, registration_complete=registration_complete),
         "izahat": unified_groups(rows, result, registration_complete=registration_complete),
         "b2b_warnings": result["warnings"], "b2b_unavailable": unavailable,
         "b2b_reg_file": result["registration"]["file"],
         "b2b_reg_state": REG_STATE_TEXT.get(c["registration_status"], ""),
         "b2b_channel_unavailable": not c["channel_ok"],
         "b2b_registration_complete": _complete(result, registration_complete)}
    if review_html:
        m["review_html"] = review_html
    return m


def unified_counts(rows) -> dict:
    return {"rows": len(rows), "customers": sum(r["side"] == "musteri" for r in rows),
            "registration_only": sum(r["side"] == "kayit" for r in rows),
            "b2b_kaydi": dict(sorted(collections.Counter(r["b2b_reg"] for r in rows).items())),
            "izahat": dict(sorted(collections.Counter(r["izahat_key"] for r in rows).items()))}


def momentum_flag(result: dict, code) -> str | None:
    s = result["status"].get(str(code))
    if s is None:
        return "—" if result["counts"]["registration_status"] != "ok" else ""
    return FLAG_TEXT[s]


def year_fields(result: dict, code, year) -> dict:
    if not result["counts"]["channel_ok"]:
        return {"b2b": None, "b2b_orders": None, "b2b_mixed": 0}
    y = result["yearly"].get(str(code), {}).get(int(year))
    return dict(y) if y else {"b2b": 0, "b2b_orders": 0, "b2b_mixed": 0}


def add_year_fields(rows, result: dict) -> None:
    """Five-column yearly detail: add B2B Alımı / Sipariş (B2B) to each existing year entry (nothing else changes)."""
    for r in rows:
        det = r.get("detail")
        if isinstance(det, dict):
            for y in det.get("years") or []:
                y.update(year_fields(result, r.get("code"), y["y"]))


def add_flag_column(mod: dict, result: dict, after: str = "rep") -> None:
    """Insert the 'B2B' registration column right after the representative column; rows/order/values unchanged."""
    cols = mod["cols"]
    ix = next(i for i, c in enumerate(cols) if c["k"] == after) + 1
    cols.insert(ix, {"k": "b2b", "l": "B2B"})
    for r in mod["rows"]:
        r["b2b"] = momentum_flag(result, r.get("code"))


def insert_after(modules: dict, after: str, key: str, value: dict) -> dict:
    out = {}
    for k, v in modules.items():
        out[k] = v
        if k == after:
            out[key] = value
    if key not in out:
        out[key] = value
    return out


EXTENSION_CSS = """
.b2b-izahat{max-width:100%}
.b2b-flag{font-family:"SF Mono",Menlo,monospace;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:#0e7490;color:#fff}
.b2b-amb{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;background:#fef6e7;color:#b45309;border:1px dashed #b45309}
.b2b-warn{border:1px solid #f1d39b;background:#fef6e7;border-radius:10px;padding:10px 14px;margin:0 0 12px;font-size:12px}
.b2b-warn summary{cursor:pointer;font-weight:700;color:#7a4b06}
.b2b-warn details{margin:6px 0 0 6px}.b2b-warn p{margin:4px 0}
.b2b-miss{border:1px solid #f1d39b;background:#fef6e7;color:#7a4b06;border-radius:10px;padding:10px 14px;margin:0 0 12px;font-size:13px;font-weight:600}
table.sub.b2b5{min-width:440px}table.sub.b2b5 th,table.sub.b2b5 td{padding:6px 10px}
.b2b-foot{font-size:11px;color:var(--muted);margin-top:6px;max-width:460px}
"""

EXTENSION_JS = r"""
(function(){
  const NL=v=>v==null;
  const yearsTable=function(d){
    const five=d.years.some(r=>'b2b' in r);
    if(!five){let y='<table class="sub"><thead><tr><th>Yıl</th><th class=r>Harcama</th><th class=r>Sipariş</th><th class=r>Sip. Ort.</th></tr></thead><tbody>';
      d.years.forEach(r=>{y+=`<tr><td>${r.y}</td><td class="r money">${fmtMoney(r.spend)}</td><td class=r>${r.orders}</td><td class="r money">${fmtMoney(r.avg)}</td></tr>`;});
      return y+'</tbody></table>';}
    let mixed=0;
    let y='<div><table class="sub b2b5"><thead><tr><th>Yıl</th><th class=r>Harcama</th><th class=r>Sipariş</th><th class=r>B2B Alımı</th><th class=r>Sipariş (B2B)</th></tr></thead><tbody>';
    d.years.forEach(r=>{mixed+=r.b2b_mixed||0;
      const mark=r.b2b_mixed?`<span title="${r.b2b_mixed} siparişte B2B ve diğer kanal satırı birlikte — doğrulanmalı">*</span>`:'';
      y+=`<tr><td>${r.y}</td><td class="r money">${fmtMoney(r.spend)}</td><td class=r>${r.orders}</td>`+
         `<td class="r money">${NL(r.b2b)?'—':fmtMoney(r.b2b)}</td><td class=r>${NL(r.b2b_orders)?'—':r.b2b_orders}${mark}</td></tr>`;});
    y+='</tbody></table>';
    y+='<div class="b2b-foot">B2B Alımı = AG sütunu "B2B" olan satırların Nettutar toplamı; Sipariş = farklı sipariş belgesi (fatura değil).'+
       (mixed?` * ${mixed} siparişte B2B ve diğer kanal satırları birlikte — doğrulanmalı.`:'')+' — = bilinmiyor.</div></div>';
    return y;
  };
  // B2B Takip has thousands of rows: its detail is built when a row is first opened (same markup, same data).
  let CUR=null, SEQ=0; const LAZY=new Map();
  const fullDetail=function(d){
    if(!d) return '<div class="detail"><span style="color:#888">Detay yok.</span></div>';
    if(d.buyers){
      let bh='<div class="prods" style="min-width:420px"><div class="prods-h">Bu ürünü alan müşteriler (çağrı listesi)</div>';
      d.buyers.forEach(b=>{bh+=`<div class="prow"><span class="pn">${esc(b.name)}</span><span class="pv">${fmtNum(b.qty)} adet · son ${b.last}</span></div>`;});
      return `<div class="detail">${bh}</div></div>`;
    }
    const hasY=d.years&&d.years.length, hasP=d.products&&d.products.length;
    if(!hasY&&!hasP) return '<div class="detail"><span style="color:#888">Detay yok.</span></div>';
    let y=hasY?yearsTable(d):'';
    let p='';
    if(hasP){p='<div class="prods"><div class="prods-h">Öne Çıkan Ürünler</div>';
      d.products.forEach(pr=>{p+=`<div class="prow"><span class="pn">${esc(pr.name)}</span><span class="pv">${fmtMoney(pr.spend)} · ${fmtNum(pr.qty)} adet</span></div>`;});
      p+='</div>';}
    return `<div class="detail">${y}${p}</div>`;
  };
  window.detailHTML=function(d){
    if(CUR==='b2b'&&d){const id=++SEQ; LAZY.set(id,d); return `<div class="detail" data-lazy="${id}"></div>`;}
    return fullDetail(d);
  };
  const baseDraw=draw;
  const deco=function(id){
    const m=DATA[id]; if(!m||!m.cols) return;
    const rx=m.cols.findIndex(c=>c.k==='b2b_reg');
    if(rx>=0) document.querySelectorAll('#'+id+' .tbl tbody tr.main').forEach(tr=>{const td=tr.children[rx]; if(!td) return;
      const t=td.textContent.trim();
      if(t==='VAR') td.innerHTML='<span class="b2b-flag">VAR</span>';
      else if(t==='Kontrol') td.innerHTML='<span class="b2b-amb" title="Kayıt eşleşmesi belirsiz — kayıtlı da kayıtsız da sayılmadı">Kontrol</span>';});
    const ix=m.cols.findIndex(c=>c.k==='b2b'); if(ix<0) return;
    document.querySelectorAll('#'+id+' .tbl tbody tr.main').forEach(tr=>{const td=tr.children[ix]; if(!td) return;
      const t=td.textContent.trim();
      if(t==='B2B') td.innerHTML='<span class="b2b-flag">B2B</span>';
      else if(t==='Belirsiz') td.innerHTML='<span class="b2b-amb" title="Kayıt eşleşmesi belirsiz — kayıtlı sayılmadı">Belirsiz</span>';});
  };
  window.draw=function(id){if(id==='b2b') LAZY.clear(); CUR=id; try{baseDraw(id);} finally{CUR=null;} deco(id);};
  const B=DATA.b2b;
  if(B&&B.izahat){
    const p=document.getElementById('b2b'); const tb=p.querySelector('.toolbar');
    const all=B.rows.slice();                 // every row; the İzahat choice only narrows what is drawn
    const iz=document.createElement('select'); iz.className='repsel b2b-izahat'; iz.title='İzahat';
    iz.append(new Option('İzahat: Tümü ('+all.length+')',''));
    B.izahat.forEach(g=>iz.append(new Option(g.label+' ('+g.n+')',g.key)));
    tb.append(iz);
    let anchor=tb;
    if(B.b2b_unavailable){const m=document.createElement('div'); m.className='b2b-miss';
      m.textContent='B2B kayıt listesi ('+B.b2b_reg_file+') '+(B.b2b_reg_state||'kullanılamadı')+'. Bu, müşterilerin kayıtsız olduğu anlamına gelmez; B2B Kaydı (VAR / YOK) gösterilmedi. Tablo yalnız AG sütununa göre eskiden B2B\'den alanları gösterir.';
      anchor.after(m); anchor=m;}
    if(B.b2b_channel_unavailable){const m=document.createElement('div'); m.className='b2b-miss';
      m.textContent='Satış dosyasında B2B kanalı (AG sütunu) doğrulanamadı. "B2B\'den almamış" ve "eskiden B2B\'den alıyordu" açıklamaları, B2B alımı sıfırmış gibi üretilmedi.';
      anchor.after(m); anchor=m;}
    if(!B.review_html&&B.b2b_warnings&&B.b2b_warnings.length){
      const box=document.createElement('div'); box.className='b2b-warn';
      let h='<details><summary>Doğrulama uyarıları ve sorular ('+B.b2b_warnings.length+')</summary>';
      B.b2b_warnings.forEach(w=>{h+=`<details data-check="${esc(w.code)}"><summary>${esc(w.title)}</summary><p><b>Sorun / etki:</b> ${esc(w.issue)}</p><p><b>Kanıt:</b> ${esc(w.evidence)}</p><p><b>Soru:</b> ${esc(w.question)}</p><p><b>Çözüm için:</b> ${esc(w.resolution)}</p></details>`;});
      box.innerHTML=h+'</details>'; anchor.after(box);}
    iz.addEventListener('change',()=>{const k=iz.value; B.rows=k?all.filter(r=>r.izahat_key===k):all;
      if('page' in state.b2b) state.b2b.page=0; draw('b2b');});
    p.addEventListener('click',e=>{const tr=e.target.closest('tr.exp'); if(!tr||!p.contains(tr)) return;
      const ph=tr.nextElementSibling&&tr.nextElementSibling.querySelector('[data-lazy]');
      if(ph) ph.outerHTML=fullDetail(LAZY.get(+ph.dataset.lazy));});
    const t=document.querySelector('.tab[data-t="b2b"] .cnt'); if(t) t.textContent=all.length;
    draw('b2b');
  }
  Object.keys(DATA).forEach(id=>{if(state[id]&&!DATA[id].withheld&&!DATA[id].finance&&id!=='b2b') draw(id);});
})();
"""


def inject(page: str) -> str:
    """Append the B2B style and script to a rendered dashboard (one exact anchor at the very end, asserted)."""
    if not page.endswith(ANCHOR) or page.count(ANCHOR) != 1:
        raise ValueError("dashboard HTML does not end with the expected template")
    if page.count("</style>") < 1:
        raise ValueError("dashboard HTML has no style block")
    i = page.index("</style>")
    page = page[:i] + EXTENSION_CSS + page[i:]
    return page[: -len(ANCHOR)] + "</script>\n<script>" + EXTENSION_JS + ANCHOR


def counts_json(result: dict) -> str:
    return json.dumps(result["counts"], ensure_ascii=False, sort_keys=True, indent=1)
