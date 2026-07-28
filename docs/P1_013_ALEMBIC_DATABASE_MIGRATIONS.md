# P1-013 / P1-020.3.1 — Alembic Database Migrations

Текущий head: `20260724_0048`.

## Безопасный bootstrap

Команда:

```cmd
db_upgrade.bat
```

вызывает `python -m backend.database.migration_manager upgrade` и до
изменения revision классифицирует базу:

| Состояние | Действие |
|---|---|
| Нет application tables | Полный `upgrade head` |
| Полная P1-013 baseline | `stamp 20260714_0001`, затем upgrade |
| Полная актуальная metadata | `stamp head` без пересоздания |
| Частичная/неоднозначная схема | Остановка без stamp |

Неизвестные таблицы плагинов не блокируют совместимую application schema.

## Исправление baseline

В исходном архиве baseline проверяла наличие любых таблиц. Во время миграции
Alembic заранее создаёт `alembic_version`, поэтому baseline ошибочно завершалась
и не создавала:

- `workspaces`;
- `projects`;
- `chats`;
- `chat_messages`;
- `settings`;
- `model_configs`;
- `memory_items`.

Revision `20260714_0001` теперь содержит явный неизменяемый DDL и игнорирует
`alembic_version`. Он больше не зависит от текущей `Base.metadata`.

## Downgrade policy

Миграции после baseline удаляют только принадлежащие им объекты. Переход
`downgrade base` намеренно сохраняет семь core-таблиц: автоматическое удаление
пользовательских Workspace, чатов и памяти слишком опасно. Полное уничтожение
базы должно быть отдельной явной административной операцией.

## Проверки

Regression test выполняет:

```text
fresh database
  -> upgrade head
  -> проверка всех 145 таблиц/колонок
  -> downgrade base
  -> проверка семи core-таблиц
  -> upgrade head
  -> повторная проверка схемы
```

Также проверяются bootstrap полной baseline, полной актуальной и отклонение
частичной unversioned-схемы.

## Команды

```cmd
db_upgrade.bat
db_current.bat
db_history.bat
```

Новая revision:

```powershell
.\scripts\alembic_revision.ps1 -Message "add council history"
```

## Правила для следующих миграций

- не импортировать текущую ORM metadata для создания исторического DDL;
- revision должна быть воспроизводима спустя годы;
- upgrade/downgrade проверяются на чистой временной SQLite-базе;
- destructive data migration требует резервной копии и явной документации;
- после изменения models должен проходить `schema_diff()` без пропусков.
