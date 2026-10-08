# Runtime identity and credential resolution

This document defines V2.6e runtime identity, public credential references, and authorized secret
resolution. Capability permission and secret access remain independent decisions.

## Two independent gates

```text
agent_version / workflow_node
→ capability.invoke

runtime
→ credential.resolve
```

A capability may start only when its executing principal may invoke it. If that capability requires
a secret, the current runtime must also be allowed to resolve the exact credential reference.

An agent, workflow node, model, tool argument, or workflow input never receives the runtime
principal and never receives raw secret values.

## Runtime identity

Each process executing agents or workflows has one configured stable identifier:

```text
runtime:<runtime_id>
```

Local development may use `local-runtime`. A deployed instance uses its immutable deployment or
runtime identity. The runtime ID comes from trusted process configuration, not an artifact, request,
LLM response, or workflow specification.

## Credential references

`CredentialRef` is a public, JSON-serializable identifier:

```yaml
id: crm_api_token
```

It contains no token, password, connection string, private key, or provider-specific secret data.
Specifications and versioned capability contracts may carry a reference, but never its value.

## Resolution boundary

```text
CredentialRef
+ runtime Principal
+ AuthorizationContext
→ credential.resolve check
→ SecretStore.resolve(ref)
→ SecretStr held only for the immediate runtime adapter
```

Missing policy, `deny`, and `approval_required` all stop before the store is read in V2.6e. Secret
approval grants are not introduced here.

The resolver records only the runtime principal, credential resource, decision, run correlation,
and safe reason code. It never records the resolved value.

## Initial secret store

`LocalSecretStore` reads an injected mapping of credential IDs to `SecretStr` values. Application
configuration may populate it from environment variables for local development and standalone
runtimes. The mapping is never persisted to PostgreSQL, artifacts, specs, MLflow, or product events.
The control-plane API does not construct a secret store; only worker and standalone runtime
processes resolve execution credentials. Deployment configuration should likewise inject secret
values only into those processes.

The `SecretStore` protocol is intentionally narrow so a later Vault, cloud secret manager, or
remote broker can replace the local adapter without changing agents, workflows, or capability
authorization.

## First migrated consumer: MCP bearer authentication

An MCP source may persist one public bearer reference beside its URL:

```yaml
name: crm
url: https://mcp.example.com/mcp
bearer_credential_ref:
  id: crm_mcp_token
```

The worker or standalone runtime resolves that reference under its configured runtime identity and
passes the resulting `Authorization` header directly to FastMCP's HTTP transport. The raw value is
not added to the MCP source record, capability descriptor, `ToolRegistry`, workflow events, or
technical trace metadata. Capability authorization still runs separately when an agent or
`CONNECTOR` invokes a discovered MCP tool.

The control-plane API intentionally has no `SecretStore`. It can persist and display the public
reference, but it cannot authenticate discovery against a protected MCP endpoint. Consequently,
authenticated MCP catalogue discovery is worker/runtime-owned in this increment. Sharing the
discovered schema back to the control plane requires a later durable catalogue contract; granting
the API access to runtime secrets is not the fallback.

Local configuration maps server names to credential IDs, then maps those IDs to injected values:

```text
MCP_BEARER_CREDENTIALS={"crm":"crm_mcp_token"}
CREDENTIAL_POLICIES={"runtime:local-runtime":["crm_mcp_token"]}
LOCAL_SECRETS={"crm_mcp_token":"injected-outside-git"}
```

## Scope and migration

V2.6e first establishes and tests the runtime identity and resolution boundary. Existing provider
keys and database connection settings are not automatically secure merely because this boundary
exists. Each consumer must be migrated explicitly to a `CredentialRef` before it can claim the new
protection.

No generic secret-management API, secret persistence, rotation engine, lease renewal, OAuth flow,
Vault adapter, or UI is part of this increment.

## Acceptance criteria

- runtime identity is derived only from trusted configuration;
- `CredentialRef` rejects unsafe or secret-like payload fields;
- allowed runtime resolves only the exact referenced credential;
- denied runtime causes no secret-store read;
- missing credential fails safely without revealing configured IDs or values;
- audit records never contain secret values;
- two runtimes can receive different decisions for the same credential;
- agents and workflow nodes cannot call `SecretStore` through public specifications;
- local and standalone runtimes use the same resolver contract;
- the MCP bearer consumer uses this boundary end to end;
- remaining provider and database secret consumers stay documented as unmigrated.
