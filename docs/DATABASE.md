# DATABASE.md

# AI Studio Enterprise — Database Design

Версия: 0.1  
Фаза: PHASE 0 — Проектирование  
Статус: Draft

---

## 1. Назначение

Документ описывает целевую модель данных AI Studio Enterprise.

База данных должна поддерживать:

- проекты;
- рабочие области;
- чаты;
- сообщения;
- память;
- документы;
- модели;
- провайдеров;
- AI Council;
- агентов;
- плагины;
- события;
- логи LLM-запросов;
- настройки;
- криптомодуль.

На раннем этапе используется SQLite. Архитектура должна позволять перейти на PostgreSQL без переписывания бизнес-логики.

---

## 2. Основные принципы

1. Все сущности имеют `id`.
2. Все важные сущности имеют `created_at` и `updated_at`.
3. Секреты не хранятся в открытом виде.
4. Работа с БД идёт только через Repository Layer.
5. Модули не выполняют SQL напрямую.
6. Схема должна поддерживать миграции.
7. Логи LLM-запросов отделяются от пользовательской памяти.
8. Память делится на уровни.

---

## 3. Технологии

Начальный этап:

- SQLite;
- SQLAlchemy;
- Alembic;
- Pydantic schemas.

Будущий этап:

- PostgreSQL;
- pgvector для embeddings;
- отдельное хранилище файлов;
- отдельный векторный индекс.

---

## 4. Группы таблиц

```text
Database
│
├── System
├── Users
├── Projects
├── Workspaces
├── Chats
├── Memory
├── Documents
├── Models
├── LLM Logs
├── AI Council
├── Agents
├── Plugins
├── Events
├── Crypto
└── Settings
```

---

## 5. System Tables

### app_meta

Хранит системную информацию.

Поля:

- id;
- key;
- value;
- created_at;
- updated_at.

Примеры:

- database_version;
- app_version;
- last_migration;
- install_id.

---

## 6. Projects

### projects

Проект — верхнеуровневый контейнер данных.

Примеры проектов:

- AI Studio Enterprise;
- Crypto AI Trader;
- Муниципальные документы;
- Недвижимость;
- Личные исследования.

Поля:

- id;
- name;
- description;
- status;
- created_at;
- updated_at;
- archived_at.

---

## 7. Workspaces

### workspaces

Workspace — рабочая область внутри проекта.

Поля:

- id;
- project_id;
- name;
- type;
- layout_json;
- created_at;
- updated_at.

Типы:

- chat;
- crypto;
- documents;
- code;
- research;
- council.

---

## 8. Chats

### chats

Поля:

- id;
- project_id;
- workspace_id;
- title;
- mode;
- model_id;
- created_at;
- updated_at;
- archived_at.

### chat_messages

Поля:

- id;
- chat_id;
- role;
- content;
- tokens;
- model_id;
- created_at;
- metadata_json.

Роли:

- user;
- assistant;
- system;
- tool;
- agent.

---

## 9. Memory

Memory делится на уровни.

### memory_items

Поля:

- id;
- scope;
- project_id;
- workspace_id;
- source_type;
- source_id;
- title;
- content;
- importance;
- tags_json;
- created_at;
- updated_at;
- expires_at;
- metadata_json.

Scope:

- global;
- project;
- workspace;
- session;
- long_term.

Source type:

- chat;
- document;
- crypto;
- manual;
- agent;
- council.

### memory_embeddings

Поля:

- id;
- memory_item_id;
- provider;
- model;
- vector;
- created_at.

В SQLite vector может временно храниться как JSON или BLOB. В PostgreSQL — pgvector.

---

## 10. Documents

### documents

Поля:

- id;
- project_id;
- workspace_id;
- filename;
- file_path;
- mime_type;
- size_bytes;
- sha256;
- status;
- created_at;
- updated_at;
- metadata_json.

### document_chunks

Поля:

- id;
- document_id;
- chunk_index;
- content;
- token_count;
- page_number;
- created_at;
- metadata_json.

### document_analysis

Поля:

- id;
- document_id;
- analysis_type;
- result_text;
- model_id;
- created_at;
- metadata_json.

---

## 11. Models and Providers

### providers

Поля:

- id;
- name;
- type;
- base_url;
- enabled;
- created_at;
- updated_at;
- config_json.

Примеры:

- openrouter;
- openai;
- anthropic;
- gemini;
- ollama;
- lmstudio.

### models

Поля:

- id;
- provider_id;
- name;
- display_name;
- context_window;
- max_output_tokens;
- supports_tools;
- supports_vision;
- supports_json;
- enabled;
- cost_input;
- cost_output;
- metadata_json.

---

## 12. LLM Request Logs

### llm_requests

Поля:

- id;
- provider_id;
- model_id;
- request_type;
- prompt_hash;
- prompt_preview;
- response_preview;
- input_tokens;
- output_tokens;
- total_tokens;
- latency_ms;
- cost;
- status;
- error_code;
- error_message;
- created_at;
- metadata_json.

Важно:

- полный prompt может не сохраняться, если он содержит чувствительные данные;
- ключи не сохраняются;
- секреты должны маскироваться.

---

## 13. AI Council

Таблицы реализованы в revision `20260716_0044`; revision `20260724_0045`
добавляет допустимый статус `cancelled`.

### council_runs

Поля:

- id;
- workspace_id;
- replay_of_run_id;
- question;
- mode;
- status;
- synthesizer provider/model;
- synthesis provider/model/status;
- final_answer;
- consensus_json;
- disagreements_json;
- recommendations_json;
- confidence;
- member_count;
- successful_member_count;
- duration_ms;
- correlation_id;
- actor_id;
- error_code;
- error_message;
- started_at;
- finished_at;
- created_at;
- metadata_json.

### council_run_members

Поля:

- id;
- run_id;
- ordinal;
- provider;
- model;
- requested_model;
- role;
- label;
- status;
- fallback_used;
- fallback_model;
- answer;
- error_code;
- error_message;
- latency_ms;
- input_tokens;
- output_tokens;
- total_tokens;
- created_at;

Политика retention хранится в общей таблице `settings` с ключом
`council.history.retention`.

---

## 14. Agents

### agents

Поля:

- id;
- name;
- type;
- role_prompt;
- enabled;
- memory_scope;
- config_json;
- created_at;
- updated_at.

### agent_runs

Поля:

- id;
- agent_id;
- project_id;
- workspace_id;
- status;
- input_json;
- output_json;
- started_at;
- finished_at;
- error_message.

---

## 15. Plugins

### plugins

Поля:

- id;
- name;
- version;
- description;
- enabled;
- path;
- permissions_json;
- created_at;
- updated_at.

### plugin_events

Поля:

- id;
- plugin_id;
- event_type;
- payload_json;
- created_at.

---

## 16. Events

### events

Поля:

- id;
- event_type;
- source;
- project_id;
- workspace_id;
- payload_json;
- created_at;
- processed_at;
- status.

Примеры событий:

- chat.message.created;
- document.uploaded;
- crypto.signal.created;
- council.session.completed;
- agent.run.failed.

---

## 17. Settings

### settings

Поля:

- id;
- scope;
- project_id;
- key;
- value_json;
- created_at;
- updated_at.

Scope:

- global;
- project;
- workspace;
- user.

---

## 18. Crypto

### exchanges

Поля:

- id;
- name;
- type;
- enabled;
- config_json;
- created_at;
- updated_at.

### market_symbols

Поля:

- id;
- exchange_id;
- symbol;
- base_asset;
- quote_asset;
- market_type;
- enabled;
- metadata_json.

### candles

Поля:

- id;
- symbol_id;
- timeframe;
- open_time;
- open;
- high;
- low;
- close;
- volume;
- created_at.

### crypto_analysis

Поля:

- id;
- project_id;
- symbol_id;
- timeframe;
- context_json;
- analysis_text;
- model_id;
- created_at.

### trade_signals

Поля:

- id;
- symbol_id;
- direction;
- entry_price;
- stop_loss;
- take_profit_json;
- confidence;
- risk_score;
- source;
- status;
- created_at;
- metadata_json.

Торговое исполнение не проектируется в этой версии. Для него будет отдельный Risk/Security Design.

---

## 19. ER-связи верхнего уровня

```text
Project
  ├── Workspaces
  ├── Chats
  ├── Documents
  ├── Memory Items
  ├── Council Sessions
  ├── Agent Runs
  └── Crypto Analysis

Chat
  └── Chat Messages

Document
  ├── Document Chunks
  └── Document Analysis

Council Session
  ├── Council Responses
  └── Council Consensus

Provider
  └── Models

Memory Item
  └── Memory Embeddings
```

---

## 20. Миграции

Проект должен использовать Alembic.

Правила:

1. Любое изменение схемы — через миграцию.
2. Миграции должны быть обратимыми, если возможно.
3. Не допускается ручное изменение production-схемы.
4. Версия схемы фиксируется в `app_meta`.

---

## 21. Repository Layer

Каждый домен должен иметь repository.

Пример:

```text
modules/chat/repository.py
modules/documents/repository.py
modules/crypto/repository.py
memory/repository.py
```

Сервис не должен напрямую работать с SQLAlchemy session за пределами repository.

---

## 22. Политика хранения секретов

В базе не должны храниться открытые API-ключи.

На раннем этапе:

- `.env`.

В будущем:

- encrypted secrets store;
- OS keychain;
- Tauri secure storage.

---

## 23. Индексация и поиск

Поиск должен поддерживать:

- обычный текстовый поиск;
- фильтрацию по проекту;
- фильтрацию по workspace;
- поиск по типу памяти;
- в будущем — vector search.

---

## 24. Резервное копирование

Нужно предусмотреть:

- экспорт SQLite;
- экспорт проекта;
- экспорт чатов;
- экспорт memory;
- восстановление из backup.

---

## 25. Что не входит в текущий дизайн

В текущий документ не входит:

- детальная схема торгового исполнения;
- шифрование на уровне полей;
- распределенная база данных;
- multi-user server mode;
- права доступа пользователей.

Эти темы будут отдельными документами.

---

## 26. Следующий документ

Следующий документ по плану:

`PLUGIN_SDK.md`

После него:

`SECURITY.md`
