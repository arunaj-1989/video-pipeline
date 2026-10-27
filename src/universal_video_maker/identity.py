"""Keycloak OIDC access-token verification and role mapping."""

from __future__ import annotations

from typing import Annotated, Protocol

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import (
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)
from pydantic import BaseModel

from universal_video_maker.config import Settings
from universal_video_maker.security import Principal, Role

ALLOWED_JWT_ALGORITHMS = ("RS256",)
BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


class AccessTokenError(Exception):
    """Token is invalid or does not match the configured OIDC client."""


class IdentityProviderUnavailable(Exception):
    """The OIDC signing keys could not be retrieved."""


class SigningKeyClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> object: ...


class TokenVerifier(Protocol):
    def verify(self, token: str) -> Principal: ...


class KeycloakTokenVerifier:
    def __init__(
        self,
        *,
        issuer_url: str,
        jwks_url: str,
        audience: str,
        jwks_client: SigningKeyClient | None = None,
    ) -> None:
        if not issuer_url.strip() or not jwks_url.strip() or not audience.strip():
            raise ValueError("OIDC issuer, JWKS URL, and audience are required.")
        self._issuer_url = issuer_url.rstrip("/")
        self._audience = audience
        self._jwks_client = jwks_client or jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            lifespan=300,
            timeout=5,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> KeycloakTokenVerifier:
        return cls(
            issuer_url=settings.oidc_issuer_url,
            jwks_url=settings.oidc_jwks_url,
            audience=settings.oidc_audience,
        )

    def verify(self, token: str) -> Principal:
        if not token.strip():
            raise AccessTokenError("Bearer token is empty.")
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            key = getattr(signing_key, "key", None)
            if key is None:
                raise AccessTokenError("OIDC signing key is invalid.")
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(ALLOWED_JWT_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer_url,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except PyJWKClientConnectionError as error:
            raise IdentityProviderUnavailable(
                "OIDC signing keys are temporarily unavailable."
            ) from error
        except (InvalidTokenError, PyJWKClientError) as error:
            raise AccessTokenError("Bearer token is invalid or expired.") from error

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AccessTokenError("Bearer token has no valid subject.")
        roles = _roles_from_claims(claims)
        email = claims.get("email")
        return Principal(
            subject=subject,
            roles=roles,
            email=email.strip() if isinstance(email, str) and email.strip() else None,
        )


def _roles_from_claims(claims: dict[str, object]) -> frozenset[Role]:
    realm_access = claims.get("realm_access")
    if not isinstance(realm_access, dict):
        return frozenset()
    raw_roles = realm_access.get("roles", [])
    if not isinstance(raw_roles, list) or any(not isinstance(role, str) for role in raw_roles):
        raise AccessTokenError("Bearer token role claim is invalid.")
    roles: set[Role] = set()
    for role_name in raw_roles:
        try:
            roles.add(Role(role_name))
        except ValueError:
            continue
    return frozenset(roles)


bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]
router = APIRouter(tags=["authentication"])


async def get_current_principal(
    request: Request,
    credentials: BearerCredentials,
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is required.",
            headers=BEARER_CHALLENGE,
        )
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        return verifier.verify(credentials.credentials)
    except AccessTokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers=BEARER_CHALLENGE,
        ) from error
    except IdentityProviderUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error


class AuthenticatedIdentity(BaseModel):
    subject: str
    roles: list[Role]


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


@router.get("/auth/me", response_model=AuthenticatedIdentity)
async def read_authenticated_identity(
    principal: CurrentPrincipal,
) -> AuthenticatedIdentity:
    return AuthenticatedIdentity(
        subject=principal.subject,
        roles=sorted(principal.roles, key=lambda role: role.value),
    )
