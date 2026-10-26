from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from universal_video_maker.api import create_app
from universal_video_maker.config import Settings
from universal_video_maker.identity import (
    AccessTokenError,
    KeycloakTokenVerifier,
)
from universal_video_maker.security import Principal, Role


class FakeJwksClient:
    def __init__(self, key) -> None:
        self.key = key

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=self.key)


class StaticTokenVerifier:
    def verify(self, token: str) -> Principal:
        if token != "valid-token":
            raise AccessTokenError("Bearer token is invalid or expired.")
        return Principal("keycloak-user-1", frozenset({Role.EDITOR, Role.VIEWER}))


@pytest.fixture
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _verifier(public_key) -> KeycloakTokenVerifier:
    return KeycloakTokenVerifier(
        issuer_url="http://localhost:8080/realms/universal-video-maker",
        jwks_url=(
            "http://localhost:8080/realms/universal-video-maker/protocol/openid-connect/certs"
        ),
        audience="universal-video-maker-api",
        jwks_client=FakeJwksClient(public_key),
    )


def _token(private_key, **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": "http://localhost:8080/realms/universal-video-maker",
        "aud": "universal-video-maker-api",
        "sub": "keycloak-user-1",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "realm_access": {"roles": ["editor", "viewer", "unknown-role"]},
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256")


def test_keycloak_verifier_validates_signature_issuer_audience_and_roles(rsa_keys) -> None:
    private_key, public_key = rsa_keys

    principal = _verifier(public_key).verify(_token(private_key))

    assert principal.subject == "keycloak-user-1"
    assert principal.roles == frozenset({Role.EDITOR, Role.VIEWER})


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "http://localhost:8080/realms/other"},
        {"aud": "another-api"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
        {"sub": ""},
    ],
)
def test_keycloak_verifier_rejects_invalid_claims(rsa_keys, claims) -> None:
    private_key, public_key = rsa_keys

    with pytest.raises(AccessTokenError):
        _verifier(public_key).verify(_token(private_key, **claims))


def test_keycloak_verifier_rejects_invalid_role_claim_shape(rsa_keys) -> None:
    private_key, public_key = rsa_keys

    with pytest.raises(AccessTokenError, match="role claim"):
        _verifier(public_key).verify(_token(private_key, realm_access={"roles": "editor"}))


def test_auth_me_requires_a_bearer_token_and_returns_verified_identity() -> None:
    app = create_app(
        Settings(_env_file=None),
        token_verifier=StaticTokenVerifier(),
    )
    with TestClient(app) as client:
        missing = client.get("/api/v1/auth/me")
        valid = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer valid-token"},
        )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert valid.status_code == 200
    assert valid.json() == {
        "subject": "keycloak-user-1",
        "roles": ["editor", "viewer"],
    }
