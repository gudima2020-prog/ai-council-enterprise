# ARCH-GOV-CORE-001 — Independent Review Request

## Review target

Branch: feature/arch-gov-core-001
Base: 0582fbb8d1b7be12319f9fb32129214759196d6c

Primary files:
- backend/task_engine/governance.py
- tests/test_task_governance_planner.py
- docs/ARCH_GOV_CORE_001.md

## Review role

Independent Reviewer. Do not treat implementation-author self-tests or user-run tests as independent reproduction.

## Required review questions

1. Does this introduce a small contract layer rather than a competing task engine or agent-governance subsystem?
2. Is Role != Capability preserved?
3. Does the planner ever convert requested capabilities into grants? It must not.
4. Are execution initiators represented as provenance rather than authority?
5. Are implementation self-test, user-run test, independent test, external/on-wire evidence and security review kept semantically distinct?
6. Is the default risk mapping policy-configurable, rather than hard-coded into planner control flow?
7. Does a low-risk side effect escalate only the controls required by policy instead of automatically launching the full council?
8. Are canonicalization and SHA-256 fingerprints deterministic and sensitive to material provenance changes?
9. Do malformed governance inputs fail before a plan is produced?
10. Does the change avoid runtime, database, network, ToolExecutionRuntime, P3-002 and production behavior wiring?
11. Does any concept duplicate an existing canonical type in agent_governance, runtime_policy, orchestration or task_engine in a way that should be reused instead?
12. Are there missing invariants that would make later persistence/runtime wiring unsafe?

## Mandatory regression checks

At minimum run:

python -m pytest -q tests/test_task_governance_planner.py
python -m pytest -q tests/test_task_state_machine.py tests/test_task_audit_evidence.py tests/test_agent_human_approval_contract.py tests/test_agent_policy_enforcement_core.py

If a test is unavailable in the review environment, state that explicitly.

## Required output

### VERDICT
PASS / PASS_WITH_NOTES / BLOCKED

### BLOCKERS
Only issues that must be fixed before human promotion.

### NOTES
Non-blocking design observations.

### SECURITY INVARIANTS CONFIRMED
List each confirmed invariant separately.

### RISKS TO CARRY FORWARD
Especially persistence, workflow materialization, ActionGateway integration, AgentRuntime integration and SkillRegistry interaction.

## Promotion rule

This review may recommend readiness for human approval. It may not merge or promote the branch.
