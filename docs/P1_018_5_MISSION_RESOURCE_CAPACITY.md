# P1-018.5 — Mission Resource Allocation and Adaptive Capacity Planning

This stage adds durable budget and capacity governance for long-running
Missions and their planning cycles.

## Safety model

Resource governance is opt-in. The default synthetic policy is:

- enabled: `false`;
- allocation mode: `manual`;
- human approval required: `true`;
- automatic allocation: `false`;
- automatic rebalance: `false`.

A Mission without an enabled resource policy behaves exactly as before. When a
policy is enabled, each Mission Cycle must pass resource admission before the
Planner is called.

## Budget admission

Admission evaluates:

- total allocatable budget after reserve;
- already recorded usage;
- outstanding commitments from active allocations;
- per-cycle budget ceiling;
- maximum concurrent cycles;
- agent slots;
- tool slots;
- compute units.

A rejected allocation does not start the Planner. The admission snapshot is
stored with the allocation and included in emitted audit events.

## Allocation lifecycle

`pending_approval -> approved -> reserved -> active`

Terminal states:

- `consumed` for a successfully completed cycle;
- `released` for a failed or cancelled cycle;
- `exceeded` when actual cost passes the allocation plus tolerance;
- `cancelled` for an explicit operator cancellation.

Every Mission Cycle has at most one durable allocation.

## Usage ledger

Resource usage is append-only at the service level and supports idempotent
recording by Mission and `idempotency_key`. Supported categories are:

- LLM;
- tool;
- compute;
- storage;
- network;
- human;
- other.

Each entry stores quantity, unit, cost, source reference, actor and details.
The linked allocation's actual cost is recalculated incrementally.

## Planner integration

Before planning, the Autonomous Mission Service requests resource admission.
The Planner receives:

- `context.mission_resources`;
- `context.mission_resource_admission`.

The context includes policy limits, current utilization, selected strategy cost
signal, current-cycle allocation and recent allocations.

## Adaptive capacity planning

Capacity recommendations use recent terminal allocations and observed actual
cost. The service calculates:

- recommended cycle budget;
- recommended parallel cycle count;
- recommended agent slots;
- recommended tool slots;
- recommended compute units;
- confidence based on sample coverage.

Automatic application requires all of the following:

- enabled Resource Policy;
- allocation mode `adaptive`;
- `auto_rebalance_enabled = true`;
- improvement reaching the configured threshold.

Manual application remains available through the API and is fully audited.

## Tables

- `mission_resource_policies`
- `mission_resource_allocations`
- `mission_resource_usage`
- `mission_capacity_plans`

## Alembic

Head revision: `20260716_0025`.
