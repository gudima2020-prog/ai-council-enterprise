# P1-010.1 — Workspace Manager: Model and CRUD

Статус: DONE / Initial implementation

## Реализовано

- таблица `workspaces`;
- `WorkspaceModel`;
- `WorkspaceRepository`;
- `WorkspaceService`;
- уникальные имена;
- типы:
  - general;
  - crypto;
  - council;
  - documents;
  - code;
  - research;
  - personal;
- статусы:
  - active;
  - archived;
  - disabled;
- icon;
- color;
- metadata;
- CRUD API;
- события:
  - `workspace.created`;
  - `workspace.updated`;
  - `workspace.deleted`;
- тесты.

## Endpoints

```text
POST   /api/workspaces
GET    /api/workspaces
GET    /api/workspaces/{workspace_id}
PATCH  /api/workspaces/{workspace_id}
DELETE /api/workspaces/{workspace_id}
```

## Пример создания Crypto Workspace

```json
{
  "name": "Crypto AI",
  "description": "Торговля, аналитика и стратегии",
  "workspace_type": "crypto",
  "icon": "chart",
  "color": "#00AA88",
  "status": "active",
  "metadata": {}
}
```

## Проверка

Полностью перезапустить backend:

```powershell
.\run_api.bat
```

Открыть Swagger:

```text
http://127.0.0.1:8000/docs
```

Выполнить:

1. `POST /api/workspaces`;
2. `GET /api/workspaces`;
3. `GET /api/workspaces/{workspace_id}`;
4. `PATCH /api/workspaces/{workspace_id}`;
5. `DELETE /api/workspaces/{workspace_id}`.

## Следующая задача

P1-010.2 — привязка Projects, Chats и Memory к Workspace.
