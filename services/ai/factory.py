"""The ONLY place a provider is chosen. Business code never branches on provider names.

Missing key / SDK never crashes startup: a NullProvider with a categorized reason
is returned instead, so the backend and all deterministic endpoints keep working.
"""
from .base import NullProvider, ERR_DISABLED, ERR_MISSING_KEY, ERR_SDK_MISSING, ERR_BAD_REQUEST
from .mock_provider import MockProvider


def _openai(settings):
    from .openai_provider import OpenAIProvider
    return OpenAIProvider(settings)


def _anthropic(settings):
    from .anthropic_provider import AnthropicProvider
    return AnthropicProvider(model=settings.get("model"))


def _mock(settings):
    return MockProvider()


REGISTRY = {"openai": _openai, "anthropic": _anthropic, "mock": _mock}


def get_provider(settings=None):
    import config as C
    if not C.ENABLE_AI:
        return NullProvider("ENABLE_AI=false", ERR_DISABLED)
    s = settings or C.ai_settings()
    name = s["provider"]
    if name == "disabled":
        return NullProvider("AI provider is not configured (AI_PROVIDER=disabled).", ERR_DISABLED)
    build = REGISTRY.get(name)
    if build is None:
        return NullProvider(f"Unknown AI_PROVIDER '{name}'. Use one of: {', '.join(C.AI_PROVIDERS)}.",
                            ERR_BAD_REQUEST)
    prov = build(s)
    if prov.is_available():
        return prov
    why = getattr(prov, "unavailable_reason", lambda: ERR_MISSING_KEY)() or ERR_MISSING_KEY
    msg = {ERR_MISSING_KEY: f"{name}: API key missing.",
           ERR_SDK_MISSING: f"{name}: SDK not installed."}.get(why, f"{name}: unavailable.")
    null = NullProvider(msg, why)
    null.name = f"{name}(unavailable)"
    return null
