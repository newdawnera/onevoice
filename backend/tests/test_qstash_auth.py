import base64
import hashlib
import time

import jwt
import pytest

from conftest import configured_settings
from qstash_auth import (
    QStashRequestVerifier,
    QStashVerificationRejected,
    QStashVerificationUnavailable,
)


CURRENT_KEY = "current-signing-key-32-bytes-long!!"
NEXT_KEY = "next-signing-key-is-also-32-byte!"
DESTINATION = "https://api.example.com/internal/reminders/run"


def verifier(**overrides):
    values = {
        "api_public_url": "https://api.example.com",
        "qstash_current_signing_key": CURRENT_KEY,
        "qstash_next_signing_key": NEXT_KEY,
    }
    values.update(overrides)
    return QStashRequestVerifier(configured_settings(**values))


def signed(key, body, *, destination=DESTINATION, exp=None, nbf=None):
    now = int(time.time())
    body_hash = base64.urlsafe_b64encode(
        hashlib.sha256(body).digest()
    ).decode().rstrip("=")
    return jwt.encode(
        {
            "iss": "Upstash",
            "sub": destination,
            "exp": exp if exp is not None else now + 300,
            "nbf": nbf if nbf is not None else now - 1,
            "iat": now,
            "jti": "test-message",
            "body": body_hash,
        },
        key,
        algorithm="HS256",
    )


@pytest.mark.parametrize("key", [CURRENT_KEY, NEXT_KEY])
def test_current_and_next_signing_keys_are_accepted(key):
    body = b'{"version":1}'
    verifier().verify(raw_body=body, signature=signed(key, body))


@pytest.mark.parametrize(
    "signature",
    ["", "not-a-jwt", signed("different-key-that-is-long-enough!!", b"{}")],
)
def test_missing_malformed_and_invalid_signatures_are_rejected(signature):
    with pytest.raises(QStashVerificationRejected):
        verifier().verify(raw_body=b"{}", signature=signature)


def test_expired_and_not_yet_valid_signatures_are_rejected():
    now = int(time.time())
    with pytest.raises(QStashVerificationRejected):
        verifier().verify(
            raw_body=b"{}",
            signature=signed(CURRENT_KEY, b"{}", exp=now - 1, nbf=now - 300),
        )
    with pytest.raises(QStashVerificationRejected):
        verifier().verify(
            raw_body=b"{}",
            signature=signed(CURRENT_KEY, b"{}", exp=now + 300, nbf=now + 60),
        )


def test_wrong_destination_and_altered_body_are_rejected():
    body = b'{"version":1}'
    with pytest.raises(QStashVerificationRejected):
        verifier().verify(
            raw_body=body,
            signature=signed(
                CURRENT_KEY,
                body,
                destination="https://attacker.example/internal/reminders/run",
            ),
        )
    with pytest.raises(QStashVerificationRejected):
        verifier().verify(raw_body=b'{}', signature=signed(CURRENT_KEY, body))


def test_parsed_and_reserialized_json_does_not_match_raw_body_hash():
    original = b'{"version":1}'
    reserialized = b'{ "version": 1 }'
    with pytest.raises(QStashVerificationRejected):
        verifier().verify(
            raw_body=reserialized,
            signature=signed(CURRENT_KEY, original),
        )


def test_missing_server_signing_configuration_fails_closed():
    with pytest.raises(QStashVerificationUnavailable):
        verifier(
            api_public_url="",
            qstash_current_signing_key="",
            qstash_next_signing_key="",
        ).verify(raw_body=b"{}", signature="anything")
