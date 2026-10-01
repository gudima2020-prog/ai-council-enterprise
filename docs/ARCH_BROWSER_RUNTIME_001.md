# ARCH-BROWSER-RUNTIME-001

## Status

Implementation candidate for Issue #12.

Base revision:

```text
fbb3d19fadc68092baf86e25085bcfa8871755a3
```

This slice adds the first executable BrowserRuntime for the contracts promoted by
`ARCH-BROWSER-CONTRACT-001`. It is deliberately restricted to a deterministic
local fixture inside a disposable Docker container. External network access,
credentials, Human Approval consumption, Council tool-runtime wiring and
evidence persistence remain future slices.

## Runtime profile

The only profile accepted by `BrowserRuntimeService` is:

```text
browser_engine       = chromium
network_mode         = fixture_only
filesystem_mode      = read_only_workspace
download_policy      = deny
capture_screenshots  = false
credential_scopes    = ()
effects               = (browser_read,)
requested_origins     = ()
```

Any other profile fails before `docker run`.

The deliberately narrow profile avoids silently interpreting a requirements
contract as an authorization grant. RUNTIME-001 does not accept
`restricted_external`, secrets, upload, download or external-write effects.

## Exact execution binding

Before runtime image inspection or execution, the service validates:

1. supplied source bytes SHA-256 equals
   `BrowserScriptArtifact.source_sha256`;
2. supplied artifact fingerprint equals
   `BrowserExecutionSpec.script_artifact_fingerprint`;
3. supplied raw input recomputes to the exact
   `BrowserExecutionSpec.input_fingerprint`;
4. the deterministic fixture root exists, is bounded and contains no
   symlink/junction indirection.

Raw input is transported to the container over stdin. Docker stdin is kept open
explicitly with `--interactive`; without that flag the container would receive
EOF instead of the bound input payload. The raw input is not placed in the Docker
command, environment or a persistent host file.

## Trusted image binding

The configured local image tag is not executed directly.

The host first resolves the tag to a local Docker image ID:

```text
sha256:<64 hex>
```

It then verifies the required labels **against that digest** and executes the
digest itself. This avoids a tag-only trust decision.

Required labels:

```text
org.ai-studio.browser-runtime=arch-browser-runtime-001
org.ai-studio.browser-runtime-version=fixture-v1
```

Runtime execution uses `--pull=never`. It does not download or rebuild an
image automatically.

## Container boundary

The execution command is shell-free and includes:

```text
--interactive
--rm
--name <unique run name>
--pull=never
--network=none
--ipc=none
--read-only
--cap-drop=ALL
--security-opt no-new-privileges=true
--pids-limit <contract bound>
--cpus <runtime bound>
--memory <contract bound>
--memory-swap <same bound>
--user 65534:65534
--tmpfs /tmp:rw,nosuid,nodev,...
```

Only two bind mounts are supplied:

- a transient host directory containing only the exact frozen `script.py` →
  `/input`, read-only;
- deterministic fixture root → `/fixture`, read-only.

The source is written before container creation and its directory is mounted
read-only. The transient source directory is created beside the already-validated
fixture root, on the same Docker-accessible project path, and is removed when the
run completes. It is not created under the Windows user Temp directory.

This is deliberate: Docker Desktop may reject both direct file binds and
directory binds from the user's Temp tree with `CreateFile ... Access is denied`
or generic `Access is denied` errors. The source staging directory contains only
the exact frozen `script.py`; only that transient directory is mounted at
`/input`. No permission/grant semantics are changed by this portability fix.

There is no host workspace write mount, Docker socket mount, browser profile,
cookie jar, credential mount or secret environment variable.

The verified image digest, not the mutable tag, is passed to `docker run`.

## Browser lifecycle

The trusted image owns the lifecycle:

1. trusted runner starts a loopback-only fixture HTTP server;
2. trusted worker creates Playwright and launches fresh headless Chromium;
3. worker creates a fresh BrowserContext and Page;
4. only then is the untrusted frozen script imported;
5. the script receives a `page` and a value-only context containing:
   - `fixture_origin`;
   - raw input for this exact execution;
6. result must be bounded JSON;
7. context and browser are closed;
8. container terminates and is removed.

The untrusted script is never asked to launch Chromium itself.

The worker emits browser/runtime metadata before importing the untrusted script,
then redirects process stdout/stderr to `/dev/null` before user code executes.
This keeps the trusted runner protocol separate from ordinary script output.
Unexpected extra protocol output fails closed.

## Fixture boundary

The fixture server binds only to `127.0.0.1` inside a container whose Docker
network mode is `none`.

The server:

- supports GET/HEAD only;
- serves only from the read-only fixture root;
- resolves requested paths under the fixture root;
- does not implement external redirects;
- returns `Cache-Control: no-store`;
- exists only for the duration of the run.

A browser script can attempt an external navigation, but Docker network
isolation remains the enforcement boundary. Model/script output cannot widen
egress.

## Runtime protocol

Trusted runner stdout is one bounded JSON envelope:

```json
{
  "schema_version": "arch-browser-runtime-001.runner.v1",
  "ok": true,
  "result": {},
  "browser_version": "…",
  "automation_runtime_version": "…"
}
```

Failure output contains only a bounded error class, not raw exception text.

The host rejects:

- missing output;
- malformed JSON;
- non-object output;
- schema mismatch;
- missing boolean `ok`;
- oversized stdout;
- missing runtime versions on success;
- non-zero runner exit.

No host fallback execution exists.

## Attestation

A successful execution returns `BrowserRuntimeExecutionResult` with a
`BrowserRuntimeAttestation` bound to:

- Task ID;
- Task revision ID;
- Workspace ID;
- generated run ID;
- exact script artifact fingerprint;
- exact input fingerprint;
- `docker-playwright-fixture` runtime provider;
- verified Docker image digest;
- Chromium engine/version;
- Playwright version;
- deterministic fixture-only network-policy fingerprint;
- start/finish timestamps;
- terminal result.

Timeout and runner failures produce a failed/timeout attestation where possible.

Attestation remains evidence. It is not an ALLOW, approval or capability grant.

## Timeout behavior

The Docker client call is bounded by
`BrowserRuntimeRequirements.max_runtime_seconds`.

The container receives a unique name. On timeout the host performs an explicit
`docker rm --force <name>` cleanup attempt before returning a timeout failure.
There is no retry or alternate runtime.

## Trusted image preparation

Run explicitly:

```cmd
prepare_arch_browser_runtime_001.bat
```

The preparation gate:

- requires a local Docker daemon;
- builds the dedicated runtime image;
- resolves the image tag to its local image digest;
- verifies trusted labels against that digest;
- runs the deterministic fixture smoke test by digest with `--interactive`,
  `--network=none` and the same isolation flags;
- prints the resulting image ID.

The runtime itself never auto-builds or auto-pulls.

## Security invariants

```text
SCRIPT_ARTIFACT != AUTHORIZATION
CANONICAL_BROWSER_REQUIREMENTS != AUTHORIZATION
REQUESTED_BROWSER_CAPABILITY != GRANTED_CAPABILITY

MODEL_OUTPUT != NETWORK_AUTHORITY
MODEL_OUTPUT != SECRET_SCOPE
MODEL_OUTPUT != CAPABILITY_GRANT

BROWSER_SESSION != PERSISTENT_STATE
RUNTIME_ATTESTATION != AUTHORIZATION
FIXTURE_ONLY != EXTERNAL_EGRESS
IMAGE_TAG != TRUSTED_IMAGE_ID
```

## Explicit non-goals

This slice does not implement:

- `restricted_external` egress;
- DNS/IP/redirect policy for external origins;
- credential resolution or injection;
- Human Approval request/consume;
- ToolExecutionRuntime integration;
- AgentToolRuntimeGovernance integration;
- ActionGateway;
- DB persistence/migration;
- persistent browser sessions/profiles;
- downloads/uploads/external write;
- screenshot/evidence materialization;
- BrowserEvidenceManifest;
- replay;
- Skill Registry/Factory/Router;
- Kubernetes/Agent Substrate.

## Files

```text
backend/browser_runtime/runtime.py
backend/browser_runtime/__init__.py
docker/browser_runtime/Dockerfile
docker/browser_runtime/requirements.txt
docker/browser_runtime/runner.py
docker/browser_runtime/worker.py
docker/browser_runtime/fixture/index.html
docker/browser_runtime/smoke_script.py
prepare_arch_browser_runtime_001.bat
tests/test_browser_runtime_fixture.py
docs/ARCH_BROWSER_RUNTIME_001.md
docs/ARCH_BROWSER_RUNTIME_001_INDEPENDENT_REVIEW_REQUEST.md
```

## Evidence classification

Repository tests performed by the implementation process are
`IMPLEMENTATION_SELF_TEST`.

A run performed by the operator on their workstation is `USER_RUN_TEST`.

Neither is Independent Review. Promotion still requires an exact frozen
candidate, independent review and explicit Human Approval.
