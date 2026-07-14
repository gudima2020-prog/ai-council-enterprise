# P1-010.4 — Active Workspace State

Статус: DONE / Initial implementation

## Реализовано

- постоянное состояние выбранного Workspace;
- хранение через существующую таблицу `settings`;
- получение активного Workspace;
- активация Workspace;
- сброс активного Workspace;
- автоматическая очистка устаревшего ID;
- запрет активации archived/disabled Workspace;
- сводные счётчики Projects, Chats и Memory;
- события:
  - `workspace.activated`;
  - `workspace.deactivated`;
- тесты.

## Endpoints

```text
GET    /api/workspaces/active
PUT    /api/workspaces/active
DELETE /api/workspaces/active
```

## Активация

```json
{
  "workspace_id": "workspace_..."
}
```

## Важное архитектурное правило

Маршруты `/workspaces/active` регистрируются раньше маршрута
`/workspaces/{workspace_id}`, чтобы слово `active` не воспринималось как ID.

## Проверка

1. Получить настоящий ID через `GET /api/workspaces`.
2. Выполнить `PUT /api/workspaces/active`.
3. Выполнить `GET /api/workspaces/active`.
4. Перезапустить backend.
5. Повторить `GET /api/workspaces/active` — выбор должен сохраниться.
6. Выполнить `DELETE /api/workspaces/active`.

## Следующая задача

P1-010.5 — Workspace integration events and context propagation.
