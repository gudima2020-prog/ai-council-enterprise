# P2-007 — Code Sandbox & Verifiable Patches

P2-007 adds a controlled path from an AI-generated coding decision to a real
working-tree change. The isolation primitive is a detached Git worktree. It is
**not** an OS/container security boundary.

## Workflow

1. Create a sandbox from a clean Git repository and immutable base commit.
2. Modify only the generated worktree.
3. Inspect the staged patch and compute SHA-256/fingerprint.
4. Classify changed files and patch size as none/low/medium/high/critical.
5. Run verification profiles.
6. Obtain a one-time Human Approval token when protected/high-risk paths are involved.
7. Recompute the patch immediately before apply and reject any changed fingerprint.
8. Verify the base repository is still clean and at the original HEAD.
9. `git apply --check`, then apply the patch without creating a commit.
10. Remove the worktree when it is no longer needed.

## Security properties

- The target repository must be under an allowed root. With no extra config,
  only the AI Studio Enterprise repository itself is allowed.
- The base repository must be clean before sandbox creation and before apply.
- Git hooks and external diff commands are disabled for Sandbox Git commands.
- Repositories with configured clean/smudge/process filters or executable
  `core.fsmonitor` are rejected because those settings can run local commands;
  this configuration is checked again immediately before staging.
- Symlink and submodule entries are blocked.
- Real `.env` files, private keys/key stores, common credential files, SQLite/runtime
  databases, `.git`, `.venv` and `node_modules` are blocked and cannot be overridden.
- Protected paths such as `.env.example`, `.gitattributes`, `.gitmodules`, migrations,
  database/security/control-center code and dependency manifests require Human Approval.
- Approval tokens are stored only as SHA-256 hashes, expire after 15 minutes,
  are one-time, and are bound to the exact patch fingerprint.
- Patch artifacts are capped at 10 MiB.
- Verification output is capped before persistence/UI display.

## Verification profiles

- `diff_check` — safe Git whitespace/conflict check.
- `python_compile` — isolated `python -I -S -m compileall`; repository code is
  compiled but not imported.
- `pytest` — executes repository test code on the host and is disabled by default.
- `frontend_build` — executes package build scripts on the host and is disabled by default.

For a trusted local repository, enable host test/build execution explicitly:

```env
AI_STUDIO_CODE_SANDBOX_ALLOW_TEST_EXECUTION=1
```

Do not enable this for untrusted repositories. A later container/VM execution
runtime is required before claiming OS-level sandboxing.

Additional repository roots can be allowed explicitly on Windows:

```env
AI_STUDIO_CODE_SANDBOX_ALLOWED_ROOTS=C:\Projects\project-a;D:\Work\project-b
```

The current AI Studio Enterprise repository remains allowed automatically.

## API

```text
GET    /api/code-sandbox/status
GET    /api/code-sandbox/sessions
POST   /api/code-sandbox/sessions
GET    /api/code-sandbox/sessions/{session_id}
POST   /api/code-sandbox/sessions/{session_id}/inspect
POST   /api/code-sandbox/sessions/{session_id}/verify
POST   /api/code-sandbox/sessions/{session_id}/approval
POST   /api/code-sandbox/sessions/{session_id}/apply
GET    /api/code-sandbox/sessions/{session_id}/patch
DELETE /api/code-sandbox/sessions/{session_id}
```

## Database

Alembic revision `20260724_0049` adds:

- `code_sandbox_sessions`
- `code_sandbox_approvals`

The database stores audit metadata and fingerprints. Patch content itself is
kept as a temporary filesystem artifact outside the target repository with best-effort
owner-only permissions where the filesystem supports them. Alembic CLI uses the same
`AI_STUDIO_DATABASE_PATH` override as the application runtime.
