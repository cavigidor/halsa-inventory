"""Sağlayıcıdan bağımsız AI arayüzü. İş mantığı asla SDK nesnesi import etmez.

Every provider implements the same two methods. A provider NEVER raises to its
caller: every failure comes back as an AIResult with a categorized error, so
deterministic analytics can never be broken by the model layer.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

# Categorized failure reasons (stable strings — used in API responses, logs and tests)
ERR_DISABLED = "disabled"              # AI_PROVIDER=disabled / ENABLE_AI=false
ERR_MISSING_KEY = "missing_key"        # provider selected but no API key
ERR_SDK_MISSING = "sdk_missing"        # provider SDK not installed
ERR_AUTH = "auth"                      # invalid key / permission denied
ERR_RATE_LIMIT = "rate_limit"
ERR_TIMEOUT = "timeout"
ERR_CONNECTION = "connection"
ERR_PROVIDER_5XX = "provider_5xx"
ERR_BAD_REQUEST = "bad_request"        # 4xx other than auth/rate (e.g. unknown model)
ERR_REFUSAL = "refusal"                # model refused
ERR_INCOMPLETE = "incomplete"          # output truncated (token limit)
ERR_MALFORMED = "malformed"            # not parseable / schema-invalid output
ERR_FACT_REFERENCE = "fact_reference"  # unknown / unresolved fact id
ERR_NUMERIC_GUARD = "numeric_guard"    # model introduced its own numbers
ERR_PACKET = "packet_rejected"         # evidence packet failed minimization checks
ERR_UNKNOWN = "unknown"


@dataclass
class AIResult:
    available: bool
    data: Optional[dict] = None
    error: Optional[str] = None            # human-readable reason (no secrets, no payload)
    raw: Optional[str] = None              # kept in memory only; never logged/persisted
    error_category: Optional[str] = None   # one of the ERR_* constants
    meta: dict = field(default_factory=dict)  # provider, model, latency_ms, input/output tokens


class AIProvider(ABC):
    name = "base"
    model: Optional[str] = None

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def generate_structured(self, system_prompt: str, payload: dict, response_model) -> AIResult: ...


class NullProvider(AIProvider):
    """AI yapılandırılmadığında: her zaman 'kullanılamıyor' döner, asla çökmez."""
    name = "none"

    def __init__(self, reason="AI provider is not configured.", category=ERR_DISABLED):
        self.reason = reason
        self.category = category

    def is_available(self) -> bool:
        return False

    def generate_structured(self, system_prompt, payload, response_model) -> AIResult:
        return AIResult(available=False, error=self.reason, error_category=self.category,
                        meta={"provider": self.name})
