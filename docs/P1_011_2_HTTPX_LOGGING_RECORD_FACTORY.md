# P1-011.2 — httpx LogRecordFactory compatibility fix

## Почему предыдущий вариант не сработал

Фильтр был установлен на logger `httpx`, но одна и та же запись затем
обрабатывалась несколькими обработчиками:

- pytest logging handler;
- root logger handlers;
- JSON/file handler приложения.

Каждый обработчик самостоятельно вызывал `record.getMessage()`.
Поэтому исправление должно происходить раньше — при создании `LogRecord`.

## Новое исправление

`tests/conftest.py` устанавливает `logging.setLogRecordFactory(...)`.

Factory:

- перехватывает только logger `httpx`;
- проверяет только известный шаблон;
- преобразует строковый код ответа `"200"` в число `200`;
- делает запись безопасной для всех последующих handlers;
- восстанавливает исходный factory после завершения pytest.

## Проверка

```powershell
.\run_tests.bat
```

Ожидаемый результат:

```text
41 passed
TEST RESULT: PASSED
```
