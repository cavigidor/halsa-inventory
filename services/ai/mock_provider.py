"""Deterministik sahte sağlayıcı — testler ve API-key olmadan geliştirme için.
Ağ/kredi gerektirmez, şema-geçerli çıktı üretir."""
import json
from .base import AIProvider, AIResult


class MockProvider(AIProvider):
    name = "mock"

    def is_available(self) -> bool:
        return True

    def generate_structured(self, system_prompt, payload, response_model) -> AIResult:
        mn = response_model.__name__
        if mn == "AIActionsOut":
            items = [{"entity_id": a["entity_id"],
                      "interpretation": f"[mock] {a['title']} — gerçeklere dayalı yorum.",
                      "recommendation": "[mock] İlgili temsilci ile takip önerilir.",
                      "confidence": a.get("confidence", "medium")}
                     for a in payload.get("actions", [])]
            data = {"items": items}
        elif mn == "CustomerAnalysisAI":
            data = {"summary": "[mock] Müşteri özeti.", "risks": ["[mock] risk"],
                    "opportunities": ["[mock] fırsat"], "recommended_action": "[mock] aksiyon",
                    "confidence": "medium"}
        elif mn == "ProductAnalysisAI":
            data = {"status": "[mock] stok durumu", "risk": "orta", "opportunity": "yüksek",
                    "best_buyers": [b.get("name", "") for b in payload.get("top_buyers", [])[:3]],
                    "recommendation": "[mock] öneri", "confidence": "medium"}
        elif mn == "MessageDraftAI":
            data = {"message": "[mock] Taslak mesaj (gönderilmedi).", "warnings": []}
        else:
            return AIResult(available=True, error=f"unknown_model:{mn}")
        try:
            response_model(**data)
            return AIResult(available=True, data=data, raw=json.dumps(data, ensure_ascii=False))
        except Exception as e:
            return AIResult(available=True, error=f"validation:{e}")
