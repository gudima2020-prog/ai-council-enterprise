# ARCH-GOV-CONSUME-001 — Canonical Governance Consumption Boundary

## Purpose

`ARCH-GOV-MAT-001` made governance requirements durable on real Tasks and Workflow node Tasks. This slice adds the first trusted runtime consumption boundary for that materialized state.

The boundary verifies integrity and transports governance requirements into the Task execution context. It does **not** decide whether execution is authorized.

## Core invariants

- `CANONICAL_REQUIREMENTS != AUTHORIZATION`
- `MATERIALIZED_PLAN != AUTHORIZATION`
- `REQUESTED_CAPABILITY != GRANTED_CAPABILITY`
- `REQUIRED_EVIDENCE != EVIDENCE_OBSERVED`
- `HUMAN_GATE_REQUIRED != HUMAN_APPROVAL_OBSERVED`
- `RAW_GOVERNANCE_ENVELOPE != HANDLER_INPUT`
- `INVALID_GOVERNANCE => HANDLER_NOT_INVOKED`
- `ABSENT_GOVERNANCE => EXISTING_PLAIN_TASK_BEHAVIOR` for this slice

## Scope

The implementation adds `TaskGovernanceConsumer`.

For a Task with no `_governance` key, consumption returns `None` and Task execution proceeds exactly through the existing plain-Task path.

For a Task with materialized governance, consumption calls the `ARCH-GOV-MAT-001` canonical recomputation path against the actual `TaskModel`. Only after successful recomputation is an immutable `CanonicalGovernanceRequirements` object created.

The requirements object carries:

- actual Task ID;
- materialized revision ID;
- risk class and execution initiator;
- side-effect classes;
- requested capabilities;
- policy tags;
- required controls, roles and evidence;
- `human_gate_required`;
- reason codes;
- policy identity/fingerprint;
- plan fingerprint;
- materialization schema identity;
- `authorization_state=requirements_only`;
- `verification_state=canonical_recomputed`.

It intentionally carries no:

- capability grant;
- observed-evidence state;
- evidence satisfaction state;
- Human Approval result;
- authorization result;
- allow/deny runtime decision.

## TaskExecutor boundary

`TaskExecutor._claim_task()` consumes governance before the handler is invoked.

For valid governed Tasks:

1. the materialized envelope is canonically recomputed;
2. canonical immutable requirements are attached to `TaskExecutionContext.governance`;
3. raw `_governance` is removed from the handler-facing business payload;
4. the persisted Task still retains its canonical `_governance` envelope;
5. the TaskRun records the consumed revision and plan fingerprint for traceability.

For invalid/tampered governed Tasks:

1. handler invocation does not occur;
2. the Task transitions to `failed`;
3. a failed TaskRun and error log are persisted;
4. the rejection is tagged as governance preflight;
5. automatic retry is not scheduled.

The rejection is returned from the claim transaction rather than raised through the session scope, so the failure record is committed rather than rolled back.

`ParallelTaskExecutor` inherits the same claim boundary through `TaskExecutor`.

### Admission/budget ordering

When `ParallelTaskExecutor` uses `TaskAdmissionManager`, governance integrity is
preflighted before admission can reserve budget or consume admission quota. The
preflight is intentionally advisory: the authoritative Task claim recomputes
canonical governance again immediately before handler context creation, closing
the time-of-check/time-of-use gap.

A second defense remains event-driven. If a reservation nevertheless exists
(for example because state changes between preflight and claim),
`task.executor.governance_rejected` is treated as a terminal release event by
`TaskAdmissionManager`, and production subscribes cost settlement to that
event. Governance rejection therefore must not leave `reserved_usd` or an
active budget commitment behind.

## Important non-enforcement semantics

A canonically valid requirements object may say `human_gate_required=True`. That fact remains a requirement only. This slice does not inspect or consume a Human Approval record and does not convert that requirement into authorization.

Likewise, the presence of `requested_capabilities` or `required_evidence` has no grant/satisfaction meaning here.

A future enforcement slice must bind these requirements to actual observed evidence, approval state, granted capabilities, actor/agent identity, concrete tool invocation and execution context.

## Explicit non-goals

This slice does not add:

- ActionGateway authorization;
- ToolExecutionRuntime policy decisions;
- capability grants;
- evidence-store integration;
- Human Approval creation or consumption;
- AgentRuntime;
- SkillRegistry;
- ModelRegistry / AdapterRegistry;
- P3-002 trusted-network wiring;
- DB migrations;
- mandatory governance enrollment for every Task;
- replacement of the exact persisted policy snapshot with current policy.

## Required USER_RUN

Focused:

```powershell
python -m pytest -q tests/test_task_governance_consumption.py
```

Governance materialization/core:

```powershell
python -m pytest -q `
  tests/test_task_governance_materialization.py `
  tests/test_task_governance_planner.py
```

Task execution/workflow regression:

```powershell
python -m pytest -q `
  tests/test_task_executor.py `
  tests/test_task_workflow_execution.py `
  tests/test_workflow_approval_gate.py `
  tests/test_workflow_template_execution.py `
  tests/test_workflow_templates.py
```

Agent-policy regression:

```powershell
python -m pytest -q `
  tests/test_agent_human_approval_contract.py `
  tests/test_agent_policy_enforcement_core.py
```

USER_RUN is user-operated evidence only and must not be counted as independent reproduction.
