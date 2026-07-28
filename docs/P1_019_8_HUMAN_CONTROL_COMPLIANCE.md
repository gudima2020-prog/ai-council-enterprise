# P1-019.8 — Human Control Compliance

This stage adds a tamper-evident, append-only operator event journal, immutable compliance reports and privileged-access reviews.

## Integrity model

Operator events are redacted, normalized and chained with SHA-256. Each row stores its sequence, previous hash and own hash. Updates and deletes are blocked by SQLAlchemy listeners.

## Access review

The review scans privileged role bindings, privileged or unbounded API tokens, active break-glass grants and privileged sessions. Findings require an explicit operator decision. Remediation can revoke the underlying role binding, token, session or break-glass grant.

## Safety

Secrets, passwords, bearer tokens, cookies and authorization values are redacted before persistence. Reports are immutable snapshots and include an evidence hash.
