import ipaddress

import pytest

from heart_of_the_swarm.credentials import CredentialRef
from heart_of_the_swarm.network_policy import (
    NetworkPolicy,
    NetworkPolicyEnforcer,
    NetworkPolicyError,
)
from heart_of_the_swarm.tools import MCPConnection, MCPToolLoader, ToolRegistry


def resolver(*addresses: str):
    async def resolve(_hostname: str, _port: int):
        return tuple(ipaddress.ip_address(value) for value in addresses)

    return resolve


async def test_default_policy_accepts_only_fully_public_dns_results() -> None:
    accepted = await NetworkPolicyEnforcer(
        NetworkPolicy(),
        resolver("93.184.216.34"),
    ).validate("https://example.test/mcp")

    assert accepted.hostname == "example.test"
    assert accepted.port == 443
    assert accepted.addresses == (ipaddress.ip_address("93.184.216.34"),)

    with pytest.raises(NetworkPolicyError, match="network destination") as mixed:
        await NetworkPolicyEnforcer(
            NetworkPolicy(),
            resolver("93.184.216.34", "10.0.0.4"),
        ).validate("https://example.test/mcp")
    assert mixed.value.reason == "private_address"


async def test_validated_destination_pins_url_host_and_tls_name() -> None:
    destination = await NetworkPolicyEnforcer(
        NetworkPolicy(),
        resolver("93.184.216.34"),
    ).validate("https://example.test:8443/mcp?mode=stream#ignored")

    pinned = destination.pin()

    assert pinned.url == "https://93.184.216.34:8443/mcp?mode=stream"
    assert pinned.host_header == "example.test:8443"
    assert pinned.tls_server_name == "example.test"


async def test_private_cidr_exception_is_explicit_and_deny_takes_precedence() -> None:
    allowed = NetworkPolicy(allowed_cidrs=("10.20.0.0/16",))
    destination = await NetworkPolicyEnforcer(
        allowed,
        resolver("10.20.3.4"),
    ).validate("http://internal.test:8080/mcp")
    assert destination.port == 8080

    denied = NetworkPolicy(
        allowed_cidrs=("10.20.0.0/16",),
        denied_cidrs=("10.20.3.0/24",),
    )
    with pytest.raises(NetworkPolicyError) as blocked:
        await NetworkPolicyEnforcer(denied, resolver("10.20.3.4")).validate(
            "http://internal.test/mcp"
        )
    assert blocked.value.reason == "denied_cidr"


@pytest.mark.parametrize(
    ("address", "policy", "reason"),
    [
        ("127.0.0.1", NetworkPolicy(allowed_cidrs=("127.0.0.0/8",)), "loopback_address"),
        (
            "169.254.20.1",
            NetworkPolicy(allowed_cidrs=("169.254.0.0/16",)),
            "link_local_address",
        ),
        (
            "169.254.169.254",
            NetworkPolicy(allow_link_local=True),
            "metadata_address",
        ),
        (
            "::ffff:169.254.169.254",
            NetworkPolicy(allow_private=True, allow_link_local=True),
            "metadata_address",
        ),
    ],
)
async def test_dedicated_destination_classes_cannot_be_enabled_accidentally(
    address: str,
    policy: NetworkPolicy,
    reason: str,
) -> None:
    with pytest.raises(NetworkPolicyError) as blocked:
        await NetworkPolicyEnforcer(policy, resolver(address)).validate("https://target.test")
    assert blocked.value.reason == reason


async def test_dedicated_flags_allow_only_their_destination_class() -> None:
    await NetworkPolicyEnforcer(
        NetworkPolicy(allow_loopback=True),
        resolver("127.0.0.1"),
    ).validate("http://local.test")
    await NetworkPolicyEnforcer(
        NetworkPolicy(allow_link_local=True),
        resolver("169.254.20.1"),
    ).validate("http://link.test")
    await NetworkPolicyEnforcer(
        NetworkPolicy(allow_metadata=True),
        resolver("169.254.169.254"),
    ).validate("http://metadata.test")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:password@example.test/mcp",
        "https://example.test:invalid/mcp",
    ],
)
async def test_invalid_authorities_are_rejected_before_dns(url: str) -> None:
    called = False

    async def unexpected_resolver(_hostname: str, _port: int):
        nonlocal called
        called = True
        return ()

    with pytest.raises(NetworkPolicyError):
        await NetworkPolicyEnforcer(NetworkPolicy(), unexpected_resolver).validate(url)
    assert called is False


async def test_mcp_loader_denies_network_before_adapter_creation() -> None:
    adapters_created = 0

    class UnexpectedCredentialResolver:
        calls = 0

        async def resolve(self, _reference):
            self.calls += 1
            raise AssertionError("denied destination attempted to resolve a credential")

    credentials = UnexpectedCredentialResolver()

    def adapter_factory(_url, _headers):
        nonlocal adapters_created
        adapters_created += 1
        raise AssertionError("denied destination reached the MCP adapter")

    loader = MCPToolLoader(
        ToolRegistry(),
        {
            "internal": MCPConnection(
                url="http://internal.test/mcp",
                bearer_credential_ref=CredentialRef(id="internal_token"),
            )
        },
        adapter_factory=adapter_factory,
        credential_resolver=credentials,  # type: ignore[arg-type]
        network_policy=NetworkPolicyEnforcer(NetworkPolicy(), resolver("10.0.0.4")),
    )

    assert await loader.load() == ()
    assert adapters_created == 0
    assert credentials.calls == 0
    assert loader.statuses[0].state == "error"
