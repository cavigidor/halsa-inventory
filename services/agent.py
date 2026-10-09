"""BusinessActionAgent — tek koordineli ajan.

Pipeline for every AI task (provider-agnostic):
    deterministic entity data
      -> services.evidence: small evidence packet (facts with ids, pre-formatted by Python)
      -> validate_payload (data minimization)
      -> provider.generate_structured (Pydantic schema)
      -> grounding.check: fact ids valid, no model-authored numbers
           (on violation: at most ONE stricter retry, then reject)
      -> grounding.resolve: [[FACT:F001]] -> exact Python display value
The agent never reads Excel, never calculates a number and never decides
customer identity, tiering, KAPALI status or receivable aging.
"""
import logging
import os

import config as C
import schemas as S
from services import evidence as EV
from services.ai import grounding as G
from services.ai import quality as Q
from services.ai.base import AIResult, ERR_PACKET, ERR_UNKNOWN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = logging.getLogger("agent")

STRICT_RETRY = (
    "\n\nÖNCEKİ CEVAP REDDEDİLDİ ({why}). Sorunlu alanlar ve ifadeler: {details}\n"
    "Bu ifadeleri KALDIR. Metinde HİÇBİR rakam, tutar, yüzde, yıl dışı sayı veya sayısal ifade yazma; "
    "adımları numaralandırma. Sayı gerekiyorsa yalnızca paketteki bir kimliği [[FACT:F001]] biçiminde "
    "kullan. Yalnızca paketteki fact_id'leri kullan. Teknik etiketleri (ör. flags) Türkçe açıkla."
)


def _default_recorder(entry):
    try:
        from services import store as ST
        ST.record_ai_call(**entry)
    except Exception as e:  # observability must never break the request
        log.debug("ai call not recorded: %s", type(e).__name__)


class BusinessActionAgent:
    def __init__(self, provider, prompt_path=None, recorder=None):
        self.provider = provider
        self.recorder = recorder or _default_recorder
        path = prompt_path or os.path.join(ROOT, "prompts", "business_agent.txt")
        try:
            with open(path, encoding="utf-8") as f:
                self.system_prompt = f.read()
        except FileNotFoundError:
            self.system_prompt = "Türk toptan ticaret için analitik karar-destek asistanı."

    @property
    def available(self):
        return self.provider.is_available()

    def _unavailable(self):
        return {"ok": False, "data": None, "packet": None,
                "reason": getattr(self.provider, "reason", "AI unavailable"),
                "category": getattr(self.provider, "category", "disabled"), "warnings": []}

    # ---------------- core grounded call ----------------
    def run(self, task, packet, response_model):
        """Returns dict(ok, data(resolved), packet, reason, category, warnings)."""
        if not self.provider.is_available():
            return self._unavailable()
        payload = packet.to_payload()
        try:
            EV.validate_payload(payload)
        except EV.PacketRejected as e:
            log.error("evidence packet rejected for %s: %s", task, e)
            return {"ok": False, "data": None, "packet": packet, "reason": f"packet_rejected: {e}",
                    "category": ERR_PACKET, "warnings": []}

        prompt, last_err = self.system_prompt, None
        attempts = 1 + max(0, min(1, C.NUMERIC_GUARD_RETRIES))   # hard cap: one retry
        for attempt in range(1, attempts + 1):
            try:
                res = self.provider.generate_structured(prompt, payload, response_model)
            except Exception as e:   # a provider must not raise; if one does, contain it
                log.error("provider raised %s in %s", type(e).__name__, task)
                res = AIResult(available=True, error=f"provider exception: {type(e).__name__}",
                               error_category=ERR_UNKNOWN)
            entry = {"task": task, "attempt": attempt, **_meta(res, self.provider)}
            if not res.available or res.error or not res.data:
                entry.update(success=False, error_category=res.error_category or "unknown")
                self.recorder(entry)
                # transport / provider errors are not retried here (SDK already did bounded retries)
                return {"ok": False, "data": None, "packet": packet, "warnings": [],
                        "reason": res.error or "unavailable", "category": res.error_category or "unknown"}
            try:
                response_model.model_validate(res.data)
                resolved = G.check_and_resolve(res.data, packet)
            except G.GroundingError as g:
                last_err = g
                entry.update(success=False, error_category=g.category)
                self.recorder(entry)
                log.warning("grounding violation (%s) task=%s attempt=%d", g.category, task, attempt)
                details = "; ".join(g.details[:6]) if g.details else str(g)[:300]
                prompt = self.system_prompt + STRICT_RETRY.format(why=g.category, details=details[:600])
                continue
            except Exception as e:  # schema invalid
                entry.update(success=False, error_category="malformed")
                self.recorder(entry)
                return {"ok": False, "data": None, "packet": packet, "warnings": [],
                        "reason": f"malformed: {type(e).__name__}", "category": "malformed"}
            entry.update(success=True, error_category=None)
            self.recorder(entry)
            quality = Q.assess(res.data)["issues"]          # writing quality: measured, never enforced
            if quality:
                log.info("ai output quality issues task=%s: %s", task, "; ".join(quality[:5]))
            return {"ok": True, "data": resolved, "packet": packet, "reason": None, "category": None,
                    "warnings": list(resolved.get("warnings", []) or []), "raw_ids": G.referenced_ids(res.data),
                    "quality_issues": quality}
        return {"ok": False, "data": None, "packet": packet, "warnings": [],
                "reason": f"{last_err.category}: {last_err}", "category": last_err.category}

    # ---------------- tasks ----------------
    def enrich_actions(self, actions, ctx=None):
        """Returns ai_available, reason, category, by_ref{A01: {...resolved, evidence}}, refs[]."""
        refs = [f"A{i:02d}" for i in range(1, len(actions) + 1)]
        if not self.provider.is_available():
            u = self._unavailable()
            return {"ai_available": False, "reason": u["reason"], "category": u["category"],
                    "by_ref": {}, "refs": refs, "warnings": []}
        if not actions:
            return {"ai_available": True, "reason": None, "category": None, "by_ref": {}, "refs": refs,
                    "warnings": []}
        packet = EV.actions_packet(actions, ctx)
        r = self.run("daily_actions", packet, S.AIActionsOut)
        if not r["ok"]:
            return {"ai_available": True, "reason": r["reason"], "category": r["category"],
                    "by_ref": {}, "refs": refs, "warnings": []}
        valid_refs = {it["action_ref"] for it in packet.items}
        by_ref = {}
        for it in r["data"]["items"]:
            if it["action_ref"] in valid_refs and it["action_ref"] not in by_ref:
                it = dict(it)
                it["evidence"] = G.evidence_for(it.get("evidence_fact_ids"), packet)
                by_ref[it["action_ref"]] = it
        return {"ai_available": True, "reason": None, "category": None, "by_ref": by_ref,
                "refs": refs, "warnings": r["warnings"]}

    def analyze_customer(self, facts, ctx=None, memory=None):
        packet = EV.customer_packet(facts, ctx, memory if memory is not None else facts.get("history"))
        r = self.run("customer_analysis", packet, S.CustomerAnalysisAI)
        if not r["ok"]:
            return None, r
        data = dict(r["data"])
        if facts.get("closed"):          # Python enforces KAPALI: no opportunities, whatever the model says
            if data.get("opportunities"):
                data["opportunities"] = []
                data["warnings"] = list(data.get("warnings", [])) + [
                    "KAPALI müşteri: fırsat önerileri sistem tarafından kaldırıldı."]
        data["evidence"] = G.evidence_for(r.get("raw_ids"), packet)
        return data, r

    def analyze_product(self, facts, ctx=None):
        packet = EV.product_packet(facts, ctx)
        r = self.run("product_analysis", packet, S.ProductAnalysisAI)
        if not r["ok"]:
            return None, r
        data = dict(r["data"])
        data["best_buyers"] = [packet.by_id[i]["display_value"] for i in data.get("best_buyer_fact_ids", [])]
        data["evidence"] = G.evidence_for(r.get("raw_ids"), packet)
        return data, r

    def draft_message(self, message_type, customer_facts, product_facts=None, instruction=None, ctx=None):
        if customer_facts.get("closed") and message_type in ("winback", "sales_offer"):
            return None, {"ok": False, "reason": "KAPALI müşteri: satış/geri kazanım mesajı üretilmez.",
                          "category": "kapali_customer"}
        packet = EV.draft_packet(message_type, customer_facts, product_facts, instruction, ctx,
                                 customer_facts.get("history"))
        r = self.run("message_draft", packet, S.MessageDraftAI)
        if not r["ok"]:
            return None, r
        data = dict(r["data"])
        data["evidence"] = G.evidence_for(r.get("raw_ids"), packet)
        return data, r


def _meta(res, provider):
    m = dict(res.meta or {})
    m.setdefault("provider", getattr(provider, "name", "?"))
    m.setdefault("model", getattr(provider, "model", None))
    return {k: m.get(k) for k in ("provider", "model", "latency_ms", "input_tokens", "output_tokens",
                                  "reasoning_tokens")}
