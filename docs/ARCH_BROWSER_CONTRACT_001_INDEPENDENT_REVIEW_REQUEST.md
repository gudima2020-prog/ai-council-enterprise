# ARCH-BROWSER-CONTRACT-001 — Independent Review Request

## Candidate identity

The reviewer must verify the exact frozen Git revision, tree, merge base,
review-bundle SHA-256, binding-evidence SHA-256, and exact Git↔bundle binding
provided with the review package.

If an artifact hash or exact binding does not match, return BLOCKED and stop.

## Review objective

Independently verify the pure contract slice for Issue #9.

The key security boundary is:

~~~text
CANONICAL_BROWSER_REQUIREMENTS != AUTHORIZATION
~~~

Related mandatory invariants:

~~~text
SCRIPT_ARTIFACT != AUTHORIZATION
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

## Scope to inspect

Review at least:

~~~text
backend/browser_runtime/contracts.py
backend/browser_runtime/__init__.py
tests/test_browser_execution_contracts.py
docs/ARCH_BROWSER_CONTRACT_001.md
docs/ARCH_BROWSER_CONTRACT_001_INDEPENDENT_REVIEW_REQUEST.md
~~~

Also inspect the exact base-to-candidate diff and confirm that no runtime,
authorization, database, Playwright, browser, network, Skill Registry, or Skill
Factory wiring was added elsewhere.

## Mandatory independent test commands

Run these yourself. Recorded USER_RUN evidence, if supplied, is not independent
reproduction.

1. Focused browser contract suite:

~~~text
python -m pytest -q tests/test_browser_execution_contracts.py
~~~

2. Governance contract/consumption regressions:

~~~text
python -m pytest -q tests/test_task_governance_planner.py tests/test_task_governance_materialization.py tests/test_task_governance_consumption.py
~~~

3. Agent policy / Human Approval / runtime-policy regressions:

~~~text
python -m pytest -q tests/test_agent_policy_enforcement_core.py tests/test_agent_human_approval_contract.py tests/test_execution_runtime_preflight.py tests/test_isolated_runtime_policy.py
~~~

Report the exact command and factual result for every run.

## Mandatory adversarial checks

Independently verify all of the following, not merely by trusting existing tests.

1. Unknown schema versions fail closed.
2. BrowserScriptArtifact is immutable and source hash changes alter identity.
3. BrowserExecutionSpec explicitly binds Task + revision + script fingerprint +
   input fingerprint.
4. Changing each of those four bindings changes the execution fingerprint.
5. Execution requirements contain no allow/grant/approval result.
6. Effect declarations do not create grants.
7. Requested origins do not become network authority.
8. Requested credential scopes contain identifiers only and do not create
   credential access.
9. BrowserRuntimeRequirements cannot disable ephemeral runtime.
10. BrowserRuntimeRequirements cannot disable fresh browser session.
11. Contradictory download/filesystem/network declarations fail closed.
12. Navigation origins reject embedded username/password, paths, queries,
    fragments, and unsupported schemes.
13. Evidence relative paths reject traversal.
14. Evidence relative paths reject POSIX absolute paths.
15. Evidence relative paths reject Windows drive forms.
16. Evidence relative paths reject alternate-data-stream style colon paths.
17. Duplicate evidence IDs fail closed.
18. Duplicate evidence locations fail closed.
19. RuntimeAttestation is immutable observed evidence only and exposes no
    authorization result.
20. EvidenceManifest is immutable observed evidence only and exposes no
    authorization result.
21. Attestation terminal/failure/timestamp contradictions fail closed.
22. Observed attestation/evidence objects cannot substitute for
    BrowserRuntimeRequirements.
23. Canonical input JSON is deterministic.
24. Fingerprints are domain-separated.
25. Nested state is actually immutable or value-only; frozen outer dataclasses
    must not hide mutable dict/list authority state.
26. No field can carry raw credentials, cookies, Authorization headers,
    approval tokens, passwords, API keys, or private keys as intended contract
    state.
27. No new Playwright/Chromium/Webwright dependency is introduced.
28. No browser launch or external network execution is introduced.
29. No ToolExecutionRuntime or AgentToolRuntimeGovernance behavior is changed.
30. Existing CODE_EXECUTION fail-closed behavior is not weakened.
31. No DB migration is added.
32. No Skill Registry, Skill Factory, auto-promotion, or RUN/ADAPT/SKIP runtime
    behavior is implemented in this slice.

## Security questions

Answer explicitly:

- Can a script fingerprint authorize execution?
- Can a requested origin authorize network access?
- Can an effect declaration grant a browser capability?
- Can a credential scope string disclose or grant a credential?
- Can RuntimeAttestation be interpreted as an ALLOW result?
- Can EvidenceManifest prove task correctness by itself?
- Can replay consistency be treated as security verification?
- Can any caller use this slice to launch Chromium or Playwright?
- Does this slice weaken existing code-execution fail-closed behavior?
- Are evidence paths cross-platform traversal/ADS safe under the stated
  contract?
- Are future Human Approval bindings able to bind exact
  Task/revision/script/input fingerprints without changing this contract?

## Scope boundary

Do not demand implementation of future browser execution in this issue.

The following are intentionally future work and are not blockers unless this
candidate incorrectly claims to implement them or weakens current security:

- actual browser runtime;
- local fixture runtime;
- external browser egress;
- DNS/IP/redirect enforcement;
- credential broker;
- Human Approval consumption;
- ToolExecutionRuntime browser authorization;
- runtime attestation collection;
- evidence artifact collection;
- replay engine;
- Skill Registry;
- Skill Factory;
- Skill Router.

## Required output

Return exactly these sections:

~~~text
VERDICT
BLOCKERS
NOTES
SECURITY INVARIANTS CONFIRMED
RISKS TO CARRY FORWARD
~~~

Do not modify source, fix findings, merge, or promote.
