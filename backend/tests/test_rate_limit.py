import asyncio
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth import AuthenticatedUser, get_current_user
from config import get_settings
from conftest import configured_settings
from rate_limit import (
    RateLimitSpec,
    RateLimiterUnavailable,
    SupabaseRateLimiter,
    enforce_rate_limits,
    get_rate_limiter,
)


USER = AuthenticatedUser(
    id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    email="user@example.com",
    role="authenticated",
    session_id=None,
)


def factory_for(handler):
    transport = httpx.MockTransport(handler)
    return lambda: httpx.AsyncClient(transport=transport)


def test_rate_limiter_allows_within_quota():
    limiter = SupabaseRateLimiter(
        configured_settings(),
        client_factory=factory_for(
            lambda _request: httpx.Response(
                200,
                json={"allowed": True, "remaining": 4, "retry_after_seconds": 30},
            )
        ),
    )
    result = asyncio.run(
        limiter.consume(USER, bucket="ai_minute", limit=5, window_seconds=60)
    )
    assert result.allowed is True
    assert result.remaining == 4


def test_rate_limiter_unavailable_fails_closed():
    limiter = SupabaseRateLimiter(
        configured_settings(supabase_service_role_key=""),
    )
    try:
        asyncio.run(
            limiter.consume(USER, bucket="ai_minute", limit=5, window_seconds=60)
        )
    except RateLimiterUnavailable:
        pass
    else:
        raise AssertionError("Unavailable rate limiting did not fail closed")


class StaticLimiter:
    def __init__(self, body=None, unavailable=False):
        self.body = body
        self.unavailable = unavailable

    async def consume(self, *_args, **_kwargs):
        if self.unavailable:
            raise RateLimiterUnavailable()
        return self.body


def dependency_client(limiter):
    from rate_limit import RateLimitResult

    app = FastAPI()
    dependency = enforce_rate_limits(
        RateLimitSpec("email_hour", "rate_limit_email_per_hour", 3600)
    )

    @app.get("/costly", dependencies=[Depends(dependency)])
    async def costly():
        return {"ok": True}

    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    return TestClient(app), RateLimitResult


def test_rate_limit_denial_returns_429_and_retry_after():
    client, result_type = dependency_client(None)
    limiter = StaticLimiter(result_type(False, 0, 42))
    client.app.dependency_overrides[get_rate_limiter] = lambda: limiter
    response = client.get("/costly")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"


def test_rate_limit_dependency_unavailable_returns_503():
    client, _result_type = dependency_client(StaticLimiter(unavailable=True))
    response = client.get("/costly")
    assert response.status_code == 503
