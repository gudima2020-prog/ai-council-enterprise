# P1-010.3 — Workspace Settings and Model Policy

Статус: DONE / Initial implementation

## Реализовано

- настройки Workspace через существующую таблицу `settings`;
- effective policy с наследованием глобальной конфигурации;
- AI provider;
- AI model;
- temperature;
- max tokens;
- memory mode;
- network access;
- filesystem access;
- enabled plugins;
- disabled plugins;
- проверка модели через Model Manager;
- проверка max tokens по лимиту модели;
- reset политики;
- проверка разрешения плагина;
- Event Bus:
  - `workspace.policy.updated`;
  - `workspace.policy.reset`;
- workspace-aware gateway factory;
- tests.

## Endpoints

```text
GET    /api/workspaces/{workspace_id}/policy
PUT    /api/workspaces/{workspace_id}/policy
DELETE /api/workspaces/{workspace_id}/policy
GET    /api/workspaces/{workspace_id}/plugins/{plugin_id}/allowed
```

## Пример политики Crypto AI

```json
{
  "ai_provider": "openrouter",
  "ai_model": "qwen/qwen3-30b-a3b",
  "ai_temperature": 0.2,
  "ai_max_tokens": 2000,
  "memory_mode": "workspace",
  "network_access": "restricted",
  "filesystem_access": "read_only",
  "enabled_plugins": ["example_status"],
  "disabled_plugins": []
}
```

## Следующая задача

P1-010.4 — Active Workspace and frontend selection state.
