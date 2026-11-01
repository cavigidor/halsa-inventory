"""M8-R3/R4 — read-only strict reconciliation and owner-approved per-metric Option B prerequisites.

M8-R4 (D-069) replaces the global non-FAIL prerequisite in this diagnostic with a per-metric, generation-bound
source-dependency check. This tool still decides no certification: it never modifies `SourceEvidence.problems()` /
`_scoped_problems()`, the register, an approval, `GENERATION_BOUND_API_EVIDENCE`, the strict reconciliation result
or its exit code, and it writes nothing.

For each named metric it reports, from ONE manifest built from the exact frozen bytes of this run:
1. the strict, source-wide result of each source the metric reads (as `reconcile_sources` printed it);
2. the metric's actual production gate decision (`accuracy.current_gate`, bound to this generation exactly as the API
   binds it) and the parts that decision is made of: the certified ceiling, open register blockers, unapproved
   definitions, the source problems as the gate judges them for this metric (dependency-scoped where the register
   declares `requires`, strict otherwise), time rules and dependencies; plus every strict problem of those sources
   that does not appear in that judgement with the same text — listed as still present in the source (re-judged for
   the fields / joins the metric reads, or not applied to it), never as resolved;
3. whether the Option B release prerequisites (release_gate.md §2 and §8.3, D-069, `validate_register`) permit certification.

Evidence that is missing, unbound to the served bytes or from another generation is reported as unavailable and never
passes. Output: codes, fixed Turkish reason texts, schema labels (column headers), counts and states only.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping

from services import accuracy as ACC
from services.stock_policy import RETIREMENT_CODE

# The metrics this diagnostic reports. Identifiers and order are kept stable so earlier diagnostic results stay
# comparable; `stock.reorder` / `stock.overstock` are RETIRED by D-070 (no longer release candidates) and stay listed
# only for historical diagnostic visibility, labelled RETIRED — D-070 (`retired_by`).
CANDIDATES = ("receivables.collections", "stock.reorder", "stock.overstock", "stock.product_counts")
# a register blocker with this code means the metric's output was withdrawn by an implemented decision
RETIRED_BY = {RETIREMENT_CODE: "D-070"}
# the core records every reconciliation run is expected to carry (liste = the customer master sheet of the sales workbook)
EXPECTED_CORE = ("aging", "liste", "sales", "stock")

# what kind of work removes a blocker (one or more; the first is what has to happen first)
BUSINESS = "business_answer"                 # an owner / accountant answer, recorded as a D-xxx decision
DATE = "authoritative_date"                  # a declared business date / period from the person who exports
DECLARATION = "owner_declaration"            # a fingerprint-bound owner declaration at upload (selection, totals, mapping)
TECHNICAL = "technical_fix"                  # a reviewed code change with tests, or a corrected source export
RECONCILIATION = "independent_reconciliation"  # real-data reconciliation evidence (reconciliation_ref)
RELEASE = "release_prerequisite"             # release_gate.md §2 / §8.3, validate_register
UNAVAILABLE = "evidence_unavailable"         # no evidence bound to this generation
RETIREMENT = "retirement_decided"            # an implemented retirement decision (D-070), not an open question
UNCLASSIFIED = "unclassified"

CATEGORY_TR = {BUSINESS: "iş kararı (sahip / muhasebe)", DATE: "yetkili rapor tarihi / dönem",
               RETIREMENT: "uygulanmış kaldırma kararı (D-070; bekleyen soru değil)",
               DECLARATION: "dosyaya bağlı sahip beyanı (seçim, toplam, eşleme)",
               TECHNICAL: "teknik düzeltme veya kaynak düzeltmesi (ayrı inceleme + test)",
               RECONCILIATION: "bağımsız gerçek veri mutabakatı", RELEASE: "yayın ön koşulu (release_gate)",
               UNAVAILABLE: "kanıt yok / başka üretime ait", UNCLASSIFIED: "sınıflandırılmadı"}

_BLOCKER_CATEGORY = {
    RETIREMENT_CODE: (RETIREMENT,),          # presentation only: the blocker code and its INVALID state are unchanged
    "DQ-4": (BUSINESS, TECHNICAL), "STOCK-REPORT-SCOPE": (BUSINESS, TECHNICAL), "INV-VALUE": (BUSINESS,),
    "OVERDUE-DEF": (BUSINESS,), "STOCK-DATE": (DATE,), "AGING-DATE": (DATE,), "REAL-DATA": (RECONCILIATION,),
    # D-071: account classes beyond 320 need an owner / accountant answer; FX balances need an answer, then code;
    # overdue needs the agreement-based report (owner supplies it; then a reviewed decision and producer).
    "CARI-ACCOUNT-CLASS": (BUSINESS,), "CARI-FX": (BUSINESS, TECHNICAL), "CARI-OVERDUE-UNSUPPORTED": (BUSINESS,),
}

# source / time problem texts (fixed strings in services/accuracy.py; field-level ones end with ": <field>.")
_TEXT_CATEGORY = (
    ("Kaynak raporun tarihi", (DATE,)), ("Kaynak rapor güncel değil", (DATE,)),
    ("Raporlama dönem", (DATE,)), ("Tarihsel dönem", (DATE,)),
    ("Beyan edilen tarihler kaynakla çelişiyor", (DATE,)),
    ("Kaynak dosya açıkça seçilmedi", (DECLARATION,)),
    ("Kaynak toplamlarının mutabakatı yapılmadı", (DECLARATION,)),
    ("Kaynak eşlemesinin onay", (DECLARATION,)),
    ("Kaynak eşlemesi onaylanmadı", (DECLARATION,)),
    ("Kaynakta yinelenen kayıtlar var", (BUSINESS, TECHNICAL)),
    ("Müşteri veya ürün eşleşmesi hatalı", (BUSINESS, TECHNICAL)),
    ("Kullanılan eşleşme sorunlu", (BUSINESS, TECHNICAL)),
    ("Tutar alanlarında boş hücreler", (BUSINESS,)), ("Kullanılan tutar alanında boş hücreler", (BUSINESS,)),
    ("Kaynak rapor sağlanmadı", (UNAVAILABLE,)), ("Kaynağın okunduğu doğrulanmadı", (UNAVAILABLE,)),
    ("Kaynakta", (TECHNICAL,)), ("Kullanılan", (TECHNICAL,)), ("Kaynak ", (TECHNICAL,)),
    ("Alan ", (TECHNICAL,)), ("Eşleşme denetimi", (TECHNICAL,)), ("Müşteri veya ürün eşleşmeleri doğrulanmadı", (TECHNICAL,)),
    ("Gerekli alanlarda", (TECHNICAL,)), ("Dış bağlantı", (TECHNICAL,)), ("Tarih ", (TECHNICAL,)),
    ("Başlık", (TECHNICAL,)), ("Boş tutar", (TECHNICAL,)), ("Excel hata", (TECHNICAL,)), ("Formül", (TECHNICAL,)),
    ("Eksik değer", (TECHNICAL,)), ("Yinelenen kayıt", (TECHNICAL,)),
)


def categorize_text(text: str) -> tuple[str, ...]:
    for prefix, cats in _TEXT_CATEGORY:
        if text.startswith(prefix):
            return cats
    return (UNCLASSIFIED,)


def strict_by_source(results: list[dict]) -> dict[str, str]:
    """Worst strict status per source from `reconcile_sources` results (`<source>:…`, stock row checks → stock)."""
    order = {"PASS": 0, "OPEN": 1, "FAIL": 2}
    out: dict[str, str] = {}
    for r in results:
        code = r.get("code") or ""
        src = code.split(":", 1)[0] if ":" in code else ("stock" if code.startswith("stock_") else None)
        if src is None:
            continue
        cur = out.get(src, "PASS")
        out[src] = max(cur, r["status"], key=order.get)
    return out


def _items(problems, *, applies: bool = True) -> list[dict]:
    return [{"state": st.value, "text": why, "categories": list(categorize_text(why)), "applies": applies}
            for st, why in problems]


def _strict_only(problems) -> list[dict]:
    """Strict, source-wide problems that do not appear in this metric's judgement with the same text. A field-,
    join- or totals-level problem is re-judged for the fields / joins the metric reads (its scoped form, if any, is in
    `problems`); the source still carries it — it is never reported as resolved."""
    rejudged = ACC.SourceEvidence._FIELD_LEVEL | ACC.SourceEvidence._TOTALS
    return [{"state": st.value, "text": why, "categories": list(categorize_text(why)), "applies": False,
             "why": "rejudged_for_used_fields_and_joins" if why in rejudged else "not_applied_to_this_metric"}
            for st, why in problems]


def diagnose(manifest, served: Mapping[str, str | None], today: dt.date, *, strict_overall: str | None,
             strict_sources: Mapping[str, str] | None = None, keys=CANDIDATES,
             expected_generation: str | None = None) -> dict[str, Any]:
    """`served`: the SHA-256 of the bytes this run actually read, per recorded source key (liste = the sales
    workbook). `expected_generation`: the generation the caller claims to be judging; defaults to the manifest's own.
    Mismatches bind nothing (the production gate's own rule) and are reported as unavailable."""
    snap = manifest.bound_snapshot(served)
    expected = expected_generation if expected_generation is not None else manifest.generation_id
    gate = ACC.current_gate(today, snap, expected)
    reg, defs = gate.register, gate.definitions
    generation_ok = snap.generation_id is not None and snap.generation_id == expected
    # R3-1: a matching generation ID does NOT mean every source is usable — a record can be unbound (its bytes are not
    # the recorded ones) or carry no evidence. Completeness is reported separately and never implied by the ID.
    bound = set(snap.evidence) if generation_ok else set()
    unbound = sorted(set(snap.unbound) | ({k for k in EXPECTED_CORE if k in snap.evidence} if not generation_ok
                                            else set()))
    missing = sorted(k for k in EXPECTED_CORE if k not in snap.evidence and k not in snap.unbound)
    complete = generation_ok and not unbound and not missing and all(k in bound for k in EXPECTED_CORE)
    out: dict[str, Any] = {
        "schema": "halsa-metric-diagnostics/1",
        "read_only": True,
        "generation": {"generation_id": (snap.generation_id or "")[:16], "expected": (expected or "")[:16],
                       "generation_id_matches": generation_ok,
                       "core_evidence_complete_and_bound": complete,
                       "bound_sources": sorted(bound), "unbound_sources": unbound,
                       "missing_evidence_sources": missing},
        "strict": {"overall": strict_overall, "sources": dict(strict_sources or {})},
        "generation_bound_api_evidence": ACC.GENERATION_BOUND_API_EVIDENCE,
        "metrics": {},
    }
    for key in keys:
        out["metrics"][key] = _metric(key, reg, defs, gate, snap, generation_ok, today, strict_overall,
                                      dict(strict_sources or {}))
    return out


def _metric(key, reg, defs, gate, snap, generation_ok, today, strict_overall, strict_sources) -> dict[str, Any]:
    d = gate.decide(key)
    m = reg.get(key)
    if m is None:
        return {"title": key, "gate_state": d.state.value, "gate_codes": list(d.codes), "unregistered": True,
                "certification_permitted": False}
    blockers = [{"code": b.code, "state": b.state.value, "text": b.reason_tr,
                 "categories": list(_BLOCKER_CATEGORY.get(b.code, (UNCLASSIFIED,)))} for b in m.blockers]
    unapproved = [{"key": dk, "text": (defs[dk].title_tr if dk in defs else dk),
                   "question": (defs[dk].question_ref if dk in defs else None), "categories": [BUSINESS]}
                  for dk in m.definitions if dk not in defs or not defs[dk].approved]
    sources, evidence_states = {}, []
    for src in m.sources:
        ev = snap.evidence.get(src) if generation_ok else None
        entry: dict[str, Any] = {"strict": strict_sources.get(src), "judgement": "scoped" if m.requires else "strict"}
        if ev is None:
            entry.update(available=False, reason=("EVIDENCE-GENERATION" if (not generation_ok or src in snap.unbound)
                                                  else "NO-EVIDENCE"),
                         problems=[{"state": ACC.State.NOT_VERIFIED.value, "text": "Kaynak doğrulama kanıtı yok.",
                                    "categories": [UNAVAILABLE], "applies": True}])
            evidence_states.append(ACC.State.NOT_VERIFIED)
            sources[src] = entry
            continue
        scope = m.scope(src) if m.requires else None
        judged = ev.problems(scope) + ACC.temporal_problems(m, ev, today)
        judged_texts = {why for _, why in judged}
        outside = [(st, why) for st, why in ev.problems() if why not in judged_texts]
        entry.update(available=True, problems=_items(judged),
                     strict_only_still_in_source=_strict_only(outside),
                     fields=list(scope.fields) if scope else None, joins=list(scope.joins) if scope else None)
        evidence_states.extend(st for st, _ in judged)
        sources[src] = entry
    deps = [{"key": dep, "state": gate.decide(dep).state.value} for dep in m.depends_on
            if not gate.decide(dep).displayable]
    # the gate's own decision is authoritative; the breakdown must recompose to it exactly
    recomposed = ACC.worst(m.certified, *(b.state for b in m.blockers),
                           *([ACC.State.NOT_VERIFIED] if unapproved else []), *evidence_states,
                           *(ACC.State(x["state"]) for x in deps))
    if m.certified != ACC.State.VERIFIED and recomposed == ACC.State.VERIFIED:
        recomposed = m.certified
    evidence_state = (ACC.worst(*evidence_states).value if evidence_states else ACC.State.VERIFIED.value)
    # Option B: verify ONLY the fields and joins this metric uses, plus its actual metric dependencies.
    # An unrelated strict FAIL is still printed and reported in Veri Kontrol, but is not this metric's prerequisite.
    # Unbound or missing evidence, UNKNOWN / OPEN on a used field, or an affected downstream metric never passes.
    scoped_ready = (generation_ok and all(src in snap.evidence and src not in snap.unbound for src in m.sources)
                    and not evidence_states and not deps)
    prereq = _prerequisites(m, d, defs, scoped_ready)
    retired = sorted({RETIRED_BY[b.code] for b in m.blockers if b.code in RETIRED_BY})
    return {
        "title": m.title_tr, "retired_by": retired[0] if retired else None,
        "sources": list(m.sources), "gate_state": d.state.value, "gate_codes": list(d.codes),
        "breakdown_matches_gate": recomposed == d.state,
        "certified_ceiling": m.certified.value, "open_blockers": blockers, "unapproved_definitions": unapproved,
        "source_evidence_state_for_this_metric": evidence_state, "source_judgement": sources,
        "dependencies_withheld": deps,
        "release_prerequisites": prereq,
        "certification_permitted": all(p["met"] is True for p in prereq),
    }


def _prerequisites(m, d, defs, scoped_ready) -> list[dict]:
    """D-069 Option B: own generation-bound source/field/join/time evidence and metric dependencies,
    not the global strict result. `met` is True, False or None (unevaluated; never passes)."""
    return [
        {"code": "gate_state_verified", "met": d.state == ACC.State.VERIFIED, "ref": "release_gate.md §2"},
        {"code": "register_certified_verified", "met": m.certified == ACC.State.VERIFIED, "ref": "release_gate.md §2"},
        {"code": "no_open_register_blockers", "met": not m.blockers, "ref": "release_gate.md §2.2"},
        {"code": "definitions_approved_by_decision",
         "met": all(k in defs and defs[k].approved for k in m.definitions), "ref": "release_gate.md §2.1"},
        {"code": "approval_refs", "met": bool(m.approval_refs), "ref": "validate_register"},
        {"code": "regression_tests", "met": bool(m.regression_tests), "ref": "release_gate.md §2.4"},
        {"code": "real_data_reconciliation_ref", "met": bool(m.reconciliation_ref), "ref": "release_gate.md §2.3"},
        {"code": "metric_source_dependencies_clear", "met": scoped_ready,
         "ref": "release_gate.md §8.3 step 2; D-069"},
        # Kredi needs its own independent reconciliation. Missing Kredi is not a prerequisite for Stock or Cari.
        *([{"code": "kredi_reconciliation_not_fail", "met": None,
            "ref": "release_gate.md §8.3 step 3"}] if "kredi" in m.sources else []),
        {"code": "app_refresh_and_restart_binding", "met": None, "ref": "release_gate.md §8.3 steps 4–5"},
        {"code": "generation_bound_api_evidence", "met": ACC.GENERATION_BOUND_API_EVIDENCE is True,
         "ref": "release_gate.md §8.3 step 6; validate_register"},
    ]


def render_tr(diag: Mapping[str, Any]) -> list[str]:
    """Console lines (Turkish labels, codes, fixed texts, states). No values, names or paths."""
    st = {"verified": "DOĞRULANDI", "not_verified": "DOĞRULANMADI", "invalid": "GEÇERSİZ"}
    g = diag["generation"]
    lines = ["", "METRİK TANILAMA (salt okunur; yukarıdaki GENEL SONUÇ'u ve çıkış kodunu değiştirmez)",
             f"  Üretim: {g['generation_id']} · beklenen: {g['expected']} · üretim kimliği eşleşiyor: "
             f"{'evet' if g['generation_id_matches'] else 'HAYIR'} · çekirdek kaynak kanıtı eksiksiz ve bağlı: "
             f"{'evet' if g['core_evidence_complete_and_bound'] else 'HAYIR'}"
             f" · bağlı kaynaklar: {', '.join(g['bound_sources']) or '-'}"
             + (f" · BAĞLANAMAYAN: {', '.join(g['unbound_sources'])}" if g['unbound_sources'] else "")
             + (f" · KANITI EKSİK: {', '.join(g['missing_evidence_sources'])}" if g['missing_evidence_sources'] else ""),
             f"  Katı (kaynak geneli) sonuç: {diag['strict']['overall']} · kaynaklar: "
             + ", ".join(f"{k}={v}" for k, v in sorted(diag['strict']['sources'].items())),
             f"  GENERATION_BOUND_API_EVIDENCE: {diag['generation_bound_api_evidence']}"]
    for key, m in diag["metrics"].items():
        lines.append("")
        retired = (f" [RETIRED — {m['retired_by']}: kaldırıldı, yayın adayı değil; yalnızca geçmiş tanılama]"
                   if m.get("retired_by") else "")
        lines.append(f"  ▸ {key} — {m['title']}{retired}: kapı kararı {st.get(m['gate_state'], m['gate_state'])}"
                     f" · sertifika izni: {'EVET' if m['certification_permitted'] else 'HAYIR'}"
                     + ("" if m.get("breakdown_matches_gate", True) else " · UYARI: döküm kapı kararıyla uyuşmuyor"))
        if m.get("unregistered"):
            continue
        for src, s in m["source_judgement"].items():
            lines.append(f"      kaynak {src}: katı={s['strict']} · bu metrik için değerlendirme={s['judgement']}"
                         + ("" if s["available"] else f" · KANIT YOK ({s['reason']})"))
            for p in s["problems"]:
                lines.append(f"        - [{st.get(p['state'], p['state'])}] {p['text']}  → "
                             + " → ".join(CATEGORY_TR[c] for c in p["categories"]))
            for p in s.get("strict_only_still_in_source", ()):
                how = ("bu metriğin okuduğu alan / eşleşmeler için yukarıda yeniden değerlendirildi"
                       if p["why"] == "rejudged_for_used_fields_and_joins" else "bu metriğe uygulanmıyor")
                lines.append(f"        · katı değerlendirmede, kaynakta HÂLÂ VAR (çözülmedi; {how}): {p['text']}")
        for b in m["open_blockers"]:
            lines.append(f"      engel {b['code']}: {b['text']}  → " + " → ".join(CATEGORY_TR[c] for c in b["categories"]))
        for dfn in m["unapproved_definitions"]:
            lines.append(f"      onaysız tanım {dfn['key']}: {dfn['text']}  → {CATEGORY_TR[BUSINESS]}")
        for dep in m["dependencies_withheld"]:
            lines.append(f"      bağımlılık {dep['key']}: {st.get(dep['state'], dep['state'])}")
        unmet = [p for p in m["release_prerequisites"] if p["met"] is not True]
        lines.append("      karşılanmayan yayın ön koşulları: "
                     + ", ".join(f"{p['code']}{'' if p['met'] is False else ' (bu komutla değerlendirilmedi)'}"
                                 for p in unmet))
    lines.append("")
    lines.append("  Bu tanılama hiçbir metriği doğrulamaz veya yayına hazır saymaz; yalnızca kanıt sunar.")
    return lines


__all__ = ["CANDIDATES", "RETIRED_BY", "diagnose", "render_tr", "strict_by_source", "categorize_text", "CATEGORY_TR"]
