# P1-010.2 — Workspace Entity Links

Статус: DONE / Initial implementation

## Реализовано

- `workspace_id` в таблице `projects`;
- `workspace_id` в таблице `chats`;
- `workspace_id` в таблице `memory_items`;
- реальные Foreign Key для новых баз данных;
- совместимость с существующей SQLite-базой через lightweight migration;
- проверка существования Workspace;
- автоматическое наследование `workspace_id` чатом от проекта;
- защита от привязки проекта к другому Workspace;
- списки сущностей Workspace;
- summary endpoint;
- тесты связей.

## Новые и обновлённые endpoints

```text
POST /api/projects
POST /api/chats

GET /api/workspaces/{workspace_id}/summary
GET /api/workspaces/{workspace_id}/projects
GET /api/workspaces/{workspace_id}/chats
GET /api/workspaces/{workspace_id}/memory
```

## Проверка

1. Создать Workspace.
2. Создать Project с его `workspace_id`.
3. Создать Chat с `project_id` и `workspace_id`.
4. Создать Memory item с `workspace_id`.
5. Выполнить:

```text
GET /api/workspaces/{workspace_id}/summary
```

Ожидается:

```json
{
  "counts": {
    "projects": 1,
    "chats": 1,
    "memory_items": 1
  }
}
```

## Важное замечание о миграции

На текущем development-этапе используется небольшой SQLite migration helper.
Он добавляет недостающие `workspace_id` колонки в существующую базу.

В дальнейшем будет внедрён Alembic как единственный механизм миграций.

## Следующая задача

P1-010.3 — Workspace Settings and model policy.
