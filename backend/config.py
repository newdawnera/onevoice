"""Application configuration and provider setup.

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

import google.generativeai as genai
from dotenv import load_dotenv


load_dotenv()

logger = logging.getLogger(__name__)

welcome_template = None
email_template = None
reminder_template = None


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
    value = _positive_int(name, default)
    if not minimum <= value <= maximum:
        raise RuntimeError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


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
    max_ai_text_chars: int
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
        max_ai_text_chars=_positive_int("MAX_AI_TEXT_CHARS", 100_000),
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
    )
    settings.validate_for_startup()
    return settings


def setup_gemini_api() -> None:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "Gemini is not configured; AI routes will fail closed at provider use."
        )
        return
    genai.configure(api_key=api_key)
    logger.info("Gemini API configured.")


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
