"""Anthropic adaptörü. SDK yalnızca çağrı anında import edilir; modül import'u SDK gerektirmez."""
import os, json, re
from .base import AIProvider, AIResult


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text).rsplit("```", 1)[0].strip()
    i, j = text.find("{"), text.rfind("}")
    return text[i:j+1] if i >= 0 and j > i else text


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, model=None):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("AI_MODEL", "claude-3-5-sonnet-20241022")
        self._client = None

    def is_available(self) -> bool:
        if not self.key:
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except Exception:
            return False

    def _client_(self):
        import anthropic
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.key)
        return self._client

    def generate_structured(self, system_prompt, payload, response_model) -> AIResult:
        if not self.is_available():
            return AIResult(available=False, error="Anthropic API key/SDK not available.")
        try:
            schema = response_model.model_json_schema()
        except Exception:
            schema = {}
        base = (system_prompt +
                "\n\nSADECE aşağıdaki JSON şemasına uyan geçerli JSON döndür; açıklama/markdown YOK.\n" +
                json.dumps(schema, ensure_ascii=False) +
                "\n\nGİRDİ (Python tarafından doğrulanmış gerçekler — sayıları DEĞİŞTİRME):\n" +
                json.dumps(payload, ensure_ascii=False))
        instr, last = base, ""
        for _ in range(2):
            try:
                msg = self._client_().messages.create(
                    model=self.model, max_tokens=1500,
                    messages=[{"role": "user", "content": instr}])
                text = "".join(getattr(b, "text", "") for b in msg.content)
                data = json.loads(_extract_json(text))
                response_model(**data)
                return AIResult(available=True, data=data, raw=text)
            except Exception as e:
                last = str(e)
                instr = base + "\n\nÖNCEKİ CEVAP GEÇERSİZDİ. Yalnızca geçerli JSON döndür."
        return AIResult(available=True, error=f"validation_failed:{last}")
