"""Shared, atomic rate limiting backed by the linked Supabase database."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

import httpx
from fastapi import Depends, HTTPException, status

from auth import AuthenticatedUser, get_current_user
from config import Settings, get_settings


class RateLimiterUnavailable(Exception):
    pass


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after: int


@dataclass(frozen=True)
class RateLimitSpec:
    bucket: str
    limit_setting: str
    window_seconds: int


class SupabaseRateLimiter:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=3.0),
                follow_redirects=False,
            )
        )

    async def consume(
        self,
        user: AuthenticatedUser,
        *,
        bucket: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitResult:
        if (
            not self.settings.supabase_url
            or not self.settings.supabase_service_role_key
        ):
            raise RateLimiterUnavailable("Shared rate limiting is not configured.")

        url = f"{self.settings.rest_url}/rpc/consume_rate_limit"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "apikey": self.settings.supabase_service_role_key,
            "Authorization": f"Bearer {self.settings.supabase_service_role_key}",
        }
        payload = {
            "p_user_id": str(user.id),
            "p_route_class": bucket,
            "p_limit": limit,
            "p_window_seconds": window_seconds,
        }
        try:
            async with self._client_factory() as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise RateLimiterUnavailable(
                "Shared rate limiting is temporarily unavailable."
            ) from exc

        if isinstance(body, list) and len(body) == 1:
            body = body[0]
        if not isinstance(body, dict):
            raise RateLimiterUnavailable("Shared rate limiter returned invalid data.")
        try:
            return RateLimitResult(
                allowed=body["allowed"] is True,
                remaining=max(0, int(body["remaining"])),
                retry_after=max(1, int(body["retry_after_seconds"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RateLimiterUnavailable(
                "Shared rate limiter returned invalid data."
            ) from exc


@lru_cache(maxsize=1)
def get_rate_limiter() -> SupabaseRateLimiter:
    return SupabaseRateLimiter(get_settings())


def enforce_rate_limits(*specs: RateLimitSpec):
    async def dependency(
        user: AuthenticatedUser = Depends(get_current_user),
        limiter: SupabaseRateLimiter = Depends(get_rate_limiter),
        settings: Settings = Depends(get_settings),
    ) -> None:
        for spec in specs:
            limit = getattr(settings, spec.limit_setting)
            try:
                result = await limiter.consume(
                    user,
                    bucket=spec.bucket,
                    limit=limit,
                    window_seconds=spec.window_seconds,
                )
            except RateLimiterUnavailable as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="This operation is temporarily unavailable.",
                ) from exc
            if not result.allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please try again later.",
                    headers={"Retry-After": str(result.retry_after)},
                )

    return dependency


AI_RATE_LIMIT = enforce_rate_limits(
    RateLimitSpec("ai_minute", "rate_limit_ai_per_minute", 60),
    RateLimitSpec("ai_day", "rate_limit_ai_per_day", 86_400),
)
TRANSCRIPTION_RATE_LIMIT = enforce_rate_limits(
    RateLimitSpec(
        "transcription_minute", "rate_limit_transcription_per_minute", 60
    ),
    RateLimitSpec(
        "transcription_day", "rate_limit_transcription_per_day", 86_400
    ),
)
DOCUMENT_RATE_LIMIT = enforce_rate_limits(
    RateLimitSpec(
        "document_upload_minute",
        "rate_limit_document_upload_per_minute",
        60,
    )
)
EMAIL_RATE_LIMIT = enforce_rate_limits(
    RateLimitSpec("email_hour", "rate_limit_email_per_hour", 3_600)
)
