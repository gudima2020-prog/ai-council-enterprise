# P1-007 — Model Manager

Реализовано:

- таблица `model_configs`;
- репозиторий моделей;
- базовые модели;
- включение/отключение;
- приоритеты;
- capabilities: tools, vision, JSON;
- выбор подходящей модели;
- событие `ai.model.updated`.

Endpoints:

```text
GET /api/models
GET /api/models/resolve
GET /api/models/{slug}
PUT /api/models
```

Следующая задача: P1-008 — Plugin Loader.
