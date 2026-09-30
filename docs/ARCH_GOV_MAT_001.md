# ARCH-GOV-MAT-001 — Task/Workflow Governance Materialization

## Goal

Materialize the promoted `ARCH-GOV-CORE-001` governance requirements into durable real Task/Workflow state without turning a materialized plan into authorization.

## Baseline

- Base branch: `feature/p3-002`
- Base commit: `13f8d455bf31855b3988db8bf48a5a4781dfdccf`
- Parent contract: `ARCH-GOV-CORE-001`

## Design

A governed Task may carry a reserved `_governance` envelope inside its existing `payload_json`. The envelope contains:

- materialization schema version;
- `authorization_state = requirements_only`;
- exact normalized policy snapshot;
- canonical serialized governance plan;
- deterministic plan fingerprint.

Trusted consumers must recompute the plan from the stored request + exact policy snapshot before using the requirements. The serialized plan is not an authorization artifact.

Workflow instances can materialize governance onto selected existing node Tasks after instantiation. This slice does not add public router/API input for risk classification and does not let a workflow node grant itself authority.

## Security invariants

1. `MATERIALIZED_PLAN != AUTHORIZATION`.
2. `REQUIRED_EVIDENCE != EVIDENCE_OBSERVED`.
3. `REQUESTED_CAPABILITY != GRANTED_CAPABILITY`.
4. Materialization is bound to the actual `TaskModel.id`.
5. Stored plan must equal canonical recomputation from stored request + policy snapshot.
6. Existing materialization is immutable in place; a changed request/policy must fail closed.
7. Generic Task payload create/update cannot inject or mutate `_governance`.
8. Generic payload replacement preserves an existing `_governance` envelope.
9. Workflow payload/result mapping preserves `_governance`.
10. No control/evidence requirement in the envelope is automatically considered satisfied.

## Allowed scope

- `backend/task_engine/governance_materialization.py`.
- TaskRepository materialize/validate/recompute adapter methods.
- `_governance` reserved-key protection in TaskRepository.
- Workflow instance materialization onto selected node Tasks.
- Preservation of governance metadata during workflow payload remapping.
- Focused tests and documentation.

## Forbidden scope

- No DB migration.
- No runtime authorization.
- No ActionGateway or ToolExecutionRuntime wiring.
- No capability grants.
- No automatic evidence generation or satisfaction.
- No automatic human approval creation or consumption.
- No AgentRuntime / SkillRegistry / ModelRegistry / AdapterRegistry.
- No Docker/Kubernetes/Agent Substrate.
- No P3-002 trusted-network changes.

## Acceptance criteria

- Real Task materialization creates a canonical requirements-only envelope.
- Materialized high-risk Task recomputes to the expected governance controls.
- Requested capabilities remain requests only.
- Same exact materialization is idempotent.
- Changed materialization on the same Task fails closed.
- Tampered task ID, request, policy, serialized plan, or fingerprint fails validation.
- Generic payload update preserves an existing envelope and rejects attempted mutation/injection.
- Workflow can materialize governance onto selected real node Tasks.
- Workflow result mapping preserves the exact governance envelope.
- Plain Tasks/Workflows are unchanged when materialization is unused.

## Required author/user tests

Focused:

`python -m pytest -q tests/test_task_governance_materialization.py`

Core governance regression:

`python -m pytest -q tests/test_task_governance_planner.py`

Task/Workflow regression:

`python -m pytest -q tests/test_task_state_machine.py tests/test_task_audit_evidence.py tests/test_task_workflow_execution.py tests/test_workflow_approval_gate.py tests/test_workflow_template_execution.py tests/test_workflow_templates.py`

Agent-policy regression:

`python -m pytest -q tests/test_agent_human_approval_contract.py tests/test_agent_policy_enforcement_core.py`

## Promotion gate

Do not merge until focused + regression USER_RUN passes, frozen artifact is independently reviewed, blockers are resolved, and a human explicitly approves the exact revision.
