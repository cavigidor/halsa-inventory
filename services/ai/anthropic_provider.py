"""Anthropic adaptörü (Milestone 2; not the Milestone 3 target provider, kept working).
SDK yalnızca çağrı anında import edilir; modül import'u SDK gerektirmez."""
import os, json, re, time
from .base import (AIProvider, AIResult, ERR_MALFORMED, ERR_MISSING_KEY, ERR_SDK_MISSING,
                   ERR_UNKNOWN)


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text).rsplit("```", 1)[0].strip()
    i, j = text.find("{"), text.rfind("}")
    return text[i:j+1] if i >= 0 and j > i else text


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, model=None):
        import config as C
        s = C.ai_settings()
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or C.DEFAULT_MODELS["anthropic"]
        self.timeout = s["timeout_seconds"]
        self.max_output_tokens = s["max_output_tokens"]
        self._client = None

    def unavailable_reason(self):
        if not self.key:
            return ERR_MISSING_KEY
        try:
            import anthropic  # noqa: F401
        except Exception:
            return ERR_SDK_MISSING
        return None

    def is_available(self) -> bool:
        return self.unavailable_reason() is None

    def _client_(self):
        import anthropic
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.key, timeout=float(self.timeout), max_retries=1)
        return self._client

    def generate_structured(self, system_prompt, payload, response_model) -> AIResult:
        meta = {"provider": self.name, "model": self.model}
        why = self.unavailable_reason()
        if why:
            return AIResult(available=False, error="Anthropic API key/SDK not available.",
                            error_category=why, meta=meta)
        try:
            schema = response_model.model_json_schema()
        except Exception:
            schema = {}
        base = ("SADECE aşağıdaki JSON şemasına uyan geçerli JSON döndür; açıklama/markdown YOK.\n" +
                json.dumps(schema, ensure_ascii=False) +
                "\n\nKANIT PAKETİ (Python tarafından doğrulanmış gerçekler):\n" +
                json.dumps(payload, ensure_ascii=False))
        instr, last = base, ""
        t0 = time.monotonic()
        for _ in range(2):   # bounded: one retry for malformed JSON only
            try:
                msg = self._client_().messages.create(
                    model=self.model, max_tokens=self.max_output_tokens, system=system_prompt,
                    messages=[{"role": "user", "content": instr}])
                text = "".join(getattr(b, "text", "") for b in msg.content)
                data = json.loads(_extract_json(text))
                response_model.model_validate(data)
                meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
                return AIResult(available=True, data=data, raw=text, meta=meta)
            except Exception as e:
                last = type(e).__name__
                instr = base + "\n\nÖNCEKİ CEVAP GEÇERSİZDİ. Yalnızca geçerli JSON döndür."
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        cat = ERR_MALFORMED if last in ("JSONDecodeError", "ValidationError") else ERR_UNKNOWN
        return AIResult(available=True, error=f"validation_failed:{last}", error_category=cat, meta=meta)
