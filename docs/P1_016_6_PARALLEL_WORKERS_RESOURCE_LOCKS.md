# P1-016.6 — Parallel Worker Pool and Resource Locks

## Реализовано

- параллельный `TaskWorkerPool`;
- четыре worker в штатном runtime;
- общий предел конкурентности;
- отдельные ограничения по `task_type`;
- именованные асинхронные блокировки ресурсов;
- семафоры с capacity больше единицы;
- упорядоченный захват нескольких ресурсов без взаимной блокировки;
- `concurrency_key` для последовательного выполнения группы задач;
- диагностика активных workers и занятых ресурсов;
- тесты параллелизма, лимитов и блокировок.

## Объявление ресурсов

На этом этапе параметры исполнения хранятся в зарезервированной секции
универсального Task payload:

```json
{
  "_execution": {
    "resource_locks": [
      "exchange:bingx:main-account"
    ],
    "concurrency_key": "trading-account-main"
  }
}
```

Остальные данные задачи располагаются рядом:

```json
{
  "pair": "BTCUSDT",
  "action": "open_position",
  "_execution": {
    "resource_locks": [
      "exchange:bingx:main-account"
    ],
    "concurrency_key": "trading-account-main"
  }
}
```

Две задачи с одинаковым resource lock или `concurrency_key` не выполняются
одновременно, даже если свободны другие workers.

## Встроенные лимиты типов

```text
system   = 4
plugin   = 2
workflow = 1
trader   = 1
worker   = 2
chat     = 4
import   = 1
export   = 2
pipeline = 2
```

## Диагностика

```text
GET /api/task-scheduler/status
GET /api/task-executor/status
GET /api/task-executor/resources
```

## Миграции

Новая миграция не требуется: декларации блокировок используют существующее
поле `payload_json`.

## Следующий этап

P1-016.7 — Task dependencies, DAG validation and workflow execution.
