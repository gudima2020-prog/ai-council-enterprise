# P1-002 — Centralized Logging

Статус: DONE / Initial implementation

Реализовано:

- `LoggerManager`;
- JSON-логи;
- вывод в консоль;
- `logs/system.log`;
- `logs/errors.log`;
- ротация файлов;
- request ID;
- correlation ID;
- HTTP middleware;
- маскирование API-ключей и токенов;
- тесты маскирования.

Проверка:

```powershell
.\run_api.bat
```

Открыть:

```text
http://127.0.0.1:8000/api/health
```

Затем:

```powershell
Get-Content .\logs\system.log -Tail 20
```

Следующая задача: P1-003 — Event Bus implementation.
