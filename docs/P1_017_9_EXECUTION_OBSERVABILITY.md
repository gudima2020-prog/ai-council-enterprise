# P1-017.9 — Execution Observability, SLA/SLO and Operations Dashboard

## Назначение

Этап добавляет постоянные метрики оркестрации, SLO-политики и автоматическую
фиксацию нарушений. Метрики вычисляются по сохранённым Execution Plan, Step Run,
Tool Invocation, Planner Run, Review и Supervisor Incident, поэтому состояние
панели восстанавливается после перезапуска приложения.

## Основные показатели

- количество Execution Plan и распределение по статусам;
- активные планы;
- успешность планов и шагов;
- P50/P95 длительности планов, шагов и инструментов;
- количество повторных попыток;
- доля ошибок Tool Runtime;
- состояние Planner и Critic/Reviewer;
- доступность зарегистрированных агентов;
- открытые Supervisor incidents;
- открытые SLO breaches.

## SLO

Поддерживаются глобальная политика и отдельная политика Workspace. Проверяются:

- минимальная успешность планов;
- минимальная успешность шагов;
- максимальная P95 длительность плана;
- максимальная P95 длительность шага;
- максимальная доля ошибок инструментов;
- максимальное количество открытых критических инцидентов.

Проверки, кроме критических инцидентов, выполняются только после достижения
`min_sample_size`. Нарушение сохраняется с постоянным `dedupe_key`. Когда метрика
возвращается в допустимый диапазон, нарушение автоматически переводится в
`resolved`.

## API

- `GET /api/execution-observability/status`
- `GET /api/execution-observability/dashboard`
- `POST /api/execution-observability/collect`
- `POST /api/execution-observability/evaluate`
- `GET /api/execution-observability/prometheus`
- CRUD чтения/изменения SLO policies
- просмотр snapshots и SLO breaches
- ручное закрытие или отклонение breach

## Prometheus

Endpoint `/api/execution-observability/prometheus` возвращает текстовый формат
Prometheus 0.0.4. Экспортируются ключевые показатели успешности, задержек,
инцидентов и нарушений SLO.

## Безопасность

Сервис только читает рабочие таблицы оркестрации и записывает собственные
snapshots/breaches. Он не отменяет планы и не меняет их результаты. Операционные
вмешательства остаются ответственностью Execution Supervisor.
