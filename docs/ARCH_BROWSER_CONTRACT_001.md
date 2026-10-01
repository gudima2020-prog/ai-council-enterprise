# ARCH-BROWSER-CONTRACT-001

## Status

Implementation candidate for Issue #9.

This slice defines immutable, provider-neutral contracts for governed browser
code-as-action execution. It does not add a browser runtime, Playwright,
Chromium, network access, credential injection, authorization wiring, database
persistence, Skill Registry, or Skill Factory.

## Design reference

Microsoft Webwright is used as an architectural reference for:

- code-as-action instead of long chains of browser micro-actions;
- workspace/code/logs/screenshots as durable state;
- disposable browser sessions;
- fresh replay;
- reusable executable browser skills.

Council does not embed Webwright or inherit its trust model. Council keeps its
existing governance boundaries and treats browser code, origins, capabilities,
credentials, evidence, and runtime facts as distinct trust domains.

## Core invariants

~~~text
BROWSER_SESSION != PERSISTENT_STATE
WORKSPACE_ARTIFACTS = PERSISTENT_STATE

SCRIPT_ARTIFACT != AUTHORIZATION
CANONICAL_BROWSER_REQUIREMENTS != AUTHORIZATION
REQUESTED_BROWSER_CAPABILITY != GRANTED_CAPABILITY

MODEL_OUTPUT != NETWORK_AUTHORITY
MODEL_OUTPUT != SECRET_SCOPE
MODEL_OUTPUT != CAPABILITY_GRANT

REPLAY_CONSISTENCY != TASK_CORRECTNESS
TASK_CORRECTNESS != SECURITY_VALIDITY
SECURITY_VALIDITY != INDEPENDENT_VERIFICATION

PUBLISHED_SKILL != EXECUTION_AUTHORIZATION
SKILL_PACKAGE != CAPABILITY_GRANT != EXECUTION_PERMISSION
~~~

No object in this slice contains an allow/deny result, granted capability,
approval result, authorization state, observed-evidence satisfaction result, or
secret value.

## Public contract surface

The implementation lives in:

~~~text
backend/browser_runtime/contracts.py
~~~

and is re-exported from:

~~~text
backend/browser_runtime/__init__.py
~~~

Schema version:

~~~text
arch-browser-contract-001.v1
~~~

### BrowserScriptArtifact

Binds a browser code artifact to artifact ID, revision ID, runtime kind, exact
source SHA-256, optional relative entrypoint, and explicit provenance
references.

The supported initial runtime kind is the contract value python-playwright. No
Playwright dependency is installed by this slice.

The artifact fingerprint is deterministic and domain-separated. It identifies
the exact artifact but grants no permission to execute it.

### BrowserExecutionSpec

Binds one proposed browser execution to Task ID, Task revision ID, Workspace ID
where present, execution initiator, exact script artifact fingerprint,
canonical input fingerprint, requested capabilities, browser effect
declarations, requested navigation origins, requested evidence, and exact
BrowserRuntimeRequirements.

The object is a requirements contract only.

Browser effect declarations are:

~~~text
browser_read
browser_external_write
browser_download
browser_upload
browser_secret_use
~~~

They classify requested effects. They are not grants.

### BrowserRuntimeRequirements

Describes provider-neutral runtime requirements:

- ephemeral runtime is mandatory;
- fresh browser session is mandatory;
- browser engine requirement;
- filesystem mode;
- network mode;
- download policy;
- runtime, memory, and PID ceilings;
- screenshot-capture requirement;
- credential scope references only.

Credential scopes are requests only and must use the existing metadata-only
`secret://provider/key` reference form. Raw credential values are not accepted
as credential scopes. Runtime/browser version fields also use a narrow bounded
version grammar rather than arbitrary text. Generic identifier fields reject
common credential/header/token/key encodings before serialization.

The contract therefore carries secret references or aliases only, never a
runtime-resolved secret value. Actual secret resolution remains a future
trusted runtime operation through the existing Secret Management boundary.

Contradictory combinations fail closed. Examples include read-only workspace
with workspace download, network mode none or fixture_only with requested
origins, restricted external network with no requested origin, browser-download
effect with deny download policy, and secret-use effects/scopes that do not
match.

### BrowserRuntimeAttestation

Represents observed runtime facts for a future BrowserRuntime:

- Task/revision/workspace/run binding;
- script artifact fingerprint;
- input fingerprint;
- runtime provider;
- runtime image digest;
- browser engine/version;
- automation runtime version;
- network-policy fingerprint;
- governance-policy fingerprints;
- start/finish timestamps;
- terminal result;
- failure class where applicable.

Attestation is evidence. It is never authorization.

### BrowserEvidenceItem / BrowserEvidenceManifest

Evidence is content-addressed by SHA-256 and bound to run ID, Task ID, Task
revision ID, Workspace ID, script artifact fingerprint, and input fingerprint.

Evidence items carry evidence ID, extensible evidence type, relative path or
logical key, SHA-256, byte size, redaction state, and privacy classification.
No raw artifact content is embedded in the manifest.

Relative evidence paths reject traversal, absolute paths, Windows drive paths,
alternate-data-stream style colon paths, and non-canonical empty/dot segments.
Duplicate evidence IDs and duplicate evidence locations fail closed.

## Canonicalization and fingerprinting

All contract fingerprints use deterministic canonical JSON and a
domain-separated SHA-256 input:

~~~text
<domain> + NUL + <canonical-json>
~~~

Dedicated domains prevent equivalent payloads in different contract classes
from sharing the same fingerprint namespace.

fingerprint_input() provides the canonical browser input fingerprint used by
BrowserExecutionSpec, RuntimeAttestation, and EvidenceManifest.

Unknown schema versions fail closed.

## Trust-boundary interpretation

Requested requirements such as capability, origin, credential scope, effect,
and evidence are never authority.

Artifact identity such as source SHA-256, script fingerprint, and input
fingerprint binds exact content but grants no permission.

Observed facts such as runtime attestation, evidence hashes, and terminal
result are provenance/evidence only. Attestation version metadata is restricted
to normalized version identifiers and is not a general free-text carrier.

Future authorization is not implemented here. A later enforcement slice must
independently bind exact Task/revision/script/input to Tool Registry, Workspace
Policy, Runtime Policy, Human Control, runtime isolation, credential scopes,
and concrete destination.

## Compatibility and non-goals

This slice does not modify TaskExecutor, governance
materialization/consumption, ToolExecutionRuntime, AgentToolRuntimeGovernance,
Runtime Policy, Human Control, Code Sandbox, Isolated Runtime, or database
schema.

It must not weaken the existing fail-closed code-execution boundary.

Explicit non-goals:

- no Playwright install;
- no Chromium install;
- no browser launch;
- no Webwright dependency;
- no Webwright agent loop;
- no terminal access;
- no external network;
- no browser egress gateway;
- no credential injection;
- no Human Approval consumption;
- no ToolExecutionRuntime authorization;
- no ActionGateway execution;
- no DB migration;
- no Skill Registry;
- no Skill Factory;
- no model/provider routing.

## Verification

Focused test:

~~~text
python -m pytest -q tests/test_browser_execution_contracts.py
~~~

Required governance regressions:

~~~text
python -m pytest -q tests/test_task_governance_planner.py tests/test_task_governance_materialization.py tests/test_task_governance_consumption.py
~~~

Required policy/runtime regressions:

~~~text
python -m pytest -q tests/test_agent_policy_enforcement_core.py tests/test_agent_human_approval_contract.py tests/test_execution_runtime_preflight.py tests/test_isolated_runtime_policy.py
~~~

The Independent Reviewer must additionally inspect source for hidden authority
semantics, mutable nested state, path-validation bypasses, secret carriers, and
runtime wiring outside this issue's scope.

## Future sequence

~~~text
ARCH-BROWSER-CONTRACT-001
        ↓
ARCH-BROWSER-RUNTIME-001
        ↓
ARCH-BROWSER-GOV-001
        ↓
ARCH-BROWSER-EGRESS-001
        ↓
ARCH-BROWSER-EVIDENCE-001
        ↓
ARCH-SKILL-REGISTRY-001
        ↓
ARCH-SKILL-FACTORY-001
        ↓
ARCH-SKILL-ROUTER-001
~~~

A future direct skill RUN still passes normal runtime governance. A published
skill never becomes an authority principal.
