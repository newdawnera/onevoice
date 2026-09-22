from dataclasses import replace

import pytest

import config
from conftest import configured_settings


def enabled_settings(**overrides):
    values = {
        "ai_enabled": True,
        "ai_provider": "groq",
        "groq_api_key": "test-only-secret",
        "ai_text_model": "openai/gpt-oss-20b",
        "ai_structured_model": "openai/gpt-oss-20b",
    }
    values.update(overrides)
    return replace(configured_settings(), **values)


def test_enabled_and_disabled_ai_configuration():
    enabled_settings().validate_for_startup()
    replace(configured_settings(), ai_enabled=False, ai_provider="groq").validate_for_startup()


@pytest.mark.parametrize(
    "overrides",
    [
        {"groq_api_key": ""},
        {"ai_text_model": ""},
        {"ai_structured_model": ""},
        {"ai_provider": "unknown"},
    ],
)
def test_enabled_ai_requires_complete_known_configuration(overrides):
    with pytest.raises(RuntimeError):
        enabled_settings(**overrides).validate_for_startup()


@pytest.mark.parametrize(
    ("field", "model"),
    [
        ("ai_text_model", "groq/compound"),
        ("ai_text_model", "whisper-large-v3"),
        ("ai_structured_model", "llama-3.3-70b-versatile"),
        ("ai_structured_model", "qwen/qwen3.8-27b"),
    ],
)
def test_unsuitable_or_non_strict_models_are_rejected(field, model):
    with pytest.raises(RuntimeError):
        enabled_settings(**{field: model}).validate_for_startup()


def test_provider_secret_is_excluded_from_repr():
    settings = enabled_settings()
    assert "test-only-secret" not in repr(settings)


@pytest.mark.parametrize(
    ("name", "value", "minimum", "maximum"),
    [
        ("AI_MAX_RETRIES", "3", 0, 2),
        ("AI_MAX_CONCURRENT_REQUESTS", "0", 1, 16),
        ("AI_CONNECT_TIMEOUT_SECONDS", "0.1", 0.5, 30.0),
    ],
)
def test_ai_numeric_bounds(monkeypatch, name, value, minimum, maximum):
    monkeypatch.setenv(name, value)
    function = config._bounded_float if "." in value else config._bounded_int
    with pytest.raises(RuntimeError):
        function(name, minimum, minimum, maximum)


def test_production_rejects_arbitrary_groq_base_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GROQ_BASE_URL", "https://attacker.invalid")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="GROQ_BASE_URL"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()
