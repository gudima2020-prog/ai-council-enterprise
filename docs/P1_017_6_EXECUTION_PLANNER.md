# P1-017.6 — Planner Service and Automatic Replanning

## Реализовано

- отдельный `ExecutionPlannerService`;
- реестр подключаемых Planner Adapter;
- встроенный `builtin.planner`;
- генерация Execution Plan из Task или текстовой цели;
- режимы `fast`, `balanced`, `thorough`;
- автоматическая валидация плана;
- автоматическое назначение агентов;
- опциональный запуск сразу после планирования;
- постоянная история Planner Run;
- сохранение входа, ответа, ошибки и длительности планирования;
- ручной replanning failed/cancelled Plan;
- перенос результатов завершённых шагов в checkpoint;
- увеличение timeout и retry для упавшего шага;
- lineage: root plan, parent plan, revision;
- перевод предыдущей версии в `superseded`;
- автоматический replanning после runtime failure;
- ограничение количества автоматических ревизий;
- события Planner включаются в Immutable Audit Trail.

## Новая таблица

```text
execution_planner_runs
```

## Endpoints

```text
GET  /api/execution-planner/status
POST /api/execution-planner/generate

GET /api/execution-planner/runs
GET /api/execution-planner/runs/{run_id}

POST /api/execution-plans/{plan_id}/replan
```

## Генерация из существующей Task

```json
{
  "source_task_id": "task_...",
  "planner_ref": "builtin.planner",
  "mode": "balanced",
  "max_parallel_steps": 4,
  "auto_validate": true,
  "auto_assign": true,
  "strict_assignment": false,
  "auto_start": false,
  "auto_replan": true,
  "max_replans": 2,
  "context": {
    "requested_by": "user"
  }
}
```

## Генерация только по цели

```json
{
  "title": "Исследование и подготовка отчёта",
  "objective": "Собрать факты, проверить риски и подготовить результат.",
  "mode": "thorough",
  "auto_validate": true,
  "auto_assign": true
}
```

## Replanning

```json
{
  "reason": "Шаг execute завершился временной ошибкой API.",
  "preserve_completed_outputs": true,
  "auto_validate": true,
  "auto_assign": true,
  "auto_start": true,
  "wait": false
}
```

При сохранении завершённых результатов соответствующие шаги новой версии
становятся `checkpoint`. Упавший шаг получает увеличенный timeout и ещё одну
допустимую попытку.

## Миграция

```text
20260715_0015
```

## Следующий этап

P1-017.7 — Critic/Reviewer loop, оценка качества плана и исправление до запуска.
