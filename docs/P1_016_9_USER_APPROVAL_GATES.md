# P1-016.9 — User Approval Gates and Workflow Resume

## Реализовано

- постоянная таблица `task_approvals`;
- запрос подтверждения для отдельной Task;
- approval gate внутри Workflow Template;
- статусы:
  - pending;
  - approved;
  - rejected;
  - expired;
  - cancelled;
- перевод Task в `waiting` до решения пользователя;
- блокировка enqueue при незавершённом approval;
- автоматическое возобновление после approve;
- отмена Task после reject или expiration;
- продолжение DAG после принятого решения;
- каскадная обработка зависимых узлов после отказа;
- срок действия approval;
- startup reconciliation просроченных запросов;
- Event Bus события и runtime-статистика;
- миграция `20260715_0006`.

## Новые endpoints

```text
GET  /api/task-approvals/status
GET  /api/task-approvals
GET  /api/task-approvals/{approval_id}

POST /api/tasks/{task_id}/approval-request
POST /api/task-approvals/{approval_id}/decision
POST /api/task-approvals/reconcile
```

## Ручной запрос подтверждения

```json
{
  "gate_key": "execution",
  "prompt": "Подтвердите выполнение задания.",
  "requested_by": "rentahuman-worker-ai",
  "expires_in_seconds": 86400,
  "metadata": {
    "risk_score": 18,
    "estimated_profit": 192
  }
}
```

После запроса Task переходит:

```text
created -> waiting
```

До решения пользователя вызов enqueue возвращает:

```text
approval_pending = true
enqueued = false
```

## Решение пользователя

Approve:

```json
{
  "decision": "approve",
  "decided_by": "user",
  "note": "Подтверждаю выполнение."
}
```

После approve:

```text
waiting -> queued -> running
```

Reject:

```json
{
  "decision": "reject",
  "decided_by": "user",
  "note": "Не выполнять."
}
```

После reject:

```text
waiting -> cancelled
```

## Approval Gate в Workflow Template

```json
{
  "key": "execute",
  "task_type": "worker",
  "title": "Выполнить подтверждённое задание",
  "payload": {
    "action": "execute_job"
  },
  "approval": {
    "required": true,
    "gate_key": "user_confirmation",
    "prompt": "Подтвердите выполнение задания и предполагаемые расходы.",
    "expires_in_seconds": 86400,
    "metadata": {
      "requires_human_confirmation": true
    }
  }
}
```

Узел workflow не попадёт в очередь, пока пользователь не подтвердит его.

## События

```text
task.approval.requested
task.approval.approved
task.approval.rejected
task.approval.expired
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая миграция:

```text
20260715_0006
```

## Следующий этап

P1-016.10 — Task audit trail, immutable execution ledger and approval evidence.
