# P1-015 — Dependency Injection Container

## Реализовано

- `AppContainer` для долгоживущих зависимостей;
- единый источник:
  - AppSettings;
  - EventBus;
  - PluginLoader;
- фабрики request-scoped сервисов;
- FastAPI dependencies;
- хранение контейнера в `app.state.container`;
- использование контейнера в lifespan;
- диагностический endpoint;
- тесты контейнера.

## Архитектурное правило

Контейнер не хранит открытую SQLAlchemy Session.

Session создаётся на один запрос:

```text
HTTP request
    ↓
get_db_session
    ↓
repository/service factory
    ↓
commit or rollback
    ↓
session close
```

Это предотвращает утечки соединений и совместное использование Session
между параллельными запросами.

## Новый endpoint

```text
GET /api/system/container
```

Ожидаемый ответ:

```json
{
  "initialized": true,
  "dependencies": {
    "settings": "AppSettings",
    "event_bus": "EventBus",
    "plugin_loader": "PluginLoader"
  }
}
```

## Использование в новых routers

```python
from fastapi import Depends
from backend.api.dependencies import get_workspace_service

@router.get("/example")
def example(service = Depends(get_workspace_service)):
    ...
```

## Проверка

```powershell
cd C:\Projects\ai-council-enterprise
.\run_tests.bat
```

Затем:

```powershell
.\run_api.bat
```

Проверить:

```text
http://127.0.0.1:8000/api/system/container
```

## Следующий этап

P1-016 — Task Engine.
