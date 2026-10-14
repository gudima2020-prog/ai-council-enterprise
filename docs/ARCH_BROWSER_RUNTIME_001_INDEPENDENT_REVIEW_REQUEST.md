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

## Previous V3 review findings requiring explicit re-test

The review of V3 revision
`c06622e88f9f4f6ead3071ab3a299133628fad6c` returned `FAIL` with four findings.
The next candidate must be checked specifically for all four:

1. Runtime configuration accepted `uid:gid` values with either component equal
   to zero. Confirm UID 0 and GID 0 are rejected before Docker command creation.
2. `_validate_profile()` accepted arbitrary `requested_capabilities` alongside
   `effects=(browser_read,)`. Confirm an explicit allowlist and consistent
   capability/effect validation run before image inspection or execution.
3. `_force_remove_container()` ignored `docker rm --force` return codes.
   Confirm both removal attempts are made, container absence is independently
   established using a successful Docker inventory, inventory errors fail
   closed, and cleanup is not reported successful while either container is
   present or its state is unknown.
4. The short-line flood probe could generate invalid Python source, and generic
   script-execution failure was accepted as success. Confirm the generated
   payload compiles before execution and that the probe requires evidence that
   the flood reached host protocol validation.

## Fresh Security Review findings requiring explicit re-test

Security Review of revision
`8cadc551da98bdaca4df674026ece060f0fb3434` returned `FAIL` with three
blockers. The next candidate must be checked specifically for all three:

1. **SEC-B03 — cleanup command errors:** a nonzero `docker rm --force` result,
   `OSError`, or timeout must fail cleanup even if a later successful inventory
   reports the exact container absent. Inventory must still be attempted, and
   both container removals must be attempted even if the first fails.
2. **SEC-B04 — short-line proof:** the probe must require host-generated,
   stage-specific evidence that the flood's first short line reached the script
   message dispatcher and was rejected there. A generic protocol exception,
   browser-ready error, script startup failure, or exception class alone is not
   sufficient. Preserve and check the stage/reason marker through runtime error
   attestation wrapping.
3. **SEC-B05 — RPC operation type:** validate `op` is a string before set
   membership. Object, array, null, numeric, and other non-string operation
   values must result in `BrowserRuntimeProtocolError`, fail-closed cleanup,
   and a typed runtime-error attestation rather than an uncaught `TypeError`.

Security Review of revision
`de534f9e980b2dfba8cb16cd9cd56315608e6f41` returned `FAIL` with blocker
`SEC-NEW-01`. The next candidate must be checked specifically for this finding:

4. **SEC-NEW-01 — cleanup ownership and original error preservation:** each
   mediated error path must perform one controlled removal attempt per active
   container. The outer `execute()` error mapping must not repeat cleanup that
   `_execute_mediated()` has already completed. When cleanup succeeds, timeout
   and protocol failures must retain their original typed error, failure
   attestation, and protocol stage/reason. Cleanup command or inventory errors
   must still fail closed. Regression tests must cover protocol and timeout
   paths and assert exact removal counts as well as preserved error metadata.

## Candidate identity

Exact revision/tree/base/bundle hashes are supplied in the immutable review
bundle.

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

Also confirm `requested_capabilities` has an explicit allowlist for this
runtime profile and is consistent with `effects=(browser_read,)`.

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

Attempt configuration with `0:0`, `0:<nonzero>` and `<nonzero>:0`; each must
fail before either Docker command is built.

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
Independently simulate `docker rm --force` nonzero status, inventory failure,
and each container remaining present. Cleanup must fail closed unless a
successful inventory confirms both exact names are absent. Verify the second
removal is attempted even when the first cannot be confirmed.

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

For bounded-I/O and strict-protocol changes, also inspect the V3 Docker probe
and independently verify oversized unterminated input, short-line floods, a
peer that does not read a large response, duplicate JSON keys, non-finite
numbers, exact response IDs and consistent `ok`/`result`/`error_code` shapes.
Compile the generated short-line-flood Python source before execution and
require the observed failure to come from host protocol validation; syntax or
generic script startup errors are not a successful probe. Require the exact
host-generated script-message stage/reason marker in the protocol exception and
attestation. Check that Docker inventory errors cannot be treated as proof of
cleanup and that Docker removal errors fail closed even if inventory shows the
container absent.

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
