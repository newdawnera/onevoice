from types import SimpleNamespace

import pytest

from conftest import configured_settings
from scripts import provision_qstash_schedule


def schedule_settings(**overrides):
    values = {
        "api_public_url": "https://api.example.com",
        "qstash_current_signing_key": "current-signing-key",
        "qstash_next_signing_key": "next-signing-key",
        "qstash_token": "server-only-management-token",
        "qstash_schedule_id": "ally-reminders-production",
        "reminder_cron": "CRON_TZ=Europe/London 0 6 * * *",
    }
    values.update(overrides)
    return configured_settings(**values)


class FakeScheduleApi:
    def __init__(self, schedules):
        self.schedules = schedules
        self.created = []

    def list(self):
        return self.schedules

    def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs["schedule_id"]


def install_client(monkeypatch, schedules):
    api = FakeScheduleApi(schedules)
    monkeypatch.setattr(
        provision_qstash_schedule,
        "QStash",
        lambda token: SimpleNamespace(schedule=api),
    )
    monkeypatch.setattr(
        provision_qstash_schedule, "get_settings", schedule_settings
    )
    return api


def test_compatible_stable_schedule_is_reused_without_a_write(monkeypatch):
    api = install_client(
        monkeypatch,
        [
            SimpleNamespace(
                schedule_id="ally-reminders-production",
                destination="https://api.example.com/internal/reminders/run",
                cron="CRON_TZ=Europe/London 0 6 * * *",
                method="POST",
                body='{"version":1}',
            )
        ],
    )
    assert provision_qstash_schedule.main() == 0
    assert api.created == []


def test_duplicate_destination_under_another_id_is_refused(monkeypatch):
    install_client(
        monkeypatch,
        [
            SimpleNamespace(
                schedule_id="old-reminder-schedule",
                destination="https://api.example.com/internal/reminders/run",
                cron="0 7 * * *",
                method="POST",
                body='{"version":1}',
            )
        ],
    )
    with pytest.raises(RuntimeError, match="another ID"):
        provision_qstash_schedule.main()


def test_new_schedule_uses_fixed_post_body_and_stable_id(monkeypatch):
    api = install_client(monkeypatch, [])
    assert provision_qstash_schedule.main() == 0
    assert api.created == [
        {
            "destination": "https://api.example.com/internal/reminders/run",
            "cron": "CRON_TZ=Europe/London 0 6 * * *",
            "body": '{"version":1}',
            "content_type": "application/json",
            "method": "POST",
            "retries": 2,
            "schedule_id": "ally-reminders-production",
            "label": "ally-reminders-v1",
        }
    ]


def test_missing_management_configuration_fails_before_network(monkeypatch):
    called = False

    def unexpected_client(_token):
        nonlocal called
        called = True
        raise AssertionError("QStash must not be called")

    monkeypatch.setattr(provision_qstash_schedule, "QStash", unexpected_client)
    monkeypatch.setattr(
        provision_qstash_schedule,
        "get_settings",
        lambda: schedule_settings(qstash_token="", reminder_cron=""),
    )
    with pytest.raises(RuntimeError, match="QSTASH_TOKEN, REMINDER_CRON"):
        provision_qstash_schedule.main()
    assert called is False


def test_cron_timezone_must_match_application_timezone(monkeypatch):
    api = install_client(monkeypatch, [])
    monkeypatch.setattr(
        provision_qstash_schedule,
        "get_settings",
        lambda: schedule_settings(reminder_cron="0 6 * * *"),
    )
    with pytest.raises(RuntimeError, match="CRON_TZ=Europe/London"):
        provision_qstash_schedule.main()
    assert api.created == []
