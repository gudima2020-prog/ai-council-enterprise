# P1-016.3 — In-memory Queue and Scheduler

## Реализовано

- async priority queue;
- приоритеты:
  - critical;
  - urgent;
  - high;
  - normal;
  - low;
- FIFO внутри одинакового приоритета;
- защита от повторного добавления Task;
- background scheduler;
- injected async handler;
- graceful start/stop;
- статистика;
- события:
  - task.scheduler.started;
  - task.scheduler.stopped;
  - task.enqueued;
  - task.dequeued;
  - task.scheduler.failed;
- API диагностики и ручного добавления;
- тесты.

## Endpoints

```text
GET  /api/task-scheduler/status
POST /api/task-scheduler/enqueue
```

## Ограничение текущего этапа

Очередь хранится в памяти процесса. После перезапуска приложения она
очищается. На следующем этапе scheduler будет восстанавливать задачи со
статусом `queued` из базы данных.

## Следующий этап

P1-016.4 — Persistent queue recovery and executor integration.
