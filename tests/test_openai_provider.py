"""OpenAI provider behind the existing AIProvider interface — fully mocked, no network, no key cost.

A fake client object is injected; it either returns a fake Responses API result or raises
real `openai` SDK exception classes, so the error mapping is tested against the real SDK types.
"""
from types import SimpleNamespace

import json

import httpx
import openai
import pytest

import schemas as S
from services.ai.base import AIProvider
from services.ai.openai_provider import OpenAIProvider

SETTINGS = {"provider": "openai", "model": "test-model", "timeout_seconds": 5,
            "max_output_tokens": 500, "max_transient_retries": 0, "reasoning_effort": None}
PAYLOAD = {"schema_version": "1", "task": "message_draft", "facts": []}
GOOD = {"message": "Merhaba, görüşmek isteriz.", "evidence_fact_ids": [], "warnings": []}


class FakeResponses:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.kwargs = result, exc, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.result


def fake_client(result=None, exc=None):
    return SimpleNamespace(responses=FakeResponses(result, exc))


def fake_response(text=None, status="completed", refusal=False, incomplete_reason=None, phase=None,
                  reasoning_tokens=900):
    """Shape of a Responses API result: output[] message items with output_text content."""
    if refusal:
        content = [SimpleNamespace(type="refusal", refusal="no")]
    else:
        content = [SimpleNamespace(type="output_text", text=text)] if text is not None else []
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason=incomplete_reason) if incomplete_reason else None,
        output=[SimpleNamespace(type="reasoning", content=None),
                SimpleNamespace(type="message", phase=phase, content=content)],
        usage=SimpleNamespace(input_tokens=120, output_tokens=40, total_tokens=160,
                              output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens)))


GOOD_JSON = json.dumps(GOOD, ensure_ascii=False)


def _req():
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def _resp(code):
    return httpx.Response(code, request=_req())


def test_follows_existing_interface():
    assert issubclass(OpenAIProvider, AIProvider)
    p = OpenAIProvider(SETTINGS, client=fake_client())
    assert p.name == "openai" and p.model == "test-model" and p.is_available()


def test_success_response_is_validated_and_metered():
    c = fake_client(fake_response(text=GOOD_JSON))
    r = OpenAIProvider(SETTINGS, client=c).generate_structured("SYS", PAYLOAD, S.MessageDraftAI)
    assert r.available and r.error is None and r.data == GOOD
    assert r.meta["input_tokens"] == 120 and r.meta["output_tokens"] == 40 and r.meta["model"] == "test-model"
    assert r.meta["reasoning_tokens"] == 900
    kw = c.responses.kwargs
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["name"] == "MessageDraftAI"
    assert fmt["schema"]["additionalProperties"] is False
    assert kw["instructions"] == "SYS"
    assert kw["max_output_tokens"] == 500 and kw["store"] is False and kw["model"] == "test-model"
    assert isinstance(kw["input"], str)              # JSON evidence only — no files/tools
    assert "tools" not in kw and "file_ids" not in kw


@pytest.mark.parametrize("exc,category", [
    (openai.AuthenticationError("bad key", response=_resp(401), body=None), "auth"),
    (openai.PermissionDeniedError("denied", response=_resp(403), body=None), "auth"),
    (openai.RateLimitError("slow down", response=_resp(429), body=None), "rate_limit"),
    (openai.RateLimitError("no credit", response=_resp(429),
                           body={"type": "insufficient_quota", "code": "insufficient_quota"}), "quota"),
    (openai.APITimeoutError(request=_req()), "timeout"),
    (openai.APIConnectionError(request=_req()), "connection"),
    (openai.InternalServerError("boom", response=_resp(500), body=None), "provider_5xx"),
    (openai.APIStatusError("bad gateway", response=_resp(502), body=None), "provider_5xx"),
    (openai.BadRequestError("unknown model", response=_resp(400), body=None), "bad_request"),
    (ValueError("could not parse"), "malformed"),
])
def test_failures_are_categorized_never_raised(exc, category):
    r = OpenAIProvider(SETTINGS, client=fake_client(exc=exc)).generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert r.data is None and r.error_category == category
    assert "sk-" not in (r.error or "")


def test_no_output_text_is_malformed():
    r = OpenAIProvider(SETTINGS, client=fake_client(fake_response(text=None))) \
        .generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert r.data is None and r.error_category == "malformed"


def test_schema_invalid_output_is_malformed_with_safe_diagnostics():
    r = OpenAIProvider(SETTINGS, client=fake_client(fake_response(text='{"message": "x"}'))) \
        .generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert r.data is None and r.error_category == "malformed"
    assert "missing" in r.error and "output_chars=" in r.error and '"x"' not in r.error   # no content leaked


def test_truncated_json_with_completed_status_is_malformed():
    r = OpenAIProvider(SETTINGS, client=fake_client(fake_response(text=GOOD_JSON[:25]))) \
        .generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert r.error_category == "malformed" and "json_invalid" in r.error


def test_incomplete_and_refusal():
    r = OpenAIProvider(SETTINGS, client=fake_client(fake_response(text=GOOD_JSON[:25], status="incomplete",
                                                                  incomplete_reason="max_output_tokens"))) \
        .generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert r.error_category == "incomplete" and "AI_MAX_OUTPUT_TOKENS" in r.error
    assert r.meta["output_tokens"] == 40 and r.meta["reasoning_tokens"] == 900      # usage kept on failure
    r = OpenAIProvider(SETTINGS, client=fake_client(fake_response(refusal=True))) \
        .generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert r.error_category == "refusal"


def test_non_final_phase_text_is_ignored():
    resp = fake_response(text=GOOD_JSON)
    resp.output.insert(1, SimpleNamespace(type="message", phase="commentary",
                                          content=[SimpleNamespace(type="output_text", text="not json")]))
    r = OpenAIProvider(SETTINGS, client=fake_client(resp)).generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert r.data == GOOD


def test_missing_key_is_unavailable_not_an_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = OpenAIProvider(SETTINGS)
    assert not p.is_available()
    r = p.generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert not r.available and r.error_category == "missing_key"


def test_real_client_construction_uses_finite_timeout_and_bounded_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    p = OpenAIProvider({**SETTINGS, "timeout_seconds": 7, "max_transient_retries": 1})
    client = p._get_client()                      # constructs the SDK client; makes no request
    assert client.max_retries == 1
    assert float(client.timeout) == 7.0


def test_reasoning_effort_only_sent_when_configured():
    c = fake_client(fake_response(text=GOOD_JSON))
    OpenAIProvider({**SETTINGS, "reasoning_effort": "low"}, client=c).generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert c.responses.kwargs["reasoning"] == {"effort": "low"}
    c2 = fake_client(fake_response(text=GOOD_JSON))
    OpenAIProvider(SETTINGS, client=c2).generate_structured("S", PAYLOAD, S.MessageDraftAI)
    assert "reasoning" not in c2.responses.kwargs
