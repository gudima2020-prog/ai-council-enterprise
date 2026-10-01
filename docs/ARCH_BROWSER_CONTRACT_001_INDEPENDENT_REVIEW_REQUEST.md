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
8. Requested credential scopes are metadata-only secret references and do not
   create credential access.
9. BrowserRuntimeRequirements cannot disable ephemeral runtime.
10. BrowserRuntimeRequirements cannot disable fresh browser session.
11. Contradictory download/filesystem/network declarations fail closed,
    including external-write with none/fixture_only and upload with none.
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

## Focused B1 remediation acceptance checklist

The reviewer must treat B1 as a complete serializable-surface audit, not as a
check of only the previously reported examples.

Acceptance requires all of the following:

1. Inventory every serialized string field reachable from:
   `BrowserProvenanceRef`, `BrowserScriptArtifact`,
   `BrowserRuntimeRequirements`, `BrowserExecutionSpec`,
   `BrowserRuntimeAttestation`, `BrowserEvidenceItem`, and
   `BrowserEvidenceManifest`.
2. Classify each field as exactly one of:
   fixed vocabulary, typed digest/fingerprint, public identifier, version
   identifier, canonical relative path/logical identifier, requested origin, or
   protected `secret://provider/key` reference.
3. Confirm there is no serialized arbitrary free-text field and no field that
   bypasses its class validator before `to_dict()`.
4. Confirm public identifiers reject delimiter-shaped carrier strings and
   common header/key/password-like prefixes rather than relying on one
   field-specific check.
5. Re-run the R3 generic-identifier probes against at least:
   artifact ID, Task ID, Workspace ID, requested capability, requested evidence,
   runtime provider, failure class, evidence ID/type, and logical key.
6. Confirm a value-shaped sensitive token cannot be embedded in either the
   provider or key portion of a `secret://provider/key` scope, while ordinary
   metadata provider/key names remain valid.
7. Confirm a value-shaped sensitive token cannot be embedded in an origin host
   label and survive canonicalization.
8. Confirm path/entrypoint segments continue to use segment-level carrier
   rejection.
9. Confirm browser/runtime version fields remain narrow identifiers, not generic
   text.
10. Confirm typed SHA-256 fields remain valid and are not accidentally rejected
    merely because they are high-entropy hexadecimal strings.
11. Confirm rejected objects cannot be serialized or fingerprinted because
    construction fails first. Confirm arbitrary raw task input is not retained
    in these dataclasses: only its SHA-256 input fingerprint is stored.
12. Confirm the remediation adds no resolver, grant, authorization, network
    execution, browser launch, dependency, migration, or Skill runtime.
13. Confirm all B2 effect/network contradiction tests remain fail-closed.
14. Run the mandatory suites independently and report exact counts.

A PASS requires both: (a) the focused adversarial probes fail closed and
(b) source inspection shows no unclassified serializable string surface.

## Security questions

Answer explicitly:

- Can a script fingerprint authorize execution?
- Can a requested origin authorize network access?
- Can an effect declaration grant a browser capability?
- Can browser_external_write coexist with none or fixture_only network mode?
- Can browser_upload coexist with network mode none?
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
