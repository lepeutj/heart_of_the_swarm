# User authentication contract

This document defines V2.6b user authentication for the control-plane HTTP API. Authentication
establishes identity; authorization remains the separate principal/action/resource decision defined
in `security.md`.

```text
HTTP request
→ bearer token extraction
→ AuthenticationProvider
→ AuthenticatedUser
→ Principal(kind="user")
→ AuthorizationRequest
→ PolicyEvaluator
```

V2.6b validates tokens only. Heart of the Swarm does not issue tokens, manage passwords, implement
OAuth flows, or place authorization policy in JWT claims.

## HTTP credential transport

Protected requests use one mechanism:

```http
Authorization: Bearer <token>
```

Tokens are never accepted in query parameters, form fields, workflow input, specifications, logs,
or SSE URLs. Bearer credentials require TLS outside local development.

The FastAPI adapter extracts the header and passes only the token value to an injected
`AuthenticationProvider`. Provider code does not depend on FastAPI.

## Authentication providers

V2.6b supports exactly two providers selected explicitly by configuration.

### DevelopmentAuthenticationProvider

The development provider compares the bearer token against a non-empty token injected through the
environment and returns one configured development user. Comparison must be constant-time. It has
no default token and the application must fail startup when development authentication is selected
without both a token and user identifier.

Development mode follows the complete authentication and authorization pipeline. Missing or wrong
tokens are rejected; there is no localhost, test, UI, or debug bypass. Tests may inject a provider
fake through the same interface.

### JWTAuthenticationProvider

The JWT provider validates a signed token using externally injected verification configuration. It
must not select an algorithm from an untrusted token. The allowed algorithm, verification key,
issuer, audience, and clock-skew allowance are application configuration. `alg=none`, unexpected
algorithms, signature failures, and untrusted key-location headers are rejected.

The following claims are required and validated:

```text
sub  non-empty user subject mapped to Principal.id
iss  exact configured issuer
aud  contains the configured control-plane audience
exp  current time is before expiration, subject only to configured clock skew
iat  valid numeric issue time and not unreasonably in the future
```

If `nbf` is present, it is also validated. JWT roles, permissions, workspace membership, and
capability access are ignored in V2.6b. The configured issuer is unique for this provider, so `sub`
is sufficient as the user principal identifier.

The provider returns:

```text
AuthenticatedUser
├── subject
├── issuer
└── authentication_method: development | jwt
```

The application maps `subject` to `Principal(kind="user", id=subject)`. The subject must satisfy the
same trusted identifier contract as any other principal.

## Protected routes

The following routes remain public:

```text
GET /
GET /health
GET /ready
GET /docs
GET /redoc
GET /openapi.json
/static/*
/workflow-editor/*
```

Every `/api/v1/*` control-plane route requires an authenticated user in V2.6b, including catalogue,
workflow, run, approval, trace, SSE, and webhook routes. Webhooks use user bearer authentication
until a later trigger-specific credential contract replaces it. Standalone runtime authentication
belongs to V2.6d and is not changed by V2.6b.

Authentication alone does not imply access. Once authorization enforcement is connected, the
authenticated user principal is evaluated for the endpoint's action and resource.

## Error contract

```text
missing bearer token       → 401
malformed bearer header    → 401
invalid signature/token    → 401
expired/not-yet-valid JWT  → 401
invalid required claims    → 401
authenticated but denied   → 403
```

All `401` responses include `WWW-Authenticate: Bearer` and one safe public message. Responses do not
distinguish signature, subject, issuer, audience, or timing failures. The protected audit log may
record a stable reason code but never the token or complete claims.

## React and SSE

The React API client obtains the bearer token from one authentication state and attaches it to
every protected `fetch` request. The minimal UI accepts an externally supplied token for the
current browser session; development mode may restore it from `sessionStorage`. Tokens are not
compiled into frontend assets and are never persisted to `localStorage`.

Native browser `EventSource` cannot attach the required `Authorization` header. Before the run
stream endpoint is protected, React must use an authenticated `fetch`-based SSE client that:

- sends the same bearer header as other API calls;
- parses the existing SSE contract without changing server event semantics;
- reconnects with `Last-Event-ID`;
- never places a token in the URL;
- preserves the current terminal-run and human-approval behavior.

## Normative scenarios

1. A valid development token and a valid JWT both produce the same user-principal shape.
2. Development mode with missing configuration fails startup instead of disabling authentication.
3. Missing, malformed, expired, wrongly signed, wrong-issuer, or wrong-audience tokens return the
   same safe `401` contract and execute no endpoint operation.
4. A valid token with no matching authorization policy returns `403`, not `401`.
5. JWT roles or permissions do not alter the user principal or grant product permissions.
6. Public health and static UI routes remain available without a token; protected API data does not.
7. SSE uses an authenticated header and reconnects from its durable event cursor without exposing
   the token in a URL.
8. Tokens and complete claims never appear in product events, MLflow metadata, or application logs.

## V2.6b scope

V2.6b adds the provider protocol, development and JWT implementations, a FastAPI dependency, safe
authentication errors, control-plane route protection, and the authenticated React API/SSE adapter.
It does not add OAuth, token issuance, users or roles in PostgreSQL, refresh tokens, multi-tenancy,
agent/runtime authentication, credential resolution, or capability-policy enforcement.

JWT handling must follow RFC 7519 and the algorithm, issuer, subject, and audience validation
recommendations in RFC 8725. Bearer transport follows RFC 6750.
