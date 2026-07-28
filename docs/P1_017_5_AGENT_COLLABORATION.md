# P1-017.5 — Agent Messaging, Delegation and Shared Context

## Реализовано

- persistent conversations для Execution Plan;
- последовательные сообщения между агентами;
- сообщения типов `message`, `request`, `response`, `delegation`, `system`;
- адресные и broadcast-сообщения;
- reply/correlation links;
- статусы `sent`, `read`, `handled`;
- общий JSON-контекст выполнения;
- области контекста `plan`, `agent`, `step`, `delegation`;
- optimistic concurrency через `expected_version`;
- иерархическое делегирование с ограничением глубины;
- принятие, отклонение, запуск и отмена delegation;
- фактическое выполнение delegation существующим Agent Runtime;
- автоматическое добавление контекста и последних сообщений в `StepExecutionContext`;
- output directives `_context_write`, `_messages`, `_delegations`;
- сохранение результата delegation в shared context;
- события `agent.*` и `execution_context.*` в Immutable Audit Trail;
- миграция `20260715_0014`.

## Новые таблицы

```text
agent_conversations
agent_messages
agent_delegations
execution_context_entries
```

## Runtime context

Agent executor получает:

```python
context.conversation_id
context.shared_context
context.recent_messages
```

## Output directives

```json
{
  "result": "ok",
  "_context_write": {
    "analysis.score": 91,
    "analysis.approved": true
  },
  "_messages": [
    {
      "recipient_agent_id": "agent_...",
      "message_type": "request",
      "subject": "Проверь результат",
      "content": {
        "score": 91
      },
      "priority": "high"
    }
  ],
  "_delegations": [
    {
      "delegate_agent_id": "agent_...",
      "objective": "Провести независимую проверку",
      "input": {
        "score": 91
      },
      "timeout_seconds": 300,
      "max_depth": 3
    }
  ]
}
```

Collaboration directives являются дополнительным слоем. Ошибка записи
служебного сообщения или контекста не отменяет уже успешно выполненный Step;
она фиксируется событием `agent.collaboration.error`.

## Endpoints

```text
GET  /api/agent-collaboration/status

POST /api/execution-plans/{plan_id}/conversations
GET  /api/execution-plans/{plan_id}/conversations
GET  /api/agent-conversations/{conversation_id}
POST /api/agent-conversations/{conversation_id}/close

POST /api/agent-conversations/{conversation_id}/messages
GET  /api/agent-conversations/{conversation_id}/messages
POST /api/agent-messages/{message_id}/read

PUT    /api/execution-plans/{plan_id}/shared-context/{key}
GET    /api/execution-plans/{plan_id}/shared-context
GET    /api/execution-plans/{plan_id}/shared-context/entries
DELETE /api/execution-plans/{plan_id}/shared-context/{key}

POST /api/execution-plans/{plan_id}/delegations
GET  /api/execution-plans/{plan_id}/delegations
GET  /api/agent-delegations/{delegation_id}
POST /api/agent-delegations/{delegation_id}/accept
POST /api/agent-delegations/{delegation_id}/reject
POST /api/agent-delegations/{delegation_id}/run
POST /api/agent-delegations/{delegation_id}/cancel
```

## Context versioning

Первая запись:

```json
{
  "value": {
    "score": 80
  },
  "scope_type": "plan"
}
```

Обновление без потери параллельных изменений:

```json
{
  "value": {
    "score": 91
  },
  "scope_type": "plan",
  "expected_version": 1
}
```

При несовпадении версии API возвращает `409 Conflict`.

## Delegation lifecycle

```text
requested -> accepted -> running -> completed
                    \-> failed
requested/accepted/running -> cancelled
requested/accepted -> rejected
```

## Проверка

```powershell
.\run_tests.bat
.\db_current.bat
```

Ожидаемая ревизия:

```text
20260715_0014
```

## Следующий этап

P1-017.6 — Planner service, automatic plan generation and replanning.
