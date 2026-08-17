# P3-002 — Governed Developer Agent Profiles

## P3-002.1a — Policy profile and context checkpoint core

Status: **implemented as a pure core contract**.

This slice creates no database tables, API routes, tool hooks, provider calls,
repository mutations, commits or remote operations. It defines the immutable
contracts that later enforcement and persistence slices must use.

### Public seams

`backend.agent_governance` exports:

- `AgentPolicyProfile` — normalized, bounded and fingerprinted manifest;
- `AgentToolRequest` — metadata-only description of one proposed tool action;
- `AgentPolicyEvaluator` — deterministic profile-level restriction decision;
- `AgentProfileRegistry` — fail-closed lookup for audited profiles;
- `AgentContextCheckpoint` — bounded continuation state with integrity
  fingerprint;
- six built-in profiles: `safe-development`, `research-readonly`,
  `document-analysis`, `repository-review`, `release-manager` and
  `production-operator`.

Each profile defines exact allowed tools, an immutable capability binding for
every tool, denied actions, mandatory checks, approval-triggering capabilities,
external domains, accepted data classifications, filesystem/network
boundaries, primary model selector, reviewer model selector, manifest version
and audit version. A request may add capabilities but cannot remove the audited
capabilities bound to its tool.

### Decision semantics

Profile evaluation has deterministic precedence:

1. unknown tool, denied action, disallowed classification or capability
   boundary produces `deny`;
2. if no denial exists and a declared approval condition is triggered, the
   result is `require_approval`;
3. otherwise the result is `allow` at the profile layer only.

Every decision includes stable reason codes, the profile fingerprint,
mandatory checks, triggered approval conditions, the effective union of bound
and request-declared capabilities, a metadata-only request snapshot and its own
SHA-256 fingerprint.

`allow` never grants platform permission. Before execution, later hooks must
still obtain the effective Workspace Policy decision, canonical Runtime Policy
decision and any exact-scope Human Control approval. Hooks may deny or require
more controls; they must never turn a canonical denial into permission.
Tool ID, action ID, domain and request capabilities must be derived by a trusted
tool adapter, never accepted as authority from model-generated arguments.

### Built-in defaults

| Profile | Filesystem | Network | Data classifications | Important approval conditions |
|---|---|---|---|---|
| `safe-development` | read/write | restricted exact domains | public, internal, confidential | stage, commit, remote mutation, external network |
| `research-readonly` | read-only | restricted exact domains | public, internal | external network |
| `document-analysis` | read-only | denied | all classifications | canonical document/Gateway policy remains mandatory |
| `repository-review` | read-only | denied | public, internal, confidential | none; mutation tools/actions are denied |
| `release-manager` | read/write | GitHub domains only | public, internal | stage, commit, remote mutation, external network |
| `production-operator` | read/write | GitHub domains only | all classifications | writes, remote/network and production change |

The built-ins are conservative audited defaults, not tenant configuration.
Workspace-scoped custom profile persistence and selection belong to the next
slice.

### Context preservation contract

`AgentContextCheckpoint` preserves only bounded task metadata:

```json
{
  "schema_version": "p3-002.1a.context-checkpoint",
  "objective": "...",
  "confirmed_facts": [],
  "changed_files": [],
  "test_results": [],
  "open_questions": [],
  "blocked_actions": [],
  "branch": "feature/p3-002",
  "current_commit": "full-git-sha",
  "migration_head": "20260806_0057",
  "next_safe_action": "...",
  "workspace_id": "...",
  "profile_id": "safe-development",
  "profile_fingerprint": "sha256"
}
```

Repository paths must be relative and traversal-free; Windows drive,
drive-relative and NTFS alternate-data-stream forms are rejected. Git SHA,
migration head,
profile identity and profile fingerprint are validated. Unknown fields and
unknown schema versions fail closed. A checkpoint envelope adds its own
SHA-256 fingerprint and rejects modified content during restore.

The fingerprint detects accidental or untrusted modification only when its
expected value is held by a trusted evidence boundary. It is not a signature:
checkpoint persistence must either protect the expected fingerprint or add a
signed/MAC-bound envelope before automated restore.

Raw prompts, source/document content, model responses, secrets, approval
tokens, credentials and personal data are outside this contract. Persistence,
retention, redaction evidence and Workspace API are intentionally deferred.
The persistence slice must add secret/PII redaction or scanning before accepting
these free-text summary fields.



## P3-002.1b — Workspace profile persistence, selection and API

Status: **implemented and verified**.

This slice adds:

- Alembic revision `20260807_0058` with Workspace-scoped immutable custom
  profile versions and one active profile selection per Workspace;
- exact `(workspace_id, profile_id, version)` immutability with idempotent
  replay only when the stored manifest and fingerprint are identical;
- fail-closed reconstruction of persisted manifests and SHA-256 integrity
  verification on every read and active-selection resolution;
- immutable code-owned built-in profiles that are selectable but cannot be
  created or replaced as custom Workspace configuration;
- a canonical trusted tool-capability catalog; custom profiles may select
  trusted tools but cannot under-declare their security capabilities;
- exact active binding to `profile_id + version + profile_fingerprint`;
- Human Control-bound REST routes for built-in discovery, custom
  create/list/get, active get and active selection;
- `human_control.view` for reads and `human_control.manage_policies` for
  mutations, with authenticated actor binding required for every mutation;
- cross-Workspace path binding and repository isolation.

The profile layer remains restrictive only. Selecting or storing a profile
does not grant provider, filesystem, network, repository, Runtime Policy or
Workspace Policy permission. Before/After Tool Execution composition remains
a later P3-002 slice.

Verification entry point: `verify_p3_002_1b.bat`.

## P3-002.2a — Before Tool Execution enforcement core

Status: **implemented and under verification**.

This slice adds a pure, deterministic and fail-closed composition boundary
before real tool execution. It does not yet wire the decision into
`ToolExecutionRuntime`, consume approval tokens, execute tools, call providers,
mutate repositories, create persistence rows or publish enforcement events.

`BeforeToolExecutionEnforcer` evaluates the selected Agent Policy Profile and
joins it with independently produced evidence from the Tool Registry,
Workspace Policy and canonical Runtime Policy. Human Control evidence is
accepted only as already validated policy evidence; exact-scope approval
lookup, token validation/consumption and evidence emission remain a runtime
adapter responsibility for the next slice.

The composition is monotonic:

1. structural integrity failures, profile denial or any canonical denial
   produce `deny`;
2. unresolved canonical isolation remains `require_isolation`;
3. unresolved profile/canonical approval remains `require_approval`;
4. `allow` is possible only when all required canonical layers are present,
   Workspace-bound and non-denying, the exact selected profile fingerprint
   matches, and every requested approval has trusted Human Control evidence.

Human Control can satisfy an approval requirement but can never override a
profile/canonical denial, replace a missing canonical layer or remove Runtime
Policy isolation.

Required canonical evidence layers are Tool Registry, Workspace Policy and
Runtime Policy. Missing or duplicate layers fail closed. Layer evidence carries
the Workspace ID, action, reason codes, upstream policy version and upstream
fingerprint. The canonical Runtime Policy adapter preserves all four Runtime
Policy outcomes: `allow`, `require_approval`, `require_isolation` and `deny`.

The enforcement decision carries the Agent Policy decision, layer evidence,
stable reason codes, unresolved approval/isolation state, mandatory checks and
a deterministic SHA-256 fingerprint. It is metadata-only: tool input, prompts,
model arguments, secrets and credentials are deliberately excluded.

`AgentToolRequest` must be constructed from trusted Tool Registry/adapter
metadata. Model-generated arguments are never authority for tool identity,
action, capabilities, domain, Workspace or canonical policy evidence.

Verification entry point: `verify_p3_002_2a.bat`.



## P3-002.2b-A — Trusted enforcement adapters

Status: **implemented and under verification**.

This slice introduces the trusted adapter boundary between existing platform
policy sources and the pure P3-002.2a Before Tool Execution composition core.
It does **not** wire enforcement into `ToolExecutionRuntime` and does not
consume Human Control approvals.

Implemented contracts:

- a persisted `ToolDefinitionModel` must explicitly bind
  `agent_policy_tool_id`, `agent_policy_action_id`, and
  `agent_policy_runtime_operation`;
- governed tool capabilities come from the code-defined agent capability
  catalog, not from model output or request arguments;
- Tool Registry capability flags must exactly match the governed boundary
  capabilities and drift fails closed;
- Tool Registry evaluation is integrity-checked and converted to a
  fingerprinted `TOOL_REGISTRY` layer decision;
- effective Workspace Policy is converted to a restrictive
  `WORKSPACE_POLICY` layer decision; denied filesystem/network boundaries
  remain denials and restricted network access requires approval;
- Runtime Policy is evaluated from trusted binding + effective Workspace
  classification and its four canonical outcomes, including
  `REQUIRE_ISOLATION`, are preserved;
- request Workspace and classification drift, forged governed tool identity,
  forged action identity, and capability drift fail closed;
- adapter and upstream decision fingerprints are deterministic and bind the
  policy evidence used for composition.

Trust-boundary preconditions for the later runtime wiring:

1. `ToolDefinitionModel` must be loaded by the platform Tool Repository in the
   execution path; an agent/model-provided object is never authoritative.
2. The three `agent_policy_*` metadata bindings are security-sensitive
   registry configuration. Their mutation must be governed before P3-002.2b-C
   permits real handler execution through this adapter.
3. `external_domain` for a network tool must be derived by trusted,
   tool-specific code from the validated execution destination; a model claim
   is not authoritative.
4. One `ToolDefinition` must represent one security-relevant governed action,
   or a trusted tool-specific action classifier must run before enforcement.
   Model arguments must never select a less restrictive `action_id`.

P3-002.2b-B remains responsible for the exact-scope, replay-safe Human Control
approval contract. P3-002.2b-C remains responsible for
`ToolExecutionRuntime` wiring, trusted destination/action derivation,
approval consumption and Event Bus evidence.

### Deferred work

- ToolExecutionRuntime wiring with trusted Tool Registry/Workspace Policy adapters,
  exact-scope Human Control approval validation/consumption and Event Bus evidence;
- After Tool Execution enforcement and evidence;
- exact-scope Human Control approval coordination and Event Bus evidence;
- independent Standards/Spec reviewer and simplification execution gates;
- persisted checkpoint lifecycle and compact restore;
- signed, pinned and license-aware skill/plugin registry.

Verification entry point: `verify_p3_002_1a.bat`.
