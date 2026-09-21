from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auth import AuthenticatedUser, get_current_user
from config import get_settings
from conftest import configured_settings
from main import app
from pyModels import ResultRequest
from rate_limit import REMINDER_RATE_LIMIT


USER = AuthenticatedUser(
    id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    email="user@example.com",
    role="authenticated",
    session_id=None,
)


client = TestClient(app)


def test_public_health_endpoint_remains_public():
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("post", "/transcribe/", {"files": {"file": ("a.webm", b"x", "audio/webm")}}),
        ("post", "/upload-document/", {"files": {"file": ("a.txt", b"x", "text/plain")}}),
        ("post", "/generate-result/", {"json": {"text": "hello"}}),
        (
            "post",
            "/ai-helper",
            {"json": {"task_type": "autocomplete", "context": {"text": "hello"}}},
        ),
        (
            "post",
            "/send-email/",
            {"data": {"recipients": "a@example.com", "subject": "x", "html_body": "x"}},
        ),
        ("post", "/send-welcome-email", {}),
        (
            "post",
            "/action-items/cccccccc-cccc-4ccc-8ccc-cccccccccccc/reminders",
            {"headers": {"Idempotency-Key": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"}},
        ),
    ],
)
def test_user_facing_routes_reject_missing_auth(method, path, kwargs):
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401


def test_scheduler_fails_closed_when_signing_configuration_is_missing():
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    try:
        response = client.post(
            "/internal/reminders/run",
            content="{}",
            headers={"Upstash-Signature": "not-a-token"},
        )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_scheduler_has_no_static_bearer_fallback_and_legacy_get_is_removed():
    response = client.post(
        "/internal/reminders/run",
        content="{}",
        headers={"Authorization": "Bearer old-secret"},
    )
    assert response.status_code == 401
    assert client.get("/send-task-reminders").status_code == 410


def test_legacy_status_route_is_disabled_and_firebase_config_is_removed():
    assert client.get("/update-task-status?userId=x&taskId=y&newStatus=z").status_code == 410
    assert client.get("/firebase-config").status_code == 404


def test_caller_supplied_user_id_is_rejected_by_models():
    with pytest.raises(ValidationError):
        ResultRequest.model_validate({"text": "hello", "userId": "someone-else"})


def test_manual_reminder_rejects_browser_owned_task_data():
    app.dependency_overrides[get_current_user] = lambda: USER
    app.dependency_overrides[REMINDER_RATE_LIMIT] = lambda: None
    try:
        response = client.post(
            "/action-items/cccccccc-cccc-4ccc-8ccc-cccccccccccc/reminders",
            headers={"Idempotency-Key": "dddddddd-dddd-4ddd-8ddd-dddddddddddd"},
            json={"userId": str(USER.id), "recipient": "victim@example.com"},
        )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(REMINDER_RATE_LIMIT, None)
