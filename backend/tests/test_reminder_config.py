from dataclasses import replace

import pytest

import config
from conftest import configured_settings


@pytest.mark.parametrize(
    "public_url",
    [
        "https://api.example.com/unexpected/path",
        "https://user:password@api.example.com",
        "https://api.example.com?destination=other",
        "https://api.example.com#fragment",
    ],
)
def test_public_api_url_must_be_an_origin_without_credentials(public_url):
    settings = configured_settings(api_public_url=public_url)
    with pytest.raises(RuntimeError, match="origin-only"):
        settings.validate_for_startup()


def test_public_api_url_must_use_https_in_production():
    settings = configured_settings(
        app_env="production",
        api_public_url="http://api.example.com",
        cors_origins=("https://app.example.com",),
    )
    with pytest.raises(RuntimeError, match="HTTPS"):
        settings.validate_for_startup()


def test_qstash_signing_keys_must_be_configured_as_a_rotation_pair():
    settings = configured_settings(
        qstash_current_signing_key="current-only",
        qstash_next_signing_key="",
    )
    with pytest.raises(RuntimeError, match="Both QStash signing keys"):
        settings.validate_for_startup()


def test_reminder_timezone_and_numeric_bounds_are_validated(monkeypatch):
    with pytest.raises(RuntimeError, match="IANA timezone"):
        replace(
            configured_settings(), reminder_timezone="Mars/Olympus_Mons"
        ).validate_for_startup()

    monkeypatch.setenv("REMINDER_BATCH_SIZE", "0")
    with pytest.raises(RuntimeError, match="greater than zero"):
        config._bounded_int("REMINDER_BATCH_SIZE", 50, 1, 200)

    monkeypatch.setenv("REMINDER_BATCH_SIZE", "201")
    with pytest.raises(RuntimeError, match="between 1 and 200"):
        config._bounded_int("REMINDER_BATCH_SIZE", 50, 1, 200)
