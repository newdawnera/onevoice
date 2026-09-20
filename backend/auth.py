"""Supabase access-token verification for FastAPI routes."""

from __future__ import annotations

import asyncio
import hmac
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Optional
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status

from config import Settings, get_settings


BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}
MAX_AUTHORIZATION_HEADER_BYTES = 8192


class AuthRejected(Exception):
    """The supplied credentials are absent or invalid."""


class AuthUnavailable(Exception):
    """Token verification could not be performed safely."""


@dataclass(frozen=True)
class AuthenticatedUser:
    id: UUID
    email: Optional[str]
    role: str
    session_id: Optional[UUID]


class JWKSCache:
    def __init__(
        self,
        url: str,
        ttl_seconds: int,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._url = url
        self._ttl_seconds = ttl_seconds
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=3.0),
                follow_redirects=False,
            )
        )
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _refresh_locked(self) -> None:
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    self._url,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AuthUnavailable("Authentication key service is unavailable.") from exc

        raw_keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(raw_keys, list) or not raw_keys:
            raise AuthUnavailable("Authentication keys are not configured.")

        parsed: dict[str, Any] = {}
        try:
            for raw_key in raw_keys:
                kid = raw_key.get("kid") if isinstance(raw_key, dict) else None
                if isinstance(kid, str) and kid:
                    parsed[kid] = jwt.PyJWK.from_dict(raw_key).key
        except (jwt.PyJWKError, ValueError, TypeError) as exc:
            raise AuthUnavailable("Authentication keys are invalid.") from exc

        if not parsed:
            raise AuthUnavailable("Authentication keys have no usable key ids.")
        self._keys = parsed
        self._expires_at = time.monotonic() + self._ttl_seconds

    async def get_key(self, kid: str, *, force_refresh: bool = False) -> Any | None:
        now = time.monotonic()
        if not force_refresh and now < self._expires_at and self._keys:
            return self._keys.get(kid)

        async with self._lock:
            now = time.monotonic()
            if force_refresh or now >= self._expires_at or not self._keys:
                await self._refresh_locked()
            return self._keys.get(kid)


class SupabaseTokenVerifier:
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
        self._jwks = JWKSCache(
            settings.jwks_url,
            settings.auth_jwks_cache_seconds,
            self._client_factory,
        )
        self._resolved_mode: str | None = None
        self._mode_lock = asyncio.Lock()

    async def _verification_mode(self) -> str:
        configured = self.settings.supabase_jwt_verification_mode
        if configured != "auto":
            return configured
        if self._resolved_mode:
            return self._resolved_mode

        async with self._mode_lock:
            if self._resolved_mode:
                return self._resolved_mode
            # The linked Ally project publishes ES256 keys. Auto mode confirms
            # that JWKS remains available; it never falls back because a token
            # failed verification.
            await self._jwks.get_key("__mode_probe__", force_refresh=True)
            self._resolved_mode = "asymmetric"
            return self._resolved_mode

    @staticmethod
    def _user_from_claims(claims: dict[str, Any]) -> AuthenticatedUser:
        role = claims.get("role")
        if role != "authenticated":
            raise AuthRejected("This token is not a user token.")

        subject = claims.get("sub")
        try:
            user_id = UUID(subject)
        except (TypeError, ValueError, AttributeError) as exc:
            raise AuthRejected("The token subject is invalid.") from exc

        raw_session_id = claims.get("session_id")
        session_id = None
        if raw_session_id is not None:
            try:
                session_id = UUID(raw_session_id)
            except (TypeError, ValueError, AttributeError) as exc:
                raise AuthRejected("The token session is invalid.") from exc

        email = claims.get("email")
        if email is not None and not isinstance(email, str):
            raise AuthRejected("The token email is invalid.")

        return AuthenticatedUser(
            id=user_id,
            email=email,
            role=role,
            session_id=session_id,
        )

    async def _verify_asymmetric(self, token: str) -> AuthenticatedUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthRejected("The access token is malformed.") from exc

        algorithm = header.get("alg")
        if algorithm not in self.settings.supabase_jwt_algorithms:
            raise AuthRejected("The access token algorithm is not allowed.")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthRejected("The access token key id is missing.")

        key = await self._jwks.get_key(kid)
        if key is None:
            key = await self._jwks.get_key(kid, force_refresh=True)
        if key is None:
            raise AuthRejected("The access token key id is unknown.")

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.settings.supabase_jwt_algorithms),
                audience=self.settings.supabase_jwt_audience,
                issuer=self.settings.supabase_jwt_issuer,
                options={
                    "require": ["exp", "iss", "aud", "sub", "role"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthRejected("The access token is invalid or expired.") from exc
        return self._user_from_claims(claims)

    async def _verify_legacy(self, token: str) -> AuthenticatedUser:
        if not self.settings.supabase_publishable_key:
            raise AuthUnavailable("Legacy authentication is not configured.")
        try:
            async with self._client_factory() as client:
                response = await client.get(
                    self.settings.auth_user_url,
                    headers={
                        "Accept": "application/json",
                        "apikey": self.settings.supabase_publishable_key,
                        "Authorization": f"Bearer {token}",
                    },
                )
        except httpx.HTTPError as exc:
            raise AuthUnavailable("Authentication validation is unavailable.") from exc

        if response.status_code in {401, 403}:
            raise AuthRejected("The access token is invalid or expired.")
        if response.status_code != 200:
            raise AuthUnavailable("Authentication validation is unavailable.")
        try:
            user_payload = response.json()
        except ValueError as exc:
            raise AuthUnavailable("Authentication returned an invalid response.") from exc

        claims = {
            "sub": user_payload.get("id"),
            "email": user_payload.get("email"),
            "role": user_payload.get("role", "authenticated"),
            "session_id": user_payload.get("session_id"),
        }
        return self._user_from_claims(claims)

    async def verify(self, token: str) -> AuthenticatedUser:
        if not token or len(token.encode("utf-8")) > MAX_AUTHORIZATION_HEADER_BYTES:
            raise AuthRejected("The access token is invalid.")
        if self.settings.supabase_publishable_key and hmac.compare_digest(
            token, self.settings.supabase_publishable_key
        ):
            raise AuthRejected("A publishable key is not a user access token.")

        mode = await self._verification_mode()
        if mode == "legacy":
            return await self._verify_legacy(token)
        return await self._verify_asymmetric(token)


@lru_cache(maxsize=1)
def get_token_verifier() -> SupabaseTokenVerifier:
    return SupabaseTokenVerifier(get_settings())


def _extract_bearer_token(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise AuthRejected("Exactly one Authorization header is required.")
    raw = values[0]
    if len(raw.encode("utf-8")) > MAX_AUTHORIZATION_HEADER_BYTES:
        raise AuthRejected("The Authorization header is too large.")
    parts = raw.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise AuthRejected("A Bearer access token is required.")
    return parts[1]


async def get_current_user(
    request: Request,
    verifier: SupabaseTokenVerifier = Depends(get_token_verifier),
) -> AuthenticatedUser:
    try:
        token = _extract_bearer_token(request)
        return await verifier.verify(token)
    except AuthRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers=BEARER_HEADERS,
        ) from exc
    except AuthUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily unavailable.",
        ) from exc


require_authenticated_user = get_current_user
