# P1-016.5 — Retry, Cancellation and Timeout Policy

## Реализовано

- timeout на уровне каждой Task;
- автоматический retry;
- экспоненциальная задержка retry;
- лимит `max_retries`;
- ручной retry;
- отмена queued и running Task;
- cooperative cancellation token;
- принудительная отмена async execution;
- сохранение причины отмены;
- корректная остановка active executions при shutdown;
- новые события и статистика;
- миграция `20260715_0003`.

## Новые поля Task

```text
timeout_seconds
retry_delay_seconds
cancel_requested_at
cancel_reason
```

## Новые endpoints

```text
POST /api/tasks/{task_id}/cancel
POST /api/tasks/{task_id}/retry
```

## Отмена

```json
{
  "reason": "Остановлено пользователем после проверки."
}
```

## Retry

Automatic retry использует задержку:

```text
retry_delay_seconds * 2^(retry_count - 1)
```

Максимальная задержка ограничена 300 секундами.

## Timeout

При превышении `timeout_seconds`:

```text
running -> failed
```

Если лимит повторов не исчерпан:

```text
failed -> retrying -> queued
```

## Проверка

```powershell
.\run_tests.bat
```

Затем:

```powershell
.\db_current.bat
```

Ожидаемая ревизия:

```text
20260715_0003
```

## Следующий этап

P1-016.6 — Parallel worker pool, concurrency limits and resource locks.
