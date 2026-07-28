# P1-018.4 — Mission Strategy Portfolio and Adaptive Prioritization

This stage adds a persistent portfolio of alternative strategies for every
long-running Mission.

## Safety model

The default policy is conservative:

- selection mode: `manual`;
- human selection required: `true`;
- automatic selection: `false`.

Automatic replacement is allowed only when all conditions are explicitly met:

- policy is enabled;
- mode is `weighted` or `adaptive`;
- `require_human_selection` is disabled;
- `auto_selection_enabled` is enabled;
- candidate score reaches the minimum threshold;
- candidate improvement reaches the configured threshold;
- strategy-selection cooldown has elapsed.

## Portfolio scoring

The base score combines:

- expected value;
- success probability;
- strategic fit;
- feasibility;
- evidence confidence;
- inverse risk;
- inverse cost;
- inverse duration;
- operator priority.

Adaptive mode additionally applies:

- observed average reward from completed Mission Cycles;
- an exploration bonus for strategies with fewer trials.

Scores are clamped to the range `0..100` and every persisted evaluation keeps
its calculation snapshot.

## Planner integration

The selected strategy is assigned to the Mission Cycle before planning. The
Planner receives:

- `context.mission_strategy`;
- `context.mission_strategy_assignment`;
- selected `strategy_hint` appended to the normal Mission strategy hint.

A Mission without a selected strategy remains executable; the feature does not
silently block existing workflows.

## Feedback loop

Terminal Mission Cycle events update the linked strategy assignment:

- completed: reward `100`;
- failed: reward `0`;
- cancelled: reward `25`.

Manual feedback can override the reward and outcome summary. Aggregate trial,
success, failure and average reward metrics are recalculated from the durable
assignment history, making repeated feedback idempotent.

## Tables

- `mission_strategy_policies`
- `mission_strategies`
- `mission_strategy_evaluations`
- `mission_strategy_selections`
- `mission_strategy_assignments`

## Alembic

Head revision: `20260716_0024`.
