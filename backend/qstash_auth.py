"""Fail-closed verification for inbound QStash deliveries."""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

from qstash import Receiver
from qstash.errors import SignatureError

from config import Settings, get_settings


MAX_QSTASH_BODY_BYTES = 4096
MAX_QSTASH_SIGNATURE_BYTES = 8192


class QStashVerificationUnavailable(Exception):
    """The server is not configured to authenticate QStash safely."""


class QStashVerificationRejected(Exception):
    """The inbound request is not a valid QStash delivery."""


class QStashRequestVerifier:
    def __init__(
        self,
        settings: Settings,
        *,
        receiver_factory: Callable[..., Receiver] = Receiver,
    ) -> None:
        self.settings = settings
        self._receiver_factory = receiver_factory

    def verify(self, *, raw_body: bytes, signature: str) -> None:
        if not self.settings.qstash_verification_configured:
            raise QStashVerificationUnavailable(
                "QStash request verification is not configured."
            )
        if not signature or len(signature.encode("utf-8")) > MAX_QSTASH_SIGNATURE_BYTES:
            raise QStashVerificationRejected("The QStash signature is invalid.")
        if len(raw_body) > MAX_QSTASH_BODY_BYTES:
            raise QStashVerificationRejected("The QStash body is too large.")
        try:
            body = raw_body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise QStashVerificationRejected(
                "The QStash body must be valid UTF-8."
            ) from exc

        receiver = self._receiver_factory(
            current_signing_key=self.settings.qstash_current_signing_key,
            next_signing_key=self.settings.qstash_next_signing_key,
        )
        try:
            receiver.verify(
                signature=signature,
                body=body,
                url=self.settings.reminder_run_url,
            )
        except (SignatureError, ValueError, TypeError) as exc:
            raise QStashVerificationRejected(
                "The QStash signature is invalid."
            ) from exc


@lru_cache(maxsize=1)
def get_qstash_verifier() -> QStashRequestVerifier:
    return QStashRequestVerifier(get_settings())
