# ARCH-BROWSER-RUNTIME-001 — Independent Review Request

## Role

Act as the Independent Reviewer for `ARCH-BROWSER-RUNTIME-001`.

Do not modify the candidate. Do not count implementation or user-run results as
independent evidence.

## Candidate identity

The exact revision/tree/bundle hashes will be supplied with the frozen review
bundle. Verify those independently before reviewing behavior.

## Scope

Review only the first fixture-only disposable BrowserRuntime.

Do not require future external egress, credential broker, Human Approval
consumption, ToolExecutionRuntime wiring, DB persistence, replay or Skill
Factory unless this candidate incorrectly claims to implement them or weakens an
existing boundary.

## Mandatory review questions

### A. Exact subject binding

Confirm before `docker run`:

1. source bytes must match `BrowserScriptArtifact.source_sha256`;
2. artifact fingerprint must equal the spec script fingerprint;
3. raw input must recompute to the exact spec input fingerprint;
4. malformed/non-canonical or oversized raw input fails closed;
5. fixture root is bounded and rejects symlink/junction indirection.

No drift may fall through to execution.

### B. Runtime profile narrowing

Confirm RUNTIME-001 accepts only:

```text
chromium
fixture_only
read_only_workspace
download deny
capture_screenshots false
no credential scopes
browser_read only
no requested external origins
```

Independently probe:

- `network_mode=none` → reject;
- `restricted_external` → reject;
- Firefox/WebKit → reject;
- writable workspace profile → reject;
- downloads → reject;
- upload → reject;
- external write → reject;
- secret-use/credential scopes → reject;
- screenshot-materialization request → reject.

All must fail before Docker execution.

### C. Docker trust boundary

Inspect exact command construction.

Confirm:

- `shell=False`;
- `--interactive` so the exact bound stdin payload reaches the container;
- `--network=none`;
- `--read-only`;
- `--ipc=none`;
- `--cap-drop=ALL`;
- `no-new-privileges=true`;
- bounded PIDs/CPU/RAM/time;
- memory swap does not exceed memory;
- numeric non-root user;
- writable tmpfs only;
- source staging directory contains only the exact frozen `script.py`;
- source staging directory is mounted read-only at `/input`;
- no individual writable source-file bind is used;
- fixture mount read-only;
- no host workspace write mount;
- no Docker socket;
- no secret env;
- no browser profile mount;
- `--pull=never`.

Verify the configured image tag is first resolved to an image digest, labels are
verified **on that digest**, and `docker run` executes the digest rather than
the mutable tag.

Look specifically for tag/label/run TOCTOU. The explicit preparation/smoke gate
must also resolve the tag to an image digest, verify labels on that digest and
run the smoke container by digest rather than by mutable tag.

### D. Timeout/cleanup

Confirm the container has a unique per-run name and timeout triggers explicit
forced removal.

Check that timeout does not silently retry or fall back to host execution.

On Windows, specifically verify the real service path succeeds with the
transient source **directory** bind created beside the validated fixture root,
not under the user's Temp tree. Earlier candidates failed with Docker daemon
return code 125 for both a direct Temp-file bind (`CreateFile ... Access is
denied`) and a Temp-directory bind (generic `Access is denied`). Regression to
either Temp-based staging path or either failure mode is a blocker.

### E. Trusted runner vs untrusted script

Inspect `runner.py` and `worker.py`.

Confirm:

- trusted runner owns fixture server;
- fresh Chromium and BrowserContext are created for each run;
- untrusted script receives an existing Page instead of browser-launch
  authority;
- worker metadata is emitted before user code import/execution;
- untrusted script stdout/stderr cannot become the trusted runner protocol;
- extra/malformed protocol bytes fail closed;
- the runner actually receives the stdin JSON payload through Docker rather
  than EOF (the smoke/runtime command must preserve stdin);
- untrusted result is treated as untrusted task output, not authority;
- raw exception text is not emitted in the protocol;
- result size is bounded.

Try adversarial scripts that print, write stderr, spawn a child that writes to
stdout, close the page/browser, return non-JSON, return oversized JSON and throw
an exception. None may create a successful forged trusted envelope.

### F. Network isolation

Confirm fixture server binds to loopback only.

Inspect fixture path translation for traversal/escape/symlink behavior.

Run or reason independently about attempts to navigate/fetch:

- fixture loopback → expected to work;
- public internet → must fail;
- metadata/private network targets → must fail because Docker network is none;
- WebSocket/external HTTP from user script → must not gain egress.

Browser/API routing logic is not accepted as the sole isolation boundary;
Docker `--network=none` must be present.

### G. Attestation semantics

Confirm success attestation binds exact:

- Task/revision/workspace/run;
- script fingerprint;
- input fingerprint;
- verified image digest;
- Chromium version;
- Playwright version;
- fixture-only network-policy fingerprint;
- timestamps/result.

Confirm attestation is evidence only and no field provides authorization,
approval or grant semantics.

### H. Existing boundaries

Confirm this slice:

- does not change `ToolExecutionRuntime`;
- does not change `AgentToolRuntimeGovernance`;
- does not weaken CODE_EXECUTION fail-closed behavior;
- adds no DB migration;
- adds no credential resolver/injection;
- adds no Human Approval consumption;
- adds no external egress;
- adds no Skill Registry/Factory/Router.

## Mandatory independent tests

Run:

```text
python -m pytest -q tests/test_browser_runtime_fixture.py
python -m pytest -q tests/test_browser_execution_contracts.py
python -m pytest -q tests/test_task_governance_planner.py tests/test_task_governance_materialization.py tests/test_task_governance_consumption.py
python -m pytest -q tests/test_agent_policy_enforcement_core.py tests/test_agent_human_approval_contract.py tests/test_execution_runtime_preflight.py tests/test_isolated_runtime_policy.py
```

If Docker is available, independently run the explicit runtime preparation/smoke
gate. Report that separately as independent runtime reproduction. If Docker is
not available, say so; do not fabricate execution evidence.

## Required output

Return exactly:

```text
VERDICT

BLOCKERS

NOTES

SECURITY INVARIANTS CONFIRMED

RISKS TO CARRY FORWARD
```

Use `PASS` only if no blocker remains. Artifact identity failure is
`BLOCKED`; a substantive implementation defect is `FAIL`.
