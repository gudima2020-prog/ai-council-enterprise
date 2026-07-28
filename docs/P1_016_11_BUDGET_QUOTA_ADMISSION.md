# P1-016.11 — Budgets, Quotas, Cost Accounting and Admission Policy

## Реализовано

- global и workspace-specific budget policies;
- периоды `daily`, `monthly`, `lifetime`;
- режимы `hard` и `observe`;
- лимит общей стоимости;
- максимальная стоимость одной Task;
- лимит Task за период;
- лимиты queued и running/admitted Task;
- allow/deny списки `task_type`;
- финальная admission-проверка непосредственно перед Executor;
- предварительная проверка при API enqueue;
- ручной override с указанием пользователя и причины;
- резервирование estimated cost;
- автоматический release/charge после выполнения;
- append-only USD cost ledger;
- расчёт стоимости по токенам;
- события бюджета автоматически входят в immutable audit trail;
- миграция `20260715_0008`.

## Оценка стоимости Task

В `payload` используется зарезервированный объект:

```json
{
  "_cost": {
    "estimated_usd": 0.18
  }
}
```

Либо расчёт по токенам:

```json
{
  "_cost": {
    "input_tokens": 12000,
    "output_tokens": 3000,
    "input_usd_per_million": 0.50,
    "output_usd_per_million": 1.50
  }
}
```

Фактическую стоимость обработчик может вернуть в результате:

```json
{
  "_usage": {
    "cost_usd": 0.143281
  }
}
```

При отсутствии фактической стоимости для успешно выполненной Task применяется
консервативная оценка `estimated_usd`.

## Endpoints

```text
GET  /api/task-admission/status

POST   /api/task-budget-policies
GET    /api/task-budget-policies
GET    /api/task-budget-policies/{policy_id}
PATCH  /api/task-budget-policies/{policy_id}
DELETE /api/task-budget-policies/{policy_id}

GET /api/task-budgets/status
GET /api/task-cost-ledger

POST /api/tasks/{task_id}/admission/evaluate
POST /api/tasks/{task_id}/admission/override
GET  /api/tasks/{task_id}/cost
POST /api/tasks/{task_id}/cost/charge
```

## Hard policy

Нарушение лимита блокирует исполнение. Task переводится в `waiting` и получает
запись в журнале.

## Observe policy

Task допускается, но нарушение сохраняется как warning в журнале, Event Bus и
audit trail.

## Override

```json
{
  "actor_id": "owner",
  "reason": "Расход подтверждён вручную.",
  "metadata": {
    "approval_id": "approval_..."
  }
}
```

Override является отдельной неизменяемой записью cost ledger и не удаляет
исходное нарушение политики.

## Ограничения

- суммы учитываются только в USD;
- автоматическая конвертация валют не выполняется;
- атомарность квот гарантируется внутри одного процесса приложения;
- для multi-node deployment потребуется общий PostgreSQL advisory lock или
  Redis-backed admission lock.

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая миграция:

```text
20260715_0008
```

## Следующий этап

P1-016.12 — Dead-letter queue, replay, operational dashboard and Task Engine
stabilization.
