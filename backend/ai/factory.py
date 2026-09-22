"""AI provider construction and FastAPI dependency wiring."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from config import Settings, get_settings

from .groq_provider import GroqProvider
from .service import AIService
from .store import SupabaseAIStore


@dataclass
class AIResources:
    service: AIService | None
    provider: GroqProvider | None
    store: SupabaseAIStore

    async def close(self) -> None:
        if self.provider is not None:
            await self.provider.close()


def create_ai_resources(settings: Settings) -> AIResources:
    store = SupabaseAIStore(settings)
    if not settings.ai_enabled:
        return AIResources(service=None, provider=None, store=store)
    provider = GroqProvider(
        api_key=settings.groq_api_key,
        text_model=settings.ai_text_model,
        structured_model=settings.ai_structured_model,
        connect_timeout=settings.ai_connect_timeout_seconds,
        read_timeout=settings.ai_read_timeout_seconds,
        write_timeout=settings.ai_write_timeout_seconds,
        pool_timeout=settings.ai_pool_timeout_seconds,
        max_retries=settings.ai_max_retries,
    )
    return AIResources(
        service=AIService(settings, provider, store),
        provider=provider,
        store=store,
    )


def get_ai_service(request: Request) -> AIService:
    service = getattr(request.app.state, "ai_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="AI service is unavailable.")
    return service


def get_ai_store(request: Request) -> SupabaseAIStore:
    store = getattr(request.app.state, "ai_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="AI review service is unavailable.")
    return store
