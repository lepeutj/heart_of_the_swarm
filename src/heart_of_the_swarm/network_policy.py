from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, IPvAnyNetwork

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
AddressResolver = Callable[[str, int], Awaitable[tuple[IPAddress, ...]]]

_METADATA_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "169.254.169.254/32",
        "169.254.170.2/32",
        "100.100.100.200/32",
        "fd00:ec2::254/128",
    )
)
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")


class NetworkPolicy(BaseModel):
    """Closed runtime policy for one outbound network destination."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allow_public: bool = True
    allow_private: bool = False
    allowed_cidrs: tuple[IPvAnyNetwork, ...] = ()
    denied_cidrs: tuple[IPvAnyNetwork, ...] = ()
    allow_loopback: bool = False
    allow_link_local: bool = False
    allow_metadata: bool = False


class NetworkPolicyError(PermissionError):
    """Safe destination denial that exposes a stable reason but no sensitive URL data."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("network destination is not allowed")


@dataclass(frozen=True)
class ValidatedNetworkDestination:
    """Parsed destination plus the complete address set accepted by runtime policy."""

    original_url: str
    scheme: str
    hostname: str
    port: int
    addresses: tuple[IPAddress, ...]

    def pin(self, address: IPAddress | None = None) -> PinnedNetworkDestination:
        """Build an IP-literal target while preserving the validated HTTP host and TLS name."""
        selected = address or self.addresses[0]
        if selected not in self.addresses:
            raise ValueError("pinned address was not validated for this destination")
        parsed = urlsplit(self.original_url)
        ip_host = f"[{selected}]" if selected.version == 6 else str(selected)
        hostname = self.hostname.encode("idna").decode("ascii")
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        return PinnedNetworkDestination(
            url=urlunsplit(
                (
                    self.scheme,
                    f"{ip_host}:{self.port}",
                    parsed.path,
                    parsed.query,
                    "",
                )
            ),
            host_header=f"{host_header}:{self.port}",
            tls_server_name=hostname if self.scheme == "https" else None,
            address=selected,
        )


@dataclass(frozen=True)
class PinnedNetworkDestination:
    """Transport target that cannot perform a second DNS lookup for the destination host."""

    url: str
    host_header: str
    tls_server_name: str | None
    address: IPAddress


class NetworkPolicyEnforcer:
    """Resolve and authorize every address before an outbound transport is constructed."""

    def __init__(
        self,
        policy: NetworkPolicy,
        resolver: AddressResolver | None = None,
    ) -> None:
        self.policy = policy
        self.resolver = resolver or resolve_addresses

    async def validate(self, url: str) -> ValidatedNetworkDestination:
        """Reject malformed or unauthorized destinations and return their resolved addresses."""
        parsed = self._parse(url)
        hostname = parsed.hostname
        if hostname is None:  # Defensive guard for the validated parser boundary.
            raise NetworkPolicyError("unsupported_destination")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            literal = ipaddress.ip_address(hostname)
            addresses = (literal,)
        except ValueError:
            try:
                addresses = await self.resolver(hostname, port)
            except (OSError, ValueError) as exc:
                raise NetworkPolicyError("dns_resolution_failed") from exc
        if not addresses:
            raise NetworkPolicyError("dns_resolution_empty")
        for address in addresses:
            self._require_allowed(address)
        return ValidatedNetworkDestination(
            original_url=url,
            scheme=parsed.scheme,
            hostname=hostname,
            port=port,
            addresses=tuple(dict.fromkeys(addresses)),
        )

    @staticmethod
    def _parse(url: str) -> SplitResult:
        try:
            parsed = urlsplit(url)
            _ = parsed.port
        except (TypeError, ValueError) as exc:
            raise NetworkPolicyError("invalid_url") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise NetworkPolicyError("unsupported_destination")
        if parsed.username is not None or parsed.password is not None:
            raise NetworkPolicyError("embedded_credentials")
        return parsed

    def _require_allowed(self, address: IPAddress) -> None:
        for candidate in _address_candidates(address):
            self._require_single_address_allowed(candidate)

    def _require_single_address_allowed(self, address: IPAddress) -> None:
        if _in_networks(address, self.policy.denied_cidrs):
            raise NetworkPolicyError("denied_cidr")
        if _in_networks(address, _METADATA_NETWORKS):
            if self.policy.allow_metadata:
                return
            raise NetworkPolicyError("metadata_address")
        if address.is_loopback:
            if self.policy.allow_loopback:
                return
            raise NetworkPolicyError("loopback_address")
        if address.is_link_local:
            if self.policy.allow_link_local:
                return
            raise NetworkPolicyError("link_local_address")
        if address.is_multicast or address.is_unspecified or address.is_reserved:
            raise NetworkPolicyError("non_unicast_address")
        if _in_networks(address, self.policy.allowed_cidrs):
            return
        if address.is_private:
            if self.policy.allow_private:
                return
            raise NetworkPolicyError("private_address")
        if address.is_global and self.policy.allow_public:
            return
        raise NetworkPolicyError("address_not_allowed")


async def resolve_addresses(hostname: str, port: int) -> tuple[IPAddress, ...]:
    """Resolve one host for TCP and preserve the resolver's stable first-seen order."""
    loop = asyncio.get_running_loop()
    records = await loop.run_in_executor(
        None,
        lambda: socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM),
    )
    return tuple(dict.fromkeys(ipaddress.ip_address(record[4][0]) for record in records))


def _in_networks(address: IPAddress, networks: tuple[IPvAnyNetwork, ...]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def _address_candidates(address: IPAddress) -> tuple[IPAddress, ...]:
    """Include IPv4 targets embedded in common IPv6 transition representations."""
    if not isinstance(address, ipaddress.IPv6Address):
        return (address,)
    embedded: list[IPAddress] = [address]
    if address.ipv4_mapped is not None:
        embedded.append(address.ipv4_mapped)
    if address.sixtofour is not None:
        embedded.append(address.sixtofour)
    if address.teredo is not None:
        embedded.extend(address.teredo)
    if address in _NAT64_WELL_KNOWN:
        embedded.append(ipaddress.IPv4Address(address.packed[-4:]))
    return tuple(dict.fromkeys(embedded))
