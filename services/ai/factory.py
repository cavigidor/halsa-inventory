"""Ortam değişkenlerinden sağlayıcı seçer. Anahtar/SDK yoksa NullProvider döner (çökmez)."""
import os
from .base import NullProvider
from .mock_provider import MockProvider


def get_provider():
    import config as C
    if not C.ENABLE_AI:
        return NullProvider("ENABLE_AI=false")
    p = os.environ.get("AI_PROVIDER", "").lower()
    if p == "mock":
        return MockProvider()
    if p == "anthropic":
        from .anthropic_provider import AnthropicProvider
        prov = AnthropicProvider()
        return prov if prov.is_available() else NullProvider("Anthropic API key or SDK missing.")
    if p == "openai":
        return NullProvider("OpenAI provider not implemented in this milestone.")
    return NullProvider("AI provider is not configured.")
