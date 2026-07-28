# P1-014 — FastAPI Lifespan

## Реализовано

- удалены устаревшие `@app.on_event("startup")`;
- удалены устаревшие `@app.on_event("shutdown")`;
- добавлен `asynccontextmanager lifespan`;
- сохранён прежний порядок запуска ядра;
- сохранена публикация событий:
  - `system.started`;
  - `system.stopped`;
- Plugin Loader продолжает регистрировать маршруты после загрузки;
- добавлены тесты жизненного цикла приложения.

## Порядок запуска

```text
Database migrations
        ↓
Model registry seed
        ↓
Plugin discovery
        ↓
Plugin loading
        ↓
Plugin router registration
        ↓
system.started
```

## Порядок остановки

```text
system.stopped
        ↓
Application shutdown completed
```

## Проверка

```powershell
cd C:\Projects\ai-council-enterprise
.\run_tests.bat
```

Предупреждения по `on_event` должны исчезнуть.

Затем:

```powershell
.\run_api.bat
```

Проверить:

```text
http://127.0.0.1:8000/api/health
http://127.0.0.1:8000/docs
```

## Оставшееся предупреждение

Предупреждение Starlette о `httpx`/`httpx2` не относится к lifespan.
Оно будет устранено отдельным этапом обновления тестового клиента и
фиксирования совместимых зависимостей.

## Следующий этап

P1-015 — Dependency Injection container.
