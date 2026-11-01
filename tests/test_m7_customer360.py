"""M7-1 Customer 360 — repository, aggregates, identity, master, receivables, Hedef, events, search, cache, API.

Synthetic workbooks only (tests/fixtures/c360_synth.py): invented 910.xx codes, names, events and amounts. No real
data, no key, no network, no AI call.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import sqlite3
import threading

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as APP
import dashboard_builder as DB
from services import context as CTX
from services import customer360 as C
from services import store as ST
from services.entity import ContextRepository
from tests.fixtures import c360_synth as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
C1, C2, C3, CDUP, CUNM, CKAP, CFB, CXSS, CWS = ("910.00.0001", "910.00.0002", "910.00.0003", "910.00.0004",
                                                "910.00.0005", "910.00.0006", "910.99.0007", "910.00.0008",
                                                "910.00.0009 ")


# ---------------------------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return F.write_all(str(tmp_path_factory.mktemp("c360_data")))


@pytest.fixture(scope="module")
def ctx(data_dir):
    return CTX.build_context(data_dir, inflation_rate=0.40)


@pytest.fixture(scope="module")
def snap(data_dir, ctx):
    return C.build_snapshot(data_dir, ctx)


def detail(snap, code, ctx=None, **kw):
    return C.customer_detail(snap, code, ctx=ctx, **kw)


def years(d):
    return {y["year"]: y for y in d["sales"]["years"]}


# =============================================================================================
# repository basics / identity
# =============================================================================================
def test_full_customer_population_not_top_n(snap, ctx, data_dir):
    sdf = DB.load_sales(os.path.join(data_dir, F.SALES_FILE))
    assert set(snap.customers) == set(sdf["_cust"].unique())          # every canonical customer (D-003, as M5)
    top_n = {r["code"] for k in ("winback", "shrinking", "collections", "risk") for r in ctx.get(k, [])}
    assert set(snap.customers) - top_n, "Customer 360 is not built from the top-N context lists"
    assert snap.summary()["customers"] == len(snap.customers) == 10


def test_canonical_identity_d003_exact_no_trim(snap):
    assert CFB in snap.customers and snap.customers[CFB]["fallback"] is True     # empty Pro_kodu → Musterikod
    assert CWS in snap.customers and CWS.strip() not in snap.customers            # trailing space kept, not trimmed
    assert "910.99.0001" not in snap.customers                                    # an invoice account, not a customer
    assert snap.customers[C1]["fallback"] is False


def test_display_name_rule(data_dir, ctx, snap, tmp_path):
    assert snap.customers[C1]["display_name"] == "Sentetik Bir Kırtasiye Ana Kayıt"      # unique master name
    assert snap.customers[C1]["display_name_source"] == "master"
    assert snap.customers[CUNM]["display_name_source"] == "sales_project"                 # unmatched → ProjeIsmi
    assert snap.customers[CDUP]["display_name"] == "Sentetik Dört Çift Kayıt"            # duplicate master → sales
    folder = F.write_all(str(tmp_path / "d"), master_rows_override=[r for r in F.master_rows()
                                                                     if r["KODU"] != CFB])
    s2 = C.build_snapshot(folder, CTX.build_context(folder, inflation_rate=0.4))
    assert s2.customers[CFB]["display_name"] == "Sentetik Yedi Fatura Müşterisi"
    assert s2.customers[CFB]["display_name_source"] == "invoice_account"


# =============================================================================================
# sales aggregates (47)
# =============================================================================================
def test_first_last_orders_and_counts(snap):
    d = detail(snap, C1)
    s = d["sales"]
    assert (s["first_order_date"], s["last_order_date"]) == ("2023-02-10", "2026-06-30")
    assert s["orders"] == 7 and s["lines"] == 8 and s["distinct_products"] == 3


def test_yearly_amounts_quantities_orders(snap):
    y = years(detail(snap, C1))
    assert y[2023]["net_order_amount"] == 15_000.5 and y[2023]["orders"] == 1 and y[2023]["lines"] == 2
    assert y[2024]["net_order_amount"] == 8_000 and y[2026]["net_order_amount"] == 8_500
    assert y[2025]["net_order_amount"] == 4_500 and y[2025]["missing_amount_lines"] == 1   # missing never zero
    assert y[2023]["quantity"] == 20


def test_active_years_current_year_recent(snap):
    s = detail(snap, C1)["sales"]
    assert s["active_years"] == [2023, 2024, 2025, 2026] and s["current_year"] == 2026
    assert s["has_current_year_orders"] is True and s["recent_orders"] == 4 and s["recent_window_days"] == 365
    old = detail(snap, CKAP)["sales"]
    assert old["has_current_year_orders"] is False and old["recent_orders"] == 0


def test_top_products_and_categories(snap):
    p = detail(snap, C1)["products"]
    assert [x["sku"] for x in p["top"]] == ["C360-001", "C360-002", "C360-003"]
    assert p["top"][0]["net_order_amount"] == 27_000.5 and p["top"][0]["orders"] == 4
    assert p["top"][2]["net_order_amount"] == 0.0 and p["top"][2]["last_order_date"] == "2025-11-03"
    assert [c["label"] for c in p["categories"]] == ["Sentetik Kategori A", "Sentetik Kategori B"]
    assert {g["label"] for g in p["groups"]} == {"SENT-GRUP-A", "SENT-GRUP-B"}
    assert p["limit"] == 10


def test_zero_vs_missing(snap):
    s = detail(snap, C1)["sales"]
    assert s["zero_amount_lines"] == 1 and s["missing_amount_lines"] == 1
    frame = pd.DataFrame({"_cust": ["X", "X"], "_amt": [float("nan"), float("nan")], "_qty": [1.0, 2.0],
                          "_ord": ["a", "b"]})
    agg = C._agg(frame, ["_cust"])
    assert pd.isna(agg.loc["X", "amount"]) and agg.loc["X", "qty"] == 3.0       # all-missing stays missing


def test_amount_label_and_no_margin(snap):
    d = detail(snap, C1)
    assert d["sales"]["amount_label"] == "Net Sipariş Tutarı (Nettutar)" == C.AMOUNT_LABEL
    assert "ciro esası" in d["sales"]["amount_note"]
    blob = json.dumps(d, ensure_ascii=False).lower()
    for banned in ("margin", "marj", "net_tutar_maliyet", "profit", "\"kar\"", "kâr"):
        assert banned not in blob, banned


# =============================================================================================
# master (49) — DQ-5 safe, exact, duplicates never resolved
# =============================================================================================
def test_master_unique_match(snap):
    m = detail(snap, C1)["master"]
    assert m["status"] == "matched" and m["closed"] is False
    assert m["name"]["confidence"] == "trusted" and m["status_raw"] == {"value": "Açık", "confidence": "trusted",
                                                                       "note": None}
    assert m["sector"] == {"value": "S-KIRTASIYE", "confidence": "unverified", "note": None}
    assert m["rep"]["value"] == "REP-A" and m["rep"]["confidence"] == "unverified"


def test_master_unmatched(snap):
    d = detail(snap, CUNM)
    assert d["master"]["status"] == "unmatched" and d["master"]["closed"] is None
    assert d["master"]["rep"]["confidence"] == "unavailable"
    assert "master_unmatched" in {w["code"] for w in d["warnings"]}


def test_master_duplicate_never_arbitrary(snap):
    d = detail(snap, CDUP)
    m = d["master"]
    assert m["status"] == "duplicate" and m["duplicate_rows"] == 2 and m["closed"] is None
    assert m["rep"]["value"] is None and m["sector"]["value"] is None
    assert "master_duplicate" in {w["code"] for w in d["warnings"]}
    assert d["sales"]["orders"] == 1                                                      # sales history kept
    assert snap.master_closed(CDUP) is None


def test_kapali_rep_sector_from_master_not_cached_sales_columns(snap):
    d = detail(snap, CKAP)
    assert d["master"]["closed"] is True and d["master"]["status_raw"]["value"] == "Kapalı"
    assert d["master"]["rep"]["value"] == "REP-B" and d["master"]["sector"]["value"] == "S-KAPALI"
    blob = json.dumps(d, ensure_ascii=False)
    assert "CACHED-REP" not in blob and "CACHED-SEKTOR" not in blob


def test_m5_cust_attrs_unchanged(data_dir):
    sdf = DB.load_sales(os.path.join(data_dir, F.SALES_FILE))
    attrs = DB.cust_attrs(sdf)
    assert attrs[CKAP] == {"rep": "CACHED-REP", "sector": "CACHED-SEKTOR", "closed": False}   # legacy M5 (DQ-5 open)


def test_ids_not_trimmed_in_master(snap):
    d = detail(snap, CWS)
    assert d["master"]["status"] == "unmatched"                   # master has the code without the trailing space
    assert "code_whitespace" in {w["code"] for w in d["warnings"]}
    assert d["identity"]["code_has_surrounding_whitespace"] is True


def test_master_sheet_missing_degrades(tmp_path):
    folder = F.write_all(str(tmp_path / "nm"), master=False)
    s = C.build_snapshot(folder, CTX.build_context(folder, inflation_rate=0.4))
    d = C.customer_detail(s, C1)
    assert s.master_status == "unavailable" and d["master"]["status"] == "unavailable"
    assert d["sales"]["orders"] == 7 and d["identity"]["display_name_source"] == "sales_project"


# =============================================================================================
# invoice accounts / receivables (48)
# =============================================================================================
def accounts(d):
    return {a["account_code"]: a for a in d["receivables"]["accounts"]}


def test_single_and_multiple_exclusive_accounts(snap):
    a = accounts(detail(snap, C1))
    assert set(a) == {C1, "910.99.0001"} and not any(x["shared"] for x in a.values())
    assert a[C1]["is_customer_code"] is True and a["910.99.0001"]["is_customer_code"] is False
    # D-071: signed balance as exported, invoice age (not a due date), overdue unknown — never derived or zero
    assert a[C1]["aging"] == {"available": True, "present_in_file": True, "balance": 2000,
                              "oldest_bucket": "Aralık-2025", "account_class": "unconfirmed",
                              "class_label": "Sınıflandırılmadı", "direction": "debit",
                              "direction_label": "Müşteri Halsa'ya borçlu", "overdue": None,
                              "overdue_status": "unavailable", "confidence": "trusted"}
    zero = a["910.99.0001"]["aging"]
    assert zero["present_in_file"] and zero["balance"] == 0 and zero["direction"] == "zero" and zero["overdue"] is None
    single = accounts(detail(snap, CKAP))
    assert list(single) == [CKAP] and single[CKAP]["aging"]["present_in_file"] is False


def test_shared_account_never_allocated(snap):
    for code in (C2, C3):
        d = detail(snap, code)
        a = accounts(d)["910.99.0002"]
        assert a["shared"] is True and a["shared_with_customers"] == 1 and a["warning"] == C.SHARED_ACCOUNT_WARNING
        assert a["aging"]["balance"] == 4000 and a["aging"]["oldest_bucket"] == "Kasım-2025 ve öncesi"
        assert a["aging"]["overdue"] is None
        assert "shared_invoice_account" in {w["code"] for w in d["warnings"]}
        keys = set(d["receivables"])
        assert not any(k for k in keys if "total" in k or "customer_overdue" in k)     # no customer-level allocation
    assert C.SHARED_ACCOUNT_WARNING.startswith("Bu fatura hesabı birden fazla sipariş müşterisi")


def test_aging_matches_m5_account_level(snap, data_dir):
    _, balances = DB.m_collections(os.path.join(data_dir, F.AGING_FILE))
    assert set(balances) == set(snap.aging)
    for acc, bal in balances.items():
        assert snap.aging[acc]["balance"] == bal and "overdue" not in snap.aging[acc]


def test_unlinked_aging_account_not_attached(snap):
    assert "910.88.0000" in snap.aging and "910.88.0000" not in snap.account_customers
    assert snap.summary()["aging_accounts"] == 4 and snap.summary()["aging_accounts_linked"] == 3
    for code in snap.customers:
        assert "910.88.0000" not in accounts(detail(snap, code))


def test_no_cari_file(tmp_path):
    folder = F.write_all(str(tmp_path / "na"), aging=False)
    s = C.build_snapshot(folder, CTX.build_context(folder, inflation_rate=0.4))
    d = C.customer_detail(s, C1)
    assert s.aging_status == "not_supplied" and d["receivables"]["aging_status"] == "not_supplied"
    assert all(a["aging"]["available"] is False and a["aging"]["confidence"] == "unavailable"
               for a in d["receivables"]["accounts"])


def test_aging_limitations_and_detailed_collections_blocked(snap):
    r = detail(snap, C1)["receivables"]
    assert r["level"] == "invoice_account" and r["detailed_collections"] == "not_supplied"
    assert r["detailed_collections_note"] == "Detaylı tahsilat raporu sağlanmadı."
    assert any("vade tarihi" in t for t in r["limitations"])
    src = open(os.path.join(ROOT, "services", "customer360.py"), encoding="utf-8").read()
    assert "TAHSİLAT ÖNCELİKLİ" not in src and "collection_priority" not in src


# =============================================================================================
# Hedef (50)
# =============================================================================================
@pytest.fixture(scope="module")
def hedef_path(tmp_path_factory):
    return F.write_hedef(str(tmp_path_factory.mktemp("hedef") / F.HEDEF_FILE))


def test_hedef_not_configured_core_works(snap):
    d = detail(snap, C1)
    assert d["hedef"]["status"] == "not_configured" and d["sales"]["orders"] == 7
    assert next(s for s in d["sources"] if s["key"] == "hedef")["status"] == "not_configured"


def test_hedef_configured_fields_and_confidence(data_dir, ctx, hedef_path):
    s = C.build_snapshot(data_dir, ctx, hedef_path=hedef_path, hedef_snapshot="2026-02-15")
    d = C.customer_detail(s, C1)
    h = d["hedef"]
    assert h["status"] == "matched" and h["snapshot_date"] == "2026-02-15" and h["snapshot_date_origin"] == "declared"
    f = {x["key"]: x for x in h["fields"]}
    for k in ("historical_weekly_stamp_capacity", "historical_risk_limit", "historical_unsecured_credit_limit"):
        assert f[k]["confidence"] == "stale" and "Eski veri" in f[k]["note"]
    assert f["visit_count"]["confidence"] == "incomplete" and f["visit_count"]["value"] == 0
    assert f["hedef_rep"]["confidence"] == "unverified" and f["city"]["value"] == "Sentetik İl"
    y = {r["year"]: r for r in h["years"]}
    assert y[2025]["stamp_qty"]["value"] == 340 and y[2025]["reported_net_revenue"]["confidence"] == "unverified"
    people = {p["source"]: p for p in d["salespeople"]}
    assert people["hedef_temsilci"]["values"] == ["HEDEF-REP-X"]
    assert people["hedef_musteri_temsilcisi"]["values"] == ["MUSTERI-REP-Y"]
    assert C.customer_detail(s, CUNM)["hedef"]["status"] == "unmatched"
    # exact match only: the Hedef row is "910.00.0009", the canonical customer carries a trailing space
    assert C.customer_detail(s, CWS)["hedef"]["status"] == "unmatched"
    assert s.summary()["hedef_configured"] is True and s.summary()["hedef_customers_matched"] == 2


def test_hedef_failed_import_core_still_works(data_dir, ctx, tmp_path):
    bad = tmp_path / "hedef bozuk.xlsx"
    pd.DataFrame({"x": [1]}).to_excel(bad, index=False)
    for path in (str(bad), str(tmp_path / "yok hedef.xlsx")):
        s = C.build_snapshot(data_dir, ctx, hedef_path=path)
        d = C.customer_detail(s, C1)
        assert s.hedef_status == "failed" and d["hedef"]["status"] == "failed" and d["sales"]["orders"] == 7
        assert "hedef_failed" in {w["code"] for w in d["warnings"]}
        assert str(tmp_path) not in json.dumps(d, ensure_ascii=False) + json.dumps(s.summary(), ensure_ascii=False)


def test_no_penetration_or_credit_automation(data_dir, ctx, hedef_path):
    s = C.build_snapshot(data_dir, ctx, hedef_path=hedef_path)
    blob = json.dumps(C.customer_detail(s, C1), ensure_ascii=False).lower()
    for banned in ("penetration", "potential", "market_share", "opportunity", "risky", "credit_block", "score"):
        assert banned not in blob, banned
    assert C.customer_detail(s, C1)["master"]["closed"] is False                # stale Risk/Kredi never change status


# =============================================================================================
# events (51)
# =============================================================================================
def test_event_counts_by_type_separate(snap):
    e = detail(snap, C1)["events"]
    assert e["status"] == "ok" and e["orders_by_type"] == {"general": 3, "fuar": 2, "etkinlik": 1, "saha": 1}
    assert e["distinct_events_by_type"] == {"etkinlik": 1, "fuar": 2, "saha": 1}
    assert detail(snap, CUNM)["events"]["orders_by_type"] == {"general": 1, "fuar": 0, "etkinlik": 0, "saha": 0}


def test_event_edition_year_and_name(snap):
    rec = detail(snap, C1)["events"]["recent"]
    kent26 = next(r for r in rec if r["event_name"] == "2026ZQXKENTFUAR")
    assert kent26["order_date"] == "2025-12-15" and kent26["edition_year"] == 2026 and kent26["edition_year_offset"] == 1
    assert [r["order_date"] for r in rec] == sorted((r["order_date"] for r in rec), reverse=True)
    assert all(r["source_type"] != "general" for r in rec)


def test_event_names_never_in_summary_or_logs(data_dir, ctx, caplog):
    with caplog.at_level(logging.DEBUG):
        s = C.build_snapshot(data_dir, ctx)
    blob = json.dumps(s.summary(), ensure_ascii=False) + caplog.text
    for secret in ("ZQXKENT", "ZQXSALON", "ZQXBOLGE", "Sentetik Bir", "910.00.0001", "<script>", "27000"):
        assert secret not in blob, secret


def test_no_conversion_window(snap):
    blob = json.dumps(detail(snap, C1)["events"], ensure_ascii=False).lower()
    for banned in ("conversion", "window", "follow_on", "roi", "is_fair"):
        assert banned not in blob, banned


# =============================================================================================
# search (52)
# =============================================================================================
def codes_of(hits):
    return [h["customer_code"] for h in hits]


def test_search_exact_code_first(snap):
    hits, _ = snap.search("910.00.0001")
    assert hits[0]["customer_code"] == C1 and hits[0]["match"] == "exact_code"


def test_search_code_prefix(snap):
    hits, _ = snap.search("910.00.000")
    assert all(h["match"] in ("code_prefix", "exact_code") for h in hits) and len(hits) == 8   # 0001–0009 minus the 910.99 fallback
    assert codes_of(hits) == codes_of(sorted(hits, key=lambda h: (C.fold(h["display_name"]), h["customer_code"])))


def test_search_name_prefix_then_substring(snap):
    hits, _ = snap.search("sentetik")
    kinds = [h["match"] for h in hits]
    assert kinds == sorted(kinds, key=["exact_code", "code_prefix", "name_prefix", "name_substring",
                                       "code_substring"].index)
    assert hits[-1]["customer_code"] == CXSS and hits[-1]["match"] == "name_substring"
    sub, _ = snap.search("okul")
    assert codes_of(sub) == [C3] and sub[0]["match"] == "name_substring"


def test_search_deterministic_and_limit(snap):
    a, b = snap.search("sentetik", 50), snap.search("sentetik", 50)
    assert a == b
    three, truncated = snap.search("sentetik", 3)
    assert len(three) == 3 and truncated is True and codes_of(three) == codes_of(a[0][:3])
    capped, _ = snap.search("910", 999)
    assert len(capped) <= C.SEARCH_MAX


def test_search_kapali_badge_and_turkish_fold(snap):
    hits, _ = snap.search("altı kapalı")
    assert hits[0]["customer_code"] == CKAP and hits[0]["closed"] is True
    assert codes_of(snap.search("İKİ")[0]) == codes_of(snap.search("iki")[0]) == [C2]
    assert snap.search("Sentetik Dört")[0][0]["closed"] is None                    # duplicate master → unknown


def test_search_no_fuzzy_and_min_chars(snap):
    assert snap.search("sentetk")[0] == [] and snap.search("s")[0] == [] and snap.search("  ")[0] == []
    assert snap.search("910.00.0009")[0][0]["customer_code"] == CWS
    assert snap.search("910.00.0009")[0][0]["match"] == "code_prefix"              # not an exact (trimmed) match


# =============================================================================================
# cache / signature / refresh consistency (54)
# =============================================================================================
def two_source_folder(tmp_path):
    """Folder with sales A (max 2026-06-30) and sales B (adds a 2026-07-15 order)."""
    folder = str(tmp_path / "ab")
    F.write_all(folder, sales_file="Sentetik A satışlar.xlsx")
    rows = F.sales_rows()
    rows.append(F._line(999, "2026-07-15", "910.00.0011", "Sentetik Yeni Müşteri", "910.00.0011",
                        "Sentetik Yeni Müşteri", 777.5))
    F.write_sales(os.path.join(folder, "Sentetik B satışlar.xlsx"), rows)
    srcA = {"stock": os.path.join(folder, F.STOCK_FILE), "sales": os.path.join(folder, "Sentetik A satışlar.xlsx"),
            "aging": os.path.join(folder, F.AGING_FILE)}
    srcB = {**srcA, "sales": os.path.join(folder, "Sentetik B satışlar.xlsx")}
    return folder, CTX.build_context(folder, inflation_rate=0.4, sources=srcA), \
        CTX.build_context(folder, inflation_rate=0.4, sources=srcB)


def test_refresh_switches_source_and_rebuilds(tmp_path):
    folder, ctx_a, ctx_b = two_source_folder(tmp_path)
    repo = ContextRepository(data_folder=folder, context=ctx_a)
    c360 = C.Customer360Repository(folder, repo, background=False)
    a = c360.snapshot()
    assert a.sales_file == "Sentetik A satışlar.xlsx" and a.as_of == dt.date(2026, 6, 30)
    assert "910.00.0011" not in a.customers and c360.snapshot() is a and c360.builds == 1   # cached
    repo.replace_context(ctx_b)                                          # controlled refresh installs source B
    b = c360.snapshot()
    assert b is not a and b.sales_file == "Sentetik B satışlar.xlsx" and b.as_of == dt.date(2026, 7, 15)
    assert "910.00.0011" in b.customers and c360.builds == 2


def test_source_date_mismatch_never_served(tmp_path):
    folder, ctx_a, ctx_b = two_source_folder(tmp_path)
    bad = {**ctx_b, "as_of_data_date": "2026-06-30"}                     # names B but carries A's date
    repo = ContextRepository(data_folder=folder, context=bad)
    c360 = C.Customer360Repository(folder, repo, background=False)
    with pytest.raises(C.Customer360Error) as e:
        c360.snapshot()
    assert e.value.code == "source_date_mismatch" and folder not in e.value.message
    st = c360.state()
    assert st["state"] == "error" and st["error_code"] == "source_date_mismatch"
    repo.replace_context(ctx_b)                                          # consistent again → builds
    assert c360.snapshot().as_of == dt.date(2026, 7, 15)


def test_missing_sales_file_and_traversal(tmp_path, ctx, data_dir):
    for name in ("../" + F.SALES_FILE, "yok satışlar.xlsx", None):
        bad = {**ctx, "source_files": {**ctx["source_files"], "sales": name}}
        with pytest.raises(C.Customer360Error) as e:
            C.build_snapshot(data_dir, bad)
        assert e.value.code == "sales_source_missing"


def test_stale_snapshot_dropped_immediately_background(tmp_path):
    folder, ctx_a, ctx_b = two_source_folder(tmp_path)
    repo = ContextRepository(data_folder=folder, context=ctx_a)
    gate = threading.Event()
    real = C.build_snapshot

    def slow(*a, **k):
        gate.wait(10)
        return real(*a, **k)
    c360 = C.Customer360Repository(folder, repo, background=True, builder=slow)
    assert c360.state() == {"state": "building"}
    gate.set()
    for _ in range(200):
        if c360.state()["state"] == "ready":
            break
        threading.Event().wait(0.05)
    assert c360.state()["as_of_data_date"] == "2026-06-30"
    gate.clear()
    repo.replace_context(ctx_b)
    with pytest.raises(C.Customer360NotReady):
        c360.snapshot()                                                   # old A snapshot is NOT served
    gate.set()
    for _ in range(200):
        if c360.state()["state"] == "ready":
            break
        threading.Event().wait(0.05)
    assert c360.state()["as_of_data_date"] == "2026-07-15"


def test_no_excel_read_per_search_or_detail(snap, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Excel read after build")
    monkeypatch.setattr(DB, "read_sales_raw", boom)
    monkeypatch.setattr(DB, "read_aging_raw", boom)
    monkeypatch.setattr(pd, "read_excel", boom)
    snap.search("sentetik")
    detail(snap, C1)


def test_build_failure_is_safe(tmp_path, ctx, data_dir, caplog):
    def broken(*a, **k):
        raise ValueError(os.path.join(str(tmp_path), "secret path"))
    c360 = C.Customer360Repository(data_dir, ContextRepository(context=ctx), background=False, builder=broken)
    with caplog.at_level(logging.WARNING), pytest.raises(C.Customer360Error) as e:
        c360.snapshot()
    assert e.value.code == "build_failed" and str(tmp_path) not in e.value.message
    assert str(tmp_path) not in caplog.text
    c360.retry()
    assert c360._error is None


# =============================================================================================
# API (53) — deterministic, read-only, no AI, no paths
# =============================================================================================
class _NoAIProvider:
    name, model = "guard", "guard"

    def is_available(self):
        return True

    def __getattr__(self, item):
        raise AssertionError(f"AI provider touched: {item}")


@pytest.fixture()
def client(data_dir, ctx):
    repo = ContextRepository(data_folder=data_dir, context=ctx)
    c360 = C.Customer360Repository(data_dir, repo, background=False)
    with TestClient(APP.app) as cl:
        APP.app.dependency_overrides[APP.get_repo] = lambda: repo
        APP.app.dependency_overrides[APP.get_c360] = lambda: c360
        yield cl, repo, c360
    APP.app.dependency_overrides.clear()


def _db_counts():
    with sqlite3.connect(ST.DB_PATH) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("actions", "action_history", "memory", "ai_cache", "ai_calls")}


def test_api_status_search_detail(client):
    cl, _, _ = client
    st = cl.get("/api/customer360/status").json()
    assert st["state"] == "ready" and st["customers"] == 10 and st["as_of_data_date"] == "2026-06-30"
    r = cl.get("/api/customer360/search", params={"q": "sentetik", "limit": 5}).json()
    assert r["count"] == 5 and r["truncated"] is True and r["min_chars"] == 2 and r["as_of_data_date"] == "2026-06-30"
    d = cl.get(f"/api/customer360/{C1}")
    assert d.status_code == 200
    body = d.json()
    assert body["ai_used"] is False and body["identity"]["customer_code"] == C1
    assert set(body) == {"customer_code", "as_of_data_date", "identity", "sources", "master", "salespeople", "sales",
                         "products", "opportunities", "receivables", "hedef", "events", "crm", "activity",
                         "warnings", "ai_used"}                        # M7-3 opportunities, M7-5 crm sections
    assert {s["key"] for s in body["sources"]} == {"sales", "master", "aging", "hedef", "events",
                                                   "detailed_collections"}


def test_api_unknown_customer_and_encoded_codes(client):
    cl, _, _ = client
    r = cl.get("/api/customer360/999.99.9999")
    assert r.status_code == 404 and "999.99.9999" in r.text
    assert cl.get("/api/customer360/" + "910.00.0009%20").json()["customer_code"] == CWS


def test_api_hedef_unavailable_and_mismatch(client, data_dir, ctx):
    cl, repo, _ = client
    assert cl.get(f"/api/customer360/{C1}").json()["hedef"]["status"] == "not_configured"
    bad = {**ctx, "as_of_data_date": "2026-01-01", "generated_at": "x"}
    c360 = C.Customer360Repository(data_dir, ContextRepository(data_folder=data_dir, context=bad), background=False)
    APP.app.dependency_overrides[APP.get_c360] = lambda: c360
    for url in ("/api/customer360/search?q=sentetik", f"/api/customer360/{C1}"):
        r = cl.get(url)
        assert r.status_code == 503 and r.json()["detail"]["error_code"] == "source_date_mismatch"
        assert data_dir not in r.text and "Traceback" not in r.text
    assert cl.get("/api/customer360/status").json()["state"] == "error"


def test_api_building_state(client, data_dir, ctx):
    cl, repo, _ = client
    gate = threading.Event()

    def slow(*a, **k):
        gate.wait(10)
        return C.build_snapshot(*a, **k)
    c360 = C.Customer360Repository(data_dir, repo, background=True, builder=slow)
    APP.app.dependency_overrides[APP.get_c360] = lambda: c360
    r = cl.get("/api/customer360/search?q=sentetik")
    assert r.status_code == 503 and r.json()["detail"]["state"] == "building"
    assert cl.get("/api/customer360/status").json()["state"] == "building"
    gate.set()


def test_api_never_calls_ai_and_never_mutates(client):
    cl, _, _ = client
    APP.app.dependency_overrides[APP.get_agent] = lambda: (_ for _ in ()).throw(AssertionError("agent used"))
    before = _db_counts()
    cl.get("/api/customer360/status")
    cl.get("/api/customer360/search", params={"q": "sentetik"})
    for code in (C1, C2, CXSS):
        assert cl.get(f"/api/customer360/{code}").status_code == 200
    assert _db_counts() == before and before["ai_calls"] == 0


def test_api_shows_existing_actions_and_memory_read_only(client, ctx):
    cl, _, _ = client
    ST.add_memory("customer", C1, "contacted", "Sentetik takip notu", next_followup="2026-07-10")
    ST.upsert_actions([{"category": "winback", "entity_type": "customer", "entity_id": C1, "priority": "high",
                        "score": 70, "title": "Sentetik eski aksiyon", "facts": ["x"]}])
    body = cl.get(f"/api/customer360/{C1}").json()
    act = body["activity"]
    assert act["memory"][0]["kind"] == "contacted" and act["memory"][0]["detail"] == "Sentetik takip notu"
    assert any(a["title"] == "Sentetik eski aksiyon" and a["origin"] == "stored" for a in act["stored_actions"])
    cur = [a for a in ctx["actions"] if a["entity_type"] == "customer" and a["entity_id"] == C1]
    assert len(act["current_actions"]) == len(cur)


def test_api_errors_have_no_paths(client, data_dir):
    cl, _, _ = client
    for url in ("/api/customer360/search?q=x", "/api/customer360/%2E%2E", "/api/customer360/status"):
        assert data_dir not in cl.get(url).text


def test_m5_customer_endpoint_unchanged(client):
    cl, _, _ = client
    r = cl.get(f"/api/agent/customer/{C1}")
    assert r.status_code in (200, 404)                 # M5 index only knows action/top-N customers (unchanged)
    assert "identity" not in r.text and "receivables" not in r.text


# =============================================================================================
# separation: no AI packets, no owner sources, no new action types
# =============================================================================================
def test_no_ai_or_owner_sources_in_customer360():
    src = open(os.path.join(ROOT, "services", "customer360.py"), encoding="utf-8").read()
    for banned in ("services.ai", "services.agent", "evidence", "openai", "anthropic", "importers.kredi",
                   "importers.gider_gelir", "importers.butce", "importers.trodat", "import_kredi", "import_butce",
                   "import_gider_gelir", "import_trodat", "register(", "make_action"):
        assert banned not in src, banned
    for f in ("services/evidence.py", "services/agent.py", "services/context.py", "services/actions.py",
              "services/action_sources.py", "services/refresh.py"):
        assert "customer360" not in open(os.path.join(ROOT, f), encoding="utf-8").read(), f


def test_no_new_ranking_or_crm_writes():
    src = open(os.path.join(ROOT, "services", "customer360.py"), encoding="utf-8").read()
    for banned in ("add_memory", "set_status", "upsert_actions", "cross_sell", "opportunity_score"):
        assert banned not in src, banned
    app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert not re.search(r'@app\.(post|put|patch|delete)\("/api/customer360', app_src)


# =============================================================================================
# dashboard tab (structure)
# =============================================================================================
C3_START, C3_END = "\n<!--C360-TAB-START-->", "<!--C360-TAB-END-->"
C3_BUTTON = '<button class="tab" data-t="c360">Müşteri 360</button>'


@pytest.fixture(scope="module")
def built(data_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("dash")
    a, b = str(out / "with.html"), str(out / "without.html")
    DB.build(data_dir, a, today=dt.date(2026, 7, 1))
    DB.build(data_dir, b, today=dt.date(2026, 7, 1), c360_panel=False)
    return open(a, encoding="utf-8").read(), open(b, encoding="utf-8").read()


def _c3_block(h):
    return h[h.index(C3_START): h.index(C3_END) + len(C3_END)]


def test_tab_present_and_rest_byte_identical(built):
    with_c3, without = built
    assert with_c3.count(C3_BUTTON) == 1 and with_c3.count('<div id="c360" class="panel"') == 1
    assert with_c3.replace(C3_BUTTON, "", 1).replace(_c3_block(with_c3), "", 1) == without
    tabs = re.findall(r'<button class="tab" data-t="([a-z0-9]+)"', with_c3)
    assert tabs[-6:] == ["c360", "opp", "fair", "crm", "own", "ai"]             # M7-3 Fırsatlar, M7-4 Fuar Merkezi


def test_tab_script_safe_and_read_only(built):
    block = _c3_block(built[0])
    js = block[block.index("<script>") + 8: block.rindex("</script>")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "method:'POST'",
                   "/api/agent/", "/analyze", "enrich", "localStorage", "sessionStorage"):
        assert banned not in js, banned
    assert "textContent" in js and "/api/customer360/" in js
    assert "Müşteri 360 için panoyu http://127.0.0.1:8000/ üzerinden açın." in js
    for label in ("Eski veri", "Eksik kayıt olabilir", "Doğrulanmadı", "Güvenilir"):
        assert label in js
    for sec in ("Kimlik", "Satış geçmişi", "Ürünler / kategoriler", "Cari hesap", "Hedef bilgileri",
                "Fuar / etkinlik geçmişi", "Aksiyonlar ve geçmiş"):
        assert sec in js
