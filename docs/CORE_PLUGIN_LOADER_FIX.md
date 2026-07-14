# P1-008 — Plugin Loader Enterprise Fix

Исправлена коллизия пакетов Python.

## Причина ошибки

Запись:

```text
backend.routes:router
```

конфликтовала с основным пакетом приложения:

```text
backend/
```

Поэтому Python пытался найти:

```text
основной_backend.routes
```

вместо backend-папки плагина.

## Новое правило

Каждый плагин является отдельным Python-пакетом.

Структура:

```text
plugins/
└── example_status/
    ├── __init__.py
    ├── plugin.json
    └── backend/
        ├── __init__.py
        └── routes.py
```

Manifest:

```json
{
  "backend": {
    "entry": "example_status.backend.routes:router"
  }
}
```

## Что изменено

- `plugins` добавляется в `sys.path`;
- каждый плагин импортируется в собственном namespace;
- имя папки должно совпадать с `plugin id`;
- обязательный `__init__.py`;
- loader отклоняет небезопасную запись `backend.routes:router`;
- добавлены проверки структуры пакета;
- добавлены тесты.

## Проверка

Полностью остановить backend:

```text
CTRL+C
```

Запустить снова:

```powershell
.\run_api.bat
```

Проверить:

```text
GET /api/plugins
GET /api/plugins/example_status
GET /api/plugins/example-status/status
```

Ожидается:

```text
status = running
error = null
```
