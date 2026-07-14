# P1-011 — Core Integration Hardening

Статус: Initial implementation

## Добавлено

- readiness-проверка ядра;
- диагностика приложения;
- проверка подключения к базе данных;
- проверка зарегистрированных моделей;
- проверка состояния плагинов;
- проверка активного Workspace;
- HTTP 503 при неготовности системы;
- единая конфигурация pytest;
- API smoke tests;
- PowerShell smoke test;
- Windows launcher для тестов.

## Endpoints

```text
GET /api/system/readiness
GET /api/system/diagnostics
```

## Автоматические тесты

```powershell
.\run_tests.bat
```

## Smoke test работающего API

Сначала запустить backend:

```powershell
.\run_api.bat
```

В другом окне PowerShell:

```powershell
.\smoke_test.bat
```

## Интерпретация readiness

```text
ready
```

Все критические компоненты готовы.

```text
degraded
```

Один или несколько критических компонентов не готовы. Endpoint возвращает HTTP 503.

Отсутствие выбранного активного Workspace считается `not_configured`, а не ошибкой.

## Следующий шаг

После прохождения тестов:

```text
P1-012 — Database migrations with Alembic
```
