# P1-016.10 — Immutable Task Audit Trail

## Реализовано

- append-only журнал событий Task Engine;
- последовательная нумерация записей;
- SHA-256 hash chain;
- защита ORM от update/delete;
- обнаружение изменения старых записей;
- автоматическая запись `task.*` и `workflow.*` событий;
- actor attribution;
- trace задачи вместе с runs, logs, artifacts и approvals;
- evidence package для пользовательского подтверждения;
- fingerprint всего trace и approval evidence;
- фильтрация журнала;
- проверка целостности через API;
- миграция `20260715_0007`.

## Новая таблица

```text
task_audit_events
```

Ключевые поля:

```text
sequence
event_id
event_type
source
task_id
approval_id
actor_type
actor_id
payload_json
previous_hash
event_hash
```

## Цепочка

```text
hash[1] = SHA256(event[1] + GENESIS_HASH)
hash[2] = SHA256(event[2] + hash[1])
hash[3] = SHA256(event[3] + hash[2])
```

Изменение payload, времени, actor, идентификаторов или порядка записей
делает проверку цепочки неуспешной.

## Endpoints

```text
GET /api/task-audit/status
GET /api/task-audit/events
GET /api/task-audit/events/{audit_id}
GET /api/task-audit/verify
GET /api/tasks/{task_id}/audit-trace
GET /api/task-approvals/{approval_id}/evidence
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая миграция:

```text
20260715_0007
```

Проверка цепочки:

```text
GET /api/task-audit/verify
```

Ожидается:

```json
{
  "valid": true,
  "entries": 10,
  "errors": []
}
```

## Approval evidence

Evidence package содержит:

- исходный prompt;
- requester;
- решение;
- пользователя, принявшего решение;
- note;
- timestamps;
- связанные audit hashes;
- SHA-256 fingerprint пакета;
- результат проверки общей цепочки.

Это техническое доказательство целостности, но не квалифицированная
электронная подпись.

## Следующий этап

P1-016.11 — Task quotas, budgets, cost accounting and execution policy.
