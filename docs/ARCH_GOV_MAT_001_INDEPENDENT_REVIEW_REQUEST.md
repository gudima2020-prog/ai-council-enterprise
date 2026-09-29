# ARCH-GOV-MAT-001 — Independent Review Request

## Review target

Branch: `feature/arch-gov-mat-001`
Base: `13f8d455bf31855b3988db8bf48a5a4781dfdccf`

Primary files:

- `backend/task_engine/governance_materialization.py`
- `backend/task_engine/repository.py`
- `backend/task_engine/workflow_templates.py`
- `tests/test_task_governance_materialization.py`
- `docs/ARCH_GOV_MAT_001.md`

## Reviewer role

Independent Reviewer. Recorded author/user tests are context only and must not be counted as independent reproduction.

## Required review questions

1. Is the stored envelope explicitly requirements-only rather than an authorization object?
2. Does trusted readback recompute from exact request + policy snapshot instead of trusting the serialized plan?
3. Does recomputation verify actual Task ID binding?
4. Can a generic Task create/update inject, remove, or alter `_governance`?
5. Is exact re-materialization idempotent while changed request/policy fails closed?
6. Can tampering with request, policy snapshot, serialized plan, fingerprint, or Task binding pass validation?
7. Does Workflow materialization operate only on real node Tasks in the selected instance?
8. Does Workflow result mapping preserve `_governance`?
9. Does this slice accidentally treat `required_evidence` as evidence already observed?
10. Does this slice create or consume human approval automatically? It must not.
11. Does this slice grant capabilities or bypass existing `AgentCapability` / policy enforcement? It must not.
12. Are there unsafe circular dependencies or behavior changes for plain Tasks/Workflows?
13. Does the absence of DB migration remain safe for this requirements-only stage?
14. What must be carried forward before runtime enforcement consumes governance materialization?

## Mandatory independent test commands

`python -m pytest -q tests/test_task_governance_materialization.py`

`python -m pytest -q tests/test_task_governance_planner.py`

`python -m pytest -q tests/test_task_state_machine.py tests/test_task_audit_evidence.py tests/test_task_workflow_execution.py tests/test_workflow_approval_gate.py tests/test_workflow_template_execution.py tests/test_workflow_templates.py`

`python -m pytest -q tests/test_agent_human_approval_contract.py tests/test_agent_policy_enforcement_core.py`

If any test cannot run, say so explicitly and do not count it as confirmed.

## Required output

### VERDICT
`PASS` / `PASS_WITH_NOTES` / `BLOCKED`

### BLOCKERS
Only issues that must be fixed before human promotion.

### NOTES
Non-blocking observations.

### SECURITY INVARIANTS CONFIRMED
List each confirmed invariant separately.

### RISKS TO CARRY FORWARD
Especially runtime enforcement, persistence/schema migration, approval evidence, ActionGateway, AgentRuntime and capability binding.

## Promotion rule

The reviewer may recommend readiness for human approval. The reviewer must not merge or promote.
