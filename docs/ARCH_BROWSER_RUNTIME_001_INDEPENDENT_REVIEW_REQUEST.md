# ARCH-BROWSER-RUNTIME-001 — Independent Review Request

## Role

Act as the Independent Reviewer for `ARCH-BROWSER-RUNTIME-001`.

Do not modify the candidate. Do not count implementation, operator USER_RUN or
Security Review results as independent evidence.

## Historical blocker context

Historical frozen revision
`0552b49bbbb56283363d12d7ecc94469809bcbc1` received Independent Review
`FAIL` with two blockers:

1. `download_policy=deny` was not enforced because the trusted context omitted
   `accept_downloads=False`;
2. arbitrary Python received a native Playwright `Page`, allowing it to regain
   BrowserContext/Browser authority and launch another Chromium.

The remediation candidate must be judged specifically on whether those blockers
are actually removed, not merely renamed or hidden.

## Candidate identity

Exact revision/tree/base/bundle hashes are supplied in the frozen review bundle.

Verify independently before behavioral review.

## Scope

Review only the first fixture-only mediated BrowserRuntime.

Do not require future external egress, credentials, Human Approval,
ToolExecutionRuntime wiring, DB persistence, replay or Skill Factory unless the
candidate claims or weakens those boundaries.

## Mandatory review questions

### A. Exact subject binding

Confirm before any runtime execution:

1. source SHA matches `BrowserScriptArtifact.source_sha256`;
2. artifact fingerprint matches execution spec;
3. raw input fingerprint recomputes exactly;
4. malformed/non-canonical or oversized input fails closed;
5. fixture root is bounded and rejects symlink/junction indirection.

### B. Narrow profile

Confirm only:

```text
chromium
fixture_only
read_only_workspace
download deny
capture_screenshots false
no credential scopes
browser_read only
no external origins
```

Unsupported profiles must fail before image execution.

### C. Two-image trust boundary

Confirm the host resolves and verifies **both** runtime images:

```text
browser runtime
script runtime
```

For each:

- configured tag -> local `sha256:` digest;
- trust/version labels inspected on that digest;
- `docker run` uses digest, not tag;
- `--pull=never`.

The script-runtime image must be minimal and must not contain Playwright or
Chromium/browser binaries.

Try importing Playwright and invoking known Chromium/browser executable names
inside the exact script-runtime image. Those attempts must fail.

### D. Container isolation

Confirm **both** containers use:

- shell-free invocation;
- `--interactive`;
- `--network=none`;
- `--read-only`;
- `--ipc=none`;
- `--cap-drop=ALL`;
- `no-new-privileges=true`;
- numeric non-root UID/GID;
- bounded PIDs/CPU/RAM/swap/time;
- bounded writable tmpfs;
- no Docker socket;
- no credentials/secrets/browser-profile mounts.

Browser container may mount only the fixture root read-only.

Script container may mount only the frozen source staging directory read-only.

### E. Download blocker remediation

This is mandatory.

Confirm trusted BrowserContext creation explicitly uses:

```python
accept_downloads=False
```

Independently use the deterministic attachment fixture
`/download.txt` and attempt to obtain a usable download.

The untrusted script must not be able to obtain:

- a Download object;
- a download path;
- suggested filename + persisted bytes;
- readable `HELLO_DOWNLOAD` contents from a downloaded artifact.

A policy declaration alone is not sufficient.

### F. Native Page/browser authority blocker remediation

This is mandatory.

The untrusted script must receive a mediated `PageProxy`, not a native
Playwright Page.

Independently probe from untrusted code:

- `page.context`;
- `page.browser`;
- private/protected attribute discovery;
- constructing BrowserContext;
- importing `playwright.async_api`;
- launching Chromium via Playwright;
- launching Chromium/browser binaries with `subprocess`;
- connecting to an existing browser/DevTools endpoint;
- sending an unsupported raw RPC op.

None may create another Browser, BrowserContext or unmanaged Page.

The trusted browser container must be the only process with Playwright/browser
authority.

### G. Host-mediated RPC

Confirm the containers have no direct communication path.

The host must mediate a fixed protocol vocabulary.

Current allowed operations are only:

```text
goto
locator_inner_text
```

Confirm:

- exact versioned schemas;
- exact key sets;
- monotonically increasing request IDs;
- bounded request count;
- bounded line size;
- malformed JSON fails closed;
- unknown fields fail closed;
- unknown operations fail closed;
- browser response ID/schema/shape mismatch fails closed.

Attempt protocol injection from:

- user stdout;
- user stderr;
- spawned child stdout/stderr;
- direct writes to inherited descriptors;
- malformed/duplicate/out-of-order request IDs.

No such attempt may forge browser metadata or widen RPC authority.

### H. Navigation / fixture confinement

Confirm trusted `goto` accepts only the exact per-run loopback fixture origin.

Independently attempt:

- public HTTP/HTTPS;
- private/metadata IPs;
- alternate loopback port;
- `file:`;
- `data:`;
- WebSocket;
- query/path traversal forms as applicable.

Browser/API validation is defense in depth; Docker `--network=none` must remain
the hard external-egress boundary.

### I. Script-runtime process authority

The script runtime is arbitrary Python, but must not have browser authority.

Probe:

- subprocess creation;
- package inspection;
- filesystem inspection;
- `/proc` inspection;
- PID/signal attempts;
- child process protocol injection.

These may not enable access to trusted browser stdio/processes or another
browser.

### J. Trusted browser lifecycle and attestation

Confirm:

- trusted browser container launches fresh Chromium;
- one trusted BrowserContext/Page is created;
- context has `service_workers="block"` and `accept_downloads=False`;
- browser/runtime metadata is emitted only by trusted browser container;
- success task result may be untrusted, but browser metadata cannot come from
  the script container;
- browser image digest is attested;
- script runtime digest is returned as execution metadata;
- task/revision/workspace/run/script/input/network/timestamps remain bound;
- attestation has no authorization semantics.

### K. Timeout / cleanup

Confirm unique names for both containers.

On timeout both must receive forced-removal attempts.

Check no host fallback/retry exists.

Re-test Windows staging ACL remediation:

- project-path staging;
- no `TemporaryDirectory` / Windows `0o700` regression;
- read-only script mount;
- staging cleanup after execution;
- cleanup failure is fail-closed.

### L. Existing boundaries

Confirm this remediation does not introduce:

- ToolExecutionRuntime wiring;
- AgentToolRuntimeGovernance wiring;
- CODE_EXECUTION fallback;
- credential injection;
- Human Approval consumption;
- DB migration/persistence;
- external-egress policy;
- Skill Registry/Factory/Router.

## Mandatory independent tests

Run:

```text
python -m pytest -q tests/test_browser_runtime_fixture.py
python -m pytest -q tests/test_browser_execution_contracts.py
python -m pytest -q tests/test_task_governance_planner.py tests/test_task_governance_materialization.py tests/test_task_governance_consumption.py
python -m pytest -q tests/test_agent_policy_enforcement_core.py tests/test_agent_human_approval_contract.py tests/test_execution_runtime_preflight.py tests/test_isolated_runtime_policy.py
```

If Docker is available, independently run
`prepare_arch_browser_runtime_001.bat` (or equivalent exact commands) and then
run adversarial scripts against the exact built image digests.

If Docker is unavailable, state that explicitly and do not claim Docker
reproduction.

## Required output

Return exactly:

```text
VERDICT

BLOCKERS

NOTES

SECURITY INVARIANTS CONFIRMED

RISKS TO CARRY FORWARD
```

Use `PASS` only if `BLOCKERS=NONE`.

Artifact identity failure is `BLOCKED`; a substantive implementation/security
defect is `FAIL`.
