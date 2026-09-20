from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from auth import AuthenticatedUser, get_current_user
from config import get_settings
from conftest import configured_settings
from main import app
from pyModels import ManualReminderRequest, ResultRequest


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
            "/send-manual-reminder",
            {"json": {"action_item_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"}},
        ),
    ],
)
def test_user_facing_routes_reject_missing_auth(method, path, kwargs):
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401


def test_scheduler_fails_closed_when_secret_is_missing():
    app.dependency_overrides[get_settings] = lambda: configured_settings(
        scheduler_secret=""
    )
    try:
        response = client.get("/send-task-reminders")
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_scheduler_rejects_invalid_secret():
    app.dependency_overrides[get_settings] = lambda: configured_settings(
        scheduler_secret="test-scheduler-secret"
    )
    try:
        assert client.get("/send-task-reminders").status_code == 401
        response = client.get(
            "/send-task-reminders",
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_legacy_status_and_firebase_config_routes_are_disabled():
    assert client.get("/update-task-status?userId=x&taskId=y&newStatus=z").status_code == 410
    assert client.get("/firebase-config").status_code == 410


def test_caller_supplied_user_id_is_rejected_by_models():
    with pytest.raises(ValidationError):
        ResultRequest.model_validate({"text": "hello", "userId": "someone-else"})


def test_manual_reminder_rejects_browser_task_data():
    with pytest.raises(ValidationError):
        ManualReminderRequest.model_validate(
            {
                "userId": "someone-else",
                "task": {"id": "x", "assigneeEmail": "victim@example.com"},
            }
        )


def test_manual_reminder_is_authenticated_but_disabled():
    app.dependency_overrides[get_current_user] = lambda: USER
    try:
        response = client.post(
            "/send-manual-reminder",
            json={"action_item_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"},
        )
        assert response.status_code == 503
    finally:
        app.dependency_overrides.pop(get_current_user, None)
