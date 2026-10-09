"""Q-1 writing quality: measured deterministically (never enforced, never touches numbers)."""
import schemas as S
from services.agent import BusinessActionAgent
from services.ai import quality as Q
from services.ai.base import AIProvider, AIResult

# Shape of the first passing live output (2026-10-09), before Q-1: it listed facts back-to-back.
LIVE_BEFORE_Q1 = {
    "summary": ("Müşteri [[FACT:F001]], temsilci [[FACT:F002]] altında kademesi [[FACT:F003]]; "
                "son aktif yıl [[FACT:F005]] ve vadesi geçen bakiye [[FACT:F012]]. Geçmiş harcama "
                "[[FACT:F004]]. 2023–2025 kayıtları: [[FACT:F006]], [[FACT:F007]], [[FACT:F008]], "
                "[[FACT:F009]], [[FACT:F010]], [[FACT:F011]]. Paket, bazı verilerin eksik olduğunu belirtiyor."),
    "risks": ["Uzun süredir sipariş yok."], "opportunities": ["Kademe 1 müşteri."],
    "recommended_action": "Temsilci arasın.", "evidence_fact_ids": [], "warnings": [], "confidence": "high"}

GOOD = {
    "summary": ("Üç yıl düzenli alım yapmış ve geçmiş harcaması [[FACT:F004]] olan değerli bir müşteri 2026'da hiç sipariş vermedi. "
                "Borcu olmaması geri kazanımı kolaylaştırıyor; erken temas önemli."),
    "risks": ["Rakibe kaymış olabilir."], "opportunities": ["Geçmiş ürün grubunda teklif fırsatı."],
    "recommended_action": "Temsilci bu hafta arayıp ihtiyaçlarını sorsun.",
    "evidence_fact_ids": ["F004", "F006"], "warnings": [], "confidence": "high"}


def test_live_output_before_q1_is_flagged():
    issues = Q.assess(LIVE_BEFORE_Q1)["issues"]
    assert any("back-to-back" in i for i in issues)
    assert any("fact refs" in i for i in issues)
    assert any("technical wording" in i for i in issues)


def test_interpretive_output_passes():
    assert Q.assess(GOOD)["issues"] == []


def test_list_limits():
    bad = {**GOOD, "risks": ["a", "b", "c", "d"], "opportunities": ["[[FACT:F001]] ve [[FACT:F002]]"]}
    issues = Q.assess(bad)["issues"]
    assert any(i.startswith("risks: 4 items") for i in issues)
    assert any(i.startswith("opportunities: an item has 2") for i in issues)


def test_actions_items_are_assessed():
    out = {"items": [{"action_ref": "A01", "interpretation": "[[FACT:F001]], [[FACT:F002]], [[FACT:F003]]",
                      "recommendation": "Ara.", "evidence_fact_ids": [], "confidence": "high"}], "warnings": []}
    assert any(i.startswith("items[0].interpretation") for i in Q.assess(out)["issues"])


def test_guidance_reaches_the_model_via_strict_schema():
    from openai.lib._parsing._responses import type_to_text_format_param
    props = type_to_text_format_param(S.CustomerAnalysisAI)["schema"]["properties"]
    assert "YORUM" in props["summary"]["description"] and "en fazla 4" in props["summary"]["description"]
    assert "numaralandırma yok" in props["recommended_action"]["description"]
    item = type_to_text_format_param(S.AIActionsOut)["schema"]
    assert "description" in str(item)


def test_prompt_contains_writing_rules():
    import os
    text = open(os.path.join(os.path.dirname(S.__file__), "prompts", "business_agent.txt"), encoding="utf-8").read()
    assert "YAZIM KALİTESİ" in text and "SIRALAMAZ" in text and "art arda dizme" in text


class _One(AIProvider):
    name, model = "one", "one-1"

    def __init__(self, out):
        self.out = out

    def is_available(self):
        return True

    def generate_structured(self, *a, **k):
        return AIResult(available=True, data=self.out)


def test_quality_issues_are_reported_but_never_reject():
    cust = {"customer_code": "900.00.0001", "name": "Sentetik", "winback_tier": 1, "historical_spend": 1000,
            "last_active_year": 2025, "overdue": 0}
    from services import evidence as EV
    ids = [f["fact_id"] for f in EV.customer_packet(cust).facts]
    run = ", ".join(f"[[FACT:{i}]]" for i in ids[:3])
    out = {**GOOD, "summary": f"Kayıtlar: {run}.", "evidence_fact_ids": []}
    agent = BusinessActionAgent(_One(out))
    data, r = agent.analyze_customer(cust)
    assert data is not None and r["ok"]                     # accepted (quality is not safety)
    assert any("back-to-back" in i for i in r["model_quality_issues"])
    assert r["tidy_fixes"] and r["quality_issues"] == []    # Python tidied the fact run away


# ---------------- second live run (2026-10-09): passed old metrics but read badly ----------------
def _smoke_packet():
    from services import evidence as EV
    return EV.customer_packet({"customer_code": "900.00.0001", "name": "Sentetik T1 Kirtasiye",
                               "rep": "TEMSILCI-A", "winback_tier": 1, "historical_spend": 184230,
                               "last_active_year": 2025, "overdue": 0,
                               "years": [{"y": 2023, "spend": 61000, "orders": 4}]})


def _fid(p, label):
    return next(f["fact_id"] for f in p.facts if f["label"] == label)


def test_lapsed_customer_gets_no_bogus_missing_fields():
    p = _smoke_packet()
    for label in ("En eski borç dönemi", "2025–2026 ciro", "Borç / ciro", "Tahsilat öncelik puanı (0–100)"):
        assert label not in p.missing
    assert "Sektör" in p.missing          # genuinely empty -> still reported


def test_second_live_run_defects_are_flagged():
    p = _smoke_packet()
    rep, tier, y23 = _fid(p, "Temsilci"), _fid(p, "Geri kazanım kademesi"), _fid(p, "2023 ciro")
    out = {**GOOD,
           "summary": f"Değerli müşteri. Temsilci TEMSILCI-A sorumludur [[FACT:{rep}]].",
           "opportunities": [f"Geri kazanımda önceliğe uygundur; [[FACT:{tier}]]",
                             f"Tekrar sipariş tetiklenebilir [[FACT:{y23}]]"],
           "recommended_action": "Temsilci arasın; eksik kayıtlar (Sektör, Temsilci) tamamlansın."}
    issues = Q.assess(out, p)["issues"]
    assert any("opportunities[0]: fact reference tacked" in i for i in issues)
    assert any("opportunities: 1 inline fact ref(s) moved" in i for i in issues)
    assert any("'TEMSILCI-A' written and referenced twice" in i for i in issues)
    p.missing.append("Temsilci")
    assert any("lists missing fields" in i for i in Q.assess(out, p)["issues"])


def test_reference_inside_sentence_is_fine():
    p = _smoke_packet()
    spend = _fid(p, "Toplam geçmiş harcama")
    out = {**GOOD, "summary": f"Geçmiş harcaması [[FACT:{spend}]] olan değerli müşteri uzun süredir alım yapmıyor."}
    assert Q.assess(out, p)["issues"] == []



# ---------------- tidy (Q-1 iteration 3): built from live run 2's exact defects ----------------
def _run2_raw(p):
    f = {lbl: _fid(p, lbl) for lbl in ("Temsilci", "Toplam geçmiş harcama", "2023 ciro", "2023 sipariş sayısı",
                                         "Vadesi geçen bakiye")}
    return {
        "summary": (f"Müşteri düzenli alıcı geçmişine sahip; geçmiş toplam harcama kaydı [[FACT:{f['Toplam geçmiş harcama']}]] "
                    f"bunu destekliyor. Son yılda işlem azalması gözleniyor, bu nedenle hızlı temas önemli "
                    f"([[FACT:{f['2023 sipariş sayısı']}]], [[FACT:{f['2023 ciro']}]]). Temsilci ataması TEMSILCI-A "
                    f"olup bu kişiye öncelik verin [[FACT:{f['Temsilci']}]]."),
        "risks": ["2026'da henüz alım yok; yeniden aktifleşme riski mevcut (lapsed_no_2026_purchase).",
                  f"Sipariş sayısında azalma gözleniyor, satış kaybı devam edebilir [[FACT:{f['2023 sipariş sayısı']}]]."],
        "opportunities": [f"Geçmişte yüksek toplam harcaması fırsat sunuyor [[FACT:{f['Toplam geçmiş harcama']}]].",
                          f"TEMSILCI-A doğrudan temasla hızlı geri kazanım başlatabilir [[FACT:{f['Temsilci']}]]."],
        "recommended_action": (f"TEMSILCI-A bu hafta müşteriyi arasın; vadesi geçen bakiye olmadığını not etsin "
                               f"[[FACT:{f['Vadesi geçen bakiye']}]]."),
        "evidence_fact_ids": [], "warnings": [], "confidence": "high"}


def test_tidy_fixes_every_run2_defect_and_quality_is_clean_after():
    p = _smoke_packet()
    raw = _run2_raw(p)
    assert len(Q.assess(raw, p)["issues"]) >= 5
    tidied, fixes = Q.tidy(raw, p)
    assert Q.assess(tidied, p)["issues"] == [], Q.assess(tidied, p)["issues"]
    assert "[[FACT:" not in " ".join(tidied["risks"] + tidied["opportunities"] + [tidied["recommended_action"]])
    assert "lapsed_no_2026_purchase" not in " ".join(tidied["risks"])
    assert tidied["summary"].endswith("bu kişiye öncelik verin.")
    assert "kaydı [[FACT:" in tidied["summary"]                     # integrated reference kept
    assert "(" not in tidied["summary"]                              # citation group removed
    assert set(tidied["evidence_fact_ids"]) >= {_fid(p, "Toplam geçmiş harcama"), _fid(p, "2023 ciro")}
    for item in tidied["risks"] + tidied["opportunities"]:
        assert item.endswith(".") and "  " not in item and " ." not in item


def test_tidy_never_adds_numbers_and_output_still_grounded():
    import re
    from services.ai import grounding as G
    p = _smoke_packet()
    raw = _run2_raw(p)
    G.check(raw, p)
    tidied, _ = Q.tidy(raw, p)
    G.check(tidied, p)
    digits = lambda d: len(re.findall(r"\d", G.FACT_TOKEN.sub("", str({k: v for k, v in d.items()
                                                                       if k not in ("evidence_fact_ids",)}))))
    assert digits(tidied) <= digits(raw)


def test_tidy_keeps_label_value_and_mid_sentence_references():
    p = _smoke_packet()
    spend = _fid(p, "Toplam geçmiş harcama")
    raw = {**GOOD, "summary": f"Toplam geçmiş harcama: [[FACT:{spend}]]. Harcaması [[FACT:{spend}]] olan müşteri değerli."}
    tidied, fixes = Q.tidy(raw, p)
    assert tidied["summary"] == raw["summary"] and fixes == []
