from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from heart_of_the_swarm.authorization import (
    AuthorizationContext,
    AuthorizationService,
    ConfiguredCredentialPolicySource,
)
from heart_of_the_swarm.config import Settings
from heart_of_the_swarm.credentials import (
    CredentialRef,
    CredentialResolutionError,
    CredentialResolver,
    LocalSecretStore,
    RuntimeIdentity,
)


class RecordingSecretStore:
    def __init__(self, value: SecretStr | None) -> None:
        self.value = value
        self.reads: list[CredentialRef] = []

    async def resolve(self, reference: CredentialRef) -> SecretStr | None:
        self.reads.append(reference)
        return self.value


def resolver(
    runtime_id: str,
    grants: dict[str, list[str]],
    store: RecordingSecretStore,
) -> CredentialResolver:
    return CredentialResolver(
        RuntimeIdentity(id=runtime_id),
        AuthorizationService(ConfiguredCredentialPolicySource(grants)),
        store,
    )


async def test_allowed_runtime_resolves_exact_credential() -> None:
    store = RecordingSecretStore(SecretStr("private-value"))
    service = resolver(
        "deployment-42",
        {"runtime:deployment-42": ["crm_api_token"]},
        store,
    )
    reference = CredentialRef(id="crm_api_token")

    secret = await service.resolve(
        reference,
        context=AuthorizationContext(run_id=uuid4(), runtime_id="untrusted-runtime"),
    )

    assert secret.get_secret_value() == "private-value"
    assert store.reads == [reference]


async def test_denied_runtime_never_reads_secret_store() -> None:
    store = RecordingSecretStore(SecretStr("private-value"))
    service = resolver(
        "deployment-43",
        {"runtime:deployment-42": ["crm_api_token"]},
        store,
    )

    with pytest.raises(CredentialResolutionError) as denied:
        await service.resolve(CredentialRef(id="crm_api_token"))

    assert denied.value.reason == "deny"
    assert store.reads == []


async def test_missing_secret_fails_after_authorization_without_leaking_value() -> None:
    store = RecordingSecretStore(None)
    service = resolver(
        "deployment-42",
        {"runtime:deployment-42": ["missing"]},
        store,
    )

    with pytest.raises(CredentialResolutionError, match="credential could not be resolved") as exc:
        await service.resolve(CredentialRef(id="missing"))

    assert exc.value.reason == "not_found"
    assert "missing" not in str(exc.value)


def test_credential_reference_rejects_secret_payload_fields() -> None:
    with pytest.raises(ValidationError):
        CredentialRef.model_validate({"id": "crm_api_token", "value": "secret"})


def test_local_store_rejects_empty_or_invalid_credentials() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        LocalSecretStore({"crm_api_token": ""})
    with pytest.raises(ValidationError):
        LocalSecretStore({"invalid credential": "secret"})


def test_runtime_identity_rejects_embedded_or_client_supplied_fields() -> None:
    with pytest.raises(ValidationError):
        RuntimeIdentity.model_validate({"id": "runtime-1", "token": "untrusted"})


def test_settings_parse_local_secret_mapping_as_secret_values() -> None:
    settings = Settings(
        runtime_id="deployment-42",
        credential_policies='{"runtime:deployment-42":["crm_api_token"]}',
        local_secrets='{"crm_api_token":"private-value"}',
    )

    assert settings.credential_policies == {"runtime:deployment-42": ["crm_api_token"]}
    assert settings.local_secrets["crm_api_token"].get_secret_value() == "private-value"
    assert "private-value" not in repr(settings.local_secrets)
