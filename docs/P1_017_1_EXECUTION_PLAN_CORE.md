# P1-017.1 — Execution Plan Core

## Назначение

Execution Plan — отдельное от Task представление того, **как** должна быть
выполнена сложная цель. Plan содержит DAG шагов, роли агентов, необходимые
capabilities, инструменты, входные данные и ограничения исполнения.

## Реализовано

- persistent Execution Plans;
- persistent Plan Steps;
- связь с исходной Task;
- типы шагов:
  - agent;
  - tool;
  - task;
  - approval;
  - decision;
  - checkpoint;
- зависимости шагов через `depends_on`;
- поиск неизвестных зависимостей;
- обнаружение циклов;
- топологическая сортировка;
- расчёт параллельных групп;
- roots, leaves и максимальная ширина DAG;
- проверка обязательного `agent_role` и `tool_name`;
- lifecycle Execution Plan;
- автоматический сброс validation после редактирования;
- события `execution_plan.*`;
- запись событий Plan в Immutable Audit Trail;
- миграция `20260715_0010`.

## Lifecycle

```text
draft -> validated -> ready -> running -> completed
                              |          -> failed
                              |          -> cancelled
                              -> superseded
```

Редактирование разрешено в `draft`, `validated` и `failed`. Любое изменение
возвращает Plan в `draft` и очищает результат предыдущей проверки.

## Endpoints

```text
POST   /api/execution-plans
GET    /api/execution-plans
GET    /api/execution-plans/{plan_id}
PATCH  /api/execution-plans/{plan_id}
DELETE /api/execution-plans/{plan_id}

POST   /api/execution-plans/{plan_id}/steps
PATCH  /api/execution-plans/{plan_id}/steps/{step_id}
DELETE /api/execution-plans/{plan_id}/steps/{step_id}

POST /api/execution-plans/{plan_id}/validate
POST /api/execution-plans/{plan_id}/transition
GET  /api/execution-plans/{plan_id}/graph
```

## Следующий этап

P1-017.2 — Agent Registry, capabilities and agent selection policy.
