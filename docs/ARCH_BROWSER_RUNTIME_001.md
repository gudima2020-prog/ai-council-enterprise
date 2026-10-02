# ARCH-BROWSER-RUNTIME-001

## Status

Implementation candidate reopened after Independent Review FAIL on historical
revision `0552b49bbbb56283363d12d7ecc94469809bcbc1`.

Base revision remains:

```text
fbb3d19fadc68092baf86e25085bcfa8871755a3
```

The remediation changes the first fixture-only BrowserRuntime from an
"in-container native Playwright Page handed to arbitrary Python" design to a
**mediated two-container design**.

The purpose is to enforce, rather than merely document, the boundary:

```text
UNTRUSTED SCRIPT != NATIVE PLAYWRIGHT AUTHORITY
UNTRUSTED SCRIPT != BROWSER/CONTEXT CREATION AUTHORITY
```

## Runtime profile

The only accepted profile remains:

```text
browser_engine       = chromium
network_mode         = fixture_only
filesystem_mode      = read_only_workspace
download_policy      = deny
capture_screenshots  = false
credential_scopes    = ()
effects              = (browser_read,)
requested_origins    = ()
```

Any other profile fails before runtime image execution.

## Exact execution binding

Before Docker execution the service validates:

1. source bytes SHA-256 equals `BrowserScriptArtifact.source_sha256`;
2. artifact fingerprint equals
   `BrowserExecutionSpec.script_artifact_fingerprint`;
3. raw input recomputes to the exact input fingerprint;
4. fixture root exists, is bounded and contains no symlink/junction
   indirection.

Raw input is sent only through the script-runtime stdin protocol. It is not put
in Docker argv, environment variables or persistent host files.

## Mediated architecture

The host starts two disposable containers by verified digest:

```text
untrusted script container
        |
        | narrow JSON RPC over host-relayed stdin/stdout
        v
trusted browser container
        |
        v
Playwright -> Browser -> BrowserContext -> Page -> fixture
```

The two containers have no direct network path to each other.

### Trusted browser container

The browser container:

- contains Playwright and Chromium;
- owns the loopback fixture HTTP server;
- launches fresh Chromium;
- creates the only trusted BrowserContext and Page;
- creates the context with:
  - `service_workers="block"`;
  - `accept_downloads=False`;
- accepts only the narrow operations:
  - `goto`;
  - `locator_inner_text`;
- accepts navigation only to the exact per-run fixture origin;
- never imports or executes the untrusted script.

The trusted browser server emits browser/Playwright metadata before processing
script operations.

### Untrusted script container

The script container:

- is built from a minimal Python image;
- contains no Playwright dependency and no Chromium installation;
- receives only the frozen `script.py` mount;
- has `--network=none`;
- receives a `PageProxy`, not a native Playwright Page;
- has no `page.context`, `page.browser`, `Browser` or `BrowserContext`
  API surface;
- can request only the host-approved RPC vocabulary.

The script interface remains:

```python
async def run(page, context):
    ...
```

but `page` is a capability-limited proxy.

This preserves the authoring shape while removing native Playwright/browser
authority from untrusted Python.

## Host mediation

The host is the only relay between the two containers.

It:

1. validates the trusted browser and script images;
2. starts the browser container;
3. validates the exact browser `ready` envelope;
4. starts the script container;
5. sends exact raw input + fixture origin;
6. relays only a fixed RPC vocabulary;
7. enforces monotonically increasing request IDs;
8. enforces a bounded request count;
9. requires exact protocol key sets;
10. rejects unknown fields/operations;
11. returns only bounded canonical-JSON task output.

No RPC allows:

- Browser creation;
- BrowserContext creation;
- arbitrary Playwright evaluation;
- arbitrary filesystem access;
- download access;
- network configuration;
- credentials;
- process control.

## Download denial

The historical Independent Review blocker was valid: the old worker declared
`download_policy=deny` but did not set `accept_downloads=False`.

The mediated browser server now explicitly creates the context with:

```python
browser.new_context(
    service_workers="block",
    accept_downloads=False,
)
```

The deterministic fixture includes an attachment response
(`download.txt`) specifically for negative testing.

The untrusted proxy also exposes no download object/path API.

## Container boundary

Both containers are shell-free and run with:

```text
--interactive
--rm
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

Browser container host mounts:

- deterministic fixture root -> `/fixture`, read-only.

Script container host mounts:

- transient directory containing only the exact frozen `script.py` ->
  `/input`, read-only.

There is no Docker socket, writable host workspace, browser profile, credential
mount or secret environment variable.

## Trusted image binding

The host independently resolves and verifies two local images:

```text
ai-studio-browser-runtime:mediated-v2
ai-studio-browser-script-runtime:mediated-v1
```

For each image:

1. tag resolves to local `sha256:<64 hex>`;
2. trust/version labels are read from that digest;
3. execution uses the digest;
4. `--pull=never` is used at runtime.

The BrowserRuntime attestation continues to bind the trusted browser image
digest. `BrowserRuntimeExecutionResult` separately carries the script-runtime
image digest.

## Windows staging

The source staging directory is created beside the validated fixture root.

Windows uses a non-`0o700` mkdir mode so Python 3.13 does not apply its
special restrictive ACL that previously caused Docker Desktop
`Access is denied`.

POSIX staging remains `0o700`.

Cleanup failure is fail-closed.

## Fixture boundary

The trusted fixture server binds only to `127.0.0.1` inside the browser
container.

It:

- supports GET/HEAD only;
- resolves paths under the read-only fixture root;
- rejects traversal/escape;
- has no external redirect implementation;
- exists only for the run.

The trusted `goto` operation additionally rejects navigation outside the
exact fixture origin.

Docker `--network=none` remains the hard external-egress boundary.

## Protocol trust

There are two explicit versioned protocols:

```text
arch-browser-runtime-001.browser-server.v1
arch-browser-runtime-001.script-runner.v1
```

The host requires exact envelope key sets.

Untrusted stdout/stderr is redirected to `/dev/null` before the user module is
loaded. The script-runner protocol uses duplicated non-inheritable file
descriptors, so child processes inherit only suppressed standard streams.

Browser metadata can originate only from the trusted browser container.

## Attestation

Success attestation binds:

- task/revision/workspace/run;
- script artifact fingerprint;
- input fingerprint;
- trusted browser image digest;
- Chromium version;
- Playwright version;
- mediated fixture-only network-policy fingerprint;
- timestamps/result state.

`BrowserRuntimeExecutionResult` additionally exposes the script-runtime image
digest.

Attestation remains evidence only. It is not authorization.

## Timeout behavior

The total mediated run is bounded by
`BrowserRuntimeRequirements.max_runtime_seconds`.

Each container has a unique run-derived name. Timeout triggers forced-removal
attempts for both containers. There is no retry or host execution fallback.

## Explicit preparation

`prepare_arch_browser_runtime_001.bat` now:

1. builds the browser image;
2. builds the minimal script image;
3. resolves both tags to image digests;
4. verifies labels on both digests;
5. runs a real mediated `BrowserRuntimeService` smoke;
6. prints both image IDs.

The runtime never auto-builds or auto-pulls.

## Security invariants

```text
SCRIPT_ARTIFACT != AUTHORIZATION
CANONICAL_BROWSER_REQUIREMENTS != AUTHORIZATION
REQUESTED_BROWSER_CAPABILITY != GRANTED_CAPABILITY

UNTRUSTED_SCRIPT != NATIVE_PLAYWRIGHT_OBJECT
UNTRUSTED_SCRIPT != BROWSER_CREATION_AUTHORITY
UNTRUSTED_SCRIPT != BROWSER_CONTEXT_AUTHORITY
PAGE_PROXY != PLAYWRIGHT_PAGE

MODEL_OUTPUT != NETWORK_AUTHORITY
MODEL_OUTPUT != SECRET_SCOPE
MODEL_OUTPUT != CAPABILITY_GRANT

DOWNLOAD_POLICY_DENY => ACCEPT_DOWNLOADS_FALSE
BROWSER_SESSION != PERSISTENT_STATE
RUNTIME_ATTESTATION != AUTHORIZATION
FIXTURE_ONLY != EXTERNAL_EGRESS
IMAGE_TAG != TRUSTED_IMAGE_ID
```

## Explicit non-goals

This slice still does not implement:

- restricted external egress;
- credentials;
- Human Approval consumption;
- ToolExecutionRuntime wiring;
- AgentToolRuntimeGovernance wiring;
- ActionGateway;
- DB persistence;
- persistent browser sessions/profiles;
- upload/download capability;
- screenshots;
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
docker/browser_runtime/service_smoke.py
docker/browser_runtime/smoke_script.py
docker/browser_runtime/fixture/index.html
docker/browser_runtime/fixture/download.txt

docker/browser_script/Dockerfile
docker/browser_script/script_runner.py

prepare_arch_browser_runtime_001.bat
tests/test_browser_runtime_fixture.py
docs/ARCH_BROWSER_RUNTIME_001.md
docs/ARCH_BROWSER_RUNTIME_001_INDEPENDENT_REVIEW_REQUEST.md
```

## Evidence classification

All historical evidence for revision `0552b49...` is historical only after
the Independent Review FAIL.

Any evidence for this remediation candidate must be regenerated.

Operator runs are `USER_RUN_TEST`. Security Review is not Independent Review.
Promotion still requires a fresh frozen candidate, independent review and
explicit Human Approval.
