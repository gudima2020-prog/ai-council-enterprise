# P1-018.10 — Mission Forecast Calibration and Learning Loop

## Purpose

P1-018.10 closes the Autonomous Workspace Engine by connecting forecast
predictions with verified Mission outcomes. The learning subsystem measures
forecast error, proposes bounded calibration coefficients, and applies them
only through an explicit approval or a deliberately enabled adaptive policy.

## Safety model

Default policy:

- learning disabled;
- manual mode;
- outcome capture enabled because it is observational;
- automatic calibration disabled;
- automatic application disabled;
- human approval required.

A calibration never changes Mission state, starts work, changes a Strategy, or
allocates resources. It only adjusts future advisory forecasts.

## Stored evidence

`mission_forecast_outcomes` stores the prediction and observed result together,
including:

- success and completion Brier scores;
- absolute probability errors;
- remaining-cost error;
- remaining-cycle error;
- P50 completion-time error;
- source event and resolution metadata.

## Calibration

Calibration may be scoped to one Mission or an entire Workspace. The built-in
method calculates bounded corrections for:

- success probability;
- completion probability;
- expected cost;
- remaining cycles;
- duration;
- forecast confidence.

The service records baseline score, calibrated score, improvement, Brier
scores, expected calibration error, sample count, and all source outcome IDs.

## Learning loop

A Learning Run creates a proposed calibration. The proposal is applied only
when:

1. the sample threshold is met, unless a human explicitly forces the run;
2. the improvement threshold is met, unless explicitly forced;
3. an automatic run is permitted by an enabled adaptive policy;
4. human approval is disabled for automatic application.

## Integrity controls

`GET /api/mission-learning/verify` checks:

- multiple active calibrations for one scope;
- applied Learning Runs with missing or invalid Calibration records;
- orphan Forecast Outcome records.

`POST /api/mission-learning/reconcile` backfills outcomes for terminal
Missions and may optionally create reviewed Learning Runs.

## Planner integration

Future Mission forecasts automatically use the most specific current
calibration:

1. Mission calibration;
2. Workspace calibration;
3. uncalibrated heuristic forecast.

The Planner receives both `context.mission_forecast` and
`context.mission_learning`.
