# P1-011.3 — Logging diagnostics

Этот пакет ничего не изменяет в приложении.

Он собирает:

- `backend/core/logging.py`;
- `tests/conftest.py`;
- `pytest.ini`;
- `requirements.txt`;
- `pyproject.toml`;
- версии FastAPI, Starlette, httpx, pytest и AnyIO;
- текущий `LogRecordFactory`;
- handlers и filters основных logger;
- минимальное воспроизведение ошибки `%d` / `"200"`.

## Запуск

```powershell
cd C:\Projects\ai-council-enterprise
.\collect_logging_diagnostics.bat
```

После выполнения будет создан файл:

```text
diagnostics\logging_diagnostics.txt
```

Его нужно передать для подготовки окончательного исправления.
