# Governed Agent Skills and Local Utilities

This design records two post-P3-001 product stages. External catalogs and agent
configuration repositories are architectural references only: they are not
runtime dependencies, embedded services, or permission authorities.

## P3-002 — Governed Developer Agent Profiles

Current delivery status:

- repository policy and progressive-disclosure documents shipped with
  v0.17.0;
- P3-002.1a immutable policy-profile evaluation and content-free checkpoint
  contracts are implemented without persistence or execution hooks;
- remaining registry, hook, reviewer, persisted-checkpoint and signed-skill
  work stays fail-closed and separately reviewable.

Proposed delivery slices:

1. Shared repository policy and progressive-disclosure agent documentation.
2. Agent Policy Profiles such as `safe-development`, `research-readonly`,
   `document-analysis`, `repository-review`, `release-manager`, and
   `production-operator`.
3. Before/After Tool Execution policy hooks integrated with Workspace Policy,
   Human Control, Event Bus, redaction, usage/cost, and decision evidence.
4. Independent reviewer/advisor and diff-simplification gates.
5. Persisted context checkpoints and compact-preservation contract.
6. Signed, pinned, license-aware skill/plugin registry with manual update review.

An agent profile manifest must define allowed tools and exact required
capabilities for each tool, denied actions, mandatory checks, approval
conditions, external domains, content classifications, primary model, reviewer
model, and audit version. Hooks cannot grant permission; they can only enforce
or further restrict a decision from the canonical policy services.

The implemented P3-002.1a contract and its deferred boundaries are specified in
`docs/P3_002_GOVERNED_DEVELOPER_AGENT_PROFILES.md`.

Example skill manifest:

```json
{
  "skill_id": "engineering.code-review",
  "version": "1.0.0",
  "source": "vendored",
  "upstream_repository": "owner/repository",
  "upstream_commit": "immutable-reviewed-sha",
  "invocation": "user_or_model",
  "tools": ["git.read", "filesystem.read", "tests.run"],
  "writes_files": false,
  "creates_commits": false,
  "network_access": "restricted",
  "requires_human_approval": false
}
```

## P3-003 — Governed Local Utilities Workspace

Proposed delivery slices:

1. Local Tool Registry and capability manifest.
2. Audited PDF/document transforms.
3. Token, context, and RAG inspectors.
4. Prompt-injection and content-safety warning tools.
5. LLM response comparator and evaluation scorecard.
6. Automated no-network privacy verification.
7. Local Utilities UI.

The first utilities should reuse Documents Safe Intake and Managed Storage. A
tool may be `LOCAL_ONLY`, `GATEWAY_ONLY`, or `SENSITIVE_APPROVAL`; the execution
label must be enforced rather than displayed as an unverified privacy claim.

Example local-tool manifest:

```json
{
  "tool_id": "document.pdf.merge",
  "version": "1.0.0",
  "execution": "local",
  "network_access": "denied",
  "filesystem_access": "bounded",
  "input_classifications": ["public", "internal", "confidential"],
  "persists_input": false,
  "telemetry": "metadata_only",
  "requires_approval": false,
  "audited_version": "1.0.0"
}
```

No external tool site may be scraped, framed, reverse engineered, or receive
user files automatically. Functional ideas must be implemented independently
with reviewed open-source dependencies and their licenses.

## Immediate P3-001 adoption

P3-001.5 adopts only prerequisites that strengthen Documents without creating
a parallel skill or utility system: explicit source manifests, transparent
token estimates, local injection warnings, exact citations, Gateway-only model
execution, Runtime Policy, and Human Control. The registries and generic hook
engine remain separate P3-002/P3-003 work.
