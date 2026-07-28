# P1-020.3.1 — Migration Chain Integrity

Документ фиксирует состояние шага P1-020.3.1. Текущий release head после
P2-002 — `20260716_0044`; описанные ниже `0043` и 143 таблицы относятся к
проверявшейся на этом шаге версии схемы.

## Найденный дефект

Чистый `alembic upgrade head` доходил до `20260716_0043`, но создавал только
136 из 143 таблиц. Baseline считала `alembic_version` существующей
application-схемой и пропускала семь core-таблиц. `downgrade base` затем
завершался с `NoSuchTableError: workspaces`.

## Исправление

- baseline `20260714_0001` получила explicit immutable DDL;
- `alembic_version` исключена из проверки application tables;
- migration manager проверяет таблицы и колонки до stamping;
- полная baseline stamp-ится на `20260714_0001`;
- полная актуальная схема stamp-ится на head;
- частичная схема отклоняется до записи revision;
- Windows upgrade script вызывает migration manager.

## Инварианты

1. Head один: `20260716_0043`.
2. После clean upgrade отсутствующих таблиц/колонок нет.
3. После downgrade base остаются семь core-таблиц с данными.
4. Повторный upgrade восстанавливает полную схему.
5. Ambiguous unversioned schema никогда не stamp-ится автоматически.
