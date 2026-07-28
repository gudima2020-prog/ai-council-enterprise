# P1-020.2 — Secret Access Policies, Ephemeral Leases and Runtime Injection

## Scope

P1-020.2 builds on the provider-backed secret registry introduced in P1-020.1.
It adds policy-evaluated access, short-lived lease credentials and runtime-only
secret injection for Tools and the AI Gateway.

The design goal is simple: the secret value may exist in memory only for the
smallest possible execution window, while task payloads, tool invocation rows,
EventBus payloads and normal logs continue to contain only `secret://...`
references or aliases.

## Access modes

`secret_access_settings.enforcement_mode` supports:

- `legacy` — backwards compatible. Policy decisions are calculated but do not
  block existing P1-020.1 consumers.
- `audit` — a denied decision is recorded as `would_allow=false`, but execution
  remains permitted. Use this to validate a policy before enforcement.
- `enforce` — the policy decision is authoritative.

The default installation remains `legacy` + `allow` to avoid breaking existing
integrations during upgrade.

## Policy matching

Policies can match:

- `secret://provider/key` patterns;
- action (`resolve`, `lease`);
- consumer type (`tool`, `gateway`, `system`, plugin-defined values);
- consumer key (tool key or provider name);
- actor patterns;
- runtime source;
- purpose text;
- Workspace.

Matching deny rules take precedence over matching allow rules. In `enforce`
mode, when no policy matches, `default_effect` is used.

## Ephemeral leases

A lease contains only authorization metadata and a SHA-256 hash of its opaque
lease token. The raw token is returned once when the lease is created and is
never persisted.

Leases are bound to:

- actor;
- consumer type;
- consumer key;
- Workspace;
- expiry time;
- maximum number of uses.

A one-use lease automatically becomes `consumed` after successful resolution.
Expired leases are reconciled to `expired`. Active leases may be revoked.

There is intentionally no HTTP endpoint that exchanges a lease for plaintext
secret material. Plaintext resolution remains an internal runtime operation.

## Tool integration

A tool declares references in metadata:

```json
{
  "secret_bindings": {
    "api_key": "secret://windows-dpapi/example/api-key"
  }
}
```

The handler receives no secret in `context.input` or persisted metadata. It may
request the value only while the invocation is active:

```python
api_key = await context.secret("api_key")
```

The Tool Runtime obtains a short-lived single-use lease, resolves the value in
memory, and closes the accessor after execution. Exact secret values that are
accidentally returned by a handler or embedded in an exception are redacted
before invocation output/error persistence.

## AI Gateway integration

Set:

```text
AI_STUDIO_OPENROUTER_SECRET_REF=secret://windows-dpapi/openrouter/primary
```

or point the reference at another configured provider.

When the reference is present it takes precedence over the legacy plaintext
`openrouter_api_key` setting. The Gateway creates an ephemeral lease, builds the
provider adapter from the in-memory key, executes the request and closes the
lease accessor immediately afterward.

## Operational rollout

Recommended rollout order:

1. Upgrade DB to Alembic `20260716_0042`.
2. Create/verify required secret records.
3. Keep access settings in `legacy` while creating policies.
4. Switch to `audit` and inspect `/api/secret-access-events` plus policy
   evaluation results.
5. Switch to `enforce` only after every required consumer has an explicit allow
   path or after a deliberate default effect is selected.

## Security properties

- plaintext secret values are not persisted by the lease subsystem;
- lease tokens are stored only as SHA-256 hashes;
- secret values are not included in Tool input/output persistence;
- Gateway logs continue to log request metadata, never provider credentials;
- exact-value output/error exfiltration is redacted by the runtime accessor;
- policy and lease actions remain observable through the existing audit/event
  architecture.

This is an application security boundary. It does not replace OS process
isolation for untrusted native code.
