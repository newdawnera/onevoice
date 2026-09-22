"""Application configuration.

Only explicitly public Supabase values may be shared with the browser. Secret
values are held on this settings object and are deliberately excluded from its
representation and from startup logging.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger(__name__)

welcome_template = None
email_template = None
reminder_template = None


# This registry is deliberately small and explicit. Preview, audio, safety,
# Compound, and tool-system identifiers are not valid application models.
AI_MODEL_CAPABILITIES = {
    "openai/gpt-oss-20b": frozenset({"text", "strict_structured"}),
    "openai/gpt-oss-120b": frozenset({"text", "strict_structured"}),
    "llama-3.3-70b-versatile": frozenset({"text"}),
    "llama-3.1-8b-instant": frozenset({"text"}),
}


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if minimum > 0 and value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            part.strip()
            for part in os.getenv(name, default).split(",")
            if part.strip()
        )
    )


@dataclass(frozen=True)
class Settings:
    app_env: str
    supabase_url: str
    supabase_publishable_key: str = field(repr=False)
    supabase_service_role_key: str = field(repr=False)
    supabase_jwt_issuer: str
    supabase_jwt_audience: str
    supabase_jwt_verification_mode: str
    supabase_jwt_algorithms: tuple[str, ...]
    cors_origins: tuple[str, ...]
    api_public_url: str
    qstash_current_signing_key: str = field(repr=False)
    qstash_next_signing_key: str = field(repr=False)
    qstash_token: str = field(repr=False)
    qstash_schedule_id: str
    reminder_cron: str
    reminder_timezone: str
    reminder_batch_size: int
    reminder_max_attempts: int
    reminder_lease_seconds: int
    reminder_retry_base_seconds: int
    action_status_link_ttl_hours: int
    brevo_api_key: str = field(repr=False)
    sender_email: str
    sender_name: str
    auth_jwks_cache_seconds: int
    max_document_upload_mb: int
    max_audio_upload_mb: int
    max_email_attachment_mb: int
    max_email_total_attachment_mb: int
    max_email_recipients: int
    max_email_subject_chars: int
    max_email_html_chars: int
    rate_limit_ai_per_minute: int
    rate_limit_ai_per_day: int
    rate_limit_transcription_per_minute: int
    rate_limit_transcription_per_day: int
    rate_limit_document_upload_per_minute: int
    rate_limit_email_per_hour: int
    rate_limit_welcome_email_per_day: int
    rate_limit_reminder_per_hour: int
    ai_enabled: bool
    ai_provider: str
    groq_api_key: str = field(repr=False)
    ai_text_model: str = ""
    ai_structured_model: str = ""
    ai_connect_timeout_seconds: float = 5.0
    ai_read_timeout_seconds: float = 45.0
    ai_write_timeout_seconds: float = 10.0
    ai_pool_timeout_seconds: float = 5.0
    ai_max_retries: int = 1
    ai_max_input_chars: int = 100_000
    ai_max_estimated_input_tokens: int = 50_000
    ai_max_output_tokens: int = 4_096
    ai_chunk_chars: int = 24_000
    ai_max_chunks: int = 5
    ai_max_action_items: int = 20
    ai_max_topics: int = 30
    ai_max_concurrent_requests: int = 4
    ai_generation_lease_seconds: int = 300
    ai_prompt_version: str = "phase2e-v1"
    rate_limit_autocomplete_per_minute: int = 20
    rate_limit_autocomplete_per_day: int = 500

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def auth_user_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/user"

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"

    @property
    def reminder_run_url(self) -> str:
        return f"{self.api_public_url.rstrip('/')}/internal/reminders/run"

    @property
    def qstash_verification_configured(self) -> bool:
        return bool(
            self.api_public_url
            and self.qstash_current_signing_key
            and self.qstash_next_signing_key
        )

    def validate_for_startup(self) -> None:
        if self.app_env not in {"development", "test", "production"}:
            raise RuntimeError("APP_ENV must be development, test, or production.")
        if self.supabase_jwt_verification_mode not in {
            "auto",
            "asymmetric",
            "legacy",
        }:
            raise RuntimeError("SUPABASE_JWT_VERIFICATION_MODE is invalid.")
        if not self.supabase_jwt_algorithms or any(
            algorithm not in {"ES256", "RS256"}
            for algorithm in self.supabase_jwt_algorithms
        ):
            raise RuntimeError(
                "SUPABASE_JWT_ALGORITHMS must contain only ES256 and/or RS256."
            )

        if self.supabase_url:
            parsed = urlparse(self.supabase_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RuntimeError("SUPABASE_URL must be an absolute HTTP(S) URL.")
            if self.is_production and parsed.scheme != "https":
                raise RuntimeError("SUPABASE_URL must use HTTPS in production.")

        if self.api_public_url:
            parsed = urlparse(self.api_public_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise RuntimeError(
                    "API_PUBLIC_URL must be an origin-only absolute HTTP(S) URL."
                )
            if self.is_production and parsed.scheme != "https":
                raise RuntimeError("API_PUBLIC_URL must use HTTPS in production.")

        try:
            ZoneInfo(self.reminder_timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise RuntimeError("REMINDER_TIMEZONE is not a valid IANA timezone.") from exc

        if bool(self.qstash_current_signing_key) != bool(
            self.qstash_next_signing_key
        ):
            raise RuntimeError(
                "Both QStash signing keys must be configured together."
            )

        if self.ai_provider not in {"", "groq"}:
            raise RuntimeError("AI_PROVIDER must be groq when configured.")
        if self.ai_enabled:
            required_ai = {
                "AI_PROVIDER": self.ai_provider,
                "GROQ_API_KEY": self.groq_api_key,
                "AI_TEXT_MODEL": self.ai_text_model,
                "AI_STRUCTURED_MODEL": self.ai_structured_model,
                "AI_PROMPT_VERSION": self.ai_prompt_version,
            }
            if any(not value for value in required_ai.values()):
                raise RuntimeError("Enabled AI configuration is incomplete.")
            text_capabilities = AI_MODEL_CAPABILITIES.get(self.ai_text_model)
            structured_capabilities = AI_MODEL_CAPABILITIES.get(
                self.ai_structured_model
            )
            if not text_capabilities or "text" not in text_capabilities:
                raise RuntimeError("AI_TEXT_MODEL is not an approved production text model.")
            if (
                not structured_capabilities
                or "strict_structured" not in structured_capabilities
            ):
                raise RuntimeError(
                    "AI_STRUCTURED_MODEL must support approved strict structured output."
                )
            for model in (self.ai_text_model, self.ai_structured_model):
                lowered = model.lower()
                if any(
                    marker in lowered
                    for marker in (
                        "compound",
                        "whisper",
                        "audio",
                        "preview",
                        "guard",
                    )
                ):
                    raise RuntimeError("The configured AI model is unsuitable.")
        if self.ai_chunk_chars > self.ai_max_input_chars:
            raise RuntimeError("AI_CHUNK_CHARS cannot exceed AI_MAX_INPUT_CHARS.")
        if self.ai_chunk_chars * self.ai_max_chunks < self.ai_max_input_chars:
            raise RuntimeError(
                "AI chunk capacity must cover AI_MAX_INPUT_CHARS without truncation."
            )
        if not self.ai_prompt_version or len(self.ai_prompt_version) > 50:
            raise RuntimeError("AI_PROMPT_VERSION must contain at most 50 characters.")

        if not self.is_production:
            return

        required = {
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_PUBLISHABLE_KEY": self.supabase_publishable_key,
            "SUPABASE_SERVICE_ROLE_KEY": self.supabase_service_role_key,
            "SUPABASE_JWT_ISSUER": self.supabase_jwt_issuer,
        }
        if any(
            not value or "placeholder" in value.lower()
            for value in required.values()
        ):
            raise RuntimeError(
                "Production Supabase authentication configuration is incomplete."
            )
        if not any(origin.startswith("https://") for origin in self.cors_origins):
            raise RuntimeError(
                "Production requires at least one explicit HTTPS CORS origin."
            )
        if any(origin == "*" for origin in self.cors_origins):
            raise RuntimeError("Wildcard CORS origins are not allowed.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    issuer = os.getenv("SUPABASE_JWT_ISSUER", "").strip()
    if not issuer and supabase_url:
        issuer = f"{supabase_url}/auth/v1"

    settings = Settings(
        app_env=os.getenv("APP_ENV", "development").strip().lower(),
        supabase_url=supabase_url,
        supabase_publishable_key=os.getenv(
            "SUPABASE_PUBLISHABLE_KEY", ""
        ).strip(),
        supabase_service_role_key=os.getenv(
            "SUPABASE_SERVICE_ROLE_KEY", ""
        ).strip(),
        supabase_jwt_issuer=issuer,
        supabase_jwt_audience=os.getenv(
            "SUPABASE_JWT_AUDIENCE", "authenticated"
        ).strip(),
        supabase_jwt_verification_mode=os.getenv(
            "SUPABASE_JWT_VERIFICATION_MODE", "auto"
        )
        .strip()
        .lower(),
        supabase_jwt_algorithms=_csv("SUPABASE_JWT_ALGORITHMS", "ES256"),
        cors_origins=_csv(
            "CORS_ORIGINS",
            "http://localhost:5500,http://127.0.0.1:5500",
        ),
        api_public_url=os.getenv("API_PUBLIC_URL", "").strip().rstrip("/"),
        qstash_current_signing_key=os.getenv(
            "QSTASH_CURRENT_SIGNING_KEY", ""
        ).strip(),
        qstash_next_signing_key=os.getenv(
            "QSTASH_NEXT_SIGNING_KEY", ""
        ).strip(),
        qstash_token=os.getenv("QSTASH_TOKEN", "").strip(),
        qstash_schedule_id=os.getenv("QSTASH_SCHEDULE_ID", "").strip(),
        reminder_cron=os.getenv("REMINDER_CRON", "").strip(),
        reminder_timezone=os.getenv(
            "REMINDER_TIMEZONE", "Europe/London"
        ).strip(),
        reminder_batch_size=_bounded_int("REMINDER_BATCH_SIZE", 50, 1, 200),
        reminder_max_attempts=_bounded_int(
            "REMINDER_MAX_ATTEMPTS", 4, 1, 10
        ),
        reminder_lease_seconds=_bounded_int(
            "REMINDER_LEASE_SECONDS", 180, 60, 900
        ),
        reminder_retry_base_seconds=_bounded_int(
            "REMINDER_RETRY_BASE_SECONDS", 60, 5, 3600
        ),
        action_status_link_ttl_hours=_bounded_int(
            "ACTION_STATUS_LINK_TTL_HOURS", 72, 1, 168
        ),
        brevo_api_key=os.getenv("BREVO_API_KEY", "").strip(),
        sender_email=os.getenv("SENDER_EMAIL", "").strip(),
        sender_name=os.getenv(
            "SENDER_NAME", "Ally, your AI Meeting Wizard"
        ).strip(),
        auth_jwks_cache_seconds=_positive_int("AUTH_JWKS_CACHE_SECONDS", 600),
        max_document_upload_mb=_positive_int("MAX_DOCUMENT_UPLOAD_MB", 10),
        max_audio_upload_mb=_positive_int("MAX_AUDIO_UPLOAD_MB", 100),
        max_email_attachment_mb=_positive_int("MAX_EMAIL_ATTACHMENT_MB", 10),
        max_email_total_attachment_mb=_positive_int(
            "MAX_EMAIL_TOTAL_ATTACHMENT_MB", 20
        ),
        max_email_recipients=_positive_int("MAX_EMAIL_RECIPIENTS", 10),
        max_email_subject_chars=_positive_int(
            "MAX_EMAIL_SUBJECT_CHARS", 200
        ),
        max_email_html_chars=_positive_int("MAX_EMAIL_HTML_CHARS", 200_000),
        rate_limit_ai_per_minute=_positive_int(
            "RATE_LIMIT_AI_PER_MINUTE", 10
        ),
        rate_limit_ai_per_day=_positive_int("RATE_LIMIT_AI_PER_DAY", 200),
        rate_limit_transcription_per_minute=_positive_int(
            "RATE_LIMIT_TRANSCRIPTION_PER_MINUTE", 3
        ),
        rate_limit_transcription_per_day=_positive_int(
            "RATE_LIMIT_TRANSCRIPTION_PER_DAY", 30
        ),
        rate_limit_document_upload_per_minute=_positive_int(
            "RATE_LIMIT_DOCUMENT_UPLOAD_PER_MINUTE", 10
        ),
        rate_limit_email_per_hour=_positive_int(
            "RATE_LIMIT_EMAIL_PER_HOUR", 10
        ),
        rate_limit_welcome_email_per_day=_positive_int(
            "RATE_LIMIT_WELCOME_EMAIL_PER_DAY", 1
        ),
        rate_limit_reminder_per_hour=_bounded_int(
            "RATE_LIMIT_REMINDER_PER_HOUR", 10, 1, 100
        ),
        ai_enabled=_boolean("AI_ENABLED", False),
        ai_provider=os.getenv("AI_PROVIDER", "").strip().lower(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        ai_text_model=os.getenv("AI_TEXT_MODEL", "").strip(),
        ai_structured_model=os.getenv("AI_STRUCTURED_MODEL", "").strip(),
        ai_connect_timeout_seconds=_bounded_float(
            "AI_CONNECT_TIMEOUT_SECONDS", 5.0, 0.5, 30.0
        ),
        ai_read_timeout_seconds=_bounded_float(
            "AI_READ_TIMEOUT_SECONDS", 45.0, 1.0, 120.0
        ),
        ai_write_timeout_seconds=_bounded_float(
            "AI_WRITE_TIMEOUT_SECONDS", 10.0, 1.0, 60.0
        ),
        ai_pool_timeout_seconds=_bounded_float(
            "AI_POOL_TIMEOUT_SECONDS", 5.0, 0.5, 30.0
        ),
        ai_max_retries=_bounded_int("AI_MAX_RETRIES", 1, 0, 2),
        ai_max_input_chars=_bounded_int(
            "AI_MAX_INPUT_CHARS", 100_000, 1_000, 500_000
        ),
        ai_max_estimated_input_tokens=_bounded_int(
            "AI_MAX_ESTIMATED_INPUT_TOKENS", 50_000, 1_000, 120_000
        ),
        ai_max_output_tokens=_bounded_int(
            "AI_MAX_OUTPUT_TOKENS", 4_096, 128, 16_384
        ),
        ai_chunk_chars=_bounded_int("AI_CHUNK_CHARS", 24_000, 2_000, 100_000),
        ai_max_chunks=_bounded_int("AI_MAX_CHUNKS", 5, 1, 12),
        ai_max_action_items=_bounded_int("AI_MAX_ACTION_ITEMS", 20, 0, 50),
        ai_max_topics=_bounded_int("AI_MAX_TOPICS", 30, 1, 100),
        ai_max_concurrent_requests=_bounded_int(
            "AI_MAX_CONCURRENT_REQUESTS", 4, 1, 16
        ),
        ai_generation_lease_seconds=_bounded_int(
            "AI_GENERATION_LEASE_SECONDS", 300, 60, 900
        ),
        ai_prompt_version=os.getenv("AI_PROMPT_VERSION", "phase2e-v1").strip(),
        rate_limit_autocomplete_per_minute=_bounded_int(
            "RATE_LIMIT_AUTOCOMPLETE_PER_MINUTE", 20, 1, 120
        ),
        rate_limit_autocomplete_per_day=_bounded_int(
            "RATE_LIMIT_AUTOCOMPLETE_PER_DAY", 500, 1, 5_000
        ),
    )
    if settings.is_production and os.getenv("GROQ_BASE_URL", "").strip():
        raise RuntimeError("GROQ_BASE_URL is not allowed in production.")
    settings.validate_for_startup()
    return settings


def load_html_templates() -> None:
    global welcome_template, email_template, reminder_template

    template_dir = Path(__file__).resolve().parent / "templates"
    names = {
        "welcome_template": "welcome-email-template.html",
        "email_template": "email-template.html",
        "reminder_template": "task-reminder-email-template.html",
    }
    for variable_name, filename in names.items():
        try:
            globals()[variable_name] = (template_dir / filename).read_text(
                encoding="utf-8"
            )
        except FileNotFoundError:
            logger.warning("Email template %s was not found.", filename)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO)
