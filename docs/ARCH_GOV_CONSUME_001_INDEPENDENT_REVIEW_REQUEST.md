# ARCH-GOV-CONSUME-001 — Independent Review Request

## Frozen target

Review the exact frozen candidate supplied by the requester. Independently verify the declared Git revision, tree and review-bundle SHA when artifacts make that possible.

Do not treat recorded USER_RUN as your own reproduction.

## Review objective

Determine whether the candidate creates a trustworthy canonical consumption boundary for already-materialized governance while preserving the rule that governance requirements are not authorization.

## Mandatory independent commands

Run:

```
python -m pytest -q tests/test_task_governance_consumption.py
```

```
python -m pytest -q tests/test_task_governance_materialization.py tests/test_task_governance_planner.py
```

```
python -m pytest -q tests/test_task_executor.py tests/test_task_workflow_execution.py tests/test_workflow_approval_gate.py tests/test_workflow_template_execution.py tests/test_workflow_templates.py
```

```
python -m pytest -q tests/test_agent_human_approval_contract.py tests/test_agent_policy_enforcement_core.py
```

Report each exact command and actual result. If execution is impossible, state that explicitly and do not count the test as independently confirmed.

## Required adversarial review

Independently test or inspect, rather than merely trusting the focused tests:

1. A plain Task has no governance context and keeps prior handler payload behavior.
2. A valid governed Task is canonically recomputed against the actual Task.
3. The handler receives immutable canonical requirements, not raw serialized `_governance`.
4. `_governance` is absent from handler business payload but remains persisted on the Task.
5. Requested capabilities remain requests and never become grants.
6. Required evidence remains requirements and never becomes observed/satisfied evidence.
7. `human_gate_required` remains a requirement and never becomes approval or an authorization decision.
8. Tampering request, policy snapshot, serialized plan, plan fingerprint, schema/authorization state, or Task binding fails closed before handler invocation.
9. Governance rejection survives the database transaction and persists a failed Task/run/log record.
10. Governance integrity rejection does not schedule automatic retry even when `max_retries > 0`.
11. The parallel executor cannot bypass the TaskExecutor consumption boundary.
12. No caller-controlled consumer/provider hook can replace canonical recomputation inside TaskExecutor.
13. No ActionGateway, ToolExecutionRuntime authorization, AgentRuntime, SkillRegistry, P3-002 or DB migration was introduced.
14. Existing plain Task/Workflow behavior remains compatible.
15. With `ParallelTaskExecutor` + `TaskAdmissionManager`, governance-invalid Tasks are rejected before normal admission reservation/quota consumption.
16. If a reservation exists before governance rejection, `task.executor.governance_rejected` settles it to zero without charge.
17. After a 2.5 USD governance-invalid Task under a 3.0 USD hard budget, a later valid 1.0 USD Task can still execute; the invalid Task must not remain in active budget commitment.

## Security questions

Explicitly answer:

- Can a stored plan reach a handler without canonical recomputation?
- Can raw `_governance` metadata be interpreted directly by a Task handler through the normal handler payload?
- Can invalid governance fail transiently but be rolled back while execution continues?
- Can a malformed governed Task reach the registered handler?
- Does the canonical requirements object contain any field whose semantics are a capability grant, evidence satisfaction, Human Approval result, or runtime authorization?
- Does `human_gate_required=True` itself block or approve execution in this slice? Explain why that behavior matches or violates the documented scope.
- Can a plain Task execute without materialized governance in this slice? Explain why that is or is not a compatibility requirement.
- Are run/log metadata descriptive evidence of consumption only, or could they accidentally be treated as authorization?
- Can governance rejection leave an admission reservation, active budget commitment, or starvation condition for later valid Tasks?

## Output

Return exactly these sections:

VERDICT

BLOCKERS

NOTES

SECURITY INVARIANTS CONFIRMED

RISKS TO CARRY FORWARD

Do not modify sources, fix defects, merge, or promote the candidate.
