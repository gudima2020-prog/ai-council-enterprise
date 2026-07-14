# P1-008 — Plugin Loader

Статус: DONE / Initial implementation

## Реализовано

- поиск плагинов в папке `plugins/`;
- чтение `plugin.json`;
- валидация идентификатора, имени и версии;
- загрузка backend entry point;
- регистрация FastAPI router;
- статусы:
  - discovered;
  - loaded;
  - running;
  - disabled;
  - failed;
- изоляция ошибок плагина;
- события:
  - `plugin.discovered`;
  - `plugin.loaded`;
  - `plugin.failed`;
- API для просмотра плагинов;
- демонстрационный `example_status`;
- тесты.

## Endpoints ядра

```text
GET /api/plugins
GET /api/plugins/{plugin_id}
```

## Endpoint демонстрационного плагина

```text
GET /api/plugins/example-status/status
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

Проверить:

1. `GET /api/plugins`;
2. `GET /api/plugins/example_status`;
3. `GET /api/plugins/example-status/status`.

## Ограничения текущей версии

- frontend-плагины пока только описываются в manifest;
- permissions пока валидируются концептуально, но не исполняются через sandbox;
- hot reload плагинов не реализован;
- зависимости между плагинами пока не разрешаются автоматически.

## Следующая задача

P1-009 — Memory Service.
