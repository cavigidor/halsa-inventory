"""Deterministik sahte sağlayıcı — testler ve API anahtarı olmadan geliştirme için.
Ağ/kredi gerektirmez. Kanıt paketinden şema-geçerli, [[FACT:..]] referanslı çıktı üretir
(sayı YAZMAZ), böylece gerçek sağlayıcıyla aynı doğrulama/çözümleme hattından geçer."""
import json
from .base import AIProvider, AIResult, ERR_MALFORMED


def _ref(fid):
    return f"[[FACT:{fid}]]" if fid else ""


class MockProvider(AIProvider):
    name = "mock"
    model = "mock-1"

    def is_available(self) -> bool:
        return True

    def generate_structured(self, system_prompt, payload, response_model) -> AIResult:
        mn = response_model.__name__
        facts = payload.get("facts", [])
        first = facts[0]["fact_id"] if facts else None
        if mn == "AIActionsOut":
            items = []
            for it in payload.get("items", []):
                fids = it.get("fact_ids", [])
                lead = fids[0] if fids else None
                items.append({"action_ref": it["action_ref"],
                              "interpretation": f"[mock] Öne çıkan gerçek: {_ref(lead)}".strip(),
                              "recommendation": "[mock] İlgili temsilci ile takip önerilir.",
                              "evidence_fact_ids": fids[:2],
                              "confidence": it.get("confidence") or "medium"})
            data = {"items": items, "warnings": []}
        elif mn == "CustomerAnalysisAI":
            data = {"summary": f"[mock] Müşteri özeti. {_ref(first)}".strip(), "risks": ["[mock] risk"],
                    "opportunities": ["[mock] fırsat"], "recommended_action": "[mock] aksiyon",
                    "evidence_fact_ids": [first] if first else [], "warnings": [], "confidence": "medium"}
        elif mn == "ProductAnalysisAI":
            buyers = [f["fact_id"] for f in facts if f["label"].startswith("Aday alıcı")
                      and "—" not in f["label"]][:3]
            data = {"status": "[mock] stok durumu", "risk": "orta", "opportunity": "yüksek",
                    "best_buyer_fact_ids": buyers, "recommendation": "[mock] öneri",
                    "evidence_fact_ids": [first] if first else [], "warnings": [], "confidence": "medium"}
        elif mn == "MessageDraftAI":
            data = {"message": "[mock] Taslak mesaj (gönderilmedi).", "evidence_fact_ids": [], "warnings": []}
        else:
            return AIResult(available=True, error=f"unknown_model:{mn}", error_category=ERR_MALFORMED)
        try:
            response_model.model_validate(data)
            return AIResult(available=True, data=data, raw=json.dumps(data, ensure_ascii=False),
                            meta={"provider": self.name, "model": self.model, "latency_ms": 0})
        except Exception as e:
            return AIResult(available=True, error=f"validation:{e}", error_category=ERR_MALFORMED)
