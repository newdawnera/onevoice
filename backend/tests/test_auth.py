import asyncio
import time
from uuid import UUID

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth import (
    AuthRejected,
    AuthenticatedUser,
    SupabaseTokenVerifier,
    get_current_user,
    get_token_verifier,
)
from conftest import configured_settings


USER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SESSION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
KID = "test-key-1"


def key_pair(kid=KID):
    private_key = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return private_key, jwk


def claims(**overrides):
    now = int(time.time())
    payload = {
        "iss": "https://project.example.supabase.co/auth/v1",
        "aud": "authenticated",
        "sub": USER_ID,
        "role": "authenticated",
        "email": "user@example.com",
        "session_id": SESSION_ID,
        "iat": now,
        "exp": now + 600,
    }
    for key, value in overrides.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


def client_factory(transport):
    return lambda: httpx.AsyncClient(transport=transport)


def verifier_for(jwks, *, settings=None, counter=None):
    def handler(request):
        if counter is not None:
            counter.append(request.url.path)
        return httpx.Response(200, json={"keys": jwks})

    transport = httpx.MockTransport(handler)
    return SupabaseTokenVerifier(
        settings or configured_settings(),
        client_factory=client_factory(transport),
    )


def sign(private_key, payload=None, *, kid=KID, algorithm="ES256"):
    return jwt.encode(
        payload or claims(),
        private_key,
        algorithm=algorithm,
        headers={"kid": kid},
    )


def test_valid_authenticated_token_is_accepted():
    private_key, jwk = key_pair()
    user = asyncio.run(verifier_for([jwk]).verify(sign(private_key)))
    assert user.id == UUID(USER_ID)
    assert user.session_id == UUID(SESSION_ID)
    assert user.role == "authenticated"


@pytest.mark.parametrize(
    "payload",
    [
        claims(exp=int(time.time()) - 1),
        claims(nbf=int(time.time()) + 300),
        claims(iss="https://wrong.example/auth/v1"),
        claims(aud="wrong-audience"),
        claims(sub=None),
        claims(sub="not-a-uuid"),
        claims(role="anon"),
        claims(role="service_role"),
    ],
    ids=[
        "expired",
        "not-yet-valid",
        "wrong-issuer",
        "wrong-audience",
        "missing-subject",
        "non-uuid-subject",
        "anon-role",
        "service-role",
    ],
)
def test_invalid_claims_are_rejected(payload):
    private_key, jwk = key_pair()
    token = sign(private_key, payload)
    with pytest.raises(AuthRejected):
        asyncio.run(verifier_for([jwk]).verify(token))


def test_invalid_token_structure_is_rejected():
    _private_key, jwk = key_pair()
    with pytest.raises(AuthRejected):
        asyncio.run(verifier_for([jwk]).verify("not-a-jwt"))


def test_alg_none_is_rejected():
    _private_key, jwk = key_pair()
    token = jwt.encode(claims(), key="", algorithm="none", headers={"kid": KID})
    with pytest.raises(AuthRejected):
        asyncio.run(verifier_for([jwk]).verify(token))


def test_unexpected_algorithm_is_rejected():
    _private_key, jwk = key_pair()
    token = jwt.encode(claims(), "not-a-real-secret", algorithm="HS256", headers={"kid": KID})
    with pytest.raises(AuthRejected):
        asyncio.run(verifier_for([jwk]).verify(token))


def test_missing_kid_is_rejected():
    private_key, jwk = key_pair()
    token = jwt.encode(claims(), private_key, algorithm="ES256")
    with pytest.raises(AuthRejected):
        asyncio.run(verifier_for([jwk]).verify(token))


def test_invalid_signature_is_rejected():
    private_key, _jwk = key_pair()
    _other_private, other_jwk = key_pair()
    with pytest.raises(AuthRejected):
        asyncio.run(verifier_for([other_jwk]).verify(sign(private_key)))


def test_unknown_kid_refreshes_once_then_rejects():
    private_key, jwk = key_pair("known-key")
    calls = []
    token = sign(private_key, kid="unknown-key")
    with pytest.raises(AuthRejected):
        asyncio.run(verifier_for([jwk], counter=calls).verify(token))
    assert len(calls) == 2


def test_unknown_kid_refresh_can_pick_up_rotated_key():
    old_private, old_jwk = key_pair("old-key")
    new_private, new_jwk = key_pair("new-key")
    del old_private
    calls = []

    def handler(request):
        calls.append(request.url.path)
        keys = [old_jwk] if len(calls) == 1 else [new_jwk]
        return httpx.Response(200, json={"keys": keys})

    verifier = SupabaseTokenVerifier(
        configured_settings(),
        client_factory=client_factory(httpx.MockTransport(handler)),
    )
    user = asyncio.run(verifier.verify(sign(new_private, kid="new-key")))
    assert user.id == UUID(USER_ID)
    assert len(calls) == 2


def test_legacy_validation_success_and_failure():
    settings = configured_settings(supabase_jwt_verification_mode="legacy")

    def success(request):
        assert request.headers["apikey"] == settings.supabase_publishable_key
        assert request.headers["authorization"] == "Bearer opaque-token"
        return httpx.Response(
            200,
            json={"id": USER_ID, "email": "user@example.com", "role": "authenticated"},
        )

    verifier = SupabaseTokenVerifier(
        settings,
        client_factory=client_factory(httpx.MockTransport(success)),
    )
    assert asyncio.run(verifier.verify("opaque-token")).id == UUID(USER_ID)

    failing = SupabaseTokenVerifier(
        settings,
        client_factory=client_factory(
            httpx.MockTransport(lambda _request: httpx.Response(401))
        ),
    )
    with pytest.raises(AuthRejected):
        asyncio.run(failing.verify("bad-token"))


class AcceptingVerifier:
    async def verify(self, token):
        if token != "valid-token":
            raise AuthRejected()
        return AuthenticatedUser(
            id=UUID(USER_ID), email="user@example.com", role="authenticated", session_id=None
        )


def auth_test_client():
    app = FastAPI()

    @app.get("/protected")
    async def protected(user=Depends(get_current_user)):
        return {"id": str(user.id)}

    app.dependency_overrides[get_token_verifier] = lambda: AcceptingVerifier()
    return TestClient(app)


@pytest.mark.parametrize(
    "headers",
    [
        None,
        {"Authorization": "Bearer"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer invalid-token"},
    ],
    ids=["missing", "empty", "malformed", "invalid"],
)
def test_authorization_header_failures_return_401(headers):
    response = auth_test_client().get("/protected", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_duplicate_authorization_headers_are_rejected():
    response = auth_test_client().get(
        "/protected",
        headers=[
            ("Authorization", "Bearer valid-token"),
            ("Authorization", "Bearer valid-token"),
        ],
    )
    assert response.status_code == 401


def test_valid_authorization_header_is_accepted():
    response = auth_test_client().get(
        "/protected", headers={"Authorization": "Bearer valid-token"}
    )
    assert response.status_code == 200
    assert response.json()["id"] == USER_ID
