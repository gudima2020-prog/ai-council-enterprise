# P1-019.3 — Human Control Authentication

This stage adds authenticated operator and service identities without silently
breaking the existing P1-019.1/P1-019.2 operator APIs.

## Security properties

- passwords use PBKDF2-HMAC-SHA256 with a random 192-bit salt;
- session, API and activation secrets are returned only once;
- persistent storage contains only SHA-256 token hashes and short prefixes;
- failed login attempts trigger configurable account lockout;
- sessions have absolute and idle expiration;
- API tokens are scoped and may be Workspace-specific;
- break-glass access may require an independent approver;
- emergency sessions are short-lived and separately identifiable;
- authentication events are persisted and mirrored to the immutable audit trail.

## Compatibility modes

`legacy` keeps existing actor_id-based integrations operational.
`audit` records unauthenticated compatibility traffic for migration planning.
`enforce` is reserved for protected-route middleware introduced in P1-019.4.
