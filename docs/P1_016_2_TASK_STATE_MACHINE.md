# P1-016.2 — Task State Machine

## Реализовано

- централизованная таблица переходов;
- запрет прямого изменения статуса через PATCH;
- проверка допустимости перехода;
- terminal statuses;
- timestamps:
  - started_at;
  - finished_at;
- retry counter;
- max_retries enforcement;
- автоматический TaskLog на каждый переход;
- Event Bus для каждого нового статуса;
- API получения допустимых переходов;
- API выполнения перехода;
- тесты state machine и service.

## Новые endpoints

```text
GET  /api/tasks/{task_id}/transitions
POST /api/tasks/{task_id}/transition
```

## Пример перехода

```json
{
  "status": "queued",
  "reason": "Задача подтверждена пользователем",
  "metadata": {}
}
```

## Основные переходы

```text
created -> queued | cancelled
queued -> planning | running | cancelled
planning -> waiting | running | failed | cancelled
waiting -> queued | running | failed | cancelled
running -> waiting | post_processing | completed | failed | cancelled
post_processing -> completed | failed | cancelled
failed -> retrying | cancelled
retrying -> queued | running | failed | cancelled
completed -> terminal
cancelled -> terminal
```

## Проверка

```powershell
.\run_tests.bat
```

После запуска API:

1. Создать Task.
2. Получить допустимые переходы.
3. Перевести Task в `queued`.
4. Получить Task и проверить автоматический log.
5. Попробовать `created -> completed` на новой Task — должен вернуться HTTP 409.

## Следующий этап

P1-016.3 — In-memory Queue and Scheduler.
