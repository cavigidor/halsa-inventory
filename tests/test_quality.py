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
    "summary": ("Üç yıl düzenli alım yapmış, değerli bir müşteri ([[FACT:F004]]) 2026'da hiç sipariş vermedi. "
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
    assert any("back-to-back" in i for i in r["quality_issues"])
