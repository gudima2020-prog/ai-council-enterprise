# EVENT_BUS.md

# AI Studio Enterprise — Event Bus Design

Версия: 0.1  
Фаза: PHASE 0 — Проектирование  
Статус: Draft

---

## 1. Назначение

Event Bus — внутренняя шина событий AI Studio Enterprise.

Он нужен для слабосвязанного взаимодействия между модулями:

- Chat;
- AI Council;
- Crypto Platform;
- Documents;
- Memory;
- Agents;
- Plugins;
- UI;
- System Services.

Модули не должны напрямую вызывать друг друга, если действие можно выразить событием.

---

## 2. Главные принципы

1. События имеют единый формат.
2. События публикуются через Event Bus.
3. Подписчики не должны блокировать издателя.
4. Ошибка одного обработчика не ломает всю систему.
5. Важные события сохраняются в журнал.
6. Плагины подписываются только на разрешённые события.
7. События не должны содержать секреты.
8. Event Bus должен быть локальным в v1, но проектироваться с возможностью перехода на Redis/NATS/Kafka в будущем.

---

## 3. Роль Event Bus в архитектуре

```text
Module A
  │
  ▼
Event Bus
  │
  ├── Memory
  ├── Agents
  ├── Plugins
  ├── UI Notifications
  └── Logs
```

Пример:

```text
Chat Module
  │
  ▼
chat.message.created
  │
  ├── Memory сохраняет сообщение
  ├── Agent может запустить анализ
  ├── UI показывает уведомление
  └── Logger пишет событие
```

---

## 4. Классы событий

### 4.1. System Events

```text
system.started
system.stopped
system.error
system.config.updated
system.health.changed
```

### 4.2. Chat Events

```text
chat.created
chat.message.created
chat.message.updated
chat.message.deleted
chat.response.started
chat.response.completed
chat.response.failed
```

### 4.3. AI Gateway Events

```text
ai.request.created
ai.request.sent
ai.response.received
ai.response.failed
ai.provider.rate_limited
ai.provider.unavailable
ai.cost.updated
```

### 4.4. AI Council Events

```text
council.session.created
council.model.started
council.model.completed
council.model.failed
council.consensus.started
council.consensus.completed
council.session.failed
```

### 4.5. Memory Events

```text
memory.item.created
memory.item.updated
memory.item.deleted
memory.search.performed
memory.embedding.created
```

### 4.6. Documents Events

```text
document.uploaded
document.parsing.started
document.parsing.completed
document.parsing.failed
document.analysis.started
document.analysis.completed
document.deleted
```

### 4.7. Crypto Events

```text
crypto.market.updated
crypto.signal.created
crypto.analysis.started
crypto.analysis.completed
crypto.risk.warning
crypto.exchange.error
```

### 4.8. Agent Events

```text
agent.created
agent.run.started
agent.run.completed
agent.run.failed
agent.tool.used
agent.permission.denied
```

### 4.9. Plugin Events

```text
plugin.discovered
plugin.loaded
plugin.enabled
plugin.disabled
plugin.failed
plugin.permission.denied
```

### 4.10. UI Events

```text
ui.notification.created
ui.workspace.opened
ui.workspace.closed
ui.theme.changed
```

---

## 5. Event Envelope

Каждое событие должно иметь единый формат.

```json
{
  "id": "event_123",
  "event_type": "chat.message.created",
  "source": "chat",
  "project_id": "project_1",
  "workspace_id": "workspace_1",
  "correlation_id": "corr_123",
  "causation_id": "event_122",
  "priority": "normal",
  "payload": {},
  "metadata": {},
  "created_at": "2026-07-09T14:00:00Z"
}
```

---

## 6. Поля события

### id

Уникальный идентификатор события.

### event_type

Тип события в формате:

```text
domain.entity.action
```

Примеры:

```text
chat.message.created
crypto.signal.created
document.uploaded
```

### source

Источник события:

```text
chat
crypto
documents
memory
plugin:<plugin_id>
agent:<agent_id>
system
```

### project_id

Опциональная привязка к проекту.

### workspace_id

Опциональная привязка к workspace.

### correlation_id

Используется для объединения группы связанных событий.

Пример: один пользовательский запрос может породить 20 событий.

### causation_id

ID события, которое стало причиной текущего события.

### priority

Приоритет события:

```text
low
normal
high
critical
```

### payload

Данные события.

### metadata

Техническая информация.

### created_at

Время создания события.

---

## 7. Правила payload

Payload должен:

- быть JSON-сериализуемым;
- не содержать секреты;
- не содержать полные большие документы;
- не содержать API-ключи;
- содержать ссылки на сущности вместо больших данных.

Правильно:

```json
{
  "document_id": "doc_123"
}
```

Неправильно:

```json
{
  "document_full_text": "огромный текст..."
}
```

---

## 8. Публикация событий

Публикация:

```python
event_bus.publish(
    event_type="chat.message.created",
    source="chat",
    payload={"chat_id": chat_id, "message_id": message_id}
)
```

Публикация должна быть быстрой.

Если обработчики выполняют долгую работу, они должны делать это асинхронно.

---

## 9. Подписка на события

Подписка:

```python
event_bus.subscribe(
    "chat.message.created",
    handler=handle_chat_message
)
```

Поддерживаемые варианты:

- точная подписка;
- wildcard-подписка;
- подписка по домену.

Примеры:

```text
chat.message.created
chat.*
crypto.signal.*
```

---

## 10. Delivery Model

Для v1 используется in-process Event Bus.

Гарантии v1:

- событие доставляется всем активным обработчикам в текущем процессе;
- ошибка одного обработчика логируется;
- остальные обработчики продолжают работу;
- критические события сохраняются в events table.

Не гарантируется в v1:

- доставка после перезапуска, если событие не сохранено;
- распределенная доставка между несколькими процессами;
- exactly-once delivery.

---

## 11. Future Delivery Model

В будущем возможно подключение:

```text
Redis Pub/Sub
NATS
Kafka
RabbitMQ
```

Цель: не менять бизнес-модули при замене реализации Event Bus.

Для этого модули работают только через интерфейс:

```python
EventBus.publish()
EventBus.subscribe()
```

---

## 12. Обработка ошибок

Если обработчик события вызывает ошибку:

1. ошибка логируется;
2. создается событие `system.error` или `event.handler.failed`;
3. обработка других подписчиков продолжается;
4. при критической ошибке модуль может быть отключен.

Пример error event:

```json
{
  "event_type": "event.handler.failed",
  "source": "event_bus",
  "payload": {
    "original_event_id": "event_123",
    "handler": "memory.save_message",
    "error_code": "HANDLER_ERROR"
  }
}
```

---

## 13. Приоритеты

Приоритеты:

```text
low
normal
high
critical
```

Примеры:

- `ui.theme.changed` — low;
- `chat.message.created` — normal;
- `crypto.risk.warning` — high;
- `security.secret.exposed` — critical.

---

## 14. Persisted Events

Не все события нужно хранить.

Хранить обязательно:

- security events;
- crypto signals;
- trade-related events;
- agent runs;
- council sessions;
- document upload/delete;
- provider errors;
- plugin permission denied.

Можно не хранить:

- UI hover events;
- temporary status updates;
- progress ticks.

---

## 15. Events Table

Таблица `events`:

```text
id
event_type
source
project_id
workspace_id
correlation_id
causation_id
priority
payload_json
metadata_json
created_at
processed_at
status
```

Статусы:

```text
created
processing
processed
failed
ignored
```

---

## 16. Event Bus и Memory

Memory подписывается на важные события:

```text
chat.message.created
document.analysis.completed
council.consensus.completed
crypto.analysis.completed
```

Memory не должна сохранять всё подряд. Она применяет Memory Policy.

---

## 17. Event Bus и AI Gateway

AI Gateway публикует события:

```text
ai.request.created
ai.request.sent
ai.response.received
ai.response.failed
ai.provider.rate_limited
```

Это позволит:

- считать стоимость;
- отслеживать задержку;
- диагностировать ошибки;
- строить статистику моделей.

---

## 18. Event Bus и Plugins

Плагин может подписываться только на события, указанные в `plugin.json`.

Пример:

```json
{
  "events": {
    "subscribes": ["chat.message.created"],
    "publishes": ["plugin.my_plugin.completed"]
  }
}
```

Если плагин пытается подписаться на запрещённое событие, операция блокируется.

---

## 19. Event Bus и Agents

Агенты могут запускаться по событиям.

Пример:

```text
document.uploaded
  ↓
Document Agent starts analysis
```

Или:

```text
crypto.signal.created
  ↓
Risk Agent evaluates signal
```

---

## 20. Event Bus и UI

Frontend может получать события через:

- polling;
- Server-Sent Events;
- WebSocket.

Для ранней версии достаточно polling.

В будущем желательно WebSocket.

---

## 21. Security Events

События безопасности:

```text
security.secret.masked
security.permission.denied
security.plugin.blocked
security.agent.blocked
security.crypto.key.added
security.crypto.key.rejected
```

Security events всегда сохраняются.

---

## 22. Naming Convention

Формат:

```text
domain.entity.action
```

Правила:

- lower case;
- только латиница;
- разделитель точка;
- действие в прошедшем времени для фактов.

Примеры:

```text
chat.message.created
document.file.uploaded
crypto.signal.generated
```

---

## 23. Event Versioning

Событие может иметь версию схемы.

```json
{
  "event_type": "chat.message.created",
  "schema_version": 1
}
```

Если payload меняется несовместимо — версия увеличивается.

---

## 24. Корреляция событий

Каждый пользовательский запрос должен иметь `correlation_id`.

Пример цепочки:

```text
chat.message.created
  ↓
ai.request.created
  ↓
ai.response.received
  ↓
chat.response.completed
  ↓
memory.item.created
```

Все эти события имеют один `correlation_id`.

---

## 25. Минимальная реализация v1

Для v1 достаточно:

- in-process Event Bus;
- publish;
- subscribe;
- wildcard-подписка;
- error handling;
- events table;
- базовый security logging.

---

## 26. После v1

После v1:

- WebSocket для UI;
- persistent queue;
- retry policy;
- dead letter queue;
- Redis/NATS adapter;
- distributed event bus;
- event replay.

---

## 27. Архитектурные запреты

Запрещено:

- передавать секреты в payload;
- передавать большие документы в payload;
- вызывать модули напрямую, если есть событие;
- позволять плагинам подписываться на любые события без permissions;
- останавливать Event Bus из-за ошибки одного обработчика.

---

## 28. Следующий документ

Следующий документ:

`AI_GATEWAY.md`

Задача:

P0-009 — AI Gateway Design.
