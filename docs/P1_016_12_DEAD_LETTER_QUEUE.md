# P1-016.12 — Dead Letter Queue and Task Engine Stabilization

## Реализовано

- durable Dead Letter Queue;
- автоматический захват Task после окончательного `failed`;
- timeout попадает в DLQ только после исчерпания retry;
- полный снимок Task, последнего Run и последних Log;
- защита от дублирования failure events;
- replay через создание новой Task;
- исходная failed Task не изменяется;
- deep-merge исправлений payload;
- idempotency key для безопасного повторного API-запроса;
- лимит replay для защиты от poison tasks;
- ручной `force` после проверки;
- replay history;
- integrity verification for DLQ state;
- автоматический `resolved` после успешного replay;
- discard с пользователем и причиной;
- startup reconciliation для ранее упавших Task;
- включение DLQ-событий в Immutable Audit Trail;
- миграция `20260715_0009`.

## Почему replay создаёт новую Task

Повторное использование исходной terminal Task разрушало бы историю статусов,
Run и подтверждений. DLQ создаёт новую Task и записывает в payload:

```json
{
  "_replay": {
    "dead_letter_id": "dlq_...",
    "source_task_id": "task_...",
    "replay_number": 1,
    "actor_id": "owner",
    "reason": "Исправлена конфигурация"
  }
}
```

## Endpoints

```text
GET  /api/task-dead-letter/status
GET  /api/task-dead-letter
GET  /api/task-dead-letter/{entry_id}
GET  /api/task-dead-letter/verify

POST /api/tasks/{task_id}/dead-letter
POST /api/task-dead-letter/{entry_id}/replay
POST /api/task-dead-letter/{entry_id}/discard
POST /api/task-dead-letter/reconcile
```

## Replay

```json
{
  "actor_id": "owner",
  "reason": "Ошибка входных данных исправлена и replay подтверждён.",
  "idempotency_key": "manual-replay-20260715-001",
  "payload_patch": {
    "input": {
      "validated": true
    }
  },
  "max_retries": 1,
  "timeout_seconds": 300,
  "force": false
}
```

`payload_patch` объединяется с исходным payload рекурсивно.

## Статусы entry

```text
open       — ожидает решения;
replayed   — создана replay Task;
resolved   — replay Task успешно completed;
discarded  — пользователь решил не воспроизводить.
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая ревизия:

```text
20260715_0009
```

## Результат этапа

P1-016 Task Engine завершён как инфраструктурный модуль. Следующий блок:
P1-017 — Execution Plans and Agent Orchestration.
