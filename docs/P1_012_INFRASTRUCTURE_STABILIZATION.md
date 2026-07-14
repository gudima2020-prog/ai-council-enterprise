# P1-012 — Infrastructure Stabilization

## Установленная первопричина

Проблема была в `SecretMaskingFilter`:

```python
record.args = tuple(mask_secrets(str(value)) for value in record.args)
```

Фильтр преобразовывал все аргументы логирования в строки.

У `httpx` шаблон содержит `%d` для HTTP-кода:

```text
HTTP Request: %s %s "%s %d %s"
```

Изначально HTTP-код был числом `200`, но фильтр превращал его в строку
`"200"`. После этого стандартный `logging` закономерно выбрасывал:

```text
TypeError: %d format: a real number is required, not str
```

## Исправление

- секреты маскируются с сохранением типов;
- числовые аргументы больше не преобразуются в строки;
- добавлен `safe_log_message`;
- консольный formatter также защищён от некорректных сторонних записей;
- JSON formatter не может обрушить приложение из-за `getMessage()`;
- удалены тестовые monkey patches;
- тесты логирования расширены.

## Важное уточнение

Версии FastAPI/Starlette/httpx не являлись причиной этой конкретной ошибки.
Стек вызовов однозначно показал изменение типа внутри
`SecretMaskingFilter` проекта.

## Проверка

```powershell
cd C:\Projects\ai-council-enterprise
.\run_tests.bat
```

Ожидается:

```text
TEST RESULT: PASSED
```

После этого:

```powershell
.\run_api.bat
```

В другом окне:

```powershell
.\smoke_test.bat
```
