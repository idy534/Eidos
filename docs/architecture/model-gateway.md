# Model Gateway

R2 separates mutable model setup from immutable Run execution:

```text
ModelProfile (declared candidate)
  -> CapabilitySnapshot (locally resolved declaration)
  -> RunModelSnapshot (immutable)
  -> ModelGatewayLease
  -> ModelAttempt
```

`ModelProfile` stores provider identity, wire protocol, model ID, an indirect
authentication reference, declared limits/capabilities, timeout and retry
policy. Declarations are never presented as verified capability.

Eidos does not actively probe model capabilities. A pure local resolver applies
explicit user declarations first, then static provider preset values, then
conservative defaults. It records the source for every resolved capability and
never guesses context-window or maximum-output limits. A local snapshot's
legacy `reachable` and `authenticated` fields are always `false`: they do not
claim network reachability or credential validation.

Network reachability, authentication, permission, rate limiting and provider
compatibility are evaluated only by the first real `ModelAttempt`. Its existing
safe Model Error mapping reports the result without changing the Profile's
declared capabilities.

Eidos persists `ModelProfile` and `RunModelSnapshot`, then directly constructs
a Pydantic AI Provider and Model from that frozen configuration. `WireAPI`
selects `OpenAIResponsesModel` or `OpenAIChatModel`; Pydantic AI supplies the
corresponding request profile, protocol encoding and stream assembly. OpenAI,
DeepSeek, Moonshot and Qwen use their available Pydantic AI providers with the
Eidos-created OpenAI client. Volcengine Ark, MiniMax and custom compatible
endpoints use `OpenAIProvider` with that same client.

The Eidos-created `AsyncOpenAI` client remains authoritative for the resolved
secret, base URL, timeouts and `max_retries=0`. Its injected `httpx.AsyncClient`
uses Pydantic AI's public `AsyncTenacityTransport`; this is the only HTTP retry
executor. Pydantic AI never executes Eidos tools; Eidos retains conversation
progress, tool execution, approval, cancellation, sensitive scanning, SQLite
events and Run lifecycle authority.

```mermaid
sequenceDiagram
    participant Loop as Runtime Loop
    participant Gateway as ModelGateway
    participant Pydantic as Pydantic AI Provider + Model
    participant Retry as AsyncTenacityTransport
    participant API as Provider HTTP API
    participant Tools as Tool Runtime
    participant DB as SQLite

    Loop->>Gateway: acquire_lease(RunModelSnapshot)
    Gateway->>Pydantic: construct from frozen profile and Eidos client
    Pydantic->>Retry: streamed request
    Retry->>API: HTTP request (bounded retry before stream)
    API-->>Pydantic: provider-native stream
    Pydantic-->>Loop: Eidos model response/deltas
    Loop->>Tools: normalized tool calls
    Loop->>DB: terminal ModelAttempt metadata
    Loop->>Gateway: close lease
```

## Retry and cancellation

`RetryPolicy.max_attempts` is the total number of HTTP requests permitted in
one logical Model Attempt, including the first request. `AsyncTenacityTransport`
uses Tenacity's bounded exponential fallback and `Retry-After` handling for
408, 425, 429, 500, 502, 503 and 504 plus explicit HTTPX connection, timeout,
read/write and protocol failures. Other 4xx responses reach the existing model
error mapper without retry. OpenAI SDK retry stays disabled.

Eidos owns the retry classification, the frozen profile budget, cancellation
and the safety boundary. Transport sub-attempts stay inside one
`SamplingRuntime.sample` and never create another SQLite `model_attempt` row.
The request-scoped tracker projects the final transport retry count and safe
diagnostics into that one row. Once streaming consumption has begun, any text
is visible, a Tool Call is complete or a Tool Result is committed, Eidos does
not replay the request. Mid-stream resume/replay is not part of B3, and neither
is a Model Event Loop migration.

Renderer cancellation reaches both the active provider stream and the
request-scoped Tenacity sleep. Cancellation is persisted as cancellation and
never becomes a generic retryable provider error. Closing a Run lease closes
the SDK client and its injected HTTP client exactly once before closing the
dedicated model loop.

## Persistence and secrets

Schema revision 8 adds:

- `model_profiles`;
- `model_capability_snapshots`;
- `run_model_snapshots`;
- ModelAttempt lease, wire, model, timeout and retry-decision columns.

Run creation and `run_model_snapshots` insertion share one SQLite transaction.
Profile edits or deletion cannot change historical Run snapshots. SQLite is the
only business-state authority.

Profiles and Run snapshots contain only `env:*` or `local:*` authentication
references. Environment values are read on lease acquisition. Local secrets
use the existing Eidos private local configuration convention: a separate
owner-only `0600` file, never SQLite, Run events, JSON-RPC results or logs.

## JSON-RPC

The Runtime exposes:

- `model_profile/list`
- `model_profile/get`
- `model_profile/create`
- `model_profile/update`
- `model_profile/delete`
- `model_profile/list_presets`

All results are versioned Eidos DTOs. Provider-native bodies and exceptions do
not cross the Runtime boundary.

## Adding a provider

Add a preset for product defaults and map it directly to a locked Pydantic AI
provider only when that provider accepts the Eidos-resolved key, preserves the
configured base URL and has deterministic client ownership. Otherwise use
`OpenAIProvider` with the Eidos-created `AsyncOpenAI` client for compatible
endpoints. A new request wire requires a separate focused change that maps a
new `WireAPI` value to a Pydantic AI model class.
