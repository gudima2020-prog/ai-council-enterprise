# P1-003 — Event Bus implementation

Статус: DONE / Initial implementation

Реализовано:

- `Event`;
- `EventBus`;
- publish/subscribe/unsubscribe;
- wildcard-подписки;
- sync и async handlers;
- изоляция ошибок;
- in-memory history;
- diagnostic API;
- startup/shutdown events;
- tests.

Endpoints:

```text
GET  /api/events/subscriptions
GET  /api/events/history
POST /api/events/test
```

Проверка:

```powershell
.\install.bat
.\run_api.bat
```

Открыть:

```text
http://127.0.0.1:8000/docs
```

Следующая задача: P1-004 — AI Gateway implementation.
