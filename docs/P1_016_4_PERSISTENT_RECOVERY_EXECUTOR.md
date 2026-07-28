# P1-016.4 — Persistent Recovery and Executor Integration

## Реализовано

- TaskExecutor;
- registry обработчиков по `task_type`;
- встроенный безопасный SYSTEM handler:
  - health_check;
  - echo;
- автоматический TaskRun;
- короткие DB-транзакции до и после долгого async handler;
- статусы running/completed/failed через State Machine;
- сохранение result;
- duration_ms;
- TaskLog начала, завершения и ошибки;
- восстановление очереди после перезапуска;
- interrupted tasks переводятся в failed;
- waiting tasks сохраняются без изменений;
- queued/retrying tasks возвращаются в очередь;
- API enqueue для реальной Task;
- executor diagnostics и ручной recovery;
- тесты успешного исполнения, ошибки и recovery.

## Новые endpoints

```text
POST /api/tasks/{task_id}/enqueue

GET  /api/task-executor/status
POST /api/task-executor/recover
```

## Проверка полного цикла

Создать Task:

```json
{
  "workspace_id": null,
  "task_type": "system",
  "priority": "normal",
  "title": "Task Engine health check",
  "description": "",
  "payload": {
    "action": "health_check",
    "components": ["database", "scheduler", "executor"]
  },
  "creator": "user",
  "executor": "builtin.system",
  "max_retries": 2
}
```

Затем:

```text
POST /api/tasks/{task_id}/enqueue
```

Через короткое время:

```text
GET /api/tasks/{task_id}
```

Ожидается:

```text
status = completed
result.status = ok
runs[0].status = completed
```

## Следующий этап

P1-016.5 — Retry policy, cancellation token and execution timeout.
