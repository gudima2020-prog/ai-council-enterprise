# ARCH-GOV-CORE-001 — Minimum Sufficient Governance

## Goal

Introduce the smallest backend contract needed for **Complexity on Demand** without wiring a new runtime, gateway, database schema, or scheduler.

The change must let the platform derive the least expensive governance workflow that still satisfies task risk and side-effect policy.

## Allowed scope

- Add a pure task-governance contract under `backend/task_engine`.
- Add deterministic risk / initiator / side-effect / governance-role / evidence-provenance types.
- Add a configurable minimum-governance planner.
- Add unit tests.
- Keep the implementation side-effect free and database independent.

## Forbidden changes

- No changes to P3-002 trusted-network files.
- No runtime wiring.
- No ToolExecutionRuntime changes.
- No Docker/Kubernetes/Agent Substrate integration.
- No database migrations.
- No API/router changes.
- No skill registry implementation yet.
- No merge/promotion.

## Security invariants

1. **Role != Capability** — a governance role never grants a capability.
2. **Requested capability != granted capability** — this planner may only carry requested capability identifiers.
3. **Evidence provenance is non-interchangeable** — implementation self-test, user-run test, independent test and external/on-wire evidence remain distinct classes.
4. **Minimum sufficient governance** — policy chooses the least expensive compliant control set.
5. **Policy, not the model, escalates governance** — side-effect/risk rules are deterministic and configurable.
6. **Execution initiator is provenance** — human, agent, scheduler, workflow and system remain distinguishable.
7. Invalid governance inputs fail closed before a plan is produced.

## Acceptance criteria

- Low risk defaults to implementation self-check only.
- Medium risk adds deterministic validation.
- High risk adds evidence agent + independent review under the default policy.
- Critical risk adds security review + human promotion.
- External write can require human promotion without forcing the entire council.
- Credential use can require evidence + security review + human promotion.
- Production change can require evidence + security review + independent review + human promotion.
- A custom policy can change these thresholds/side-effect rules without changing planner code.
- Governance plan is deterministically fingerprinted.
- Reordering side effects/capability requests/policy tags does not change the fingerprint.
- Changing execution initiator changes the fingerprint.
- Planner output contains requested capabilities but no granted-capability field.

## Required evidence

Implementation-agent self-test:

`python -m pytest -q tests/test_task_governance_planner.py`

This self-test is **not** independent verification.

## Promotion gate

Do not merge until:
- implementation self-test passes;
- independent reviewer checks the contract against current task-engine / agent-governance semantics;
- human approves promotion.
