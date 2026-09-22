"""Create Ally's one stable QStash schedule after inspecting existing schedules."""

from __future__ import annotations

import sys
from pathlib import Path

from qstash import QStash


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    required = {
        "API_PUBLIC_URL": settings.api_public_url,
        "QSTASH_CURRENT_SIGNING_KEY": settings.qstash_current_signing_key,
        "QSTASH_NEXT_SIGNING_KEY": settings.qstash_next_signing_key,
        "QSTASH_TOKEN": settings.qstash_token,
        "QSTASH_SCHEDULE_ID": settings.qstash_schedule_id,
        "REMINDER_CRON": settings.reminder_cron,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Schedule provisioning is disabled until these values are set: "
            + ", ".join(missing)
        )
    timezone_prefix = f"CRON_TZ={settings.reminder_timezone} "
    if not settings.reminder_cron.startswith(timezone_prefix):
        raise RuntimeError(
            "REMINDER_CRON must begin with the configured REMINDER_TIMEZONE "
            f"prefix: {timezone_prefix}"
        )

    destination = settings.reminder_run_url
    expected_body = '{"version":1}'
    client = QStash(settings.qstash_token)
    schedules = client.schedule.list()
    same_id = next(
        (
            schedule
            for schedule in schedules
            if schedule.schedule_id == settings.qstash_schedule_id
        ),
        None,
    )
    if same_id:
        compatible = (
            same_id.destination == destination
            and same_id.cron == settings.reminder_cron
            and same_id.method == "POST"
            and same_id.body == expected_body
        )
        if not compatible:
            raise RuntimeError(
                "The configured QStash schedule ID already exists with different "
                "settings. Inspect it in Upstash before changing or replacing it."
            )
        print(f"Compatible schedule already exists: {same_id.schedule_id}")
        return 0

    duplicates = [
        schedule.schedule_id
        for schedule in schedules
        if schedule.destination == destination
    ]
    if duplicates:
        raise RuntimeError(
            "A schedule already targets the reminder endpoint under another ID: "
            + ", ".join(duplicates)
            + ". Reuse or deliberately retire it before provisioning."
        )

    schedule_id = client.schedule.create(
        destination=destination,
        cron=settings.reminder_cron,
        body=expected_body,
        content_type="application/json",
        method="POST",
        retries=2,
        schedule_id=settings.qstash_schedule_id,
        label="ally-reminders-v1",
    )
    print(f"Created QStash schedule: {schedule_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
