# P1-017.8 — Execution Supervisor Agent

## Назначение

Execution Supervisor наблюдает за выполнением Execution Plan, фиксирует
операционные инциденты и применяет только явно разрешённые политики
вмешательства.

## Контроль

- события запуска, завершения, отмены и отказа Plan;
- ошибки и повторные попытки отдельных Steps;
- зависшие Plan без активности;
- слишком долго выполняющиеся Steps;
- дедупликация повторяющихся инцидентов;
- ручное подтверждение и закрытие инцидентов;
- ручной cancel или replan;
- автоматический cancel/replan только при включённом
  `automatic_actions_enabled`.

## Безопасная политика по умолчанию

При первом запуске создаётся глобальная политика `global`. Она записывает
инциденты, но не выполняет разрушительные действия автоматически.

## Таблицы

- `execution_supervisor_policies`
- `execution_supervisor_incidents`
- `execution_supervisor_actions`

## Основные API

- `GET /api/execution-supervisor/status`
- `POST /api/execution-supervisor/scan`
- `POST /api/execution-supervisor/policies`
- `GET /api/execution-supervisor/policies`
- `PATCH /api/execution-supervisor/policies/{policy_id}`
- `GET /api/execution-supervisor/incidents`
- `POST /api/execution-supervisor/incidents/{incident_id}/acknowledge`
- `POST /api/execution-supervisor/incidents/{incident_id}/resolve`
- `GET /api/execution-supervisor/actions`
- `POST /api/execution-plans/{plan_id}/supervisor/intervene`
- `GET /api/execution-plans/{plan_id}/supervisor`

## Audit

Все события имеют префикс `execution_plan.supervisor.*` и автоматически
попадают в существующий Immutable Audit Trail через подписку
`execution_plan.*`.
