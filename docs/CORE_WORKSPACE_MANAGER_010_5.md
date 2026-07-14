# P1-010.5 — Workspace Lifecycle

Статус: DONE / Initial implementation

## Реализовано

- формальная таблица допустимых переходов;
- archive;
- restore;
- disable;
- enable;
- блокировка недопустимых переходов;
- HTTP 409 для конфликтов состояния;
- события:
  - `workspace.archived`;
  - `workspace.restored`;
  - `workspace.disabled`;
  - `workspace.enabled`;
- тесты.

## Endpoints

```text
POST /api/workspaces/{workspace_id}/archive
POST /api/workspaces/{workspace_id}/restore
POST /api/workspaces/{workspace_id}/disable
POST /api/workspaces/{workspace_id}/enable
```

## Допустимые переходы

```text
active   -> archived  (archive)
active   -> disabled  (disable)
archived -> active    (restore)
disabled -> active    (enable)
disabled -> archived  (archive)
```

## Проверка текущего Crypto AI

Workspace сейчас имеет статус:

```text
archived
```

Выполнить:

```text
POST /api/workspaces/{workspace_id}/restore
```

После этого статус станет:

```text
active
```

Затем снова выполнить:

```text
PUT /api/workspaces/active
```

## Следующая задача

P1-010.6 — Workspace Context Propagation.
