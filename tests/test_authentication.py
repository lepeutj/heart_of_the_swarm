from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

from heart_of_the_swarm.api import app, get_application
from heart_of_the_swarm.authentication import (
    AuthenticationError,
    AuthenticationMethod,
    DevelopmentAuthenticationProvider,
    JWTAuthenticationProvider,
    create_authentication_provider,
)
from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationService,
    DevelopmentPolicySource,
    PrincipalKind,
)
from heart_of_the_swarm.config import Settings

JWT_SECRET = "test-signing-secret-with-at-least-32-bytes"  # noqa: S105
WRONG_JWT_SECRET = "wrong-signing-secret-with-at-least-32-bytes"  # noqa: S105


@asynccontextmanager
async def isolated_lifespan(_app):
    yield


@pytest.fixture
def isolated_api():
    original = app.router.lifespan_context
    app.router.lifespan_context = isolated_lifespan
    try:
        yield
    finally:
        app.router.lifespan_context = original
        app.dependency_overrides.clear()


def test_development_provider_uses_the_real_user_principal_contract() -> None:
    provider = DevelopmentAuthenticationProvider("dev-secret", "developer-1")

    user = provider.authenticate("dev-secret")

    assert user.method is AuthenticationMethod.DEVELOPMENT
    assert user.to_principal().kind is PrincipalKind.USER
    assert user.to_principal().id == "developer-1"


def test_development_provider_rejects_an_invalid_token() -> None:
    provider = DevelopmentAuthenticationProvider("dev-secret", "developer-1")

    with pytest.raises(AuthenticationError, match="invalid authentication credentials"):
        provider.authenticate("wrong-secret")


def test_authentication_factory_rejects_incomplete_configuration() -> None:
    settings = Settings(_env_file=None, auth_mode="development")

    with pytest.raises(ValueError, match="DEVELOPMENT_AUTH_TOKEN"):
        create_authentication_provider(settings)


def jwt_token(**overrides: object) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": "user-42",
        "iss": "https://issuer.example",
        "aud": "heart-of-the-swarm",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "roles": ["administrator"],
    }
    claims.update(overrides)
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


def jwt_provider() -> JWTAuthenticationProvider:
    return JWTAuthenticationProvider(
        JWT_SECRET,
        "HS256",
        "https://issuer.example",
        "heart-of-the-swarm",
    )


def test_jwt_provider_rejects_the_unsigned_algorithm() -> None:
    with pytest.raises(ValueError, match="requires key, algorithm"):
        JWTAuthenticationProvider(
            JWT_SECRET,
            "none",
            "https://issuer.example",
            "heart-of-the-swarm",
        )


def test_jwt_provider_validates_required_claims_without_importing_roles() -> None:
    user = jwt_provider().authenticate(jwt_token())

    assert user.subject == "user-42"
    assert user.method is AuthenticationMethod.JWT
    assert not hasattr(user, "roles")


@pytest.mark.parametrize(
    "token",
    [
        jwt_token(exp=datetime.now(UTC) - timedelta(seconds=1)),
        jwt_token(iss="https://wrong.example"),
        jwt_token(aud="another-service"),
        jwt.encode(
            {
                "sub": "user-42",
                "iss": "https://issuer.example",
                "aud": "heart-of-the-swarm",
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(minutes=5),
            },
            WRONG_JWT_SECRET,
            algorithm="HS256",
        ),
    ],
)
def test_jwt_provider_rejects_invalid_evidence(token: str) -> None:
    with pytest.raises(AuthenticationError):
        jwt_provider().authenticate(token)


def test_public_health_does_not_require_authentication(isolated_api) -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200


def test_protected_api_requires_a_valid_bearer_token(isolated_api) -> None:
    policies = DevelopmentPolicySource("developer-1")
    policies.grant(AuthorizationAction.CAPABILITY_READ, "capability:catalog")
    runtime = SimpleNamespace(
        authentication=DevelopmentAuthenticationProvider("dev-secret", "developer-1"),
        authorization=AuthorizationService(policies),
        policy_source=policies,
        providers=SimpleNamespace(statuses=lambda: []),
    )
    app.dependency_overrides[get_application] = lambda: runtime

    with TestClient(app) as client:
        missing = client.get("/api/v1/providers")
        invalid = client.get("/api/v1/providers", headers={"Authorization": "Bearer wrong-secret"})
        valid = client.get("/api/v1/providers", headers={"Authorization": "Bearer dev-secret"})

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert invalid.status_code == 401
    assert valid.status_code == 200
