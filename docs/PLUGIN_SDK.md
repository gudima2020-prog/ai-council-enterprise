# PLUGIN_SDK.md

# AI Studio Enterprise — Plugin SDK Design

Версия: 0.1  
Фаза: PHASE 0 — Проектирование  
Статус: Draft

---

## 1. Назначение

Plugin SDK описывает правила создания, подключения и исполнения плагинов AI Studio Enterprise.

Плагин — независимое расширение платформы, которое может добавлять:

- backend API;
- frontend-разделы;
- обработчики событий;
- команды;
- инструменты для агентов;
- интеграции с внешними сервисами;
- новые workspace-модули.

Цель Plugin SDK — позволить расширять платформу без изменения ядра.

---

## 2. Главные принципы

1. Плагин не должен изменять код ядра.
2. Плагин регистрируется через Plugin Loader.
3. Плагин имеет manifest-файл.
4. Плагин должен явно объявлять права доступа.
5. Плагин не получает доступ к секретам напрямую.
6. Плагин общается с LLM только через AI Gateway.
7. Плагин взаимодействует с другими модулями через Event Bus.
8. Плагин может иметь backend-часть, frontend-часть или обе части.
9. Плагин должен быть отключаемым.
10. Ошибка плагина не должна ломать всю платформу.

---

## 3. Структура плагина

Стандартная структура:

```text
plugins/
└── plugin_name/
    ├── plugin.json
    ├── backend/
    │   ├── __init__.py
    │   ├── routes.py
    │   ├── service.py
    │   ├── schemas.py
    │   └── events.py
    ├── frontend/
    │   ├── index.tsx
    │   ├── components/
    │   └── routes.ts
    ├── prompts/
    ├── README.md
    └── tests/
```

Минимальный плагин может содержать только:

```text
plugin_name/
├── plugin.json
└── README.md
```

---

## 4. Manifest: plugin.json

Каждый плагин обязан иметь `plugin.json`.

Пример:

```json
{
  "id": "crypto_tools",
  "name": "Crypto Tools",
  "version": "0.1.0",
  "description": "Crypto market analysis tools",
  "author": "AI Studio Enterprise",
  "enabled": true,
  "backend": {
    "entry": "backend.routes:router",
    "prefix": "/api/plugins/crypto-tools"
  },
  "frontend": {
    "entry": "frontend/index.tsx",
    "workspace": true,
    "menu_label": "Crypto Tools"
  },
  "permissions": [
    "ai_gateway.ask",
    "memory.read",
    "memory.write",
    "events.publish"
  ],
  "events": {
    "subscribes": [
      "crypto.signal.created"
    ],
    "publishes": [
      "plugin.crypto_tools.analysis.completed"
    ]
  },
  "settings_schema": {},
  "dependencies": []
}
```

---

## 5. Идентификатор плагина

Поле `id` должно быть:

- уникальным;
- в нижнем регистре;
- без пробелов;
- с символами `a-z`, `0-9`, `_`, `-`.

Пример:

```text
crypto_tools
document_classifier
market_news_agent
```

---

## 6. Backend-плагины

Backend-плагин может регистрировать собственные FastAPI routes.

Пример:

```python
from fastapi import APIRouter

router = APIRouter()

@router.get("/status")
def status():
    return {"status": "ok"}
```

Plugin Loader подключает router по prefix из manifest.

Плагин не должен создавать собственный FastAPI app.

---

## 7. Frontend-плагины

Frontend-плагин может добавлять:

- пункт меню;
- workspace;
- виджет;
- страницу настроек;
- панель инструментов.

Frontend-плагин не должен напрямую обращаться к внешним API. Все запросы идут через backend.

---

## 8. Permissions

Плагин обязан явно указать права доступа.

Базовые права:

```text
ai_gateway.ask
ai_gateway.stream
memory.read
memory.write
events.publish
events.subscribe
documents.read
documents.write
crypto.read
crypto.write
settings.read
settings.write
files.read
files.write
network.access
```

Если плагин пытается выполнить действие без права, Plugin Runtime должен отклонить операцию.

---

## 9. Доступ к AI Gateway

Плагин не может вызывать OpenRouter, OpenAI, Gemini или другие LLM напрямую.

Правильно:

```python
result = context.ai_gateway.ask(
    prompt="Analyze this market",
    model="default"
)
```

Неправильно:

```python
OpenAI(api_key="...")
```

---

## 10. Доступ к Memory

Плагин получает доступ к памяти только через контекст:

```python
context.memory.search(...)
context.memory.save(...)
```

Плагин не должен напрямую обращаться к таблицам БД.

---

## 11. Event Bus

Плагин может публиковать события:

```python
context.events.publish(
    "plugin.example.completed",
    payload={"result": "ok"}
)
```

Плагин может подписываться на события, если это указано в manifest.

---

## 12. Plugin Context

Каждому плагину передается Plugin Context.

```python
class PluginContext:
    plugin_id: str
    settings: SettingsService
    memory: MemoryService
    events: EventBus
    ai_gateway: AIGateway
    logger: Logger
```

Plugin Context — единственный допустимый способ доступа к сервисам платформы.

---

## 13. Plugin Lifecycle

Жизненный цикл плагина:

```text
DISCOVERED
  ↓
LOADED
  ↓
VALIDATED
  ↓
ENABLED
  ↓
RUNNING
  ↓
DISABLED
  ↓
UNLOADED
```

### DISCOVERED

Плагин найден в папке `plugins`.

### LOADED

Manifest загружен.

### VALIDATED

Manifest прошел проверку.

### ENABLED

Плагин разрешен пользователем.

### RUNNING

Плагин активен.

### DISABLED

Плагин отключен.

---

## 14. Plugin Loader

Plugin Loader отвечает за:

- поиск плагинов;
- чтение manifest;
- валидацию;
- проверку прав;
- регистрацию backend routes;
- регистрацию frontend entries;
- регистрацию event handlers;
- отключение неисправных плагинов.

---

## 15. Ошибки плагинов

Ошибка плагина не должна останавливать платформу.

Если плагин падает:

1. ошибка логируется;
2. плагин получает статус `failed`;
3. пользователь видит предупреждение;
4. остальные модули продолжают работать.

---

## 16. Версионирование плагинов

Плагины используют SemVer:

```text
MAJOR.MINOR.PATCH
```

Пример:

```text
1.2.0
```

Платформа должна проверять совместимость plugin API version.

---

## 17. Совместимость

Manifest должен содержать минимальную версию платформы:

```json
{
  "platform": {
    "min_version": "0.5.0",
    "api_version": "1"
  }
}
```

---

## 18. Настройки плагина

Плагин может объявить схему настроек:

```json
{
  "settings_schema": {
    "api_url": {
      "type": "string",
      "required": true
    },
    "enabled": {
      "type": "boolean",
      "default": true
    }
  }
}
```

Настройки сохраняются в таблице `settings` или `plugins`.

---

## 19. Безопасность

Плагинам запрещено:

- читать `.env` напрямую;
- читать секреты напрямую;
- отправлять секреты во внешнюю сеть;
- изменять системные файлы без разрешения;
- выполнять shell-команды без специального права;
- обращаться к LLM напрямую.

Особо опасные разрешения:

```text
network.access
files.write
shell.execute
secrets.read
crypto.execution
```

Такие разрешения должны требовать отдельного подтверждения пользователя.

---

## 20. Типы плагинов

Планируемые типы:

```text
workspace
agent_tool
data_connector
document_processor
crypto_connector
ui_widget
automation
research_tool
```

---

## 21. Пример: простой backend-плагин

```text
plugins/example_status/
├── plugin.json
└── backend/routes.py
```

`plugin.json`:

```json
{
  "id": "example_status",
  "name": "Example Status",
  "version": "0.1.0",
  "backend": {
    "entry": "backend.routes:router",
    "prefix": "/api/plugins/example-status"
  },
  "permissions": []
}
```

---

## 22. Пример: crypto-плагин

Crypto-плагин может:

- получать рыночные данные;
- публиковать события;
- сохранять анализ в Memory;
- отправлять контекст в AI Gateway.

Но не должен напрямую открывать сделки без отдельного разрешения и Risk/Security design.

---

## 23. Plugin Registry

В будущем может быть добавлен Plugin Registry:

- локальный список плагинов;
- marketplace;
- проверка обновлений;
- подпись плагинов;
- рейтинг;
- категории.

---

## 24. Минимальная реализация v1

Для первой версии Plugin SDK достаточно:

- загрузка manifest;
- список плагинов;
- включение/отключение;
- регистрация backend routes;
- базовые permissions;
- логирование ошибок.

---

## 25. Что не входит в v1

В v1 не входит:

- marketplace;
- цифровая подпись;
- удаленная установка;
- sandbox для Python-кода;
- полноценная система прав;
- платные плагины.

---

## 26. Следующий документ

Следующий документ:

`SECURITY.md`

Задача:

P0-007 — Security Design.
