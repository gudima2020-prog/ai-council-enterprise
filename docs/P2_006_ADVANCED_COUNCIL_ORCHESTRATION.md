# P2-006 — Advanced Council Orchestration

Version: **0.10.0**
Date: **2026-07-24**
Alembic revision: **`20260724_0048`**

## Goal

Turn AI Council from one fixed multi-model workflow into a bounded, auditable
orchestration engine whose additional model calls remain governed by the P2-005
budget, quota, reservation and actual-cost controls.

## Execution modes

- **Solo** — exactly one member; its answer is the final result and no extra
  synthesis request is made.
- **Council** — 2–6 independent member responses followed by PRIMARY synthesis.
- **Best-of-N** — 2–6 independent candidates followed by a dedicated selection
  prompt that compares and synthesizes the strongest result.
- **Review** — member responses → PRIMARY draft → independent Reviewer → PRIMARY
  revision. Reviewer cannot be a member or the chair.
- **Arbitration** — member responses → independent Reviewer → separate Arbiter.
  Arbiter cannot be a member or the Reviewer.
- **Delegate** — members provide the initial analysis; PRIMARY creates a bounded
  JSON task plan, up to 8 depth-1 delegates execute the selected subproblems,
  then PRIMARY synthesizes the final answer.

## Safety and bounded autonomy

Delegation depth is hard-coded and schema-validated as **1**. A delegate canno
spawn another delegate. `delegation_max_calls` is constrained to **1–8** and is
included in the preflight reservation as a worst-case cost before execution.

Review and arbitration models are explicit request fields and are validated for
independence. Additional orchestration stages use the same AI Gateway, timeout,
fallback, cancellation, Workspace, actor and correlation context as normal
Council members.

## Cost and audit integration

P2-006 extends `council_cost_ledger.kind` with:

- `draf
- `reviewer
- `planner
- `delegate

Final selection/revision/arbitration responses use the final synthesis ledger
line, while auxiliary calls are persisted separately. Provider-reported cos
still takes priority; otherwise P2-005 token-based accounting is used when the
pricing snapshot is known.

The run history stores `execution_mode` and an `orchestration` trace with
auxiliary calls, finalizer stage, delegation depth and actual delegation count.
Replay/retry reconstruct the same orchestration configuration.

## Live UI

The frontend exposes the six modes, independent Reviewer/Arbiter selectors,
delegation call limit, full preset persistence and stage-specific SSE progress.
Reports show the orchestration trace alongside actual cost and token usage.
