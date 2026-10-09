"""Configuration + centralized provider selection. The app must start with no key."""
import importlib

import pytest
from fastapi.testclient import TestClient

import config as C
from services.ai.base import NullProvider
from services.ai.factory import get_provider
from services.ai.mock_provider import MockProvider
from services.ai.openai_provider import OpenAIProvider


def test_defaults_are_disabled_and_bounded():
    s = C.ai_settings()
    assert s["provider"] == "disabled"
    assert 1 <= s["timeout_seconds"] <= 120
    assert 256 <= s["max_output_tokens"] <= 16000
    assert 0 <= s["max_transient_retries"] <= 3


def test_bounds_are_enforced(monkeypatch):
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "100000")
    monkeypatch.setenv("AI_MAX_OUTPUT_TOKENS", "-5")
    monkeypatch.setenv("AI_MAX_RETRIES", "99")
    s = C.ai_settings()
    assert s["timeout_seconds"] == 120 and s["max_output_tokens"] == 256 and s["max_transient_retries"] == 3


def test_model_configurable_with_single_default_location(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    assert C.ai_settings()["model"] == C.DEFAULT_MODELS["openai"]
    monkeypatch.setenv("AI_MODEL", "some-other-model")
    assert C.ai_settings()["model"] == "some-other-model"


@pytest.mark.parametrize("value", ["", "disabled", "none", "OFF"])
def test_disabled_values(monkeypatch, value):
    monkeypatch.setenv("AI_PROVIDER", value)
    p = get_provider()
    assert isinstance(p, NullProvider) and not p.is_available() and p.category == "disabled"


def test_openai_without_key_degrades_cleanly(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    p = get_provider()
    assert isinstance(p, NullProvider) and p.category == "missing_key" and "openai" in p.name


def test_openai_with_key_selected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    p = get_provider()
    assert isinstance(p, OpenAIProvider) and p.is_available()


def test_mock_and_unknown(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "mock")
    assert isinstance(get_provider(), MockProvider)
    monkeypatch.setenv("AI_PROVIDER", "nonsense")
    p = get_provider()
    assert isinstance(p, NullProvider) and not p.is_available()


def test_provider_names_only_in_factory_and_config():
    """No business module branches on provider names (selection is centralized)."""
    import pathlib
    root = pathlib.Path(C.__file__).parent
    offenders = []
    for path in [root / "app.py", *(root / "services").glob("*.py")]:
        text = path.read_text(encoding="utf-8")
        if '== "openai"' in text or "== 'openai'" in text or '== "anthropic"' in text:
            offenders.append(path.name)
    assert offenders == []


def test_app_starts_without_any_key(monkeypatch, synthetic_dir):
    monkeypatch.setenv("AI_PROVIDER", "openai")            # selected but no key
    monkeypatch.setenv("DATA_FOLDER", synthetic_dir)
    import app as APP
    importlib.reload(APP)
    with TestClient(APP.app) as client:                    # runs the real startup event
        h = client.get("/api/health").json()
        assert h["status"] == "ok" and h["ai_available"] is False and h["ai_reason"]
        assert client.get("/api/context").status_code == 200
        a = client.get("/api/agent/actions").json()
        assert a["ai_available"] is False and a["count"] > 0
        assert all(x["interpretation"] is None for x in a["actions"])
