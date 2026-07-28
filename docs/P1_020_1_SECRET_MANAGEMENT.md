# P1-020.1 — Secret Management Foundation

This stage introduces a metadata-only secret registry and provider abstraction.
Plaintext secret values are never returned by the HTTP API and are never stored
in `secret_records`.

Built-in providers:

- `env` — read-only references to process environment variables.
- `windows_dpapi` — writable local vault encrypted with Windows DPAPI.
- `external` — provider metadata whose `adapter_key` is supplied by a plugin or
  later cloud/Vault integration.

Reference format:

```text
secret://provider-key/secret-key
```

Examples:

```text
secret://env/OPENAI_API_KEY
secret://windows-dpapi/openai/primary
```

Provider config is intentionally non-secret. Keys containing password, token,
secret, API key, private key, or credential markers are rejected. External
provider authentication should itself use a `secret://` reference.

The API never exposes a plaintext resolve endpoint. Runtime services resolve
secrets through `SecretManagerService.resolve_ref()` and every resolution is
recorded in the immutable `secret_access_events` ledger.

## External provider adapters

Plugins may register an adapter at runtime:

```python
container.secret_manager_service.register_provider_adapter(my_adapter)
```

The adapter must expose `key`, `read_only`, `health()`, `store()` and
`resolve()`. Provider configuration may contain non-secret connection metadata
and `*_ref` fields pointing to other `secret://` references, but it must not
contain credentials directly.

## Windows DPAPI backup note

The default `windows-dpapi` provider uses user scope. Its encrypted values can
normally be decrypted only by the same Windows user profile. A database backup
therefore preserves ciphertext, but disaster recovery under another Windows
account requires either machine scope or an external provider.

## HTTP security

Secret Management endpoints require an authenticated operator or API token even
when the wider Human Control policy remains in legacy mode. Permissions are:
`secret.view`, `secret.manage`, `secret.rotate`, and `secret.audit`. There is no
HTTP endpoint that returns plaintext secret material.
