# P1-019.5 — Browser Security, CSRF and Trusted Clients

This stage adds a secure browser authentication channel to Human Control Center.

## Security model

- the session token is stored in an `HttpOnly` cookie;
- the CSRF token is stored in a readable cookie and must also be sent in the configured request header;
- state-changing requests require exact cookie/header equality and verification against the server-side SHA-256 hash;
- browser requests are admitted only from trusted Origins;
- optional user-agent and client-IP binding limits stolen-cookie reuse;
- Bearer API tokens remain independent from browser CSRF checks;
- browser secrets are returned only during login or CSRF rotation.

## New tables

- `human_control_browser_policies`
- `human_control_trusted_clients`

Browser sessions reuse `human_control_sessions`. Their browser marker and CSRF hash are stored in `metadata_json`, so existing session schema and compatibility are preserved.

## Safe defaults

Browser sessions are disabled until a policy is explicitly enabled. Trusted clients and Origin validation are required by default. Cookies default to `Secure`, `HttpOnly` for the session cookie, and `SameSite=Strict`.

For local HTTP development, set `cookie_secure=false` only in the development policy.
