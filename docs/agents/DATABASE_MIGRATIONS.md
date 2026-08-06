# Database Migration Policy

- Create one immutable Alembic revision with the current repository head as its
  `down_revision`. Never rewrite a revision that may already be shared.
- Keep model constraints and migration constraints equivalent, including
  Workspace keys, statuses, non-negative limits, and unique idempotency keys.
- Use explicit foreign-key deletion behavior. Document-derived content must be
  removed in the service transaction before a document tombstone is committed.
- Migration data transforms must be deterministic, bounded, restart-safe, and
  must not place raw content or secrets in console output.
- Add or update migration-manager tests for the new head, upgrade ordering,
  duplicate heads, and schema contract.
- Verify `python -m alembic heads` and record the exact head in the handoff.
- Do not mark a migration complete solely because `Base.metadata.create_all`
  works in an in-memory test.
