# AI_GATEWAY.md

# AI Studio Enterprise — AI Gateway Design

Версия: 0.1  
Фаза: PHASE 0 — Проектирование  
Статус: Draft

---

## 1. Назначение

AI Gateway — единая точка доступа ко всем LLM-провайдерам и моделям в AI Studio Enterprise.

Ни один модуль платформы не должен обращаться к внешним LLM напрямую.

Все запросы должны идти по цепочке:

```text
Module / Agent / Plugin
        ↓
AI Gateway
        ↓
Provider Adapter
        ↓
External LLM Provider
```

---

## 2. Главные задачи AI Gateway

AI Gateway должен:

- принимать запросы от модулей;
- выбирать модель;
- выбирать провайдера;
- управлять токенами;
- управлять стоимостью;
- обрабатывать ошибки;
- выполнять fallback;
- вести журнал запросов;
- публиковать события в Event Bus;
- поддерживать streaming;
- нормализовать ответы разных провайдеров;
- защищать API-ключи;
- предоставлять единый интерфейс для Chat, Council, Agents, Documents, Crypto и Plugins.

---

## 3. Поддерживаемые провайдеры

На раннем этапе:

```text
OpenRouter
```

В будущем:

```text
OpenAI
Anthropic
Google Gemini
DeepSeek direct
Qwen direct
Ollama
LM Studio
LocalAI
AnythingLLM
```

Каждый провайдер подключается через отдельный Provider Adapter.

---

## 4. Архитектура

```text
AI Gateway
│
├── Gateway API
├── Request Router
├── Model Manager
├── Provider Registry
├── Provider Adapters
├── Token Manager
├── Cost Tracker
├── Retry Manager
├── Fallback Manager
├── Response Normalizer
├── Safety Filter
├── Cache Layer
├── Event Publisher
└── Request Logger
```

---

## 5. Gateway API

Внутренний интерфейс:

```python
gateway.ask(
    prompt="...",
    model="default",
    mode="universal",
    context={},
)
```

Расширенный интерфейс:

```python
gateway.complete(
    messages=[
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."}
    ],
    model="openrouter/free",
    provider="openrouter",
    temperature=0.4,
    max_tokens=1000,
    metadata={}
)
```

---

## 6. Request Object

Единый формат запроса:

```json
{
  "request_id": "req_123",
  "project_id": "project_1",
  "workspace_id": "workspace_1",
  "source": "chat",
  "mode": "universal",
  "provider": "openrouter",
  "model": "openrouter/free",
  "messages": [],
  "temperature": 0.4,
  "max_tokens": 1000,
  "stream": false,
  "metadata": {}
}
```

---

## 7. Response Object

Единый формат ответа:

```json
{
  "request_id": "req_123",
  "provider": "openrouter",
  "model": "openrouter/free",
  "content": "Ответ модели",
  "status": "success",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 300,
    "total_tokens": 400
  },
  "latency_ms": 1200,
  "cost": 0.0012,
  "error": null,
  "raw": {}
}
```

---

## 8. Provider Adapter

Provider Adapter приводит внешний API к единому интерфейсу.

Интерфейс:

```python
class ProviderAdapter:
    def complete(request: GatewayRequest) -> GatewayResponse:
        ...
```

Каждый адаптер отвечает за:

- форматирование запроса;
- вызов внешнего API;
- обработку ответа;
- нормализацию usage;
- нормализацию ошибок;
- возврат GatewayResponse.

---

## 9. OpenRouter Adapter

На раннем этапе основной адаптер — OpenRouter.

Требования:

- base_url: `https://openrouter.ai/api/v1`;
- поддержка chat completions;
- поддержка max_tokens;
- поддержка temperature;
- поддержка provider errors;
- маскирование ключей;
- понятные ошибки для пользователя.

---

## 10. Model Manager

Model Manager отвечает за:

- список моделей;
- модель по умолчанию;
- fallback-модели;
- разрешённые модели для модулей;
- параметры модели;
- лимиты токенов;
- стоимость;
- доступность.

Пример конфигурации:

```json
{
  "default_model": "openrouter/free",
  "fallback_models": [
    "openai/gpt-oss-20b:free",
    "google/gemma-4-31b-it:free"
  ],
  "max_tokens": 1000,
  "temperature": 0.4
}
```

---

## 11. Request Router

Request Router выбирает, куда отправить запрос.

Критерии маршрутизации:

- выбранная пользователем модель;
- тип задачи;
- стоимость;
- доступность провайдера;
- поддержка vision/tools/json;
- лимит токенов;
- fallback policy.

Примеры маршрутов:

```text
chat → default model
code → coding model
documents → long-context model
crypto → analytical model
council → multiple models
```

---

## 12. Token Manager

Token Manager отвечает за:

- лимит max_tokens;
- оценку размера prompt;
- обрезку контекста;
- предупреждение о превышении лимита;
- выбор модели с большим context window.

Минимум для v1:

- всегда задавать max_tokens;
- не допускать запросов без лимита.

---

## 13. Cost Tracker

Cost Tracker должен считать:

- стоимость запроса;
- стоимость по модели;
- стоимость по проекту;
- стоимость по модулю;
- стоимость за день/месяц.

Для v1 достаточно сохранять usage, если провайдер его возвращает.

---

## 14. Retry Manager

Retry Manager обрабатывает временные ошибки.

Повтор допустим при:

```text
rate_limit
timeout
temporary_provider_error
network_error
```

Повтор не выполняется при:

```text
invalid_api_key
insufficient_quota
model_not_found
permission_denied
bad_request
```

---

## 15. Fallback Manager

Fallback Manager выбирает резервную модель, если основная недоступна.

Пример:

```text
deepseek unavailable
  ↓
qwen fallback
  ↓
gemini fallback
```

Fallback должен быть прозрачным, но пользователь должен видеть, какая модель фактически ответила.

---

## 16. Error Normalization

Все ошибки провайдеров приводятся к единому формату.

```json
{
  "code": "INSUFFICIENT_CREDITS",
  "message": "Недостаточно средств или лимита для выполнения запроса.",
  "provider": "openrouter",
  "recoverable": false,
  "details": {}
}
```

Коды ошибок:

```text
INVALID_API_KEY
INSUFFICIENT_CREDITS
MODEL_NOT_FOUND
MODEL_UNAVAILABLE
RATE_LIMIT
TIMEOUT
NETWORK_ERROR
PROVIDER_ERROR
BAD_REQUEST
UNKNOWN_ERROR
```

---

## 17. Event Bus Integration

AI Gateway публикует события:

```text
ai.request.created
ai.request.sent
ai.response.received
ai.response.failed
ai.provider.rate_limited
ai.provider.unavailable
ai.cost.updated
```

Каждый запрос должен иметь correlation_id.

---

## 18. Logging

AI Gateway должен логировать:

- request_id;
- provider;
- model;
- source module;
- status;
- latency;
- usage;
- error code;
- masked metadata.

Запрещено логировать:

- API-ключи;
- полный prompt с секретами;
- seed phrases;
- пароли;
- приватные токены.

---

## 19. Security

AI Gateway — единственный компонент, имеющий доступ к LLM API keys.

Модули, агенты и плагины не получают ключи напрямую.

Запрещено:

```text
Plugin -> OpenAI
Agent -> OpenRouter
Crypto Module -> Gemini
Documents Module -> Anthropic
```

Разрешено:

```text
Plugin -> AI Gateway
Agent -> AI Gateway
Crypto Module -> AI Gateway
Documents Module -> AI Gateway
```

---

## 20. Streaming

AI Gateway поддерживает streaming через `ask_stream`. Provider adapter передаёт
нормализованные текстовые fragments, а Gateway сохраняет единый формат
результата, usage, logging, события и secret lease.

Схема:

```text
Provider Stream
  ↓
AI Gateway Stream Normalizer
  ↓
Backend SSE/WebSocket
  ↓
Frontend
```

В v0.7.0 streaming используется Live Council. Adapter без собственной
потоковой реализации остаётся совместимым и передаёт полный ответ одним
fragment.

---

## 21. Cache Layer

Кэширование может использоваться для:

- одинаковых справочных запросов;
- embeddings;
- моделей;
- metadata провайдеров.

Нельзя кэшировать:

- приватные документы без разрешения;
- чувствительные запросы;
- торговые решения без политики хранения.

---

## 22. AI Council Integration

AI Council использует AI Gateway для каждого запроса.

```text
Council
  ├── gateway.ask_stream(model_a)
  ├── gateway.ask_stream(model_b)
  ├── gateway.ask_stream(model_c)
  └── gateway.ask_stream(judge_model) ──► SSE
```

AI Council не должен иметь собственного клиента OpenRouter.

---

## 23. Agents Integration

Агенты используют AI Gateway как инструмент.

```python
agent.context.ai_gateway.ask(...)
```

Агент не должен иметь доступ к ключам.

---

## 24. Documents Integration

Documents Module отправляет в AI Gateway только подготовленный контекст:

- chunks;
- summary;
- extracted text;
- metadata.

Большие документы не отправляются целиком без chunking.

---

## 25. Crypto Integration

Crypto Module отправляет в AI Gateway структурированный рыночный контекст.

Пример:

```json
{
  "symbol": "ENA/USDT",
  "timeframes": ["5m", "15m", "1h"],
  "price": 0.123,
  "indicators": {},
  "risk_context": {}
}
```

AI Gateway не принимает торговые решения сам. Он только возвращает аналитический ответ.

---

## 26. Минимальная реализация v1

Для версии v1 необходимо:

- OpenRouter adapter;
- GatewayRequest;
- GatewayResponse;
- max_tokens;
- error normalization;
- request logging;
- Event Bus events;
- default model;
- fallback model;
- integration with Chat;
- integration with AI Council;
- streaming interface и cooperative cancellation.

---

## 27. После v1

После v1:

- cost dashboard;
- provider health checks;
- automatic routing;
- local models;
- tool calling;
- JSON mode;
- structured outputs;
- embeddings;
- multimodal input.

---

## 28. Архитектурные запреты

Запрещено:

- прямой LLM-вызов из модулей;
- отсутствие max_tokens;
- сохранение API-ключей в логах;
- хранение provider-specific response в бизнес-логике;
- смешивание prompt manager и provider adapter;
- fallback без уведомления пользователя.

---

## 29. Завершение PHASE 0

Этот документ завершает PHASE 0.

Следующий этап:

`PHASE 1 — Core`

Первая задача:

`P1-001 — Core Configuration Manager`
