# P1-001 — Core Configuration Manager

Статус: DONE / Initial implementation

Реализовано:

- `AppSettings`;
- `ConfigurationManager`;
- загрузка `.env`;
- загрузка `config/config.json`;
- загрузка `data/user_settings.json`, если файл существует;
- environment overrides;
- валидация параметров;
- endpoint `/api/settings` без раскрытия API-ключа.

Приоритет:

1. Environment variables.
2. User settings.
3. Project config.
4. Defaults.

Проверка:

```powershell
.\run_api.bat
```

Открыть:

```text
http://127.0.0.1:8000/api/settings
```

Следующая задача: P1-002 — Centralized Logging.
