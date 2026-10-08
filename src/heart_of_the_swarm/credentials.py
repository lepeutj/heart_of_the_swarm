from collections.abc import Mapping
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from heart_of_the_swarm.authorization import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationService,
    Principal,
    PrincipalKind,
)
from heart_of_the_swarm.observability import audit_event

CredentialId = Annotated[
    str,
    Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$"),
]


class RuntimeIdentity(BaseModel):
    """Trusted process identity used only for runtime-owned authorization decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: CredentialId

    @property
    def principal(self) -> Principal:
        return Principal(kind=PrincipalKind.RUNTIME, id=self.id)


class CredentialRef(BaseModel):
    """Public identifier for a secret that is resolved only inside a trusted runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: CredentialId


class SecretStore(Protocol):
    """Resolve secret values without exposing storage details to execution code."""

    async def resolve(self, reference: CredentialRef) -> SecretStr | None:
        """Return one secret or `None` without logging or persisting its value."""


class LocalSecretStore:
    """Process-local environment-backed store for development and standalone runtimes."""

    def __init__(self, secrets: Mapping[str, SecretStr | str]) -> None:
        self._secrets: dict[str, SecretStr] = {}
        for credential_id, value in secrets.items():
            reference = CredentialRef(id=credential_id)
            secret = value if isinstance(value, SecretStr) else SecretStr(value)
            if not secret.get_secret_value():
                raise ValueError("local credentials cannot be empty")
            self._secrets[reference.id] = secret

    async def resolve(self, reference: CredentialRef) -> SecretStr | None:
        return self._secrets.get(reference.id)


class CredentialResolutionError(PermissionError):
    """Safe failure raised before a denied or unavailable credential can be returned."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("credential could not be resolved")


class CredentialResolver:
    """Authorize one runtime principal before reading an injected secret store."""

    def __init__(
        self,
        identity: RuntimeIdentity,
        authorization: AuthorizationService,
        store: SecretStore,
    ) -> None:
        self.identity = identity
        self.authorization = authorization
        self.store = store

    async def resolve(
        self,
        reference: CredentialRef,
        *,
        context: AuthorizationContext | None = None,
    ) -> SecretStr:
        """Return one secret only after an exact runtime credential allow."""
        trusted_context = (context or AuthorizationContext()).model_copy(
            update={"runtime_id": self.identity.id}
        )
        request = AuthorizationRequest(
            principal=self.identity.principal,
            action=AuthorizationAction.CREDENTIAL_RESOLVE,
            resource=f"credential:{reference.id}",
            context=trusted_context,
        )
        decision = await self.authorization.decide(request)
        self._audit(request, decision)
        if decision != AuthorizationDecision.ALLOW:
            raise CredentialResolutionError(decision.value)

        secret = await self.store.resolve(reference)
        if secret is None:
            audit_event(
                "credential.resolve_failed",
                runtime_id=self.identity.id,
                resource=request.resource,
                reason="not_found",
                run_id=str(trusted_context.run_id) if trusted_context.run_id else None,
            )
            raise CredentialResolutionError("not_found")
        return secret

    @staticmethod
    def _audit(
        request: AuthorizationRequest,
        decision: AuthorizationDecision,
    ) -> None:
        """Record the credential decision without reading or exposing its secret value."""
        audit_event(
            "authorization.runtime_decision",
            principal_kind=request.principal.kind,
            principal_id=request.principal.id,
            action=request.action,
            resource=request.resource,
            decision=decision,
            boundary="credential_resolution",
            run_id=str(request.context.run_id) if request.context.run_id else None,
        )
