import hmac
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

import jwt
from pydantic import BaseModel, ConfigDict, ValidationError

from heart_of_the_swarm.authorization import Identifier, Principal, PrincipalKind

if TYPE_CHECKING:
    from heart_of_the_swarm.config import Settings


class AuthenticationMethod(StrEnum):
    DEVELOPMENT = "development"
    JWT = "jwt"


class AuthenticatedUser(BaseModel):
    """Verified HTTP identity before it enters the authorization layer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: Identifier
    issuer: str
    method: AuthenticationMethod

    def to_principal(self) -> Principal:
        """Convert authentication evidence into the shared authorization identity."""
        return Principal(kind=PrincipalKind.USER, id=self.subject)


class AuthenticationError(Exception):
    """Authentication failure carrying a safe reason code for audit logs."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("invalid authentication credentials")


class AuthenticationProvider(Protocol):
    def authenticate(self, token: str) -> AuthenticatedUser: ...


class DevelopmentAuthenticationProvider:
    """Authenticate one explicitly configured local user through the real bearer pipeline."""

    def __init__(self, token: str, user_id: str) -> None:
        if not token or not user_id:
            raise ValueError("development authentication requires a token and user id")
        self._token = token
        self._user_id = user_id

    def authenticate(self, token: str) -> AuthenticatedUser:
        if not hmac.compare_digest(token, self._token):
            raise AuthenticationError("invalid_token")
        return AuthenticatedUser(
            subject=self._user_id,
            issuer="heart-of-the-swarm:development",
            method=AuthenticationMethod.DEVELOPMENT,
        )


class JWTAuthenticationProvider:
    """Validate signed JWTs against one explicit issuer, audience, and algorithm."""

    def __init__(
        self,
        verification_key: str,
        algorithm: str,
        issuer: str,
        audience: str,
        leeway_seconds: int = 0,
    ) -> None:
        if not all((verification_key, algorithm, issuer, audience)) or algorithm.lower() == "none":
            raise ValueError("JWT authentication requires key, algorithm, issuer, and audience")
        self._verification_key = verification_key
        self._algorithm = algorithm
        self._issuer = issuer
        self._audience = audience
        self._leeway_seconds = leeway_seconds

    def authenticate(self, token: str) -> AuthenticatedUser:
        try:
            claims = jwt.decode(
                token,
                self._verification_key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway_seconds,
                options={"require": ["sub", "iss", "aud", "exp", "iat"]},
            )
            return AuthenticatedUser(
                subject=claims["sub"],
                issuer=claims["iss"],
                method=AuthenticationMethod.JWT,
            )
        except (jwt.PyJWTError, KeyError, ValidationError) as exc:
            raise AuthenticationError("invalid_token") from exc


def create_authentication_provider(settings: "Settings") -> AuthenticationProvider:
    """Build the configured API authenticator without affecting worker-only processes."""
    if settings.auth_mode == AuthenticationMethod.DEVELOPMENT:
        if settings.development_auth_token is None or settings.development_auth_user_id is None:
            raise ValueError(
                "development authentication requires DEVELOPMENT_AUTH_TOKEN "
                "and DEVELOPMENT_AUTH_USER_ID"
            )
        return DevelopmentAuthenticationProvider(
            settings.development_auth_token.get_secret_value(),
            settings.development_auth_user_id,
        )
    if (
        settings.jwt_verification_key is None
        or not settings.jwt_issuer
        or not settings.jwt_audience
    ):
        raise ValueError(
            "JWT authentication requires JWT_VERIFICATION_KEY, JWT_ISSUER, and JWT_AUDIENCE"
        )
    return JWTAuthenticationProvider(
        settings.jwt_verification_key.get_secret_value(),
        settings.jwt_algorithm,
        settings.jwt_issuer,
        settings.jwt_audience,
        settings.jwt_clock_skew_seconds,
    )
