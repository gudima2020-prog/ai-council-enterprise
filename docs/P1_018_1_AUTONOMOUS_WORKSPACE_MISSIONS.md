# P1-018.1 — Autonomous Workspaces and Long-Running Missions

## Purpose

This stage introduces a durable control plane for Workspace-level objectives
that outlive a single Task or Execution Plan.

A Mission owns:

- a long-term objective and success criteria;
- a dependency-aware Goal graph;
- weighted progress;
- repeated planning cycles;
- links to generated Execution Plans;
- an evidence-bearing progress history;
- explicit lifecycle and safety controls.

## Safety model

Autonomy is disabled when no persistent Workspace policy exists.

Policy modes:

- `observe`: record and inspect Missions without scheduled cycles;
- `supervised`: create due cycles, but require an explicit run request;
- `autonomous`: create and plan due cycles automatically.

Automatic Execution Plan start additionally requires all of the following:

- policy is enabled;
- mode is `autonomous`;
- `allow_auto_start=true`;
- `require_user_approval=false`;
- Mission-level `auto_start_plans` does not disable it.

## Mission lifecycle

```text
draft -> active -> paused -> active
                 |          |
                 |          +-> completed
                 |          +-> failed
                 |          +-> cancelled
                 +------------> cancelled
```

Only a valid Mission with at least one acyclic Goal can be activated.

## Planning cycle lifecycle

```text
queued -> planning -> ready -> running -> completed
                   |          |       -> failed
                   |          +----------> cancelled
                   +---------------------> failed
```

A supervised cycle stops at `queued` until an explicit run request. A planned
cycle stops at `ready` unless the safety policy allows automatic start.

## Environment

```text
AI_STUDIO_MISSION_TICK_SECONDS=15
```

The minimum accepted scheduler interval is five seconds. Workspace policy
controls the interval between Mission cycles.

## Persistence

Tables:

- `autonomous_workspace_policies`;
- `workspace_missions`;
- `mission_goals`;
- `mission_cycles`;
- `mission_progress_updates`.

Mission and autonomy events are included in the existing immutable audit trail
and durable distributed event transport bridge.
