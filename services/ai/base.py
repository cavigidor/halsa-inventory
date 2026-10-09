"""Sağlayıcıdan bağımsız AI arayüzü. İş mantığı asla SDK nesnesi import etmez."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class AIResult:
    available: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    raw: Optional[str] = None


class AIProvider(ABC):
    name = "base"

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def generate_structured(self, system_prompt: str, payload: dict, response_model) -> AIResult: ...


class NullProvider(AIProvider):
    """AI yapılandırılmadığında: her zaman 'kullanılamıyor' döner, asla çökmez."""
    name = "none"

    def __init__(self, reason="AI provider is not configured."):
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def generate_structured(self, system_prompt, payload, response_model) -> AIResult:
        return AIResult(available=False, error=self.reason)
