"""BusinessActionAgent — tek koordineli ajan. agent_context + varlık bağlamı + (opsiyonel)
makro alır; AIProvider üzerinden YORUM/ÖNERİ üretir. Sayı HESAPLAMAZ, Excel OKUMAZ."""
import os, logging
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import schemas as S

log = logging.getLogger("agent")


class BusinessActionAgent:
    def __init__(self, provider, prompt_path=None):
        self.provider = provider
        path = prompt_path or os.path.join(ROOT, "prompts", "business_agent.txt")
        try:
            with open(path, encoding="utf-8") as f:
                self.system_prompt = f.read()
        except FileNotFoundError:
            self.system_prompt = "Türk toptan ticaret için analitik karar-destek asistanı."

    @property
    def available(self):
        return self.provider.is_available()

    # ---- enrich the deterministic daily actions with interpretation/recommendation ----
    def enrich_actions(self, actions):
        if not self.provider.is_available():
            return {"ai_available": False, "reason": getattr(self.provider, "reason", "unavailable"),
                    "by_id": {}}
        payload = {"actions": [{"entity_id": a["entity_id"], "title": a["title"],
                                "facts": a["facts"], "confidence": a["confidence"]} for a in actions]}
        res = self.provider.generate_structured(self.system_prompt, payload, S.AIActionsOut)
        if not res.available or res.error or not res.data:
            log.warning("enrich_actions failed: %s", res.error)
            return {"ai_available": True, "reason": res.error, "by_id": {}}
        by_id = {}
        for it in res.data["items"]:
            by_id[it["entity_id"]] = it
        return {"ai_available": True, "reason": None, "by_id": by_id}

    def analyze_customer(self, facts):
        if not self.provider.is_available():
            return None, getattr(self.provider, "reason", "unavailable")
        res = self.provider.generate_structured(self.system_prompt, facts, S.CustomerAnalysisAI)
        if not res.available or res.error or not res.data:
            return None, (res.error or "unavailable")
        return res.data, None

    def analyze_product(self, facts):
        if not self.provider.is_available():
            return None, getattr(self.provider, "reason", "unavailable")
        res = self.provider.generate_structured(self.system_prompt, facts, S.ProductAnalysisAI)
        if not res.available or res.error or not res.data:
            return None, (res.error or "unavailable")
        return res.data, None

    def draft_message(self, message_type, customer_facts, product_facts=None, instruction=None):
        if not self.provider.is_available():
            return None, getattr(self.provider, "reason", "unavailable")
        payload = {"message_type": message_type, "customer": customer_facts,
                   "product": product_facts, "instruction": instruction,
                   "rules": "Müşteriye içsel analizi ifşa etme. Yalnızca taslak."}
        res = self.provider.generate_structured(self.system_prompt, payload, S.MessageDraftAI)
        if not res.available or res.error or not res.data:
            return None, (res.error or "unavailable")
        return res.data, None
