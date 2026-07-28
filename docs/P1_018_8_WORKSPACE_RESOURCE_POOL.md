# P1-018.8 — Workspace Shared Resource Pool

This stage introduces a second resource-governance layer above individual Mission budgets.

## Scope

- shared Workspace budget;
- shared Agent, Tool and Compute capacity;
- cross-Mission reservation admission;
- persistent conflicts and resolution history;
- safe preemption of lower-priority reserved work;
- weighted/adaptive capacity rebalance;
- Planner context integration;
- Audit Trail and distributed Event Transport integration.

## Safety defaults

The Workspace pool is disabled by default. Manual approval is enabled, automatic rebalance is disabled and cycle admission is not enforced until explicitly configured.

## Data model

- `workspace_resource_policies`
- `workspace_resource_reservations`
- `workspace_resource_conflicts`
- `workspace_resource_rebalances`

## Admission sequence

1. Mission governance and schedule checks.
2. Mission portfolio admission.
3. Mission-level budget and capacity reservation.
4. Workspace-level shared budget and capacity reservation.
5. Planner invocation.

The Mission-level policy limits one Mission. The Workspace policy limits the aggregate commitments of all Missions.

## Conflict resolution

Supported actions:

- `defer` — cancel the pending request;
- `preempt` — release lower-priority reserved capacity;
- `rebalance` — calculate and apply a portfolio-wide allocation plan;
- `increase_capacity` — explicitly increase Workspace limits;
- `force` — accept the reservation with a recorded override;
- `waive` — close the conflict without admitting the reservation.

Active work is not preempted unless `force=true`.

## Alembic

Current head: `20260716_0028`.
