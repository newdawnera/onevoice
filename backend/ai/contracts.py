"""Provider-neutral AI contracts used by application services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class AIMessage:
    role: str
    content: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class ProviderTextResult:
    content: str
    provider: str
    model: str
    finish_reason: str
    usage: TokenUsage
    refusal: bool = False
    request_id: str | None = None


@dataclass(frozen=True)
class ProviderStructuredResult:
    data: dict[str, Any]
    provider: str
    model: str
    finish_reason: str
    usage: TokenUsage
    refusal: bool = False
    request_id: str | None = None


class AIProvider(Protocol):
    name: str
    text_model: str
    structured_model: str

    async def generate_text(
        self,
        *,
        messages: Sequence[AIMessage],
        temperature: float,
        max_output_tokens: int,
    ) -> ProviderTextResult: ...

    async def generate_structured(
        self,
        *,
        messages: Sequence[AIMessage],
        schema_name: str,
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
    ) -> ProviderStructuredResult: ...

    async def close(self) -> None: ...
