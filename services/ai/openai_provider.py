"""OpenAI adaptörü — Responses API (responses.create), resmi Python SDK, yapılandırılmış çıktı.

- The SDK is imported lazily: importing this module never requires `openai`.
- The model receives ONLY the evidence packet (JSON) as input, never files,
  DataFrames, databases or directories.
- Output is constrained by a strict JSON schema derived from the Pydantic model
  (Structured Outputs). Status and usage are checked first (truncation ->
  ERR_INCOMPLETE); anything that does not validate is ERR_MALFORMED, never "repaired".
- Every exception is mapped to a categorized AIResult; nothing is raised.
- `store=False`: the request is not retained for later retrieval on OpenAI's side.
"""
import json
import os
import time

from .base import (AIProvider, AIResult, ERR_AUTH, ERR_BAD_REQUEST, ERR_CONNECTION,
                   ERR_INCOMPLETE, ERR_MALFORMED, ERR_MISSING_KEY, ERR_PROVIDER_5XX,
                   ERR_QUOTA, ERR_RATE_LIMIT, ERR_REFUSAL, ERR_SDK_MISSING, ERR_TIMEOUT, ERR_UNKNOWN)


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, settings=None, client=None):
        import config as C
        s = settings or C.ai_settings()
        self.model = s.get("model") or C.DEFAULT_MODELS["openai"]
        self.timeout = s["timeout_seconds"]
        self.max_output_tokens = s["max_output_tokens"]
        self.max_retries = s["max_transient_retries"]
        self.reasoning_effort = s.get("reasoning_effort")
        self._client = client            # injectable for tests (no network)

    # ---- availability ----
    @staticmethod
    def _key():
        return (os.environ.get("OPENAI_API_KEY") or "").strip() or None

    def sdk_installed(self):
        try:
            import openai  # noqa: F401
            return True
        except Exception:
            return False

    def unavailable_reason(self):
        if self._client is not None:
            return None
        if not self._key():
            return ERR_MISSING_KEY
        if not self.sdk_installed():
            return ERR_SDK_MISSING
        return None

    def is_available(self) -> bool:
        return self.unavailable_reason() is None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            # finite timeout + small bounded SDK retry for transient errors only
            self._client = OpenAI(api_key=self._key(), timeout=float(self.timeout),
                                  max_retries=int(self.max_retries))
        return self._client

    # ---- call ----
    def generate_structured(self, system_prompt, payload, response_model) -> AIResult:
        meta = {"provider": self.name, "model": self.model}
        why = self.unavailable_reason()
        if why:
            msg = ("OPENAI_API_KEY is not set." if why == ERR_MISSING_KEY
                   else "The 'openai' package is not installed.")
            return AIResult(available=False, error=msg, error_category=why, meta=meta)

        try:
            text_format = _text_format(response_model)
        except Exception as e:
            return AIResult(available=True, error=f"Could not build output schema ({type(e).__name__}).",
                            error_category=ERR_MALFORMED, meta=meta)
        kwargs = dict(
            model=self.model,
            instructions=system_prompt,
            input=json.dumps(payload, ensure_ascii=False),
            text={"format": text_format},          # strict JSON schema (Structured Outputs)
            max_output_tokens=self.max_output_tokens,
            store=False,
        )
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}

        t0 = time.monotonic()
        try:
            # create() (not parse()): we inspect status/usage BEFORE parsing, so a truncated
            # answer is reported as "incomplete" with token counts instead of a bare ValidationError.
            resp = self._get_client().responses.create(**kwargs)
        except Exception as e:  # mapped below; never re-raised
            meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
            cat, msg = _classify_exception(e)
            return AIResult(available=True, error=msg, error_category=cat, meta=meta)
        meta["latency_ms"] = int((time.monotonic() - t0) * 1000)
        meta.update(_usage(resp))

        status = getattr(resp, "status", None)
        if status == "incomplete":
            reason = getattr(getattr(resp, "incomplete_details", None), "reason", None) or "unknown"
            hint = (" Raise AI_MAX_OUTPUT_TOKENS or lower AI_REASONING_EFFORT."
                    if reason == "max_output_tokens" else "")
            return AIResult(available=True, error=f"Model output incomplete ({reason}).{hint}",
                            error_category=ERR_INCOMPLETE, meta=meta)
        if _has_refusal(resp):
            return AIResult(available=True, error="Model refused the request.",
                            error_category=ERR_REFUSAL, meta=meta)

        text = _final_text(resp)
        if not text:
            return AIResult(available=True, error=f"No output text returned (status={status}).",
                            error_category=ERR_MALFORMED, meta=meta)
        try:
            parsed = response_model.model_validate_json(text)
            data = parsed.model_dump()
        except Exception as e:
            kinds = sorted({err.get("type", "?") for err in getattr(e, "errors", lambda: [])()})[:4]
            return AIResult(available=True,
                            error=(f"Schema validation failed ({type(e).__name__}: {', '.join(kinds) or '?'}; "
                                   f"status={status}, output_chars={len(text)})."),
                            error_category=ERR_MALFORMED, meta=meta)
        return AIResult(available=True, data=data, raw=text, meta=meta)


# ---------------- helpers ----------------
def _text_format(response_model):
    """Strict json_schema text format for a Pydantic model, built by the official SDK helper."""
    from openai.lib._parsing._responses import type_to_text_format_param
    return type_to_text_format_param(response_model)


def _final_text(resp):
    """Text of the final answer message (ignores non-final phases, e.g. commentary)."""
    texts = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) not in (None, "message"):
            continue
        if getattr(item, "phase", None) not in (None, "final_answer"):
            continue
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) == "output_text" and isinstance(getattr(c, "text", None), str):
                texts.append(c.text)
    return texts[-1] if texts else None


def _usage(resp):
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    out = {}
    for k in ("input_tokens", "output_tokens", "total_tokens"):
        v = getattr(u, k, None)
        if isinstance(v, int):
            out[k] = v
    r = getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", None)
    if isinstance(r, int):
        out["reasoning_tokens"] = r
    return out


def _has_refusal(resp):
    for item in getattr(resp, "output", None) or []:
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) == "refusal":
                return True
    return False


def _classify_exception(e):
    """Map SDK / parsing exceptions to (category, safe message). Never includes payload or key."""
    try:
        import openai
    except Exception:  # pragma: no cover - SDK missing is handled earlier
        return ERR_UNKNOWN, type(e).__name__
    name = type(e).__name__
    if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ERR_AUTH, "Authentication with OpenAI failed (check OPENAI_API_KEY)."
    if isinstance(e, openai.RateLimitError):
        if getattr(e, "code", None) == "insufficient_quota" or getattr(e, "type", None) == "insufficient_quota":
            return ERR_QUOTA, ("OpenAI account has no available credit (insufficient_quota): add credit "
                               "or raise the usage limit in the OpenAI platform billing settings.")
        return ERR_RATE_LIMIT, "OpenAI rate limit exceeded (too many requests); try again shortly."
    if isinstance(e, openai.APITimeoutError):          # subclass of APIConnectionError: check first
        return ERR_TIMEOUT, "OpenAI request timed out."
    if isinstance(e, openai.APIConnectionError):
        return ERR_CONNECTION, "Could not connect to OpenAI."
    if isinstance(e, openai.InternalServerError):
        return ERR_PROVIDER_5XX, "OpenAI server error."
    if isinstance(e, openai.APIStatusError):
        code = getattr(e, "status_code", None)
        if code and code >= 500:
            return ERR_PROVIDER_5XX, f"OpenAI server error ({code})."
        return ERR_BAD_REQUEST, f"OpenAI rejected the request ({code})."
    if isinstance(e, (openai.LengthFinishReasonError,)):
        return ERR_INCOMPLETE, "Model output incomplete (length)."
    if isinstance(e, (openai.ContentFilterFinishReasonError,)):
        return ERR_REFUSAL, "Model output blocked by content filter."
    if isinstance(e, (ValueError, TypeError)) or name in ("ValidationError", "JSONDecodeError"):
        return ERR_MALFORMED, f"Malformed model output ({name})."
    return ERR_UNKNOWN, f"Unexpected provider error ({name})."
