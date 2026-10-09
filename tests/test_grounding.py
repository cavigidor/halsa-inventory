"""Numeric safety: fact references resolve to exact Python values; model numbers are rejected."""
import pytest

import schemas as S
from services import evidence as EV
from services.agent import BusinessActionAgent
from services.ai import grounding as G
from services.ai.base import AIProvider, AIResult


def packet():
    p = EV.EvidencePacket("customer_analysis", as_of="2026-06-30",
                          entity={"type": "customer", "id": "900.00.0001", "name": "Sentetik T1 Kirtasiye"})
    p.fact("Toplam geçmiş harcama", 184230, "money")       # F001 -> ₺184.230
    p.fact("Güçlü yıl sayısı", 3, "count")                  # F002 -> 3
    p.fact("Geri kazanım kademesi", "T1", "text")           # F003
    p.fact("Son aktif yıl", 2025, "year")                   # F004
    p.fact("Nominal değişim", -60.0, "percent")             # F005 -> %-60,0
    return p


def ok(**over):
    d = {"summary": "Yüksek değerli geri kazanım fırsatı. Geçmiş harcama [[FACT:F001]].",
         "risks": ["2025'ten beri sipariş yok."], "opportunities": ["Kademe [[FACT:F003]] müşteri."],
         "recommended_action": "Temsilci araması önerilir.", "evidence_fact_ids": ["F001", "F003"],
         "warnings": [], "confidence": "high"}
    d.update(over)
    return d


def test_display_formatting_is_deterministic():
    assert EV.fmt_money(184230) == "₺184.230"
    assert EV.fmt_money(-1500.4) == "-₺1.500"
    assert EV.fmt_pct(-60.0) == "%-60,0"
    assert EV.fmt_int(3000) == "3.000"


def test_placeholder_resolves_to_exact_python_value():
    out = G.check_and_resolve(ok(), packet())
    assert out["summary"].endswith("Geçmiş harcama ₺184.230.")
    assert out["opportunities"] == ["Kademe T1 müşteri."]
    assert out["evidence_fact_ids"] == ["F001", "F003"]        # ids untouched


def test_unknown_fact_id_in_text_fails():
    with pytest.raises(G.GroundingError) as e:
        G.check(ok(summary="Harcama [[FACT:F099]]."), packet())
    assert e.value.category == "fact_reference"


def test_unknown_fact_id_in_evidence_list_fails():
    with pytest.raises(G.GroundingError) as e:
        G.check(ok(evidence_fact_ids=["F001", "F777"]), packet())
    assert e.value.category == "fact_reference"


@pytest.mark.parametrize("bad", ["Harcama [[FACT:F001]", "Harcama [[FACT: F001]].", "Harcama [[F001]].",
                                 "Harcama FACT:F001."])
def test_malformed_or_unresolved_placeholder_fails(bad):
    with pytest.raises(G.GroundingError) as e:
        G.check(ok(summary=bad), packet())
    assert e.value.category == "fact_reference"


@pytest.mark.parametrize("claim", [
    "Geçmiş harcama ₺184.230.",           # copied the number instead of referencing it
    "Geçmiş harcama 190.000 TL.",          # invented amount
    "Satışlar %40 düştü.",                 # invented percentage
    "Büyüme -55% oldu.",                   # altered growth
    "Vadesi geçen bakiye 12,5 bin.",       # altered receivable
    "Marj yaklaşık 18 puan.",              # changed margin
    "Stokta 3000 adet var.",               # changed stock amount
    "Satışlar yüzde kırk azaldı.",         # number word
    "Ciro yarısına indi.",                 # ratio claim without digits
    "Harcama 2,5 milyon.",
])
def test_numeric_guard_catches_model_authored_numbers(claim):
    with pytest.raises(G.GroundingError) as e:
        G.check(ok(summary=claim), packet())
    assert e.value.category == "numeric_guard"


def test_numeric_guard_allows_packet_years_and_names():
    p = packet()
    p.allow("3M Sentetik Bayi")
    G.check(ok(summary="2025'ten beri 3M Sentetik Bayi üzerinden sipariş yok; harcama [[FACT:F001]]."), p)


def test_numeric_guard_rejects_year_not_in_packet():
    with pytest.raises(G.GroundingError):
        G.check(ok(summary="2019'dan beri müşteri."), packet())


def test_ai_cannot_change_deterministic_facts():
    p = packet()
    before = [dict(f) for f in p.facts]
    G.check_and_resolve(ok(), p)
    assert [dict(f) for f in p.facts] == before


# ---------------- agent pipeline: retry is bounded, numbers never accepted ----------------
class Scripted(AIProvider):
    name, model = "scripted", "scripted-1"

    def __init__(self, outputs):
        self.outputs, self.calls, self.prompts, self.payloads = list(outputs), 0, [], []

    def is_available(self):
        return True

    def generate_structured(self, system_prompt, payload, response_model):
        self.calls += 1
        self.prompts.append(system_prompt)
        self.payloads.append(payload)
        out = self.outputs.pop(0) if self.outputs else self.last
        self.last = out
        if isinstance(out, AIResult):
            return out
        return AIResult(available=True, data=out, meta={"provider": self.name, "model": self.model})


CUST = {"customer_code": "900.00.0001", "name": "Sentetik T1 Kirtasiye", "winback_tier": 1,
        "historical_spend": 184230, "last_active_year": 2025, "overdue": 0, "rep": "TEMSILCI-A"}


def _ids(prov_payload):
    return {f["label"]: f["fact_id"] for f in prov_payload["facts"]}


def test_numeric_violation_retries_once_then_succeeds():
    bad = ok(summary="Harcama 184 bin TL.", evidence_fact_ids=[], opportunities=[])
    good = ok(summary="Harcama yüksek.", evidence_fact_ids=[], opportunities=[])
    prov = Scripted([bad, good])
    data, r = BusinessActionAgent(prov).analyze_customer(CUST)
    assert prov.calls == 2 and data is not None
    assert "ÖNCEKİ CEVAP REDDEDİLDİ" in prov.prompts[1]


def test_numeric_violation_twice_is_rejected_never_accepted():
    bad = ok(summary="Harcama 184 bin TL.", evidence_fact_ids=[], opportunities=[])
    prov = Scripted([bad, bad, bad])
    data, r = BusinessActionAgent(prov).analyze_customer(CUST)
    assert data is None and r["category"] == "numeric_guard"
    assert prov.calls == 2                    # exactly one retry, never a loop


def test_resolved_number_in_final_output_comes_from_python():
    prov = Scripted([])
    agent = BusinessActionAgent(prov)
    # first call to learn the fact ids the packet assigns
    pkt = EV.customer_packet(CUST)
    fid = next(f["fact_id"] for f in pkt.facts if f["label"] == "Toplam geçmiş harcama")
    prov.outputs = [ok(summary=f"Geçmiş harcama [[FACT:{fid}]].", evidence_fact_ids=[fid], opportunities=[])]
    data, r = agent.analyze_customer(CUST)
    assert data["summary"] == "Geçmiş harcama ₺184.230."
    assert data["evidence"][0]["display_value"] == "₺184.230"


def test_malformed_structured_response_fails_safely():
    prov = Scripted([{"summary": "eksik alanlar"}])
    data, r = BusinessActionAgent(prov).analyze_customer(CUST)
    assert data is None and r["category"] == "malformed"


def test_kapali_opportunities_removed_by_python():
    cust = {**CUST, "closed": True}
    prov = Scripted([ok(summary="Kapalı hesap.", opportunities=["Yeni ürün öner."], evidence_fact_ids=[])])
    data, _ = BusinessActionAgent(prov).analyze_customer(cust)
    assert data["opportunities"] == []
    assert any("KAPALI" in w for w in data["warnings"])


def test_kapali_winback_draft_refused_without_calling_model():
    prov = Scripted([])
    data, r = BusinessActionAgent(prov).draft_message("winback", {**CUST, "closed": True})
    assert data is None and r["category"] == "kapali_customer" and prov.calls == 0


def test_product_best_buyers_must_be_buyer_facts():
    prod = {"product_code": "SP-002", "name": "Sentetik Defter", "status": "overstock", "stock": 1000,
            "inventory_value": 250000, "historical_buyer_count": 2,
            "ranked_buyers": [{"name": "Sentetik Aktif", "spend": 90000, "last": 2026}]}
    pkt = EV.product_packet(prod)
    buyer = next(f["fact_id"] for f in pkt.facts if f["kind"] == "buyer")
    stock = next(f["fact_id"] for f in pkt.facts if f["label"] == "Stok miktarı")
    base = {"status": "Fazla stok.", "risk": "Orta.", "opportunity": "Var.", "recommendation": "Ara.",
            "evidence_fact_ids": [], "warnings": [], "confidence": "medium"}
    data, _ = BusinessActionAgent(Scripted([{**base, "best_buyer_fact_ids": [buyer]}])).analyze_product(prod)
    assert data["best_buyers"] == ["Sentetik Aktif"]
    data, r = BusinessActionAgent(Scripted([{**base, "best_buyer_fact_ids": [stock]}] * 2)).analyze_product(prod)
    assert data is None and r["category"] == "fact_reference"
