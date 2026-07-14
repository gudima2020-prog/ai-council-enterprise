# P1-009 — Memory Service

Статус: DONE / Initial implementation

## Реализовано

- таблица `memory_items`;
- `MemoryRepository`;
- `MemoryService`;
- уровни памяти:
  - global;
  - project;
  - workspace;
  - session;
  - long_term;
- типы источников:
  - manual;
  - chat;
  - document;
  - crypto;
  - agent;
  - council;
  - system;
- создание;
- получение;
- список;
- фильтрация;
- текстовый поиск;
- обновление;
- удаление;
- importance score;
- tags;
- metadata;
- Event Bus события:
  - `memory.item.created`;
  - `memory.item.updated`;
  - `memory.item.deleted`;
- tests.

## Endpoints

```text
POST   /api/memory
GET    /api/memory
GET    /api/memory/search
GET    /api/memory/{memory_item_id}
PATCH  /api/memory/{memory_item_id}
DELETE /api/memory/{memory_item_id}
```

## Проверка

Перезапустить backend:

```powershell
.\run_api.bat
```

Открыть:

```text
http://127.0.0.1:8000/docs
```

Создать запись:

```json
{
  "scope": "project",
  "title": "Тестовая память",
  "content": "AI Studio сохраняет важный контекст проекта.",
  "source_type": "manual",
  "project_id": null,
  "workspace_id": null,
  "source_id": null,
  "importance": 0.8,
  "tags": ["test", "core"],
  "metadata": {}
}
```

Затем проверить:

```text
GET /api/memory
GET /api/memory/search?query=AI Studio
```

## Ограничения текущей версии

- поиск пока обычный текстовый;
- embeddings пока не реализованы;
- автоматическая запись чатов в Memory будет добавлена позже;
- policy engine для отбора важных данных пока отсутствует;
- векторное хранилище будет отдельным этапом.

## Следующая задача

P1-010 — Workspace Manager.
