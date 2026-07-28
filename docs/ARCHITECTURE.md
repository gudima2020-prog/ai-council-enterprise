# ARCHITECTURE.md

# AI Studio Enterprise — архитектура платформы

Версия документа: 0.1  
Фаза проекта: PHASE 0 — Проектирование  
Статус: Draft  
Дата: 2026-07-09

---

## 1. Назначение документа

Этот документ описывает целевую архитектуру AI Studio Enterprise.

Документ определяет:

- основные слои системы;
- границы ответственности компонентов;
- правила взаимодействия модулей;
- принципы работы AI Gateway;
- принципы Memory;
- Event Bus;
- Agents;
- Plugin System;
- структуру backend и frontend;
- технические ограничения для будущей разработки.

Этот документ является архитектурной основой проекта. Новая крупная функция не должна реализовываться без проверки соответствия этой архитектуре.

---

## 2. Архитектурный стиль

AI Studio Enterprise строится как модульная платформа.

Основные принципы:

1. Модульность.
2. Слабая связанность.
3. Единая точка доступа к LLM.
4. Событийное взаимодействие.
5. Расширяемость через плагины.
6. Разделение backend, frontend и desktop shell.
7. Конфигурация вместо жестко заданных значений.
8. Безопасное хранение секретов.
9. Возможность масштабирования от локального приложения до серверной платформы.

---

## 3. Верхнеуровневая схема

```text
AI Studio Enterprise
│
├── Desktop Shell
│   └── Tauri
│
├── Frontend
│   ├── React
│   ├── TypeScript
│   ├── Vite
│   └── UI Workspaces
│
├── Backend
│   ├── FastAPI
│   ├── Core
│   ├── AI Gateway
│   ├── Memory
│   ├── Event Bus
│   ├── Agents
│   ├── Plugin Loader
│   └── Domain Modules
│
├── Storage
│   ├── SQLite
│   ├── Files
│   ├── Vector Index
│   └── Logs
│
└── External Integrations
    ├── OpenRouter
    ├── OpenAI
    ├── Anthropic
    ├── Gemini
    ├── Ollama
    ├── Exchanges
    ├── GitHub
    └── Web Sources
```

---

## 4. Слои системы

## 4.1. Desktop Shell

Desktop Shell отвечает только за запуск локального приложения.

Технология:

- Tauri.

Задачи:

- запуск frontend;
- упаковка приложения;
- доступ к локальной файловой системе через безопасные разрешения;
- подготовка к desktop-режиму.

Desktop Shell не должен содержать бизнес-логику.

---

## 4.2. Frontend

Frontend отвечает за интерфейс пользователя.

Технологии:

- React;
- TypeScript;
- Vite.

Основные зоны интерфейса:

```text
Frontend
│
├── App Shell
├── Sidebar
├── Workspace Area
├── Chat Panel
├── Model Selector
├── Settings Panel
├── Status Bar
└── Notification System
```

Frontend не должен напрямую обращаться к LLM-провайдерам. Все запросы идут только в Backend API.

---

## 4.3. Backend

Backend является основным центром бизнес-логики.

Технологии:

- Python;
- FastAPI;
- Pydantic;
- Uvicorn.

Основные зоны backend:

```text
backend/
│
├── api/
├── core/
├── gateway/
├── memory/
├── events/
├── agents/
├── modules/
├── plugins/
├── database/
├── security/
└── tests/
```

---

## 5. Core

Core — центральный слой платформы.

Он отвечает за общие сервисы:

- загрузка конфигурации;
- управление настройками;
- логирование;
- запуск приложения;
- маршрутизация внутренних сервисов;
- регистрация модулей;
- доступ к Event Bus;
- доступ к Memory;
- доступ к AI Gateway.

Core не должен знать детали конкретных модулей, таких как Crypto, Documents или Code.

---

## 6. AI Gateway

AI Gateway — единая точка доступа ко всем LLM.

Все запросы к моделям должны проходить через AI Gateway.

Запрещено:

- вызывать OpenRouter напрямую из Chat Module;
- вызывать OpenAI напрямую из Crypto Module;
- вызывать Gemini напрямую из Document Module;
- хранить логику выбора модели внутри модулей.

Правильная схема:

```text
Module
  │
  ▼
AI Gateway
  │
  ▼
Provider Adapter
  │
  ▼
External LLM Provider
```

---

## 7. Provider Adapter

Каждый внешний поставщик моделей подключается через адаптер.

Примеры:

```text
providers/
│
├── openrouter_provider.py
├── openai_provider.py
├── anthropic_provider.py
├── gemini_provider.py
├── ollama_provider.py
└── lmstudio_provider.py
```

Каждый адаптер должен приводить ответ к единому формату.

Единый формат ответа:

```json
{
  "provider": "openrouter",
  "model": "openrouter/free",
  "content": "Ответ модели",
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 300,
    "total_tokens": 400
  },
  "latency_ms": 1200,
  "cost": 0.0012,
  "raw": {}
}
```

---

## 8. Model Manager

Model Manager отвечает за управление моделями.

Функции:

- список доступных моделей;
- модель по умолчанию;
- резервная модель;
- лимиты токенов;
- параметры температуры;
- разрешенные модели для каждого модуля;
- статистика использования.

---

## 9. Prompt Manager

Prompt Manager отвечает за системные промпты.

Он должен поддерживать:

- системные промпты;
- промпты режимов;
- промпты агентов;
- промпты AI Council;
- версионирование промптов.

Прямое хранение больших промптов в коде нежелательно. Они должны выноситься в конфигурацию или отдельные файлы.

---

## 10. Event Bus

Event Bus — механизм внутреннего обмена событиями.

Пример события:

```json
{
  "event_type": "chat.message.created",
  "source": "chat",
  "payload": {
    "chat_id": "123",
    "message": "Текст"
  },
  "created_at": "2026-07-09T14:00:00"
}
```

Задачи Event Bus:

- уведомлять Memory о новых данных;
- уведомлять UI о статусах;
- запускать агентов;
- передавать события между модулями;
- сохранять важные события в журнал.

---

## 11. Memory

Memory — система хранения и повторного использования контекста.

Уровни памяти:

```text
Memory
│
├── Global Memory
├── Project Memory
├── Workspace Memory
├── Session Memory
└── Long-term Memory
```

### Global Memory

Общие настройки, правила, системные знания.

### Project Memory

Контекст конкретного проекта.

Например:

- Crypto AI Trader;
- AI Studio Enterprise;
- документы по муниципальным задачам;
- торговые стратегии.

### Workspace Memory

Контекст открытого рабочего пространства.

### Session Memory

Данные текущего сеанса.

### Long-term Memory

Важные решения и выводы, которые пользователь явно сохраняет.

---

## 12. Domain Modules

Каждый крупный функциональный блок является доменным модулем.

Планируемые домены:

```text
modules/
│
├── chat/
├── council/
├── crypto/
├── documents/
├── code/
├── research/
├── browser/
├── vision/
└── settings/
```

Каждый модуль должен иметь:

```text
module/
│
├── api.py
├── service.py
├── schemas.py
├── events.py
├── repository.py
├── prompts/
└── README.md
```

---

## 13. Chat Module

Chat Module отвечает только за чат.

Он не должен знать, как устроен OpenRouter.

Схема:

```text
Chat UI
  │
  ▼
Chat API
  │
  ▼
Chat Service
  │
  ▼
AI Gateway
```

---

## 14. AI Council Module

AI Council является сервисом коллективного анализа.

Функции:

- запуск нескольких моделей;
- сбор ответов;
- сравнение ответов;
- выделение совпадений;
- выделение расхождений;
- итоговый вывод;
- оценка уверенности;
- Workspace-изолированная история запусков;
- просмотр, replay, удаление и retention policy;
- фоновый live manager, SSE buffer и cooperative cancellation;
- потоковый синтез через единый AI Gateway;
- повтор только неуспешных участников.

Схема:

```text
Council Request
  │
  ├── Model A ─┐
  ├── Model B ─┼── Live events
  ├── Model C ─┘
  │
  ▼
Consensus Engine
  │
  ├── Provider stream ──► SSE buffer ──► React EventSource
  ▼
Persisted Final Answer
```

---

## 15. Crypto Module

Crypto Module должен быть разделен на слои.

```text
crypto/
│
├── exchanges/
├── market_data/
├── indicators/
├── analysis/
├── risk/
├── strategies/
├── execution/
└── api/
```

### Exchange Layer

Подключение к биржам:

- BingX;
- Binance;
- Bybit;
- OKX;
- Hyperliquid.

### Market Layer

Нормализация рыночных данных.

### Analysis Layer

Индикаторы, паттерны, рыночный контекст.

### Risk Layer

Оценка риска.

### Strategy Layer

Стратегии и сценарии.

### Execution Layer

Торговое исполнение. Не реализуется до отдельного Security/Risk design.

---

## 16. Documents Module

Documents Module отвечает за работу с файлами.

Слои:

```text
documents/
│
├── loaders/
├── parsers/
├── ocr/
├── chunking/
├── embeddings/
├── search/
└── analysis/
```

---

## 17. Agents

Agents — независимые сервисы, работающие через инструменты платформы.

Агент не должен напрямую обращаться к LLM. Только через AI Gateway.

Структура агента:

```text
Agent
│
├── Role
├── Tools
├── Memory Scope
├── Prompt
├── Limits
└── Execution Log
```

Планируемые агенты:

- Chief Agent;
- Crypto Agent;
- Research Agent;
- Code Agent;
- Document Agent;
- News Agent;
- Risk Agent.

---

## 18. Plugin System

Плагин должен иметь стандартную структуру:

```text
plugin_name/
│
├── plugin.json
├── backend/
├── frontend/
├── README.md
└── tests/
```

`plugin.json` должен описывать:

- имя;
- версию;
- права доступа;
- backend routes;
- frontend entries;
- события;
- зависимости.

---

## 19. Database Layer

На раннем этапе используется SQLite.

Доступ к базе должен происходить через Repository Layer.

Модули не должны напрямую выполнять SQL-запросы из сервисов.

Схема:

```text
Service
  │
  ▼
Repository
  │
  ▼
Database
```

---

## 20. API Layer

Backend API должен быть стабильным и документированным.

Основные группы API:

```text
/api/health
/api/settings
/api/models
/api/chat
/api/council
/api/memory
/api/projects
/api/documents
/api/crypto
/api/plugins
```

---

## 21. Security

Основные правила:

- API-ключи не хранятся в Git.
- `.env` не публикуется.
- Секреты не логируются.
- Ошибки не должны раскрывать ключи.
- Торговые ключи должны иметь отдельный уровень защиты.
- Модули не должны иметь доступ к секретам напрямую.

---

## 22. Logging

Логирование должно быть централизованным.

Категории логов:

- system;
- api;
- llm;
- crypto;
- documents;
- agents;
- errors;
- security.

---

## 23. Конфигурация

Конфигурация должна быть многоуровневой:

```text
.env
config.yaml
user_settings.json
project_settings.json
```

Приоритет:

1. Runtime settings.
2. Project settings.
3. User settings.
4. Environment variables.
5. Defaults.

---

## 24. Ошибки

Ошибки должны приводиться к единому формату:

```json
{
  "code": "PROVIDER_RATE_LIMIT",
  "message": "Провайдер временно ограничил запросы",
  "details": {},
  "recoverable": true
}
```

---

## 25. Версионирование

Проект использует семантическое версионирование:

```text
MAJOR.MINOR.PATCH
```

Пример:

```text
0.3.0
1.0.0
```

---

## 26. Стратегия развития

Порядок развития:

1. Core architecture.
2. AI Gateway.
3. Chat Workspace.
4. Memory.
5. AI Council.
6. Frontend shell.
7. Plugin System.
8. Crypto Platform.
9. Documents.
10. Agents.

---

## 27. Архитектурные запреты

Запрещено:

- прямые вызовы LLM из модулей;
- хранение ключей в коде;
- смешивание UI и backend-логики;
- торговое исполнение без Risk/Security design;
- привязка Crypto Platform к одной бирже;
- добавление новой функции без обновления документации.

---

## 28. Следующий документ

Следующий документ:

`ROADMAP.md`

Задача:

P0-004 — Roadmap проекта.
