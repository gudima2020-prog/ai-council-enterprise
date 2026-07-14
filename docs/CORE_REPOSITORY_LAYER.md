# P1-005 — Repository Layer

Статус: DONE / Initial implementation

## Реализовано

- SQLAlchemy 2;
- SQLite database;
- `Base`;
- `SessionLocal`;
- `session_scope`;
- автоматическое создание таблиц;
- базовый generic `Repository`;
- `ProjectRepository`;
- `ChatRepository`;
- модели Projects, Chats, Chat Messages;
- diagnostic CRUD endpoints;
- tests.

## Файл базы

После запуска создаётся:

```text
data/ai_studio.db
```

## Новые endpoints

```text
POST /api/projects
GET  /api/projects

POST /api/chats
GET  /api/chats/{chat_id}

POST /api/chats/{chat_id}/messages
```

## Проверка

1. Установить зависимости:

```powershell
.\install.bat
```

2. Запустить:

```powershell
.\run_api.bat
```

3. Открыть:

```text
http://127.0.0.1:8000/docs
```

4. Создать проект.
5. Создать чат.
6. Добавить сообщения.
7. Получить чат с историей.

## Архитектурное правило

Сервисы и модули не должны выполнять SQL напрямую.

Правильная цепочка:

```text
API / Service
  ↓
Repository
  ↓
SQLAlchemy
  ↓
Database
```

## Следующая задача

P1-006 — Settings Service.
