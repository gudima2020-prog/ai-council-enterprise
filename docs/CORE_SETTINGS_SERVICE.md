# P1-006 — Settings Service

Статус: DONE / Initial implementation

## Реализовано

- таблица `settings`;
- `SettingModel`;
- `SettingsRepository`;
- `SettingsService`;
- scopes:
  - global;
  - project;
  - workspace;
  - user;
- upsert;
- list/filter;
- delete;
- события:
  - `system.config.updated`;
  - `system.config.deleted`;
- tests.

## Endpoints

```text
GET    /api/settings
GET    /api/settings/items
PUT    /api/settings/items
DELETE /api/settings/items/{setting_id}
```

## Пример создания настройки

```json
{
  "scope": "global",
  "key": "theme",
  "value": "dark",
  "project_id": null,
  "workspace_id": null
}
```

## Проверка

```powershell
.\run_api.bat
```

Открыть:

```text
http://127.0.0.1:8000/docs
```

Выполнить:

1. `PUT /api/settings/items`;
2. `GET /api/settings/items`;
3. `DELETE /api/settings/items/{setting_id}`.

## Следующая задача

P1-007 — Model Manager.
