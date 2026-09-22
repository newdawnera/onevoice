"""Secure reminder claiming, delivery, and status-token workflows."""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from typing import Any, Callable
from urllib.parse import urlencode
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from email_validator import EmailNotValidError, validate_email

import config
from config import Settings, get_settings


logger = logging.getLogger(__name__)
MAX_RETRY_AFTER_SECONDS = 86_400
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class ReminderInfrastructureError(Exception):
    """A durable database operation could not be completed safely."""


class StatusPostRateLimiter:
    """Small per-process guard; opaque high-entropy tokens remain the authority."""

    def __init__(self, *, limit: int = 20, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._entries: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, identity: str, now: float) -> bool:
        async with self._lock:
            started, count = self._entries.get(identity, (now, 0))
            if now - started >= self.window_seconds:
                started, count = now, 0
            if count >= self.limit:
                return False
            self._entries[identity] = (started, count + 1)
            if len(self._entries) > 10_000:
                cutoff = now - self.window_seconds
                self._entries = {
                    key: value for key, value in self._entries.items()
                    if value[0] >= cutoff
                }
            return True


@dataclass(frozen=True)
class BrevoResult:
    kind: str
    failure_code: str | None = None
    message: str | None = None
    message_id: str | None = None
    retry_after_seconds: int | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def logical_date(now: datetime, timezone_name: str) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ZoneInfo(timezone_name)).date()


def scheduled_reminder_type(action: dict[str, Any], today: date) -> str | None:
    """Pure precedence helper mirrored by the database claim function."""

    if action.get("status") == "completed":
        return None
    if action.get("deadline") == today:
        return "deadline"
    if action.get("start_date") == today:
        return "start_date"
    if action.get("deadline") == today + timedelta(days=1):
        return "before_deadline"
    return None


def _parse_retry_after(value: str | None, now: datetime) -> int | None:
    if not value:
        return None
    try:
        seconds = int(value)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = int((parsed - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
    return min(MAX_RETRY_AFTER_SECONDS, max(1, seconds))


class BrevoEmailAdapter:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=5.0, read=15.0, write=10.0),
                follow_redirects=False,
            )
        )

    async def send(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        subject: str,
        html_content: str,
        delivery_id: str,
    ) -> BrevoResult:
        if not self.settings.brevo_api_key or not self.settings.sender_email:
            return BrevoResult(
                "terminal",
                failure_code="email_not_configured",
                message="Email delivery is not configured.",
            )

        payload = {
            "sender": {
                "name": self.settings.sender_name,
                "email": self.settings.sender_email,
            },
            "to": [{"email": recipient_email, "name": recipient_name[:200]}],
            "subject": subject[:200],
            "htmlContent": html_content,
            "tags": ["ally-reminder"],
            "headers": {"X-Ally-Delivery": delivery_id},
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "api-key": self.settings.brevo_api_key,
        }
        try:
            async with self._client_factory() as client:
                response = await client.post(BREVO_URL, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return BrevoResult(
                "retryable",
                failure_code="provider_connection_failed",
                message="The provider connection failed before a response.",
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.WriteError):
            return BrevoResult(
                "ambiguous",
                failure_code="provider_timeout_unknown",
                message="The provider outcome could not be confirmed.",
            )
        except httpx.HTTPError:
            return BrevoResult(
                "ambiguous",
                failure_code="provider_transport_unknown",
                message="The provider outcome could not be confirmed.",
            )

        if response.status_code == 201:
            message_id = None
            try:
                body = response.json()
                raw_message_id = body.get("messageId") if isinstance(body, dict) else None
                if isinstance(raw_message_id, str) and raw_message_id:
                    message_id = raw_message_id[:255]
            except ValueError:
                pass
            return BrevoResult("accepted", message_id=message_id)

        retry_after = _parse_retry_after(
            response.headers.get("Retry-After"), self._clock()
        )
        if response.status_code in {408, 429} or 500 <= response.status_code <= 599:
            return BrevoResult(
                "retryable",
                failure_code=f"provider_http_{response.status_code}",
                message="The provider returned a retryable response.",
                retry_after_seconds=retry_after,
            )
        if 400 <= response.status_code <= 499:
            return BrevoResult(
                "terminal",
                failure_code=f"provider_http_{response.status_code}",
                message="The provider rejected the reminder request.",
            )
        return BrevoResult(
            "ambiguous",
            failure_code="provider_unexpected_response",
            message="The provider outcome could not be confirmed.",
        )


class SupabaseReminderStore:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                follow_redirects=False,
            )
        )

    async def _rpc(self, name: str, payload: dict[str, Any]) -> Any:
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise ReminderInfrastructureError("Reminder database access is unavailable.")
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
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ReminderInfrastructureError(
                "A reminder database operation failed."
            ) from exc

    async def claim_manual(
        self,
        *,
        user_id: UUID,
        action_item_id: UUID,
        idempotency_key: UUID,
        claim_token: UUID,
    ) -> dict[str, Any]:
        result = await self._rpc(
            "claim_manual_reminder",
            {
                "p_user_id": str(user_id),
                "p_action_item_id": str(action_item_id),
                "p_idempotency_key": str(idempotency_key),
                "p_claim_token": str(claim_token),
                "p_lease_seconds": self.settings.reminder_lease_seconds,
                "p_max_attempts": self.settings.reminder_max_attempts,
            },
        )
        if not isinstance(result, dict):
            raise ReminderInfrastructureError("The reminder claim response was invalid.")
        return result

    async def claim_scheduled(self, *, today: date) -> list[dict[str, Any]]:
        result = await self._rpc(
            "claim_scheduled_reminders",
            {
                "p_logical_date": today.isoformat(),
                "p_batch_size": self.settings.reminder_batch_size,
                "p_lease_seconds": self.settings.reminder_lease_seconds,
                "p_max_attempts": self.settings.reminder_max_attempts,
            },
        )
        if not isinstance(result, list) or any(
            not isinstance(item, dict) for item in result
        ):
            raise ReminderInfrastructureError("The scheduled claim response was invalid.")
        return result

    async def prepare(
        self,
        *,
        delivery_id: str,
        claim_token: str,
        sibling_group_id: UUID,
        expires_at: datetime,
        token_hashes: list[dict[str, str]],
    ) -> dict[str, Any]:
        result = await self._rpc(
            "prepare_reminder_delivery",
            {
                "p_delivery_id": delivery_id,
                "p_claim_token": claim_token,
                "p_sibling_group_id": str(sibling_group_id),
                "p_expires_at": expires_at.isoformat(),
                "p_token_hashes": token_hashes,
            },
        )
        if not isinstance(result, dict):
            raise ReminderInfrastructureError("The reminder preparation response was invalid.")
        return result

    async def mark_send_started(self, *, delivery_id: str, claim_token: str) -> bool:
        result = await self._rpc(
            "mark_reminder_send_started",
            {"p_delivery_id": delivery_id, "p_claim_token": claim_token},
        )
        return result is True

    async def finalize(
        self,
        *,
        delivery_id: str,
        claim_token: str,
        outcome: str,
        provider_message_id: str | None = None,
        failure_code: str | None = None,
        error_message: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> bool:
        result = await self._rpc(
            "finalize_reminder_delivery",
            {
                "p_delivery_id": delivery_id,
                "p_claim_token": claim_token,
                "p_outcome": outcome,
                "p_provider_message_id": provider_message_id,
                "p_failure_code": failure_code,
                "p_error_message": error_message,
                "p_next_attempt_at": (
                    next_attempt_at.isoformat() if next_attempt_at else None
                ),
            },
        )
        return result is True

    async def inspect_status_token(
        self, *, token_hash: str, now: datetime
    ) -> dict[str, Any]:
        result = await self._rpc(
            "inspect_action_status_token",
            {"p_token_hash": token_hash, "p_now": now.isoformat()},
        )
        return result if isinstance(result, dict) else {"valid": False}

    async def consume_status_token(
        self, *, token_hash: str, now: datetime
    ) -> dict[str, Any]:
        result = await self._rpc(
            "consume_action_status_token",
            {"p_token_hash": token_hash, "p_now": now.isoformat()},
        )
        return result if isinstance(result, dict) else {"updated": False}


def _status_buttons(links: dict[str, str]) -> str:
    labels = {
        "in_progress": "Set to In Progress",
        "completed": "Mark Completed",
    }
    colors = {"in_progress": "#3b82f6", "completed": "#16a34a"}
    buttons = []
    for target in ("in_progress", "completed"):
        url = links.get(target)
        if not url:
            continue
        buttons.append(
            '<a href="{}" style="display:inline-block;background-color:{};'
            'color:#fff;font-size:14px;font-weight:600;padding:12px 24px;'
            'margin:8px;text-decoration:none;border-radius:6px">{}</a>'.format(
                html.escape(url, quote=True), colors[target], labels[target]
            )
        )
    return "".join(buttons)


def build_reminder_email(
    prepared: dict[str, Any],
    links: dict[str, str],
    template: str | None,
    *,
    now: datetime,
) -> tuple[str, str]:
    reminder_type = prepared.get("reminder_type")
    copy = {
        "deadline": ("Action due today", "This action is due today."),
        "start_date": ("Action starts today", "This action is scheduled to start today."),
        "before_deadline": ("Action due tomorrow", "This action is due tomorrow."),
        "manual_notification": ("Action reminder", "A reminder was requested for this action."),
    }
    subject, message = copy.get(reminder_type, ("Action reminder", "This is an action reminder."))
    values = {
        "[REMINDER_SUBJECT]": html.escape(subject),
        "[PREHEADER_TEXT]": html.escape(message),
        "[ASSIGNEE_NAME]": html.escape(str(prepared.get("assignee") or "there")),
        "[REMINDER_MESSAGE_HTML]": html.escape(message),
        "[TASK_TITLE]": html.escape(str(prepared.get("title") or "Action item")),
        "[TASK_START_DATE]": html.escape(str(prepared.get("start_date") or "Not set")),
        "[TASK_DEADLINE]": html.escape(str(prepared.get("deadline") or "Not set")),
        "[STATUS_BUTTONS_HTML]": _status_buttons(links),
        "[CURRENT_YEAR]": str(now.year),
    }
    output = template or (
        "<!doctype html><html><body><p>Hi [ASSIGNEE_NAME],</p>"
        "<p>[REMINDER_MESSAGE_HTML]</p><p><strong>Task:</strong> [TASK_TITLE]</p>"
        "<p>[STATUS_BUTTONS_HTML]</p></body></html>"
    )
    for placeholder, value in values.items():
        output = output.replace(placeholder, value)
    return subject, output


class ReminderService:
    def __init__(
        self,
        settings: Settings,
        store: SupabaseReminderStore,
        provider: BrevoEmailAdapter,
        *,
        clock: Callable[[], datetime] = utc_now,
        jitter: Callable[[int], int] = secrets.randbelow,
    ) -> None:
        self.settings = settings
        self.store = store
        self.provider = provider
        self._clock = clock
        self._jitter = jitter

    @staticmethod
    def _public_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "delivery_id": result.get("delivery_id"),
            "status": result.get("status", "unknown"),
            "attempt_count": result.get("attempt_count"),
            "next_attempt_at": result.get("next_attempt_at"),
        }

    async def request_manual(
        self, *, user_id: UUID, action_item_id: UUID, idempotency_key: UUID
    ) -> dict[str, Any]:
        claim = await self.store.claim_manual(
            user_id=user_id,
            action_item_id=action_item_id,
            idempotency_key=idempotency_key,
            claim_token=uuid4(),
        )
        outcome = claim.get("outcome")
        if outcome == "not_found":
            return {"outcome": "not_found"}
        if outcome == "incompatible":
            return {"outcome": "incompatible"}
        if outcome == "ineligible":
            return {"outcome": "ineligible"}
        if outcome == "existing":
            return {"outcome": "existing", **self._public_result(claim)}
        if outcome != "claimed":
            raise ReminderInfrastructureError("The manual reminder claim was invalid.")
        delivered = await self._process_claim(claim)
        return {"outcome": "processed", **delivered}

    async def run_scheduled(self) -> dict[str, int]:
        today = logical_date(self._clock(), self.settings.reminder_timezone)
        claims = await self.store.claim_scheduled(today=today)
        counts = {"claimed": len(claims), "sent": 0, "failed": 0, "unknown": 0, "cancelled": 0}
        for claim in claims:
            result = await self._process_claim(claim)
            status = result["status"]
            if status in counts:
                counts[status] += 1
        return counts

    def _retry_delay(self, attempt_count: int, provider_delay: int | None) -> int:
        if provider_delay is not None:
            return min(MAX_RETRY_AFTER_SECONDS, max(1, provider_delay))
        base = min(
            MAX_RETRY_AFTER_SECONDS,
            self.settings.reminder_retry_base_seconds * (2 ** max(0, attempt_count - 1)),
        )
        jitter_limit = max(1, base // 4)
        return min(MAX_RETRY_AFTER_SECONDS, base + self._jitter(jitter_limit))

    async def _finalize(self, **kwargs: Any) -> None:
        if not await self.store.finalize(**kwargs):
            raise ReminderInfrastructureError("The reminder claim could not be finalized.")

    async def _process_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        delivery_id = str(claim.get("delivery_id") or "")
        claim_token = str(claim.get("claim_token") or "")
        attempt_count = int(claim.get("attempt_count") or 0)
        if not delivery_id or not claim_token or attempt_count <= 0:
            raise ReminderInfrastructureError("The reminder claim was malformed.")

        raw_tokens = {
            "in_progress": secrets.token_urlsafe(32),
            "completed": secrets.token_urlsafe(32),
        }
        token_hashes = [
            {
                "target_status": target,
                "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            }
            for target, token in raw_tokens.items()
        ]
        prepared = await self.store.prepare(
            delivery_id=delivery_id,
            claim_token=claim_token,
            sibling_group_id=uuid4(),
            expires_at=self._clock()
            + timedelta(hours=self.settings.action_status_link_ttl_hours),
            token_hashes=token_hashes,
        )
        if prepared.get("outcome") == "cancelled":
            return {"delivery_id": delivery_id, "status": "cancelled", "attempt_count": attempt_count}
        if prepared.get("outcome") != "prepared":
            raise ReminderInfrastructureError("The reminder claim was lost before delivery.")

        recipient = str(prepared.get("recipient_email") or "")
        try:
            recipient = validate_email(recipient, check_deliverability=False).normalized
        except EmailNotValidError:
            await self._finalize(
                delivery_id=delivery_id,
                claim_token=claim_token,
                outcome="failed",
                failure_code="invalid_recipient",
                error_message="The stored reminder recipient is invalid.",
            )
            return {"delivery_id": delivery_id, "status": "failed", "attempt_count": attempt_count}

        allowed_targets = {
            target for target in prepared.get("allowed_targets", [])
            if target in raw_tokens
        }
        links = {
            target: f"{self.settings.api_public_url}/action-status?{urlencode({'token': raw_tokens[target]})}"
            for target in allowed_targets
        }
        if not self.settings.api_public_url or not links:
            await self._finalize(
                delivery_id=delivery_id,
                claim_token=claim_token,
                outcome="failed",
                failure_code="status_links_unavailable",
                error_message="Secure status links are not configured.",
            )
            return {"delivery_id": delivery_id, "status": "failed", "attempt_count": attempt_count}

        subject, html_content = build_reminder_email(
            prepared,
            links,
            config.reminder_template,
            now=self._clock(),
        )
        if not await self.store.mark_send_started(
            delivery_id=delivery_id, claim_token=claim_token
        ):
            raise ReminderInfrastructureError("The reminder claim expired before delivery.")

        provider_result = await self.provider.send(
            recipient_email=recipient,
            recipient_name=str(prepared.get("assignee") or "Recipient"),
            subject=subject,
            html_content=html_content,
            delivery_id=delivery_id,
        )
        if provider_result.kind == "accepted":
            await self._finalize(
                delivery_id=delivery_id,
                claim_token=claim_token,
                outcome="sent",
                provider_message_id=provider_result.message_id,
            )
            logger.info("Reminder delivery accepted delivery_id=%s", delivery_id)
            return {"delivery_id": delivery_id, "status": "sent", "attempt_count": attempt_count}

        if provider_result.kind == "ambiguous":
            await self._finalize(
                delivery_id=delivery_id,
                claim_token=claim_token,
                outcome="unknown",
                failure_code=provider_result.failure_code,
                error_message=provider_result.message,
            )
            logger.warning("Reminder delivery outcome unknown delivery_id=%s", delivery_id)
            return {"delivery_id": delivery_id, "status": "unknown", "attempt_count": attempt_count}

        next_attempt_at = None
        failure_code = provider_result.failure_code
        if (
            provider_result.kind == "retryable"
            and attempt_count < self.settings.reminder_max_attempts
        ):
            next_attempt_at = self._clock() + timedelta(
                seconds=self._retry_delay(
                    attempt_count, provider_result.retry_after_seconds
                )
            )
        elif provider_result.kind == "retryable":
            failure_code = "attempts_exhausted"

        await self._finalize(
            delivery_id=delivery_id,
            claim_token=claim_token,
            outcome="failed",
            failure_code=failure_code,
            error_message=provider_result.message,
            next_attempt_at=next_attempt_at,
        )
        return {
            "delivery_id": delivery_id,
            "status": "failed",
            "attempt_count": attempt_count,
            "next_attempt_at": next_attempt_at.isoformat() if next_attempt_at else None,
        }

    async def inspect_status_token(self, raw_token: str) -> dict[str, Any]:
        return await self.store.inspect_status_token(
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            now=self._clock(),
        )

    async def consume_status_token(self, raw_token: str) -> dict[str, Any]:
        return await self.store.consume_status_token(
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            now=self._clock(),
        )


@lru_cache(maxsize=1)
def get_reminder_store() -> SupabaseReminderStore:
    return SupabaseReminderStore(get_settings())


@lru_cache(maxsize=1)
def get_brevo_adapter() -> BrevoEmailAdapter:
    return BrevoEmailAdapter(get_settings())


@lru_cache(maxsize=1)
def get_reminder_service() -> ReminderService:
    settings = get_settings()
    return ReminderService(settings, get_reminder_store(), get_brevo_adapter())


@lru_cache(maxsize=1)
def get_status_post_limiter() -> StatusPostRateLimiter:
    return StatusPostRateLimiter()
