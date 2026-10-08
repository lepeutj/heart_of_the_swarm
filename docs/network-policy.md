# Runtime network policy

This document defines the network-destination contract required before remote MCP servers or other
outbound capabilities are used in a deployed runtime. It is a runtime/deployment policy, not part
of `AgentSpec`, `WorkflowSpec`, tool arguments, or LLM output.

## Policy model

The first implementation should expose one closed configuration:

```yaml
allow_public: true
allow_private: false
allowed_cidrs: []
denied_cidrs: []
allow_loopback: false
allow_link_local: false
allow_metadata: false
```

`allowed_cidrs` provides explicit exceptions for private destinations without enabling every
private network. `denied_cidrs` always takes precedence. Loopback, link-local, and cloud metadata
destinations require their dedicated flags and cannot be enabled accidentally through
`allow_private` or a broad allowed CIDR.

The default permits normal public HTTP/HTTPS destinations and rejects private, loopback,
link-local, metadata, multicast, unspecified, and reserved addresses. A local development profile
may explicitly allow selected private CIDRs or loopback. A remote deployment chooses its own policy
and never inherits network permission from the initiating user, agent, workflow, or URL itself.

## Validation boundary

Every outbound HTTP destination must pass the same sequence:

```text
parse URL
→ validate scheme and credential-free authority
→ resolve DNS
→ validate every resolved address
→ connect without permitting a different unvalidated destination
→ verify every redirect with the same process
```

Validation of only the URL string is insufficient. A hostname can resolve to a prohibited address,
return several addresses, change after validation, or redirect to another destination.

Required invariants:

- only explicitly supported schemes are accepted;
- URL-embedded credentials are rejected;
- every DNS result must be allowed, not merely the first result;
- denied CIDRs win over all general allow flags;
- metadata destinations remain denied unless `allow_metadata` is explicitly enabled;
- redirects are bounded and each target is parsed, resolved, and authorized again;
- the connected peer must remain one of the validated addresses, preventing DNS rebinding between
  validation and connection;
- failures use safe public errors and audit the policy reason without credentials or unrestricted
  payloads;
- policy checks apply to connection tests, discovery, refresh, and runtime invocation alike.

## MCP integration

`MCPServerCreate` continues to store only the server name, URL, and enabled state. It does not carry
network permission. `MCPToolLoader` receives an injected network-policy adapter and applies it before
creating or refreshing the LangChain MCP adapter.

```text
persisted MCP source
→ runtime NetworkPolicy
→ validated destination
→ LangChain MCPAdapter
→ shared ToolRegistry
```

This preserves the existing capability architecture. It does not create another MCP catalogue or
network-aware tool registry.

## Relationship to authorization

Authorization and network policy are independent gates:

```text
principal may capability.invoke
AND runtime provides the capability
AND destination passes NetworkPolicy
→ outbound call may start
```

An `allow` authorization decision cannot broaden network access. Conversely, a reachable network
destination does not grant permission to invoke a capability.

## Deferred implementation

This contract does not yet add proxy support, service meshes, dynamic firewall programming,
Kubernetes network policies, VPN management, or per-request user-defined CIDRs. Those belong to
deployment infrastructure only when a real target requires them.

## Acceptance criteria

- public MCP destination accepted by the default policy;
- private destination rejected by default and accepted only by explicit private/CIDR policy;
- loopback, link-local, and metadata destinations use their dedicated decisions;
- denied CIDR overrides every normal allow;
- mixed allowed/prohibited DNS answers are rejected;
- redirect to a prohibited destination is rejected;
- DNS rebinding cannot change the validated peer silently;
- MCP test, discovery, refresh, and invocation share the same policy boundary;
- policy denial creates no outbound request and returns a safe error.
