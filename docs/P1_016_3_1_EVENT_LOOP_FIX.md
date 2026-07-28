# P1-016.3.1 — Task Runtime Event Loop Fix

## Причина ошибки

`AppContainer` создавался один раз при импорте `backend.main`.

Вместе с ним один раз создавался:

```text
asyncio.PriorityQueue
```

Каждый `TestClient(app)` запускал новый lifespan, который мог работать в
другом event loop. Очередь оставалась привязана к предыдущему loop, поэтому
при следующей остановке возникала ошибка:

```text
RuntimeError: PriorityQueue is bound to a different event loop
```

## Исправление

- TaskQueue больше не создаётся при импорте приложения;
- TaskQueue и TaskScheduler создаются внутри каждого lifespan;
- после shutdown они останавливаются и удаляются из контейнера;
- следующий startup получает новую пару queue/scheduler;
- endpoint статуса корректно работает до/после runtime;
- добавлен regression-тест трёх последовательных TestClient lifespans.

## Проверка

```powershell
.\run_tests.bat
```

Ожидается:

```text
64 passed
TEST RESULT: PASSED
```
