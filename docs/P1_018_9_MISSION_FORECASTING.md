# P1-018.9 — Mission Outcome Forecasting and What-if Simulation

## Purpose

This stage adds transparent, advisory forecasting for long-running Missions.
The built-in forecast does not claim statistical certainty. It stores all
assumptions, input metrics and score drivers so an operator can inspect why a
result was produced.

Forecasts and simulations never start a Mission Cycle, change Mission status,
allocate resources or select a scenario by themselves.

## Components

- `MissionForecastService`;
- per-Mission forecast policy;
- versioned forecast history;
- baseline, optimistic, pessimistic and custom scenarios;
- explicit scenario selection;
- Workspace portfolio simulations constrained by budget and capacity;
- comparison of saved simulations;
- forecast and selected-scenario context for Planner;
- event-driven refresh when explicitly enabled.

## Safety defaults

```json
{
  "enabled": false,
  "forecast_mode": "manual",
  "auto_refresh_enabled": false,
  "require_human_approval": true,
  "allow_auto_scenario_selection": false
}
```

## Built-in forecast drivers

The deterministic `builtin.heuristic.v1` method uses:

- Mission priority and current progress;
- completed, failed and cancelled Mission Cycles;
- observed progress velocity;
- open Risk exposure;
- hard dependency blockers;
- selected Strategy score;
- observed resource cost;
- available cycle count and deadline pressure.

Every forecast contains `drivers`, `metrics` and `assumptions`.

## Scenario overrides

Supported keys include:

- `success_probability_delta`;
- `completion_probability_delta`;
- `progress_delta_percent`;
- `risk_delta_percent`;
- `cost_multiplier`;
- `duration_multiplier`;
- `additional_budget_usd`;
- `agent_slots`, `tool_slots`, `compute_units` for simulations.

## Database

- `mission_forecast_policies`;
- `mission_forecasts`;
- `mission_scenarios`;
- `workspace_portfolio_simulations`.

Alembic head: `20260716_0029`.
