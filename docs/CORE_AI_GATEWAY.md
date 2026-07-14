# P1-004 — AI Gateway implementation

Статус: DONE / Initial implementation

## Реализовано

- `GatewayRequest`;
- `GatewayResponse`;
- `GatewayUsage`;
- `GatewayError`;
- `ProviderAdapter`;
- `OpenRouterAdapter`;
- `AIGateway`;
- единая нормализация ошибок;
- max_tokens;
- timeout;
- Event Bus integration;
- AI request logs;
- режимные системные prompts;
- Chat API переведён на AI Gateway;
- diagnostic endpoints;
- tests.

## Новые endpoints

```text
GET  /api/gateway/status
POST /api/gateway/test
```

## Проверка

Запустить:

```powershell
.\\install.bat
.\\run_api.bat
```

Открыть:

```text
http://127.0.0.1:8000/docs
```

Сначала выполнить:

```text
GET /api/gateway/status
```

Затем:

```text
POST /api/gateway/test
```

После успешной проверки можно протестировать:

```text
POST /api/chat
```

## Архитектурное изменение

`backend/routers/chat.py` больше не обращается к OpenRouter напрямую.

Теперь цепочка:

```text
Chat API
  ↓
AI Gateway
  ↓
OpenRouter Adapter
  ↓
OpenRouter
```

## Следующая задача

P1-005 — Repository Layer.
