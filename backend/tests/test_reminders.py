from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

import reminders
from auth import AuthenticatedUser, get_current_user
from conftest import configured_settings
from main import app
from rate_limit import REMINDER_RATE_LIMIT
from reminders import (
    BrevoEmailAdapter,
    BrevoResult,
    ReminderService,
    get_reminder_service,
    get_status_post_limiter,
    logical_date,
    scheduled_reminder_type,
)


NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
USER = AuthenticatedUser(
    id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    email="owner@example.com",
    role="authenticated",
    session_id=None,
)
ACTION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
KEY = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CLAIM = {
    "outcome": "claimed",
    "delivery_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    "claim_token": "ffffffff-ffff-4fff-8fff-ffffffffffff",
    "attempt_count": 1,
}


class FakeStore:
    def __init__(self, claim=None, prepared=None):
        self.claim = claim or dict(CLAIM)
        self.prepared = prepared or {
            "outcome": "prepared",
            "delivery_id": CLAIM["delivery_id"],
            "reminder_type": "manual_notification",
            "attempt_count": 1,
            "title": '<script>alert("x")</script>',
            "assignee": "Alice & Bob",
            "recipient_email": "recipient@example.com",
            "start_date": "2026-09-21",
            "deadline": "2026-09-22",
            "current_status": "not_started",
            "allowed_targets": ["in_progress", "completed"],
        }
        self.claim_calls = []
        self.prepare_calls = []
        self.finalize_calls = []
        self.started = []
        self.inspected = []
        self.consumed = []

    async def claim_manual(self, **kwargs):
        self.claim_calls.append(kwargs)
        return self.claim

    async def claim_scheduled(self, *, today):
        self.scheduled_today = today
        return [self.claim] if self.claim.get("outcome") == "claimed" else []

    async def prepare(self, **kwargs):
        self.prepare_calls.append(kwargs)
        return self.prepared

    async def mark_send_started(self, **kwargs):
        self.started.append(kwargs)
        return True

    async def finalize(self, **kwargs):
        self.finalize_calls.append(kwargs)
        return True

    async def inspect_status_token(self, **kwargs):
        self.inspected.append(kwargs)
        return {"valid": True, "target_status": "completed"}

    async def consume_status_token(self, **kwargs):
        self.consumed.append(kwargs)
        return {"updated": True, "target_status": "completed"}


class FakeProvider:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def send(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def settings(**overrides):
    values = {
        "api_public_url": "https://api.example.com",
        "brevo_api_key": "server-only-test-key",
        "sender_email": "sender@example.com",
        "sender_name": "Ally",
    }
    values.update(overrides)
    return configured_settings(**values)


@pytest.mark.asyncio
async def test_manual_reminder_uses_server_claim_and_stores_only_hashes(monkeypatch, caplog):
    tokens = iter(["A" * 43, "B" * 43])
    monkeypatch.setattr(reminders.secrets, "token_urlsafe", lambda _size: next(tokens))
    store = FakeStore()
    provider = FakeProvider(BrevoResult("accepted", message_id="provider-id"))
    service = ReminderService(settings(), store, provider, clock=lambda: NOW)

    result = await service.request_manual(
        user_id=USER.id, action_item_id=ACTION_ID, idempotency_key=KEY
    )

    assert result["status"] == "sent"
    assert store.claim_calls[0]["user_id"] == USER.id
    assert store.claim_calls[0]["action_item_id"] == ACTION_ID
    assert store.claim_calls[0]["idempotency_key"] == KEY
    hashes = store.prepare_calls[0]["token_hashes"]
    assert all(len(item["token_hash"]) == 64 for item in hashes)
    assert all("A" * 43 not in item["token_hash"] for item in hashes)
    assert "<script>" not in provider.calls[0]["html_content"]
    assert "&lt;script&gt;" in provider.calls[0]["html_content"]
    assert store.started
    assert store.finalize_calls[-1]["outcome"] == "sent"
    assert store.finalize_calls[-1]["provider_message_id"] == "provider-id"
    assert "A" * 43 not in caplog.text
    assert "B" * 43 not in caplog.text


@pytest.mark.asyncio
async def test_duplicate_manual_claim_returns_existing_without_sending():
    store = FakeStore(
        claim={
            "outcome": "existing",
            "delivery_id": CLAIM["delivery_id"],
            "status": "sent",
            "attempt_count": 1,
            "next_attempt_at": None,
        }
    )
    provider = FakeProvider(BrevoResult("accepted"))
    service = ReminderService(settings(), store, provider, clock=lambda: NOW)
    result = await service.request_manual(
        user_id=USER.id, action_item_id=ACTION_ID, idempotency_key=KEY
    )
    assert result["status"] == "sent"
    assert provider.calls == []


@pytest.mark.asyncio
async def test_retryable_failure_uses_bounded_backoff_and_stops_at_max_attempts():
    store = FakeStore()
    provider = FakeProvider(
        BrevoResult(
            "retryable",
            failure_code="provider_http_429",
            message="retryable",
            retry_after_seconds=120,
        )
    )
    service = ReminderService(
        settings(reminder_max_attempts=4),
        store,
        provider,
        clock=lambda: NOW,
        jitter=lambda _limit: 0,
    )
    result = await service._process_claim(dict(CLAIM))
    assert result["status"] == "failed"
    assert store.finalize_calls[-1]["next_attempt_at"] == NOW + timedelta(seconds=120)

    store.finalize_calls.clear()
    exhausted = dict(CLAIM, attempt_count=4)
    await service._process_claim(exhausted)
    assert store.finalize_calls[-1]["failure_code"] == "attempts_exhausted"
    assert store.finalize_calls[-1]["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_ambiguous_provider_outcome_is_unknown_and_not_retried():
    store = FakeStore()
    provider = FakeProvider(
        BrevoResult(
            "ambiguous",
            failure_code="provider_timeout_unknown",
            message="unknown",
        )
    )
    service = ReminderService(settings(), store, provider, clock=lambda: NOW)
    result = await service._process_claim(dict(CLAIM))
    assert result["status"] == "unknown"
    assert store.finalize_calls[-1]["outcome"] == "unknown"
    assert store.finalize_calls[-1].get("next_attempt_at") is None


@pytest.mark.asyncio
async def test_invalid_recipient_fails_without_contacting_provider():
    store = FakeStore(prepared={**FakeStore().prepared, "recipient_email": "invalid"})
    provider = FakeProvider(BrevoResult("accepted"))
    service = ReminderService(settings(), store, provider, clock=lambda: NOW)
    result = await service._process_claim(dict(CLAIM))
    assert result["status"] == "failed"
    assert provider.calls == []
    assert store.finalize_calls[-1]["failure_code"] == "invalid_recipient"


@pytest.mark.asyncio
async def test_status_token_helpers_hash_raw_tokens_before_database_calls():
    store = FakeStore()
    service = ReminderService(
        settings(), store, FakeProvider(BrevoResult("accepted")), clock=lambda: NOW
    )
    raw = "Z" * 43
    await service.inspect_status_token(raw)
    await service.consume_status_token(raw)
    expected = reminders.hashlib.sha256(raw.encode()).hexdigest()
    assert store.inspected[0]["token_hash"] == expected
    assert store.consumed[0]["token_hash"] == expected
    assert raw not in str(store.inspected + store.consumed)


def _adapter_with(handler):
    transport = httpx.MockTransport(handler)
    return BrevoEmailAdapter(
        settings(),
        client_factory=lambda: httpx.AsyncClient(transport=transport),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "kind"),
    [(201, "accepted"), (400, "terminal"), (429, "retryable"), (503, "retryable")],
)
async def test_brevo_http_outcomes_are_classified_without_raw_provider_errors(
    status_code, kind
):
    def handler(_request):
        body = {"messageId": "provider-message"} if status_code == 201 else {"code": "secret raw detail"}
        return httpx.Response(
            status_code,
            json=body,
            headers={"Retry-After": "30"} if status_code == 429 else {},
        )

    result = await _adapter_with(handler).send(
        recipient_email="recipient@example.com",
        recipient_name="Recipient",
        subject="Reminder",
        html_content="<p>Reminder</p>",
        delivery_id=CLAIM["delivery_id"],
    )
    assert result.kind == kind
    assert "secret raw detail" not in str(result)
    if status_code == 201:
        assert result.message_id == "provider-message"
    if status_code == 429:
        assert result.retry_after_seconds == 30


@pytest.mark.asyncio
async def test_brevo_connect_failure_is_retryable_but_read_timeout_is_ambiguous():
    def connect_failure(request):
        raise httpx.ConnectError("offline", request=request)

    def read_timeout(request):
        raise httpx.ReadTimeout("unknown after send", request=request)

    common = {
        "recipient_email": "recipient@example.com",
        "recipient_name": "Recipient",
        "subject": "Reminder",
        "html_content": "<p>Reminder</p>",
        "delivery_id": CLAIM["delivery_id"],
    }
    assert (await _adapter_with(connect_failure).send(**common)).kind == "retryable"
    assert (await _adapter_with(read_timeout).send(**common)).kind == "ambiguous"


def test_schedule_precedence_boundaries_and_dst_use_configured_timezone():
    today = date(2026, 9, 21)
    assert scheduled_reminder_type(
        {"status": "not_started", "deadline": today, "start_date": today}, today
    ) == "deadline"
    assert scheduled_reminder_type(
        {"status": "not_started", "deadline": today + timedelta(days=3), "start_date": today}, today
    ) == "start_date"
    assert scheduled_reminder_type(
        {"status": "not_started", "deadline": today + timedelta(days=1)}, today
    ) == "before_deadline"
    assert scheduled_reminder_type(
        {"status": "completed", "deadline": today}, today
    ) is None

    before_fallback = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    after_fallback = datetime(2026, 10, 25, 2, 30, tzinfo=timezone.utc)
    assert logical_date(before_fallback, "Europe/London") == date(2026, 10, 25)
    assert logical_date(after_fallback, "Europe/London") == date(2026, 10, 25)


class RouteWorkflow:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def request_manual(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    async def inspect_status_token(self, raw):
        self.calls.append(("inspect", raw))
        return {"valid": True, "target_status": "completed"}

    async def consume_status_token(self, raw):
        self.calls.append(("consume", raw))
        return {"updated": True}


def test_manual_route_derives_user_and_returns_generic_cross_user_not_found():
    workflow = RouteWorkflow({"outcome": "not_found"})
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[REMINDER_RATE_LIMIT] = lambda: None
    app.dependency_overrides[get_reminder_service] = lambda: workflow
    client = TestClient(app)
    try:
        response = client.post(
            f"/action-items/{ACTION_ID}/reminders",
            headers={"Idempotency-Key": str(KEY)},
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Action item not found."}
        assert workflow.calls[0]["user_id"] == USER.id
        assert workflow.calls[0]["action_item_id"] == ACTION_ID
        assert workflow.calls[0]["idempotency_key"] == KEY
    finally:
        app.dependency_overrides.clear()


def test_status_get_does_not_consume_and_post_redirect_is_token_free():
    token = "T" * 43
    workflow = RouteWorkflow({})
    app.dependency_overrides[get_reminder_service] = lambda: workflow
    app.dependency_overrides[get_status_post_limiter] = lambda: reminders.StatusPostRateLimiter()
    client = TestClient(app, follow_redirects=False)
    try:
        page = client.get(f"/action-status?token={token}")
        assert page.status_code == 200
        assert workflow.calls == [("inspect", token)]
        assert "third-party" not in page.text
        assert "<script" not in page.text
        assert page.headers["Cache-Control"].startswith("no-store")
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert "form-action 'self'" in page.headers["Content-Security-Policy"]
        assert page.headers["Referrer-Policy"] == "no-referrer"
        assert page.headers["X-Content-Type-Options"] == "nosniff"
        assert "https://" not in page.text

        result = client.post(
            "/action-status",
            data={"token": token},
            headers={"Origin": "https://api.example.com"},
        )
        assert result.status_code == 303
        assert result.headers["location"] == "/action-status/result?outcome=updated"
        assert token not in result.headers["location"]
        assert workflow.calls[-1] == ("consume", token)

        clean = client.get(result.headers["location"])
        assert clean.status_code == 200
        assert token not in clean.text
        assert "https://" not in clean.text
        assert clean.headers["Cache-Control"].startswith("no-store")
        assert "frame-ancestors 'none'" in clean.headers["Content-Security-Policy"]
    finally:
        app.dependency_overrides.clear()


def test_status_routes_reject_malformed_oversized_and_cross_origin_posts():
    token = "T" * 43
    workflow = RouteWorkflow({})
    app.dependency_overrides[get_reminder_service] = lambda: workflow
    app.dependency_overrides[get_status_post_limiter] = lambda: reminders.StatusPostRateLimiter()
    client = TestClient(app, follow_redirects=False)
    try:
        malformed = client.get("/action-status?token=short")
        assert malformed.status_code == 200
        assert workflow.calls == []

        malformed_post = client.post(
            "/action-status",
            json={"token": "short"},
            headers={"Origin": "https://api.example.com"},
        )
        assert malformed_post.status_code == 303
        assert malformed_post.headers["location"].endswith("outcome=unavailable")
        assert workflow.calls == []

        oversized = client.post(
            "/action-status",
            content=("token=" + "T" * 3000),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://api.example.com",
            },
        )
        assert oversized.status_code == 413

        cross_origin = client.post(
            "/action-status",
            data={"token": token},
            headers={
                "Origin": "https://attacker.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert cross_origin.status_code == 403
        assert workflow.calls == []
    finally:
        app.dependency_overrides.clear()


def test_status_get_redacts_token_before_the_asgi_access_logger():
    token = "T" * 43
    workflow = RouteWorkflow({})
    captured_query_strings = []

    async def access_log_probe(scope, receive, send):
        await app(scope, receive, send)
        captured_query_strings.append(scope.get("query_string"))

    app.dependency_overrides[get_reminder_service] = lambda: workflow
    client = TestClient(access_log_probe)
    try:
        response = client.get(f"/action-status?token={token}")
        assert response.status_code == 200
        assert captured_query_strings == [b"token=%5BREDACTED%5D"]
        assert token.encode() not in captured_query_strings[0]
    finally:
        app.dependency_overrides.clear()
