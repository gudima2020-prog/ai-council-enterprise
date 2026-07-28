# P1-017.10 — Distributed Workers, Leases and Horizontal Scaling

## Назначение

Этап добавляет постоянную очередь выполнения Execution Plan, реестр Worker и
lease-механизм с fencing token. Несколько процессов AI Studio Enterprise могут
работать с одной базой данных и безопасно забирать независимые Work Items.

## Гарантии

- один Work Item имеет только одного актуального владельца;
- lease ограничен по времени и требует продления;
- каждый повторный захват увеличивает `fencing_token`;
- устаревший Worker не может завершить работу после повторного назначения;
- просроченные leases возвращаются в очередь до исчерпания `max_attempts`;
- Worker, прекративший heartbeat, переводится в `offline`;
- статус `draining` запрещает получение новой работы, но не отбирает текущие leases;
- `idempotency_key` защищает от повторной постановки одного плана;
- очередь поддерживает приоритет, отложенный запуск, capabilities и несколько queue names.

## Таблицы

- `execution_workers`;
- `execution_work_items`;
- `execution_leases`.

## Режимы использования

### Внешний Worker

Внешний процесс использует API:

1. зарегистрироваться;
2. периодически отправлять heartbeat;
3. вызвать claim;
4. выполнять Execution Plan;
5. продлевать lease;
6. завершить lease через complete или fail.

### Встроенный Worker

В каждом backend-процессе можно включить локальный Worker:

```env
AI_STUDIO_DISTRIBUTED_WORKER_ENABLED=1
AI_STUDIO_WORKER_KEY=node-01
AI_STUDIO_WORKER_QUEUES=default,research
AI_STUDIO_WORKER_CAPABILITIES=execution_plan,research
AI_STUDIO_WORKER_CONCURRENCY=2
AI_STUDIO_WORKER_LEASE_SECONDS=90
```

Для горизонтального масштабирования каждый процесс должен иметь уникальный
`AI_STUDIO_WORKER_KEY`. Все процессы должны использовать общую БД.

## Ограничение SQLite

Lease и fencing token защищают логическую целостность, однако SQLite остаётся
однописательной БД. Для интенсивного многопроцессного режима рекомендуется
PostgreSQL. На SQLite следует использовать небольшое число Worker и умеренную
частоту polling.

## События аудита

- `execution_worker.*`;
- `execution_dispatch.*`;
- `execution_lease.*`;
- `execution_distribution.*`.

Все события автоматически записываются в Immutable Audit Trail.
