# P1-018.7 — Mission Dependencies and Portfolio Coordination

## Purpose

P1-018.7 adds coordination across multiple long-running Mission objects in the
same Workspace. A Mission may depend on another Mission, and a Workspace may
rank active Mission objects before allocating a limited number of execution
slots.

## Safety defaults

The Workspace portfolio policy is disabled by default:

- mode: `manual`;
- human approval required;
- automatic rebalance disabled;
- cycle admission enforcement disabled.

Hard Mission dependencies are still enforced when they exist. A manual
`force=true` cycle run is recorded in the admission context and may bypass a
blocked dependency when an operator intentionally accepts the risk.

## Dependency types

- `hard`: blocks the dependent Mission Cycle until satisfied or waived;
- `soft`: adds a warning and reduces the portfolio score;
- `informational`: appears in Planner context without blocking execution.

Dependencies form a DAG inside one Workspace. Self-dependencies,
cross-Workspace links and graph cycles are rejected.

## Portfolio score

The weighted score uses:

- Mission priority;
- current Mission progress;
- deadline urgency;
- dependency readiness;
- selected strategy quality;
- open risk exposure as a penalty.

The result is clamped to `0..100`. A Mission with an unsatisfied hard
dependency receives a `block` decision regardless of its numeric score.

## Persistence

New tables:

- `mission_dependencies`;
- `mission_portfolio_policies`;
- `mission_portfolio_evaluations`;
- `mission_portfolio_assignments`.

## Runtime integration

`AutonomousMissionService` now receives:

- `mission_portfolio` Planner context;
- `mission_portfolio_admission` preflight result;
- portfolio maintenance during the Mission scheduler tick.

All `mission.dependency.*` and `mission.portfolio.*` events are captured by the
existing immutable audit trail and durable event transport.
