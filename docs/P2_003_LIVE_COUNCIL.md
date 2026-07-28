# P2-003 — Live Council

Версия: **v0.7.0**
Alembic revision: **`20260724_0045`**

## Результат

AI Council выполняется как фоновый live-запуск. HTTP-запрос создания быстро
возвращает `run_id`, после чего интерфейс получает прогресс участников и
поток итогового синтеза через Server-Sent Events.

Реализованы:

- параллельный запуск участников;
- именованные SSE-события и heartbeat;
- ограниченный буфер событий и возобновление по `Last-Event-ID`;
- настоящий OpenRouter streaming через общий AI Gateway;
- отмена активного запуска;
- тайм-аут каждого участника и председателя;
- live replay;
- повтор только неуспешных участников;
- сохранение завершённых, частичных, неуспешных и отменённых запусков.

Старый синхронный `POST /api/council/run` сохранён для совместимости.

## API

```tex
POST   /api/council/live
GET    /api/council/live/{run_id}
GET    /api/council/live/{run_id}/events
DELETE /api/council/live/{run_id}

POST   /api/council/runs/{run_id}/replay/live
POST   /api/council/runs/{run_id}/retry-failed


### Создание запуска

`POST /api/council/live` принимает обычный `CouncilRunRequest` с дополнительным
полем:

```json
{
  "member_timeout_seconds": 60
}


Допустимый диапазон — 5–600 секунд. Значение применяется отдельно к каждому
участнику и к итоговому синтезу.

Ответ имеет HTTP 202:

```json
{
  "run_id": "council_...",
  "status": "queued",
  "kind": "run",
  "replay_of_run_id": null,
  "events_url": "/api/council/live/council_.../events",
  "status_url": "/api/council/live/council_...",
  "cancel_url": "/api/council/live/council_..."
}


`kind` принимает `run`, `replay` или `retry_failed`.

### Статус

`GET /api/council/live/{run_id}` возвращает:

- `queued`;
- `running`;
- `completed`;
- `failed`;
- `cancelled`.

Live-status описывает состояние фоновой задачи. Итоговый Council-отчёт внутри
события `run.completed` по-прежнему имеет бизнес-статус `completed` или
`partial`.

### SSE

`GET /api/council/live/{run_id}/events` отвечает
`text/event-stream`. Основные события:

| Событие | Назначение |
|---|---|
| `run.accepted` | запуск зарегистрирован |
| `run.started` | оркестрация началась |
| `member.started` | участник начал анализ |
| `member.completed` | участник вернул ответ |
| `member.failed` | участник завершился с ошибкой |
| `member.reused` | использован ранее сохранённый успешный ответ |
| `synthesis.started` | председатель начал синтез |
| `synthesis.delta` | очередной текстовый fragment provider stream |
| `synthesis.completed` | структурированный синтез сформирован |
| `run.cancel.requested` | команда отмены принята |
| `history.failed` | результат готов, но запись истории не удалась |
| `run.completed` | терминальный успешный результат |
| `run.failed` | терминальная ошибка |
| `run.cancelled` | терминальная отмена |

Каждое событие содержит числовые `id`/`sequence`. Браузерный `EventSource
автоматически передаёт `Last-Event-ID` при переподключении. Сервер повторяет
доступные события после этого номера. Во время ожидания отправляется
SSE-комментарий heartbeat.

Терминальное состояние и терминальное событие согласованы: stream не
закрывается до доставки `run.completed`, `run.failed` или `run.cancelled`.

## Streaming через AI Gateway

Council не обращается к OpenRouter напрямую:

```tex
CouncilService
  -> AIGateway.ask_stream
  -> OpenRouterAdapter.complete_stream
  -> OpenRouter stream
  -> SSE buffer
  -> React EventSource


Adapter собирает полный ответ, передаёт chunks вызывающему коду и сохраняет
token usage из финального provider chunk. Секрет извлекается тем же lease
механизмом, что и для обычного запроса, и закрывается после завершения.

Для adapters без собственной потоковой реализации базовый интерфейс
совместимости возвращает ответ одним fragment.

## Отмена и тайм-аут

`DELETE /api/council/live/{run_id}` устанавливает async- и thread-safe сигналы
отмены. Активный OpenRouter stream проверяет сигнал между chunks и закрывается.
Незавершённые участники получают:

```tex
COUNCIL_MEMBER_CANCELLED


При превышении тайм-аута участник получает:

```tex
COUNCIL_MEMBER_TIMEOU


Отмена сохраняется в истории со статусом `cancelled`. Revision 0045 расширяет
ограничение `ck_council_runs_status`; downgrade переводит такие записи в
`failed`, не удаляя отчёты.

## Повтор ошибок

`POST /api/council/runs/{run_id}/retry-failed`:

1. загружает исходный запуск только из текущего Workspace;
2. повторно использует сохранённые результаты со статусом `success`;
3. вызывает provider только для участников со статусом `error`;
4. заново выполняет синтез;
5. сохраняет новый отчёт с `replay_of_run_id`.

Если политика retention не сохраняла тексты успешных ответов, сервер возвращает
HTTP 409. Это предотвращает скрытую подмену отсутствующих данных.

## Workspace isolation

Создание live-запуска связывает его с эффективным Workspace. Получение статуса,
SSE, отмена, replay и retry с другим `X-Workspace-ID` отвечают 404, не раскрывая
существование запуска.

SSE использует тот же Workspace resolver, что и остальные маршруты. В
браузерном интерфейсе `EventSource` наследует активный локальный Workspace.

## Жизненный цикл и хранение

- активные и недавно завершённые live-состояния находятся в памяти процесса;
- буфер одного запуска ограничен 4096 событиями;
- завершённое состояние удаляется из памяти после TTL;
- долговременный отчёт сохраняется отдельной SQLite-сессией;
- ошибка сохранения не уничтожает уже полученный AI-результат;
- при завершении FastAPI активным live-запускам отправляется сигнал отмены.

После перезапуска backend live-stream недоступен, но ранее сохранённый отчёт
остаётся в истории.

## Интерфейс

React UI показывает:

- состояние каждого участника;
- использованный сохранённый ответ при retry;
- текущий этап Совета;
- поток председателя;
- выбор тайм-аута;
- кнопку отмены;
- статус `cancelled` в истории;
- отдельную кнопку **Повторить ошибки**.

После терминального события живой экран заменяется готовым структурированным
отчётом.
