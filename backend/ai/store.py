"""Service-role persistence adapter for generation and action review."""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

import httpx

from config import Settings

from .exceptions import AIInfrastructureError


class SupabaseAIStore:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=False,
            )
        )

    async def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise AIInfrastructureError("AI persistence is unavailable")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "apikey": self.settings.supabase_service_role_key,
            "Authorization": f"Bearer {self.settings.supabase_service_role_key}",
        }
        try:
            async with self._client_factory() as client:
                response = await client.post(
                    f"{self.settings.rest_url}/rpc/{name}",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise AIInfrastructureError("AI persistence failed") from error

    async def claim(
        self,
        *,
        user_id: UUID,
        idempotency_key: UUID,
        request_hash: str,
        claim_token: UUID,
    ) -> dict[str, Any]:
        result = await self._rpc(
            "claim_ai_generation",
            {
                "p_user_id": str(user_id),
                "p_operation": "meeting_generation",
                "p_idempotency_key": str(idempotency_key),
                "p_request_hash": request_hash,
                "p_claim_token": str(claim_token),
                "p_lease_seconds": self.settings.ai_generation_lease_seconds,
            },
        )
        if not isinstance(result, dict):
            raise AIInfrastructureError("AI claim returned invalid data")
        return result

    async def mark_provider_started(
        self, *, user_id: UUID, idempotency_key: UUID, claim_token: UUID
    ) -> bool:
        result = await self._rpc(
            "mark_ai_generation_provider_started",
            {
                "p_user_id": str(user_id),
                "p_idempotency_key": str(idempotency_key),
                "p_claim_token": str(claim_token),
            },
        )
        return result is True

    async def fail(
        self,
        *,
        user_id: UUID,
        idempotency_key: UUID,
        claim_token: UUID,
        category: str,
        uncertain: bool,
    ) -> None:
        await self._rpc(
            "fail_ai_generation",
            {
                "p_user_id": str(user_id),
                "p_idempotency_key": str(idempotency_key),
                "p_claim_token": str(claim_token),
                "p_failure_category": category[:64],
                "p_uncertain": uncertain,
            },
        )

    async def persist(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._rpc("persist_ai_generation", payload)
        if not isinstance(result, dict):
            raise AIInfrastructureError("AI persistence returned invalid data")
        return result

    async def get_result(
        self, *, user_id: UUID, idempotency_key: UUID
    ) -> dict[str, Any]:
        result = await self._rpc(
            "get_ai_generation_result",
            {
                "p_user_id": str(user_id),
                "p_idempotency_key": str(idempotency_key),
            },
        )
        if not isinstance(result, dict) or not result:
            raise AIInfrastructureError("Persisted AI result is unavailable")
        return result

    async def review_action(
        self,
        *,
        user_id: UUID,
        action_item_id: UUID,
        decision: str,
        title: str,
        assignee: str | None,
        assignee_email: str | None,
        start_date: str | None,
        deadline: str | None,
    ) -> dict[str, Any]:
        result = await self._rpc(
            "review_ai_action",
            {
                "p_user_id": str(user_id),
                "p_action_item_id": str(action_item_id),
                "p_decision": decision,
                "p_title": title,
                "p_assignee": assignee,
                "p_assignee_email": assignee_email,
                "p_start_date": start_date,
                "p_deadline": deadline,
            },
        )
        if not isinstance(result, dict):
            raise AIInfrastructureError("AI review returned invalid data")
        return result
