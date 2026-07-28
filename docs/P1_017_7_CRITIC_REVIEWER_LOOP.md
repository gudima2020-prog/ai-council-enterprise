# P1-017.7 — Critic/Reviewer Loop

## Реализовано

- постоянная история review каждого Execution Plan;
- подключаемый реестр Critic Adapter;
- встроенный `builtin.critic`;
- оценка по шести направлениям;
- решения `pass`, `revise`, `reject`;
- quality threshold;
- несколько раундов review;
- детерминированные auto-fix операции;
- обязательный preflight review перед запуском Runtime;
- Approval Gate перед рискованными инструментами;
- финальный Quality Review step;
- повторная validation и assignment после исправления;
- события review включаются в Immutable Audit Trail.

## Измерения качества

```text
structural_integrity
objective_coverage
safety
executability
resilience
observability
```

## Auto-fix

Critic умеет безопасно:

- удалить self/unknown dependencies;
- вывести capability из agent_role;
- увеличить слишком короткий timeout;
- добавить retry для Agent step;
- добавить базовую strategy;
- поставить Approval Gate перед рискованным Tool step;
- добавить независимый Quality Review перед выдачей результата.

Критические ошибки, например пустой Plan или dependency cycle, автоматически
не маскируются и приводят к `reject`.

## Endpoints

```text
GET  /api/execution-critic/status
POST /api/execution-plans/{plan_id}/review
POST /api/execution-plans/{plan_id}/review-and-fix
POST /api/execution-plans/{plan_id}/apply-review-fixes
GET  /api/execution-plan-reviews
GET  /api/execution-plan-reviews/{review_id}
```

## Preflight policy

Planner записывает в metadata:

```json
{
  "_review_policy": {
    "enabled": true,
    "reviewer_ref": "builtin.critic",
    "threshold": 80,
    "auto_fix": true,
    "max_rounds": 2,
    "require_pass": true
  }
}
```

Runtime не запускает Plan, пока review не вернёт `pass`.

## Миграция

```text
20260715_0016
```

## Следующий этап

P1-017.8 — Supervisor Agent, health monitoring and intervention policy.
