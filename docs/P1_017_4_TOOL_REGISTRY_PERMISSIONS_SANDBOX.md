# P1-017.4 — Tool Registry, Permissions and Restricted Execution

## Реализовано

- persistent Tool Registry;
- глобальные и Workspace-specific инструменты;
- версии обработчиков через `handler_ref`;
- уровни риска `low`, `medium`, `high`, `critical`;
- explicit allow / deny permissions;
- deny имеет приоритет над allow;
- разрешения по Workspace и Agent;
- expiration для permissions;
- JSON Schema boundary для input/output;
- ограничения размера input/output;
- timeout каждого инструмента;
- max concurrency каждого инструмента;
- capability declaration:
  - network;
  - filesystem read;
  - filesystem write;
- immutable-style invocation history;
- интеграция с Execution Plan Tool Steps;
- события `tool.*` подключены к Audit Trail;
- миграция `20260715_0013`.

## Модель изоляции

Режим `restricted` создаёт прикладную границу:

- input проходит JSON round-trip;
- handler не получает Session, AppContainer или секреты;
- доступны только явно объявленные capabilities;
- применяются schema, payload, timeout и concurrency limits.

Это не OS/container sandbox. Исполнение непроверенного нативного кода должно
в дальнейшем выполняться в отдельном worker-процессе или контейнере.

## Таблицы

```text
tool_definitions
tool_permissions
tool_invocations
```

## Endpoints

```text
GET  /api/tool-runtime/status

POST   /api/tools
GET    /api/tools
GET    /api/tools/{tool_id}
PATCH  /api/tools/{tool_id}
DELETE /api/tools/{tool_id}

POST   /api/tools/{tool_id}/permissions
GET    /api/tools/{tool_id}/permissions
DELETE /api/tools/{tool_id}/permissions/{permission_id}
POST   /api/tools/{tool_id}/permission/evaluate
POST   /api/tools/{tool_id}/execute

GET /api/tool-invocations
GET /api/tool-invocations/{invocation_id}
```

## Политика по умолчанию

Low/medium-risk tool разрешён, если нет deny и не запрошены опасные
capabilities.

Explicit allow обязателен для:

- high/critical risk;
- `http` и `subprocess` kind;
- network access;
- filesystem write;
- `requires_explicit_allow=true`.

## Следующий этап

P1-017.5 — Agent-to-agent messaging, delegation and shared context.
