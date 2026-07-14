# P1-010.6 — Workspace Context Propagation

Статус: DONE / Initial implementation

## Реализовано

- request-scoped Workspace context через `contextvars`;
- middleware для определения Workspace;
- приоритет заголовка `X-Workspace-ID`;
- fallback на активный Workspace;
- проверка существования и статуса Workspace;
- автоматическая загрузка Workspace Policy;
- `request.state.workspace_context`;
- response headers:
  - `X-Workspace-ID`;
  - `X-Workspace-Source`;
- endpoint диагностики;
- workspace-aware AI Gateway factory;
- тесты.

## Порядок разрешения контекста

```text
X-Workspace-ID header
        ↓
active Workspace
        ↓
no Workspace context
```

## Endpoint

```text
GET /api/context
```

## Проверка через Swagger

1. Убедиться, что Workspace восстановлен и активирован.
2. Выполнить:

```text
GET /api/context
```

Без заголовка будет использован активный Workspace.

## Проверка явного Workspace

Swagger неудобен для произвольных заголовков. Можно проверить через PowerShell:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/context" `
  -Headers @{
    "X-Workspace-ID" = "workspace_..."
  }
```

## Ожидаемый результат

```json
{
  "workspace_id": "workspace_...",
  "source": "request_header",
  "workspace": {
    "name": "Crypto AI",
    "status": "active"
  },
  "policy": {
    "model": "qwen/qwen3-30b-a3b",
    "memory_mode": "workspace"
  }
}
```

## Следующая задача

P1-011 — Core integration hardening and automated test suite.
